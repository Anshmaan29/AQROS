# Design Document: Training Pipeline

## Overview

The Training Pipeline (`backend/training-pipeline`, module `aqros_training_pipeline`) is a new AQROS backend microservice that trains and evaluates candidate ML models against datasets produced by the Dataset Builder service. It never touches Market Data, Feature Store, or any other service's database — it consumes exactly one upstream dependency, the Dataset Builder's published REST API, and owns its own Postgres database and local artifact store.

The service follows the domain/adapters/api layering already established by `aqros_market_data`, `aqros_feature_store`, and `aqros_dataset_builder`: pure domain logic behind ports, concrete I/O adapters implementing those ports, and a thin FastAPI layer wiring everything together via dependency injection. The Dataset Builder service is used as the direct implementation template throughout this design — file names, class shapes, and conventions are carried over 1:1 wherever they fit, and every deliberate departure is called out explicitly.

## Key Design Decisions

Each decision below is stated explicitly with its rationale, per the interpretive points the requirements leave open.

### 1. Artifact serialization format: uniform `joblib` for all four Model_Types

**Decision:** Serialize every `Model_Artifact` — `logistic_regression`, `random_forest`, `xgboost`, and `lightgbm` — with `joblib.dump`/`joblib.load`, never a per-model-type format.

**Rationale:** `scikit-learn`'s `LogisticRegression`/`RandomForestClassifier` are joblib-picklable by design. XGBoost and LightGBM both ship scikit-learn-compatible wrapper estimators (`xgboost.XGBClassifier`, `lightgbm.LGBMClassifier`) that are equally joblib-picklable — there is no need to drop to each library's native booster format (`.json`/`.txt`) to get a working model. A single uniform format keeps the `Artifact_Store` port's contract to plain bytes in/bytes out (Requirement 13.4's "swappable interface" requirement), with zero model-type branching anywhere above the `Model_Trainer`.

### 2. One Training_Run may train multiple Model_Types; each type versions independently

**Decision:** A single `Training_Request` may list 1–4 `Model_Types` (Requirement 7.1). Each requested `Model_Type` produces its own `Trained_Model`, and each is assigned its own `Model_Version` from its own model name's version sequence — training `logistic_regression` and `random_forest` together in one run never causes them to share or interleave version numbers.

### 3. "Model name" (Requirement 8) is scoped to model identity **and** dataset lineage: `{dataset_name}__{model_type}`

**Decision (revised per user feedback):** A `Trained_Model`'s "model name" is the composite string `f"{dataset_name}__{model_type}"` — e.g. `"aapl_5d_direction__random_forest"` — not the bare `Model_Type` value. `Model_Version` (Requirement 8) is assigned and incremented per this composite name, so training `random_forest` against `aapl_5d_direction` and training `random_forest` against `msft_20d_return` are two independent version sequences, each starting at 1.

**Scoping to `dataset_name`, not `dataset_name` + `dataset_version`:** the composite key uses the dataset's *name* only, not its exact version. Retraining the same `Model_Type` against a newer `dataset_version` of the same named dataset (its ongoing "lineage") is treated as a new candidate in the *same* versioning sequence — it gets the next `Model_Version`, not a reset to 1 — because it is still logically the same model-on-dataset identity evolving over time. Only a genuinely different dataset *name* starts a new, independent version sequence.

**Rationale:** This directly implements the user's explicit correction that different datasets must not share one monotonically increasing counter. Requirement 8's "per model name" language is satisfied literally — `model_name` is still a single string key the versioning logic treats opaquely — it is just constructed from two identity components instead of one. `Reproducibility_Metadata` (Requirement 12.1) continues to record `dataset_name`/`dataset_version` explicitly, so this change does not remove any existing traceability; it only changes what counts as "the same model" for version-counter purposes.

**Consequence for storage/URLs:** every place that takes a `model_name` (the `Artifact_Store` path, the `(model_name, model_version)` unique constraint, and the REST path parameters in Section 6) now takes this composite string. `dataset_name` values must not themselves contain the `__` separator (enforced by the same `name` validation the Dataset Builder already applies to its own dataset names); if this ever proves too restrictive, the separator can be swapped for a character guaranteed disjoint from valid dataset names without changing any other part of this design.

### 4. ROC AUC / `predict_proba` availability across all four Model_Types

**Confirmed:** All four required estimators — `sklearn.linear_model.LogisticRegression`, `sklearn.ensemble.RandomForestClassifier`, `xgboost.XGBClassifier`, and `lightgbm.LGBMClassifier` — implement the scikit-learn `predict_proba` interface, so ROC AUC (Requirement 9.1) can always be computed from a common code path with no per-model-type special-casing, except for the single-class-test-fold exclusion already required by Requirement 9.3.

### 5. Hyperparameter defaults

**Decision:** When a `Training_Request` omits hyperparameters for a requested `Model_Type`, the `Model_Trainer` fits that type with platform-chosen defaults (below) rather than rejecting the request — Requirement 7 never says hyperparameters are mandatory, only that a `Model_Type` must be specified.

| Model_Type | Default hyperparameters |
|---|---|
| `logistic_regression` | `penalty="l2"`, `C=1.0`, `solver="lbfgs"`, `max_iter=1000` |
| `random_forest` | `n_estimators=200`, `max_depth=None`, `random_state=42` |
| `xgboost` | `n_estimators=200`, `max_depth=6`, `learning_rate=0.1`, `random_state=42` |
| `lightgbm` | `n_estimators=200`, `max_depth=-1`, `learning_rate=0.1`, `random_state=42` |

Any hyperparameter the caller does supply overrides the corresponding default; unspecified ones fall back to the table above.

### 6. Dataset_Builder_Client retry policy — resolved as **zero retries, immediate fail-fast**

**Decision:** The `Dataset_Builder_Client` performs **no retry of any kind** — not against another source, and not a bounded retry against the Dataset Builder endpoint itself. The first error response or connection failure on any of the three calls (`GET /v1/runs/{run_id}`, `GET /v1/runs/{run_id}/manifest`, `GET /v1/runs/{run_id}/download`) immediately fails the `Training_Run`.

**Rationale:** This was flagged as a genuinely ambiguous interpretive point — Requirement 1.4's literal text forbids retrying "against any other data source," which does not, on its own, forbid a small bounded retry against the *same* Dataset Builder endpoint for a transient connection blip. However, the same sentence also says the client "SHALL immediately fail the Training_Run... without retrying" — the word "immediately" describes the reaction to *any* error/unreachability, not just after some retry budget is exhausted. Reading "immediately" and "without retrying" together leaves no textual room for an intervening retry loop of any kind. Per the fallback guidance for genuinely ambiguous cases, this design defaults to zero retries / immediate fail-fast, which is unambiguously compliant with the literal acceptance criterion text. This is a deliberate departure from the retry-with-backoff pattern in `adapters/market_data_client.py`, which is dropped entirely here — see Component 3 below.

### 7. Synchronous training-run execution (no background job queue) in the MVP

**Decision:** `POST /v1/training-runs` executes the full pipeline (verification → training → evaluation → persistence) synchronously within the request/response cycle, exactly mirroring `aqros_dataset_builder`'s `POST /v1/datasets/{name}/build` pattern (`await service.build_dataset(...)` inline in the route handler).

**Rationale:** Requirements 14.1–14.2 only require an endpoint to "create a new Training_Run" and a separate endpoint to retrieve its status/report — they do not require asynchronous execution. Matching the Dataset Builder's existing synchronous-trigger convention keeps the two services architecturally consistent and avoids introducing a task queue dependency not required by any acceptance criterion. A future phase could move training to a background worker without changing the REST contract's shape (the status-polling endpoint already exists); that is out of scope here.

### 8. Multi-Model_Type partial failure within one Training_Run (revised per user feedback)

**Decision:** If **any** requested `Model_Type` raises an unexpected exception during fitting/evaluation, the `Training_Run` as a whole is marked `failed` — even if one or more other requested `Model_Types` in the same run trained successfully. A `Training_Run` reports `succeeded` **only if every** requested `Model_Type` produced a `Trained_Model`; it is `failed` if pre-training verification (Requirements 2–4) fails, if the zero-evaluable-folds check (Requirement 6.4) rejects the run, or if **any** requested `Model_Type` fails to train.

