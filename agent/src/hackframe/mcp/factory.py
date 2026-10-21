"""Builds MAF MCP tools from config/mcp.yaml.

A fresh tool instance is created per agent; the Agent connects it lazily on first run
and closes it with the agent, so no manual lifecycle handling is needed.
"""

import sys
from typing import Any

from agent_framework import MCPStdioTool, MCPStreamableHTTPTool

from hackframe.core.config import MCPServerSpec


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
            return MCPStdioTool(command=command, args=spec.args, env=spec.env, **common)
        return MCPStreamableHTTPTool(url=spec.url, static_headers=spec.headers, **common)
