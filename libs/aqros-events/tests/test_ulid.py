from __future__ import annotations

import re

from aqros_events.ulid import ulid


class TestULID:
    def test_length(self) -> None:
        uid = ulid()
        assert len(uid) == 26

    def test_encoding_chars(self) -> None:
        uid = ulid()
        assert re.fullmatch(r"[0-9A-Z]{26}", uid)

    def test_no_ambiguous_chars(self) -> None:
        uid = ulid()
        assert "I" not in uid
        assert "L" not in uid
        assert "O" not in uid
        assert "U" not in uid

    def test_is_unique(self) -> None:
        ids = {ulid() for _ in range(1000)}
        assert len(ids) == 1000

    def test_time_prefix_is_non_decreasing(self) -> None:
        ids = [ulid() for _ in range(100)]
        for i in range(len(ids) - 1):
            assert ids[i + 1][:10] >= ids[i][:10]

    def test_time_prefix_moves_forward(self) -> None:
        earlier = ulid()
        later = ulid()
        assert later[:10] >= earlier[:10]
