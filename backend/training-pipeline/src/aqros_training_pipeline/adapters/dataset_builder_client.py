"""HTTP client implementation of the ``DatasetBuilderClient`` port.

The Training Pipeline's sole channel to the Dataset Builder — three REST
calls only (``GET /v1/runs/{id}``, ``.../manifest``, ``.../download``),
never the Dataset Builder's Python package or database (CLAUDE.md §7.9).

**Zero retries** (Key Design Decision 6): the first error response or
connection failure on any call immediately raises — a 404 becomes
``DatasetBuildRunNotFoundError`` (Requirement 2.3), anything else becomes
``UpstreamSourceError`` (Requirement 1.4). This deliberately does NOT reuse
the retry-with-backoff loop from
``aqros_dataset_builder.adapters.market_data_client``.

JSON responses are translated to the local, decoupled ``DatasetManifest`` /
``DatasetBuildRun`` dataclasses by the ``_to_domain_*`` static methods —
``aqros_dataset_builder``'s own types are never imported.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import httpx

from aqros_training_pipeline.domain.models import DatasetBuildRun, DatasetManifest
from aqros_training_pipeline.domain.ports import (
    DatasetBuilderClient,
    DatasetBuildRunNotFoundError,
    UpstreamSourceError,
)


class HttpDatasetBuilderClient(DatasetBuilderClient):
    """Reads build runs, manifests, and dataset artifacts from the Dataset Builder REST API."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _get(self, path: str) -> httpx.Response:
        """Issue a single GET with zero retries; translate errors per Decision 6."""
        try:
            response = await self._client.get(path)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise DatasetBuildRunNotFoundError(
                    f"Dataset Builder returned 404 for {path}"
                ) from exc
            raise UpstreamSourceError(
                f"Dataset Builder returned {exc.response.status_code} for {path}"
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamSourceError(f"Dataset Builder unreachable for {path}: {exc}") from exc

    async def get_build_run(self, build_run_id: int) -> DatasetBuildRun:
        response = await self._get(f"/v1/runs/{build_run_id}")
        return self._to_domain_build_run(response.json())

    async def get_manifest(self, build_run_id: int) -> DatasetManifest:
        response = await self._get(f"/v1/runs/{build_run_id}/manifest")
        return self._to_domain_manifest(response.json())

    async def download_dataset(self, build_run_id: int) -> bytes:
        response = await self._get(f"/v1/runs/{build_run_id}/download")
        return response.content

    @staticmethod
    def _to_domain_build_run(data: dict[str, Any]) -> DatasetBuildRun:
        return DatasetBuildRun(
            id=int(data["id"]),
            dataset_name=str(data["dataset_name"]),
            dataset_version=int(data["dataset_version"]),
            leakage_audit_passed=data.get("leakage_audit_passed"),
            leakage_audit_findings=list(data.get("leakage_audit_findings", [])),
        )

    @staticmethod
    def _to_domain_manifest(data: dict[str, Any]) -> DatasetManifest:
        return DatasetManifest(
            dataset_name=str(data["dataset_name"]),
            dataset_version=int(data["dataset_version"]),
            build_run_id=int(data["build_run_id"]),
            checksum=str(data["checksum"]),
            checksum_algorithm=str(data["checksum_algorithm"]),
            feature_names=tuple(data["feature_names"]),
            feature_versions=dict(data.get("feature_versions", {})),
            label_type=str(data["label_type"]),
            label_definition=str(data.get("label_definition", "")),
            horizon=str(data["horizon"]),
            split_strategy=str(data["split_strategy"]),
            split_params=dict(data.get("split_params", {})),
            start_date=date.fromisoformat(str(data["start_date"])),
            end_date=date.fromisoformat(str(data["end_date"])),
            created_at=datetime.fromisoformat(str(data["created_at"])),
            row_count=int(data["row_count"]),
            git_commit=data.get("git_commit"),
            market_data_source_url=str(data.get("market_data_source_url", "")),
            feature_store_source_url=str(data.get("feature_store_source_url", "")),
            quality_report=dict(data.get("quality_report", {})),
        )
