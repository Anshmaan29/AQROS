from __future__ import annotations

from datetime import UTC, datetime

from aqros_outbox import OutboxEvent, OutboxStatistics, OutboxStatus


class TestOutboxEvent:
    def test_create_defaults(self) -> None:
        event = OutboxEvent(
            event_id="01ARZ3NDEKTSV4RRFFQ69G5FAV",
            topic="test.event",
            payload=b"{}",
        )
        assert event.event_id == "01ARZ3NDEKTSV4RRFFQ69G5FAV"
        assert event.topic == "test.event"
        assert event.payload == b"{}"
        assert event.content_type == "application/json"
        assert event.status == OutboxStatus.PENDING
        assert event.retry_count == 0
        assert event.max_retries == 5
        assert event.headers == {}

    def test_full_construction(self) -> None:
        now = datetime.now(UTC)
        event = OutboxEvent(
            event_id="EVT001",
            topic="orders.filled",
            payload=b'{"order_id": "123"}',
            content_type="application/json",
            event_time=now,
            knowledge_time=now,
            producer="oms",
            schema_version="1.0",
            correlation_id="CORR001",
            causation_id="CAUS001",
            headers={"x-retry": "3"},
            status=OutboxStatus.PENDING,
            retry_count=0,
            max_retries=10,
            last_error=None,
            created_at=now,
            next_retry_at=None,
            delivered_at=None,
        )
        assert event.topic == "orders.filled"
        assert event.causation_id == "CAUS001"
        assert event.max_retries == 10

    def test_status_enum_values(self) -> None:
        assert OutboxStatus.PENDING.value == "PENDING"
        assert OutboxStatus.PROCESSING.value == "PROCESSING"
        assert OutboxStatus.DELIVERED.value == "DELIVERED"
        assert OutboxStatus.FAILED.value == "FAILED"
        assert OutboxStatus.DEAD_LETTER.value == "DEAD_LETTER"


class TestOutboxStatistics:
    def test_defaults(self) -> None:
        stats = OutboxStatistics()
        assert stats.pending == 0
        assert stats.total == 0

    def test_with_values(self) -> None:
        stats = OutboxStatistics(
            pending=5,
            processing=2,
            delivered=100,
            failed=3,
            dead_letter=1,
            total=111,
            retry_sum=15,
        )
        assert stats.pending == 5
        assert stats.delivered == 100
        assert stats.dead_letter == 1
