"""Configuration for the dataset-builder service.

Extends the shared :class:`BaseServiceSettings` with this service's own
database connection, the Market Data and Feature Store services' base URLs,
and the local Parquet artifact directory. Everything is loaded from
environment variables (prefix ``AQROS_``) or a ``.env`` file and validated at
construction time, so a misconfigured deployment fails fast at startup —
same discipline as ``aqros_market_data.config`` and
``aqros_feature_store.config``.
"""

from __future__ import annotations

from pydantic import AnyHttpUrl, Field, PostgresDsn

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """dataset-builder settings (override defaults via AQROS_* env vars)."""

    service_name: str = "dataset-builder"
    port: int = 8008

    # --- Database (owned exclusively by this service) -------------------
    database_url: PostgresDsn = Field(
        default=PostgresDsn(
            "postgresql+asyncpg://aqros:aqros@localhost:5434/aqros_dataset_builder"
        ),
        description="Async SQLAlchemy DSN for the dataset-builder database.",
    )
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    # --- Upstream service clients -----------------------------------------
    # The Dataset Builder never touches market-data's or feature-store's
    # databases (CLAUDE.md §7.9): it reads OHLCV bars (for labels, which may
    # legitimately use future prices per claude_ROI.md §18.2) exclusively
    # through market-data's REST API, and reads engineered features (the X
    # matrix, which must stay strictly causal) exclusively through
    # feature-store's REST API.
    market_data_base_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://localhost:8002"),
        description="Base URL of the Market Data Service's REST API.",
    )
    feature_store_base_url: AnyHttpUrl = Field(
        default=AnyHttpUrl("http://localhost:8003"),
        description="Base URL of the Feature Store Service's REST API.",
    )
    upstream_request_timeout_seconds: float = 30.0
    upstream_max_retries: int = 3
    upstream_retry_backoff_seconds: float = 1.0
    upstream_page_size: int = 1000

    # --- Dataset artifact storage -----------------------------------------
    # A local directory behind the DatasetStorage port, standing in for the
    # eventual object-store/lake (CLAUDE.md §9: "mocks sit behind real
    # interfaces" — swapping this for S3/R2/MinIO later is a new adapter,
    # not a rewrite of domain/api code).
    dataset_artifact_dir: str = "/data/dataset-builder/artifacts"

    # --- Manifest generation -----------------------------------------------
    # Working directory to run `git rev-parse HEAD` in, for the dataset
    # manifest's `git_commit` field (CLAUDE.md §5: manifests pin a code SHA).
    # Defaults to the container's app directory, where the repo is checked
    # out; falls back to `None` (manifest omits git_commit) if not a repo.
    git_repo_root: str = "/app"
