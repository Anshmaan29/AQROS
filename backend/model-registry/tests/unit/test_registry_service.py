"""Property tests for ``ModelRegistryService`` / ``RegistryQueryService`` (task 8.7).

Exercises ``domain/services.py`` end-to-end against fakes for every port
(no real HTTP, filesystem, or database access — Requirement 26.1): the
Training-Pipeline-only ingestion boundary, the no-partial-version-on-failure
guarantee, idempotent registration, the initial lifecycle/approval state,
the no-auto-promotion invariant, the single-PRODUCTION invariant with
incumbent demotion, rollback-eligibility gating, and the append-only nature
of both promotion history and the audit trail.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aqros_model_registry.domain.integrity import compute_checksum
from aqros_model_registry.domain.models import (
    AggregatedMetrics,
    ApprovalState,
    LifecycleState,
    PerFoldMetrics,
    PrincipalKind,
    PromotionRequest,
    TrainedModelRecord,
)
from aqros_model_registry.domain.ports import (
    TrainedModelNotFoundError,
    UpstreamSourceError,
)
from aqros_model_registry.domain.services import (
    ModelRegistryService,
    ModelVersionNotFoundError,
    NeverInProductionError,
    RegistryQueryService,
)
from tests.unit.fakes import (
    FakeArtifactSigner,
    FakeArtifactStore,
    FakeAuditRepository,
    FakeClock,
    FakeModelVersionRepository,
    FakePromotionRepository,
    FakeTrainingPipelineClient,
)

_MODEL_NAME = "aapl_5d_direction__random_forest"

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
        fold=1,
        accuracy=0.6,
        precision=0.6,
        recall=0.6,
        f1_score=0.6,
        roc_auc=None,
        test_row_count=100,
    ),
)


def _build_artifact_bytes(model_name: str, version: int) -> bytes:
    return f"artifact-bytes-{model_name}-{version}".encode()


def _build_record(
    model_name: str,
    version: int,
    *,
    training_run_id: int | None = None,
) -> TrainedModelRecord:
    """Build a fully-populated, mandatory-metadata-complete ``TrainedModelRecord``.

    The ``artifact_checksum`` is computed from the exact bytes
    ``_build_artifact_bytes`` returns for the same ``(model_name, version)``,
    so a matching record/artifact pair always passes the checksum gate on
    ingestion (Requirement 7.1) — these property tests exercise
    ``ModelRegistryService`` orchestration, not the checksum gate itself
    (covered by ``test_integrity.py``, Property 7/8).
    """
    checksum = compute_checksum(_build_artifact_bytes(model_name, version), "sha256")
    return TrainedModelRecord(
        model_name=model_name,
        model_type="random_forest",
        model_version=version,
        training_run_id=training_run_id if training_run_id is not None else version * 10,
        dataset_name="aapl_5d_direction",
        dataset_version=1,
        dataset_checksum="dataset-checksum",
        checksum_algorithm="sha256",
        artifact_checksum=checksum,
        feature_versions={"close_return_5d": 1},
        per_fold_metrics=_PER_FOLD_METRICS,
        aggregated_metrics=_AGGREGATED_METRICS,
        feature_importance={"close_return_5d": 1.0},
        git_commit="a" * 40,
        trained_at=datetime(2024, 1, 1),
        hyperparameters={"n_estimators": 100},
    )


class _Wiring:
    """A freshly-wired ``ModelRegistryService`` + ``RegistryQueryService`` pair.

    Every port is backed by an in-memory fake so each test exercises the
    domain orchestration logic alone (Requirement 26.1). Built fresh for every
    test to avoid any state bleeding across examples.
    """

    def __init__(
        self,
        *,
        records: dict[tuple[str, int], TrainedModelRecord] | None = None,
        artifacts: dict[tuple[str, int], bytes] | None = None,
        missing: set[tuple[str, int]] | None = None,
        upstream_error: set[tuple[str, int]] | None = None,
    ) -> None:
        self.training_pipeline = FakeTrainingPipelineClient(
            records=records,
            artifacts=artifacts,
            missing=missing,
            upstream_error=upstream_error,
        )
        self.artifact_store = FakeArtifactStore()
        self.model_versions = FakeModelVersionRepository()
        self.promotions = FakePromotionRepository()
        self.audit = FakeAuditRepository()
        self.signer = FakeArtifactSigner()
        self.clock = FakeClock()

        self.service = ModelRegistryService(
            training_pipeline_client=self.training_pipeline,
            artifact_store=self.artifact_store,
            model_version_repository=self.model_versions,
            promotion_repository=self.promotions,
            audit_repository=self.audit,
            artifact_signer=self.signer,
            clock=self.clock,
        )
        self.query = RegistryQueryService(
            model_version_repository=self.model_versions,
            promotion_repository=self.promotions,
            audit_repository=self.audit,
            artifact_store=self.artifact_store,
            artifact_signer=self.signer,
        )


def _wire(
    *,
    records: dict[tuple[str, int], TrainedModelRecord] | None = None,
    artifacts: dict[tuple[str, int], bytes] | None = None,
    missing: set[tuple[str, int]] | None = None,
    upstream_error: set[tuple[str, int]] | None = None,
) -> _Wiring:
    """Construct a fresh, fully-wired ``_Wiring`` bundle for one test."""
    return _Wiring(
        records=records, artifacts=artifacts, missing=missing, upstream_error=upstream_error
    )


async def _register_simple(
    wiring: _Wiring, model_name: str, version: int, *, training_run_id: int | None = None
) -> None:
    """Register ``(model_name, version)`` after preloading a matching record/artifact."""
    record = _build_record(model_name, version, training_run_id=training_run_id)
    wiring.training_pipeline._records[(model_name, version)] = record
    wiring.training_pipeline._artifacts[(model_name, version)] = _build_artifact_bytes(
        model_name, version
    )
    await wiring.service.register(
        model_name=model_name,
        version=version,
        training_run_id=record.training_run_id,
        actor="researcher",
        correlation_id=f"corr-{model_name}-{version}",
    )


async def _promote_to_production(
    wiring: _Wiring,
    model_name: str,
    version: int,
    *,
    approvers: tuple[str, str] = ("alice", "bob"),
    requester: str = "requester",
) -> None:
    """Drive a registered version all the way to PRODUCTION.

    Walks REGISTERED -> VALIDATED (evidence) -> STAGING (single approval)
    -> PRODUCTION (four-eyes, two distinct human approvers), using
    ``approve``'s existing gate logic for every step.
    """
    from aqros_model_registry.domain.models import ValidationEvidence

    await wiring.service.request_transition(
        model_name=model_name,
        version=version,
        to_state=LifecycleState.VALIDATED,
        requester=requester,
        justification="validated",
        correlation_id="corr-validate",
        validation_evidence=ValidationEvidence(
            kind="backtest_report", reference="dossier-1", attached_at=datetime(2024, 1, 1)
        ),
    )

    staging_result = await wiring.service.request_transition(
        model_name=model_name,
        version=version,
        to_state=LifecycleState.STAGING,
        requester=requester,
        justification="stage it",
        correlation_id="corr-stage",
    )
    assert isinstance(staging_result, PromotionRequest)
    await wiring.service.approve(
        request_id=staging_result.id,  # type: ignore[arg-type]
        approver="single-approver",
        approver_kind=PrincipalKind.HUMAN,
        correlation_id="corr-stage-approve",
    )

    production_result = await wiring.service.request_transition(
        model_name=model_name,
        version=version,
        to_state=LifecycleState.PRODUCTION,
        requester=requester,
        justification="promote to production",
        correlation_id="corr-promote",
    )
    assert isinstance(production_result, PromotionRequest)
    request_id = production_result.id
    assert request_id is not None
    for approver in approvers:
        await wiring.service.approve(
            request_id=request_id,
            approver=approver,
            approver_kind=PrincipalKind.HUMAN,
            correlation_id="corr-promote-approve",
        )


# --- Property 1: ingestion via Training Pipeline REST only ------------------


# Feature: model-registry, Property 1: Ingestion is via the Training Pipeline REST API only; downstream never queries it
# For any registration, the Model_Version's record and artifact are obtained
# solely through the Training Pipeline's REST API, and every downstream read
# (metadata, metrics, lineage, artifact) is served by the Registry without
# contacting the Training Pipeline.
# Validates: Requirements 1.2, 1.4, 1.5, 28.7
@settings(max_examples=100)
@given(version=st.integers(min_value=1, max_value=1_000))
async def test_register_only_calls_training_pipeline_client_and_reads_never_do(
    version: int,
) -> None:
    """Registration ingests solely via the ``TrainingPipelineClient`` port (its
    ``.calls`` log records exactly ``get_trained_model_record`` and
    ``download_artifact``, never anything else), and the resulting
    ``ModelVersion``'s fields match what the fake supplied. Once registered,
    every downstream read (metadata, metrics, lineage, artifact) succeeds
    without any further call reaching the ``TrainingPipelineClient`` port —
    the Training Pipeline is contacted only at registration time."""
    wiring = _wire()
    record = _build_record(_MODEL_NAME, version)
    wiring.training_pipeline._records[(_MODEL_NAME, version)] = record
    artifact_bytes = _build_artifact_bytes(_MODEL_NAME, version)
    wiring.training_pipeline._artifacts[(_MODEL_NAME, version)] = artifact_bytes

    model_version = await wiring.service.register(
        model_name=_MODEL_NAME,
        version=version,
        training_run_id=record.training_run_id,
        actor="researcher",
        correlation_id="corr-1",
    )

    # Only the two Training_Pipeline_Client methods were ever invoked, and
    # nothing else was called through that port (Req 1.2).
    assert wiring.training_pipeline.calls == ["get_trained_model_record", "download_artifact"]

    # The resulting ModelVersion's fields match what the fake supplied.
    assert model_version.model_name == record.model_name
    assert model_version.version == record.model_version
    assert model_version.training_run_id == record.training_run_id
    assert model_version.artifact_checksum == record.artifact_checksum
    assert model_version.dataset_version.dataset_name == record.dataset_name
    assert model_version.feature_versions == record.feature_versions

    calls_after_registration = len(wiring.training_pipeline.calls)

    # Every downstream read is served by the Registry without contacting the
    # Training Pipeline again (Req 1.4, 28.7).
    fetched = await wiring.query.get_model_version(_MODEL_NAME, version)
    assert fetched == model_version

    metrics = await wiring.query.get_metrics(_MODEL_NAME, version)
    assert metrics == model_version.metrics

    lineage = await wiring.query.get_lineage(_MODEL_NAME, version)
    assert lineage.model_name == _MODEL_NAME
    assert lineage.dataset_version == model_version.dataset_version

    artifact = await wiring.query.get_artifact(_MODEL_NAME, version)
    assert artifact == artifact_bytes

    # No additional calls were made to the Training_Pipeline_Client port by any
    # of the reads above.
    assert len(wiring.training_pipeline.calls) == calls_after_registration


# --- Property 2: upstream failure yields no partial Model_Version -----------


# Feature: model-registry, Property 2: Upstream failure yields no partial Model_Version
# For any Training Pipeline error, unreachability, or 404 during registration,
# no Model_Version is persisted and a failure reason is recorded.
# Validates: Requirements 1.6, 2.3, 20.3
@settings(max_examples=100)
@given(
    version=st.integers(min_value=1, max_value=1_000),
    failure_mode=st.sampled_from(["not_found", "upstream_error"]),
)
async def test_upstream_failure_during_registration_persists_nothing(
    version: int, failure_mode: str
) -> None:
    """Whether the Training Pipeline reports the trained model as missing
    (404 -> ``TrainedModelNotFoundError``) or is otherwise unreachable/erroring
    (``UpstreamSourceError``), ``register`` propagates the exception and the
    ``ModelVersionRepository`` remains completely empty — no partial
    ``ModelVersion`` is ever persisted."""
    key = (_MODEL_NAME, version)
    missing = {key} if failure_mode == "not_found" else set()
    upstream_error = {key} if failure_mode == "upstream_error" else set()
    wiring = _wire(missing=missing, upstream_error=upstream_error)

    expected_exception = (
        TrainedModelNotFoundError if failure_mode == "not_found" else UpstreamSourceError
    )
    with pytest.raises(expected_exception):
        await wiring.service.register(
            model_name=_MODEL_NAME,
            version=version,
            training_run_id=version * 10,
            actor="researcher",
            correlation_id="corr-fail",
        )

    assert await wiring.model_versions.list() == []
    assert await wiring.model_versions.get(_MODEL_NAME, version) is None


# --- Property 3: registration is idempotent ----------------------------------


# Feature: model-registry, Property 3: Registration is idempotent on (model_name, version, training_run_id)
# For any repeated registration of the same reference, exactly one
# Model_Version exists and the repeated call returns the existing record.
# Validates: Requirements 2.4
@settings(max_examples=100)
@given(
    version=st.integers(min_value=1, max_value=1_000),
    repeat_count=st.integers(min_value=2, max_value=5),
)
async def test_repeated_registration_is_idempotent(version: int, repeat_count: int) -> None:
    """Calling ``register`` repeatedly with the same ``(model_name, version,
    training_run_id)`` never creates a duplicate: exactly one ``ModelVersion``
    ever exists, and every call after the first returns that same record
    (same id) without invoking the Training_Pipeline_Client again."""
    wiring = _wire()
    record = _build_record(_MODEL_NAME, version)
    wiring.training_pipeline._records[(_MODEL_NAME, version)] = record
    wiring.training_pipeline._artifacts[(_MODEL_NAME, version)] = _build_artifact_bytes(
        _MODEL_NAME, version
    )

    results = []
    for _ in range(repeat_count):
        results.append(
            await wiring.service.register(
                model_name=_MODEL_NAME,
                version=version,
                training_run_id=record.training_run_id,
                actor="researcher",
                correlation_id="corr-idempotent",
            )
        )

    first = results[0]
    for result in results[1:]:
        assert result == first
        assert result.id == first.id

    all_versions = await wiring.model_versions.list(model_name=_MODEL_NAME)
    assert len(all_versions) == 1

    # Only the first registration ever contacted the Training Pipeline.
    assert wiring.training_pipeline.calls == ["get_trained_model_record", "download_artifact"]


# --- Property 4: new Model_Version starts REGISTERED / NOT_REQUIRED ---------


# Feature: model-registry, Property 4: A new Model_Version starts in REGISTERED / NOT_REQUIRED
# For any successful first registration, the Model_Version's lifecycle state
# is REGISTERED and its approval state is NOT_REQUIRED.
# Validates: Requirements 2.5
@settings(max_examples=100)
@given(version=st.integers(min_value=1, max_value=1_000))
async def test_new_registration_starts_registered_and_not_required(version: int) -> None:
    wiring = _wire()
    record = _build_record(_MODEL_NAME, version)
    wiring.training_pipeline._records[(_MODEL_NAME, version)] = record
    wiring.training_pipeline._artifacts[(_MODEL_NAME, version)] = _build_artifact_bytes(
        _MODEL_NAME, version
    )

    model_version = await wiring.service.register(
        model_name=_MODEL_NAME,
        version=version,
        training_run_id=record.training_run_id,
        actor="researcher",
        correlation_id="corr-initial-state",
    )

    assert model_version.lifecycle_state == LifecycleState.REGISTERED
    assert model_version.approval_state == ApprovalState.NOT_REQUIRED


# --- Property 17: no automatic promotion to PRODUCTION ----------------------


# Feature: model-registry, Property 17: No automatic promotion to PRODUCTION
# For any sequence of events lacking an explicit approved Promotion_Request
# targeting PRODUCTION, no Model_Version ever becomes PRODUCTION.
# Validates: Requirements 13.5
@settings(max_examples=100)
@given(version=st.integers(min_value=1, max_value=1_000))
async def test_request_transition_to_production_never_returns_a_production_model_version(
    version: int,
) -> None:
    """A request to transition a STAGING ``ModelVersion`` to PRODUCTION always
    goes through the FOUR_EYES gate: ``request_transition`` returns a
    ``PENDING`` ``PromotionRequest``, never a ``ModelVersion`` whose
    lifecycle state is already PRODUCTION — there is no ungated path that
    applies a PRODUCTION transition directly."""
    wiring = _wire()
    await _register_simple(wiring, _MODEL_NAME, version)
    from aqros_model_registry.domain.models import ValidationEvidence

    await wiring.service.request_transition(
        model_name=_MODEL_NAME,
        version=version,
        to_state=LifecycleState.VALIDATED,
        requester="requester",
        justification="validated",
        correlation_id="corr-validate",
        validation_evidence=ValidationEvidence(
            kind="backtest_report", reference="dossier-1", attached_at=datetime(2024, 1, 1)
        ),
    )
    staging_request = await wiring.service.request_transition(
        model_name=_MODEL_NAME,
        version=version,
        to_state=LifecycleState.STAGING,
        requester="requester",
        justification="stage it",
        correlation_id="corr-stage",
    )
    assert isinstance(staging_request, PromotionRequest)
    await wiring.service.approve(
        request_id=staging_request.id,  # type: ignore[arg-type]
        approver="single-approver",
        approver_kind=PrincipalKind.HUMAN,
        correlation_id="corr-stage-approve",
    )

    result = await wiring.service.request_transition(
        model_name=_MODEL_NAME,
        version=version,
        to_state=LifecycleState.PRODUCTION,
        requester="requester",
        justification="promote to production",
        correlation_id="corr-promote",
    )

    assert isinstance(result, PromotionRequest)
    assert result.approval_state == ApprovalState.PENDING

    stored = await wiring.model_versions.get(_MODEL_NAME, version)
    assert stored is not None
    assert stored.lifecycle_state != LifecycleState.PRODUCTION


# --- Property 18: single-PRODUCTION invariant with incumbent demotion -------


# Feature: model-registry, Property 18: Single-PRODUCTION invariant with incumbent demotion
# For any Registered_Model at any time, at most one Model_Version is
# PRODUCTION; promoting a new version demotes the prior incumbent to
# DEPRECATED atomically, even under concurrent promotion attempts.
# Validates: Requirements 16.1, 16.2, 23.2, 23.3
#
# Two full four-eyes promotion chains (each requiring evidence + two approval
# steps + a final four-eyes approval) make this scenario expensive per
# example; 25 examples across two version pairs is sufficient to exercise the
# invariant without an excessive runtime, while every other property test in
# this module keeps the full 100-example minimum.
@settings(max_examples=25)
@given(
    version_a=st.integers(min_value=1, max_value=100),
    version_b=st.integers(min_value=101, max_value=200),
)
async def test_promoting_a_second_version_demotes_the_incumbent_and_resolves_uniquely(
    version_a: int, version_b: int
) -> None:
    """Promoting ``version_a`` to PRODUCTION and then promoting ``version_b``
    (of the same model) to PRODUCTION must demote ``version_a`` to DEPRECATED
    and leave ``version_b`` as the sole PRODUCTION version; ``resolve_production``
    must resolve exactly ``version_b``."""
    wiring = _wire()
    await _register_simple(wiring, _MODEL_NAME, version_a)
    await _register_simple(wiring, _MODEL_NAME, version_b)

    await _promote_to_production(wiring, _MODEL_NAME, version_a)
    version_a_after_first = await wiring.model_versions.get(_MODEL_NAME, version_a)
    assert version_a_after_first is not None
    assert version_a_after_first.lifecycle_state == LifecycleState.PRODUCTION

    await _promote_to_production(wiring, _MODEL_NAME, version_b, approvers=("carol", "dave"))

    version_a_after_second = await wiring.model_versions.get(_MODEL_NAME, version_a)
    version_b_after_second = await wiring.model_versions.get(_MODEL_NAME, version_b)
    assert version_a_after_second is not None
    assert version_b_after_second is not None
    assert version_a_after_second.lifecycle_state == LifecycleState.DEPRECATED
    assert version_b_after_second.lifecycle_state == LifecycleState.PRODUCTION

    resolved = await wiring.query.resolve_production(_MODEL_NAME)
    assert resolved is not None
    assert resolved.version == version_b

    all_versions = await wiring.model_versions.list(model_name=_MODEL_NAME)
    production_versions = [
        mv for mv in all_versions if mv.lifecycle_state == LifecycleState.PRODUCTION
    ]
    assert len(production_versions) == 1
    assert production_versions[0].version == version_b


# --- Property 19: production resolution -------------------------------------


# Feature: model-registry, Property 19: Production resolution
# For any Registered_Model, the production endpoint returns the unique
# PRODUCTION version, or reports none exists if there is no PRODUCTION
# version.
# Validates: Requirements 16.3, 16.4
@settings(max_examples=100)
@given(version=st.integers(min_value=1, max_value=1_000))
async def test_resolve_production_reports_none_until_a_version_is_promoted(
    version: int,
) -> None:
    """Before any promotion, ``resolve_production`` reports that no
    Production_Model exists for the Registered_Model (Requirement 16.4) —
    both for a model name with no registered versions at all and for one
    whose only registered version has not reached PRODUCTION. Once that
    version is promoted all the way to PRODUCTION, ``resolve_production``
    resolves exactly that version (Requirement 16.3)."""
    wiring = _wire()

    # No Model_Version registered at all for this model name yet.
    assert await wiring.query.resolve_production(_MODEL_NAME) is None

    await _register_simple(wiring, _MODEL_NAME, version)

    # Registered but not yet PRODUCTION.
    assert await wiring.query.resolve_production(_MODEL_NAME) is None

    await _promote_to_production(wiring, _MODEL_NAME, version)

    resolved = await wiring.query.resolve_production(_MODEL_NAME)
    assert resolved is not None
    assert resolved.version == version
    assert resolved.lifecycle_state == LifecycleState.PRODUCTION


# --- Property 20: rollback only from a previously-PRODUCTION version --------


# Feature: model-registry, Property 20: Rollback only from a previously-PRODUCTION version
# For any rollback request naming a version that was never PRODUCTION, the
# request is rejected; a valid rollback sets the target to PRODUCTION,
# demotes the incumbent, and is recorded as a rollback in history.
# Validates: Requirements 15.1, 15.3, 15.4, 15.5
@settings(max_examples=100)
@given(version=st.integers(min_value=1, max_value=1_000))
async def test_rollback_of_a_version_never_in_production_is_rejected(version: int) -> None:
    """A version that is currently ``DEPRECATED`` (satisfying the rollback
    edge's current-state legality, Requirement 15.1) but whose
    ``Promotion_History`` never records a transition *into* ``PRODUCTION``
    cannot be rolled back: ``rollback`` raises ``NeverInProductionError`` and
    nothing is mutated.

    In this domain's normal lifecycle graph, ``DEPRECATED`` is reachable only
    by having first been ``PRODUCTION`` (via the mandated ``PRODUCTION ->
    DEPRECATED`` edge or an automatic incumbent demotion), so this test
    forces the version directly into ``DEPRECATED`` via the repository — with
    no accompanying history entry — to isolate the "never in production"
    check (a defense-in-depth guard, Requirement 15.4) from the current-state
    legality check (Requirement 15.1)."""
    wiring = _wire()
    await _register_simple(wiring, _MODEL_NAME, version)
    await wiring.model_versions.set_lifecycle_state(_MODEL_NAME, version, LifecycleState.DEPRECATED)

    with pytest.raises(NeverInProductionError):
        await wiring.service.rollback(
            model_name=_MODEL_NAME,
            version=version,
            requester="requester",
            justification="attempted rollback",
            correlation_id="corr-rollback-invalid",
        )

    stored = await wiring.model_versions.get(_MODEL_NAME, version)
    assert stored is not None
    assert stored.lifecycle_state == LifecycleState.DEPRECATED
    history = await wiring.query.get_promotion_history(_MODEL_NAME, version)
    assert history == []


@settings(max_examples=25)
@given(
    version_a=st.integers(min_value=1, max_value=100),
    version_b=st.integers(min_value=101, max_value=200),
)
async def test_rollback_of_a_previously_production_version_restores_it_and_demotes_incumbent(
    version_a: int, version_b: int
) -> None:
    """After ``version_a`` is promoted to PRODUCTION and then superseded (and
    demoted to DEPRECATED) by ``version_b``, rolling back to ``version_a``
    must, once approved by Four_Eyes, set ``version_a`` back to PRODUCTION and
    demote ``version_b`` to DEPRECATED; the resulting history entry for
    ``version_a`` must be flagged as a rollback."""
    wiring = _wire()
    await _register_simple(wiring, _MODEL_NAME, version_a)
    await _register_simple(wiring, _MODEL_NAME, version_b)

    await _promote_to_production(wiring, _MODEL_NAME, version_a)
    await _promote_to_production(wiring, _MODEL_NAME, version_b, approvers=("carol", "dave"))

    version_a_before_rollback = await wiring.model_versions.get(_MODEL_NAME, version_a)
    assert version_a_before_rollback is not None
    assert version_a_before_rollback.lifecycle_state == LifecycleState.DEPRECATED

    rollback_request = await wiring.service.rollback(
        model_name=_MODEL_NAME,
        version=version_a,
        requester="requester",
        justification="rolling back to the known-good version",
        correlation_id="corr-rollback-valid",
    )
    assert rollback_request.approval_state == ApprovalState.PENDING
    assert rollback_request.is_rollback is True
    request_id = rollback_request.id
    assert request_id is not None

    await wiring.service.approve(
        request_id=request_id,
        approver="erin",
        approver_kind=PrincipalKind.HUMAN,
        correlation_id="corr-rollback-approve-1",
    )
    await wiring.service.approve(
        request_id=request_id,
        approver="frank",
        approver_kind=PrincipalKind.HUMAN,
        correlation_id="corr-rollback-approve-2",
    )

    version_a_after_rollback = await wiring.model_versions.get(_MODEL_NAME, version_a)
    version_b_after_rollback = await wiring.model_versions.get(_MODEL_NAME, version_b)
    assert version_a_after_rollback is not None
    assert version_b_after_rollback is not None
    assert version_a_after_rollback.lifecycle_state == LifecycleState.PRODUCTION
    assert version_b_after_rollback.lifecycle_state == LifecycleState.DEPRECATED

    history_a = await wiring.query.get_promotion_history(_MODEL_NAME, version_a)
    rollback_entries = [entry for entry in history_a if entry.is_rollback]
    assert len(rollback_entries) == 1
    assert rollback_entries[0].to_state == LifecycleState.PRODUCTION


# --- Property 21: promotion history is complete and append-only -------------


# Feature: model-registry, Property 21: Promotion history is complete and append-only
# For any applied transition or rollback, exactly one ordered history entry
# is appended (from, to, requester, approvers, justification, timestamp), and
# no history entry is ever modified or deleted.
# Validates: Requirements 17.1, 17.2, 17.3
@settings(max_examples=100)
@given(version=st.integers(min_value=1, max_value=1_000))
async def test_promotion_history_grows_by_exactly_one_entry_per_applied_transition_and_is_ordered(
    version: int,
) -> None:
    """Each applied (ungated or evidence-gated) transition appends exactly one
    ordered ``PromotionHistoryEntry``; the history list only ever grows, is
    returned in the order entries were appended, and no repository method
    exists that could remove or overwrite an existing entry (the
    ``FakePromotionRepository.append_history`` implementation only ever
    appends to its internal list)."""
    wiring = _wire()
    await _register_simple(wiring, _MODEL_NAME, version)
    from aqros_model_registry.domain.models import ValidationEvidence

    history_before = await wiring.query.get_promotion_history(_MODEL_NAME, version)
    assert history_before == []

    await wiring.service.request_transition(
        model_name=_MODEL_NAME,
        version=version,
        to_state=LifecycleState.VALIDATED,
        requester="requester",
        justification="validated",
        correlation_id="corr-validate",
        validation_evidence=ValidationEvidence(
            kind="backtest_report", reference="dossier-1", attached_at=datetime(2024, 1, 1)
        ),
    )
    history_after_one = await wiring.query.get_promotion_history(_MODEL_NAME, version)
    assert len(history_after_one) == 1
    assert history_after_one[0].from_state == LifecycleState.REGISTERED
    assert history_after_one[0].to_state == LifecycleState.VALIDATED

    staging_request = await wiring.service.request_transition(
        model_name=_MODEL_NAME,
        version=version,
        to_state=LifecycleState.STAGING,
        requester="requester",
        justification="stage it",
        correlation_id="corr-stage",
    )
    assert isinstance(staging_request, PromotionRequest)
    # A PENDING request (gate not yet satisfied) must not append any history
    # entry — history records only applied transitions (Req 17.1).
    history_while_pending = await wiring.query.get_promotion_history(_MODEL_NAME, version)
    assert history_while_pending == history_after_one

    await wiring.service.approve(
        request_id=staging_request.id,  # type: ignore[arg-type]
        approver="single-approver",
        approver_kind=PrincipalKind.HUMAN,
        correlation_id="corr-stage-approve",
    )
    history_after_two = await wiring.query.get_promotion_history(_MODEL_NAME, version)
    assert len(history_after_two) == 2
    # The list only ever grows: every earlier entry is still present, in order.
    assert history_after_two[:1] == history_after_one
    assert history_after_two[1].from_state == LifecycleState.VALIDATED
    assert history_after_two[1].to_state == LifecycleState.STAGING

    # Direct repository inspection: append_history only ever appends; there is
    # no update/delete path on FakePromotionRepository (append-only by
    # construction, Requirement 17.2).
    assert wiring.promotions._history == list(history_after_two)


# --- Property 22: audit trail captures every privileged action, append-only -


# Feature: model-registry, Property 22: Audit trail captures every privileged action, append-only
# For any registration, transition request, approval, rejection, rollback, or
# PRODUCTION-artifact retrieval, exactly one Audit_Event is recorded with
# actor, timestamp, affected version, before/after state, justification
# (where applicable), and correlation id, and no Audit_Event is ever modified
# or deleted.
# Validates: Requirements 18.1, 18.2, 18.3
@settings(max_examples=100)
@given(version=st.integers(min_value=1, max_value=1_000))
async def test_audit_history_records_every_privileged_action_and_only_grows(
    version: int,
) -> None:
    """Registering, requesting a transition, approving it, and rejecting a
    second request all append distinct ``Audit_Event`` entries; the audit
    trail returned by ``get_audit_history`` only ever grows across calls, and
    every expected action is present exactly once."""
    wiring = _wire()

    audit_before = await wiring.query.get_audit_history(model_name=_MODEL_NAME)
    assert audit_before == []

    await _register_simple(wiring, _MODEL_NAME, version)
    audit_after_register = await wiring.query.get_audit_history(model_name=_MODEL_NAME)
    assert len(audit_after_register) == len(audit_before) + 1
    assert audit_after_register[-1].action == "registered"

    from aqros_model_registry.domain.models import ValidationEvidence

    await wiring.service.request_transition(
        model_name=_MODEL_NAME,
        version=version,
        to_state=LifecycleState.VALIDATED,
        requester="requester",
        justification="validated",
        correlation_id="corr-validate",
        validation_evidence=ValidationEvidence(
            kind="backtest_report", reference="dossier-1", attached_at=datetime(2024, 1, 1)
        ),
    )
    audit_after_validate = await wiring.query.get_audit_history(model_name=_MODEL_NAME)
    assert len(audit_after_validate) == len(audit_after_register) + 1
    assert audit_after_validate[-1].action == "transition_requested"
    assert audit_after_validate[-1].after_state == LifecycleState.VALIDATED.value

    stage_pending = await wiring.service.request_transition(
        model_name=_MODEL_NAME,
        version=version,
        to_state=LifecycleState.STAGING,
        requester="requester",
        justification="stage it",
        correlation_id="corr-stage",
    )
    assert isinstance(stage_pending, PromotionRequest)
    audit_after_stage_request = await wiring.query.get_audit_history(model_name=_MODEL_NAME)
    assert len(audit_after_stage_request) == len(audit_after_validate) + 1
    assert audit_after_stage_request[-1].action == "transition_requested"

    await wiring.service.reject(
        request_id=stage_pending.id,  # type: ignore[arg-type]
        approver="rejector",
        approver_kind=PrincipalKind.HUMAN,
        reason="not ready yet",
        correlation_id="corr-reject",
    )
    audit_after_reject = await wiring.query.get_audit_history(model_name=_MODEL_NAME)
    assert len(audit_after_reject) == len(audit_after_stage_request) + 1
    assert audit_after_reject[-1].action == "rejected"
    assert audit_after_reject[-1].justification == "not ready yet"

    # The audit trail only ever grows: every earlier entry remains present, in
    # the same order, across every call (append-only, Req 18.2).
    assert audit_after_reject[: len(audit_after_stage_request)] == audit_after_stage_request
    assert audit_after_stage_request[: len(audit_after_validate)] == audit_after_validate
    assert audit_after_validate[: len(audit_after_register)] == audit_after_register

    actions_seen = [event.action for event in audit_after_reject]
    assert actions_seen.count("registered") == 1
    assert actions_seen.count("rejected") == 1

    # Direct repository inspection: append is the only write path on
    # FakeAuditRepository (append-only by construction, Requirement 18.2).
    assert wiring.audit._events == audit_after_reject


# --- Supplementary: ModelVersionNotFoundError on unknown targets ------------


async def test_request_transition_on_unknown_model_version_raises_not_found() -> None:
    wiring = _wire()
    with pytest.raises(ModelVersionNotFoundError):
        await wiring.service.request_transition(
            model_name=_MODEL_NAME,
            version=1,
            to_state=LifecycleState.VALIDATED,
            requester="requester",
            justification="n/a",
            correlation_id="corr-missing",
        )


async def test_rollback_on_unknown_model_version_raises_not_found() -> None:
    wiring = _wire()
    with pytest.raises(ModelVersionNotFoundError):
        await wiring.service.rollback(
            model_name=_MODEL_NAME,
            version=1,
            requester="requester",
            justification="n/a",
            correlation_id="corr-missing",
        )
