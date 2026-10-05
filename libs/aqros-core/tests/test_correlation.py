"""Tests for correlation-ID generation, validation, and propagation."""

from __future__ import annotations

import pytest

from aqros_core.correlation import (
    CORRELATION_HEADER,
    is_valid,
    new_correlation_id,
    resolve,
    sanitize,
)


class TestGenerate:
    def test_new_id_is_unique(self) -> None:
        ids = {new_correlation_id() for _ in range(1000)}
        assert len(ids) == 1000

    def test_new_id_is_valid(self) -> None:
        assert is_valid(new_correlation_id())

    def test_new_id_has_no_unsafe_chars(self) -> None:
        cid = new_correlation_id()
        assert cid.isalnum()


class TestValidate:
    @pytest.mark.parametrize(
        "candidate",
        [
            "abc123",
            "a" * 128,
            "550e8400-e29b-41d4-a716-446655440000",
            "trace_id-with_underscore",
        ],
    )
    def test_accepts_safe_ids(self, candidate: str) -> None:
        assert is_valid(candidate)

    @pytest.mark.parametrize(
        "candidate",
        [
            "",
            "a" * 129,  # too long: would bloat every log line downstream
            "has space",
            "has\nnewline",  # log/header injection
            "has\rreturn",
            'quote"inject',
            "semi;colon",
        ],
    )
    def test_rejects_unsafe_ids(self, candidate: str) -> None:
        assert not is_valid(candidate)

    def test_none_is_not_valid(self) -> None:
        assert not is_valid(None)


class TestSanitize:
    def test_strips_surrounding_whitespace(self) -> None:
        assert sanitize("  abc123  ") == "abc123"

    def test_returns_none_for_none(self) -> None:
        assert sanitize(None) is None

    @pytest.mark.parametrize("bad", ["has\nnewline", "a" * 200, ""])
    def test_drops_unusable_ids(self, bad: str) -> None:
        """Dropped rather than truncated.

        Truncating would echo a different value to different services, silently
        splitting one request into two traces.
        """
        assert sanitize(bad) is None

    def test_idempotent_on_safe_id(self) -> None:
        assert sanitize(sanitize("abc123")) == "abc123"


class TestResolve:
    def test_reuses_valid_inbound_id(self) -> None:
        assert resolve("inbound-123") == "inbound-123"

    def test_mints_new_when_missing(self) -> None:
        assert resolve(None) != resolve(None)

    def test_mints_new_when_malformed(self) -> None:
        resolved = resolve("bad\nvalue")
        assert resolved != "bad\nvalue"
        assert is_valid(resolved)

    def test_does_not_return_malformed_input(self) -> None:
        assert is_valid(resolve("x" * 500))


class TestHeaderContract:
    def test_header_name_is_conventional(self) -> None:
        assert CORRELATION_HEADER == "X-Correlation-ID"

    def test_header_name_is_case_insensitive_friendly(self) -> None:
        assert CORRELATION_HEADER.lower() == "x-correlation-id"
