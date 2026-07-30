"""HTTP adapter for the Model Registry's REST API."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx

from aqros_backtesting_engine.domain.models import ResolvedModel
from aqros_backtesting_engine.domain.ports import (
    ModelNotFoundError,
    ModelRegistryClient,
    ModelRegistryUnavailableError,
)


class HttpModelRegistryClient(ModelRegistryClient):
    """Retrieve resolved models and publish artifacts through an injected :class:`httpx.AsyncClient`.

    The client is deliberately unaware of the Model Registry's Python
    package. Its only contract with that service is the published JSON API.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def resolve_production(self, model_name: str) -> ResolvedModel:
        payload = await self._get(f"/v1/models/{model_name}/production")
        return self._resolved_from_json(payload, model_name, "production")

    async def get_version(self, model_name: str, version: int) -> ResolvedModel:
        payload = await self._get(f"/v1/models/{model_name}/versions/{version}")
        return self._resolved_from_json(payload, model_name, "pinned")

    async def download_artifact(self, model_name: str, version: int) -> bytes:
        try:
            response = await self._client.get(
                f"/v1/models/{model_name}/versions/{version}/artifact"
            )
            response.raise_for_status()
            return response.content
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ModelNotFoundError(
                    f"model {model_name} version {version} artifact not found"
                ) from exc
            raise ModelRegistryUnavailableError(
                f"Model Registry request failed for {model_name} v{version}: {exc}"
            ) from exc
        except httpx.RequestError as exc:
            raise ModelRegistryUnavailableError(
                f"Model Registry request failed for {model_name} v{version}: {exc}"
            ) from exc

    async def publish_result(
        self,
        run_uuid: UUID,
        report: dict[str, object],
        signature: str,
    ) -> dict[str, object]:
        try:
            response = await self._client.post(
                "/v1/backtest-results",
                json={"run_uuid": str(run_uuid), "report": report, "signature": signature},
            )
            response.raise_for_status()
            result: dict[str, object] = response.json()
            return result
        except httpx.HTTPStatusError as exc:
            raise ModelRegistryUnavailableError(
                f"Failed to publish backtest result {run_uuid}: {exc}"
            ) from exc
        except httpx.RequestError as exc:
            raise ModelRegistryUnavailableError(
                f"Failed to publish backtest result {run_uuid}: {exc}"
            ) from exc

    async def _get(self, path: str) -> dict[str, Any]:
        try:
            response = await self._client.get(path)
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            return payload
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise ModelNotFoundError(f"model not found at {path}") from exc
            raise ModelRegistryUnavailableError(
                f"Model Registry request failed for {path}: {exc}"
            ) from exc
        except httpx.RequestError as exc:
            raise ModelRegistryUnavailableError(
                f"Model Registry request failed for {path}: {exc}"
            ) from exc

    @staticmethod
    def _resolved_from_json(
        payload: dict[str, Any],
        model_name: str,
        resolved_as: str,
    ) -> ResolvedModel:
        return ResolvedModel(
            model_name=str(payload.get("model_name", model_name)),
            version=int(payload.get("version", 0)),
            checksum=str(payload.get("checksum", "")),
            checksum_algorithm=str(payload.get("checksum_algorithm", "sha256")),
            lineage=dict(payload.get("lineage", {})),
            resolved_as=resolved_as,
        )
