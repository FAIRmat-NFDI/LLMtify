import json
from urllib.parse import quote

import requests

from hackframe.tools.builtin import register_tool

PROTEIN_URL = "https://data.rcsb.org/graphql"

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

def query_PDB_by_sequence(sequence: str, identity_cutoff=0.9) -> dict:
    """Search PDB for polymer entities matching a protein sequence.

    Queries the RCSB Search API via sequence similarity (BLAST-based).

    Parameters
    ----------
    sequence : str
        Amino acid sequence in one-letter code.
    identity_cutoff : float, optional
        Minimum sequence identity threshold (0–1). Default is 0.9.

    Returns
    -------
    dict
        Raw JSON response from the search API, containing ``result_set``
        with ranked hits and alignment metadata.

    Notes
    -----
    Returns up to 25 experimental structures sorted by combined score.
    For large-scale retrieval, paginate via ``request_options.paginate``.
    """
    query = {
        "query": {
            "type": "terminal",
            "service": "sequence",
            "parameters": {
                "evalue_cutoff": 0.1,
                "identity_cutoff": identity_cutoff,
                "sequence_type": "protein",
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
    
    response = requests.get(url)
    result = response.json()
    return result


def lookup_protein_data(protein_id: str):
    """Retrieve comprehensive structural and functional metadata for a PDB entry.

    Fetches via the RCSB Data API using the module-level ``GRAPH_QUERY``.

    Parameters
    ----------
    protein_id : str
        PDB identifier, e.g. ``"1CBS"``.

    Returns
    -------
    dict
        JSON response with nested entry, polymer entity, citation,
        UniProt cross-references, EC classification, and ligand data.

    Raises
    ------
    requests.HTTPError
        On non-2xx status codes (not explicitly handled here).

    See Also
    --------
    GRAPH_QUERY : GraphQL query string defining the returned data shape.
    """
    variables = {"id": protein_id}
    
    response = requests.post(
        PROTEIN_URL,
        json={"query": GRAPH_QUERY, "variables": variables},
        headers={"Content-Type": "application/json"}
    )

    return response.json()

@register_tool("PDB_sequence_lookup")
def query_most_similar_protein_from_PDB(sequence: str) -> dict:
    """Return full metadata for the PDB entry most similar to the given sequence.

    Convenience wrapper: executes sequence search, takes the top hit,
    strips the chain suffix from the polymer entity identifier, and
    resolves the corresponding entry via ``lookup_protein_data``.

    Parameters
    ----------
    sequence : str
        Query amino acid sequence.

    Returns
    -------
    dict
        ``lookup_protein_data`` result for the best-matching PDB entry.

    Raises
    ------
    IndexError
        If the sequence search returns an empty ``result_set``.
    KeyError
        If the response structure deviates from the expected schema.

    Examples
    --------
    >>> data = query_most_similar_protein("MTEYKLVVVGAGGVGKSALTIQLIQNHFVDEYDPTIEDSYRKQVVIDGET")
    >>> data["data"]["entry"]["rcsb_id"]
    '6B41'
    """
    ids_dict = query_PDB_by_sequence(sequence)
    id_ = ids_dict['result_set'][0]['identifier'].split('_')[0]
    protein_data = lookup_protein_data(id_)
    return protein_data