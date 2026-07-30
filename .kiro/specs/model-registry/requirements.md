# Requirements Document

## Introduction

The Model Registry is a new AQROS backend microservice (`backend/model-registry`, module `aqros_model_registry`) that is the **single, authoritative source of truth for every trained model on the platform**. It ingests trained-model records exclusively from the Training Pipeline service's published REST API, records each as an immutable, versioned, fully-lineaged entry, governs each model through a strictly-ordered lifecycle, and serves model metadata, metrics, lineage, and artifacts to every downstream consumer (Backtesting, Strategy Engine, Paper Trading, Live Trading, and the AI Brain).

The Model Registry is the **governed gate between research and capital** (CLAUDE.md Hard Rule §7.4): a model reaches production use only through an explicit, human-approved, four-eyes promotion, and it is never auto-promoted. The registry itself never trains, evaluates, or serves inference — it records, versions, governs, and vends. Because it is the single source of truth, **no downstream service is permitted to query the Training Pipeline directly**; all model reads flow through the registry. Every action that changes a model's lifecycle or approval state is captured in an append-only, tamper-evident audit history to preserve reproducibility and satisfy the platform's auditability discipline (CLAUDE.md Hard Rule §7.8).

This document covers the MVP-through-V1 scope of the Model Registry: model-version registration and ingestion, immutable versioning, mandatory metadata and lineage capture, dataset- and feature-version linkage, metrics storage, artifact integrity and retrieval, the six-state model lifecycle, the promotion and four-eyes approval workflows, rollback, promotion history, audit history, reproducibility, the REST API, repository/persistence, Docker deployment, and the testing and quality gates shared by every existing AQROS backend service.

The mandated lifecycle is strictly ordered:

```
REGISTERED → VALIDATED → STAGING → PRODUCTION → DEPRECATED → ARCHIVED
```

with an explicit, audited rollback edge that re-promotes a previously-production version back to PRODUCTION.

## Glossary

- **Model_Registry**: The `model-registry` backend microservice as a whole, exposing a REST API and owning the authoritative record of every trained model.
- **Registered_Model**: A logical model family identified by a unique model name (the composite `{dataset_name}__{model_type}` string produced by the Training Pipeline), under which one or more immutable Model_Versions are recorded.
- **Model_Version**: An immutable, monotonically incrementing integer-versioned record of one trained model under a Registered_Model, carrying its complete metadata, lineage, metrics, artifact reference, lifecycle state, and approval state.
- **Training_Pipeline_Client**: The adapter component within the Model_Registry that communicates exclusively with the Training Pipeline service's published REST API to ingest trained-model records and their serialized artifacts.
- **Trained_Model_Record**: The Training Pipeline's published record of one trained candidate (model name, model type, model version, training run id, dataset name, dataset version, dataset checksum, artifact reference, per-fold and aggregated metrics, feature importance, and reproducibility metadata), retrieved via `GET /v1/trained-models/...`.
- **Model_Artifact**: The serialized, versioned model file (bytes) associated with a Model_Version, persisted immutably by the Model_Registry's Artifact_Store.
- **Artifact_Store**: The component responsible for persisting and retrieving versioned, immutable Model_Artifact bytes, backed by local artifact storage in the MVP behind the ArtifactStore interface and swappable for an object store later without any change to domain or API logic, mirroring the Dataset Builder / Training Pipeline storage-port pattern.
- **Model_Checksum**: The cryptographic checksum of a Model_Artifact, computed with a named algorithm and used to verify artifact integrity on ingestion and on retrieval.
- **Reproducibility_Metadata**: The lineage record captured for every Model_Version: model version, dataset name, dataset version, dataset checksum, feature names and feature versions, git commit of the Training Pipeline code at training time, training run id, training timestamp, hyperparameters used, and the model's aggregated metrics.
- **Lineage**: The full provenance chain of a Model_Version — the dataset build run and dataset version it was trained on, the feature versions used, the code commit, the training run, and the metrics that resulted.
- **Dataset_Version**: The immutable dataset version (name + version + checksum) that a Model_Version was trained on, as recorded by the Dataset Builder and carried through the Training Pipeline.
- **Feature_Versions**: The mapping from feature name to feature version used to train a Model_Version.
- **Metrics_Record**: The per-fold and aggregated evaluation metrics of a Model_Version, captured at registration and immutable thereafter.
- **Training_Run_Id**: The identifier of the Training Pipeline training run that produced a Model_Version.
- **Git_Commit**: The git commit SHA of the Training Pipeline code at the time the model was trained, or an explicit absence marker if it could not be determined.
- **Lifecycle_State**: The current governed state of a Model_Version, one of `REGISTERED`, `VALIDATED`, `STAGING`, `PRODUCTION`, `DEPRECATED`, `ARCHIVED`.
- **Approval_State**: The approval status of a pending governed transition, one of `NOT_REQUIRED`, `PENDING`, `APPROVED`, `REJECTED`.
- **Validation_Evidence**: The proof that a Model_Version has passed the platform's validation gauntlet (e.g., a signed backtest report / validation dossier reference), required to move a Model_Version from `REGISTERED` to `VALIDATED`.
- **Promotion**: A governed transition of a Model_Version from one Lifecycle_State to the next-permitted state.
- **Promotion_Request**: A request to transition a Model_Version to a new Lifecycle_State, carrying the requester identity, the target state, and a justification.
- **Approver**: A human principal, authenticated and role-authorized, who approves or rejects a Promotion_Request.
- **Four_Eyes**: The control requiring that a governed transition into PRODUCTION (including rollback) be approved by at least two distinct Approvers, none of whom is the requester.
- **Production_Model**: The single Model_Version of a Registered_Model currently in the `PRODUCTION` Lifecycle_State (the "champion").
- **Rollback**: A governed, audited transition that re-designates a previously-PRODUCTION, currently-DEPRECATED Model_Version as the Production_Model, demoting the incumbent.
- **Promotion_History**: The append-only ordered record of every Lifecycle_State transition and rollback for a Model_Version.
- **Audit_Event**: An append-only, tamper-evident record of one privileged action (registration, transition request, approval, rejection, rollback, or production-artifact retrieval), including actor, timestamp, before/after state, justification, and correlation identifier.
- **Audit_History**: The complete, append-only, query-only sequence of Audit_Events.
- **Registry_API**: The FastAPI REST interface exposed by the Model_Registry for registration, governance, and retrieval.
- **Downstream_Consumer**: Any service that reads model metadata, metrics, lineage, or artifacts — including Backtesting, Strategy Engine, Paper Trading, Live Trading, and the AI Brain.

