"""initial schema: training_runs, trained_models

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
        "training_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("dataset_name", sa.String(length=64), nullable=False),
        sa.Column("build_run_id", sa.Integer(), nullable=False),
        sa.Column("model_types_json", sa.Text(), nullable=False),
        sa.Column("hyperparameters_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcomes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index("ix_training_runs_dataset_name", "training_runs", ["dataset_name"])

    op.create_table(
        "trained_models",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("model_type", sa.String(length=32), nullable=False),
        sa.Column("model_version", sa.Integer(), nullable=False),
        sa.Column(
            "training_run_id",
            sa.BigInteger(),
            sa.ForeignKey("training_runs.id"),
            nullable=False,
        ),
        sa.Column("dataset_name", sa.String(length=64), nullable=False),
        sa.Column("dataset_version", sa.Integer(), nullable=False),
        sa.Column("build_run_id", sa.Integer(), nullable=False),
        sa.Column("artifact_path", sa.String(length=512), nullable=False),
        sa.Column("per_fold_metrics_json", sa.Text(), nullable=False),
        sa.Column("aggregated_metrics_json", sa.Text(), nullable=False),
        sa.Column("feature_importance_json", sa.Text(), nullable=False),
        sa.Column("reproducibility_metadata_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("model_name", "model_version", name="uq_trained_models_name_version"),
    )
    op.create_index("ix_trained_models_model_name", "trained_models", ["model_name"])


def downgrade() -> None:
    op.drop_index("ix_trained_models_model_name", table_name="trained_models")
    op.drop_table("trained_models")

    op.drop_index("ix_training_runs_dataset_name", table_name="training_runs")
    op.drop_table("training_runs")
