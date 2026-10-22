"""Structural executors wrapped around every workflow: intake, merge (fan-in), finalize."""

from typing import Any, Never

from agent_framework import Executor, WorkflowContext, handler

from hackframe.core.schemas import FinalOutput, StepMessage, TaskInput


class IntakeExecutor(Executor):
    """Workflow entry: TaskInput -> StepMessage."""

    def __init__(self, id: str = "intake") -> None:
        super().__init__(id=id)

    @handler
    async def start(self, task: TaskInput, ctx: WorkflowContext[StepMessage]) -> None:
        await ctx.send_message(StepMessage(task=task))


class MergeExecutor(Executor):
    """Fan-in join: list[StepMessage] -> StepMessage."""

    def __init__(self, id: str = "merge") -> None:
        super().__init__(id=id)

    @handler
    async def join(self, messages: list[StepMessage], ctx: WorkflowContext[StepMessage]) -> None:
        await ctx.send_message(StepMessage.merge(messages))


class FinalizeExecutor(Executor):
    """Workflow exit (yielded as workflow output):
    - default: FinalOutput with every step's result
    - output_step set (workflows.yaml `output: <step id>`): only that step's output dict
      (or only its `output_field`, e.g. a markdown string), or {"ok": false, "error", "summary"} on failure
    """

    def __init__(self, id: str = "finalize", output_step: str | None = None, output_field: str | None = None) -> None:
        super().__init__(id=id)
        self.output_step = output_step
        self.output_field = output_field

    @handler
    async def finish(
        self, message: StepMessage, ctx: WorkflowContext[Never, FinalOutput | dict[str, Any] | str]
    ) -> None:
        ok = all(r.status == "ok" for r in message.results)
        summary = " | ".join(f"{r.step_id}:{r.status}" for r in message.results)

        if self.output_step:
            if ok:
                output = message.output_of(self.output_step)
                await ctx.yield_output(output[self.output_field] if self.output_field else output)
            else:
                errors = [f"{r.step_id}: {r.error}" for r in message.results if r.status == "error"]
                await ctx.yield_output({"ok": False, "error": "; ".join(errors), "summary": summary})
            return

        await ctx.yield_output(
            FinalOutput(run_id=message.run_id, task=message.task.task, steps=message.results, ok=ok, summary=summary)
        )
