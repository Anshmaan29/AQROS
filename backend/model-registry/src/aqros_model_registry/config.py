"""Configuration for the model-registry service.

Extends the shared :class:`BaseServiceSettings` with this service's own
database, the Training Pipeline service's base URL (its sole upstream
dependency), the local model-artifact directory, and optional
artifact-signing configuration. Everything is loaded from ``AQROS_*``
environment variables or a ``.env`` file and validated at construction — a
misconfigured deployment fails fast at startup, matching
``aqros_training_pipeline.config``.
"""

from __future__ import annotations

from pydantic import AnyHttpUrl, Field, PostgresDsn

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """model-registry settings (override defaults via AQROS_* env vars)."""

    service_name: str = "model-registry"
    port: int = 8004

    # --- Database (owned exclusively by this service) -------------------
    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://aqros:aqros@localhost:5436/aqros_model_registry"),
        description="Async SQLAlchemy DSN for the model-registry database.",
    )
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    # --- Upstream service client (Training Pipeline ONLY) -----------------
    # The Model Registry's only integration point with the rest of the
    # platform is the Training Pipeline's published REST API (CLAUDE.md
    # §7.9, Requirements 1.1-1.4). It never opens the Training Pipeline's
    # database and never contacts Dataset Builder, Feature Store, or Market
    # Data at all.
    training_pipeline_base_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://localhost:8009"),
        description="Base URL of the Training Pipeline Service's REST API.",
    )
    upstream_request_timeout_seconds: float = 30.0

    # --- Model artifact storage -------------------------------------------
    # A local directory behind the ArtifactStore port, standing in for the
    # eventual object store (Requirement 8.4, 25.4 — swappable interface).
    artifact_dir: str = "/data/model-registry/artifacts"

    # --- Artifact signing (optional) --------------------------------------
    # Where configured, the ArtifactSigner port verifies a signature before
    # serving an artifact and refuses to serve an unsigned or invalidly
    # signed one (Requirement 21.3). Disabled by default: the adapter is a
    # tolerant no-op when signing is not configured.
    artifact_signing_enabled: bool = False
    artifact_signing_public_key_path: str | None = None
