from glob import glob
from xtal2txt.core import TextRep
from pymatgen.core import Structure
import json
from ase.io import write
from nomad.datamodel.metainfo.system import Atoms
import numpy as np
import ase
from nomad.units import ureg

def get_atoms(data):
    atoms_dict={}
    functional_type=data['results']['material'].get('functional_type',None)
    for i in data['results']['material']['topology']:
        metadata={'functional_type':functional_type}
        for k in ['label','building_block','structural_type','method','paretn_system','child_systems']:
            try:
                metadata[k]=i[k]
            except:
                metadata[k]=None
        metadata['system_relation'] = i.get('system_relation',{}).get('type',None)
        atoms_ref=i['atoms_ref']
        atoms=data.copy()
        for j in atoms_ref.split('/')[1:]:
            try:
                atoms=atoms[j]
            except:
                atoms=atoms[int(j)]
        if 'indices' in i:
            new_atoms=atoms.copy()
            indices=i['indices']
            for k in ['species','labels','positions']:
                d=np.array(atoms.get(k))
                if d is not None:
                    new_atoms[k]=d[indices[0]]
                
            if i['structural_type'] not in ['bulk','group']:
                for p in ['lattice_vectors','periodic', 'lattice_vectors_reciprocal']:
                    new_atoms.pop(p)
            atoms=Atoms.m_from_dict(new_atoms)
            atoms=ase.Atoms(symbols=atoms.labels,
            positions=atoms.positions.to(ureg.angstroms).m,
            )
        else:
            atoms=Atoms.m_from_dict(atoms).to_ase()
        atoms_dict[i['system_id']]={'atoms':atoms,'metadata':metadata}
    return atoms_dict
def get_text_representations(cif_file):
    """
    Get text representations for all CIF files in the specified directory.

    Parameters:
    cif_file (str): The path to the CIF file.

    Returns:
    dict: A dictionary where keys are CIF file names and values are dictionaries of text representations.
    """
    text_representations = {}
    
    structure = Structure.from_file(c, "cif")

    # Initialize TextRep Class
    text_rep = TextRep.from_input(structure)

    requested_reps = [
        "cif_p1",
        "slices",
        "atom_sequences",
        "atom_sequences_plusplus",
        "crystal_text_llm",
        "zmatrix",
    ]

    # Get the requested text representations
    requested_text_reps = text_rep.get_requested_text_reps(requested_reps)
    text_representations[cif_file] = requested_text_reps

    return text_representations
