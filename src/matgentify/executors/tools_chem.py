"""Scraper tools (router pattern). Each is an isolated executor that handles one WorkItem.

    cif_to_smiles   CIF (from temp/<uuid>.json) -> SMILES via descriptors.py (ASE + RDKit)
    pubchem_mcp     SMILES -> CID (structure match) -> details via PubChem MCP; LLM name search as fallback
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

from matgentify.core.config import StepSpec
from matgentify.core.material import cif_key, system_meta
from matgentify.core.schemas import WorkItem
from matgentify.executors.base import EXECUTORS, BuildContext, build_agent
from matgentify.executors.routing import BaseToolExecutor
from matgentify.tools import pubchem as pubchem_tools
from matgentify.tools.structure import cif_to_smiles, read_cell

log = logging.getLogger(__name__)


@EXECUTORS.register("cif_to_smiles")
class CifToSmilesExecutor(BaseToolExecutor):
    executor_type = "cif_to_smiles"
    description = (
        "Convert the subsystem's CIF structure to SMILES (ASE + RDKit, bonds from covalent radii). "
        "Requires has_cif=true. Uses the parent crystal cell when the CIF has none. "
        "Output: smiles, fragments, heavy_fragments (no explicit H), bonded_fragments (with bond orders), formula."
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
            bool(self.params.get("bond_orders", True)),
        )
        return {**out, "cell_from": cell_from if out["periodic"] else None}

    async def ancestor_cell(self, item: WorkItem) -> tuple[Any | None, str | None]:
        """Cell of the nearest system (self, parent, ..., root) whose CIF has one."""
        data = await self.artifacts.load(item.artifact_id)
        systems, cifs = data.get("systems") or {}, data.get("cifs") or {}
        key, seen = item.system, set()
        while key and key not in seen:
            seen.add(key)
            ck = cif_key(key, cifs)
            if ck and (cell := await asyncio.to_thread(read_cell, cifs[ck])) is not None:
                return cell, key
            key = system_meta(systems.get(key)).get("parent_system")
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


def neutralize(smiles: str | None) -> str | None:
    """'C[NH+]1CCCC1' -> 'CN1CCCC1' (RDKit Uncharger); None if unchanged or unparsable."""
    if not smiles or ("+" not in smiles and "-" not in smiles):
        return None
    try:
        from rdkit import Chem
        from rdkit.Chem.MolStandardize import rdMolStandardize

        mol = Chem.MolFromSmiles(smiles)
        out = Chem.MolToSmiles(rdMolStandardize.Uncharger().uncharge(mol)) if mol else None
        return out if out and out != smiles else None
    except Exception:
        return None


@EXECUTORS.register("pubchem_mcp")
class PubChemMCPExecutor(BaseToolExecutor):
    """SMILES -> PubChem compound, per fragment:

    1. exact structure match SMILES -> CID (PubChem REST; the MCP server has no SMILES search)
    2. CID -> name, IUPAC name, formula, synonyms via the PubChem MCP server (`get_compound_details`, no LLM)
    3. only if 1 finds nothing: the agent (agents.yaml `mcp: [pubchem]`, skill pubchem-scrape) names the
       fragment and uses the MCP name search. No response_format there (constrained decoding can block
       tool calls on vLLM); the agent ends with a JSON block validated against PubChemFindings.

    Params: max_fragments (3), agent_fallback (true).
    """

    executor_type = "pubchem_mcp"
    description = (
        "Identify SMILES fragments in PubChem: exact SMILES structure match -> CID, details from the PubChem MCP "
        "server; fragments without a match are named by an LLM and searched via MCP. Needs fragments from "
        "cif_to_smiles. Output: compounds with CID, name, formula, molecular weight, synonyms."
    )
    client = pubchem_tools._client  # SMILES -> CID (PubChem REST); swap in tests

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

    # ------------------------------------------------------------ 1) SMILES -> CID (structure match)
    async def smiles_to_cid(self, query: dict[str, str]) -> tuple[pubchem_tools.PubChemResult, str] | None:
        """Exact structure lookup in PubChem (the MCP server has no SMILES search). Order:
        1. bond-order SMILES (aromatic rings, nitriles ... exact for any molecule)
        2. converter SMILES without H (PubChem fills implicit H: exact for saturated molecules,
           e.g. C(CC1CCNCC1)CC1CCNCC1 -> CID 85631)
        3. converter SMILES as is
        Charged bond-order SMILES (protonated MD ligands, e.g. R3NH+) are tried neutralised right after."""
        bonded = query.get("bonded_smiles")
        candidates = [bonded, neutralize(bonded), query["smiles_without_h"], query["query_smiles"]]
        for smiles in dict.fromkeys(s for s in candidates if s):
            res = await self.client.lookup(smiles, include_synonyms=True, max_synonyms=5)
            if res.found:
                return res, smiles
        return None

    # ------------------------------------------------------------ 2) CID -> details via PubChem MCP
    @property
    def mcp_tool(self) -> Any:
        tools = getattr(self.agent, "mcp_tools", [])
        return tools[0] if tools else None

    async def mcp_details(self, cid: int) -> dict[str, Any]:
        """`get_compound_details` on the PubChem MCP server, called directly (no LLM)."""
        tool = self.mcp_tool
        if tool is None:
            raise RuntimeError("pubchem agent has no MCP tool (agents.yaml: mcp: [pubchem])")
        if not getattr(tool, "is_connected", False):
            await tool.connect()
        contents = await tool.call_tool("get_compound_details", cid=cid)
        data = json.loads(" ".join(getattr(c, "text", "") or "" for c in contents) or "{}")
        data = data.get("result", data)
        if "error" in data:
            raise RuntimeError(f"MCP get_compound_details({cid}): {data.get('error')} {data.get('message', '')}")
        return data

    async def describe(self, query: dict[str, str], res: pubchem_tools.PubChemResult, matched: str) -> CompoundHit:
        c = res.compound
        hit = CompoundHit(
            query_smiles=query["query_smiles"], found=True, cid=c.cid, iupac_name=c.iupac_name,
            formula=c.molecular_formula, molecular_weight=c.molecular_weight, synonyms=res.synonyms,
            name=(res.synonyms[0] if res.synonyms else c.iupac_name), searched_as=f"smiles:{matched}",
        )
        try:  # prefer the MCP server's view for name/properties
            d = await self.mcp_details(c.cid)
            props, syns = d.get("properties") or {}, d.get("synonyms") or []
            hit.synonyms = syns[:5] or hit.synonyms
            hit.name = (syns[0] if syns else None) or hit.name
            hit.iupac_name = props.get("IUPACName") or hit.iupac_name
            hit.formula = props.get("MolecularFormula") or hit.formula
            if props.get("MolecularWeight"):
                hit.molecular_weight = float(props["MolecularWeight"])
            hit.note = "matched by SMILES; details from PubChem MCP"
        except Exception as exc:  # MCP unavailable: keep the REST data
            hit.note = f"matched by SMILES; MCP details unavailable ({str(exc)[:80]})"
        return hit

    # ------------------------------------------------------------ 3) fallback: LLM + MCP name search
    async def run_tool(self, item: WorkItem) -> dict[str, Any]:
        fragments = item.find("fragments") or ([s] if (s := item.find("smiles")) else [])
        if not fragments:
            raise ValueError("no SMILES from an earlier tool (plan cif_to_smiles before pubchem)")
        heavy = item.find("heavy_fragments") or fragments
        bonded = item.find("bonded_fragments") or [None] * len(fragments)
        limit = int(self.params.get("max_fragments", 3))
        queries = [
            {"query_smiles": f, "smiles_without_h": h, **({"bonded_smiles": b} if b else {})}
            for f, h, b in zip(fragments, heavy, bonded)
        ][:limit]

        hits: list[CompoundHit] = []
        unresolved: list[dict[str, str]] = []
        for q in queries:
            if match := await self.smiles_to_cid(q):
                hits.append(await self.describe(q, *match))
            else:
                unresolved.append(q)

        parsed_by = "smiles"
        if unresolved and self.params.get("agent_fallback", True):
            agent_hits, parsed_by = await self.identify_by_name(item, unresolved)
            hits += agent_hits
        else:
            hits += [CompoundHit(query_smiles=q["query_smiles"], found=False, note="no PubChem match for SMILES")
                     for q in unresolved]
        return {
            "compounds": [h.model_dump() for h in hits],
            "fragments_not_looked_up": max(0, len(fragments) - limit),
            "matched_by_smiles": len(queries) - len(unresolved),
            "parsed_by": parsed_by,
        }

    async def identify_by_name(self, item: WorkItem, queries: list[dict[str, str]]) -> tuple[list[CompoundHit], str]:
        """Fragments with no exact SMILES match: the agent names them and uses the MCP name search."""
        prompt = (
            "Identify each fragment in PubChem with the PubChem MCP tools, following the pubchem-scrape skill.\n\n"
            f"SUBSYSTEM: {item.system} (label: {item.metadata.get('label')}, "
            f"structural_type: {item.metadata.get('structural_type')}, formula of whole subsystem: {item.find('formula')})\n"
            f"FRAGMENTS:\n{json.dumps(queries, indent=2)}\n\n"
            'Finish with one ```json block: {"compounds": [ ...one entry per fragment... ]}'
        )
        text = await self.ask_agent(prompt)
        try:
            findings, parsed_by = normalize_findings(extract_json(text), queries), "agent"
        except (ValueError, TypeError) as exc:  # incl. pydantic ValidationError
            log.warning("pubchem agent answer not usable (%s); asking it to reformat", str(exc)[:120])
            findings, parsed_by = normalize_findings(await self.format_answer(text, queries), queries), "agent_reformat"
        return findings.compounds, parsed_by


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
