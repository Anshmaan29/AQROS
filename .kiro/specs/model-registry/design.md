# Design Document: Model Registry

## Overview

The Model Registry (`backend/model-registry`, module `aqros_model_registry`) is the single, authoritative system of record for every trained model on the AQROS platform. It ingests trained-model records exclusively from the Training Pipeline's published REST API, records each as an immutable, fully-lineaged `Model_Version`, governs each through a strictly-ordered lifecycle behind a four-eyes promotion gate, and serves model metadata, metrics, lineage, and artifacts to every downstream consumer so that no downstream service ever queries the Training Pipeline directly (Requirements 1, 8, 28.7; CLAUDE.md §7.4, §7.9).

The service follows the domain/adapters/api layering already established by `aqros_market_data`, `aqros_feature_store`, `aqros_dataset_builder`, and `aqros_training_pipeline`: pure domain logic (lifecycle state machine, four-eyes policy, integrity checks) behind ports, concrete I/O adapters implementing those ports, and a thin FastAPI layer wired via dependency injection. The Training Pipeline is the direct implementation template — file names, repository shapes, the `ArtifactStore` port, the `Reproducibility_Metadata` shape, and the config/health/DB conventions are carried over 1:1, and every deliberate departure is called out explicitly.

Ports and databases are verified against the live `docker-compose.yml`: the `model-registry` service slot is already reserved at **HTTP port 8004** (`args: { SERVICE: model-registry, MODULE: aqros_model_registry, PORT: "8004" }`). The dedicated Postgres database `model-registry-db` is assigned **port 5436**, continuing the existing sequential allocation (market-data 5432, feature-store 5433, dataset-builder 5434, training-pipeline 5435), exactly as the Training Pipeline design "continues the existing sequence."

## Key Design Decisions

Each decision resolves an interpretive point the requirements leave open, with its rationale.

### 1. Pull-based ingestion: the Registry calls the Training Pipeline, never the reverse

**Decision:** Registration is initiated at the Registry (`POST /v1/models` with a Training Pipeline reference), and the Registry's `Training_Pipeline_Client` pulls the `Trained_Model_Record` and the artifact bytes from the Training Pipeline's published REST API (Requirements 1.2, 2.1). The Training Pipeline is never modified and never calls the Registry — the dependency direction is strictly `model-registry → training-pipeline`.

**Rationale:** Keeps the completed Training Pipeline untouched (it must not be redesigned) and keeps the Registry's dependency one-directional and testable. The Training Pipeline already exposes `GET /v1/trained-models/{model_name}/versions/{version}/metadata`, `/metrics`, and `/artifact` — exactly the read surface the Registry needs.

### 2. The Registry takes an independent, verified copy of the artifact (not a pointer)

**Decision:** On registration the Registry downloads the `Model_Artifact` bytes from the Training Pipeline, verifies their checksum, and persists an independent immutable copy in its own `Artifact_Store` (Requirements 1.5, 7, 8). Downstream artifact retrieval reads only from the Registry's store.

**Rationale:** A pointer back to the Training Pipeline would force downstream services to query the Training Pipeline (violating Requirements 1.4/28.7) and would couple downstream availability to the Training Pipeline. An independent, checksum-verified copy makes the Registry the true single source of truth and lets it guarantee integrity on every retrieval.

### 3. Artifact storage is local in the MVP, swappable for an object store later

**Decision:** The `Artifact_Store` is an `ABC` taking/returning plain `bytes`; the MVP adapter is `LocalArtifactStore` writing to a mounted volume (`{base_dir}/{model_name}/v{model_version}/model.joblib`), mirroring the Training Pipeline's `LocalArtifactStore` verbatim. An object-store-backed adapter (S3/MinIO/R2) is a drop-in swap in `api/deps.py` with zero change to domain or API logic (Requirements 8.4, 25.4).

**Rationale:** Object storage is not hardcoded as a mandatory dependency; the established adapter pattern (already proven by Dataset Builder and Training Pipeline) preserves the swap-later property without any MVP infrastructure requirement.

### 4. Version is inherited, not re-assigned: the Registry records the Training Pipeline's version verbatim

**Decision:** A `Model_Version`'s version integer is the version the Training Pipeline already assigned for that composite `model_name` (`{dataset_name}__{model_type}`); the Registry does not compute a new counter. It enforces uniqueness of `(model_name, version)` and monotonic, immutable identity, but the number itself comes from the source (Requirements 3.1, 3.2, 3.3).

**Rationale:** The Training Pipeline is the authority on "which trained model this is." Re-numbering in the Registry would create two competing version spaces and break reproducibility. The Registry's job is to record immutably and govern, not to re-version (Requirement 28).

### 5. Lifecycle is an explicit, forward-only state machine with two escape edges

**Decision:** `Lifecycle_State` transitions are permitted only along `REGISTERED → VALIDATED → STAGING → PRODUCTION → DEPRECATED → ARCHIVED`, plus exactly two additional legal edges: the **Rollback** edge `DEPRECATED → PRODUCTION` (Requirement 15) and an **abandonment** edge from any non-`PRODUCTION` state directly to `ARCHIVED` (Requirement 11.5). `ARCHIVED` is terminal. `PRODUCTION → ARCHIVED` is forbidden without passing through `DEPRECATED` (Requirement 11.6). Every other transition is rejected as illegal (Requirement 11.3).

**Rationale:** A single pure `transition_allowed(from, to)` function encodes the whole legality table, making it exhaustively property-testable and impossible to bypass.

### 6. Approval policy: mandated gates plus a small, configurable default

**Decision:** The gate for each transition is defined by an `ApprovalPolicy`. Gates mandated by the requirements are fixed; the remainder are sensible governance defaults (configurable, but never weaker than the mandate):

| Transition | Gate | Source |
|---|---|---|
| `REGISTERED → VALIDATED` | `Validation_Evidence` attached; no human approval | Requirement 12 (mandated) |
| `VALIDATED → STAGING` | one authorized approval | design default (Requirement 13 permits; not mandated) |
| `STAGING → PRODUCTION` | Four_Eyes (2 distinct approvers, neither the requester) | Requirement 14 (mandated) |
| `PRODUCTION → DEPRECATED` | automatic when superseded by a new PRODUCTION promotion; otherwise one authorized approval | Requirement 16.2 (auto) / design default (manual) |
| `DEPRECATED → ARCHIVED` | one authorized approval | design default |
| non-`PRODUCTION` → `ARCHIVED` (abandon) | one authorized approval | Requirement 11.5 permits; design-default gate |
| `DEPRECATED → PRODUCTION` (Rollback) | Four_Eyes | Requirement 15.2 (mandated) |

