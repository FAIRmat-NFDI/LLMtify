"""MOF route (mofid -> pubchem -> judge) and protein route (cif_to_seq -> pdb_lookup -> judge).
LLMs, PubChem and RCSB are stubbed; MOFid output is a real run captured from the example root CIF.
CIF -> sequence is real (Open Babel / topology method)."""

import asyncio
import json
import time
import shutil
from pathlib import Path

import pytest

from matgentify.core.runtime import Runtime
from matgentify.executors.judge import JudgeExecutor, MaterialSummary
from matgentify.executors.tool_finder import SystemPlan, ToolFinderExecutor, ToolPlan
from matgentify.executors.tools_mof_protein import PDBLookupExecutor
from matgentify.settings import Settings
from matgentify.tools import protein as protein_tools
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
AG, LINKER_SYS = "results/material/topology/3", "results/material/topology/5"


def judge_down(monkeypatch) -> None:
    async def down(self, *_):
        raise ConnectionError("down")

    monkeypatch.setattr(JudgeExecutor, "ask_llm", down)


async def test_mof_route_mofid_and_subsystems_run_in_parallel(rt: Runtime, monkeypatch) -> None:
    """LLM says MOF: root -> mofid AND every molecule subsystem -> cif_to_smiles -> pubchem -> pubchem_safety,
    in parallel lanes (the slow MOFid run overlaps the PubChem lookups), then fan-in to the judge."""
    from matgentify.executors.tools_chem import CifToSmilesExecutor
    from matgentify.executors.tools_mof_protein import MOFidExecutor
    from tests.test_material_pipeline import fake_mofid

    times: dict[str, float] = {}

    def slow_mofid(cif, timeout=600):
        times["mofid_start"] = time.perf_counter()
        time.sleep(1.0)
        times["mofid_end"] = time.perf_counter()
        return fake_mofid(cif)

    orig = CifToSmilesExecutor.run_tool

    async def smiles(self, item):
        times.setdefault("smiles_" + item.system, time.perf_counter())
        return await orig(self, item)

    from matgentify.executors.tools_chem import PubChemMCPExecutor
    orig_lookup = PubChemMCPExecutor.client.lookup

    async def slow_lookup(smiles, *a, **kw):  # PubChem round trip
        await asyncio.sleep(0.4)
        res = await orig_lookup(smiles, *a, **kw)
        times["pubchem_end"] = time.perf_counter()
        return res

    async def plan(self, systems):
        return ToolPlan(material_class="MOF", class_reason="label MOF, Ag + organic SBUs", plans=[])

    monkeypatch.setattr(MOFidExecutor, "run_mofid", staticmethod(slow_mofid))
    monkeypatch.setattr(CifToSmilesExecutor, "run_tool", smiles)
    monkeypatch.setattr(PubChemMCPExecutor.client, "lookup", slow_lookup)
    monkeypatch.setattr(ToolFinderExecutor, "ask_llm", plan)
    judge_down(monkeypatch)
    out = await rt.run_workflow("material_pipeline", EXAMPLE)

    ev = out["evidence"]
    assert ev["material_class"] == "mof"
    comps = {c["system"]: c for c in ev["components"]}
    assert list(comps) == [ROOT, AG, LINKER_SYS]                         # MOFid + every molecule subsystem
    assert list(comps[ROOT]["tool_summaries"]) == ["mofid"]
    assert comps[AG]["compounds"][0]["cid"] == 23954                     # subsystem -> SMILES -> PubChem
    # concurrency: the MOFid run and the subsystem PubChem lookups overlap in time
    assert times["smiles_" + AG] < times["mofid_end"] and times["mofid_start"] < times["pubchem_end"]
    s = out["summary"]
    assert s.startswith("The given system is a metal-organic framework (MOF) containing Silver (PubChem ID 23954)")
    assert "MOFid identifies 1 node type(s) (Ag) and 1 organic linker type(s) (C38H28N4)" in s
    assert "could not determine its net topology" in s                    # never guess a topology


