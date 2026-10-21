"""PubChem PUG REST lookup: SMILES -> structured compound record (JSON).

API:   https://pubchem.ncbi.nlm.nih.gov/rest/pug
Calls: POST /compound/smiles/property/<props>/JSON   (form field `smiles`; POST avoids
                                                     URL-escaping '/', '#', '\\' in SMILES)
       GET  /compound/cid/<cid>/synonyms/JSON
Policy: max ~5 req/s and 400 req/min per client; 503 = server busy -> back off and retry.
"""

import asyncio
import json
import time
from typing import Annotated, Any

import httpx
from pydantic import BaseModel, Field

from hackframe.tools.builtin import register_tool

BASE_URL = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
WEB_URL = "https://pubchem.ncbi.nlm.nih.gov/compound/{cid}"

# Requested properties. Both SMILES naming schemes are parsed (PubChem renamed
# IsomericSMILES->SMILES and CanonicalSMILES->ConnectivitySMILES in 2025).
PROPERTIES = (
    "MolecularFormula,MolecularWeight,SMILES,ConnectivitySMILES,IUPACName,InChI,InChIKey,"
    "XLogP,ExactMass,TPSA,Complexity,Charge,HBondDonorCount,HBondAcceptorCount,"
    "RotatableBondCount,HeavyAtomCount"
)


# ---------------------------------------------------------------- schemas
class PubChemCompound(BaseModel):
    cid: int
    molecular_formula: str | None = None
    molecular_weight: float | None = None
    smiles: str | None = Field(None, description="Full SMILES incl. stereo/isotopes")
    connectivity_smiles: str | None = Field(None, description="SMILES without stereo/isotopes")
    iupac_name: str | None = None
    inchi: str | None = None
    inchikey: str | None = None
    xlogp: float | None = None
    exact_mass: float | None = None
    tpsa: float | None = None
    complexity: float | None = None
    charge: int | None = None
    h_bond_donors: int | None = None
    h_bond_acceptors: int | None = None
    rotatable_bonds: int | None = None
    heavy_atoms: int | None = None
    url: str

    @classmethod
    def from_pubchem(cls, p: dict[str, Any]) -> "PubChemCompound":
        def num(key: str) -> float | None:
            v = p.get(key)
            return float(v) if v not in (None, "") else None  # PubChem returns MW as string

        cid = int(p["CID"])
        return cls(
            cid=cid,
            molecular_formula=p.get("MolecularFormula"),
            molecular_weight=num("MolecularWeight"),
            smiles=p.get("SMILES") or p.get("IsomericSMILES"),
            connectivity_smiles=p.get("ConnectivitySMILES") or p.get("CanonicalSMILES"),
            iupac_name=p.get("IUPACName"),
            inchi=p.get("InChI"),
            inchikey=p.get("InChIKey"),
            xlogp=num("XLogP"),
            exact_mass=num("ExactMass"),
            tpsa=num("TPSA"),
            complexity=num("Complexity"),
            charge=p.get("Charge"),
            h_bond_donors=p.get("HBondDonorCount"),
            h_bond_acceptors=p.get("HBondAcceptorCount"),
            rotatable_bonds=p.get("RotatableBondCount"),
            heavy_atoms=p.get("HeavyAtomCount"),
            url=WEB_URL.format(cid=cid),
        )


class PubChemResult(BaseModel):
    query_smiles: str
    found: bool
    compound: PubChemCompound | None = None
    synonyms: list[str] = Field(default_factory=list)
    error: str | None = None


class PubChemError(RuntimeError):
    pass