**Rationale:** The requirements mandate four-eyes only for `PRODUCTION` and rollback, and evidence for `VALIDATED`. Leaving `STAGING`/abandonment ungated would be surprising for a governance system, so a single-approval default is applied — expressed as policy so it never contradicts Requirement 13.3 (a transition whose policy gate is "none" applies immediately).

### 7. The single-PRODUCTION invariant is enforced by the database, not just application logic

**Decision:** At most one `Model_Version` per `Registered_Model` may be `PRODUCTION` at a time (Requirement 16.1), enforced by a Postgres **partial unique index** `UNIQUE (registered_model_id) WHERE lifecycle_state = 'PRODUCTION'`. Promotion to `PRODUCTION` (and rollback) runs in one serializable transaction that demotes the incumbent to `DEPRECATED` and promotes the target; the partial unique index is the hard backstop under concurrent promotions (Requirements 16.2, 23.3).

**Rationale:** An application-only check races under concurrent writers. A DB-level partial unique index makes a second concurrent PRODUCTION physically impossible, exactly as the Training Pipeline uses `UniqueConstraint(model_name, model_version)` for its uniqueness guarantee.

### 8. Idempotency of registration and governance actions

**Decision:** Registration is idempotent on `(model_name, model_version, training_run_id)` — a repeated request returns the existing `Model_Version` without creating a duplicate (Requirement 2.4). Every mutating governance endpoint requires a client-supplied idempotency key so a retried promotion/approval/rollback never double-applies (Requirement 19.11).

**Rationale:** At-least-once clients and retries must never fork a model's identity or double-count an approval toward four-eyes (Requirement 14.6).

### 9. Audit is written atomically with the state change; forwarding is guaranteed-delivery

**Decision:** Every privileged action appends an `Audit_Event` in the **same database transaction** as the state change it records (Requirement 18.1) — so an audit record can never be lost relative to the change it describes. Where a platform Audit Ledger is configured, events are forwarded via an outbox relay (rows written in the same transaction, published at-least-once) so a transient failure never drops an event (Requirement 18.4). `Audit_History` and `Promotion_History` are append-only with no update/delete code path (Requirements 17.2, 18.2).

**Rationale:** A "fire-and-forget" audit append could lose records under crash; the transactional/outbox approach makes the audit trail inviolable, satisfying CLAUDE.md §7.8.

### 10. Automated principals can never satisfy a PRODUCTION gate

**Decision:** The domain distinguishes human from automated principals; the four-eyes evaluator rejects any approval or PRODUCTION promotion attributed to a non-human principal (Requirements 14.7, 21.2; CLAUDE.md §7.3). This is enforced in pure domain logic, not just at the API edge.

## 1. Overall Architecture

```mermaid
graph TB
    subgraph "Model Registry Service (port 8004)"
        API[Registry_API<br/>FastAPI routes]
        SVC[ModelRegistryService /<br/>RegistryQueryService]
        LC[Lifecycle_State_Machine]
        POL[Approval_Policy + Four_Eyes]
        INT[Integrity_Verifier]
        LIN[Lineage_Assembler]
        subgraph Ports
            P1[TrainingPipelineClient]
            P2[ArtifactStore]
            P3[ModelVersionRepository]
            P4[PromotionRepository]
            P5[AuditRepository]
            P6[ArtifactSigner]
            P7[Clock]
        end
        API --> SVC
        SVC --> LC
        SVC --> POL
        SVC --> INT
        SVC --> LIN
        SVC --> P1
        SVC --> P2
        SVC --> P3
        SVC --> P4
        SVC --> P5
        SVC --> P6
        SVC --> P7
    end

    subgraph Adapters
        A1[HttpTrainingPipelineClient]
        A2[LocalArtifactStore]
        A3[SqlAlchemyModelVersionRepository]
        A4[SqlAlchemyPromotionRepository]
        A5[SqlAlchemyAuditRepository]
        A6[CosignArtifactVerifier]
    end
    P1 -.implements.-> A1
    P2 -.implements.-> A2
    P3 -.implements.-> A3
    P4 -.implements.-> A4
    P5 -.implements.-> A5
    P6 -.implements.-> A6

    A1 -->|"REST only:<br/>GET /v1/trained-models/.../metadata<br/>/metrics<br/>/artifact"| TP[Training Pipeline Service<br/>port 8009]
    A3 --> PG[(model-registry-db<br/>Postgres :5436)]
    A4 --> PG
    A5 --> PG
    A2 --> FS[(Local filesystem<br/>artifact volume — swappable)]

    DS[Downstream consumers<br/>Backtesting · Strategy · Paper · Live · AI Brain] -->|read models/metrics/lineage/artifacts| API
    DS -.->|"never queried directly"| TP
```

The Registry never opens a database connection to any other service (Requirement 1.3); its only outbound dependency is the Training Pipeline's REST API, used solely at registration time. Downstream consumers read models only from the Registry (Requirements 1.4, 28.7).

## 2. Data Flow

```mermaid
flowchart LR
    A[POST /v1/models<br/>{model_name, version, training_run_id}] --> B[Idempotency check<br/>on model_name+version+run_id]
    B -->|already registered| Z[Return existing Model_Version]
    B -->|new| C[Fetch Trained_Model_Record<br/>via Training Pipeline REST]
    C -->|404| X1[Reject: trained model not found]
    C -->|error/unreachable| X2[Fail registration<br/>record reason]
    C -->|record| D[Validate mandatory metadata<br/>dataset/feature/metrics/run/checksum]
    D -->|incomplete| X3[Reject: missing metadata]
    D -->|complete| E[Download Model_Artifact bytes]
    E --> F[Verify checksum vs record]
    F -->|mismatch| X4[Reject: checksum mismatch]
    F -->|match| G[Persist artifact copy<br/>Artifact_Store]
    G --> H[Persist Model_Version<br/>state=REGISTERED, approval=NOT_REQUIRED]
    H --> I[Append Audit_Event 'registered']
    I --> J[Retrievable via Registry_API]
```

