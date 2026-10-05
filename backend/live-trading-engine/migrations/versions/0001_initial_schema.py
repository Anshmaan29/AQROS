from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "live_orders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.String(64), nullable=False),
        sa.Column("client_order_id", sa.String(128), nullable=False),
        sa.Column("broker_order_id", sa.String(128), nullable=True),
        sa.Column("portfolio_id", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("order_type", sa.String(16), nullable=False),
        sa.Column("quantity", sa.String(64), nullable=False),
        sa.Column("price", sa.String(64), nullable=True),
        sa.Column("stop_price", sa.String(64), nullable=True),
        sa.Column("time_in_force", sa.String(16), nullable=False, server_default="day"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("route_status", sa.String(16), nullable=False, server_default="pending_route"),
        sa.Column("filled_quantity", sa.String(64), nullable=False, server_default="0"),
        sa.Column("remaining_quantity", sa.String(64), nullable=False, server_default="0"),
        sa.Column("avg_fill_price", sa.String(64), nullable=True),
        sa.Column("last_fill_price", sa.String(64), nullable=True),
        sa.Column("total_commission", sa.String(64), nullable=False, server_default="0"),
        sa.Column("broker_latency_ms", sa.Float(), nullable=True),
        sa.Column("reject_reason", sa.String(64), nullable=True),
        sa.Column("reject_message", sa.Text(), nullable=True),
        sa.Column("strategy", sa.String(128), nullable=False, server_default="unknown"),
        sa.Column("correlation_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("routed_to", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_order_id", name="uq_live_orders_client_order_id"),
    )
    op.create_index("ix_live_orders_order_id", "live_orders", ["order_id"], unique=True)
    op.create_index("ix_live_orders_client_order_id", "live_orders", ["client_order_id"])
    op.create_index("ix_live_orders_broker_order_id", "live_orders", ["broker_order_id"])
    op.create_index("ix_live_orders_portfolio_id", "live_orders", ["portfolio_id"])
    op.create_index("ix_live_orders_symbol", "live_orders", ["symbol"])
    op.create_index("ix_live_orders_status", "live_orders", ["status"])

    op.create_table(
        "broker_connections",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("broker_name", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="disconnected"),
        sa.Column("last_connected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_failures", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_disconnections", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_reconnections", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("kill_switch_status", sa.String(32), nullable=False, server_default="armed"),
        sa.Column("kill_switch_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("kill_switch_triggered_by", sa.String(64), nullable=False, server_default=""),
        sa.Column("kill_switch_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("broker_name"),
    )

    op.create_table(
        "position_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("portfolio_id", sa.String(64), nullable=False),
        sa.Column("symbol", sa.String(32), nullable=False),
        sa.Column("quantity", sa.String(64), nullable=False),
        sa.Column("market_value", sa.String(64), nullable=False),
        sa.Column("cost_basis", sa.String(64), nullable=False),
        sa.Column("avg_entry_price", sa.String(64), nullable=True),
        sa.Column("unrealized_pl", sa.String(64), nullable=False, server_default="0"),
        sa.Column("realized_pl", sa.String(64), nullable=False, server_default="0"),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_position_snapshots_portfolio_id", "position_snapshots", ["portfolio_id"])
    op.create_index("ix_position_snapshots_symbol", "position_snapshots", ["symbol"])


def downgrade() -> None:
    op.drop_table("position_snapshots")
    op.drop_table("broker_connections")
    op.drop_table("live_orders")
