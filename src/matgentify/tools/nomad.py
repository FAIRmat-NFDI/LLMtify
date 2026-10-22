"""NOMAD input: entry URL / id -> archive (NOMAD API) -> pipeline input {"systems": {...}, "cifs": {...}}.

Fetching is the domain team's client (NomadClient + required_data). The converter rebuilds the
per-subsystem CIFs from results.material.topology (indices into run/system/atoms), like the
hand-made input JSON: the root keeps its cell, subsystems are Cartesian cut-outs.
"""

import io
import os
import re
import time
from typing import Any

import numpy as np
import requests
from ase import Atoms
from ase.io import write

NOMAD_BASE_URL = os.environ.get("NOMAD_BASE_URL", "https://nomad-lab.eu/prod/v1/api/v1")

query = {"and": [{}, {"results.material.topology.label:any": ["MOF"]}]}
required_data = {
    "results": {"material": "*"},
    "run": {"system": {"atoms": "*"}},
}


class NomadClient:
    """Thin wrapper around `requests` for calling the NOMAD API.

    Retries on 5xx responses and on connection/timeout errors, using a fixed
    backoff between attempts. Raises `requests.HTTPError` (or the underlying
    `requests` exception) once retries are exhausted.
    """

    def __init__(
        self,
        base_url: str = NOMAD_BASE_URL,
        max_retries: int = 3,
        backoff_seconds: float = 5.0,
        timeout: float = 60.0,
    ):
        self.base_url = base_url
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.timeout = timeout

    def post(self, path: str, payload: dict) -> dict:
        """POST `payload` as JSON to `base_url + path`, returning the parsed JSON body."""
        url = f"{self.base_url}{path}"
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.post(url, json=payload, timeout=self.timeout)
                response.raise_for_status()
                return response.json()
            except requests.HTTPError as exc:
                if exc.response is None:
                    raise
                elif exc.response.status_code == 429:
                    # Too Many Requests - wait and retry
                    time.sleep(self.backoff_seconds)
                    continue
                elif exc.response.status_code < 500:
                    raise
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(self.backoff_seconds)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    time.sleep(self.backoff_seconds)
        raise last_exc


def fetch_by_entry_ids(entry_ids: list[str], client: NomadClient | None = None) -> dict[str, dict]:
    """Archives (results.material + run.system.atoms) for `entry_ids`, as {entry_id: archive}."""
    client = client or NomadClient()
    batch_size = 50
    archives: dict[str, dict] = {}
    for i in range(0, len(entry_ids), batch_size):
        batch = entry_ids[i : i + batch_size]
        payload = {
            "query": {"entry_id:any": batch},
            "required": required_data,
            "pagination": {"page_size": batch_size},
        }
        response = client.post("/entries/archive/query", payload)
        time.sleep(1)  # avoid overwhelming the server with back-to-back requests
        for archive_entry in response.get("data", []):
            entry_id = archive_entry.get("entry_id")
            if entry_id:
                archives[entry_id] = archive_entry["archive"]
    return archives


# ---------------------------------------------------------------- entry reference
_ENTRY_PATTERNS = (r"/entry/id/([^/?#]+)", r"[?&]entry_id=([^&#]+)", r"/entries/([^/?#]+)/archive")


def parse_entry_id(ref: str) -> str:
    """Entry id from a NOMAD GUI/API URL or a bare id."""
    ref = ref.strip()
    for pattern in _ENTRY_PATTERNS:
        if m := re.search(pattern, ref):
            return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_\-]{8,}", ref):
        return ref
    raise ValueError(f"cannot find a NOMAD entry id in {ref!r}")


def fetch_archive(entry_id: str, client: NomadClient | None = None) -> dict[str, Any]:
    archives = fetch_by_entry_ids([entry_id], client)
    if entry_id not in archives:
        raise LookupError(f"NOMAD entry {entry_id} not found (or not public)")
    return archives[entry_id]


# ---------------------------------------------------------------- archive -> {systems, cifs}
def _resolve(archive: dict[str, Any], ref: str) -> Any:
    """'#/run/0/system/0/atoms' -> archive['run'][0]['system'][0]['atoms']."""
    node: Any = archive
    for part in ref.split("#", 1)[-1].strip("/").split("/"):
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def _atoms(section: dict[str, Any]) -> Atoms:
    """NOMAD Atoms section (SI units) -> ase.Atoms in Angstrom."""
    symbols = section.get("labels") or section.get("species")
    positions = np.asarray(section["positions"], dtype=float)
    scale = 1e10 if np.abs(positions).max() < 1e-5 else 1.0  # archive JSON is in metres
    cell = section.get("lattice_vectors")
    pbc = section.get("periodic") or [False, False, False]
    atoms = Atoms(symbols=symbols, positions=positions * scale)
    if cell is not None:
        atoms.set_cell(np.asarray(cell, dtype=float) * scale)
        atoms.set_pbc(pbc)
    return atoms


def _cif(atoms: Atoms) -> str:
    buf = io.BytesIO()
    write(buf, atoms, format="cif")
    return buf.getvalue().decode()


_META = ("label", "structural_type", "method", "building_block", "functional_type",
         "parent_system", "child_systems", "chemical_formula_hill", "elements", "n_atoms")


def archive_to_material(archive: dict[str, Any], entry_id: str | None = None) -> dict[str, Any]:
    """results.material.topology -> {"systems": {key: flat metadata}, "cifs": {key: CIF}}."""
    topology = ((archive.get("results") or {}).get("material") or {}).get("topology") or []
    if not topology:
        raise ValueError("archive has no results.material.topology")
    systems: dict[str, Any] = {}
    cifs: dict[str, str] = {}
    for i, item in enumerate(topology):
        key = f"results/material/topology/{i}"
        relation = (item.get("system_relation") or {}).get("type")
        atoms = None
        if item.get("atoms"):
            atoms = _atoms(item["atoms"])
        elif item.get("atoms_ref"):
            base = _atoms(_resolve(archive, item["atoms_ref"]))
            rows = item.get("indices")
            if rows:  # a group holds every instance; a subsystem/molecule is represented by its first one
                rows = rows if relation == "group" or item.get("structural_type") in ("group", "bulk") else rows[:1]
                idx = list(dict.fromkeys(int(j) for row in rows for j in row))
                atoms = base[idx]
                atoms.set_cell(np.zeros((3, 3)))  # Cartesian cut-out, like the subsystem CIFs
                atoms.set_pbc(False)
            else:
                atoms = base
        systems[key] = {**{k: item.get(k) for k in _META}, "system_relation": relation, "has_cif": atoms is not None}
        if atoms is not None:
            cifs[key] = _cif(atoms)
    material = {"systems": systems, "cifs": cifs}
    if entry_id:
        material["entry_id"] = entry_id
    return material
