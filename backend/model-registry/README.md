# model-registry

The single, authoritative source of truth for every trained model on the
AQROS platform. The Model Registry ingests trained-model records exclusively
from the Training Pipeline service's published REST API, records each as an
immutable, fully-lineaged model version, governs each through a
strictly-ordered lifecycle behind a four-eyes promotion gate, and serves model
metadata, metrics, lineage, and artifacts to every downstream consumer
(CLAUDE.md Hard Rule §7.4).

**This service never trains, evaluates, or serves inference.** It records,
versions, governs, and vends. It does not build datasets, compute features,
re-score metrics, or make any trading, sizing, or risk decision. A model
reaches production use only through an explicit, human-approved promotion — it
is never auto-promoted.

## Service boundary: one upstream, never its database

The Model Registry reads from exactly **one** upstream service — the
**Training Pipeline Service** — and only through its published REST API
(CLAUDE.md §7.9 — never another service's database or internals):

- `GET /v1/trained-models/{model_name}/versions/{version}/metadata` — the
  trained model's reproducibility metadata.
- `GET /v1/trained-models/{model_name}/versions/{version}/metrics` — the
  per-fold and aggregated metrics plus feature importance.
- `GET /v1/trained-models/{model_name}/versions/{version}/artifact` — the
  serialized model artifact bytes.

The Registry never opens a database connection to the Training Pipeline's,
Dataset Builder's, Feature Store's, or Market Data's databases, and it contacts
the Training Pipeline only at registration time. It owns its own Postgres
database (metadata only) and its own artifact store (model bytes).

**Downstream services never query the Training Pipeline.** Because the Registry
is the single source of truth, it exposes every model read that downstream
consumers — Backtesting, Strategy Engine, Paper Trading, Live Trading, and the
AI Brain — require. On registration the Registry downloads the artifact bytes
from the Training Pipeline, verifies their checksum, and persists an
independent immutable copy in its own artifact store, so that downstream
artifact retrieval never contacts the Training Pipeline.

## Model lifecycle

Every model version moves only through a strictly-ordered lifecycle:

```
REGISTERED → VALIDATED → STAGING → PRODUCTION → DEPRECATED → ARCHIVED
```

with an explicit, audited rollback edge (`DEPRECATED → PRODUCTION`) that
re-promotes a previously-production version, and an abandonment edge from any
non-`PRODUCTION` state directly to `ARCHIVED`. Promotion into `PRODUCTION` (and
rollback) requires four-eyes approval by two distinct human approvers, neither
of whom is the requester. `ARCHIVED` is terminal.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/models` | Register a model version from a Training Pipeline reference (idempotent) |
| `GET` | `/v1/models` | List model versions (optionally filtered by model name / lifecycle state) |
| `GET` | `/v1/models/{model_name}/versions/{version}` | Full metadata + lineage + reproducibility metadata |
| `GET` | `/v1/models/{model_name}/versions/{version}/metrics` | Metrics record |
| `GET` | `/v1/models/{model_name}/versions/{version}/lineage` | Full lineage chain |
| `GET` | `/v1/models/{model_name}/versions/{version}/artifact` | Download the verified model artifact |
| `POST` | `/v1/models/{model_name}/versions/{version}/transition` | Request a lifecycle transition |
| `POST` | `/v1/models/{model_name}/versions/{version}/approve` | Approve a pending promotion request |
| `POST` | `/v1/models/{model_name}/versions/{version}/reject` | Reject a pending promotion request |
| `POST` | `/v1/models/{model_name}/versions/{version}/rollback` | Request a rollback to this previously-production version |
| `GET` | `/v1/models/{model_name}/production` | Resolve the current production model |
| `GET` | `/v1/models/{model_name}/versions/{version}/promotion-history` | Ordered promotion history |
| `GET` | `/v1/models/{model_name}/versions/{version}/audit` | Audit history (filterable by correlation id) |
| `GET` | `/health`, `/health/live`, `/health/ready` | Health probes (readiness checks DB + artifact store) |
| `GET` | `/docs`, `/openapi.json` | OpenAPI docs |

## Running locally

Via Docker Compose (brings up the Training Pipeline and its own upstreams too):

```bash
docker compose up -d training-pipeline-db training-pipeline \
  model-registry-db model-registry
docker exec -w /app/backend/model-registry <container> python -m alembic upgrade head
curl localhost:8004/health/ready
```

Without Docker, against a local Postgres:

```bash
uv sync --group dev
uv run --package aqros-model-registry alembic -c backend/model-registry/alembic.ini upgrade head
uv run --package aqros-model-registry uvicorn aqros_model_registry.app:app --port 8004
```

## Configuration (env vars, prefix `AQROS_`)

| Variable | Default | Purpose |
|---|---|---|
| `AQROS_DATABASE_URL` | `postgresql+asyncpg://aqros:aqros@localhost:5436/aqros_model_registry` | Metadata-only database |
| `AQROS_TRAINING_PIPELINE_BASE_URL` | `http://localhost:8009` | Training Pipeline REST API base URL (sole upstream dependency) |
| `AQROS_ARTIFACT_DIR` | `/data/model-registry/artifacts` | Local, versioned model artifact directory |

## Port / database assignments

| Setting | Value |
|---|---|
| Service port | `8004` |
| Postgres port | `5436` |
| Database name | `aqros_model_registry` |

This continues the existing port sequence: market-data 8002/5432,
feature-store 8003/5433, dataset-builder 8008/5434, training-pipeline 8009/5435,
model-registry 8004/5436.

Model artifacts are persisted to a **local artifact directory** behind the
`ArtifactStore` interface in the MVP, mounted as a dedicated volume. An
object-store-backed implementation (S3/MinIO/R2) can be configured later
without any change to domain or API logic — no object-store dependency is
introduced.

## Tests

- `tests/unit/` — pure domain logic: lifecycle state machine, approval /
  four-eyes policy, checksum integrity gates, lineage assembly and
  mandatory-metadata completeness, versioning immutability, and the registry
  service, all against fakes for every port.
- `tests/integration/` — real Postgres via testcontainers, the full API via
  `httpx.AsyncClient` with a faked Training Pipeline client (no live Training
  Pipeline instance required), a real local artifact store, and an Alembic
  schema check.

```bash
uv run pytest backend/model-registry                       # unit + integration
uv run pytest backend/model-registry -m "not integration"   # unit only, no Docker
```
