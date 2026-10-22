"""Executor base classes.

Middle step = subclass BaseStepExecutor, implement `process`, register it:

    @EXECUTORS.register("my_step")
    class MyStep(BaseStepExecutor):
        async def process(self, message: StepMessage) -> dict:
            prev = message.output_of("preprocess")   # upstream step output
            return {"value": 42}

First step with its own typed input = subclass BaseEntryExecutor (see preprocess.py).

Then use `type: my_step` in config/workflows.yaml. The base classes handle
structured I/O, timing, error capture and forwarding to the next edge.
"""

import logging
from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from agent_framework import Executor, WorkflowContext, handler
from pydantic import BaseModel

from matgentify.core.config import StepSpec
from matgentify.core.registry import Registry
from matgentify.core.schemas import StepMessage, StepResult, TaskInput

if TYPE_CHECKING:
    from matgentify.agents.factory import AgentFactory
    from matgentify.core.artifacts import ArtifactStore
    from matgentify.settings import Settings
    from matgentify.skills.loader import SkillsLoader

log = logging.getLogger(__name__)

EXECUTORS: Registry[type["ExecutorCore"]] = Registry("executor")


@dataclass
class BuildContext:
    """Dependencies an executor may need at construction time."""

    agents: "AgentFactory | None" = None
    settings: "Settings | None" = None
    artifacts: "ArtifactStore | None" = None
    skills: "SkillsLoader | None" = None


class ExecutorCore(Executor):
    """Config + shared services for every registered executor (no handler, no contract)."""

    executor_type: str = "base"
    is_entry: bool = False  # first step with its own input type
    is_router: bool = False  # emits WorkItems (router pattern)
    is_tool: bool = False  # handles WorkItems (router pattern)

    def __init__(self, id: str, **params: Any) -> None:
        super().__init__(id=id)
        self.params = params
        self.artifacts: "ArtifactStore | None" = None  # temp/<uuid>.json access
        self.skills: "SkillsLoader | None" = None

    @classmethod
    def from_spec(cls, step: StepSpec, build: BuildContext) -> "ExecutorCore":
        obj = cls(step.id, **step.params)
        obj.bind(build)
        return obj

    def bind(self, build: BuildContext) -> None:
        self.artifacts = build.artifacts
        self.skills = build.skills

    def model_name(self) -> str | None:
        return None


def build_agent(step: StepSpec, build: BuildContext, params: dict[str, Any]) -> tuple[Any, str]:
    """Create the agents.yaml agent named by `agent:` in a step. Returns (agent, model id)."""
    name = params.pop("agent", None)
    if not name or build.agents is None:
        raise ValueError(f"step '{step.id}' needs `agent: <name>` from agents.yaml")
    return build.agents.create(name), build.agents.model_name(name)


class StepCore(ExecutorCore):
    """Shared step logic (no handler): run `process`, record a StepResult, forward."""

    @abstractmethod
    async def process(self, message: StepMessage) -> dict[str, Any]:
        """Return this step's output payload. Raise to mark the step as failed."""

    def _result(self) -> StepResult:
        return StepResult(step_id=self.id, executor_type=self.executor_type, model=self.model_name())

    @staticmethod
    def _fail(result: StepResult, exc: Exception) -> None:
        result.status, result.error = "error", f"{type(exc).__name__}: {exc}"
        result.finished_at = datetime.now(timezone.utc)

    async def run_step(self, message: StepMessage, ctx: WorkflowContext[StepMessage]) -> None:
        result = self._result()
        failed = [r.step_id for r in message.results if r.status != "ok"]
        if failed:  # don't run on top of a failed upstream step; pass the failure through
            result.status, result.error = "skipped", f"upstream failed: {', '.join(failed)}"
            result.finished_at = datetime.now(timezone.utc)
            log.warning("step %s skipped (upstream failed: %s)", self.id, failed)
            await ctx.send_message(message.with_result(result))
            return
        try:
            result.output = await self.process(message)
            result.finished_at = datetime.now(timezone.utc)
        except Exception as exc:  # keep the graph alive; failure is recorded, not raised
            log.exception("step %s failed", self.id)
            self._fail(result, exc)
        await ctx.send_message(message.with_result(result))


class BaseStepExecutor(StepCore):
    """Middle step: consumes and emits StepMessage."""

    @handler
    async def handle(self, message: StepMessage, ctx: WorkflowContext[StepMessage]) -> None:
        await self.run_step(message, ctx)


class BaseEntryExecutor(StepCore):
    """First step with its own typed input (becomes the workflow start executor;
    DevUI renders `input_model` as the input form).

    Subclasses declare the handler with the concrete input type and call `start()`:

        class MyEntry(BaseEntryExecutor):
            input_model = MyRequest

            @handler
            async def receive(self, request: MyRequest, ctx: WorkflowContext[StepMessage]) -> None:
                await self.start(request, ctx)

            async def ingest(self, request: MyRequest) -> TaskInput: ...   # may do IO
            async def process(self, message: StepMessage) -> dict: ...
    """

    is_entry = True
    input_model: type[BaseModel] = BaseModel

    @abstractmethod
    async def ingest(self, request: Any) -> TaskInput:
        """Persist/convert the entry input; return the TaskInput carried by every StepMessage."""

    async def start(self, request: BaseModel, ctx: WorkflowContext[StepMessage]) -> None:
        try:
            task = await self.ingest(request)
        except Exception as exc:  # record ingest failure as this step's error
            log.exception("step %s ingest failed", self.id)
            result = self._result()
            self._fail(result, exc)
            await ctx.send_message(StepMessage(task=TaskInput(task="ingest failed")).with_result(result))
            return
        await self.run_step(StepMessage(task=task), ctx)
