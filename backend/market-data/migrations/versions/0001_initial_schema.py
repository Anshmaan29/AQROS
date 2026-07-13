"""initial schema: instruments and ohlcv_bars

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
        "instruments",
        sa.Column("symbol", sa.String(length=32), primary_key=True),
        sa.Column("name", sa.String(length=256), nullable=True),
        sa.Column("exchange", sa.String(length=64), nullable=True),
        sa.Column("currency", sa.String(length=8), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "ohlcv_bars",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("interval", sa.String(length=8), nullable=False),
        sa.Column("open", sa.Numeric(18, 6), nullable=False),
        sa.Column("high", sa.Numeric(18, 6), nullable=False),
        sa.Column("low", sa.Numeric(18, 6), nullable=False),
        sa.Column("close", sa.Numeric(18, 6), nullable=False),
        sa.Column("adjusted_close", sa.Numeric(18, 6), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("knowledge_time", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "symbol", "event_time", "interval", "source", name="uq_ohlcv_bars_identity"
        ),
    )
    op.create_index("ix_ohlcv_bars_symbol", "ohlcv_bars", ["symbol"])
    op.create_index("ix_ohlcv_bars_event_time", "ohlcv_bars", ["event_time"])


def downgrade() -> None:
    op.drop_index("ix_ohlcv_bars_event_time", table_name="ohlcv_bars")
    op.drop_index("ix_ohlcv_bars_symbol", table_name="ohlcv_bars")
    op.drop_table("ohlcv_bars")
    op.drop_table("instruments")
