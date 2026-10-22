"""MOF route (mofid -> pubchem -> judge) and protein route (cif_to_seq -> pdb_lookup -> judge).
LLMs, PubChem and RCSB are stubbed; MOFid output is a real run captured from the example root CIF.
CIF -> sequence is real (Open Babel / topology method)."""

import json
import shutil
from pathlib import Path

import pytest

from hackframe.core.runtime import Runtime
from hackframe.executors.judge import JudgeExecutor
from hackframe.executors.tool_finder import SystemPlan, ToolFinderExecutor, ToolPlan
from hackframe.executors.tools_mof_protein import PDBLookupExecutor
from hackframe.settings import Settings
from hackframe.tools import protein as protein_tools
from tests.test_material_pipeline import EXAMPLE, ROOT, install_fakes, llm_down

DATA = Path(__file__).parent / "data"
PEPTIDE_CIF = (DATA / "peptide.cif").read_text()
PEPTIDE_SEQ = "MKTAYIAKQRGSWFDE"
MOFID_OUT = json.loads((DATA / "mofid_output.json").read_text())


@pytest.fixture
def rt(tmp_path: Path, monkeypatch) -> Runtime:
    install_fakes(monkeypatch)
    runtime = Runtime(Settings(hackframe_temp_dir=tmp_path / "temp"))
    runtime.config.workflows["material_pipeline"].output_field = None
    return runtime


def protein_input(label: str = "protein chain A") -> dict:
    root = "results/material/topology/0"
    return {
        "systems": {root: {
            "label": label, "structural_type": None, "system_relation": "root", "parent_system": None,
            "child_systems": [], "chemical_formula_hill": "C87H131N23O25S", "elements": ["C", "H", "N", "O", "S"],
            "n_atoms": 266, "has_cif": True,
        }},
        "cifs": {root: PEPTIDE_CIF},
    }


# ---------------------------------------------------------------- MOF
async def test_mof_route_is_strict_mofid_then_judge(rt: Runtime, monkeypatch) -> None:
    """LLM says MOF but also plans CIF->SMILES/PubChem everywhere: the route is enforced -> only mofid on the root."""
    async def plan(self, systems):
        return ToolPlan(material_class="MOF", class_reason="label MOF, Ag + organic SBUs", plans=[
            SystemPlan(system=s["system"], tools=["cif_to_smiles", "pubchem", "pubchem_safety"], reason="x")
            for s in systems if s["has_cif"]
        ])

    monkeypatch.setattr(ToolFinderExecutor, "ask_llm", plan)
    monkeypatch.setattr(JudgeExecutor, "ask_llm", lambda self, ev: (_ for _ in ()).throw(ConnectionError("down")))
    out = await rt.run_workflow("material_pipeline", EXAMPLE)

    ev = out["evidence"]
    assert ev["material_class"] == "mof"
    (root,) = ev["components"]                                        # only the root structure was processed
    assert root["system"] == ROOT and list(root["tool_summaries"]) == ["mofid"] and root["compounds"] == []
    mof = root["tool_summaries"]["mofid"]
    assert mof["nodes"] == ["[Ag]"] and mof["linker_formulas"] == ["C38H28N4"] and mof["topology_failed"] is True
    assert out["summary"].startswith("The given system is a metal-organic framework (MOF). MOFid identifies "
                                     "1 node type(s) (Ag) and 1 organic linker type(s) (C38H28N4)")
    assert "could not determine its net topology" in out["summary"]   # never guess a topology
    assert "PubChem" not in out["summary"] and "not identified" not in json.dumps(out["subsystems"])
    assert out["errors"] == {}


async def test_mof_fallback_without_llm(rt: Runtime, monkeypatch) -> None:
    llm_down(monkeypatch)
    out = await rt.run_workflow("material_pipeline", EXAMPLE)
    assert out["evidence"]["material_class"] == "mof"
    assert [c["system"] for c in out["evidence"]["components"]] == [ROOT]


async def test_mof_missing_image_is_reported(rt: Runtime, monkeypatch) -> None:
    from hackframe.executors.tools_mof_protein import MOFidExecutor
    from hackframe.tools.mofid import get_mofid

    llm_down(monkeypatch)
    monkeypatch.setattr(MOFidExecutor, "run_mofid", staticmethod(get_mofid))
    monkeypatch.setattr("hackframe.tools.mofid.shutil.which", lambda _: "/usr/bin/singularity")
    monkeypatch.setattr("hackframe.tools.mofid.sif_candidates", lambda: [Path("/nonexistent/mofid.sif")])
    out = await rt.run_workflow("material_pipeline", EXAMPLE)
    assert "mofid.sif not found" in json.dumps(out["errors"])
    assert out["summary"].startswith("The given system is a metal-organic framework (MOF)")


# ---------------------------------------------------------------- protein
def test_cif_to_sequence_real() -> None:
    assert protein_tools.cif_to_sequences(PEPTIDE_CIF) == [PEPTIDE_SEQ]


PDB_HIT = {"result_set": [{"identifier": "1ABC_1", "score": 1.0}]}
PDB_DATA = {"data": {"entry": {"rcsb_id": "1ABC", "polymer_entities": [{
    "rcsb_polymer_entity": {"pdbx_description": "Test peptide"},
    "rcsb_entity_source_organism": [{"ncbi_scientific_name": "Escherichia coli", "rcsb_gene_name": [{"value": "tst"}]}],
    "uniprots": [{"rcsb_id": "P00001"}],
}]}}}


async def test_protein_fallback_routes_seq_then_pdb(rt: Runtime, monkeypatch) -> None:
    llm_down(monkeypatch)
    searched = []
    monkeypatch.setattr(PDBLookupExecutor, "search", staticmethod(lambda seq, cutoff: searched.append(seq) or PDB_HIT))
    monkeypatch.setattr(PDBLookupExecutor, "lookup", staticmethod(lambda pdb_id: PDB_DATA))
    out = await rt.run_workflow("material_pipeline", protein_input())

    assert out["evidence"]["material_class"] == "protein"
    assert searched == [PEPTIDE_SEQ]                                  # cif_to_seq output fed pdb_lookup
    assert out["summary"].startswith("The given system is a protein chain of 16 residues.")
    assert "PDB entry 1ABC (Test peptide, Escherichia coli)" in out["summary"]
    assert "most similar to PDB 1ABC" in out["subsystems"][0]["description"]
    assert out["errors"] == {}


async def test_protein_without_pdb_match(rt: Runtime, monkeypatch) -> None:
    llm_down(monkeypatch)
    monkeypatch.setattr(PDBLookupExecutor, "search", staticmethod(lambda seq, cutoff: {"result_set": []}))
    out = await rt.run_workflow("material_pipeline", protein_input())
    assert "protein chain of 16 residues" in out["summary"] and "1ABC" not in out["summary"]
    assert out["errors"] == {}


# ---------------------------------------------------------------- real MOFid (needs singularity + mofid.sif)
def _sif():
    from hackframe.tools.mofid import MOFidError, find_sif
    try:
        return find_sif()
    except MOFidError:
        return None


@pytest.mark.skipif(not (shutil.which("singularity") and _sif()), reason="singularity / mofid.sif not installed")
def test_real_mofid_on_example_root() -> None:
    from hackframe.tools.mofid import get_mofid

    key = next(k for k in EXAMPLE["cifs"] if k.startswith(ROOT))
    res = get_mofid(EXAMPLE["cifs"][key])
    assert res["mofid_parsed"]["nodes"] == ["[Ag]"]
    assert res["smiles_linkers"] == MOFID_OUT["smiles_linkers"]
