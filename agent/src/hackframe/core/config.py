"""Typed loading of config/*.yaml with ${VAR:-default} env interpolation."""

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _interpolate(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    return value


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return _interpolate(yaml.safe_load(path.read_text()) or {})


# ---------- models.yaml ----------
class ProviderSpec(BaseModel, extra="allow"):
    type: str | None = None  # provider class key; defaults to the provider's name
    base_url: str | None = None
    api_key: str | None = None
    rpm: int | None = None  # client-side requests/minute cap
    max_concurrency: int | None = None  # client-side parallel request cap (1 = serial)
    timeout: float = 300.0


class ModelProfile(BaseModel):
    provider: str = "litellm"
    model: str
    options: dict[str, Any] = Field(default_factory=dict)  # default chat options


class ModelsConfig(BaseModel):
    providers: dict[str, ProviderSpec] = Field(default_factory=dict)
    profiles: dict[str, ModelProfile] = Field(default_factory=dict)
    default_profile: str | None = None


# ---------- agents.yaml ----------
class AgentSpec(BaseModel):
    name: str = ""
    description: str = ""
    model: str | None = None
    instructions: str = "You are a helpful assistant."
    context_providers: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    mcp: list[str] = Field(default_factory=list)
    skills: bool = False


# ---------- mcp.yaml ----------
class MCPServerSpec(BaseModel):
    transport: Literal["stdio", "http"]
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    description: str | None = None
    approval_mode: Literal["always_require", "never_require"] = "never_require"
    allowed_tools: list[str] | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def _check(self) -> "MCPServerSpec":
        if self.transport == "stdio" and not self.command:
            raise ValueError("stdio MCP server needs `command`")
        if self.transport == "http" and not self.url:
            raise ValueError("http MCP server needs `url`")
        return self


# ---------- workflows.yaml ----------
class StepSpec(BaseModel, extra="allow"):
    """Extra keys (e.g. `agent: exA`) are passed to the executor constructor."""

    id: str
    type: str

    @property
    def params(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class EdgeSpec(BaseModel):
    source: str | list[str] = Field(alias="from")
    target: str | list[str] = Field(alias="to")


class WorkflowSpec(BaseModel):
    name: str = ""
    description: str = ""
    pattern: Literal["sequential", "fan_out_in", "graph"] = "sequential"
    steps: list[StepSpec]
    entry: list[str] = Field(default_factory=list)  # graph only
    edges: list[EdgeSpec] = Field(default_factory=list)  # graph only
    exit: str | None = None  # graph only


# ---------- aggregate ----------
class FrameworkConfig(BaseModel):
    models: ModelsConfig
    agents: dict[str, AgentSpec]
    mcp_servers: dict[str, MCPServerSpec]
    workflows: dict[str, WorkflowSpec]

    @classmethod
    def load(cls, config_dir: Path) -> "FrameworkConfig":
        agents = load_yaml(config_dir / "agents.yaml").get("agents", {}) or {}
        mcp = load_yaml(config_dir / "mcp.yaml").get("mcp_servers", {}) or {}
        wfs = load_yaml(config_dir / "workflows.yaml").get("workflows", {}) or {}
        return cls(
            models=ModelsConfig(**load_yaml(config_dir / "models.yaml")),
            agents={k: AgentSpec(name=k, **v) for k, v in agents.items()},
            mcp_servers={k: MCPServerSpec(**v) for k, v in mcp.items()},
            workflows={k: WorkflowSpec(name=k, **v) for k, v in wfs.items()},
        )