## Requirements

### Requirement 1: Single Source of Truth and Ingestion from the Training Pipeline Only

**User Story:** As a platform architect, I want the Model Registry to be the single authoritative source of every trained model, ingesting only from the Training Pipeline's published API, so that downstream services never depend on the Training Pipeline directly and model records have exactly one owner.

#### Acceptance Criteria

1. THE Model_Registry SHALL be the single authoritative system of record for every trained Model_Version on the platform.
2. WHEN the Model_Registry ingests a Trained_Model_Record, THE Training_Pipeline_Client SHALL retrieve that record only via HTTP requests to the Training Pipeline's published REST API.
3. THE Model_Registry SHALL NOT establish a direct database connection to the Training Pipeline's, Dataset Builder's, Feature Store's, or Market Data's databases.
4. THE Model_Registry SHALL expose all model metadata, metrics, lineage, and artifacts required by any Downstream_Consumer so that no Downstream_Consumer needs to query the Training Pipeline.
5. WHEN a Trained_Model_Record is registered, THE Training_Pipeline_Client SHALL retrieve the associated Model_Artifact bytes from the Training Pipeline's published artifact endpoint and hand them to the Artifact_Store for independent, immutable persistence, so that artifact retrieval by a Downstream_Consumer never contacts the Training Pipeline.
6. IF the Training Pipeline's REST API returns an error response or is unreachable during ingestion, THEN THE Model_Registry SHALL fail the registration, record the failure reason, and SHALL NOT create a partial or unverified Model_Version.

### Requirement 2: Model Registration

**User Story:** As a quant researcher, I want every trained model recorded in the registry with a stable identity, so that a candidate can be referenced, governed, and reproduced unambiguously.

#### Acceptance Criteria

