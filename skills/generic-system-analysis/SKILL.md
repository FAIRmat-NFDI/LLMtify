---
name: generic-system-analysis
description: Use this skill for systems that no more specific skill is available for.
---

# Generic system analysis

## Steps

1. **Get the CIF.** The pipeline passes each subsystem's CIF to the tools automatically (no `system_id_to_cif`
   call is needed).
2. **Identify what can be identified.** For discrete `molecule` subsystems with a CIF, plan
   `cif_to_smiles` -> `pubchem` -> `pubchem_safety`.
3. **Report on the system.** Try to characterize it from what is known: material class, composition (formula,
   elements), the subsystem hierarchy and any identified compounds. Be informative but concise; do not
   invent properties that are not in the evidence.
