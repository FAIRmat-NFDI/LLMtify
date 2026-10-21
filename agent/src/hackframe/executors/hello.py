"""Placeholder executor - no LLM. Copy this file to start a new step type."""

import asyncio
from typing import Any

from hackframe.core.schemas import StepMessage
from hackframe.executors.base import EXECUTORS, BaseStepExecutor


@EXECUTORS.register("hello")
class HelloExecutor(BaseStepExecutor):
    executor_type = "hello"

    async def process(self, message: StepMessage) -> dict[str, Any]:
        delay = float(self.params.get("delay", 0))
        if delay:
            await asyncio.sleep(delay)
        seen = [r.step_id for r in message.results]
        print(f"[{self.id}] hello world | task={message.task.task!r} | upstream={seen}")
        return {"greeting": f"hello from {self.id}", "upstream": seen}