1. THE Registry_API SHALL expose an endpoint that registers a new Model_Version from a Training Pipeline reference (model name, model version, and training run id).
2. WHEN a registration request references a Trained_Model_Record that exists in the Training Pipeline, THE Model_Registry SHALL create a Model_Version under the corresponding Registered_Model, capturing all mandatory metadata defined in Requirement 6.
3. IF a registration request references a model name and version that the Training Pipeline does not have, THEN THE Model_Registry SHALL reject the request and report that the referenced trained model does not exist.
4. WHEN a registration request is received for a model name, version, and training run id that has already been registered, THE Model_Registry SHALL treat the request as idempotent and SHALL NOT create a duplicate Model_Version.
5. WHEN a Model_Version is first created, THE Model_Registry SHALL set its Lifecycle_State to `REGISTERED` and its Approval_State to `NOT_REQUIRED`.

### Requirement 3: Immutable, Incrementing Model Versioning

**User Story:** As a platform architect, I want every model version to be immutable and monotonically incrementing per model name, so that model references are as reproducible and unambiguous as the datasets they were trained on.

#### Acceptance Criteria

1. WHEN the Model_Registry registers the first Model_Version for a given Registered_Model, THE Model_Registry SHALL assign it the version recorded by the Training Pipeline for that model name.
2. FOR ALL Model_Versions sharing the same Registered_Model, THE Model_Registry SHALL ensure every version is unique.
3. THE Model_Registry SHALL NOT modify, reuse, or decrement the version of any recorded Model_Version.
4. THE Model_Registry SHALL NOT modify the identity fields of a recorded Model_Version — its version, Model_Checksum, Dataset_Version, Feature_Versions, Metrics_Record, Git_Commit, Training_Run_Id, and Model_Artifact reference — after registration.
5. WHILE a Model_Version exists, THE Model_Registry SHALL permit changes only to its Lifecycle_State, its Approval_State, and its append-only Promotion_History and Audit_History.

### Requirement 4: Dataset Version Linkage

**User Story:** As a quant researcher, I want each model version linked to the exact dataset version it was trained on, so that I can trace any model back to its training data.

#### Acceptance Criteria

1. WHEN the Model_Registry registers a Model_Version, THE Model_Registry SHALL record the dataset name, dataset version, and dataset checksum on which that model was trained, as reported by the Training Pipeline.
2. THE Model_Registry SHALL expose the Dataset_Version linkage of any Model_Version through the Registry_API.
3. IF a Trained_Model_Record lacks a dataset name, dataset version, or dataset checksum, THEN THE Model_Registry SHALL reject the registration and record the missing linkage as the rejection reason.

### Requirement 5: Feature Version Linkage

**User Story:** As a quant researcher, I want each model version linked to the exact feature versions used, so that I can reproduce and audit the feature inputs of any model.

#### Acceptance Criteria

1. WHEN the Model_Registry registers a Model_Version, THE Model_Registry SHALL record the mapping from feature name to feature version used to train that model, as reported by the Training Pipeline.
2. THE Model_Registry SHALL expose the Feature_Versions linkage of any Model_Version through the Registry_API.
3. IF a Trained_Model_Record omits the feature versions, THEN THE Model_Registry SHALL reject the registration and record the missing feature-version linkage as the rejection reason.

### Requirement 6: Mandatory Model Metadata Completeness

**User Story:** As a platform architect, I want every model version to carry complete, verified metadata, so that no model can be governed or served without full provenance.

#### Acceptance Criteria

1. WHEN the Model_Registry registers a Model_Version, THE Model_Registry SHALL record all of the following: an immutable version, a Model_Checksum, the Dataset_Version, the Feature_Versions, the Metrics_Record, the Model_Artifact reference, the Git_Commit, the Training_Run_Id, the Approval_State, the Lifecycle_State, and the Lineage.
2. THE Model_Registry SHALL NOT create a Model_Version whose mandatory metadata is incomplete.
3. IF the Git_Commit of the Training Pipeline code cannot be determined for a Trained_Model_Record, THEN THE Model_Registry SHALL record the Git_Commit as explicitly absent and SHALL proceed with the remainder of the mandatory metadata.
4. IF recording any mandatory metadata field other than an absent Git_Commit fails for any reason, including a storage error, THEN THE Model_Registry SHALL NOT persist the Model_Version.

### Requirement 7: Artifact Integrity and Checksum Verification

**User Story:** As a risk-conscious platform owner, I want the registry to guarantee that every served model artifact is exactly the trained bytes, so that a corrupted or substituted model can never reach capital.

#### Acceptance Criteria

