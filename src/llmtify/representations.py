from glob import glob

import json
from ase.io import write
from nomad.datamodel.metainfo.system import Atoms
import numpy as np
import ase
from nomad.units import ureg
import os
import copy
import io
def get_atoms(data):
    atoms_dict={}
    functional_type=data['results']['material'].get('functional_type',None)
    for i in data['results']['material']['topology']:
        metadata={'functional_type':functional_type}
        for k in ['label','building_block','structural_type','method','parent_system','child_systems','chemical_formula_hill','elements','n_atoms']:
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

def atoms_dict_to_cifs(atoms_dict,output_dir):
    """
    Convert a dictionary of atoms to CIF files.

    Parameters:
    atoms_dict (dict): A dictionary where keys are system IDs and values are dictionaries containing 'atoms' and 'metadata'.

    Returns:
    dict: A dictionary where keys are system IDs and values are the corresponding CIF file paths.
    """
    cif_files = {}
    os.makedirs(output_dir, exist_ok=True)
    for system_id, data in atoms_dict.items():
        fname=system_id.replace('/','--')
        atoms = data['atoms']
        cif_file_path = f"{output_dir}/{fname}.cif"
        write(cif_file_path, atoms, format='cif')
        cif_files[system_id] = cif_file_path
    return cif_files

def atoms_to_cif_string(atoms):
    cif_string = ''
    with io.BytesIO() as fd:
        ase.io.write(fd, atoms, format='cif')
        cif_string = fd.getvalue().decode()
    return cif_string

def cif_string_to_atoms(cif_string):
    atoms = ase.io.read(io.StringIO(cif_string), format='cif')
    return atoms

def get_cif_string(input_dict, system_id):
    """
    Get the CIF representation of a specific system from the atoms dictionary.

    Parameters:
    atoms_dict (dict): A dictionary where keys are system IDs and values are dictionaries containing 'atoms' and 'metadata'.
    system_id (str): The system ID for which to retrieve the CIF representation.

    Returns:
    str: The CIF representation of the specified system.
    """
    if system_id not in input_dict['cifs']:
        return "System ID not found in atoms dictionary."
    
    return input_dict['cifs'][system_id]

def get_text_representations(cif_file):
    """
    Get text representations for all CIF files in the specified directory.

    Parameters:
    cif_file (str): The path to the CIF file.

    Returns:
    dict: A dictionary where keys are CIF file names and values are dictionaries of text representations.
    """
    from xtal2txt.core import TextRep
    from pymatgen.core import Structure
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


def build_tree(atoms_dict):
    # work on a copy so the input isn't mutated
    nodes = copy.deepcopy(atoms_dict)

    # pass 1: every node gets an empty 'childs' dict
    for data in nodes.values():
        data['metadata']['childs'] = {}

    # pass 2: attach each node to its parent (or collect as root)
    roots = {}
    for sid, data in nodes.items():
        parent = data['metadata']['parent_system']
        if parent is None:
            roots[sid] = data
        else:
            nodes[parent]['metadata']['childs'][sid] = data

    # leaves get childs = None, as you wanted
    for data in nodes.values():
        md = data['metadata']
        if not md['childs']:
            md['childs'] = None
        md['child_systems'] = None  # optional: drop the old flat list

    return roots


def create_input(archive):
    atoms_dict = get_atoms(archive)
    cifs = {}
    systems = {}
    for k, v in atoms_dict.items():
        cifs[f'{k}'] = atoms_to_cif_string(v['atoms'])
        v.pop('atoms')  # remove the Atoms object to save space
        v['metadata']['has_cif'] = True 
        systems[k] = v.pop('metadata')  # keep only the metadata
         # keep only the metadata
    atoms_dict = {'systems': systems, 'cifs': cifs}
    return atoms_dict

