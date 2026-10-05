#!/usr/bin/env python
"""Apply Alembic migrations to AQROS services.

Migrations are NOT run automatically at container startup. That is a deliberate
choice — running DDL on every boot means a rolling deploy can race itself, and a
bad migration takes the service down before its health check can report the
problem. Instead you run this explicitly:

    make migrate            # all DB-owning services that are up
    make migrate SERVICE=x  # just one

Every command runs *inside the container* and reaches its database over the
compose network. This matters on a developer machine that already runs Postgres
on 5432: the published host port may point at your local install rather than the
container's database, and migrations would silently land in the wrong database.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

# Services that own a database and ship migrations, in dependency order.
SERVICES = (
    "market-data",
    "feature-store",
    "dataset-builder",
    "training-pipeline",
    "model-registry",
    "backtesting-engine",
    "risk-engine",
    "portfolio",
    "auth",
    "audit-ledger",
    "oms",
    "live-trading-engine",
)


def sh(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def service_is_up(service: str) -> bool:
    """Whether the compose service exists and has a running container."""
    ps = sh("docker", "compose", "ps", "--status", "running", "--format", "{{.Service}}")
    if ps.returncode != 0:
        return False
    return service in ps.stdout.split()


def migrate(service: str, downgrade: bool = False) -> bool:
    """Apply (or roll back) migrations for one service.

    Uses ``docker compose run --rm`` rather than ``exec``: migrations must be
    applicable while the service is still DOWN, because several services seed
    rows during startup and crash-loop against an empty schema. A one-off
    container uses the same image and the same compose network, so it reaches
    the database without the service running and without publishing a host port.
    """
    verb = "downgrade base" if downgrade else "upgrade head"
    result = sh(
        "docker",
        "compose",
        "run",
        "--rm",
        "--no-deps",
        "--entrypoint",
        "sh",
        service,
        "-c",
        # Explicit venv python: `python -m alembic` can resolve to the system
        # interpreter depending on how the shell is invoked, and the system
        # python has no alembic.
        f"cd /app/backend/{service} && /app/.venv/bin/python -m alembic {verb}",
    )
    if result.returncode == 0:
        print(f"  ok     {service:<24} {verb}")
        return True

    print(f"  FAIL   {service:<24} {verb}")
    for line in (result.stdout + result.stderr).splitlines()[-6:]:
        if line.strip():
            print(f"         {line}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", help="migrate a single service")
    parser.add_argument(
        "--downgrade",
        action="store_true",
        help="roll back to base instead of upgrading (destructive)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="only report which services are up and have migrations",
    )
    args = parser.parse_args()

    if sh("docker", "compose", "ps").returncode != 0:
        print("docker compose is not available or the stack is not initialised", file=sys.stderr)
        return 2

    targets = (args.service,) if args.service else SERVICES

    if args.check:
        print("service                     status")
        for service in targets:
            print(f"{service:<26} {'up' if service_is_up(service) else 'not running'}")
        return 0

    if args.downgrade:
        print("DESTRUCTIVE: rolling databases back to base.\n")

    print("Applying migrations:\n")
    failures = [s for s in targets if not migrate(s, args.downgrade)]

    print()
    if failures:
        print(f"Failed: {', '.join(failures)}")
        return 1
    print("All targeted services migrated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
