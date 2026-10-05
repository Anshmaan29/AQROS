"""Tests for the resilient HTTP client: retry policy and circuit breaker.

The critical property here is the money-path rule: a non-idempotent request must
NOT be retried automatically, because a duplicate order is worse than a failed
one (CLAUDE.md §5, §7.7).
"""

from __future__ import annotations

import httpx
import pytest

from aqros_core.http import (
    RETRYABLE_STATUS,
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    ResilientClient,
    RetryPolicy,
    create_async_client,
)


class TestRetryPolicy:
    def test_delay_grows_with_attempt(self) -> None:
        p = RetryPolicy(base_delay=0.1, jitter=False)
        assert p.delay_for(1) == 0.1
        assert p.delay_for(2) == 0.2
        assert p.delay_for(3) == 0.4

    def test_delay_is_capped(self) -> None:
        p = RetryPolicy(base_delay=1.0, max_delay=2.0, jitter=False)
        assert p.delay_for(20) == 2.0

    def test_jitter_stays_within_bound(self) -> None:
        """Full jitter prevents a thundering herd after a shared outage.

        Attempt 3's raw delay is base * 2**2 = 2.0, and jitter samples
        uniformly from [0, raw] — never above it.
        """
        p = RetryPolicy(base_delay=0.5, jitter=True)
        for _ in range(200):
            assert 0.0 <= p.delay_for(3) <= 2.0

    def test_jitter_actually_varies(self) -> None:
        """Constant backoff would defeat the point of jitter."""
        p = RetryPolicy(base_delay=1.0, jitter=True)
        samples = {p.delay_for(4) for _ in range(50)}
        assert len(samples) > 1


class TestCircuitBreaker:
    def test_starts_closed_and_allows(self) -> None:
        b = CircuitBreaker()
        assert b.state is CircuitState.CLOSED
        assert b.allows_request()

    def test_opens_after_threshold(self) -> None:
        b = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            b.record_failure()
        assert b.state is CircuitState.OPEN
        assert not b.allows_request()

    def test_does_not_open_before_threshold(self) -> None:
        b = CircuitBreaker(failure_threshold=3)
        b.record_failure()
        b.record_failure()
        assert b.state is CircuitState.CLOSED
        assert b.allows_request()

    def test_success_resets_failure_run(self) -> None:
        """Failures must be *consecutive*; a success in between clears the run."""
        b = CircuitBreaker(failure_threshold=3)
        b.record_failure()
        b.record_failure()
        b.record_success()
        b.record_failure()
        assert b.state is CircuitState.CLOSED

    def test_half_opens_after_recovery_timeout(self) -> None:
        now = [0.0]
        b = CircuitBreaker(failure_threshold=1, recovery_timeout=30.0)
        b._clock = lambda: now[0]
        b.record_failure()
        assert not b.allows_request()

        now[0] = 31.0
        assert b.allows_request()
        assert b.state is CircuitState.HALF_OPEN

    def test_half_open_admits_only_one_probe(self) -> None:
        """A half-open breaker is a single probe, not a free-for-all."""
        now = [0.0]
        b = CircuitBreaker(failure_threshold=1, recovery_timeout=10.0)
        b._clock = lambda: now[0]
        b.record_failure()
        now[0] = 11.0
        assert b.allows_request() is True
        assert b.allows_request() is False

    def test_failed_probe_reopens(self) -> None:
        now = [0.0]
        b = CircuitBreaker(failure_threshold=1, recovery_timeout=10.0)
        b._clock = lambda: now[0]
        b.record_failure()
        now[0] = 11.0
        b.allows_request()
        b.record_failure()
        assert b.state is CircuitState.OPEN
        assert not b.allows_request()

    def test_successful_probe_closes(self) -> None:
        now = [0.0]
        b = CircuitBreaker(failure_threshold=1, recovery_timeout=10.0)
        b._clock = lambda: now[0]
        b.record_failure()
        now[0] = 11.0
        b.allows_request()
        b.record_success()
        assert b.state is CircuitState.CLOSED
        assert b.allows_request()


