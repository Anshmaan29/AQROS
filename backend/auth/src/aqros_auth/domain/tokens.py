"""Password hashing and HMAC-signed tokens, using only the standard library.

The repo has no crypto dependency today (the backtest report signer uses stdlib
``hmac``/``hashlib``), so this follows the same precedent rather than pulling in
a new library for the auth service.

Two deliberate choices:

* **PBKDF2-HMAC-SHA256** for passwords, with a per-password random salt and an
  explicit iteration count. Comparison uses ``hmac.compare_digest`` so a timing
  signal cannot leak the hash.
* **JWT (HS256) implemented directly.** It is a small, well-specified format;
  writing it explicitly makes the algorithm, the claims, and the expiry
  impossible to get subtly wrong by misusing a library default.

*This is not a substitute for an audited crypto library before real money.* The
docstrings mark the upgrade path (argon2id + a vetted JWT library) as a
prerequisite for production.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

#: PBKDF2 iterations. High enough to be slow to brute-force, low enough for an
#: interactive login. Raise this before production and re-hash on next login.
PBKDF2_ITERATIONS: Final = 600_000

_SALT_BYTES: Final = 16
_ALGORITHM: Final = "HS256"


class TokenError(Exception):
    """Raised when a token is malformed, mis-signed, or expired."""


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Hash a password with PBKDF2-HMAC-SHA256 and a random salt.

    Returns a self-describing string:
    ``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Verify a password against an encoded hash in constant time."""
    try:
        algorithm, iterations_raw, salt_hex, expected_hex = encoded.split("$")
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    try:
        iterations = int(iterations_raw)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)
    except ValueError:
        return False

    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    # compare_digest avoids leaking the hash through timing.
    return hmac.compare_digest(candidate, expected)


def needs_rehash(encoded: str, *, iterations: int = PBKDF2_ITERATIONS) -> bool:
    """Whether a stored hash used weaker parameters than current policy."""
    try:
        algorithm, iterations_raw, _, _ = encoded.split("$")
    except ValueError:
        return True
    return algorithm != "pbkdf2_sha256" or int(iterations_raw) < iterations


# ---------------------------------------------------------------------------
# JWT (HS256)
# ---------------------------------------------------------------------------
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data + padding)
    except (binascii.Error, ValueError) as exc:
        # A malformed segment must surface as a TokenError, not a binascii
        # traceback: callers authenticate untrusted input.
        raise TokenError("malformed token: segment is not valid base64url") from exc


@dataclass(frozen=True)
class TokenClaims:
    """Decoded, validated JWT claims."""

    subject: str
    issuer: str
    audience: str
    issued_at: datetime
    expires_at: datetime
    roles: tuple[str, ...]
    principal_id: str
    is_service: bool

    @property
    def is_expired(self) -> bool:
        return datetime.now(UTC) > self.expires_at


def encode_jwt(
    *,
    subject: str,
    principal_id: str,
    roles: list[str] | tuple[str, ...],
    secret: str,
    issuer: str,
    audience: str,
    ttl: timedelta,
    is_service: bool = False,
    now: datetime | None = None,
) -> str:
    """Issue a signed HS256 JWT.

    ``exp``/``iat`` are Unix seconds, per RFC 7519. The clock is injectable so
    token expiry is testable without sleeping.
    """
    issued = now if now is not None else datetime.now(UTC)
    header = {"alg": _ALGORITHM, "typ": "JWT"}
    payload = {
        "sub": subject,
        "principal_id": principal_id,
        "roles": list(roles),
        "iss": issuer,
        "aud": audience,
        "iat": int(issued.timestamp()),
        "exp": int((issued + ttl).timestamp()),
        "is_service": is_service,
    }
    signing_input = ".".join(
        (
            _b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode()),
            _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()),
        )
    )
    signature = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(signature)}"


def decode_jwt(
    token: str,
    *,
    secret: str,
    issuer: str,
    audience: str,
    now: datetime | None = None,
) -> TokenClaims:
    """Verify and decode a JWT.

    Rejects: wrong shape, wrong algorithm, bad signature, wrong issuer/audience,
    and expiry. Signature verification happens before trusting any claim.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("malformed token: expected three dot-separated segments")
    signing_input = f"{parts[0]}.{parts[1]}"
    provided_signature = _b64url_decode(parts[2])

    expected_signature = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(provided_signature, expected_signature):
        raise TokenError("invalid token signature")

    try:
        header = json.loads(_b64url_decode(parts[0]))
        payload = json.loads(_b64url_decode(parts[1]))
    except (ValueError, json.JSONDecodeError) as exc:
        raise TokenError("malformed token: segments are not valid JSON") from exc

    # Reject "alg: none" and algorithm confusion outright.
    if header.get("alg") != _ALGORITHM:
        raise TokenError(f"unsupported token algorithm: {header.get('alg')!r}")

    if payload.get("iss") != issuer:
        raise TokenError(f"token issuer mismatch: {payload.get('iss')!r}")
    if payload.get("aud") != audience:
        raise TokenError(f"token audience mismatch: {payload.get('aud')!r}")

    exp = payload.get("exp")
    iat = payload.get("iat")
    if not isinstance(exp, int) or not isinstance(iat, int):
        raise TokenError("token is missing integer 'exp'/'iat' claims")

    current = now if now is not None else datetime.now(UTC)
    if current.timestamp() > exp:
        raise TokenError("token has expired")

    roles = payload.get("roles", [])
    if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
        raise TokenError("token 'roles' claim must be a list of strings")

    return TokenClaims(
        subject=str(payload.get("sub", "")),
        issuer=str(payload["iss"]),
        audience=str(payload["aud"]),
        issued_at=datetime.fromtimestamp(iat, tz=UTC),
        expires_at=datetime.fromtimestamp(exp, tz=UTC),
        roles=tuple(roles),
        principal_id=str(payload.get("principal_id", "")),
        is_service=bool(payload.get("is_service", False)),
    )


def generate_api_key() -> tuple[str, str]:
    """Generate a service API key, returning ``(key_id, secret)``.

    The key id is safe to log; the secret is shown once and only its hash is
    ever stored.
    """
    key_id = f"sk_{secrets.token_hex(8)}"
    return key_id, f"sk_{secrets.token_urlsafe(32)}"


def hash_api_key(raw_key: str) -> str:
    """Hash an API key for storage.

    API keys are high-entropy random strings (not user-chosen passwords), so a
    single SHA-256 is appropriate here — PBKDF2 would only add latency, since
    there is no dictionary to attack.
    """
    return hashlib.sha256(raw_key.encode()).hexdigest()


def verify_api_key(raw_key: str, stored_hash: str) -> bool:
    """Verify an API key against its stored hash in constant time."""
    return hmac.compare_digest(hash_api_key(raw_key), stored_hash)


def safe_token_payload(token: str) -> dict[str, Any]:
    """Decode a token's payload *without* verifying it, for logging only.

    Never use the result of this for an authorization decision.
    """
    try:
        return dict(json.loads(_b64url_decode(token.split(".")[1])))
    except (IndexError, ValueError, json.JSONDecodeError):
        return {}
