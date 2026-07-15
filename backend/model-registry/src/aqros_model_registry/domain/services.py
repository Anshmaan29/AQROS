"""Domain services: ``ModelRegistryService`` (write) and ``RegistryQueryService`` (read).

``ModelRegistryService`` is the pure orchestration kernel of the Model Registry
(design.md Sections 9-12). It coordinates the domain primitives
(``integrity``, ``lineage``, ``lifecycle``, ``approval``) and the injected ports
(``TrainingPipelineClient``, ``ArtifactStore``, the repositories,
``ArtifactSigner``, ``Clock``) to register, govern, and vend model versions —
never importing ``aqros_training_pipeline`` (CLAUDE.md §7.9) and never touching
transport or persistence details directly.

The service is a *unit-of-work coordinator*: the repository ports it drives
never ``commit()`` themselves (the request-scoped session owns the
transaction, design.md Section 5.1). Every privileged action therefore appends
its ``Audit_Event`` through the same session that records the state change, so
the audit trail can never be lost relative to the change it describes
(Requirement 18.1, design.md Key Design Decision 9).

This module implements the ``ModelRegistryService`` write kernel — the
constructor, ``register`` (task 2.7), ``request_transition`` (task 2.8),
``approve``/``reject`` (task 2.9), and ``rollback`` (task 2.10) — and the
read-only ``RegistryQueryService`` (task 2.11), which serves every downstream
consumer directly from the Registry so the Training Pipeline is never queried
downstream (Requirements 1.4, 28.7).
"""

from __future__ import annotations

from datetime import datetime

from aqros_model_registry.domain.approval import (
    DECISION_APPROVE,
    DECISION_REJECT,
    GateKind,
    four_eyes_satisfied,
    gate_for,
)
from aqros_model_registry.domain.integrity import (
    ArtifactIntegrityError,
    ChecksumMismatchError,
    compute_checksum,
    verify_checksum,
)
from aqros_model_registry.domain.lifecycle import IllegalTransitionError, transition_allowed
from aqros_model_registry.domain.lineage import (
    Lineage,
    assemble_reproducibility_metadata,
    mandatory_metadata_complete,
)
from aqros_model_registry.domain.models import (
    Approval,
    ApprovalState,
    AuditEvent,
    DatasetVersionRef,
    LifecycleState,
    MetricsRecord,
    ModelVersion,
    PrincipalKind,
    PromotionHistoryEntry,
    PromotionRequest,
    ValidationEvidence,
)
from aqros_model_registry.domain.ports import (
    ArtifactSigner,
    ArtifactStore,
    AuditRepository,
    Clock,
    ModelVersionRepository,
    PromotionRepository,
    TrainingPipelineClient,
)

# The audit action recorded when a Model_Version is first registered
# (design.md Section 4, ``AuditEvent.action``).
AUDIT_ACTION_REGISTERED = "registered"

# The audit action recorded when a lifecycle transition is requested — whether it
# is applied immediately (gate ``none``/``evidence``) or parked as a ``PENDING``
# ``Promotion_Request`` awaiting approval (design.md Section 11, Requirement 18.1).
# Applied and pending events are distinguished by their before/after states: an
# applied transition records ``after_state`` as the new state, while a pending
# request leaves the lifecycle state unchanged (``after_state == before_state``).
AUDIT_ACTION_TRANSITION_REQUESTED = "transition_requested"

# The audit action recorded when an authorized approver approves a
# ``Promotion_Request`` — carried by every state change applied once the gate's
# approval threshold is met, including the automatic incumbent demotion that
# accompanies a PRODUCTION promotion (design.md Section 11, Requirement 18.1).
AUDIT_ACTION_APPROVED = "approved"

# The audit action recorded when an authorized approver rejects a
# ``Promotion_Request``; the lifecycle state is left unchanged and the rejection
# reason is captured in the event's justification (Requirement 14.5, 18.1).
AUDIT_ACTION_REJECTED = "rejected"

# The audit action recorded when a rollback is *requested* — i.e. when a
# ``PENDING`` rollback ``Promotion_Request`` (``DEPRECATED → PRODUCTION``,
# ``is_rollback=True``) is created and parked awaiting Four_Eyes approval
# (Requirements 15.1, 15.2, 18.1). It is the rollback counterpart of
# ``transition_requested`` and, like it, leaves the lifecycle state unchanged
# while the request is ``PENDING``; the rollback itself is applied later by
# ``approve`` (which records an ``approved`` event and a ``Promotion_History``
# entry flagged ``is_rollback=True`` — Requirement 15.5).
AUDIT_ACTION_ROLLBACK_REQUESTED = "rollback_requested"


class MandatoryMetadataIncompleteError(RuntimeError):
    """Raised when a ``Trained_Model_Record`` is missing mandatory metadata.

    A ``Model_Version`` may be persisted only when its source record supplies
    every mandatory field (Requirements 6.2, 6.4). Registration is rejected
    with no persistence and the missing fields are named so the caller can
    record a precise, human-readable reason (Requirements 4.3, 5.3, 20.5). An
    absent git commit is *not* one of these fields — it is tolerated and
    recorded as explicitly absent (Requirement 6.3).
    """

    def __init__(self, model_name: str, version: int, missing_fields: tuple[str, ...]) -> None:
        self.model_name = model_name
        self.version = version
        self.missing_fields = missing_fields
        joined = ", ".join(missing_fields)
        super().__init__(
            f"Cannot register {model_name} v{version}: "
            f"mandatory metadata incomplete (missing: {joined})."
        )


class ModelVersionNotFoundError(RuntimeError):
    """Raised when a governance action names a ``Model_Version`` that does not exist.

    A transition (or later approval/rollback) can only be requested against a
    ``Model_Version`` the Registry already holds; a request for an unknown
    ``(model_name, version)`` pair is rejected so the caller can surface a typed
    404 (design.md Section 13, Requirement 19.10).
    """

    def __init__(self, model_name: str, version: int) -> None:
        self.model_name = model_name
        self.version = version
        super().__init__(f"No Model_Version found for {model_name} v{version}.")