1. WHEN the Model_Registry ingests a Model_Artifact, THE Model_Registry SHALL compute the artifact's checksum using the algorithm named in the Trained_Model_Record and compare it against the checksum reported by the Training Pipeline.
2. IF the computed checksum of an ingested Model_Artifact does not equal the checksum reported by the Training Pipeline, THEN THE Model_Registry SHALL reject the registration and record a checksum-mismatch error without persisting the Model_Version.
3. WHEN a Model_Artifact is persisted, THE Model_Registry SHALL store its verified Model_Checksum as an immutable field of the Model_Version.
4. WHEN the Registry_API serves a Model_Artifact, THE Model_Registry SHALL verify that the served bytes match the recorded Model_Checksum.
5. IF the bytes of a stored Model_Artifact do not match the recorded Model_Checksum at retrieval time, THEN THE Model_Registry SHALL refuse to serve the artifact and record an integrity-failure error.
6. THE Artifact_Store SHALL NOT overwrite a previously persisted Model_Artifact.

### Requirement 8: Artifact Storage and Retrieval

**User Story:** As a quant researcher, I want every model's serialized artifact stored immutably in the registry and retrievable by version, so that any recorded model can be reloaded exactly as it was produced, without ever contacting the Training Pipeline.

#### Acceptance Criteria

1. WHEN the Model_Registry persists a Model_Artifact, THE Artifact_Store SHALL store it under a path or key that includes its model name and version.
2. WHEN the Registry_API receives a request for a Model_Artifact identified by model name and version, THE Artifact_Store SHALL return that exact Model_Artifact.
3. THE Model_Registry SHALL serve Model_Artifacts to Downstream_Consumers such that no Downstream_Consumer contacts the Training Pipeline to obtain an artifact.
4. WHERE a future deployment configures an object-store-backed Artifact_Store implementation in place of the local implementation, THE Model_Registry SHALL persist and retrieve Model_Artifacts through the same Artifact_Store interface without any change to the Registry_API or governance logic.

### Requirement 9: Lineage and Reproducibility

**User Story:** As a platform architect, I want every model version to carry complete lineage and be fully reproducible, so that any model can be traced to the exact data, code, and configuration that produced it.

#### Acceptance Criteria

1. WHEN the Model_Registry registers a Model_Version, THE Model_Registry SHALL record Reproducibility_Metadata containing the version, dataset name, dataset version, dataset checksum, feature names and feature versions, git commit, training run id, training timestamp, hyperparameters, and aggregated metrics.
2. THE Registry_API SHALL expose an endpoint that returns the full Lineage of a Model_Version, including its Dataset_Version, Feature_Versions, Git_Commit, and Training_Run_Id.
3. FOR ALL recorded Model_Versions, THE Model_Registry SHALL keep the Reproducibility_Metadata immutable.
4. THE Model_Registry SHALL retain sufficient Reproducibility_Metadata for a Model_Version to be reproduced independently, without recourse to any mutable external state.

### Requirement 10: Metrics Storage

**User Story:** As a quant researcher, I want each model version's evaluation metrics stored in the registry, so that I can compare and select candidates without re-running training.

#### Acceptance Criteria

1. WHEN the Model_Registry registers a Model_Version, THE Model_Registry SHALL record the model's per-fold metrics and aggregated metrics as reported by the Training Pipeline.
2. THE Registry_API SHALL expose an endpoint that returns the Metrics_Record of a Model_Version by its model name and version.
3. FOR ALL recorded Model_Versions, THE Model_Registry SHALL keep the Metrics_Record immutable after registration.

### Requirement 11: Model Lifecycle States and Legal Transitions

**User Story:** As a risk-conscious platform owner, I want models to move only through a strictly-ordered lifecycle, so that no model can reach production without passing every prior gate.

#### Acceptance Criteria

1. THE Model_Registry SHALL represent the Lifecycle_State of every Model_Version as exactly one of `REGISTERED`, `VALIDATED`, `STAGING`, `PRODUCTION`, `DEPRECATED`, or `ARCHIVED`.
2. THE Model_Registry SHALL permit forward Lifecycle_State transitions only in the order `REGISTERED → VALIDATED → STAGING → PRODUCTION → DEPRECATED → ARCHIVED`.
3. IF a transition request would move a Model_Version to a state that is not the next permitted forward state, and is not the explicit Rollback transition defined in Requirement 15, and is not an abandonment transition to `ARCHIVED` defined in Acceptance Criterion 11.5, THEN THE Model_Registry SHALL reject the transition and record an illegal-transition error.
4. THE Model_Registry SHALL treat `ARCHIVED` as a terminal Lifecycle_State from which no further transition is permitted.
5. WHERE a Model_Version has not reached `PRODUCTION`, THE Model_Registry SHALL permit an abandonment transition from its current non-`PRODUCTION` state directly to `ARCHIVED`, recorded and audited like any other transition.
6. THE Model_Registry SHALL NOT permit a Model_Version in `PRODUCTION` to transition to `ARCHIVED` without first transitioning to `DEPRECATED`.

