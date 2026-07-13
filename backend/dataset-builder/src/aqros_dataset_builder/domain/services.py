"""Business logic: dataset-generation pipeline orchestration and querying.

Pure orchestration over ports — no direct DB, HTTP, or filesystem usage
here, mirroring ``aqros_market_data.domain.services`` and
``aqros_feature_store.domain.services``. ``DatasetBuilderService`` depends on
``MarketDataSource``, ``FeatureSource``, and the three repository/storage
ports (interfaces only); swapping any concrete adapter is a wiring change in
``api/deps.py``.

This module is the Dataset Builder's *entire* reason for existing: it must
never train a model (out of scope per the milestone brief) — it only reads,
aligns, labels, splits, audits, and persists.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from statistics import fmean, pstdev

import pandas as pd

from aqros_dataset_builder.domain.labels import compute_label
from aqros_dataset_builder.domain.manifest import build_manifest
from aqros_dataset_builder.domain.models import (
    BuildStatus,
    DatasetBuildRun,
    DatasetDefinition,
    LabelBalance,
    LabelType,
    OHLCVBar,
    SplitRole,
)
from aqros_dataset_builder.domain.ports import (
    DatasetBuildRunRepository,
    DatasetDefinitionRepository,
    DatasetStorage,
    FeatureSource,
    GitInfoProvider,
    MarketDataSource,
)
from aqros_dataset_builder.domain.quality import build_quality_report, row_value_as_float
from aqros_dataset_builder.domain.splitters import Fold, compute_splits
from aqros_dataset_builder.domain.validation import audit_finite_values, audit_fold_integrity


def _bars_to_close_series(bars: list[OHLCVBar]) -> pd.Series:
    """Convert domain bars into a pandas Series of close prices, sorted ascending."""
    ordered = sorted(bars, key=lambda b: b.event_time)
    return pd.Series(
        [float(b.close) for b in ordered],
        index=[b.event_time for b in ordered],
    )


def _manifest_to_dict(manifest: object) -> dict[str, object]:
    """Convert a :class:`DatasetManifest` (and its nested quality report) to a
    plain, JSON-serializable dict for ``DatasetStorage.write_manifest``."""
    from dataclasses import asdict

    data: dict[str, object] = asdict(manifest)  # type: ignore[call-overload]
    # Enum members inside the dataclass survive asdict() as-is; normalize to
    # their string value so the written manifest is plain JSON, not a repr.
    for key in ("label_type", "horizon", "split_strategy"):
        value = data.get(key)
        if hasattr(value, "value"):
            data[key] = value.value
    return data


class DatasetBuilderService:
    """Builds and persists reproducible supervised-learning datasets."""

    def __init__(
        self,
        market_data_source: MarketDataSource,
        feature_source: FeatureSource,
        definition_repository: DatasetDefinitionRepository,
        run_repository: DatasetBuildRunRepository,
        storage: DatasetStorage,
        git_info_provider: GitInfoProvider,
        *,
        market_data_source_url: str,
        feature_store_source_url: str,
    ) -> None:
        self._market_data_source = market_data_source
        self._feature_source = feature_source
        self._definition_repository = definition_repository
        self._run_repository = run_repository
        self._storage = storage
        self._git_info_provider = git_info_provider
        self._market_data_source_url = market_data_source_url
        self._feature_store_source_url = feature_store_source_url

    async def create_definition(self, definition: DatasetDefinition) -> DatasetDefinition:
        """Register a new, immutable dataset definition version."""
        return await self._definition_repository.create_definition(definition)

    async def build_dataset(
        self, dataset_name: str, dataset_version: int, *, now: datetime | None = None
    ) -> DatasetBuildRun:
        """Run the full dataset-generation pipeline for a registered definition.

        Reads OHLCV bars (for labels) and feature values (for the X matrix)
        for every symbol in the definition via their respective ports,
        aligns them on event_time, computes labels from future prices only,
        assigns split roles, runs the leakage audit, and — only if the
        audit passes — persists the result. Every run is recorded (running
        -> succeeded/failed) regardless of outcome, exactly mirroring the
        ingestion/feature-computation audit-trail pattern already
        established by market-data and feature-store.
        """
        started_at = now if now is not None else datetime.now(UTC)

        definition = await self._definition_repository.get_definition(dataset_name, dataset_version)
        if definition is None:
            raise ValueError(f"Unknown dataset definition '{dataset_name}'@{dataset_version}")

        run = await self._run_repository.create_run(
            DatasetBuildRun(
                dataset_name=dataset_name,
                dataset_version=dataset_version,
                status=BuildStatus.RUNNING,
                started_at=started_at,
            )
        )

        try:
            all_rows: list[dict[str, object]] = []
            all_feature_values: list[float] = []
            all_label_values: list[float] = []
            raw_frames: list[pd.DataFrame] = []
            feature_versions: dict[str, int] = {}
            row_counts_by_role: dict[str, int] = {role.value: 0 for role in SplitRole}
            bars_read = 0
            rows_rejected = 0
            rejection_reasons: list[str] = []
            audit_findings: list[str] = []

            for symbol in definition.symbols:
                bars = await self._market_data_source.get_bars(
                    symbol, start=definition.start_date, end=definition.end_date
                )
                bars_read += len(bars)
                if not bars:
                    rejection_reasons.append(f"{symbol}: no OHLCV bars available")
                    continue

                close_series = _bars_to_close_series(bars)
                label_series = compute_label(
                    close_series, definition.label_type, definition.horizon
                )

                feature_series_by_name: dict[str, pd.Series] = {}
                for feature_name in definition.feature_names:
                    values = await self._feature_source.get_feature_values(
                        symbol,
                        feature_name,
                        start=definition.start_date,
                        end=definition.end_date,
                    )
                    feature_series_by_name[feature_name] = pd.Series(
                        {v.event_time: v.value for v in values}
                    )
                    if values and feature_name not in feature_versions:
                        feature_versions[feature_name] = values[0].feature_version

                frame = pd.DataFrame({"label": label_series})
                for feature_name, series in feature_series_by_name.items():
                    frame[feature_name] = series
                frame = frame.sort_index()
                raw_frames.append(frame)

                # Drop rows missing the label (trailing horizon bars, or a
                # symbol with no bars) or any requested feature — we never
                # fabricate a value for missing history (mirrors the
                # discipline in market-data's and feature-store's own
                # validators).
                complete_mask = frame.notna().all(axis=1)
                rows_rejected += int((~complete_mask).sum())
                clean_frame = frame.loc[complete_mask]

                if clean_frame.empty:
                    rejection_reasons.append(
                        f"{symbol}: no rows with both a complete label and every "
                        f"requested feature"
                    )
                    continue

                folds: list[Fold] = compute_splits(len(clean_frame), definition.split_params)
                audit_findings.extend(
                    self._audit_and_collect(
                        symbol, clean_frame, folds, definition, all_feature_values, all_label_values
                    )
                )

                for fold_idx, fold in enumerate(folds):
                    for row_position, role in fold.items():
                        row = clean_frame.iloc[row_position]
                        event_time = clean_frame.index[row_position]
                        row_dict: dict[str, object] = {
                            "symbol": symbol,
                            "event_time": event_time,
                            "fold": fold_idx,
                            "split_role": role.value,
                            "label": float(row["label"]),
                        }
                        for feature_name in definition.feature_names:
                            row_dict[feature_name] = float(row[feature_name])
                        all_rows.append(row_dict)
                        row_counts_by_role[role.value] += 1

            label_values_for_balance = [row_value_as_float(r["label"]) for r in all_rows]
            label_balance = self._compute_label_balance(
                label_values_for_balance, definition.label_type
            )

            # Fold-integrity findings were already collected per-symbol above
            # (each symbol has its own set of folds); finite-value checks run
            # once here over the full, flattened feature/label columns.
            finite_findings = [
                *audit_finite_values(all_feature_values, "features"),
                *audit_finite_values(all_label_values, "label"),
            ]
            all_findings = [*audit_findings, *finite_findings]
            audit_passed = not all_findings

            combined_raw_frame = (
                pd.concat(raw_frames, ignore_index=True) if raw_frames else pd.DataFrame()
            )
            quality_report = build_quality_report(
                combined_raw_frame, all_rows, definition.feature_names, definition.label_type
            )

            parquet_path: str | None = None
            manifest_path: str | None = None
            if audit_passed and all_rows:
                parquet_path = await self._storage.write_dataset(
                    dataset_name, dataset_version, run.id or 0, all_rows
                )
                checksum = await self._storage.compute_checksum(parquet_path)
                git_commit = await self._git_info_provider.get_commit_sha()
                manifest = build_manifest(
                    definition=definition,
                    build_run_id=run.id or 0,
                    feature_versions=feature_versions,
                    row_count=len(all_rows),
                    checksum=checksum,
                    git_commit=git_commit,
                    market_data_source_url=self._market_data_source_url,
                    feature_store_source_url=self._feature_store_source_url,
                    quality_report=quality_report,
                )
                manifest_path = await self._storage.write_manifest(
                    dataset_name, dataset_version, run.id or 0, _manifest_to_dict(manifest)
                )

            completed = replace(
                run,
                status=BuildStatus.SUCCEEDED,
                bars_read=bars_read,
                rows_generated=len(all_rows),
                rows_rejected=rows_rejected,
                rejection_reasons=rejection_reasons,
                leakage_audit_passed=audit_passed,
                leakage_audit_findings=all_findings,
                label_balance=label_balance,
                row_counts_by_role=row_counts_by_role,
                quality_report=quality_report,
                parquet_path=parquet_path,
                manifest_path=manifest_path,
                completed_at=datetime.now(UTC),
            )
            await self._run_repository.complete_run(completed)
            return completed

        except Exception as exc:
            failed = replace(
                run,
                status=BuildStatus.FAILED,
                error_message=str(exc),
                completed_at=datetime.now(UTC),
            )
            await self._run_repository.complete_run(failed)
            raise

    def _audit_and_collect(
        self,
        symbol: str,
        clean_frame: pd.DataFrame,
        folds: list[Fold],
        definition: DatasetDefinition,
        all_feature_values: list[float],
        all_label_values: list[float],
    ) -> list[str]:
        """Run the per-symbol fold-integrity audit and accumulate flat value lists."""
        findings = audit_fold_integrity(folds, definition.split_strategy)
        for feature_name in definition.feature_names:
            all_feature_values.extend(float(v) for v in clean_frame[feature_name].tolist())
        all_label_values.extend(float(v) for v in clean_frame["label"].tolist())
        return [f"{symbol}: {finding}" for finding in findings]

    @staticmethod
    def _compute_label_balance(values: list[float], label_type: LabelType) -> LabelBalance | None:
        if not values:
            return None
        positive_fraction = None
        if label_type is LabelType.BINARY_DIRECTION:
            positive_fraction = sum(1 for v in values if v == 1.0) / len(values)
        return LabelBalance(
            count=len(values),
            mean=fmean(values),
            std=pstdev(values) if len(values) > 1 else 0.0,
            minimum=min(values),
            maximum=max(values),
            positive_fraction=positive_fraction,
        )

    async def get_run(self, run_id: int) -> DatasetBuildRun | None:
        return await self._run_repository.get_run(run_id)

    async def list_runs(
        self, *, dataset_name: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[DatasetBuildRun]:
        return await self._run_repository.list_runs(
            dataset_name=dataset_name, limit=limit, offset=offset
        )


class DatasetQueryService:
    """Read-side operations over dataset definitions and their generated artifacts."""

    def __init__(
        self,
        definition_repository: DatasetDefinitionRepository,
        run_repository: DatasetBuildRunRepository,
        storage: DatasetStorage,
    ) -> None:
        self._definition_repository = definition_repository
        self._run_repository = run_repository
        self._storage = storage

    async def list_definitions(self) -> list[DatasetDefinition]:
        return await self._definition_repository.list_definitions()

    async def get_definition(self, name: str, version: int) -> DatasetDefinition | None:
        return await self._definition_repository.get_definition(name, version)

    async def get_latest_definition(self, name: str) -> DatasetDefinition | None:
        return await self._definition_repository.get_latest_definition(name)

    async def preview_rows(self, run_id: int, limit: int = 20) -> list[dict[str, object]]:
        """Return the first ``limit`` rows of a completed build run's dataset."""
        run = await self._run_repository.get_run(run_id)
        if run is None or run.parquet_path is None:
            return []
        rows = await self._storage.read_dataset(run.parquet_path)
        return rows[:limit]

    async def get_manifest(self, run_id: int) -> dict[str, object] | None:
        """Return the reproducibility manifest for a completed build run, if any."""
        run = await self._run_repository.get_run(run_id)
        if run is None or run.manifest_path is None:
            return None
        return await self._storage.read_manifest(run.manifest_path)

    async def get_run(self, run_id: int) -> DatasetBuildRun | None:
        return await self._run_repository.get_run(run_id)

    async def list_runs(
        self, *, dataset_name: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[DatasetBuildRun]:
        return await self._run_repository.list_runs(
            dataset_name=dataset_name, limit=limit, offset=offset
        )
