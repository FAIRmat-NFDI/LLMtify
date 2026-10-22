"""Internal demo MCP server (stdio). Template for scientists' own MCP servers.

Run standalone:  python -m hackframe.mcp.servers.hello_server
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("hello_local")


@mcp.tool()
def echo(text: str) -> str:
    """Echo the given text back."""
    return f"hello_local echo: {text}"


@mcp.tool()
def add(a: float, b: float) -> float:
    """Add two numbers."""
    return a + b


if __name__ == "__main__":
    mcp.run()  # stdio transport
