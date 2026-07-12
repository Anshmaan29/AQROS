"""Typed configuration management.

All settings are loaded from environment variables (prefix ``AQROS_``) with an
optional ``.env`` file, validated at construction time so a misconfigured
service fails fast at startup rather than at first use.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environment."""

    DEV = "dev"
    TEST = "test"
    STAGING = "staging"
    PROD = "prod"


class BaseServiceSettings(BaseSettings):
    """Base settings shared by every AQROS service.

    Individual services subclass this to override defaults (e.g. ``service_name``
    and ``port``). Any field can be overridden by an ``AQROS_<FIELD>`` env var.
    """

    model_config = SettingsConfigDict(
        env_prefix="AQROS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    service_name: str = "aqros-service"
    version: str = "0.1.0"
    environment: Environment = Environment.DEV
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    log_json: bool = False

    @property
    def is_production(self) -> bool:
        """True when running in the production environment."""
        return self.environment is Environment.PROD
