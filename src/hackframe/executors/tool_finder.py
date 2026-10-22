"""EXECUTOR_TOOL_FINDER: the agentic switch. An LLM grounded on the `tool-finder` skill (plus the
domain skills mof-analysis, protein-chain-sequence, generic-system-analysis) first judges what the
input is (material_class: mof | protein | molecular | other), then decides per subsystem which tool
executors to run and in which order.

workflows.yaml:
  - {id: tool_finder, type: tool_finder, agent: tool_finder, skill: tool-finder, source: compact}

Strict routes (skill metadata, applied after the class is judged, LLM or fallback):
  route_mof_tools      (mofid)                  -> root structure only, then judge (no CIF->SMILES / PubChem)
  route_protein_tools  (cif_to_seq,pdb_lookup)  -> chain subsystems only, then judge (no CIF->SMILES / PubChem)
If the LLM call fails, the class comes from labels (fallback_mof_labels, fallback_protein_labels);
molecular/other: fallback_structural_types / fallback_tools on each molecule subsystem.
"""

import json
import logging
from typing import Any

from agent_framework import Agent
from pydantic import BaseModel, Field

from hackframe.core.config import StepSpec
from hackframe.core.material import system_meta
from hackframe.core.schemas import StepMessage, WorkItem
from hackframe.executors.base import EXECUTORS, BuildContext, build_agent
from hackframe.executors.routing import BaseRouterExecutor

log = logging.getLogger(__name__)


class SystemPlan(BaseModel):
    system: str = Field(..., description="Subsystem key exactly as given")
    tools: list[str] = Field(..., description="Tool ids in execution order; empty = skip this subsystem")
    reason: str = Field(..., description="One short sentence")


class ToolPlan(BaseModel):
    material_class: str = Field(..., description="mof | protein | molecular | other")
    class_reason: str = Field(..., description="One sentence: why this class")
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
            meta = system_meta(value)
            out.append(
                {
                    "system": key,
                    "label": meta.get("label"),
                    "structural_type": meta.get("structural_type"),
                    "system_relation": meta.get("system_relation"),
                    "method": meta.get("method"),
                    "parent_system": meta.get("parent_system"),
                    "child_systems": meta.get("child_systems"),
                    "formula": meta.get("chemical_formula_hill"),
                    "elements": meta.get("elements"),
                    "n_atoms": meta.get("n_atoms"),
                    "has_cif": key in system_cif,
                }
            )
        return out

    async def ask_llm(self, systems: list[dict[str, Any]]) -> ToolPlan:
        prompt = (
            "First judge what this input is (material_class), then plan the tool chain for each subsystem, "
            "following the tool-finder skill and the domain skills.\n\n"
            f"AVAILABLE TOOLS (use these ids only):\n{json.dumps(self.tools, indent=2)}\n\n"
            f"SUBSYSTEMS:\n{json.dumps(systems, indent=2)}"
        )
        response = await self.agent.run(prompt, options={"response_format": ToolPlan})
        if response.value is None:
            raise ValueError(f"no structured plan from LLM; raw: {response.text[:300]}")
        return response.value

    async def fallback(self, systems: list[dict[str, Any]]) -> ToolPlan:
        """Label-based class decision from the skill metadata (used when the LLM is unavailable).
        MOF/protein routes are filled in by `enforce_route`; molecular/other plan every molecule subsystem."""
        meta = await self.skills.metadata(self.skill) if self.skills else {}
        mof_labels = {x.lower() for x in _csv(meta.get("fallback_mof_labels"))}
        protein_labels = {x.lower() for x in _csv(meta.get("fallback_protein_labels"))}
        types = set(_csv(meta.get("fallback_structural_types")))
        label = lambda s: (s.get("label") or "").lower()  # noqa: E731

        if any(label(s) in mof_labels for s in systems):
            return ToolPlan(material_class="mof", class_reason="fallback: MOF label", plans=[])
        protein_systems = [s for s in systems if s["has_cif"] and any(p in label(s) for p in protein_labels)]
        if protein_systems:
            return ToolPlan(material_class="protein", class_reason="fallback: protein label", plans=[
                SystemPlan(system=s["system"], tools=["cif_to_seq"], reason="fallback: protein label")
                for s in protein_systems])
        material_class = "molecular" if any(s["structural_type"] in types for s in systems) else "other"
        plans = [
            SystemPlan(system=s["system"], tools=_csv(meta.get("fallback_tools")), reason="fallback: molecule subsystem")
            for s in systems if s["has_cif"] and s["structural_type"] in types
        ]
        return ToolPlan(material_class=material_class, class_reason="fallback rules from skill metadata", plans=plans)

    async def enforce_route(self, tool_plan: ToolPlan, systems: list[dict[str, Any]]) -> ToolPlan:
        """Strict routes per class (skill metadata `route_<class>_tools`): once the class is judged,
        the class decides the tools, not the per-subsystem plan.
          mof     -> only the root (periodic) structure, tools route_mof_tools (mofid) -> judge
          protein -> only the chain subsystems, tools route_protein_tools (cif_to_seq, pdb_lookup) -> judge
        Other classes keep the LLM's plan."""
        meta = await self.skills.metadata(self.skill) if self.skills else {}
        cls = (tool_plan.material_class or "").strip().lower()
        tools = _csv(meta.get(f"route_{cls}_tools"))
        if not tools:
            return tool_plan
        with_cif = [s for s in systems if s["has_cif"]]
        root = next((s for s in with_cif if s["system_relation"] == "root"), None)
        if cls == "mof":
            targets = [root] if root else []
            reason = "MOF -> MOFid on the periodic root structure"
        else:  # protein: the chains the LLM pointed at (any plan with tools), else the root
            planned = {p.system for p in tool_plan.plans if p.tools}
            targets = [s for s in with_cif if s["system"] in planned] or ([root] if root else with_cif[:1])
            reason = "protein chain -> sequence -> PDB"
        return ToolPlan(material_class=cls, class_reason=tool_plan.class_reason,
                        plans=[SystemPlan(system=t["system"], tools=tools, reason=reason) for t in targets])

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
        tool_plan = await self.enforce_route(tool_plan, systems)
        planner["material_class"] = tool_plan.material_class
        planner["class_reason"] = tool_plan.class_reason

        by_key = {s["system"]: s for s in systems}
        meta = {k: system_meta(v) for k, v in (small.get("systems") or {}).items()}
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
        log.info("[%s] %s: class=%s plan=%s", self.id, planner["by"], tool_plan.material_class,
                 {i.system: i.plan for i in items})
        return items, planner
