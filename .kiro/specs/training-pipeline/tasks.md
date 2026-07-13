# Implementation Plan: Training Pipeline

This plan implements `backend/training-pipeline` (module `aqros_training_pipeline`) exactly as specified in `requirements.md` (approved, final) and `design.md` (approved, final, including the two user-requested corrections: composite `{dataset_name}__{model_type}` model-name versioning, and all-or-nothing multi-Model_Type run status). Tasks are ordered domain -> adapters -> migrations -> API -> app wiring -> Docker -> tests -> quality gates, mirroring how `backend/dataset-builder` was built.

## 1. Project Scaffolding

- [x] 1.1 Create `backend/training-pipeline/pyproject.toml` declaring package `aqros-training-pipeline`, module `aqros_training_pipeline`, dependencies (fastapi, uvicorn, sqlalchemy[asyncio], asyncpg, alembic, httpx, pydantic, pydantic-settings, structlog, pandas, pyarrow, joblib, scikit-learn, xgboost, lightgbm, `aqros-core` workspace source) and a dev dependency group adding `hypothesis` alongside the shared pytest/ruff/black/mypy tools, matching `backend/dataset-builder/pyproject.toml`'s structure.
  - _Requirements: 17.1, 17.2, 17.3_
- [x] 1.2 Create the package skeleton: `src/aqros_training_pipeline/__init__.py`, `src/aqros_training_pipeline/py.typed`, and empty `__init__.py` files for `domain/`, `adapters/`, `api/`, `api/routes/`, per design.md Section 5.1's file layout.
  - _Requirements: 17.3_
- [x] 1.3 Create `backend/training-pipeline/README.md` documenting the service's purpose, its single upstream dependency (Dataset Builder REST API only), local run instructions, and the port/DB assignments (8009 / 5435).
  - _Requirements: 1.1, 1.2, 1.3_
- [x] 1.4 Create `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py` scaffolding matching design.md Section 5.1's test layout.
  - _Requirements: 16.1, 16.2, 16.3_

## 2. Domain Layer

- [x] 2.1 Implement `domain/models.py`: `ModelType`, `SplitRole`, `TrainingRunStatus` StrEnums; `DatasetManifest`, `DatasetBuildRun` local decoupled dataclasses; `TrainingRequest`, `ConfusionMatrix`, `PerFoldMetrics`, `AggregatedMetrics`, `ReproducibilityMetadata`, `TrainedModel` (with `model_name` documented and constructed as the composite `f"{dataset_name}__{model_type}"` string per Key Design Decision 3 — never the bare `ModelType` value), `ModelTypeOutcome`, `TrainingRun` — all frozen/slots dataclasses, exactly as specified in design.md Section 4.
  - _Requirements: 7.1, 8.1, 8.2, 8.3, 8.4, 9.1, 9.2, 9.3, 12.1_
- [x] 2.2 Implement `domain/ports.py`: `DatasetBuilderClient` ABC (`get_build_run`, `get_manifest`, `download_dataset`), `DatasetBuildRunNotFoundError` and `UpstreamSourceError` exceptions, `ArtifactStore` ABC (`write_artifact`, `read_artifact`) and `ArtifactAlreadyExistsError`, `TrainingRunRepository` ABC (`create_run`, `complete_run`, `get_run`, `list_runs`), `TrainedModelRepository` ABC (`create_trained_model`, `get_trained_model`, `list_trained_models`, `get_latest_version`), `GitInfoProvider` ABC (`get_commit_sha`) — per design.md Sections 3, 8, 5.2.
  - _Requirements: 1.1, 1.2, 1.4, 2.1, 2.2, 8.1, 8.2, 12.3, 13.1, 13.2, 13.3, 13.4_
- [x] 2.3 Implement `domain/verification.py` (Pre_Training_Verifier): `verify_checksum(manifest, downloaded_bytes) -> bool` using the algorithm named in `manifest.checksum_algorithm`; `verify_leakage(build_run) -> VerificationResult` checking `leakage_audit_passed is True`; a combined `verify(manifest, downloaded_bytes, build_run)` that AND-gates both checks and only permits proceeding if both pass, recording the leakage findings as the rejection reason when leakage fails.
  - _Requirements: 3.1, 3.2, 3.3, 4.1, 4.2, 4.3, 4.4, 18.4_
