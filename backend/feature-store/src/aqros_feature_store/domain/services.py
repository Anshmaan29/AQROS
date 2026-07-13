"""Business logic: feature engineering pipeline orchestration and querying.

Pure orchestration over ports — no direct DB or HTTP client usage here,
mirroring ``aqros_market_data.domain.services``. ``FeatureEngineeringService``
depends on ``MarketDataSource``, ``FeatureValueRepository``,
``FeatureDefinitionRepository``, and ``FeatureComputationRunRepository``
(interfaces only); swapping any concrete adapter is a wiring change in
``api/deps.py``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from aqros_feature_store.domain.feature_definitions import FEATURE_REGISTRY
from aqros_feature_store.domain.models import (
    BarInterval,
    ComputationMode,
    ComputationStatus,
    FeatureComputationRun,
    FeatureDefinition,
    FeatureStatistics,
    FeatureValue,
    OHLCVBar,
)
from aqros_feature_store.domain.ports import (
    FeatureComputationRunRepository,
    FeatureDefinitionRepository,
    FeatureValueRepository,
    MarketDataSource,
)
from aqros_feature_store.domain.validation import validate_feature_values


def _bars_to_frame(bars: list[OHLCVBar]) -> pd.DataFrame:
    """Convert domain bars into a pandas DataFrame, sorted ascending by time.

    Sorting happens exactly once, here, rather than inside every indicator —
    every ``domain/indicators.py`` function trusts its input is already in
    causal order.
    """
    ordered = sorted(bars, key=lambda b: b.event_time)
    return pd.DataFrame(
        {
            "event_time": [b.event_time for b in ordered],
            "open": [float(b.open) for b in ordered],
            "high": [float(b.high) for b in ordered],
            "low": [float(b.low) for b in ordered],
            "close": [float(b.close) for b in ordered],
            "volume": [float(b.volume) for b in ordered],
        }
    )


class FeatureEngineeringService:
    """Computes and persists engineered features from validated OHLCV data."""

    def __init__(
        self,
        market_data_source: MarketDataSource,
        value_repository: FeatureValueRepository,
        definition_repository: FeatureDefinitionRepository,
        run_repository: FeatureComputationRunRepository,
        *,
        lookback_buffer_days: int = 90,
    ) -> None:
        self._market_data_source = market_data_source
        self._value_repository = value_repository
        self._definition_repository = definition_repository
        self._run_repository = run_repository
        self._lookback_buffer_days = lookback_buffer_days

    async def run_computation(
        self,
        symbol: str,
        *,
        mode: ComputationMode = ComputationMode.INCREMENTAL,
        interval: BarInterval = BarInterval.DAILY,
        now: datetime | None = None,
    ) -> FeatureComputationRun:
        """Run the full feature-engineering pipeline for one symbol.

        Reads OHLCV bars via the ``MarketDataSource`` port, computes every
        feature in the registry, validates the results, and persists the
        valid ones. Every run is recorded (running -> succeeded/failed) via
        the ``FeatureComputationRunRepository`` for audit and observability,
        regardless of outcome.
        """
        symbol = symbol.upper()
        started_at = now if now is not None else datetime.now(UTC)

        run = await self._run_repository.create_run(
            FeatureComputationRun(
                symbol=symbol,
                mode=mode,
                status=ComputationStatus.RUNNING,
                started_at=started_at,
            )
        )

        try:
            await self._ensure_definitions_registered()
            start = await self._determine_start(symbol, mode)
            bars = await self._market_data_source.get_bars(symbol, start=start, interval=interval)

            if not bars:
                completed = replace(
                    run,
                    status=ComputationStatus.SUCCEEDED,
                    bars_read=0,
                    completed_at=datetime.now(UTC),
                )
                await self._run_repository.complete_run(completed)
                return completed

            frame = _bars_to_frame(bars)
            reference_now = datetime.now(UTC)
            computed_values = self._compute_all_features(symbol, frame, reference_now)

            results = validate_feature_values(computed_values, now=reference_now)
            valid_values = [r.value for r in results if r.is_valid]
            rejection_reasons = [
                f"{r.value.feature_name}@{r.value.event_time.isoformat()}: "
                f"{'; '.join(r.violations)}"
                for r in results
                if not r.is_valid
            ]

            persisted = 0
            if valid_values:
                persisted = await self._value_repository.upsert_values(valid_values)

            completed = replace(
                run,
                status=ComputationStatus.SUCCEEDED,
                bars_read=len(bars),
                features_computed=len(computed_values),
                features_persisted=persisted,
                features_rejected=len(rejection_reasons),
                rejection_reasons=rejection_reasons,
                completed_at=datetime.now(UTC),
            )
            await self._run_repository.complete_run(completed)
            return completed

        except Exception as exc:
            failed = replace(
                run,
                status=ComputationStatus.FAILED,
                error_message=str(exc),
                completed_at=datetime.now(UTC),
            )
            await self._run_repository.complete_run(failed)
            raise

    async def get_run(self, run_id: int) -> FeatureComputationRun | None:
        return await self._run_repository.get_run(run_id)

    async def list_runs(
        self, *, symbol: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[FeatureComputationRun]:
        return await self._run_repository.list_runs(symbol=symbol, limit=limit, offset=offset)

    def _compute_all_features(
        self, symbol: str, frame: pd.DataFrame, reference_now: datetime
    ) -> list[FeatureValue]:
        """Run every registered feature's compute function over ``frame``.

        Rows where an indicator hasn't yet accumulated enough history return
        ``NaN`` by design (see ``domain/indicators.py``); those rows are
        dropped here as an *expected warm-up gap*, not a validation failure —
        validation (next step) catches genuine anomalies (e.g. infinities)
        in what remains.
        """
        values: list[FeatureValue] = []
        for registration in FEATURE_REGISTRY:
            series = registration.compute(frame)
            for event_time, raw_value in zip(frame["event_time"], series, strict=True):
                if pd.isna(raw_value):
                    continue
                values.append(
                    FeatureValue(
                        symbol=symbol,
                        feature_name=registration.definition.name,
                        feature_version=registration.definition.version,
                        event_time=event_time,
                        value=float(raw_value),
                        knowledge_time=reference_now,
                    )
                )
        return values

    async def _ensure_definitions_registered(self) -> None:
        """Idempotently register every catalog definition before computing.

        The app's startup lifespan also seeds these (so they're queryable
        even before any computation runs), but registering here too makes
        the service correct on its own, independent of startup wiring.
        """
        for registration in FEATURE_REGISTRY:
            await self._definition_repository.upsert_definition(registration.definition)

    async def _determine_start(self, symbol: str, mode: ComputationMode) -> date | None:
        """Determine the earliest bar date to fetch for this run.

        ``FULL`` always refetches all history (``start=None``). For
        ``INCREMENTAL``, the high-water mark is the *oldest* "latest computed
        event_time" across every feature — if even one feature has never
        been computed for this symbol, that mark is ``None`` and we fall back
        to a full fetch (there is nothing to incrementally build on yet).
        Otherwise we re-fetch from ``lookback_buffer_days`` before that mark,
        so windowed indicators (e.g. a 26-bar MACD) have enough trailing
        history to produce correct values for the new bars — re-upserting a
        few already-correct historical points is harmless (idempotent) and
        far cheaper than a full recompute.
        """
        if mode is ComputationMode.FULL:
            return None

        high_water_marks: list[datetime | None] = [
            await self._value_repository.get_latest_event_time(
                symbol, registration.definition.name, registration.definition.version
            )
            for registration in FEATURE_REGISTRY
        ]
        if any(mark is None for mark in high_water_marks):
            return None

        oldest_mark = min(mark for mark in high_water_marks if mark is not None)
        return (oldest_mark - timedelta(days=self._lookback_buffer_days)).date()


class FeatureQueryService:
    """Read-side operations over feature definitions, values, and statistics."""

    def __init__(
        self,
        value_repository: FeatureValueRepository,
        definition_repository: FeatureDefinitionRepository,
    ) -> None:
        self._value_repository = value_repository
        self._definition_repository = definition_repository

    async def list_definitions(self) -> list[FeatureDefinition]:
        return await self._definition_repository.list_definitions()

    async def get_definition(self, name: str, version: int) -> FeatureDefinition | None:
        return await self._definition_repository.get_definition(name, version)

    async def get_latest_definition(self, name: str) -> FeatureDefinition | None:
        return await self._definition_repository.get_latest_definition(name)

    async def get_values(
        self,
        symbol: str,
        feature_name: str,
        *,
        feature_version: int | None = None,
        start: date | None = None,
        end: date | None = None,
        as_of: datetime | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> tuple[list[FeatureValue], int]:
        """Return (values, total_count) for the given filters.

        ``as_of``, when provided, restricts results to values with
        ``knowledge_time <= as_of`` — the point-in-time query surface
        (docs/claude_ROI.md §17): a consumer reconstructing "what did we know
        on date X" can never see a feature value computed after X.
        """
        values = await self._value_repository.get_values(
            symbol,
            feature_name,
            feature_version=feature_version,
            start=start,
            end=end,
            as_of=as_of,
            limit=limit,
            offset=offset,
        )
        total = await self._value_repository.count_values(
            symbol,
            feature_name,
            feature_version=feature_version,
            start=start,
            end=end,
            as_of=as_of,
        )
        return values, total

    async def get_statistics(
        self,
        symbol: str,
        feature_name: str,
        feature_version: int,
        *,
        start: date | None = None,
        end: date | None = None,
    ) -> FeatureStatistics:
        return await self._value_repository.compute_statistics(
            symbol, feature_name, feature_version, start=start, end=end
        )
