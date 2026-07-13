# dataset-builder

Transforms engineered features into reproducible, point-in-time-correct
machine learning datasets. This is the AQROS Phase 3 slice of the
`datasets/` layer described in CLAUDE.md §3 ("Dataset/feature/label
DEFINITIONS... not raw data") and `docs/claude_MLResearchFramework.md` §2
(Stage 3: "assemble the point-in-time-correct training/research dataset").

**This service never trains a model.** Its only responsibility is preparing
datasets — reading, aligning, labeling, splitting, auditing, and persisting.

## Service boundary: two upstreams, never their databases

The Dataset Builder reads from **two** upstream services, exclusively
through their published REST APIs (CLAUDE.md §7.9 — never another
service's database or internals):

- **Market Data Service** — OHLCV bars, used *only* to compute labels.
  Labels are legitimately allowed to use future prices
  (`docs/claude_ROI.md` §18.2: "a label at time t legitimately uses future
  prices... that's the point").
- **Feature Store Service** — engineered feature values, used to build the
  X matrix. These are already strictly causal by construction in
  feature-store, so no additional point-in-time enforcement is needed here
  beyond trusting that boundary.

This is why the requirement "read engineered features from the Feature
Store API" is necessary but not sufficient — a supervised dataset also
needs a *label*, and a label can only be computed honestly from prices, not
from already-computed (necessarily backward-looking) features.

## Architecture (ports-and-adapters)

- `domain/` — pure business logic, no I/O:
  - `models.py` — `DatasetDefinition` (immutable, versioned), `DatasetBuildRun`
    (the audit trail / "leakage-clearance certificate"), `DatasetManifest`,
    `DatasetQualityReport`, split-parameter types.
  - `labels.py` — the three label families (binary direction, future return,
    volatility), computed from future *prices only*, never features.
  - `splitters.py` — the four split algorithms (walk-forward, rolling
    window, expanding window, purged CV with embargo), all strictly
    temporal — no random shuffling of time.
  - `validation.py` — the automated leakage audit: fold-integrity, purge/
    embargo, and finite-value checks.
  - `quality.py` — dataset quality metrics: missing values, duplicate rows,
    class balance, per-feature statistics, and basic data-quality
    validation (non-blocking; informational).
  - `manifest.py` — assembles the versioned reproducibility manifest
    (checksum, feature versions, label definition, git commit, source URLs).
  - `ports.py` — `MarketDataSource`, `FeatureSource`,
    `DatasetDefinitionRepository`, `DatasetBuildRunRepository`,
    `DatasetStorage`, `GitInfoProvider`.
  - `services.py` — `DatasetBuilderService` (the full pipeline) and
    `DatasetQueryService` (definitions, runs, preview, manifest).
- `adapters/` — concrete I/O:
  - `market_data_client.py` / `feature_store_client.py` — httpx-based
    clients against each upstream's REST API (paginated, retried).
  - `parquet_storage.py` — local-filesystem Parquet + JSON-manifest
    artifact storage, behind the real `DatasetStorage` interface (swappable
    for S3/R2/MinIO later without touching domain/API code — CLAUDE.md §9).
  - `git_info.py` — looks up the current git commit SHA for the manifest
    (degrades to `None` gracefully if not run inside a git checkout).
  - `db.py` / `orm.py` / `repository.py` — async SQLAlchemy 2.0 engine, ORM
    models, and Postgres-backed repository implementations for *metadata
    only* (definitions + build-run audit trail — the actual dataset rows
    live in Parquet, never in Postgres).
- `api/` — FastAPI routes (`routes/datasets.py`, `routes/build_runs.py`),
  Pydantic schemas (`schemas.py`), and DI wiring (`deps.py`).
- `migrations/` — Alembic migrations (`dataset_definitions`, `dataset_build_runs`).

## Labels

| Label type | Formula | Notes |
|---|---|---|
| `binary_direction` | 1.0 if `close[t+h] > close[t]` else 0.0 | Direction classification |
| `future_return` | `(close[t+h] / close[t]) - 1` | Simple forward return |
| `volatility` | std of one-bar log returns over `(t, t+h]` | Forward realized volatility |

Horizons: `1d`, `5d`, `20d` — expressed in trading bars, not calendar days.

## Split strategies

| Strategy | Shape | Use case |
|---|---|---|
| `walk_forward` | Many sequential folds, fixed-size sliding window | Simulates periodic retraining through history |
| `rolling_window` | One fold, fixed-size *most recent* window | Non-stationary markets; discard stale regimes |
| `expanding_window` | One fold, train grows to include all prior history | Stable relationships; more data helps |
| `purged_cv` | k contiguous time blocks as test, each with a purge + embargo gap | The gold-standard defense against label-window leakage (López de Prado) |

Every split is strictly temporal — validation/test indices are always later
in time than the train indices in the same fold. Random k-fold is never
offered as an option; `docs/claude_MLResearchFramework.md` §8 calls this
"FORBIDDEN" for financial data.

## Leakage prevention

Every build run is validated before it may be persisted:

- **Fold integrity** — no train index may fall inside a purged/embargoed
  test window; forward-looking strategies must have train chronologically
  before validation before test.
- **Finite-value check** — every feature and label value must be finite
  (no NaN/Inf reaches a persisted row).
- **Missing-data rejection** — rows missing the label (trailing horizon
  bars) or *any* requested feature are dropped, never fabricated.

A run whose audit fails is recorded (status still `succeeded`, but
`leakage_audit_passed=false` with the specific findings) and its Parquet
artifact and manifest are **not** written — a failed audit blocks
persistence.

## Dataset manifest

Every successful build writes a JSON manifest alongside the Parquet
artifact, containing everything needed to understand and reproduce the
dataset (CLAUDE.md §5: "Any model, dataset, feature, or result reconstructs
bit-for-bit from an immutable manifest"):

- Dataset name, version, and build run id
- Symbols, feature names, and each feature's registered version
- Label type and its exact definition
- Horizon, split strategy, and split parameters
- Date range and creation timestamp
- Row count and a SHA-256 checksum of the Parquet artifact
- The git commit that produced it (when available)
- The upstream Market Data / Feature Store URLs used
- The full quality report (see below)

## Quality metrics

Every build computes a `DatasetQualityReport`, independent of the leakage
audit (quality issues are informational; they never block persistence):

- **Missing values** — null counts per column, over the raw joined data
  before row-level cleaning.
- **Duplicate rows** — exact duplicates by (symbol, event_time, fold, split_role).
- **Class balance** — per-role positive-fraction, for binary labels.
- **Feature statistics** — count, missing count, mean, std, min, max per feature.
- **Basic validation findings** — e.g. a feature missing >50% of its values
  over the requested range, or a feature with zero variance.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/datasets` | Register a new dataset definition (auto-versioned) |
| `GET` | `/v1/datasets` | List every registered definition |
| `GET` | `/v1/datasets/{name}` | Latest version of a definition |
| `GET` | `/v1/datasets/{name}/versions/{version}` | One specific version |
| `POST` | `/v1/datasets/{name}/build` | Run the pipeline for a definition version |
| `GET` | `/v1/datasets/{name}/runs/{run_id}/preview` | Preview generated rows |
| `GET` | `/v1/runs` | List build runs (optionally filtered by dataset) |
| `GET` | `/v1/runs/{run_id}` | Fetch one build run (incl. leakage-audit & quality report) |
| `GET` | `/v1/runs/{run_id}/manifest` | Fetch the reproducibility manifest |
| `GET` | `/v1/runs/{run_id}/download` | Download the Parquet artifact |
| `GET` | `/health`, `/health/live`, `/health/ready` | Health probes (readiness checks DB + both upstreams) |
| `GET` | `/docs`, `/openapi.json` | OpenAPI docs |

## Running locally

Via Docker Compose (brings up market-data, feature-store, and their DBs too):

```bash
docker compose up -d market-data-db market-data feature-store-db feature-store \
  dataset-builder-db dataset-builder
docker exec -w /app/backend/dataset-builder <container> python -m alembic upgrade head
curl localhost:8008/health/ready
```

## Configuration (env vars, prefix `AQROS_`)

| Variable | Default | Purpose |
|---|---|---|
| `AQROS_DATABASE_URL` | `postgresql+asyncpg://aqros:aqros@localhost:5434/aqros_dataset_builder` | Metadata-only database |
| `AQROS_MARKET_DATA_BASE_URL` | `http://localhost:8002` | Market Data REST API base URL |
| `AQROS_FEATURE_STORE_BASE_URL` | `http://localhost:8003` | Feature Store REST API base URL |
| `AQROS_DATASET_ARTIFACT_DIR` | `/data/dataset-builder/artifacts` | Local Parquet + manifest artifact directory |
| `AQROS_GIT_REPO_ROOT` | `/app` | Working directory for the manifest's `git_commit` lookup |

## Tests

- `tests/unit/` — pure domain logic: labels (hand-verified formulas),
  splitters (temporal ordering, purge/embargo correctness), the leakage
  audit, quality metrics, manifest assembly, and pipeline orchestration
  against fakes for every port.
- `tests/integration/` — real Postgres via testcontainers, the full API via
  `httpx.AsyncClient` with faked upstreams, and an Alembic schema check.

```bash
uv run pytest backend/dataset-builder                      # unit + integration
uv run pytest backend/dataset-builder -m "not integration"  # unit only, no Docker
```
