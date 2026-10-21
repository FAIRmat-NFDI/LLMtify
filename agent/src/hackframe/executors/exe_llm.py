"""exe_llm - sanity-check executor: is the LLM reachable and does structured output work?

No tools, skills or MCP. Calls one model profile directly and reports:
  reply, latency, and (optionally) whether response_format JSON parsing works.

workflows.yaml:
  - {id: check, type: exe_llm, profile: hu, structured: true}
"""

import time
from typing import Any

from agent_framework import Agent

from hackframe.core.config import StepSpec
from hackframe.core.schemas import AgentAnswer, StepMessage
from hackframe.executors.base import EXECUTORS, BaseStepExecutor, BuildContext


@EXECUTORS.register("exe_llm")
class ExeLLM(BaseStepExecutor):
    executor_type = "exe_llm"

    def __init__(self, id: str, agent: Agent, model: str, structured: bool = True, **params: Any) -> None:
        super().__init__(id, **params)
        self.agent = agent
        self._model = model
        self.structured = structured

    @classmethod
    def from_spec(cls, step: StepSpec, build: BuildContext) -> "ExeLLM":
        if build.agents is None:
            raise ValueError("exe_llm needs the model factory")
        params = step.params
        profile = params.pop("profile", None)  # None -> default_profile
        models = build.agents.models
        agent = Agent(
            client=models.client(profile),
            default_options=models.default_options(profile) or None,
            name=f"{step.id}_probe",
            instructions="You are a test probe. Answer briefly.",
        )
        return cls(step.id, agent=agent, model=models.profile(profile).model, **params)

    def model_name(self) -> str | None:
        return self._model

    async def process(self, message: StepMessage) -> dict[str, Any]:
        out: dict[str, Any] = {"model": self._model}

        t0 = time.perf_counter()
        resp = await self.agent.run(message.task.task)
        out["text_ok"] = bool(resp.text.strip())
        out["text_reply"] = resp.text.strip()[:500]
        out["text_latency_s"] = round(time.perf_counter() - t0, 2)
        print(f"[{self.id}] {self._model} text: {out['text_reply'][:120]!r} ({out['text_latency_s']}s)")

        if self.structured:
            t0 = time.perf_counter()
            try:
                sresp = await self.agent.run(message.task.task, options={"response_format": AgentAnswer})
                out["structured_ok"] = sresp.value is not None
                out["structured_reply"] = sresp.value.model_dump() if sresp.value else sresp.text[:300]
            except Exception as exc:  # report, don't fail the probe
                out["structured_ok"] = False
                out["structured_error"] = f"{type(exc).__name__}: {exc}"[:300]
            out["structured_latency_s"] = round(time.perf_counter() - t0, 2)
            print(f"[{self.id}] {self._model} structured_ok={out['structured_ok']} ({out['structured_latency_s']}s)")

        if not out["text_ok"]:
            raise RuntimeError("empty reply from model")
        return out
