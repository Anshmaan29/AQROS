"""Shared builders for unit tests: manifests, build runs, and dataset frames."""

from __future__ import annotations

import io
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd

from aqros_training_pipeline.domain.models import DatasetBuildRun, DatasetManifest

FEATURE_NAMES = ("f0", "f1", "f2")


def make_dataframe(
    *,
    n_folds: int = 2,
    rows_per_role: int = 12,
    feature_names: tuple[str, ...] = FEATURE_NAMES,
    seed: int = 7,
    single_class_test: bool = False,
    empty_test: bool = False,
) -> pd.DataFrame:
    """Build a small point-in-time-style dataset with fold/split_role/label columns."""
    rng = np.random.default_rng(seed)
    records: list[dict[str, object]] = []
    ts = datetime(2024, 1, 1, tzinfo=UTC)
    for fold in range(n_folds):
        roles = ["train"] if empty_test else ["train", "test"]
        for role in roles:
            for i in range(rows_per_role):
                row: dict[str, object] = {
                    "symbol": f"SYM{i % 3}",
                    "event_time": ts,
                    "fold": fold,
                    "split_role": role,
                }
                for name in feature_names:
                    row[name] = float(rng.normal())
                if role == "test" and single_class_test:
                    row["label"] = 1
                else:
                    row["label"] = int(i % 2)
                records.append(row)
    return pd.DataFrame.from_records(records)


def to_parquet_bytes(dataframe: pd.DataFrame) -> bytes:
    """Serialize a DataFrame to Parquet bytes (matches the download payload)."""
    buffer = io.BytesIO()
    dataframe.to_parquet(buffer, index=False)
    return buffer.getvalue()


def make_manifest(
    *,
    checksum: str,
    checksum_algorithm: str = "sha256",
    feature_names: tuple[str, ...] = FEATURE_NAMES,
    dataset_name: str = "aapl_5d_direction",
    dataset_version: int = 1,
    build_run_id: int = 42,
    row_count: int = 48,
) -> DatasetManifest:
    return DatasetManifest(
        dataset_name=dataset_name,
        dataset_version=dataset_version,
        build_run_id=build_run_id,
        checksum=checksum,
        checksum_algorithm=checksum_algorithm,
        feature_names=feature_names,
        feature_versions=dict.fromkeys(feature_names, 1),
        label_type="binary_direction",
        label_definition="sign(close[t+5] - close[t])",
        horizon="5d",
        split_strategy="expanding_window",
        split_params={"n_folds": 2},
        start_date=date(2024, 1, 1),
        end_date=date(2024, 6, 1),
        created_at=datetime(2024, 6, 2, tzinfo=UTC),
        row_count=row_count,
        git_commit="abc123",
        market_data_source_url="http://market-data:8002",
        feature_store_source_url="http://feature-store:8003",
        quality_report={},
    )


def make_build_run(
    *,
    leakage_audit_passed: bool | None = True,
    findings: list[str] | None = None,
    build_run_id: int = 42,
    dataset_name: str = "aapl_5d_direction",
    dataset_version: int = 1,
) -> DatasetBuildRun:
    return DatasetBuildRun(
        id=build_run_id,
        dataset_name=dataset_name,
        dataset_version=dataset_version,
        leakage_audit_passed=leakage_audit_passed,
        leakage_audit_findings=findings or [],
    )
