"""Guards for Hard Rule §7.1 — one codebase for backtest, paper, and live.

CLAUDE.md §7.1 forbids duplicating strategy/risk/order logic between the
backtest engine and the live engines. That rule decays quietly: a service gains
its own `OrderSide`, adds a value the shared core lacks, and the two drift
apart until a backtest can no longer faithfully replay what live does.

These tests are cheap structural assertions that make the drift loud.
"""

from __future__ import annotations

import ast
import pathlib
from functools import cache

import pytest

from aqros_strategy_core.contracts import OrderSide, OrderType

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

#: Services that place orders and therefore must share the core's vocabulary.
ORDER_PLACING_SERVICES = (
    "backtesting-engine",
    "live-trading-engine",
    "paper-trading-engine",
)


@cache
def _classes_in(path: pathlib.Path) -> dict[str, ast.ClassDef]:
    tree = ast.parse(path.read_text())
    return {n.name: n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}


def _enum_members(node: ast.ClassDef) -> set[str]:
    """Member names of an enum class.

    Handles both plain assignments (``BUY = "buy"``) and annotated ones, since
    the codebase uses both styles.
    """
    members: set[str] = set()
    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            members.add(stmt.target.id)
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    members.add(target.id)
    return members


class TestSharedOrderSide:
    @pytest.mark.parametrize("service", ORDER_PLACING_SERVICES)
    def test_service_order_side_is_identical_to_shared_core(self, service: str) -> None:
        """Any redefinition must match the shared core exactly.

        Divergence here would mean a backtest interprets a side differently from
        live, which is precisely the failure mode §7.1 exists to prevent.
        """
        candidates = list((REPO_ROOT / "backend" / service / "src").rglob("*.py"))
        redefinitions: list[tuple[pathlib.Path, set[str]]] = []
        shared = {m.name for m in OrderSide}

        for path in candidates:
            if "mypy_cache" in str(path):
                continue
            node = _classes_in(path).get("OrderSide")
            if node is not None:
                members = _enum_members(node)
                if members != shared:
                    redefinitions.append((path, members))

        assert not redefinitions, (
            f"{service} redefines OrderSide differently from the shared core "
            f"{sorted(shared)}: " + ", ".join(f"{p.name}={sorted(m)}" for p, m in redefinitions)
        )


class TestSharedOrderType:
    @pytest.mark.parametrize("service", ORDER_PLACING_SERVICES)
    def test_shared_core_covers_every_type_services_support(self, service: str) -> None:
        """The shared core must be a superset of what each service can emit.

        If a service supports an order type the core lacks, a backtest cannot
        replay that order and parity becomes unprovable. This is the invariant
        that was violated for STOP/STOP_LIMIT.
        """
        shared = {m.name for m in OrderType}
        unsupported: dict[str, set[str]] = {}

        for path in (REPO_ROOT / "backend" / service / "src").rglob("*.py"):
            if "mypy_cache" in str(path):
                continue
            node = _classes_in(path).get("OrderType")
            if node is None:
                continue
            extra = _enum_members(node) - shared
            if extra:
                unsupported[path.name] = extra

        assert not unsupported, (
            f"{service} supports order types the shared core cannot express: "
            f"{unsupported}. Add them to aqros_strategy_core instead so backtest, "
            "paper, and live stay in sync (CLAUDE.md §7.1)."
        )

    def test_shared_core_supports_stop_orders(self) -> None:
        """Regression guard for the divergence this suite was written for."""
        assert {"STOP", "STOP_LIMIT"} <= {m.name for m in OrderType}

    def test_stop_price_is_available_on_order_intent(self) -> None:
        from aqros_strategy_core.contracts import OrderIntent

        assert "stop_price" in {f.name for f in OrderIntent.__dataclass_fields__.values()}


class TestSharedCoreIsActuallyShared:
    def test_order_placing_services_depend_on_the_shared_core(self) -> None:
        """A service that places orders must depend on aqros-strategy-core.

        Depending on nothing while reimplementing the contracts is how the
        duplication started.
        """
        offenders = []
        for service in ORDER_PLACING_SERVICES:
            manifest = REPO_ROOT / "backend" / service / "pyproject.toml"
            text = manifest.read_text()
            if "aqros-strategy-core" not in text:
                offenders.append(service)
        assert not offenders, (
            f"these services place orders but do not depend on aqros-strategy-core: "
            f"{offenders}. They will drift from the shared contracts."
        )


class TestNoCrossServiceImports:
    def test_no_service_imports_another_services_internals(self) -> None:
        """CLAUDE.md §7.9 — services talk over APIs, not imports.

        Importing ``aqros_oms.domain`` from another service turns the monorepo
        into a distributed monolith.
        """
        import_names = {
            "api_gateway",
            "audit_ledger",
            "auth",
            "backtesting_engine",
            "dataset_builder",
            "feature_store",
            "inference_service",
            "live_trading",
            "market_data",
            "model_registry",
            "oms",
            "paper_trading",
            "parity_monitor",
            "portfolio",
            "risk_engine",
            "strategy_engine",
            "training_pipeline",
        }
        violations: list[str] = []

        for path in (REPO_ROOT / "backend").rglob("*.py"):
            if "__pycache__" in str(path) or "mypy_cache" in str(path):
                continue
            try:
                tree = ast.parse(path.read_text())
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    head = name.split(".")[0]
                    if head in import_names and head != _own_package(path):
                        violations.append(f"{path.relative_to(REPO_ROOT)} -> {name}")

        assert not violations, (
            "cross-service internal imports found (CLAUDE.md §7.9): " f"{violations}"
        )


def _own_package(path: pathlib.Path) -> str:
    """The aqros package a file belongs to, e.g. ``oms`` for oms/adapters/db.py."""
    parts = path.parts
    if "backend" not in parts:
        return ""
    idx = parts.index("backend")
    service = parts[idx + 1]
    src = parts[idx + 2] if idx + 2 < len(parts) else ""
    if src != "src":
        return ""
    return service.replace("-", "_")
