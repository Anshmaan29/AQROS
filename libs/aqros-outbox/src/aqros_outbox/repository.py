from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aqros_outbox.domain import OutboxEvent, OutboxRepository, OutboxStatistics, OutboxStatus
from aqros_outbox.models import OutboxModel


def _model_to_event(row: OutboxModel) -> OutboxEvent:
    return OutboxEvent(
        event_id=row.event_id,
        topic=row.topic,
        payload=row.payload,
        content_type=row.content_type,
        event_time=row.event_time,
        knowledge_time=row.knowledge_time,
        producer=row.producer,
        schema_version=row.schema_version,
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        headers=json.loads(row.headers) if row.headers else {},
        status=OutboxStatus(row.status),
        retry_count=row.retry_count,
        max_retries=row.max_retries,
        last_error=row.last_error,
        created_at=row.created_at,
        next_retry_at=row.next_retry_at,
        delivered_at=row.delivered_at,
    )


def _event_to_model(event: OutboxEvent) -> OutboxModel:
    return OutboxModel(
        event_id=event.event_id,
        topic=event.topic,
        payload=event.payload,
        content_type=event.content_type,
        event_time=event.event_time or datetime.now(UTC),
        knowledge_time=event.knowledge_time or datetime.now(UTC),
        producer=event.producer,
        schema_version=event.schema_version,
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
        headers=json.dumps(event.headers, separators=(",", ":")),
        status=event.status.value if isinstance(event.status, OutboxStatus) else event.status,
        retry_count=event.retry_count,
        max_retries=event.max_retries,
        last_error=event.last_error,
        created_at=event.created_at or datetime.now(UTC),
        next_retry_at=event.next_retry_at,
        delivered_at=event.delivered_at,
    )


