"""Runtime: the single composition root. Loads config, wires factories, exposes
agents and workflows. Everything else gets its dependencies from here."""

import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from functools import cached_property
from typing import Any

from agent_framework import Agent, Workflow
from pydantic import BaseModel

import hackframe.executors  # noqa: F401  (registers built-in executors)
from hackframe.agents.factory import AgentFactory
from hackframe.core.artifacts import ArtifactStore
from hackframe.core.config import FrameworkConfig
from hackframe.core.schemas import FinalOutput, TaskInput
from hackframe.executors import BuildContext
from hackframe.llm.providers import ModelFactory
from hackframe.mcp.factory import MCPFactory
from hackframe.settings import Settings, get_settings
from hackframe.skills.loader import SkillsLoader
from hackframe.workflows.builder import WorkflowFactory


class Runtime:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.config = FrameworkConfig.load(self.settings.hackframe_config_dir)

    # ---- factories ----
    @cached_property
    def models(self) -> ModelFactory:
        return ModelFactory(self.config.models)

    @cached_property
    def mcp(self) -> MCPFactory:
        return MCPFactory(self.config.mcp_servers)

    @cached_property
    def skills(self) -> SkillsLoader:
        return SkillsLoader(self.settings.hackframe_skills_dir)

    @cached_property
    def agents(self) -> AgentFactory:
        return AgentFactory(self.config.agents, self.models, self.mcp, self.skills, self.settings)

    @cached_property
    def workflows(self) -> WorkflowFactory:
        return WorkflowFactory(
            self.config.workflows,
            BuildContext(agents=self.agents, settings=self.settings, artifacts=self.artifacts, skills=self.skills),
        )

    @cached_property
    def artifacts(self) -> ArtifactStore:
        return ArtifactStore(self.settings.hackframe_temp_dir)

    # ---- convenience ----
    def agent(self, name: str) -> Agent:
        return self.agents.create(name)

    def workflow(self, name: str) -> Workflow:
        return self.workflows.create(name)

    def all_entities(self) -> list[Any]:
        """Every agent + workflow, for DevUI."""
        return [self.agent(n) for n in self.config.agents] + [
            self.workflow(n) for n in self.workflows.names()
        ]

    @asynccontextmanager
    async def open_workflow(self, name: str) -> AsyncIterator[Workflow]:
        """Build a workflow and open/close its agents (MCP connections) in the caller's task."""
        start = len(self.agents.created)
        wf = self.workflow(name)
        async with AsyncExitStack() as stack:
            for agent in self.agents.created[start:]:
                await stack.enter_async_context(agent)
            yield wf

    async def run_workflow(self, name: str, task: TaskInput | BaseModel | dict[str, Any]) -> FinalOutput | dict[str, Any]:
        """`task` may be the workflow's input model, or a dict (validated unless the input is plain JSON).
        Returns FinalOutput, or the `output:` step's dict when the workflow sets one."""
        model = self.workflows.input_model(name)
        if isinstance(task, dict):
            if model is str:  # entry step takes JSON text (e.g. preprocess)
                task = json.dumps(task)
            elif model is not dict:
                task = model.model_validate(task)
        async with self.open_workflow(name) as wf:
            result = await wf.run(task)
        outputs = result.get_outputs()
        if not outputs:
            raise RuntimeError(f"workflow '{name}' produced no output (state={result.get_final_state()})")
        return outputs[-1]

    def describe(self) -> dict[str, Any]:
        from hackframe.context.providers import CONTEXT_PROVIDERS
        from hackframe.executors import EXECUTORS
        from hackframe.llm.providers import PROVIDERS
        from hackframe.tools.builtin import TOOLS

        return {
            "model_profiles": {k: f"{p.provider}:{p.model}" for k, p in self.config.models.profiles.items()},
            "agents": {k: a.model for k, a in self.config.agents.items()},
            "workflows": {k: w.pattern for k, w in self.config.workflows.items()},
            "mcp_servers": {k: f"{s.transport}{'' if s.enabled else ' (disabled)'}" for k, s in self.config.mcp_servers.items()},
            "skills": self.skills.available(),
            "registered": {
                "executors": EXECUTORS.keys(),
                "context_providers": CONTEXT_PROVIDERS.keys(),
                "tools": TOOLS.keys(),
                "model_providers": PROVIDERS.keys(),
            },
        }
