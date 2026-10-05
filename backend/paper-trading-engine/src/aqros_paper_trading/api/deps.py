from __future__ import annotations

from typing import cast

from fastapi import Request

from aqros_paper_trading.domain.models import SimulatedExchange


def get_exchange(request: Request) -> SimulatedExchange:
    return cast(SimulatedExchange, request.app.state.exchange)
