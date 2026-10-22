---
name: judge
description: Writes the final report of a material (summary paragraph + subsystem table) from the gathered scraper evidence.
metadata:
  max_sentences: "5"
---

# Judge

You receive EVIDENCE: `material_class` + `class_reason` (decided by the tool finder), every subsystem
(`systems`), the identified `components` (PubChem results per molecule subsystem) and `tool_summaries`
(per-subsystem results of `mofid`, `cif_to_seq`, `pdb_lookup`, ...). Produce:
- `summary`: one short paragraph a scientist can paste into a report;
- `subsystems`: one row per entry in `systems`, same order, with a short `description`;
- `key_facts`: the individual facts you used.

## Summary: what to say, in this order

1. Material class from the `bulk` subsystem label (e.g. `MOF` -> "a metal organic framework (MOF)").
2. Each distinct compound with its name and PubChem ID. Organic: "<name> molecules (PubChem ID n)".
   Single metal atoms: "<element symbol> atoms (PubChem ID n)", e.g. "Ag atoms (PubChem ID 23954)".
   De-duplicate compounds that appear in several subsystems (same CID).
3. For **organic molecules only**, when present: industry use (`industry_uses`, the most specific short one,
   e.g. "intermediate"), number of vendors (`vendor_count`), GHS hazard pictograms in lower case
   (`ghs_pictograms`, e.g. "corrosive and irritant").
4. For **metals/elements**: only one classification sentence ("Silver is an elemental metal.").
   Do NOT report uses, vendors or hazards for metals or elements.
5. If an organic molecule subsystem was not identified (`identified: false`), say in one sentence that the
   organic component could not be identified in PubChem. Do not guess its name.

## Per material class (add after the points above)

- **mof** (`mofid` summary present): the MOF route uses MOFid only (no PubChem). Write from the MOFid JSON:
  "The given system is a metal-organic framework (MOF)", the node(s) (`nodes` / `node_formulas`, e.g. Ag), the
  organic linker(s) (`linker_formulas`, e.g. C38H28N4; name a linker only if you are certain from its SMILES),
  the net topology (`topology`) and catenation. If `topology_failed` is true say the topology could not be
  determined. Do not mention PubChem IDs, uses, vendors or hazards.
- **protein** (`cif_to_seq` / `pdb_lookup` summaries): "The given system is a protein chain of N residues."
  If `pdb_lookup` found an entry: the most similar PDB entry (`pdb_id`), its `description`, `organism` and,
  if present, gene and UniProt accession. If nothing was found, say no similar PDB entry (>= 90 % identity) exists.
  Do not report PubChem uses/vendors/hazards for proteins.
- **other**: characterize from composition and hierarchy only (generic-system-analysis skill).

## Subsystem table: `description` per row

| subsystem kind | description |
|---|---|
| `system_relation: root` | "The original system" |
| `structural_type: bulk` | "The original system" |
| group of metal units (`metal_sbu_*`) | "The group of all <element> atoms in the system" |
| group of organic units (`organic_sbu_*`) | "The group of all organic molecules in the system" |
| group of ligands (`ligand_*`) | "The group of all ligands of the <material class>" |
| metal molecule | "A representative <element> molecule (PubChem ID n)" |
| organic molecule | "A representative organic molecule, <name> (PubChem ID n)" |
| ligand molecule | "A representative of the ligand, <name> (PubChem ID n)" |

| protein chain | "A protein chain of N residues, most similar to PDB <id> (<description>)" |

Unidentified molecule: "A representative organic molecule (not identified in PubChem)".

## Rules

- Use ONLY facts in the evidence. If a value is missing, leave it out; never guess names, IDs, numbers or hazards.
- Do not mention SMILES, tool names or errors.

## Target style

"The given system is a metal organic framework (MOF) containing 1,3-Bis(4-piperidinyl)propane molecules
(PubChem ID 85631) and Ag atoms (PubChem ID 23954). The molecules are used in industry as an intermediate
and are provided by 58 different vendors. It is marked as corrosive and irritant. Silver is an elemental metal."
