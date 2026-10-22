---
name: mof-analysis
description: Use this skill when the system is a metal-organic framework (MOF) - to identify its nodes, linkers and net topology with MOFid.
---

# MOF analysis

## When to use

The input is a MOF: a subsystem labelled `MOF`, metal SBU + organic SBU / ligand subsystems, or a periodic
structure with metal centres connected by organic molecules.

## Steps

1. **Run `mofid` on the root system** (the periodic CIF with the unit cell). Never send a subsystem cut-out
   (no cell) to MOFid.
2. **In parallel, identify every subsystem**: `cif_to_smiles` -> `pubchem` -> `pubchem_safety` on each molecule
   subsystem (metal SBU, organic SBU, ligand).
3. The judge combines both: MOFid for the framework (nodes, linkers, topology), PubChem for the named compounds.

## Reading MOFid results

- `nodes`: inorganic SBUs (no carbon), e.g. `[Ag]`, `[Zn][Zn]`, `[Cu][Cu]` (paddlewheel).
- `linkers`: organic linkers as SMILES.
- `topology`: RCSR net code (e.g. `pcu`, `dia`, `sql`); `topology_failed: true` means MOFid could not
  determine the net - report it as "not determined", never guess.
- `catenation`: interpenetration count (0 = not interpenetrated), only meaningful if the topology is known.
- `linker_formulas` / `node_formulas`: molecular formulas of the building blocks (from the SMILES).
- `mofkey`: MOFkey (InChIKey-based identifier of the framework).
