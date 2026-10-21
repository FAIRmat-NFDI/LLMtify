"""Executor base classes.

Write a new step = subclass BaseStepExecutor, implement `process`, register it:

    @EXECUTORS.register("my_step")
    class MyStep(BaseStepExecutor):
        async def process(self, message: StepMessage) -> dict:
            return {"value": 42}

Then use `type: my_step` in config/workflows.yaml. The base class handles
structured I/O, timing, error capture and forwarding to the next edge.
"""

import logging
from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from agent_framework import Executor, WorkflowContext, handler

from hackframe.core.config import StepSpec
from hackframe.core.registry import Registry
from hackframe.core.schemas import StepMessage, StepResult

if TYPE_CHECKING:
    from hackframe.agents.factory import AgentFactory

log = logging.getLogger(__name__)

EXECUTORS: Registry[type["BaseStepExecutor"]] = Registry("executor")


@dataclass
class BuildContext:
    """Dependencies an executor may need at construction time."""

    agents: "AgentFactory | None" = None


class BaseStepExecutor(Executor):
    executor_type: str = "base"

    def __init__(self, id: str, **params: Any) -> None:
        super().__init__(id=id)
        self.params = params

    @classmethod
    def from_spec(cls, step: StepSpec, build: BuildContext) -> "BaseStepExecutor":
        return cls(step.id, **step.params)

    @abstractmethod
    async def process(self, message: StepMessage) -> dict[str, Any]:
        """Return this step's output payload. Raise to mark the step as failed."""

    def model_name(self) -> str | None:
        return None

    @handler
    async def handle(self, message: StepMessage, ctx: WorkflowContext[StepMessage]) -> None:
        result = StepResult(step_id=self.id, executor_type=self.executor_type, model=self.model_name())
        try:
            result.output = await self.process(message)
        except Exception as exc:  # keep the graph alive; failure is recorded, not raised
            log.exception("step %s failed", self.id)
            result.status, result.error = "error", f"{type(exc).__name__}: {exc}"
        result.finished_at = datetime.now(timezone.utc)
        await ctx.send_message(message.with_result(result))
