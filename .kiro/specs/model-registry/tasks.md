# Implementation Plan: Model Registry

This plan implements `backend/model-registry` (module `aqros_model_registry`) exactly as specified in `requirements.md` (approved, final) and `design.md` (approved, final). The Model Registry is the single source of truth for all trained models: it ingests only from the Training Pipeline's published REST API, records immutable fully-lineaged model versions, governs them through the `REGISTERED → VALIDATED → STAGING → PRODUCTION → DEPRECATED → ARCHIVED` lifecycle behind a four-eyes gate (plus rollback), and serves every downstream consumer so the Training Pipeline is never queried directly. Tasks are ordered domain → adapters → migrations → API → app wiring → docker → tests → quality gates, mirroring how `backend/training-pipeline` was built. Artifact storage uses the local `ArtifactStore` adapter in the MVP (swappable for an object store later — no object-store dependency is introduced).

## 1. Project Scaffolding

- [x] 1.1 Create `backend/model-registry/pyproject.toml` declaring package `aqros-model-registry`, module `aqros_model_registry`, dependencies (fastapi, uvicorn, sqlalchemy[asyncio], asyncpg, alembic, httpx, pydantic, pydantic-settings, structlog, `aqros-core` workspace source) and a dev dependency group adding `hypothesis` alongside the shared pytest/pytest-asyncio/testcontainers[postgres]/psycopg[binary] tooling, matching `backend/training-pipeline/pyproject.toml`'s structure (no ML libraries — artifacts are handled as opaque bytes).
  - _Requirements: 25.1, 25.5, 27_
- [x] 1.2 Create the package skeleton: `src/aqros_model_registry/__init__.py`, `src/aqros_model_registry/py.typed`, and empty `__init__.py` files for `domain/`, `adapters/`, `api/`, `api/routes/`, per design.md Section 5's file layout.
  - _Requirements: 24.3_
- [x] 1.3 Create `backend/model-registry/README.md` documenting the service's purpose (single source of truth for trained models), its single upstream dependency (Training Pipeline REST API only), the fact that downstream services never query the Training Pipeline, local run instructions, and the port/DB assignments (8004 / 5436) with local artifact storage.
  - _Requirements: 1.1, 1.4, 25.2, 25.3, 28.7_
- [x] 1.4 Create `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py` scaffolding matching design.md Section 5's test layout.
  - _Requirements: 26.1, 26.3_

## 2. Domain Layer

- [x] 2.1 Implement `domain/models.py`: `LifecycleState`, `ApprovalState`, `PrincipalKind` StrEnums; `PerFoldMetrics`, `AggregatedMetrics`, `MetricsRecord`, `DatasetVersionRef`, `ReproducibilityMetadata`, `ValidationEvidence`, `TrainedModelRecord` (local decoupled copy of the Training Pipeline payload — never import `aqros_training_pipeline`), `ModelVersion`, `Approval`, `PromotionRequest`, `PromotionHistoryEntry`, `AuditEvent` — all frozen/slots dataclasses, exactly as specified in design.md Section 4.
  - _Requirements: 3.1, 3.4, 4.1, 5.1, 6.1, 9.1, 10.1, 11.1, 12.3, 17.1, 18.1_
- [x] 2.2 Implement `domain/ports.py`: `TrainingPipelineClient` ABC (`get_trained_model_record`, `download_artifact`) with `TrainedModelNotFoundError` and `UpstreamSourceError`; `ArtifactStore` ABC (`write_artifact`, `read_artifact`) with `ArtifactAlreadyExistsError`; `ModelVersionRepository`, `PromotionRepository`, `AuditRepository` ABCs; `ArtifactSigner` ABC; `Clock` — per design.md Sections 3, 5.1, 8.
  - _Requirements: 1.2, 1.5, 7, 8, 17, 18, 21.3, 22_
