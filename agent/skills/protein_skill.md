---
name: protein-chain-sequence
description: Use this skill when you are working with a molecular system that is a protein chain and need its amino acid sequence. Triggers include requests for the sequence, residues, or any task where a protein chain's amino acid sequence is a required input. Do not use for non-protein systems (nucleic acids, small molecules, ligands, etc.).
---

# Protein chain: CIF to amino acid sequence

## When to use this skill

Use this skill when the system you are looking at is a **protein chain** and you need its amino acid sequence.

If the system is not a protein chain (for example DNA/RNA, a ligand, or a small molecule), do not use this skill or call `protein_chain_cif_to_aa_sequence`. If you are unsure of the system type, check it first rather than guessing.

## Steps

1. **Get the CIF string.** If you only have a `system_id`, use the existing `system_id_to_cif` tool/skill to retrieve the CIF string for that system. If you already have the CIF string, skip this step.

2. **Call `protein_chain_cif_to_aa_sequence`.** Pass the full CIF string as the input. Do not truncate, summarize, reformat, or modify it.

3. **Use the result.** The tool returns the amino acid sequence. Check if its a amino acid sequence and then report or use it as the task requires.