async def test_mof_fallback_without_llm(rt: Runtime, monkeypatch) -> None:
    llm_down(monkeypatch)
    out = await rt.run_workflow("material_pipeline", EXAMPLE)
    assert out["evidence"]["material_class"] == "mof"
    assert [c["system"] for c in out["evidence"]["components"]] == [ROOT, AG, LINKER_SYS]


async def test_mof_without_metainfo_processes_every_subsystem(rt: Runtime, monkeypatch) -> None:
    """metainfo=false: no labels/structural types -> the LLM judges MOF; every non-root subsystem with a CIF runs."""
    async def plan(self, systems):
        assert all(s["label"] is None and s["structural_type"] is None for s in systems)
        return ToolPlan(material_class="mof", class_reason="periodic Ag + C/H/N", plans=[])

    monkeypatch.setattr(ToolFinderExecutor, "ask_llm", plan)
    judge_down(monkeypatch)
    out = await rt.run_workflow("material_pipeline", {**EXAMPLE, "metainfo": False})
    systems = [c["system"] for c in out["evidence"]["components"]]
    with_cif = [k.removesuffix(".cif") for k in EXAMPLE["cifs"]]
    assert systems[0] == ROOT and sorted(systems) == sorted(with_cif)


async def test_mof_missing_image_is_reported(rt: Runtime, monkeypatch) -> None:
    from matgentify.executors.tools_mof_protein import MOFidExecutor
    from matgentify.tools.mofid import get_mofid

    llm_down(monkeypatch)
    monkeypatch.setattr(MOFidExecutor, "run_mofid", staticmethod(get_mofid))
    monkeypatch.setattr("matgentify.tools.mofid.shutil.which", lambda _: "/usr/bin/singularity")
    monkeypatch.setattr("matgentify.tools.mofid.sif_candidates", lambda: [Path("/nonexistent/mofid.sif")])
    out = await rt.run_workflow("material_pipeline", EXAMPLE)
    assert "mofid.sif not found" in json.dumps(out["errors"])
    assert out["summary"].startswith("The given system is a metal-organic framework (MOF) containing Silver")


# ---------------------------------------------------------------- agentic judge
async def test_judge_reads_cif_when_evidence_is_thin(rt: Runtime, monkeypatch) -> None:
    """The judge agent may call read_cif and add knowledge-based notes; they are reported separately."""
    seen = {}

    async def judge(self, evidence, message, reads):
        read_cif = self.cif_reader(message, reads)
        seen["cif"] = await read_cif(LINKER_SYS)
        seen["missing"] = await read_cif("results/material/topology/999")    # unknown system
        for _ in range(5):
            last = await read_cif(ROOT)
        seen["limit"] = last
        return MaterialSummary(
            summary="The given system is a MOF.", subsystems=[],
            key_facts=["MOFid"], knowledge_notes=["The linker likely binds Ag through its nitrile groups."],
        )

    llm_down(monkeypatch)
    monkeypatch.setattr(JudgeExecutor, "ask_llm", judge)
    rt.config.workflows["material_pipeline"].output_field = None
    out = await rt.run_workflow("material_pipeline", EXAMPLE)

    assert seen["cif"].startswith("data_") and "no CIF" in seen["missing"] and "read limit" in seen["limit"]
    assert out["generated_by"] == "llm" and out["cif_reads"][0] == LINKER_SYS and len(out["cif_reads"]) == 4
    assert out["knowledge_notes"] == ["The linker likely binds Ag through its nitrile groups."]
    assert "Not from the mined data" in out["report"] and "nitrile groups" in out["report"]


# ---------------------------------------------------------------- protein
def test_cif_to_sequence_real() -> None:
    assert protein_tools.cif_to_sequences(PEPTIDE_CIF) == [PEPTIDE_SEQ]


PDB_HIT = {"result_set": [{"identifier": "1ABC_1", "score": 1.0}]}
PDB_DATA = {"data": {"entry": {"rcsb_id": "1ABC", "polymer_entities": [{
    "rcsb_polymer_entity": {"pdbx_description": "Test peptide"},
    "rcsb_entity_source_organism": [{"ncbi_scientific_name": "Escherichia coli", "rcsb_gene_name": [{"value": "tst"}]}],
    "uniprots": [{"rcsb_id": "P00001"}],
}]}}}