## 3. Component Diagram

| Component | Responsibility | Requirements |
|---|---|---|
| `Registry_API` | FastAPI routers for registration, governance, retrieval; OpenAPI; typed 404s; idempotency keys | 19 |
| `Training_Pipeline_Client` (port `TrainingPipelineClient`, adapter `HttpTrainingPipelineClient`) | Sole channel to the Training Pipeline REST API; fetch record + artifact; distinguish 404 from other errors | 1, 2 |
| `Integrity_Verifier` | Compute/verify `Model_Checksum` on ingest and retrieval; refuse on mismatch | 7 |
| `Lifecycle_State_Machine` | Pure `transition_allowed(from,to)`; forward-only + rollback + abandonment; ARCHIVED terminal | 11, 15 |
| `Approval_Policy` + `Four_Eyes` | Gate per transition; enforce two distinct approvers ≠ requester; block automated principals | 12, 13, 14, 21 |
| `Lineage_Assembler` | Assemble dataset-version, feature-versions, git commit, run id, metrics into Lineage/Reproducibility_Metadata | 4, 5, 9, 10 |
| `Model_Versioner` (record-only) | Record inherited version; enforce `(model_name, version)` uniqueness and immutability | 3, 6 |
| `Artifact_Store` (port `ArtifactStore`, adapter `LocalArtifactStore`) | Versioned, immutable, swappable artifact bytes; never overwrite | 8, 25.4 |
| `ModelVersionRepository` / `PromotionRepository` / `AuditRepository` | Postgres persistence: versions (write-once identity), promotion requests + history (append-only), audit (append-only) | 17, 18, 22 |
| `ArtifactSigner` (adapter `CosignArtifactVerifier`) | Verify artifact signature before serving, where configured | 21.3 |

## 4. Domain Model

`domain/models.py` (pure, frozen/slots dataclasses + StrEnums — no I/O), mirroring `aqros_training_pipeline.domain.models` conventions. All foreign payloads are **local, decoupled copies** — `aqros_training_pipeline` is never imported (CLAUDE.md §7.9).

```python
class LifecycleState(StrEnum):
    REGISTERED = "registered"
    VALIDATED = "validated"
    STAGING = "staging"
    PRODUCTION = "production"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"

class ApprovalState(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"

class PrincipalKind(StrEnum):
    HUMAN = "human"
    AUTOMATED = "automated"

@dataclass(frozen=True, slots=True)
class PerFoldMetrics:          # local decoupled copy of the Training Pipeline shape
    fold: int
    accuracy: float
    precision: float
    recall: float
    f1_score: float
    roc_auc: float | None
    test_row_count: int

@dataclass(frozen=True, slots=True)
class AggregatedMetrics:       # local decoupled copy
    accuracy_mean: float
    accuracy_std: float
    precision_mean: float
    precision_std: float
    recall_mean: float
    recall_std: float
    f1_mean: float
    f1_std: float
    roc_auc_mean: float | None
    roc_auc_std: float | None
    evaluated_fold_count: int
    roc_auc_evaluated_fold_count: int

@dataclass(frozen=True, slots=True)
class MetricsRecord:
    per_fold: tuple[PerFoldMetrics, ...]
    aggregated: AggregatedMetrics
    feature_importance: dict[str, float]

@dataclass(frozen=True, slots=True)
class DatasetVersionRef:
    dataset_name: str
    dataset_version: int
    dataset_checksum: str

@dataclass(frozen=True, slots=True)
class ReproducibilityMetadata:
    model_version: int
    dataset_name: str
    dataset_version: int
    dataset_checksum: str
    feature_versions: dict[str, int]
    git_commit: str | None       # None => explicitly absent (Requirement 6.3)
    training_run_id: int
    trained_at: datetime
    hyperparameters: dict[str, object]
    aggregated_metrics: AggregatedMetrics

@dataclass(frozen=True, slots=True)
class ValidationEvidence:
    kind: str                    # e.g. "backtest_report"
    reference: str               # URI / dossier id (opaque; Registry never computes it)
    attached_at: datetime

@dataclass(frozen=True, slots=True)
class TrainedModelRecord:        # local decoupled copy pulled from the Training Pipeline
    model_name: str
    model_type: str
    model_version: int
    training_run_id: int
    dataset_name: str
    dataset_version: int
    dataset_checksum: str
    checksum_algorithm: str
    artifact_checksum: str
    feature_versions: dict[str, int]
    per_fold_metrics: tuple[PerFoldMetrics, ...]
    aggregated_metrics: AggregatedMetrics
    feature_importance: dict[str, float]
    git_commit: str | None
    trained_at: datetime
    hyperparameters: dict[str, object]

@dataclass(frozen=True, slots=True)
class ModelVersion:
    model_name: str              # == f"{dataset_name}__{model_type}" (inherited)
    model_type: str
    version: int
    training_run_id: int
    dataset_version: DatasetVersionRef
    feature_versions: dict[str, int]
    metrics: MetricsRecord
    artifact_path: str
    artifact_checksum: str
    checksum_algorithm: str
    git_commit: str | None
    reproducibility_metadata: ReproducibilityMetadata
    lifecycle_state: LifecycleState
    approval_state: ApprovalState
    validation_evidence: ValidationEvidence | None
    created_at: datetime
    id: int | None = None

@dataclass(frozen=True, slots=True)
class Approval:
    approver: str
    approver_kind: PrincipalKind
    decision: str                # "approve" | "reject"
    reason: str | None
    created_at: datetime

@dataclass(frozen=True, slots=True)
class PromotionRequest:
    model_name: str
    version: int
    from_state: LifecycleState
    to_state: LifecycleState
    requester: str
    justification: str
    approval_state: ApprovalState
    approvals: tuple[Approval, ...] = field(default_factory=tuple)
    is_rollback: bool = False
    created_at: datetime | None = None
    id: int | None = None

@dataclass(frozen=True, slots=True)
class PromotionHistoryEntry:
    model_name: str
    version: int
    from_state: LifecycleState
    to_state: LifecycleState
    requester: str
    approvers: tuple[str, ...]
    justification: str
    is_rollback: bool
    created_at: datetime

@dataclass(frozen=True, slots=True)
class AuditEvent:
    action: str                  # registered | transition_requested | approved | rejected | rolled_back | artifact_served
    actor: str
    model_name: str | None
    version: int | None
    before_state: str | None
    after_state: str | None
    justification: str | None
    correlation_id: str
    created_at: datetime
```

