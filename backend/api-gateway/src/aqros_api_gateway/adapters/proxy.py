"""Proxying to upstream services.

Transport lives in ``adapters/``; the decision of *whether* a request may be
proxied lives in ``domain/topology.py``. This module is the thin transport
seam between them.

Two behaviours matter for correctness:

* **Strip hop-by-hop headers.** A proxy must not forward ``Connection``,
  ``Keep-Alive``, or ``Transfer-Encoding``; forwarding them corrupts the
  upstream connection.
* **Propagate the correlation ID.** The gateway is where a trace is born, so
  the ID must reach the upstream or the trace dies at the edge.
"""

from __future__ import annotations

from typing import Any

from aqros_core.correlation import CORRELATION_HEADER

# Headers that describe a single hop and must never be forwarded.
HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        # Not strictly hop-by-hop, but the gateway must set its own upstream
        # identity rather than forwarding the client's claims about itself.
        "host",
    }
)


def build_upstream_headers(headers: dict[str, str], correlation_id: str) -> dict[str, str]:
    """Build the header set to forward upstream.

    Args:
        headers: Incoming client headers (lowercased keys expected).
        correlation_id: Resolved correlation ID for this request.

    Returns:
        Headers safe to forward, with the correlation ID injected.
    """
    forwarded = {k: v for k, v in headers.items() if k.lower() not in HOP_BY_HOP}
    forwarded[CORRELATION_HEADER.lower()] = correlation_id
    return forwarded


class GatewayProxy:
    """Forwards a request to an upstream service and returns its response.

    Owns the ``ResilientClient`` so timeouts, retry, and circuit breaking are
    applied uniformly to every upstream (CLAUDE.md §5).
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    async def forward(
        self,
        method: str,
        base_url: str,
        path: str,
        query: str,
        headers: dict[str, str],
        correlation_id: str,
        body: bytes | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        """Forward a request and return ``(status, headers, body)``.

        Headers are returned as a dict with lowercased keys so the caller can
        merge them without worrying about the upstream's capitalisation.
        """
        url = f"{base_url.rstrip('/')}{path}"
        if query:
            url = f"{url}?{query}"

        response = await self._client.request(
            method,
            url,
            headers=build_upstream_headers(headers, correlation_id),
            content=body,
        )

        response_headers = {k.lower(): v for k, v in response.headers.items()}
        # The upstream's own correlation header must not clobber ours.
        response_headers[CORRELATION_HEADER.lower()] = correlation_id
        return response.status_code, response_headers, response.content

    async def aclose(self) -> None:
        await self._client.aclose()