def short_pdb_queries(rt: Runtime, n: int = 10) -> None:
    """The test peptide has 16 residues; RCSB needs >= 20 by default."""
    step = next(s for s in rt.config.workflows["material_pipeline"].steps if s.id == "pdb_lookup")
    step.model_extra["min_length"] = n


async def test_protein_fallback_routes_seq_then_pdb(rt: Runtime, monkeypatch) -> None:
    llm_down(monkeypatch)
    short_pdb_queries(rt)
    searched = []
    monkeypatch.setattr(PDBLookupExecutor, "search", staticmethod(lambda seq, cutoff, *_: searched.append(seq) or PDB_HIT))
    monkeypatch.setattr(PDBLookupExecutor, "lookup", staticmethod(lambda pdb_id: PDB_DATA))
    out = await rt.run_workflow("material_pipeline", protein_input())

    assert out["evidence"]["material_class"] == "protein"
    assert searched == [PEPTIDE_SEQ]                                  # cif_to_seq output fed pdb_lookup
    assert out["summary"].startswith("The given system contains a protein chain (protein chain A, 16 residues).")
    assert "protein chain A is most similar to PDB entry 1ABC (Test peptide, Escherichia coli)" in out["summary"]
    assert "most similar to PDB 1ABC" in out["subsystems"][0]["description"]
    assert out["errors"] == {}


async def test_protein_without_pdb_match(rt: Runtime, monkeypatch) -> None:
    llm_down(monkeypatch)
    short_pdb_queries(rt)
    monkeypatch.setattr(PDBLookupExecutor, "search", staticmethod(lambda seq, cutoff, *_: {"result_set": []}))
    out = await rt.run_workflow("material_pipeline", protein_input())
    assert "a protein chain (protein chain A, 16 residues)" in out["summary"] and "1ABC" not in out["summary"]
    assert out["errors"] == {}


async def test_short_sequence_is_not_sent_to_rcsb(rt: Runtime, monkeypatch) -> None:
    llm_down(monkeypatch)
    monkeypatch.setattr(PDBLookupExecutor, "search", staticmethod(lambda *a: pytest.fail("RCSB must not be queried")))
    rt.config.workflows["material_pipeline"].output_field = None
    out = await rt.run_workflow("material_pipeline", protein_input())
    assert out["errors"] == {}
    note = out["evidence"]["components"][0]["tool_summaries"]["pdb_lookup"]["note"]
    assert "too short for the RCSB sequence search (min 20)" in note


# ---------------------------------------------------------------- real MOFid (needs singularity + mofid.sif)
def _sif():
    from matgentify.tools.mofid import MOFidError, find_sif
    try:
        return find_sif()
    except MOFidError:
        return None


@pytest.mark.skipif(not (shutil.which("singularity") and _sif()), reason="singularity / mofid.sif not installed")
def test_real_mofid_on_example_root() -> None:
    from matgentify.tools.mofid import get_mofid

    key = next(k for k in EXAMPLE["cifs"] if k.startswith(ROOT))
    res = get_mofid(EXAMPLE["cifs"][key])
    assert res["mofid_parsed"]["nodes"] == ["[Ag]"]
    assert res["smiles_linkers"] == MOFID_OUT["smiles_linkers"]


# ---------------------------------------------------------------- protein-DNA complex (real NOMAD entry, trimmed)
COMPLEX = json.loads((DATA / "protein_dna_complex.json").read_text())
T = "results/material/topology/"
LOG_PLAN = {  # the plan the HU model returned for this entry (class 'biomolecular_complex')
    "1": ["cif_to_seq"], "2": ["cif_to_seq"], "11": ["cif_to_seq"], "12": ["cif_to_seq"], "15": ["cif_to_seq"],
    "16": ["cif_to_seq"], "60": ["cif_to_smiles", "pubchem", "pubchem_safety"], "62": ["cif_to_smiles", "pubchem"],
    "64": ["cif_to_smiles", "pubchem"], "14": ["cif_to_smiles", "pubchem"],
}


