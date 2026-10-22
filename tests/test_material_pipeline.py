"""Full pipeline, molecular route: preprocess -> compact -> tool_finder =switch=> cif_to_smiles -> pubchem => gather -> judge.
LLM calls and PubChem are stubbed; CIF -> SMILES is real (ASE + RDKit).
The example is a MOF; here the tool finder is made to judge it `molecular` (autouse fixture) to exercise the
CIF -> SMILES -> PubChem chain. The strict MOF / protein routes are tested in test_mof_protein.py."""

import json
from pathlib import Path

import pytest

from hackframe.core.artifacts import ArtifactNotFound, ArtifactStore
from hackframe.core.runtime import Runtime
from hackframe.executors.judge import JudgeExecutor, MaterialSummary, SubsystemRow
from hackframe.executors.tool_finder import SystemPlan, ToolFinderExecutor, ToolPlan
from hackframe.executors.tools_chem import PubChemMCPExecutor, PubChemSafetyExecutor
from hackframe.executors.tools_mof_protein import MOFidExecutor
from hackframe.tools.mofid import MOFid
from hackframe.settings import Settings
from hackframe.tools.pubchem import CompoundDetails, PubChemCompound, PubChemResult

EXAMPLE_PATH = Path(__file__).parent.parent / "examples" / "material_example.json"
EXAMPLE = json.loads(EXAMPLE_PATH.read_text())
AG = "results/material/topology/3"
ROOT = "results/material/topology/0"
MOFID_OUT = json.loads((Path(__file__).parent / "data" / "mofid_output.json").read_text())  # real MOFid run on the root CIF


def fake_mofid(cif, timeout=600):
    return {**MOFID_OUT, "mofid_parsed": MOFid.parse(MOFID_OUT["mofid"]).model_dump()}


PROMPTS: list[str] = []


async def fake_mcp_agent(self, prompt: str) -> str:
    """Stands in for the LLM + PubChem MCP loop: answers like the pubchem-scrape skill asks."""
    PROMPTS.append(prompt)
    if '"query_smiles": "[Ag]"' in prompt:
        hit = {"query_smiles": "[Ag]", "found": True, "cid": 23954, "name": "Silver", "formula": "Ag", "searched_as": "silver"}
    else:
        hit = {"query_smiles": "?", "found": False, "note": "no match"}
    return "Searched PubChem.\n```json\n" + json.dumps({"compounds": [hit]}) + "\n```"


class FakeSafety:
    async def details(self, cid):
        return CompoundDetails(cid=cid, vendor_count=12)


PIPERIDINYL = "C(CC1CCNCC1)CC1CCNCC1"  # 1,3-Bis(4-piperidinyl)propane, CID 85631
KNOWN = {"[Ag]": (23954, "Silver", "Ag"), PIPERIDINYL: (85631, "1,3-Bis(4-piperidinyl)propane", "C13H26N2")}


class FakeSmilesLookup:
    """PubChem REST structure match: only exact known SMILES are found."""

    async def lookup(self, smiles, include_synonyms=True, max_synonyms=5):
        if smiles in KNOWN:
            cid, name, formula = KNOWN[smiles]
            return PubChemResult(query_smiles=smiles, found=True, synonyms=[name],
                                 compound=PubChemCompound(cid=cid, molecular_formula=formula, url="u"))
        return PubChemResult(query_smiles=smiles, found=False, error="no PubChem match")


async def fake_mcp_details(self, cid):
    name, formula = {c: (n, f) for c, n, f in KNOWN.values()}[cid]
    return {"CID": cid, "properties": {"MolecularFormula": formula, "IUPACName": name.lower()}, "synonyms": [name]}


def install_fakes(monkeypatch) -> None:
    monkeypatch.setattr(PubChemMCPExecutor, "client", FakeSmilesLookup())
    monkeypatch.setattr(PubChemMCPExecutor, "mcp_details", fake_mcp_details)
    monkeypatch.setattr(PubChemMCPExecutor, "ask_agent", fake_mcp_agent)
    monkeypatch.setattr(PubChemSafetyExecutor, "client", FakeSafety())
    monkeypatch.setattr(MOFidExecutor, "run_mofid", staticmethod(fake_mofid))


