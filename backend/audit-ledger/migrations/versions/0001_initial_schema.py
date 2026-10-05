"""Create the append-only audit ledger schema.

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
        "audit_entries",
        sa.Column("entry_id", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("resource", sa.String(256), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("correlation_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("previous_hash", sa.String(64), nullable=False),
        sa.Column("entry_hash", sa.String(64), nullable=False),
        # Monotonic chain ordering; independent of wall-clock time so two
        # entries written in the same millisecond still verify in order.
        sa.Column("sequence", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.PrimaryKeyConstraint("entry_id"),
    )
    op.create_index("ix_audit_entries_recorded_at", "audit_entries", ["recorded_at"])
    op.create_index("ix_audit_entries_actor_id", "audit_entries", ["actor_id"])
    op.create_index("ix_audit_entries_event_type", "audit_entries", ["event_type"])
    op.create_index("ix_audit_entries_correlation_id", "audit_entries", ["correlation_id"])

    # --- WORM enforcement at the database level -----------------------------
    # The application port already exposes no update/delete, but a compromised
    # service account or a direct psql session could still mutate history. This
    # trigger is the last line of defence: any UPDATE or DELETE on the ledger
    # raises, regardless of who issues it. (CLAUDE.md §7.8)
    op.execute("""
        CREATE OR REPLACE FUNCTION audit_entries_append_only()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'audit_entries is append-only (WORM): % is not permitted', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """)
    op.execute("""
        CREATE TRIGGER trg_audit_entries_append_only
        BEFORE UPDATE OR DELETE ON audit_entries
        FOR EACH ROW EXECUTE FUNCTION audit_entries_append_only();
        """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_entries_append_only ON audit_entries;")
    op.execute("DROP FUNCTION IF EXISTS audit_entries_append_only();")
    op.drop_index("ix_audit_entries_correlation_id", table_name="audit_entries")
    op.drop_index("ix_audit_entries_event_type", table_name="audit_entries")
    op.drop_index("ix_audit_entries_actor_id", table_name="audit_entries")
    op.drop_index("ix_audit_entries_recorded_at", table_name="audit_entries")
    op.drop_table("audit_entries")