def complex_fakes(monkeypatch) -> list:
    searched: list = []
    monkeypatch.setattr(PDBLookupExecutor, "search",
                        staticmethod(lambda seq, cutoff, seq_type="protein": searched.append((seq_type, len(seq))) or PDB_HIT))
    monkeypatch.setattr(PDBLookupExecutor, "lookup", staticmethod(lambda pdb_id: PDB_DATA))
    return searched


async def test_complex_free_form_class_is_normalized_and_routed(rt: Runtime, monkeypatch) -> None:
    async def plan(self, systems):
        return ToolPlan(material_class="biomolecular_complex", class_reason="protein + DNA + ligand", plans=[
            SystemPlan(system=T + k, tools=v, reason="x") for k, v in LOG_PLAN.items()])

    searched = complex_fakes(monkeypatch)
    monkeypatch.setattr(ToolFinderExecutor, "ask_llm", plan)
    judge_down(monkeypatch)
    out = await rt.run_workflow("material_pipeline", COMPLEX)

    ev = out["evidence"]
    assert ev["material_class"] == "protein"                                   # 'biomolecular_complex' normalized
    comps = {c["system"].split("/")[-1]: c for c in ev["components"]}
    assert set(comps) == {"2", "12", "16", "14", "60", "62", "64"}               # groups -> molecule children
    for k in ("2", "12", "16"):
        assert set(comps[k]["tool_summaries"]) == {"cif_to_seq", "pdb_lookup"}   # chains: seq -> PDB
    dna = comps["2"]["tool_summaries"]["cif_to_seq"]
    assert (dna["polymer"], dna["sequence"]) == ("dna", "CGGGCATGCCC")
    assert "too short" in comps["2"]["tool_summaries"]["pdb_lookup"]["note"]   # 11 nt: not sent to RCSB
    prot = comps["16"]["tool_summaries"]["cif_to_seq"]
    assert prot["polymer"] == "protein" and prot["length"] == 197
    assert searched == [("protein", 197)]
    assert out["errors"] == {}
    s = out["summary"]
    assert s.startswith("The given system contains a DNA chain (DNA_chain_C, 11 nucleotides), a DNA chain "
                        "(DNA_chain_D, 11 nucleotides) and a protein chain (Protein_chain_G, 197 residues).")


async def test_complex_fallback_keeps_ions_out_of_chains(rt: Runtime, monkeypatch) -> None:
    searched = complex_fakes(monkeypatch)
    llm_down(monkeypatch)
    out = await rt.run_workflow("material_pipeline", COMPLEX)
    plans = {c["system"].split("/")[-1]: c["tool_summaries"] for c in out["evidence"]["components"]}
    assert out["evidence"]["material_class"] == "protein"
    assert "cif_to_seq" in plans["2"] and "cif_to_seq" in plans["16"]
    assert "cif_to_seq" not in plans["14"]                                      # 'Ion_chain_E' (Zn) is an ion
    assert out["errors"] == {} and searched == [("protein", 197)]


def test_ion_and_protonated_ligand_smiles() -> None:
    from matgentify.core.material import cif_key
    from matgentify.executors.tools_chem import neutralize
    from matgentify.tools.structure import cif_to_smiles

    cif = lambda n: COMPLEX["cifs"][cif_key(T + str(n), COMPLEX["cifs"])]  # noqa: E731
    assert cif_to_smiles(cif(64))["fragments"] == ["[Na]"]                 # not [NaH] (sodium hydride)
    lig = cif_to_smiles(cif(60))["bonded_fragments"][0]
    assert "[NH+]" in lig                                                   # MD ligand: protonated amine
    assert neutralize(lig) == "CN1CCC(Nc2cccc3c(CC(F)(F)F)c(C#CCNc4ccc(P(C)(C)=O)cc4)sc23)CC1"
