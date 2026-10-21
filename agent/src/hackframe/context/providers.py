"""Context providers: inject instructions/messages/tools before each agent run,
and capture state after it.

Add one:
    @CONTEXT_PROVIDERS.register("my_ctx")
    class MyContext(BaseHackContextProvider):
        async def before_run(self, *, agent, session, context, state):
            context.extend_instructions(self.source_id, "...")
Then list `my_ctx` under an agent's `context_providers` in config/agents.yaml.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_framework import AgentSession, ContextProvider, SessionContext

from hackframe.core.config import AgentSpec
from hackframe.core.registry import Registry
from hackframe.settings import Settings

CONTEXT_PROVIDERS: Registry[type["BaseHackContextProvider"]] = Registry("context provider")


class BaseHackContextProvider(ContextProvider):
    """Base with a uniform constructor so the factory can build any provider."""

    key: str = "base"

    def __init__(self, agent_spec: AgentSpec, settings: Settings) -> None:
        super().__init__(self.key)
        self.agent_spec = agent_spec
        self.settings = settings

    @classmethod
    def create(cls, agent_spec: AgentSpec, settings: Settings) -> "BaseHackContextProvider":
        return cls(agent_spec, settings)


@CONTEXT_PROVIDERS.register("session_info")
class SessionInfoProvider(BaseHackContextProvider):
    """Adds run metadata and counts runs per session (state is per-session)."""

    key = "session_info"

    async def before_run(
        self, *, agent: Any, session: AgentSession, context: SessionContext, state: dict[str, Any]
    ) -> None:
        state["runs"] = state.get("runs", 0) + 1
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        context.extend_instructions(
            self.source_id,
            f"[session] agent={self.agent_spec.name} utc={now} run#={state['runs']}",
        )


@CONTEXT_PROVIDERS.register("domain_notes")
class DomainNotesProvider(BaseHackContextProvider):
    """Injects config/knowledge/domain_notes.md - the scientist-editable knowledge file."""

    key = "domain_notes"

    def __init__(self, agent_spec: AgentSpec, settings: Settings) -> None:
        super().__init__(agent_spec, settings)
        path = Path(settings.hackframe_config_dir) / "knowledge" / "domain_notes.md"
        self._notes = path.read_text() if path.exists() else ""

    async def before_run(
        self, *, agent: Any, session: AgentSession, context: SessionContext, state: dict[str, Any]
    ) -> None:
        if self._notes:
            context.extend_instructions(self.source_id, f"[domain notes]\n{self._notes}")


# Placeholder: retrieval / vector-store context
# @CONTEXT_PROVIDERS.register("rag")
# class RagProvider(BaseHackContextProvider):
#     key = "rag"
#     async def before_run(self, *, agent, session, context, state):
#         query = " ".join(m.text or "" for m in context.get_messages())
#         docs = await my_retriever.search(query)
#         context.extend_instructions(self.source_id, "\n".join(docs))
