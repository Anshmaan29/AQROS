"""Property tests for the Lineage_Assembler (task 8.6).

Exercises ``domain/lineage.py`` in isolation: the pure
``mandatory_metadata_complete`` completeness gate (Property 6) and the
``assemble_reproducibility_metadata``/``assemble_lineage`` projections
(Property 10), against arbitrary ``TrainedModelRecord`` instances built via a
hypothesis composite strategy (design.md Section 4, Correctness Properties 6
and 10).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from aqros_model_registry.domain.lineage import (
    FIELD_ARTIFACT_CHECKSUM,
    FIELD_DATASET_CHECKSUM,
    FIELD_DATASET_NAME,
    FIELD_DATASET_VERSION,
    FIELD_FEATURE_VERSIONS,
    FIELD_METRICS,
    FIELD_TRAINING_RUN_ID,
    assemble_lineage,
    assemble_reproducibility_metadata,
    mandatory_metadata_complete,
)
from aqros_model_registry.domain.models import (
    AggregatedMetrics,
    PerFoldMetrics,
    TrainedModelRecord,
)

_AGGREGATED_METRICS = AggregatedMetrics(
    accuracy_mean=0.6,
    accuracy_std=0.05,
    precision_mean=0.6,
    precision_std=0.05,
    recall_mean=0.6,
    recall_std=0.05,
    f1_mean=0.6,
    f1_std=0.05,
    roc_auc_mean=None,
    roc_auc_std=None,
    evaluated_fold_count=1,
    roc_auc_evaluated_fold_count=0,
)

_PER_FOLD_METRICS = (
    PerFoldMetrics(
        fold=0,
        accuracy=0.6,
        precision=0.6,
        recall=0.6,
        f1_score=0.6,
        roc_auc=None,
        test_row_count=10,
    ),
)


def _build_record(
    *,
    dataset_name: str = "aapl_5d_direction",
    dataset_version: int = 1,
    dataset_checksum: str = "dataset-checksum",
    feature_versions: dict[str, int] | None = None,
    per_fold_metrics: tuple[PerFoldMetrics, ...] = _PER_FOLD_METRICS,
    training_run_id: int = 42,
    artifact_checksum: str = "artifact-checksum",
    git_commit: str | None = "a" * 40,
    model_name: str = "aapl_5d_direction__random_forest",
    model_type: str = "random_forest",
    model_version: int = 3,
    hyperparameters: dict[str, object] | None = None,
) -> TrainedModelRecord:
    """Build a fully-populated ``TrainedModelRecord`` with every mandatory field present."""
    return TrainedModelRecord(
        model_name=model_name,
        model_type=model_type,
        model_version=model_version,
        training_run_id=training_run_id,
        dataset_name=dataset_name,
        dataset_version=dataset_version,
        dataset_checksum=dataset_checksum,
        checksum_algorithm="sha256",
        artifact_checksum=artifact_checksum,
        feature_versions=(
            feature_versions if feature_versions is not None else {"close_return_5d": 1}
        ),
        per_fold_metrics=per_fold_metrics,
        aggregated_metrics=_AGGREGATED_METRICS,
        feature_importance={"close_return_5d": 0.5},
        git_commit=git_commit,
        trained_at=datetime(2024, 1, 1),
        hyperparameters=hyperparameters if hyperparameters is not None else {"n_estimators": 100},
    )


# Non-blank text: at least one non-whitespace character, so a "fully
# populated" generated record is never accidentally blank per
# ``mandatory_metadata_complete``'s own blank check (`str.strip()`).
_non_blank_text = st.text(min_size=1, max_size=30).filter(lambda s: s.strip() != "")


# A hypothesis composite strategy for arbitrary, fully-populated
# ``TrainedModelRecord`` instances (every mandatory field present, git_commit
# either None or an arbitrary string).
@st.composite
def _valid_trained_model_records(draw: st.DrawFn) -> TrainedModelRecord:
    dataset_name = draw(_non_blank_text)
    dataset_version = draw(st.integers(min_value=1, max_value=1_000))
    dataset_checksum = draw(_non_blank_text)
    feature_versions = draw(
        st.dictionaries(
            st.text(min_size=1, max_size=20),
            st.integers(min_value=1, max_value=100),
            min_size=1,
            max_size=5,
        )
    )
    fold_count = draw(st.integers(min_value=1, max_value=3))
    per_fold_metrics = tuple(
        PerFoldMetrics(
            fold=i,
            accuracy=0.5,
            precision=0.5,
            recall=0.5,
            f1_score=0.5,
            roc_auc=None,
            test_row_count=10,
        )
        for i in range(fold_count)
    )
    training_run_id = draw(st.integers(min_value=1, max_value=100_000))
    artifact_checksum = draw(_non_blank_text)
    git_commit = draw(st.one_of(st.none(), st.text(min_size=1, max_size=40)))
    model_type = draw(st.text(min_size=1, max_size=20).filter(lambda s: s.strip() != ""))
    model_version = draw(st.integers(min_value=1, max_value=1_000))
    hyperparameters: dict[str, object] = draw(
        st.dictionaries(st.text(min_size=1, max_size=10), st.integers(), max_size=3)
    )

    return _build_record(
        dataset_name=dataset_name,
        dataset_version=dataset_version,
        dataset_checksum=dataset_checksum,
        feature_versions=feature_versions,
        per_fold_metrics=per_fold_metrics,
        training_run_id=training_run_id,
        artifact_checksum=artifact_checksum,
        git_commit=git_commit,
        model_name=f"{dataset_name}__{model_type}",
        model_type=model_type,
        model_version=model_version,
        hyperparameters=hyperparameters,
    )


# --- Property 6: mandatory metadata completeness gates persistence ---------


# Feature: model-registry, Property 6: Mandatory metadata completeness gates persistence
# For any Trained_Model_Record missing a mandatory field (dataset name/version/
# checksum, feature versions, metrics, training run id, checksum), no
# Model_Version is persisted; an absent git commit alone is tolerated and
# recorded as absent.
# Validates: Requirements 4.3, 5.3, 6.1, 6.2, 6.3, 6.4
@settings(max_examples=100)
@given(record=_valid_trained_model_records())
def test_fully_populated_record_is_always_complete_regardless_of_git_commit(
    record: TrainedModelRecord,
) -> None:
    """A fully-populated record (every mandatory field present) is always
    ``ok=True`` with no missing fields, whether ``git_commit`` is ``None`` or
    an arbitrary string — an absent git commit is never a completeness
    failure (Requirement 6.3)."""
    result = mandatory_metadata_complete(record)
    assert result.ok is True
    assert result.missing_fields == ()
    assert FIELD_DATASET_NAME + "_never_present_marker" not in result.missing_fields
    # git_commit is never among the mandatory fields checked, regardless of value.
    assert "git_commit" not in result.missing_fields


# Feature: model-registry, Property 6: Mandatory metadata completeness gates persistence
# For any Trained_Model_Record missing a mandatory field (dataset name/version/
# checksum, feature versions, metrics, training run id, checksum), no
# Model_Version is persisted; an absent git commit alone is tolerated and
# recorded as absent.
# Validates: Requirements 4.3, 5.3, 6.1, 6.2, 6.3, 6.4
@settings(max_examples=100)
@given(record=_valid_trained_model_records(), git_commit=st.one_of(st.none(), st.text(max_size=40)))
def test_git_commit_value_never_affects_completeness(
    record: TrainedModelRecord, git_commit: str | None
) -> None:
    """Varying ``git_commit`` (including setting it to ``None``) on an
    otherwise fully-populated record never changes the completeness verdict
    and ``git_commit`` never appears in ``missing_fields`` (Requirement 6.3)."""
    varied = replace(record, git_commit=git_commit)
    result = mandatory_metadata_complete(varied)
    assert result.ok is True
    assert result.missing_fields == ()


# Feature: model-registry, Property 6: Mandatory metadata completeness gates persistence
# For any Trained_Model_Record missing a mandatory field (dataset name/version/
# checksum, feature versions, metrics, training run id, checksum), no
# Model_Version is persisted; an absent git commit alone is tolerated and
# recorded as absent.
# Validates: Requirements 4.3, 5.3, 6.1, 6.2, 6.3, 6.4
@settings(max_examples=100)
@given(
    record=_valid_trained_model_records(),
    blank_dataset_name=st.sampled_from(["", "   ", "\t\n"]),
)
def test_blank_dataset_name_is_flagged_as_missing(
    record: TrainedModelRecord, blank_dataset_name: str
) -> None:
    """A record with an empty or whitespace-only ``dataset_name`` is flagged
    as exactly ``[dataset_name]`` missing, with every other mandatory field
    (which remains fully populated) untouched."""
    invalid = replace(record, dataset_name=blank_dataset_name)
    result = mandatory_metadata_complete(invalid)
    assert result.ok is False
    assert result.missing_fields == (FIELD_DATASET_NAME,)


# Feature: model-registry, Property 6: Mandatory metadata completeness gates persistence
# For any Trained_Model_Record missing a mandatory field (dataset name/version/
# checksum, feature versions, metrics, training run id, checksum), no
# Model_Version is persisted; an absent git commit alone is tolerated and
# recorded as absent.
# Validates: Requirements 4.3, 5.3, 6.1, 6.2, 6.3, 6.4
@settings(max_examples=100)
@given(record=_valid_trained_model_records(), non_positive_version=st.integers(max_value=0))
def test_non_positive_dataset_version_is_flagged_as_missing(
    record: TrainedModelRecord, non_positive_version: int
) -> None:
    """A record with a non-positive ``dataset_version`` (zero or negative) is
    flagged as exactly ``[dataset_version]`` missing."""
    invalid = replace(record, dataset_version=non_positive_version)
    result = mandatory_metadata_complete(invalid)
    assert result.ok is False
    assert result.missing_fields == (FIELD_DATASET_VERSION,)


# Feature: model-registry, Property 6: Mandatory metadata completeness gates persistence
# For any Trained_Model_Record missing a mandatory field (dataset name/version/
# checksum, feature versions, metrics, training run id, checksum), no
# Model_Version is persisted; an absent git commit alone is tolerated and
# recorded as absent.
# Validates: Requirements 4.3, 5.3, 6.1, 6.2, 6.3, 6.4
@settings(max_examples=100)
@given(
    record=_valid_trained_model_records(),
    blank_checksum=st.sampled_from(["", "   ", "\t\n"]),
)
def test_blank_dataset_checksum_is_flagged_as_missing(
    record: TrainedModelRecord, blank_checksum: str
) -> None:
    """A record with an empty or whitespace-only ``dataset_checksum`` is
    flagged as exactly ``[dataset_checksum]`` missing."""
    invalid = replace(record, dataset_checksum=blank_checksum)
    result = mandatory_metadata_complete(invalid)
    assert result.ok is False
    assert result.missing_fields == (FIELD_DATASET_CHECKSUM,)


# Feature: model-registry, Property 6: Mandatory metadata completeness gates persistence
# For any Trained_Model_Record missing a mandatory field (dataset name/version/
# checksum, feature versions, metrics, training run id, checksum), no
# Model_Version is persisted; an absent git commit alone is tolerated and
# recorded as absent.
# Validates: Requirements 4.3, 5.3, 6.1, 6.2, 6.3, 6.4
@settings(max_examples=100)
@given(record=_valid_trained_model_records())
def test_empty_feature_versions_is_flagged_as_missing(record: TrainedModelRecord) -> None:
    """A record with an empty ``feature_versions`` mapping is flagged as
    exactly ``[feature_versions]`` missing."""
    invalid = replace(record, feature_versions={})
    result = mandatory_metadata_complete(invalid)
    assert result.ok is False
    assert result.missing_fields == (FIELD_FEATURE_VERSIONS,)


# Feature: model-registry, Property 6: Mandatory metadata completeness gates persistence
# For any Trained_Model_Record missing a mandatory field (dataset name/version/
# checksum, feature versions, metrics, training run id, checksum), no
# Model_Version is persisted; an absent git commit alone is tolerated and
# recorded as absent.
# Validates: Requirements 4.3, 5.3, 6.1, 6.2, 6.3, 6.4
@settings(max_examples=100)
@given(record=_valid_trained_model_records())
def test_empty_per_fold_metrics_is_flagged_as_missing(record: TrainedModelRecord) -> None:
    """A record with an empty ``per_fold_metrics`` sequence is flagged as
    exactly ``[metrics]`` missing."""
    invalid = replace(record, per_fold_metrics=())
    result = mandatory_metadata_complete(invalid)
    assert result.ok is False
    assert result.missing_fields == (FIELD_METRICS,)


# Feature: model-registry, Property 6: Mandatory metadata completeness gates persistence
# For any Trained_Model_Record missing a mandatory field (dataset name/version/
# checksum, feature versions, metrics, training run id, checksum), no
# Model_Version is persisted; an absent git commit alone is tolerated and
# recorded as absent.
# Validates: Requirements 4.3, 5.3, 6.1, 6.2, 6.3, 6.4
@settings(max_examples=100)
@given(record=_valid_trained_model_records(), non_positive_run_id=st.integers(max_value=0))
def test_non_positive_training_run_id_is_flagged_as_missing(
    record: TrainedModelRecord, non_positive_run_id: int
) -> None:
    """A record with a non-positive ``training_run_id`` (zero or negative) is
    flagged as exactly ``[training_run_id]`` missing."""
    invalid = replace(record, training_run_id=non_positive_run_id)
    result = mandatory_metadata_complete(invalid)
    assert result.ok is False
    assert result.missing_fields == (FIELD_TRAINING_RUN_ID,)


# Feature: model-registry, Property 6: Mandatory metadata completeness gates persistence
# For any Trained_Model_Record missing a mandatory field (dataset name/version/
# checksum, feature versions, metrics, training run id, checksum), no
# Model_Version is persisted; an absent git commit alone is tolerated and
# recorded as absent.
# Validates: Requirements 4.3, 5.3, 6.1, 6.2, 6.3, 6.4
@settings(max_examples=100)
@given(
    record=_valid_trained_model_records(),
    blank_artifact_checksum=st.sampled_from(["", "   ", "\t\n"]),
)
def test_blank_artifact_checksum_is_flagged_as_missing(
    record: TrainedModelRecord, blank_artifact_checksum: str
) -> None:
    """A record with an empty or whitespace-only ``artifact_checksum`` is
    flagged as exactly ``[artifact_checksum]`` missing."""
    invalid = replace(record, artifact_checksum=blank_artifact_checksum)
    result = mandatory_metadata_complete(invalid)
    assert result.ok is False
    assert result.missing_fields == (FIELD_ARTIFACT_CHECKSUM,)


# Feature: model-registry, Property 6: Mandatory metadata completeness gates persistence
# For any Trained_Model_Record missing a mandatory field (dataset name/version/
# checksum, feature versions, metrics, training run id, checksum), no
# Model_Version is persisted; an absent git commit alone is tolerated and
# recorded as absent.
# Validates: Requirements 4.3, 5.3, 6.1, 6.2, 6.3, 6.4
@settings(max_examples=100)
@given(record=_valid_trained_model_records())
def test_multiple_blanked_fields_are_all_flagged_as_missing(record: TrainedModelRecord) -> None:
    """Blanking several mandatory fields at once flags exactly that set as
    missing, in the module's canonical order — no false positives or
    negatives, and no interference between independently-checked fields."""
    invalid = replace(
        record,
        dataset_name="",
        feature_versions={},
        training_run_id=0,
    )
    result = mandatory_metadata_complete(invalid)
    assert result.ok is False
    assert result.missing_fields == (
        FIELD_DATASET_NAME,
        FIELD_FEATURE_VERSIONS,
        FIELD_TRAINING_RUN_ID,
    )


# --- Property 10: lineage and reproducibility are complete and immutable ---


# Feature: model-registry, Property 10: Lineage and reproducibility are complete and immutable
# For any Model_Version, the Reproducibility_Metadata contains version,
# dataset name/version/checksum, feature names and versions, git commit,
# training run id, timestamp, hyperparameters, and aggregated metrics, and
# none of it changes after registration.
# Validates: Requirements 9.1, 9.3, 9.4, 4.1, 5.1, 10.1, 10.3
@settings(max_examples=100)
@given(record=_valid_trained_model_records())
def test_reproducibility_metadata_carries_forward_every_source_field(
    record: TrainedModelRecord,
) -> None:
    """``assemble_reproducibility_metadata`` faithfully carries forward every
    source field: model version, dataset name/version/checksum, feature
    versions, git commit, training run id, trained_at timestamp,
    hyperparameters, and aggregated metrics."""
    metadata = assemble_reproducibility_metadata(record)

    assert metadata.model_version == record.model_version
    assert metadata.dataset_name == record.dataset_name
    assert metadata.dataset_version == record.dataset_version
    assert metadata.dataset_checksum == record.dataset_checksum
    assert metadata.feature_versions == record.feature_versions
    assert metadata.git_commit == record.git_commit
    assert metadata.training_run_id == record.training_run_id
    assert metadata.trained_at == record.trained_at
    assert metadata.hyperparameters == record.hyperparameters
    assert metadata.aggregated_metrics == record.aggregated_metrics


# Feature: model-registry, Property 10: Lineage and reproducibility are complete and immutable
# For any Model_Version, the Reproducibility_Metadata contains version,
# dataset name/version/checksum, feature names and versions, git commit,
# training run id, timestamp, hyperparameters, and aggregated metrics, and
# none of it changes after registration.
# Validates: Requirements 9.1, 9.3, 9.4, 4.1, 5.1, 10.1, 10.3
@settings(max_examples=100)
@given(record=_valid_trained_model_records())
def test_lineage_carries_forward_every_source_field(record: TrainedModelRecord) -> None:
    """``assemble_lineage`` faithfully carries forward every source field:
    model name/version, dataset version reference (name/version/checksum),
    feature versions, git commit, training run id, and trained_at timestamp."""
    lineage = assemble_lineage(record)

    assert lineage.model_name == record.model_name
    assert lineage.model_version == record.model_version
    assert lineage.dataset_version.dataset_name == record.dataset_name
    assert lineage.dataset_version.dataset_version == record.dataset_version
    assert lineage.dataset_version.dataset_checksum == record.dataset_checksum
    assert lineage.feature_versions == record.feature_versions
    assert lineage.git_commit == record.git_commit
    assert lineage.training_run_id == record.training_run_id
    assert lineage.trained_at == record.trained_at


# Feature: model-registry, Property 10: Lineage and reproducibility are complete and immutable
# For any Model_Version, the Reproducibility_Metadata contains version,
# dataset name/version/checksum, feature names and versions, git commit,
# training run id, timestamp, hyperparameters, and aggregated metrics, and
# none of it changes after registration.
# Validates: Requirements 9.1, 9.3, 9.4, 4.1, 5.1, 10.1, 10.3
@settings(max_examples=100)
@given(record=_valid_trained_model_records())
def test_mutating_source_feature_versions_after_assembly_does_not_affect_reproducibility_metadata(
    record: TrainedModelRecord,
) -> None:
    """A defensive-copy check: after assembling ``ReproducibilityMetadata``,
    mutating the *source* record's ``feature_versions`` dict in place must
    not affect the already-assembled metadata's ``feature_versions``
    (Requirement 9.3) — the assembler must have taken a copy, not an alias."""
    metadata = assemble_reproducibility_metadata(record)
    original_feature_versions = dict(record.feature_versions)

    record.feature_versions["mutated-after-assembly"] = 999

    assert metadata.feature_versions == original_feature_versions
    assert "mutated-after-assembly" not in metadata.feature_versions


# Feature: model-registry, Property 10: Lineage and reproducibility are complete and immutable
# For any Model_Version, the Reproducibility_Metadata contains version,
# dataset name/version/checksum, feature names and versions, git commit,
# training run id, timestamp, hyperparameters, and aggregated metrics, and
# none of it changes after registration.
# Validates: Requirements 9.1, 9.3, 9.4, 4.1, 5.1, 10.1, 10.3
@settings(max_examples=100)
@given(record=_valid_trained_model_records())
def test_mutating_source_feature_versions_after_assembly_does_not_affect_lineage(
    record: TrainedModelRecord,
) -> None:
    """The same defensive-copy check for :func:`assemble_lineage`: mutating
    the source record's ``feature_versions`` dict after assembly must not
    affect the already-assembled ``Lineage``'s ``feature_versions``."""
    lineage = assemble_lineage(record)
    original_feature_versions = dict(record.feature_versions)

    record.feature_versions["mutated-after-assembly"] = 999

    assert lineage.feature_versions == original_feature_versions
    assert "mutated-after-assembly" not in lineage.feature_versions