- [x] 2.3 Implement `domain/lifecycle.py` (Lifecycle_State_Machine): a pure `transition_allowed(from_state, to_state, is_rollback) -> bool` encoding the forward chain `REGISTERED → VALIDATED → STAGING → PRODUCTION → DEPRECATED → ARCHIVED`, the rollback edge `DEPRECATED → PRODUCTION`, and abandonment from any non-`PRODUCTION` state to `ARCHIVED`; treat `ARCHIVED` as terminal and forbid `PRODUCTION → ARCHIVED` without passing through `DEPRECATED`; raise `IllegalTransitionError` for every other transition.
  - _Requirements: 11.2, 11.3, 11.4, 11.5, 11.6, 15.1_
- [x] 2.4 Implement `domain/integrity.py` (Integrity_Verifier): `compute_checksum(data, algorithm)` and `verify_checksum(data, expected, algorithm) -> bool` using the algorithm named in the `Trained_Model_Record`; define `ChecksumMismatchError` (ingestion) and `ArtifactIntegrityError` (retrieval).
  - _Requirements: 7.1, 7.2, 7.4, 7.5_
- [x] 2.5 Implement `domain/approval.py` (Approval_Policy + Four_Eyes): the per-transition gate table from design.md Key Design Decision 6 (`VALIDATED` = evidence; `STAGING`/abandonment/`DEPRECATED→ARCHIVED` = one authorized approval; `PRODUCTION` and rollback = Four_Eyes); a `four_eyes_satisfied(request, approvals)` evaluator requiring two distinct approvers, each different from the requester, counting each approver at most once, and rejecting any approval or `PRODUCTION` promotion attributed to a non-human principal.
  - _Requirements: 12.1, 13.2, 13.3, 14.1, 14.2, 14.6, 14.7, 21.2_
- [x] 2.6 Implement `domain/lineage.py` (Lineage_Assembler): assemble `ReproducibilityMetadata` and the Lineage view from a `TrainedModelRecord`, and a `mandatory_metadata_complete(record) -> Result` check that rejects a record missing any mandatory field (dataset name/version/checksum, feature versions, metrics, training run id, artifact checksum) while tolerating an absent git commit (recorded as explicitly absent).
  - _Requirements: 4.1, 4.3, 5.1, 5.3, 6.1, 6.2, 6.3, 6.4, 9.1, 10.1_
- [x] 2.7 Implement `domain/services.py` `ModelRegistryService.register`: idempotent on `(model_name, version, training_run_id)`; pull the `Trained_Model_Record` (halt with not-found on 404, fail with upstream error otherwise, never persisting a partial version), validate mandatory metadata, download the artifact, verify its checksum (reject on mismatch without persisting), persist an independent immutable artifact copy via the `Artifact_Store`, persist the `Model_Version` in `REGISTERED`/`NOT_REQUIRED`, and append a `registered` `Audit_Event` in the same transaction.
  - _Requirements: 1.5, 1.6, 2.1, 2.2, 2.3, 2.4, 2.5, 6.2, 6.4, 7.1, 7.2, 7.3, 8.1, 18.1, 20.3_
- [x] 2.8 In `domain/services.py`, implement `request_transition`: assert `transition_allowed`, resolve the `Approval_Policy` gate, apply immediately when the gate is "none" (legal per Requirement 11), else create a `PENDING` `Promotion_Request` and set `Approval_State=PENDING`; require and immutably attach `Validation_Evidence` for `REGISTERED→VALIDATED`; append a `Promotion_History` entry and `Audit_Event` on every applied transition; never auto-promote to `PRODUCTION`.
  - _Requirements: 11.3, 12.1, 12.2, 12.3, 13.1, 13.2, 13.3, 13.4, 13.5, 17.1, 18.1, 20.4, 20.5_
