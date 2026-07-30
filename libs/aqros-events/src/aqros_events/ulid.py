"""ULID generator — time-sortable unique identifiers.

ULIDs are 26-character Crockford Base32-encoded strings with a 48-bit
millisecond-timestamp prefix and an 80-bit random suffix. They are
lexicographically sortable (timestamp-first), URL-safe, and carry no
MAC-address or other identifying information.

Design Decision
---------------
We implement ULID in-house rather than depending on ``ulid-py`` because
the algorithm is small (≈40 LoC), stable, and has zero moving parts —
exactly the kind of dependency that should be owned rather than vendored.
If the community library later adds value (e.g. monotonic-random split,
database integration), switching is a one-line import change behind the
``ulid()`` function signature ``() -> str``.
"""

from __future__ import annotations

import os
import time
from typing import Final

_ENCODING: Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ENCODING_LEN: Final[int] = 32

# Pre-computed lookups for encoding 10-bit chunks to 2 Base32 chars.
# ULID encodes 48 bits of timestamp and 80 bits of randomness into 26 chars.
# Encoding approach: shift and mask 5 bits at a time.


def _encode(value: int, length: int) -> str:
    """Encode ``value`` as a ``length``-character Crockford Base32 string."""
    chars: list[str] = []
    for _ in range(length):
        chars.append(_ENCODING[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def ulid() -> str:
    """Return a new ULID string.

    The first 10 characters encode the current time as milliseconds since
    Unix epoch (48 bits). The remaining 16 characters encode 80 bits of
    cryptographic randomness.

    Returns:
        A 26-character Crockford Base32-encoded ULID string, time-sortable
        at millisecond precision.

    Example:
        ``"01ARZ3NDEKTSV4RRFFQ69G5FAV"``
    """
    timestamp_ms: int = int(time.time() * 1000)
    random_bytes: bytes = os.urandom(10)
    randomness: int = int.from_bytes(random_bytes, byteorder="big")
    return _encode(timestamp_ms, 10) + _encode(randomness, 16)