_ORIG_FALLBACK = ToolFinderExecutor.fallback


@pytest.fixture(autouse=True)
def molecular_route(monkeypatch) -> None:
    """Fallback class decision sees no 'MOF' label -> molecular route (CIF -> SMILES -> PubChem)."""
    async def fallback(self, systems):
        return await _ORIG_FALLBACK(self, [{**x, "label": "framework" if x["label"] == "MOF" else x["label"]} for x in systems])

    monkeypatch.setattr(ToolFinderExecutor, "fallback", fallback)


@pytest.fixture
def rt(tmp_path: Path, monkeypatch) -> Runtime:
    PROMPTS.clear()
    install_fakes(monkeypatch)
    runtime = Runtime(Settings(hackframe_temp_dir=tmp_path / "temp"))
    runtime.config.workflows["material_pipeline"].output_field = None  # tests inspect the full judge output
    return runtime


def llm_down(monkeypatch) -> None:
    async def boom(self, *_):
        raise ConnectionError("LLM down")

    monkeypatch.setattr(ToolFinderExecutor, "ask_llm", boom)
    monkeypatch.setattr(JudgeExecutor, "ask_llm", boom)


async def test_llm_plan_routes_tools_and_judge_summarises(rt: Runtime, monkeypatch) -> None:
    seen = {}

    async def plan(self, systems):
        seen["systems"] = systems
        seen["tools"] = self.tools
        return ToolPlan(material_class="molecular", class_reason="test: molecular route", plans=[
            SystemPlan(system=AG, tools=["cif_to_smiles", "pubchem", "pubchem_safety", "made_up_tool"], reason="metal node"),
            SystemPlan(system="results/material/topology/1", tools=[], reason="bulk"),
        ])

    async def judge(self, evidence, *_):
        seen["evidence"] = evidence
        return MaterialSummary(
            summary="The given system is a MOF containing Ag atoms (PubChem ID 23954).",
            subsystems=[SubsystemRow(system=AG, description="A representative Ag molecule (PubChem ID 23954)")],
            key_facts=["Ag"],
        )

    monkeypatch.setattr(ToolFinderExecutor, "ask_llm", plan)
    monkeypatch.setattr(JudgeExecutor, "ask_llm", judge)

    out = await rt.run_workflow("material_pipeline", EXAMPLE)

    assert out["generated_by"] == "llm" and "PubChem ID 23954" in out["summary"]
    assert set(seen["tools"]) == {"cif_to_smiles", "pubchem", "pubchem_safety", "mofid", "cif_to_seq", "pdb_lookup"}   # builder injected tool list
    assert {s["system"]: s["has_cif"] for s in seen["systems"]}[AG] is True
    comp = seen["evidence"]["components"]
    assert [c["system"] for c in comp] == [AG]                                     # only planned subsystem
    assert comp[0]["smiles_fragments"] == ["[Ag]"]                                  # real ASE+RDKit result
    # SMILES match + MCP details (name/formula/iupac) and pubchem_safety (vendor_count) merged per CID
    assert comp[0]["compounds"] == [{"cid": 23954, "name": "Silver", "iupac_name": "silver", "formula": "Ag", "vendor_count": 12}]
    assert PROMPTS == []                                                           # no LLM needed: SMILES matched


async def test_fallbacks_when_llm_is_down(rt: Runtime, monkeypatch) -> None:
    llm_down(monkeypatch)
    out = await rt.run_workflow("material_pipeline", EXAMPLE)

    assert out["generated_by"] == "fallback"
    assert out["summary"].startswith("The given system is a metal-organic framework (MOF) containing Silver (PubChem ID 23954)")
    planned = [c["system"] for c in out["evidence"]["components"]]
    assert planned == [AG, "results/material/topology/5"]  # skill metadata: molecule subsystems with a CIF
    assert out["evidence"]["material_class"] == "molecular"


