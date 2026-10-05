"""Tests for the audit ledger service and HTTP surface.

Includes an explicit check that the WORM surface has no write path: if an
update or delete route ever appears, the ledger is no longer append-only and
CLAUDE.md §7.8 is violated. That assertion is the point of this file.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from aqros_audit_ledger.adapters.inmemory import InMemoryLedgerRepository
from aqros_audit_ledger.domain.chain import Outcome
from aqros_audit_ledger.domain.service import AppendRequest, AuditLedgerService
from fastapi.testclient import TestClient

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@dataclass
class Ledger:
    client: TestClient
    service: AuditLedgerService
    repository: InMemoryLedgerRepository


@pytest.fixture
def ledger() -> Iterator[Ledger]:
    from aqros_audit_ledger.app import build_app

    repository = InMemoryLedgerRepository()
    app = build_app(repository=repository)
    service = AuditLedgerService(repository)
    with TestClient(app) as client:
        yield Ledger(client=client, service=service, repository=repository)


def append_request(n: int = 1, **overrides: object) -> AppendRequest:
    base: dict[str, object] = {
        "event_type": "order.filled",
        "actor_id": "alice",
        "action": "submit_order",
        "resource": f"order-{n}",
        "outcome": Outcome.ALLOWED,
        "payload": {"qty": n},
        "correlation_id": "corr-1",
        "entry_id": f"aud_{n:04d}",
    }
    base.update(overrides)
    return AppendRequest(**base)  # type: ignore[arg-type]


# ===========================================================================
# Append and chaining
# ===========================================================================
class TestAppend:
    async def test_append_returns_sealed_entry(self, ledger: Ledger) -> None:
        entry = await ledger.service.append(append_request())
        assert entry.entry_hash != ""
        assert entry.previous_hash == "0" * 64

    async def test_subsequent_entries_link(self, ledger: Ledger) -> None:
        first = await ledger.service.append(append_request(1))
        second = await ledger.service.append(append_request(2))
        assert second.previous_hash == first.entry_hash

    async def test_ledger_verifies_after_appends(self, ledger: Ledger) -> None:
        for i in range(1, 6):
            await ledger.service.append(append_request(i))
        result = await ledger.service.verify()
        assert result.is_intact
        assert result.entries_checked == 5

    async def test_generates_entry_id_when_absent(self, ledger: Ledger) -> None:
        entry = await ledger.service.append(append_request(entry_id=None))
        assert entry.entry_id.startswith("aud_")

    async def test_append_is_idempotent_on_entry_id(self, ledger: Ledger) -> None:
        """A retried append must not fork the chain or duplicate the record."""
        first = await ledger.service.append(append_request(1))
        second = await ledger.service.append(append_request(1))
        assert first.entry_id == second.entry_id
        assert await ledger.service.count() == 1


class TestQuery:
    async def test_filters_by_actor(self, ledger: Ledger) -> None:
        await ledger.service.append(append_request(1, actor_id="alice"))
        await ledger.service.append(append_request(2, actor_id="bob"))
        found = await ledger.service.query(actor_id="bob")
        assert [e.actor_id for e in found] == ["bob"]

    async def test_filters_by_correlation_id(self, ledger: Ledger) -> None:
        await ledger.service.append(append_request(1, correlation_id="c-1"))
        await ledger.service.append(append_request(2, correlation_id="c-2"))
        found = await ledger.service.query(correlation_id="c-2")
        assert len(found) == 1

    async def test_paginates(self, ledger: Ledger) -> None:
        for i in range(1, 6):
            await ledger.service.append(append_request(i))
        page = await ledger.service.query(limit=2, offset=0)
        assert len(page) == 2


# ===========================================================================
# HTTP surface
# ===========================================================================
class TestHttpAppend:
    def test_post_event_returns_201(self, ledger: Ledger) -> None:
        response = ledger.client.post(
            "/v1/audit/events",
            json={
                "event_type": "order.filled",
                "actor_id": "alice",
                "action": "submit_order",
                "resource": "order-1",
                "outcome": "ALLOWED",
                "payload": {"qty": 10},
                "correlation_id": "corr-1",
                "entry_id": "aud_0001",
            },
        )
        assert response.status_code == 201
        body = response.json()
        assert body["entry_hash"]
        assert body["previous_hash"] == "0" * 64

    def test_invalid_outcome_is_422(self, ledger: Ledger) -> None:
        response = ledger.client.post(
            "/v1/audit/events",
            json={
                "event_type": "x",
                "actor_id": "a",
                "action": "b",
                "resource": "c",
                "outcome": "MAYBE",
            },
        )
        assert response.status_code == 422

    def test_repeat_post_does_not_duplicate(self, ledger: Ledger) -> None:
        payload = {
            "event_type": "order.filled",
            "actor_id": "alice",
            "action": "submit_order",
            "resource": "order-1",
            "outcome": "ALLOWED",
            "payload": {"qty": 10},
            "entry_id": "aud_0001",
        }
        first = ledger.client.post("/v1/audit/events", json=payload)
        second = ledger.client.post("/v1/audit/events", json=payload)
        assert first.status_code == 201
        # Same id: the original is returned rather than a duplicate created.
        assert second.json()["entry_hash"] == first.json()["entry_hash"]
        assert ledger.client.get("/v1/audit/stats").json()["total_entries"] == 1


class TestHttpVerify:
    def test_verify_reports_verified(self, ledger: Ledger) -> None:
        for i in range(1, 4):
            ledger.client.post(
                "/v1/audit/events",
                json={
                    "event_type": "x",
                    "actor_id": "a",
                    "action": "b",
                    "resource": f"r{i}",
                    "outcome": "ALLOWED",
                    "entry_id": f"aud_{i:04d}",
                },
            )
        response = ledger.client.get("/v1/audit/verify")
        assert response.status_code == 200
        assert response.json()["status"] == "VERIFIED"
        assert response.json()["entries_checked"] == 3

    def test_verify_on_empty_ledger(self, ledger: Ledger) -> None:
        response = ledger.client.get("/v1/audit/verify")
        assert response.json()["status"] == "EMPTY"

    def test_verify_detects_tampering(self, ledger: Ledger) -> None:
        """Corrupt a stored payload behind the API's back, then verify."""
        from dataclasses import replace

        for i in range(1, 4):
            ledger.client.post(
                "/v1/audit/events",
                json={
                    "event_type": "x",
                    "actor_id": "a",
                    "action": "b",
                    "resource": f"r{i}",
                    "outcome": "ALLOWED",
                    "entry_id": f"aud_{i:04d}",
                },
            )

        # Simulate silent alteration directly in storage.
        entry = ledger.repository._entries["aud_0002"]
        ledger.repository._entries["aud_0002"] = replace(entry, payload={"qty": 99999})

        response = ledger.client.get("/v1/audit/verify")
        body = response.json()
        assert body["status"] == "TAMPERED"
        assert body["first_invalid_entry_id"] == "aud_0002"


