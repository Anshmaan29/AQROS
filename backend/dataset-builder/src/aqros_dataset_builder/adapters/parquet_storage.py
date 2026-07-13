"""Local-filesystem Parquet implementation of the ``DatasetStorage`` port.

Stands in for the eventual object-store/lake (S3/R2/MinIO) behind the *real*
interface (CLAUDE.md §9: "mocks sit behind real interfaces... so later
stages compose onto earlier ones instead of reworking them"). Swapping this
adapter for an S3-backed one later touches only this file — the domain and
API layers never change.

Each build run's rows are written to a single, versioned, immutable file —
never overwritten in place — mirroring the "immutable, content-addressed"
dataset-artifact doctrine (CLAUDE.md §10, `claude_ROI.md` §16, §19).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pandas as pd

from aqros_dataset_builder.domain.manifest import CHECKSUM_ALGORITHM
from aqros_dataset_builder.domain.ports import DatasetStorage


class LocalParquetStorage(DatasetStorage):
    """Writes/reads generated dataset rows (Parquet) and manifests (JSON) on local disk."""

    def __init__(self, base_dir: str) -> None:
        self._base_dir = Path(base_dir)

    async def write_dataset(
        self,
        dataset_name: str,
        dataset_version: int,
        run_id: int,
        rows: list[dict[str, object]],
    ) -> str:
        path = self._data_path_for(dataset_name, dataset_version, run_id)
        await asyncio.to_thread(self._write_parquet_sync, path, rows)
        return str(path)

    async def read_dataset(self, path: str) -> list[dict[str, object]]:
        return await asyncio.to_thread(self._read_parquet_sync, Path(path))

    async def compute_checksum(self, path: str) -> str:
        return await asyncio.to_thread(self._compute_checksum_sync, Path(path))

    async def write_manifest(
        self,
        dataset_name: str,
        dataset_version: int,
        run_id: int,
        manifest: dict[str, object],
    ) -> str:
        path = self._manifest_path_for(dataset_name, dataset_version, run_id)
        await asyncio.to_thread(self._write_manifest_sync, path, manifest)
        return str(path)

    async def read_manifest(self, path: str) -> dict[str, object]:
        return await asyncio.to_thread(self._read_manifest_sync, Path(path))

    def _data_path_for(self, dataset_name: str, dataset_version: int, run_id: int) -> Path:
        directory = self._base_dir / dataset_name / f"v{dataset_version}"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"run_{run_id}.parquet"

    def _manifest_path_for(self, dataset_name: str, dataset_version: int, run_id: int) -> Path:
        directory = self._base_dir / dataset_name / f"v{dataset_version}"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"run_{run_id}.manifest.json"

    @staticmethod
    def _write_parquet_sync(path: Path, rows: list[dict[str, object]]) -> None:
        frame = pd.DataFrame(rows)
        frame.to_parquet(path, engine="pyarrow", index=False)

    @staticmethod
    def _read_parquet_sync(path: Path) -> list[dict[str, object]]:
        # pandas-stubs' overloads for `read_parquet` don't cleanly resolve
        # with `engine="pyarrow"` as a plain keyword argument (a known
        # stub-precision gap, not a real type error).
        frame = pd.read_parquet(str(path), engine="pyarrow")  # type: ignore[call-overload]
        records: list[dict[str, object]] = frame.to_dict(orient="records")
        return records

    @staticmethod
    def _compute_checksum_sync(path: Path) -> str:
        hasher = hashlib.new(CHECKSUM_ALGORITHM)
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    def _write_manifest_sync(path: Path, manifest: dict[str, object]) -> None:
        path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    @staticmethod
    def _read_manifest_sync(path: Path) -> dict[str, object]:
        data: dict[str, object] = json.loads(path.read_text(encoding="utf-8"))
        return data
