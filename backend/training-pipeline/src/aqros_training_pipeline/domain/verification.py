"""The Pre_Training_Verifier — the pre-training "leakage-clearance gate."

Pure functions — no I/O. This is the sole gatekeeper standing between a
downloaded ``Dataset_Artifact`` and the ``Model_Trainer``: no training run
may proceed unless both of its checks pass (Requirement 4.4). It never
re-runs the Dataset Builder's own leakage audit itself — it only inspects
the ``leakage_audit_passed``/``leakage_audit_findings`` fields the Dataset
Builder already computed and published on the ``Dataset_Build_Run``
(Requirement 18.4) — exactly the same "trust but verify the certificate,
don't recompute it" boundary the Dataset Builder's own
``domain/validation.py`` establishes one layer down, for market/feature
data.

Checks performed, in order:

1. **Checksum verification** (Requirements 3.1-3.3) — the downloaded
   ``Dataset_Artifact`` bytes are hashed with the algorithm named in the
   ``Dataset_Manifest``'s ``checksum_algorithm`` field and compared against
   the manifest's recorded ``checksum``. A mismatch rejects the request
   without ever inspecting the leakage-audit fields.
2. **Leakage-audit gate** (Requirements 4.1-4.4, 18.4) — the
   ``Dataset_Build_Run``'s ``leakage_audit_passed`` field must be exactly
   ``True``; a value of ``False`` or ``None`` (``null``) rejects the
   request and records the build run's ``leakage_audit_findings`` as the
   rejection reason.

``verify`` AND-gates both checks: training is permitted to proceed if and
only if the checksum matches *and* ``leakage_audit_passed is True``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from aqros_training_pipeline.domain.models import DatasetBuildRun, DatasetManifest


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Outcome of one (or the combined) pre-training verification step.

    ``passed`` is ``True`` only when the check(s) it represents succeeded.
    ``reason`` is ``None`` when ``passed`` is ``True``, and otherwise holds
    a human-readable rejection reason suitable for recording directly on a
    ``TrainingRun`` (Requirement 3.2, 4.2) — for a leakage-audit failure,
    this is built from the ``Dataset_Build_Run``'s own
    ``leakage_audit_findings``.
    """

    passed: bool
    reason: str | None = None
    leakage_audit_findings: list[str] = field(default_factory=list)


def verify_checksum(manifest: DatasetManifest, downloaded_bytes: bytes) -> bool:
    """Compute the downloaded artifact's checksum and compare it to the manifest's.

    Uses the hash algorithm named in ``manifest.checksum_algorithm`` (e.g.
    ``"sha256"``) — never a hardcoded algorithm — so this stays correct even
    if the Dataset Builder changes its own default (Requirement 3.1).
    Returns ``True`` iff the computed digest equals ``manifest.checksum``
    exactly (Requirement 3.2/3.3).
    """
    hasher = hashlib.new(manifest.checksum_algorithm)
    hasher.update(downloaded_bytes)
    return hasher.hexdigest() == manifest.checksum


def verify_leakage(build_run: DatasetBuildRun) -> VerificationResult:
    """Inspect ``build_run.leakage_audit_passed`` and gate on it being exactly ``True``.

    A ``False`` or ``None`` (``null``) value rejects the request and
    records ``build_run.leakage_audit_findings`` as the rejection reason
    (Requirement 4.2); ``True`` permits training to proceed (Requirement
    4.3) — provided the checksum check has already passed (enforced by
    ``verify``, not by this function in isolation).
    """
    if build_run.leakage_audit_passed is True:
        return VerificationResult(passed=True)

    findings = list(build_run.leakage_audit_findings)
    if findings:
        reason = "leakage audit failed: " + "; ".join(findings)
    else:
        reason = (
            "leakage audit failed: leakage_audit_passed="
            f"{build_run.leakage_audit_passed!r} and no findings were recorded"
        )
    return VerificationResult(passed=False, reason=reason, leakage_audit_findings=findings)


def verify(
    manifest: DatasetManifest, downloaded_bytes: bytes, build_run: DatasetBuildRun
) -> VerificationResult:
    """AND-gate the checksum check and the leakage-audit check.

    Training is permitted to proceed (the returned ``VerificationResult``
    has ``passed=True``) if and only if the checksum matches *and*
    ``leakage_audit_passed is True`` (Requirement 4.4). The checksum check
    is evaluated first: a mismatch rejects immediately with a
    checksum-mismatch reason and the leakage-audit fields are never even
    inspected (Requirement 3.2). Only once the checksum matches does this
    permit — but not guarantee — progression to the leakage-audit check
    (Requirement 3.3), which may still independently reject the request,
    recording the build run's ``leakage_audit_findings`` as the reason
    (Requirement 4.2).
    """
    if not verify_checksum(manifest, downloaded_bytes):
        return VerificationResult(
            passed=False,
            reason=(
                "checksum mismatch: downloaded artifact does not match "
                f"manifest checksum (algorithm={manifest.checksum_algorithm!r})"
            ),
        )

    return verify_leakage(build_run)
