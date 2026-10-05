from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from aqros_events import EventEnvelope, InProcessEventBus
from aqros_outbox import (
    InMemoryOutboxRepository,
    OutboxConfig,
    OutboxDispatcher,
    OutboxEvent,
    OutboxMetrics,
    OutboxStatus,
)

pytestmark = pytest.mark.asyncio


class TestOutboxDispatcher:
    @pytest.fixture
    def repo(self) -> InMemoryOutboxRepository:
        return InMemoryOutboxRepository()

    @pytest.fixture
    def config(self) -> OutboxConfig:
        return OutboxConfig(
            poll_interval_seconds=0.05,
            batch_size=10,
            retention_hours=72,
            cleanup_interval_minutes=60,
        )

    @pytest.fixture
    def metrics(self) -> OutboxMetrics:
        return OutboxMetrics()

    async def test_dispatches_pending_events(
        self,
        repo: InMemoryOutboxRepository,
        event_bus: InProcessEventBus,
        config: OutboxConfig,
        metrics: OutboxMetrics,
    ) -> None:
        collected: list[EventEnvelope] = []

        async def handler(envelope: EventEnvelope) -> None:
            collected.append(envelope)

        await event_bus.subscribe("test.events", handler)

        await repo.add(OutboxEvent(event_id="D1", topic="test.events", payload=b'{"a":1}'))
        await repo.add(OutboxEvent(event_id="D2", topic="test.events", payload=b'{"b":2}'))

        dispatcher = OutboxDispatcher(repo, event_bus, config, metrics)
        await dispatcher.start()
        await asyncio.sleep(0.2)
        await dispatcher.stop()

        assert len(collected) == 2
        assert metrics.processed_total == 2

    async def test_retry_on_failure(
        self,
        event_bus: InProcessEventBus,
    ) -> None:
        repo = InMemoryOutboxRepository()
        config = OutboxConfig(
            poll_interval_seconds=0.05,
            batch_size=10,
            base_retry_delay_seconds=0.01,
        )
        metrics = OutboxMetrics()

        fail_count = 0

        class FailingBus(InProcessEventBus):
            async def publish(self, envelope: EventEnvelope) -> None:
                nonlocal fail_count
                fail_count += 1
                if fail_count <= 1:
                    msg = "transient error"
                    raise RuntimeError(msg)
                await super().publish(envelope)

        failing_bus = FailingBus()
        collected: list[EventEnvelope] = []

        async def handler(envelope: EventEnvelope) -> None:
            collected.append(envelope)

        await failing_bus.subscribe("test.events", handler)

        event = OutboxEvent(
            event_id="RETRY1",
            topic="test.events",
            payload=b"{}",
            max_retries=3,
        )
        await repo.add(event)

        dispatcher = OutboxDispatcher(repo, failing_bus, config, metrics)
        await dispatcher.start()
        await asyncio.sleep(0.5)
        await dispatcher.stop()

        assert len(collected) == 1
        assert metrics.processed_total == 1
        assert metrics.failed_total == 1

    async def test_dead_letter_after_max_retries(
        self,
        event_bus: InProcessEventBus,
    ) -> None:
        repo = InMemoryOutboxRepository()
        config = OutboxConfig(
            poll_interval_seconds=0.05,
            batch_size=10,
            base_retry_delay_seconds=0.01,
        )
        metrics = OutboxMetrics()

        class AlwaysFailBus(InProcessEventBus):
            async def publish(self, envelope: EventEnvelope) -> None:
                msg = "always fails"
                raise RuntimeError(msg)

        failing_bus = AlwaysFailBus()

        event = OutboxEvent(
            event_id="DEAD1",
            topic="test.events",
            payload=b"{}",
            max_retries=2,
        )
        await repo.add(event)

        dispatcher = OutboxDispatcher(repo, failing_bus, config, metrics)
        await dispatcher.start()
        await asyncio.sleep(0.5)
        await dispatcher.stop()

        stats = await repo.statistics()
        assert stats.dead_letter == 1
        assert metrics.dead_letter_total == 1

    async def test_graceful_shutdown(
        self,
        repo: InMemoryOutboxRepository,
        event_bus: InProcessEventBus,
        config: OutboxConfig,
    ) -> None:
        collected: list[EventEnvelope] = []

        async def handler(envelope: EventEnvelope) -> None:
            await asyncio.sleep(0.05)
            collected.append(envelope)

        await event_bus.subscribe("test.events", handler)

        for i in range(10):
            await repo.add(OutboxEvent(event_id=f"GS{i}", topic="test.events", payload=b"{}"))

        dispatcher = OutboxDispatcher(repo, event_bus, config)
        await dispatcher.start()
        await asyncio.sleep(0.1)
        await dispatcher.stop()

        assert dispatcher.is_running is False

    async def test_empty_repository_no_errors(
        self,
        repo: InMemoryOutboxRepository,
        event_bus: InProcessEventBus,
        config: OutboxConfig,
    ) -> None:
        dispatcher = OutboxDispatcher(repo, event_bus, config)
        await dispatcher.start()
        await asyncio.sleep(0.1)
        await dispatcher.stop()

    async def test_concurrent_dispatch(
        self,
        event_bus: InProcessEventBus,
        config: OutboxConfig,
    ) -> None:
        repo = InMemoryOutboxRepository()
        collected: list[EventEnvelope] = []
        lock = asyncio.Lock()

        async def handler(envelope: EventEnvelope) -> None:
            async with lock:
                collected.append(envelope)

        await event_bus.subscribe("test.events", handler)

        for i in range(25):
            await repo.add(
                OutboxEvent(
                    event_id=f"CON{i:04d}",
                    topic="test.events",
                    payload=b"{}",
                )
            )

        dispatcher = OutboxDispatcher(repo, event_bus, config)
        await dispatcher.start()
        await asyncio.sleep(0.5)
        await dispatcher.stop()

        assert len(collected) == 25


