"""Integration test: Alembic migrations actually produce the expected schema.

Runs ``alembic upgrade head`` against a throwaway Postgres (via
testcontainers) using a *sync* driver (Alembic's migration runner in this
service uses SQLAlchemy's async-engine-to-sync-connection bridge, but we
still need a syncable DSN for the CLI env setup), then asserts the tables and
constraints from ``0001_initial_schema`` exist.
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
            assert {"instruments", "ohlcv_bars", "alembic_version"}.issubset(tables)

            ohlcv_columns = {c["name"] for c in inspector.get_columns("ohlcv_bars")}
            assert {
                "symbol",
                "event_time",
                "interval",
                "open",
                "high",
                "low",
                "close",
                "adjusted_close",
                "volume",
                "source",
                "knowledge_time",
            }.issubset(ohlcv_columns)

            unique_constraints = inspector.get_unique_constraints("ohlcv_bars")
            assert any(uc["name"] == "uq_ohlcv_bars_identity" for uc in unique_constraints)
        finally:
            sync_engine.dispose()
