"""Signature-verification implementation of the ``ArtifactSigner`` port.

The Registry serves an independent, verified copy of each artifact. Where the
platform has configured artifact signing (e.g. a `cosign` public key / trust
policy), the artifact's signature is verified *before* the bytes are served and
an unsigned or invalidly-signed artifact is refused with
``SignatureVerificationError`` (Requirement 21.3). Where signing is *not*
configured, verification is a tolerant no-op so the MVP runs without any
signing infrastructure — exactly the "optional, where configured" contract
recorded in design.md Section 3.

``CosignArtifactVerifier`` is deliberately config-gated and MVP-appropriate:
the real `cosign` bundle verification is a well-defined extension point
(``verify_hook``) rather than a hardcoded dependency, so wiring in the actual
`sigstore`/`cosign` verification later touches only this file — no domain or
API change (mirroring the swap-later property of ``LocalArtifactStore``).

Fail-closed semantics: once signing *is* configured, the verifier refuses to
serve anything it cannot positively verify. If no concrete verification hook is
wired in yet, every artifact is refused rather than served unverified — a
configured-but-unverifiable state is treated as a verification failure, never
as an implicit pass.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from aqros_model_registry.domain.ports import ArtifactSigner, SignatureVerificationError

# Extension point for real `cosign`/`sigstore` verification. Given the model
# coordinates and the artifact bytes, an implementation returns ``True`` when the
# signature is valid and ``False`` (or raises) otherwise.
VerifyHook = Callable[[str, int, bytes], Awaitable[bool]]


class CosignArtifactVerifier(ArtifactSigner):
    """Verifies artifact signatures before serving, where signing is configured.

    Constructed with entirely optional signing configuration:

    * ``public_key`` — the trusted verification key/material (``None`` means
      signing is not configured for this deployment).
    * ``verify_hook`` — the concrete verification routine (real `cosign`
      integration). Left ``None`` in the MVP, in which case a *configured*
      verifier fails closed and refuses every artifact.

    When neither is supplied, ``verify_artifact`` is a tolerant no-op.
    """

    def __init__(
        self,
        *,
        public_key: str | None = None,
        verify_hook: VerifyHook | None = None,
    ) -> None:
        self._public_key = public_key
        self._verify_hook = verify_hook

    @property
    def is_configured(self) -> bool:
        """True when artifact signing is configured for this deployment."""
        return self._public_key is not None or self._verify_hook is not None

    async def verify_artifact(self, model_name: str, model_version: int, data: bytes) -> None:
        """Verify ``data``'s signature for ``(model_name, model_version)``.

        No-op when signing is not configured. When configured, delegates to the
        ``verify_hook`` and raises ``SignatureVerificationError`` if the hook is
        absent (nothing can be verified yet) or reports the artifact as unsigned
        or invalidly signed.
        """
        if not self.is_configured:
            return

        if self._verify_hook is None:
            # Configured to require signatures but no concrete verifier is wired
            # in yet: fail closed rather than serve an unverified artifact.
            raise SignatureVerificationError(
                f"artifact signing is configured for {model_name} v{model_version} "
                "but no signature verifier is available; refusing to serve unverified artifact"
            )

        try:
            verified = await self._verify_hook(model_name, model_version, data)
        except SignatureVerificationError:
            raise
        except Exception as exc:  # any verifier failure means refuse to serve
            raise SignatureVerificationError(
                f"signature verification failed for {model_name} v{model_version}: {exc}"
            ) from exc

        if not verified:
            raise SignatureVerificationError(
                f"artifact for {model_name} v{model_version} is unsigned or has an invalid signature"
            )
