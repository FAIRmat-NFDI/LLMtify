import io

from ase.io import read

from hackframe.tools.builtin import register_tool
from llmtify.descriptors import atoms_to_smiles


@register_tool("compute_smiles")
def compute_smiles(cif_string: str):
    atoms = read(io.StringIO(cif_string), format="cif")
    smiles = atoms_to_smiles(atoms)
    return smiles
