from __future__ import annotations

from datetime import UTC, datetime

from aqros_live_trading.domain.models import (
    BrokerAccount,
    BrokerPosition,
    ConnectionHealth,
    ExecutionReport,
    LiveOrder,
)


class IBKRAdapter:
    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        base_url: str = "",
        heartbeat_interval: float = 5.0,
        heartbeat_timeout: float = 15.0,
    ) -> None:
        self._name = "ibkr"
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url
        self._health = ConnectionHealth(
            heartbeat_interval=heartbeat_interval,
            heartbeat_timeout=heartbeat_timeout,
        )
        self._connected = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def health(self) -> ConnectionHealth:
        return self._health

    async def connect(self) -> None:
        now = datetime.now(UTC)
        self._connected = True
        self._health.mark_connected(now)

    async def disconnect(self) -> None:
        now = datetime.now(UTC)
        self._connected = False
        self._health.mark_disconnected(now)

    async def is_connected(self) -> bool:
        return self._connected

    async def heartbeat(self) -> bool:
        if not self._connected:
            return False
        now = datetime.now(UTC)
        self._health.mark_heartbeat(now)
        self._health.mark_heartbeat_sent(now)
        return True

    async def submit_order(self, order: LiveOrder) -> ExecutionReport:
        raise NotImplementedError("IBKRAdapter.submit_order not yet implemented")

    async def cancel_order(self, broker_order_id: str) -> ExecutionReport:
        raise NotImplementedError("IBKRAdapter.cancel_order not yet implemented")

    async def get_order_status(self, broker_order_id: str) -> ExecutionReport:
        raise NotImplementedError("IBKRAdapter.get_order_status not yet implemented")

    async def get_positions(self) -> list[BrokerPosition]:
        raise NotImplementedError("IBKRAdapter.get_positions not yet implemented")

    async def get_account(self) -> BrokerAccount:
        raise NotImplementedError("IBKRAdapter.get_account not yet implemented")

    async def sync_positions(self) -> list[BrokerPosition]:
        raise NotImplementedError("IBKRAdapter.sync_positions not yet implemented")

    async def sync_account(self) -> BrokerAccount:
        raise NotImplementedError("IBKRAdapter.sync_account not yet implemented")
