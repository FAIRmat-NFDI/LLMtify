"""Structured I/O contracts shared by every executor.

Flow:  TaskInput --intake--> StepMessage --steps...--> StepMessage --finalize--> FinalOutput

Every step executor consumes a StepMessage and emits a StepMessage with one more
StepResult appended, so executors compose freely in sequential, fan-out or graph topologies.
"""

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TaskInput(BaseModel):
    """Workflow entry payload (DevUI renders a form from this schema)."""

    task: str = Field(..., description="What the workflow should do")
    params: dict[str, Any] = Field(default_factory=dict, description="Free-form parameters")


class AgentAnswer(BaseModel):
    """response_format for LLM-backed steps (structured output)."""

    answer: str = Field(..., description="Main answer")
    confidence: float = Field(..., ge=0, le=1, description="Self-reported confidence 0..1")
    notes: list[str] = Field(default_factory=list, description="Caveats or follow-ups")


class StepResult(BaseModel):
    step_id: str
    executor_type: str
    status: str = "ok"  # ok | error | skipped
    output: dict[str, Any] = Field(default_factory=dict)
    model: str | None = None
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None
    error: str | None = None


class StepMessage(BaseModel):
    """Envelope passed along every edge."""

    run_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    task: TaskInput
    results: list[StepResult] = Field(default_factory=list)

    def with_result(self, result: StepResult) -> "StepMessage":
        return self.model_copy(update={"results": [*self.results, result]})

    @classmethod
    def merge(cls, messages: list["StepMessage"]) -> "StepMessage":
        """Fan-in: union of results, de-duplicated by step_id, order preserved."""
        if not messages:
            raise ValueError("merge() needs at least one message")
        seen: dict[str, StepResult] = {}
        for msg in messages:
            for r in msg.results:
                seen.setdefault(r.step_id, r)
        base = messages[0]
        return cls(run_id=base.run_id, task=base.task, results=list(seen.values()))


class FinalOutput(BaseModel):
    run_id: str
    task: str
    steps: list[StepResult]
    ok: bool
    summary: str
