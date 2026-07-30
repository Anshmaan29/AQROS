"""EventEnvelope — the versioned, immutable carrier for every event on the bus.

Every event published through ``EventBus`` is wrapped in an ``EventEnvelope``
carrying routing metadata (topic), tracing metadata (correlation_id,
causation_id), bitemporal timestamps (event_time, knowledge_time), producer
identity, and a schema version — so consumers can validate, trace, and
reconstruct the event's place in the causal chain without inspecting the
payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from aqros_events.ulid import ulid


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """The immutable, versioned, self-describing carrier for a single event.

    Every instance carries its own ``event_id`` (ULID), routing and tracing
    metadata, bitemporal timestamps, and the opaque payload as ``bytes``.

    The envelope is frozen and immutable by contract — once constructed, no
    field may be mutated. The ``replace`` pattern (via ``dataclasses.replace``)
    is the correct way to derive a related envelope (e.g. a causation chain).

    Args:
        topic: The event topic (e.g. ``"orders.filled"``, ``"market.bars.1min"``).
        payload: The opaque, serialised event payload.
        event_time: When the fact recorded by the event occurred.
        knowledge_time: When the fact first became knowable to the platform.
        producer: The service that produced the event
            (e.g. ``"market-data"``, ``"backtesting-engine"``).
        schema_version: The version identifier consumers use to select the
            correct deserialiser (e.g. ``"1.0"``, ``"2026-07-31"``).
        content_type: The payload's media type (e.g. ``"application/json"``).
            Defaults to ``"application/json"``.
        event_id: The event's unique ULID identifier. Auto-generated if not
            provided. Pass an explicit value only when replaying or
            reconstructing a previously-emitted event.
        correlation_id: Traces a causal chain of events back to its root.
            Auto-generated if not provided. All events that share the same
            root cause carry the same ``correlation_id``.
        causation_id: The ``event_id`` of the event that directly caused this
            one. ``None`` for root events (those not caused by another event).
    """

    topic: str
    payload: bytes
    event_time: datetime
    knowledge_time: datetime
    producer: str
    schema_version: str
    content_type: str = "application/json"
    event_id: str = field(default_factory=ulid)
    correlation_id: str = field(default_factory=ulid)
    causation_id: str | None = None
