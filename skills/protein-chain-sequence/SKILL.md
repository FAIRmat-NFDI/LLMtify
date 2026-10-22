---
name: protein-chain-sequence
description: Use this skill when you are working with a molecular system that is a protein chain (or a DNA/RNA chain) and need its sequence. Triggers include requests for the sequence, residues, or any task where a protein chain's amino acid sequence is a required input. Do not use for small molecules, ligands, ions or water.
---

# Protein chain: CIF to amino acid sequence

## When to use this skill

Use this skill when the system you are looking at is a **protein chain** and you need its amino acid sequence.

`cif_to_seq` also handles DNA/RNA chains (nucleotide sequence; `pdb_lookup` then searches nucleic-acid
entities; sequences shorter than 20 are too short for the RCSB search and are reported as such).
Do not plan `cif_to_seq` for ligands, ions, water or small molecules - they go to `cif_to_smiles` -> `pubchem`. If you are unsure of the system type, check it first rather than guessing:
a protein chain contains only C, H, N, O (and S), has many atoms, and its formula has roughly one N per
3-4 C (peptide backbone).

## Steps

1. **Get the CIF.** The pipeline passes each subsystem's CIF to the tools automatically (no `system_id_to_cif`
   call is needed). The subsystem must have `has_cif: true`.
2. **Plan `cif_to_seq`.** It converts the full CIF (do not truncate or modify it) to the amino acid sequence:
   Open Babel + Biopython first, a bond-topology method as fallback.
3. **Plan `pdb_lookup` after it.** It searches RCSB PDB for the most similar experimental structure
   (sequence identity >= 90 %) and returns description, organism, gene, UniProt and citation.
4. **Use the result.** Check that the sequence is a real amino acid sequence (mostly standard letters, few `X`)
   before reporting it.
