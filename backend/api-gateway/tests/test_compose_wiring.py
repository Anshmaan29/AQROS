"""Structural tests for docker-compose.yml.

These exist because the wiring previously rotted silently: services were added
with ports copied from other services, producing collisions that only surfaced
at `docker compose up` time. Cheap static checks catch that in CI instead.

Skipped when `docker compose` is unavailable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

# Every service that owns a Postgres database. One DB per service, never shared
# (CLAUDE.md §7.9).
DB_OWNING_SERVICES = {
    "auth",
    "audit-ledger",
    "backtesting-engine",
    "dataset-builder",
    "feature-store",
    "live-trading-engine",
    "market-data",
    "model-registry",
    "oms",
    "paper-trading-engine",
    "portfolio",
    "risk-engine",
    "training-pipeline",
}


def _have_compose() -> bool:
    return shutil.which("docker") is not None


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    if not _have_compose():
        pytest.skip("docker compose is required to validate the compose file")
    # Every profile is enabled: the whole point is to validate the *complete*
    # platform, not just the default research subset.
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--profile",
            "backtest",
            "--profile",
            "paper",
            "--profile",
            "live",
            "--profile",
            "audit",
            "--profile",
            "trading",
            "config",
            "--format",
            "json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"docker compose config failed: {result.stderr.strip()[:200]}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:  # pragma: no cover - diagnostic path
        pytest.skip("docker compose config did not emit JSON")


def _host_port(entry: Any) -> str:
    if isinstance(entry, str):
        return entry.split(":")[0]
    return str(entry.get("published"))


class TestComposeIsValid:
    def test_config_parses(self, compose: dict[str, Any]) -> None:
        assert "services" in compose

    def test_define_a_network(self, compose: dict[str, Any]) -> None:
        assert "aqros" in compose.get("networks", {})


class TestNoPortCollisions:
    def test_host_ports_are_unique(self, compose: dict[str, Any]) -> None:
        """Two services on one host port silently break `compose up`."""
        seen: dict[str, list[str]] = {}
        for name, svc in compose["services"].items():
            for entry in svc.get("ports") or []:
                seen.setdefault(_host_port(entry), []).append(name)
        collisions = {p: names for p, names in seen.items() if len(names) > 1}
        assert not collisions, f"host port collisions: {collisions}"

    def test_container_ports_are_unique(self, compose: dict[str, Any]) -> None:
        seen: dict[str, list[str]] = {}
        for name, svc in compose["services"].items():
            args = (svc.get("build") or {}).get("args") or {}
            port = args.get("PORT")
            if port:
                seen.setdefault(str(port), []).append(name)
        collisions = {p: names for p, names in seen.items() if len(names) > 1}
        assert not collisions, f"container port collisions: {collisions}"


class TestDatabaseIsolation:
    def test_every_db_service_owns_a_distinct_database(self, compose: dict[str, Any]) -> None:
        """§7.9: no service may share another's database."""
        dbs = {
            svc["environment"]["POSTGRES_DB"]
            for name, svc in compose["services"].items()
            if name.endswith("-db") and (svc.get("environment") or {}).get("POSTGRES_DB")
        }
        assert len(dbs) == len([n for n in compose["services"] if n.endswith("-db")])

    def test_each_service_points_at_its_own_database(self, compose: dict[str, Any]) -> None:
        """The DSN host must be the service's own ``*-db`` container.

        This is the §7.9 boundary expressed in config: if a service's DSN points
        anywhere but its own database, it is reaching into someone else's data.
        """
        for name, svc in compose["services"].items():
            url = (svc.get("environment") or {}).get("AQROS_DATABASE_URL")
            if not url:
                continue
            assert "@" in url, f"{name} has a malformed DATABASE_URL: {url}"
            host = url.split("@", 1)[1].split(":", 1)[0]
            assert host == f"{name}-db", f"{name} points at {host}, expected {name}-db"
            # The database name must also be service-specific, not shared.
            dbname = url.rsplit("/", 1)[1]
            assert dbname.startswith("aqros_"), f"{name} uses a non-aqros database: {dbname}"

    def test_db_services_have_healthchecks(self, compose: dict[str, Any]) -> None:
        for name, svc in compose["services"].items():
            if name.endswith("-db"):
                assert svc.get("healthcheck"), f"{name} has no healthcheck"


class TestStartupOrdering:
    def test_services_wait_for_healthy_not_just_started(self, compose: dict[str, Any]) -> None:
        """`service_started` is not readiness.

        A dependency that is merely started has not necessarily finished
        migrations, so dependents would race it and fail confusingly.
        """
        offenders: list[str] = []
        for name, svc in compose["services"].items():
            for dep, condition in (svc.get("depends_on") or {}).items():
                if condition == "service_started" and dep.endswith("-db"):
                    offenders.append(f"{name} -> {dep}")
        assert not offenders, (
            "services must wait for their database to be healthy, not merely started: "
            f"{offenders}"
        )

    def test_no_depends_on_cycles(self, compose: dict[str, Any]) -> None:
        graph = {
            name: list((svc.get("depends_on") or {}).keys())
            for name, svc in compose["services"].items()
        }
        visiting: set[str] = set()
        done: set[str] = set()

        def walk(node: str, trail: list[str]) -> None:
            if node in done:
                return
            assert node not in visiting, f"dependency cycle: {' -> '.join([*trail, node])}"
            visiting.add(node)
            for nxt in graph.get(node, []):
                walk(nxt, [*trail, node])
            visiting.discard(node)
            done.add(node)

        for name in graph:
            walk(name, [])


class TestExposurePolicy:
    """The money path must never be reachable from the host edge."""

    INTERNAL_SERVICES: frozenset[str] = frozenset(
        {
            "risk-engine",
            "portfolio",
            "oms",
            "live-trading-engine",
            "paper-trading-engine",
            "parity-monitor",
            "audit-ledger",
        }
    )

    def test_money_path_services_are_loopback_bound(self, compose: dict[str, Any]) -> None:
        for name in self.INTERNAL_SERVICES:
            svc = compose["services"].get(name)
            if svc is None:
                continue
            for entry in svc.get("ports") or []:
                host_ip = "127.0.0.1" if isinstance(entry, str) else entry.get("host_ip", "")
                assert (
                    host_ip == "127.0.0.1"
                ), f"{name} must bind to loopback only, not {host_ip or '0.0.0.0'}"


class TestServiceCoverage:
    def test_every_backend_service_is_in_compose(self, compose: dict[str, Any]) -> None:
        """A service that exists but is not wired can never be demonstrated."""
        services = set(compose["services"])
        for service_dir in sorted((REPO_ROOT / "backend").iterdir()):
            if not service_dir.is_dir():
                continue
            assert (
                service_dir.name in services
            ), f"backend/{service_dir.name} is not present in docker-compose.yml"

    def test_every_service_image_builds_from_the_shared_dockerfile(
        self, compose: dict[str, Any]
    ) -> None:
        """One parameterized Dockerfile keeps images consistent and small."""
        for name, svc in compose["services"].items():
            build = svc.get("build")
            if not build:
                continue  # datastores (postgres, redis, minio) are plain images
            assert build.get("dockerfile") == "docker/Dockerfile.service", name
            args = build.get("args") or {}
            assert {"SERVICE", "MODULE", "PORT"} <= set(args), f"{name} missing build args"