# ---------------------------------------------------------------- client
class PubChemClient:
    """Async PUG REST client with throttling, retry on 503 and an in-memory cache."""

    def __init__(
        self,
        base_url: str = BASE_URL,
        timeout: float = 15.0,
        max_retries: int = 3,
        min_interval: float = 0.21,  # stay under 5 req/s
        transport: httpx.AsyncBaseTransport | None = None,  # injectable for tests
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_interval = min_interval
        self.transport = transport
        self._lock = asyncio.Lock()
        self._last_call = 0.0
        self._cache: dict[tuple[str, bool, int], PubChemResult] = {}

    async def lookup(self, smiles: str, include_synonyms: bool = True, max_synonyms: int = 10) -> PubChemResult:
        smiles = smiles.strip()
        if not smiles:
            return PubChemResult(query_smiles=smiles, found=False, error="empty SMILES")

        key = (smiles, include_synonyms, max_synonyms)
        if key in self._cache:
            return self._cache[key]

        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as http:
                data = await self._request(
                    http, "POST", f"/compound/smiles/property/{PROPERTIES}/JSON", data={"smiles": smiles}
                )
                if data is None:
                    result = PubChemResult(query_smiles=smiles, found=False, error="no PubChem match")
                else:
                    props = (data.get("PropertyTable") or {}).get("Properties") or []
                    props = [p for p in props if int(p.get("CID", 0)) > 0]  # CID 0 = valid SMILES, not in PubChem
                    if not props:
                        result = PubChemResult(query_smiles=smiles, found=False, error="no PubChem match")
                    else:
                        compound = PubChemCompound.from_pubchem(props[0])
                        synonyms = (
                            await self._synonyms(http, compound.cid, max_synonyms) if include_synonyms else []
                        )
                        result = PubChemResult(query_smiles=smiles, found=True, compound=compound, synonyms=synonyms)
        except PubChemError as exc:
            return PubChemResult(query_smiles=smiles, found=False, error=str(exc))  # not cached

        self._cache[key] = result
        return result

    async def _synonyms(self, http: httpx.AsyncClient, cid: int, limit: int) -> list[str]:
        try:
            data = await self._request(http, "GET", f"/compound/cid/{cid}/synonyms/JSON")
        except PubChemError:
            return []  # synonyms are best-effort
        info = ((data or {}).get("InformationList") or {}).get("Information") or [{}]
        return list(info[0].get("Synonym", []))[:limit]

    async def _request(self, http: httpx.AsyncClient, method: str, path: str, **kw: Any) -> dict[str, Any] | None:
        """Returns parsed JSON, None for 404 (not found); raises PubChemError otherwise."""
        for attempt in range(self.max_retries + 1):
            await self._throttle()
            try:
                resp = await http.request(method, self.base_url + path, **kw)
            except httpx.HTTPError as exc:
                if attempt == self.max_retries:
                    raise PubChemError(f"network error: {exc}") from exc
                await asyncio.sleep(2**attempt)
                continue

            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 404:
                return None
            if resp.status_code in (500, 503, 504) and attempt < self.max_retries:
                await asyncio.sleep(2**attempt)  # server busy / timeout -> back off
                continue
            raise PubChemError(f"HTTP {resp.status_code}: {self._fault(resp)}")
        raise PubChemError("retries exhausted")

    async def _throttle(self) -> None:
        async with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()

    @staticmethod
    def _fault(resp: httpx.Response) -> str:
        try:
            fault = resp.json().get("Fault", {})
            return f"{fault.get('Code', '')} {fault.get('Message', '')} {' '.join(fault.get('Details', []))}".strip()
        except (ValueError, AttributeError):
            return resp.text[:200]


_client = PubChemClient()


# ---------------------------------------------------------------- agent tool
@register_tool("pubchem_lookup")
async def pubchem_lookup(
    smiles: Annotated[str, "SMILES string of the molecule, e.g. CC(=O)OC1=CC=CC=C1C(=O)O"],
    include_synonyms: Annotated[bool, "Also return common names / synonyms"] = True,
) -> str:
    """Look up a molecule in PubChem by SMILES. Returns JSON with CID, formula, molecular
    weight, IUPAC name, InChI/InChIKey, XLogP, TPSA, H-bond donors/acceptors and synonyms.
    `found` is false (with `error`) if the structure is not in PubChem."""
    result = await _client.lookup(smiles, include_synonyms=include_synonyms)
    return result.model_dump_json(exclude_none=True)


if __name__ == "__main__":  # python -m hackframe.tools.pubchem "CCO"
    import sys

    res = asyncio.run(_client.lookup(sys.argv[1] if len(sys.argv) > 1 else "CC(=O)OC1=CC=CC=C1C(=O)O"))
    print(json.dumps(res.model_dump(mode="json", exclude_none=True), indent=2))
