"""Auth service use cases: authenticate, authorize, and run four-eyes approvals.

This is the application layer. It orchestrates the pure domain (policy, tokens,
approvals) and the ports (repositories, audit sink). No I/O of its own.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from aqros_auth.domain.approvals import (
    ApprovalRequest,
    ApprovalStatus,
    SeparationOfDutiesError,
)
from aqros_auth.domain.policy import (
    AuthorizationDecision,
    Permission,
    Policy,
    Principal,
    Role,
)
from aqros_auth.domain.ports import ApprovalRepository, AuditSink, UserRecord, UserRepository
from aqros_auth.domain.tokens import (
    TokenClaims,
    TokenError,
    decode_jwt,
    encode_jwt,
    verify_password,
)


class AuthError(Exception):
    """Authentication failed. Deliberately vague to the caller."""


class UserNotFoundError(AuthError):
    """The named user does not exist."""


class UserExistsError(AuthError):
    """The username is already taken."""


class InvalidCredentialsError(AuthError):
    """Username/password did not match."""


@dataclass(frozen=True)
class IssuedToken:
    """A freshly issued access token."""

    token: str
    expires_at: datetime
    principal: Principal

    def to_response(self) -> dict[str, object]:
        return {
            "access_token": self.token,
            "token_type": "Bearer",
            "expires_at": self.expires_at.isoformat(),
            "principal_id": self.principal.principal_id,
            "roles": sorted(r.value for r in self.principal.roles),
        }


@dataclass(frozen=True)
class TokenSettings:
    """Token issuance parameters."""

    secret: str
    issuer: str
    audience: str
    ttl: timedelta = timedelta(hours=8)

    def __post_init__(self) -> None:
        if len(self.secret) < 32:
            # Fail fast at construction rather than issuing weak tokens later.
            raise ValueError("token secret must be at least 32 characters")


class AuthService:
    """Application service for identity and authorization."""

    def __init__(
        self,
        users: UserRepository,
        approvals: ApprovalRepository,
        audit: AuditSink,
        token_settings: TokenSettings,
        policy: Policy | None = None,
    ) -> None:
        self._users = users
        self._approvals = approvals
        self._audit = audit
        self._tokens = token_settings
        self._policy = policy if policy is not None else Policy()

    # -- Identity ----------------------------------------------------------
    async def authenticate(self, username: str, password: str) -> IssuedToken:
        """Verify credentials and issue a token.

        Failure paths are deliberately indistinguishable to the caller: an
        unknown user and a wrong password both raise ``InvalidCredentialsError``, so
        the endpoint cannot be used to enumerate usernames.
        """
        record = await self._users.get_by_username(username)
        if record is None:
            # Still spend a hash to avoid a timing oracle on username existence.
            verify_password(password, _DUMMY_HASH)
            raise InvalidCredentialsError("invalid username or password")
        if not record.is_active:
            raise InvalidCredentialsError("invalid username or password")
        if not verify_password(password, record.password_hash):
            raise InvalidCredentialsError("invalid username or password")

        issued = self.issue_token(record.to_principal())
        return issued

    def issue_token(self, principal: Principal, now: datetime | None = None) -> IssuedToken:
        """Issue an access token for an already-authenticated principal."""
        current = now if now is not None else datetime.now(UTC)
        token = encode_jwt(
            subject=principal.display_name,
            principal_id=principal.principal_id,
            roles=sorted(r.value for r in principal.roles),
            secret=self._tokens.secret,
            issuer=self._tokens.issuer,
            audience=self._tokens.audience,
            ttl=self._tokens.ttl,
            is_service=principal.is_service,
            now=current,
        )
        return IssuedToken(token=token, expires_at=current + self._tokens.ttl, principal=principal)

    async def authenticate_token(self, token: str, now: datetime | None = None) -> Principal:
        """Resolve a bearer token to a principal, re-checking the stored record.

        Validating the signature alone is not enough: the user may have been
        deactivated after the token was issued, and a deactivated principal must
        not keep acting.

        Raises:
            AuthError: for any unusable credential — malformed, mis-signed,
                expired, unknown principal, or deactivated. Callers map this to
                401; letting ``TokenError`` escape would surface a 500 and tell
                an attacker they found an unhandled path.
        """
        try:
            claims = self.verify_token(token, now=now)
        except TokenError as exc:
            raise AuthError("invalid or expired token") from exc

        record = await self._users.get_by_principal_id(claims.principal_id)
        if record is None:
            raise AuthError("token references an unknown principal")
        if not record.is_active:
            raise AuthError("principal is deactivated")
        return record.to_principal()

    def verify_token(self, token: str, now: datetime | None = None) -> TokenClaims:
        """Verify a token's signature, issuer, audience, and expiry."""
        return decode_jwt(
            token,
            secret=self._tokens.secret,
            issuer=self._tokens.issuer,
            audience=self._tokens.audience,
            now=now,
        )

    # -- Users -------------------------------------------------------------
    async def create_user(
        self,
        *,
        username: str,
        display_name: str,
        password: str,
        roles: list[Role],
        is_service: bool = False,
        actor: Principal | None = None,
    ) -> UserRecord:
        """Create a principal.

        Args:
            roles: Role values. Strings are accepted and coerced so callers
                (the API layer) can pass request payloads directly.

        Raises:
            ValueError: on a weak password or an unknown role name.
        """
        from aqros_auth.domain.tokens import hash_password

        if not is_service and len(password) < 12:
            # Short passwords are the single most common cause of account
            # compromise; reject at creation rather than at breach.
            raise ValueError("password must be at least 12 characters")

        resolved: list[Role] = []
        for role in roles:
            # Accept a Role or its string value; reject anything else loudly so
            # a typo'd role cannot silently grant nothing.
            resolved.append(role if isinstance(role, Role) else Role(role))

        # Check before hashing: PBKDF2 is deliberately expensive, and a
        # duplicate-username probe should not cost a full key-derivation.
        if await self._users.exists(username):
            raise UserExistsError(f"username already taken: {username}")

        record = UserRecord(
            principal_id=f"prn_{uuid.uuid4().hex[:16]}",
            username=username,
            display_name=display_name,
            password_hash=hash_password(password),
            roles=resolved,
            is_service=is_service,
            created_at=datetime.now(UTC),
        )
        return await self._users.create(record)

    async def list_users(self) -> list[UserRecord]:
        return await self._users.list_all()

    # -- Authorization -----------------------------------------------------
    def authorize(self, principal: Principal, permission: Permission) -> AuthorizationDecision:
        """Decide whether a principal may exercise a permission directly."""
        return self._policy.can(principal, permission)

    def permissions_of(self, principal: Principal) -> frozenset[Permission]:
        return self._policy.permissions_for(principal)

    # -- Four-eyes approvals ----------------------------------------------
    async def request_approval(
        self,
        *,
        requester: Principal,
        action: str,
        resource: str,
        payload: dict[str, object],
        ttl: timedelta = timedelta(hours=24),
        now: datetime | None = None,
    ) -> ApprovalRequest:
        """Open a four-eyes approval request.

        The requester must be allowed to *ask* for the action even though they
        cannot perform it alone — otherwise no request could ever be opened.
        """
        created = now if now is not None else datetime.now(UTC)
        request = ApprovalRequest(
            request_id=f"apr_{uuid.uuid4().hex[:16]}",
            action=action,
            resource=resource,
            payload=dict(payload),
            requester_id=requester.principal_id,
            created_at=created,
            ttl=ttl,
        )
        return await self._approvals.save(request)

    async def approve_request(
        self,
        *,
        request_id: str,
        approver: Principal,
        reason: str = "",
        now: datetime | None = None,
    ) -> ApprovalRequest:
        """Approve a pending request. Enforces four-eyes end to end."""
        current = now if now is not None else datetime.now(UTC)
        request = await self._require_request(request_id)

        decision = self._policy.may_approve(
            approver,
            _permission_for_action(request.action),
            requester=Principal(
                principal_id=request.requester_id,
                display_name="",
                roles=frozenset(),
            ),
        )
        if not decision.allowed:
            raise SeparationOfDutiesError(decision.reason)

        request.approve(approver.principal_id, current, reason)
        return await self._approvals.save(request)

    async def reject_request(
        self,
        *,
        request_id: str,
        actor: Principal,
        reason: str,
        now: datetime | None = None,
    ) -> ApprovalRequest:
        current = now if now is not None else datetime.now(UTC)
        request = await self._require_request(request_id)
        request.reject(actor.principal_id, current, reason)
        return await self._approvals.save(request)

    async def execute_approved(
        self,
        *,
        request_id: str,
        actor: Principal,
        reason: str = "",
        now: datetime | None = None,
    ) -> ApprovalRequest:
        """Mark an approved request executed.

        Only APPROVED requests execute. This is the check that stops an
        unapproved limit change or promotion from reaching the risk kernel.
        """
        current = now if now is not None else datetime.now(UTC)
        request = await self._require_request(request_id)
        if request.status is not ApprovalStatus.APPROVED:
            raise ValueError(
                f"only an APPROVED request can execute (status={request.status.value})"
            )
        request.execute(actor.principal_id, current, reason)
        return await self._approvals.save(request)

    async def get_approval(self, request_id: str) -> ApprovalRequest | None:
        return await self._approvals.get(request_id)

    async def list_approvals(self, status: ApprovalStatus | None = None) -> list[ApprovalRequest]:
        if status is None:
            return await self._approvals.list_all()
        return await self._approvals.list_by_status(status)

    async def _require_request(self, request_id: str) -> ApprovalRequest:
        request = await self._approvals.get(request_id)
        if request is None:
            raise UserNotFoundError(f"approval request not found: {request_id}")
        return request


def _permission_for_action(action: str) -> Permission:
    """Map an approval action string to the permission it exercises."""
    mapping = {
        "change_risk_limit": Permission.CHANGE_RISK_LIMIT,
        "promote_model": Permission.PROMOTE_MODEL,
        "arm_live": Permission.ARM_LIVE,
    }
    return mapping.get(action, Permission.CHANGE_RISK_LIMIT)


# Used to burn a comparable amount of CPU on the unknown-user path so response
# time does not reveal whether a username exists.
_DUMMY_HASH = (
    "pbkdf2_sha256$600000$00000000000000000000000000000000$"
    "0000000000000000000000000000000000000000000000000000000000000000"
)
