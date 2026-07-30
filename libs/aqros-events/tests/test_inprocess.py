from __future__ import annotations

from datetime import UTC, datetime

import pytest

from aqros_events.envelope import EventEnvelope
from aqros_events.errors import EventPublishError
from aqros_events.inprocess import InProcessEventBus


def _envelope(topic: str = "test.topic", payload: bytes = b"{}") -> EventEnvelope:
    return EventEnvelope(
        topic=topic,
        payload=payload,
        event_time=datetime.now(UTC),
        knowledge_time=datetime.now(UTC),
        producer="test",
        schema_version="1.0",
    )


class TestInProcessEventBus:
    async def test_publish_without_subscribers_is_noop(self) -> None:
        bus = InProcessEventBus()
        await bus.publish(_envelope())

    async def test_subscribed_handler_receives_event(self) -> None:
        bus = InProcessEventBus()
        received: list[EventEnvelope] = []

        async def handler(env: EventEnvelope) -> None:
            received.append(env)

        await bus.subscribe("test.topic", handler)
        env = _envelope()
        await bus.publish(env)
        assert len(received) == 1
        assert received[0] is env

    async def test_multiple_handlers_all_called(self) -> None:
        bus = InProcessEventBus()
        results: list[int] = []

        async def h1(env: EventEnvelope) -> None:
            results.append(1)

        async def h2(env: EventEnvelope) -> None:
            results.append(2)

        await bus.subscribe("test.topic", h1)
        await bus.subscribe("test.topic", h2)
        await bus.publish(_envelope())
        assert results == [1, 2]

    async def test_handlers_called_in_registration_order(self) -> None:
        bus = InProcessEventBus()
        seq: list[int] = []

        async def h1(env: EventEnvelope) -> None:
            seq.append(1)

        async def h2(env: EventEnvelope) -> None:
            seq.append(2)

        async def h3(env: EventEnvelope) -> None:
            seq.append(3)

        await bus.subscribe("test.topic", h1)
        await bus.subscribe("test.topic", h2)
        await bus.subscribe("test.topic", h3)
        await bus.publish(_envelope())
        assert seq == [1, 2, 3]

    async def test_different_topics_isolated(self) -> None:
        bus = InProcessEventBus()
        topic1_msgs: list[str] = []
        topic2_msgs: list[str] = []

        async def h1(env: EventEnvelope) -> None:
            topic1_msgs.append(env.topic)

        async def h2(env: EventEnvelope) -> None:
            topic2_msgs.append(env.topic)

        await bus.subscribe("topic.1", h1)
        await bus.subscribe("topic.2", h2)
        await bus.publish(_envelope(topic="topic.1"))
        await bus.publish(_envelope(topic="topic.2"))
        assert topic1_msgs == ["topic.1"]
        assert topic2_msgs == ["topic.2"]

    async def test_unsubscribed_handler_not_called(self) -> None:
        bus = InProcessEventBus()
        received: list[EventEnvelope] = []

        async def handler(env: EventEnvelope) -> None:
            received.append(env)

        await bus.subscribe("test.topic", handler)
        await bus.unsubscribe("test.topic", handler)
        await bus.publish(_envelope())
        assert len(received) == 0

    async def test_unsubscribe_nonexistent_is_noop(self) -> None:
        bus = InProcessEventBus()

        async def handler(env: EventEnvelope) -> None:
            pass

        await bus.unsubscribe("nonexistent.topic", handler)

    async def test_subscribe_twice_is_idempotent(self) -> None:
        bus = InProcessEventBus()
        call_count = 0

        async def handler(env: EventEnvelope) -> None:
            nonlocal call_count
            call_count += 1

        await bus.subscribe("test.topic", handler)
        await bus.subscribe("test.topic", handler)
        await bus.publish(_envelope())
        assert call_count == 1

    async def test_raises_on_handler_failure(self) -> None:
        bus = InProcessEventBus()

        async def failing(env: EventEnvelope) -> None:
            msg = "oops"
            raise ValueError(msg)

        async def ok(env: EventEnvelope) -> None:
            pass

        await bus.subscribe("test.topic", failing)
        await bus.subscribe("test.topic", ok)
        with pytest.raises(EventPublishError, match=r"handler.*failed"):
            await bus.publish(_envelope())

    async def test_handler_failure_does_not_block_other_handlers(self) -> None:
        bus = InProcessEventBus()
        ok_called = False

        async def failing(env: EventEnvelope) -> None:
            msg = "fail"
            raise RuntimeError(msg)

        async def ok(env: EventEnvelope) -> None:
            nonlocal ok_called
            ok_called = True

        await bus.subscribe("test.topic", failing)
        await bus.subscribe("test.topic", ok)
        with pytest.raises(EventPublishError):
            await bus.publish(_envelope())
        assert ok_called

    async def test_handler_count(self) -> None:
        bus = InProcessEventBus()

        async def h1(env: EventEnvelope) -> None:
            pass

        async def h2(env: EventEnvelope) -> None:
            pass

        assert bus.handler_count("test.topic") == 0
        await bus.subscribe("test.topic", h1)
        assert bus.handler_count("test.topic") == 1
        await bus.subscribe("test.topic", h2)
        assert bus.handler_count("test.topic") == 2
        await bus.unsubscribe("test.topic", h1)
        assert bus.handler_count("test.topic") == 1

    async def test_concurrent_publishes_are_serialised(self) -> None:
        bus = InProcessEventBus()
        seen: list[int] = []

        async def handler(env: EventEnvelope) -> None:
            seen.append(1)

        await bus.subscribe("test.topic", handler)

        async def publish_many() -> None:
            for _ in range(50):
                await bus.publish(_envelope())

        import asyncio

        await asyncio.gather(publish_many(), publish_many(), publish_many())
        assert len(seen) == 150
