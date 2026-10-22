"""EXTIR_JUDGE: gathers every subsystem finding and writes the report:
a natural-language summary + a table describing every subsystem.
An LLM grounded on the `judge` skill; only facts present in the evidence may be used.

workflows.yaml:
  - {id: judge, type: judge, agent: judge, source: scrape, structure: compact}

Step output: {summary, subsystems, key_facts, report (markdown), generated_by, evidence}.
With `output: judge` + `output_field: report` the workflow returns only the markdown report.
If the LLM call fails, a deterministic template is used (generated_by: "fallback").
"""

import json
import logging
from typing import Any

from agent_framework import Agent
from pydantic import BaseModel, Field

from hackframe.core.config import StepSpec
from hackframe.core.material import system_meta
from hackframe.core.schemas import StepMessage
from hackframe.executors.base import EXECUTORS, BaseStepExecutor, BuildContext, build_agent

log = logging.getLogger(__name__)

_COMPOUND_KEYS = (
    "cid", "name", "iupac_name", "formula", "molecular_weight",
    "ghs_pictograms", "ghs_signal", "ghs_hazard_statements", "industry_uses", "vendor_count",
)


class SubsystemRow(BaseModel):
    system: str = Field(..., description="Subsystem key exactly as in the evidence")
    description: str = Field(..., description="One short phrase, e.g. 'The group of all Ag atoms in the system'")


class MaterialSummary(BaseModel):
    summary: str = Field(..., description="The natural-language summary paragraph")
    subsystems: list[SubsystemRow] = Field(..., description="One row per subsystem in the evidence, same order")
    key_facts: list[str] = Field(..., description="Short facts used, each traceable to the evidence")


def short_key(system: str) -> str:
    """'results/material/topology/5' -> 'topology/5'"""
    return "/".join(system.split("/")[-2:])


def _trim_uses(uses: list[str], n: int = 5, max_len: int = 60) -> list[str]:
    """PubChem lists can hold dozens of long HSDB paragraphs; keep a few short category-like entries."""
    short = [u for u in uses if len(u) <= max_len and "not known" not in u.lower() and u.lower() != "other"]
    return short[:n]


