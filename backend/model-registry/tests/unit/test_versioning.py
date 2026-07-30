"""Property tests for version identity and immutability (task 8.5).

Exercises ``ModelVersion`` and ``FakeModelVersionRepository`` in isolation:
version uniqueness/monotonic tracking per ``Registered_Model``, and the
guarantee that only ``lifecycle_state`` and ``validation_evidence`` are ever
mutated after registration — every other (identity) field is written once
(design.md Section 4, Requirements 3.2, 3.3, 3.4, 3.5, 22.2, 22.3).

``ModelVersion`` itself is a frozen, ``slots=True`` dataclass, so attempting
to set any attribute on an already-constructed instance raises
``dataclasses.FrozenInstanceError`` at the language level — Python enforces
this immutability directly, with no ``setattr`` path available to bypass it.
"""

from __future__ import annotations

import dataclasses
from dataclasses import replace
from datetime import datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aqros_model_registry.domain.models import (
    AggregatedMetrics,
    ApprovalState,
    DatasetVersionRef,
    LifecycleState,
    MetricsRecord,
    ModelVersion,
    ReproducibilityMetadata,
    ValidationEvidence,
)

from .fakes import FakeModelVersionRepository

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

_MODEL_NAME = "aapl_5d_direction__random_forest"
_DECOY_MODEL_NAME = "msft_5d_direction__xgboost"


def _build_model_version(
    model_name: str,
    version: int,
    *,
    lifecycle_state: LifecycleState = LifecycleState.REGISTERED,
    approval_state: ApprovalState = ApprovalState.NOT_REQUIRED,
    validation_evidence: ValidationEvidence | None = None,
) -> ModelVersion:
    """Build a fully-populated ``ModelVersion`` for a given (model_name, version)."""
    reproducibility_metadata = ReproducibilityMetadata(
        model_version=version,
        dataset_name="aapl_5d_direction",
        dataset_version=1,
        dataset_checksum="dataset-checksum",
        feature_versions={"close_return_5d": 1},
        git_commit="a" * 40,
        training_run_id=version * 10,
        trained_at=datetime(2024, 1, 1),
        hyperparameters={"n_estimators": 100},
        aggregated_metrics=_AGGREGATED_METRICS,
    )
    return ModelVersion(
        model_name=model_name,
        model_type="random_forest",
        version=version,
        training_run_id=version * 10,
        dataset_version=DatasetVersionRef(
            dataset_name="aapl_5d_direction",
            dataset_version=1,
            dataset_checksum="dataset-checksum",
        ),
        feature_versions={"close_return_5d": 1},
        metrics=MetricsRecord(per_fold=(), aggregated=_AGGREGATED_METRICS, feature_importance={}),
        artifact_path=f"local://{model_name}/v{version}/model.joblib",
        artifact_checksum=f"checksum-{version}",
        checksum_algorithm="sha256",
        git_commit="a" * 40,
        reproducibility_metadata=reproducibility_metadata,
        lifecycle_state=lifecycle_state,
        approval_state=approval_state,
        validation_evidence=validation_evidence,
        created_at=datetime(2024, 1, 1),
    )


# --- Property 5: version identity is unique and immutable -------------------


# Feature: model-registry, Property 5: Version identity is unique and immutable
# For all Model_Versions of a Registered_Model, versions are unique, and no
# identity field (version, checksum, dataset version, feature versions,
# metrics, git commit, training run id, artifact reference) is ever modified
# after registration.
# Validates: Requirements 3.2, 3.3, 3.4, 3.5, 22.2, 22.3
@settings(max_examples=100)
@given(
    versions=st.lists(
        st.integers(min_value=1, max_value=1_000), unique=True, min_size=1, max_size=8
    )
)
async def test_repository_tracks_unique_versions_per_model_name(versions: list[int]) -> None:
    """For an arbitrary set of distinct versions registered under the same
    ``model_name``, ``get_latest_version`` reflects the max, ``get`` returns
    the correct row for every version, and ``list`` returns exactly the set
    created for that ``model_name`` — a decoy version under a different
    ``model_name`` proves the repository correctly scopes by name rather
    than tracking a single global version space."""
    repo = FakeModelVersionRepository()

    # A decoy under a different Registered_Model must never leak into this
    # model_name's latest-version, get, or list results.
    await repo.create_model_version(_build_model_version(_DECOY_MODEL_NAME, 999))

    created: dict[int, ModelVersion] = {}
    for version in versions:
        created[version] = await repo.create_model_version(
            _build_model_version(_MODEL_NAME, version)
        )

    assert await repo.get_latest_version(_MODEL_NAME) == max(versions)

    for version in versions:
        fetched = await repo.get(_MODEL_NAME, version)
        assert fetched is not None
        assert fetched.version == version
        assert fetched == created[version]

    listed = await repo.list(model_name=_MODEL_NAME)
    assert {mv.version for mv in listed} == set(versions)
    assert len(listed) == len(versions)

    # The decoy is untouched and does not appear under this model_name.
    assert await repo.get_latest_version(_DECOY_MODEL_NAME) == 999


