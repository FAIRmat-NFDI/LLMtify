"""Helpers for the material JSON. Two layouts are accepted:

  nested: systems[k] = {"metadata": {"label": ..., "structural_type": ...}}, cifs["<k>.cif"] = "..."
  flat:   systems[k] = {"label": ..., "structural_type": ..., "chemical_formula_hill": ..., "has_cif": true},
          cifs["<k>"] = "..."
"""

from typing import Any


def system_meta(entry: Any) -> dict[str, Any]:
    """Metadata of one system entry, nested (`metadata`) or flat."""
    if not isinstance(entry, dict):
        return {}
    meta = entry.get("metadata")
    return meta if isinstance(meta, dict) else entry


def cif_key(system: str, cifs: dict[str, Any]) -> str | None:
    """Key of a system's CIF in `cifs`: '<system>.cif' or '<system>'."""
    for key in (f"{system}.cif", system, system.removesuffix(".cif")):
        if key in cifs:
            return key
    return None
