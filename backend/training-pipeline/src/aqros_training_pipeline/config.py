"""Configuration for the training-pipeline service.

Extends the shared :class:`BaseServiceSettings` with this service's own
database, the Dataset Builder service's base URL (its sole upstream
dependency), and the local model-artifact directory. Everything is loaded
from ``AQROS_*`` environment variables or a ``.env`` file and validated at
construction — a misconfigured deployment fails fast at startup, matching
``aqros_dataset_builder.config``.
"""

from __future__ import annotations

from pydantic import AnyHttpUrl, Field, PostgresDsn

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """training-pipeline settings (override defaults via AQROS_* env vars)."""

    service_name: str = "training-pipeline"
    port: int = 8009

    # --- Database (owned exclusively by this service) -------------------
    database_url: PostgresDsn = Field(
        default=PostgresDsn(
            "postgresql+asyncpg://aqros:aqros@localhost:5435/aqros_training_pipeline"
        ),
        description="Async SQLAlchemy DSN for the training-pipeline database.",
    )
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    # --- Upstream service client (Dataset Builder ONLY) -------------------
    # The Training Pipeline's only integration point with the rest of the
    # platform is the Dataset Builder's published REST API (CLAUDE.md §7.9,
    # Requirements 1.1-1.4). It never opens the Dataset Builder's database
    # and never contacts Market Data or Feature Store at all.
    dataset_builder_base_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://localhost:8008"),
        description="Base URL of the Dataset Builder Service's REST API.",
    )
    upstream_request_timeout_seconds: float = 30.0

    # --- Model artifact storage -------------------------------------------
    # A local directory behind the ArtifactStore port, standing in for the
    # eventual object store (Requirement 13.4 — swappable interface).
    artifact_dir: str = "/data/training-pipeline/artifacts"

    # --- Reproducibility metadata -----------------------------------------
    # Working directory for `git rev-parse HEAD` (Reproducibility_Metadata's
    # git_commit field, Requirement 12.3). Falls back to "commit unavailable"
    # if not a git repo.
    git_repo_root: str = "/app"
