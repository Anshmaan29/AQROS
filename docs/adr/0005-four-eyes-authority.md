# ADR 0005 — Four-eyes approval lives in auth, and no role can self-approve

- **Status:** Accepted
- **Date:** 2026-10-05
- **Affects:** auth, model-registry, risk-engine, live-trading-engine

## Context

CLAUDE.md forbids the AI from raising its own risk limits (§7.3) and from
promoting a model to real capital (§7.4). But the review found that "four-eyes"
was hand-waved in three different places with **no single authority**: registry
mentioned promotion, risk mentioned limits, notification mentioned arming. None
owned the workflow.

Without one owner, "four-eyes" means four different implementations of a safety
rule, and the weakest one determines the platform's actual safety.

There is also a specific trap: a role hierarchy with an `admin` role normally
implies admin can do anything. If admin can both request and approve, then
four-eyes is theatre.

## Decision

**`auth` is the single authority for identity, RBAC, and the four-eyes workflow.**

1. **Four-eyes permissions are never granted to any role.**
   `CHANGE_RISK_LIMIT`, `PROMOTE_MODEL`, and `ARM_LIVE` are in
   `FOUR_EYES_PERMISSIONS`. `Policy.can()` *always* denies them — they can only
   be exercised through an approval record. This holds for `admin`.

2. **Separation of duties is enforced by identity, not by role.**
   `ApprovalRequest.approve()` raises `SeparationOfDutiesError` if
   `approver_id == requester_id`. Checked before the approver-role check, so the
   error message names the actual problem.

3. **`admin` is deliberately *not* an approver role.**
   `APPROVER_ROLES = {committee}`. Otherwise admin becomes a backdoor around
   four-eyes.

4. **Terminal states are absorbing.** Rejected, executed, expired, and withdrawn
   requests can never transition again, so a withdrawn approval id cannot be
   revived.

5. **Only an APPROVED request may execute.** An unapproved limit change or
   promotion cannot reach the risk kernel even by direct API call.

6. **Other services hold no approval logic.** They consume auth's decision. If
   registry and risk each implemented their own check, the weakest would win.

## Consequences

**Positive**

- "Four-eyes" has one definition and one test suite.
- The dangerous case — a genuine approver rubber-stamping their own request — is
  refused on identity, which no amount of role configuration can bypass.
- Enforcement is a database row, queryable afterwards via the audit ledger.

**Negative**

- An emergency risk-limit change needs two people. That is the point, but it is
  slower, and an on-call procedure must exist for it.
- Bootstrap requires seeding a committee principal from a secrets manager before
  the platform can arm capital. Dev seeds `admin`/`committee` automatically;
  staging/prod must not.
- Two-person approval makes testing awkward; tests use distinct seeded actors.

**Neutral**

- `admin` can still create users, read audit, and disarm/kill. It simply cannot
  approve a four-eyes action, which is the intended ceiling.

## Alternatives rejected

- **Per-service approval implementations.** Three implementations, three
  chances to get the safety rule wrong. Rejected.
- **Grant `admin` the four-eyes permissions and rely on process.** A role that
  *can* self-approve will eventually be used to self-approve under time pressure.
  Rejected.
- **Workflow engine (Temporal/Camunda) for the approval flow.** Better for
  long-running, retry-heavy processes. Overkill for a three-state approval;
  revisited if approvals ever need multi-day escalation or delegation.