async def test_full_json_saved_and_cif_resolvable(rt: Runtime, monkeypatch) -> None:
    llm_down(monkeypatch)
    await rt.run_workflow("material_pipeline", EXAMPLE)
    (saved,) = list(rt.artifacts.root.glob("*.json"))
    assert json.loads(saved.read_text()) == EXAMPLE
    aid = saved.stem
    assert await rt.artifacts.get_cif(aid, AG) == EXAMPLE["cifs"][f"{AG}.cif"]
    with pytest.raises(ArtifactNotFound):
        await rt.artifacts.get_cif(aid, "results/material/topology/99")


async def test_truncated_json_fails_cleanly(rt: Runtime) -> None:
    out = await rt.run_workflow("material_pipeline", EXAMPLE_PATH.read_text()[:5000])
    assert out["ok"] is False
    assert out["error"].startswith("preprocess: ValueError: input is not valid JSON")
    assert "compact:skipped" in out["summary"] and "judge:skipped" in out["summary"]


async def test_empty_input_reports_error(rt: Runtime) -> None:
    out = await rt.run_workflow("material_pipeline", {})
    assert out["ok"] is False and "non-empty JSON object" in out["error"]


async def test_store_rejects_bad_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        await ArtifactStore(tmp_path).load("../../etc/passwd")


async def test_bad_agent_answer_stops_chain_but_judge_runs(rt: Runtime, monkeypatch) -> None:
    llm_down(monkeypatch)

    async def chatty(self, prompt):
        return "I could not find it, sorry."

    async def reformat_fails(self, answer, queries):
        raise ValueError("formatting retry returned no JSON")

    monkeypatch.setattr(PubChemMCPExecutor, "ask_agent", chatty)
    monkeypatch.setattr(PubChemMCPExecutor, "format_answer", reformat_fails)
    out = await rt.run_workflow("material_pipeline", EXAMPLE)
    errs = out["errors"]["results/material/topology/5"]  # Ag matched by SMILES; the linker went to the agent
    assert "formatting retry returned no JSON" in errs["pubchem"] and "pubchem_safety" not in errs
    assert AG not in out["errors"]
    assert out["summary"].startswith("The given system is a metal-organic framework (MOF) containing Silver (PubChem ID 23954)")


def test_extract_json() -> None:
    from hackframe.executors.tools_chem import extract_json

    assert extract_json('ok\n```json\n{"compounds": []}\n```') == {"compounds": []}
    assert extract_json('result: {"compounds": [{"a": 1}]} done') == {"compounds": [{"a": 1}]}


async def test_cif_to_smiles_inherits_root_cell(rt: Runtime, monkeypatch) -> None:
    llm_down(monkeypatch)
    out = await rt.run_workflow("material_pipeline", EXAMPLE)
    linker = next(c for c in out["evidence"]["components"] if c["system"] == "results/material/topology/5")
    assert len(linker["smiles_fragments"]) == 1  # was split into pieces before the cell was applied


def test_normalize_findings_accepts_real_model_shapes() -> None:
    from hackframe.executors.tools_chem import extract_json, normalize_findings

    q = [{"query_smiles": "[Ag]", "smiles_without_h": "[Ag]"}]
    # the shape seen live: one bare compound with PubChem-style keys inside a json block
    bare = 'Found it.\n```json\n{"CID": 23954, "name": "Silver", "MolecularFormula": "Ag", "InChIKey": "BQCADISMDOOEFD-UHFFFAOYSA-N"}\n```'
    hit = normalize_findings(extract_json(bare), q).compounds[0]
    assert (hit.cid, hit.name, hit.formula, hit.found, hit.query_smiles) == (23954, "Silver", "Ag", True, "[Ag]")
    # a plain list, and the last of several blocks wins
    assert normalize_findings([{"cid": 1}], q).compounds[0].cid == 1
    two = '```json\n{"draft": 1}\n```\nfinal:\n```json\n{"compounds": [{"query_smiles": "[Ag]", "found": false}]}\n```'
    assert normalize_findings(extract_json(two), q).compounds[0].found is False


