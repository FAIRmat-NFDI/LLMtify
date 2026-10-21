"""Offline smoke tests: no LLM, no network."""

import pytest

from hackframe.core.runtime import Runtime


@pytest.fixture(scope="module")
def rt() -> Runtime:
    return Runtime()


def test_only_one_workflow(rt: Runtime) -> None:
    assert rt.workflows.names() == ["agents_fanout"]


def test_agents_build_with_per_agent_models(rt: Runtime) -> None:
    assert rt.agents.model_name("exA") == "gpt-5-mini"
    assert rt.agents.model_name("exB") == "gpt-5-nano"
    rt.workflow("agents_fanout")  # builds without calling the LLM