**Successful artifacts are still retained even when the run is marked failed:** any `Model_Type` that trained successfully before a sibling `Model_Type` failed still has its `Trained_Model` row persisted, its `Model_Artifact` written, and its `Model_Version` assigned exactly as it would be in a fully successful run — none of that is rolled back. The `Training_Run`'s own status is simply `failed`, and its `Training_Report` lists which `Model_Types` succeeded (with their `Trained_Model` ids, retrievable via the normal `GET /v1/trained-models` and per-model-type endpoints for debugging or later analysis) and which failed (with the error). A `failed` `Training_Run` can therefore still have one or more real, queryable `Trained_Model` children — this is a deliberate, explicit exception to the usual assumption that `failed` implies "nothing was produced."

**Rationale:** The user explicitly rejected "succeeds if at least one type trains" — a `Training_Run`'s status must accurately reflect whether *all* of what was requested was delivered, not treat a partial result as full success. At the same time, discarding already-successfully-trained artifacts on a sibling's failure would waste real compute and destroy debuggable state for no requirement-driven reason, so persistence of individually-successful `Trained_Model`s is kept unconditional on the outcome of other `Model_Types` in the same run. See the Failure State Machine section for the exact transition table.

## 1. Overall Architecture

```mermaid
graph TB
    subgraph "Training Pipeline Service (port 8009)"
        API[Training_API<br/>FastAPI routes]
        SVC[TrainingPipelineService /<br/>TrainingQueryService]
        VERIFY[Pre_Training_Verifier]
        TRAIN[Model_Trainer]
        EVAL[Evaluation_Engine]
        FIMP[Feature_Importance_Extractor]
        REPORT[Report_Generator]
        subgraph Ports
            P1[DatasetBuilderClient]
            P2[ArtifactStore]
            P3[TrainedModelRepository]
            P4[TrainingRunRepository]
            P5[GitInfoProvider]
        end
        API --> SVC
        SVC --> VERIFY
        SVC --> TRAIN
        SVC --> EVAL
        SVC --> FIMP
        SVC --> REPORT
        SVC --> P1
        SVC --> P2
        SVC --> P3
        SVC --> P4
        SVC --> P5
    end

    subgraph Adapters
        A1[HttpDatasetBuilderClient]
        A2[LocalArtifactStore]
        A3[SqlAlchemyTrainedModelRepository]
        A4[SqlAlchemyTrainingRunRepository]
        A5[SubprocessGitInfoProvider]
    end
    P1 -.implements.-> A1
    P2 -.implements.-> A2
    P3 -.implements.-> A3
    P4 -.implements.-> A4
    P5 -.implements.-> A5

    A1 -->|"REST only:<br/>GET /v1/runs/{id}<br/>GET /v1/runs/{id}/manifest<br/>GET /v1/runs/{id}/download"| DB_SVC[Dataset Builder Service<br/>port 8008]
    A3 --> PG[(training-pipeline-db<br/>Postgres :5435)]
    A4 --> PG
    A2 --> FS[(Local filesystem<br/>artifact volume)]

    DB_SVC -.->|"never queried directly by<br/>Training Pipeline"| MD[Market Data DB]
    DB_SVC -.->|"never queried directly by<br/>Training Pipeline"| FSDB[Feature Store DB]
```

The Training Pipeline never opens a connection to the Dataset Builder's, Market Data's, or Feature Store's databases (Requirement 1.2), and never issues an HTTP request to Market Data or Feature Store (Requirements 1.3, 18.2, 18.3). Its only outbound network dependency is the Dataset Builder's REST API.

## 2. Data Flow

```mermaid
flowchart LR
    A[Training_Request<br/>via REST] --> B[Fetch Dataset_Manifest<br/>+ Dataset_Build_Run]
    B -->|404 at either step| X1[Reject: run not found]
    B -->|both succeed| C[Download Dataset_Artifact]
    C -->|upstream error/unreachable| X2[Fail Training_Run<br/>no retry]
    C -->|downloaded| D[Compute checksum<br/>via manifest.checksum_algorithm]
    D -->|mismatch| X3[Reject: checksum mismatch]
    D -->|match| E[Inspect leakage_audit_passed]
    E -->|false/null| X4[Reject: leakage audit findings]
    E -->|true| F[Partition rows by existing<br/>fold + split_role columns]
    F --> G{>=1 fold with<br/>non-empty test role?}
    G -->|no| X5[Reject: no evaluable folds]
    G -->|yes| H[For each requested Model_Type:<br/>fit per-fold on train rows]
    H --> I[Evaluate per-fold on test rows]
    I --> J[Aggregate mean/std across folds]
    J --> K[Extract Feature_Importance]
    K --> L[Assign next Model_Version<br/>for that model name]
    L --> M[Persist Model_Artifact<br/>via Artifact_Store]
    M --> N[Record Reproducibility_Metadata<br/>+ persist Trained_Model]
    N --> O[Generate Training_Report<br/>+ Metrics_Report]
    O --> P[Retrievable via Training_API]
```

## 3. Component Diagram

| Component | Responsibility | Requirements |
|---|---|---|
| `Training_API` | FastAPI routers exposing all REST endpoints, OpenAPI docs, typed 404s | 14, 11.3 |
| `Dataset_Builder_Client` (port `DatasetBuilderClient`, adapter `HttpDatasetBuilderClient`) | Sole channel to the Dataset Builder's REST API; zero retry, fail-fast on any error; distinguishes 404 ("not found") from other errors | 1, 2 |
| `Pre_Training_Verifier` | Checksum verification against `manifest.checksum_algorithm`; leakage-audit gate (AND with checksum) | 3, 4, 18.4 |
| `Fold_Partitioner` | Reads existing `fold`/`split_role` columns verbatim; never re-splits, shuffles, reorders, or resamples | 5 |
| `Model_Trainer` | Fits one of the four `Model_Types` per fold, using only that fold's `train` rows | 5.5, 7 |
| `Evaluation_Engine` | Computes independent `Per_Fold_Metrics`; aggregates `Aggregated_Metrics`; rejects zero-evaluable-fold datasets | 6, 9 |
| `Feature_Importance_Extractor` | Coefficients (`logistic_regression`) or impurity/gain importances (tree ensembles); one value per manifest `feature_name` | 10 |
| `Model_Versioner` | Assigns the next monotonic `Model_Version` per model name; immutable once recorded | 8 |
| `Report_Generator` | Builds `Training_Report` and `Metrics_Report` | 11 |
| `Artifact_Store` (port `ArtifactStore`, adapter `LocalArtifactStore`) | Versioned, immutable, swappable persistence of `Model_Artifact` bytes | 13 |
| `TrainedModelRepository` / `TrainingRunRepository` | Postgres persistence of run/model metadata, reproducibility metadata, metrics, feature importance | 12, 8 |
| `GitInfoProvider` (adapter `SubprocessGitInfoProvider`) | Current commit SHA, tolerating absence | 12.3 |

Component 3's fail-fast decision (no retry, see Key Design Decision 6) is implemented entirely inside `HttpDatasetBuilderClient._fetch` — there is deliberately no retry loop, unlike `aqros_dataset_builder.adapters.market_data_client.HttpMarketDataSource._fetch_page`, whose retry-with-backoff pattern is explicitly *not* reused here.

## 4. Domain Model

`domain/models.py` (pure, frozen/slots dataclasses + StrEnums — no I/O), following `aqros_dataset_builder.domain.models` exactly:

```python
class ModelType(StrEnum):
    LOGISTIC_REGRESSION = "logistic_regression"
    RANDOM_FOREST = "random_forest"
    XGBOOST = "xgboost"
    LIGHTGBM = "lightgbm"

class SplitRole(StrEnum):          # read-only mirror of the Dataset Builder's own enum;
    TRAIN = "train"                # Training Pipeline never constructs a value of this
    VALIDATION = "validation"       # type itself, only parses it from downloaded rows.
    TEST = "test"

class TrainingRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

@dataclass(frozen=True, slots=True)
class DatasetManifest:              # local, decoupled copy of the Dataset Builder's
    dataset_name: str               # manifest shape, read via REST only (CLAUDE.md §7.9) —
    dataset_version: int            # never imported from aqros_dataset_builder.
    build_run_id: int
    checksum: str
    checksum_algorithm: str
    feature_names: tuple[str, ...]
    feature_versions: dict[str, int]
    label_type: str
    label_definition: str
    horizon: str
    split_strategy: str
    split_params: dict[str, int]
    start_date: date
    end_date: date
    created_at: datetime
    row_count: int
    git_commit: str | None
    market_data_source_url: str
    feature_store_source_url: str
    quality_report: dict[str, object]

@dataclass(frozen=True, slots=True)
class DatasetBuildRun:               # local, decoupled copy — see Component 18 below.
    id: int
    dataset_name: str
    dataset_version: int
    leakage_audit_passed: bool | None
    leakage_audit_findings: list[str] = field(default_factory=list)

@dataclass(frozen=True, slots=True)
class TrainingRequest:
    dataset_name: str
    build_run_id: int
    model_types: tuple[ModelType, ...]
    hyperparameters: dict[ModelType, dict[str, object]] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class ConfusionMatrix:
    true_negative: int
    false_positive: int
    false_negative: int
    true_positive: int

@dataclass(frozen=True, slots=True)
class PerFoldMetrics:
    fold: int
    accuracy: float
    precision: float
    recall: float
    f1_score: float
    roc_auc: float | None            # None when the fold's test rows are single-class (Req 9.3)
    confusion_matrix: ConfusionMatrix
    test_row_count: int

@dataclass(frozen=True, slots=True)
class AggregatedMetrics:
    accuracy_mean: float
    accuracy_std: float
    precision_mean: float
    precision_std: float
    recall_mean: float
    recall_std: float
    f1_mean: float
    f1_std: float
    roc_auc_mean: float | None       # None if every fold's roc_auc was undefined
    roc_auc_std: float | None
    evaluated_fold_count: int
    roc_auc_evaluated_fold_count: int

@dataclass(frozen=True, slots=True)
class ReproducibilityMetadata:
    model_version: int
    dataset_name: str
    dataset_version: int
    dataset_checksum: str
    manifest_reference: str          # build_run_id, stable and sufficient to re-fetch the manifest
    git_commit: str | None
    trained_at: datetime
    hyperparameters: dict[str, object]
    aggregated_metrics: AggregatedMetrics

@dataclass(frozen=True, slots=True)
class TrainedModel:
    model_name: str                  # == f"{dataset_name}__{model_type}", see Key Design Decision 3
    model_type: ModelType
    model_version: int
    training_run_id: int
    dataset_name: str
    dataset_version: int
    artifact_path: str
    per_fold_metrics: tuple[PerFoldMetrics, ...]
    aggregated_metrics: AggregatedMetrics
    feature_importance: dict[str, float]
    reproducibility_metadata: ReproducibilityMetadata
    created_at: datetime
    id: int | None = None

@dataclass(frozen=True, slots=True)
class ModelTypeOutcome:
    model_type: ModelType
    trained_model_id: int | None     # None if this type failed to train
    error_message: str | None

@dataclass(frozen=True, slots=True)
class TrainingRun:
    dataset_name: str
    build_run_id: int
    requested_model_types: tuple[ModelType, ...]
    status: TrainingRunStatus
    started_at: datetime
    outcomes: tuple[ModelTypeOutcome, ...] = field(default_factory=tuple)
    completed_at: datetime | None = None
    error_message: str | None = None
    id: int | None = None
```

**Departure from the Dataset Builder's row-dataclass pattern:** `Dataset_Artifact` rows are *not* modeled as one dataclass per row (unlike `OHLCVBar`/`FeatureValue`) because their feature columns are dynamic — determined per-dataset by `DatasetManifest.feature_names`, not a fixed schema known at class-definition time. The `Fold_Partitioner`, `Model_Trainer`, and `Evaluation_Engine` operate on a `pandas.DataFrame` read from the downloaded Parquet bytes instead, indexed by the fixed `symbol`/`event_time`/`fold`/`split_role`/`label` columns plus whatever feature columns `feature_names` names. This is a deliberate, explicitly-stated departure, not an oversight.

## 5. Repository Structure

### 5.1 File / folder layout

```
backend/training-pipeline/
├── README.md
├── pyproject.toml
├── alembic.ini
├── migrations/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial_schema.py
├── src/aqros_training_pipeline/
│   ├── __init__.py
│   ├── py.typed
│   ├── config.py
│   ├── app.py
│   ├── main.py
│   ├── domain/
│   │   ├── __init__.py
│   │   ├── models.py            # Section 4
│   │   ├── ports.py              # DatasetBuilderClient, ArtifactStore, repositories, GitInfoProvider
│   │   ├── verification.py       # Pre_Training_Verifier (checksum + leakage AND-gate)
│   │   ├── partitioning.py       # Fold_Partitioner (read-only fold/split_role handling)
│   │   ├── trainers.py           # Model_Trainer (per-Model_Type fit dispatch + hyperparameter defaults)
│   │   ├── evaluation.py         # Evaluation_Engine (per-fold metrics + aggregation)
│   │   ├── feature_importance.py # Feature_Importance_Extractor
│   │   ├── versioning.py         # Model_Versioner (monotonic per-model-name assignment)
│   │   ├── reports.py            # Report_Generator (Training_Report / Metrics_Report)
│   │   └── services.py           # TrainingPipelineService, TrainingQueryService
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── db.py                 # engine/session_factory/session_scope/ping — identical pattern
│   │   ├── orm.py                # TrainingRunORM, TrainedModelORM
│   │   ├── repository.py         # SqlAlchemyTrainingRunRepository, SqlAlchemyTrainedModelRepository
│   │   ├── dataset_builder_client.py  # HttpDatasetBuilderClient (zero-retry, see Decision 6)
│   │   ├── local_artifact_store.py    # LocalArtifactStore (joblib bytes, versioned immutable paths)
│   │   └── git_info.py           # SubprocessGitInfoProvider (copy of dataset-builder's pattern)
│   └── api/
│       ├── __init__.py
│       ├── schemas.py            # Pydantic request/response models + ErrorResponse
│       ├── deps.py                # FastAPI DI wiring off app.state / request-scoped session
│       └── routes/
│           ├── __init__.py
│           ├── training_runs.py  # POST/GET /v1/training-runs
│           └── trained_models.py # GET /v1/trained-models, .../metadata, .../metrics, .../artifact
└── tests/
    ├── __init__.py
    ├── test_health.py
    ├── unit/
    │   ├── __init__.py
    │   ├── test_verification.py
    │   ├── test_partitioning.py
    │   ├── test_trainers.py
    │   ├── test_evaluation.py
    │   ├── test_feature_importance.py
    │   ├── test_versioning.py
    │   ├── test_reports.py
    │   └── test_training_pipeline_service.py
    └── integration/
        ├── __init__.py
        ├── conftest.py           # testcontainers Postgres, same pattern as dataset-builder's
        ├── test_api.py           # httpx ASGITransport, faked DatasetBuilderClient
        ├── test_repository.py
        └── test_migrations.py
```

### 5.2 Repository-pattern persistence design

Two repository ports, one ORM-backed implementation each, exactly mirroring `SqlAlchemyDatasetDefinitionRepository`/`SqlAlchemyDatasetBuildRunRepository`:

- `TrainingRunRepository.create_run(run) -> TrainingRun` / `complete_run(run) -> None` / `get_run(id)` / `list_runs(...)` — `create_run` does `session.add()` + `session.flush()` only (never `commit()`; the request-scoped session dependency owns the transaction boundary).
- `TrainedModelRepository.create_trained_model(model) -> TrainedModel` / `get_trained_model(model_name, version)` / `list_trained_models(model_name=None)` / `get_latest_version(model_name) -> int | None`.

