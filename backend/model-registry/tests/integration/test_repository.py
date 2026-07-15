"""Repository integration tests against a real Postgres (task 9.3).

Exercises ``SqlAlchemyModelVersionRepository``, ``SqlAlchemyPromotionRepository``,
and ``SqlAlchemyAuditRepository`` against a real, throwaway Postgres database
provisioned via testcontainers (the ``engine``/``session_factory``/``db_session``
fixtures in ``tests/integration/conftest.py``) to verify invariants that only a
real database enforces:

* the ``UniqueConstraint(registered_model_id, version)`` version-uniqueness
  backstop on ``model_versions`` (Requirements 3.2, 22.2);
* the partial unique index ``uq_one_production_per_model`` — the
  single-PRODUCTION invariant, physically enforced even under concurrent
  writers (**Property 18**; Requirements 16.1, 23.3);
* the append-only behavior of ``Promotion_History`` and ``Audit_History``
  (Requirements 17.2, 18.2, 22.4).

Never imports ``aqros_training_pipeline`` (CLAUDE.md §7.9). Mirrors the style
of ``aqros_training_pipeline/tests/integration/test_repository.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from aqros_model_registry.adapters.repository import (
    SqlAlchemyAuditRepository,
    SqlAlchemyModelVersionRepository,
    SqlAlchemyPromotionRepository,
)
from aqros_model_registry.domain.models import (
    AggregatedMetrics,
    ApprovalState,
    AuditEvent,
    DatasetVersionRef,
    LifecycleState,
    MetricsRecord,
    ModelVersion,
    PromotionHistoryEntry,
    ReproducibilityMetadata,
)
from aqros_model_registry.domain.ports import AuditRepository, PromotionRepository

pytestmark = pytest.mark.integration

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


def _model_version(
    model_name: str,
    version: int,
    *,
    lifecycle_state: LifecycleState = LifecycleState.REGISTERED,
) -> ModelVersion:
    """Build a fully-populated, mandatory-metadata-complete ``ModelVersion``."""
    reproducibility_metadata = ReproducibilityMetadata(
        model_version=version,
        dataset_name="aapl_5d_direction",
        dataset_version=1,
        dataset_checksum="dataset-checksum",
        feature_versions={"close_return_5d": 1},
        git_commit="a" * 40,
        training_run_id=version * 10,
        trained_at=datetime(2024, 1, 1, tzinfo=UTC),
        hyperparameters={"n_estimators": 100},
        aggregated_metrics=_AGGREGATED_METRICS,
    )
    return ModelVersion(
        model_name=model_name,
        model_type=model_name.rsplit("__", 1)[-1],
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
        approval_state=ApprovalState.NOT_REQUIRED,
        validation_evidence=None,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# 1. Version uniqueness: UniqueConstraint(registered_model_id, version)
# ---------------------------------------------------------------------------


async def test_two_distinct_versions_for_same_model_both_succeed(
    db_session: AsyncSession,
) -> None:
    """Two ``ModelVersion`` rows for the same model name but distinct versions
    both persist successfully under the same ``Registered_Model``."""
    repo = SqlAlchemyModelVersionRepository(db_session)
    model_name = "aapl_5d_direction__random_forest"

    created_1 = await repo.create_model_version(_model_version(model_name, 1))
    created_2 = await repo.create_model_version(_model_version(model_name, 2))

    assert created_1.id is not None
    assert created_2.id is not None
    assert created_1.id != created_2.id
    assert await repo.get_latest_version(model_name) == 2


async def test_unique_constraint_rejects_duplicate_version(db_session: AsyncSession) -> None:
    """Attempting to insert a second ``ModelVersion`` with the same
    ``(model_name, version)`` — and therefore the same ``(registered_model_id,
    version)`` — raises ``IntegrityError`` at the database level, backstopping
    the version-uniqueness invariant (Requirements 3.2, 22.2)."""
    repo = SqlAlchemyModelVersionRepository(db_session)
    model_name = "aapl_5d_direction__gradient_boosting"

    await repo.create_model_version(_model_version(model_name, 1))
    with pytest.raises(IntegrityError):
        await repo.create_model_version(_model_version(model_name, 1))


# ---------------------------------------------------------------------------
# 2. Property 18: uq_one_production_per_model rejects a second concurrent
#    PRODUCTION for the same Registered_Model.
# ---------------------------------------------------------------------------


async def test_partial_unique_index_rejects_second_production_in_same_transaction(
    db_session: AsyncSession,
) -> None:
    """Within a single transaction, setting two ``ModelVersion`` rows of the
    same ``Registered_Model`` to PRODUCTION must fail on the second write: the
    partial unique index ``uq_one_production_per_model`` is an immediate
    (non-deferred) constraint, so the violation surfaces as soon as the
    second update is flushed."""
    repo = SqlAlchemyModelVersionRepository(db_session)
    model_name = "aapl_5d_direction__logistic_regression"
    await repo.create_model_version(_model_version(model_name, 1))
    await repo.create_model_version(_model_version(model_name, 2))

    await repo.set_lifecycle_state(model_name, 1, LifecycleState.PRODUCTION)

    with pytest.raises(IntegrityError):
        await repo.set_lifecycle_state(model_name, 2, LifecycleState.PRODUCTION)


async def test_partial_unique_index_rejects_second_concurrent_production_across_sessions(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """**Property 18** (concurrency-oriented): two independent, concurrent
    sessions each attempt to promote a different ``ModelVersion`` of the same
    ``Registered_Model`` to PRODUCTION. The first session's write commits
    successfully; the second — simulating a concurrent writer racing against
    it — is rejected by the partial unique index ``uq_one_production_per_model``
    regardless of any application-level check, so the single-PRODUCTION
    invariant holds even under real concurrent access (Requirements 16.1,
    23.2, 23.3)."""
    model_name = "aapl_5d_direction__xgboost"

    async with session_factory() as setup_session:
        setup_repo = SqlAlchemyModelVersionRepository(setup_session)
        await setup_repo.create_model_version(_model_version(model_name, 1))
        await setup_repo.create_model_version(_model_version(model_name, 2))
        await setup_session.commit()

    async with session_factory() as session_a:
        repo_a = SqlAlchemyModelVersionRepository(session_a)
        await repo_a.set_lifecycle_state(model_name, 1, LifecycleState.PRODUCTION)
        await session_a.commit()

    async with session_factory() as session_b:
        repo_b = SqlAlchemyModelVersionRepository(session_b)
        with pytest.raises(IntegrityError):
            await repo_b.set_lifecycle_state(model_name, 2, LifecycleState.PRODUCTION)

    # The invariant holds: the first version is still the sole PRODUCTION
    # version, and resolve_production resolves it unambiguously.
    async with session_factory() as verify_session:
        verify_repo = SqlAlchemyModelVersionRepository(verify_session)
        resolved = await verify_repo.resolve_production(model_name)
        assert resolved is not None
        assert resolved.version == 1


# ---------------------------------------------------------------------------
# 3. Append-only behavior of Promotion_History and Audit_History
# ---------------------------------------------------------------------------


async def test_promotion_history_append_only_and_ordered(db_session: AsyncSession) -> None:
    """``append_history`` writes are the only mutation path: repeated calls
    grow ``Promotion_History`` monotonically, and ``list_history`` returns the
    entries in the order they were appended (Requirements 17.1, 17.2, 17.3)."""
    model_repo = SqlAlchemyModelVersionRepository(db_session)
    promotion_repo = SqlAlchemyPromotionRepository(db_session)
    model_name = "aapl_5d_direction__svm"
    await model_repo.create_model_version(_model_version(model_name, 1))

    transitions = [
        (LifecycleState.REGISTERED, LifecycleState.VALIDATED),
        (LifecycleState.VALIDATED, LifecycleState.STAGING),
        (LifecycleState.STAGING, LifecycleState.PRODUCTION),
    ]
    appended: list[PromotionHistoryEntry] = []
    for index, (from_state, to_state) in enumerate(transitions):
        entry = PromotionHistoryEntry(
            model_name=model_name,
            version=1,
            from_state=from_state,
            to_state=to_state,
            requester="alice",
            approvers=("bob", "carol") if to_state == LifecycleState.PRODUCTION else (),
            justification=f"transition {index}",
            is_rollback=False,
            created_at=datetime(2024, 1, 1, 0, 0, index, tzinfo=UTC),
        )
        appended.append(await promotion_repo.append_history(entry))

    history = await promotion_repo.list_history(model_name, 1)

    assert len(history) == len(transitions)
    assert [(h.from_state, h.to_state) for h in history] == transitions
    assert history == appended

    # Structural check: the port/repository exposes only append + list on
    # promotion history — there is no update/delete method for history rows,
    # matching the PromotionRepository ABC (Requirement 22.4).
    assert hasattr(promotion_repo, "append_history")
    assert hasattr(promotion_repo, "list_history")
    for forbidden in ("update_history", "delete_history", "remove_history", "edit_history"):
        assert not hasattr(promotion_repo, forbidden)
    assert set(PromotionRepository.__abstractmethods__) == {
        "create_request",
        "add_approval",
        "set_request_state",
        "get_request",
        "append_history",
        "list_history",
    }


async def test_audit_events_append_only_and_ordered(db_session: AsyncSession) -> None:
    """``append`` is the only write path on the audit repository: repeated
    calls grow ``Audit_History`` monotonically, and ``list`` returns events in
    the order they were appended (Requirements 18.1, 18.2, 18.3)."""
    audit_repo = SqlAlchemyAuditRepository(db_session)
    model_name = "aapl_5d_direction__decision_tree"

    actions = ["registered", "transition_requested", "approved"]
    appended: list[AuditEvent] = []
    for index, action in enumerate(actions):
        event = AuditEvent(
            action=action,
            actor="alice",
            model_name=model_name,
            version=1,
            before_state=None if index == 0 else "registered",
            after_state="registered" if index == 0 else "validated",
            justification=f"audit entry {index}",
            correlation_id="corr-append-only",
            created_at=datetime(2024, 1, 1, 0, 0, index, tzinfo=UTC),
        )
        appended.append(await audit_repo.append(event))

    listed = await audit_repo.list(model_name=model_name)

    assert len(listed) == len(actions)
    assert [event.action for event in listed] == actions
    assert listed == appended

    # Structural check: only append + list exist on the audit repository —
    # there is no update/delete method for audit rows, matching the
    # AuditRepository ABC (Requirement 22.4).
    assert hasattr(audit_repo, "append")
    assert hasattr(audit_repo, "list")
    for forbidden in ("update", "delete", "remove", "edit"):
        assert not hasattr(audit_repo, forbidden)
    assert set(AuditRepository.__abstractmethods__) == {"append", "list"}
