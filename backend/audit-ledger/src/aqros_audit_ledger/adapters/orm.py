"""SQLAlchemy ORM for the audit ledger.

The schema is append-only by construction: there is no updated_at, and the
table grants are narrowed to INSERT/SELECT only in the migration. Hash columns
are ``NOT NULL`` so an entry can never be stored unsealed.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class AuditEntryModel(Base):
    """One immutable, hash-sealed audit record."""

    __tablename__ = "audit_entries"

    # ULID-ish sortable id: the primary key doubles as the append ordering.
    entry_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource: Mapped[str] = mapped_column(String(256), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # Monotonic sequence, so chain order is recoverable even if ids are
    # reordered or a range query needs a stable window.
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, autoincrement=True)

    __table_args__ = (
        Index("ix_audit_entries_recorded_at", "recorded_at"),
        Index("ix_audit_entries_actor_id", "actor_id"),
        Index("ix_audit_entries_event_type", "event_type"),
        Index("ix_audit_entries_correlation_id", "correlation_id"),
    )
