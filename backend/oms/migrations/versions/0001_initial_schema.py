from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("client_order_id", sa.String(length=128), nullable=False),
        sa.Column("portfolio_id", sa.String(length=64), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("order_type", sa.String(length=16), nullable=False),
        sa.Column("quantity", sa.String(length=64), nullable=False),
        sa.Column("price", sa.String(length=64), nullable=True),
        sa.Column("stop_price", sa.String(length=64), nullable=True),
        sa.Column("time_in_force", sa.String(length=16), nullable=False, server_default="day"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("filled_quantity", sa.String(length=64), nullable=False, server_default="0"),
        sa.Column("filled_value", sa.String(length=64), nullable=False, server_default="0"),
        sa.Column("total_commission", sa.String(length=64), nullable=False, server_default="0"),
        sa.Column("avg_fill_price", sa.String(length=64), nullable=True),
        sa.Column("last_fill_price", sa.String(length=64), nullable=True),
        sa.Column("reject_reason", sa.String(length=64), nullable=True),
        sa.Column("reject_message", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("strategy", sa.String(length=128), nullable=False, server_default="unknown"),
        sa.Column("correlation_id", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_order_id", name="uq_orders_client_order_id"),
    )
    op.create_index("ix_orders_order_id", "orders", ["order_id"], unique=True)
    op.create_index("ix_orders_client_order_id", "orders", ["client_order_id"])
    op.create_index("ix_orders_portfolio_id", "orders", ["portfolio_id"])
    op.create_index("ix_orders_symbol", "orders", ["symbol"])

    op.create_table(
        "fills",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trade_id", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.String(length=64), nullable=False),
        sa.Column("price", sa.String(length=64), nullable=False),
        sa.Column("commission", sa.String(length=64), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_id", name="uq_fills_trade_id"),
    )
    op.create_index("ix_fills_trade_id", "fills", ["trade_id"])
    op.create_index("ix_fills_order_id", "fills", ["order_id"])


def downgrade() -> None:
    op.drop_table("fills")
    op.drop_table("orders")
