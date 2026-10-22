"""PubChem tool tests with a mocked HTTP transport (no network)."""

import json

import httpx

import hackframe.tools  # noqa: F401  (package import registers tools)
from hackframe.tools.builtin import TOOLS as REGISTRY
from hackframe.tools.pubchem import PubChemClient

ASPIRIN = "CC(=O)OC1=CC=CC=C1C(=O)O"

PROPS = {
    "PropertyTable": {
        "Properties": [
            {
                "CID": 2244,
                "MolecularFormula": "C9H8O4",
                "MolecularWeight": "180.16",
                "ConnectivitySMILES": ASPIRIN,
                "SMILES": ASPIRIN,
                "IUPACName": "2-acetyloxybenzoic acid",
                "InChIKey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
                "XLogP": 1.2,
                "TPSA": 63.6,
                "HBondDonorCount": 1,
                "HBondAcceptorCount": 4,
            }
        ]
    }
}
SYNS = {"InformationList": {"Information": [{"CID": 2244, "Synonym": ["aspirin", "acetylsalicylic acid", "50-78-2"]}]}}


def make_client(handler) -> PubChemClient:
    return PubChemClient(transport=httpx.MockTransport(handler), min_interval=0, max_retries=1)


async def test_found_with_synonyms() -> None:
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, req.content.decode()))
        if "/property/" in req.url.path:
            return httpx.Response(200, json=PROPS)
        return httpx.Response(200, json=SYNS)

    res = await make_client(handler).lookup(ASPIRIN, max_synonyms=2)
    assert res.found and res.compound.cid == 2244
    assert res.compound.molecular_weight == 180.16
    assert res.compound.url.endswith("/compound/2244")
    assert res.synonyms == ["aspirin", "acetylsalicylic acid"]
    assert seen[0][0] == "POST" and "smiles=" in seen[0][2]  # SMILES sent as form body


async def test_legacy_smiles_keys() -> None:
    legacy = {"PropertyTable": {"Properties": [{"CID": 702, "CanonicalSMILES": "CCO", "IsomericSMILES": "CCO"}]}}
    res = await make_client(lambda r: httpx.Response(200, json=legacy)).lookup("CCO", include_synonyms=False)
    assert res.compound.smiles == "CCO" and res.compound.connectivity_smiles == "CCO"


async def test_not_found_404_and_cid_zero() -> None:
    fault = {"Fault": {"Code": "PUGREST.NotFound", "Message": "No CID found"}}
    r404 = await make_client(lambda r: httpx.Response(404, json=fault)).lookup("C1CC1CCCCX")
    assert not r404.found and r404.error == "no PubChem match"

    zero = {"PropertyTable": {"Properties": [{"CID": 0}]}}
    r0 = await make_client(lambda r: httpx.Response(200, json=zero)).lookup("C[Si](C)(C)C(C)(C)C")
    assert not r0.found


async def test_bad_request_and_retry_on_503() -> None:
    bad = {"Fault": {"Code": "PUGREST.BadRequest", "Message": "Unable to standardize the given structure"}}
    rbad = await make_client(lambda r: httpx.Response(400, json=bad)).lookup("not-a-smiles")
    assert not rbad.found and "BadRequest" in rbad.error

    calls = {"n": 0}

    def flaky(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] == 1 else httpx.Response(200, json=PROPS)

    client = PubChemClient(transport=httpx.MockTransport(flaky), min_interval=0, max_retries=2)
    client_sleep_free = await client.lookup(ASPIRIN, include_synonyms=False)
    assert client_sleep_free.found and calls["n"] == 2


async def test_cache() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=PROPS)

    c = make_client(handler)
    await c.lookup(ASPIRIN, include_synonyms=False)
    await c.lookup(ASPIRIN, include_synonyms=False)
    assert calls["n"] == 1


def test_tool_registered_with_schema() -> None:
    t = REGISTRY.get("pubchem_lookup")
    schema = json.dumps(t.to_json_schema_spec() if hasattr(t, "to_json_schema_spec") else t.parameters())
    assert "smiles" in schema and "include_synonyms" in schema
