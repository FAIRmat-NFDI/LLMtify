"""Offline smoke tests: no LLM, no network."""

import pytest

from hackframe.core.runtime import Runtime


@pytest.fixture(scope="module")
def rt() -> Runtime:
    return Runtime()


def test_workflows(rt: Runtime) -> None:
    assert rt.workflows.names() == ["agents_fanout", "llm_check"]
    rt.workflow("llm_check")  # builds without calling the LLM


def test_agents_build_with_per_agent_models(rt: Runtime) -> None:
    assert rt.agents.model_name("exA") == "hu-llm3"
    assert rt.agents.model_name("exB") == "hu-llm3"
    rt.workflow("agents_fanout")  # builds without calling the LLM


def test_hu_provider_is_rate_limited_and_shared(rt: Runtime) -> None:
    from hackframe.llm.ratelimit import RateLimitedTransport

    hu = rt.models.provider("hu")
    oa = hu._openai_client()
    transport = oa._client._transport
    assert isinstance(transport, RateLimitedTransport)
    assert (transport.rpm, transport.max_concurrency) == (12, 1)
    # hu and hu_think profiles share one limiter
    assert rt.models.client("hu").client is rt.models.client("hu_think").client
