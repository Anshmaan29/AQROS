from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime

import structlog

from aqros_live_trading.domain.models import (
    BrokerAdapter,
    ConnectionHealth,
    ConnectionStatus,
    KillSwitch,
    ReconnectionPolicy,
)

_logger = structlog.get_logger(__name__)


class BrokerSessionManager:
    def __init__(
        self,
        broker: BrokerAdapter,
        health: ConnectionHealth,
        kill_switch: KillSwitch,
        reconnection_policy: ReconnectionPolicy,
    ) -> None:
        self._broker = broker
        self._health = health
        self._kill_switch = kill_switch
        self._reconnection = reconnection_policy
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._running = False

    @property
    def health(self) -> ConnectionHealth:
        return self._health

    @property
    def kill_switch(self) -> KillSwitch:
        return self._kill_switch

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await self._connect()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self) -> None:
        self._running = False
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._heartbeat_task
        await self._disconnect()

    async def _connect(self) -> None:
        try:
            self._health.status = ConnectionStatus.CONNECTING
            await self._broker.connect()
            self._health.mark_connected()
            _logger.info("broker_connected", broker=self._broker.name)
            self._reconnection.reset()
        except Exception as exc:
            self._health.mark_disconnected()
            _logger.error("broker_connect_failed", broker=self._broker.name, error=str(exc))
            await self._handle_reconnect()

    async def _disconnect(self) -> None:
        try:
            await self._broker.disconnect()
            self._health.mark_disconnected()
            _logger.info("broker_disconnected", broker=self._broker.name)
        except Exception as exc:
            _logger.error("broker_disconnect_error", broker=self._broker.name, error=str(exc))

    async def _heartbeat_loop(self) -> None:
        while self._running:
            await asyncio.sleep(self._health.heartbeat_interval)
            try:
                ok = await self._broker.heartbeat()
                if ok:
                    self._health.mark_heartbeat()
                    self._health.consecutive_failures = 0
                else:
                    self._health.consecutive_failures += 1
                    _logger.warning(
                        "heartbeat_failed",
                        broker=self._broker.name,
                        consecutive=self._health.consecutive_failures,
                    )
                    if self._health.is_heartbeat_stale():
                        await self._handle_reconnect()
            except Exception as exc:
                self._health.consecutive_failures += 1
                _logger.error(
                    "heartbeat_error",
                    broker=self._broker.name,
                    error=str(exc),
                )
                if self._health.is_heartbeat_stale():
                    await self._handle_reconnect()

            if (
                self._kill_switch.auto_trigger_on_disconnect_seconds > 0
                and self._health.status == ConnectionStatus.DISCONNECTED
                and self._health.last_disconnected_at is not None
            ):
                elapsed = (datetime.now(UTC) - self._health.last_disconnected_at).total_seconds()
                if elapsed >= self._kill_switch.auto_trigger_on_disconnect_seconds:
                    self._kill_switch.trigger(
                        by="auto_disconnect",
                        reason=f"Disconnected for {elapsed}s exceeded threshold",
                    )
                    _logger.warning("kill_switch_auto_triggered", broker=self._broker.name)

    async def _handle_reconnect(self) -> None:
        self._health.status = ConnectionStatus.RECONNECTING
        delay = self._reconnection.get_delay()
        if delay < 0:
            self._health.status = ConnectionStatus.FAILED
            _logger.error(
                "reconnect_exhausted",
                broker=self._broker.name,
                attempts=self._reconnection.attempt,
            )
            return

        _logger.info(
            "reconnecting",
            broker=self._broker.name,
            attempt=self._reconnection.attempt,
            delay=delay,
        )
        await asyncio.sleep(delay)

        try:
            await self._broker.connect()
            self._health.mark_connected()
            self._health.total_reconnections += 1
            self._reconnection.reset()
            _logger.info(
                "reconnected",
                broker=self._broker.name,
                attempt=self._reconnection.attempt,
            )
        except Exception as exc:
            self._health.mark_disconnected()
            _logger.error(
                "reconnect_failed",
                broker=self._broker.name,
                error=str(exc),
            )
