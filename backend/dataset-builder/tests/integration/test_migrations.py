"""Integration test: Alembic migrations actually produce the expected schema."""

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
            assert {"dataset_definitions", "dataset_build_runs", "alembic_version"}.issubset(tables)

            definition_columns = {c["name"] for c in inspector.get_columns("dataset_definitions")}
            assert {
                "name",
                "version",
                "symbols_json",
                "feature_names_json",
                "label_type",
                "horizon",
                "split_strategy",
                "split_params_json",
                "start_date",
                "end_date",
            }.issubset(definition_columns)

            run_columns = {c["name"] for c in inspector.get_columns("dataset_build_runs")}
            assert {
                "dataset_name",
                "dataset_version",
                "status",
                "leakage_audit_passed",
                "quality_report_json",
                "parquet_path",
                "manifest_path",
            }.issubset(run_columns)

            unique_constraints = inspector.get_unique_constraints("dataset_definitions")
            assert any(
                uc["name"] == "uq_dataset_definitions_name_version" for uc in unique_constraints
            )
        finally:
            sync_engine.dispose()
