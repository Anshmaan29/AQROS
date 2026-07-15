"""initial schema: registered_models, model_versions, promotion_requests, approvals, promotion_history, audit_events

Revision ID: 0001
Revises:
Create Date: 2026-07-14

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "registered_models",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("model_type", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("model_name", name="uq_registered_models_model_name"),
    )
    op.create_index("ix_registered_models_model_name", "registered_models", ["model_name"])

    op.create_table(
        "model_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "registered_model_id",
            sa.BigInteger(),
            sa.ForeignKey("registered_models.id"),
            nullable=False,
        ),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("training_run_id", sa.Integer(), nullable=False),
        sa.Column("dataset_name", sa.String(length=64), nullable=False),
        sa.Column("dataset_version", sa.Integer(), nullable=False),
        sa.Column("dataset_checksum", sa.String(length=128), nullable=False),
        sa.Column("feature_versions_json", sa.Text(), nullable=False),
        sa.Column("metrics_json", sa.Text(), nullable=False),
        sa.Column("artifact_path", sa.String(length=512), nullable=False),
        sa.Column("artifact_checksum", sa.String(length=128), nullable=False),
        sa.Column("checksum_algorithm", sa.String(length=32), nullable=False),
        sa.Column("git_commit", sa.String(length=64), nullable=True),
        sa.Column("reproducibility_metadata_json", sa.Text(), nullable=False),
        sa.Column("lifecycle_state", sa.String(length=16), nullable=False),
        sa.Column("approval_state", sa.String(length=16), nullable=False),
        sa.Column("validation_evidence_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "registered_model_id", "version", name="uq_model_versions_model_version"
        ),
    )
    op.create_index("ix_model_versions_model_name", "model_versions", ["model_name"])
    # Single-PRODUCTION invariant: at most one PRODUCTION version per registered
    # model, enforced as a Postgres partial unique index.
    op.create_index(
        "uq_one_production_per_model",
        "model_versions",
        ["registered_model_id"],
        unique=True,
        postgresql_where=sa.text("lifecycle_state = 'production'"),
    )

    op.create_table(
        "promotion_requests",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "model_version_id",
            sa.BigInteger(),
            sa.ForeignKey("model_versions.id"),
            nullable=False,
        ),
        sa.Column("from_state", sa.String(length=16), nullable=False),
        sa.Column("to_state", sa.String(length=16), nullable=False),
        sa.Column("requester", sa.String(length=128), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("approval_state", sa.String(length=16), nullable=False),
        sa.Column("is_rollback", sa.Boolean(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "model_version_id",
            "idempotency_key",
            name="uq_promotion_requests_version_idempotency_key",
        ),
    )

    op.create_table(
        "approvals",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "promotion_request_id",
            sa.BigInteger(),
            sa.ForeignKey("promotion_requests.id"),
            nullable=False,
        ),
        sa.Column("approver", sa.String(length=128), nullable=False),
        sa.Column("approver_kind", sa.String(length=16), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "promotion_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "model_version_id",
            sa.BigInteger(),
            sa.ForeignKey("model_versions.id"),
            nullable=False,
        ),
        sa.Column("from_state", sa.String(length=16), nullable=False),
        sa.Column("to_state", sa.String(length=16), nullable=False),
        sa.Column("requester", sa.String(length=128), nullable=False),
        sa.Column("approvers_json", sa.Text(), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("is_rollback", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_promotion_history_model_version_id",
        "promotion_history",
        ["model_version_id"],
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("before_state", sa.String(length=16), nullable=True),
        sa.Column("after_state", sa.String(length=16), nullable=True),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_correlation_id", "audit_events", ["correlation_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_correlation_id", table_name="audit_events")
    op.drop_table("audit_events")

    op.drop_index("ix_promotion_history_model_version_id", table_name="promotion_history")
    op.drop_table("promotion_history")

    op.drop_table("approvals")

    op.drop_table("promotion_requests")

    op.drop_index("uq_one_production_per_model", table_name="model_versions")
    op.drop_index("ix_model_versions_model_name", table_name="model_versions")
    op.drop_table("model_versions")

    op.drop_index("ix_registered_models_model_name", table_name="registered_models")
    op.drop_table("registered_models")
