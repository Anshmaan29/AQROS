from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aqros_events.envelope import EventEnvelope


class TestEventEnvelope:
    def test_auto_generates_event_id(self) -> None:
        env = EventEnvelope(
            topic="test.topic",
            payload=b"{}",
            event_time=datetime.now(UTC),
            knowledge_time=datetime.now(UTC),
            producer="test-service",
            schema_version="1.0",
        )
        assert len(env.event_id) == 26

    def test_auto_generates_correlation_id(self) -> None:
        env = EventEnvelope(
            topic="test.topic",
            payload=b"{}",
            event_time=datetime.now(UTC),
            knowledge_time=datetime.now(UTC),
            producer="test-service",
            schema_version="1.0",
        )
        assert len(env.correlation_id) == 26

    def test_event_and_correlation_id_are_different(self) -> None:
        env = EventEnvelope(
            topic="test.topic",
            payload=b"{}",
            event_time=datetime.now(UTC),
            knowledge_time=datetime.now(UTC),
            producer="test-service",
            schema_version="1.0",
        )
        assert env.event_id != env.correlation_id

    def test_causation_id_is_none_by_default(self) -> None:
        env = EventEnvelope(
            topic="test.topic",
            payload=b"{}",
            event_time=datetime.now(UTC),
            knowledge_time=datetime.now(UTC),
            producer="test-service",
            schema_version="1.0",
        )
        assert env.causation_id is None

    def test_causation_id_set_explicitly(self) -> None:
        env = EventEnvelope(
            topic="test.topic",
            payload=b"{}",
            event_time=datetime.now(UTC),
            knowledge_time=datetime.now(UTC),
            producer="test-service",
            schema_version="1.0",
            causation_id="cause-ulid-here",
        )
        assert env.causation_id == "cause-ulid-here"

    def test_correlation_id_set_explicitly(self) -> None:
        env = EventEnvelope(
            topic="test.topic",
            payload=b"{}",
            event_time=datetime.now(UTC),
            knowledge_time=datetime.now(UTC),
            producer="test-service",
            schema_version="1.0",
            correlation_id="explicit-correlation",
        )
        assert env.correlation_id == "explicit-correlation"

    def test_event_id_set_explicitly(self) -> None:
        env = EventEnvelope(
            topic="test.topic",
            payload=b"{}",
            event_time=datetime.now(UTC),
            knowledge_time=datetime.now(UTC),
            producer="test-service",
            schema_version="1.0",
            event_id="explicit-event-id",
        )
        assert env.event_id == "explicit-event-id"

    def test_default_content_type(self) -> None:
        env = EventEnvelope(
            topic="test.topic",
            payload=b"{}",
            event_time=datetime.now(UTC),
            knowledge_time=datetime.now(UTC),
            producer="test-service",
            schema_version="1.0",
        )
        assert env.content_type == "application/json"

    def test_custom_content_type(self) -> None:
        env = EventEnvelope(
            topic="test.topic",
            payload=b"{}",
            event_time=datetime.now(UTC),
            knowledge_time=datetime.now(UTC),
            producer="test-service",
            schema_version="1.0",
            content_type="application/octet-stream",
        )
        assert env.content_type == "application/octet-stream"

    def test_all_fields(self) -> None:
        now = datetime.now(UTC)
        env = EventEnvelope(
            topic="orders.filled",
            payload=b'{"order_id": "abc"}',
            event_time=now,
            knowledge_time=now + timedelta(seconds=1),
            producer="oms",
            schema_version="1.0",
            content_type="application/json",
            event_id="abc123",
            correlation_id="corr456",
            causation_id="cause789",
        )
        assert env.topic == "orders.filled"
        assert env.payload == b'{"order_id": "abc"}'
        assert env.event_time == now
        assert env.knowledge_time == now + timedelta(seconds=1)
        assert env.producer == "oms"
        assert env.schema_version == "1.0"
        assert env.content_type == "application/json"
        assert env.event_id == "abc123"
        assert env.correlation_id == "corr456"
        assert env.causation_id == "cause789"

    def test_is_frozen(self) -> None:
        env = EventEnvelope(
            topic="test.topic",
            payload=b"{}",
            event_time=datetime.now(UTC),
            knowledge_time=datetime.now(UTC),
            producer="test",
            schema_version="1.0",
        )
        try:
            env.topic = "other"
            pytest.fail("should have raised")
        except (AttributeError, TypeError):
            pass
