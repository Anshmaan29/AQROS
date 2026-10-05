"""Create initial risk-engine schema: decisions, limits, kill_switch, snapshots.

Revision ID: 0001
Revises:
Create Date: 2026-07-31
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "risk_decisions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("signal_id", sa.String(128), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("strategy", sa.String(128), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("risk_level", sa.String(16), nullable=False),
        sa.Column("reasons_json", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("requested_quantity", sa.String(64), nullable=False),
        sa.Column("approved_quantity", sa.String(64), nullable=False),
        sa.Column("stop_loss", sa.String(64), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("portfolio_equity_at_check", sa.String(64), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("signal_id", name="uq_risk_decisions_signal_id"),
    )
    op.create_index("ix_risk_decisions_signal_id", "risk_decisions", ["signal_id"])
    op.create_index("ix_risk_decisions_created_at", "risk_decisions", ["created_at"])

    op.create_table(
        "risk_limits",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("scope", sa.String(32), nullable=False, server_default="global"),
        sa.Column("scope_ref", sa.String(128), nullable=True),
        sa.Column("limit_type", sa.String(64), nullable=False),
        sa.Column("limit_value", sa.Float(), nullable=False),
        sa.Column("is_kernel", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("approved_by", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_risk_limits_scope", "risk_limits", ["scope"])

    op.create_table(
        "kill_switch_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("scope_ref", sa.String(128), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("armed_by", sa.String(128), nullable=False),
        sa.Column("armed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resumed_by", sa.String(128), nullable=True),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kill_switch_armed_at", "kill_switch_events", ["armed_at"])

    op.create_table(
        "exposure_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("account_id", sa.String(64), nullable=False),
        sa.Column("total_equity", sa.String(64), nullable=False),
        sa.Column("gross_exposure", sa.String(64), nullable=False),
        sa.Column("net_exposure", sa.String(64), nullable=False),
        sa.Column("cash", sa.String(64), nullable=False),
        sa.Column("leverage", sa.Float(), nullable=False),
        sa.Column("position_count", sa.Integer(), nullable=False),
        sa.Column("var_95", sa.Float(), nullable=False),
        sa.Column("var_99", sa.Float(), nullable=False),
        sa.Column("daily_pnl", sa.String(64), nullable=False),
        sa.Column("max_drawdown_pct", sa.Float(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_exposure_snapshots_account_as_of", "exposure_snapshots", ["account_id", "as_of"]
    )


def downgrade() -> None:
    op.drop_index("ix_exposure_snapshots_account_as_of", table_name="exposure_snapshots")
    op.drop_table("exposure_snapshots")
    op.drop_index("ix_kill_switch_armed_at", table_name="kill_switch_events")
    op.drop_table("kill_switch_events")
    op.drop_index("ix_risk_limits_scope", table_name="risk_limits")
    op.drop_table("risk_limits")
    op.drop_index("ix_risk_decisions_created_at", table_name="risk_decisions")
    op.drop_index("ix_risk_decisions_signal_id", table_name="risk_decisions")
    op.drop_table("risk_decisions")
