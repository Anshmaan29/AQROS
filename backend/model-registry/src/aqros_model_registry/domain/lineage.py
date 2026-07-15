"""The pure ``Lineage_Assembler`` for the Model Registry (Requirements 4, 5, 6, 9, 10).

Every ``Model_Version`` the Registry records must carry complete, verified
provenance so that it can be traced to the exact data, code, and configuration
that produced it, and reproduced independently of any mutable external state
(Requirement 9.4). This module is the pure kernel of that guarantee. It does
three things and nothing else:

* :func:`assemble_reproducibility_metadata` — project a ``TrainedModelRecord``
  pulled from the Training Pipeline into the immutable
  :class:`~aqros_model_registry.domain.models.ReproducibilityMetadata` recorded
  on the ``Model_Version`` (Requirements 9.1, 9.3, 10.1).
* :func:`assemble_lineage` — project the same record into the read-only
  :class:`Lineage` view served by the lineage endpoint: the dataset version,
  feature versions, git commit, and training run id (Requirement 9.2).
* :func:`mandatory_metadata_complete` — gate persistence on completeness of the
  mandatory metadata (dataset name/version/checksum, feature versions, metrics,
  training run id, artifact checksum), while tolerating an absent git commit,
  which is recorded as explicitly absent (Requirements 4.3, 5.3, 6.1-6.4).

This module is pure: no I/O, no framework dependencies, exhaustively
property-testable, and it never imports ``aqros_training_pipeline`` — it works
only against the local, decoupled copies in ``domain/models.py`` (CLAUDE.md
§7.9).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from aqros_model_registry.domain.models import (
    DatasetVersionRef,
    ReproducibilityMetadata,
    TrainedModelRecord,
)

# The names of the mandatory metadata fields, in the order they are reported by
# ``mandatory_metadata_complete``. These match the mandatory set enumerated in
# Requirements 4.1, 5.1, 6.1 (an absent git commit is *not* in this set — it is
# tolerated per Requirement 6.3).
FIELD_DATASET_NAME = "dataset_name"
FIELD_DATASET_VERSION = "dataset_version"
FIELD_DATASET_CHECKSUM = "dataset_checksum"
FIELD_FEATURE_VERSIONS = "feature_versions"
FIELD_METRICS = "metrics"
FIELD_TRAINING_RUN_ID = "training_run_id"
FIELD_ARTIFACT_CHECKSUM = "artifact_checksum"


@dataclass(frozen=True, slots=True)
class Lineage:
    """The read-only provenance view served for a ``Model_Version`` (Requirement 9.2).

    Assembled from a ``TrainedModelRecord`` (or, equivalently, from the
    immutable fields of a recorded ``Model_Version``); it exposes the full
    provenance chain — the dataset version the model was trained on, the
    feature versions used, the code commit, the training run, and the training
    timestamp — without exposing any mutable governance state.
    """

    model_name: str
    model_version: int
    dataset_version: DatasetVersionRef
    feature_versions: dict[str, int]
    git_commit: str | None  # None => explicitly absent (Requirement 6.3)
    training_run_id: int
    trained_at: datetime


@dataclass(frozen=True, slots=True)
class MetadataCompletenessResult:
    """The result of a mandatory-metadata completeness check (Requirement 6.2).

    ``ok`` is ``True`` only when *every* mandatory field is present; otherwise
    ``missing_fields`` names each absent field (in the canonical order defined
    by this module) so the caller can record a precise, human-readable
    rejection reason (Requirements 4.3, 5.3, 6.2, 20.5). An absent git commit
    never appears here — it is tolerated and recorded as explicitly absent
    (Requirement 6.3).
    """

    ok: bool
    missing_fields: tuple[str, ...] = field(default_factory=tuple)


def _is_blank(value: str) -> bool:
    """Return ``True`` if ``value`` is empty or only whitespace."""
    return not value.strip()


def mandatory_metadata_complete(record: TrainedModelRecord) -> MetadataCompletenessResult:
    """Check that ``record`` carries every mandatory metadata field.

    A ``Model_Version`` may be persisted only if its source
    ``TrainedModelRecord`` supplies all of the mandatory metadata
    (Requirement 6.2). This function decides completeness purely; it never
    persists anything and never raises. The mandatory fields are:

    * the dataset name, version, and checksum (Requirement 4.1, 4.3),
    * the feature-name-to-version mapping (Requirement 5.1, 5.3),
    * the metrics (per-fold metrics reported by the Training Pipeline;
      Requirement 10.1),
    * the training run id (Requirement 6.1), and
    * the artifact checksum (the ``Model_Checksum``; Requirements 6.1, 7.1).

    A missing dataset/artifact string is one that is empty or only whitespace;
    a missing integer identifier (dataset version, training run id) is one that
    is not strictly positive, since the Training Pipeline assigns monotonically
    incrementing positive versions and run ids; missing feature versions is an
    empty mapping; and missing metrics is an empty per-fold sequence.

    An absent git commit is *not* a completeness failure: it is tolerated and
    recorded as explicitly absent by the assemblers (Requirement 6.3).

    Returns:
        A :class:`MetadataCompletenessResult`; ``ok`` is ``True`` with an empty
        ``missing_fields`` when every mandatory field is present, otherwise
        ``ok`` is ``False`` and ``missing_fields`` names each absent field.
    """
    missing: list[str] = []

    if _is_blank(record.dataset_name):
        missing.append(FIELD_DATASET_NAME)
    if record.dataset_version <= 0:
        missing.append(FIELD_DATASET_VERSION)
    if _is_blank(record.dataset_checksum):
        missing.append(FIELD_DATASET_CHECKSUM)
    if not record.feature_versions:
        missing.append(FIELD_FEATURE_VERSIONS)
    if not record.per_fold_metrics:
        missing.append(FIELD_METRICS)
    if record.training_run_id <= 0:
        missing.append(FIELD_TRAINING_RUN_ID)
    if _is_blank(record.artifact_checksum):
        missing.append(FIELD_ARTIFACT_CHECKSUM)

    return MetadataCompletenessResult(ok=not missing, missing_fields=tuple(missing))


def assemble_reproducibility_metadata(
    record: TrainedModelRecord,
) -> ReproducibilityMetadata:
    """Project ``record`` into immutable ``ReproducibilityMetadata`` (Requirement 9.1).

    Carries forward the model version, dataset name/version/checksum, feature
    versions, git commit (``None`` when the Training Pipeline could not
    determine it — recorded as explicitly absent per Requirement 6.3), training
    run id, training timestamp, hyperparameters, and aggregated metrics — the
    full set required to reproduce the model independently (Requirements 9.1,
    9.4). Copies of the mutable ``dict`` fields are taken so the assembled
    metadata cannot be aliased and later mutated (Requirement 9.3).

    This assembler assumes ``record`` has already passed
    :func:`mandatory_metadata_complete`; it does not re-validate completeness.
    """
    return ReproducibilityMetadata(
        model_version=record.model_version,
        dataset_name=record.dataset_name,
        dataset_version=record.dataset_version,
        dataset_checksum=record.dataset_checksum,
        feature_versions=dict(record.feature_versions),
        git_commit=record.git_commit,
        training_run_id=record.training_run_id,
        trained_at=record.trained_at,
        hyperparameters=dict(record.hyperparameters),
        aggregated_metrics=record.aggregated_metrics,
    )


def assemble_lineage(record: TrainedModelRecord) -> Lineage:
    """Project ``record`` into the read-only :class:`Lineage` view (Requirement 9.2).

    Exposes the full provenance chain — dataset version, feature versions, git
    commit, training run id, and training timestamp — for the lineage endpoint.
    ``model_name`` is the composite name inherited verbatim from the Training
    Pipeline (Key Design Decision 4). A copy of the feature-versions mapping is
    taken so the view cannot be aliased and later mutated (Requirement 9.3).
    """
    return Lineage(
        model_name=record.model_name,
        model_version=record.model_version,
        dataset_version=DatasetVersionRef(
            dataset_name=record.dataset_name,
            dataset_version=record.dataset_version,
            dataset_checksum=record.dataset_checksum,
        ),
        feature_versions=dict(record.feature_versions),
        git_commit=record.git_commit,
        training_run_id=record.training_run_id,
        trained_at=record.trained_at,
    )