**Departure note:** metrics and reproducibility shapes are duplicated as local decoupled dataclasses rather than imported from `aqros_training_pipeline`, exactly as the Training Pipeline duplicates `DatasetManifest` rather than importing from `aqros_dataset_builder` (CLAUDE.md §7.9).

## 5. Repository Structure

```
backend/model-registry/
├── README.md
├── pyproject.toml
├── alembic.ini
├── migrations/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_initial_schema.py
├── src/aqros_model_registry/
│   ├── __init__.py
│   ├── py.typed
│   ├── config.py
│   ├── app.py
│   ├── main.py
│   ├── domain/
│   │   ├── __init__.py
│   │   ├── models.py            # Section 4
│   │   ├── ports.py             # TrainingPipelineClient, ArtifactStore, repositories, ArtifactSigner, Clock
│   │   ├── lifecycle.py         # Lifecycle_State_Machine (pure transition_allowed)
│   │   ├── approval.py          # Approval_Policy + Four_Eyes evaluation
│   │   ├── integrity.py         # Integrity_Verifier (checksum compute/verify)
│   │   ├── lineage.py           # Lineage_Assembler / Reproducibility_Metadata assembly
│   │   └── services.py          # ModelRegistryService, RegistryQueryService
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── db.py                # engine/session_factory/session_scope/ping — identical pattern
│   │   ├── orm.py               # RegisteredModelORM, ModelVersionORM, PromotionRequestORM, ApprovalORM, PromotionHistoryORM, AuditEventORM
│   │   ├── repository.py        # SqlAlchemy{ModelVersion,Promotion,Audit}Repository
│   │   ├── training_pipeline_client.py   # HttpTrainingPipelineClient
│   │   ├── local_artifact_store.py       # LocalArtifactStore (bytes, versioned immutable paths — swappable)
│   │   └── signer.py            # CosignArtifactVerifier (optional, where configured)
│   └── api/
│       ├── __init__.py
│       ├── schemas.py           # Pydantic request/response + ErrorResponse
│       ├── deps.py              # DI wiring off app.state / request-scoped session
│       └── routes/
│           ├── __init__.py
│           ├── models.py        # POST /v1/models, GET list/detail/lineage/metrics
│           ├── artifacts.py     # GET artifact download
│           ├── transitions.py   # promote / approve / reject / rollback
│           └── history.py       # promotion-history, audit-history, production resolution
└── tests/
    ├── __init__.py
    ├── test_health.py
    ├── unit/
    │   ├── __init__.py
    │   ├── test_lifecycle.py
    │   ├── test_approval.py
    │   ├── test_integrity.py
    │   ├── test_lineage.py
    │   ├── test_versioning.py
    │   └── test_registry_service.py
    └── integration/
        ├── __init__.py
        ├── conftest.py          # testcontainers Postgres + real LocalArtifactStore + faked TrainingPipelineClient
        ├── test_api.py
        ├── test_repository.py
        └── test_migrations.py
```

### 5.1 Repository-pattern persistence design

- `ModelVersionRepository`: `create_model_version(mv) -> ModelVersion` (write-once identity), `get(model_name, version)`, `list(model_name=None, lifecycle_state=None)`, `set_lifecycle_state(model_name, version, state)` (updates only the mutable state column), `get_latest_version(model_name) -> int | None`, `resolve_production(model_name) -> ModelVersion | None`, `attach_validation_evidence(...)`.
- `PromotionRepository`: `create_request`, `add_approval`, `set_request_state`, `get_request`, `append_history`, `list_history(model_name, version)`.
- `AuditRepository`: `append(event)` (append-only), `list(model_name=None, correlation_id=None)`.

Repositories translate ORM rows to/from frozen domain dataclasses via private `_to_domain_*` helpers, take an `AsyncSession` via constructor, and never `commit()` (the request-scoped session owns the transaction), exactly as the Training Pipeline's repositories do.

## 6. REST API Design

All endpoints are Pydantic-typed, documented via FastAPI OpenAPI (Requirement 19.9), return `ErrorResponse(error, detail)` with `404` for any missing resource (Requirement 19.10), and require a client `Idempotency-Key` header on every mutating call (Requirement 19.11).

| Method | Path | Purpose | Requirement |
|---|---|---|---|
| `POST` | `/v1/models` | Register a Model_Version from a Training Pipeline reference (idempotent) | 2, 19.1 |
| `GET` | `/v1/models` | List Model_Versions; optional `model_name` / `lifecycle_state` filters | 19.2 |
| `GET` | `/v1/models/{model_name}/versions/{version}` | Full metadata + Lineage + Reproducibility_Metadata | 19.3 |
| `GET` | `/v1/models/{model_name}/versions/{version}/metrics` | Metrics_Record | 19.4, 10 |
| `GET` | `/v1/models/{model_name}/versions/{version}/lineage` | Full Lineage chain | 9.2 |
| `GET` | `/v1/models/{model_name}/versions/{version}/artifact` | Download verified Model_Artifact bytes | 19.5, 8 |
| `POST` | `/v1/models/{model_name}/versions/{version}/transition` | Request a lifecycle transition (requester, target, justification) | 13, 19.6 |
| `POST` | `/v1/models/{model_name}/versions/{version}/approve` | Approve a pending Promotion_Request | 14, 19.6 |
| `POST` | `/v1/models/{model_name}/versions/{version}/reject` | Reject a pending Promotion_Request | 14.5, 19.6 |
| `POST` | `/v1/models/{model_name}/versions/{version}/rollback` | Request a rollback to this previously-PRODUCTION version | 15, 19.6 |
| `GET` | `/v1/models/{model_name}/production` | Resolve current Production_Model | 16.3, 19.7 |
| `GET` | `/v1/models/{model_name}/versions/{version}/promotion-history` | Ordered Promotion_History | 17.3, 19.8 |
| `GET` | `/v1/models/{model_name}/versions/{version}/audit` | Audit_History (also filterable by correlation id) | 18.3, 19.8 |
| `GET` | `/health`, `/health/live`, `/health/ready` | Liveness/readiness (own DB + Artifact_Store) | 24.1 |

