# training-pipeline

Trains and evaluates candidate machine learning models on datasets produced
by the Dataset Builder service. This is the AQROS Phase 4 slice that sits
directly downstream of the `datasets/` layer described in CLAUDE.md §3 and
`docs/claude_MLResearchFramework.md` (Stage 4: fit and evaluate candidate
models on the point-in-time-correct dataset already assembled in Stage 3).

**This service never generates or builds a dataset.** Its only
responsibility is training and evaluating: verifying a Dataset Builder
build run is trustworthy, fitting each requested model type per fold using
the fold/split-role structure the Dataset Builder already assigned,
evaluating per fold and in aggregate, extracting feature importance, and
persisting versioned, reproducible model artifacts and metadata.

## Service boundary: one upstream, never its database

The Training Pipeline reads from exactly **one** upstream service — the
**Dataset Builder Service** — and only through its published REST API
(CLAUDE.md §7.9 — never another service's database or internals):

- `GET /v1/runs/{run_id}` — the Dataset Builder build-run record, including
  `leakage_audit_passed` / `leakage_audit_findings`.
- `GET /v1/runs/{run_id}/manifest` — the dataset's reproducibility manifest
  (checksum, feature names/versions, label definition, split strategy, etc).
- `GET /v1/runs/{run_id}/download` — the Parquet dataset artifact itself.

The Training Pipeline never opens a database connection to the Dataset
Builder's, Market Data's, or Feature Store's databases, and never issues an
HTTP request to the Market Data Service or the Feature Store Service —
there is nothing to disable for either, because neither is wired up in the
first place. It owns its own Postgres database (metadata only) and its own
local artifact store (model bytes).

**This service never promotes a model to production or live trading use.**
It trains and records candidates only — model promotion is the
responsibility of the future Model Registry service (CLAUDE.md Hard Rule
§7.4). No "promoted" flag or promotion endpoint exists anywhere in this
codebase.

## Pre-training verification

Before any training happens, every build run is checked against two
independent gates, both of which must pass:

- **Checksum verification** — the downloaded Parquet artifact's checksum
  (computed with the algorithm named in the manifest's
  `checksum_algorithm`) must equal the manifest's recorded `checksum`.
- **Leakage audit gate** — the build run's `leakage_audit_passed` field
  must be `true`. A `false` or `null` value rejects the request and
  surfaces the build run's `leakage_audit_findings` as the rejection
  reason.

Training only proceeds if both gates pass. The Training Pipeline never
re-splits, re-labels, shuffles, reorders, or resamples the rows it reads —
it groups strictly by the dataset's existing `fold` and `split_role`
columns, verbatim.

## Model types

Four baseline model classes are selectable per training request:

| Model type | Estimator |
|---|---|
| `logistic_regression` | Regularized logistic regression |
| `random_forest` | Random forest classifier |
| `xgboost` | Gradient-boosted trees (XGBoost) |
| `lightgbm` | Gradient-boosted trees (LightGBM) |

Every trained model is versioned independently per `{dataset_name}__{model_type}`
— a monotonically incrementing integer, never reused or decremented, scoped
so that different datasets never share a version counter.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/training-runs` | Create and synchronously execute a training run |
| `GET` | `/v1/training-runs/{run_id}` | Retrieve status + training report of a run |
| `GET` | `/v1/trained-models` | List trained models (optionally filtered by model name) |
| `GET` | `/v1/trained-models/{model_name}/versions/{version}/metadata` | Reproducibility metadata for one trained model |
| `GET` | `/v1/trained-models/{model_name}/versions/{version}/metrics` | Metrics report (per-fold + aggregated metrics, feature importance) |
| `GET` | `/v1/trained-models/{model_name}/versions/{version}/artifact` | Download the serialized model artifact |
| `GET` | `/health`, `/health/live`, `/health/ready` | Health probes (readiness checks DB + the Dataset Builder API) |
| `GET` | `/docs`, `/openapi.json` | OpenAPI docs |

## Running locally

Via Docker Compose (brings up dataset-builder and its own upstreams too):

```bash
docker compose up -d market-data-db market-data feature-store-db feature-store \
  dataset-builder-db dataset-builder training-pipeline-db training-pipeline
docker exec -w /app/backend/training-pipeline <container> python -m alembic upgrade head
curl localhost:8009/health/ready
```

Without Docker, against a local Postgres:

```bash
uv sync --group dev
uv run --package aqros-training-pipeline alembic -c backend/training-pipeline/alembic.ini upgrade head
uv run --package aqros-training-pipeline uvicorn aqros_training_pipeline.app:app --port 8009
```

## Configuration (env vars, prefix `AQROS_`)

| Variable | Default | Purpose |
|---|---|---|
| `AQROS_DATABASE_URL` | `postgresql+asyncpg://aqros:aqros@localhost:5435/aqros_training_pipeline` | Metadata-only database |
| `AQROS_DATASET_BUILDER_BASE_URL` | `http://localhost:8008` | Dataset Builder REST API base URL (sole upstream dependency) |
| `AQROS_ARTIFACT_DIR` | `/data/training-pipeline/artifacts` | Local, versioned model artifact directory |

## Port / database assignments

| Setting | Value |
|---|---|
| Service port | `8009` |
| Postgres port | `5435` |
| Database name | `aqros_training_pipeline` |

This continues the existing port sequence: market-data 8002/5432,
feature-store 8003/5433, dataset-builder 8008/5434, training-pipeline
8009/5435.

## Tests

- `tests/unit/` — pure domain logic: pre-training verification (checksum +
  leakage AND-gate), fold/split-role partitioning, model training dispatch,
  per-fold and aggregated evaluation metrics, feature importance
  extraction, model versioning, and report generation, all against fakes
  for every port.
- `tests/integration/` — real Postgres via testcontainers, the full API via
  `httpx.AsyncClient` with a faked Dataset Builder client (no live Dataset
  Builder instance required), and an Alembic schema check.

```bash
uv run pytest backend/training-pipeline                      # unit + integration
uv run pytest backend/training-pipeline -m "not integration"  # unit only, no Docker
```
