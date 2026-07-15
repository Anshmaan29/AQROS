"""HTTP client implementation of the ``TrainingPipelineClient`` port.

The Model Registry's *sole* channel to the Training Pipeline — three REST
calls only against its published read surface, never the Training Pipeline's
Python package or database (Requirements 1.2, 1.3; CLAUDE.md §7.9):

* ``GET /v1/trained-models/{model_name}/versions/{version}/metadata``
* ``GET /v1/trained-models/{model_name}/versions/{version}/metrics``
* ``GET /v1/trained-models/{model_name}/versions/{version}/artifact``

``get_trained_model_record`` composes the metadata and metrics endpoints into
a single local, decoupled ``TrainedModelRecord``; ``download_artifact`` fetches
the raw artifact bytes. Following the same **zero-retry, fail-fast** contract
as ``aqros_training_pipeline.adapters.dataset_builder_client``: the first error
response or connection failure on any call raises immediately — a 404 becomes
``TrainedModelNotFoundError`` (Requirements 2.3, 20.3), and any other HTTP
error status or connection failure becomes ``UpstreamSourceError``
(Requirements 1.6, 20.3), so registration never proceeds on a partial or
unverified record.

JSON responses are translated to the local domain dataclasses by the
``_to_domain_*`` static methods — ``aqros_training_pipeline``'s own types are
never imported (CLAUDE.md §7.9). Fields are read defensively so that a
well-formed-but-incomplete upstream payload still yields a constructible
``TrainedModelRecord``; enforcing mandatory-metadata completeness is the
domain layer's responsibility (Requirement 6.2), not the transport layer's.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from aqros_model_registry.domain.models import (
    AggregatedMetrics,
    PerFoldMetrics,
    TrainedModelRecord,
)
from aqros_model_registry.domain.ports import (
    TrainedModelNotFoundError,
    TrainingPipelineClient,
    UpstreamSourceError,
)

_BASE_PATH = "/v1/trained-models"


class HttpTrainingPipelineClient(TrainingPipelineClient):
    """Reads trained-model records and artifacts from the Training Pipeline REST API."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def _get(self, path: str) -> httpx.Response:
        """Issue a single GET with zero retries; translate errors fail-fast.

        A 404 becomes ``TrainedModelNotFoundError`` (Requirements 2.3, 20.3);
        any other HTTP error status or connection failure becomes
        ``UpstreamSourceError`` (Requirements 1.6, 20.3).
        """
        try:
            response = await self._client.get(path)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == httpx.codes.NOT_FOUND:
                raise TrainedModelNotFoundError(
                    f"Training Pipeline returned 404 for {path}"
                ) from exc
            raise UpstreamSourceError(
                f"Training Pipeline returned {exc.response.status_code} for {path}"
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamSourceError(f"Training Pipeline unreachable for {path}: {exc}") from exc

    def _version_path(self, model_name: str, version: int) -> str:
        return f"{_BASE_PATH}/{model_name}/versions/{version}"

    async def get_trained_model_record(self, model_name: str, version: int) -> TrainedModelRecord:
        """Compose the metadata + metrics endpoints into one ``TrainedModelRecord``."""
        base = self._version_path(model_name, version)
        metadata = (await self._get(f"{base}/metadata")).json()
        metrics = (await self._get(f"{base}/metrics")).json()
        return self._to_domain_record(model_name, version, metadata, metrics)

    async def download_artifact(self, model_name: str, version: int) -> bytes:
        """Fetch the raw ``Model_Artifact`` bytes from the artifact endpoint."""
        response = await self._get(f"{self._version_path(model_name, version)}/artifact")
        return response.content

    # ------------------------------------------------------------------
    # JSON -> local domain translation (never imports aqros_training_pipeline)
    # ------------------------------------------------------------------

    @staticmethod
    def _opt_float(value: object) -> float | None:
        return None if value is None else float(value)  # type: ignore[arg-type]

    @staticmethod
    def _parse_datetime(value: object) -> datetime:
        """Parse an ISO-8601 timestamp; fall back to epoch UTC when absent.

        A missing timestamp yields a sentinel rather than raising, keeping the
        record constructible so the domain completeness gate owns rejection.
        """
        if value is None:
            return datetime.fromtimestamp(0, tz=UTC)
        return datetime.fromisoformat(str(value))

    @classmethod
    def _to_domain_per_fold(cls, data: dict[str, Any]) -> PerFoldMetrics:
        return PerFoldMetrics(
            fold=int(data["fold"]),
            accuracy=float(data["accuracy"]),
            precision=float(data["precision"]),
            recall=float(data["recall"]),
            f1_score=float(data["f1_score"]),
            roc_auc=cls._opt_float(data.get("roc_auc")),
            test_row_count=int(data["test_row_count"]),
        )

    @classmethod
    def _to_domain_aggregated(cls, data: dict[str, Any]) -> AggregatedMetrics:
        return AggregatedMetrics(
            accuracy_mean=float(data["accuracy_mean"]),
            accuracy_std=float(data["accuracy_std"]),
            precision_mean=float(data["precision_mean"]),
            precision_std=float(data["precision_std"]),
            recall_mean=float(data["recall_mean"]),
            recall_std=float(data["recall_std"]),
            f1_mean=float(data["f1_mean"]),
            f1_std=float(data["f1_std"]),
            roc_auc_mean=cls._opt_float(data.get("roc_auc_mean")),
            roc_auc_std=cls._opt_float(data.get("roc_auc_std")),
            evaluated_fold_count=int(data["evaluated_fold_count"]),
            roc_auc_evaluated_fold_count=int(data["roc_auc_evaluated_fold_count"]),
        )

    @classmethod
    def _to_domain_record(
        cls,
        model_name: str,
        version: int,
        metadata: dict[str, Any],
        metrics: dict[str, Any],
    ) -> TrainedModelRecord:
        """Merge the metadata and metrics payloads into a local ``TrainedModelRecord``.

        ``model_name`` is the authoritative composite key from the request
        path; ``model_type`` is its final ``__``-delimited segment when the
        upstream payload omits it. Provenance fields are sourced from the
        metadata payload and evaluation fields from the metrics payload; both
        are read defensively so an incomplete payload still constructs.
        """
        aggregated_source = metrics.get("aggregated_metrics") or metadata.get("aggregated_metrics")
        aggregated = cls._to_domain_aggregated(dict(aggregated_source or {}))
        per_fold = tuple(
            cls._to_domain_per_fold(dict(item)) for item in metrics.get("per_fold_metrics", [])
        )
        model_type = str(metadata.get("model_type") or model_name.rsplit("__", 1)[-1])
        return TrainedModelRecord(
            model_name=model_name,
            model_type=model_type,
            model_version=int(metadata.get("model_version", version)),
            training_run_id=int(metadata.get("training_run_id", 0)),
            dataset_name=str(metadata.get("dataset_name", "")),
            dataset_version=int(metadata.get("dataset_version", 0)),
            dataset_checksum=str(metadata.get("dataset_checksum", "")),
            checksum_algorithm=str(metadata.get("checksum_algorithm", "")),
            artifact_checksum=str(metadata.get("artifact_checksum", "")),
            feature_versions={
                str(k): int(v) for k, v in dict(metadata.get("feature_versions", {})).items()
            },
            per_fold_metrics=per_fold,
            aggregated_metrics=aggregated,
            feature_importance={
                str(k): float(v) for k, v in dict(metrics.get("feature_importance", {})).items()
            },
            git_commit=(
                None if metadata.get("git_commit") is None else str(metadata["git_commit"])
            ),
            trained_at=cls._parse_datetime(metadata.get("trained_at")),
            hyperparameters=dict(metadata.get("hyperparameters", {})),
        )
