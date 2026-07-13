"""End-to-end API integration tests against a real Postgres (task 9.2).

Exercises the full stack (FastAPI routes -> services -> SQLAlchemy
repositories -> Postgres + local artifact store) with the Dataset Builder
client swapped for an in-memory fake — no live Dataset Builder required.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_happy_path_train_then_fetch_artifact(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/training-runs",
        json={
            "dataset_name": "aapl_5d_direction",
            "build_run_id": 42,
            "model_types": ["logistic_regression"],
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "succeeded"
    assert len(body["trained_model_ids"]) == 1

    model_name = "aapl_5d_direction__logistic_regression"

    listed = await client.get("/v1/trained-models", params={"model_name": model_name})
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    metadata = await client.get(f"/v1/trained-models/{model_name}/versions/1/metadata")
    assert metadata.status_code == 200
    assert metadata.json()["dataset_checksum"]
    assert metadata.json()["git_commit"] == "testsha"

    metrics = await client.get(f"/v1/trained-models/{model_name}/versions/1/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["per_fold_metrics"]

    artifact = await client.get(f"/v1/trained-models/{model_name}/versions/1/artifact")
    assert artifact.status_code == 200
    assert len(artifact.content) > 0


async def test_get_run_returns_report(client: AsyncClient) -> None:
    created = await client.post(
        "/v1/training-runs",
        json={
            "dataset_name": "aapl_5d_direction",
            "build_run_id": 42,
            "model_types": ["random_forest"],
        },
    )
    run_id = created.json()["id"]
    fetched = await client.get(f"/v1/training-runs/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "succeeded"


async def test_unsupported_model_type_rejected_422(client: AsyncClient) -> None:
    resp = await client.post(
        "/v1/training-runs",
        json={
            "dataset_name": "aapl_5d_direction",
            "build_run_id": 42,
            "model_types": ["not_a_real_model"],
        },
    )
    assert resp.status_code == 422
    assert "not_a_real_model" in resp.text


async def test_build_run_not_found_maps_to_404(client: AsyncClient) -> None:
    client.fake_dataset_builder.not_found = True  # type: ignore[attr-defined]
    resp = await client.post(
        "/v1/training-runs",
        json={
            "dataset_name": "aapl_5d_direction",
            "build_run_id": 999,
            "model_types": ["logistic_regression"],
        },
    )
    assert resp.status_code == 404


async def test_upstream_error_maps_to_502(client: AsyncClient) -> None:
    client.fake_dataset_builder.upstream_error = True  # type: ignore[attr-defined]
    resp = await client.post(
        "/v1/training-runs",
        json={
            "dataset_name": "aapl_5d_direction",
            "build_run_id": 42,
            "model_types": ["logistic_regression"],
        },
    )
    assert resp.status_code == 502


# Feature: training-pipeline, Property 25: any nonexistent resource id yields a typed 404.
async def test_property_25_unknown_run_404(client: AsyncClient) -> None:
    resp = await client.get("/v1/training-runs/999999")
    assert resp.status_code == 404
    assert "detail" in resp.json()


async def test_property_25_unknown_model_metadata_404(client: AsyncClient) -> None:
    resp = await client.get("/v1/trained-models/nope__xgboost/versions/1/metadata")
    assert resp.status_code == 404


async def test_property_25_unknown_model_metrics_404(client: AsyncClient) -> None:
    resp = await client.get("/v1/trained-models/nope__xgboost/versions/1/metrics")
    assert resp.status_code == 404


async def test_property_25_unknown_model_artifact_404(client: AsyncClient) -> None:
    resp = await client.get("/v1/trained-models/nope__xgboost/versions/1/artifact")
    assert resp.status_code == 404


async def test_readiness_reports_health_checks(client: AsyncClient) -> None:
    resp = await client.get("/health/ready")
    assert resp.status_code == 200
    checks = {c["name"]: c["healthy"] for c in resp.json()["checks"]}
    assert checks["database"] is True
    assert checks["dataset_builder_service"] is True