Both repositories translate ORM rows to/from the frozen domain dataclasses via private `_to_domain_*` helpers, and both take an `AsyncSession` injected through their constructor — no repository ever imports `aqros_training_pipeline.api` or vice versa.

`get_latest_version` is the operation `Model_Versioner` calls before assigning a new `Model_Version` (Requirement 8.1/8.2): it runs `SELECT MAX(model_version) WHERE model_name = :name`, returning `None` for a brand-new name (→ version 1) or the current max (→ max + 1). Because `create_trained_model` and this lookup happen inside the same request-scoped transaction with a `UniqueConstraint(model_name, model_version)` at the database level, a concurrent duplicate assignment is rejected by the constraint rather than silently succeeding — this is what makes Requirement 8.4's uniqueness guarantee hold even under concurrent requests for the same model name, not just within a single process.

## 6. REST API Design

All endpoints are Pydantic-typed, documented automatically via FastAPI's OpenAPI generation (Requirement 14.7), and return `ErrorResponse(error, detail)` with `404` for any missing resource (Requirement 14.8).

| Method | Path | Purpose | Requirement |
|---|---|---|---|
| `POST` | `/v1/training-runs` | Create and (synchronously) execute a `Training_Run` for a `Training_Request` | 14.1 |
| `GET` | `/v1/training-runs/{run_id}` | Retrieve status + `Training_Report` of a run | 14.2, 11.3 |
| `GET` | `/v1/trained-models` | List `Trained_Model` records, optional `model_name` filter | 14.3 |
| `GET` | `/v1/trained-models/{model_name}/versions/{version}/metadata` | Retrieve `Reproducibility_Metadata` | 14.4 |
| `GET` | `/v1/trained-models/{model_name}/versions/{version}/metrics` | Retrieve `Metrics_Report` | 14.5, 11.3 |
| `GET` | `/v1/trained-models/{model_name}/versions/{version}/artifact` | Download `Model_Artifact` bytes | 14.6 |
| `GET` | `/health`, `/health/live`, `/health/ready` | Liveness/readiness (own DB + Dataset Builder reachability) | 15.4 |

Request body for `POST /v1/training-runs`:

```json
{
  "dataset_name": "aapl_5d_direction",
  "build_run_id": 42,
  "model_types": ["logistic_regression", "random_forest"],
  "hyperparameters": {
    "random_forest": {"n_estimators": 300}
  }
}
```

`model_types` is validated against `{logistic_regression, random_forest, xgboost, lightgbm}` at the Pydantic layer; any other value produces a `422` naming the offending value (Requirement 7.6) before the request ever reaches the domain layer.

## 7. Database Schema

Postgres via SQLAlchemy 2.0, `DeclarativeBase` + `Mapped`/`mapped_column`, snake_case plural tables — identical style to `aqros_dataset_builder.adapters.orm`. Only metadata is persisted here; the `Model_Artifact` bytes live in the `Artifact_Store` (Section 8), not in Postgres — mirroring the Dataset Builder's own "structured metadata in Postgres, bulk artifact bytes outside it" split.

```mermaid
erDiagram
    TRAINING_RUNS {
        bigint id PK
        string dataset_name
        integer build_run_id
        text model_types_json
        text hyperparameters_json
        string status
        datetime started_at
        datetime completed_at
        text outcomes_json
        text error_message
    }
    TRAINED_MODELS {
        bigint id PK
        string model_name
        string model_type
        integer model_version
        bigint training_run_id FK
        string dataset_name
        integer dataset_version
        integer build_run_id
        string artifact_path
        text per_fold_metrics_json
        text aggregated_metrics_json
        text feature_importance_json
        text reproducibility_metadata_json
        datetime created_at
    }
    TRAINING_RUNS ||--o{ TRAINED_MODELS : produces
```

```python
class TrainingRunORM(Base):
    __tablename__ = "training_runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dataset_name: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    build_run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    model_types_json: Mapped[str] = mapped_column(Text, nullable=False)
    hyperparameters_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcomes_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

class TrainedModelORM(Base):
    __tablename__ = "trained_models"
    __table_args__ = (UniqueConstraint("model_name", "model_version", name="uq_trained_models_name_version"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    model_type: Mapped[str] = mapped_column(String(32), nullable=False)
    model_version: Mapped[int] = mapped_column(Integer, nullable=False)
    training_run_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("training_runs.id"), nullable=False)
    dataset_name: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_version: Mapped[int] = mapped_column(Integer, nullable=False)
    build_run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    artifact_path: Mapped[str] = mapped_column(String(512), nullable=False)
    per_fold_metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
    aggregated_metrics_json: Mapped[str] = mapped_column(Text, nullable=False)
    feature_importance_json: Mapped[str] = mapped_column(Text, nullable=False)
    reproducibility_metadata_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

`(model_name, model_version)` is a database-level `UniqueConstraint`, not just an application-level check — this is the concrete mechanism behind Requirement 8.4's uniqueness guarantee under concurrent writers. `Trained_Model` rows are only ever inserted, never updated (Requirement 8.3's immutability) — no repository method updates an existing `trained_models` row.

Migration `0001_initial_schema.py` creates both tables plus `ix_training_runs_dataset_name` and `ix_trained_models_model_name` indexes, with a symmetric `downgrade()`, in the exact style of the Dataset Builder's own `0001_initial_schema.py`.

## 8. Model Artifact Storage

`ArtifactStore` port (mirrors `DatasetStorage`'s write/read/checksum shape, simplified — no manifest responsibility, since `Reproducibility_Metadata` already lives in `TrainedModelRepository`):

```python
class ArtifactStore(ABC):
    @abstractmethod
    async def write_artifact(self, model_name: str, model_version: int, data: bytes) -> str: ...
    @abstractmethod
    async def read_artifact(self, model_name: str, model_version: int) -> bytes: ...

class ArtifactAlreadyExistsError(RuntimeError):
    """Raised by write_artifact when (model_name, model_version) is already persisted."""
```

`LocalArtifactStore` (adapter, mirrors `LocalParquetStorage`):

- Path construction: `{base_dir}/{model_name}/v{model_version}/model.joblib` — deterministically encodes both the model name and version in the path itself (Requirement 13.1).
- `write_artifact` checks `path.exists()` first and raises `ArtifactAlreadyExistsError` rather than overwriting (Requirement 13.2) — the check-then-write is done inside a single `asyncio.to_thread` call to avoid a TOCTOU race between two concurrent writers for the same version (a real risk given `Model_Versioner` and `write_artifact` are separate steps); the artifact directory's immutability is additionally enforced by never issuing a second `create_trained_model` for an already-used `(model_name, model_version)` pair (the DB constraint in Section 7 is the authoritative guard).
- `read_artifact` returns the exact bytes written, by path (Requirement 13.3).
- All blocking file I/O wrapped in `asyncio.to_thread`, exactly as `LocalParquetStorage` does.

Requirement 13.4 (swappable interface) is satisfied structurally: `ArtifactStore` is an `ABC` with two methods taking/returning plain `bytes`, no filesystem-specific parameter anywhere in the port — an S3-backed `S3ArtifactStore` implementing the same two methods is a drop-in adapter swap in `api/deps.py`, with zero changes to `Model_Trainer` or any route handler.

## 9. Training Workflow

```mermaid
sequenceDiagram
    participant Client
    participant API as Training_API
    participant Svc as TrainingPipelineService
    participant DBC as Dataset_Builder_Client
    participant Ver as Pre_Training_Verifier
    participant Part as Fold_Partitioner
    participant Trn as Model_Trainer
    participant Eval as Evaluation_Engine
    participant Ver2 as Model_Versioner
    participant Store as Artifact_Store
    participant Repo as TrainedModelRepository

    Client->>API: POST /v1/training-runs {dataset_name, build_run_id, model_types}
    API->>Svc: create_training_run(request)
    Svc->>Repo: create_run(status=RUNNING)
    Svc->>DBC: get_build_run(build_run_id)
    alt 404
        DBC-->>Svc: DatasetBuildRunNotFoundError
        Svc->>Repo: complete_run(status=FAILED, "build run not found")
        Svc-->>API: 404-mapped error
    else success
        Svc->>DBC: get_manifest(build_run_id)
        Svc->>DBC: download_dataset(build_run_id)
        Svc->>Ver: verify(manifest, downloaded_bytes, build_run)
        alt checksum mismatch or leakage_audit not true
            Ver-->>Svc: VerificationFailure(reason)
            Svc->>Repo: complete_run(status=FAILED, reason)
        else verified
            Svc->>Part: partition(dataframe)
            alt zero evaluable folds
                Part-->>Svc: NoEvaluableFoldsError
                Svc->>Repo: complete_run(status=FAILED, "no evaluable folds")
            else folds available
                loop for each requested Model_Type
                    Svc->>Trn: fit_per_fold(model_type, folds, hyperparameters)
                    alt fit/eval raises
                        Trn-->>Svc: outcome = failed for this Model_Type
                    else fit/eval succeeds
                        Svc->>Eval: evaluate(fitted_models_per_fold)
                        Svc->>Ver2: next_version(model_name="{dataset_name}__{model_type}")
                        Svc->>Store: write_artifact(model_name, version, bytes)
                        Svc->>Repo: create_trained_model(...)
                        Trn-->>Svc: outcome = succeeded for this Model_Type
                    end
                end
                alt every requested Model_Type outcome == succeeded
                    Svc->>Repo: complete_run(status=SUCCEEDED, outcomes)
                else one or more Model_Type outcomes == failed
                    Svc->>Repo: complete_run(status=FAILED, outcomes)
                    Note over Svc,Repo: Trained_Model rows already persisted for<br/>succeeded Model_Types are NOT rolled back (Design Decision 8)
                end
            end
        end
        Svc-->>API: TrainingRun + TrainingReport
    end
    API-->>Client: 201 Created
