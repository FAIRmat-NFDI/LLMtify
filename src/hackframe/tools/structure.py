"""CIF text -> SMILES, using the domain team's descriptors.atoms_to_smiles (ASE + RDKit)."""

import io
from typing import Any

from ase.io import read
from rdkit import Chem

from hackframe.tools.descriptors import ase_to_rdkit

_H_ONLY = {"H", "[H]", "[HH]", "[H][H]"}


def _heavy_atoms(smiles: str) -> int:
    mol = Chem.MolFromSmiles(smiles, sanitize=False)
    return sum(1 for a in mol.GetAtoms() if a.GetAtomicNum() > 1) if mol else 0


def _without_h(smiles: str) -> str:
    """Drop explicit H atoms (no sanitising): '[H]C([H])([H])N([H])[H]' -> 'CN'. Easier to name/search."""
    mol = Chem.MolFromSmiles(smiles, sanitize=False)
    return Chem.MolToSmiles(Chem.RemoveHs(mol, sanitize=False)) if mol else smiles


def read_cell(cif_text: str) -> Any | None:
    """Unit cell of a CIF, or None if it has none (e.g. Cartesian-only subsystem CIFs)."""
    atoms = read(io.StringIO(cif_text), format="cif")
    return atoms.cell.copy() if atoms.cell.volume > 1e-6 else None


def cif_to_smiles(
    cif_text: str, tolerance: float = 1.3, max_atoms: int = 2000, cell: Any | None = None
) -> dict[str, Any]:
    """Parse a CIF, bond atoms by covalent radii, return SMILES.

    Returns:
        smiles           : full SMILES (fragments joined by '.')
        fragments        : unique fragments, largest first, pure-hydrogen fragments dropped
        heavy_fragments  : the same fragments without explicit H atoms (same order)
        formula          : Hill formula from ASE
        n_atoms          : atom count
        periodic         : True if bonds were found with periodic images (cell applied)

    `cell`: used when the CIF itself has no cell. Subsystem CIFs from the topology analysis are
    Cartesian cut-outs of a periodic crystal; without the parent's cell, atoms bonded across the
    cell boundary look far apart and the molecule falls into fragments.
    """
    atoms = read(io.StringIO(cif_text), format="cif")
    if len(atoms) > max_atoms:
        raise ValueError(f"{len(atoms)} atoms > max_atoms={max_atoms}")
    if atoms.cell.volume < 1e-6 and cell is not None:
        atoms.set_cell(cell)
        atoms.set_pbc(True)
    smiles = Chem.MolToSmiles(ase_to_rdkit(atoms, tolerance=tolerance))
    fragments = sorted(
        {f for f in smiles.split(".") if f and f not in _H_ONLY},
        key=lambda f: (-_heavy_atoms(f), f),
    )
    return {
        "smiles": smiles,
        "fragments": fragments,
        "heavy_fragments": [_without_h(f) for f in fragments],
        "formula": atoms.get_chemical_formula(mode="hill"),
        "n_atoms": len(atoms),
        "periodic": bool(atoms.pbc.any()),
    }
