"""Dataset manifest assembly.

Pure functions over already-computed inputs — no I/O, no framework
imports. Building the manifest itself never touches a filesystem or a
subprocess; callers (``domain/services.py``) supply the checksum (hashed
from the written artifact's bytes by ``adapters/parquet_storage.py``) and
the git commit (looked up by ``adapters/git_info.py``) as plain values.

The manifest is the reproducibility contract described in CLAUDE.md §5:
"Any model, dataset, feature, or result reconstructs bit-for-bit from an
immutable manifest (data snapshot + code SHA + config)." Every field here
maps directly to that sentence:

- feature_versions / label_definition / horizon / split_strategy+params /
  symbols / date range -> "config"
- git_commit -> "code SHA"
- checksum -> lets a consumer verify the artifact they have matches the one
  this manifest describes ("data snapshot", made verifiable)
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import UTC, datetime

from aqros_dataset_builder.domain.models import (
    DatasetDefinition,
    DatasetManifest,
    DatasetQualityReport,
)

CHECKSUM_ALGORITHM = "sha256"


def compute_checksum(data: bytes) -> str:
    """Hash raw artifact bytes with :data:`CHECKSUM_ALGORITHM`."""
    return hashlib.sha256(data).hexdigest()


def build_manifest(
    *,
    definition: DatasetDefinition,
    build_run_id: int,
    feature_versions: dict[str, int],
    row_count: int,
    checksum: str,
    git_commit: str | None,
    market_data_source_url: str,
    feature_store_source_url: str,
    quality_report: DatasetQualityReport,
    now: datetime | None = None,
) -> DatasetManifest:
    """Assemble the immutable, versioned reproducibility manifest for one build run."""
    label_definitions: dict[str, str] = {
        "binary_direction": (
            "1.0 if close[t+horizon] > close[t] else 0.0, computed from future close prices"
        ),
        "future_return": "(close[t+horizon] / close[t]) - 1.0, computed from future close prices",
        "volatility": (
            "std of one-bar log returns realized over (t, t+horizon], computed from "
            "future close prices"
        ),
    }

    return DatasetManifest(
        dataset_name=definition.name,
        dataset_version=definition.version,
        build_run_id=build_run_id,
        symbols=definition.symbols,
        feature_names=definition.feature_names,
        feature_versions=feature_versions,
        label_type=definition.label_type,
        label_definition=label_definitions[definition.label_type.value],
        horizon=definition.horizon,
        split_strategy=definition.split_strategy,
        split_params=asdict(definition.split_params),
        start_date=definition.start_date,
        end_date=definition.end_date,
        created_at=now if now is not None else datetime.now(UTC),
        row_count=row_count,
        checksum=checksum,
        checksum_algorithm=CHECKSUM_ALGORITHM,
        git_commit=git_commit,
        market_data_source_url=market_data_source_url,
        feature_store_source_url=feature_store_source_url,
        quality_report=quality_report,
    )


__all__ = ["CHECKSUM_ALGORITHM", "build_manifest", "compute_checksum"]
