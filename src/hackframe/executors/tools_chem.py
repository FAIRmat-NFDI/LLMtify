"""Scraper tools (router pattern). Each is an isolated executor that handles one WorkItem.

    cif_to_smiles   CIF (from temp/<uuid>.json) -> SMILES via descriptors.py (ASE + RDKit)
    pubchem_mcp     SMILES -> PubChem compound, agentically via the BioContext PubChem MCP server
    pubchem_safety  CID -> GHS hazards, industry uses, vendor count (PUG-View; not in the MCP server)

workflows.yaml:
  - {id: cif_to_smiles, type: cif_to_smiles, tolerance: 1.3}
  - {id: pubchem, type: pubchem_mcp, agent: pubchem, max_fragments: 3}
  - {id: pubchem_safety, type: pubchem_safety}
"""

import asyncio
import json
import logging
import re
from typing import Any

from agent_framework import Agent
from pydantic import BaseModel, Field

from hackframe.core.config import StepSpec
from hackframe.core.schemas import WorkItem
from hackframe.executors.base import EXECUTORS, BuildContext, build_agent
from hackframe.executors.routing import BaseToolExecutor
from hackframe.tools import pubchem as pubchem_tools
from hackframe.tools.structure import cif_to_smiles, read_cell

log = logging.getLogger(__name__)


@EXECUTORS.register("cif_to_smiles")
class CifToSmilesExecutor(BaseToolExecutor):
    executor_type = "cif_to_smiles"
    description = (
        "Convert the subsystem's CIF structure to SMILES (ASE + RDKit, bonds from covalent radii). "
        "Requires has_cif=true. Uses the parent crystal cell when the CIF has none. "
        "Output: smiles, fragments, heavy_fragments (no explicit H), formula, n_atoms."
    )

    async def run_tool(self, item: WorkItem) -> dict[str, Any]:
        if not item.cif_key:
            raise ValueError(f"{item.system} has no CIF in the input")
        cif = await self.artifacts.get_cif(item.artifact_id, item.cif_key)
        cell, cell_from = None, None
        if self.params.get("inherit_cell", True):
            cell, cell_from = await self.ancestor_cell(item)
        out = await asyncio.to_thread(
            cif_to_smiles,
            cif,
            float(self.params.get("tolerance", 1.3)),
            int(self.params.get("max_atoms", 2000)),
            cell,
        )
        return {**out, "cell_from": cell_from if out["periodic"] else None}

    async def ancestor_cell(self, item: WorkItem) -> tuple[Any | None, str | None]:
        """Cell of the nearest system (self, parent, ..., root) whose CIF has one."""
        data = await self.artifacts.load(item.artifact_id)
        systems, cifs = data.get("systems") or {}, data.get("cifs") or {}
        key, seen = item.system, set()
        while key and key not in seen:
            seen.add(key)
            if (cif := cifs.get(f"{key}.cif")) and (cell := await asyncio.to_thread(read_cell, cif)) is not None:
                return cell, key
            key = ((systems.get(key) or {}).get("metadata") or {}).get("parent_system")
        return None, None


# ---------------------------------------------------------------- PubChem via MCP (agentic)
class CompoundHit(BaseModel):
    query_smiles: str = ""
    found: bool = False
    cid: int | None = None
    name: str | None = Field(None, description="Common name (first synonym)")
    iupac_name: str | None = None
    formula: str | None = None
    molecular_weight: float | None = None
    synonyms: list[str] = Field(default_factory=list)
    searched_as: str | None = Field(None, description="Name passed to search_compound")
    note: str = ""


class PubChemFindings(BaseModel):
    compounds: list[CompoundHit]


def extract_json(text: str) -> Any:
    """Last JSON value in an LLM answer: the last parseable ```json block, else the last {...}/[...]."""
    for block in reversed(re.findall(r"```(?:json)?\s*(.*?)```", text, re.S)):
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue
    decoder = json.JSONDecoder()
    best, best_end, pos = None, -1, 0
    for m in re.finditer(r"[\[{]", text):
        if m.start() < pos:  # inside an object already decoded: keep the outermost
            continue
        try:
            value, end = decoder.raw_decode(text, m.start())
        except json.JSONDecodeError:
            continue
        pos = end
        if isinstance(value, (dict, list)) and value and end > best_end:
            best, best_end = value, end
    if best is None:
        raise ValueError(f"no JSON object in agent answer: {text[:200]!r}")
    return best


_KEY_ALIASES = {
    "CID": "cid", "Cid": "cid", "MolecularFormula": "formula", "molecular_formula": "formula",
    "MolecularWeight": "molecular_weight", "IUPACName": "iupac_name", "Name": "name", "Synonyms": "synonyms",
}


def normalize_findings(value: Any, queries: list[dict[str, str]]) -> PubChemFindings:
    """Accept the shapes models actually return: {"compounds": [...]}, a list, or one bare compound."""
    if isinstance(value, dict) and "compounds" in value:
        raw = value["compounds"]
    elif isinstance(value, list):
        raw = value
    elif isinstance(value, dict):
        raw = [value]
    else:
        raise ValueError(f"unexpected JSON shape: {type(value).__name__}")
    hits = []
    for i, c in enumerate(raw if isinstance(raw, list) else [raw]):
        if not isinstance(c, dict):
            continue
        c = {_KEY_ALIASES.get(k, k): v for k, v in c.items()}
        if not c.get("query_smiles") and i < len(queries):
            c["query_smiles"] = queries[i]["query_smiles"]
        c.setdefault("found", c.get("cid") is not None)
        hits.append(CompoundHit.model_validate(c))
    return PubChemFindings(compounds=hits)