Request body for `POST /v1/models`:

```json
{ "model_name": "aapl_5d_direction__random_forest", "version": 3, "training_run_id": 42 }
```

## 7. Database Schema

Postgres via SQLAlchemy 2.0 (`DeclarativeBase` + `Mapped`/`mapped_column`), snake_case plural tables — identical style to `aqros_training_pipeline.adapters.orm`. Only metadata is persisted here; artifact bytes live in the `Artifact_Store` (Section 8).

```mermaid
erDiagram
    REGISTERED_MODELS {
        bigint id PK
        string model_name
        string model_type
        datetime created_at
    }
    MODEL_VERSIONS {
        bigint id PK
        bigint registered_model_id FK
        string model_name
        integer version
        integer training_run_id
        string dataset_name
        integer dataset_version
        string dataset_checksum
        text feature_versions_json
        text metrics_json
        string artifact_path
        string artifact_checksum
        string checksum_algorithm
        string git_commit
        text reproducibility_metadata_json
        string lifecycle_state
        string approval_state
        text validation_evidence_json
        datetime created_at
    }
    PROMOTION_REQUESTS {
        bigint id PK
        bigint model_version_id FK
        string from_state
        string to_state
        string requester
        text justification
        string approval_state
        boolean is_rollback
        string idempotency_key
        datetime created_at
    }
    APPROVALS {
        bigint id PK
        bigint promotion_request_id FK
        string approver
        string approver_kind
        string decision
        text reason
        datetime created_at
    }
    PROMOTION_HISTORY {
        bigint id PK
        bigint model_version_id FK
        string from_state
        string to_state
        string requester
        text approvers_json
        text justification
        boolean is_rollback
        datetime created_at
    }
    AUDIT_EVENTS {
        bigint id PK
        string action
        string actor
        string model_name
        integer version
        string before_state
        string after_state
        text justification
        string correlation_id
        datetime created_at
    }
    REGISTERED_MODELS ||--o{ MODEL_VERSIONS : has
    MODEL_VERSIONS ||--o{ PROMOTION_REQUESTS : governs
    PROMOTION_REQUESTS ||--o{ APPROVALS : collects
    MODEL_VERSIONS ||--o{ PROMOTION_HISTORY : records
```

Key constraints and indexes:
- `UNIQUE (registered_model_id, version)` on `model_versions` — the version-uniqueness backstop (Requirements 3.2, 22.2).
- **Partial unique index** `uq_one_production_per_model` = `UNIQUE (registered_model_id) WHERE lifecycle_state = 'production'` — the single-PRODUCTION invariant (Requirements 16.1, 23.3).
- `UNIQUE (model_version_id, idempotency_key)` on `promotion_requests` — governance idempotency (Requirement 19.11).
- Indexes `ix_model_versions_model_name`, `ix_audit_events_correlation_id`, `ix_promotion_history_model_version_id`.
- `model_versions` identity columns (version, checksums, dataset/feature refs, metrics, reproducibility, artifact_path) are written once and never updated; only `lifecycle_state`, `approval_state`, and `validation_evidence_json` are mutable (Requirements 3.4, 3.5, 22.3).
- `promotion_history` and `audit_events` have no update/delete code path (append-only; Requirements 17.2, 18.2, 22.4).

Migration `0001_initial_schema.py` creates all tables, the partial unique index, and the indexes above, with a symmetric `downgrade()`, in the exact style of the Training Pipeline's `0001_initial_schema.py`.

## 8. Model Artifact Storage

