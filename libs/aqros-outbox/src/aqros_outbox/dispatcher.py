from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from aqros_events import EventBus
from aqros_outbox.domain import OutboxEvent, OutboxRepository
from aqros_outbox.metrics import NoopOutboxMetrics, OutboxMetrics
from aqros_outbox.serializer import event_to_envelope

_logger = logging.getLogger(__name__)


@dataclass
class OutboxConfig:
    poll_interval_seconds: float = 1.0
    batch_size: int = 50
    claim_timeout_seconds: int = 30
    max_retries: int = 5
    base_retry_delay_seconds: float = 1.0
    max_retry_delay_seconds: float = 60.0
    retention_hours: int = 72
    cleanup_interval_minutes: int = 60


class OutboxDispatcher:
    def __init__(
        self,
        repository: OutboxRepository,
        event_bus: EventBus,
        config: OutboxConfig | None = None,
        metrics: OutboxMetrics | NoopOutboxMetrics | None = None,
    ) -> None:
        self._repository = repository
        self._event_bus = event_bus
        self._config = config or OutboxConfig()
        self._metrics = metrics or NoopOutboxMetrics()
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._last_cleanup: datetime = datetime.now(UTC)

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run())
        _logger.info("outbox_dispatcher.started")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        _logger.info("outbox_dispatcher.stopped")

    async def _run(self) -> None:
        while self._running:
            try:
                events = await self._repository.claim_ready(
                    batch_size=self._config.batch_size,
                    claim_timeout_seconds=self._config.claim_timeout_seconds,
                )
                if events:
                    for event in events:
                        if not self._running:
                            break
                        await self._dispatch(event)
                    await self._periodic_cleanup()
                else:
                    await asyncio.sleep(self._config.poll_interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception:
                _logger.exception("outbox_dispatcher.error")
                await asyncio.sleep(1.0)
        _logger.info("outbox_dispatcher.exiting")

    async def _dispatch(self, event: OutboxEvent) -> None:
        envelope = event_to_envelope(event)
        start_time = time.monotonic()
        try:
            await self._event_bus.publish(envelope)
            await self._repository.mark_delivered(event.event_id)
            latency = time.monotonic() - start_time
            self._metrics.record_dispatch(latency)
            self._metrics.inc_processed()
            _logger.debug(
                "outbox.dispatched",
                extra={
                    "event_id": event.event_id,
                    "topic": event.topic,
                    "latency_ms": round(latency * 1000, 2),
                },
            )
        except Exception as exc:
            error = str(exc)
            _logger.warning(
                "outbox.dispatch_failed",
                extra={
                    "event_id": event.event_id,
                    "topic": event.topic,
                    "retry_count": event.retry_count,
                    "error": error,
                },
            )
            if event.retry_count + 1 >= event.max_retries:
                await self._repository.mark_dead_letter(event.event_id, error)
                self._metrics.inc_dead_letter()
            else:
                await self._repository.mark_failed(event.event_id, error)
                self._metrics.inc_failed()
                self._metrics.inc_retry()

    async def _periodic_cleanup(self) -> None:
        now = datetime.now(UTC)
        if (now - self._last_cleanup).total_seconds() < self._config.cleanup_interval_minutes * 60:
            return
        self._last_cleanup = now
        cutoff = now - timedelta(hours=self._config.retention_hours)
        try:
            deleted = await self._repository.delete_processed(cutoff)
            if deleted:
                _logger.info("outbox.cleanup deleted=%d", deleted)
        except Exception:
            _logger.exception("outbox.cleanup_error")