@EXECUTORS.register("pubchem_mcp")
class PubChemMCPExecutor(BaseToolExecutor):
    """An agent with the PubChem MCP tools (agents.yaml `mcp: [pubchem]`) identifies each fragment.

    The MCP server searches by NAME only, so the agent proposes names from the SMILES/formula,
    calls search_compound, and checks the hit's formula. Guidance lives in skills/pubchem-scrape.
    No response_format here: structured-output decoding can block tool calls on some servers (vLLM),
    so the agent ends with a JSON block that is validated against PubChemFindings.
    """

    executor_type = "pubchem_mcp"
    description = (
        "Identify SMILES fragments in PubChem through the PubChem MCP server (an LLM picks search names, "
        "calls the MCP tools, verifies by formula). Needs fragments from cif_to_smiles. "
        "Output: compounds with CID, name, formula, molecular weight, synonyms."
    )

    def __init__(self, id: str, agent: Agent, model: str | None = None, **params: Any) -> None:
        super().__init__(id, **params)
        self.agent = agent
        self._model = model

    @classmethod
    def from_spec(cls, step: StepSpec, build: BuildContext) -> "PubChemMCPExecutor":
        params = step.params
        agent, model = build_agent(step, build, params)
        obj = cls(step.id, agent=agent, model=model, **params)
        obj.bind(build)
        return obj

    def model_name(self) -> str | None:
        return self._model

    async def ask_agent(self, prompt: str) -> str:
        return (await self.agent.run(prompt)).text

    async def format_answer(self, answer: str, queries: list[dict[str, str]]) -> Any:
        """Second chance when the tool-using answer has no usable JSON: one call, no tools,
        structured output, that only reformats what the agent already found."""
        prompt = (
            "Convert this PubChem identification answer into the required JSON. Use only facts stated in it; "
            "fragments it did not identify get found=false.\n\n"
            f"FRAGMENTS:\n{json.dumps(queries, indent=2)}\n\nANSWER:\n{answer[-6000:]}"
        )
        response = await self.agent.run(prompt, options={"response_format": PubChemFindings, "tool_choice": "none"})
        if response.value is None:
            raise ValueError(f"formatting retry returned no JSON: {response.text[:200]!r}")
        return response.value.model_dump()

    async def run_tool(self, item: WorkItem) -> dict[str, Any]:
        fragments = item.find("fragments") or ([s] if (s := item.find("smiles")) else [])
        if not fragments:
            raise ValueError("no SMILES from an earlier tool (plan cif_to_smiles before pubchem)")
        heavy = item.find("heavy_fragments") or fragments
        limit = int(self.params.get("max_fragments", 3))
        queries = [{"query_smiles": f, "smiles_without_h": h} for f, h in zip(fragments, heavy)][:limit]
        prompt = (
            "Identify each fragment in PubChem with the PubChem MCP tools, following the pubchem-scrape skill.\n\n"
            f"SUBSYSTEM: {item.system} (label: {item.metadata.get('label')}, "
            f"structural_type: {item.metadata.get('structural_type')}, formula of whole subsystem: {item.find('formula')})\n"
            f"FRAGMENTS:\n{json.dumps(queries, indent=2)}\n\n"
            'Finish with one ```json block: {"compounds": [ ...one entry per fragment... ]}'
        )
        text = await self.ask_agent(prompt)
        try:
            findings, parsed_by = normalize_findings(extract_json(text), queries), "answer"
        except (ValueError, TypeError) as exc:  # incl. pydantic ValidationError
            log.warning("pubchem agent answer not usable (%s); asking it to reformat", str(exc)[:120])
            findings, parsed_by = normalize_findings(await self.format_answer(text, queries), queries), "reformat"
        return {
            "compounds": [c.model_dump() for c in findings.compounds],
            "fragments_not_looked_up": max(0, len(fragments) - limit),
            "parsed_by": parsed_by,
        }


# ---------------------------------------------------------------- PUG-View extras (not in the MCP server)
@EXECUTORS.register("pubchem_safety")
class PubChemSafetyExecutor(BaseToolExecutor):
    executor_type = "pubchem_safety"
    description = (
        "For CIDs found by an earlier tool (pubchem): GHS hazard pictograms and statements, industry uses, "
        "number of chemical vendors. Run after pubchem."
    )
    client = pubchem_tools._client  # swap in tests

    async def run_tool(self, item: WorkItem) -> dict[str, Any]:
        cids = list(dict.fromkeys(c["cid"] for c in item.find("compounds") or [] if c.get("found") and c.get("cid")))
        if not cids:  # nothing identified upstream: not an error, just nothing to look up
            return {"compounds": [], "note": "no PubChem CID from an earlier tool"}
        compounds = []
        for cid in cids:
            details = await self.client.details(cid)
            compounds.append({"cid": cid, "found": True, **details.model_dump(exclude={"cid"})})
        return {"compounds": compounds}
