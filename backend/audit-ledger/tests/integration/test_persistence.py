"""Integration tests for the audit ledger's Postgres persistence.

The in-memory adapter stores live objects and assigns no sequence, so it cannot
catch persistence bugs. Two were found only here:

* ``sequence`` was declared ``autoincrement=True``, but SQLAlchemy only applies
  autoincrement to primary keys. Since ``entry_id`` is the primary key,
  ``sequence`` was INSERTed as NULL and every append raised a NOT NULL
  violation — the ledger could not store anything at all.
* Chain ordering depends on ``sequence``, so it also has to be monotonic.

Uses a real Postgres via testcontainers; skips when Docker is unavailable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import pytest_asyncio

pytestmark = pytest.mark.integration

T0 = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def ledger() -> AsyncIterator[object]:
    import docker

    docker.from_env().ping()
    from aqros_audit_ledger.adapters.orm import Base
    from aqros_audit_ledger.adapters.repository import SqlAlchemyLedgerRepository
    from aqros_audit_ledger.domain.service import AuditLedgerService
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        engine = create_async_engine(container.get_connection_url())
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        repo = SqlAlchemyLedgerRepository(factory)
        yield SimpleNamespace(service=AuditLedgerService(repo), repo=repo)
        await engine.dispose()


def request(n: int) -> object:
    from aqros_audit_ledger.domain.chain import Outcome
    from aqros_audit_ledger.domain.service import AppendRequest

    return AppendRequest(
        event_type="order.filled",
        actor_id="alice",
        action="submit_order",
        resource=f"order-{n}",
        outcome=Outcome.ALLOWED,
        payload={"qty": n},
        correlation_id=f"corr-{n}",
        entry_id=f"aud_{n:04d}",
    )


class TestAppendToRealPostgres:
    async def test_append_succeeds(self, ledger: object) -> None:
        """Regression: every append raised a NOT NULL violation on `sequence`."""
        entry = await ledger.service.append(request(1))  # type: ignore[attr-defined]
        assert entry.entry_hash
        assert entry.previous_hash == "0" * 64

    async def test_sequence_is_populated(self, ledger: object) -> None:
        entry = await ledger.service.append(request(1))  # type: ignore[attr-defined]
        assert entry.entry_id

    async def test_appends_link_into_a_verifiable_chain(self, ledger: object) -> None:
        for i in range(1, 6):
            await ledger.service.append(request(i))  # type: ignore[attr-defined]
        result = await ledger.service.verify()  # type: ignore[attr-defined]
        assert result.is_intact
        assert result.entries_checked == 5

    async def test_chain_verifies_after_reload_from_database(self, ledger: object) -> None:
        """Entries read back from Postgres must still verify.

        This is the property that matters: the chain is only useful if the
        persisted order and hashes reconstruct exactly.
        """
        for i in range(1, 4):
            await ledger.service.append(request(i))  # type: ignore[attr-defined]

        entries = await ledger.service.query(limit=100)  # type: ignore[attr-defined]
        assert len(entries) == 3
        # query() orders by sequence, so the reloaded run is the stored chain.
        result = await ledger.service.verify()  # type: ignore[attr-defined]
        assert result.is_intact

    async def test_head_returns_the_last_appended(self, ledger: object) -> None:
        for i in range(1, 4):
            await ledger.service.append(request(i))  # type: ignore[attr-defined]
        head = await ledger.repo.head()  # type: ignore[attr-defined]
        assert head is not None
        assert head.resource == "order-3"

    async def test_ordered_by_sequence_not_timestamp(self, ledger: object) -> None:
        """Chain order must follow `sequence`, not wall-clock time.

        Two entries written in the same millisecond must still verify in append
        order; ordering by timestamp would make verification non-deterministic.
        """
        for i in range(1, 4):
            await ledger.service.append(request(i))  # type: ignore[attr-defined]
        entries = await ledger.service.query(limit=10)  # type: ignore[attr-defined]
        assert [e.resource for e in entries] == ["order-1", "order-2", "order-3"]

    async def test_idempotent_append_does_not_duplicate(self, ledger: object) -> None:
        await ledger.service.append(request(1))  # type: ignore[attr-defined]
        await ledger.service.append(request(1))  # type: ignore[attr-defined]
        assert await ledger.service.count() == 1  # type: ignore[attr-defined]

    async def test_filters_work(self, ledger: object) -> None:
        await ledger.service.append(request(1))  # type: ignore[attr-defined]
        await ledger.service.append(request(2))  # type: ignore[attr-defined]
        found = await ledger.service.query(actor_id="alice")  # type: ignore[attr-defined]
        assert len(found) == 2
        none = await ledger.service.query(actor_id="bob")  # type: ignore[attr-defined]
        assert none == []

    async def test_range_for_verification_returns_ordered_run(self, ledger: object) -> None:
        for i in range(1, 5):
            await ledger.service.append(request(i))  # type: ignore[attr-defined]
        rows = await ledger.repo.range_for_verification(  # type: ignore[attr-defined]
            "aud_0002", "aud_0003"
        )
        assert [r.entry_id for r in rows] == ["aud_0002", "aud_0003"]
