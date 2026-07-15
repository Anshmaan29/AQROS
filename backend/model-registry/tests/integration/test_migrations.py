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
            assert {
                "registered_models",
                "model_versions",
                "promotion_requests",
                "approvals",
                "promotion_history",
                "audit_events",
                "alembic_version",
            }.issubset(tables)

            registered_model_columns = {
                c["name"] for c in inspector.get_columns("registered_models")
            }
            assert {"model_name", "model_type", "created_at"}.issubset(registered_model_columns)

            model_version_columns = {c["name"] for c in inspector.get_columns("model_versions")}
            assert {
                "registered_model_id",
                "model_name",
                "version",
                "training_run_id",
                "dataset_name",
                "dataset_version",
                "dataset_checksum",
                "feature_versions_json",
                "metrics_json",
                "artifact_path",
                "artifact_checksum",
                "checksum_algorithm",
                "git_commit",
                "reproducibility_metadata_json",
                "lifecycle_state",
                "approval_state",
                "validation_evidence_json",
            }.issubset(model_version_columns)

            promotion_request_columns = {
                c["name"] for c in inspector.get_columns("promotion_requests")
            }
            assert {
                "model_version_id",
                "from_state",
                "to_state",
                "requester",
                "justification",
                "approval_state",
                "is_rollback",
                "idempotency_key",
            }.issubset(promotion_request_columns)

            approval_columns = {c["name"] for c in inspector.get_columns("approvals")}
            assert {
                "promotion_request_id",
                "approver",
                "approver_kind",
                "decision",
                "reason",
            }.issubset(approval_columns)

            promotion_history_columns = {
                c["name"] for c in inspector.get_columns("promotion_history")
            }
            assert {
                "model_version_id",
                "from_state",
                "to_state",
                "requester",
                "approvers_json",
                "justification",
                "is_rollback",
            }.issubset(promotion_history_columns)

            audit_event_columns = {c["name"] for c in inspector.get_columns("audit_events")}
            assert {
                "action",
                "actor",
                "model_name",
                "version",
                "before_state",
                "after_state",
                "justification",
                "correlation_id",
            }.issubset(audit_event_columns)

            # Version-uniqueness backstop (Requirements 3.2, 22.2).
            unique_constraints = inspector.get_unique_constraints("model_versions")
            assert any(uc["name"] == "uq_model_versions_model_version" for uc in unique_constraints)

            # Governance idempotency (Requirement 19.11).
            promotion_request_unique_constraints = inspector.get_unique_constraints(
                "promotion_requests"
            )
            assert any(
                uc["name"] == "uq_promotion_requests_version_idempotency_key"
                for uc in promotion_request_unique_constraints
            )

            # Single-PRODUCTION invariant: the partial unique index must exist
            # after upgrade (Requirements 16.1, 23.3).
            with sync_engine.connect() as conn:
                row = conn.execute(
                    sa.text(
                        "SELECT indexdef FROM pg_indexes "
                        "WHERE tablename = 'model_versions' "
                        "AND indexname = 'uq_one_production_per_model'"
                    )
                ).fetchone()
                assert row is not None, "uq_one_production_per_model index not found after upgrade"
                indexdef = row[0]
                assert "UNIQUE" in indexdef
                assert "lifecycle_state" in indexdef
                assert "production" in indexdef
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

        sync_engine_after = sa.create_engine(sync_url)
        try:
            inspector_after = inspect(sync_engine_after)
            tables_after = set(inspector_after.get_table_names())
            assert (
                not {
                    "registered_models",
                    "model_versions",
                    "promotion_requests",
                    "approvals",
                    "promotion_history",
                    "audit_events",
                }
                & tables_after
            )

            # The partial unique index must be gone along with its table.
            with sync_engine_after.connect() as conn:
                row_after = conn.execute(
                    sa.text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE indexname = 'uq_one_production_per_model'"
                    )
                ).fetchone()
                assert (
                    row_after is None
                ), "uq_one_production_per_model index still present after downgrade"
        finally:
            sync_engine_after.dispose()