### Requirement 12: Validation Gate

**User Story:** As a risk-conscious platform owner, I want a model to reach VALIDATED only with attached validation evidence, so that unvalidated models cannot advance toward capital.

#### Acceptance Criteria

1. WHEN a request is made to transition a Model_Version from `REGISTERED` to `VALIDATED`, THE Model_Registry SHALL require Validation_Evidence to be attached to the Model_Version.
2. IF a transition from `REGISTERED` to `VALIDATED` is requested without Validation_Evidence, THEN THE Model_Registry SHALL reject the transition and record the missing evidence as the rejection reason.
3. WHEN Validation_Evidence is attached to a Model_Version, THE Model_Registry SHALL record it immutably as part of the Model_Version's Lineage.

### Requirement 13: Promotion Workflow

**User Story:** As a risk-officer, I want promotions to be explicit, justified, and governed, so that every advancement toward production is deliberate and auditable.

#### Acceptance Criteria

1. THE Registry_API SHALL expose an endpoint that accepts a Promotion_Request for a Model_Version, carrying the requester identity, the target Lifecycle_State, and a justification.
2. WHEN a Promotion_Request targets a Lifecycle_State that requires approval per Requirement 14, THE Model_Registry SHALL set the Model_Version's Approval_State to `PENDING` and SHALL NOT change its Lifecycle_State until the approval workflow completes.
3. WHEN a Promotion_Request targets a Lifecycle_State that does not require approval, THE Model_Registry SHALL apply the transition immediately if it is legal per Requirement 11.
4. WHEN a Promotion_Request is applied, THE Model_Registry SHALL append the transition to the Model_Version's Promotion_History and record a corresponding Audit_Event.
5. THE Model_Registry SHALL NOT automatically promote any Model_Version to `PRODUCTION` without an explicit, approved Promotion_Request.

### Requirement 14: Approval Workflow and Four-Eyes Control

**User Story:** As a platform owner, I want promotions to production to require two distinct human approvals, so that no single actor can place a model into production use.

#### Acceptance Criteria

1. WHEN a Promotion_Request targets the `PRODUCTION` Lifecycle_State, THE Model_Registry SHALL require Four_Eyes approval before applying the transition.
2. THE Model_Registry SHALL require that the two distinct Approvers of a `PRODUCTION` promotion are each different from the requester of that Promotion_Request.
3. WHILE a Promotion_Request's Approval_State is `PENDING`, THE Registry_API SHALL expose endpoints for an authorized Approver to approve or reject the request.
4. WHEN the required number of distinct authorized approvals for a Promotion_Request has been recorded, THE Model_Registry SHALL set its Approval_State to `APPROVED` and apply the targeted transition if it is legal per Requirement 11.
5. IF an authorized Approver rejects a Promotion_Request, THEN THE Model_Registry SHALL set its Approval_State to `REJECTED`, SHALL NOT change the Model_Version's Lifecycle_State, and SHALL record the rejection reason.
6. THE Model_Registry SHALL NOT count the same Approver more than once toward the Four_Eyes threshold for a single Promotion_Request.
7. THE Model_Registry SHALL NOT permit any non-human or automated principal to satisfy an approval required for a transition into `PRODUCTION`.

### Requirement 15: Rollback

**User Story:** As a risk-officer, I want to instantly roll back to a previous production model, so that a bad production model can be reverted without retraining.

#### Acceptance Criteria

