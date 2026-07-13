"""Integration test: Alembic migrations produce the expected schema (task 9.4)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import inspect
from testcontainers.postgres import PostgresContainer

pytestmark = pytest.mark.integration

SERVICE_ROOT = Path(__file__).resolve().parents[2]


def test_alembic_upgrade_head_creates_expected_schema() -> None:
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        async_url = container.get_connection_url()

        env = os.environ.copy()
        env["AQROS_DATABASE_URL"] = async_url

        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=SERVICE_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, f"alembic upgrade failed:\n{result.stdout}\n{result.stderr}"

        sync_url = async_url.replace("postgresql+asyncpg", "postgresql+psycopg")
        sync_engine = sa.create_engine(sync_url)
        try:
            inspector = inspect(sync_engine)
            tables = set(inspector.get_table_names())
            assert {"training_runs", "trained_models", "alembic_version"}.issubset(tables)

            run_columns = {c["name"] for c in inspector.get_columns("training_runs")}
            assert {
                "dataset_name",
                "build_run_id",
                "model_types_json",
                "status",
                "started_at",
                "outcomes_json",
            }.issubset(run_columns)

            model_columns = {c["name"] for c in inspector.get_columns("trained_models")}
            assert {
                "model_name",
                "model_type",
                "model_version",
                "training_run_id",
                "artifact_path",
                "reproducibility_metadata_json",
            }.issubset(model_columns)

            unique_constraints = inspector.get_unique_constraints("trained_models")
            assert any(uc["name"] == "uq_trained_models_name_version" for uc in unique_constraints)
        finally:
            sync_engine.dispose()

        downgrade = subprocess.run(
            [sys.executable, "-m", "alembic", "downgrade", "base"],
            cwd=SERVICE_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert (
            downgrade.returncode == 0
        ), f"downgrade failed:\n{downgrade.stdout}\n{downgrade.stderr}"
