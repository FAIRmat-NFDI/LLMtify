"""Launch Agent Framework DevUI with every configured agent and workflow."""

from agent_framework.devui import serve

from hackframe.core.runtime import Runtime
from hackframe.telemetry.otel import setup_telemetry


def launch(runtime: Runtime, port: int | None = None) -> None:
    s = runtime.settings
    setup_telemetry(s)
    serve(
        entities=runtime.all_entities(),
        port=port or s.devui_port,
        auto_open=s.devui_auto_open,
        auth_enabled=s.devui_auth_enabled,
        instrumentation_enabled=True,
    )