- [x] 2.9 In `domain/services.py`, implement `approve` / `reject`: enforce Four_Eyes via `domain/approval.py` (distinct authorized human approvers, none the requester, no double-count, automated principals rejected); on rejection set `REJECTED`, leave the Lifecycle_State unchanged, and record the reason; on satisfying the threshold apply the targeted transition, and for `PRODUCTION` demote any incumbent Production_Model to `DEPRECATED` within one serializable transaction so the single-PRODUCTION invariant holds; append `Promotion_History` + `Audit_Event`.
  - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 16.1, 16.2, 20.4, 20.5, 21.2, 23.2, 23.3_
- [x] 2.10 In `domain/services.py`, implement `rollback`: reject if the designated `Model_Version` was never in `PRODUCTION` (checked against Promotion_History); require Four_Eyes; on apply, set the target to `PRODUCTION` and demote the incumbent to `DEPRECATED` in one transaction; append a `Promotion_History` entry flagged as a rollback and a corresponding `Audit_Event`.
  - _Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 16.1, 16.2, 18.1_
- [x] 2.11 Implement `RegistryQueryService` in `domain/services.py`: `get_model_version`, `list_model_versions(model_name=None, lifecycle_state=None)`, `get_metrics`, `get_lineage`, `resolve_production(model_name)`, `get_promotion_history`, `get_audit_history(model_name=None, correlation_id=None)`, and `get_artifact` (retrieve bytes then verify against the recorded checksum, refusing on integrity failure) — thin read-only wrappers over the repository and Artifact_Store ports.
  - _Requirements: 4.2, 5.2, 7.4, 7.5, 9.2, 10.2, 16.3, 16.4, 17.3, 18.3, 19.2, 19.3, 19.4, 19.5, 19.7, 19.8_

## 3. Adapters Layer

- [x] 3.1 Implement `adapters/db.py`: `create_engine(settings)`, `create_session_factory(engine)`, async `session_scope`, `ping(engine) -> bool` — copy the exact pattern from `aqros_training_pipeline.adapters.db`.
  - _Requirements: 24.1_
- [x] 3.2 Implement `adapters/orm.py`: `RegisteredModelORM`, `ModelVersionORM`, `PromotionRequestORM`, `ApprovalORM`, `PromotionHistoryORM`, `AuditEventORM` SQLAlchemy 2.0 `DeclarativeBase` models exactly matching design.md Section 7, including `UniqueConstraint(registered_model_id, version)`, the partial unique index `uq_one_production_per_model` on `(registered_model_id) WHERE lifecycle_state='production'`, `UniqueConstraint(model_version_id, idempotency_key)` on promotion requests, and the foreign keys; identity columns are write-once and only `lifecycle_state`/`approval_state`/`validation_evidence_json` are mutable.
  - _Requirements: 3.4, 3.5, 16.1, 17.2, 18.2, 19.11, 22.2, 22.3, 22.4_
- [x] 3.3 Implement `adapters/repository.py`: `SqlAlchemyModelVersionRepository` (`create_model_version` via `add()`+`flush()` only — never update an identity field, `get`, `list`, `set_lifecycle_state` updating only the state column, `get_latest_version`, `resolve_production`, `attach_validation_evidence`), `SqlAlchemyPromotionRepository` (`create_request`, `add_approval`, `set_request_state`, `get_request`, `append_history`, `list_history`), and `SqlAlchemyAuditRepository` (`append`, `list`) — each translating ORM rows to/from the frozen domain dataclasses via private `_to_domain_*` helpers, taking an `AsyncSession` via constructor and never committing.
  - _Requirements: 3.2, 3.3, 16.3, 17.1, 17.3, 18.2, 18.3, 22.1, 22.5_
- [x] 3.4 Implement `adapters/training_pipeline_client.py` (`HttpTrainingPipelineClient`): wraps an injected `httpx.AsyncClient`; `get_trained_model_record(model_name, version)` composes the Training Pipeline's metadata + metrics endpoints into a local `TrainedModelRecord`, `download_artifact(model_name, version)` calls the artifact endpoint; raises `TrainedModelNotFoundError` on any 404 and `UpstreamSourceError` on any other error/connection failure; translates JSON to the local dataclasses via static `_to_domain_*` helpers, never importing `aqros_training_pipeline`.
  - _Requirements: 1.2, 1.3, 1.5, 2.1, 2.3_
