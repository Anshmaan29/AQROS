"""initial schema: dataset_definitions, dataset_build_runs

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
        "dataset_definitions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("symbols_json", sa.Text(), nullable=False),
        sa.Column("feature_names_json", sa.Text(), nullable=False),
        sa.Column("label_type", sa.String(length=32), nullable=False),
        sa.Column("horizon", sa.String(length=8), nullable=False),
        sa.Column("split_strategy", sa.String(length=32), nullable=False),
        sa.Column("split_params_json", sa.Text(), nullable=False),
        sa.Column("start_date", sa.String(length=10), nullable=False),
        sa.Column("end_date", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", "version", name="uq_dataset_definitions_name_version"),
    )
    op.create_index("ix_dataset_definitions_name", "dataset_definitions", ["name"])

    op.create_table(
        "dataset_build_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("dataset_name", sa.String(length=64), nullable=False),
        sa.Column("dataset_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("bars_read", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_generated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rejection_reasons_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("leakage_audit_passed", sa.Boolean(), nullable=True),
        sa.Column("leakage_audit_findings_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("label_balance_json", sa.Text(), nullable=True),
        sa.Column("row_counts_by_role_json", sa.Text(), nullable=True),
        sa.Column("quality_report_json", sa.Text(), nullable=True),
        sa.Column("parquet_path", sa.String(length=512), nullable=True),
        sa.Column("manifest_path", sa.String(length=512), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_dataset_build_runs_dataset_name", "dataset_build_runs", ["dataset_name"])


def downgrade() -> None:
    op.drop_index("ix_dataset_build_runs_dataset_name", table_name="dataset_build_runs")
    op.drop_table("dataset_build_runs")

    op.drop_index("ix_dataset_definitions_name", table_name="dataset_definitions")
    op.drop_table("dataset_definitions")
