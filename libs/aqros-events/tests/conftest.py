from __future__ import annotations

import socket

import pytest


def kafka_reachable(host: str = "localhost", port: int = 9092) -> bool:
    """Check if a Kafka broker is reachable via TCP connect."""
    try:
        with socket.create_connection((host, port), timeout=2.0):
            return True
    except (OSError, TimeoutError):
        return False


kafka_unavailable = pytest.mark.skipif(
    not kafka_reachable(),
    reason="Kafka/Redpanda broker not available on localhost:9092",
)