- [x] 3.5 Implement `adapters/local_artifact_store.py` (`LocalArtifactStore`): path `{base_dir}/{model_name}/v{model_version}/model.joblib`; `write_artifact` checks-then-writes inside a single `asyncio.to_thread` call, raising `ArtifactAlreadyExistsError` instead of overwriting; `read_artifact` returns the exact persisted bytes — mirroring `aqros_training_pipeline.adapters.local_artifact_store` verbatim (swappable for an object store with no domain/API change).
  - _Requirements: 7.6, 8.1, 8.2, 8.4, 25.4_
- [x] 3.6 Implement `adapters/signer.py` (`CosignArtifactVerifier`): implements the `ArtifactSigner` port; verifies an artifact's signature before serving when signing is configured and refuses unsigned/invalid artifacts, and is a tolerant no-op when signing is not configured.
  - _Requirements: 21.3_

## 4. Database Migrations

- [x] 4.1 Create `backend/model-registry/alembic.ini` and `migrations/env.py`/`migrations/script.py.mako`, mirroring `backend/training-pipeline`'s Alembic setup (async engine config from `Settings`, target metadata pointed at `adapters.orm.Base.metadata`).
  - _Requirements: 22.1_
- [x] 4.2 Create `migrations/versions/0001_initial_schema.py`: `upgrade()` creates `registered_models`, `model_versions`, `promotion_requests`, `approvals`, `promotion_history`, and `audit_events` with every column from design.md Section 7, the `UniqueConstraint(registered_model_id, version)`, the partial unique index `uq_one_production_per_model`, the `UniqueConstraint(model_version_id, idempotency_key)`, the `ix_model_versions_model_name`/`ix_audit_events_correlation_id`/`ix_promotion_history_model_version_id` indexes, and all foreign keys; `downgrade()` drops them symmetrically — same style as `backend/training-pipeline/migrations/versions/0001_initial_schema.py`.
  - _Requirements: 16.1, 17.2, 18.2, 19.11, 22.2, 22.4_

## 5. API Layer

- [x] 5.1 Implement `api/schemas.py`: `RegisterModelRequest`, `ModelVersionResponse`, `MetricsResponse`, `LineageResponse`, `ReproducibilityMetadataResponse`, `TransitionRequestSchema`, `ApprovalRequestSchema`, `RollbackRequestSchema`, `PromotionHistoryResponse`, `AuditEventResponse`, `ProductionResolutionResponse`, and the shared `ErrorResponse(error, detail)` envelope — each response schema with a `from_domain(...)` classmethod converter, mirroring `aqros_training_pipeline.api.schemas`.
  - _Requirements: 19.1, 19.2, 19.3, 19.4, 19.7, 19.8, 19.10_
- [x] 5.2 Implement `api/deps.py`: FastAPI DI functions reading `training_pipeline_client`, `artifact_store`, `artifact_signer` off `request.app.state`, `get_session` for request-scoped sessions, repository constructors, and `get_model_registry_service`/`get_registry_query_service` composing the domain services from injected ports — mirroring `aqros_training_pipeline.api.deps`.
  - _Requirements: 19, 21.1_
- [x] 5.3 Implement `api/routes/models.py`: `POST /v1/models` (register from a Training Pipeline reference; requires an `Idempotency-Key`; maps `TrainedModelNotFoundError` to 404 and `UpstreamSourceError` to 502; maps missing-metadata and checksum-mismatch to 422 per design.md Section 13), `GET /v1/models` (optional `model_name`/`lifecycle_state` filters), `GET /v1/models/{model_name}/versions/{version}` (metadata + lineage + reproducibility, 404 if missing), `GET .../metrics`, and `GET .../lineage`.
  - _Requirements: 2.1, 4.2, 5.2, 6.2, 7.2, 9.2, 10.2, 19.1, 19.2, 19.3, 19.4, 19.10, 19.11, 20.5_
