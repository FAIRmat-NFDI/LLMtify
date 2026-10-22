"""Tool-level tests: CIF -> SMILES (real ASE/RDKit) and PubChem PUG-View parsing (mocked HTTP)."""

import json
from pathlib import Path

import httpx

from hackframe.tools.pubchem import PubChemClient
from hackframe.tools.structure import cif_to_smiles

CIFS = json.loads((Path(__file__).parent.parent / "examples" / "material_example.json").read_text())["cifs"]


def test_single_metal_atom() -> None:
    out = cif_to_smiles(CIFS["results/material/topology/3.cif"])
    assert out["smiles"] == "[Ag]" and out["fragments"] == ["[Ag]"] and out["n_atoms"] == 1
    assert out["heavy_fragments"] == ["[Ag]"]


def test_fragments_are_deduplicated() -> None:
    out = cif_to_smiles(CIFS["results/material/topology/2.cif"])
    assert out["smiles"] == "[Ag].[Ag]" and out["fragments"] == ["[Ag]"]


def test_hydrogen_only_fragments_dropped_and_largest_first() -> None:
    out = cif_to_smiles(CIFS["results/material/topology/5.cif"])
    assert "[HH]" in out["smiles"] and "[HH]" not in out["fragments"]
    assert all("[H]" not in f for f in out["heavy_fragments"])
    assert len(out["fragments"][0]) > len(out["fragments"][-1])


GHS = {"Record": {"Section": [{"TOCHeading": "Safety and Hazards", "Section": [{"TOCHeading": "GHS Classification",
    "Information": [
        {"Name": "Pictogram(s)", "Value": {"StringWithMarkup": [{"String": "  ", "Markup": [{"Extra": "Corrosive"}, {"Extra": "Irritant"}]}]}},
        {"Name": "Signal", "Value": {"StringWithMarkup": [{"String": "Danger"}]}},
        {"Name": "GHS Hazard Statements", "Value": {"StringWithMarkup": [{"String": "H314: Causes severe skin burns"}, {"String": "H315: Causes skin irritation"}]}},
        {"Name": "Pictogram(s)", "Value": {"StringWithMarkup": [{"String": " ", "Markup": [{"Extra": "Irritant"}]}]}},
    ]}]}]}}
USES = {"Record": {"Section": [{"Section": [{"Section": [{"TOCHeading": "Industry Uses",
    "Information": [{"Value": {"StringWithMarkup": [{"String": "Intermediate"}]}}]}]}]}]}}
CATS = {"SourceCategories": {"Categories": [{"Category": "Chemical Vendors", "Sources": [{}] * 65}, {"Category": "Literature", "Sources": [{}]}]}}


async def test_details_parses_pug_view() -> None:
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if "/categories/" in req.url.path:
            return httpx.Response(200, json=CATS)
        heading = req.url.params.get("heading")
        return httpx.Response(200, json={"GHS Classification": GHS, "Industry Uses": USES}[heading])

    client = PubChemClient(transport=httpx.MockTransport(handler), min_interval=0)
    d = await client.details(85631)
    assert d.ghs_pictograms == ["Corrosive", "Irritant"]  # deduplicated across sources
    assert d.ghs_signal == "Danger" and len(d.ghs_hazard_statements) == 2
    assert d.industry_uses == ["Intermediate"] and d.vendor_count == 65
    assert all("/rest/pug_view/" in u for u in calls)


async def test_details_missing_sections_stay_empty() -> None:
    client = PubChemClient(transport=httpx.MockTransport(lambda r: httpx.Response(404, json={})), min_interval=0)
    d = await client.details(23954)
    assert d.ghs_pictograms == [] and d.industry_uses == [] and d.vendor_count is None


def test_parent_cell_joins_fragments_across_boundary() -> None:
    from hackframe.tools.structure import read_cell

    cell = read_cell(CIFS["results/material/topology/0.cif"])
    assert read_cell(CIFS["results/material/topology/5.cif"]) is None  # Cartesian cut-out, no cell
    alone = cif_to_smiles(CIFS["results/material/topology/5.cif"])
    joined = cif_to_smiles(CIFS["results/material/topology/5.cif"], cell=cell)
    assert len(alone["fragments"]) > 1 and not alone["periodic"]
    assert joined["periodic"] and len(joined["heavy_fragments"]) == 1   # one linker molecule
    assert joined["formula"] == "C38H28N4"


def test_bond_orders_give_real_molecule() -> None:
    from hackframe.tools.structure import read_cell

    out = cif_to_smiles(CIFS["results/material/topology/5.cif"], cell=read_cell(CIFS["results/material/topology/0.cif"]))
    (bonded,) = out["bonded_fragments"]
    assert "C#N" in bonded and "c1ccc" in bonded and "@" not in bonded  # aromatic + nitrile, no 3D stereo
    assert out["heavy_fragments"][0].startswith("NCC1CCC")              # domain converter output unchanged
    assert cif_to_smiles(CIFS["results/material/topology/3.cif"])["bonded_fragments"] == ["[Ag]"]
