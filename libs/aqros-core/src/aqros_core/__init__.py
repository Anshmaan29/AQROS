"""AQROS shared foundation package.

Provides the building blocks every service reuses: typed configuration,
structured logging, a health-check framework, and a FastAPI application
factory. Contains **no business logic** — only cross-cutting infrastructure.
"""

from __future__ import annotations

from aqros_core.app import create_app
from aqros_core.config import BaseServiceSettings, Environment
from aqros_core.health import HealthRegistry, HealthReport, HealthState
from aqros_core.logging import configure_logging

__version__ = "0.1.0"

__all__ = [
    "BaseServiceSettings",
    "Environment",
    "HealthRegistry",
    "HealthReport",
    "HealthState",
    "__version__",
    "configure_logging",
    "create_app",
]
