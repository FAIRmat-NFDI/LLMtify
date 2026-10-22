"""Full pipeline: preprocess -> compact -> tool_finder =switch=> cif_to_smiles -> pubchem => gather -> judge.
LLM calls and PubChem are stubbed; CIF -> SMILES is real (ASE + RDKit)."""

import json
from pathlib import Path

import pytest

from hackframe.core.artifacts import ArtifactNotFound, ArtifactStore
from hackframe.core.runtime import Runtime
from hackframe.executors.judge import JudgeExecutor, MaterialSummary
from hackframe.executors.tool_finder import SystemPlan, ToolFinderExecutor, ToolPlan
from hackframe.executors.tools_chem import PubChemMCPExecutor, PubChemSafetyExecutor
from hackframe.settings import Settings
from hackframe.tools.pubchem import CompoundDetails

EXAMPLE_PATH = Path(__file__).parent.parent / "examples" / "material_example.json"
EXAMPLE = json.loads(EXAMPLE_PATH.read_text())
AG = "results/material/topology/3"


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


@pytest.fixture
def rt(tmp_path: Path, monkeypatch) -> Runtime:
    PROMPTS.clear()
    monkeypatch.setattr(PubChemMCPExecutor, "ask_agent", fake_mcp_agent)
    monkeypatch.setattr(PubChemSafetyExecutor, "client", FakeSafety())
    return Runtime(Settings(hackframe_temp_dir=tmp_path / "temp"))


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
        return ToolPlan(plans=[
            SystemPlan(system=AG, tools=["cif_to_smiles", "pubchem", "pubchem_safety", "made_up_tool"], reason="metal node"),
            SystemPlan(system="results/material/topology/1", tools=[], reason="bulk"),
        ])

    async def judge(self, evidence):
        seen["evidence"] = evidence
        return MaterialSummary(summary="The given system is a MOF containing Ag atoms (PubChem ID 23954).", key_facts=["Ag"])

    monkeypatch.setattr(ToolFinderExecutor, "ask_llm", plan)
    monkeypatch.setattr(JudgeExecutor, "ask_llm", judge)

    out = await rt.run_workflow("material_pipeline", EXAMPLE)

    assert out["generated_by"] == "llm" and "PubChem ID 23954" in out["summary"]
    assert set(seen["tools"]) == {"cif_to_smiles", "pubchem", "pubchem_safety"}   # builder injected tool list
    assert {s["system"]: s["has_cif"] for s in seen["systems"]}[AG] is True
    comp = seen["evidence"]["components"]
    assert [c["system"] for c in comp] == [AG]                                     # only planned subsystem
    assert comp[0]["smiles_fragments"] == ["[Ag]"]                                  # real ASE+RDKit result
    # MCP hit (name/formula) and pubchem_safety (vendor_count) merged per CID
    assert comp[0]["compounds"] == [{"cid": 23954, "name": "Silver", "formula": "Ag", "vendor_count": 12}]
    assert '"smiles_without_h": "[Ag]"' in PROMPTS[0]                                # agent got name-friendly SMILES


async def test_fallbacks_when_llm_is_down(rt: Runtime, monkeypatch) -> None:
    llm_down(monkeypatch)
    out = await rt.run_workflow("material_pipeline", EXAMPLE)

    assert out["generated_by"] == "fallback"
    assert out["summary"].startswith("The given system is a MOF containing Silver (PubChem ID 23954)")
    planned = [c["system"] for c in out["evidence"]["components"]]
    assert planned == [AG, "results/material/topology/5"]  # skill metadata: molecule subsystems with a CIF


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
    comp = out["evidence"]["components"][0]
    assert "formatting retry returned no JSON" in comp["errors"]["pubchem"] and "pubchem_safety" not in comp["errors"]
    assert out["summary"] == "No component could be identified in PubChem."


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
        return {"compounds": [{"query_smiles": queries[0]["query_smiles"], "found": True, "cid": 23954, "name": "Silver"}]}

    monkeypatch.setattr(PubChemMCPExecutor, "ask_agent", prose)
    monkeypatch.setattr(PubChemMCPExecutor, "format_answer", reformat)
    out = await rt.run_workflow("material_pipeline", EXAMPLE)
    ag = next(c for c in out["evidence"]["components"] if c["system"] == AG)
    assert calls and ag["compounds"][0]["cid"] == 23954 and ag["errors"] == {}
    assert "Silver (PubChem ID 23954)" in out["summary"]


async def test_safety_without_cids_is_not_an_error(rt: Runtime, monkeypatch) -> None:
    llm_down(monkeypatch)

    async def nothing(self, prompt):
        return '```json\n{"compounds": [{"query_smiles": "x", "found": false, "note": "no match"}]}\n```'

    monkeypatch.setattr(PubChemMCPExecutor, "ask_agent", nothing)
    out = await rt.run_workflow("material_pipeline", EXAMPLE)
    assert all(c["errors"] == {} for c in out["evidence"]["components"])
