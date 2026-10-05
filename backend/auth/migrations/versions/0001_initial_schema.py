"""Create the initial auth schema: users and four-eyes approval requests.

Revision ID: 0001
Revises:
Create Date: 2026-10-05
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("principal_id", sa.String(64), nullable=False),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        # PBKDF2 hash only; never reversible and never logged.
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column(
            "roles",
            postgresql.ARRAY(sa.String(32)),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("is_service", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("principal_id"),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_index("ix_users_username", "users", ["username"])

    op.create_table(
        "approval_requests",
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource", sa.String(256), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("requester_id", sa.String(64), nullable=False),
        sa.Column("approver_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("history", sa.Text(), nullable=False, server_default=sa.text("'[]'")),
        sa.PrimaryKeyConstraint("request_id"),
    )
    op.create_index("ix_approval_requests_action", "approval_requests", ["action"])
    op.create_index("ix_approval_requests_requester_id", "approval_requests", ["requester_id"])
    op.create_index("ix_approval_requests_status", "approval_requests", ["status"])


def downgrade() -> None:
    op.drop_index("ix_approval_requests_status", table_name="approval_requests")
    op.drop_index("ix_approval_requests_requester_id", table_name="approval_requests")
    op.drop_index("ix_approval_requests_action", table_name="approval_requests")
    op.drop_table("approval_requests")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
