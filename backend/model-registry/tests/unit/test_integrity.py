"""Unit + property tests for the Integrity_Verifier (task 8.2).

Exercises ``domain/integrity.py`` in isolation: the pure ``compute_checksum``
and ``verify_checksum`` primitives that gate both artifact ingestion
(Property 7) and artifact retrieval (Property 8), plus the shape of the
``ChecksumMismatchError`` and ``ArtifactIntegrityError`` exceptions that the
service layer raises on each edge.
"""

from __future__ import annotations

import hashlib

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from aqros_model_registry.domain.integrity import (
    ArtifactIntegrityError,
    ChecksumMismatchError,
    UnsupportedChecksumAlgorithmError,
    compute_checksum,
    verify_checksum,
)

_ALGORITHMS = ("sha256", "sha1", "md5")
_HEX_DIGITS = "0123456789abcdef"


def _flip_hex_char(checksum: str) -> str:
    """Return ``checksum`` with its first hex character changed to a different one.

    Produces a checksum string that is guaranteed to differ from the input,
    without relying on generating a second, independently-computed digest
    (which would require reasoning about hash collisions between distinct
    byte strings).
    """
    first = checksum[0]
    new_char = _HEX_DIGITS[(_HEX_DIGITS.index(first) + 1) % len(_HEX_DIGITS)]
    return new_char + checksum[1:]


# Feature: model-registry, Property 7: Checksum gate on ingestion
# For any downloaded artifact whose computed checksum != the reported
# checksum, registration is rejected and nothing is persisted.
# Validates: Requirements 7.1, 7.2
#
# Feature: model-registry, Property 8: Checksum gate on retrieval; never overwrite
# For any stored artifact, retrieval verifies bytes against the recorded
# checksum and refuses on mismatch; a second write to an existing
# (model_name, version) is rejected and the original bytes are unchanged.
# Validates: Requirements 7.3, 7.4, 7.5, 7.6
#
# The same primitive (`compute_checksum` + `verify_checksum`) underlies both
# the ingestion-time gate (Property 7) and the retrieval-time gate
# (Property 8), so a matching checksum must verify True on both edges.
@settings(max_examples=100)
@given(data=st.binary(max_size=512), algorithm=st.sampled_from(_ALGORITHMS))
def test_checksum_round_trip_holds_across_algorithms(data: bytes, algorithm: str) -> None:
    checksum = compute_checksum(data, algorithm)
    assert verify_checksum(data, checksum, algorithm) is True


# Feature: model-registry, Property 7: Checksum gate on ingestion
# For any downloaded artifact whose computed checksum != the reported
# checksum, registration is rejected and nothing is persisted.
# Validates: Requirements 7.1, 7.2
#
# Feature: model-registry, Property 8: Checksum gate on retrieval; never overwrite
# For any stored artifact, retrieval verifies bytes against the recorded
# checksum and refuses on mismatch; a second write to an existing
# (model_name, version) is rejected and the original bytes are unchanged.
# Validates: Requirements 7.3, 7.4, 7.5, 7.6
#
# A checksum that differs from the correct one must never verify True — this
# is the failure mode that triggers `ChecksumMismatchError` on ingest and
# `ArtifactIntegrityError` on retrieval.
@settings(max_examples=100)
@given(data=st.binary(max_size=512), algorithm=st.sampled_from(_ALGORITHMS))
def test_mismatched_checksum_never_verifies(data: bytes, algorithm: str) -> None:
    correct = compute_checksum(data, algorithm)
    wrong = _flip_hex_char(correct)
    assert wrong != correct
    assert verify_checksum(data, wrong, algorithm) is False


def test_known_match_verifies_true() -> None:
    """A concrete known-good example: verify_checksum matches a real digest."""
    data = b"model-artifact-bytes"
    checksum = hashlib.sha256(data).hexdigest()
    assert verify_checksum(data, checksum, "sha256") is True


def test_known_mismatch_verifies_false() -> None:
    """A concrete known-bad example: a wrong checksum fails verification."""
    data = b"model-artifact-bytes"
    wrong_checksum = hashlib.sha256(b"different-bytes").hexdigest()
    assert verify_checksum(data, wrong_checksum, "sha256") is False


def test_compute_checksum_is_case_insensitive_on_algorithm_name() -> None:
    data = b"model-artifact-bytes"
    assert compute_checksum(data, "SHA256") == compute_checksum(data, "sha256")


def test_verify_checksum_is_case_insensitive_on_expected_digest() -> None:
    data = b"model-artifact-bytes"
    checksum = hashlib.sha256(data).hexdigest()
    assert verify_checksum(data, checksum.upper(), "sha256") is True


def test_unsupported_algorithm_raises_on_compute() -> None:
    with pytest.raises(UnsupportedChecksumAlgorithmError) as exc_info:
        compute_checksum(b"data", "not-a-real-algorithm")
    assert exc_info.value.algorithm == "not-a-real-algorithm"


def test_unsupported_algorithm_raises_on_verify() -> None:
    with pytest.raises(UnsupportedChecksumAlgorithmError) as exc_info:
        verify_checksum(b"data", "deadbeef", "not-a-real-algorithm")
    assert exc_info.value.algorithm == "not-a-real-algorithm"


def test_checksum_mismatch_error_carries_expected_attributes() -> None:
    error = ChecksumMismatchError(algorithm="sha256", expected="abc123", actual="def456")
    assert error.algorithm == "sha256"
    assert error.expected == "abc123"
    assert error.actual == "def456"


def test_artifact_integrity_error_carries_expected_attributes() -> None:
    error = ArtifactIntegrityError(
        model_name="aapl_5d_direction__random_forest",
        model_version=3,
        algorithm="sha256",
        expected="abc123",
        actual="def456",
    )
    assert error.model_name == "aapl_5d_direction__random_forest"
    assert error.model_version == 3
    assert error.algorithm == "sha256"
    assert error.expected == "abc123"
    assert error.actual == "def456"