- [x] 5.4 Implement `api/routes/artifacts.py`: `GET /v1/models/{model_name}/versions/{version}/artifact` (streams the verified Model_Artifact bytes; recomputes and compares the checksum before serving, refusing on integrity failure; 404 if missing).
  - _Requirements: 7.4, 7.5, 8.2, 8.3, 19.5, 19.10_
- [x] 5.5 Implement `api/routes/transitions.py`: `POST .../transition`, `POST .../approve`, `POST .../reject`, and `POST .../rollback` — each RBAC-authorized, requiring an `Idempotency-Key`, mapping illegal transitions to 409, missing validation evidence to 422, unsatisfied four-eyes to 202/409, automated-principal attempts to 403, and never-production rollback to 409 per design.md Section 13.
  - _Requirements: 11.3, 12.1, 12.2, 13.1, 14.1, 14.5, 14.7, 15.1, 15.4, 19.6, 19.11, 20.4, 20.5, 21.1, 21.2_
- [x] 5.6 Implement `api/routes/history.py`: `GET /v1/models/{model_name}/production` (resolve the current Production_Model, or report none exists), `GET .../promotion-history`, and `GET .../audit` (filterable by model version or correlation identifier).
  - _Requirements: 16.3, 16.4, 17.3, 18.3, 19.7, 19.8, 19.10_

## 6. App Wiring

- [x] 6.1 Implement `config.py`: `Settings` extending `aqros_core.config.BaseServiceSettings` with `service_name="model-registry"`, `port=8004`, `database_url` defaulting to `postgresql+asyncpg://aqros:aqros@localhost:5436/aqros_model_registry`, pool-size settings, `training_pipeline_base_url: AnyHttpUrl` defaulting to `http://localhost:8009`, `upstream_request_timeout_seconds`, `artifact_dir: str` defaulting to `/data/model-registry/artifacts`, and optional artifact-signing configuration — all overridable via `AQROS_*` env vars, mirroring `aqros_training_pipeline.config`.
  - _Requirements: 25.5_
- [x] 6.2 Implement `app.py`: module-level `Settings()`, `create_engine`/`create_session_factory`, a `HealthRegistry` registering `database` (via `db.ping`) and `artifact_store` (reachable/writable check), a `_build_app()` wrapping `aqros_core.app.create_app`'s lifespan to attach the `training_pipeline_client` (httpx), `artifact_store`, and `artifact_signer` to `app.state` and close the httpx client/engine on shutdown, then include the `models`, `artifacts`, `transitions`, and `history` routers.
  - _Requirements: 24.1, 24.2_
- [x] 6.3 Implement `main.py`: trivial uvicorn entrypoint reading `Settings()` and running `aqros_model_registry.app:app` — copy `aqros_training_pipeline.main`'s pattern.
  - _Requirements: 25.1_

## 7. Docker & Compose Integration

- [x] 7.1 Update the root `docker-compose.yml`: add the `model-registry-db` Postgres service (port 5436, DB `aqros_model_registry`) and extend the existing reserved `model-registry` entry with `AQROS_DATABASE_URL`, `AQROS_TRAINING_PIPELINE_BASE_URL=http://training-pipeline:8009`, `AQROS_ARTIFACT_DIR`, a `model-registry-artifacts` volume, and `depends_on: model-registry-db (service_healthy), training-pipeline (service_started)`, plus the `model-registry-db-data` and `model-registry-artifacts` named volumes — using the exact block drafted in design.md Section 14.
  - _Requirements: 25.2, 25.3, 25.4_
