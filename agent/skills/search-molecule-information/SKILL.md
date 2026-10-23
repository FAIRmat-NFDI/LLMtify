---
name: search-molecule-information
description: Obtain information about a molecular structure from PubChem. This is achieved by computing the SMILES string of the structure from the molecules CIF file and submitting a query to PubChem.
---

# Data retrieval

1. Call `compute_smiles` on the provided CIF data file content
2. Call `pubchem_lookup` with the SMILES string that was obtained in the previous step
3. Report all information that would help an experimental scientist to work with this molecule, e.g., the common names, toxicity, and providers.