class TestResilientClient:
    async def test_get_succeeds(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        async with ResilientClient(httpx.AsyncClient(transport=httpx.MockTransport(handler))) as c:
            r = await c.get("http://x/y")
        assert r.status_code == 200

    async def test_get_retries_transient_status(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(503)
            return httpx.Response(200, json={"ok": True})

        policy = RetryPolicy(max_attempts=3, base_delay=0.0, jitter=False)
        async with ResilientClient(
            httpx.AsyncClient(transport=httpx.MockTransport(handler)), policy=policy
        ) as c:
            r = await c.get("http://x/y")
        assert r.status_code == 200
        assert calls["n"] == 3

    async def test_get_gives_up_after_max_attempts(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(503)

        policy = RetryPolicy(max_attempts=2, base_delay=0.0, jitter=False)
        async with ResilientClient(
            httpx.AsyncClient(transport=httpx.MockTransport(handler)), policy=policy
        ) as c:
            r = await c.get("http://x/y")
        assert r.status_code == 503
        assert calls["n"] == 2

    async def test_post_is_not_retried_by_default(self) -> None:
        """The money-path rule: never auto-retry a state-changing request."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(503)

        policy = RetryPolicy(max_attempts=3, base_delay=0.0, jitter=False)
        async with ResilientClient(
            httpx.AsyncClient(transport=httpx.MockTransport(handler)), policy=policy
        ) as c:
            r = await c.post("http://x/orders", json={"q": 1})
        assert r.status_code == 503
        assert calls["n"] == 1, "POST must be attempted exactly once"

    async def test_post_retries_when_explicitly_allowed(self) -> None:
        """A caller that guarantees its own idempotency key can opt in."""
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(503)

        policy = RetryPolicy(max_attempts=3, base_delay=0.0, jitter=False)
        async with ResilientClient(
            httpx.AsyncClient(transport=httpx.MockTransport(handler)), policy=policy
        ) as c:
            await c.post("http://x/orders", retry_non_idempotent=True)
        assert calls["n"] == 3

    async def test_timeout_is_retried(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            raise httpx.ConnectTimeout("boom")

        policy = RetryPolicy(max_attempts=2, base_delay=0.0, jitter=False)
        async with ResilientClient(
            httpx.AsyncClient(transport=httpx.MockTransport(handler)), policy=policy
        ) as c:
            with pytest.raises(httpx.ConnectTimeout):
                await c.get("http://x/y")
        assert calls["n"] == 2

    async def test_open_circuit_rejects_without_calling_upstream(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(503)

        policy = RetryPolicy(max_attempts=1, base_delay=0.0, jitter=False)
        breaker = CircuitBreaker(failure_threshold=1)
        async with ResilientClient(
            httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            policy=policy,
            breaker=breaker,
        ) as c:
            await c.get("http://x/y")
            assert breaker.state is CircuitState.OPEN
            with pytest.raises(CircuitOpenError):
                await c.get("http://x/y")
        assert calls["n"] == 1, "open breaker must not hit the upstream"

    async def test_client_error_does_not_trip_breaker(self) -> None:
        """A 404 is our fault, not the upstream's — it must not open the circuit."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        breaker = CircuitBreaker(failure_threshold=1)
        async with ResilientClient(
            httpx.AsyncClient(transport=httpx.MockTransport(handler)), breaker=breaker
        ) as c:
            r = await c.get("http://x/y")
        assert r.status_code == 404
        assert breaker.state is CircuitState.CLOSED

    async def test_retryable_statuses_exclude_client_errors(self) -> None:
        assert 400 not in RETRYABLE_STATUS
        assert 404 not in RETRYABLE_STATUS
        assert 503 in RETRYABLE_STATUS
        assert 429 in RETRYABLE_STATUS


class TestClientFactory:
    def test_separates_connect_and_read_timeouts(self) -> None:
        """A single 10s timeout would let a dead host stall for 10s."""
        client = create_async_client(timeout_seconds=10.0, connect_timeout_seconds=3.0)
        assert client.timeout.connect == 3.0
        assert client.timeout.read == 10.0