`ArtifactStore` port (mirrors the Training Pipeline's verbatim — bytes in/bytes out, no filesystem-specific parameter):

```python
class ArtifactStore(ABC):
    @abstractmethod
    async def write_artifact(self, model_name: str, model_version: int, data: bytes) -> str: ...
    @abstractmethod
    async def read_artifact(self, model_name: str, model_version: int) -> bytes: ...

class ArtifactAlreadyExistsError(RuntimeError):
    """Raised by write_artifact when (model_name, model_version) is already persisted."""
```

`LocalArtifactStore` (MVP adapter, mirrors the Training Pipeline's):
- Path `{base_dir}/{model_name}/v{model_version}/model.joblib` — deterministically encodes model name and version (Requirement 8.1).
- `write_artifact` checks-then-writes inside a single `asyncio.to_thread` call and raises `ArtifactAlreadyExistsError` rather than overwriting (Requirement 7.6).
- `read_artifact` returns the exact persisted bytes (Requirement 8.2); the service recomputes and compares the checksum on retrieval and refuses on mismatch (Requirements 7.4, 7.5).
- Object-store swap (S3/MinIO/R2) is a drop-in adapter change in `api/deps.py`, zero change to domain or API (Requirements 8.4, 25.4). **Object storage is not a mandatory MVP dependency.**

## 9. Registration & Ingestion Workflow

```mermaid
sequenceDiagram
    participant Client
    participant API as Registry_API
    participant Svc as ModelRegistryService
    participant TPC as Training_Pipeline_Client
    participant Int as Integrity_Verifier
    participant Store as Artifact_Store
    participant Repo as ModelVersionRepository
    participant Aud as AuditRepository

    Client->>API: POST /v1/models {model_name, version, training_run_id}
    API->>Svc: register(ref)
    Svc->>Repo: get(model_name, version)
    alt already registered (same run id)
        Repo-->>Svc: existing ModelVersion
        Svc-->>API: 200 existing (idempotent)
    else new
        Svc->>TPC: get_trained_model_record(model_name, version)
        alt 404
            TPC-->>Svc: NotFound
            Svc-->>API: 404 trained model not found
        else record
            Svc->>Svc: validate mandatory metadata (dataset/feature/metrics/run/checksum)
            Svc->>TPC: download_artifact(model_name, version)
            Svc->>Int: verify_checksum(bytes, record)
            alt mismatch
                Int-->>Svc: fail
                Svc-->>API: 422 checksum mismatch (no persistence)
            else match
                Svc->>Store: write_artifact(model_name, version, bytes)
                Svc->>Repo: create_model_version(state=REGISTERED, approval=NOT_REQUIRED)
                Svc->>Aud: append('registered')  (same transaction)
                Svc-->>API: 201 ModelVersion
            end
        end
    end
```

## 10. Lifecycle State Machine

```mermaid
stateDiagram-v2
    [*] --> registered: register()
    registered --> validated: + Validation_Evidence (Req 12)
    validated --> staging: approval (policy)
    staging --> production: FOUR-EYES (Req 14)
    production --> deprecated: superseded (auto, Req 16.2) / manual
    deprecated --> archived: approval (policy)
    deprecated --> production: ROLLBACK · FOUR-EYES (Req 15)
    registered --> archived: abandon (Req 11.5)
    validated --> archived: abandon (Req 11.5)
    staging --> archived: abandon (Req 11.5)
    archived --> [*]
```

`transition_allowed(from_state, to_state, is_rollback)` is a pure function encoding exactly this table; any transition not on a permitted edge is rejected with an illegal-transition error (Requirement 11.3). `PRODUCTION` may reach `ARCHIVED` only via `DEPRECATED` (Requirement 11.6); `ARCHIVED` is terminal (Requirement 11.4).

## 11. Promotion & Approval Workflow

```mermaid
sequenceDiagram
    participant Requester
    participant API as Registry_API
    participant Svc as ModelRegistryService
    participant Pol as Approval_Policy
    participant Repo as ModelVersionRepository
    participant PRepo as PromotionRepository
    participant Aud as AuditRepository

    Requester->>API: POST .../transition {to_state, justification}
    API->>Svc: request_transition(...)
    Svc->>Svc: assert transition_allowed(from,to)
    Svc->>Pol: gate_for(from,to)
    alt gate == none
        Svc->>Repo: set_lifecycle_state(to)
        Svc->>PRepo: append_history(...)
        Svc->>Aud: append('transition_requested'+'applied')
    else gate == approval / four-eyes
        Svc->>PRepo: create_request(state=PENDING)
        Svc->>Repo: set approval_state=PENDING
        Svc->>Aud: append('transition_requested')
    end

    Note over Requester,API: later — distinct authorized approver(s)
    Requester->>API: POST .../approve
    API->>Svc: approve(request, approver)
    Svc->>Pol: is approver human, authorized, distinct from requester & prior approvers?
    alt satisfies four-eyes threshold
        Svc->>Repo: (txn) demote incumbent PRODUCTION -> DEPRECATED, set target -> to_state
        Svc->>PRepo: set request APPROVED + append_history
        Svc->>Aud: append('approved'+'applied')
    else needs more approvals
        Svc->>PRepo: record approval; keep PENDING
        Svc->>Aud: append('approved (partial)')
    end
```

The demote-incumbent + promote-target step for `PRODUCTION` runs in one serializable transaction guarded by the partial unique index (Section 7) so the single-PRODUCTION invariant holds under concurrency (Requirements 16.1, 16.2, 23.3).

## 12. Rollback Workflow

```mermaid
sequenceDiagram
    participant Requester
    participant Svc as ModelRegistryService
    participant Repo as ModelVersionRepository
    participant Aud as AuditRepository
    Requester->>Svc: rollback(model_name, version)
    Svc->>Repo: assert version was previously PRODUCTION (in promotion history)
    alt never production
        Svc-->>Requester: reject (Req 15.4)
    else eligible
        Svc->>Svc: require FOUR-EYES (Req 15.2)
        Svc->>Repo: (txn) demote current PRODUCTION -> DEPRECATED; set target -> PRODUCTION
        Svc->>Aud: append('rolled_back') + promotion-history entry (is_rollback=true, Req 15.5)
    end
```

## 13. Error Handling

| Failure | Detected by | Outcome | HTTP surface |
|---|---|---|---|
| Trained model not found upstream | `Training_Pipeline_Client` | registration rejected; reason recorded | `404` |
| Training Pipeline unreachable / error | `Training_Pipeline_Client` | registration failed; no partial version (Req 1.6, 20.3) | `502` |
| Missing dataset/feature/metrics/run metadata | `ModelRegistryService` | registration rejected; missing-field reason (Req 4.3, 5.3, 6.2) | `422` |
| Artifact checksum mismatch on ingest | `Integrity_Verifier` | registration rejected; no persistence (Req 7.2) | `422` |
| Stored artifact bytes ≠ recorded checksum on retrieval | `Integrity_Verifier` | refuse to serve; integrity-failure recorded (Req 7.5) | `409`/`500` typed |
| Illegal lifecycle transition | `Lifecycle_State_Machine` | rejected; illegal-transition reason (Req 11.3) | `409` |
| VALIDATED requested without evidence | `Approval_Policy` | rejected; missing-evidence reason (Req 12.2) | `422` |
| PRODUCTION/rollback without four-eyes | `Four_Eyes` | stays PENDING; not applied (Req 14, 15.2) | `202`/`409` |
| Approver == requester or duplicate approver | `Four_Eyes` | approval not counted (Req 14.2, 14.6) | `409` |
| Automated principal attempts PRODUCTION/approval | `Four_Eyes` | rejected (Req 14.7, 21.2) | `403` |
| Rollback of never-PRODUCTION version | `ModelRegistryService` | rejected; reason recorded (Req 15.4) | `409` |
| Audit/approval dependency unavailable | `ModelRegistryService` | promotion refused; reads still served (Req 20.1, 20.2, 23.1) | `503` on write |
| Unknown model/version/artifact/history | Query routes | typed 404 (Req 19.10) | `404` |
| Governed transition fails partway | transaction rollback | state + approval unchanged (Req 20.4) | `500` typed |

Every rejection records a human-readable reason (Requirement 20.5). All privileged actions append an `Audit_Event` in the same transaction (Requirement 18.1).

## 14. Docker Deployment

- Image: shared `docker/Dockerfile.service` with `SERVICE=model-registry`, `MODULE=aqros_model_registry`, `PORT=8004` — no Dockerfile change (Requirement 25.1). Verified: this exact service slot already exists in `docker-compose.yml`.
- Ports (verified against `docker-compose.yml`): **service 8004** (existing reserved slot), **dedicated Postgres 5436** (`model-registry-db`), continuing the sequence 5432/5433/5434/5435 (Requirements 25.2, 25.3).
- The reserved `model-registry` compose entry gains a DB dependency, env, and a local artifact volume (implementation-phase edit, shown here for the design record):

```yaml
  model-registry-db:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: aqros
      POSTGRES_PASSWORD: aqros
      POSTGRES_DB: aqros_model_registry
    ports: ["5436:5432"]
    volumes:
      - model-registry-db-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U aqros -d aqros_model_registry"]
      interval: 5s
      timeout: 5s
      retries: 10
    networks: [aqros]

  model-registry:
    <<: *service-defaults
    build:
      context: .
      dockerfile: docker/Dockerfile.service
      args: { SERVICE: model-registry, MODULE: aqros_model_registry, PORT: "8004" }
    ports: ["8004:8004"]
    environment:
      AQROS_ENVIRONMENT: dev
      AQROS_LOG_JSON: "false"
      AQROS_DATABASE_URL: postgresql+asyncpg://aqros:aqros@model-registry-db:5432/aqros_model_registry
      # Reaches the Training Pipeline only through its published REST API.
      AQROS_TRAINING_PIPELINE_BASE_URL: http://training-pipeline:8009
      AQROS_ARTIFACT_DIR: /data/model-registry/artifacts
    volumes:
      - model-registry-artifacts:/data/model-registry/artifacts
    depends_on:
      model-registry-db:
        condition: service_healthy
      training-pipeline:
        condition: service_started
```

  plus `model-registry-db-data` and `model-registry-artifacts` named volumes. Artifact persistence is a **local volume behind the ArtifactStore interface** (no object-store service required in the MVP; swappable later — Requirement 25.4).

- `Settings` (`config.py`) extends `aqros_core.config.BaseServiceSettings`: `service_name="model-registry"`, `port=8004`, `database_url` defaulting to `postgresql+asyncpg://aqros:aqros@localhost:5436/aqros_model_registry`, `training_pipeline_base_url: AnyHttpUrl` defaulting to `http://localhost:8009`, and `artifact_dir` — all overridable via `AQROS_*` (Requirement 25.5).
- Health: `/health/live` (always healthy), `/health/ready` (checks `database` via `db.ping` and `artifact_store`), `/health` alias — identical pattern to the existing services (Requirement 24.1).
- Root `pyproject.toml` `[tool.ruff.lint.isort].known-first-party` gains `"aqros_model_registry"` (implementation-phase edit).

## 15. Security

- **RBAC:** every mutating action authorized against the acting principal's role; promotion requests, approvals, and rollbacks require the mandated roles (Requirement 21.1).
- **No self-promotion:** the `Four_Eyes` evaluator rejects any approval or PRODUCTION promotion by a non-human principal (Requirements 14.7, 21.2; CLAUDE.md §7.3) — enforced in pure domain logic.
- **Four-eyes:** two distinct human approvers, neither the requester, for PRODUCTION and rollback; the same approver is never counted twice (Requirements 14.1, 14.2, 14.6).
- **Artifact signing:** where configured, the `ArtifactSigner` port verifies a signature before serving and refuses unsigned/invalid artifacts (Requirement 21.3).
- **Secrets:** any credential is fetched at runtime from the platform secrets mechanism; none in code, image layers, or version control (Requirement 21.4; CLAUDE.md §7.5).
- **Audit:** every privileged action is hash-chainable, append-only, written atomically with its state change, and forwarded (guaranteed-delivery) to the platform Audit Ledger where configured (Requirements 18, 21.5; CLAUDE.md §7.8).

## Correctness Properties

### Property 1: Ingestion is via the Training Pipeline REST API only; downstream never queries it
For any registration, the Model_Version's record and artifact are obtained solely through the Training Pipeline's REST API, and every downstream read (metadata, metrics, lineage, artifact) is served by the Registry without contacting the Training Pipeline.
**Validates: Requirements 1.2, 1.4, 1.5, 28.7**

### Property 2: Upstream failure yields no partial Model_Version
For any Training Pipeline error, unreachability, or 404 during registration, no Model_Version is persisted and a failure reason is recorded.
**Validates: Requirements 1.6, 2.3, 20.3**

### Property 3: Registration is idempotent on (model_name, version, training_run_id)
For any repeated registration of the same reference, exactly one Model_Version exists and the repeated call returns the existing record.
**Validates: Requirements 2.4**

### Property 4: A new Model_Version starts in REGISTERED / NOT_REQUIRED
For any successful first registration, the Model_Version's lifecycle state is REGISTERED and its approval state is NOT_REQUIRED.
**Validates: Requirements 2.5**

### Property 5: Version identity is unique and immutable
For all Model_Versions of a Registered_Model, versions are unique, and no identity field (version, checksum, dataset version, feature versions, metrics, git commit, training run id, artifact reference) is ever modified after registration.
**Validates: Requirements 3.2, 3.3, 3.4, 3.5, 22.2, 22.3**

### Property 6: Mandatory metadata completeness gates persistence
For any Trained_Model_Record missing a mandatory field (dataset name/version/checksum, feature versions, metrics, training run id, checksum), no Model_Version is persisted; an absent git commit alone is tolerated and recorded as absent.
**Validates: Requirements 4.3, 5.3, 6.1, 6.2, 6.3, 6.4**

### Property 7: Checksum gate on ingestion
For any downloaded artifact whose computed checksum ≠ the reported checksum, registration is rejected and nothing is persisted.
**Validates: Requirements 7.1, 7.2**

### Property 8: Checksum gate on retrieval; never overwrite
For any stored artifact, retrieval verifies bytes against the recorded checksum and refuses on mismatch; a second write to an existing (model_name, version) is rejected and the original bytes are unchanged.
**Validates: Requirements 7.3, 7.4, 7.5, 7.6**

### Property 9: Artifact round-trip
For any artifact persisted under (model_name, version), reading it back returns exactly the bytes written.
**Validates: Requirements 8.1, 8.2, 8.3**

### Property 10: Lineage and reproducibility are complete and immutable
For any Model_Version, the Reproducibility_Metadata contains version, dataset name/version/checksum, feature names and versions, git commit, training run id, timestamp, hyperparameters, and aggregated metrics, and none of it changes after registration.
**Validates: Requirements 9.1, 9.3, 9.4, 4.1, 5.1, 10.1, 10.3**

### Property 11: Only legal lifecycle transitions are applied
For any (from_state, to_state), a transition is applied only if it lies on a permitted edge (forward chain, rollback DEPRECATED→PRODUCTION, or abandonment to ARCHIVED); every other transition is rejected.
**Validates: Requirements 11.2, 11.3**

### Property 12: ARCHIVED is terminal and PRODUCTION cannot skip DEPRECATED
For any Model_Version in ARCHIVED, no further transition is permitted; no PRODUCTION version transitions to ARCHIVED without first entering DEPRECATED.
**Validates: Requirements 11.4, 11.6**

### Property 13: VALIDATED requires validation evidence
For any REGISTERED→VALIDATED transition, the transition succeeds only if Validation_Evidence is attached, and that evidence is recorded immutably.
**Validates: Requirements 12.1, 12.2, 12.3**

### Property 14: PRODUCTION and rollback require four-eyes by distinct humans
For any promotion to PRODUCTION or any rollback, the transition applies only after two distinct human approvers, neither of whom is the requester, have approved; the same approver never counts twice.
**Validates: Requirements 14.1, 14.2, 14.4, 14.6, 15.2**

### Property 15: Automated principals cannot satisfy a PRODUCTION gate
For any approval or PRODUCTION promotion attributed to a non-human principal, the action is rejected and the state is unchanged.
**Validates: Requirements 14.7, 21.2**

### Property 16: Rejection blocks the transition
For any rejected Promotion_Request, the approval state becomes REJECTED, the lifecycle state is unchanged, and a reason is recorded.
**Validates: Requirements 14.5, 20.4, 20.5**

### Property 17: No automatic promotion to PRODUCTION
For any sequence of events lacking an explicit approved Promotion_Request targeting PRODUCTION, no Model_Version ever becomes PRODUCTION.
**Validates: Requirements 13.5**

### Property 18: Single-PRODUCTION invariant with incumbent demotion
For any Registered_Model at any time, at most one Model_Version is PRODUCTION; promoting a new version demotes the prior incumbent to DEPRECATED atomically, even under concurrent promotion attempts.
**Validates: Requirements 16.1, 16.2, 23.2, 23.3**

### Property 19: Production resolution
For any Registered_Model, the production endpoint returns the unique PRODUCTION version, or reports none exists if there is no PRODUCTION version.
**Validates: Requirements 16.3, 16.4**

### Property 20: Rollback only from a previously-PRODUCTION version
For any rollback request naming a version that was never PRODUCTION, the request is rejected; a valid rollback sets the target to PRODUCTION, demotes the incumbent, and is recorded as a rollback in history.
**Validates: Requirements 15.1, 15.3, 15.4, 15.5**

### Property 21: Promotion history is complete and append-only
For any applied transition or rollback, exactly one ordered history entry is appended (from, to, requester, approvers, justification, timestamp), and no history entry is ever modified or deleted.
**Validates: Requirements 17.1, 17.2, 17.3**

### Property 22: Audit trail captures every privileged action, append-only
For any registration, transition request, approval, rejection, rollback, or PRODUCTION-artifact retrieval, exactly one Audit_Event is recorded with actor, timestamp, affected version, before/after state, justification (where applicable), and correlation id, and no Audit_Event is ever modified or deleted.
**Validates: Requirements 18.1, 18.2, 18.3**

### Property 23: Any nonexistent resource yields a typed 404
For any identifier that does not correspond to an existing Registered_Model, Model_Version, artifact, Promotion_Request, metrics, or lineage, the API responds with 404 and a typed error body naming the missing resource.
**Validates: Requirements 19.10**

### Property 24: Reads remain available while promotion dependencies degrade
While an approval/audit dependency is unavailable, reads of existing versions, metrics, lineage, artifacts, and production resolution still succeed, while PRODUCTION promotions are refused.
**Validates: Requirements 20.1, 20.2, 23.1**

### Property 25: Reproducibility round-trip
For any registered Model_Version, its metadata, lineage, and artifact are retrievable unchanged, sufficient to reproduce the model independently.
**Validates: Requirements 9.2, 9.4, 26.4**

## Testing Strategy

### Dual testing approach
- **Unit tests** (`tests/unit/`) exercise every domain module (`lifecycle.py`, `approval.py`, `integrity.py`, `lineage.py`, `versioning`, `services.py`) against fakes for every port (`FakeTrainingPipelineClient`, `FakeArtifactStore`, `FakeModelVersionRepository`, `FakePromotionRepository`, `FakeAuditRepository`) — no real HTTP, filesystem, or database access (Requirement 26.1).
- **Property-based tests** using `hypothesis` (min `@settings(max_examples=100)`), one test per correctness property above, tagged `# Feature: model-registry, Property N: <text>` directly above each test, placed in the unit file for the module the property most exercises (e.g. Property 11/12 in `test_lifecycle.py`, Property 14/15 in `test_approval.py`, Property 18 in `test_registry_service.py`). Covers the invariants required by Requirement 26.2: monotonic/unique versioning, legal-transition-only, four-eyes, single-PRODUCTION, immutability, checksum-integrity gate.
- **Integration tests** (`tests/integration/`) run the full FastAPI app via `httpx.AsyncClient` + `ASGITransport` against a real Postgres from `testcontainers.postgres.PostgresContainer` and a real `LocalArtifactStore`, with `TrainingPipelineClient` swapped for an in-memory fake (no live Training Pipeline required) — Requirements 26.3. Mirrors the Training Pipeline's `conftest.py`/`test_api.py` fixtures (`postgres_container`, `engine`, `session_factory`, `db_session`, `client` overriding `get_session`/`get_training_pipeline_client`/`get_artifact_store`).
- `tests/integration/test_migrations.py` runs the Alembic `0001` upgrade/downgrade (including the partial unique index) against the testcontainers Postgres.
- A **reproducibility round-trip** integration test registers a Model_Version and asserts metadata, lineage, and artifact round-trip unchanged (Requirement 26.4).
- `tests/test_health.py` covers readiness composition (all healthy → 200; one failing → 503) — Requirement 24.1.

### Quality gates
`ruff check`, `black --check`, `mypy --strict`, and `pytest` (non-zero exit on any failure or setup error) run against `backend/model-registry` in the same CI pattern as the four existing services (Requirements 26.5, 27).