```

`TrainingPipelineService.create_training_run` wraps the entire body in a single `try/except`, exactly like `DatasetBuilderService.build_dataset` — every run is recorded (`succeeded` or `failed`) regardless of outcome, never left `running` forever on an unhandled exception.

## 10. Cross-Validation Workflow

Per-fold train/test using only the existing `fold`/`split_role` columns — the Training Pipeline never calls anything resembling `train_test_split` or a k-fold splitter.

```mermaid
flowchart TB
    A[Downloaded DataFrame:<br/>symbol, event_time, fold, split_role, label, features...] --> B[Group by existing 'fold' column value]
    B --> C1[Fold 0]
    B --> C2[Fold 1]
    B --> C3[Fold N]
    C1 --> D1[rows where split_role == 'train']
    C1 --> E1[rows where split_role == 'test']
    D1 --> F1[Model_Trainer.fit]
    F1 --> G1[Evaluation_Engine.evaluate on E1]
    G1 --> H1[PerFoldMetrics fold=0]
    C2 --> D2[...] --> F2[...] --> G2[...] --> H2[PerFoldMetrics fold=1]
    C3 --> D3[...] --> F3[...] --> G3[...] --> H3[PerFoldMetrics fold=N]
    H1 --> I[Evaluation_Engine.aggregate]
    H2 --> I
    H3 --> I
    I --> J[AggregatedMetrics: mean/std per metric,<br/>ROC AUC excluded for single-class folds]
```

`Fold_Partitioner.partition(dataframe)` is a pure function: `dataframe.groupby("fold")`, then within each group, `frame[frame.split_role == "train"]` / `frame[frame.split_role == "test"]` — no sort, no shuffle, no `sample()` call anywhere in this path (Requirement 5.4). If, across every fold, the `test`-role slice is empty, `Fold_Partitioner` raises `NoEvaluableFoldsError` before any `Model_Trainer` call is made (Requirement 6.4).

## 11. Model Versioning

```mermaid
sequenceDiagram
    participant Trn as Model_Trainer
    participant MV as Model_Versioner
    participant Repo as TrainedModelRepository
    Trn->>MV: assign_version(model_name="aapl_5d_direction__random_forest")
    MV->>Repo: get_latest_version("aapl_5d_direction__random_forest")
    alt no existing version
        Repo-->>MV: None
        MV-->>Trn: 1
    else existing max = N
        Repo-->>MV: N
        MV-->>Trn: N + 1
    end
```

`model_name` is always the composite `f"{dataset_name}__{model_type}"` string (Key Design Decision 3) — `Model_Trainer` builds this string once per requested `Model_Type` from the `Training_Request`'s `dataset_name` and hands it to `Model_Versioner` opaquely; training `random_forest` against `msft_20d_return` computes a different `model_name` and therefore starts from its own independent version-1 baseline, never interacting with `aapl_5d_direction__random_forest`'s counter. `Model_Versioner.assign_version` and the subsequent `Repo.create_trained_model(...)` call happen inside the same DB transaction as the surrounding `Training_Run`; the `UniqueConstraint(model_name, model_version)` (Section 7) is the final backstop against a race between two concurrent training runs for the same model name. Once a `Trained_Model` row exists for a given `(model_name, model_version)`, no code path ever updates that row's `model_version` column — versions are write-once.

## 12. Metrics Collection

`Evaluation_Engine.evaluate_fold(y_true, y_pred, y_proba)` computes, per fold, from that fold's `test`-role rows only:

- `accuracy` — `sklearn.metrics.accuracy_score`
- `precision`, `recall`, `f1_score` — `sklearn.metrics.precision_score/recall_score/f1_score` (binary target, matching the Dataset Builder's `binary_direction` label type)
- `confusion_matrix` — `sklearn.metrics.confusion_matrix`, unpacked into the four named fields of `ConfusionMatrix`
- `roc_auc` — `sklearn.metrics.roc_auc_score(y_true, y_proba[:, 1])`, computed **unless** `len(set(y_true)) == 1`, in which case `roc_auc = None` for that fold (Requirement 9.3)

`Evaluation_Engine.aggregate(per_fold_metrics)` computes, across all folds of one `Trained_Model`:

- mean/std of `accuracy`, `precision`, `recall`, `f1_score` across **every** fold
- mean/std of `roc_auc` across only the folds whose `roc_auc is not None`; if every fold's `roc_auc` is `None`, `roc_auc_mean`/`roc_auc_std` are `None` and `roc_auc_evaluated_fold_count == 0`

Using `statistics.fmean`/`statistics.pstdev` (population std, `ddof=0`), consistent with `LabelBalance`'s computation in the Dataset Builder.

## 13. Feature Importance Generation

`Feature_Importance_Extractor.extract(fitted_model, model_type, feature_names) -> dict[str, float]`:

- `logistic_regression` → `fitted_model.coef_[0]` (binary classification → single coefficient row)
- `random_forest`, `xgboost`, `lightgbm` → `fitted_model.feature_importances_` (impurity-based for `RandomForestClassifier`; gain-based for `XGBClassifier`/`LGBMClassifier`, matching each library's default `importance_type`)

Both branches `zip(feature_names, importance_array)` into a `dict[str, float]`. Because `Model_Trainer` always fits with columns ordered exactly as `DatasetManifest.feature_names` (never reordered), the resulting mapping is guaranteed to contain exactly one entry per manifest `feature_name`, no more, no fewer (Requirement 10.3) — enforced by construction rather than a post-hoc check.

## 14. Error Handling

| Failure | Detected by | Training_Run outcome | HTTP surface |
|---|---|---|---|
| Dataset build run not found (404 on any of the 3 upstream calls) | `Dataset_Builder_Client` | `failed`, reason = "build run not found" | `404` at creation |
| Dataset Builder unreachable / non-404 error response | `Dataset_Builder_Client` | `failed`, reason = upstream error text | `502` at creation |
| Checksum mismatch | `Pre_Training_Verifier` | `failed`, reason = "checksum mismatch" | `422` at creation |
| `leakage_audit_passed` is `false`/`null` | `Pre_Training_Verifier` | `failed`, reason = leakage findings | `422` at creation |
| Zero evaluable folds | `Fold_Partitioner` | `failed`, reason = "no evaluable folds" | `422` at creation |
| Unsupported `Model_Type` string | Pydantic schema validation | request never creates a run | `422` |
| Any one (or more) requested `Model_Type` raises during fit/eval, while others succeed | `Model_Trainer`/`Evaluation_Engine` | `failed` overall (Design Decision 8, revised) — the failing type's `ModelTypeOutcome.error_message` is set, but any sibling `Model_Type` that trained successfully still has its `Trained_Model` row, `Model_Artifact`, and `Model_Version` persisted and retained | `201` at creation (the run itself was created and executed); its status field reports `failed`, reflected in `Training_Report` |
| All requested `Model_Type`s raise during fit/eval | ditto | `failed`, reason = "all requested model types failed" | `201` at creation; status `failed` |
| Reproducibility metadata incomplete (any field but `git_commit` missing) | `Report_Generator`/`TrainingPipelineService` | that type's outcome recorded as failed; `Trained_Model` row never inserted; overall run marked `failed` per the rule above | reflected in `Training_Report` |
| Artifact write collides with an existing `(model_name, version)` | `Artifact_Store` | that type's outcome recorded as failed; no `Trained_Model` row inserted; overall run marked `failed` per the rule above | reflected in `Training_Report` |
| Requested run/model/report/artifact does not exist | Query routes | n/a (read-only) | `404` with `ErrorResponse` |

Every rejection path records a human-readable reason string on the `Training_Run` (or the specific `ModelTypeOutcome`) rather than only raising an exception — this is what `Training_Report` surfaces back to the caller (Requirement 11.1).

## 15. Failure State Machine

**`Training_Run` status transitions:**

```mermaid
stateDiagram-v2
    [*] --> pending: POST /v1/training-runs received
    pending --> running: create_run() persisted
    running --> failed: 404 on manifest/build-run/download (Req 2.3)
    running --> failed: upstream error/unreachable (Req 1.4)
    running --> failed: checksum mismatch (Req 3.2)
    running --> failed: leakage_audit_passed != true (Req 4.2, 18.4)
    running --> failed: zero evaluable folds (Req 6.4)
    running --> failed: any requested Model_Type failed to train (Design Decision 8, revised)
    running --> succeeded: every requested Model_Type<br/>produced a persisted Trained_Model (Design Decision 8, revised)
    failed --> [*]
    succeeded --> [*]
