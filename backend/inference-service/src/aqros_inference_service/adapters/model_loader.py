"""HttpModelLoader — loads model artifacts from the Model Registry REST API.

Implements the ``ModelLoader`` port. Talks to the Model Registry Service's
REST API to fetch model metadata and artifact URIs.
"""

from __future__ import annotations

from typing import Any

import httpx

from aqros_inference_service.adapters.backends import InferenceBackend
from aqros_inference_service.domain.model_manager import LoadedModel
from aqros_inference_service.domain.models import PredictionType
from aqros_inference_service.ports.ports import (
    ModelLoader,
    ModelNotFoundError,
)


class HttpModelLoader(ModelLoader):
    """Loads model metadata and artifacts from the Model Registry.

    Args:
        client: An ``httpx.AsyncClient`` pointed at the Model Registry.
        backend: The ``InferenceBackend`` to use for loading model binaries.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        backend: InferenceBackend,
    ) -> None:
        self._client = client
        self._backend = backend

    async def load(self, name: str, version: int) -> LoadedModel:
        metadata = await self.get_model_metadata(name, version)
        artifact_uri = metadata.get("artifact_uri", "")
        checksum = metadata.get("artifact_checksum", "") or metadata.get("checksum", "")
        model_type_str = metadata.get("model_type", "regression")
        model_type = PredictionType(model_type_str)

        model_binary = await self._fetch_artifact(artifact_uri)
        try:
            loaded_backend = await self._backend.load(model_binary, metadata)
        except Exception as exc:
            raise RuntimeError(f"Failed to load model '{name}' v{version}: {exc}") from exc

        return LoadedModel(
            name=name,
            version=version,
            model_type=model_type,
            backend=loaded_backend,
            checksum=checksum,
            metadata=metadata,
        )

    async def get_latest_version(self, name: str) -> int | None:
        try:
            resp = await self._client.get(
                f"/v1/models/{name}/versions/latest",
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            body = resp.json()
            return int(body.get("version", 0))
        except httpx.HTTPError:
            return None

    async def get_model_metadata(self, name: str, version: int) -> dict[str, Any]:
        try:
            resp = await self._client.get(
                f"/v1/models/{name}/versions/{version}",
            )
            if resp.status_code == 404:
                raise ModelNotFoundError(name, version)
            resp.raise_for_status()
            return dict(resp.json())
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ModelNotFoundError(name, version) from exc
            raise RuntimeError(f"Model Registry request failed: {exc}") from exc

    async def _fetch_artifact(self, uri: str) -> bytes:
        if not uri:
            return b""
        try:
            resp = await self._client.get(uri)
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Failed to fetch model artifact from '{uri}': {exc}") from exc
