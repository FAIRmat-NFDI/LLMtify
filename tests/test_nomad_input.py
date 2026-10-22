"""NOMAD input: entry URL -> archive -> {systems, cifs}. The archive is rebuilt from the flat example
in NOMAD's shape (run/system/atoms in metres + topology indices), so no network is needed."""

import io
import json
from pathlib import Path

import numpy as np
import pytest
from ase.io import read

from matgentify.tools import nomad
from matgentify.tools.structure import cif_to_smiles, read_cell

FLAT = json.loads((Path(__file__).parent.parent / "examples" / "material_flat_example.json").read_text())
ROOT_REF = "#/run/0/system/0/atoms"


def make_archive() -> dict:
    root = read(io.StringIO(FLAT["cifs"]["results/material/topology/0"]), format="cif")
    archive = {
        "run": [{"system": [{"atoms": {
            "labels": root.get_chemical_symbols(),
            "positions": (root.positions * 1e-10).tolist(),
            "lattice_vectors": (np.asarray(root.cell) * 1e-10).tolist(),
            "periodic": [True, True, True],
        }}]}],
        "results": {"material": {"topology": []}},
    }
    for key, meta in FLAT["systems"].items():
        item = {k: meta.get(k) for k in nomad._META}
        item["system_relation"] = {"type": meta["system_relation"] or "subsystem"}
        item["atoms_ref"] = ROOT_REF
        if meta["system_relation"] != "root":
            sub = read(io.StringIO(FLAT["cifs"][key]), format="cif")
            idx = []
            for p, s in zip(sub.positions, sub.get_chemical_symbols()):
                d = root.cell.scaled_positions(np.vstack([root.positions - p]))
                d -= np.round(d)
                dist = np.linalg.norm(d @ np.asarray(root.cell), axis=1)
                j = int(np.argmin(dist))
                assert dist[j] < 1e-3 and root[j].symbol == s
                idx.append(j)
            item["indices"] = [idx]
        archive["results"]["material"]["topology"].append(item)
    return archive


@pytest.mark.parametrize("ref, expected", [
    ("abc123DEF456ghi", "abc123DEF456ghi"),
    ("https://nomad-lab.eu/prod/v1/gui/search/entries/entry/id/abc123DEF456ghi", "abc123DEF456ghi"),
    ("https://nomad-lab.eu/prod/v1/gui/user/uploads/upload/id/UPL0AD123/entry/id/abc123DEF456ghi/data", "abc123DEF456ghi"),
    ("https://nomad-lab.eu/prod/v1/api/v1/entries/abc123DEF456ghi/archive", "abc123DEF456ghi"),
    ("https://example.org/x?entry_id=abc123DEF456ghi", "abc123DEF456ghi"),
])
def test_parse_entry_id(ref: str, expected: str) -> None:
    assert nomad.parse_entry_id(ref) == expected


def test_parse_entry_id_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        nomad.parse_entry_id("not an id!")


def test_archive_to_material_round_trip() -> None:
    material = nomad.archive_to_material(make_archive(), "abc123DEF456ghi")
    assert material["entry_id"] == "abc123DEF456ghi"
    assert list(material["systems"]) == list(FLAT["systems"])
    for key, meta in material["systems"].items():
        orig = FLAT["systems"][key]
        assert (meta["label"], meta["structural_type"], meta["system_relation"]) == \
               (orig["label"], orig["structural_type"], orig["system_relation"] or "subsystem")
        new = read(io.StringIO(material["cifs"][key]), format="cif")
        old = read(io.StringIO(FLAT["cifs"][key]), format="cif")
        assert new.get_chemical_formula() == old.get_chemical_formula()       # same atoms per subsystem
    root = read(io.StringIO(material["cifs"]["results/material/topology/0"]), format="cif")
    assert root.cell.volume > 1 and root.pbc.all()                            # root keeps its cell (Angstrom)
    cell = read_cell(material["cifs"]["results/material/topology/0"])
    a = cif_to_smiles(material["cifs"]["results/material/topology/5"], cell=cell)
    b = cif_to_smiles(FLAT["cifs"]["results/material/topology/5"], cell=read_cell(FLAT["cifs"]["results/material/topology/0"]))
    assert a["bonded_fragments"] == b["bonded_fragments"] and "C#N" in a["bonded_fragments"][0]


