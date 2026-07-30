from __future__ import annotations

import asyncio
import base64
import json
import socket
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from aiokafka.errors import KafkaError

from aqros_events.envelope import EventEnvelope
from aqros_events.errors import EventPublishError
from aqros_events.kafka import (
    KafkaEventBus,
    deserialize_envelope,
    serialize_envelope,
)

# ---------------------------------------------------------------------------
# Kafka availability check
# ---------------------------------------------------------------------------


def _kafka_reachable(host: str = "localhost", port: int = 9092) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2.0):
            return True
    except (OSError, TimeoutError):
        return False


kafka_unavailable = pytest.mark.skipif(
    not _kafka_reachable(),
    reason="Kafka/Redpanda broker not available on localhost:9092",
)


# ---------------------------------------------------------------------------
# Serialization tests (pure, no Kafka broker needed)
# ---------------------------------------------------------------------------


class TestSerializationRoundTrip:
    def test_serialize_deserialize_full_envelope(self) -> None:
        now = datetime.now(UTC)
        env = EventEnvelope(
            topic="orders.filled",
            payload=b'{"order_id": "abc"}',
            event_time=now,
            knowledge_time=now + timedelta(seconds=1),
            producer="oms",
            schema_version="1.0",
            content_type="application/json",
            event_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            correlation_id="01ARZ3NDEKTSV4RRFFQ69G5FAW",
            causation_id="01ARZ3NDEKTSV4RRFFQ69G5FAX",
        )
        data = serialize_envelope(env)
        restored = deserialize_envelope(data)
        assert restored == env

    def test_serialize_deserialize_minimal_envelope(self) -> None:
        now = datetime.now(UTC)
        env = EventEnvelope(
            topic="test.topic",
            payload=b"{}",
            event_time=now,
            knowledge_time=now,
            producer="test",
            schema_version="1.0",
        )
        data = serialize_envelope(env)
        restored = deserialize_envelope(data)
        assert restored.topic == env.topic
        assert restored.payload == env.payload
        assert restored.event_time == env.event_time
        assert restored.knowledge_time == env.knowledge_time
        assert restored.producer == env.producer
        assert restored.schema_version == env.schema_version
        assert restored.content_type == "application/json"
        assert restored.event_id == env.event_id
        assert restored.correlation_id == env.correlation_id
        assert restored.causation_id is None

    def test_serialize_binary_payload(self) -> None:
        raw = bytes(range(256))
        env = EventEnvelope(
            topic="binary.test",
            payload=raw,
            event_time=datetime.now(UTC),
            knowledge_time=datetime.now(UTC),
            producer="test",
            schema_version="1.0",
            content_type="application/octet-stream",
        )
        data = serialize_envelope(env)
        obj = json.loads(data)
        assert obj["payload_b64"] == base64.b64encode(raw).decode("ascii")
        restored = deserialize_envelope(data)
        assert restored.payload == raw

    def test_serialize_output_is_utf8_json(self) -> None:
        env = EventEnvelope(
            topic="json.test",
            payload=b"{}",
            event_time=datetime.now(UTC),
            knowledge_time=datetime.now(UTC),
            producer="test",
            schema_version="1.0",
        )
        data = serialize_envelope(env)
        assert isinstance(data, bytes)
        obj = json.loads(data.decode("utf-8"))
        assert obj["__v"] == "1"
        assert obj["topic"] == "json.test"

    def test_deserialize_without_causation_id(self) -> None:
        data = json.dumps(
            {
                "__v": "1",
                "event_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
                "topic": "test.t",
                "payload_b64": base64.b64encode(b"hello").decode("ascii"),
                "event_time": "2026-07-31T12:00:00+00:00",
                "knowledge_time": "2026-07-31T12:00:00+00:00",
                "producer": "p",
                "schema_version": "1.0",
                "content_type": "application/json",
                "correlation_id": "01ARZ3NDEKTSV4RRFFQ69G5FAW",
            }
        ).encode("utf-8")
        env = deserialize_envelope(data)
        assert env.causation_id is None

    def test_deserialize_backward_compat_missing_content_type(self) -> None:
        data = json.dumps(
            {
                "__v": "1",
                "event_id": "01ARZ3NDEKTSV4RRFFQ69G5FAV",
                "topic": "test.t",
                "payload_b64": base64.b64encode(b"x").decode("ascii"),
                "event_time": "2026-07-31T12:00:00+00:00",
                "knowledge_time": "2026-07-31T12:00:00+00:00",
                "producer": "p",
                "schema_version": "1.0",
                "correlation_id": "01ARZ3NDEKTSV4RRFFQ69G5FAW",
            }
        ).encode("utf-8")
        env = deserialize_envelope(data)
        assert env.content_type == "application/json"


# ---------------------------------------------------------------------------
# KafkaEventBus unit tests (mocked aiokafka, no broker)
# ---------------------------------------------------------------------------


@pytest.fixture
def envelope() -> EventEnvelope:
    return EventEnvelope(
        topic="test.topic",
        payload=b'{"k": "v"}',
        event_time=datetime.now(UTC),
        knowledge_time=datetime.now(UTC),
        producer="test",
        schema_version="1.0",
    )


