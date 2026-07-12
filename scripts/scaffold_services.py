#!/usr/bin/env python3
"""Scaffold generator for AQROS backend services and top-level area folders.

This is a developer tool (not application logic). Each backend service is a
thin, uniform FastAPI package built from ``aqros_core.create_app`` exposing only
health endpoints in Phase 0. To add a new service, add an entry to ``SERVICES``
and re-run::

    python scripts/scaffold_services.py

Existing files are overwritten so the scaffold stays canonical; hand-written
business logic (added in later phases) lives in ``api/``/``domain/``/``adapters/``
which the generator does not touch beyond creating empty package markers.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# name -> (port, one-line description)
SERVICES: dict[str, tuple[int, str]] = {
    "api-gateway": (8000, "Single ingress; routing, auth handoff, rate limits."),
    "auth": (8001, "Identity for humans (OIDC) and services (mTLS); RBAC."),
    "market-data": (8002, "Vendor/venue feed boundary; normalize and publish ticks."),
    "feature-store": (8003, "Point-in-time feature serving (offline + online)."),
    "model-registry": (8004, "Versioned, governed store of models and lineage."),
    "risk-engine": (8005, "Pre-trade checks and the sovereign hard risk kernel."),
    "portfolio": (8006, "Positions, P&L accounting, and optimization."),
    "audit-ledger": (8007, "Append-only, tamper-evident record of every action."),
}

# Top-level domain areas (Python/logic areas, not runnable services in Phase 0).
AREAS: dict[str, str] = {
    "agents": "The cognitive layer: perception, analysts, PM, risk-critic, reflection.",
    "models": "Model code and specs (weights live in object storage).",
    "datasets": "Dataset, feature, and label DEFINITIONS (not raw data).",
    "training": "Training pipelines, HPO, and the validation harness.",
    "backtesting": "Backtest engine + cost simulator (drives the shared core).",
    "execution": "OMS, EMS, and broker/venue adapters (the money path).",
    "research": "Notebooks, hypotheses, and the negative-results log.",
    "frontend": "React/TypeScript UIs (research, control, admin).",
    "infra": "Terraform / IaC and environment definitions (secret references only).",
    "kubernetes": "Helm / Kustomize deployment manifests.",
    "monitoring": "Prometheus rules and Grafana dashboards (as code).",
}


def _module(name: str) -> str:
    return "aqros_" + name.replace("-", "_")


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  wrote {path.relative_to(ROOT)}")


def scaffold_service(name: str, port: int, description: str) -> None:
    module = _module(name)
    base = ROOT / "backend" / name
    pkg = base / "src" / module

    _write(
        base / "pyproject.toml",
        f"""[project]
name = "aqros-{name}"
version = "0.1.0"
description = "AQROS {name} service — {description}"
requires-python = ">=3.12"
dependencies = ["aqros-core"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/{module}"]

[tool.uv.sources]
aqros-core = {{ workspace = true }}
""",
    )

    _write(
        base / "README.md",
        f"# {name}\n\n{description}\n\n"
        f"Phase 0: health endpoints only (`/health`, `/health/live`, `/health/ready`). "
        f"Business logic is added in later phases under `api/`, `domain/`, `adapters/`.\n\n"
        f"Run locally: `uv run python -m {module}.main` (listens on port {port}).\n",
    )

    _write(pkg / "__init__.py", f'"""AQROS {name} service."""\n\n__version__ = "0.1.0"\n')

    _write(
        pkg / "config.py",
        f'''"""Configuration for the {name} service."""

from __future__ import annotations

from aqros_core.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    """{name} settings (override defaults via AQROS_* env vars)."""

    service_name: str = "{name}"
    port: int = {port}
''',
    )

    _write(
        pkg / "app.py",
        f'''"""ASGI application for the {name} service (health-only in Phase 0)."""

from __future__ import annotations

from aqros_core.app import create_app

from {module}.config import Settings

settings = Settings()
app = create_app(settings)
''',
    )

    _write(
        pkg / "main.py",
        f'''"""Entrypoint: run the {name} service with uvicorn."""

from __future__ import annotations

import uvicorn

from {module}.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run("{module}.app:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
''',
    )

    _write(pkg / "py.typed", "")
    for sub in ("api", "domain", "adapters"):
        _write(
            pkg / sub / "__init__.py",
            f'"""{sub} layer for the {name} service (populated in later phases)."""\n',
        )

    _write(base / "migrations" / ".gitkeep", "")
    _write(
        base / "tests" / "test_health.py",
        f'''"""Health endpoint tests for the {name} service."""

from __future__ import annotations

from fastapi.testclient import TestClient

from {module}.app import app

client = TestClient(app)


def test_liveness() -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"


def test_readiness() -> None:
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["service"] == "{name}"
''',
    )


def scaffold_area(name: str, description: str) -> None:
    base = ROOT / name
    _write(
        base / "README.md",
        f"# {name}/\n\n{description}\n\n_Placeholder — populated in later phases._\n",
    )
    _write(base / ".gitkeep", "")


def main() -> None:
    print("Scaffolding backend services:")
    for name, (port, desc) in SERVICES.items():
        scaffold_service(name, port, desc)
    print("Scaffolding top-level areas:")
    for name, desc in AREAS.items():
        scaffold_area(name, desc)
    print("Done.")


if __name__ == "__main__":
    main()
