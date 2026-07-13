"""Concrete MarketDataProvider implementations (one module per vendor)."""

from __future__ import annotations

from aqros_market_data.adapters.providers.yfinance_provider import YFinanceProvider
from aqros_market_data.config import Settings
from aqros_market_data.domain.ports import MarketDataProvider

_PROVIDERS: dict[str, type[MarketDataProvider]] = {
    "yfinance": YFinanceProvider,
}


def create_provider(settings: Settings) -> MarketDataProvider:
    """Select and construct the configured :class:`MarketDataProvider`.

    This is the single place a new vendor is registered — add it to
    ``_PROVIDERS`` and it becomes selectable via ``AQROS_MARKET_DATA_PROVIDER``
    without touching any other file.
    """
    provider_cls = _PROVIDERS.get(settings.market_data_provider)
    if provider_cls is None:
        available = ", ".join(sorted(_PROVIDERS))
        raise ValueError(
            f"Unknown market_data_provider '{settings.market_data_provider}'. "
            f"Available providers: {available}"
        )
    if provider_cls is YFinanceProvider:
        return YFinanceProvider(
            request_timeout_seconds=settings.ingestion_request_timeout_seconds,
            max_retries=settings.ingestion_max_retries,
            retry_backoff_seconds=settings.ingestion_retry_backoff_seconds,
        )
    return provider_cls()  # pragma: no cover - reached once a 2nd provider is added