async def test_prose_answer_is_reformatted(rt: Runtime, monkeypatch) -> None:
    llm_down(monkeypatch)
    calls = []

    async def prose(self, prompt):
        return "1. Structural analysis: this is metallic silver, CID 23954 ... (no JSON, ran out of words)"

    async def reformat(self, answer, queries):
        calls.append(answer)
        return {"compounds": [{"query_smiles": queries[0]["query_smiles"], "found": True, "cid": 999, "name": "Linker X"}]}

    monkeypatch.setattr(PubChemMCPExecutor, "ask_agent", prose)
    monkeypatch.setattr(PubChemMCPExecutor, "format_answer", reformat)
    out = await rt.run_workflow("material_pipeline", EXAMPLE)
    linker = next(c for c in out["evidence"]["components"] if c["system"] == "results/material/topology/5")
    assert calls and linker["compounds"][0]["cid"] == 999 and out["errors"] == {}
    assert "Linker X (PubChem ID 999)" in out["summary"]


async def test_safety_without_cids_is_not_an_error(rt: Runtime, monkeypatch) -> None:
    llm_down(monkeypatch)

    async def nothing(self, prompt):
        return '```json\n{"compounds": [{"query_smiles": "x", "found": false, "note": "no match"}]}\n```'

    monkeypatch.setattr(PubChemMCPExecutor, "ask_agent", nothing)
    out = await rt.run_workflow("material_pipeline", EXAMPLE)
    assert out["errors"] == {}


async def test_markdown_report_is_the_workflow_output(tmp_path: Path, monkeypatch) -> None:
    install_fakes(monkeypatch)
    llm_down(monkeypatch)
    rt = Runtime(Settings(hackframe_temp_dir=tmp_path / "temp"))  # workflows.yaml: output_field: report
    report = await rt.run_workflow("material_pipeline", EXAMPLE)

    assert isinstance(report, str)
    summary, table = report.split("\n\n", 1)
    assert summary.startswith("The given system is a metal-organic framework (MOF) containing Silver (PubChem ID 23954)")
    assert "vendors" not in summary  # no uses/vendors/hazards for elements
    lines = table.strip().splitlines()
    assert lines[:2] == ["| subsystem | description |", "|---|---|"]
    rows = dict(line.strip("| ").split(" | ") for line in lines[2:])
    assert list(rows) == [f"topology/{i}" for i in range(8)]  # every subsystem, in order
    assert rows["topology/0"] == rows["topology/1"] == "The original system"
    assert rows["topology/2"] == "The group of all metal atoms in the system"
    assert rows["topology/3"] == "A representative metal unit, Silver (PubChem ID 23954)"
    assert rows["topology/5"] == "A representative organic molecule (not identified in PubChem)"


async def test_llm_rows_fill_gaps_from_template(rt: Runtime, monkeypatch) -> None:
    async def plan(self, systems):
        return ToolPlan(material_class="molecular", class_reason="test: molecular route", plans=[SystemPlan(system=AG, tools=["cif_to_smiles", "pubchem"], reason="metal")])

    async def judge(self, evidence, *_):  # LLM returns only one row
        return MaterialSummary(summary="S.", subsystems=[SubsystemRow(system=AG, description="Ag row from LLM")], key_facts=[])

    monkeypatch.setattr(ToolFinderExecutor, "ask_llm", plan)
    monkeypatch.setattr(JudgeExecutor, "ask_llm", judge)
    out = await rt.run_workflow("material_pipeline", EXAMPLE)
    rows = {r["system"]: r["description"] for r in out["subsystems"]}
    assert len(rows) == 8 and rows[AG] == "Ag row from LLM" and rows["results/material/topology/0"] == "The original system"
    assert "| topology/3 | Ag row from LLM |" in out["report"]