class ValidationEvidenceRequiredError(RuntimeError):
    """Raised when ``REGISTERED → VALIDATED`` is requested without evidence.

    The validation gate mandates that ``Validation_Evidence`` be attached before
    a ``Model_Version`` may reach ``VALIDATED``; a request lacking it is rejected
    with the missing evidence recorded as the reason and nothing is mutated
    (Requirements 12.1, 12.2, 20.5).
    """

    def __init__(self, model_name: str, version: int) -> None:
        self.model_name = model_name
        self.version = version
        super().__init__(
            f"Cannot transition {model_name} v{version} to VALIDATED: "
            "validation evidence is required but was not supplied."
        )


class PromotionRequestNotFoundError(RuntimeError):
    """Raised when an approval/rejection names a ``Promotion_Request`` that does not exist.

    An approver may only act against a ``Promotion_Request`` the Registry
    already holds; a request for an unknown id is rejected so the caller can
    surface a typed 404 (design.md Section 13, Requirement 19.10).
    """

    def __init__(self, request_id: int) -> None:
        self.request_id = request_id
        super().__init__(f"No Promotion_Request found for id {request_id}.")


class PromotionRequestNotPendingError(RuntimeError):
    """Raised when an approval/rejection targets a non-``PENDING`` ``Promotion_Request``.

    Only a ``PENDING`` request may be approved or rejected; a request that has
    already been ``APPROVED`` or ``REJECTED`` is settled and cannot be acted on
    again (Requirement 14.3), so the caller can surface a typed 409.
    """

    def __init__(self, request_id: int, current_state: ApprovalState) -> None:
        self.request_id = request_id
        self.current_state = current_state
        super().__init__(
            f"Promotion_Request {request_id} is not PENDING "
            f"(current approval state: {current_state.value})."
        )


class AutomatedApprovalNotPermittedError(RuntimeError):
    """Raised when a non-human principal attempts to approve a ``PRODUCTION`` transition.

    No automated principal may ever satisfy — or even contribute to — the
    ``Four_Eyes`` control guarding a transition into ``PRODUCTION`` (Requirements
    14.7, 21.2); the attempt is rejected outright so the caller can surface a
    typed 403 rather than silently not counting it.
    """

    def __init__(self, request_id: int, approver: str) -> None:
        self.request_id = request_id
        self.approver = approver
        super().__init__(
            f"Automated principal {approver!r} may not approve PRODUCTION "
            f"Promotion_Request {request_id}: PRODUCTION promotions require human approvers."
        )


class NeverInProductionError(RuntimeError):
    """Raised when a rollback designates a ``Model_Version`` that was never in ``PRODUCTION``.

    A rollback may only re-promote a version that has genuinely been the
    ``Production_Model`` before; a request naming a version with no prior
    ``PRODUCTION`` transition in its ``Promotion_History`` is rejected with the
    reason recorded, so the caller can surface a typed 409 (Requirement 15.4,
    design.md Section 13). The current-state legality (the version must be
    ``DEPRECATED`` to take the ``DEPRECATED → PRODUCTION`` rollback edge) is
    enforced separately by the ``Lifecycle_State_Machine`` (Requirement 15.1).
    """

    def __init__(self, model_name: str, version: int) -> None:
        self.model_name = model_name
        self.version = version
        super().__init__(
            f"Cannot roll back {model_name} v{version} to PRODUCTION: "
            "this Model_Version was never in PRODUCTION."
        )


