"""Correlation-ID generation, extraction, and propagation.

CLAUDE.md §5 requires a correlation ID threaded through every request and
decision across services. This module is the single implementation so the ID
is generated and echoed identically everywhere rather than per-service guesswork.

The ID is carried in the standard ``X-Correlation-ID`` header. Incoming IDs are
validated before being echoed back: the value is logged and forwarded to
internal services, so accepting arbitrary attacker-controlled text would let a
caller inject newlines or megabytes into log lines and downstream headers.
"""

from __future__ import annotations

import re
import uuid

CORRELATION_HEADER = "X-Correlation-ID"

# Conservative allowlist: hex (UUIDs, trace ids) plus the ``-_`` separators some
# tracing systems use. Anything else is replaced rather than trusted.
_VALID_ID = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


def new_correlation_id() -> str:
    """Generate a fresh correlation ID."""
    return uuid.uuid4().hex


def is_valid(correlation_id: str | None) -> bool:
    """Whether ``correlation_id`` is safe to log and forward downstream."""
    if correlation_id is None:
        return False
    return bool(_VALID_ID.match(correlation_id))


def sanitize(correlation_id: str | None) -> str | None:
    """Return a safe correlation ID, or None if the input is unusable.

    An oversized or malformed incoming ID is dropped (the caller then mints a
    fresh one) rather than truncated, so the same value is never half-echoed
    to one service and half-echoed to another.
    """
    if correlation_id is None:
        return None
    candidate = correlation_id.strip()
    if not is_valid(candidate):
        return None
    return candidate


def resolve(incoming: str | None) -> str:
    """Resolve the correlation ID for a request.

    Reuses a valid inbound ID so a trace survives across the gateway, and mints
    a new one otherwise.
    """
    return sanitize(incoming) or new_correlation_id()
