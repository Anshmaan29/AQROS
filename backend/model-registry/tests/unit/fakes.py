"""In-memory fakes for every port, shared across unit tests (task 8.1).

No real HTTP, filesystem, or database access — these implement the same
port ABCs as the real adapters so the domain logic can be exercised in
isolation (Requirement 26.1). Mirrors the organization of
``aqros_training_pipeline``'s ``tests/unit/fakes.py``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from aqros_model_registry.domain.models import (
    Approval,
    ApprovalState,
    AuditEvent,
    LifecycleState,
    ModelVersion,
    PromotionHistoryEntry,
    PromotionRequest,
    TrainedModelRecord,
    ValidationEvidence,
)
from aqros_model_registry.domain.ports import (
    ArtifactAlreadyExistsError,
    ArtifactSigner,
    ArtifactStore,
    AuditRepository,
    Clock,
    ModelVersionRepository,
    PromotionRepository,
    SignatureVerificationError,
    TrainedModelNotFoundError,
    TrainingPipelineClient,
    UpstreamSourceError,
)


class FakeTrainingPipelineClient(TrainingPipelineClient):
    """Serves preloaded records/artifacts, or raises to order for given keys.

    ``missing`` keys raise ``TrainedModelNotFoundError`` (simulating a 404
    from the Training Pipeline); ``upstream_error`` keys raise
    ``UpstreamSourceError`` (simulating unreachability/non-404 errors).
    """

    def __init__(
        self,
        *,
        records: dict[tuple[str, int], TrainedModelRecord] | None = None,
        artifacts: dict[tuple[str, int], bytes] | None = None,
        missing: set[tuple[str, int]] | None = None,
        upstream_error: set[tuple[str, int]] | None = None,
    ) -> None:
        self._records: dict[tuple[str, int], TrainedModelRecord] = dict(records or {})
        self._artifacts: dict[tuple[str, int], bytes] = dict(artifacts or {})
        self._missing = set(missing or set())
        self._upstream_error = set(upstream_error or set())
        self.calls: list[str] = []

    def _guard(self, key: tuple[str, int]) -> None:
        if key in self._missing:
            raise TrainedModelNotFoundError(f"fake: no trained model for {key}")
        if key in self._upstream_error:
            raise UpstreamSourceError(f"fake: upstream error for {key}")

    async def get_trained_model_record(self, model_name: str, version: int) -> TrainedModelRecord:
        self.calls.append("get_trained_model_record")
        key = (model_name, version)
        self._guard(key)
        record = self._records.get(key)
        if record is None:
            raise TrainedModelNotFoundError(f"fake: no trained model for {key}")
        return record

    async def download_artifact(self, model_name: str, version: int) -> bytes:
        self.calls.append("download_artifact")
        key = (model_name, version)
        self._guard(key)
        artifact = self._artifacts.get(key)
        if artifact is None:
            raise TrainedModelNotFoundError(f"fake: no artifact for {key}")
        return artifact


class FakeArtifactStore(ArtifactStore):
    """Dict-backed artifact store enforcing write-once semantics."""

    def __init__(self, *, fail_on: set[str] | None = None) -> None:
        self._store: dict[tuple[str, int], bytes] = {}
        self._fail_on = fail_on or set()

    async def write_artifact(self, model_name: str, model_version: int, data: bytes) -> str:
        if model_name in self._fail_on:
            raise RuntimeError(f"fake: artifact write failed for {model_name}")
        key = (model_name, model_version)
        if key in self._store:
            raise ArtifactAlreadyExistsError(f"fake: {key} exists")
        self._store[key] = data
        return f"memory://{model_name}/v{model_version}"

    async def read_artifact(self, model_name: str, model_version: int) -> bytes:
        key = (model_name, model_version)
        if key not in self._store:
            raise FileNotFoundError(f"fake: no artifact for {key}")
        return self._store[key]


class FakeModelVersionRepository(ModelVersionRepository):
    """List-backed model-version repository assigning sequential ids."""

    def __init__(self) -> None:
        self._versions: list[ModelVersion] = []
        self._next_id = 1

    async def create_model_version(self, model_version: ModelVersion) -> ModelVersion:
        stored = replace(model_version, id=self._next_id)
        self._next_id += 1
        self._versions.append(stored)
        return stored

    async def get(self, model_name: str, version: int) -> ModelVersion | None:
        for mv in self._versions:
            if mv.model_name == model_name and mv.version == version:
                return mv
        return None

    async def list(
        self,
        *,
        model_name: str | None = None,
        lifecycle_state: LifecycleState | None = None,
    ) -> list[ModelVersion]:
        result = list(self._versions)
        if model_name is not None:
            result = [mv for mv in result if mv.model_name == model_name]
        if lifecycle_state is not None:
            result = [mv for mv in result if mv.lifecycle_state == lifecycle_state]
        return result

    async def set_lifecycle_state(
        self, model_name: str, version: int, state: LifecycleState
    ) -> ModelVersion:
        for index, mv in enumerate(self._versions):
            if mv.model_name == model_name and mv.version == version:
                updated = replace(mv, lifecycle_state=state)
                self._versions[index] = updated
                return updated
        raise KeyError(f"fake: no model version for {(model_name, version)}")

    async def get_latest_version(self, model_name: str) -> int | None:
        versions = [mv.version for mv in self._versions if mv.model_name == model_name]
        return max(versions) if versions else None

    async def resolve_production(self, model_name: str) -> ModelVersion | None:
        for mv in self._versions:
            if mv.model_name == model_name and mv.lifecycle_state == LifecycleState.PRODUCTION:
                return mv
        return None

    async def attach_validation_evidence(
        self, model_name: str, version: int, evidence: ValidationEvidence
    ) -> ModelVersion:
        for index, mv in enumerate(self._versions):
            if mv.model_name == model_name and mv.version == version:
                updated = replace(mv, validation_evidence=evidence)
                self._versions[index] = updated
                return updated
        raise KeyError(f"fake: no model version for {(model_name, version)}")


class FakePromotionRepository(PromotionRepository):
    """List-backed promotion repository with per-request approvals and history."""

    def __init__(self) -> None:
        self._requests: dict[int, PromotionRequest] = {}
        self._next_id = 1
        self._history: list[PromotionHistoryEntry] = []

    async def create_request(self, request: PromotionRequest) -> PromotionRequest:
        request_id = self._next_id
        self._next_id += 1
        stored = replace(request, id=request_id)
        self._requests[request_id] = stored
        return stored

    async def add_approval(self, request_id: int, approval: Approval) -> PromotionRequest:
        request = self._requests.get(request_id)
        if request is None:
            raise KeyError(f"fake: no promotion request for id {request_id}")
        updated = replace(request, approvals=(*request.approvals, approval))
        self._requests[request_id] = updated
        return updated

    async def set_request_state(self, request_id: int, state: ApprovalState) -> PromotionRequest:
        request = self._requests.get(request_id)
        if request is None:
            raise KeyError(f"fake: no promotion request for id {request_id}")
        updated = replace(request, approval_state=state)
        self._requests[request_id] = updated
        return updated

    async def get_request(self, request_id: int) -> PromotionRequest | None:
        return self._requests.get(request_id)

    async def append_history(self, entry: PromotionHistoryEntry) -> PromotionHistoryEntry:
        self._history.append(entry)
        return entry

    async def list_history(self, model_name: str, version: int) -> list[PromotionHistoryEntry]:
        return [
            entry
            for entry in self._history
            if entry.model_name == model_name and entry.version == version
        ]


class FakeAuditRepository(AuditRepository):
    """List-backed append-only audit repository."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    async def append(self, event: AuditEvent) -> AuditEvent:
        self._events.append(event)
        return event

    async def list(
        self,
        *,
        model_name: str | None = None,
        correlation_id: str | None = None,
    ) -> list[AuditEvent]:
        result = list(self._events)
        if model_name is not None:
            result = [e for e in result if e.model_name == model_name]
        if correlation_id is not None:
            result = [e for e in result if e.correlation_id == correlation_id]
        return result


class FakeClock(Clock):
    """Returns a fixed (or settable) current time for deterministic tests."""

    def __init__(self, *, fixed_time: datetime | None = None) -> None:
        self.fixed_time = fixed_time or datetime(2024, 1, 1)

    def now(self) -> datetime:
        return self.fixed_time


class FakeArtifactSigner(ArtifactSigner):
    """No-op signature verifier by default; configurable to raise on demand."""

    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.calls: list[tuple[str, int]] = []

    async def verify_artifact(self, model_name: str, model_version: int, data: bytes) -> None:
        self.calls.append((model_name, model_version))
        if self.should_fail:
            raise SignatureVerificationError(
                f"fake: signature verification failed for {(model_name, model_version)}"
            )
