"""End-to-end API integration tests against a real Postgres (task 9.2).

Exercises the full stack (FastAPI routes -> domain services -> SQLAlchemy
repositories -> Postgres + a real ``LocalArtifactStore``) with the Training
Pipeline client swapped for the in-memory ``FakeTrainingPipelineClient`` (no
live Training Pipeline required, Requirement 26.3). Never imports
``aqros_training_pipeline``.

Covers: the registration -> read -> artifact-download happy path; the full
four-eyes promotion chain from REGISTERED through PRODUCTION; a rollback
path; the typed-404 guarantee for every read/governance endpoint
(Property 23); reads remaining available while a promotion is stuck
mid-governance (Property 24, narrowed -- see its docstring); and a
reproducibility round-trip (Property 25).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from httpx import AsyncClient

from aqros_model_registry.domain.integrity import compute_checksum
from aqros_model_registry.domain.models import (
    AggregatedMetrics,
    PerFoldMetrics,
    TrainedModelRecord,
)

pytestmark = pytest.mark.integration

_MODEL_NAME = "aapl_5d_direction__random_forest"

_AGGREGATED_METRICS = AggregatedMetrics(
    accuracy_mean=0.6,
    accuracy_std=0.05,
    precision_mean=0.6,
    precision_std=0.05,
    recall_mean=0.6,
    recall_std=0.05,
    f1_mean=0.6,
    f1_std=0.05,
    roc_auc_mean=None,
    roc_auc_std=None,
    evaluated_fold_count=1,
    roc_auc_evaluated_fold_count=0,
)

_PER_FOLD_METRICS = (
    PerFoldMetrics(
        fold=1,
        accuracy=0.6,
        precision=0.6,
        recall=0.6,
        f1_score=0.6,
        roc_auc=None,
        test_row_count=100,
    ),
)


def _idempotency_headers() -> dict[str, str]:
    """A fresh ``Idempotency-Key`` header for one mutating call (Requirement 19.11)."""
    return {"Idempotency-Key": str(uuid.uuid4())}


def _build_artifact_bytes(model_name: str, version: int) -> bytes:
    return f"artifact-bytes-{model_name}-{version}".encode()


def _build_record(model_name: str, version: int, *, training_run_id: int) -> TrainedModelRecord:
    """A fully-populated, mandatory-metadata-complete ``TrainedModelRecord``.

    ``artifact_checksum`` is computed from the exact bytes
    ``_build_artifact_bytes`` returns for the same ``(model_name, version)``,
    so a matching record/artifact pair always passes the checksum gate on
    ingestion.
    """
    checksum = compute_checksum(_build_artifact_bytes(model_name, version), "sha256")
    return TrainedModelRecord(
        model_name=model_name,
        model_type="random_forest",
        model_version=version,
        training_run_id=training_run_id,
        dataset_name="aapl_5d_direction",
        dataset_version=1,
        dataset_checksum="dataset-checksum",
        checksum_algorithm="sha256",
        artifact_checksum=checksum,
        feature_versions={"close_return_5d": 1},
        per_fold_metrics=_PER_FOLD_METRICS,
        aggregated_metrics=_AGGREGATED_METRICS,
        feature_importance={"close_return_5d": 1.0},
        git_commit="a" * 40,
        trained_at=datetime(2024, 1, 1),
        hyperparameters={"n_estimators": 100},
    )


def _preload(client: AsyncClient, model_name: str, version: int, *, training_run_id: int) -> bytes:
    """Preload a matching record+artifact on the fake Training Pipeline client.

    Returns the artifact bytes so the caller can assert byte-identity later.
    """
    record = _build_record(model_name, version, training_run_id=training_run_id)
    artifact_bytes = _build_artifact_bytes(model_name, version)
    fake = client.fake_training_pipeline  # type: ignore[attr-defined]
    fake._records[(model_name, version)] = record
    fake._artifacts[(model_name, version)] = artifact_bytes
    return artifact_bytes


async def _register(
    client: AsyncClient, model_name: str, version: int, *, training_run_id: int
) -> dict:
    """Preload + register ``(model_name, version)``, asserting a ``201``."""
    _preload(client, model_name, version, training_run_id=training_run_id)
    resp = await client.post(
        "/v1/models",
        json={
            "model_name": model_name,
            "version": version,
            "training_run_id": training_run_id,
        },
        headers=_idempotency_headers(),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _promote_to_production(
    client: AsyncClient,
    model_name: str,
    version: int,
    *,
    approvers: tuple[str, str] = ("alice", "bob"),
    requester: str = "researcher",
) -> dict:
    """Drive a REGISTERED model version all the way to PRODUCTION over HTTP.

    Walks REGISTERED -> VALIDATED (evidence, applied immediately) ->
    STAGING (single approval) -> PRODUCTION (four-eyes, two distinct human
    approvers), returning the final ``ModelVersionResponse`` body.
    """
    base = f"/v1/models/{model_name}/versions/{version}"

    validated = await client.post(
        f"{base}/transition",
        json={
            "to_state": "validated",
            "justification": "passed the validation gauntlet",
            "validation_evidence": {"kind": "backtest_report", "reference": "dossier-1"},
        },
        headers={**_idempotency_headers(), "X-Actor": requester},
    )
    assert validated.status_code == 200, validated.text

    staging_pending = await client.post(
        f"{base}/transition",
        json={"to_state": "staging", "justification": "stage it"},
        headers={**_idempotency_headers(), "X-Actor": requester},
    )
    assert staging_pending.status_code == 202, staging_pending.text
    staging_request_id = staging_pending.json()["id"]

    staging_applied = await client.post(
        f"{base}/approve",
        params={"request_id": staging_request_id},
        json={"approver": "single-approver", "approver_kind": "human"},
        headers=_idempotency_headers(),
    )
    assert staging_applied.status_code == 200, staging_applied.text
    assert staging_applied.json()["lifecycle_state"] == "staging"

    production_pending = await client.post(
        f"{base}/transition",
        json={"to_state": "production", "justification": "promote to production"},
        headers={**_idempotency_headers(), "X-Actor": requester},
    )
    assert production_pending.status_code == 202, production_pending.text
    request_id = production_pending.json()["id"]

    first_approver, second_approver = approvers
    first_approve = await client.post(
        f"{base}/approve",
        params={"request_id": request_id},
        json={"approver": first_approver, "approver_kind": "human"},
        headers=_idempotency_headers(),
    )
    assert first_approve.status_code == 202, first_approve.text

    second_approve = await client.post(
        f"{base}/approve",
        params={"request_id": request_id},
        json={"approver": second_approver, "approver_kind": "human"},
        headers=_idempotency_headers(),
    )
    assert second_approve.status_code == 200, second_approve.text
    assert second_approve.json()["lifecycle_state"] == "production"
    return second_approve.json()


# --------------------------------------------------------------------------- #
# Happy path                                                                   #
# --------------------------------------------------------------------------- #


async def test_happy_path_register_then_read_metadata_lineage_metrics_and_artifact(
    client: AsyncClient,
) -> None:
    version = 1
    artifact_bytes = _preload(client, _MODEL_NAME, version, training_run_id=10)

    register_resp = await client.post(
        "/v1/models",
        json={"model_name": _MODEL_NAME, "version": version, "training_run_id": 10},
        headers=_idempotency_headers(),
    )
    assert register_resp.status_code == 201, register_resp.text
    registered = register_resp.json()
    assert registered["lifecycle_state"] == "registered"
    assert registered["approval_state"] == "not_required"

    detail = await client.get(f"/v1/models/{_MODEL_NAME}/versions/{version}")
    assert detail.status_code == 200
    assert detail.json()["model_name"] == _MODEL_NAME
    assert detail.json()["version"] == version

    metrics = await client.get(f"/v1/models/{_MODEL_NAME}/versions/{version}/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["per_fold"]

    lineage = await client.get(f"/v1/models/{_MODEL_NAME}/versions/{version}/lineage")
    assert lineage.status_code == 200
    assert lineage.json()["dataset_version"]["dataset_name"] == "aapl_5d_direction"

    artifact = await client.get(f"/v1/models/{_MODEL_NAME}/versions/{version}/artifact")
    assert artifact.status_code == 200
    assert artifact.content == artifact_bytes


# --------------------------------------------------------------------------- #
# Full promotion chain                                                        #
# --------------------------------------------------------------------------- #


async def test_full_promotion_chain_through_production_resolves_via_production_endpoint(
    client: AsyncClient,
) -> None:
    version = 1
    await _register(client, _MODEL_NAME, version, training_run_id=11)

    promoted = await _promote_to_production(client, _MODEL_NAME, version)
    assert promoted["version"] == version

    production = await client.get(f"/v1/models/{_MODEL_NAME}/production")
    assert production.status_code == 200
    body = production.json()
    assert body["exists"] is True
    assert body["production"]["version"] == version
    assert body["production"]["lifecycle_state"] == "production"


# --------------------------------------------------------------------------- #
# Rollback path                                                               #
# --------------------------------------------------------------------------- #


async def test_rollback_restores_previous_production_and_demotes_incumbent(
    client: AsyncClient,
) -> None:
    version_1 = 1
    version_2 = 2
    await _register(client, _MODEL_NAME, version_1, training_run_id=21)
    await _register(client, _MODEL_NAME, version_2, training_run_id=22)

    await _promote_to_production(client, _MODEL_NAME, version_1)

    # Promoting version_2 must demote version_1 to DEPRECATED (Req 16.2).
    await _promote_to_production(client, _MODEL_NAME, version_2, approvers=("carol", "dave"))

    version_1_detail = await client.get(f"/v1/models/{_MODEL_NAME}/versions/{version_1}")
    assert version_1_detail.json()["lifecycle_state"] == "deprecated"

    rollback_resp = await client.post(
        f"/v1/models/{_MODEL_NAME}/versions/{version_1}/rollback",
        json={"requester": "requester", "justification": "rolling back to the known-good version"},
        headers=_idempotency_headers(),
    )
    assert rollback_resp.status_code == 202, rollback_resp.text
    rollback_body = rollback_resp.json()
    assert rollback_body["is_rollback"] is True
    request_id = rollback_body["id"]

    first_approve = await client.post(
        f"/v1/models/{_MODEL_NAME}/versions/{version_1}/approve",
        params={"request_id": request_id},
        json={"approver": "erin", "approver_kind": "human"},
        headers=_idempotency_headers(),
    )
    assert first_approve.status_code == 202, first_approve.text

    second_approve = await client.post(
        f"/v1/models/{_MODEL_NAME}/versions/{version_1}/approve",
        params={"request_id": request_id},
        json={"approver": "frank", "approver_kind": "human"},
        headers=_idempotency_headers(),
    )
    assert second_approve.status_code == 200, second_approve.text
    assert second_approve.json()["lifecycle_state"] == "production"

    version_1_after = await client.get(f"/v1/models/{_MODEL_NAME}/versions/{version_1}")
    version_2_after = await client.get(f"/v1/models/{_MODEL_NAME}/versions/{version_2}")
    assert version_1_after.json()["lifecycle_state"] == "production"
    assert version_2_after.json()["lifecycle_state"] == "deprecated"


# --------------------------------------------------------------------------- #
# Property 23: any nonexistent resource yields a typed 404                    #
# --------------------------------------------------------------------------- #


# Feature: model-registry, Property 23: Any nonexistent resource yields a typed 404
# For any identifier that does not correspond to an existing Registered_Model,
# Model_Version, artifact, Promotion_Request, metrics, or lineage, the API
# responds with 404 and a typed error body naming the missing resource.
# Validates: Requirements 19.10
async def test_property_23_unknown_model_version_returns_typed_404(client: AsyncClient) -> None:
    resp = await client.get("/v1/models/nope__xgboost/versions/1")
    assert resp.status_code == 404
    assert "detail" in resp.json()


async def test_property_23_unknown_model_metrics_returns_typed_404(client: AsyncClient) -> None:
    resp = await client.get("/v1/models/nope__xgboost/versions/1/metrics")
    assert resp.status_code == 404
    assert "detail" in resp.json()


async def test_property_23_unknown_model_lineage_returns_typed_404(client: AsyncClient) -> None:
    resp = await client.get("/v1/models/nope__xgboost/versions/1/lineage")
    assert resp.status_code == 404
    assert "detail" in resp.json()


async def test_property_23_unknown_model_artifact_returns_typed_404(client: AsyncClient) -> None:
    resp = await client.get("/v1/models/nope__xgboost/versions/1/artifact")
    assert resp.status_code == 404
    assert "detail" in resp.json()


async def test_property_23_unknown_model_promotion_history_returns_typed_404(
    client: AsyncClient,
) -> None:
    resp = await client.get("/v1/models/nope__xgboost/versions/1/promotion-history")
    assert resp.status_code == 404
    assert "detail" in resp.json()


async def test_property_23_unknown_promotion_request_approve_returns_typed_404(
    client: AsyncClient,
) -> None:
    resp = await client.post(
        "/v1/models/nope__xgboost/versions/1/approve",
        params={"request_id": 999_999},
        json={"approver": "alice", "approver_kind": "human"},
        headers=_idempotency_headers(),
    )
    assert resp.status_code == 404
    assert "detail" in resp.json()


async def test_property_23_unknown_promotion_request_reject_returns_typed_404(
    client: AsyncClient,
) -> None:
    resp = await client.post(
        "/v1/models/nope__xgboost/versions/1/reject",
        params={"request_id": 999_999},
        json={"approver": "alice", "approver_kind": "human", "reason": "n/a"},
        headers=_idempotency_headers(),
    )
    assert resp.status_code == 404
    assert "detail" in resp.json()


# --------------------------------------------------------------------------- #
# Property 24: reads remain available while a promotion dependency degrades   #
# --------------------------------------------------------------------------- #


# Feature: model-registry, Property 24: Reads remain available while promotion dependencies degrade
# While an approval/audit dependency is unavailable, reads of existing
# versions, metrics, lineage, artifacts, and production resolution still
# succeed, while PRODUCTION promotions are refused.
# Validates: Requirements 20.1, 20.2, 23.1
async def test_property_24_reads_succeed_while_a_production_promotion_is_stuck_pending(
    client: AsyncClient,
) -> None:
    """Narrowed interpretation of Property 24 at the API level.

    Simulating an actual outage of the approval/audit repository would
    require swapping out the SQLAlchemy repositories mid-request, but they
    are constructed fresh per request from the request-scoped ``AsyncSession``
    (see ``api/deps.py``) -- there is no seam in this HTTP-level fixture to
    inject a failing repository for one call while leaving others healthy.
    Instead, this test exercises the externally observable half of Property
    24: while a ``PRODUCTION`` promotion is stuck mid-governance (a
    ``PENDING`` ``Promotion_Request`` awaiting a second Four_Eyes approval --
    the same externally-visible state a degraded approval dependency would
    leave a request in), every read endpoint (model version, metrics,
    lineage, artifact, production resolution) continues to serve the
    already-recorded ``Model_Version`` unaffected, and the ``PRODUCTION``
    transition itself is never applied until the gate is satisfied
    (Requirements 20.1, 20.2, 23.1).
    """
    version = 1
    await _register(client, _MODEL_NAME, version, training_run_id=31)
    base = f"/v1/models/{_MODEL_NAME}/versions/{version}"

    validated = await client.post(
        f"{base}/transition",
        json={
            "to_state": "validated",
            "justification": "passed the validation gauntlet",
            "validation_evidence": {"kind": "backtest_report", "reference": "dossier-1"},
        },
        headers=_idempotency_headers(),
    )
    assert validated.status_code == 200

    staging_pending = await client.post(
        f"{base}/transition",
        json={"to_state": "staging", "justification": "stage it"},
        headers=_idempotency_headers(),
    )
    assert staging_pending.status_code == 202
    staging_request_id = staging_pending.json()["id"]
    staging_applied = await client.post(
        f"{base}/approve",
        params={"request_id": staging_request_id},
        json={"approver": "single-approver", "approver_kind": "human"},
        headers=_idempotency_headers(),
    )
    assert staging_applied.status_code == 200

    production_pending = await client.post(
        f"{base}/transition",
        json={"to_state": "production", "justification": "promote to production"},
        headers=_idempotency_headers(),
    )
    assert production_pending.status_code == 202
    request_id = production_pending.json()["id"]

    # Only one of the two required Four_Eyes approvals is recorded: the
    # promotion is left PENDING, mirroring what a degraded approval/audit
    # dependency would produce -- a promotion that cannot complete.
    partial_approve = await client.post(
        f"{base}/approve",
        params={"request_id": request_id},
        json={"approver": "alice", "approver_kind": "human"},
        headers=_idempotency_headers(),
    )
    assert partial_approve.status_code == 202

    # Reads of the already-recorded Model_Version continue to succeed.
    detail = await client.get(base)
    assert detail.status_code == 200
    assert detail.json()["lifecycle_state"] == "staging"

    metrics = await client.get(f"{base}/metrics")
    assert metrics.status_code == 200

    lineage = await client.get(f"{base}/lineage")
    assert lineage.status_code == 200

    artifact = await client.get(f"{base}/artifact")
    assert artifact.status_code == 200

    production = await client.get(f"/v1/models/{_MODEL_NAME}/production")
    assert production.status_code == 200
    # The PRODUCTION promotion was refused (never applied) while the gate
    # remains unsatisfied.
    assert production.json()["exists"] is False


# --------------------------------------------------------------------------- #
# Property 25: reproducibility round trip                                     #
# --------------------------------------------------------------------------- #


# Feature: model-registry, Property 25: Reproducibility round trip
# For any registered Model_Version, its metadata, lineage, and artifact are
# retrievable unchanged, sufficient to reproduce the model independently.
# Validates: Requirements 9.2, 9.4, 26.4
async def test_property_25_reproducibility_round_trip(client: AsyncClient) -> None:
    version = 1
    training_run_id = 41
    artifact_bytes = _preload(client, _MODEL_NAME, version, training_run_id=training_run_id)
    record = _build_record(_MODEL_NAME, version, training_run_id=training_run_id)

    register_resp = await client.post(
        "/v1/models",
        json={
            "model_name": _MODEL_NAME,
            "version": version,
            "training_run_id": training_run_id,
        },
        headers=_idempotency_headers(),
    )
    assert register_resp.status_code == 201, register_resp.text

    detail = await client.get(f"/v1/models/{_MODEL_NAME}/versions/{version}")
    assert detail.status_code == 200
    body = detail.json()

    # Metadata round-trips exactly what was ingested.
    assert body["artifact_checksum"] == record.artifact_checksum
    assert body["checksum_algorithm"] == record.checksum_algorithm
    assert body["feature_versions"] == record.feature_versions
    assert body["dataset_version"]["dataset_name"] == record.dataset_name
    assert body["dataset_version"]["dataset_version"] == record.dataset_version
    assert body["dataset_version"]["dataset_checksum"] == record.dataset_checksum
    assert body["git_commit"] == record.git_commit

    lineage = await client.get(f"/v1/models/{_MODEL_NAME}/versions/{version}/lineage")
    assert lineage.status_code == 200
    lineage_body = lineage.json()
    assert lineage_body["feature_versions"] == record.feature_versions
    assert lineage_body["training_run_id"] == record.training_run_id

    artifact = await client.get(f"/v1/models/{_MODEL_NAME}/versions/{version}/artifact")
    assert artifact.status_code == 200
    assert artifact.content == artifact_bytes
    # The served bytes still checksum to exactly the recorded Model_Checksum --
    # sufficient to reproduce the model independently (Requirement 9.4).
    assert compute_checksum(artifact.content, record.checksum_algorithm) == record.artifact_checksum
