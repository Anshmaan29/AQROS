"""Unit + property tests for the Pre_Training_Verifier (task 8.2)."""

from __future__ import annotations

import hashlib

from hypothesis import given, settings
from hypothesis import strategies as st

from aqros_training_pipeline.domain import verification
from tests.unit.builders import make_build_run, make_manifest


def _manifest_for(data: bytes, algorithm: str = "sha256"):
    digest = hashlib.new(algorithm, data).hexdigest()
    return make_manifest(checksum=digest, checksum_algorithm=algorithm)


def test_checksum_matches() -> None:
    data = b"dataset-bytes"
    assert verification.verify_checksum(_manifest_for(data), data) is True


def test_checksum_mismatch() -> None:
    manifest = _manifest_for(b"dataset-bytes")
    assert verification.verify_checksum(manifest, b"tampered") is False


def test_leakage_passed_true() -> None:
    result = verification.verify_leakage(make_build_run(leakage_audit_passed=True))
    assert result.passed is True


def test_leakage_passed_false_records_findings() -> None:
    result = verification.verify_leakage(
        make_build_run(leakage_audit_passed=False, findings=["future leak in f1"])
    )
    assert result.passed is False
    assert "future leak in f1" in (result.reason or "")


def test_leakage_passed_null_rejects() -> None:
    result = verification.verify_leakage(make_build_run(leakage_audit_passed=None))
    assert result.passed is False


# Feature: training-pipeline, Property 3: checksum-and-leakage AND-gate determines
# training eligibility — training proceeds iff the checksum matches AND
# leakage_audit_passed is True; every other combination rejects.
@settings(max_examples=100)
@given(
    checksum_matches=st.booleans(),
    leakage=st.sampled_from([True, False, None]),
)
def test_property_3_and_gate(checksum_matches: bool, leakage: bool | None) -> None:
    data = b"the-real-dataset-bytes"
    manifest = _manifest_for(data)
    downloaded = data if checksum_matches else b"corrupted"
    build_run = make_build_run(leakage_audit_passed=leakage, findings=["some finding"])

    result = verification.verify(manifest, downloaded, build_run)

    should_pass = checksum_matches and leakage is True
    assert result.passed is should_pass
    if not should_pass:
        assert result.reason
