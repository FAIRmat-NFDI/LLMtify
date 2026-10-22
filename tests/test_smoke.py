"""Offline smoke tests: no LLM, no network."""

import pytest

from matgentify.core.runtime import Runtime


@pytest.fixture(scope="module")
def rt() -> Runtime:
    return Runtime()


def test_workflows(rt: Runtime) -> None:
    assert rt.workflows.names() == ["material_pipeline"]
    assert rt.workflows.input_model("material_pipeline") is str
    rt.workflow("material_pipeline")  # builds (router + switch-case edges) without network


def test_agents_build_with_per_agent_models(rt: Runtime) -> None:
    assert rt.agents.model_name("tool_finder") == "hu-llm3"
    assert rt.agents.model_name("judge") == "hu-llm3"
    assert rt.agents.model_name("pubchem") == "hu-llm3"
    assert rt.config.agents["pubchem"].mcp == ["pubchem"]


def test_hu_provider_is_rate_limited_and_shared(rt: Runtime) -> None:
    from matgentify.llm.ratelimit import RateLimitedTransport

    hu = rt.models.provider("hu")
    oa = hu._openai_client()
    transport = oa._client._transport
    assert isinstance(transport, RateLimitedTransport)
    assert (transport.rpm, transport.max_concurrency) == (12, 1)
    # hu and hu_think profiles share one limiter
    assert rt.models.client("hu").client is rt.models.client("hu_think").client


async def test_skills_parsed_by_maf(rt: Runtime) -> None:
    assert rt.skills.available() == [
        "generic-system-analysis", "judge", "mof-analysis", "protein-chain-sequence", "pubchem-scrape", "tool-finder",
    ]
    meta = await rt.skills.metadata("tool-finder")
    assert meta["fallback_tools"] == "cif_to_smiles,pubchem,pubchem_safety"
    assert meta["route_mof_tools"] == "mofid"
    assert meta["route_protein_tools"] == "cif_to_seq,pdb_lookup"
    assert "Tool finder" in await rt.skills.content("tool-finder")