# Feature: model-registry, Property 5: Version identity is unique and immutable
# For all Model_Versions of a Registered_Model, versions are unique, and no
# identity field (version, checksum, dataset version, feature versions,
# metrics, git commit, training run id, artifact reference) is ever modified
# after registration.
# Validates: Requirements 3.2, 3.3, 3.4, 3.5, 22.2, 22.3
@settings(max_examples=100)
@given(
    version=st.integers(min_value=1, max_value=1_000),
    new_state=st.sampled_from(list(LifecycleState)),
)
async def test_set_lifecycle_state_changes_only_lifecycle_state(
    version: int, new_state: LifecycleState
) -> None:
    """``set_lifecycle_state`` must update only the ``lifecycle_state`` column;
    every identity field (and ``approval_state``/``validation_evidence``) must
    remain byte-identical. Comparing against ``replace(before,
    lifecycle_state=new_state)`` asserts equality across *every* field at
    once, so any unintended mutation of an identity field would fail this
    check."""
    repo = FakeModelVersionRepository()
    before = await repo.create_model_version(_build_model_version(_MODEL_NAME, version))

    after = await repo.set_lifecycle_state(_MODEL_NAME, version, new_state)

    assert after == replace(before, lifecycle_state=new_state)
    assert after.lifecycle_state == new_state


# Feature: model-registry, Property 5: Version identity is unique and immutable
# For all Model_Versions of a Registered_Model, versions are unique, and no
# identity field (version, checksum, dataset version, feature versions,
# metrics, git commit, training run id, artifact reference) is ever modified
# after registration.
# Validates: Requirements 3.2, 3.3, 3.4, 3.5, 22.2, 22.3
@settings(max_examples=100)
@given(
    version=st.integers(min_value=1, max_value=1_000),
    kind=st.text(min_size=1, max_size=20),
    reference=st.text(min_size=1, max_size=40),
)
async def test_attach_validation_evidence_changes_only_validation_evidence(
    version: int, kind: str, reference: str
) -> None:
    """``attach_validation_evidence`` must update only the
    ``validation_evidence`` column; every identity field (and
    ``lifecycle_state``/``approval_state``) must remain byte-identical."""
    repo = FakeModelVersionRepository()
    before = await repo.create_model_version(_build_model_version(_MODEL_NAME, version))
    evidence = ValidationEvidence(kind=kind, reference=reference, attached_at=datetime(2024, 1, 1))

    after = await repo.attach_validation_evidence(_MODEL_NAME, version, evidence)

    assert after == replace(before, validation_evidence=evidence)
    assert after.validation_evidence == evidence


# --- Language-level immutability of identity fields -------------------------


# Feature: model-registry, Property 5: Version identity is unique and immutable
# For all Model_Versions of a Registered_Model, versions are unique, and no
# identity field (version, checksum, dataset version, feature versions,
# metrics, git commit, training run id, artifact reference) is ever modified
# after registration.
# Validates: Requirements 3.2, 3.3, 3.4, 3.5, 22.2, 22.3
@settings(max_examples=100)
@given(
    field_name=st.sampled_from(
        [
            "model_name",
            "model_type",
            "version",
            "training_run_id",
            "dataset_version",
            "feature_versions",
            "metrics",
            "artifact_path",
            "artifact_checksum",
            "checksum_algorithm",
            "git_commit",
            "reproducibility_metadata",
            "created_at",
        ]
    )
)
def test_model_version_identity_fields_are_frozen_at_the_language_level(
    field_name: str,
) -> None:
    """``ModelVersion`` is a ``frozen=True, slots=True`` dataclass: Python
    itself refuses any attribute assignment on a constructed instance, for
    every identity field, by raising ``dataclasses.FrozenInstanceError`` —
    there is no ``setattr``/mutation path available to the application at
    all, independent of any repository-layer discipline."""
    model_version = _build_model_version(_MODEL_NAME, 1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(model_version, field_name, getattr(model_version, field_name))


def test_model_version_mutable_fields_are_also_frozen_at_the_language_level() -> None:
    """Even the two fields the *repository* is permitted to update
    (``lifecycle_state``, ``approval_state``) cannot be mutated in place on an
    existing ``ModelVersion`` instance — the repository always produces a new
    instance via ``dataclasses.replace`` rather than mutating one, and the
    dataclass itself forbids in-place mutation regardless."""
    model_version = _build_model_version(_MODEL_NAME, 1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        model_version.lifecycle_state = LifecycleState.VALIDATED  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        model_version.approval_state = ApprovalState.PENDING  # type: ignore[misc]


# --- Duplicate (model_name, version): a repository/DB concern, not domain --


async def test_fake_repository_does_not_itself_reject_a_duplicate_version() -> None:
    """``FakeModelVersionRepository.create_model_version`` simply appends —
    it does not itself enforce ``(model_name, version)`` uniqueness. That
    invariant is instead a persistence-layer concern, enforced at the
    database level by the ``UniqueConstraint(registered_model_id, version)``
    on ``model_versions`` (design.md Section 7; task 3.2) and verified
    against a real Postgres database in
    ``tests/integration/test_repository.py`` (task 9.3). This test documents
    the in-memory fake's actual behavior so it is not mistaken for the
    uniqueness backstop itself."""
    repo = FakeModelVersionRepository()

    first = _build_model_version(_MODEL_NAME, 1)
    second = _build_model_version(_MODEL_NAME, 1)

    stored_first = await repo.create_model_version(first)
    stored_second = await repo.create_model_version(second)

    assert stored_first.version == stored_second.version == 1
    assert stored_first.id != stored_second.id