- [x] 7.2 Add `"aqros_model_registry"` to the root `pyproject.toml`'s `[tool.ruff.lint.isort].known-first-party` list.
  - _Requirements: 27.1_
- [x] 7.3 Verify (no edit expected) that `backend/model-registry` is picked up by the existing `[tool.uv.workspace] members = ["libs/*", "backend/*"]` glob and that `docker/Dockerfile.service`'s parameterized `SERVICE`/`MODULE`/`PORT` build args require no changes to build this service.
  - _Requirements: 25.1_

## 8. Unit Tests (fakes for every port + property-based tests)

- [x] 8.1 Create fakes: `FakeTrainingPipelineClient`, `FakeArtifactStore`, `FakeModelVersionRepository`, `FakePromotionRepository`, `FakeAuditRepository` implementing every port from `domain/ports.py` in-memory, for use across all unit tests.
  - _Requirements: 26.1_
- [x] 8.2 `tests/unit/test_integrity.py`: property tests for **Property 7** (checksum gate on ingestion) and **Property 8** (checksum verify on retrieval and never-overwrite), plus concrete match/mismatch examples.
  - _Requirements: 7.1, 7.2, 7.4, 7.5, 7.6_
- [x] 8.3 `tests/unit/test_lifecycle.py`: property tests for **Property 11** (only legal transitions applied) and **Property 12** (ARCHIVED terminal; PRODUCTION cannot skip DEPRECATED), exercising the full transition table including the rollback and abandonment edges.
  - _Requirements: 11.2, 11.3, 11.4, 11.5, 11.6, 15.1_
- [x] 8.4 `tests/unit/test_approval.py`: property tests for **Property 13** (VALIDATED requires evidence), **Property 14** (four-eyes by two distinct humans, neither the requester, no double-count), **Property 15** (automated principals cannot satisfy a PRODUCTION gate), and **Property 16** (rejection blocks the transition).
  - _Requirements: 12.1, 12.2, 13.2, 13.3, 14.1, 14.2, 14.4, 14.5, 14.6, 14.7, 21.2_
- [x] 8.5 `tests/unit/test_versioning.py`: property test for **Property 5** (version uniqueness and identity-field immutability per Registered_Model).
  - _Requirements: 3.2, 3.3, 3.4, 3.5, 22.2, 22.3_
- [x] 8.6 `tests/unit/test_lineage.py`: property tests for **Property 6** (mandatory-metadata completeness gates persistence; absent git commit tolerated) and **Property 10** (lineage/reproducibility complete and immutable).
  - _Requirements: 4.1, 4.3, 5.1, 5.3, 6.1, 6.2, 6.3, 6.4, 9.1, 9.3, 9.4, 10.1, 10.3_
- [x] 8.7 `tests/unit/test_registry_service.py`: property tests for **Property 1** (ingestion via Training Pipeline REST only; downstream reads served by the Registry), **Property 2** (upstream failure yields no partial version), **Property 3** (idempotent registration), **Property 4** (new version starts REGISTERED/NOT_REQUIRED), **Property 17** (no automatic PRODUCTION promotion), **Property 18** (single-PRODUCTION invariant with incumbent demotion), **Property 20** (rollback only from a previously-PRODUCTION version), **Property 21** (promotion history append-only and complete), and **Property 22** (audit trail captures every privileged action, append-only).
  - _Requirements: 1.2, 1.4, 1.5, 1.6, 2.3, 2.4, 2.5, 13.5, 15.1, 15.3, 15.4, 15.5, 16.1, 16.2, 17.1, 17.2, 18.1, 18.2, 20.3_
- [x] 8.8 `tests/unit/test_local_artifact_store.py`: property tests for **Property 9** (write-then-read round trip) and the deterministic-path / never-overwrite behavior of `LocalArtifactStore`.
  - _Requirements: 7.6, 8.1, 8.2, 8.3_
