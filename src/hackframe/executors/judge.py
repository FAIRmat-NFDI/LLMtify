"""EXTIR_JUDGE: gathers every subsystem finding and writes the natural-language summary.
An LLM grounded on the `judge` skill; only facts present in the evidence may be used.

workflows.yaml:
  - {id: judge, type: judge, agent: judge, source: scrape, structure: compact}

If the LLM call fails, a plain template summary is returned (generated_by: "fallback").
"""

import json
import logging
from typing import Any

from agent_framework import Agent
from pydantic import BaseModel, Field

from hackframe.core.config import StepSpec
from hackframe.core.schemas import StepMessage
from hackframe.executors.base import EXECUTORS, BaseStepExecutor, BuildContext, build_agent

log = logging.getLogger(__name__)

_COMPOUND_KEYS = (
    "cid", "name", "iupac_name", "formula", "molecular_weight",
    "ghs_pictograms", "ghs_signal", "ghs_hazard_statements", "industry_uses", "vendor_count",
)


class MaterialSummary(BaseModel):
    summary: str = Field(..., description="The natural-language summary paragraph")
    key_facts: list[str] = Field(..., description="Short facts used, each traceable to the evidence")


@EXECUTORS.register("judge")
class JudgeExecutor(BaseStepExecutor):
    executor_type = "judge"

    def __init__(self, id: str, agent: Agent, model: str | None = None, **params: Any) -> None:
        super().__init__(id, **params)
        self.agent = agent
        self._model = model
        self.source = params.get("source", "scrape")
        self.structure = params.get("structure", "compact")

    @classmethod
    def from_spec(cls, step: StepSpec, build: BuildContext) -> "JudgeExecutor":
        params = step.params
        agent, model = build_agent(step, build, params)
        obj = cls(step.id, agent=agent, model=model, **params)
        obj.bind(build)
        return obj

    def model_name(self) -> str | None:
        return self._model

    # ------------------------------------------------------------------ evidence
    def evidence(self, message: StepMessage) -> dict[str, Any]:
        small = message.output_of(self.structure)
        scrape = message.output_of(self.source)
        overview = [
            {
                "system": k,
                "label": (v or {}).get("metadata", {}).get("label"),
                "structural_type": (v or {}).get("metadata", {}).get("structural_type"),
                "system_relation": (v or {}).get("metadata", {}).get("system_relation"),
            }
            for k, v in (small.get("systems") or {}).items()
        ]
        components = []
        for s in scrape.get("systems", []):
            findings = [f for f in s["findings"].values() if isinstance(f, dict)]
            merged: dict[int, dict[str, Any]] = {}  # one entry per CID, facts merged across tools
            for finding in findings:
                for c in finding.get("compounds", []):
                    if c.get("found") and c.get("cid"):
                        entry = merged.setdefault(c["cid"], {"cid": c["cid"]})
                        entry.update({k: c[k] for k in _COMPOUND_KEYS if c.get(k) not in (None, [], "")})
            fragments = next((f.get("heavy_fragments") or f["fragments"] for f in findings if "fragments" in f), [])
            components.append(
                {
                    "system": s["system"],
                    "label": s["label"],
                    "structural_type": s["structural_type"],
                    "smiles_fragments": fragments[:3],
                    "compounds": list(merged.values()),
                    "errors": s["errors"],
                }
            )
        return {"systems": overview, "components": components}

    async def ask_llm(self, evidence: dict[str, Any]) -> MaterialSummary:
        prompt = (
            "Write the material summary following the judge skill. Use ONLY facts in this evidence.\n\n"
            f"EVIDENCE:\n{json.dumps(evidence, indent=2)}"
        )
        response = await self.agent.run(prompt, options={"response_format": MaterialSummary})
        if response.value is None:
            raise ValueError(f"no structured summary from LLM; raw: {response.text[:300]}")
        return response.value

    @staticmethod
    def template(evidence: dict[str, Any]) -> MaterialSummary:
        """Deterministic summary used when the LLM is unavailable."""
        framework = next(
            (s["label"] for s in evidence["systems"] if s.get("structural_type") == "bulk" and s.get("label")),
            None,
        )
        compounds = {c["cid"]: c for comp in evidence["components"] for c in comp["compounds"]}.values()
        if not compounds:
            return MaterialSummary(summary="No component could be identified in PubChem.", key_facts=[])
        names = [f"{c.get('name', 'unknown')} (PubChem ID {c['cid']})" for c in compounds]
        head = f"The given system is a {framework} containing " if framework else "The given system contains "
        sentences = [head + (", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]) + "."]
        facts = list(names)
        for c in compounds:
            bits = []
            if c.get("industry_uses"):
                bits.append(f"industry use: {', '.join(c['industry_uses'])}")
            if c.get("vendor_count"):
                bits.append(f"{c['vendor_count']} vendors")
            if c.get("ghs_pictograms"):
                bits.append(f"GHS: {', '.join(c['ghs_pictograms']).lower()}")
            if bits:
                sentences.append(f"{c.get('name', c['cid'])}: {'; '.join(bits)}.")
                facts += bits
        return MaterialSummary(summary=" ".join(sentences), key_facts=facts)

    async def process(self, message: StepMessage) -> dict[str, Any]:
        evidence = self.evidence(message)
        try:
            result, by = await self.ask_llm(evidence), "llm"
        except Exception as exc:
            log.warning("judge LLM failed (%s); using template", exc)
            result, by = self.template(evidence), "fallback"
        return {**result.model_dump(), "generated_by": by, "evidence": evidence}