class SqlAlchemyOutboxRepository(OutboxRepository):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def add(self, event: OutboxEvent) -> OutboxEvent:
        """Persist an event on its own transaction.

        This is only safe when the event is *not* coupled to a business state
        change. For the transactional-outbox guarantee — the event must become
        durable in the *same* commit as the state it describes — use
        :meth:`stage`, which enlists a caller-owned session instead.
        """
        now = datetime.now(UTC)
        event.created_at = event.created_at or now
        event.status = OutboxStatus.PENDING
        async with self._session_factory() as session:
            model = _event_to_model(event)
            session.add(model)
            await session.commit()
        return event

    def stage(self, session: AsyncSession, event: OutboxEvent) -> OutboxEvent:
        """Stage an event onto a caller-owned session without committing.

        The caller is responsible for committing, which makes the event and
        the business state change atomic — the defining property of the
        transactional outbox. Writes the row via ``session.add`` only; no
        I/O is performed here, so this stays safe to call from inside an
        existing transaction::

            async with session_factory() as session:
                order = Order(...)
                session.add(order)
                outbox.stage(session, OutboxEvent(...))  # same commit
                await session.commit()
        """
        now = datetime.now(UTC)
        event.created_at = event.created_at or now
        event.status = OutboxStatus.PENDING
        session.add(_event_to_model(event))
        return event

    async def claim_ready(
        self,
        batch_size: int,
        claim_timeout_seconds: int,
    ) -> list[OutboxEvent]:
        """Atomically claim a batch of deliverable events.

        Also reclaims rows stuck in ``PROCESSING`` for longer than
        ``claim_timeout_seconds``: if the dispatcher dies mid-dispatch the
        row would otherwise stay ``PROCESSING`` forever and be silently lost,
        which on the order path means a lost ``orders.filled`` event.
        """
        now = datetime.now(UTC)
        # A PROCESSING row whose next_retry_at is older than the claim timeout
        # was claimed by a dispatcher that never finished; reclaim it.
        stale_before = now - timedelta(seconds=claim_timeout_seconds)
        async with self._session_factory() as session:
            stmt = (
                select(OutboxModel)
                .where(
                    or_(
                        and_(
                            OutboxModel.status.in_(
                                [
                                    OutboxStatus.PENDING.value,
                                    OutboxStatus.FAILED.value,
                                ]
                            ),
                            or_(
                                OutboxModel.next_retry_at.is_(None),
                                OutboxModel.next_retry_at <= now,
                            ),
                        ),
                        and_(
                            OutboxModel.status == OutboxStatus.PROCESSING.value,
                            OutboxModel.next_retry_at.is_not(None),
                            OutboxModel.next_retry_at <= stale_before,
                        ),
                    ),
                )
                .order_by(OutboxModel.created_at)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            result = await session.execute(stmt)
            rows = list(result.scalars().all())

            claimed: list[OutboxEvent] = []
            for row in rows:
                row.status = OutboxStatus.PROCESSING.value
                # Record the claim time so a crashed dispatcher can be detected.
                row.next_retry_at = now.replace(tzinfo=UTC) if now.tzinfo is None else now
                claimed.append(_model_to_event(row))

            await session.commit()

        for event in claimed:
            event.status = OutboxStatus.PROCESSING
        return claimed

    async def mark_delivered(self, event_id: str) -> None:
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            row = await session.get(OutboxModel, event_id)
            if row is not None:
                row.status = OutboxStatus.DELIVERED.value
                row.delivered_at = now
                row.last_error = None
                await session.commit()

    async def mark_failed(self, event_id: str, error: str) -> None:
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            row = await session.get(OutboxModel, event_id)
            if row is not None:
                row.retry_count += 1
                row.last_error = error
                if row.retry_count >= row.max_retries:
                    row.status = OutboxStatus.DEAD_LETTER.value
                else:
                    row.status = OutboxStatus.FAILED.value
                    delay = min(60.0, 1.0 * (2.0 ** (row.retry_count - 1)))
                    import random

                    jitter = random.uniform(0, 0.5 * delay)
                    from datetime import timedelta

                    row.next_retry_at = now + timedelta(seconds=delay + jitter)
                await session.commit()

    async def mark_dead_letter(self, event_id: str, error: str) -> None:
        async with self._session_factory() as session:
            row = await session.get(OutboxModel, event_id)
            if row is not None:
                row.status = OutboxStatus.DEAD_LETTER.value
                row.last_error = error
                await session.commit()

    async def statistics(self) -> OutboxStatistics:
        async with self._session_factory() as session:
            counts = await session.execute(
                select(OutboxModel.status, func.count(OutboxModel.event_id)).group_by(
                    OutboxModel.status
                )
            )
            retry_total = await session.execute(
                select(func.coalesce(func.sum(OutboxModel.retry_count), 0))
            )
            stats_map: dict[str, int] = {}
            for status_val, count in counts:
                stats_map[str(status_val)] = count

            return OutboxStatistics(
                pending=stats_map.get(OutboxStatus.PENDING.value, 0),
                processing=stats_map.get(OutboxStatus.PROCESSING.value, 0),
                delivered=stats_map.get(OutboxStatus.DELIVERED.value, 0),
                failed=stats_map.get(OutboxStatus.FAILED.value, 0),
                dead_letter=stats_map.get(OutboxStatus.DEAD_LETTER.value, 0),
                total=sum(stats_map.values()),
                retry_sum=retry_total.scalar() or 0,
            )

    async def delete_processed(self, before: datetime) -> int:
        async with self._session_factory() as session:
            result = await session.execute(
                delete(OutboxModel).where(
                    OutboxModel.status == OutboxStatus.DELIVERED.value,
                    OutboxModel.delivered_at < before,
                )
            )
            await session.commit()
            return result.rowcount  # type: ignore[attr-defined,no-any-return]

    async def reprocess_dead_letters(self, max_events: int = 100) -> int:
        async with self._session_factory() as session:
            stmt = (
                select(OutboxModel)
                .where(OutboxModel.status == OutboxStatus.DEAD_LETTER.value)
                .limit(max_events)
                .with_for_update(skip_locked=True)
            )
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
            for row in rows:
                row.status = OutboxStatus.PENDING.value
                row.retry_count = 0
                row.next_retry_at = None
                row.last_error = None
            await session.commit()
            return len(rows)


class InMemoryOutboxRepository(OutboxRepository):
    def __init__(self, base_retry_delay_seconds: float = 0.01) -> None:
        self._events: dict[str, OutboxEvent] = {}
        self._base_retry_delay = base_retry_delay_seconds
        self._lock: Any = None

    async def add(self, event: OutboxEvent) -> OutboxEvent:
        now = datetime.now(UTC)
        event.created_at = event.created_at or now
        event.status = OutboxStatus.PENDING
        self._events[event.event_id] = event
        return event

    async def claim_ready(self, batch_size: int, claim_timeout_seconds: int) -> list[OutboxEvent]:
        now = datetime.now(UTC)
        stale_before = now - timedelta(seconds=claim_timeout_seconds)
        ready: list[OutboxEvent] = []
        for event in sorted(self._events.values(), key=lambda e: e.created_at or now):
            if len(ready) >= batch_size:
                break
            schedulable = event.status in (OutboxStatus.PENDING, OutboxStatus.FAILED) and (
                event.next_retry_at is None or event.next_retry_at <= now
            )
            # Mirror the SQL repo: a PROCESSING row claimed longer ago than the
            # timeout was orphaned by a dead dispatcher and must be reclaimed,
            # otherwise the event is silently lost.
            orphaned = (
                event.status == OutboxStatus.PROCESSING
                and event.next_retry_at is not None
                and event.next_retry_at <= stale_before
            )
            if schedulable or orphaned:
                event.status = OutboxStatus.PROCESSING
                event.next_retry_at = now
                ready.append(event)
        return ready

    async def mark_delivered(self, event_id: str) -> None:
        event = self._events.get(event_id)
        if event is not None:
            event.status = OutboxStatus.DELIVERED
            event.delivered_at = datetime.now(UTC)
            event.last_error = None

    async def mark_failed(self, event_id: str, error: str) -> None:
        event = self._events.get(event_id)
        if event is not None:
            event.retry_count += 1
            event.last_error = error
            if event.retry_count >= event.max_retries:
                event.status = OutboxStatus.DEAD_LETTER
            else:
                event.status = OutboxStatus.FAILED
                delay = min(60.0, self._base_retry_delay * (2.0 ** (event.retry_count - 1)))
                import random

                jitter = random.uniform(0, 0.5 * delay)
                from datetime import timedelta

                event.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay + jitter)

    async def mark_dead_letter(self, event_id: str, error: str) -> None:
        event = self._events.get(event_id)
        if event is not None:
            event.status = OutboxStatus.DEAD_LETTER
            event.last_error = error

    async def statistics(self) -> OutboxStatistics:
        stats = OutboxStatistics()
        for event in self._events.values():
            stats.total += 1
            if event.status == OutboxStatus.PENDING:
                stats.pending += 1
            elif event.status == OutboxStatus.PROCESSING:
                stats.processing += 1
            elif event.status == OutboxStatus.DELIVERED:
                stats.delivered += 1
            elif event.status == OutboxStatus.FAILED:
                stats.failed += 1
            elif event.status == OutboxStatus.DEAD_LETTER:
                stats.dead_letter += 1
            stats.retry_sum += event.retry_count
        return stats

    async def delete_processed(self, before: datetime) -> int:
        to_delete = [
            eid
            for eid, event in self._events.items()
            if event.status == OutboxStatus.DELIVERED
            and event.delivered_at is not None
            and event.delivered_at < before
        ]
        for eid in to_delete:
            del self._events[eid]
        return len(to_delete)

    async def reprocess_dead_letters(self, max_events: int = 100) -> int:
        count = 0
        for event in self._events.values():
            if count >= max_events:
                break
            if event.status == OutboxStatus.DEAD_LETTER:
                event.status = OutboxStatus.PENDING
                event.retry_count = 0
                event.next_retry_at = None
                event.last_error = None
                count += 1
        return count


