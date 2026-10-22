"""Router pattern (agentic switch) on MAF switch-case edges.

    StepMessage ─▶ router ─(WorkItem per subsystem, plan=[tool ids])─▶ tool ─▶ tool ─▶ ... ─▶ gather ─▶ StepMessage
                        └──────────────── ScrapeStart (expected count) ──────────────────────▶ gather

- The router decides each subsystem's tool chain (`WorkItem.plan`).
- MAF switch-case edges send a WorkItem to the executor named by `next_tool`, or to gather when done.
- Each tool appends `findings[tool_id]` and advances the cursor; on error the chain stops.
- gather waits for all items (MAF workflow state), then emits one StepMessage with a
  StepResult(step_id=<gather id>, output={"planner", "systems": [...]}) for the next step.

Lanes (concurrent; `lanes:` in workflows.yaml):

    router ══(RoutePlan, MAF fan-out: lanes run in parallel)══▶ lane_mof      ══╗
                                                             ▶ lane_molecule ══╬══(MAF fan-in)══▶ gather
                                                             ▶ lane_protein  ══╝
- Each item gets the lane whose tool list holds its first tool; a lane runs its items concurrently
  (asyncio, `max_concurrency`) and each item's tools in plan order.
- MAF delivers one executor's messages one at a time, so per-item switch-case routing is sequential;
  lanes make MOFid, PubChem and PDB work overlap.

Add a tool: subclass BaseToolExecutor, implement run_tool(), register, list it under the
workflow's `tools:` (and in a lane), and describe when to use it in the router's skill.
"""

import asyncio
import logging
import time
from abc import abstractmethod
from typing import Any

from agent_framework import Executor, WorkflowContext, handler
from opentelemetry import trace

from hackframe.core.schemas import LaneResult, RoutePlan, ScrapeStart, StepMessage, StepResult, WorkItem
from hackframe.executors.base import ExecutorCore

log = logging.getLogger(__name__)
tracer = trace.get_tracer("hackframe.routing")


class BaseToolExecutor(ExecutorCore):
    """One isolated tool step for a WorkItem."""

    is_tool = True
    description: str = ""  # shown to the router when it plans

    @abstractmethod
    async def run_tool(self, item: WorkItem) -> dict[str, Any]:
        """Return this tool's finding. Use item.find(key) to read earlier tools' outputs."""

    async def step(self, item: WorkItem) -> WorkItem:
        """Run this tool on a copy of the item; on error the item's chain stops."""
        item = item.model_copy(deep=True)
        with tracer.start_as_current_span(f"tool {self.id}") as span:
            span.set_attribute("hackframe.system", item.system)
            try:
                item.findings[self.id] = await self.run_tool(item)
                item.cursor += 1
            except Exception as exc:  # stop this item's chain; gather still gets it
                log.warning("tool %s failed for %s: %s", self.id, item.system, exc)
                item.errors[self.id] = f"{type(exc).__name__}: {exc}"
                item.cursor = len(item.plan)
                span.set_attribute("hackframe.error", item.errors[self.id][:300])
        return item

    @handler
    async def handle(self, item: WorkItem, ctx: WorkflowContext[WorkItem]) -> None:
        await ctx.send_message(await self.step(item))


class BaseRouterExecutor(ExecutorCore):
    """Decides which tools run for which subsystem. `tools` is injected by the workflow builder."""

    is_router = True

    def __init__(self, id: str, **params: Any) -> None:
        super().__init__(id, **params)
        self.tools: dict[str, str] = {}  # tool id -> description
        self.lanes: dict[str, list[str]] = {}  # lane name -> tool ids (lanes pattern; set by the builder)

    def lane_for(self, plan: list[str]) -> str | None:
        return next((name for name, tools in self.lanes.items() if plan and plan[0] in tools), None)

    @abstractmethod
    async def plan(self, message: StepMessage) -> tuple[list[WorkItem], dict[str, Any]]:
        """Return (work items with non-empty plans, planner info for the record)."""

    @handler
    async def route(self, message: StepMessage, ctx: WorkflowContext[WorkItem | ScrapeStart | RoutePlan]) -> None:
        failed = [r.step_id for r in message.results if r.status != "ok"]
        if failed:
            planner: dict[str, Any] = {"by": "skipped", "error": f"upstream failed: {', '.join(failed)}"}
            items: list[WorkItem] = []
        else:
            try:
                items, planner = await self.plan(message)
            except Exception as exc:
                log.exception("router %s failed", self.id)
                items, planner = [], {"by": "error", "error": f"{type(exc).__name__}: {exc}"}
        planner["order"] = [i.system for i in items]
        if self.lanes:
            for item in items:
                item.lane = self.lane_for(item.plan)
            planner["lanes"] = {n: [i.system for i in items if i.lane == n] for n in self.lanes}
            if unassigned := [f"{i.system}: {i.plan}" for i in items if i.lane is None]:
                planner["unassigned"] = unassigned  # first tool is in no lane -> not run
                log.warning("[%s] no lane for %s", self.id, unassigned)
            log.info("[%s] lanes: %s", self.id, {n: len(v) for n, v in planner["lanes"].items()})
            await ctx.send_message(RoutePlan(run_id=message.run_id, base=message, planner=planner, items=items))
            return
        await ctx.send_message(ScrapeStart(run_id=message.run_id, expected=len(items), base=message, planner=planner))
        for item in items:
            await ctx.send_message(item)


