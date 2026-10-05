from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aqros_outbox import (
    DirectOutboxRepository,
    InMemoryOutboxRepository,
    OutboxEvent,
    OutboxStatus,
    SqlAlchemyOutboxRepository,
)
from aqros_outbox.models import Base, OutboxModel

pytestmark = pytest.mark.asyncio


class TestInMemoryOutboxRepository:
    @pytest.fixture
    def repo(self) -> InMemoryOutboxRepository:
        return InMemoryOutboxRepository()

    async def test_add_and_statistics(self, repo: InMemoryOutboxRepository) -> None:
        event = OutboxEvent(event_id="E1", topic="t", payload=b"{}")
        await repo.add(event)
        stats = await repo.statistics()
        assert stats.pending == 1
        assert stats.total == 1

    async def test_claim_ready_returns_pending_events(self, repo: InMemoryOutboxRepository) -> None:
        await repo.add(OutboxEvent(event_id="E1", topic="t", payload=b"{}"))
        await repo.add(OutboxEvent(event_id="E2", topic="t", payload=b"{}"))
        claimed = await repo.claim_ready(batch_size=10, claim_timeout_seconds=30)
        assert len(claimed) == 2
        assert claimed[0].status == OutboxStatus.PROCESSING

    async def test_claim_respects_batch_size(self, repo: InMemoryOutboxRepository) -> None:
        for i in range(10):
            await repo.add(OutboxEvent(event_id=f"E{i}", topic="t", payload=b"{}"))
        claimed = await repo.claim_ready(batch_size=3, claim_timeout_seconds=30)
        assert len(claimed) == 3

    async def test_claim_reclaims_stale_processing_event(
        self, repo: InMemoryOutboxRepository
    ) -> None:
        """The in-memory repo must mirror the SQL repo's stale-claim reclaim.

        This repo backs unit tests and dry-run wiring, so it has to reproduce
        the SQL behaviour exactly — otherwise a test would pass against the
        fake while production silently strands the event.
        """
        await repo.add(OutboxEvent(event_id="E_STALE", topic="t", payload=b"{}"))
        assert len(await repo.claim_ready(batch_size=10, claim_timeout_seconds=30)) == 1

        # Within the timeout: not handed out twice.
        assert await repo.claim_ready(batch_size=10, claim_timeout_seconds=30) == []

        # Age the claim past the timeout, as a dead dispatcher would leave it.
        event = repo._events["E_STALE"]
        event.next_retry_at = datetime.now(UTC) - timedelta(seconds=120)
        reclaimed = await repo.claim_ready(batch_size=10, claim_timeout_seconds=30)
        assert [e.event_id for e in reclaimed] == ["E_STALE"]

    async def test_mark_delivered(self, repo: InMemoryOutboxRepository) -> None:
        await repo.add(OutboxEvent(event_id="E1", topic="t", payload=b"{}"))
        await repo.claim_ready(batch_size=10, claim_timeout_seconds=30)
        await repo.mark_delivered("E1")
        stats = await repo.statistics()
        assert stats.delivered == 1
        assert stats.pending == 0

    async def test_mark_failed_with_retry(self, repo: InMemoryOutboxRepository) -> None:
        event = OutboxEvent(event_id="E1", topic="t", payload=b"{}", max_retries=3)
        await repo.add(event)
        await repo.claim_ready(batch_size=10, claim_timeout_seconds=30)
        await repo.mark_failed("E1", "timeout")
        stats = await repo.statistics()
        assert stats.failed == 1
        assert stats.retry_sum == 1

        event2 = repo._events["E1"]
        assert event2.next_retry_at is not None
        assert event2.next_retry_at > datetime.now(UTC)

    async def test_mark_failed_goes_to_dead_letter(self, repo: InMemoryOutboxRepository) -> None:
        event = OutboxEvent(event_id="E1", topic="t", payload=b"{}", max_retries=1)
        await repo.add(event)
        await repo.claim_ready(batch_size=10, claim_timeout_seconds=30)
        await repo.mark_failed("E1", "fatal")
        stats = await repo.statistics()
        assert stats.dead_letter == 1

    async def test_mark_dead_letter_direct(self, repo: InMemoryOutboxRepository) -> None:
        await repo.add(OutboxEvent(event_id="E1", topic="t", payload=b"{}"))
        await repo.mark_dead_letter("E1", "manual")
        stats = await repo.statistics()
        assert stats.dead_letter == 1

    async def test_delete_processed(self, repo: InMemoryOutboxRepository) -> None:
        now = datetime.now(UTC)
        old = OutboxEvent(
            event_id="OLD",
            topic="t",
            payload=b"{}",
            status=OutboxStatus.DELIVERED,
            delivered_at=now - timedelta(hours=100),
        )
        recent = OutboxEvent(
            event_id="NEW",
            topic="t",
            payload=b"{}",
            status=OutboxStatus.DELIVERED,
            delivered_at=now - timedelta(hours=1),
        )
        await repo.add(old)
        await repo.add(recent)
        repo._events["OLD"].status = OutboxStatus.DELIVERED
        repo._events["NEW"].status = OutboxStatus.DELIVERED

        deleted = await repo.delete_processed(now - timedelta(hours=48))
        assert deleted == 1
        assert "OLD" not in repo._events
        assert "NEW" in repo._events

    async def test_reprocess_dead_letters(self, repo: InMemoryOutboxRepository) -> None:
        await repo.add(OutboxEvent(event_id="DL1", topic="t", payload=b"{}"))
        await repo.add(OutboxEvent(event_id="DL2", topic="t", payload=b"{}"))
        await repo.mark_dead_letter("DL1", "err1")
        await repo.mark_dead_letter("DL2", "err2")
        reprocessed = await repo.reprocess_dead_letters(10)
        assert reprocessed == 2
        stats = await repo.statistics()
        assert stats.pending == 2
        assert stats.dead_letter == 0

    async def test_claim_respects_status_filter(self, repo: InMemoryOutboxRepository) -> None:
        event = OutboxEvent(event_id="E1", topic="t", payload=b"{}")
        await repo.add(event)
        await repo.claim_ready(batch_size=10, claim_timeout_seconds=30)
        claimed2 = await repo.claim_ready(batch_size=10, claim_timeout_seconds=30)
        assert len(claimed2) == 0


