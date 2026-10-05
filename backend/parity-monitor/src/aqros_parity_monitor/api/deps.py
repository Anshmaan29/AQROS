"""FastAPI dependency wiring for the parity monitor.

Wires the ``ParityService`` with its dependencies. Adapting the same pattern
as the feature store's ``api/deps.py``: app state holds singleton adapters,
routes depend on the service via ``Depends(get_parity_service)``.
"""

from __future__ import annotations

from fastapi import Request

from aqros_parity_monitor.application.service import ParityService
from aqros_parity_monitor.ports.ports import (
    OfflineFeatureProvider,
    OnlineFeatureProvider,
)


def get_parity_service(request: Request) -> ParityService:
    """Return the singleton ``ParityService`` from app state."""
    svc: ParityService = request.app.state.parity_service
    return svc


def get_offline_provider(request: Request) -> OfflineFeatureProvider:
    provider: OfflineFeatureProvider = request.app.state.offline_provider
    return provider


def get_online_provider(request: Request) -> OnlineFeatureProvider:
    provider: OnlineFeatureProvider = request.app.state.online_provider
    return provider
