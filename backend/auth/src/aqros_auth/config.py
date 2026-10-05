"""Configuration for the auth service."""

from __future__ import annotations

from datetime import timedelta

from pydantic import Field, PostgresDsn, model_validator

from aqros_core.config import BaseServiceSettings, Environment

#: Placeholder used only for local development. Startup refuses to use this
#: outside `dev`, because a shipped default signing secret is the classic way a
#: JWT key ends up in a public repository.
_DEV_SECRET = "dev-only-insecure-secret-change-me-32chars"


class Settings(BaseServiceSettings):
    """auth settings (override defaults via AQROS_* env vars)."""

    service_name: str = "auth"
    port: int = 8001

    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://aqros:aqros@localhost:5445/aqros_auth"),
    )
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    # --- Token issuance -----------------------------------------------------
    jwt_secret: str = Field(default=_DEV_SECRET)
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "aqros-auth"
    jwt_audience: str = "aqros-platform"
    access_token_ttl_seconds: int = 8 * 60 * 60

    # --- Password policy ----------------------------------------------------
    min_password_length: int = 12
    max_failed_logins_before_lockout: int = 10
    lockout_duration_seconds: int = 900

    # --- Four-eyes workflow -------------------------------------------------
    default_approval_ttl_hours: int = 24

    audit_base_url: str = Field(default="http://audit-ledger:8007")
    audit_request_timeout_seconds: float = 5.0

    @model_validator(mode="after")
    def _reject_placeholder_secret_outside_dev(self) -> Settings:
        """Fail fast on an unsafe signing secret (CLAUDE.md §5: validate at startup).

        Refusing to start is far safer than starting with a known-to-everyone
        signing key — an attacker could then mint a token with any role,
        including the ones that arm live capital.
        """
        if self.environment is not Environment.DEV and self.jwt_secret == _DEV_SECRET:
            raise ValueError(
                "AQROS_JWT_SECRET must be set to a real secret outside the dev "
                "environment; refusing to start with the development placeholder"
            )
        if len(self.jwt_secret) < 32:
            raise ValueError("AQROS_JWT_SECRET must be at least 32 characters")
        return self

    @property
    def access_token_ttl(self) -> timedelta:
        return timedelta(seconds=self.access_token_ttl_seconds)

    @property
    def default_approval_ttl(self) -> timedelta:
        return timedelta(hours=self.default_approval_ttl_hours)