- [x] 8.9 Configure every property test above with `hypothesis`, `@settings(max_examples=100)` minimum, and a `# Feature: model-registry, Property N: <property text>` comment tag directly above each test function, per design.md's Testing Strategy section.
  - _Requirements: 26.2_

## 9. Integration Tests

- [x] 9.1 Create `tests/integration/conftest.py`: `postgres_container` (testcontainers `PostgresContainer`), `engine`, `session_factory`, `db_session` fixtures, and a `client` fixture building the FastAPI app via `httpx.AsyncClient`+`ASGITransport` with `get_session`, `get_training_pipeline_client` (overridden with an in-memory fake, no live Training Pipeline required), `get_artifact_store` (real `LocalArtifactStore` on a tmp dir), and `get_artifact_signer` overridden — mirroring `aqros_training_pipeline`'s integration conftest, including manually populating `app.state` since `ASGITransport` never runs the lifespan.
  - _Requirements: 26.2, 26.3_
- [x] 9.2 Create `tests/integration/test_api.py`: end-to-end happy path (register → get metadata/lineage/metrics → download artifact); promote through VALIDATED → STAGING → PRODUCTION exercising four-eyes and the production-resolution endpoint; a rollback path; 404-path tests for every endpoint per **Property 23**; a degraded-dependency test per **Property 24** (reads succeed while a promotion dependency is unavailable); and a reproducibility round-trip test per **Property 25**.
  - _Requirements: 14.1, 16.3, 16.4, 19.1, 19.2, 19.3, 19.4, 19.5, 19.6, 19.7, 19.8, 19.10, 20.1, 20.2, 23.1, 26.3, 26.4_
- [x] 9.3 Create `tests/integration/test_repository.py`: exercises the repositories against the real Postgres container, including a test asserting the `UniqueConstraint(model_name, version)` rejects a duplicate version and a concurrency-oriented test asserting the partial unique index `uq_one_production_per_model` rejects a second concurrent PRODUCTION for the same Registered_Model (**Property 18**), plus append-only behavior of promotion history and audit events.
  - _Requirements: 16.1, 17.2, 18.2, 22.2, 22.4, 23.3, 26.2_
- [x] 9.4 Create `tests/integration/test_migrations.py`: runs the Alembic `0001_initial_schema` upgrade/downgrade (including the partial unique index) against the testcontainers Postgres, mirroring `aqros_training_pipeline`'s own `test_migrations.py`.
  - _Requirements: 26.2_
- [x] 9.5 Create `tests/test_health.py`: 1-2 concrete examples covering Requirement 24.1's readiness composition (database + artifact-store healthy → 200; one check failing → 503).
  - _Requirements: 24.1_

## 10. Quality Gates & Final Verification

- [x] 10.1 Run `ruff check backend/model-registry` and fix any findings.
  - _Requirements: 27.1_
- [x] 10.2 Run `black --check backend/model-registry` and fix any formatting issues.
  - _Requirements: 27.2_
- [x] 10.3 Run `mypy --strict` against `backend/model-registry/src` and resolve any type errors, ensuring every public interface is fully type-hinted.
  - _Requirements: 27.3_
- [x] 10.4 Run the full `pytest` suite for `backend/model-registry` (unit + integration) and confirm a non-zero exit code on any failure or setup error.
  - _Requirements: 26.5_
- [x] 10.5 Build the `model-registry` Docker image via `docker compose build model-registry` and verify `docker compose up` brings up `model-registry` and `model-registry-db` with `/health/ready` reporting healthy.
  - _Requirements: 25.1, 25.2, 25.3, 24.1_
- [x] 10.6 Traceability check: confirm every requirement (1.1 through 28.7) in `requirements.md` is satisfied by at least one completed task above, and that all 25 correctness properties in `design.md` are covered by a test task; confirm the Training Pipeline is never queried by any downstream path and no object-store dependency was introduced (local `ArtifactStore` adapter only).
  - _Requirements: all (1-28)_