- [x] 2.4 Implement `domain/partitioning.py` (Fold_Partitioner): `partition(dataframe) -> dict[int, FoldFrames]` grouping strictly by the existing `fold` column and splitting each group by the existing `split_role` column into `train`/`test` slices, with no sort/shuffle/`sample()` call anywhere; raise `NoEvaluableFoldsError` if every fold's `test`-role slice is empty.
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 6.4_
- [x] 2.5 Implement `domain/trainers.py` (Model_Trainer): a dispatch function mapping each `ModelType` to its scikit-learn-compatible estimator class (`LogisticRegression`, `RandomForestClassifier`, `XGBClassifier`, `LGBMClassifier`), the hyperparameter-defaults table from design.md Key Design Decision 5, a merge function applying caller-supplied hyperparameters over the defaults, and `fit_per_fold(model_type, folds, hyperparameters)` fitting one estimator per fold using only that fold's `train`-role rows, columns ordered exactly as `DatasetManifest.feature_names`.
  - _Requirements: 5.5, 7.2, 7.3, 7.4, 7.5_
- [x] 2.6 Implement `domain/evaluation.py` (Evaluation_Engine): `evaluate_fold(y_true, y_pred, y_proba) -> PerFoldMetrics` computing accuracy/precision/recall/F1/confusion-matrix always, and `roc_auc` unless the fold's test rows are single-class (in which case `roc_auc=None`); `aggregate(per_fold_metrics) -> AggregatedMetrics` computing mean/std via `statistics.fmean`/`statistics.pstdev` for accuracy/precision/recall/F1 across all folds, and for roc_auc across only the folds whose `roc_auc is not None`.
  - _Requirements: 6.1, 6.2, 6.3, 9.1, 9.2, 9.3_
- [x] 2.7 Implement `domain/feature_importance.py` (Feature_Importance_Extractor): `extract(fitted_model, model_type, feature_names) -> dict[str, float]` using `coef_[0]` for `logistic_regression` and `feature_importances_` for the three tree-ensemble types, `zip(feature_names, importance_array)` so the result always has exactly one entry per manifest `feature_name`.
  - _Requirements: 10.1, 10.2, 10.3_
- [x] 2.8 Implement `domain/versioning.py` (Model_Versioner): a `build_model_name(dataset_name, model_type) -> str` helper producing the composite `f"{dataset_name}__{model_type}"` string (Key Design Decision 3), plus `assign_version(model_name, repository) -> int` calling `repository.get_latest_version(model_name)` and returning `1` if `None`, else `existing_max + 1`; document explicitly that two different `dataset_name`s produce two fully independent version sequences even for the same `ModelType`.
  - _Requirements: 8.1, 8.2, 8.3, 8.4_
- [x] 2.9 Implement `domain/reports.py` (Report_Generator): `build_training_report(run) -> TrainingReport` summarizing the request parameters and every resulting `Trained_Model` id (from `run.outcomes`); `build_metrics_report(trained_model) -> MetricsReport` containing that model's `per_fold_metrics`, `aggregated_metrics`, and `feature_importance` unmodified.
  - _Requirements: 11.1, 11.2, 11.3_
- [x] 2.10 Implement `domain/services.py` (`TrainingPipelineService`, `TrainingQueryService`): `TrainingPipelineService.create_training_run(request)` orchestrating the full pipeline in one `try/except`-wrapped method — retrieve build run then manifest (halting immediately on a 404 from either, Requirement 2.3), download the artifact (zero-retry fail-fast per Key Design Decision 6), run `Pre_Training_Verifier.verify`, partition folds, and for each requested `Model_Type` run fit -> evaluate -> extract feature importance -> assign version (via the composite model name from task 2.8) -> write artifact -> record reproducibility metadata -> persist `Trained_Model`, collecting one `ModelTypeOutcome` per type regardless of success/failure.
  - _Requirements: 1.4, 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 4.1, 4.2, 4.3, 4.4, 6.4, 12.1, 12.2, 12.3, 12.4_