class ModelRegistryService:
    """Orchestrates registration and governance of ``Model_Version`` records.

    Pure coordination logic: every side effect is delegated to an injected
    port, so the whole service is exercisable against in-memory fakes
    (Requirement 26.1). The service never commits — it composes writes that the
    surrounding request-scoped transaction commits atomically (design.md
    Section 5.1, Key Design Decision 9).
    """

    def __init__(
        self,
        *,
        training_pipeline_client: TrainingPipelineClient,
        artifact_store: ArtifactStore,
        model_version_repository: ModelVersionRepository,
        promotion_repository: PromotionRepository,
        audit_repository: AuditRepository,
        artifact_signer: ArtifactSigner,
        clock: Clock,
    ) -> None:
        self._training_pipeline = training_pipeline_client
        self._artifacts = artifact_store
        self._model_versions = model_version_repository
        self._promotions = promotion_repository
        self._audit = audit_repository
        self._signer = artifact_signer
        self._clock = clock

    async def register(
        self,
        *,
        model_name: str,
        version: int,
        training_run_id: int,
        actor: str,
        correlation_id: str,
    ) -> ModelVersion:
        """Register a ``Model_Version`` from a Training Pipeline reference.

        Ingests exclusively through the ``Training_Pipeline_Client`` (Requirement
        1.2) and, on success, records an immutable, fully-lineaged
        ``Model_Version`` in ``REGISTERED``/``NOT_REQUIRED`` (Requirement 2.5)
        together with a ``registered`` ``Audit_Event`` in the same transaction
        (Requirement 18.1). The steps, in order (design.md Section 9):

        1. **Idempotency** — if a ``Model_Version`` already exists for
           ``(model_name, version)`` with the same ``training_run_id``, return
           it unchanged without creating a duplicate (Requirement 2.4).
        2. **Fetch the record** — pull the ``Trained_Model_Record`` from the
           Training Pipeline. A 404 (``TrainedModelNotFoundError``) or any other
           upstream failure (``UpstreamSourceError``) propagates to the caller
           and *nothing is persisted* (Requirements 1.6, 2.3, 20.3).
        3. **Validate mandatory metadata** — reject with
           ``MandatoryMetadataIncompleteError`` if any mandatory field is absent
           (Requirements 4.3, 5.3, 6.2, 6.4); an absent git commit is tolerated
           (Requirement 6.3).
        4. **Download + verify the artifact** — download the bytes and verify
           their checksum against the record using the record's named algorithm
           (Requirement 7.1); on mismatch, reject with ``ChecksumMismatchError``
           and persist nothing (Requirement 7.2).
        5. **Persist an independent artifact copy** — store the verified bytes in
           the Registry's own ``Artifact_Store`` so downstream retrieval never
           contacts the Training Pipeline (Requirements 1.5, 8.1).
        6. **Persist the ``Model_Version`` + audit** — record the immutable
           version and append the ``registered`` ``Audit_Event`` within the same
           unit of work (Requirements 2.2, 6.1, 7.3, 18.1).

        Because every persisting step runs only after all validation passes and
        the surrounding transaction commits atomically, a failure at any point
        leaves no partial or unverified ``Model_Version`` behind (Requirement
        20.3).

        Args:
            model_name: The composite ``{dataset_name}__{model_type}`` name.
            version: The Training-Pipeline-assigned version to register.
            training_run_id: The training run that produced the model; part of
                the idempotency key.
            actor: The principal performing the registration (for the audit).
            correlation_id: The request correlation identifier (for the audit).

        Returns:
            The persisted (or pre-existing, on an idempotent replay)
            ``ModelVersion``.

        Raises:
            TrainedModelNotFoundError: if the Training Pipeline has no such
                trained model (mapped to 404 upstream).
            UpstreamSourceError: if the Training Pipeline is unreachable or errors
                (mapped to 502 upstream).
            MandatoryMetadataIncompleteError: if mandatory metadata is missing.
            ChecksumMismatchError: if the downloaded artifact fails its checksum.
        """
        existing = await self._model_versions.get(model_name, version)
        if existing is not None and existing.training_run_id == training_run_id:
            return existing

        # Ingest solely via the Training Pipeline REST port; a 404 or any other
        # upstream failure propagates and nothing is persisted (Req 1.6, 2.3).
        record = await self._training_pipeline.get_trained_model_record(model_name, version)

        completeness = mandatory_metadata_complete(record)
        if not completeness.ok:
            raise MandatoryMetadataIncompleteError(
                record.model_name, record.model_version, completeness.missing_fields
            )

        artifact_bytes = await self._training_pipeline.download_artifact(model_name, version)
        if not verify_checksum(artifact_bytes, record.artifact_checksum, record.checksum_algorithm):
            actual = compute_checksum(artifact_bytes, record.checksum_algorithm)
            raise ChecksumMismatchError(record.checksum_algorithm, record.artifact_checksum, actual)

        # Persist an independent, immutable copy so downstream reads never touch
        # the Training Pipeline (Req 1.5, 8.1).
        artifact_path = await self._artifacts.write_artifact(
            record.model_name, record.model_version, artifact_bytes
        )

        now = self._clock.now()
        model_version = ModelVersion(
            model_name=record.model_name,
            model_type=record.model_type,
            version=record.model_version,
            training_run_id=record.training_run_id,
            dataset_version=DatasetVersionRef(
                dataset_name=record.dataset_name,
                dataset_version=record.dataset_version,
                dataset_checksum=record.dataset_checksum,
            ),
            feature_versions=dict(record.feature_versions),
            metrics=MetricsRecord(
                per_fold=record.per_fold_metrics,
                aggregated=record.aggregated_metrics,
                feature_importance=dict(record.feature_importance),
            ),
            artifact_path=artifact_path,
            artifact_checksum=record.artifact_checksum,
            checksum_algorithm=record.checksum_algorithm,
            git_commit=record.git_commit,
            reproducibility_metadata=assemble_reproducibility_metadata(record),
            lifecycle_state=LifecycleState.REGISTERED,
            approval_state=ApprovalState.NOT_REQUIRED,
            validation_evidence=None,
            created_at=now,
        )
        persisted = await self._model_versions.create_model_version(model_version)

        # Append the audit record in the same unit of work as the state change
        # (Req 18.1); the surrounding session commits both atomically.
        await self._audit.append(
            AuditEvent(
                action=AUDIT_ACTION_REGISTERED,
                actor=actor,
                model_name=record.model_name,
                version=record.model_version,
                before_state=None,
                after_state=LifecycleState.REGISTERED.value,
                justification=None,
                correlation_id=correlation_id,
                created_at=now,
            )
        )
        return persisted

    async def request_transition(
        self,
        *,
        model_name: str,
        version: int,
        to_state: LifecycleState,
        requester: str,
        justification: str,
        correlation_id: str,
        validation_evidence: ValidationEvidence | None = None,
    ) -> ModelVersion | PromotionRequest:
        """Request a lifecycle transition, applying it now or parking it for approval.

        Coordinates the ``Lifecycle_State_Machine`` and the ``Approval_Policy`` to
        move a ``Model_Version`` toward (or out of) production under governance
        (design.md Sections 10-11). The steps, in order:

        1. **Resolve the version** — fetch the ``Model_Version``; a request for an
           unknown ``(model_name, version)`` pair is rejected with
           ``ModelVersionNotFoundError`` (Requirement 19.10).
        2. **Assert legality** — call ``transition_allowed(from, to, is_rollback=False)``,
           which raises ``IllegalTransitionError`` for any edge that is not a
           permitted forward or abandonment transition (Requirements 11.2, 11.3).
           Rollback is a distinct entry point (Requirement 15), so this method
           never treats a request as a rollback.
        3. **Resolve the gate** — look up the governing ``GateKind`` for the
           (now-legal) edge via ``gate_for`` (design.md Key Design Decision 6).
        4. **Apply or park**, depending on the gate:

           * ``EVIDENCE`` (``REGISTERED → VALIDATED``) — require the caller to
             supply ``Validation_Evidence`` (else ``ValidationEvidenceRequiredError``,
             Requirements 12.1, 12.2); attach it immutably to the version's
             lineage (Requirement 12.3), then apply the transition immediately,
             appending a ``Promotion_History`` entry and an ``Audit_Event``
             (Requirements 13.4, 17.1, 18.1).
           * ``NONE`` — a gate of "none" applies immediately if legal
             (Requirement 13.3), appending history + audit; the Registry never
             auto-promotes to ``PRODUCTION`` this way (Requirement 13.5).
           * ``SINGLE_APPROVAL`` / ``FOUR_EYES`` — do **not** change the lifecycle
             state; create a ``PENDING`` ``Promotion_Request`` (approval state
             ``PENDING``) and append a ``transition_requested`` ``Audit_Event``
             (Requirements 13.2, 14.1). No ``Promotion_History`` entry is written
             yet — history records only *applied* transitions (Requirement 17.1);
             the transition is applied later by ``approve`` once the gate is met.

        The service never commits; every write above is composed into the
        surrounding request-scoped transaction so state change and audit record
        are persisted atomically (design.md Section 5.1, Key Design Decision 9).

        Args:
            model_name: The composite ``{dataset_name}__{model_type}`` name.
            version: The ``Model_Version`` to transition.
            to_state: The requested target ``LifecycleState``.
            requester: The principal requesting the transition (recorded as the
                request's requester and the audit actor).
            justification: The human-readable reason for the transition.
            correlation_id: The request correlation identifier (for the audit).
            validation_evidence: Evidence to attach; required only for the
                ``REGISTERED → VALIDATED`` (``EVIDENCE``) gate.

        Returns:
            The updated ``ModelVersion`` when the transition is applied
            immediately (``NONE``/``EVIDENCE`` gates), or the newly created
            ``PENDING`` ``PromotionRequest`` when the transition awaits approval
            (``SINGLE_APPROVAL``/``FOUR_EYES`` gates).

        Raises:
            ModelVersionNotFoundError: if no such ``Model_Version`` exists.
            IllegalTransitionError: if the requested edge is not permitted, or if
                an immediate (ungated) transition would reach ``PRODUCTION``.
            ValidationEvidenceRequiredError: if ``REGISTERED → VALIDATED`` is
                requested without ``Validation_Evidence``.
        """
        model_version = await self._model_versions.get(model_name, version)
        if model_version is None:
            raise ModelVersionNotFoundError(model_name, version)

        from_state = model_version.lifecycle_state

        # Legality first: raises IllegalTransitionError for any edge that is not a
        # permitted forward/abandonment transition (Req 11.3). Rollback is a
        # separate entry point, so is_rollback is always False here (Req 15).
        transition_allowed(from_state, to_state, is_rollback=False)

        # Resolve the gate governing this already-legal edge (design KDD 6).
        gate = gate_for(from_state, to_state, is_rollback=False)

        now = self._clock.now()

        if gate is GateKind.EVIDENCE:
            # REGISTERED -> VALIDATED: evidence is mandatory (Req 12.1, 12.2) and
            # is attached immutably to the version's lineage (Req 12.3).
            if validation_evidence is None:
                raise ValidationEvidenceRequiredError(model_name, version)
            await self._model_versions.attach_validation_evidence(
                model_name, version, validation_evidence
            )
            return await self._apply_transition(
                model_version=model_version,
                to_state=to_state,
                requester=requester,
                approvers=(),
                justification=justification,
                correlation_id=correlation_id,
                is_rollback=False,
                now=now,
            )

        if gate is GateKind.NONE:
            # A gate of "none" applies immediately if legal (Req 13.3) — but the
            # Registry never auto-promotes to PRODUCTION without an approved
            # Promotion_Request (Req 13.5).
            if to_state is LifecycleState.PRODUCTION:
                raise IllegalTransitionError(from_state, to_state, False)
            return await self._apply_transition(
                model_version=model_version,
                to_state=to_state,
                requester=requester,
                approvers=(),
                justification=justification,
                correlation_id=correlation_id,
                is_rollback=False,
                now=now,
            )

        # SINGLE_APPROVAL / FOUR_EYES: leave the lifecycle state untouched; record
        # a PENDING Promotion_Request and one 'transition_requested' Audit_Event
        # (Req 13.2, 14.1, 18.1). No Promotion_History entry yet — history records
        # only applied transitions (Req 17.1); approve() applies it later.
        request = await self._promotions.create_request(
            PromotionRequest(
                model_name=model_name,
                version=version,
                from_state=from_state,
                to_state=to_state,
                requester=requester,
                justification=justification,
                approval_state=ApprovalState.PENDING,
                is_rollback=False,
                created_at=now,
            )
        )
        await self._audit.append(
            AuditEvent(
                action=AUDIT_ACTION_TRANSITION_REQUESTED,
                actor=requester,
                model_name=model_name,
                version=version,
                before_state=from_state.value,
                after_state=from_state.value,  # lifecycle unchanged while PENDING
                justification=justification,
                correlation_id=correlation_id,
                created_at=now,
            )
        )
        return request

    async def _apply_transition(
        self,
        *,
        model_version: ModelVersion,
        to_state: LifecycleState,
        requester: str,
        approvers: tuple[str, ...],
        justification: str,
        correlation_id: str,
        is_rollback: bool,
        now: datetime,
        audit_action: str = AUDIT_ACTION_TRANSITION_REQUESTED,
    ) -> ModelVersion:
        """Apply a legal, gate-satisfied transition and record it atomically.

        Updates only the ``lifecycle_state`` column (Requirement 22.3), appends
        exactly one ordered ``Promotion_History`` entry (from/to/requester/
        approvers/justification/timestamp — Requirements 13.4, 17.1), and appends
        one ``Audit_Event`` capturing the before/after states (Requirement 18.1).
        All three writes join the surrounding transaction so the state change,
        history, and audit record commit together (Key Design Decision 9).

        ``audit_action`` names the ``Audit_Event.action`` recorded for the change:
        it defaults to ``transition_requested`` for an immediately-applied request
        (``NONE``/``EVIDENCE`` gates in ``request_transition``) and is passed as
        ``approved`` when the transition is applied by ``approve`` once an approval
        gate is satisfied (design.md Section 11).
        """
        from_state = model_version.lifecycle_state
        updated = await self._model_versions.set_lifecycle_state(
            model_version.model_name, model_version.version, to_state
        )
        await self._promotions.append_history(
            PromotionHistoryEntry(
                model_name=model_version.model_name,
                version=model_version.version,
                from_state=from_state,
                to_state=to_state,
                requester=requester,
                approvers=approvers,
                justification=justification,
                is_rollback=is_rollback,
                created_at=now,
            )
        )
        await self._audit.append(
            AuditEvent(
                action=audit_action,
                actor=requester,
                model_name=model_version.model_name,
                version=model_version.version,
                before_state=from_state.value,
                after_state=to_state.value,
                justification=justification,
                correlation_id=correlation_id,
                created_at=now,
            )
        )
        return updated

    async def approve(
        self,
        *,
        request_id: int,
        approver: str,
        approver_kind: PrincipalKind,
        correlation_id: str,
        reason: str | None = None,
    ) -> PromotionRequest | ModelVersion:
        """Record an approval and, once the gate is satisfied, apply the transition.

        Drives the approval half of the promotion workflow (design.md Section
        11). The steps, in order:

        1. **Resolve the request** — fetch the ``Promotion_Request``; an unknown
           id is rejected with ``PromotionRequestNotFoundError`` (Requirement
           19.10) and a settled (non-``PENDING``) request with
           ``PromotionRequestNotPendingError`` (Requirement 14.3).
        2. **Reject automated PRODUCTION approvers** — no non-human principal may
           ever contribute to the ``Four_Eyes`` control guarding a transition
           into ``PRODUCTION``; such an attempt is rejected outright with
           ``AutomatedApprovalNotPermittedError`` before it is recorded
           (Requirements 14.7, 21.2).
        3. **Record the approval** — append this approver's ``approve`` decision
           to the request via ``add_approval`` (Requirement 14.3).
        4. **Evaluate the gate** — ``SINGLE_APPROVAL`` needs one recorded
           approval; ``FOUR_EYES`` needs ``four_eyes_satisfied`` (two distinct
           human approvers, neither the requester, each counted once —
           Requirements 14.1, 14.2, 14.6). If the threshold is not yet met, the
           request stays ``PENDING`` and is returned unchanged (Requirement 14.4,
           the promotion is not applied).
        5. **Apply on satisfaction** — set the request's ``Approval_State`` to
           ``APPROVED`` and apply the targeted transition via ``_apply_transition``
           (recording the distinct approvers, an ``approved`` ``Audit_Event`` and
           a ``Promotion_History`` entry — Requirements 14.4, 17.1, 18.1). When
           the target is ``PRODUCTION``, the incumbent ``Production_Model`` (if
           any and distinct from the target) is first demoted to ``DEPRECATED`` in
           the *same* transaction, so at most one ``PRODUCTION`` version ever
           exists for the ``Registered_Model`` (Requirements 16.1, 16.2; the
           partial unique index in design.md Section 7 backstops this under
           concurrency).

        The service never commits; the incumbent demotion and the target
        promotion are composed into the surrounding serializable transaction so
        they succeed or fail together (design.md Key Design Decision 7).

        Args:
            request_id: The ``Promotion_Request`` to approve.
            approver: The principal recording the approval.
            approver_kind: Whether the approver is a human or automated principal.
            correlation_id: The request correlation identifier (for the audit).
            reason: An optional human-readable note attached to the approval.

        Returns:
            The updated ``PromotionRequest`` while the request remains ``PENDING``
            (threshold not yet met), or the promoted ``ModelVersion`` once the
            gate is satisfied and the transition is applied.

        Raises:
            PromotionRequestNotFoundError: if no such ``Promotion_Request`` exists.
            PromotionRequestNotPendingError: if the request is already settled.
            AutomatedApprovalNotPermittedError: if a non-human principal attempts
                to approve a transition into ``PRODUCTION``.
            ModelVersionNotFoundError: if the request's ``Model_Version`` is gone.
        """
        request = await self._promotions.get_request(request_id)
        if request is None:
            raise PromotionRequestNotFoundError(request_id)
        if request.approval_state is not ApprovalState.PENDING:
            raise PromotionRequestNotPendingError(request_id, request.approval_state)

        # Resolve the gate governing this already-legal edge (design KDD 6);
        # is_rollback is carried on the request so rollback promotions (also
        # Four_Eyes) are evaluated identically.
        gate = gate_for(request.from_state, request.to_state, request.is_rollback)

        # An automated principal can never satisfy — nor even contribute to — the
        # Four_Eyes control for a PRODUCTION transition (Req 14.7, 21.2).
        if (
            request.to_state is LifecycleState.PRODUCTION
            and approver_kind is not PrincipalKind.HUMAN
        ):
            raise AutomatedApprovalNotPermittedError(request_id, approver)

        now = self._clock.now()
        updated_request = await self._promotions.add_approval(
            request_id,
            Approval(
                approver=approver,
                approver_kind=approver_kind,
                decision=DECISION_APPROVE,
                reason=reason,
                created_at=now,
            ),
        )

        if not self._gate_satisfied(gate, updated_request):
            # Threshold not yet met: keep PENDING and do not apply (Req 14.4).
            return updated_request

        # Threshold met: settle the request and apply the transition atomically.
        await self._promotions.set_request_state(request_id, ApprovalState.APPROVED)

        # The distinct principals who approved, recorded on the Promotion_History
        # entry (Req 17.1) — sorted for a stable, deterministic ordering.
        approvers = tuple(
            sorted(
                {a.approver for a in updated_request.approvals if a.decision == DECISION_APPROVE}
            )
        )

        model_version = await self._model_versions.get(request.model_name, request.version)
        if model_version is None:
            raise ModelVersionNotFoundError(request.model_name, request.version)

        # Single-PRODUCTION invariant: demote the incumbent (if any, and not the
        # target itself) to DEPRECATED in the same transaction before promoting
        # the target (Req 16.1, 16.2, design Section 7).
        if request.to_state is LifecycleState.PRODUCTION:
            incumbent = await self._model_versions.resolve_production(request.model_name)
            if incumbent is not None and incumbent.version != request.version:
                await self._apply_transition(
                    model_version=incumbent,
                    to_state=LifecycleState.DEPRECATED,
                    requester=request.requester,
                    approvers=approvers,
                    justification=(
                        f"Automatically demoted: superseded by {request.model_name} "
                        f"v{request.version} promotion to PRODUCTION (Requirement 16.2)."
                    ),
                    correlation_id=correlation_id,
                    is_rollback=False,
                    now=now,
                    audit_action=AUDIT_ACTION_APPROVED,
                )

        return await self._apply_transition(
            model_version=model_version,
            to_state=request.to_state,
            requester=request.requester,
            approvers=approvers,
            justification=request.justification,
            correlation_id=correlation_id,
            is_rollback=request.is_rollback,
            now=now,
            audit_action=AUDIT_ACTION_APPROVED,
        )

    async def reject(
        self,
        *,
        request_id: int,
        approver: str,
        approver_kind: PrincipalKind,
        reason: str,
        correlation_id: str,
    ) -> PromotionRequest:
        """Reject a ``PENDING`` ``Promotion_Request``, leaving the lifecycle unchanged.

        Records the approver's ``reject`` decision (with its reason), sets the
        request's ``Approval_State`` to ``REJECTED``, and appends a ``rejected``
        ``Audit_Event`` carrying the reason as its justification — without
        touching the ``Model_Version``'s ``Lifecycle_State`` (Requirements 14.5,
        18.1, 20.5). All writes join the surrounding transaction (Key Design
        Decision 9); the service never commits.

        Args:
            request_id: The ``Promotion_Request`` to reject.
            approver: The principal recording the rejection.
            approver_kind: Whether the approver is a human or automated principal.
            reason: The human-readable rejection reason (recorded, Requirement 20.5).
            correlation_id: The request correlation identifier (for the audit).

        Returns:
            The updated ``PromotionRequest`` in ``REJECTED`` state.

        Raises:
            PromotionRequestNotFoundError: if no such ``Promotion_Request`` exists.
            PromotionRequestNotPendingError: if the request is already settled.
        """
        request = await self._promotions.get_request(request_id)
        if request is None:
            raise PromotionRequestNotFoundError(request_id)
        if request.approval_state is not ApprovalState.PENDING:
            raise PromotionRequestNotPendingError(request_id, request.approval_state)

        now = self._clock.now()
        await self._promotions.add_approval(
            request_id,
            Approval(
                approver=approver,
                approver_kind=approver_kind,
                decision=DECISION_REJECT,
                reason=reason,
                created_at=now,
            ),
        )
        updated_request = await self._promotions.set_request_state(
            request_id, ApprovalState.REJECTED
        )
        # Lifecycle state is left unchanged; the rejection reason is recorded on
        # the audit event (Req 14.5, 20.5, 18.1).
        await self._audit.append(
            AuditEvent(
                action=AUDIT_ACTION_REJECTED,
                actor=approver,
                model_name=request.model_name,
                version=request.version,
                before_state=request.from_state.value,
                after_state=request.from_state.value,  # lifecycle unchanged (Req 14.5)
                justification=reason,
                correlation_id=correlation_id,
                created_at=now,
            )
        )
        return updated_request

    async def rollback(
        self,
        *,
        model_name: str,
        version: int,
        requester: str,
        justification: str,
        correlation_id: str,
    ) -> PromotionRequest:
        """Request a governed rollback that re-promotes a demoted version to ``PRODUCTION``.

        A rollback re-designates a previously-``PRODUCTION``, currently-``DEPRECATED``
        ``Model_Version`` as the ``Production_Model`` (design.md Key Design
        Decision 5, Requirement 15). Like any promotion into ``PRODUCTION`` it is
        governed by ``Four_Eyes`` (Requirement 15.2), so this entry point does
        **not** apply the transition itself — it records a ``PENDING``
        ``Promotion_Request`` on the rollback edge (``DEPRECATED → PRODUCTION``,
        ``is_rollback=True``) that the existing ``approve`` path applies once two
        distinct human approvers are recorded. Modelling it this way lets
        ``approve`` reuse its ``FOUR_EYES`` gate (``gate_for`` returns
        ``FOUR_EYES`` for the rollback edge) and its incumbent-demotion
        ``PRODUCTION`` branch (Requirements 15.3, 16.1, 16.2), and lets
        ``_apply_transition`` stamp the resulting ``Promotion_History`` entry as a
        rollback (Requirement 15.5). The steps, in order:

        1. **Resolve the version** — fetch the ``Model_Version``; an unknown
           ``(model_name, version)`` pair is rejected with
           ``ModelVersionNotFoundError`` (Requirement 19.10).
        2. **Assert rollback legality** — call
           ``transition_allowed(from, PRODUCTION, is_rollback=True)``, whose only
           permitted edge is ``DEPRECATED → PRODUCTION``; any other current state
           raises ``IllegalTransitionError`` (Requirements 11.3, 15.1), so a
           rollback can only be initiated from ``DEPRECATED``.
        3. **Assert prior production** — inspect the version's ``Promotion_History``;
           unless some entry records a transition *into* ``PRODUCTION`` (the
           version was genuinely a ``Production_Model`` before), reject with
           ``NeverInProductionError`` and record the reason (Requirement 15.4).
        4. **Park a PENDING rollback request** — create a ``PENDING``
           ``Promotion_Request`` on the ``DEPRECATED → PRODUCTION`` edge flagged
           ``is_rollback=True`` and append a ``rollback_requested`` ``Audit_Event``
           (Requirements 15.1, 15.2, 18.1). The lifecycle state is left unchanged
           while the request is ``PENDING``; ``approve`` applies the rollback and
           demotes the incumbent later.

        The service never commits; every write above joins the surrounding
        request-scoped transaction (design.md Key Design Decision 9).

        Args:
            model_name: The composite ``{dataset_name}__{model_type}`` name.
            version: The ``Model_Version`` to roll back to ``PRODUCTION``.
            requester: The principal requesting the rollback (recorded as the
                request's requester and the audit actor); it may not later count
                toward the Four_Eyes threshold (Requirement 14.2).
            justification: The human-readable reason for the rollback.
            correlation_id: The request correlation identifier (for the audit).

        Returns:
            The newly created ``PENDING`` ``PromotionRequest`` awaiting Four_Eyes
            approval.

        Raises:
            ModelVersionNotFoundError: if no such ``Model_Version`` exists.
            IllegalTransitionError: if the version is not currently ``DEPRECATED``
                (the only legal source of the rollback edge).
            NeverInProductionError: if the version was never in ``PRODUCTION``.
        """
        model_version = await self._model_versions.get(model_name, version)
        if model_version is None:
            raise ModelVersionNotFoundError(model_name, version)

        from_state = model_version.lifecycle_state

        # Rollback legality: the only permitted edge is DEPRECATED -> PRODUCTION,
        # so any other current state raises IllegalTransitionError (Req 11.3, 15.1).
        transition_allowed(from_state, LifecycleState.PRODUCTION, is_rollback=True)

        # A rollback may only re-promote a version that was genuinely PRODUCTION
        # before: require some Promotion_History entry recording a transition into
        # PRODUCTION, else reject with the reason recorded (Req 15.4).
        history = await self._promotions.list_history(model_name, version)
        was_in_production = any(entry.to_state is LifecycleState.PRODUCTION for entry in history)
        if not was_in_production:
            raise NeverInProductionError(model_name, version)

        # Park a PENDING rollback request on the DEPRECATED -> PRODUCTION edge,
        # flagged is_rollback=True so approve() applies Four_Eyes and the
        # incumbent-demotion PRODUCTION branch (Req 15.2, 15.3, 16.1, 16.2). The
        # lifecycle state is left unchanged while PENDING; history records only
        # applied transitions (Req 17.1), so no entry is written here.
        now = self._clock.now()
        request = await self._promotions.create_request(
            PromotionRequest(
                model_name=model_name,
                version=version,
                from_state=LifecycleState.DEPRECATED,
                to_state=LifecycleState.PRODUCTION,
                requester=requester,
                justification=justification,
                approval_state=ApprovalState.PENDING,
                is_rollback=True,
                created_at=now,
            )
        )
        await self._audit.append(
            AuditEvent(
                action=AUDIT_ACTION_ROLLBACK_REQUESTED,
                actor=requester,
                model_name=model_name,
                version=version,
                before_state=from_state.value,
                after_state=from_state.value,  # lifecycle unchanged while PENDING
                justification=justification,
                correlation_id=correlation_id,
                created_at=now,
            )
        )
        return request

    def _gate_satisfied(self, gate: GateKind, request: PromotionRequest) -> bool:
        """Return ``True`` iff the recorded approvals satisfy ``gate`` for ``request``.

        ``SINGLE_APPROVAL`` is met by a single recorded ``approve`` decision;
        ``FOUR_EYES`` delegates to ``four_eyes_satisfied`` (two distinct human
        approvers, neither the requester, each counted once — Requirements 14.1,
        14.2, 14.6, 14.7, 21.2). ``NONE``/``EVIDENCE`` gates never route through
        ``approve`` (they apply immediately in ``request_transition`` and create
        no ``PENDING`` request), so they are never considered satisfied here.
        """
        if gate is GateKind.FOUR_EYES:
            return four_eyes_satisfied(request, request.approvals)
        if gate is GateKind.SINGLE_APPROVAL:
            return any(a.decision == DECISION_APPROVE for a in request.approvals)
        return False


