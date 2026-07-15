"""Local-filesystem implementation of the ``ArtifactStore`` port.

Stands in for the eventual object store behind the real interface
(Requirement 25.4 — swapping for S3 touches only this file). Path layout
``{base_dir}/{model_name}/v{model_version}/model.joblib`` deterministically
encodes both the (composite) model name and version (Requirement 8.1).

``write_artifact`` checks-then-writes inside a single ``asyncio.to_thread``
call and raises ``ArtifactAlreadyExistsError`` rather than overwriting
(Requirement 7.6) — the check-then-write is atomic within that thread to
avoid a TOCTOU race between concurrent writers for the same version.
Serialization is uniform ``joblib`` bytes for all Model_Types; this store
treats the payload as opaque bytes (Requirement 8.4). Mirrors
``aqros_training_pipeline.adapters.local_artifact_store`` verbatim.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from aqros_model_registry.domain.ports import ArtifactAlreadyExistsError, ArtifactStore


class LocalArtifactStore(ArtifactStore):
    """Writes/reads versioned, immutable model-artifact bytes on local disk."""

    def __init__(self, base_dir: str) -> None:
        self._base_dir = Path(base_dir)

    def _path_for(self, model_name: str, model_version: int) -> Path:
        return self._base_dir / model_name / f"v{model_version}" / "model.joblib"

    async def write_artifact(self, model_name: str, model_version: int, data: bytes) -> str:
        path = self._path_for(model_name, model_version)
        await asyncio.to_thread(self._write_sync, path, data)
        return str(path)

    async def read_artifact(self, model_name: str, model_version: int) -> bytes:
        path = self._path_for(model_name, model_version)
        return await asyncio.to_thread(self._read_sync, path)

    @staticmethod
    def _write_sync(path: Path, data: bytes) -> None:
        if path.exists():
            raise ArtifactAlreadyExistsError(f"artifact already exists at {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    @staticmethod
    def _read_sync(path: Path) -> bytes:
        return path.read_bytes()