```

A `Training_Run` reaching `failed` via the "any requested Model_Type failed to train" transition may still have one or more real `Trained_Model` children already persisted (those requested types that trained successfully before a sibling failed) — the run's `failed` status reflects that the request was not fully satisfied, not that nothing was produced. See Design Decision 8 for the full rationale and Section 6/14 for how those retained artifacts remain queryable via `GET /v1/trained-models`.

**Per-`Model_Type` outcome within a `Training_Run`** (not a top-level `Training_Run` status, but recorded per `ModelTypeOutcome` — runs independently of whether the overall run ends `succeeded` or `failed`):

```mermaid
stateDiagram-v2
    [*] --> fitting: Model_Trainer.fit invoked for this Model_Type
    fitting --> evaluating: fit succeeds
    fitting --> outcome_failed: fit raises (Design Decision 8)
    evaluating --> extracting_importance: Evaluation_Engine.evaluate/aggregate succeeds
    extracting_importance --> versioning: Feature_Importance_Extractor succeeds (Req 10)
    versioning --> storing_artifact: Model_Versioner.assign_version succeeds (Req 8.1, 8.2)
    storing_artifact --> recording_metadata: Artifact_Store.write_artifact succeeds (Req 13.1, 13.2)
    storing_artifact --> outcome_failed: ArtifactAlreadyExistsError (Req 13.2)
    recording_metadata --> outcome_succeeded: Reproducibility_Metadata complete<br/>(git_commit absence tolerated, Req 12.3) — Trained_Model persisted (Req 12.1)
    recording_metadata --> outcome_failed: any other metadata field missing<br/>or persistence error (Req 12.2, 12.4)
    outcome_succeeded --> [*]
    outcome_failed --> [*]
```

A `Trained_Model` row is never partially written: `TrainedModelRepository.create_trained_model` either inserts one complete row (artifact already durably stored, metadata complete) or is never called for that `Model_Type` at all.

## 16. Docker Deployment

- Image: built from the existing shared `docker/Dockerfile.service` with `SERVICE=training-pipeline`, `MODULE=aqros_training_pipeline`, `PORT=8009` — no changes to the Dockerfile itself.
- Port assignment: **service port 8009**, **dedicated Postgres port 5435** (`training-pipeline-db`), continuing the existing sequence (market-data 8002/5432, feature-store 8003/5433, dataset-builder 8008/5434).
- `docker-compose.yml` additions (implementation-phase edit, shown here for the design record):

```yaml
  training-pipeline-db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: aqros
      POSTGRES_PASSWORD: aqros
      POSTGRES_DB: aqros_training_pipeline
    ports: ["5435:5432"]
    volumes:
      - training-pipeline-db-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U aqros -d aqros_training_pipeline"]
      interval: 5s
      timeout: 5s
      retries: 10
    networks: [aqros]

  training-pipeline:
    <<: *service-defaults
    build:
      context: .
      dockerfile: docker/Dockerfile.service
      args: { SERVICE: training-pipeline, MODULE: aqros_training_pipeline, PORT: "8009" }
    ports: ["8009:8009"]
    environment:
      AQROS_ENVIRONMENT: dev
      AQROS_LOG_JSON: "false"
      AQROS_DATABASE_URL: postgresql+asyncpg://aqros:aqros@training-pipeline-db:5432/aqros_training_pipeline
      # Reaches the Dataset Builder Service only through its published REST API.
      AQROS_DATASET_BUILDER_BASE_URL: http://dataset-builder:8008
      AQROS_ARTIFACT_DIR: /data/training-pipeline/artifacts
    volumes:
      - training-pipeline-artifacts:/data/training-pipeline/artifacts
    depends_on:
      training-pipeline-db:
        condition: service_healthy
      dataset-builder:
        condition: service_started
```

  plus `training-pipeline-db-data` and `training-pipeline-artifacts` named volumes.

- Health endpoints: `GET /health/live` (always healthy), `GET /health/ready` (registers `database` via `db.ping` and `dataset_builder_service` via the same lenient "any response counts as reachable" check `aqros_dataset_builder.app._check_upstream_reachable` uses), `GET /health` as the readiness alias — identical pattern to all three existing services (Requirement 15.4).
- `Settings` (`config.py`) extends `aqros_core.config.BaseServiceSettings` exactly like the Dataset Builder's own `config.py`: `service_name="training-pipeline"`, `port=8009`, `database_url` defaulting to `postgresql+asyncpg://aqros:aqros@localhost:5435/aqros_training_pipeline`, `dataset_builder_base_url: AnyHttpUrl` defaulting to `http://localhost:8008`, plus an `artifact_dir` path setting — all overridable via `AQROS_*` env vars.
- Root `pyproject.toml`'s `[tool.ruff.lint.isort].known-first-party` list must add `"aqros_training_pipeline"` — noted here as a required edit for the implementation/tasks phase, not made now.

## 17. Sequence Diagrams

### 17.1 Full POST-training-request-to-persistence flow

See Section 9 (Training Workflow) — the primary end-to-end sequence diagram.

### 17.2 Pre-training verification chain (Requirements 2–4)

