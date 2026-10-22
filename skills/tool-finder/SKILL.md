---
name: tool-finder
description: Judges what the input material is (MOF, protein, molecular, other) and decides which scraper tools run on which subsystem.
metadata:
  route_mof_tools: mofid
  route_mof_subsystem_tools: cif_to_smiles,pubchem,pubchem_safety
  route_protein_tools: cif_to_seq,pdb_lookup
  route_protein_subsystem_tools: cif_to_smiles,pubchem,pubchem_safety
  fallback_mof_labels: MOF
  fallback_protein_labels: protein,peptide,polypeptide,chain,dna,rna,nucleic
  polymer_min_atoms: "20"
  fallback_structural_types: molecule
  fallback_tools: cif_to_smiles,pubchem,pubchem_safety
---

# Tool finder

You receive the subsystems of one material (label, structural_type, system_relation, formula, elements,
n_atoms, has_cif) and the AVAILABLE TOOLS. Do two things:

1. **Judge the material** -> `material_class` + `class_reason`:
   - `mof`: a metal-organic framework. Signals: a subsystem labelled `MOF`, `metal_sbu_*` + `organic_sbu_*`/`ligand_*`
     subsystems, a periodic root with metal + C/H/N/O. Follow the **mof-analysis** skill.
   - `protein`: any biomolecular system - protein / peptide / DNA / RNA chains, protein-DNA complexes, solvated
     MD systems with chains. Use exactly `protein` (not "biomolecular_complex" etc.). Signals: chain subsystems with C, H, N, O, S
     (protein, N:C about 0.25-0.3) or C, H, N, O, P (DNA/RNA), many atoms, labels like protein/chain/peptide/DNA/RNA,
     residue-named `monomer` subsystems (ALA, GLY, DA, DT ...); the whole system may also hold water, ions, ligands. Follow the **protein-chain-sequence** skill.
   - `molecular`: discrete small molecules (structural_type `molecule`) that are not a MOF or protein.
   - `other`: anything else. Follow the **generic-system-analysis** skill.
2. **Plan** -> one entry per subsystem with the ordered tool ids (empty list = skip).

## Subsystem anatomy

- `system_relation: root` -> the whole input structure (the only CIF with a periodic cell).
- `structural_type: bulk` (e.g. label `MOF`) -> the framework as a whole; its label names the material class.
- `structural_type: group` -> a container of repeated units; its `molecule` children carry the same chemistry once.
- `structural_type: molecule` -> one building block (metal node, organic linker, ligand, polymer chain, ion, water).
- `structural_type: monomer` -> one residue type of a chain (ALA, DA ...): never plan these.

## Plans per class

| class | subsystem | tools |
|---|---|---|
| mof | root (`system_relation: root`) | `mofid` |
| mof | every `molecule` subsystem with a CIF (all non-root subsystems if structural types are missing) | `cif_to_smiles`, `pubchem`, `pubchem_safety` |
| protein | each polymer chain `molecule` subsystem (protein, DNA, RNA; the root if the whole input is one chain) | `cif_to_seq`, `pdb_lookup` |
| protein | every other `molecule` subsystem (ligand, ions, water) | `cif_to_smiles`, `pubchem`, `pubchem_safety` |
| molecular | each `molecule` subsystem with a CIF | `cif_to_smiles`, `pubchem`, `pubchem_safety` |
| other | each `molecule` subsystem with a CIF, if any | `cif_to_smiles`, `pubchem`, `pubchem_safety` |

Never plan `group` subsystems (they hold all copies; the pipeline replaces a group by its molecule children),
`monomer` subsystems (single residues of a chain), or `bulk`. Skip (and the root, except for `mofid` or a single-chain protein).
The MOF and protein routes are enforced from `route_<class>_tools` (+ `route_mof_subsystem_tools`) once you judge the
class, so getting `material_class` right is the most important decision. Labels and structural types can be missing
(null): then judge from formula, elements, n_atoms and the subsystem hierarchy (metal + C/H/N/O periodic root with
organic + metal subsystems -> mof). MOFid and the subsystem lookups run in parallel lanes.

## Rules

1. A tool that reads a CIF needs `has_cif: true`.
2. Order matters: `pubchem` needs SMILES (from `cif_to_smiles` or `mofid`); `pubchem_safety` needs CIDs from
   `pubchem`; `pdb_lookup` needs the sequence from `cif_to_seq`.
3. Never run `cif_to_seq`/`pdb_lookup` on non-protein systems, nor `mofid` on non-periodic or non-MOF systems.
4. Use only tool ids from AVAILABLE TOOLS. Never invent tools.

## Example (MOF)

`material_class: mof` - "label MOF, Ag metal SBU and organic SBU subsystems".
`results/material/topology/0` (root) -> `["mofid"]`
`results/material/topology/3` (`metal_sbu_1`, molecule) -> `["cif_to_smiles", "pubchem", "pubchem_safety"]`
`results/material/topology/5` (`organic_sbu_1`, molecule) -> `["cif_to_smiles", "pubchem", "pubchem_safety"]`
`results/material/topology/1` (`MOF`, bulk), groups -> `[]`
