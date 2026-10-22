"""Router pattern (agentic switch) on MAF switch-case edges.

    StepMessage ─▶ router ─(WorkItem per subsystem, plan=[tool ids])─▶ tool ─▶ tool ─▶ ... ─▶ gather ─▶ StepMessage
                        └──────────────── ScrapeStart (expected count) ──────────────────────▶ gather

- The router decides each subsystem's tool chain (`WorkItem.plan`).
- MAF switch-case edges send a WorkItem to the executor named by `next_tool`, or to gather when done.
- Each tool appends `findings[tool_id]` and advances the cursor; on error the chain stops.
- gather waits for all items (MAF workflow state), then emits one StepMessage with a
  StepResult(step_id=<gather id>, output={"planner", "systems": [...]}) for the next step.

Add a tool: subclass BaseToolExecutor, implement run_tool(), register, list it under the
workflow's `tools:`, and describe when to use it in the router's skill.
"""

import logging
from abc import abstractmethod
from typing import Any

from agent_framework import Executor, WorkflowContext, handler

from hackframe.core.schemas import ScrapeStart, StepMessage, StepResult, WorkItem
from hackframe.executors.base import ExecutorCore

log = logging.getLogger(__name__)


class BaseToolExecutor(ExecutorCore):
    """One isolated tool step for a WorkItem."""

    is_tool = True
    description: str = ""  # shown to the router when it plans

    @abstractmethod
    async def run_tool(self, item: WorkItem) -> dict[str, Any]:
        """Return this tool's finding. Use item.find(key) to read earlier tools' outputs."""

    @handler
    async def handle(self, item: WorkItem, ctx: WorkflowContext[WorkItem]) -> None:
        item = item.model_copy(deep=True)
        try:
            item.findings[self.id] = await self.run_tool(item)
            item.cursor += 1
        except Exception as exc:  # stop this item's chain; gather still gets it
            log.warning("tool %s failed for %s: %s", self.id, item.system, exc)
            item.errors[self.id] = f"{type(exc).__name__}: {exc}"
            item.cursor = len(item.plan)
        await ctx.send_message(item)


class BaseRouterExecutor(ExecutorCore):
    """Decides which tools run for which subsystem. `tools` is injected by the workflow builder."""

    is_router = True

    def __init__(self, id: str, **params: Any) -> None:
        super().__init__(id, **params)
        self.tools: dict[str, str] = {}  # tool id -> description

    @abstractmethod
    async def plan(self, message: StepMessage) -> tuple[list[WorkItem], dict[str, Any]]:
        """Return (work items with non-empty plans, planner info for the record)."""

    @handler
    async def route(self, message: StepMessage, ctx: WorkflowContext[WorkItem | ScrapeStart]) -> None:
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

        planner = start["planner"]
        order = {s: i for i, s in enumerate(planner.get("order", []))}
        items = sorted((WorkItem.model_validate(i) for i in state["items"]), key=lambda i: order.get(i.system, 1e9))
        systems = [
            {
                "system": i.system,
                "label": i.metadata.get("label"),
                "structural_type": i.metadata.get("structural_type"),
                "plan": i.plan,
                "reason": i.reason,
                "findings": i.findings,
                "errors": i.errors,
            }
            for i in items
        ]
        result = StepResult(step_id=self.id, executor_type="gather", output={"planner": planner, "systems": systems})
        if planner.get("by") in ("error", "skipped"):
            result.status, result.error = "error", planner.get("error")
        log.info("[%s] gathered %d subsystem(s), planner=%s", self.id, len(items), planner.get("by"))
        await ctx.send_message(StepMessage.model_validate(start["base"]).with_result(result))
