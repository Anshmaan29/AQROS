"""AQROS shared event bus library.

Typed ``EventBus`` protocol, ``EventEnvelope`` with versioned metadata,
a deterministic ``InProcessEventBus`` adapter for development and test, and
a production ``KafkaEventBus`` adapter backed by Kafka/Redpanda.
"""

from __future__ import annotations

from aqros_events.bus import EventBus, EventHandler
from aqros_events.envelope import EventEnvelope
from aqros_events.errors import (
    EventBusError,
    EventPublishError,
    EventSubscriptionError,
)
from aqros_events.inprocess import InProcessEventBus
from aqros_events.kafka import KafkaEventBus, deserialize_envelope, serialize_envelope
from aqros_events.ulid import ulid

__version__ = "0.1.1"

__all__ = [
    "EventBus",
    "EventBusError",
    "EventEnvelope",
    "EventHandler",
    "EventPublishError",
    "EventSubscriptionError",
    "InProcessEventBus",
    "KafkaEventBus",
    "__version__",
    "deserialize_envelope",
    "serialize_envelope",
    "ulid",
]
