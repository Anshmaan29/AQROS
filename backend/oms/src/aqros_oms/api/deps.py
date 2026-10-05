from __future__ import annotations

from typing import cast

from fastapi import Request

from aqros_oms.adapters.repository import OrderRepository


def get_order_repository(request: Request) -> OrderRepository:
    return cast(OrderRepository, request.app.state.order_repository)
