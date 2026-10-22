"""Structure -> SMILES helpers (provided by the domain team; kept verbatim)."""

from ase.data import covalent_radii
from rdkit import Chem


def ase_to_rdkit(atoms, tolerance=1.3):
    """Convert ASE Atoms to a RDKit molecule"""
    mol = Chem.RWMol()
    for symbol in atoms.get_chemical_symbols():
        mol.AddAtom(Chem.Atom(symbol))
    
    # Bonds from covalent distances
    distances = atoms.get_all_distances(mic=True)
    for i in range(len(atoms)):
        for j in range(i+1, len(atoms)):
            dist = distances[i][j]
            r_cov = covalent_radii[atoms.numbers[i]] + covalent_radii[atoms.numbers[j]]
            if dist < tolerance * r_cov:
                mol.AddBond(i, j, Chem.BondType.SINGLE)    
    return mol.GetMol()

def atoms_to_smiles(atoms):
    mol = ase_to_rdkit(atoms)
    smiles = Chem.MolToSmiles(mol)
    return smiles