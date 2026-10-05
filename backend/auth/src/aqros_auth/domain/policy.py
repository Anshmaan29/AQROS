"""Roles, permissions, and the authorization decision.

Pure domain: no I/O, no framework. The permission model is deliberately small
and explicit rather than a general policy language — an auditable table is the
point when the thing being authorized is *"may this human arm real capital?"*.

Two invariants from CLAUDE.md are enforced here, not merely documented:

* **Hard Rules §7.3/§7.4 — nobody can raise their own limits or self-promote.**
  Permission to *change* a risk limit and permission to *promote a model* are
  both four-eyes actions, so no single role (not even ``admin``) holds them
  alone. :func:`requires_four_eyes` marks them.
* **Separation of duties.** The role that *requests* an action must never be the
  role that *approves* it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Role(StrEnum):
    """Platform roles.

    Roles are coarse by design. Fine-grained per-object ACLs are a V2 concern;
    what matters now is that the dangerous actions are impossible for one person.
    """

    #: Read-only access to research and control surfaces.
    OBSERVER = "observer"
    #: May run research: backtests, dataset builds, training runs.
    RESEARCHER = "researcher"
    #: May operate the system: place/cancel orders, arm the kill switch.
    OPERATOR = "operator"
    #: Reviews risk decisions and may veto. Cannot originate trades.
    RISK_OFFICER = "risk-officer"
    #: Approves model promotion and risk-limit changes (four-eyes).
    COMMITTEE = "committee"
    #: Platform administration. Still cannot self-approve a four-eyes action.
    ADMIN = "admin"


class Permission(StrEnum):
    """Individual authorizable actions."""

    VIEW_RESEARCH = "research:read"
    RUN_BACKTEST = "research:backtest"
    BUILD_DATASET = "research:dataset"
    TRAIN_MODEL = "research:train"
    REGISTER_MODEL = "research:register"

    VIEW_SIGNALS = "strategy:view_signals"
    SUBMIT_ORDER = "trading:submit_order"
    CANCEL_ORDER = "trading:cancel_order"
    ARM_LIVE = "trading:arm_live"
    DISARM_LIVE = "trading:disarm_live"
    KILL_SWITCH = "trading:kill_switch"

    VIEW_RISK = "risk:read"
    VETO_TRADE = "risk:veto"
    # Four-eyes: changes a human-owned hard limit (§7.3).
    CHANGE_RISK_LIMIT = "risk:change_limit"
    # Four-eyes: promotes a model to a higher trust rung (§7.4).
    PROMOTE_MODEL = "model:promote"

    READ_AUDIT = "audit:read"
    APPEND_AUDIT = "audit:append"
    MANAGE_USERS = "identity:manage_users"


#: Permissions that can never be exercised by a single actor. Approving one of
#: these requires two distinct principals (§7.3, §7.4).
FOUR_EYES_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.CHANGE_RISK_LIMIT,
        Permission.PROMOTE_MODEL,
        Permission.ARM_LIVE,
    }
)


# Role → permissions. Read this table as the platform's authorization matrix.
ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OBSERVER: frozenset(
        {
            Permission.VIEW_RESEARCH,
            Permission.VIEW_SIGNALS,
            Permission.VIEW_RISK,
            Permission.READ_AUDIT,
        }
    ),
    Role.RESEARCHER: frozenset(
        {
            Permission.VIEW_RESEARCH,
            Permission.RUN_BACKTEST,
            Permission.BUILD_DATASET,
            Permission.TRAIN_MODEL,
            Permission.REGISTER_MODEL,
            Permission.VIEW_SIGNALS,
            Permission.READ_AUDIT,
        }
    ),
    # An operator may act on the system but may NOT approve their own request:
    # OPERATOR holds no four-eyes permission.
    Role.OPERATOR: frozenset(
        {
            Permission.VIEW_RESEARCH,
            Permission.VIEW_SIGNALS,
            Permission.SUBMIT_ORDER,
            Permission.CANCEL_ORDER,
            Permission.DISARM_LIVE,
            Permission.KILL_SWITCH,
            Permission.VIEW_RISK,
        }
    ),
    Role.RISK_OFFICER: frozenset(
        {
            Permission.VIEW_RESEARCH,
            Permission.VIEW_SIGNALS,
            Permission.VIEW_RISK,
            Permission.VETO_TRADE,
            Permission.KILL_SWITCH,
            Permission.READ_AUDIT,
        }
    ),
    Role.COMMITTEE: frozenset(
        {
            Permission.VIEW_RESEARCH,
            Permission.VIEW_RISK,
            Permission.CHANGE_RISK_LIMIT,
            Permission.PROMOTE_MODEL,
            Permission.ARM_LIVE,
            Permission.READ_AUDIT,
        }
    ),
    Role.ADMIN: frozenset(
        {
            Permission.VIEW_RESEARCH,
            Permission.VIEW_SIGNALS,
            Permission.VIEW_RISK,
            Permission.MANAGE_USERS,
            Permission.READ_AUDIT,
            Permission.APPEND_AUDIT,
            Permission.DISARM_LIVE,
            Permission.KILL_SWITCH,
        }
    ),
}

#: Roles allowed to approve a four-eyes request. Kept separate from the
#: permission table so the *approver* set can be audited on its own.
APPROVER_ROLES: frozenset[Role] = frozenset({Role.COMMITTEE})


@dataclass(frozen=True)
class Principal:
    """An authenticated actor (a human or a service)."""

    principal_id: str
    display_name: str
    roles: frozenset[Role]
    is_service: bool = False
    is_active: bool = True

    def has_role(self, role: Role) -> bool:
        return role in self.roles

    @property
    def is_human(self) -> bool:
        return not self.is_service


@dataclass(frozen=True)
class AuthorizationDecision:
    """The result of an authorization check."""

    allowed: bool
    reason: str
    requires_four_eyes: bool = False

    def __bool__(self) -> bool:
        return self.allowed


@dataclass(frozen=True)
class Policy:
    """The platform authorization policy.

    Constructed from the module-level tables so tests can build a restricted
    policy without mutating globals.
    """

    role_permissions: dict[Role, frozenset[Permission]] = field(
        default_factory=lambda: dict(ROLE_PERMISSIONS)
    )
    four_eyes_permissions: frozenset[Permission] = FOUR_EYES_PERMISSIONS
    approver_roles: frozenset[Role] = APPROVER_ROLES

    def permissions_for(self, principal: Principal) -> frozenset[Permission]:
        """Union of permissions across a principal's roles."""
        granted: set[Permission] = set()
        for role in principal.roles:
            granted |= self.role_permissions.get(role, frozenset())
        return frozenset(granted)

    def requires_four_eyes(self, permission: Permission) -> bool:
        """Whether exercising this permission needs a second, distinct approver."""
        return permission in self.four_eyes_permissions

    def can(self, principal: Principal, permission: Permission) -> AuthorizationDecision:
        """Whether ``principal`` may exercise ``permission`` directly.

        A four-eyes permission is never granted by this method — it must go
        through an approval record instead. Returning "allowed" here would let a
        single caller self-approve the exact actions the Hard Rules forbid.
        """
        if not principal.is_active:
            return AuthorizationDecision(
                allowed=False, reason="principal is deactivated", requires_four_eyes=False
            )

        if self.requires_four_eyes(permission):
            return AuthorizationDecision(
                allowed=False,
                reason=(
                    f"{permission} requires four-eyes approval " "(a second, distinct approver)"
                ),
                requires_four_eyes=True,
            )

        if permission not in self.permissions_for(principal):
            return AuthorizationDecision(
                allowed=False,
                reason=f"role(s) {sorted(r.value for r in principal.roles)} " f"lack {permission}",
                requires_four_eyes=False,
            )

        return AuthorizationDecision(allowed=True, reason="granted", requires_four_eyes=False)

    def may_approve(
        self,
        approver: Principal,
        permission: Permission,
        requester: Principal,
    ) -> AuthorizationDecision:
        """Whether ``approver`` may approve ``requester``'s request.

        Enforces three things at once:

        1. the approver holds an approver role;
        2. the permission is genuinely a four-eyes permission (otherwise no
           approval record is needed in the first place);
        3. approver and requester are **different principals** — the separation
           of duties that makes four-eyes mean anything.
        """
        if not approver.is_active:
            return AuthorizationDecision(False, "approver is deactivated")

        if not self.requires_four_eyes(permission):
            return AuthorizationDecision(
                False, f"{permission} is not a four-eyes permission; no approval needed"
            )

        # Separation of duties is checked *before* the approver-role check: a
        # requester attempting to approve their own request should be told
        # exactly that, rather than a less informative "you lack a role".
        if approver.principal_id == requester.principal_id:
            return AuthorizationDecision(
                False,
                "separation of duties: a principal cannot approve their own request",
                requires_four_eyes=True,
            )

        if not (approver.roles & self.approver_roles):
            return AuthorizationDecision(
                False,
                f"approver must hold one of {sorted(r.value for r in self.approver_roles)}",
            )

        return AuthorizationDecision(
            allowed=True, reason="approved by a distinct approver", requires_four_eyes=True
        )