class RegistryQueryService:
    """Read-only façade serving every downstream consumer from the Registry itself.

    The Model Registry is the single source of truth for trained models: once a
    ``Model_Version`` is registered, downstream services read its metadata,
    metrics, lineage, promotion history, audit trail, and artifact bytes *only*
    from the Registry — never from the Training Pipeline (Requirements 1.4,
    28.7). This service is the pure, side-effect-free read half of that
    contract: thin wrappers over the read ports (the repositories, the
    ``Artifact_Store`` and the ``ArtifactSigner``) that translate a missing
    ``(model_name, version)`` into a typed ``ModelVersionNotFoundError`` (mapped
    to a 404 upstream, Requirement 19.10) and, for artifact retrieval, refuse to
    serve any bytes whose signature or checksum no longer verifies (Requirements
    7.4, 7.5, 21.3).

    Like ``ModelRegistryService`` it never opens a transaction of its own; reads
    are served within the surrounding request-scoped session (design.md Section
    5.1).
    """

    def __init__(
        self,
        *,
        model_version_repository: ModelVersionRepository,
        promotion_repository: PromotionRepository,
        audit_repository: AuditRepository,
        artifact_store: ArtifactStore,
        artifact_signer: ArtifactSigner,
    ) -> None:
        self._model_versions = model_version_repository
        self._promotions = promotion_repository
        self._audit = audit_repository
        self._artifacts = artifact_store
        self._signer = artifact_signer

    async def get_model_version(self, model_name: str, version: int) -> ModelVersion:
        """Return the ``Model_Version`` for ``(model_name, version)``.

        Raises:
            ModelVersionNotFoundError: if no such ``Model_Version`` exists,
                so the caller can surface a typed 404 (Requirement 19.10).
        """
        model_version = await self._model_versions.get(model_name, version)
        if model_version is None:
            raise ModelVersionNotFoundError(model_name, version)
        return model_version

    async def list_model_versions(
        self,
        *,
        model_name: str | None = None,
        lifecycle_state: LifecycleState | None = None,
    ) -> list[ModelVersion]:
        """List ``Model_Version`` records, optionally filtered by name and/or state.

        With no filters this returns every recorded version; ``model_name``
        restricts the result to one ``Registered_Model`` and ``lifecycle_state``
        to a single lifecycle state (Requirements 19.2, 19.3). An empty list is a
        valid result, not an error.
        """
        return await self._model_versions.list(
            model_name=model_name, lifecycle_state=lifecycle_state
        )

    async def get_metrics(self, model_name: str, version: int) -> MetricsRecord:
        """Return the recorded ``Metrics`` for one ``Model_Version`` (Requirements 10.2, 19.4).

        The metrics were pinned onto the ``Model_Version`` at registration and
        are immutable thereafter; this is a pure read of that recorded value.

        Raises:
            ModelVersionNotFoundError: if no such ``Model_Version`` exists.
        """
        model_version = await self.get_model_version(model_name, version)
        return model_version.metrics

    async def get_lineage(self, model_name: str, version: int) -> Lineage:
        """Return the read-only ``Lineage`` view for one ``Model_Version`` (Requirement 9.2).

        Assembled from the immutable provenance recorded on the ``Model_Version``
        (its dataset version, feature versions, git commit, training run and
        training timestamp — carried in the version's
        ``reproducibility_metadata``), so it reproduces exactly what was pinned
        at registration without re-contacting the Training Pipeline
        (Requirements 9.1, 9.4).

        Raises:
            ModelVersionNotFoundError: if no such ``Model_Version`` exists.
        """
        model_version = await self.get_model_version(model_name, version)
        repro = model_version.reproducibility_metadata
        return Lineage(
            model_name=model_version.model_name,
            model_version=model_version.version,
            dataset_version=model_version.dataset_version,
            feature_versions=dict(model_version.feature_versions),
            git_commit=model_version.git_commit,
            training_run_id=repro.training_run_id,
            trained_at=repro.trained_at,
        )

    async def resolve_production(self, model_name: str) -> ModelVersion | None:
        """Return the unique ``PRODUCTION`` ``Model_Version`` for ``model_name``, or ``None``.

        Resolving the current ``Production_Model`` is a first-class read
        (Requirements 16.3, 16.4, 19.7); ``None`` means no version is currently
        in ``PRODUCTION``, which the caller reports as such rather than as an
        error.
        """
        return await self._model_versions.resolve_production(model_name)

    async def get_promotion_history(
        self, model_name: str, version: int
    ) -> list[PromotionHistoryEntry]:
        """Return the ordered ``Promotion_History`` for one ``Model_Version``.

        History is append-only and records every applied transition (including
        rollbacks, flagged as such) in order (Requirements 17.1, 17.3, 19.8). An
        empty list means no transition has yet been applied to the version.
        """
        return await self._promotions.list_history(model_name, version)

    async def get_audit_history(
        self,
        *,
        model_name: str | None = None,
        correlation_id: str | None = None,
    ) -> list[AuditEvent]:
        """Return the append-only ``Audit_Event`` trail, optionally filtered.

        With no filters this returns every recorded privileged action;
        ``model_name`` restricts the trail to one ``Registered_Model`` and
        ``correlation_id`` to a single request correlation identifier
        (Requirements 18.3, 19.8).
        """
        return await self._audit.list(model_name=model_name, correlation_id=correlation_id)

    async def get_artifact(self, model_name: str, version: int) -> bytes:
        """Return the verified ``Model_Artifact`` bytes for one ``Model_Version``.

        Serves the artifact only after re-establishing both of the Registry's
        integrity guarantees on the *stored* bytes (never contacting the
        Training Pipeline, Requirement 1.4):

        1. **Resolve the version** — fetch the ``Model_Version``; an unknown
           ``(model_name, version)`` pair is rejected with
           ``ModelVersionNotFoundError`` so the caller can surface a typed 404
           (Requirement 19.10).
        2. **Read the stored bytes** — read the artifact back from the Registry's
           own ``Artifact_Store`` (Requirements 8.2, 8.3, 19.5).
        3. **Verify the signature** — where signing is configured the
           ``ArtifactSigner`` refuses an unsigned or invalid artifact by raising
           ``SignatureVerificationError``; where it is not, verification is a
           tolerant no-op (Requirement 21.3).
        4. **Verify the checksum** — recompute the checksum of the read bytes
           with the version's recorded algorithm and compare it against the
           recorded ``Model_Checksum``; on any mismatch refuse to serve and raise
           ``ArtifactIntegrityError`` (Requirements 7.4, 7.5).

        Returns:
            The exact, integrity-verified artifact bytes.

        Raises:
            ModelVersionNotFoundError: if no such ``Model_Version`` exists.
            SignatureVerificationError: if signing is configured and the stored
                artifact is unsigned or its signature is invalid.
            ArtifactIntegrityError: if the stored bytes no longer match the
                recorded ``Model_Checksum``.
        """
        model_version = await self.get_model_version(model_name, version)
        data = await self._artifacts.read_artifact(model_name, version)

        # Refuse unsigned/invalid artifacts before verifying the checksum, where
        # signing is configured (Req 21.3); a tolerant no-op otherwise.
        await self._signer.verify_artifact(model_name, version, data)

        # Re-establish the integrity guarantee on the stored bytes: recompute and
        # compare against the recorded Model_Checksum, refusing on mismatch
        # (Req 7.4, 7.5).
        if not verify_checksum(
            data, model_version.artifact_checksum, model_version.checksum_algorithm
        ):
            actual = compute_checksum(data, model_version.checksum_algorithm)
            raise ArtifactIntegrityError(
                model_name,
                version,
                model_version.checksum_algorithm,
                model_version.artifact_checksum,
                actual,
            )
        return data
