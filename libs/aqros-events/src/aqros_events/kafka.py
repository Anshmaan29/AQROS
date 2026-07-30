"""KafkaEventBus — a production ``EventBus`` adapter backed by Kafka/Redpanda.

Uses ``aiokafka`` for async Kafka connectivity. Every ``EventEnvelope`` is
serialised to JSON (with binary ``payload`` base64-encoded), sent to a Kafka
topic matching ``envelope.topic``, and deserialised on consumption.

The adapter implements at-least-once delivery: the same ``event_id`` may be
delivered more than once; consumers SHOULD deduplicate on ``event_id``.

Requires the ``kafka`` extra: ``pip install aqros-events[kafka]``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from contextlib import suppress
from datetime import datetime
from typing import TYPE_CHECKING, Any

from aqros_events.bus import EventBus, EventHandler
from aqros_events.envelope import EventEnvelope
from aqros_events.errors import EventPublishError

try:
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer  # type: ignore[import-untyped]
    from aiokafka.errors import KafkaError  # type: ignore[import-untyped]
except ImportError:
    if not TYPE_CHECKING:

        class _MissingKafka:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                msg = "KafkaEventBus requires the kafka extra: " "pip install aqros-events[kafka]"
                raise ImportError(msg)

        AIOKafkaConsumer = _MissingKafka  # type: ignore[assignment]
        AIOKafkaProducer = _MissingKafka  # type: ignore[assignment]
        KafkaError = RuntimeError  # type: ignore[assignment]

_logger = logging.getLogger(__name__)

_SERIALIZATION_VERSION = "1"
_POLL_TIMEOUT_MS = 500
_RECONNECT_DELAY_S = 1.0


def serialize_envelope(envelope: EventEnvelope) -> bytes:
    data: dict[str, Any] = {
        "__v": _SERIALIZATION_VERSION,
        "event_id": envelope.event_id,
        "topic": envelope.topic,
        "payload_b64": base64.b64encode(envelope.payload).decode("ascii"),
        "event_time": envelope.event_time.isoformat(),
        "knowledge_time": envelope.knowledge_time.isoformat(),
        "producer": envelope.producer,
        "schema_version": envelope.schema_version,
        "content_type": envelope.content_type,
        "correlation_id": envelope.correlation_id,
    }
    if envelope.causation_id is not None:
        data["causation_id"] = envelope.causation_id
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


def deserialize_envelope(data: bytes) -> EventEnvelope:
    obj: dict[str, Any] = json.loads(data)
    return EventEnvelope(
        event_id=obj["event_id"],
        topic=obj["topic"],
        payload=base64.b64decode(obj["payload_b64"]),
        event_time=datetime.fromisoformat(obj["event_time"]),
        knowledge_time=datetime.fromisoformat(obj["knowledge_time"]),
        producer=obj["producer"],
        schema_version=obj["schema_version"],
        content_type=obj.get("content_type", "application/json"),
        correlation_id=obj["correlation_id"],
        causation_id=obj.get("causation_id"),
    )


class KafkaEventBus(EventBus):
    """Kafka/Redpanda-backed ``EventBus`` for production use.

    This adapter serialises ``EventEnvelope`` to JSON (base64 for binary
    payload) and publishes to a Kafka topic named after ``envelope.topic``.
    It uses a single consumer that subscribes to all registered topics and
    dispatches received messages to every handler registered for that topic.

    Handlers run sequentially in the consumer background task. A slow handler
    will delay subsequent handlers but not cause message loss (Kafka offsets
    are committed only after successful dispatch). Handler exceptions are
    logged and do not block other handlers for the same message.

    Usage::

        bus = KafkaEventBus(bootstrap_servers="localhost:9092")
        await bus.start()
        await bus.subscribe("orders.filled", my_handler)
        await bus.publish(envelope)
        ...
        await bus.stop()

    Args:
        bootstrap_servers: Comma-separated ``host:port`` list.
        client_id: Kafka client ID for the producer and consumer.
        group_id: Kafka consumer group ID. If ``None``, each instance operates
            independently (default: ``None``).
        auto_offset_reset: Where to start consuming when no committed offset
            exists (``"latest"`` or ``"earliest"``; default ``"latest"``).
        producer_settings: Additional kwargs passed to ``AIOKafkaProducer``.
        consumer_settings: Additional kwargs passed to ``AIOKafkaConsumer``.
    """

    def __init__(
        self,
        bootstrap_servers: str = "localhost:9092",
        client_id: str = "aqros-events",
        group_id: str | None = None,
        auto_offset_reset: str = "latest",
        producer_settings: dict[str, Any] | None = None,
        consumer_settings: dict[str, Any] | None = None,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._client_id = client_id
        self._group_id = group_id
        self._auto_offset_reset = auto_offset_reset
        self._producer_settings = producer_settings or {}
        self._consumer_settings = consumer_settings or {}

        self._producer: AIOKafkaProducer | None = None
        self._consumer: AIOKafkaConsumer | None = None
        self._consumer_started = False
        self._handlers: dict[str, list[EventHandler]] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._consumer_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            client_id=self._client_id,
            **self._producer_settings,
        )
        await self._producer.start()
        self._running = True
        self._consumer_task = asyncio.create_task(self._consume_loop())

    async def stop(self) -> None:
        self._running = False
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._consumer_task
            self._consumer_task = None
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None
            self._consumer_started = False
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None

    async def publish(self, envelope: EventEnvelope) -> None:
        if self._producer is None:
            msg = "KafkaEventBus not started: call start() first"
            raise RuntimeError(msg)
        try:
            value = serialize_envelope(envelope)
            await self._producer.send(
                topic=envelope.topic,
                value=value,
                key=envelope.event_id.encode("utf-8"),
            )
        except KafkaError as exc:
            raise EventPublishError(
                envelope.topic,
                envelope.event_id,
                str(exc),
            ) from exc

    async def subscribe(self, topic: str, handler: EventHandler) -> None:
        async with self._lock:
            if topic not in self._handlers:
                self._handlers[topic] = []
            if handler not in self._handlers[topic]:
                self._handlers[topic].append(handler)
        await self._refresh_subscription()

    async def unsubscribe(self, topic: str, handler: EventHandler) -> None:
        async with self._lock:
            handlers = self._handlers.get(topic)
            if handlers is not None and handler in handlers:
                handlers.remove(handler)
                if not handlers:
                    del self._handlers[topic]
        await self._refresh_subscription()

    async def _refresh_subscription(self) -> None:
        if self._consumer is not None and self._running:
            async with self._lock:
                topics = list(self._handlers.keys())
            if topics:
                self._consumer.subscribe(topics=topics)
            else:
                self._consumer.unsubscribe()

    def _ensure_consumer(self) -> AIOKafkaConsumer:
        if self._consumer is None:
            self._consumer = AIOKafkaConsumer(
                bootstrap_servers=self._bootstrap_servers,
                client_id=self._client_id,
                group_id=self._group_id,
                auto_offset_reset=self._auto_offset_reset,
                **self._consumer_settings,
            )
        return self._consumer

    def handler_count(self, topic: str) -> int:
        """Return the number of handlers registered for ``topic``."""
        return len(self._handlers.get(topic, []))

    async def _consume_loop(self) -> None:
        while self._running:
            try:
                async with self._lock:
                    topics = list(self._handlers.keys())
                if not topics:
                    await asyncio.sleep(0.1)
                    continue
                consumer = self._ensure_consumer()
                if not self._consumer_started:
                    await consumer.start()
                    self._consumer_started = True
                await self._refresh_subscription()
                batch = await consumer.getmany(timeout_ms=_POLL_TIMEOUT_MS)
                for topic, messages in batch.items():
                    async with self._lock:
                        handlers = list(self._handlers.get(topic, []))
                    for msg in messages:
                        envelope = deserialize_envelope(msg.value)
                        for handler in handlers:
                            try:
                                await handler(envelope)
                            except Exception:
                                _logger.exception(
                                    "kafka_handler_failed",
                                    extra={
                                        "topic": topic,
                                        "event_id": envelope.event_id,
                                    },
                                )
            except asyncio.CancelledError:
                break
            except KafkaError:
                _logger.warning(
                    "kafka_consumer_error",
                    exc_info=True,
                )
                await asyncio.sleep(_RECONNECT_DELAY_S)
            except Exception:
                _logger.exception("kafka_consumer_loop_fatal")
                await asyncio.sleep(_RECONNECT_DELAY_S)
