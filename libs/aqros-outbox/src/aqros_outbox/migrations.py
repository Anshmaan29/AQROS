"""Alembic migration script template for adding the outbox_events table.

Copy this file into a service's ``migrations/versions/`` directory and
register it in its Alembic revision chain. This creates the ``outbox_events``
table with proper indexes for the dispatcher's polling query.

Replace:
    - ``revision`` with the next revision ID
    - ``down_revision`` with the service's current head revision
    - ``date`` with the current date
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_outbox"
down_revision: str | None = "0001"
branch_labels: Sequence[str] | str | None = None
depends_on: Sequence[str] | str | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column("event_id", sa.String(26), primary_key=True),
        sa.Column("topic", sa.String(128), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column("content_type", sa.String(64), nullable=False, server_default="application/json"),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("knowledge_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("producer", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("correlation_id", sa.String(26), nullable=False),
        sa.Column("causation_id", sa.String(26), nullable=True),
        sa.Column("headers", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_outbox_status_created", "outbox_events", ["status", "created_at"])
    op.create_index("ix_outbox_next_retry", "outbox_events", ["next_retry_at"])
    op.create_index("ix_outbox_topic", "outbox_events", ["topic"])
    op.create_index("ix_outbox_correlation", "outbox_events", ["correlation_id"])


def downgrade() -> None:
    op.drop_index("ix_outbox_correlation", table_name="outbox_events")
    op.drop_index("ix_outbox_topic", table_name="outbox_events")
    op.drop_index("ix_outbox_next_retry", table_name="outbox_events")
    op.drop_index("ix_outbox_status_created", table_name="outbox_events")
    op.drop_table("outbox_events")