class TestWormSurface:
    """CLAUDE.md §7.8: the ledger must have no modification path."""

    def test_no_update_endpoint_exists(self, ledger: Ledger) -> None:
        ledger.client.post(
            "/v1/audit/events",
            json={
                "event_type": "x",
                "actor_id": "a",
                "action": "b",
                "resource": "r",
                "outcome": "ALLOWED",
                "entry_id": "aud_0001",
            },
        )
        for method in ("put", "patch"):
            response = getattr(ledger.client, method)(
                "/v1/audit/events/aud_0001", json={"actor_id": "mallory"}
            )
            assert response.status_code in (
                404,
                405,
            ), f"{method.upper()} must not be supported by the ledger"

    def test_no_delete_endpoint_exists(self, ledger: Ledger) -> None:
        ledger.client.post(
            "/v1/audit/events",
            json={
                "event_type": "x",
                "actor_id": "a",
                "action": "b",
                "resource": "r",
                "outcome": "ALLOWED",
                "entry_id": "aud_0001",
            },
        )
        assert ledger.client.delete("/v1/audit/events/aud_0001").status_code in (404, 405)

    def test_repository_port_has_no_mutation_methods(self) -> None:
        """The WORM guarantee is structural: the port has no update/delete."""
        from aqros_audit_ledger.domain.service import LedgerRepository

        public = {n for n in dir(LedgerRepository) if not n.startswith("_")}
        assert public == {
            "append",
            "count",
            "get",
            "head",
            "list_entries",
            "range_for_verification",
        }
        assert not {"update", "delete", "remove", "truncate"} & public


class TestHealth:
    def test_liveness(self, ledger: Ledger) -> None:
        assert ledger.client.get("/health/live").status_code == 200

    def test_metrics(self, ledger: Ledger) -> None:
        assert "aqros_http_requests_total" in ledger.client.get("/metrics").text
