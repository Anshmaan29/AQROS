"""Tests for the auth domain: policy, tokens, and the four-eyes workflow.

The four-eyes tests are the load-bearing ones. CLAUDE.md §7.3/§7.4 say the AI
may never raise its own risk limits or promote a model to real capital; these
tests are what make that claim enforced rather than aspirational.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from aqros_auth.domain.approvals import (
    ApprovalRequest,
    ApprovalStatus,
    InvalidTransitionError,
    SeparationOfDutiesError,
)
from aqros_auth.domain.policy import (
    APPROVER_ROLES,
    FOUR_EYES_PERMISSIONS,
    Permission,
    Policy,
    Principal,
    Role,
)
from aqros_auth.domain.tokens import (
    TokenError,
    decode_jwt,
    encode_jwt,
    hash_password,
    needs_rehash,
    verify_password,
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
SECRET = "test-secret-that-is-at-least-32-chars-long"


def principal(
    pid: str = "p1", *roles: Role, service: bool = False, active: bool = True
) -> Principal:
    return Principal(
        principal_id=pid,
        display_name=pid,
        roles=frozenset(roles) if roles else frozenset({Role.OBSERVER}),
        is_service=service,
        is_active=active,
    )


# ===========================================================================
# Password hashing
# ===========================================================================
class TestPasswordHashing:
    def test_hash_is_not_the_password(self) -> None:
        digest = hash_password("correct horse battery staple")
        assert "correct horse" not in digest

    def test_verifies_correct_password(self) -> None:
        digest = hash_password("s3cret-passphrase!")
        assert verify_password("s3cret-passphrase!", digest)

    def test_rejects_wrong_password(self) -> None:
        digest = hash_password("s3cret-passphrase!")
        assert not verify_password("wrong-password", digest)

    def test_salt_makes_identical_passwords_differ(self) -> None:
        """Two users with the same password must not share a hash."""
        assert hash_password("same-password!") != hash_password("same-password!")

    def test_rejects_malformed_hash(self) -> None:
        assert not verify_password("x", "garbage")
        assert not verify_password("x", "a$b$c")

    def test_needs_rehash_flags_weak_iterations(self) -> None:
        weak = hash_password("pw", iterations=1000)
        assert needs_rehash(weak)
        assert not needs_rehash(hash_password("pw"))


# ===========================================================================
# JWT
# ===========================================================================
class TestTokens:
    def _token(self, **overrides: object) -> str:
        kwargs: dict[str, object] = {
            "subject": "alice",
            "principal_id": "p1",
            "roles": ["observer"],
            "secret": SECRET,
            "issuer": "aqros-auth",
            "audience": "aqros-platform",
            "ttl": timedelta(hours=1),
            "now": NOW,
        }
        kwargs.update(overrides)
        return encode_jwt(**kwargs)  # type: ignore[arg-type]

    def _decode(self, token: str, now: datetime | None = None) -> object:
        return decode_jwt(
            token,
            secret=SECRET,
            issuer="aqros-auth",
            audience="aqros-platform",
            now=now or NOW,
        )

    def test_round_trip(self) -> None:
        claims = self._decode(self._token())
        assert claims.subject == "alice"
        assert claims.principal_id == "p1"
        assert claims.roles == ("observer",)

    def test_rejects_tampered_signature(self) -> None:
        token = self._token()
        head, body, sig = token.split(".")
        forged = f"{head}.{body}.{'A' * len(sig)}"
        with pytest.raises(TokenError):
            self._decode(forged)

    def test_rejects_wrong_secret(self) -> None:
        token = self._token()
        with pytest.raises(TokenError):
            decode_jwt(
                token,
                secret="a-completely-different-secret-32chars",
                issuer="aqros-auth",
                audience="aqros-platform",
                now=NOW,
            )

    def test_rejects_tampered_payload(self) -> None:
        """Privilege escalation by editing the payload must fail.

        This is the attack that matters: rewrite "observer" to "admin" and keep
        the original signature. It must not verify.
        """
        import base64
        import json

        head, body, sig = self._token().split(".")
        padding = "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body + padding))
        payload["roles"] = ["admin"]
        forged_body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        with pytest.raises(TokenError):
            self._decode(f"{head}.{forged_body}.{sig}")

    def test_rejects_alg_none(self) -> None:
        import base64
        import json

        header = (
            base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).rstrip(b"=").decode()
        )
        body = self._token().split(".")[1]
        with pytest.raises(TokenError):
            self._decode(f"{header}.{body}.x")

    def test_rejects_wrong_issuer(self) -> None:
        with pytest.raises(TokenError):
            decode_jwt(
                self._token(),
                secret=SECRET,
                issuer="somebody-else",
                audience="aqros-platform",
                now=NOW,
            )

    def test_rejects_wrong_audience(self) -> None:
        """A token minted for another service must not be accepted here."""
        with pytest.raises(TokenError):
            decode_jwt(
                self._token(),
                secret=SECRET,
                issuer="aqros-auth",
                audience="some-other-service",
                now=NOW,
            )

    def test_rejects_expired(self) -> None:
        token = self._token(ttl=timedelta(minutes=5))
        with pytest.raises(TokenError):
            self._decode(token, now=NOW + timedelta(minutes=6))

    def test_accepts_just_before_expiry(self) -> None:
        token = self._token(ttl=timedelta(minutes=5))
        assert self._decode(token, now=NOW + timedelta(minutes=4, seconds=59))

    def test_rejects_malformed(self) -> None:
        for bad in ("", "a.b", "a.b.c.d", "not-a-token"):
            with pytest.raises(TokenError):
                self._decode(bad)


# ===========================================================================
# RBAC
# ===========================================================================
class TestPolicy:
    def test_observer_can_read_research(self) -> None:
        assert Policy().can(principal("p1", Role.OBSERVER), Permission.VIEW_RESEARCH)

    def test_observer_cannot_trade(self) -> None:
        decision = Policy().can(principal("p1", Role.OBSERVER), Permission.SUBMIT_ORDER)
        assert not decision.allowed
        assert "lack" in decision.reason

    def test_operator_can_submit_orders(self) -> None:
        assert Policy().can(principal("p1", Role.OPERATOR), Permission.SUBMIT_ORDER)

    def test_deactivated_principal_is_denied(self) -> None:
        p = principal("p1", Role.OPERATOR, active=False)
        decision = Policy().can(p, Permission.SUBMIT_ORDER)
        assert not decision.allowed
        assert "deactivated" in decision.reason

    def test_roles_union(self) -> None:
        p = principal("p1", Role.OBSERVER, Role.OPERATOR)
        perms = Policy().permissions_for(p)
        assert Permission.VIEW_RESEARCH in perms
        assert Permission.SUBMIT_ORDER in perms


class TestFourEyesPolicy:
    """No role may exercise a four-eyes permission by itself."""

    @pytest.mark.parametrize("permission", sorted(FOUR_EYES_PERMISSIONS, key=str))
    @pytest.mark.parametrize("role", sorted(Role, key=str))
    def test_no_role_can_exercise_four_eyes_directly(
        self, permission: Permission, role: Role
    ) -> None:
        decision = Policy().can(principal("p1", role), permission)
        assert not decision.allowed
        assert decision.requires_four_eyes

    def test_admin_cannot_promote_alone(self) -> None:
        """§7.4: even admin cannot self-promote a model to real capital."""
        decision = Policy().can(principal("p1", Role.ADMIN), Permission.PROMOTE_MODEL)
        assert not decision.allowed
        assert decision.requires_four_eyes

    def test_admin_cannot_change_limits_alone(self) -> None:
        """§7.3: risk limits are human-owned and cannot be raised by one actor."""
        decision = Policy().can(principal("p1", Role.ADMIN), Permission.CHANGE_RISK_LIMIT)
        assert not decision.allowed

    def test_non_approver_role_cannot_approve(self) -> None:
        decision = Policy().may_approve(
            principal("o1", Role.OPERATOR),
            Permission.PROMOTE_MODEL,
            requester=principal("p1", Role.OPERATOR),
        )
        assert not decision.allowed
        assert "approver must hold" in decision.reason

    def test_approver_cannot_approve_their_own_request(self) -> None:
        """Even a committee member cannot approve their own request.

        This is the separation-of-duties rule: the approver role alone is not
        enough, the requester and approver must be different people.
        """
        p = principal("c1", Role.COMMITTEE)
        decision = Policy().may_approve(p, Permission.CHANGE_RISK_LIMIT, requester=p)
        assert not decision.allowed
        assert "cannot approve their own" in decision.reason

    def test_admin_is_not_an_approver(self) -> None:
        """Admin cannot approve a four-eyes request; it is not an approver role.

        Otherwise 'admin' would become a backdoor around four-eyes.
        """
        decision = Policy().may_approve(
            principal("a1", Role.ADMIN),
            Permission.PROMOTE_MODEL,
            requester=principal("p1", Role.OPERATOR),
        )
        assert not decision.allowed
        assert Role.ADMIN not in APPROVER_ROLES

    def test_committee_can_approve_someone_elses_request(self) -> None:
        decision = Policy().may_approve(
            principal("c1", Role.COMMITTEE),
            Permission.PROMOTE_MODEL,
            requester=principal("p1", Role.OPERATOR),
        )
        assert decision.allowed

    def test_approver_must_be_distinct(self) -> None:
        p = principal("c1", Role.COMMITTEE)
        decision = Policy().may_approve(p, Permission.PROMOTE_MODEL, requester=p)
        assert not decision.allowed

    def test_non_four_eyes_permission_needs_no_approval(self) -> None:
        decision = Policy().may_approve(
            principal("c1", Role.COMMITTEE),
            Permission.SUBMIT_ORDER,
            requester=principal("p1", Role.OPERATOR),
        )
        assert not decision.allowed
        assert "not a four-eyes permission" in decision.reason

    def test_deactivated_approver_is_denied(self) -> None:
        decision = Policy().may_approve(
            principal("c1", Role.COMMITTEE, active=False),
            Permission.PROMOTE_MODEL,
            requester=principal("p1", Role.OPERATOR),
        )
        assert not decision.allowed


# ===========================================================================
# Approval state machine
# ===========================================================================
def make_request(*, ttl: timedelta = timedelta(hours=24), requester: str = "p1") -> ApprovalRequest:
    return ApprovalRequest(
        request_id="apr_1",
        action="promote_model",
        resource="momentum_v3",
        payload={},
        requester_id=requester,
        created_at=NOW,
        ttl=ttl,
    )


class TestApprovalWorkflow:
    def test_starts_pending(self) -> None:
        assert make_request().status is ApprovalStatus.PENDING

    def test_approve_moves_to_approved(self) -> None:
        req = make_request()
        req.approve("c1", NOW + timedelta(minutes=5))
        assert req.status is ApprovalStatus.APPROVED
        assert req.approver_id == "c1"

    def test_self_approval_is_refused(self) -> None:
        """The single most important assertion in the whole service."""
        req = make_request(requester="p1")
        with pytest.raises(SeparationOfDutiesError):
            req.approve("p1", NOW)
        assert req.status is ApprovalStatus.PENDING

    def test_self_approval_leaves_no_trace_of_approval(self) -> None:
        req = make_request(requester="p1")
        with pytest.raises(SeparationOfDutiesError):
            req.approve("p1", NOW)
        assert req.approver_id is None
        assert req.status is ApprovalStatus.PENDING

    def test_rejection_is_final(self) -> None:
        req = make_request()
        req.reject("c1", NOW, reason="not ready")
        assert req.status is ApprovalStatus.REJECTED
        with pytest.raises(InvalidTransitionError):
            req.approve("c2", NOW)

    def test_execute_requires_approval_first(self) -> None:
        """An unapproved request must never reach execution."""
        req = make_request()
        with pytest.raises(InvalidTransitionError):
            req.execute("c1", NOW)

    def test_approved_then_executed(self) -> None:
        req = make_request()
        req.approve("c1", NOW)
        req.execute("c1", NOW)
        assert req.status is ApprovalStatus.EXECUTED

    def test_terminal_states_are_absorbing(self) -> None:
        for terminal in (
            ApprovalStatus.REJECTED,
            ApprovalStatus.EXECUTED,
            ApprovalStatus.EXPIRED,
            ApprovalStatus.WITHDRAWN,
        ):
            req = make_request()
            if terminal is ApprovalStatus.REJECTED:
                req.reject("c1", NOW, "no")
            elif terminal is ApprovalStatus.EXECUTED:
                req.approve("c1", NOW)
                req.execute("c1", NOW)
            elif terminal is ApprovalStatus.EXPIRED:
                req.expire_if_due(NOW + timedelta(days=2))
            else:
                req.withdraw("p1", NOW)
            assert req.status is terminal
            with pytest.raises(InvalidTransitionError):
                req.approve("c2", NOW)

    def test_double_approval_is_refused(self) -> None:
        req = make_request()
        req.approve("c1", NOW)
        with pytest.raises(InvalidTransitionError):
            req.approve("c2", NOW)

    def test_expiry_blocks_approval(self) -> None:
        req = make_request(ttl=timedelta(hours=1))
        with pytest.raises(InvalidTransitionError):
            req.approve("c1", NOW + timedelta(hours=2))
        assert req.status is ApprovalStatus.EXPIRED

    def test_expire_if_due_is_idempotent(self) -> None:
        req = make_request(ttl=timedelta(hours=1))
        assert req.expire_if_due(NOW + timedelta(hours=2)) is True
        assert req.expire_if_due(NOW + timedelta(hours=3)) is False

    def test_does_not_expire_early(self) -> None:
        req = make_request(ttl=timedelta(hours=24))
        assert req.expire_if_due(NOW + timedelta(hours=1)) is False
        assert req.status is ApprovalStatus.PENDING

    def test_history_is_recorded_for_audit(self) -> None:
        req = make_request()
        req.approve("c1", NOW, reason="reviewed")
        req.execute("c1", NOW)
        assert [e.to_status for e in req.events] == [
            ApprovalStatus.APPROVED,
            ApprovalStatus.EXECUTED,
        ]

    def test_snapshot_is_json_safe(self) -> None:
        import json

        req = make_request()
        req.approve("c1", NOW, reason="ok")
        json.dumps(req.snapshot())  # must not raise