class GatherExecutor(Executor):
    """Collects every WorkItem of a run (MAF workflow state), then forwards one StepMessage."""

    def __init__(self, id: str = "scrape") -> None:
        super().__init__(id=id)

    def _key(self, run_id: str) -> str:
        return f"{self.id}:{run_id}"

    @handler
    async def start(self, start: ScrapeStart, ctx: WorkflowContext[StepMessage]) -> None:
        state = ctx.get_state(self._key(start.run_id)) or {"items": []}
        state["start"] = start.model_dump(mode="json")
        ctx.set_state(self._key(start.run_id), state)
        await self._maybe_finish(start.run_id, ctx)

    @handler
    async def collect(self, item: WorkItem, ctx: WorkflowContext[StepMessage]) -> None:
        state = ctx.get_state(self._key(item.run_id)) or {"items": []}
        state["items"] = [*state["items"], item.model_dump(mode="json")]
        ctx.set_state(self._key(item.run_id), state)
        await self._maybe_finish(item.run_id, ctx)

    async def _maybe_finish(self, run_id: str, ctx: WorkflowContext[StepMessage]) -> None:
        state = ctx.get_state(self._key(run_id)) or {}
        start = state.get("start")
        if not start or len(state["items"]) < start["expected"]:
            return
        ctx.set_state(self._key(run_id), None)

        items = [WorkItem.model_validate(i) for i in state["items"]]
        result = scrape_result(self.id, start["planner"], items)
        await ctx.send_message(StepMessage.model_validate(start["base"]).with_result(result))


def scrape_result(step_id: str, planner: dict[str, Any], items: list[WorkItem]) -> StepResult:
    """The gather step's output: planner record + one entry per subsystem in planning order."""
    order = {s: i for i, s in enumerate(planner.get("order", []))}
    items = sorted(items, key=lambda i: order.get(i.system, 1e9))
    systems = [
        {
            "system": i.system,
            "label": i.metadata.get("label"),
            "structural_type": i.metadata.get("structural_type"),
            "lane": i.lane,
            "plan": i.plan,
            "reason": i.reason,
            "findings": i.findings,
            "errors": i.errors,
        }
        for i in items
    ]
    result = StepResult(step_id=step_id, executor_type="gather", output={"planner": planner, "systems": systems})
    if planner.get("by") in ("error", "skipped"):
        result.status, result.error = "error", planner.get("error")
    log.info("[%s] gathered %d subsystem(s), planner=%s", step_id, len(items), planner.get("by"))
    return result


class LaneExecutor(Executor):
    """One parallel lane: takes its items from the RoutePlan and runs them concurrently.

    Each item runs its plan in order (e.g. cif_to_smiles -> pubchem -> pubchem_safety) through the tool
    executors' `step()`. Lanes themselves run in parallel (MAF fan-out from the router)."""

    def __init__(self, name: str, tools: dict[str, BaseToolExecutor], max_concurrency: int = 4,
                 tool_timeout: float = 300) -> None:
        super().__init__(id=f"lane_{name}")
        self.name = name
        self.tools = tools
        self.max_concurrency = max(1, max_concurrency)
        self.tool_timeout = tool_timeout

    def timeout_for(self, tool: BaseToolExecutor) -> float:
        """Lane default, or the tool's own `timeout` param + margin (e.g. mofid: 600 s)."""
        own = tool.params.get("timeout") if hasattr(tool, "params") else None
        return max(self.tool_timeout, float(own) + 60) if own else self.tool_timeout

    async def run_item(self, item: WorkItem, sem: asyncio.Semaphore, progress: list[int], total: int) -> WorkItem:
        async with sem:
            while (tool := item.next_tool) is not None:
                if tool not in self.tools:
                    item.errors[tool] = f"tool '{tool}' is not in lane '{self.name}'"
                    break
                limit = self.timeout_for(self.tools[tool])
                started = time.perf_counter()
                try:
                    item = await asyncio.wait_for(self.tools[tool].step(item), timeout=limit)
                except asyncio.TimeoutError:
                    item.errors[tool] = f"TimeoutError: no result after {limit:.0f} s"
                    log.warning("[%s] %s timed out for %s after %.0fs", self.id, tool, item.system, limit)
                    break
                log.info("[%s] %s %s %.1fs%s", self.id, tool, item.system.split("/")[-1],
                         time.perf_counter() - started, " (error)" if tool in item.errors else "")
        progress[0] += 1
        log.info("[%s] %d/%d done: %s", self.id, progress[0], total, item.system)
        return item

    @handler
    async def run(self, plan: RoutePlan, ctx: WorkflowContext[LaneResult]) -> None:
        mine = [i for i in plan.items if i.lane == self.name]
        started = time.perf_counter()
        sem = asyncio.Semaphore(self.max_concurrency)
        with tracer.start_as_current_span(f"lane {self.name}") as span:
            span.set_attribute("hackframe.lane.items", len(mine))
            progress = [0]
            done = list(await asyncio.gather(*(self.run_item(i, sem, progress, len(mine)) for i in mine)))
        seconds = round(time.perf_counter() - started, 2)
        if mine:
            log.info("[%s] %d item(s) in %.1fs", self.id, len(mine), seconds)
        await ctx.send_message(LaneResult(run_id=plan.run_id, lane=self.name, base=plan.base,
                                          planner=plan.planner, items=done, seconds=seconds))


class LaneGatherExecutor(Executor):
    """MAF fan-in target: runs once every lane has finished, merges their items into one StepMessage."""

    def __init__(self, id: str = "scrape") -> None:
        super().__init__(id=id)

    @handler
    async def merge(self, results: list[LaneResult], ctx: WorkflowContext[StepMessage]) -> None:
        first = results[0]
        planner = {**first.planner, "lane_seconds": {r.lane: r.seconds for r in results}}
        items = [i for r in results for i in r.items]
        result = scrape_result(self.id, planner, items)
        await ctx.send_message(first.base.with_result(result))
