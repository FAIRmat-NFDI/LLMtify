"""EXECUTOR_TOOL_FINDER: the agentic switch. An LLM grounded on the `tool-finder` skill decides,
per subsystem, which tool executors to run (and in which order).

workflows.yaml:
  - {id: tool_finder, type: tool_finder, agent: tool_finder, skill: tool-finder, source: compact}

If the LLM call fails, the skill's `metadata:` gives a deterministic fallback:
  fallback_structural_types: molecule          # which subsystems
  fallback_tools: cif_to_smiles,pubchem        # which chain
"""

import json
import logging
from typing import Any

from agent_framework import Agent
from pydantic import BaseModel, Field

from hackframe.core.config import StepSpec
from hackframe.core.schemas import StepMessage, WorkItem
from hackframe.executors.base import EXECUTORS, BuildContext, build_agent
from hackframe.executors.routing import BaseRouterExecutor

log = logging.getLogger(__name__)


class SystemPlan(BaseModel):
    system: str = Field(..., description="Subsystem key exactly as given")
    tools: list[str] = Field(..., description="Tool ids in execution order; empty = skip this subsystem")
    reason: str = Field(..., description="One short sentence")


class ToolPlan(BaseModel):
    plans: list[SystemPlan]


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


@EXECUTORS.register("tool_finder")
class ToolFinderExecutor(BaseRouterExecutor):
    executor_type = "tool_finder"

    def __init__(self, id: str, agent: Agent, model: str | None = None, **params: Any) -> None:
        super().__init__(id, **params)
        self.agent = agent
        self._model = model
        self.skill = params.get("skill", "tool-finder")
        self.source = params.get("source", "compact")

    @classmethod
    def from_spec(cls, step: StepSpec, build: BuildContext) -> "ToolFinderExecutor":
        params = step.params
        agent, model = build_agent(step, build, params)
        obj = cls(step.id, agent=agent, model=model, **params)
        obj.bind(build)
        return obj

    def model_name(self) -> str | None:
        return self._model

    # ------------------------------------------------------------------ planning
    @staticmethod
    def candidates(small: dict[str, Any]) -> list[dict[str, Any]]:
        """Compact, LLM-friendly view of every subsystem."""
        system_cif = small.get("system_cif", {})
        out = []
        for key, value in (small.get("systems") or {}).items():
            meta = (value or {}).get("metadata", {}) if isinstance(value, dict) else {}
            out.append(
                {
                    "system": key,
                    "label": meta.get("label"),
                    "structural_type": meta.get("structural_type"),
                    "system_relation": meta.get("system_relation"),
                    "method": meta.get("method"),
                    "parent_system": meta.get("parent_system"),
                    "child_systems": meta.get("child_systems"),
                    "has_cif": key in system_cif,
                }
            )
        return out

    async def ask_llm(self, systems: list[dict[str, Any]]) -> ToolPlan:
        prompt = (
            "Plan the tool chain for each subsystem following the tool-finder skill.\n\n"
            f"AVAILABLE TOOLS (use these ids only):\n{json.dumps(self.tools, indent=2)}\n\n"
            f"SUBSYSTEMS:\n{json.dumps(systems, indent=2)}"
        )
        response = await self.agent.run(prompt, options={"response_format": ToolPlan})
        if response.value is None:
            raise ValueError(f"no structured plan from LLM; raw: {response.text[:300]}")
        return response.value

    async def fallback(self, systems: list[dict[str, Any]]) -> ToolPlan:
        meta = await self.skills.metadata(self.skill) if self.skills else {}
        types = set(_csv(meta.get("fallback_structural_types")))
        tools = _csv(meta.get("fallback_tools"))
        return ToolPlan(
            plans=[
                SystemPlan(system=s["system"], tools=tools, reason="fallback rule from skill metadata")
                for s in systems
                if s["has_cif"] and s["structural_type"] in types
            ]
        )

    async def plan(self, message: StepMessage) -> tuple[list[WorkItem], dict[str, Any]]:
        small = message.output_of(self.source)
        systems = self.candidates(small)
        planner: dict[str, Any] = {"by": "llm", "model": self._model}
        try:
            tool_plan = await self.ask_llm(systems)
        except Exception as exc:
            log.warning("tool_finder LLM failed (%s); using skill fallback", exc)
            tool_plan = await self.fallback(systems)
            planner = {"by": "fallback", "llm_error": f"{type(exc).__name__}: {exc}"[:300]}

        by_key = {s["system"]: s for s in systems}
        meta = {k: (v or {}).get("metadata", {}) for k, v in (small.get("systems") or {}).items()}
        items, dropped, seen = [], [], set()
        for p in tool_plan.plans:
            if p.system not in by_key or p.system in seen:
                dropped.append({"system": p.system, "why": "unknown or duplicate system"})
                continue
            seen.add(p.system)
            tools = [t for t in p.tools if t in self.tools]
            if unknown := [t for t in p.tools if t not in self.tools]:
                dropped.append({"system": p.system, "why": f"unknown tools {unknown}"})
            if not tools:
                continue
            items.append(
                WorkItem(
                    run_id=message.run_id,
                    artifact_id=message.artifact_id,
                    system=p.system,
                    metadata=meta.get(p.system, {}),
                    cif_key=small.get("system_cif", {}).get(p.system),
                    plan=tools,
                    reason=p.reason,
                )
            )
        if dropped:
            planner["dropped"] = dropped
        log.info("[%s] %s plan: %s", self.id, planner["by"], {i.system: i.plan for i in items})
        return items, planner