# Feature: model-registry, Property 10: Lineage and reproducibility are complete and immutable
# For any Model_Version, the Reproducibility_Metadata contains version,
# dataset name/version/checksum, feature names and versions, git commit,
# training run id, timestamp, hyperparameters, and aggregated metrics, and
# none of it changes after registration.
# Validates: Requirements 9.1, 9.3, 9.4, 4.1, 5.1, 10.1, 10.3
@settings(max_examples=100)
@given(record=_valid_trained_model_records())
def test_mutating_returned_reproducibility_metadata_dicts_does_not_affect_source_record(
    record: TrainedModelRecord,
) -> None:
    """The inverse defensive-copy direction: mutating the ``feature_versions``
    dict on the *assembled* ``ReproducibilityMetadata`` must not alter the
    source record's ``feature_versions`` — confirming the assembler copies
    rather than sharing the same dict object in either direction."""
    original_feature_versions = dict(record.feature_versions)
    metadata = assemble_reproducibility_metadata(record)

    metadata.feature_versions["mutated-after-assembly"] = 999

    assert record.feature_versions == original_feature_versions
    assert "mutated-after-assembly" not in record.feature_versions


# Concrete example: absent git commit is tolerated and recorded as absent
# both in completeness checking and in assembly.
def test_absent_git_commit_is_tolerated_and_recorded_as_none() -> None:
    record = _build_record(git_commit=None)

    result = mandatory_metadata_complete(record)
    assert result.ok is True
    assert result.missing_fields == ()

    metadata = assemble_reproducibility_metadata(record)
    assert metadata.git_commit is None

    lineage = assemble_lineage(record)
    assert lineage.git_commit is None


def test_present_git_commit_is_recorded_verbatim() -> None:
    record = _build_record(git_commit="deadbeef" * 5)

    metadata = assemble_reproducibility_metadata(record)
    assert metadata.git_commit == "deadbeef" * 5

    lineage = assemble_lineage(record)
    assert lineage.git_commit == "deadbeef" * 5
