"""Structural executors wrapped around every workflow: intake, merge (fan-in), finalize."""

from typing import Never

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
    """Workflow exit: StepMessage -> FinalOutput (yielded as workflow output)."""

    def __init__(self, id: str = "finalize") -> None:
        super().__init__(id=id)

    @handler
    async def finish(self, message: StepMessage, ctx: WorkflowContext[Never, FinalOutput]) -> None:
        ok = all(r.status == "ok" for r in message.results)
        summary = " | ".join(f"{r.step_id}:{r.status}" for r in message.results)
        await ctx.yield_output(
            FinalOutput(run_id=message.run_id, task=message.task.task, steps=message.results, ok=ok, summary=summary)
        )
