"""Create outbox_events table for transactional outbox pattern.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-31
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("topic", sa.String(128), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("headers", sa.Text(), nullable=True),
        sa.Column("correlation_id", sa.String(128), nullable=True),
        sa.Column("causation_id", sa.String(64), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("producer", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_id", name="uq_outbox_events_event_id"),
    )
    op.create_index("ix_outbox_events_status_created", "outbox_events", ["status", "created_at"])
    op.create_index("ix_outbox_events_next_retry", "outbox_events", ["next_retry_at"])
    op.create_index("ix_outbox_events_topic", "outbox_events", ["topic"])
    op.create_index("ix_outbox_events_correlation_id", "outbox_events", ["correlation_id"])


def downgrade() -> None:
    op.drop_index("ix_outbox_events_correlation_id", table_name="outbox_events")
    op.drop_index("ix_outbox_events_topic", table_name="outbox_events")
    op.drop_index("ix_outbox_events_next_retry", table_name="outbox_events")
    op.drop_index("ix_outbox_events_status_created", table_name="outbox_events")
    op.drop_table("outbox_events")
