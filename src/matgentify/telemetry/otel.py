"""OpenTelemetry -> Aspire dashboard (OTLP gRPC). Traces, metrics, logs follow the
OTel GenAI semantic conventions emitted by Agent Framework (agents, workflows, tools)."""

import logging

from agent_framework.observability import configure_otel_providers

from matgentify.settings import Settings

_configured = False


def setup_telemetry(settings: Settings, *, console: bool = False) -> None:
    global _configured
    if _configured:
        return
    configure_otel_providers(
        service_name=settings.otel_service_name,
        otlp_endpoint=settings.otel_exporter_otlp_endpoint or None,
        enable_sensitive_data=settings.enable_sensitive_data,
        enable_console_exporters=console,
    )
    logging.getLogger(__name__).info(
        "telemetry -> %s (service=%s)", settings.otel_exporter_otlp_endpoint, settings.otel_service_name
    )
    _configured = True
