"""Unit tests for HttpFeatureStoreClient."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from aqros_backtesting_engine.adapters.feature_store_client import (
    HttpFeatureStoreClient,
)
from aqros_backtesting_engine.domain.models import FeatureValue
from aqros_backtesting_engine.domain.ports import FeatureStoreUnavailableError

TZ = UTC


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_response(
    json_data: Any,
    status_code: int = 200,
) -> MagicMock:
    response = MagicMock(spec=httpx.Response)
    response.json.return_value = json_data
    if status_code >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(spec=httpx.Request),
            response=response,
        )
    else:
        response.raise_for_status.return_value = None
    return response


def _make_client(json_data: Any = None) -> AsyncMock:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _make_response(
        json_data if json_data is not None else {"values": [], "total": 0},
    )
    return client


def _payload(
    symbol: str = "AAPL",
    feature_name: str = "sma_20",
    feature_version: int = 1,
    index: int = 1,
    *,
    include_value: bool = True,
    include_event_time: bool = True,
    include_knowledge_time: bool = True,
    snake_case: bool = True,
) -> dict[str, Any]:
    event_time = datetime(2024, 1, index, tzinfo=TZ)
    knowledge_time = datetime(2024, 1, 1, tzinfo=TZ)

    def _key(k: str) -> str:
        if snake_case:
            return k
        return {
            "symbol": "symbol",
            "feature_name": "featureName",
            "feature_version": "featureVersion",
            "event_time": "eventTime",
            "value": "value",
            "knowledge_time": "knowledgeTime",
        }[k]

    payload: dict[str, Any] = {}
    payload[_key("symbol")] = symbol
    payload[_key("feature_name")] = feature_name
    if include_value:
        payload[_key("feature_version")] = feature_version
    if include_event_time:
        payload[_key("event_time")] = event_time.isoformat()
    if include_value:
        payload[_key("value")] = float(index) * 10.0
    if include_knowledge_time:
        payload[_key("knowledge_time")] = knowledge_time.isoformat()
    return payload


def _make_adapter(client: AsyncMock | None = None, page_size: int = 10) -> HttpFeatureStoreClient:
    return HttpFeatureStoreClient(
        client if client is not None else _make_client(),
        page_size=page_size,
    )


_params: dict[str, Any] = {
    "symbol": "AAPL",
    "feature_name": "sma_20",
    "feature_version": 1,
    "start": datetime(2024, 1, 1, tzinfo=TZ),
    "end": datetime(2024, 1, 10, tzinfo=TZ),
    "as_of": datetime(2024, 1, 5, tzinfo=TZ),
}


# ---------------------------------------------------------------------------
# Request path and query parameters
# ---------------------------------------------------------------------------


class TestRequestPath:
    async def test_correct_request_path(self):
        client = _make_client()
        adapter = _make_adapter(client)
        await adapter.get_feature_values(**_params)
        call = client.get.call_args
        assert call is not None
        assert call[0][0] == "/v1/instruments/AAPL/features/sma_20"

    async def test_url_encoded_symbol(self):
        client = _make_client()
        adapter = _make_adapter(client)
        await adapter.get_feature_values(
            symbol="S&P 500",
            feature_name="sma_20",
            feature_version=1,
            start=datetime(2024, 1, 1, tzinfo=TZ),
            end=datetime(2024, 1, 10, tzinfo=TZ),
            as_of=datetime(2024, 1, 5, tzinfo=TZ),
        )
        call = client.get.call_args
        assert call is not None
        path = call[0][0]
        assert "S%26P" in path
        assert " " not in path

    async def test_url_encoded_feature_name(self):
        client = _make_client()
        adapter = _make_adapter(client)
        await adapter.get_feature_values(
            symbol="AAPL",
            feature_name="my/feature",
            feature_version=1,
            start=datetime(2024, 1, 1, tzinfo=TZ),
            end=datetime(2024, 1, 10, tzinfo=TZ),
            as_of=datetime(2024, 1, 5, tzinfo=TZ),
        )
        call = client.get.call_args
        assert call is not None
        path = call[0][0]
        assert "my%2Ffeature" in path


class TestQueryParameters:
    async def test_all_required_params_present(self):
        client = _make_client()
        adapter = _make_adapter(client)
        await adapter.get_feature_values(**_params)
        call = client.get.call_args
        assert call is not None
        params = call[1]["params"]
        assert params["feature_version"] == 1
        assert params["start"] == "2024-01-01T00:00:00+00:00"
        assert params["end"] == "2024-01-10T00:00:00+00:00"
        assert params["as_of"] == "2024-01-05T00:00:00+00:00"
        assert params["limit"] == 10
        assert params["offset"] == 0

    async def test_pagination_params(self):
        client = _make_client()
        adapter = _make_adapter(client, page_size=50)
        await adapter.get_feature_values(**_params)
        call = client.get.call_args
        assert call is not None
        params = call[1]["params"]
        assert params["limit"] == 50
        assert params["offset"] == 0


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestPagination:
    async def test_single_page(self):
        client = _make_client({"values": [_payload(index=1)], "total": 1})
        adapter = _make_adapter(client)
        result = await adapter.get_feature_values(**_params)
        assert len(result) == 1
        client.get.assert_called_once()

    async def test_multiple_pages(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        page1 = _make_response(
            {"values": [_payload(index=i) for i in range(1, 4)], "total": 5},
        )
        page2 = _make_response(
            {"values": [_payload(index=i) for i in range(4, 6)], "total": 5},
        )
        client.get.side_effect = [page1, page2]

        adapter = _make_adapter(client, page_size=3)
        result = await adapter.get_feature_values(**_params)
        assert len(result) == 5

    async def test_pagination_terminates_on_empty_page(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        page1 = _make_response(
            {"values": [_payload(index=i) for i in range(1, 4)], "total": 10},
        )
        page2 = _make_response({"values": [], "total": 10})
        client.get.side_effect = [page1, page2]

        adapter = _make_adapter(client, page_size=3)
        result = await adapter.get_feature_values(**_params)
        assert len(result) == 3

    async def test_pagination_terminates_when_total_reached(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        page1 = _make_response(
            {"values": [_payload(index=i) for i in range(1, 4)], "total": 5},
        )
        page2 = _make_response(
            {"values": [_payload(index=i) for i in range(4, 6)], "total": 5},
        )
        client.get.side_effect = [page1, page2]

        adapter = _make_adapter(client, page_size=3)
        result = await adapter.get_feature_values(**_params)
        assert len(result) == 5

    async def test_pagination_terminates_when_page_underfilled(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        page1 = _make_response(
            {"values": [_payload(index=i) for i in range(1, 4)], "total": 5},
        )
        page2 = _make_response(
            {"values": [_payload(index=4)], "total": 5},
        )
        client.get.side_effect = [page1, page2]

        adapter = _make_adapter(client, page_size=3)
        result = await adapter.get_feature_values(**_params)
        assert len(result) == 4

    async def test_as_of_propagated_to_all_page_requests(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        page1 = _make_response(
            {"values": [_payload(index=i) for i in range(1, 4)], "total": 5},
        )
        page2 = _make_response(
            {"values": [_payload(index=i) for i in range(4, 6)], "total": 5},
        )
        client.get.side_effect = [page1, page2]

        adapter = _make_adapter(client, page_size=3)
        await adapter.get_feature_values(**_params)

        assert client.get.call_count == 2
        for call in client.get.call_args_list:
            params = call[1]["params"]
            assert params["as_of"] == "2024-01-05T00:00:00+00:00"


# ---------------------------------------------------------------------------
# Response mapping
# ---------------------------------------------------------------------------


class TestResponseMapping:
    async def test_feature_value_mapped_correctly(self):
        client = _make_client({"values": [_payload(index=1)], "total": 1})
        adapter = _make_adapter(client)
        result = await adapter.get_feature_values(**_params)
        assert len(result) == 1
        v = result[0]
        assert isinstance(v, FeatureValue)
        assert v.symbol == "AAPL"
        assert v.feature_name == "sma_20"
        assert v.feature_version == 1
        assert v.event_time == datetime(2024, 1, 1, tzinfo=TZ)
        assert v.value == 10.0
        assert v.knowledge_time == datetime(2024, 1, 1, tzinfo=TZ)

    async def test_camel_case_keys_accepted(self):
        client = _make_client(
            {"values": [_payload(index=1, snake_case=False)], "total": 1},
        )
        adapter = _make_adapter(client)
        result = await adapter.get_feature_values(**_params)
        assert len(result) == 1
        v = result[0]
        assert v.symbol == "AAPL"
        assert v.feature_name == "sma_20"
        assert v.event_time == datetime(2024, 1, 1, tzinfo=TZ)
        assert v.value == 10.0

    async def test_results_sorted_by_event_time(self):
        client = _make_client(
            {
                "values": [_payload(index=3), _payload(index=1)],
                "total": 2,
            },
        )
        adapter = _make_adapter(client)
        result = await adapter.get_feature_values(**_params)
        assert len(result) == 2
        assert result[0].event_time < result[1].event_time
        assert result[0].event_time == datetime(2024, 1, 1, tzinfo=TZ)
        assert result[1].event_time == datetime(2024, 1, 3, tzinfo=TZ)

    async def test_cross_page_results_sorted(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        page1 = _make_response(
            {"values": [_payload(index=3), _payload(index=5)], "total": 4},
        )
        page2 = _make_response(
            {"values": [_payload(index=1), _payload(index=2)], "total": 4},
        )
        client.get.side_effect = [page1, page2]

        adapter = _make_adapter(client, page_size=2)
        result = await adapter.get_feature_values(**_params)
        assert len(result) == 4
        for i in range(len(result) - 1):
            assert result[i].event_time <= result[i + 1].event_time


# ---------------------------------------------------------------------------
# as_of propagation and look-ahead rejection
# ---------------------------------------------------------------------------


class TestAsOfPropagation:
    async def test_as_of_in_request_params(self):
        client = _make_client()
        adapter = _make_adapter(client)
        await adapter.get_feature_values(**_params)
        call = client.get.call_args
        assert call is not None
        params = call[1]["params"]
        assert params["as_of"] == "2024-01-05T00:00:00+00:00"


class TestLookAheadRejection:
    async def test_look_ahead_data_rejected(self):
        payload = _payload(index=1)
        payload["knowledge_time"] = "2024-01-10T00:00:00+00:00"
        client = _make_client({"values": [payload], "total": 1})
        adapter = _make_adapter(client)
        with pytest.raises(FeatureStoreUnavailableError):
            await adapter.get_feature_values(**_params)

    async def test_look_ahead_in_first_page_rejected(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        bad = _payload(index=1)
        bad["knowledge_time"] = "2024-01-10T00:00:00+00:00"
        page1 = _make_response({"values": [bad], "total": 2})
        page2 = _make_response(
            {"values": [_payload(index=2)], "total": 2},
        )
        client.get.side_effect = [page1, page2]

        adapter = _make_adapter(client)
        with pytest.raises(FeatureStoreUnavailableError):
            await adapter.get_feature_values(**_params)

    async def test_look_ahead_in_later_page_rejected(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        page1 = _make_response(
            {"values": [_payload(index=1)], "total": 2},
        )
        bad = _payload(index=2)
        bad["knowledge_time"] = "2024-01-10T00:00:00+00:00"
        page2 = _make_response({"values": [bad], "total": 2})
        client.get.side_effect = [page1, page2]

        adapter = _make_adapter(client, page_size=1)
        with pytest.raises(FeatureStoreUnavailableError):
            await adapter.get_feature_values(**_params)


# ---------------------------------------------------------------------------
# HTTP and network failures
# ---------------------------------------------------------------------------


class TestHttpFailures:
    async def test_http_error_403_raises(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get.return_value = _make_response(None, status_code=403)
        adapter = _make_adapter(client)
        with pytest.raises(FeatureStoreUnavailableError):
            await adapter.get_feature_values(**_params)

    async def test_http_error_500_raises(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get.return_value = _make_response(None, status_code=500)
        adapter = _make_adapter(client)
        with pytest.raises(FeatureStoreUnavailableError):
            await adapter.get_feature_values(**_params)

    async def test_network_error_raises(self):
        client = _make_client()
        client.get.side_effect = httpx.ConnectError("connection refused")
        adapter = _make_adapter(client)
        with pytest.raises(FeatureStoreUnavailableError):
            await adapter.get_feature_values(**_params)

    async def test_timeout_raises(self):
        client = _make_client()
        client.get.side_effect = httpx.TimeoutException("timed out")
        adapter = _make_adapter(client)
        with pytest.raises(FeatureStoreUnavailableError):
            await adapter.get_feature_values(**_params)

    async def test_invalid_json_raises(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        response = MagicMock(spec=httpx.Response)
        response.raise_for_status.return_value = None
        response.json.side_effect = ValueError("invalid json")
        client.get.return_value = response
        adapter = _make_adapter(client)
        with pytest.raises(FeatureStoreUnavailableError):
            await adapter.get_feature_values(**_params)


# ---------------------------------------------------------------------------
# Malformed payloads
# ---------------------------------------------------------------------------


class TestMalformedPayloads:
    async def test_missing_value_field_raises(self):
        client = _make_client(
            {"values": [_payload(index=1, include_value=False)], "total": 1},
        )
        adapter = _make_adapter(client)
        with pytest.raises(FeatureStoreUnavailableError):
            await adapter.get_feature_values(**_params)

    async def test_missing_event_time_raises(self):
        client = _make_client(
            {"values": [_payload(index=1, include_event_time=False)], "total": 1},
        )
        adapter = _make_adapter(client)
        with pytest.raises(FeatureStoreUnavailableError):
            await adapter.get_feature_values(**_params)

    async def test_missing_knowledge_time_raises(self):
        client = _make_client(
            {"values": [_payload(index=1, include_knowledge_time=False)], "total": 1},
        )
        adapter = _make_adapter(client)
        with pytest.raises(FeatureStoreUnavailableError):
            await adapter.get_feature_values(**_params)

    async def test_invalid_timestamp_string_raises(self):
        payload = _payload(index=1)
        payload["event_time"] = "not-a-timestamp"
        client = _make_client({"values": [payload], "total": 1})
        adapter = _make_adapter(client)
        with pytest.raises(FeatureStoreUnavailableError):
            await adapter.get_feature_values(**_params)

    async def test_non_numeric_value_raises(self):
        payload = _payload(index=1)
        payload["value"] = "not-a-number"
        client = _make_client({"values": [payload], "total": 1})
        adapter = _make_adapter(client)
        with pytest.raises(FeatureStoreUnavailableError):
            await adapter.get_feature_values(**_params)

    async def test_non_numeric_feature_version_raises(self):
        payload = _payload(index=1)
        payload["feature_version"] = "not-a-number"
        client = _make_client({"values": [payload], "total": 1})
        adapter = _make_adapter(client)
        with pytest.raises(FeatureStoreUnavailableError):
            await adapter.get_feature_values(**_params)


# ---------------------------------------------------------------------------
# Invalid value boundaries (NaN / Inf / negative)
# ---------------------------------------------------------------------------


class TestInvalidValues:
    async def test_nan_value_accepted(self):
        payload = _payload(index=1)
        payload["value"] = math.nan
        client = _make_client({"values": [payload], "total": 1})
        adapter = _make_adapter(client)
        result = await adapter.get_feature_values(**_params)
        assert math.isnan(result[0].value)

    async def test_inf_value_accepted(self):
        payload = _payload(index=1)
        payload["value"] = math.inf
        client = _make_client({"values": [payload], "total": 1})
        adapter = _make_adapter(client)
        result = await adapter.get_feature_values(**_params)
        assert math.isinf(result[0].value) and result[0].value > 0

    async def test_negative_inf_value_accepted(self):
        payload = _payload(index=1)
        payload["value"] = -math.inf
        client = _make_client({"values": [payload], "total": 1})
        adapter = _make_adapter(client)
        result = await adapter.get_feature_values(**_params)
        assert math.isinf(result[0].value) and result[0].value < 0

    async def test_negative_value_accepted(self):
        payload = _payload(index=1)
        payload["value"] = -42.0
        client = _make_client({"values": [payload], "total": 1})
        adapter = _make_adapter(client)
        result = await adapter.get_feature_values(**_params)
        assert result[0].value == -42.0

    async def test_zero_value_accepted(self):
        payload = _payload(index=1)
        payload["value"] = 0.0
        client = _make_client({"values": [payload], "total": 1})
        adapter = _make_adapter(client)
        result = await adapter.get_feature_values(**_params)
        assert result[0].value == 0.0


# ---------------------------------------------------------------------------
# Missing-field fallback
# ---------------------------------------------------------------------------


class TestMissingFieldFallback:
    async def test_missing_symbol_falls_back_to_requested(self):
        payload = _payload(index=1)
        payload.pop("symbol")
        client = _make_client({"values": [payload], "total": 1})
        adapter = _make_adapter(client)
        result = await adapter.get_feature_values(**_params)
        assert result[0].symbol == "AAPL"

    async def test_missing_feature_name_falls_back_to_requested(self):
        payload = _payload(index=1)
        del payload["feature_name"]
        client = _make_client({"values": [payload], "total": 1})
        adapter = _make_adapter(client)
        result = await adapter.get_feature_values(**_params)
        assert result[0].feature_name == "sma_20"

    async def test_missing_feature_version_raises(self):
        payload = _payload(index=1)
        del payload["feature_version"]
        client = _make_client({"values": [payload], "total": 1})
        adapter = _make_adapter(client)
        with pytest.raises(FeatureStoreUnavailableError):
            await adapter.get_feature_values(**_params)


# ---------------------------------------------------------------------------
# Paginated response shape validation
# ---------------------------------------------------------------------------


class TestPaginatedResponseShape:
    async def test_list_payload_accepted(self):
        client = _make_client([_payload(index=1), _payload(index=2)])
        adapter = _make_adapter(client)
        result = await adapter.get_feature_values(**_params)
        assert len(result) == 2

    async def test_missing_total_field_handled(self):
        client = _make_client({"values": [_payload(index=1)]})
        adapter = _make_adapter(client)
        result = await adapter.get_feature_values(**_params)
        assert len(result) == 1

    async def test_non_list_values_raises(self):
        client = _make_client({"values": "not-a-list", "total": 0})
        adapter = _make_adapter(client)
        with pytest.raises(FeatureStoreUnavailableError):
            await adapter.get_feature_values(**_params)

    async def test_non_dict_non_list_payload_raises(self):
        client = _make_client("invalid-string")
        adapter = _make_adapter(client)
        with pytest.raises(FeatureStoreUnavailableError):
            await adapter.get_feature_values(**_params)

    async def test_list_with_non_dict_element_raises(self):
        client = _make_client(["not-a-dict"])
        adapter = _make_adapter(client)
        with pytest.raises(FeatureStoreUnavailableError):
            await adapter.get_feature_values(**_params)

    async def test_non_numeric_total_handled(self):
        client = _make_client(
            {"values": [_payload(index=1)], "total": "not-a-number"},
        )
        adapter = _make_adapter(client)
        result = await adapter.get_feature_values(**_params)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Page size configuration
# ---------------------------------------------------------------------------


class TestPageConfig:
    async def test_default_page_size_is_1000(self):
        client = _make_client()
        adapter = HttpFeatureStoreClient(client)
        assert adapter._page_size == 1000

    async def test_custom_page_size_applied(self):
        client = _make_client()
        adapter = HttpFeatureStoreClient(client, page_size=5)
        assert adapter._page_size == 5

    async def test_invalid_page_size_raises(self):
        client = _make_client()
        with pytest.raises(ValueError, match="page_size must be positive"):
            HttpFeatureStoreClient(client, page_size=0)
        with pytest.raises(ValueError, match="page_size must be positive"):
            HttpFeatureStoreClient(client, page_size=-1)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    async def test_empty_result_returns_empty_list(self):
        client = _make_client({"values": [], "total": 0})
        adapter = _make_adapter(client)
        result = await adapter.get_feature_values(**_params)
        assert result == []

    async def test_empty_list_payload_returns_empty_list(self):
        client = _make_client([])
        adapter = _make_adapter(client)
        result = await adapter.get_feature_values(**_params)
        assert result == []


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------


class TestTimestampParsing:
    async def test_z_suffix_timestamp_accepted(self):
        payload = _payload(index=1)
        payload["event_time"] = "2024-01-01T00:00:00Z"
        client = _make_client({"values": [payload], "total": 1})
        adapter = _make_adapter(client)
        result = await adapter.get_feature_values(**_params)
        assert result[0].event_time == datetime(2024, 1, 1, tzinfo=TZ)

    async def test_utc_offset_timestamp_accepted(self):
        payload = _payload(index=1)
        payload["event_time"] = "2024-01-01T00:00:00+00:00"
        client = _make_client({"values": [payload], "total": 1})
        adapter = _make_adapter(client)
        result = await adapter.get_feature_values(**_params)
        assert result[0].event_time == datetime(2024, 1, 1, tzinfo=TZ)
