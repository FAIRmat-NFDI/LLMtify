"""Builds MAF MCP tools from config/mcp.yaml.

A fresh tool instance is created per agent; the Agent connects it lazily on first run
and closes it with the agent, so no manual lifecycle handling is needed.
"""

import os
import sys
from typing import Any

from agent_framework import MCPStdioTool, MCPStreamableHTTPTool
from mcp.client.stdio import get_default_environment

from hackframe.core.config import MCPServerSpec

_PASSTHROUGH = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE"}


class MCPFactory:
    def __init__(self, servers: dict[str, MCPServerSpec]) -> None:
        self.servers = servers

    def enabled(self) -> list[str]:
        return [k for k, s in self.servers.items() if s.enabled]

    def create(self, key: str) -> Any:
        if key not in self.servers:
            raise KeyError(f"Unknown MCP server '{key}'. Known: {sorted(self.servers)}")
        spec = self.servers[key]
        if not spec.enabled:
            raise ValueError(f"MCP server '{key}' is disabled in config/mcp.yaml")

        common: dict[str, Any] = {
            "name": key,
            "description": spec.description,
            "approval_mode": spec.approval_mode,
            "allowed_tools": spec.allowed_tools,
            "tool_name_prefix": key,  # avoids name clashes across servers
        }
        if spec.transport == "stdio":
            command = sys.executable if spec.command == "python" else spec.command
            return MCPStdioTool(command=command, args=spec.args, env=self.stdio_env(spec), **common)
        return MCPStreamableHTTPTool(url=spec.url, static_headers=spec.headers, **common)

    @staticmethod
    def stdio_env(spec: MCPServerSpec) -> dict[str, str]:
        """The MCP SDK starts stdio servers with a minimal env (no proxy settings). Pass proxy/CA
        variables through so servers that call the internet (e.g. PubChem) work behind a proxy/VPN."""
        env = get_default_environment()
        env.update({k: v for k, v in os.environ.items() if k.upper() in _PASSTHROUGH})
        env.update(spec.env or {})
        return env