@patch("aqros_events.kafka.AIOKafkaProducer", autospec=True)
class TestKafkaEventBusUnit:
    async def test_publish_without_start_raises(
        self, mock_producer: AsyncMock, envelope: EventEnvelope
    ) -> None:
        bus = KafkaEventBus(bootstrap_servers="localhost:9092")
        with pytest.raises(RuntimeError, match="not started"):
            await bus.publish(envelope)

    async def test_subscribe_without_start_does_not_raise(
        self, mock_producer: AsyncMock, envelope: EventEnvelope
    ) -> None:
        bus = KafkaEventBus(bootstrap_servers="localhost:9092")

        async def handler(env: EventEnvelope) -> None:
            pass

        await bus.subscribe("test.topic", handler)
        assert bus.handler_count("test.topic") == 1

    async def test_publish_calls_producer_send(
        self, mock_producer: AsyncMock, envelope: EventEnvelope
    ) -> None:
        producer_instance = AsyncMock()
        mock_producer.return_value = producer_instance
        bus = KafkaEventBus(bootstrap_servers="localhost:9092")
        await bus.start()
        await bus.publish(envelope)
        producer_instance.send.assert_awaited_once()
        call_args = producer_instance.send.call_args[1]
        assert call_args["topic"] == "test.topic"
        assert call_args["key"] == envelope.event_id.encode("utf-8")
        assert isinstance(call_args["value"], bytes)
        await bus.stop()

    async def test_publish_propagates_kafka_error(
        self, mock_producer: AsyncMock, envelope: EventEnvelope
    ) -> None:
        producer_instance = AsyncMock()
        producer_instance.send.side_effect = KafkaError("kafka down")
        mock_producer.return_value = producer_instance
        bus = KafkaEventBus(bootstrap_servers="localhost:9092")
        await bus.start()
        with pytest.raises(EventPublishError):
            await bus.publish(envelope)
        await bus.stop()

    async def test_subscribe_idempotent(self, mock_producer: AsyncMock) -> None:
        bus = KafkaEventBus(bootstrap_servers="localhost:9092")

        async def handler(env: EventEnvelope) -> None:
            pass

        await bus.subscribe("t", handler)
        await bus.subscribe("t", handler)
        assert bus.handler_count("t") == 1
        assert len(bus._handlers["t"]) == 1  # type: ignore[attr-defined]

    async def test_unsubscribe_removes_handler(self, mock_producer: AsyncMock) -> None:
        bus = KafkaEventBus(bootstrap_servers="localhost:9092")

        async def handler(env: EventEnvelope) -> None:
            pass

        await bus.subscribe("t", handler)
        assert bus.handler_count("t") == 1
        await bus.unsubscribe("t", handler)
        assert bus.handler_count("t") == 0

    async def test_unsubscribe_nonexistent_is_noop(self, mock_producer: AsyncMock) -> None:
        bus = KafkaEventBus(bootstrap_servers="localhost:9092")

        async def handler(env: EventEnvelope) -> None:
            pass

        await bus.unsubscribe("nonexistent", handler)

    def test_handler_count(self, mock_producer: AsyncMock) -> None:
        bus = KafkaEventBus(bootstrap_servers="localhost:9092")
        assert bus.handler_count("t") == 0

    async def test_start_stop_lifecycle(self, mock_producer: AsyncMock) -> None:
        producer_instance = AsyncMock()
        mock_producer.return_value = producer_instance
        bus = KafkaEventBus(bootstrap_servers="localhost:9092")
        assert bus._producer is None  # type: ignore[attr-defined]
        await bus.start()
        assert bus._producer is not None  # type: ignore[attr-defined]
        await bus.stop()
        assert bus._producer is None  # type: ignore[attr-defined]

    async def test_stop_without_start_is_noop(self, mock_producer: AsyncMock) -> None:
        bus = KafkaEventBus(bootstrap_servers="localhost:9092")
        await bus.stop()  # should not raise


# ---------------------------------------------------------------------------
# KafkaEventBus integration tests (requires running Kafka/Redpanda broker)
# ---------------------------------------------------------------------------


@kafka_unavailable
@pytest.mark.integration
class TestKafkaEventBusIntegration:
    async def test_publish_and_consume(self) -> None:
        bus = KafkaEventBus(
            bootstrap_servers="localhost:9092",
            group_id="test-integration",
            auto_offset_reset="earliest",
        )
        received: list[EventEnvelope] = []

        async def handler(env: EventEnvelope) -> None:
            received.append(env)

        await bus.start()
        await bus.subscribe("test.integration", handler)
        env = EventEnvelope(
            topic="test.integration",
            payload=b'{"msg": "hello"}',
            event_time=datetime.now(UTC),
            knowledge_time=datetime.now(UTC),
            producer="test",
            schema_version="1.0",
        )
        await bus.publish(env)
        for _ in range(20):
            if received:
                break
            await asyncio.sleep(0.1)
        await bus.stop()
        assert len(received) >= 1
        assert received[0].event_id == env.event_id
        assert received[0].payload == env.payload
