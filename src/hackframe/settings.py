"""Process-level settings (env / .env). Domain config lives in config/*.yaml."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # framework
    hackframe_config_dir: Path = Path("config")
    hackframe_skills_dir: Path = Path("skills")

    # run artifacts (full input JSON per run: temp/<uuid>.json)
    hackframe_temp_dir: Path = Path("temp")

    # telemetry
    otel_service_name: str = "hackframe"
    otel_exporter_otlp_endpoint: str | None = "http://localhost:4317"
    enable_sensitive_data: bool = True

    # devui
    devui_port: int = 8080
    devui_auto_open: bool = False
    devui_auth_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
