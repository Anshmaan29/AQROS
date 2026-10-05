"""AQROS shared foundation package.

Provides the building blocks every service reuses: typed configuration,
structured logging, a health-check framework, and a FastAPI application
factory. Contains **no business logic** — only cross-cutting infrastructure.
"""

from __future__ import annotations

from aqros_core.app import create_app
from aqros_core.config import BaseServiceSettings, Environment
from aqros_core.correlation import new_correlation_id
from aqros_core.correlation import resolve as resolve_correlation_id
from aqros_core.health import HealthRegistry, HealthReport, HealthState
from aqros_core.logging import configure_logging
from aqros_core.metrics import REGISTRY, MetricsRegistry, observe_request
from aqros_core.middleware import install_observability

__version__ = "0.1.0"

__all__ = [
    "REGISTRY",
    "BaseServiceSettings",
    "Environment",
    "HealthRegistry",
    "HealthReport",
    "HealthState",
    "MetricsRegistry",
    "__version__",
    "configure_logging",
    "create_app",
    "install_observability",
    "new_correlation_id",
    "observe_request",
    "resolve_correlation_id",
]
