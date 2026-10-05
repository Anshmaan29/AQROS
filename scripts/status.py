#!/usr/bin/env python
"""Show the health of every AQROS service in the local stack.

Answers the question you actually have when bringing a stack up: *is it working,
and if not, which piece is broken?* Reads Docker Compose state and the gateway's
aggregate health endpoint rather than guessing from container status.

Usage:
    make status
    uv run python scripts/status.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request

GATEWAY = "http://localhost:8000"


def sh(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def container_states() -> dict[str, str]:
    result = sh(
        "docker",
        "compose",
        "--profile",
        "trading",
        "--profile",
        "backtest",
        "--profile",
        "audit",
        "ps",
        "-a",
        "--format",
        '{"service":"{{.Service}}","state":"{{.State}}"}',
    )
    states: dict[str, str] = {}
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        states[row["service"]] = row["state"]
    return states


def fetch_json(url: str, timeout: float = 8.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None


def main() -> int:
    states = container_states()
    if not states:
        print("No containers found. Is the stack running? Try `make up`.")
        return 1

    platform = fetch_json(f"{GATEWAY}/v1/health/platform")

    # The gateway's aggregate view is authoritative when it is reachable,
    # because it probes each service's real /health/live over the compose network.
    probe: dict[str, dict] = {}
    if platform:
        probe = {s["name"]: s for s in platform.get("services", [])}

    print(f"{'SERVICE':<24} {'CONTAINER':<12} {'HEALTH':<8} NOTES")
    print("-" * 78)

    unhealthy: list[str] = []
    unknown: list[str] = []

    for service in sorted(states):
        state = states[service]
        info = probe.get(service)
        if info is None:
            health = "-"
            note = "(no db) not probed" if service.endswith("-db") else "not probed"
            unknown.append(service)
        elif info["healthy"]:
            health = "ok"
            note = ""
        else:
            health = "FAIL"
            note = info.get("error") or "not responding"
            unhealthy.append(service)

        print(f"{service:<24} {state:<12} {health:<8} {note}")

    print()
    if platform:
        total = platform.get("total", 0)
        healthy = platform.get("healthy", 0)
        print(f"Gateway aggregate: {platform.get('status')}  ({healthy}/{total} services healthy)")
    else:
        print("Gateway aggregate unavailable — is api-gateway running on :8000?")

    if unhealthy:
        print()
        print("Unhealthy services (these are the ones to fix first):")
        for service in unhealthy:
            print(f"  - {service}")

    # The two questions that follow almost every failed `make up`.
    unmigrated = [s for s in states if s.endswith("-db")]
    not_ready = [
        s for s in states if not s.endswith("-db") and s in probe and probe[s]["healthy"] is False
    ]
    if not_ready:
        print()
        print("If a service reports 503 with a failing 'schema' check, run:")
        print("  make migrate")
        del unmigrated

    return 0


if __name__ == "__main__":
    sys.exit(main())
