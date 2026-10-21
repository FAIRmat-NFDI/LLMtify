"""Model providers: turn a ModelProfile into a MAF chat client.

Default: LiteLLM proxy (OpenAI-compatible /v1/chat/completions), so any model
LiteLLM can route (OpenAI, Azure, Anthropic, Ollama, ...) is one alias away.

Add a provider:
    @PROVIDERS.register("my_provider")
    class MyProvider(ModelProvider):
        def create_client(self, profile): ...
"""

from abc import ABC, abstractmethod
from typing import Any

from agent_framework.openai import OpenAIChatCompletionClient

from hackframe.core.config import ModelProfile, ModelsConfig, ProviderSpec
from hackframe.core.registry import Registry

PROVIDERS: Registry[type["ModelProvider"]] = Registry("model provider")


class ModelProvider(ABC):
    def __init__(self, spec: ProviderSpec) -> None:
        self.spec = spec

    @abstractmethod
    def create_client(self, profile: ModelProfile) -> Any:
        """Return a MAF chat client (anything implementing SupportsChatGetResponse)."""


@PROVIDERS.register("litellm")
class LiteLLMProxyProvider(ModelProvider):
    def create_client(self, profile: ModelProfile) -> OpenAIChatCompletionClient:
        return OpenAIChatCompletionClient(
            model=profile.model,
            base_url=self.spec.base_url,
            api_key=self.spec.api_key or "not-needed",
        )


@PROVIDERS.register("openai")
class OpenAIDirectProvider(ModelProvider):
    """Bypass the proxy (reads OPENAI_API_KEY if api_key unset)."""

    def create_client(self, profile: ModelProfile) -> OpenAIChatCompletionClient:
        return OpenAIChatCompletionClient(model=profile.model, api_key=self.spec.api_key or None)


class ModelFactory:
    """Resolves profile name -> chat client. Clients are cached per profile."""

    def __init__(self, config: ModelsConfig) -> None:
        self.config = config
        self._cache: dict[str, Any] = {}

    def profile(self, name: str | None) -> ModelProfile:
        key = name or self.config.default_profile
        if not key or key not in self.config.profiles:
            raise KeyError(f"Unknown model profile '{key}'. Known: {sorted(self.config.profiles)}")
        return self.config.profiles[key]

    def client(self, name: str | None) -> Any:
        key = name or self.config.default_profile or ""
        if key not in self._cache:
            prof = self.profile(key)
            provider_cls = PROVIDERS.get(prof.provider)
            spec = self.config.providers.get(prof.provider, ProviderSpec())
            self._cache[key] = provider_cls(spec).create_client(prof)
        return self._cache[key]
