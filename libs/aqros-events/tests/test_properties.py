"""Property-based tests for the event bus library using Hypothesis."""

from __future__ import annotations

import asyncio
import itertools
from datetime import UTC, datetime

from hypothesis import assume, given
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from aqros_events.bus import EventHandler
from aqros_events.envelope import EventEnvelope
from aqros_events.inprocess import InProcessEventBus
from aqros_events.ulid import ulid

# -- Strategies ---------------------------------------------------------------

topic_strategy = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122, categories=("L",)),
    min_size=1,
    max_size=64,
).map(lambda s: s.replace("_", ".").replace("-", "."))


def _ulid_strategy() -> st.SearchStrategy[str]:
    return st.builds(ulid)


def _envelope_strategy() -> st.SearchStrategy[EventEnvelope]:
    return st.builds(
        EventEnvelope,
        topic=topic_strategy,
        payload=st.binary(min_size=0, max_size=1024),
        event_time=st.datetimes(timezones=st.timezones()),
        knowledge_time=st.datetimes(timezones=st.timezones()),
        producer=topic_strategy,
        schema_version=st.sampled_from(["1.0", "2.0", "2026-07-31"]),
        content_type=st.sampled_from(["application/json", "application/octet-stream"]),
        event_id=_ulid_strategy(),
        correlation_id=_ulid_strategy(),
        causation_id=st.one_of(_ulid_strategy(), st.none()),
    )


# -- Property tests -----------------------------------------------------------


class TestULIDProperties:
    @given(st.data())
    def test_all_ulids_are_26_chars(self, data: st.DataObject) -> None:
        uid = data.draw(_ulid_strategy())
        assert len(uid) == 26

    @given(st.data())
    def test_ulid_contains_only_base32_chars(self, data: st.DataObject) -> None:
        uid = data.draw(_ulid_strategy())
        valid = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
        assert all(c in valid for c in uid)

    @given(st.lists(_ulid_strategy(), min_size=2, max_size=200))
    def test_ulids_are_sortable(self, ids: list[str]) -> None:
        sorted_ids = sorted(ids)
        assert all(a <= b for a, b in itertools.pairwise(sorted_ids))

    @given(st.lists(_ulid_strategy(), min_size=2, max_size=1000))
    def test_ulids_are_unique(self, ids: list[str]) -> None:
        assert len(set(ids)) == len(ids)


class TestEnvelopeProperties:
    @given(_envelope_strategy())
    def test_envelope_is_frozen(self, env: EventEnvelope) -> None:
        try:
            env.topic = "other"
            msg = "should have raised"
            raise AssertionError(msg)
        except (AttributeError, TypeError):
            pass

    @given(_envelope_strategy())
    def test_event_id_is_ulid(self, env: EventEnvelope) -> None:
        assert len(env.event_id) == 26

    @given(_envelope_strategy())
    def test_correlation_id_is_ulid(self, env: EventEnvelope) -> None:
        assert len(env.correlation_id) == 26

    @given(_envelope_strategy())
    def test_payload_is_bytes(self, env: EventEnvelope) -> None:
        assert isinstance(env.payload, bytes)


class TestInProcessBusProperties:
    @given(
        topics=st.lists(topic_strategy, min_size=1, max_size=10),
        num_events=st.integers(min_value=0, max_value=50),
    )
    async def test_publish_without_handlers_never_raises(
        self,
        topics: list[str],
        num_events: int,
    ) -> None:
        bus = InProcessEventBus()
        for topic in topics:
            for _ in range(num_events):
                await bus.publish(
                    EventEnvelope(
                        topic=topic,
                        payload=b"{}",
                        event_time=datetime.now(UTC),
                        knowledge_time=datetime.now(UTC),
                        producer="test",
                        schema_version="1.0",
                    )
                )


# -- Stateful testing ---------------------------------------------------------


class EventBusModel(RuleBasedStateMachine):
    """Stateful model that simulates an InProcessEventBus and verifies
    that the actual bus behaviour matches the model's expectations."""

    def __init__(self) -> None:
        super().__init__()
        self.bus = InProcessEventBus()
        self._handlers: dict[str, list[tuple[str, EventHandler]]] = {}
        self._ever_subscribed: set[str] = set()
        self.published: dict[str, list[EventEnvelope]] = {}
        self.received: dict[str, list[str]] = {}
        self._loop = asyncio.new_event_loop()

    def _run(self, coro: asyncio.Future) -> None:
        self._loop.run_until_complete(coro)

    @rule(topic=topic_strategy)
    def subscribe(self, topic: str) -> None:
        handler_id = f"handler-{sum(len(v) for v in self._handlers.values())}"

        async def handler(env: EventEnvelope) -> None:
            self.received.setdefault(env.topic, []).append(handler_id)

        self._ever_subscribed.add(handler_id)
        self._run(self.bus.subscribe(topic, handler))
        self._handlers.setdefault(topic, []).append((handler_id, handler))

    @rule(topic=topic_strategy)
    def unsubscribe(self, topic: str) -> None:
        assume(topic in self._handlers and self._handlers[topic])
        _, handler = self._handlers[topic].pop()
        self._run(self.bus.unsubscribe(topic, handler))

    @rule(topic=topic_strategy)
    def publish(self, topic: str) -> None:
        env = EventEnvelope(
            topic=topic,
            payload=b"{}",
            event_time=datetime.now(UTC),
            knowledge_time=datetime.now(UTC),
            producer="test",
            schema_version="1.0",
        )
        self.published.setdefault(topic, []).append(env)
        self._run(self.bus.publish(env))

    @invariant()
    def all_receivers_were_ever_subscribed(self) -> None:
        for _topic, received_ids in self.received.items():
            for hid in received_ids:
                assert hid in self._ever_subscribed


TestEventBusStates = EventBusModel.TestCase
