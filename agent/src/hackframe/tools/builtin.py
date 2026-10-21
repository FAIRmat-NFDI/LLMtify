"""Local (in-process) function tools.

Add one:
    @register_tool("my_tool")
    def my_tool(x: Annotated[int, "description"]) -> str: ...
Then list `my_tool` under an agent's `tools` in config/agents.yaml.
"""

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Annotated, Any

from agent_framework import tool

from hackframe.core.registry import Registry

TOOLS: Registry[Any] = Registry("tool")


def register_tool(key: str) -> Callable[[Callable[..., Any]], Any]:
    def decorator(fn: Callable[..., Any]) -> Any:
        wrapped = tool(fn)
        TOOLS.add(key, wrapped)
        return wrapped

    return decorator


@register_tool("utc_now")
def utc_now() -> str:
    """Return the current UTC time in ISO-8601."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@register_tool("unit_convert")
def unit_convert(
    value: Annotated[float, "Numeric value"],
    factor: Annotated[float, "Multiplicative conversion factor"],
) -> float:
    """Placeholder scientific helper: value * factor."""
    return value * factor
