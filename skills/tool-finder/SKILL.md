---
name: tool-finder
description: Decides which scraper tools run on which subsystem of a material (system/subsystem JSON from the porosity/topology analysis).
metadata:
  fallback_structural_types: molecule
  fallback_tools: cif_to_smiles,pubchem,pubchem_safety
---

# Tool finder

You receive the subsystems of one material and the list of available tools.
Return, for every subsystem, the ordered list of tool ids to run (empty list = skip).

## Subsystem anatomy (domain knowledge)

- `system_relation: root`, `label: original` -> the whole input structure. Skip it (too large, not a single compound).
- `structural_type: bulk` (e.g. label `MOF`) -> the framework as a whole. Skip it; its label names the material class.
- `structural_type: group` (e.g. `metal_sbu_1`, `organic_sbu_1`, `ligand_1`) -> a container of repeated units. Skip it;
  its `molecule` children carry the same chemistry once.
- `structural_type: molecule` -> one building block (metal node, organic linker, ligand). These are the ones to identify.

## Rules

1. Only plan subsystems with `structural_type: molecule`.
2. A subsystem needs `has_cif: true` for `cif_to_smiles`. Without a CIF, plan nothing for it.
3. To identify a molecule: `cif_to_smiles` -> `pubchem` -> `pubchem_safety`.
   pubchem needs the SMILES from cif_to_smiles; pubchem_safety needs the CIDs from pubchem.
4. If two molecule subsystems have the same label, plan both anyway (the judge de-duplicates).
5. Use only tool ids from AVAILABLE TOOLS. Never invent tools.

## Example

Subsystem `results/material/topology/3` (`metal_sbu_1`, molecule, has_cif) -> `["cif_to_smiles", "pubchem", "pubchem_safety"]`
Subsystem `results/material/topology/1` (`MOF`, bulk) -> `[]`

<!-- Domain experts: add rules here when new tools exist, e.g.
     "metal_sbu molecules with a single atom -> element_lookup instead of cif_to_smiles". -->
