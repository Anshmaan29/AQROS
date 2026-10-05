"""Create initial portfolio-engine schema: portfolios, positions, pnl_records, equity_curve.

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
        "portfolios",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("portfolio_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("cash_total", sa.String(64), nullable=False),
        sa.Column("cash_reserved", sa.String(64), nullable=False),
        sa.Column("total_fees", sa.String(64), nullable=False),
        sa.Column("total_pnl_realized", sa.String(64), nullable=False),
        sa.Column("daily_pnl", sa.String(64), nullable=False),
        sa.Column("peak_equity", sa.String(64), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("portfolio_id", name="uq_portfolios_portfolio_id"),
    )
    op.create_index("ix_portfolios_portfolio_id", "portfolios", ["portfolio_id"])

    op.create_table(
        "positions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("position_id", sa.String(64), nullable=False),
        sa.Column("portfolio_id", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("quantity", sa.String(64), nullable=False),
        sa.Column("avg_entry_price", sa.String(64), nullable=False),
        sa.Column("current_price", sa.String(64), nullable=False),
        sa.Column("stop_loss", sa.String(64), nullable=True),
        sa.Column("take_profit", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("sector", sa.String(64), nullable=True),
        sa.Column("beta", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("volatility", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("avg_daily_volume", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("realized_pnl", sa.String(64), nullable=False),
        sa.Column("fees_paid", sa.String(64), nullable=False),
        sa.Column("correlation_id", sa.String(128), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("portfolio_id", "symbol", "status", name="uq_portfolio_symbol_status"),
    )
    op.create_index("ix_positions_position_id", "positions", ["position_id"])
    op.create_index("ix_positions_portfolio_id", "positions", ["portfolio_id"])

    op.create_table(
        "pnl_records",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("portfolio_id", sa.String(64), nullable=False),
        sa.Column("total_equity", sa.String(64), nullable=False),
        sa.Column("unrealized_pnl", sa.String(64), nullable=False),
        sa.Column("realized_pnl", sa.String(64), nullable=False),
        sa.Column("daily_pnl", sa.String(64), nullable=False),
        sa.Column("daily_return_pct", sa.Float(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pnl_records_portfolio_id", "pnl_records", ["portfolio_id"])

    op.create_table(
        "equity_curve_points",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("portfolio_id", sa.String(64), nullable=False),
        sa.Column("equity", sa.String(64), nullable=False),
        sa.Column("cash", sa.String(64), nullable=False),
        sa.Column("market_value", sa.String(64), nullable=False),
        sa.Column("daily_pnl", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_equity_curve_points_portfolio_id", "equity_curve_points", ["portfolio_id"])


def downgrade() -> None:
    op.drop_table("equity_curve_points")
    op.drop_table("pnl_records")
    op.drop_table("positions")
    op.drop_table("portfolios")