```mermaid
sequenceDiagram
    participant Svc as TrainingPipelineService
    participant DBC as Dataset_Builder_Client
    participant Ver as Pre_Training_Verifier

    Svc->>DBC: get_manifest(build_run_id)  (GET /v1/runs/{id}/manifest)
    alt 404
        DBC-->>Svc: DatasetBuildRunNotFoundError
        Note over Svc: halt immediately (Req 2.3) — get_build_run/download never called
    else 200
        Svc->>DBC: get_build_run(build_run_id)  (GET /v1/runs/{id})
        alt 404
            DBC-->>Svc: DatasetBuildRunNotFoundError
            Note over Svc: halt immediately (Req 2.3) — download never called
        else 200
            Note over Svc: both manifest and build_run now retrieved (Req 2.4)
            Svc->>DBC: download_dataset(build_run_id)  (GET /v1/runs/{id}/download)
            alt error/unreachable
                DBC-->>Svc: UpstreamSourceError
                Note over Svc: fail Training_Run, no retry (Req 1.4)
            else bytes
                Svc->>Ver: verify_checksum(manifest, bytes)
                alt mismatch
                    Ver-->>Svc: reject "checksum mismatch" (Req 3.2)
                else match
                    Note over Ver: permitted to proceed, not guaranteed (Req 3.3)
                    Svc->>Ver: verify_leakage(build_run)
                    alt leakage_audit_passed != true
                        Ver-->>Svc: reject with leakage_audit_findings (Req 4.2)
                    else true
                        Ver-->>Svc: proceed to training (Req 4.3, 4.4)
                    end
                end
            end
        end
    end
```

## 18. Integration with Dataset Builder Only Through Its REST API

Restating the boundary rule this entire service is built around (CLAUDE.md §7.9, Requirements 1.1–1.4, 2.1–2.4, 18.1–18.3): the Training Pipeline's **only** integration point with the Dataset Builder is `HttpDatasetBuilderClient`, which issues exactly three kinds of HTTP call —

- `GET /v1/runs/{run_id}` → `Dataset_Build_Run` (including `leakage_audit_passed`/`leakage_audit_findings`)
- `GET /v1/runs/{run_id}/manifest` → `Dataset_Manifest`
- `GET /v1/runs/{run_id}/download` → raw Parquet bytes

`DatasetManifest`/`DatasetBuildRun` in `domain/models.py` are local, decoupled dataclasses translated from JSON responses by `HttpDatasetBuilderClient._to_domain_*` static methods — never imported from `aqros_dataset_builder`'s own package, exactly as `aqros_dataset_builder` itself duplicates `OHLCVBar` rather than importing from `aqros_market_data`. No SQLAlchemy session, engine, or connection string for the Dataset Builder's database exists anywhere in this codebase (Requirement 1.2), and no `httpx.AsyncClient` pointed at Market Data or Feature Store exists anywhere in this codebase (Requirements 1.3, 18.2, 18.3) — there is nothing to disable, because it is never wired up in the first place.

## 19. Future Integration Points for Model Registry

No Model Registry service is built as part of this design — Requirement 18.5 explicitly forbids this service from marking any `Trained_Model` as promoted. The seam a future Model Registry service would consume is deliberately kept to this service's **REST API only**, never its database directly (same CLAUDE.md §7.9 boundary this service itself respects toward the Dataset Builder):

