from __future__ import annotations

import re

from aqros_live_trading.domain.models import BrokerAdapter, LiveOrder, RouteRule


class OrderRouter:
    def __init__(self, brokers: dict[str, BrokerAdapter]) -> None:
        self._brokers = brokers
        self._rules: list[RouteRule] = []

    def add_rule(self, rule: RouteRule) -> None:
        self._rules.append(rule)

    def clear_rules(self) -> None:
        self._rules.clear()

    def get_rules(self) -> list[RouteRule]:
        return list(self._rules)

    async def route(self, order: LiveOrder) -> tuple[str, BrokerAdapter]:
        for rule in self._rules:
            if not self._matches_symbol(order.symbol, rule.symbol_pattern):
                continue
            if order.order_type not in rule.order_types:
                continue
            if order.quantity < rule.min_quantity:
                continue
            if order.quantity > rule.max_quantity:
                continue
            if rule.preferred_broker and rule.preferred_broker in self._brokers:
                broker = self._brokers[rule.preferred_broker]
                if await broker.is_connected():
                    return rule.preferred_broker, broker
            for fallback_name in rule.fallback_brokers:
                if fallback_name in self._brokers:
                    broker = self._brokers[fallback_name]
                    if await broker.is_connected():
                        return fallback_name, broker

        for name, broker in self._brokers.items():
            if await broker.is_connected():
                return name, broker

        raise ConnectionError("No available broker to route order")

    def _matches_symbol(self, symbol: str, pattern: str) -> bool:
        if pattern == "*":
            return True
        regex = re.escape(pattern).replace(r"\*", ".*")
        return bool(re.match(f"^{regex}$", symbol, re.IGNORECASE))
