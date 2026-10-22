---
name: pubchem-scrape
description: Fallback for SMILES fragments that had no exact PubChem structure match - identify them by name with the PubChem MCP tools.
---

# PubChem scrape (via PubChem MCP)

You only see fragments whose SMILES had **no exact structure match** in PubChem (exact matches are resolved
automatically before you are called). Usually the skeleton lacks bond orders, so identify it by name.

Tools (PubChem MCP server):
- `pubchem_search_compound(query, max_results)`: search by **name** (common or IUPAC). It does NOT accept SMILES.
- `pubchem_get_compound_details(cid)`: properties (MolecularFormula, MolecularWeight, IUPACName, ...) and synonyms.
- `pubchem_get_compound_properties(cid)`: properties only.

## Read the SMILES correctly (important)

The SMILES comes from a distance-based converter that writes **every bond as single**. It is a connectivity
skeleton, not the real bonding. Use the subsystem formula to recover unsaturation:
- an all-carbon 6-ring `C1CCC(...)CC1` is usually a **benzene ring** (phenyl / phenylene);
- a terminal `CN` / `NC` on a chain end with no H on N is usually a **nitrile** (C≡N, "cyano");
- a 6-ring with one N and N–H/two C neighbours (`C1CCNCC1`) is a saturated **piperidine**;
- compare H counts: formula H much lower than a saturated skeleton implies aromatic rings / triple bonds.
Example: skeleton `NCC1CCC(C23CC4(...)...)CC1` with formula C38H28N4 -> adamantane core with four
4-cyanophenyl arms -> search "1,3,5,7-tetrakis(4-cyanophenyl)adamantane".

## Procedure, per fragment

1. Read `bonded_smiles` if present (MOFid linker/node SMILES are already bonded) (real bond orders: aromatic rings, nitriles), else `smiles_without_h`,
   plus the subsystem formula.
2. Propose a name:
   - single atom like `[Ag]`, `[Cu]`, `[Zn]` -> the element name ("silver", "copper", "zinc");
   - small or well-known molecules -> the common name;
   - otherwise a systematic name built from the structure (e.g. `C(CC1CCNCC1)CC1CCNCC1` ->
     "1,3-bis(4-piperidyl)propane", also try "4,4'-trimethylenedipiperidine").
3. Call `pubchem_search_compound` with `max_results: 3`. If nothing is found, try one alternative name.
4. Verify the hit: its MolecularFormula must match the fragment's heavy atoms (H count may differ for
   framework fragments). Prefer the hit whose formula matches; otherwise mark `found: false`.
5. For the accepted CID call `pubchem_get_compound_details` once to get synonyms and properties.

## Answer format

Keep reasoning short (a few lines). ALWAYS end with exactly one ```json block, even if nothing was found:

```json
{"compounds": [
  {"query_smiles": "<the fragment's query_smiles>", "found": true, "cid": 23954, "name": "Silver",
   "iupac_name": "silver", "formula": "Ag", "molecular_weight": 107.868,
   "synonyms": ["Silver", "7440-22-4"], "searched_as": "silver", "note": ""}
]}
```

- One entry per fragment, `query_smiles` copied exactly.
- `found: false` with a short `note` if not identified. Never guess a CID or copy values not returned by the tools.
- `name`: first synonym returned by PubChem.
