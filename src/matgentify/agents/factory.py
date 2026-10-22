"""AgentSpec -> agent_framework.Agent.

One agent class for everything; model, instructions, context, tools, MCP and skills
all come from the spec, so exA can run on `mini` and exB on `nano` with no code change.
"""

from typing import Any

from agent_framework import Agent

from matgentify.context.providers import CONTEXT_PROVIDERS, SkillGroundingProvider
from matgentify.core.config import AgentSpec
from matgentify.llm.providers import ModelFactory
from matgentify.mcp.factory import MCPFactory
from matgentify.settings import Settings
from matgentify.skills.loader import SkillsLoader
from matgentify.tools.builtin import TOOLS


class AgentFactory:
    def __init__(
        self,
        specs: dict[str, AgentSpec],
        models: ModelFactory,
        mcp: MCPFactory,
        skills: SkillsLoader,
        settings: Settings,
    ) -> None:
        self.specs = specs
        self.models = models
        self.mcp = mcp
        self.skills = skills
        self.settings = settings
        self.created: list[Agent] = []  # tracked so the runtime can open/close their MCP connections

    def spec(self, name: str) -> AgentSpec:
        if name not in self.specs:
            raise KeyError(f"Unknown agent '{name}'. Known: {sorted(self.specs)}")
        return self.specs[name]

    def model_name(self, name: str) -> str:
        return self.models.profile(self.spec(name).model).model

    def create(self, name: str) -> Agent:
        spec = self.spec(name)

        context_providers: list[Any] = [
            CONTEXT_PROVIDERS.get(k).create(spec, self.settings) for k in spec.context_providers
        ]
        if spec.ground_skills:
            context_providers.append(SkillGroundingProvider(self.skills, spec.ground_skills))
        if spec.skills and (sp := self.skills.provider()):
            context_providers.append(sp)

        tools: list[Any] = [TOOLS.get(k) for k in spec.tools]
        tools += [self.mcp.create(k) for k in spec.mcp]

        agent = Agent(
            client=self.models.client(spec.model),
            default_options=self.models.default_options(spec.model) or None,
            name=spec.name,
            description=spec.description or None,
            instructions=spec.instructions,
            tools=tools or None,
            context_providers=context_providers or None,
        )
        self.created.append(agent)
        return agent
