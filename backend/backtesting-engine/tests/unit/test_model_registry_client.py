from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from aqros_backtesting_engine.adapters.model_registry_client import (
    HttpModelRegistryClient,
)
from aqros_backtesting_engine.domain.ports import (
    ModelNotFoundError,
    ModelRegistryUnavailableError,
)


def _make_response(json_data: Any, status_code: int = 200) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.json.return_value = json_data
    response.status_code = status_code
    if status_code >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(spec=httpx.Request),
            response=response,
        )
    else:
        response.raise_for_status.return_value = None
    return response


def _make_client() -> AsyncMock:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _make_response(
        {
            "model_name": "test_model",
            "version": 1,
            "checksum": "abc",
            "checksum_algorithm": "sha256",
            "lineage": {},
        }
    )
    client.post.return_value = _make_response({"artifact_id": "bt-123", "version": 1})
    return client


class TestHttpModelRegistryClient:
    async def test_resolve_production(self) -> None:
        client = _make_client()
        adapter = HttpModelRegistryClient(client)
        model = await adapter.resolve_production("test_model")
        assert model.model_name == "test_model"
        assert model.version == 1
        assert model.resolved_as == "production"

    async def test_get_version(self) -> None:
        client = _make_client()
        adapter = HttpModelRegistryClient(client)
        model = await adapter.get_version("test_model", 2)
        assert model.model_name == "test_model"
        assert model.version == 1
        assert model.resolved_as == "pinned"

    async def test_download_artifact(self) -> None:
        client = _make_client()
        client.get.return_value = _make_response({"name": "test"}, 200)
        client.get.return_value.content = b"model-bytes"
        adapter = HttpModelRegistryClient(client)
        data = await adapter.download_artifact("test_model", 1)
        assert data == b"model-bytes"

    async def test_download_artifact_404_raises_model_not_found(self) -> None:
        client = _make_client()
        client.get.return_value = _make_response({"detail": "not found"}, 404)
        adapter = HttpModelRegistryClient(client)
        with pytest.raises(ModelNotFoundError):
            await adapter.download_artifact("test_model", 1)

    async def test_download_artifact_500_raises_unavailable(self) -> None:
        client = _make_client()
        client.get.return_value = _make_response({"detail": "error"}, 500)
        adapter = HttpModelRegistryClient(client)
        with pytest.raises(ModelRegistryUnavailableError):
            await adapter.download_artifact("test_model", 1)

    async def test_publish_result(self) -> None:
        client = _make_client()
        adapter = HttpModelRegistryClient(client)
        run_uuid = uuid4()
        result = await adapter.publish_result(run_uuid, {"key": "value"}, "v1:abc123")
        assert result["artifact_id"] == "bt-123"
        assert result["version"] == 1
        call = client.post.call_args
        assert call is not None
        assert call[0][0] == "/v1/backtest-results"
        payload = call[1]["json"]
        assert payload["run_uuid"] == str(run_uuid)

    async def test_publish_result_raises_on_error(self) -> None:
        client = _make_client()
        client.post.return_value = _make_response({"detail": "error"}, 500)
        adapter = HttpModelRegistryClient(client)
        with pytest.raises(ModelRegistryUnavailableError):
            await adapter.publish_result(uuid4(), {"key": "value"}, "v1:abc")

    async def test_404_raises_model_not_found(self) -> None:
        client = _make_client()
        client.get.return_value = _make_response({"detail": "not found"}, 404)
        adapter = HttpModelRegistryClient(client)
        with pytest.raises(ModelNotFoundError):
            await adapter.resolve_production("unknown")

    async def test_500_raises_unavailable(self) -> None:
        client = _make_client()
        client.get.return_value = _make_response({"detail": "error"}, 500)
        adapter = HttpModelRegistryClient(client)
        with pytest.raises(ModelRegistryUnavailableError):
            await adapter.resolve_production("unknown")