1. THE Registry_API SHALL expose an endpoint that requests a Rollback, designating a previously-PRODUCTION, currently-`DEPRECATED` Model_Version to be re-promoted to `PRODUCTION`.
2. WHEN a Rollback is requested, THE Model_Registry SHALL require Four_Eyes approval per Requirement 14 before applying it.
3. WHEN a Rollback is applied, THE Model_Registry SHALL set the designated Model_Version's Lifecycle_State to `PRODUCTION` and SHALL demote the incumbent Production_Model to `DEPRECATED`.
4. IF a Rollback designates a Model_Version that was never in `PRODUCTION`, THEN THE Model_Registry SHALL reject the Rollback and record the reason.
5. WHEN a Rollback is applied, THE Model_Registry SHALL append it to the Promotion_History and record a corresponding Audit_Event identifying it as a rollback.

### Requirement 16: Single Production Model Invariant

**User Story:** As a platform architect, I want at most one production model per model family at any time, so that downstream services always resolve an unambiguous champion.

#### Acceptance Criteria

1. FOR ALL Registered_Models, THE Model_Registry SHALL ensure that at most one Model_Version is in the `PRODUCTION` Lifecycle_State at any time.
2. WHEN a Model_Version is promoted to `PRODUCTION` and another Model_Version of the same Registered_Model is already in `PRODUCTION`, THE Model_Registry SHALL transition the incumbent to `DEPRECATED` as part of applying the promotion.
3. THE Registry_API SHALL expose an endpoint that resolves the current Production_Model for a Registered_Model.
4. IF no Model_Version of a Registered_Model is in `PRODUCTION`, THEN the production-resolution endpoint SHALL respond that no Production_Model exists for that Registered_Model.

### Requirement 17: Promotion History

**User Story:** As an auditor, I want the complete promotion history of every model version, so that I can reconstruct exactly how a model reached its current state.

#### Acceptance Criteria

1. WHEN any Lifecycle_State transition or Rollback is applied to a Model_Version, THE Model_Registry SHALL append an entry to that Model_Version's Promotion_History recording the from-state, to-state, requester, approvers, justification, and timestamp.
2. THE Model_Registry SHALL keep the Promotion_History append-only and SHALL NOT modify or delete any recorded entry.
3. THE Registry_API SHALL expose an endpoint that returns the ordered Promotion_History of a Model_Version.

### Requirement 18: Audit Trail

**User Story:** As a compliance officer, I want every privileged registry action recorded in an append-only, tamper-evident audit history, so that all governance decisions are traceable and inviolable.

#### Acceptance Criteria

1. WHEN a registration, transition request, approval, rejection, rollback, or retrieval of a `PRODUCTION` Model_Artifact occurs, THE Model_Registry SHALL record an Audit_Event capturing the actor, the timestamp, the affected Model_Version, the before and after state, the justification where applicable, and a correlation identifier.
2. THE Model_Registry SHALL keep the Audit_History append-only and SHALL NOT provide any operation that modifies or deletes a recorded Audit_Event.
3. THE Registry_API SHALL expose an endpoint that returns Audit_History filtered by Model_Version or correlation identifier.
4. WHERE a platform Audit Ledger service is configured, THE Model_Registry SHALL forward each Audit_Event to it using guaranteed-delivery semantics that do not lose an Audit_Event on a transient failure.

### Requirement 19: Registry REST API

**User Story:** As a quant researcher, I want a REST API to register, govern, and retrieve models and their metadata, so that I can integrate the registry into research and execution workflows without direct database or filesystem access.

#### Acceptance Criteria

1. THE Registry_API SHALL expose an endpoint that registers a new Model_Version from a Training Pipeline reference.
2. THE Registry_API SHALL expose an endpoint that lists Model_Versions, optionally filtered by model name or Lifecycle_State.
3. THE Registry_API SHALL expose an endpoint that retrieves one Model_Version's full metadata, Lineage, and Reproducibility_Metadata by its model name and version.
4. THE Registry_API SHALL expose an endpoint that retrieves one Model_Version's Metrics_Record by its model name and version.
5. THE Registry_API SHALL expose an endpoint that downloads one Model_Version's Model_Artifact by its model name and version.
6. THE Registry_API SHALL expose endpoints that request a promotion, approve or reject a pending promotion, and request a rollback.
7. THE Registry_API SHALL expose an endpoint that resolves the current Production_Model for a Registered_Model.
8. THE Registry_API SHALL expose endpoints that return the Promotion_History and the Audit_History of a Model_Version.
9. THE Registry_API SHALL expose OpenAPI documentation describing every exposed endpoint.
10. IF a request identifies a Registered_Model, Model_Version, Model_Artifact, Promotion_Request, Metrics_Record, or Lineage that does not exist, THEN THE Registry_API SHALL respond with a 404 response and a typed error body identifying the missing resource.
11. THE Registry_API SHALL require every mutating endpoint to accept a client-supplied idempotency key such that a retried request never produces a duplicate registration, transition, or approval.

