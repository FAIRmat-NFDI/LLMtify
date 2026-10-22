"""Domain tools chosen by the tool finder for MOFs and proteins. Each handles one WorkItem;
each output has a compact `summary` that the judge reads.

    mofid        Get_mof_id: periodic CIF (root system) -> MOFid JSON: nodes, linkers (+ formulas), topology, catenation.
                 Strict MOF route: mofid -> judge (no CIF->SMILES / PubChem).
    cif_to_seq   Cif2Seq: protein chain CIF -> amino acid sequence(s)
    pdb_lookup   sequence -> most similar RCSB PDB entry (description, organism, UniProt, citation)

workflows.yaml:
  - {id: mofid, type: mofid}
  - {id: cif_to_seq, type: cif_to_seq}
  - {id: pdb_lookup, type: pdb_lookup, identity_cutoff: 0.9}
"""

import asyncio
from typing import Any

from hackframe.core.schemas import WorkItem
from hackframe.executors.base import EXECUTORS
from hackframe.executors.routing import BaseToolExecutor
from hackframe.tools import mofid as mofid_tools
from hackframe.tools import pdb as pdb_tools
from hackframe.tools import protein as protein_tools


async def _cif(executor: BaseToolExecutor, item: WorkItem) -> str:
    if not item.cif_key:
        raise ValueError(f"{item.system} has no CIF in the input")
    return await executor.artifacts.get_cif(item.artifact_id, item.cif_key)


@EXECUTORS.register("mofid")
class MOFidExecutor(BaseToolExecutor):
    executor_type = "mofid"
    description = (
        "Get_mof_id: run MOFid on the periodic CIF of the whole framework (use on the root system). "
        "Output: MOFid string, MOFkey, nodes (SBUs), linkers (SMILES + formulas), RCSR topology, catenation."
    )
    run_mofid = staticmethod(mofid_tools.get_mofid)  # swap in tests

    async def run_tool(self, item: WorkItem) -> dict[str, Any]:
        cif = await _cif(self, item)
        raw = await asyncio.to_thread(self.run_mofid, cif, timeout=float(self.params.get("timeout", 600)))
        parsed = raw["mofid_parsed"]
        linkers, nodes = parsed["linkers"], parsed["nodes"]
        summary = {
            "mofid": raw.get("mofid"),
            "mofkey": raw.get("mofkey"),
            "topology": parsed["topology"],
            "topology_failed": parsed["topology_failed"],
            "catenation": parsed["catenation"],
            "nodes": nodes,
            "linkers": linkers,
            "node_formulas": [mofid_tools.Component(smiles=x).formula() or x for x in nodes],
            "linker_formulas": [mofid_tools.Component(smiles=x).formula() or x for x in linkers],
        }
        components = linkers + nodes  # linkers first: they are the interesting PubChem lookups
        return {
            "summary": summary,
            # same keys as cif_to_smiles, so `pubchem` can follow mofid in a chain
            "fragments": components,
            "heavy_fragments": components,
            "bonded_fragments": components,
            "raw": {k: v for k, v in raw.items() if k != "mofid_parsed"},
        }


@EXECUTORS.register("cif_to_seq")
class CifToSeqExecutor(BaseToolExecutor):
    executor_type = "cif_to_seq"
    description = (
        "Cif2Seq: protein chain CIF -> amino acid sequence (Open Babel + Biopython, topology-based fallback). "
        "Use only on protein/peptide chains. Output: sequence, sequences per chain, length."
    )

    async def run_tool(self, item: WorkItem) -> dict[str, Any]:
        cif = await _cif(self, item)
        method = "openbabel"
        seq = ""
        try:
            seq = await asyncio.to_thread(protein_tools.protein_chain_cif_to_aa_sequence, cif)
        except Exception:
            pass
        sequences = [seq] if seq else []
        if not seq or seq.count("X") > len(seq) / 2:  # no residue info -> rebuild from the bonded topology
            sequences, method = await asyncio.to_thread(protein_tools.cif_to_sequences, cif), "topology"
            seq = max(sequences, key=len, default="")
        if not seq:
            raise ValueError("no amino acid sequence found (not a protein chain?)")
        return {
            "sequence": seq,
            "sequences": sequences,
            "length": len(seq),
            "method": method,
            "summary": {"length": len(seq), "chains": len(sequences), "sequence": seq if len(seq) <= 60 else seq[:60] + "..."},
        }


@EXECUTORS.register("pdb_lookup")
class PDBLookupExecutor(BaseToolExecutor):
    executor_type = "pdb_lookup"
    description = (
        "PDB: amino acid sequence (from cif_to_seq) -> most similar experimental RCSB PDB entry: "
        "description, organism, gene, UniProt, EC number, citation."
    )
    search = staticmethod(pdb_tools.query_PDB_by_sequence)  # swap in tests
    lookup = staticmethod(pdb_tools.lookup_protein_data)

    async def run_tool(self, item: WorkItem) -> dict[str, Any]:
        seq = item.find("sequence")
        if not seq:
            raise ValueError("no amino acid sequence from an earlier tool (plan cif_to_seq before pdb_lookup)")
        cutoff = float(self.params.get("identity_cutoff", 0.9))
        result = await asyncio.to_thread(self.search, seq, cutoff)
        hits = result.get("result_set") or []
        if not hits:
            return {"found": False, "summary": {"found": False, "note": f"no PDB entry with >= {cutoff:.0%} identity"}}
        top = hits[0]
        pdb_id = top["identifier"].split("_")[0]
        data = await asyncio.to_thread(self.lookup, pdb_id)
        summary = {"found": True, **pdb_tools.summarize_entry(data, top)}
        return {"found": True, "pdb_id": pdb_id, "n_hits": len(hits), "summary": summary}
