"""RCSB PDB lookup by amino acid sequence (domain team's code) + a compact summary for the judge."""

import json
from typing import Any
from urllib.parse import quote

import requests

from hackframe.tools.builtin import register_tool

PROTEIN_URL = "https://data.rcsb.org/graphql"
TIMEOUT = 60

GRAPH_QUERY = """
query structure($id: String!) {
  entry(entry_id: $id) {
    rcsb_id
    entry { id }
    database_2 { database_id pdbx_database_accession }
    citation {
      id title rcsb_journal_abbrev rcsb_authors year
      journal_volume page_first page_last
      pdbx_database_id_PubMed pdbx_database_id_DOI
    }
    pdbx_database_related { db_id db_name }
    polymer_entities {
      uniprots {
        rcsb_id
        rcsb_uniprot_protein { source_organism { scientific_name } }
        rcsb_uniprot_external_reference { reference_name reference_id }
      }
      rcsb_polymer_entity {
        pdbx_description
        rcsb_ec_lineage { id }
        pdbx_ec
        rcsb_enzyme_class_combined { ec provenance_source }
      }
      rcsb_entity_source_organism {
        scientific_name ncbi_scientific_name
        rcsb_gene_name { value provenance_source }
      }
      rcsb_entity_host_organism { ncbi_scientific_name }
      prd {
        rcsb_id
        pdbx_reference_molecule {
          prd_id chem_comp_id name type class
        }
      }
      chem_comp_nstd_monomers {
        chem_comp { id name formula type mon_nstd_parent_comp_id }
      }
    }
  }
}
"""


def query_PDB_by_sequence(sequence: str, identity_cutoff=0.9, sequence_type: str = "protein") -> dict:
    """Search PDB for polymer entities matching a sequence (RCSB Search API, BLAST-based).
    sequence_type: "protein" | "dna" | "rna".

    Returns the raw JSON response, containing ``result_set`` with ranked hits.
    """
    query = {
        "query": {
            "type": "terminal",
            "service": "sequence",
            "parameters": {
                "evalue_cutoff": 0.1,
                "identity_cutoff": identity_cutoff,
                "sequence_type": sequence_type,
                "value": sequence
            }
        },
        "return_type": "polymer_entity",
        "request_options": {
            "paginate": {"start": 0, "rows": 25},
            "results_content_type": ["experimental"],
            "sort": [{"sort_by": "score", "direction": "desc"}],
            "scoring_strategy": "combined"
        }
    }

    json_str = json.dumps(query, separators=(',', ':'))
    url = f"https://search.rcsb.org/rcsbsearch/v2/query?json={quote(json_str, safe='')}"

    response = requests.get(url, timeout=TIMEOUT)
    if response.status_code == 204:  # no hits
        return {"result_set": []}
    response.raise_for_status()
    return response.json()


def lookup_protein_data(protein_id: str):
    """Structural and functional metadata for a PDB entry (RCSB Data API, GRAPH_QUERY)."""
    variables = {"id": protein_id}

    response = requests.post(
        PROTEIN_URL,
        json={"query": GRAPH_QUERY, "variables": variables},
        headers={"Content-Type": "application/json"},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


@register_tool("PDB_sequence_lookup")
def query_most_similar_protein_from_PDB(sequence: str) -> dict:
    """Return full metadata for the PDB entry most similar to the given amino acid sequence."""
    ids_dict = query_PDB_by_sequence(sequence)
    id_ = ids_dict['result_set'][0]['identifier'].split('_')[0]
    protein_data = lookup_protein_data(id_)
    return protein_data


def summarize_entry(data: dict, hit: dict | None = None) -> dict[str, Any]:
    """Small, judge-friendly view of lookup_protein_data()'s answer."""
    entry = (data.get("data") or {}).get("entry") or {}
    entities = entry.get("polymer_entities") or []
    descriptions, organisms, uniprots, ecs, genes = [], [], [], [], []
    for ent in entities:
        if d := (ent.get("rcsb_polymer_entity") or {}).get("pdbx_description"):
            descriptions.append(d)
        if ec := (ent.get("rcsb_polymer_entity") or {}).get("pdbx_ec"):
            ecs.append(ec)
        for org in ent.get("rcsb_entity_source_organism") or []:
            if name := org.get("ncbi_scientific_name") or org.get("scientific_name"):
                organisms.append(name)
            genes += [g["value"] for g in org.get("rcsb_gene_name") or [] if g.get("value")]
        uniprots += [u["rcsb_id"] for u in ent.get("uniprots") or [] if u.get("rcsb_id")]
    citation = next(iter(entry.get("citation") or []), {}) or {}
    uniq = lambda xs: list(dict.fromkeys(xs))  # noqa: E731
    out = {
        "pdb_id": entry.get("rcsb_id"),
        "description": uniq(descriptions)[:3],
        "organism": uniq(organisms)[:3],
        "gene": uniq(genes)[:3],
        "uniprot": uniq(uniprots)[:3],
        "ec": uniq(ecs)[:3],
        "citation": {k: citation.get(k) for k in ("title", "year", "pdbx_database_id_DOI") if citation.get(k)},
        "url": f"https://www.rcsb.org/structure/{entry.get('rcsb_id')}" if entry.get("rcsb_id") else None,
    }
    if hit:
        out["matched_entity"] = hit.get("identifier")
        out["score"] = hit.get("score")
    return {k: v for k, v in out.items() if v not in (None, [], {})}
