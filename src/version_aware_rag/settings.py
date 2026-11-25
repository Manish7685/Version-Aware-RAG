"""Application settings, read from environment variables (prefix ``VRAG_``) or a local ``.env``.

Secrets never get default values and are never logged. The Gemini key (RAG-M4 S3,
``VRAG_GEMINI_API_KEY``) is a ``SecretStr`` excluded from ``model_dump``, so ``vrag info`` never
shows it.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LogFormat(StrEnum):
    """Output format for log records."""

    JSON = "json"
    CONSOLE = "console"


class LogLevel(StrEnum):
    """Standard library logging levels accepted in configuration."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Settings(BaseSettings):
    """Typed, validated runtime configuration."""

    model_config = SettingsConfigDict(
        env_prefix="VRAG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    log_level: LogLevel = LogLevel.INFO
    log_format: LogFormat = LogFormat.JSON
    data_dir: Path = Field(default=Path("data"), description="Manifests and locally built data")
    results_dir: Path = Field(default=Path("results"), description="Raw outputs and tables")
    gemini_api_key: SecretStr | None = Field(
        default=None, exclude=True, description="Gemini API key (benchmark drafting only)"
    )


def get_settings() -> Settings:
    """Build settings from the current environment (no caching, so tests stay isolated)."""
    return Settings()
