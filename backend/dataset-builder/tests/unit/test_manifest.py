"""Unit tests for dataset manifest assembly (pure domain logic, no I/O)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

from aqros_dataset_builder.domain.manifest import (
    CHECKSUM_ALGORITHM,
    build_manifest,
    compute_checksum,
)
from aqros_dataset_builder.domain.models import (
    DatasetDefinition,
    DatasetQualityReport,
    LabelType,
    PredictionHorizon,
    SplitStrategy,
    WalkForwardParams,
)

FIXED_NOW = datetime(2024, 6, 1, tzinfo=UTC)


def _definition() -> DatasetDefinition:
    return DatasetDefinition(
        name="aapl_5d_direction",
        version=1,
        symbols=("AAPL", "MSFT"),
        feature_names=("sma_20", "rsi_14"),
        label_type=LabelType.BINARY_DIRECTION,
        horizon=PredictionHorizon.FIVE_DAY,
        split_strategy=SplitStrategy.WALK_FORWARD,
        split_params=WalkForwardParams(
            train_size=100, validation_size=20, test_size=20, step_size=20
        ),
        start_date=date(2023, 1, 1),
        end_date=date(2024, 1, 1),
        created_at=FIXED_NOW,
    )


def _empty_quality_report() -> DatasetQualityReport:
    return DatasetQualityReport(
        total_rows=0,
        duplicate_row_count=0,
        missing_value_counts={},
        class_balance={},
        feature_statistics=(),
        validation_passed=True,
        validation_findings=[],
    )


def test_compute_checksum_is_deterministic_for_same_bytes() -> None:
    data = b"some parquet bytes"
    assert compute_checksum(data) == compute_checksum(data)


def test_compute_checksum_differs_for_different_bytes() -> None:
    assert compute_checksum(b"a") != compute_checksum(b"b")


def test_build_manifest_captures_definition_fields() -> None:
    manifest = build_manifest(
        definition=_definition(),
        build_run_id=42,
        feature_versions={"sma_20": 1, "rsi_14": 1},
        row_count=500,
        checksum="abc123",
        git_commit="deadbeef",
        market_data_source_url="http://market-data:8002",
        feature_store_source_url="http://feature-store:8003",
        quality_report=_empty_quality_report(),
        now=FIXED_NOW,
    )

    assert manifest.dataset_name == "aapl_5d_direction"
    assert manifest.dataset_version == 1
    assert manifest.build_run_id == 42
    assert manifest.symbols == ("AAPL", "MSFT")
    assert manifest.feature_names == ("sma_20", "rsi_14")
    assert manifest.feature_versions == {"sma_20": 1, "rsi_14": 1}
    assert manifest.label_type is LabelType.BINARY_DIRECTION
    assert "future close prices" in manifest.label_definition
    assert manifest.horizon is PredictionHorizon.FIVE_DAY
    assert manifest.split_strategy is SplitStrategy.WALK_FORWARD
    assert manifest.row_count == 500
    assert manifest.checksum == "abc123"
    assert manifest.checksum_algorithm == CHECKSUM_ALGORITHM
    assert manifest.git_commit == "deadbeef"
    assert manifest.market_data_source_url == "http://market-data:8002"
    assert manifest.feature_store_source_url == "http://feature-store:8003"
    assert manifest.created_at == FIXED_NOW


def test_build_manifest_handles_missing_git_commit() -> None:
    manifest = build_manifest(
        definition=_definition(),
        build_run_id=1,
        feature_versions={},
        row_count=0,
        checksum="x",
        git_commit=None,
        market_data_source_url="http://market-data:8002",
        feature_store_source_url="http://feature-store:8003",
        quality_report=_empty_quality_report(),
        now=FIXED_NOW,
    )
    assert manifest.git_commit is None


def test_build_manifest_label_definition_varies_by_label_type() -> None:
    for label_type, expected_substring in (
        (LabelType.BINARY_DIRECTION, "close[t+horizon] > close[t]"),
        (LabelType.FUTURE_RETURN, "close[t+horizon] / close[t]"),
        (LabelType.VOLATILITY, "log returns"),
    ):
        definition = replace(_definition(), label_type=label_type)
        manifest = build_manifest(
            definition=definition,
            build_run_id=1,
            feature_versions={},
            row_count=0,
            checksum="x",
            git_commit=None,
            market_data_source_url="http://market-data:8002",
            feature_store_source_url="http://feature-store:8003",
            quality_report=_empty_quality_report(),
            now=FIXED_NOW,
        )
        assert expected_substring in manifest.label_definition
