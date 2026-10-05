"""Round-trip tests for the auth Postgres repositories.

These exist because the in-memory adapters store the *live* domain object, so
they cannot catch a serialisation/deserialisation bug. Every one of the bugs
found here only appeared against a real database:

* assigning to ``ApprovalRequest.expires_at``, which is a derived read-only
  property → ``AttributeError`` on every approval lookup;
* losing a non-default TTL, so a request came back with the 24h default.

The tests use a real Postgres via testcontainers and skip when Docker is
unavailable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from aqros_auth.adapters.orm import Base
from aqros_auth.adapters.repository import (
    SqlAlchemyApprovalRepository,
    SqlAlchemyUserRepository,
)
from aqros_auth.domain.approvals import ApprovalRequest, ApprovalStatus
from aqros_auth.domain.policy import Role
from aqros_auth.domain.ports import UserRecord
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    """A real Postgres, because these bugs only appear on a real database.

    Deliberately a *sync* module fixture: pytest-asyncio gives each test its own
    event loop, so a module-scoped async engine would end up bound to a different
    loop than the test ("attached to a different loop").
    """
    import docker

    docker.from_env().ping()
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        yield container.get_connection_url()


@pytest_asyncio.fixture
async def session_factory(postgres_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(bind=engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def approvals(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[SqlAlchemyApprovalRepository]:
    repo = SqlAlchemyApprovalRepository(session_factory)
    yield repo
    # Clean up between tests.
    async with session_factory() as session:
        await session.execute(sql_delete())
        await session.commit()


def sql_delete() -> object:
    from aqros_auth.adapters.orm import ApprovalRequestModel
    from sqlalchemy import delete

    return delete(ApprovalRequestModel)


@pytest_asyncio.fixture
async def users(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[SqlAlchemyUserRepository]:
    repo = SqlAlchemyUserRepository(session_factory)
    yield repo
    from aqros_auth.adapters.orm import UserModel
    from sqlalchemy import delete

    async with session_factory() as session:
        await session.execute(delete(UserModel))
        await session.commit()


def make_request(**overrides: object) -> ApprovalRequest:
    base: dict[str, object] = {
        "request_id": "apr_test_1",
        "action": "promote_model",
        "resource": "momentum_v3",
        "payload": {"to": "paper"},
        "requester_id": "p1",
        "created_at": NOW,
        "ttl": timedelta(hours=24),
    }
    base.update(overrides)
    return ApprovalRequest(**base)  # type: ignore[arg-type]


class TestApprovalRoundTrip:
    async def test_saves_and_reads_back(self, approvals: SqlAlchemyApprovalRepository) -> None:
        """Regression: reading back raised AttributeError.

        ``expires_at`` is a derived property, so assigning it on load crashed
        every approval lookup against a real database.
        """
        request = make_request()
        await approvals.save(request)
        loaded = await approvals.get("apr_test_1")
        assert loaded is not None
        assert loaded.request_id == "apr_test_1"
        assert loaded.action == "promote_model"

    async def test_preserves_non_default_ttl(self, approvals: SqlAlchemyApprovalRepository) -> None:
        """Regression: a 1-hour TTL came back as the 24h default."""
        await approvals.save(make_request(ttl=timedelta(hours=1)))
        loaded = await approvals.get("apr_test_1")
        assert loaded is not None
        assert loaded.ttl == timedelta(hours=1)
        assert loaded.expires_at == loaded.created_at + timedelta(hours=1)

    async def test_preserves_status_and_approver(
        self, approvals: SqlAlchemyApprovalRepository
    ) -> None:
        request = make_request()
        request.approve("c1", NOW + timedelta(minutes=5), reason="reviewed")
        await approvals.save(request)

        loaded = await approvals.get("apr_test_1")
        assert loaded is not None
        assert loaded.status is ApprovalStatus.APPROVED
        assert loaded.approver_id == "c1"

    async def test_preserves_history(self, approvals: SqlAlchemyApprovalRepository) -> None:
        """The audit trail must survive the round trip."""
        request = make_request()
        request.approve("c1", NOW, reason="ok")
        request.execute("c1", NOW)
        await approvals.save(request)

        loaded = await approvals.get("apr_test_1")
        assert loaded is not None
        assert [e.to_status for e in loaded.events] == [
            ApprovalStatus.APPROVED,
            ApprovalStatus.EXECUTED,
        ]

    async def test_loaded_request_is_usable_for_transitions(
        self, approvals: SqlAlchemyApprovalRepository
    ) -> None:
        """A reloaded request must still be able to move through the workflow.

        A load that silently lost fields would leave a request that can be read
        but never approved — the four-eyes workflow would deadlock.
        """
        await approvals.save(make_request())
        loaded = await approvals.get("apr_test_1")
        assert loaded is not None
        loaded.approve("c1", NOW + timedelta(minutes=1), reason="ok")
        assert loaded.status is ApprovalStatus.APPROVED
        assert loaded.approver_id == "c1"

    async def test_append_is_idempotent_on_request_id(
        self, approvals: SqlAlchemyApprovalRepository
    ) -> None:
        """A retried request must not create a duplicate row."""
        request = make_request()
        await approvals.save(request)
        await approvals.save(request)
        assert len(await approvals.list_all()) == 1

    async def test_missing_request_returns_none(
        self, approvals: SqlAlchemyApprovalRepository
    ) -> None:
        assert await approvals.get("apr_does_not_exist") is None


class TestUserRoundTrip:
    async def test_saves_and_reads_back(self, users: SqlAlchemyUserRepository) -> None:
        record = UserRecord(
            principal_id="prn_1",
            username="alice",
            display_name="Alice",
            password_hash="pbkdf2_sha256$1$aa$bb",
            roles=[Role.OPERATOR],
            created_at=NOW,
        )
        await users.create(record)
        loaded = await users.get_by_username("alice")
        assert loaded is not None
        assert loaded.principal_id == "prn_1"
        assert Role.OPERATOR in loaded.roles

    async def test_password_hash_round_trips(self, users: SqlAlchemyUserRepository) -> None:
        """The hash must be stored verbatim — never re-derived or truncated."""
        record = UserRecord(
            principal_id="prn_2",
            username="bob",
            display_name="Bob",
            password_hash="pbkdf2_sha256$600000$deadbeef$cafe",
            roles=[Role.OBSERVER],
            created_at=NOW,
        )
        await users.create(record)
        loaded = await users.get_by_username("bob")
        assert loaded is not None
        assert loaded.password_hash == "pbkdf2_sha256$600000$deadbeef$cafe"

    async def test_exists(self, users: SqlAlchemyUserRepository) -> None:
        record = UserRecord(
            principal_id="prn_3",
            username="carol",
            display_name="Carol",
            password_hash="x",
            roles=[Role.COMMITTEE],
            created_at=NOW,
        )
        await users.create(record)
        assert await users.exists("carol")
        assert not await users.exists("nobody")

    async def test_multiple_roles_round_trip(self, users: SqlAlchemyUserRepository) -> None:
        await users.create(
            UserRecord(
                principal_id="prn_4",
                username="dave",
                display_name="Dave",
                password_hash="x",
                roles=[Role.OPERATOR, Role.RISK_OFFICER],
                created_at=NOW,
            )
        )
        loaded = await users.get_by_username("dave")
        assert loaded is not None
        assert {Role.OPERATOR, Role.RISK_OFFICER} <= set(loaded.roles)