async def test_expected_smiles_identified_by_structure_match(rt: Runtime) -> None:
    """The user's expected SMILES for the organic subsystem -> CID 85631 without any LLM call."""
    from hackframe.core.schemas import WorkItem
    from hackframe.tools.structure import _without_h

    expected = "[H]C1CNC([H])C([H])C1C([H])([H])C([H])([H])C([H])([H])C1C([H])C([H])NC([H])C1[H]"
    ex = PubChemMCPExecutor("pubchem", agent=rt.agent("pubchem"))
    item = WorkItem(run_id="r", artifact_id="a", system="results/material/topology/5", plan=["pubchem"],
                    findings={"cif_to_smiles": {"fragments": [expected], "heavy_fragments": [_without_h(expected)]}})
    out = await ex.run_tool(item)
    (hit,) = out["compounds"]
    assert (hit["cid"], hit["name"], hit["formula"]) == (85631, "1,3-Bis(4-piperidinyl)propane", "C13H26N2")
    assert out["parsed_by"] == "smiles" and out["matched_by_smiles"] == 1 and PROMPTS == []


async def test_mcp_details_failure_keeps_rest_data(rt: Runtime, monkeypatch) -> None:
    from hackframe.core.schemas import WorkItem

    async def mcp_down(self, cid):
        raise RuntimeError("MCP server not reachable")

    monkeypatch.setattr(PubChemMCPExecutor, "mcp_details", mcp_down)
    ex = PubChemMCPExecutor("pubchem", agent=rt.agent("pubchem"))
    item = WorkItem(run_id="r", artifact_id="a", system="s", plan=["pubchem"],
                    findings={"cif_to_smiles": {"fragments": ["[Ag]"], "heavy_fragments": ["[Ag]"]}})
    (hit,) = (await ex.run_tool(item))["compounds"]
    assert hit["cid"] == 23954 and hit["name"] == "Silver" and "MCP details unavailable" in hit["note"]


def canon(smiles):
    from rdkit import Chem
    mol = Chem.MolFromSmiles(smiles)
    return Chem.MolToSmiles(mol) if mol else smiles


LINKER = "N#Cc1ccc(C23CC4(c5ccc(C#N)cc5)CC(c5ccc(C#N)cc5)(C2)CC(c2ccc(C#N)cc2)(C3)C4)cc1"


async def test_flat_layout_bonded_smiles_identifies_linker(rt: Runtime, monkeypatch) -> None:
    """Flat systems (no `metadata`), CIF keys without '.cif'; the linker matches by its bond-order SMILES."""
    llm_down(monkeypatch)
    looked_up = []
    orig = FakeSmilesLookup.lookup

    async def lookup(self, smiles, include_synonyms=True, max_synonyms=5):
        looked_up.append(smiles)
        if canon(smiles) == canon(LINKER):  # PubChem matches structures, not strings (MOFid writes another SMILES)
            return PubChemResult(query_smiles=smiles, found=True, synonyms=["Linker A"],
                                 compound=PubChemCompound(cid=777, molecular_formula="C38H28N4", url="u"))
        return await orig(self, smiles, include_synonyms, max_synonyms)

    async def details(self, cid):
        return {"properties": {}, "synonyms": []}  # MCP: nothing extra

    monkeypatch.setattr(FakeSmilesLookup, "lookup", lookup)
    monkeypatch.setattr(PubChemMCPExecutor, "mcp_details", details)
    flat = json.loads((EXAMPLE_PATH.parent / "material_flat_example.json").read_text())
    out = await rt.run_workflow("material_pipeline", flat)

    comps = {c["system"].split("/")[-1]: c for c in out["evidence"]["components"]}
    assert set(comps) == {"3", "5", "7"}                                  # flat metadata read correctly
    assert comps["5"]["compounds"][0]["cid"] == 777 and LINKER in looked_up  # bonded SMILES tried first
    assert comps["3"]["compounds"][0]["cid"] == 23954
    assert out["errors"] == {} and PROMPTS == []                          # no LLM needed
    assert "Linker A (PubChem ID 777)" in out["summary"]
