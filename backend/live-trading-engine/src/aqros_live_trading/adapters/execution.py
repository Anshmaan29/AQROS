from __future__ import annotations

from datetime import UTC, datetime

from aqros_live_trading.domain.models import (
    BrokerAccount,
    BrokerAdapter,
    BrokerPosition,
    ExecutionReport,
    LiveOrder,
    OrderRouteStatus,
)


class ExecutionEngine:
    def __init__(self, broker: BrokerAdapter) -> None:
        self._broker = broker

    @property
    def broker(self) -> BrokerAdapter:
        return self._broker

    async def execute(self, order: LiveOrder) -> LiveOrder:
        report = await self._broker.submit_order(order)
        order.broker_order_id = report.broker_order_id
        order.status = report.status
        order.filled_quantity = report.filled_quantity
        order.remaining_quantity = report.remaining_quantity
        order.avg_fill_price = report.avg_fill_price
        order.last_fill_price = report.last_fill_price
        order.total_commission = report.cummulative_commission
        order.broker_latency_ms = report.broker_latency_ms
        order.reject_reason = report.reject_reason
        order.reject_message = report.reject_message
        order.route_status = OrderRouteStatus.ROUTED
        order.routed_to = self._broker.name
        order.updated_at = datetime.now(UTC)
        return order

    async def cancel(self, broker_order_id: str) -> ExecutionReport:
        return await self._broker.cancel_order(broker_order_id)

    async def get_status(self, broker_order_id: str) -> ExecutionReport:
        return await self._broker.get_order_status(broker_order_id)

    async def get_positions(self) -> list[BrokerPosition]:
        return await self._broker.get_positions()

    async def get_account(self) -> BrokerAccount:
        return await self._broker.get_account()