async def test_pipeline_from_entry_url(tmp_path: Path, monkeypatch) -> None:
    from tests.test_material_pipeline import install_fakes, llm_down
    from matgentify.core.runtime import Runtime
    from matgentify.settings import Settings

    install_fakes(monkeypatch)
    llm_down(monkeypatch)
    fetched = []
    monkeypatch.setattr(nomad, "fetch_archive", lambda entry_id, client=None: fetched.append(entry_id) or make_archive())

    rt = Runtime(Settings(hackframe_temp_dir=tmp_path / "temp"))
    url = "https://nomad-lab.eu/prod/v1/gui/search/entries/entry/id/abc123DEF456ghi"
    report = await rt.run_workflow("material_pipeline", {"entry": url})

    assert fetched == ["abc123DEF456ghi"]
    assert isinstance(report, str) and "metal-organic framework (MOF)" in report   # strict MOF route: mofid -> judge
    assert "| topology/3 |" in report and "| topology/7 |" in report
    (saved,) = list(rt.artifacts.root.glob("*.json"))
    assert json.loads(saved.read_text())["entry_id"] == "abc123DEF456ghi"


async def test_bare_url_input_and_full_json_still_work(tmp_path: Path, monkeypatch) -> None:
    from tests.test_material_pipeline import install_fakes, llm_down
    from matgentify.core.runtime import Runtime
    from matgentify.settings import Settings

    install_fakes(monkeypatch)
    llm_down(monkeypatch)
    monkeypatch.setattr(nomad, "fetch_archive", lambda entry_id, client=None: make_archive())
    rt = Runtime(Settings(hackframe_temp_dir=tmp_path / "temp"))
    assert "MOFid identifies 1 node type(s) (Ag)" in await rt.run_workflow("material_pipeline", "abc123DEF456ghi")
    assert "MOFid identifies 1 node type(s) (Ag)" in await rt.run_workflow("material_pipeline", FLAT)


@pytest.mark.parametrize("flag", [False, "false"])
async def test_metainfo_false_nulls_labels_before_saving(tmp_path: Path, monkeypatch, flag) -> None:
    from tests.test_material_pipeline import install_fakes, llm_down
    from matgentify.core.material import system_meta
    from matgentify.core.runtime import Runtime
    from matgentify.settings import Settings

    install_fakes(monkeypatch)
    llm_down(monkeypatch)
    monkeypatch.setattr(nomad, "fetch_archive", lambda entry_id, client=None: make_archive())
    rt = Runtime(Settings(hackframe_temp_dir=tmp_path / "temp"))
    await rt.run_workflow("material_pipeline", {"entry": "abc123DEF456ghi", "metainfo": flag})

    (saved,) = list(rt.artifacts.root.glob("*.json"))
    systems = json.loads(saved.read_text())["systems"]
    for entry in systems.values():
        meta = system_meta(entry)
        assert (meta["label"], meta["structural_type"], meta["building_block"]) == (None, None, None)
        assert meta["system_relation"] and meta["chemical_formula_hill"]      # everything else kept


async def test_metainfo_true_keeps_labels(tmp_path: Path, monkeypatch) -> None:
    from tests.test_material_pipeline import install_fakes, llm_down
    from matgentify.core.runtime import Runtime
    from matgentify.settings import Settings

    install_fakes(monkeypatch)
    llm_down(monkeypatch)
    rt = Runtime(Settings(hackframe_temp_dir=tmp_path / "temp"))
    await rt.run_workflow("material_pipeline", {**FLAT, "metainfo": True})
    (saved,) = list(rt.artifacts.root.glob("*.json"))
    data = json.loads(saved.read_text())
    assert "metainfo" not in data and data["systems"] == FLAT["systems"]