- [x] 2.11 In `domain/services.py`, implement the all-or-nothing multi-Model_Type run-status rule (Key Design Decision 8, revised): after every requested `Model_Type` has been attempted, mark the `Training_Run` `succeeded` if and only if every `ModelTypeOutcome` succeeded; mark it `failed` if any `ModelTypeOutcome` failed (including the "all failed" case) or if pre-training verification/zero-evaluable-folds rejected the run earlier — and explicitly do NOT roll back, delete, or unpersist any `Trained_Model` row/artifact/version already recorded for a `ModelTypeOutcome` that succeeded before a sibling failed.
  - _Requirements: (Design Decision 8, revised — no single numbered requirement governs multi-Model_Type partial failure; this task implements the platform's documented behavior for that gap)_
- [x] 2.12 Add `TrainingQueryService` to `domain/services.py`: `get_run`, `list_runs`, `get_trained_model(model_name, version)`, `list_trained_models(model_name=None)`, `get_metrics_report(model_name, version)` — thin read-only wrappers over the two repository ports.
  - _Requirements: 14.2, 14.3, 14.4, 14.5_

## 3. Adapters Layer

- [x] 3.1 Implement `adapters/db.py`: `create_engine(settings)`, `create_session_factory(engine)`, async `session_scope`, `ping(engine) -> bool` — copy the exact pattern from `aqros_dataset_builder.adapters.db`.
  - _Requirements: 15.4_
- [x] 3.2 Implement `adapters/orm.py`: `TrainingRunORM` and `TrainedModelORM` SQLAlchemy 2.0 `DeclarativeBase` models exactly matching design.md Section 7's column definitions, including the `UniqueConstraint("model_name", "model_version")` on `TrainedModelORM` and the `ForeignKey("training_runs.id")` on `training_run_id`.
  - _Requirements: 8.4, 12.1_
- [x] 3.3 Implement `adapters/repository.py`: `SqlAlchemyTrainingRunRepository` (`create_run` via `session.add()`+`flush()`, `complete_run` via row update, `get_run`, `list_runs`) and `SqlAlchemyTrainedModelRepository` (`create_trained_model` via `add()`+`flush()` only — never `update` an existing row, `get_trained_model(model_name, version)`, `list_trained_models(model_name=None)`, `get_latest_version(model_name)` running `SELECT MAX(model_version) WHERE model_name = :name`), each translating ORM rows to/from the frozen domain dataclasses via private `_to_domain_*` helpers.
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 11.1, 11.2, 12.1, 12.2, 12.4, 14.2, 14.3_
- [x] 3.4 Implement `adapters/dataset_builder_client.py` (`HttpDatasetBuilderClient`): wraps an injected `httpx.AsyncClient`; `get_build_run(run_id)` calls `GET /v1/runs/{run_id}`, `get_manifest(run_id)` calls `GET /v1/runs/{run_id}/manifest`, `download_dataset(run_id)` calls `GET /v1/runs/{run_id}/download`; raises `DatasetBuildRunNotFoundError` on any 404 and `UpstreamSourceError` on any other error/connection failure; performs **zero retries** of any kind on any of the three calls (Key Design Decision 6 — deliberately does NOT reuse the retry-with-backoff loop from `aqros_dataset_builder.adapters.market_data_client`); translates JSON responses to the local `DatasetManifest`/`DatasetBuildRun` dataclasses via static `_to_domain_*` helpers, never importing `aqros_dataset_builder`'s own types.
  - _Requirements: 1.1, 1.2, 1.4, 2.1, 2.2, 2.3_
- [x] 3.5 Implement `adapters/local_artifact_store.py` (`LocalArtifactStore`): path construction `{base_dir}/{model_name}/v{model_version}/model.joblib` (where `model_name` is already the composite `{dataset_name}__{model_type}` string produced by task 2.8); `write_artifact` checks-then-writes inside a single `asyncio.to_thread` call, raising `ArtifactAlreadyExistsError` instead of overwriting; `read_artifact` returns the exact persisted bytes; serialize/deserialize with `joblib.dump`/`joblib.load` uniformly for all four `Model_Types` (Key Design Decision 1).
  - _Requirements: 13.1, 13.2, 13.3, 13.4_
- [x] 3.6 Implement `adapters/git_info.py` (`SubprocessGitInfoProvider`): copy `aqros_dataset_builder.adapters.git_info`'s pattern verbatim — shell out to `git rev-parse HEAD` via `asyncio.create_subprocess_exec`, return `None` (never raise) on any failure.
  - _Requirements: 12.3_

## 4. Database Migrations

- [x] 4.1 Create `backend/training-pipeline/alembic.ini` and `migrations/env.py`/`migrations/script.py.mako`, mirroring `backend/dataset-builder`'s Alembic setup (async engine config, target metadata pointed at `adapters.orm.Base.metadata`).
  - _Requirements: 17.3_
- [x] 4.2 Create `migrations/versions/0001_initial_schema.py`: `upgrade()` creates `training_runs` and `trained_models` tables with every column from design.md Section 7's ORM definitions, the `uq_trained_models_name_version` unique constraint, `ix_training_runs_dataset_name` and `ix_trained_models_model_name` indexes, and the `training_run_id` foreign key; `downgrade()` drops them symmetrically — same style as `backend/dataset-builder/migrations/versions/0001_initial_schema.py`.
  - _Requirements: 8.4, 12.1_

## 5. API Layer

- [x] 5.1 Implement `api/schemas.py`: `TrainingRequestSchema` (validating `model_types` against `{logistic_regression, random_forest, xgboost, lightgbm}` at the Pydantic layer, producing a 422 naming the offending value for Requirement 7.6), `TrainingRunResponse`, `TrainedModelResponse`, `ReproducibilityMetadataResponse`, `MetricsReportResponse`, `PerFoldMetricsResponse`, `AggregatedMetricsResponse`, and the shared `ErrorResponse(error, detail)` envelope — each response schema with a `from_domain(...)` classmethod converter, mirroring `aqros_dataset_builder.api.schemas`.
  - _Requirements: 7.1, 7.6, 11.3, 14.7, 14.8_
- [x] 5.2 Implement `api/deps.py`: FastAPI DI functions reading `dataset_builder_client`, `artifact_store`, `git_info_provider` off `request.app.state`, `get_session` for request-scoped sessions, repository constructors, and `get_training_pipeline_service`/`get_training_query_service` composing the domain services from injected ports — mirroring `aqros_dataset_builder.api.deps`.
  - _Requirements: 1.1, 1.2, 13.4_
- [x] 5.3 Implement `api/routes/training_runs.py`: `POST /v1/training-runs` (creates and synchronously executes a `Training_Run`, Key Design Decision 7; maps `DatasetBuildRunNotFoundError` to 404, other creation-time failures to the appropriate status per design.md Section 14's Error Handling table) and `GET /v1/training-runs/{run_id}` (returns status + `Training_Report`, 404 if missing).
  - _Requirements: 14.1, 14.2, 14.8_
- [x] 5.4 Implement `api/routes/trained_models.py`: `GET /v1/trained-models` (optional `model_name` filter), `GET /v1/trained-models/{model_name}/versions/{version}/metadata` (Reproducibility_Metadata, 404 if missing), `GET /v1/trained-models/{model_name}/versions/{version}/metrics` (Metrics_Report, 404 if missing), `GET /v1/trained-models/{model_name}/versions/{version}/artifact` (streams the Model_Artifact bytes via a `Response`/`FileResponse`-style download, 404 if missing) — note `{model_name}` in these paths is the full composite `{dataset_name}__{model_type}` string per Key Design Decision 3.
  - _Requirements: 13.3, 14.3, 14.4, 14.5, 14.6, 14.8_

## 6. App Wiring

- [x] 6.1 Implement `config.py`: `Settings` extending `aqros_core.config.BaseServiceSettings` with `service_name="training-pipeline"`, `port=8009`, `database_url` defaulting to `postgresql+asyncpg://aqros:aqros@localhost:5435/aqros_training_pipeline`, pool-size settings, `dataset_builder_base_url: AnyHttpUrl` defaulting to `http://localhost:8008`, `upstream_request_timeout_seconds`, and `artifact_dir: str` defaulting to `/data/training-pipeline/artifacts` — all overridable via `AQROS_*` env vars, mirroring `aqros_dataset_builder.config`.
  - _Requirements: 15.1, 15.2, 15.3, 13.4_
- [x] 6.2 Implement `app.py`: module-level `Settings()`, `create_engine`/`create_session_factory`, a `HealthRegistry` registering `database` (via `db.ping`) and `dataset_builder_service` (via the same lenient "any response counts as reachable" check pattern as `aqros_dataset_builder.app._check_upstream_reachable`), a `_build_app()` wrapping `aqros_core.app.create_app`'s lifespan to attach `dataset_builder_client`, `artifact_store`, `git_info_provider` to `app.state` and close the httpx client/engine on shutdown, then include the `training_runs` and `trained_models` routers.
  - _Requirements: 15.4_
- [x] 6.3 Implement `main.py`: trivial uvicorn entrypoint reading `Settings()` and running `aqros_training_pipeline.app:app` — copy `aqros_dataset_builder.main`'s pattern.
  - _Requirements: 15.1_

## 7. Docker & Compose Integration

- [x] 7.1 Add the `training-pipeline-db` and `training-pipeline` service entries to the root `docker-compose.yml`, using the exact block drafted in design.md Section 16 (port 8009, Postgres port 5435, `AQROS_DATASET_BUILDER_BASE_URL=http://dataset-builder:8008`, `depends_on: training-pipeline-db (service_healthy), dataset-builder (service_started)`), plus the `training-pipeline-db-data` and `training-pipeline-artifacts` named volumes.
  - _Requirements: 15.2, 15.3_
- [x] 7.2 Add `"aqros_training_pipeline"` to the root `pyproject.toml`'s `[tool.ruff.lint.isort].known-first-party` list.
  - _Requirements: 17.1_
- [x] 7.3 Verify (no edit expected) that `backend/training-pipeline` is picked up by the existing `[tool.uv.workspace] members = ["libs/*", "backend/*"]` glob and that `docker/Dockerfile.service`'s parameterized `SERVICE`/`MODULE`/`PORT` build args require no changes to build this service.
  - _Requirements: 15.1_

## 8. Unit Tests (fakes for every port + property-based tests)

- [x] 8.1 Create fakes: `FakeDatasetBuilderClient`, `FakeArtifactStore`, `FakeTrainedModelRepository`, `FakeTrainingRunRepository`, `FakeGitInfoProvider` implementing every port from `domain/ports.py` in-memory, for use across all unit tests.
  - _Requirements: 16.1_
- [x] 8.2 `tests/unit/test_verification.py`: concrete tests for checksum match/mismatch and each `leakage_audit_passed` value (`true`/`false`/`null`); property test for **Property 3** (checksum-and-leakage AND-gate).
  - _Requirements: 3.1, 3.2, 3.3, 4.1, 4.2, 4.3, 4.4, 18.4_
- [x] 8.3 `tests/unit/test_partitioning.py`: property tests for **Property 4** (fold/split-role read verbatim), **Property 5** (no shuffle/reorder/resample), **Property 6** (per-fold fitting uses exactly that fold's train rows), **Property 9** (zero evaluable folds rejects).
  - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 6.4_
- [x] 8.4 `tests/unit/test_trainers.py`: one concrete unit test per `Model_Type` (Requirements 7.2-7.5) confirming the correct estimator class and hyperparameter defaults/overrides are applied; property test for **Property 10** (any non-empty subset of the four types is accepted) and **Property 11** (unsupported type rejected, validation-layer test) if exercised at the domain layer.
  - _Requirements: 7.2, 7.3, 7.4, 7.5, 7.6_
- [x] 8.5 `tests/unit/test_evaluation.py`: property tests for **Property 7** (per-fold evaluation isolation), **Property 8** (aggregated mean/std correctness), **Property 13** (per-fold metrics match scikit-learn reference formulas), **Property 14** (single-class fold ROC AUC undefined and excluded from aggregation).
  - _Requirements: 6.1, 6.2, 6.3, 9.1, 9.2, 9.3_
- [x] 8.6 `tests/unit/test_feature_importance.py`: property tests for **Property 15** (extracted importance equals the fitted model's own values) and **Property 16** (exactly one value per manifest feature_name).
  - _Requirements: 10.1, 10.2, 10.3_
- [x] 8.7 `tests/unit/test_versioning.py`: property test for **Property 12** (monotonic, unique, immutable per composite model name, and independent across distinct dataset names) — must include an explicit case exercising two different `dataset_name`s against the same `ModelType` and asserting their version sequences never interact.
  - _Requirements: 8.1, 8.2, 8.3, 8.4_
- [x] 8.8 `tests/unit/test_reports.py`: property tests for **Property 17** (Training_Report lists exactly the produced Trained_Model ids) and **Property 18** (Metrics_Report round-trips a Trained_Model's data unchanged).
  - _Requirements: 11.1, 11.2_
- [x] 8.9 `tests/unit/test_training_pipeline_service.py`: property tests for **Property 1** (fail-closed, zero retry on upstream failure), **Property 2** (manifest+build-run precede download, 404 halts immediately), **Property 19** (reproducibility metadata fields match source values), **Property 20** (incomplete metadata blocks persistence except absent git commit), **Property 21** (non-git-commit metadata failures block persistence), and **Property 26** (all-or-nothing multi-Model_Type run status — succeeded iff every requested type trained; failed if any fails; successful siblings' artifacts are retained and never rolled back).
  - _Requirements: 1.4, 2.1, 2.2, 2.3, 2.4, 12.1, 12.2, 12.3, 12.4_
- [x] 8.10 `tests/unit/test_local_artifact_store.py` (or fold into an adapters unit test directory): property tests for **Property 22** (artifact path deterministically encodes model name and version), **Property 23** (never overwrites), **Property 24** (write-then-read round trip).
  - _Requirements: 13.1, 13.2, 13.3_
- [x] 8.11 Configure every property test above with `hypothesis`, `@settings(max_examples=100)` minimum, and a `# Feature: training-pipeline, Property N: <property text>` comment tag directly above each test function, per design.md's Testing Strategy section.
  - _Requirements: 16.1_

## 9. Integration Tests

- [x] 9.1 Create `tests/integration/conftest.py`: `postgres_container` (testcontainers `PostgresContainer`), `engine`, `session_factory`, `db_session` fixtures, and a `client` fixture building the FastAPI app via `httpx.AsyncClient`+`ASGITransport` with `get_session`, `get_dataset_builder_client` (overridden with an in-memory fake, no live Dataset Builder required), and `get_git_info_provider` overridden — mirroring `aqros_dataset_builder`'s `tests/integration/conftest.py` fixture shapes, including manually populating `app.state` since `ASGITransport` never runs the app lifespan.
  - _Requirements: 16.2, 16.3_
- [x] 9.2 Create `tests/integration/test_api.py`: end-to-end happy-path test for `POST /v1/training-runs` through to `GET /v1/trained-models/.../artifact` against the real Postgres container and the faked Dataset Builder client; 404-path tests for every endpoint per **Property 25**; a test exercising the all-or-nothing failure path (one `Model_Type` engineered to fail via the fake) asserting the run status is `failed` while the successful sibling's `Trained_Model` is still retrievable.
  - _Requirements: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8, 16.2, 16.3_
- [x] 9.3 Create `tests/integration/test_repository.py`: exercises `SqlAlchemyTrainingRunRepository` and `SqlAlchemyTrainedModelRepository` against the real Postgres container, including a concurrency-oriented test asserting the `UniqueConstraint(model_name, model_version)` rejects a duplicate version assignment.
  - _Requirements: 8.4, 16.2_
- [x] 9.4 Create `tests/integration/test_migrations.py`: runs the Alembic `0001_initial_schema` upgrade/downgrade against the testcontainers Postgres, mirroring `aqros_dataset_builder`'s own `test_migrations.py`.
  - _Requirements: 16.2_
- [x] 9.5 Create `tests/test_health.py`: 1-2 concrete examples covering Requirement 15.4's readiness composition (all checks healthy -> 200; one check failing -> 503).
  - _Requirements: 15.4_

## 10. Quality Gates & Final Verification

- [x] 10.1 Run `ruff check backend/training-pipeline` and fix any findings.
  - _Requirements: 17.1_
- [x] 10.2 Run `black --check backend/training-pipeline` and fix any formatting issues.
  - _Requirements: 17.2_
- [x] 10.3 Run `mypy --strict` against `backend/training-pipeline/src` and resolve any type errors, ensuring every public interface is fully type-hinted.
  - _Requirements: 17.3_
- [x] 10.4 Run the full `pytest` suite for `backend/training-pipeline` (unit + integration) and confirm a non-zero exit code on any failure or setup error.
  - _Requirements: 16.4_
- [x] 10.5 Build the `training-pipeline` Docker image via `docker compose build training-pipeline` and verify `docker compose up` brings up `training-pipeline` and `training-pipeline-db` with `/health/ready` reporting healthy once the Dataset Builder service is reachable.
  - _Requirements: 15.1, 15.2, 15.3, 15.4_
- [x] 10.6 Traceability check: confirm every requirement (1.1 through 18.5) in `requirements.md` is satisfied by at least one completed task above, and both user-approved design corrections (composite model-name versioning; all-or-nothing multi-Model_Type run status) are reflected in the implemented code, not just the design document.
  - _Requirements: all (1-18)_
