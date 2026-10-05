"""Resilient HTTP client: timeout + idempotent retry + circuit breaker.

CLAUDE.md §5 requires *every* external call to have a timeout, a retry policy
(for idempotent operations), and a circuit breaker. The architecture review
found those were hand-rolled ad hoc in each service. This is the single
implementation.

Why the breaker defaults to **closed** but trips fast: an alpha-path call
(feature store, inference) should degrade rather than fail the request, while a
money-path call (risk, OMS) should fail closed. Callers get the distinction via
:attr:`CircuitState.is_open` rather than by catching a special exception, which
keeps the money-path decision in the caller's domain code.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx

# Status codes worth retrying: transient server-side and rate-limit failures.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised when a call is rejected because the breaker is open."""


@dataclass
class CircuitBreaker:
    """Consecutive-failure circuit breaker.

    Trips after ``failure_threshold`` consecutive failures, then stays open for
    ``recovery_timeout`` seconds before allowing a single probe through
    (half-open). The probe decides whether to close again or re-open.

    The breaker is intentionally *not* thread- or process-safe beyond asyncio:
    it protects one process's outbound calls, which is where it belongs. A
    fleet-wide breaker would be a shared store (Redis) — see the review's
    note on that being a later step.
    """

    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    half_open_max_calls: int = 1

    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _consecutive_failures: int = field(default=0, init=False)
    _opened_at: float = field(default=0.0, init=False)
    _half_open_calls: int = field(default=0, init=False)
    _clock: Any = None

    @property
    def state(self) -> CircuitState:
        return self._state

    def _now(self) -> float:
        # time.monotonic, not the event loop clock: the breaker must keep working
        # outside a running loop (and asyncio.get_event_loop is deprecated when
        # there is none).
        return self._clock() if self._clock is not None else time.monotonic()

    def allows_request(self) -> bool:
        """Whether a call may proceed right now."""
        if self._state is CircuitState.CLOSED:
            return True
        if self._state is CircuitState.OPEN:
            if self._now() - self._opened_at >= self.recovery_timeout:
                # Cooldown elapsed: let one probe through to test the waters.
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
            else:
                return False
        if self._half_open_calls >= self.half_open_max_calls:
            return False
        self._half_open_calls += 1
        return True

    def record_success(self) -> None:
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._half_open_calls = 0

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._state is CircuitState.HALF_OPEN:
            # The probe failed: go straight back to open and restart the cooldown.
            self._state = CircuitState.OPEN
            self._opened_at = self._now()
            self._half_open_calls = 0
            return
        if self._consecutive_failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = self._now()

    def reset(self) -> None:
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._half_open_calls = 0


@dataclass
class RetryPolicy:
    """Exponential backoff with full jitter.

    Full jitter (``uniform(0, delay)``) rather than a fixed delay, so a fleet of
    services recovering from the same upstream outage does not resynchronise
    into a thundering herd.
    """

    max_attempts: int = 3
    base_delay: float = 0.1
    max_delay: float = 5.0
    jitter: bool = True

    def delay_for(self, attempt: int) -> float:
        """Delay before retry ``attempt`` (1-based; attempt 1 has no delay)."""
        raw = min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))
        return random.uniform(0.0, raw) if self.jitter else raw


def create_async_client(
    timeout_seconds: float = 10.0,
    *,
    connect_timeout_seconds: float = 3.0,
    max_connections: int = 50,
) -> httpx.AsyncClient:
    """Create an ``httpx.AsyncClient`` with explicit, bounded timeouts.

    A single global timeout is the usual httpx footgun: a 10s connect timeout
    means a dead host stalls the request for 10s. Connect and read timeouts are
    separated here so a hung connection fails fast while a slow-but-alive
    upstream still gets its budget.
    """
    timeout = httpx.Timeout(
        timeout_seconds,
        connect=connect_timeout_seconds,
    )
    limits = httpx.Limits(
        max_connections=max_connections, max_keepalive_connections=max_connections // 2
    )
    return httpx.AsyncClient(timeout=timeout, limits=limits)


class ResilientClient:
    """``httpx.AsyncClient`` with retry + circuit breaker + correlation-ID echo.

    Wraps rather than subclasses, so the full ``httpx`` API stays available.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        policy: RetryPolicy | None = None,
        breaker: CircuitBreaker | None = None,
        correlation_header: str = "X-Correlation-ID",
    ) -> None:
        self._client = client if client is not None else create_async_client()
        self._owns_client = client is None
        self.policy = policy if policy is not None else RetryPolicy()
        self.breaker = breaker if breaker is not None else CircuitBreaker()
        self.correlation_header = correlation_header

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> ResilientClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Perform a request with retries and breaker protection.

        Retries only idempotent methods by default. A non-idempotent request
        (POST to create an order) is *not* retried automatically unless the
        caller passes ``retry_non_idempotent=True``: CLAUDE.md §5 requires
        idempotency on anything that mutates money state, so an automatic
        retry here could duplicate an order.
        """
        retry_non_idempotent = bool(kwargs.pop("retry_non_idempotent", False))
        idempotent = method.upper() in {"GET", "HEAD", "OPTIONS", "TRACE"} or retry_non_idempotent
        max_attempts = self.policy.max_attempts if idempotent else 1

        if not self.breaker.allows_request():
            raise CircuitOpenError(f"circuit open for {url}")

        last_error: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                response = await self._client.request(method, url, **kwargs)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt == max_attempts:
                    self.breaker.record_failure()
                    raise
                await asyncio.sleep(self.policy.delay_for(attempt))
                continue

            if response.status_code in RETRYABLE_STATUS and attempt < max_attempts:
                await asyncio.sleep(self.policy.delay_for(attempt))
                continue

            if response.status_code >= 500:
                self.breaker.record_failure()
            else:
                self.breaker.record_success()
            return response

        self.breaker.record_failure()
        raise last_error if last_error is not None else RuntimeError("request failed")

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)