class TestSqlAlchemyOutboxRepository:
    @pytest.fixture
    async def repo(self) -> SqlAlchemyOutboxRepository:
        from sqlalchemy.ext.asyncio import (
            async_sessionmaker,
            create_async_engine,
        )

        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        return SqlAlchemyOutboxRepository(factory)

    async def test_add_and_claim(self, repo: SqlAlchemyOutboxRepository) -> None:
        event = OutboxEvent(event_id="SA1", topic="test", payload=b"{}", producer="svc")
        await repo.add(event)
        claimed = await repo.claim_ready(10, 30)
        assert len(claimed) == 1
        assert claimed[0].event_id == "SA1"

    async def test_deliver_lifecycle(self, repo: SqlAlchemyOutboxRepository) -> None:
        event = OutboxEvent(event_id="SA2", topic="test", payload=b"{}", producer="svc")
        await repo.add(event)
        await repo.claim_ready(10, 30)
        await repo.mark_delivered("SA2")
        stats = await repo.statistics()
        assert stats.delivered == 1

    async def test_retry_then_dead_letter(self, repo: SqlAlchemyOutboxRepository) -> None:
        event = OutboxEvent(
            event_id="SA3", topic="test", payload=b"{}", max_retries=2, producer="svc"
        )
        await repo.add(event)
        await repo.claim_ready(10, 30)
        await repo.mark_failed("SA3", "err1")
        stats = await repo.statistics()
        assert stats.failed == 1

        await repo.mark_failed("SA3", "err2")
        stats = await repo.statistics()
        assert stats.dead_letter == 1

    async def test_statistics_counts(self, repo: SqlAlchemyOutboxRepository) -> None:
        for i in range(5):
            e = OutboxEvent(event_id=f"SA{i}", topic="t", payload=b"{}", producer="svc")
            await repo.add(e)
        claimed = await repo.claim_ready(2, 30)
        assert len(claimed) == 2
        await repo.mark_delivered(claimed[0].event_id)
        stats = await repo.statistics()
        assert stats.pending == 3
        assert stats.processing == 1
        assert stats.delivered == 1
        assert stats.total == 5

    async def test_delete_processed(self, repo: SqlAlchemyOutboxRepository) -> None:
        now = datetime.now(UTC)
        for i in range(3):
            e = OutboxEvent(event_id=f"SA_D{i}", topic="t", payload=b"{}", producer="svc")
            await repo.add(e)
            await repo.claim_ready(10, 30)
            await repo.mark_delivered(e.event_id)

        from sqlalchemy import select

        from aqros_outbox.models import OutboxModel

        async with repo._session_factory() as session:
            rows = (await session.execute(select(OutboxModel))).scalars().all()
            for row in rows:
                row.delivered_at = now - timedelta(hours=100)
            await session.commit()

        deleted = await repo.delete_processed(now - timedelta(hours=48))
        assert deleted == 3

    async def test_reprocess_dead_letters(self, repo: SqlAlchemyOutboxRepository) -> None:
        e = OutboxEvent(event_id="SA_DL1", topic="t", payload=b"{}", max_retries=1, producer="svc")
        await repo.add(e)
        await repo.claim_ready(10, 30)
        await repo.mark_failed("SA_DL1", "fatal")
        stats = await repo.statistics()
        assert stats.dead_letter == 1

        reprocessed = await repo.reprocess_dead_letters(10)
        assert reprocessed == 1
        stats = await repo.statistics()
        assert stats.pending == 1
        assert stats.dead_letter == 0

    async def test_claim_reclaims_stale_processing_event(
        self, repo: SqlAlchemyOutboxRepository
    ) -> None:
        """A dispatcher that dies mid-dispatch must not strand the event.

        Regression test: ``claim_ready`` used to only match PENDING/FAILED and
        ignored ``claim_timeout_seconds``, so a row left in PROCESSING by a
        crashed dispatcher was never retried — a silent event loss. On the
        order path that means a lost ``orders.filled``.
        """
        event = OutboxEvent(event_id="SA_STALE", topic="t", payload=b"{}", producer="svc")
        await repo.add(event)
        # First claim succeeds; the dispatcher then "crashes" before marking it delivered.
        first = await repo.claim_ready(10, claim_timeout_seconds=30)
        assert len(first) == 1

        # Still within the timeout: must NOT be handed out a second time
        # (a live dispatcher could still be working on it).
        assert await repo.claim_ready(10, claim_timeout_seconds=30) == []

        # Simulate the claim aging past the timeout.
        stale = datetime.now(UTC) - timedelta(seconds=120)
        async with repo._session_factory() as session:
            row = await session.get(OutboxModel, "SA_STALE")
            assert row is not None
            row.next_retry_at = stale
            await session.commit()

        reclaimed = await repo.claim_ready(10, claim_timeout_seconds=30)
        assert [e.event_id for e in reclaimed] == ["SA_STALE"]

    async def test_stage_is_atomic_with_caller_transaction(
        self, repo: SqlAlchemyOutboxRepository
    ) -> None:
        """``stage`` must not commit on its own — that is the whole point.

        Regression test: ``add`` opens and commits its own session, so it cannot
        be atomic with a business state change. ``stage`` enlists the caller's
        session so the event and the state land in the same commit.
        """
        async with repo._session_factory() as session:
            event = OutboxEvent(event_id="SA_STAGE", topic="t", payload=b"{}", producer="svc")
            repo.stage(session, event)
            await session.rollback()

        # The rollback discarded the event: nothing was committed behind the
        # caller's back, which is what makes the outbox transactional.
        stats = await repo.statistics()
        assert stats.total == 0

        # A real commit does persist it.
        async with repo._session_factory() as session:
            repo.stage(session, OutboxEvent(event_id="SA_STAGE", topic="t", payload=b"{}"))
            await session.commit()
        stats = await repo.statistics()
        assert stats.pending == 1


class TestDirectOutboxRepository:
    @pytest.fixture
    def repo(self, event_bus: object) -> DirectOutboxRepository:
        from aqros_outbox import DirectOutboxRepository

        return DirectOutboxRepository(event_bus)

    async def test_add_publishes_directly(
        self,
        repo: DirectOutboxRepository,
        collected_events: list[object],
    ) -> None:
        event = OutboxEvent(event_id="DIR1", topic="test.events", payload=b'{"k": "v"}')
        result = await repo.add(event)
        assert result.status == OutboxStatus.DELIVERED
        assert len(collected_events) == 1
        assert collected_events[0].event_id == "DIR1"

    async def test_claim_ready_returns_empty(self, repo: DirectOutboxRepository) -> None:
        claimed = await repo.claim_ready(10, 30)
        assert claimed == []

    async def test_statistics(self, repo: DirectOutboxRepository) -> None:
        await repo.add(OutboxEvent(event_id="DIR2", topic="test.events", payload=b"{}"))
        stats = await repo.statistics()
        assert stats.delivered == 1