- `GET /v1/trained-models?model_name=...` already returns every candidate's `model_version`, `aggregated_metrics`, and `feature_importance` — a future Model Registry could poll or query this endpoint to select promotion candidates without this service knowing a Model Registry exists.
- `GET /v1/trained-models/{model_name}/versions/{version}/metadata` already exposes the full `Reproducibility_Metadata` a promotion decision would need to justify traceability.
- `GET /v1/trained-models/{model_name}/versions/{version}/artifact` already exposes the exact versioned, immutable bytes a Model Registry would copy into its own store upon promotion (this service's `Artifact_Store` remains the source of truth; the Model Registry would read-copy from it, never write back).

No new endpoint, field, or "promotable" flag is added now — the read-only surface above is already sufficient, and adding a write-capable promotion endpoint here would itself violate Requirement 18.5.

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system-essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Upstream failure is fail-closed with zero retry

For any kind of Dataset Builder error response or connection failure (404, other HTTP error status, timeout, connection refused) encountered on any of the three upstream calls, the Training_Run's status becomes `failed`, the triggering error is recorded on the run, and no other data source is contacted as a fallback.

**Validates: Requirements 1.4**

### Property 2: Manifest and build-run retrieval precede download; any 404 halts immediately

For any Training_Request, the Dataset_Manifest and Dataset_Build_Run are both retrieved successfully before the Dataset_Artifact download is ever attempted, and for any of the three retrieval steps that returns 404, every subsequent retrieval step is never invoked and the Training_Request is rejected as "build run not found."

**Validates: Requirements 2.1, 2.2, 2.3, 2.4**

### Property 3: Checksum-and-leakage AND-gate determines training eligibility

For any combination of (checksum match/mismatch) and (leakage_audit_passed true/false/null), training proceeds if and only if the checksum matches AND leakage_audit_passed is true; every other combination rejects the Training_Request with the correct reason (checksum mismatch, or the build run's leakage_audit_findings).

**Validates: Requirements 3.1, 3.2, 3.3, 4.1, 4.2, 4.3, 4.4, 18.4**

### Property 4: Fold and split-role assignment is read verbatim, never re-derived

For any Dataset_Artifact with arbitrary (valid) fold and split_role column values, the role and fold the Training_Pipeline uses internally for every row equals exactly that row's original fold and split_role values — no new split is computed, generated, or assigned.

**Validates: Requirements 5.1, 5.2, 5.3**

### Property 5: Row set used for training is preserved without shuffle, reorder, or resampling

For any Dataset_Artifact, the exact set of rows (identified by symbol + event_time) passed into training and evaluation equals the corresponding train/test-role rows of the original artifact, regardless of the row order in the downloaded file — no row is duplicated, dropped, or reordered relative to its original fold/split_role grouping.

**Validates: Requirements 5.4**

### Property 6: Per-fold fitting uses exactly that fold's train rows

For any Dataset_Artifact with multiple folds, the row set passed to Model_Trainer.fit for a given fold equals exactly the rows whose fold equals that fold and whose split_role equals train — never rows from another fold or another role.

**Validates: Requirements 5.5**

### Property 7: Per-fold evaluation is isolated to that fold's own test rows

For any Dataset_Artifact with multiple folds, each fold's Per_Fold_Metrics is computed from exactly that fold's test-role rows, and never combines rows from more than one fold.

**Validates: Requirements 6.1, 6.3**

### Property 8: Aggregated mean/std equal the statistical mean/std of the per-fold values

For any list of Per_Fold_Metrics values for one Trained_Model, the Aggregated_Metrics mean and standard deviation for each metric equal the arithmetic mean and population standard deviation of that metric's values across the contributing folds.

**Validates: Requirements 6.2, 9.2**

### Property 9: Zero evaluable folds rejects the Training_Request

For any Dataset_Artifact in which every fold's test-role row count is zero (including the zero-fold case), the Evaluation_Engine rejects the Training_Request and records that no evaluable folds were found, without producing any Per_Fold_Metrics.

**Validates: Requirements 6.4**

### Property 10: Any non-empty subset of the four Model_Types is accepted

For any non-empty subset of {logistic_regression, random_forest, xgboost, lightgbm} named in a Training_Request, the Training_API accepts the request and the resulting Training_Run targets exactly that subset of Model_Types.

**Validates: Requirements 7.1**

### Property 11: An unsupported Model_Type is rejected with a validation error naming it

For any string outside {logistic_regression, random_forest, xgboost, lightgbm} supplied as a Model_Type, the Training_API rejects the Training_Request with a validation error that identifies the unsupported value.

**Validates: Requirements 7.6**

### Property 12: Model_Version assignment is monotonic, unique, and immutable per model name, and independent across dataset names

For any sequence of successful trainings against the same composite model name (`{dataset_name}__{model_type}`), the first assigned Model_Version is 1, each subsequent assigned version equals one more than the highest previously assigned version for that model name, no two Trained_Model records for that name ever share a version, and no previously recorded Trained_Model's version is ever changed by a later training. For any two distinct dataset names trained against the same Model_Type, their respective Model_Version sequences are fully independent — a version assigned under one dataset name's composite model name never influences, and is never influenced by, the version counter of the other.

**Validates: Requirements 8.1, 8.2, 8.3, 8.4**

### Property 13: Per-fold metric computations match reference formulas

For any generated set of true/predicted labels and predicted probabilities for one fold's test rows, the computed accuracy, precision, recall, F1 score, and confusion matrix equal the values produced by a reference implementation (scikit-learn's own metric functions) applied to the same inputs.

**Validates: Requirements 9.1**

### Property 14: Single-class fold ROC AUC is undefined and excluded from aggregation

For any fold whose test-role rows contain only one label class, that fold's ROC AUC is recorded as undefined, and the Aggregated_Metrics mean and standard deviation for ROC AUC are computed only from the folds whose ROC AUC is defined.

**Validates: Requirements 9.3**

### Property 15: Extracted feature importance equals the fitted model's own importance values

For any fitted Trained_Model of any of the four Model_Types, the extracted Feature_Importance values equal — in the same order as the manifest's feature_names — the fitted model's own coefficient array (logistic_regression) or impurity/gain-based feature_importances_ array (random_forest, xgboost, lightgbm).

**Validates: Requirements 10.1, 10.2**

### Property 16: Feature importance has exactly one value per manifest feature_name

For any Trained_Model derived from a Dataset_Manifest listing N feature_names, the Feature_Importance mapping contains exactly those N feature names as keys, with no additional or missing entries.

**Validates: Requirements 10.3**

### Property 17: Training_Report lists exactly the Trained_Model ids the run produced

For any completed Training_Run that produced a given set of Trained_Model ids, the generated Training_Report's list of resulting Trained_Model identifiers equals exactly that set.

**Validates: Requirements 11.1**

### Property 18: Metrics_Report round-trips a Trained_Model's data unchanged

For any Trained_Model's Per_Fold_Metrics, Aggregated_Metrics, and Feature_Importance, the generated Metrics_Report contains exactly those same values, unmodified.

**Validates: Requirements 11.2**

### Property 19: Reproducibility_Metadata fields exactly match their source values

For any successfully trained model, the recorded Reproducibility_Metadata's model_version, dataset name, dataset version, dataset checksum, manifest reference, training timestamp, hyperparameters, and Aggregated_Metrics each exactly equal the corresponding value that produced that Trained_Model.

**Validates: Requirements 12.1**

### Property 20: Incomplete Reproducibility_Metadata blocks persistence, except an absent git commit

For any Reproducibility_Metadata missing one or more required fields, persistence of the Trained_Model is refused entirely — unless the only missing field is git_commit, in which case the Trained_Model is persisted with git_commit recorded as absent and every other field populated.

**Validates: Requirements 12.2, 12.3**

### Property 21: Non-git-commit metadata failures block persistence of the Trained_Model

For any simulated failure while recording Reproducibility_Metadata that is not the git-commit-absence case (including a repository/database error), no Trained_Model record for that training attempt is left persisted, whether partially or fully written.

**Validates: Requirements 12.4**

### Property 22: Artifact path deterministically encodes model name and version

For any (model_name, model_version) pair, the Artifact_Store's derived storage location includes both values, and distinct pairs always produce distinct locations.

**Validates: Requirements 13.1**

### Property 23: Artifact_Store never overwrites a previously persisted artifact

For any (model_name, model_version) pair already written once, a second write attempt at that same pair is rejected and the originally persisted bytes remain unchanged.

**Validates: Requirements 13.2**

### Property 24: Artifact write-then-read round trip returns identical bytes

For any artifact bytes written under a given (model_name, model_version), reading that same (model_name, model_version) back returns exactly the bytes that were written.

**Validates: Requirements 13.3**

### Property 25: Any nonexistent resource id yields a typed 404

For any randomly generated identifier that does not correspond to an existing Training_Run, Trained_Model, Training_Report, Metrics_Report, or Model_Artifact, the corresponding Training_API endpoint responds with 404 and an ErrorResponse body identifying the missing resource kind.

**Validates: Requirements 14.8**

### Property 26: A Training_Run reports succeeded if and only if every requested Model_Type trained successfully; successful artifacts survive a sibling's failure

For any Training_Request naming two or more Model_Types, if every named Model_Type produces a Trained_Model, the Training_Run's final status is succeeded; if one or more (but not all) named Model_Types fail to train while at least one other succeeds, the Training_Run's final status is failed, and every Model_Type that did succeed still has its Trained_Model row, Model_Artifact, and Model_Version persisted and retrievable exactly as it would be in a fully successful run — none of it is rolled back or deleted as a result of the sibling's failure.

**Validates: Design Decision 8 (revised); no single numbered requirement dictates multi-Model_Type partial-failure semantics, so this property documents the platform's chosen behavior for that gap.**

## Testing Strategy

### Dual testing approach

- **Unit tests** (`tests/unit/`) exercise every domain module (`verification.py`, `partitioning.py`, `trainers.py`, `evaluation.py`, `feature_importance.py`, `versioning.py`, `reports.py`, `services.py`) against fakes for every port (`FakeDatasetBuilderClient`, `FakeArtifactStore`, `FakeTrainedModelRepository`, `FakeTrainingRunRepository`, `FakeGitInfoProvider`) — no real HTTP, filesystem, or database access, matching Requirement 16.1.
- **Property-based tests**, using `hypothesis` (the standard Python PBT library — not implemented from scratch), implement each of the 25 correctness properties above as a single property test each, configured for a minimum of 100 examples (`@settings(max_examples=100)`), tagged with a comment in the form `# Feature: training-pipeline, Property N: <property text>` directly above the test function, and placed in the unit test file for the module the property most directly exercises (e.g. Property 6 in `test_partitioning.py`, Property 12 in `test_versioning.py`, Property 13 in `test_evaluation.py` using scikit-learn's own metric functions as the Property 13 reference implementation for model-based testing).
- **Integration tests** (`tests/integration/`) run the full FastAPI app via `httpx.AsyncClient` + `ASGITransport` against a real Postgres provisioned by `testcontainers.postgres.PostgresContainer`, with `DatasetBuilderClient` swapped for an in-memory fake HTTP-shaped implementation (no live Dataset Builder instance required) — matching Requirements 16.2 and 16.3, and mirroring `aqros_dataset_builder`'s own `tests/integration/conftest.py`/`test_api.py` pattern exactly (same fixture shapes: `postgres_container`, `engine`, `session_factory`, `db_session`, and a `client` fixture overriding `get_session`/`get_dataset_builder_client`/`get_git_info_provider`).
- `tests/integration/test_migrations.py` exercises the Alembic migration itself (upgrade/downgrade) against the same testcontainers Postgres, mirroring the Dataset Builder's own `test_migrations.py`.
- `tests/test_health.py` covers Requirement 15.4's readiness composition with 1-2 concrete examples (all checks healthy → 200; one check failing → 503) — a wiring check, not a property.

### Unit test balance (examples and edge cases, not covered by a property)

- Requirement 7.2–7.5: one concrete unit test per Model_Type confirming `Model_Trainer` instantiates the correct estimator class with regularization/ensemble settings applied — wiring checks, not properties.
- Requirement 11.3 and Requirement 14.1–14.7: a handful of concrete integration examples per endpoint (happy path + 404 path), since REST wiring correctness does not vary meaningfully with input.
- Requirement 15.1–15.3: verified by reviewing the Dockerfile/compose content directly (structural review), not an automated test.
- Requirements 16.4, 17.1–17.3: enforced by CI (`pytest`'s own non-zero exit code on failure; `ruff check`, `black --check`, `mypy --strict` run against `backend/training-pipeline` in the same CI job pattern as the other three services), not by application-level tests.

### Property test configuration

- Library: `hypothesis` (added to the service's dev dependency group, consistent with the monorepo's existing dev tooling in the root `pyproject.toml`).
- Minimum 100 iterations per property test.
- Each property test references its design-document property number and text in an adjacent comment, per the tag format above.
- Where a property involves fitting an actual model (Properties 12, 13, 15), generated datasets are kept small (tens of rows, 2-4 folds, 2-5 features) to keep iteration cost low while still exercising input variation — full-size training runs are left to the integration tests.
