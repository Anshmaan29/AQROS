"""Tests for the shared database helpers, especially readiness honesty.

The ``schema`` check exists because a connectivity-only readiness check is
actively dangerous: a service with an unmigrated database answers ``SELECT 1``
happily, reports itself healthy, and then 500s on every real request.
"""

from __future__ import annotations

import inspect
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from aqros_core.db import create_engine, create_session_factory, schema_check, schema_ready
from aqros_core.health import HealthRegistry


class _FakeResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _FakeConnection:
    def __init__(self, scalar: object) -> None:
        self._scalar = scalar

    async def execute(self, *_args: Any, **_kwargs: Any) -> _FakeResult:
        return _FakeResult(self._scalar)

    async def __aenter__(self) -> _FakeConnection:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakeEngine:
    """Minimal stand-in for AsyncEngine.connect()."""

    def __init__(self, scalar: object) -> None:
        self._scalar = scalar
        self.calls = 0

    def connect(self) -> _FakeConnection:
        self.calls += 1
        return _FakeConnection(self._scalar)


class TestSchemaCheckIsReusable:
    """Regression: the check must survive being called repeatedly.

    It was originally registered as an already-created coroutine. The registry
    invokes its check on every probe, so that worked on the first request and
    then raised "cannot reuse already awaited coroutine" forever after — a
    readiness probe that reported healthy once and unhealthy ever since.
    """

    def test_returns_a_callable_not_a_coroutine(self) -> None:
        engine = _FakeEngine(scalar=1)  # type: ignore[arg-type]
        check = schema_check(engine)  # type: ignore[arg-type]
        assert callable(check)
        assert not inspect.iscoroutine(check)

    async def test_can_be_awaited_repeatedly(self) -> None:
        engine = _FakeEngine(scalar=1)  # type: ignore[arg-type]
        check = schema_check(engine)  # type: ignore[arg-type]
        for _ in range(5):
            assert await check() is True  # type: ignore[misc]
        assert engine.calls == 5

    async def test_registry_can_run_it_many_times(self) -> None:
        """The real failure mode: two consecutive readiness probes."""
        engine = _FakeEngine(scalar=1)  # type: ignore[arg-type]
        registry = HealthRegistry()
        registry.register("schema", schema_check(engine))  # type: ignore[arg-type]
        for _ in range(3):
            results = await registry.run()
            assert [r.healthy for r in results] == [True]


class TestSchemaReadyLogic:
    async def test_true_when_alembic_version_present(self) -> None:
        engine = _FakeEngine(scalar=1)
        assert await schema_ready(engine) is True  # type: ignore[arg-type]

    async def test_false_when_table_absent(self) -> None:
        engine = _FakeEngine(scalar=None)
        assert await schema_ready(engine) is False  # type: ignore[arg-type]


# Every AQROS service owns Postgres (CLAUDE.md §3), so the shared factory is
# Postgres-shaped: it always passes pool_size/max_overflow, which SQLAlchemy's
# SQLite StaticPool rejects. Creating an engine does not connect, so no server
# is needed for these tests.
POSTGRES_URL = "postgresql+asyncpg://aqros:aqros@localhost:5432/aqros_test"


class TestEngineFactory:
    def test_create_engine_accepts_a_url(self) -> None:
        engine = create_engine(POSTGRES_URL)
        assert isinstance(engine, AsyncEngine)

    def test_engine_enables_pre_ping(self) -> None:
        """A service idle across a database restart holds dead connections;
        without pre_ping its first real request fails after looking healthy."""
        engine = create_engine(POSTGRES_URL)
        assert engine.pool._pre_ping is True

    def test_session_factory_is_not_autoflushing(self) -> None:
        """Autoflush would fire partial writes inside a transaction and make
        the outbox's atomicity harder to reason about."""
        factory = create_session_factory(create_engine(POSTGRES_URL))
        assert isinstance(factory, async_sessionmaker)
        assert factory.kw.get("autoflush") is False