def render_report(summary: str, rows: list[SubsystemRow]) -> str:
    lines = [summary.strip(), "", "| subsystem | description |", "|---|---|"]
    lines += [f"| {short_key(r.system)} | {r.description.strip().replace('|', '/')} |" for r in rows]
    return "\n".join(lines) + "\n"


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
        overview = []
        for k, v in (small.get("systems") or {}).items():
            meta = system_meta(v)
            overview.append(
                {
                    "system": k,
                    "label": meta.get("label"),
                    "structural_type": meta.get("structural_type"),
                    "system_relation": meta.get("system_relation"),
                    "parent_system": meta.get("parent_system"),
                }
            )
        components = []
        for s in scrape.get("systems", []):
            findings = [f for f in s["findings"].values() if isinstance(f, dict)]
            merged: dict[int, dict[str, Any]] = {}  # one entry per CID, facts merged across tools
            for finding in findings:
                for c in finding.get("compounds", []):
                    if c.get("found") and c.get("cid"):
                        entry = merged.setdefault(c["cid"], {"cid": c["cid"]})
                        entry.update({k: c[k] for k in _COMPOUND_KEYS if c.get(k) not in (None, [], "")})
            for entry in merged.values():
                if "industry_uses" in entry:
                    entry["industry_uses"] = _trim_uses(entry["industry_uses"])
                if "ghs_hazard_statements" in entry:
                    entry["ghs_hazard_statements"] = entry["ghs_hazard_statements"][:4]
            fragments = next((f.get("heavy_fragments") or f["fragments"] for f in findings if "fragments" in f), [])
            components.append(
                {
                    "system": s["system"],
                    "label": s["label"],
                    "structural_type": s["structural_type"],
                    "smiles_fragments": fragments[:3],
                    "compounds": list(merged.values()),
                    "identified": bool(merged),
                    # compact outputs of domain tools (mofid, cif_to_seq, pdb_lookup, ...)
                    "tool_summaries": {t: f["summary"] for t, f in s["findings"].items()
                                       if isinstance(f, dict) and isinstance(f.get("summary"), dict)},
                }
            )
        planner = scrape.get("planner") or {}
        return {
            "material_class": planner.get("material_class"),
            "class_reason": planner.get("class_reason"),
            "systems": overview,
            "components": components,
        }

    async def ask_llm(self, evidence: dict[str, Any]) -> MaterialSummary:
        prompt = (
            "Write the material report following the judge skill. Use ONLY facts in this evidence.\n\n"
            f"EVIDENCE:\n{json.dumps(evidence, indent=2)}"
        )
        response = await self.agent.run(prompt, options={"response_format": MaterialSummary})
        if response.value is None:
            raise ValueError(f"no structured summary from LLM; raw: {response.text[:300]}")
        return response.value

    # ------------------------------------------------------------------ deterministic template
    @staticmethod
    def template_rows(evidence: dict[str, Any]) -> list[SubsystemRow]:
        by_system = {c["system"]: c for c in evidence["components"]}
        labels = {s["system"]: (s.get("label") or "") for s in evidence["systems"]}
        rows = []
        for s in evidence["systems"]:
            label, stype = labels[s["system"]], s.get("structural_type")
            comp = by_system.get(s["system"])
            cpd = comp["compounds"][0] if comp and comp["compounds"] else None
            ident = f", {cpd['name']} (PubChem ID {cpd['cid']})" if cpd else ""
            tools = (comp or {}).get("tool_summaries", {})
            seq = next((t for t in tools.values() if "length" in t and "sequence" in t), None)
            pdb = next((t for t in tools.values() if t.get("pdb_id")), None)
            if seq:
                text = f"A protein chain of {seq['length']} residues"
                if pdb:
                    desc = ", ".join(pdb.get("description", [])[:1])
                    text += f", most similar to PDB {pdb['pdb_id']}" + (f" ({desc})" if desc else "")
            elif s.get("system_relation") == "root" or stype == "bulk":
                text = "The original system"
            elif stype == "group":
                kind = "metal atoms" if "metal" in label else "ligands" if "ligand" in label else "organic molecules"
                text = f"The group of all {kind} in the system"
            elif stype == "molecule":
                kind = "metal unit" if "metal" in label else "ligand" if "ligand" in label else "organic molecule"
                if cpd:
                    text = f"A representative {kind}{ident}"
                elif comp:  # looked up, nothing found
                    text = f"A representative {kind} (not identified in PubChem)"
                else:       # not planned (strict MOF / protein route)
                    text = f"A representative {kind}"
            else:
                text = label or "Subsystem"
            rows.append(SubsystemRow(system=s["system"], description=text))
        return rows

    @classmethod
    def template(cls, evidence: dict[str, Any]) -> MaterialSummary:
        framework = next(
            (s["label"] for s in evidence["systems"] if s.get("structural_type") == "bulk" and s.get("label")), None
        )
        compounds = list({c["cid"]: c for comp in evidence["components"] for c in comp["compounds"]}.values())
        rows = cls.template_rows(evidence)
        extra, extra_facts = cls.domain_sentences(evidence)
        if not compounds:
            cls_text = {"mof": "a metal-organic framework (MOF)", "protein": "a protein"}.get(evidence.get("material_class") or "")
            fallback = (f"The given system is {cls_text}; its analysis tool did not return a result."
                        if cls_text else "No component could be identified in PubChem.")
            summary = " ".join(extra) if extra else fallback
            return MaterialSummary(summary=summary, subsystems=rows, key_facts=extra_facts)
        names = [f"{c.get('name', 'unknown')} (PubChem ID {c['cid']})" for c in compounds]
        head = f"The given system is a {framework} containing " if framework else "The given system contains "
        sentences = [head + (", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]) + "."]
        facts = list(names)
        for c in compounds:
            if c.get("formula", "").isalpha() and len(c.get("formula", "")) <= 2:  # element: no uses/hazards
                continue
            bits = []
            if c.get("industry_uses"):
                bits.append(f"industry use: {', '.join(c['industry_uses'][:2])}")
            if c.get("vendor_count"):
                bits.append(f"{c['vendor_count']} vendors")
            if c.get("ghs_pictograms"):
                bits.append(f"GHS: {', '.join(c['ghs_pictograms']).lower()}")
            if bits:
                sentences.append(f"{c.get('name', c['cid'])}: {'; '.join(bits)}.")
                facts += bits
        return MaterialSummary(summary=" ".join(sentences + extra), subsystems=rows, key_facts=facts + extra_facts)

    @staticmethod
    def domain_sentences(evidence: dict[str, Any]) -> tuple[list[str], list[str]]:
        """Sentences from domain-tool summaries (MOFid, protein sequence, PDB)."""
        sentences, facts = [], []
        for comp in evidence["components"]:
            for tool, t in comp.get("tool_summaries", {}).items():
                if "mofid" in t:
                    nodes = ", ".join(t.get("node_formulas") or t.get("nodes") or []) or "none"
                    linkers = ", ".join(t.get("linker_formulas") or t.get("linkers") or []) or "none"
                    topo = (f"its net topology is {t['topology']}" if t.get("topology") and not t.get("topology_failed")
                            else "MOFid could not determine its net topology")
                    if t.get("catenation") is not None and not t.get("topology_failed"):
                        topo += f" (catenation {t['catenation']})"
                    sentences.insert(0, f"The given system is a metal-organic framework (MOF). MOFid identifies "
                                        f"{len(t.get('nodes', []))} node type(s) ({nodes}) and "
                                        f"{len(t.get('linkers', []))} organic linker type(s) ({linkers}); {topo}.")
                    facts.append(f"MOFid: {t['mofid']}")
                    if t.get("mofkey"):
                        facts.append(f"MOFkey: {t['mofkey']}")
                elif t.get("pdb_id"):
                    desc = ", ".join(t.get("description", [])[:1]) or "a protein"
                    org = ", ".join(t.get("organism", [])[:1])
                    sentences.append(f"The chain is most similar to PDB entry {t['pdb_id']} ({desc}"
                                     + (f", {org}" if org else "") + ").")
                    facts.append(f"PDB {t['pdb_id']}")
                elif "length" in t and "sequence" in t:
                    sentences.insert(0, f"The given system is a protein chain of {t['length']} residues.")
                    facts.append(f"sequence: {t['sequence']}")
        return sentences, facts

    # ------------------------------------------------------------------ step
    async def process(self, message: StepMessage) -> dict[str, Any]:
        evidence = self.evidence(message)
        try:
            result, by = await self.ask_llm(evidence), "llm"
        except Exception as exc:
            log.warning("judge LLM failed (%s); using template", exc)
            result, by = self.template(evidence), "fallback"

        # every subsystem exactly once, in evidence order; fill gaps from the template
        llm_rows = {r.system: r for r in result.subsystems}
        rows = [llm_rows.get(t.system, t) for t in self.template_rows(evidence)]
        scrape = message.output_of(self.source)
        return {
            "summary": result.summary,
            "subsystems": [r.model_dump() for r in rows],
            "key_facts": result.key_facts,
            "report": render_report(result.summary, rows),
            "generated_by": by,
            "evidence": evidence,
            # diagnostics only (kept out of the LLM evidence so the summary never talks about errors)
            "errors": {s["system"]: s["errors"] for s in scrape.get("systems", []) if s["errors"]},
        }