class TestCrashRecovery:
    async def test_processing_events_reclaimable_after_timeout(self) -> None:
        repo = InMemoryOutboxRepository()
        event = OutboxEvent(event_id="CRASH1", topic="test", payload=b"{}")
        await repo.add(event)
        await repo.claim_ready(10, 30)

        evt = repo._events["CRASH1"]
        evt.status = OutboxStatus.PROCESSING
        repo._events["CRASH1"] = evt

        claimed = await repo.claim_ready(10, 30)
        assert len(claimed) == 0

        evt.status = OutboxStatus.PENDING
        evt.next_retry_at = datetime.now(UTC)
        repo._events["CRASH1"] = evt

        claimed2 = await repo.claim_ready(10, 30)
        assert len(claimed2) == 1

    async def test_claim_excludes_delivered(self) -> None:
        repo = InMemoryOutboxRepository()
        for i in range(3):
            e = OutboxEvent(event_id=f"EXCL{i}", topic="test", payload=b"{}")
            await repo.add(e)

        await repo.claim_ready(10, 30)
        for eid in ["EXCL0", "EXCL1", "EXCL2"]:
            await repo.mark_delivered(eid)

        claimed = await repo.claim_ready(10, 30)
        assert len(claimed) == 0


class TestDispatcherMetrics:
    @pytest.fixture
    def config(self) -> OutboxConfig:
        return OutboxConfig(poll_interval_seconds=0.05, batch_size=10)

    async def test_metrics_track_latency(
        self,
        event_bus: InProcessEventBus,
        config: OutboxConfig,
    ) -> None:
        repo = InMemoryOutboxRepository()
        metrics = OutboxMetrics()

        collected: list[EventEnvelope] = []

        async def handler(envelope: EventEnvelope) -> None:
            await asyncio.sleep(0.01)
            collected.append(envelope)

        await event_bus.subscribe("test.events", handler)

        await repo.add(OutboxEvent(event_id="M1", topic="test.events", payload=b"{}"))

        dispatcher = OutboxDispatcher(repo, event_bus, config, metrics)
        await dispatcher.start()
        await asyncio.sleep(0.3)
        await dispatcher.stop()

        assert metrics.dispatch_count >= 1
        assert metrics.avg_dispatch_latency > 0
