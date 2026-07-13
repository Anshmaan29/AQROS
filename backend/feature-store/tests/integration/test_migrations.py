"""Integration test: Alembic migrations actually produce the expected schema.

Runs ``alembic upgrade head`` against a throwaway Postgres (via
testcontainers), then asserts the tables and constraints from
``0001_initial_schema`` exist. Mirrors ``aqros_market_data``'s equivalent
test exactly.
"""

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
            assert {
                "feature_definitions",
                "feature_values",
                "feature_computation_runs",
                "alembic_version",
            }.issubset(tables)

            definition_columns = {c["name"] for c in inspector.get_columns("feature_definitions")}
            assert {
                "name",
                "version",
                "category",
                "description",
                "parameters_json",
                "min_bars_required",
            }.issubset(definition_columns)

            value_columns = {c["name"] for c in inspector.get_columns("feature_values")}
            assert {
                "symbol",
                "feature_name",
                "feature_version",
                "event_time",
                "value",
                "knowledge_time",
                "computation_run_id",
            }.issubset(value_columns)

            unique_constraints = inspector.get_unique_constraints("feature_values")
            assert any(uc["name"] == "uq_feature_values_identity" for uc in unique_constraints)

            def_unique_constraints = inspector.get_unique_constraints("feature_definitions")
            assert any(
                uc["name"] == "uq_feature_definitions_name_version" for uc in def_unique_constraints
            )

            fks = inspector.get_foreign_keys("feature_values")
            assert any(fk["referred_table"] == "feature_computation_runs" for fk in fks)
        finally:
            sync_engine.dispose()
