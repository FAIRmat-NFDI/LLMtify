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

    @property
    def artifact_id(self) -> str:
        """UUID of temp/<artifact_id>.json written by the entry step (preprocess)."""
        aid = self.task.params.get("artifact_id")
        if not aid:
            raise KeyError("no artifact_id on this run (did preprocess run first?)")
        return aid

    def output_of(self, step_id: str) -> dict[str, Any]:
        """Output of an upstream step (raises if it is missing or failed)."""
        for r in self.results:
            if r.step_id == step_id:
                if r.status != "ok":
                    raise RuntimeError(f"upstream step '{step_id}' failed: {r.error}")
                return r.output
        raise KeyError(f"no result from step '{step_id}' (have: {[r.step_id for r in self.results]})")

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


# ---------------------------------------------------------------- scraper stage (router pattern)
class WorkItem(BaseModel):
    """One subsystem travelling through its tool chain: router -> tool -> tool -> ... -> gather.

    MAF switch-case edges route it by `next_tool`; each tool appends its finding and advances `cursor`.
    """

    run_id: str
    artifact_id: str
    system: str  # e.g. "results/material/topology/3"
    metadata: dict[str, Any] = Field(default_factory=dict)
    cif_key: str | None = None  # e.g. "results/material/topology/3.cif" (None if the input has no CIF)
    plan: list[str] = Field(default_factory=list)  # tool executor ids, in order
    reason: str = ""  # why the router chose this plan
    cursor: int = 0
    findings: dict[str, Any] = Field(default_factory=dict)  # tool id -> output
    errors: dict[str, str] = Field(default_factory=dict)  # tool id -> error

    @property
    def next_tool(self) -> str | None:
        return self.plan[self.cursor] if self.cursor < len(self.plan) else None

    def find(self, key: str) -> Any:
        """First value of `key` in any upstream finding (decouples tools from each other's ids)."""
        for finding in self.findings.values():
            if isinstance(finding, dict) and key in finding:
                return finding[key]
        return None


class ScrapeStart(BaseModel):
    """Router -> gather: how many WorkItems to wait for, plus the upstream StepMessage."""

    run_id: str
    expected: int
    base: StepMessage
    planner: dict[str, Any] = Field(default_factory=dict)  # {"by": "llm"|"fallback"|"skipped", ...}