### Requirement 20: Failure Handling

**User Story:** As a risk-conscious platform owner, I want the registry to fail closed on governance and integrity, so that ambiguity never advances a model toward capital.

#### Acceptance Criteria

1. IF the Model_Registry cannot verify an artifact's integrity, complete a mandatory-metadata write, or reach a required approval or audit dependency, THEN THE Model_Registry SHALL refuse the affected promotion or registration rather than proceed.
2. WHILE a required approval or audit dependency is unavailable, THE Model_Registry SHALL block promotions into `PRODUCTION` and SHALL continue to serve already-recorded Model_Versions and the existing Production_Model resolution.
3. WHEN a registration fails partway, THE Model_Registry SHALL leave no partial or unverified Model_Version persisted.
4. WHEN a governed transition fails partway, THE Model_Registry SHALL leave the Model_Version's Lifecycle_State and Approval_State unchanged from their pre-transition values.
5. THE Model_Registry SHALL record a human-readable reason for every rejected registration, transition, or approval.

### Requirement 21: Security and Access Control

**User Story:** As a platform owner, I want registry actions authenticated, authorized, and free of self-promotion, so that models are governed by the right humans and can never be promoted by the system itself.

#### Acceptance Criteria

1. THE Model_Registry SHALL authorize every mutating action against the acting principal's role, permitting promotion requests, approvals, and rollbacks only to principals holding the required roles.
2. THE Model_Registry SHALL NOT permit any automated or non-human principal to raise a Model_Version's Lifecycle_State to `PRODUCTION` or to satisfy an approval required for a `PRODUCTION` transition.
3. WHERE artifact signing is configured, THE Model_Registry SHALL verify a Model_Artifact's signature before serving it and SHALL refuse to serve an unsigned or invalidly-signed artifact.
4. THE Model_Registry SHALL retrieve any credential it requires at runtime from the platform secrets mechanism and SHALL NOT read a credential from source code, an image layer, or a version-controlled file.
5. THE Model_Registry SHALL record every privileged action to the Audit_History per Requirement 18.

### Requirement 22: Repository and Persistence Requirements

**User Story:** As a platform maintainer, I want the registry to own its database with immutable and append-only guarantees, so that the source of truth cannot be silently corrupted.

#### Acceptance Criteria

1. THE Model_Registry SHALL own its own database and SHALL NOT share a database with any other backend service.
2. THE Model_Registry SHALL enforce, at the database level, that the combination of model name and version is unique.
3. THE Model_Registry SHALL persist Model_Version identity and metadata fields such that they are written once and never updated.
4. THE Model_Registry SHALL persist Promotion_History and Audit_History as append-only records with no update or delete path.
5. THE Model_Registry SHALL provide a repository operation that resolves the highest recorded version for a Registered_Model and one that resolves the current Production_Model for a Registered_Model.

### Requirement 23: Non-Functional — Availability, Consistency, and Performance

**User Story:** As a platform operator, I want the registry to remain available for reads and strongly consistent for governance, so that downstream services can always resolve a trustworthy champion.

#### Acceptance Criteria

1. WHILE a promotion dependency is degraded, THE Model_Registry SHALL continue to serve reads of existing Model_Versions, metrics, lineage, artifacts, and the current Production_Model resolution.
2. THE Model_Registry SHALL apply governed transitions and approvals with strong consistency such that a Model_Version's Lifecycle_State and the Single Production Model Invariant are never observed in a contradictory state.
3. WHEN two promotion or rollback requests would concurrently place two Model_Versions of the same Registered_Model into `PRODUCTION`, THE Model_Registry SHALL serialize them so that the Single Production Model Invariant of Requirement 16 always holds.

### Requirement 24: Non-Functional — Observability and Maintainability

**User Story:** As a platform maintainer, I want the registry to be observable and consistently structured, so that its behavior can be diagnosed in production and maintained like every other service.

#### Acceptance Criteria

