from __future__ import annotations

from datetime import UTC, datetime

import structlog

from aqros_live_trading.domain.models import BrokerAccount, BrokerAdapter, BrokerPosition

_logger = structlog.get_logger(__name__)


class PositionSynchronizer:
    def __init__(
        self,
        broker: BrokerAdapter,
        sync_interval: float = 30.0,
        account_sync_interval: float = 60.0,
    ) -> None:
        self._broker = broker
        self._sync_interval = sync_interval
        self._account_sync_interval = account_sync_interval
        self._last_sync_at: datetime | None = None
        self._last_account_sync_at: datetime | None = None

    @property
    def last_sync_at(self) -> datetime | None:
        return self._last_sync_at

    async def sync_positions(self) -> list[BrokerPosition]:
        try:
            positions = await self._broker.sync_positions()
            self._last_sync_at = datetime.now(UTC)
            _logger.info(
                "positions_synced",
                broker=self._broker.name,
                count=len(positions),
            )
            return positions
        except Exception as exc:
            _logger.error(
                "position_sync_failed",
                broker=self._broker.name,
                error=str(exc),
            )
            return []

    async def sync_account(self) -> BrokerAccount | None:
        try:
            account = await self._broker.sync_account()
            self._last_account_sync_at = datetime.now(UTC)
            _logger.info(
                "account_synced",
                broker=self._broker.name,
                account_id=account.account_id,
            )
            return account
        except Exception as exc:
            _logger.error(
                "account_sync_failed",
                broker=self._broker.name,
                error=str(exc),
            )
            return None
