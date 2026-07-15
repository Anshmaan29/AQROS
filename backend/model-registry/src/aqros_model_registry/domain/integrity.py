"""The pure ``Integrity_Verifier`` for the Model Registry (Requirement 7).

Every ``Model_Artifact`` the Registry persists must be exactly the bytes the
Training Pipeline trained, and every artifact the Registry serves must be
exactly the bytes it persisted. This module provides the two pure primitives
that enforce that guarantee on both edges:

* ``compute_checksum(data, algorithm)`` — the cryptographic checksum of some
  bytes, computed with the algorithm *named in the* ``Trained_Model_Record``
  (Requirement 7.1), so the Registry never assumes an algorithm the source did
  not use.
* ``verify_checksum(data, expected, algorithm)`` — a constant-time comparison
  of freshly-computed bytes against a recorded checksum, used both on
  ingestion (Requirement 7.1) and on retrieval (Requirement 7.4).

Two named errors express the two failure edges from design.md Section 13:

* ``ChecksumMismatchError`` — an *ingested* artifact's computed checksum does
  not equal the checksum reported by the Training Pipeline; registration is
  rejected and nothing is persisted (Requirement 7.2).
* ``ArtifactIntegrityError`` — a *stored* artifact's bytes no longer match the
  recorded ``Model_Checksum`` at retrieval time; the Registry refuses to serve
  it (Requirement 7.5).

This module is pure: no I/O, no framework dependencies, exhaustively
property-testable.
"""

from __future__ import annotations

import hashlib
import hmac


class UnsupportedChecksumAlgorithmError(ValueError):
    """Raised when the algorithm named in a record is not a known hash algorithm.

    The algorithm name comes from the ``Trained_Model_Record`` (Requirement
    7.1); an unrecognised name cannot be trusted to verify integrity, so it is
    surfaced explicitly rather than silently defaulting to another algorithm.
    """

    def __init__(self, algorithm: str) -> None:
        self.algorithm = algorithm
        super().__init__(f"Unsupported checksum algorithm: {algorithm!r}.")


class ChecksumMismatchError(RuntimeError):
    """Raised on ingestion when a downloaded artifact fails its checksum check.

    The computed checksum of the downloaded ``Model_Artifact`` did not equal
    the checksum reported by the Training Pipeline; registration is rejected
    and no ``Model_Version`` is persisted (Requirement 7.2). Carries the
    algorithm, the expected checksum, and the actually-computed checksum so
    callers can record a human-readable rejection reason (Requirement 20.5).
    """

    def __init__(self, algorithm: str, expected: str, actual: str) -> None:
        self.algorithm = algorithm
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Artifact checksum mismatch on ingestion ({algorithm}): "
            f"expected {expected}, computed {actual}."
        )


class ArtifactIntegrityError(RuntimeError):
    """Raised on retrieval when stored artifact bytes fail their checksum check.

    The bytes read back from the ``Artifact_Store`` no longer match the
    recorded ``Model_Checksum``; the Registry refuses to serve the artifact
    and records an integrity-failure error (Requirement 7.5). Carries the
    offending ``(model_name, model_version)`` along with the algorithm and both
    checksums for the recorded reason.
    """

    def __init__(
        self,
        model_name: str,
        model_version: int,
        algorithm: str,
        expected: str,
        actual: str,
    ) -> None:
        self.model_name = model_name
        self.model_version = model_version
        self.algorithm = algorithm
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Artifact integrity failure for {model_name} v{model_version} "
            f"({algorithm}): recorded {expected}, computed {actual}."
        )


def compute_checksum(data: bytes, algorithm: str) -> str:
    """Return the hex-digest checksum of ``data`` under the named ``algorithm``.

    ``algorithm`` is the algorithm named in the ``Trained_Model_Record``
    (Requirement 7.1); it is resolved case-insensitively against the hash
    algorithms guaranteed available by :mod:`hashlib`.

    Raises:
        UnsupportedChecksumAlgorithmError: if ``algorithm`` names no known hash
            algorithm.
    """
    try:
        digest = hashlib.new(algorithm.lower())
    except (ValueError, TypeError) as exc:
        raise UnsupportedChecksumAlgorithmError(algorithm) from exc
    digest.update(data)
    return digest.hexdigest()


def verify_checksum(data: bytes, expected: str, algorithm: str) -> bool:
    """Return ``True`` if ``data`` checksums to ``expected`` under ``algorithm``.

    Recomputes the checksum of ``data`` with ``compute_checksum`` and compares
    it against ``expected`` in constant time (case-insensitively on the hex
    digest). Used on ingestion (Requirement 7.1) and on retrieval
    (Requirement 7.4). Neither edge is decided here — the caller raises the
    appropriate ``ChecksumMismatchError`` or ``ArtifactIntegrityError`` on a
    ``False`` result.

    Raises:
        UnsupportedChecksumAlgorithmError: if ``algorithm`` names no known hash
            algorithm.
    """
    actual = compute_checksum(data, algorithm)
    return hmac.compare_digest(actual.lower(), expected.strip().lower())
