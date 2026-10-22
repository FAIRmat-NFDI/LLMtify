---
name: tool-finder
description: Judges what the input material is (MOF, protein, molecular, other) and decides which scraper tools run on which subsystem.
metadata:
  route_mof_tools: mofid
  route_protein_tools: cif_to_seq,pdb_lookup
  fallback_mof_labels: MOF
  fallback_protein_labels: protein,peptide,polypeptide,chain
  fallback_structural_types: molecule
  fallback_tools: cif_to_smiles,pubchem,pubchem_safety
---

# Tool finder

You receive the subsystems of one material (label, structural_type, system_relation, formula, elements,
n_atoms, has_cif) and the AVAILABLE TOOLS. Do two things:

1. **Judge the material** -> `material_class` + `class_reason`:
   - `mof`: a metal-organic framework. Signals: a subsystem labelled `MOF`, `metal_sbu_*` + `organic_sbu_*`/`ligand_*`
     subsystems, a periodic root with metal + C/H/N/O. Follow the **mof-analysis** skill.
   - `protein`: a protein / peptide chain. Signals: only C, H, N, O, S elements, many atoms (typically > 100),
     labels like protein/chain/peptide, formula with N:C ratio around 0.25-0.3. Follow the **protein-chain-sequence** skill.
   - `molecular`: discrete small molecules (structural_type `molecule`) that are not a MOF or protein.
   - `other`: anything else. Follow the **generic-system-analysis** skill.
2. **Plan** -> one entry per subsystem with the ordered tool ids (empty list = skip).

## Subsystem anatomy

- `system_relation: root` -> the whole input structure (the only CIF with a periodic cell).
- `structural_type: bulk` (e.g. label `MOF`) -> the framework as a whole; its label names the material class.
- `structural_type: group` -> a container of repeated units; its `molecule` children carry the same chemistry once.
- `structural_type: molecule` -> one building block (metal node, organic linker, ligand).

## Plans per class

| class | subsystem | tools |
|---|---|---|
| mof | root (`system_relation: root`) ONLY | `mofid` - nothing else; no `cif_to_smiles`/`pubchem` on any subsystem |
| protein | each protein/peptide chain subsystem with a CIF (or the root, if the whole input is one chain) ONLY | `cif_to_seq`, `pdb_lookup` - nothing else |
| molecular | each `molecule` subsystem with a CIF | `cif_to_smiles`, `pubchem`, `pubchem_safety` |
| other | each `molecule` subsystem with a CIF, if any | `cif_to_smiles`, `pubchem`, `pubchem_safety` |

Skip `group` and `bulk` subsystems (and the root, except for `mofid` or a single-chain protein).
The MOF and protein routes are strict: the pipeline enforces them from `route_<class>_tools` once you judge the class,
so getting `material_class` right is the most important decision.

## Rules

1. A tool that reads a CIF needs `has_cif: true`.
2. Order matters: `pubchem` needs SMILES (from `cif_to_smiles` or `mofid`); `pubchem_safety` needs CIDs from
   `pubchem`; `pdb_lookup` needs the sequence from `cif_to_seq`.
3. Never run `cif_to_seq`/`pdb_lookup` on non-protein systems, nor `mofid` on non-periodic or non-MOF systems.
4. Use only tool ids from AVAILABLE TOOLS. Never invent tools.

## Example (MOF)

`material_class: mof` - "label MOF, Ag metal SBU and organic SBU subsystems".
`results/material/topology/0` (root) -> `["mofid"]`
every other subsystem -> `[]`
