"""initial schema: feature_definitions, feature_computation_runs, feature_values

Revision ID: 0001
Revises:
Create Date: 2026-07-13

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
        "feature_definitions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("parameters_json", sa.Text(), nullable=False),
        sa.Column("min_bars_required", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", "version", name="uq_feature_definitions_name_version"),
    )
    op.create_index("ix_feature_definitions_name", "feature_definitions", ["name"])

    op.create_table(
        "feature_computation_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bars_read", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("features_computed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("features_persisted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("features_rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejection_reasons_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_feature_computation_runs_symbol", "feature_computation_runs", ["symbol"])

    op.create_table(
        "feature_values",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("feature_name", sa.String(length=64), nullable=False),
        sa.Column("feature_version", sa.Integer(), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("knowledge_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "computation_run_id",
            sa.BigInteger(),
            sa.ForeignKey("feature_computation_runs.id"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "symbol",
            "feature_name",
            "feature_version",
            "event_time",
            name="uq_feature_values_identity",
        ),
    )
    op.create_index("ix_feature_values_symbol", "feature_values", ["symbol"])
    op.create_index("ix_feature_values_feature_name", "feature_values", ["feature_name"])
    op.create_index("ix_feature_values_event_time", "feature_values", ["event_time"])


def downgrade() -> None:
    op.drop_index("ix_feature_values_event_time", table_name="feature_values")
    op.drop_index("ix_feature_values_feature_name", table_name="feature_values")
    op.drop_index("ix_feature_values_symbol", table_name="feature_values")
    op.drop_table("feature_values")

    op.drop_index("ix_feature_computation_runs_symbol", table_name="feature_computation_runs")
    op.drop_table("feature_computation_runs")

    op.drop_index("ix_feature_definitions_name", table_name="feature_definitions")
    op.drop_table("feature_definitions")
