"""Model providers: turn a ModelProfile into a MAF chat client.

Default: LiteLLM proxy (OpenAI-compatible /v1/chat/completions), so any model
LiteLLM can route (OpenAI, Azure, HU Berlin vLLM, Ollama, ...) is one alias away.

Provider entries in models.yaml are named; `type` picks the class (defaults to the name):
    providers:
      hu: {type: litellm, base_url: ..., rpm: 12, max_concurrency: 1}

Add a provider class:
    @PROVIDERS.register("my_provider")
    class MyProvider(ModelProvider):
        def create_client(self, profile): ...
"""

from abc import ABC, abstractmethod
from typing import Any

import httpx
from agent_framework.openai import OpenAIChatCompletionClient
from openai import AsyncOpenAI

from hackframe.core.config import ModelProfile, ModelsConfig, ProviderSpec
from hackframe.core.registry import Registry
from hackframe.llm.ratelimit import RateLimitedTransport

PROVIDERS: Registry[type["ModelProvider"]] = Registry("model provider")


class ModelProvider(ABC):
    def __init__(self, spec: ProviderSpec) -> None:
        self.spec = spec

    @abstractmethod
    def create_client(self, profile: ModelProfile) -> Any:
        """Return a MAF chat client (anything implementing SupportsChatGetResponse)."""


@PROVIDERS.register("litellm")
class LiteLLMProxyProvider(ModelProvider):
    """Any OpenAI-compatible endpoint (LiteLLM proxy, vLLM, HU Berlin LLMs, ...).
    One rate-limited HTTP client per provider entry, shared by all its profiles."""

    def __init__(self, spec: ProviderSpec) -> None:
        super().__init__(spec)
        self._openai: AsyncOpenAI | None = None

    def _openai_client(self) -> AsyncOpenAI:
        if self._openai is None:
            http = None
            if self.spec.rpm or self.spec.max_concurrency:
                transport = RateLimitedTransport(rpm=self.spec.rpm, max_concurrency=self.spec.max_concurrency)
                http = httpx.AsyncClient(transport=transport, timeout=self.spec.timeout)
            self._openai = AsyncOpenAI(
                base_url=self.spec.base_url,
                api_key=self.spec.api_key or "not-needed",
                timeout=self.spec.timeout,
                http_client=http,
            )
        return self._openai

    def create_client(self, profile: ModelProfile) -> OpenAIChatCompletionClient:
        return OpenAIChatCompletionClient(model=profile.model, async_client=self._openai_client())


@PROVIDERS.register("openai")
class OpenAIDirectProvider(ModelProvider):
    """Bypass the proxy (reads OPENAI_API_KEY if api_key unset)."""

    def create_client(self, profile: ModelProfile) -> OpenAIChatCompletionClient:
        return OpenAIChatCompletionClient(model=profile.model, api_key=self.spec.api_key or None)


class ModelFactory:
    """Resolves profile name -> chat client. Clients cached per profile, providers per entry."""

    def __init__(self, config: ModelsConfig) -> None:
        self.config = config
        self._clients: dict[str, Any] = {}
        self._providers: dict[str, ModelProvider] = {}

    def profile(self, name: str | None) -> ModelProfile:
        key = name or self.config.default_profile
        if not key or key not in self.config.profiles:
            raise KeyError(f"Unknown model profile '{key}'. Known: {sorted(self.config.profiles)}")
        return self.config.profiles[key]

    def provider(self, name: str) -> ModelProvider:
        if name not in self._providers:
            spec = self.config.providers.get(name, ProviderSpec())
            self._providers[name] = PROVIDERS.get(spec.type or name)(spec)
        return self._providers[name]

    def client(self, name: str | None) -> Any:
        key = name or self.config.default_profile or ""
        if key not in self._clients:
            prof = self.profile(key)
            self._clients[key] = self.provider(prof.provider).create_client(prof)
        return self._clients[key]

    def default_options(self, name: str | None) -> dict[str, Any]:
        """Per-profile chat options (e.g. temperature, extra_body) -> Agent default_options."""
        return dict(self.profile(name).options)
