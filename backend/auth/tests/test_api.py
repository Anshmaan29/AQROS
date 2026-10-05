"""Integration tests for the auth HTTP surface.

These run against the real app with in-memory repositories (no Postgres needed)
and exercise the whole path: login → bearer token → RBAC check → four-eyes
approval. The money-path invariants are asserted through HTTP, because that is
where a caller would actually hit them.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import anyio
import pytest
from aqros_auth.adapters.inmemory import (
    InMemoryApprovalRepository,
    InMemoryUserRepository,
    RecordingAuditSink,
)
from aqros_auth.domain.service import AuthService, TokenSettings
from fastapi.testclient import TestClient

SECRET = "test-secret-that-is-at-least-32-chars-long"
PASSWORD = "correct-horse-battery-staple"


@dataclass
class Stack:
    """The app plus the collaborators a test needs to inspect."""

    client: TestClient
    service: AuthService
    audit: RecordingAuditSink
    users: InMemoryUserRepository
    approvals: InMemoryApprovalRepository


def build_stack() -> Stack:
    """Build an app wired to in-memory repositories, with actors seeded."""
    from aqros_auth.app import _build_app
    from aqros_auth.config import Settings

    settings = Settings(environment="test", jwt_secret=SECRET)
    users = InMemoryUserRepository()
    approvals = InMemoryApprovalRepository()
    audit = RecordingAuditSink()
    service = AuthService(
        users=users,
        approvals=approvals,
        audit=audit,
        token_settings=TokenSettings(
            secret=SECRET,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
        ),
    )

    app = _build_app(
        users=users,
        approvals=approvals,
        audit_sink=audit,
        bootstrap_users=False,
    )

    # Seed the actors the four-eyes scenarios need.
    async def seed() -> None:
        for username, role in (("alice", "operator"), ("carol", "committee"), ("root", "admin")):
            await service.create_user(
                username=username,
                display_name=username,
                password=PASSWORD,
                roles=[role],
            )

    anyio.run(seed)

    client = TestClient(app)
    return Stack(client=client, service=service, audit=audit, users=users, approvals=approvals)


@pytest.fixture
def stack() -> Iterator[Stack]:
    s = build_stack()
    with s.client:
        yield s


def token_for(stack: Stack, username: str) -> dict[str, str]:
    """Log in and return the Authorization header."""
    response = stack.client.post(
        "/v1/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# ===========================================================================
# Login and identity
# ===========================================================================
class TestLogin:
    def test_valid_credentials_return_token(self, stack: Stack) -> None:
        response = stack.client.post(
            "/v1/auth/login", json={"username": "alice", "password": PASSWORD}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "Bearer"
        assert body["access_token"]
        assert "operator" in body["roles"]

    def test_wrong_password_is_401(self, stack: Stack) -> None:
        response = stack.client.post(
            "/v1/auth/login", json={"username": "alice", "password": "wrong"}
        )
        assert response.status_code == 401

    def test_unknown_user_is_401_with_same_message(self, stack: Stack) -> None:
        """The message must not reveal whether the username exists."""
        unknown = stack.client.post(
            "/v1/auth/login", json={"username": "nobody", "password": "whatever"}
        )
        wrong = stack.client.post(
            "/v1/auth/login", json={"username": "alice", "password": "whatever"}
        )
        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json()["detail"] == wrong.json()["detail"]

    def test_login_is_audited(self, stack: Stack) -> None:
        token_for(stack, "alice")
        assert any(e["event_type"] == "auth.login" for e in stack.audit.events)

    def test_failed_login_is_audited_as_denied(self, stack: Stack) -> None:
        stack.client.post("/v1/auth/login", json={"username": "alice", "password": "no"})
        denials = [e for e in stack.audit.events if e.get("outcome") == "DENIED"]
        assert denials


class TestMe:
    def test_returns_own_permissions(self, stack: Stack) -> None:
        response = stack.client.get("/v1/auth/me", headers=token_for(stack, "alice"))
        assert response.status_code == 200
        body = response.json()
        assert "trading:submit_order" in body["permissions"]

    def test_requires_authentication(self, stack: Stack) -> None:
        assert stack.client.get("/v1/auth/me").status_code == 401

    def test_rejects_garbage_token(self, stack: Stack) -> None:
        response = stack.client.get("/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
        assert response.status_code == 401

    def test_rejects_wrong_scheme(self, stack: Stack) -> None:
        response = stack.client.get("/v1/auth/me", headers={"Authorization": "Basic abc"})
        assert response.status_code == 401


class TestAuthorizationCheck:
    def test_operator_may_submit_orders(self, stack: Stack) -> None:
        response = stack.client.post(
            "/v1/authz/check",
            json={"principal_id": "self", "permission": "trading:submit_order"},
            headers=token_for(stack, "alice"),
        )
        assert response.status_code == 200
        assert response.json()["allowed"] is True

    def test_four_eyes_permission_reports_requiring_approval(self, stack: Stack) -> None:
        """The answer must explain the wall, not just deny."""
        headers = token_for(stack, "carol")
        response = stack.client.post(
            "/v1/authz/check",
            json={"principal_id": "self", "permission": "model:promote"},
            headers=headers,
        )
        body = response.json()
        assert body["allowed"] is False
        assert body["requires_four_eyes"] is True
        assert "four-eyes" in body["reason"]

    def test_unknown_permission_is_422(self, stack: Stack) -> None:
        response = stack.client.post(
            "/v1/authz/check",
            json={"principal_id": "self", "permission": "not:a:permission"},
            headers=token_for(stack, "alice"),
        )
        assert response.status_code == 422


class TestUserManagement:
    def test_admin_can_create_user(self, stack: Stack) -> None:
        response = stack.client.post(
            "/v1/users",
            json={
                "username": "dave",
                "display_name": "Dave",
                "password": PASSWORD,
                "roles": ["observer"],
            },
            headers=token_for(stack, "root"),
        )
        assert response.status_code == 201
        assert response.json()["username"] == "dave"

    def test_response_never_includes_password_hash(self, stack: Stack) -> None:
        response = stack.client.post(
            "/v1/users",
            json={
                "username": "erin",
                "display_name": "Erin",
                "password": PASSWORD,
                "roles": ["observer"],
            },
            headers=token_for(stack, "root"),
        )
        assert "password" not in response.text
        assert "hash" not in response.text.lower()

    def test_operator_cannot_create_user(self, stack: Stack) -> None:
        response = stack.client.post(
            "/v1/users",
            json={
                "username": "frank",
                "display_name": "Frank",
                "password": PASSWORD,
                "roles": ["admin"],
            },
            headers=token_for(stack, "alice"),
        )
        assert response.status_code == 403

    def test_unknown_role_is_422(self, stack: Stack) -> None:
        response = stack.client.post(
            "/v1/users",
            json={
                "username": "grace",
                "display_name": "Grace",
                "password": PASSWORD,
                "roles": ["superuser"],
            },
            headers=token_for(stack, "root"),
        )
        assert response.status_code == 422

    def test_duplicate_username_is_409(self, stack: Stack) -> None:
        response = stack.client.post(
            "/v1/users",
            json={
                "username": "alice",
                "display_name": "Alice2",
                "password": PASSWORD,
                "roles": ["observer"],
            },
            headers=token_for(stack, "root"),
        )
        assert response.status_code == 409


# ===========================================================================
# Four-eyes approvals — the reason this service exists
# ===========================================================================
class TestFourEyesOverHTTP:
    def _open_request(self, stack: Stack, username: str = "alice") -> dict[str, Any]:
        response = stack.client.post(
            "/v1/approvals",
            json={
                "action": "promote_model",
                "resource": "momentum_v3",
                "payload": {"from": "research", "to": "paper"},
                "reason": "passed validation gauntlet",
            },
            headers=token_for(stack, username),
        )
        assert response.status_code == 201, response.text
        return response.json()

    def test_request_starts_pending(self, stack: Stack) -> None:
        body = self._open_request(stack)
        assert body["status"] == "PENDING"
        assert body["requester_id"]

    def test_requester_cannot_approve_their_own_request(self, stack: Stack) -> None:
        """The core four-eyes guarantee, over HTTP.

        Note the requester here is an *approver* (carol holds `committee`). The
        dangerous case is not "an operator trying to self-approve" — they are
        denied for lacking the role anyway — it is a genuine approver deciding
        their own request deserves a second pair of eyes.
        """
        body = self._open_request(stack, "carol")
        headers = token_for(stack, "carol")
        response = stack.client.post(
            f"/v1/approvals/{body['request_id']}/approve",
            json={"reason": "approving my own request"},
            headers=headers,
        )
        # 403, not 409: this is a policy denial, not a state conflict.
        assert response.status_code == 403
        assert "own request" in response.json()["detail"]

    def test_operator_cannot_self_approve(self, stack: Stack) -> None:
        """An operator is denied too — on role grounds, before any state changes."""
        body = self._open_request(stack, "alice")
        response = stack.client.post(
            f"/v1/approvals/{body['request_id']}/approve",
            json={"reason": "self approve"},
            headers=token_for(stack, "alice"),
        )
        assert response.status_code == 403
        assert (
            stack.client.get(
                f"/v1/approvals/{body['request_id']}", headers=token_for(stack, "alice")
            ).json()["status"]
            == "PENDING"
        )

    def test_distinct_approver_can_approve(self, stack: Stack) -> None:
        body = self._open_request(stack, "alice")
        response = stack.client.post(
            f"/v1/approvals/{body['request_id']}/approve",
            json={"reason": "reviewed"},
            headers=token_for(stack, "carol"),
        )
        assert response.status_code == 200
        assert response.json()["status"] == "APPROVED"

    def test_non_approver_role_cannot_approve(self, stack: Stack) -> None:
        """An operator lacks the approver role entirely."""
        body = self._open_request(stack, "carol")
        response = stack.client.post(
            f"/v1/approvals/{body['request_id']}/approve",
            json={"reason": "looks fine to me"},
            headers=token_for(stack, "alice"),
        )
        assert response.status_code == 403
        assert "approver must hold" in response.json()["detail"]

    def test_cannot_execute_before_approval(self, stack: Stack) -> None:
        body = self._open_request(stack, "alice")
        response = stack.client.post(
            f"/v1/approvals/{body['request_id']}/execute",
            json={"reason": "executing early"},
            headers=token_for(stack, "carol"),
        )
        assert response.status_code == 409
        assert "APPROVED" in response.json()["detail"]

    def test_full_happy_path_request_approve_execute(self, stack: Stack) -> None:
        body = self._open_request(stack, "alice")
        request_id = body["request_id"]
        approved = stack.client.post(
            f"/v1/approvals/{request_id}/approve",
            json={"reason": "reviewed"},
            headers=token_for(stack, "carol"),
        )
        assert approved.json()["status"] == "APPROVED"

        executed = stack.client.post(
            f"/v1/approvals/{request_id}/execute",
            json={"reason": "promoting"},
            headers=token_for(stack, "carol"),
        )
        assert executed.status_code == 200
        final = executed.json()
        assert final["status"] == "EXECUTED"
        assert len(final["history"]) == 2

    def test_rejection_is_final(self, stack: Stack) -> None:
        body = self._open_request(stack, "alice")
        request_id = body["request_id"]
        rejected = stack.client.post(
            f"/v1/approvals/{request_id}/reject",
            json={"reason": "validation not sufficient"},
            headers=token_for(stack, "carol"),
        )
        assert rejected.json()["status"] == "REJECTED"

        again = stack.client.post(
            f"/v1/approvals/{request_id}/approve",
            json={"reason": "changed my mind"},
            headers=token_for(stack, "carol"),
        )
        assert again.status_code == 409

    def test_execute_after_rejection_is_refused(self, stack: Stack) -> None:
        body = self._open_request(stack, "alice")
        request_id = body["request_id"]
        stack.client.post(
            f"/v1/approvals/{request_id}/reject",
            json={"reason": "no"},
            headers=token_for(stack, "carol"),
        )
        response = stack.client.post(
            f"/v1/approvals/{request_id}/execute",
            json={"reason": "sneaking it through"},
            headers=token_for(stack, "carol"),
        )
        assert response.status_code == 409

    def test_unknown_request_is_404(self, stack: Stack) -> None:
        response = stack.client.post(
            "/v1/approvals/apr_does_not_exist/approve",
            json={"reason": "x"},
            headers=token_for(stack, "carol"),
        )
        assert response.status_code == 404

    def test_approval_requires_authentication(self, stack: Stack) -> None:
        body = self._open_request(stack, "alice")
        response = stack.client.post(
            f"/v1/approvals/{body['request_id']}/approve", json={"reason": "x"}
        )
        assert response.status_code == 401

    def test_requester_can_see_their_own_request(self, stack: Stack) -> None:
        body = self._open_request(stack, "alice")
        response = stack.client.get(
            f"/v1/approvals/{body['request_id']}", headers=token_for(stack, "alice")
        )
        assert response.status_code == 200

    def test_approver_can_see_the_request(self, stack: Stack) -> None:
        body = self._open_request(stack, "alice")
        response = stack.client.get(
            f"/v1/approvals/{body['request_id']}", headers=token_for(stack, "carol")
        )
        assert response.status_code == 200

    def test_decisions_are_audited(self, stack: Stack) -> None:
        body = self._open_request(stack, "alice")
        stack.client.post(
            f"/v1/approvals/{body['request_id']}/approve",
            json={"reason": "ok"},
            headers=token_for(stack, "carol"),
        )
        assert stack.audit.of_type("approval.approved")


# ===========================================================================
# Config guard
# ===========================================================================
class TestConfigGuard:
    def test_refuses_placeholder_secret_outside_dev(self) -> None:
        """A known signing secret outside dev must stop the service starting."""
        from aqros_auth.config import Settings
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Settings(
                environment="prod",
                jwt_secret="dev-only-insecure-secret-change-me-32chars",
            )

    def test_rejects_short_secret(self) -> None:
        from aqros_auth.config import Settings
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Settings(jwt_secret="too-short")

    def test_allows_strong_secret_in_prod(self) -> None:
        from aqros_auth.config import Settings

        assert Settings(environment="prod", jwt_secret="a" * 48).environment.value == "prod"
