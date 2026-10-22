"""CIF text -> SMILES, using the domain team's descriptors.ase_to_rdkit (ASE + RDKit).

Two SMILES per fragment:
  fragments / heavy_fragments : the domain converter's output (all bonds single), with / without explicit H
  bonded_fragments            : same connectivity + bond orders from RDKit rdDetermineBonds
                                (aromatic rings, C#N, C=O ...) - what PubChem needs for an exact match
"""

import io
from collections import deque
from typing import Any

from ase import Atoms
from ase.geometry import find_mic
from ase.io import read
from rdkit import Chem, RDLogger
from rdkit.Chem import rdDetermineBonds

from hackframe.tools.descriptors import ase_to_rdkit

RDLogger.DisableLog("rdApp.*")
_H_ONLY = {"H", "[H]", "[HH]", "[H][H]"}


def _heavy_atoms(mol: Chem.Mol) -> int:
    return sum(1 for a in mol.GetAtoms() if a.GetAtomicNum() > 1)


def _without_h(smiles: str) -> str:
    """Drop explicit H atoms (no sanitising): '[H]C([H])([H])N([H])[H]' -> 'CN'. Easier to name/search."""
    mol = Chem.MolFromSmiles(smiles, sanitize=False)
    return Chem.MolToSmiles(Chem.RemoveHs(mol, sanitize=False)) if mol else smiles


def read_cell(cif_text: str) -> Any | None:
    """Unit cell of a CIF, or None if it has none (e.g. Cartesian-only subsystem CIFs)."""
    atoms = read(io.StringIO(cif_text), format="cif")
    return atoms.cell.copy() if atoms.cell.volume > 1e-6 else None


def _unwrapped_positions(atoms: Atoms, skeleton: Chem.Mol) -> Any:
    """Make every molecule whole in space (follow bonds across periodic images)."""
    pos = atoms.positions.copy()
    if not atoms.pbc.any():
        return pos
    seen: set[int] = set()
    for start in range(len(atoms)):
        if start in seen:
            continue
        seen.add(start)
        queue = deque([start])
        while queue:
            i = queue.popleft()
            for nb in skeleton.GetAtomWithIdx(i).GetNeighbors():
                j = nb.GetIdx()
                if j not in seen:
                    vec, _ = find_mic(atoms.positions[j] - atoms.positions[i], atoms.cell, pbc=atoms.pbc)
                    pos[j] = pos[i] + vec
                    seen.add(j)
                    queue.append(j)
    return pos


def _bond_orders(fragment: Chem.Mol, charges: tuple[int, ...] = (0, 1, -1, 2, -2)) -> str | None:
    """Same connectivity, bond orders perceived from 3D geometry. Tries a neutral molecule first, then
    small net charges (MD/biomolecular ligands are often protonated, e.g. R3NH+). None if all fail."""
    if fragment.GetNumAtoms() < 2:
        return _single_atom(fragment)
    for charge in charges:
        try:
            mol = Chem.Mol(fragment)
            for b in mol.GetBonds():
                b.SetBondType(Chem.BondType.SINGLE)
            rdDetermineBonds.DetermineBondOrders(mol, charge=charge)
            Chem.RemoveStereochemistry(mol)  # 3D-derived stereo tags would block exact matches
            return Chem.MolToSmiles(Chem.RemoveHs(mol))
        except Exception:  # metals, odd valences, radicals, wrong charge ...
            continue
    return None


def _single_atom(fragment: Chem.Mol) -> str:
    """Isolated atom/ion -> '[Na]', '[Zn]' (RDKit would add implicit H: '[NaH]' = sodium hydride)."""
    return f"[{fragment.GetAtomWithIdx(0).GetSymbol()}]"


def cif_to_smiles(
    cif_text: str, tolerance: float = 1.3, max_atoms: int = 2000, cell: Any | None = None, bond_orders: bool = True
) -> dict[str, Any]:
    """Parse a CIF, bond atoms by covalent radii (descriptors.ase_to_rdkit), return SMILES.

    Returns:
        smiles           : full SMILES of the domain converter (fragments joined by '.')
        fragments        : unique fragments, largest first, pure-hydrogen fragments dropped
        heavy_fragments  : the same fragments without explicit H atoms (same order)
        bonded_fragments : the same fragments with perceived bond orders (same order; null where it failed)
        formula, n_atoms : from ASE
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

    skeleton = ase_to_rdkit(atoms, tolerance=tolerance)  # domain converter: connectivity, single bonds
    smiles = Chem.MolToSmiles(skeleton)

    mol = Chem.Mol(skeleton)
    if bond_orders:
        conf = Chem.Conformer(len(atoms))
        for i, p in enumerate(_unwrapped_positions(atoms, skeleton)):
            conf.SetAtomPosition(i, p.tolist())
        mol.AddConformer(conf, assignId=True)

    unique: dict[str, tuple[int, str | None]] = {}
    for frag in Chem.GetMolFrags(mol, asMols=True, sanitizeFrags=False):
        frag_smiles = _single_atom(frag) if frag.GetNumAtoms() == 1 else Chem.MolToSmiles(frag)
        if frag_smiles in _H_ONLY or frag_smiles in unique:
            continue
        unique[frag_smiles] = (_heavy_atoms(frag), _bond_orders(frag) if bond_orders else None)
    ordered = sorted(unique, key=lambda f: (-unique[f][0], f))

    return {
        "smiles": smiles,
        "fragments": ordered,
        "heavy_fragments": [_without_h(f) for f in ordered],
        "bonded_fragments": [unique[f][1] for f in ordered],
        "formula": atoms.get_chemical_formula(mode="hill"),
        "n_atoms": len(atoms),
        "periodic": bool(atoms.pbc.any()),
    }