1. THE Model_Registry SHALL expose `/health`, `/health/live`, and `/health/ready` endpoints consistent with the existing backend services, WHERE the readiness check additionally verifies connectivity to its own database and to its Artifact_Store.
2. THE Model_Registry SHALL emit structured logs carrying a correlation identifier for every request and governed action.
3. THE Model_Registry SHALL follow the platform's domain/adapters/api layering so that governance logic is pure and independent of transport and persistence.

### Requirement 25: Docker Deployment

**User Story:** As a platform operator, I want the registry deployable via the same Docker conventions as the existing services, so that it fits the existing local and future cloud workflows without special-casing.

#### Acceptance Criteria

1. THE Model_Registry SHALL build into a container image using the shared parameterized `docker/Dockerfile.service` pattern used by market-data, feature-store, dataset-builder, and training-pipeline.
2. THE Model_Registry SHALL register a `model-registry` service entry in the root `docker-compose.yml` that exposes port `8004` and depends on its own dedicated Postgres database service.
3. THE Model_Registry SHALL register a `model-registry-db` Postgres service entry in the root `docker-compose.yml`, exposing port `5436`, that is not shared with any other backend service.
4. THE Model_Registry SHALL persist Model_Artifacts to a local artifact directory behind the Artifact_Store interface in the MVP, mounted as a dedicated volume in the same manner as the Dataset Builder and Training Pipeline services, WHERE an object-store-backed Artifact_Store implementation MAY be configured later without any change to domain or API logic.
5. THE Model_Registry SHALL read all configuration from `AQROS_*` environment variables, consistent with the existing services.

### Requirement 26: Testing Requirements

**User Story:** As a platform maintainer, I want comprehensive automated tests for the registry, so that its governance logic, integrity guarantees, and integrations are verified without manual testing.

#### Acceptance Criteria

1. THE Model_Registry SHALL include unit tests that exercise its governance and integrity logic against fakes for every port, including the Training_Pipeline_Client port, the Artifact_Store port, and the repository ports.
2. THE Model_Registry SHALL include property-based tests for its core invariants, including monotonic and unique versioning, legal-transition-only lifecycle enforcement, the Four_Eyes control, the Single Production Model Invariant, metadata and artifact immutability, and the checksum-integrity gate.
3. THE Model_Registry SHALL include integration tests that exercise its REST API through an HTTP client against a real Postgres database provisioned via testcontainers and a real Artifact_Store, with the Training Pipeline integration exercised against a faked Training_Pipeline_Client without requiring a live Training Pipeline instance.
4. THE Model_Registry SHALL include a reproducibility test that registers a Model_Version and confirms its metadata, lineage, and artifact round-trip unchanged.
5. WHEN the Model_Registry's automated test suite is executed, THE Model_Registry SHALL report a non-zero exit status IF any test fails or IF a test setup, missing test file, or test configuration error prevents the suite from running to completion.

### Requirement 27: Quality Gates

**User Story:** As a platform maintainer, I want the registry held to the same linting, formatting, and type-checking standards as the rest of the monorepo, so that the codebase stays consistent and maintainable.

#### Acceptance Criteria

1. THE Model_Registry SHALL pass Ruff linting using the rule set configured in the root `pyproject.toml`.
2. THE Model_Registry SHALL pass Black formatting checks using the configuration in the root `pyproject.toml`.
3. THE Model_Registry SHALL pass MyPy strict type checking using the configuration in the root `pyproject.toml`, with every function and public interface fully type-hinted.

### Requirement 28: Explicit Non-Goals

**User Story:** As a platform architect, I want the registry's responsibilities explicitly bounded, so that it never duplicates the Training Pipeline's or downstream services' responsibilities.

#### Acceptance Criteria

1. THE Model_Registry SHALL NOT train, retrain, or fine-tune any model.
2. THE Model_Registry SHALL NOT generate, build, or persist a dataset, nor compute features.
3. THE Model_Registry SHALL NOT serve model inference or produce predictions.
4. THE Model_Registry SHALL NOT compute or re-score model evaluation metrics; it records the metrics reported by the Training Pipeline and the Validation_Evidence supplied to it.
5. THE Model_Registry SHALL NOT make any trading, sizing, or risk decision.
6. THE Model_Registry SHALL NOT autonomously decide a promotion; every advancement into `STAGING` or `PRODUCTION` requires an explicit request and, where mandated, human approval.
7. THE Model_Registry SHALL NOT be bypassed: it SHALL provide every model read that Downstream_Consumers require so that no Downstream_Consumer queries the Training Pipeline directly.