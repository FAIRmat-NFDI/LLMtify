"""LLM-backed step: wraps a configured Agent and returns structured output."""

import json
from typing import Any

from agent_framework import Agent

from hackframe.core.config import StepSpec
from hackframe.core.schemas import AgentAnswer, StepMessage
from hackframe.executors.base import EXECUTORS, BaseStepExecutor, BuildContext, build_agent


@EXECUTORS.register("agent")
class AgentStepExecutor(BaseStepExecutor):
    executor_type = "agent"
    response_format: type = AgentAnswer  # override in subclasses for other schemas

    def __init__(self, id: str, agent: Agent, model: str | None = None, **params: Any) -> None:
        super().__init__(id, **params)
        self.agent = agent
        self._model = model

    @classmethod
    def from_spec(cls, step: StepSpec, build: BuildContext) -> "AgentStepExecutor":
        params = step.params
        agent, model = build_agent(step, build, params)
        obj = cls(step.id, agent=agent, model=model, **params)
        obj.bind(build)
        return obj

    def model_name(self) -> str | None:
        return self._model

    def build_prompt(self, message: StepMessage) -> str:
        prior = [r.model_dump(mode="json", include={"step_id", "status", "output"}) for r in message.results]
        return (
            f"TASK:\n{message.task.task}\n\n"
            f"PARAMS:\n{json.dumps(message.task.params)}\n\n"
            f"PRIOR STEP RESULTS:\n{json.dumps(prior, indent=2)}"
        )

    async def process(self, message: StepMessage) -> dict[str, Any]:
        response = await self.agent.run(
            self.build_prompt(message), options={"response_format": self.response_format}
        )
        if response.value is None:
            raise ValueError(f"no structured output; raw text: {response.text[:300]}")
        return response.value.model_dump(mode="json")
