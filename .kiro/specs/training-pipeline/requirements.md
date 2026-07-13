# Requirements Document

## Introduction

The Training Pipeline is a new AQROS backend microservice (`backend/training-pipeline`, module `aqros_training_pipeline`) responsible for training and evaluating candidate machine learning models on datasets produced by the Dataset Builder service. It never generates data itself: it consumes only Dataset Builder-approved, leakage-audited datasets through the Dataset Builder's published REST API, trains on the fold and split-role structure already present in the data, evaluates per fold and in aggregate, and produces versioned model artifacts together with full reproducibility metadata. The Training Pipeline follows the platform's point-in-time correctness discipline, the "never re-split a dataset the Dataset Builder already split" rule, and the service-boundary rule that no service may reach into another service's database (CLAUDE.md §7.9). This service trains and records model candidates only; it never promotes a model to production or live trading use — that responsibility belongs to the future Model Registry service (CLAUDE.md Hard Rule §7.4).

This document covers the MVP scope of the Training Pipeline: batch training runs triggered via REST API, four baseline model classes, per-fold and aggregate evaluation, metrics and feature importance computation, reproducibility metadata capture, versioned artifact storage, and the associated Docker/testing/quality-gate requirements shared by every existing AQROS backend service.

## Glossary

- **Training_Pipeline**: The `training-pipeline` backend microservice as a whole, exposing a REST API and orchestrating model training runs.
- **Dataset_Builder_Client**: The adapter component within the Training_Pipeline that communicates exclusively with the Dataset Builder service's published REST API (`GET /v1/datasets/{name}`, `GET /v1/runs/{run_id}`, `GET /v1/runs/{run_id}/manifest`, `GET /v1/runs/{run_id}/download`).
- **Dataset_Manifest**: The reproducibility manifest returned by the Dataset Builder's `GET /v1/runs/{run_id}/manifest` endpoint, containing `dataset_name`, `dataset_version`, `build_run_id`, `checksum`, `checksum_algorithm`, `feature_names`, `feature_versions`, `label_type`, `label_definition`, `horizon`, `split_strategy`, `split_params`, `start_date`, `end_date`, `created_at`, `row_count`, `git_commit`, `market_data_source_url`, `feature_store_source_url`, and `quality_report`.
- **Dataset_Build_Run**: The Dataset Builder build-run record returned by `GET /v1/runs/{run_id}`, including the `leakage_audit_passed` flag and `leakage_audit_findings`.
- **Dataset_Artifact**: The Parquet file downloaded from the Dataset Builder's `GET /v1/runs/{run_id}/download` endpoint, containing one row per observation with columns `symbol`, `event_time`, `fold`, `split_role`, `label`, and one column per feature.
- **Fold**: An integer identifying one partition produced by the Dataset Builder's split strategy (walk-forward, rolling window, expanding window, or purged CV), as recorded in the `fold` column of a Dataset_Artifact.
- **Split_Role**: The value of the `split_role` column of a Dataset_Artifact row, constrained to `train`, `validation`, or `test`, assigned by the Dataset Builder and never reassigned by the Training_Pipeline.
- **Pre_Training_Verifier**: The component that validates a downloaded Dataset_Artifact against its Dataset_Manifest and Dataset_Build_Run before any training begins.
- **Model_Type**: One of the four supported baseline model classes: `logistic_regression`, `random_forest`, `xgboost`, or `lightgbm`.
- **Training_Request**: A REST API request specifying the dataset name, dataset build run identifier, one or more Model_Types, and hyperparameters to train.
- **Training_Run**: A single execution of the Training_Pipeline against one Training_Request, producing one or more Trained_Model records.
- **Trained_Model**: The Training_Pipeline's own record of one trained candidate: an immutable, versioned combination of a Model_Type, its Model_Version, its serialized Model_Artifact, its Per_Fold_Metrics, its Aggregated_Metrics, its Feature_Importance, and its Reproducibility_Metadata.
- **Model_Version**: An immutable, monotonically incrementing integer assigned per model name (matching the Dataset Builder's own dataset-versioning pattern in `api/routes/datasets.py`), never reused or decremented.
- **Model_Artifact**: The serialized, versioned model file produced by training one Model_Type on one dataset, stored by the Artifact_Store.
- **Per_Fold_Metrics**: The set of evaluation metrics (accuracy, precision, recall, F1 score, ROC AUC, confusion matrix) computed independently on the `test` Split_Role rows of one Fold.
- **Aggregated_Metrics**: The mean and standard deviation of each Per_Fold_Metrics value across every Fold of a Trained_Model.
- **Feature_Importance**: A mapping from feature name to an importance value for a Trained_Model — coefficients for `logistic_regression`, impurity- or gain-based importance for `random_forest`, `xgboost`, and `lightgbm`.
- **Training_Report**: A structured, retrievable document summarizing a Training_Run: the Training_Request parameters, the resulting Trained_Model identifiers, and a human-readable summary of outcomes.
- **Metrics_Report**: A structured, retrievable document containing the Per_Fold_Metrics, Aggregated_Metrics, and Feature_Importance for one Trained_Model.
- **Reproducibility_Metadata**: The lineage record captured for every Trained_Model: Model_Version, dataset name, dataset version, dataset checksum, Dataset_Manifest reference, git commit of the Training_Pipeline code, training timestamp, hyperparameters used, and the full Aggregated_Metrics.
- **Artifact_Store**: The component responsible for persisting and retrieving versioned, immutable Model_Artifact files, backed by the local filesystem in the MVP and swappable for an object store later, mirroring the Dataset Builder's `DatasetStorage` port pattern.
- **Training_API**: The FastAPI REST interface exposed by the Training_Pipeline for triggering Training_Runs and retrieving Trained_Model metadata, Training_Reports, Metrics_Reports, and Model_Artifacts.
- **Leakage_Audit_Passed**: The boolean field on a Dataset_Build_Run indicating whether the Dataset Builder's automated leakage audit succeeded for the dataset build run in question.

## Requirements

### Requirement 1: Dataset Consumption Exclusively via the Dataset Builder REST API

**User Story:** As a platform architect, I want the Training Pipeline to obtain all dataset data exclusively through the Dataset Builder's published REST API, so that service boundaries stay intact and no service ever reaches into another service's database or internals.

#### Acceptance Criteria

1. WHEN the Training_Pipeline requires dataset rows for a Training_Request, THE Dataset_Builder_Client SHALL retrieve those rows only via an HTTP request to the Dataset Builder's `GET /v1/runs/{run_id}/download` endpoint.
2. THE Training_Pipeline SHALL NOT establish a direct database connection to the Dataset Builder's, Market Data's, or Feature Store's databases.
3. THE Training_Pipeline SHALL NOT issue any HTTP request to the Market Data Service or the Feature Store Service.
4. IF the Dataset Builder's REST API returns an error response or is unreachable, THEN THE Dataset_Builder_Client SHALL immediately fail the Training_Run, set the Training_Run's status to `failed`, and record the error in the Training_Run's status without retrying against any other data source.

### Requirement 2: Dataset Manifest Retrieval Before Training

**User Story:** As a quant researcher, I want the Training Pipeline to retrieve the dataset's reproducibility manifest before training starts, so that every training run is tied to a known, documented dataset lineage.

#### Acceptance Criteria

1. WHEN a Training_Request specifies a dataset build run identifier, THE Dataset_Builder_Client SHALL retrieve the corresponding Dataset_Manifest via `GET /v1/runs/{run_id}/manifest` before downloading the Dataset_Artifact.
2. WHEN a Training_Request specifies a dataset build run identifier, THE Dataset_Builder_Client SHALL retrieve the corresponding Dataset_Build_Run via `GET /v1/runs/{run_id}` before downloading the Dataset_Artifact.
3. IF the Dataset Builder's API returns a 404 response for the requested dataset build run identifier at any retrieval step, THEN THE Training_Pipeline SHALL immediately halt further retrieval steps, reject the Training_Request, and report that the dataset build run does not exist.
4. THE Training_Pipeline SHALL NOT download a Dataset_Artifact for a Training_Request until both the Dataset_Manifest and the Dataset_Build_Run have been successfully retrieved.

### Requirement 3: Dataset Checksum Verification

**User Story:** As a quant researcher, I want the Training Pipeline to verify that the downloaded dataset file matches the checksum recorded in its manifest, so that training never runs against a corrupted or substituted dataset artifact.

#### Acceptance Criteria

1. WHEN the Dataset_Builder_Client downloads a Dataset_Artifact, THE Pre_Training_Verifier SHALL compute the checksum of the downloaded file using the algorithm named in the Dataset_Manifest's `checksum_algorithm` field.
2. IF the computed checksum of the downloaded Dataset_Artifact does not equal the Dataset_Manifest's `checksum` field, THEN THE Pre_Training_Verifier SHALL reject the Training_Request and record a checksum-mismatch error without proceeding to training.
3. WHEN the computed checksum of the downloaded Dataset_Artifact equals the Dataset_Manifest's `checksum` field, THE Pre_Training_Verifier SHALL permit (but not guarantee) the Training_Run to proceed to the leakage-audit verification step, WHERE the leakage-audit verification step may still independently reject the Training_Request.

### Requirement 4: Leakage Audit Verification Before Training

**User Story:** As a risk-conscious platform owner, I want the Training Pipeline to refuse to train on any dataset whose leakage audit did not pass, so that models are never trained on data that violates the platform's point-in-time correctness discipline.

#### Acceptance Criteria

1. WHEN the Pre_Training_Verifier has verified the Dataset_Artifact's checksum, THE Pre_Training_Verifier SHALL inspect the `leakage_audit_passed` field of the corresponding Dataset_Build_Run.
2. IF the Dataset_Build_Run's `leakage_audit_passed` field is `false` or `null`, THEN THE Pre_Training_Verifier SHALL reject the Training_Request and record the Dataset_Build_Run's `leakage_audit_findings` in the rejection reason without proceeding to training.
3. WHEN the Dataset_Build_Run's `leakage_audit_passed` field is `true` AND the Dataset_Artifact's checksum has already been verified per Requirement 3, THE Pre_Training_Verifier SHALL permit the Training_Run to proceed to model training.
4. THE Pre_Training_Verifier SHALL NOT permit the Training_Run to proceed to model training unless every verification step defined in Requirements 3 and 4 has completed successfully.

### Requirement 5: Training via Existing Fold and Split-Role Columns Only

**User Story:** As a quant researcher, I want the Training Pipeline to train and evaluate strictly using the fold and split_role columns already present in the dataset, so that the Dataset Builder remains the single owner of data splitting and the platform's leakage discipline is never violated by re-splitting.

#### Acceptance Criteria

1. WHEN the Training_Pipeline partitions a Dataset_Artifact's rows for training and evaluation, THE Training_Pipeline SHALL assign each row's role using only the value already present in that row's `split_role` column.
2. WHEN the Training_Pipeline groups a Dataset_Artifact's rows by fold, THE Training_Pipeline SHALL assign each row's fold using only the value already present in that row's `fold` column.
3. THE Training_Pipeline SHALL NOT compute, generate, or assign a new train/validation/test split for any Dataset_Artifact.
4. THE Training_Pipeline SHALL NOT shuffle, reorder, or resample the rows of a Dataset_Artifact prior to training, for any purpose including performance optimization.
5. FOR ALL Dataset_Artifact rows within one Fold, THE Model_Trainer SHALL fit each Model_Type using only the rows whose `split_role` equals `train` for that Fold.

### Requirement 6: Independent Per-Fold Evaluation and Cross-Fold Aggregation

**User Story:** As a quant researcher, I want the Training Pipeline to evaluate each fold independently and then aggregate results across folds, so that model performance reflects the walk-forward/purged-CV evaluation discipline the platform is built on.

#### Acceptance Criteria

1. FOR ALL Folds present in a Dataset_Artifact, THE Evaluation_Engine SHALL compute one independent Per_Fold_Metrics record using only the rows of that Fold whose `split_role` equals `test`.
2. WHEN Per_Fold_Metrics have been computed for every Fold of a Trained_Model, THE Evaluation_Engine SHALL compute Aggregated_Metrics containing the mean and standard deviation of each metric across all Folds.
3. THE Evaluation_Engine SHALL NOT combine rows from more than one Fold when computing a single Fold's Per_Fold_Metrics.
4. IF a Dataset_Artifact contains fewer than one Fold with a non-empty `test` Split_Role, THEN THE Evaluation_Engine SHALL reject the Training_Request and record that no evaluable folds were found.

### Requirement 7: Support for Four Selectable Baseline Model Types

**User Story:** As a quant researcher, I want to select from four baseline model classes when triggering a training run, so that I always have access to the mandatory linear baseline alongside the tree-ensemble workhorses used for tabular financial prediction.

#### Acceptance Criteria

1. THE Training_API SHALL accept a Training_Request specifying one or more Model_Types from the set `{logistic_regression, random_forest, xgboost, lightgbm}`.
2. WHEN a Training_Request specifies `logistic_regression`, THE Model_Trainer SHALL fit a regularized logistic regression model.
3. WHEN a Training_Request specifies `random_forest`, THE Model_Trainer SHALL fit a random forest classifier.
4. WHEN a Training_Request specifies `xgboost`, THE Model_Trainer SHALL fit a gradient-boosted tree model using XGBoost.
5. WHEN a Training_Request specifies `lightgbm`, THE Model_Trainer SHALL fit a gradient-boosted tree model using LightGBM.
6. IF a Training_Request specifies a Model_Type outside the set `{logistic_regression, random_forest, xgboost, lightgbm}`, THEN THE Training_API SHALL reject the Training_Request with a validation error identifying the unsupported Model_Type.

### Requirement 8: Immutable, Incrementing Model Versioning

**User Story:** As a quant researcher, I want every trained model to receive an immutable, incrementing version number per model name, so that trained candidates are as reproducible and unambiguously referenceable as the datasets they were trained on.

#### Acceptance Criteria

1. WHEN a Model_Trainer successfully produces the first Trained_Model for a given model name, THE Training_Pipeline SHALL assign that Trained_Model a Model_Version of 1.
2. WHEN a Model_Trainer successfully produces a subsequent Trained_Model for a model name that already has one or more existing Model_Versions, THE Training_Pipeline SHALL assign that Trained_Model a Model_Version equal to one greater than the highest existing Model_Version for that model name.
3. THE Training_Pipeline SHALL NOT modify or reassign the Model_Version of a previously recorded Trained_Model, WHERE this constraint applies only once a Trained_Model record for that Model_Version has been recorded and does not prevent the initial Model_Version assignment described in Acceptance Criteria 8.1 and 8.2.
4. FOR ALL Trained_Model records sharing the same model name, THE Training_Pipeline SHALL ensure every Model_Version among them is unique.

### Requirement 9: Per-Fold and Aggregated Metrics Computation

**User Story:** As a quant researcher, I want the standard classification metrics computed for every fold and in aggregate, so that I can assess a candidate model's predictive quality and its stability across folds.

#### Acceptance Criteria

1. FOR ALL Per_Fold_Metrics records, THE Evaluation_Engine SHALL compute accuracy, precision, recall, F1 score, ROC AUC, and a confusion matrix from the Fold's `test` Split_Role rows.
2. FOR ALL Aggregated_Metrics records, THE Evaluation_Engine SHALL compute the mean and standard deviation of accuracy, precision, recall, F1 score, and ROC AUC across all Folds of the Trained_Model.
3. WHEN the Evaluation_Engine computes ROC AUC for a Fold whose `test` rows contain only one label class, THE Evaluation_Engine SHALL record that Fold's ROC AUC as undefined for that Fold and exclude it from the ROC AUC component of the Aggregated_Metrics mean and standard deviation.

### Requirement 10: Feature Importance Extraction

**User Story:** As a quant researcher, I want feature importance extracted for every trained model, so that I can inspect which features drove each candidate's predictions.

#### Acceptance Criteria

1. WHEN a Model_Trainer completes training a `logistic_regression` Trained_Model, THE Feature_Importance_Extractor SHALL compute Feature_Importance from the fitted model's coefficients.
2. WHEN a Model_Trainer completes training a `random_forest`, `xgboost`, or `lightgbm` Trained_Model, THE Feature_Importance_Extractor SHALL compute Feature_Importance from the fitted model's impurity- or gain-based feature importance values.
3. FOR ALL Trained_Model records, THE Feature_Importance_Extractor SHALL produce one Feature_Importance value for every feature listed in the Dataset_Manifest's `feature_names` field.

### Requirement 11: Training Report and Metrics Report Generation

**User Story:** As a quant researcher, I want structured training and metrics reports generated for every training run, so that I can review outcomes without inspecting raw artifacts.

#### Acceptance Criteria

1. WHEN a Training_Run completes, THE Report_Generator SHALL produce one Training_Report summarizing the Training_Request parameters and the identifiers of every resulting Trained_Model.
2. WHEN a Model_Trainer completes training one Trained_Model, THE Report_Generator SHALL produce one Metrics_Report containing that Trained_Model's Per_Fold_Metrics, Aggregated_Metrics, and Feature_Importance.
3. THE Training_API SHALL make every Training_Report and Metrics_Report retrievable via a REST endpoint identified by its Training_Run or Trained_Model identifier.

### Requirement 12: Full Reproducibility Metadata Capture

**User Story:** As a platform architect, I want every trained model to carry complete lineage metadata, so that any trained candidate can be traced back to the exact dataset, code, and configuration that produced it.

#### Acceptance Criteria

1. WHEN a Model_Trainer successfully produces a Trained_Model, THE Training_Pipeline SHALL record Reproducibility_Metadata containing the Trained_Model's Model_Version, the dataset name, the dataset version, the dataset's `checksum` from its Dataset_Manifest, a reference to the Dataset_Manifest, the git commit of the Training_Pipeline code at training time, the training timestamp, the hyperparameters used, and the Trained_Model's Aggregated_Metrics.
2. THE Training_Pipeline SHALL NOT persist a Trained_Model record whose Reproducibility_Metadata is incomplete.
3. IF the git commit of the Training_Pipeline code cannot be determined at training time, THEN THE Training_Pipeline SHALL record the Reproducibility_Metadata's git commit field as absent and proceed with the remainder of the Reproducibility_Metadata.
4. IF recording any part of a Trained_Model's Reproducibility_Metadata fails for any reason other than an absent git commit, including a database error, THEN THE Training_Pipeline SHALL NOT persist that Trained_Model record.

### Requirement 13: Versioned, Immutable Model Artifact Storage

**User Story:** As a quant researcher, I want every trained model's serialized artifact stored immutably and retrievably, so that a previously trained candidate can always be reloaded exactly as it was produced.

#### Acceptance Criteria

1. WHEN a Model_Trainer successfully produces a Trained_Model, THE Artifact_Store SHALL persist that Trained_Model's serialized Model_Artifact under a path or key that includes its model name and Model_Version.
2. THE Artifact_Store SHALL NOT overwrite a previously persisted Model_Artifact.
3. WHEN the Training_API receives a request for a previously persisted Model_Artifact identified by model name and Model_Version, THE Artifact_Store SHALL return that exact Model_Artifact.
4. WHERE a future deployment configures an object-store-backed Artifact_Store implementation in place of the local-filesystem implementation, THE Training_Pipeline SHALL persist and retrieve Model_Artifacts through the same Artifact_Store interface without any change to the Model_Trainer or Training_API code.

### Requirement 14: REST API for Triggering Training and Retrieving Results

**User Story:** As a quant researcher, I want a REST API to trigger training runs and retrieve trained models, reports, and artifacts, so that I can integrate training into research workflows without direct database or filesystem access.

#### Acceptance Criteria

1. THE Training_API SHALL expose an endpoint that accepts a Training_Request and creates a new Training_Run.
2. THE Training_API SHALL expose an endpoint that retrieves the status and Training_Report of a Training_Run by its identifier.
3. THE Training_API SHALL expose an endpoint that lists Trained_Model records, optionally filtered by model name.
4. THE Training_API SHALL expose an endpoint that retrieves one Trained_Model's Reproducibility_Metadata by its model name and Model_Version.
5. THE Training_API SHALL expose an endpoint that retrieves one Trained_Model's Metrics_Report by its model name and Model_Version.
6. THE Training_API SHALL expose an endpoint that downloads one Trained_Model's Model_Artifact by its model name and Model_Version.
7. THE Training_API SHALL expose OpenAPI documentation describing every exposed endpoint.
8. IF a request identifies a Training_Run, Trained_Model, Training_Report, Metrics_Report, or Model_Artifact that does not exist, THEN THE Training_API SHALL respond with a 404 response and a typed error body identifying the missing resource.

### Requirement 15: Docker Deployment Following the Shared Service Pattern

**User Story:** As a platform operator, I want the Training Pipeline deployable via the same Docker and docker-compose conventions as the existing services, so that it fits into the existing local and future cloud deployment workflows without special-casing.

#### Acceptance Criteria

1. THE Training_Pipeline SHALL build into a container image using the shared parameterized `docker/Dockerfile.service` pattern used by market-data, feature-store, and dataset-builder.
2. THE Training_Pipeline SHALL register a `training-pipeline` service entry in the root `docker-compose.yml` that exposes its own port and depends on its own dedicated Postgres database service.
3. THE Training_Pipeline SHALL register a `training-pipeline-db` Postgres service entry in the root `docker-compose.yml` that is not shared with any other backend service.
4. THE Training_Pipeline SHALL expose `/health`, `/health/live`, and `/health/ready` endpoints consistent with the existing market-data, feature-store, and dataset-builder services, WHERE the readiness check additionally verifies connectivity to its own database and to the Dataset Builder Service's REST API.

### Requirement 16: Automated Testing Coverage

**User Story:** As a platform maintainer, I want comprehensive automated tests for the Training Pipeline, so that its domain logic and its integration with the Dataset Builder and its own database are verified without manual testing.

#### Acceptance Criteria

1. THE Training_Pipeline SHALL include unit tests that exercise its domain logic against fakes for every port, including the Dataset_Builder_Client port, the Artifact_Store port, and the Trained_Model repository port.
2. THE Training_Pipeline SHALL include integration tests that exercise its REST API through an HTTP client against a real Postgres database provisioned via testcontainers.
3. THE Training_Pipeline SHALL include integration tests that exercise its REST API against a faked Dataset Builder REST client, without requiring a live Dataset Builder service instance.
4. WHEN the Training_Pipeline's automated test suite is executed, THE Training_Pipeline SHALL report a non-zero exit status IF any unit test or integration test fails, or IF a test setup error, missing test file, or test configuration error prevents the suite from running to completion.

### Requirement 17: Code Quality Gates Matching the Monorepo Standard

**User Story:** As a platform maintainer, I want the Training Pipeline held to the same linting, formatting, and type-checking standards as the rest of the monorepo, so that the codebase remains consistent and maintainable.

#### Acceptance Criteria

1. THE Training_Pipeline SHALL pass Ruff linting using the rule set already configured in the root `pyproject.toml`.
2. THE Training_Pipeline SHALL pass Black formatting checks using the line length and target version already configured in the root `pyproject.toml`.
3. THE Training_Pipeline SHALL pass MyPy strict type checking using the configuration already defined in the root `pyproject.toml`, with every function and public interface fully type-hinted.

### Requirement 18: Boundary Enforcement and Explicit Non-Goals

**User Story:** As a platform architect, I want the Training Pipeline's responsibilities explicitly bounded, so that it never duplicates the Dataset Builder's responsibilities and never bypasses the platform's model-promotion governance.

#### Acceptance Criteria

1. THE Training_Pipeline SHALL NOT generate, build, or persist a dataset.
2. THE Training_Pipeline SHALL NOT issue any request to the Market Data Service.
3. THE Training_Pipeline SHALL NOT issue any request to the Feature Store Service.
4. THE Training_Pipeline SHALL NOT train on a dataset whose corresponding Dataset_Build_Run has a `leakage_audit_passed` value other than `true`.
5. THE Training_Pipeline SHALL NOT mark, flag, or otherwise designate any Trained_Model as promoted to production or live trading use.
