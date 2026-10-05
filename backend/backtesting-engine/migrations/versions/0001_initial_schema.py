"""Create the initial backtesting-engine schema.

Hand-written to match ``adapters/orm.py`` exactly (run_uuid, strategy_id,
model_name, status), including the unique constraint on run_uuid that makes a
result write-once per run. Every FK targets ``backtest_runs.run_uuid``, so that
table is created first and dropped last.

Revision ID: 0001
Revises:
Create Date: 2026-10-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_uuid", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("strategy_id", sa.String(length=128), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_uuid", name="uq_backtest_runs_run_uuid"),
    )
    op.create_index("ix_backtest_runs_model_name", "backtest_runs", ["model_name"])
    op.create_index("ix_backtest_runs_run_uuid", "backtest_runs", ["run_uuid"])
    op.create_index("ix_backtest_runs_status", "backtest_runs", ["status"])
    op.create_index("ix_backtest_runs_strategy_id", "backtest_runs", ["strategy_id"])

    op.create_table(
        "trade_log_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "run_uuid",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("backtest_runs.run_uuid"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("client_order_id", sa.String(length=128), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=8), nullable=False),
        sa.Column("order_type", sa.String(length=16), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("price", sa.Numeric(38, 18), nullable=True),
        sa.Column("commission", sa.Numeric(38, 18), nullable=False),
        sa.Column("clock_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trade_log_entries_run_uuid", "trade_log_entries", ["run_uuid"])

    op.create_table(
        "equity_points",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "run_uuid",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("backtest_runs.run_uuid"),
            nullable=False,
        ),
        sa.Column("clock_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_value", sa.Numeric(38, 18), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_equity_points_run_uuid", "equity_points", ["run_uuid"])

    op.create_table(
        "backtest_results",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "run_uuid",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("backtest_runs.run_uuid"),
            nullable=False,
        ),
        sa.Column("performance_json", sa.JSON(), nullable=False),
        sa.Column("risk_json", sa.JSON(), nullable=False),
        sa.Column("drawdown_json", sa.JSON(), nullable=False),
        sa.Column("benchmark_json", sa.JSON(), nullable=True),
        sa.Column("final_portfolio_json", sa.JSON(), nullable=False),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_uuid", name="uq_backtest_results_run_uuid"),
    )
    op.create_index("ix_backtest_results_run_uuid", "backtest_results", ["run_uuid"])


def downgrade() -> None:
    op.drop_index("ix_backtest_results_run_uuid", table_name="backtest_results")
    op.drop_table("backtest_results")
    op.drop_index("ix_equity_points_run_uuid", table_name="equity_points")
    op.drop_table("equity_points")
    op.drop_index("ix_trade_log_entries_run_uuid", table_name="trade_log_entries")
    op.drop_table("trade_log_entries")
    op.drop_index("ix_backtest_runs_strategy_id", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_status", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_run_uuid", table_name="backtest_runs")
    op.drop_index("ix_backtest_runs_model_name", table_name="backtest_runs")
    op.drop_table("backtest_runs")