class DirectOutboxRepository(OutboxRepository):
    def __init__(self, event_bus: Any) -> None:
        self._event_bus = event_bus
        self._delivered: list[str] = []

    async def add(self, event: OutboxEvent) -> OutboxEvent:
        from aqros_events import EventEnvelope

        now = datetime.now(UTC)
        envelope = EventEnvelope(
            event_id=event.event_id,
            topic=event.topic,
            payload=event.payload,
            content_type=event.content_type,
            event_time=event.event_time or now,
            knowledge_time=event.knowledge_time or now,
            producer=event.producer,
            schema_version=event.schema_version,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
        )
        await self._event_bus.publish(envelope)
        event.status = OutboxStatus.DELIVERED
        event.delivered_at = now
        event.created_at = now
        self._delivered.append(event.event_id)
        return event

    async def claim_ready(self, batch_size: int, claim_timeout_seconds: int) -> list[OutboxEvent]:
        return []

    async def mark_delivered(self, event_id: str) -> None:
        pass

    async def mark_failed(self, event_id: str, error: str) -> None:
        pass

    async def mark_dead_letter(self, event_id: str, error: str) -> None:
        pass

    async def statistics(self) -> OutboxStatistics:
        return OutboxStatistics(
            delivered=len(self._delivered),
            total=len(self._delivered),
        )

    async def delete_processed(self, before: datetime) -> int:
        return 0

    async def reprocess_dead_letters(self, max_events: int = 100) -> int:
        return 0
