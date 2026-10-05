"""The gateway's service topology — pure domain logic, no I/O.

The gateway is the single north-south ingress. It holds a registry mapping a
logical service name to the base URL of that service, plus the policy for
*which* services are reachable from outside.

Two rules from the architecture review are encoded here:

* **The trading hot path is never exposed publicly.** Strategy → Risk → OMS →
  Broker runs synchronously and internally; the gateway only serves research
  and control surfaces. ``exposure`` makes that explicit and testable rather
  than a comment somebody eventually ignores.
* **Money-path services are internal-only.** ``risk-engine``, ``oms`` and
  ``live-trading-engine`` carry order state; reaching them from the public
  edge would bypass auth, rate limiting, and audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Exposure(StrEnum):
    """Whether a service may be reached through the public gateway."""

    #: Reachable from outside; authenticated and audited.
    PUBLIC = "public"
    #: Reachable only from inside the cluster (service-to-service).
    INTERNAL = "internal"


class Zone(StrEnum):
    """Deployment zone, used for policy and blast-radius reasoning."""

    RESEARCH = "research"
    INTELLIGENCE = "intelligence"
    DECISION = "decision"
    EXECUTION = "execution"
    CONTROL = "control"


@dataclass(frozen=True)
class ServiceRoute:
    """A routable backend service.

    Attributes:
        name: Logical service name, used as the path prefix (e.g. ``market-data``).
        base_url: Base URL of the upstream service.
        port: Host port the service listens on (documentation/aggregation).
        zone: Deployment zone the service belongs to.
        exposure: Whether the gateway will proxy to it from the public edge.
        description: Human-readable purpose, surfaced by the discovery endpoint.
    """

    name: str
    base_url: str
    port: int
    zone: Zone
    exposure: Exposure
    description: str


# The topology of the platform. Kept as module-level data so it is greppable,
# testable, and cannot drift between the gateway and the docs by accident.
ROUTES: tuple[ServiceRoute, ...] = (
    # --- Research zone: read-heavy, safe to expose to authenticated users ---
    ServiceRoute(
        "market-data",
        "http://market-data:8002",
        8002,
        Zone.RESEARCH,
        Exposure.PUBLIC,
        "OHLCV bar ingestion and point-in-time market data.",
    ),
    ServiceRoute(
        "feature-store",
        "http://feature-store:8003",
        8003,
        Zone.RESEARCH,
        Exposure.PUBLIC,
        "Offline/online feature definitions and point-in-time feature values.",
    ),
    ServiceRoute(
        "dataset-builder",
        "http://dataset-builder:8008",
        8008,
        Zone.RESEARCH,
        Exposure.PUBLIC,
        "Research dataset and label construction (X/y matrices).",
    ),
    ServiceRoute(
        "training-pipeline",
        "http://training-pipeline:8009",
        8009,
        Zone.RESEARCH,
        Exposure.PUBLIC,
        "Model training, validation, and experiment runs.",
    ),
    ServiceRoute(
        "backtesting-engine",
        "http://backtesting-engine:8010",
        8010,
        Zone.RESEARCH,
        Exposure.PUBLIC,
        "Deterministic historical replay and the validation gauntlet.",
    ),
    # --- Intelligence zone ---
    ServiceRoute(
        "model-registry",
        "http://model-registry:8004",
        8004,
        Zone.INTELLIGENCE,
        Exposure.PUBLIC,
        "Model registry, promotion workflow, and signed artifacts.",
    ),
    ServiceRoute(
        "inference-service",
        "http://inference-service:8014",
        8014,
        Zone.INTELLIGENCE,
        Exposure.PUBLIC,
        "Model serving and batched/streaming inference.",
    ),
    ServiceRoute(
        "strategy-engine",
        "http://strategy-engine:8011",
        8011,
        Zone.INTELLIGENCE,
        Exposure.PUBLIC,
        "Strategy evaluation producing advisory signals.",
    ),
    # --- Decision zone: INTERNAL. Reached by the sync order path, not the edge. ---
    ServiceRoute(
        "risk-engine",
        "http://risk-engine:8005",
        8005,
        Zone.DECISION,
        Exposure.INTERNAL,
        "Sovereign risk kernel and pre-trade checks. Never public.",
    ),
    ServiceRoute(
        "portfolio",
        "http://portfolio:8006",
        8006,
        Zone.DECISION,
        Exposure.INTERNAL,
        "Authoritative positions, P&L, and exposure book.",
    ),
    # --- Execution zone: INTERNAL. Money path; internal sync calls only. ---
    ServiceRoute(
        "oms",
        "http://oms:8015",
        8015,
        Zone.EXECUTION,
        Exposure.INTERNAL,
        "Order management: lifecycle, idempotency, broker reconciliation.",
    ),
    ServiceRoute(
        "live-trading-engine",
        "http://live-trading-engine:8013",
        8013,
        Zone.EXECUTION,
        Exposure.INTERNAL,
        "Broker execution, routing, kill switch. Never public.",
    ),
    ServiceRoute(
        "paper-trading-engine",
        "http://paper-trading-engine:8012",
        8012,
        Zone.EXECUTION,
        Exposure.INTERNAL,
        "Simulated exchange used for the paper-trading rung.",
    ),
    ServiceRoute(
        "parity-monitor",
        "http://parity-monitor:8021",
        8021,
        Zone.EXECUTION,
        Exposure.INTERNAL,
        "Live-vs-paper and backtest-vs-paper divergence monitoring.",
    ),
    # --- Control zone: the trust-gated surfaces. ---
    ServiceRoute(
        "auth",
        "http://auth:8001",
        8001,
        Zone.CONTROL,
        Exposure.PUBLIC,
        "Identity, RBAC, and the four-eyes approval workflow.",
    ),
    ServiceRoute(
        "audit-ledger",
        "http://audit-ledger:8007",
        8007,
        Zone.CONTROL,
        Exposure.INTERNAL,
        "Append-only WORM audit ledger (hash-chained).",
    ),
)


class RouteRegistry:
    """Name → route lookup over the platform topology.

    Kept as a class (rather than free functions over a dict) so the gateway can
    be constructed with an explicit topology in tests instead of reaching for a
    module-level global.
    """

    def __init__(self, routes: tuple[ServiceRoute, ...] = ROUTES) -> None:
        self._routes = {route.name: route for route in routes}

    def __contains__(self, name: object) -> bool:
        return name in self._routes

    def get(self, name: str) -> ServiceRoute | None:
        """Return the route for ``name``, or None if unknown."""
        return self._routes.get(name)

    def all(self) -> tuple[ServiceRoute, ...]:
        """Return every route, in declaration order."""
        return tuple(self._routes.values())

    def public(self) -> tuple[ServiceRoute, ...]:
        """Return only routes the gateway will proxy from the public edge."""
        return tuple(r for r in self._routes.values() if r.exposure is Exposure.PUBLIC)

    def internal(self) -> tuple[ServiceRoute, ...]:
        """Return only routes restricted to internal (service-to-service) calls."""
        return tuple(r for r in self._routes.values() if r.exposure is Exposure.INTERNAL)
