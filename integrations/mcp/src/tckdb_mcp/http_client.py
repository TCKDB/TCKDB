"""Thin synchronous HTTP wrapper for the TCKDB read API.

Why this wrapper instead of ``tckdb-client``?

- ``tckdb-client``'s ``health()`` calls ``{base_url}/health``. When the
  configured ``base_url`` is ``http://host/api/v1`` (the documented
  client default) that resolves to ``http://host/api/v1/health``, which
  does not exist — health is root-mounted at ``http://host/health``.
- The MCP covers a broad scientific read/query surface. A small local
  wrapper keeps route, include-token, and artifact-safety policy explicit
  while the generated client catches up.

``docs/specs/mcp_readonly_integration.md`` still prefers
``tckdb-client`` long-term. Revisit when it supports root-relative
paths and/or a POST species search.
"""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlparse, urlunparse

import httpx

from .errors import MCPToolError, map_http_status, map_httpx_exception

API_KEY_HEADER = "X-API-Key"


class TCKDBHttpClient:
    """Synchronous HTTP client for the read-only MCP integration.

    Parameters
    ----------
    base_url:
        Full API root, including the ``/api/vX`` segment (e.g.
        ``http://127.0.0.1:8010/api/v1``). Trailing slashes are stripped.
    api_key:
        Optional ``X-API-Key`` value. ``None`` runs anonymous (the
        scientific endpoints are public-read).
    timeout_seconds:
        Per-request timeout for both connect and read.
    transport:
        Optional ``httpx`` transport. Tests pass ``httpx.MockTransport``;
        production callers leave this unset.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        timeout_seconds: float,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url must not be empty")
        self._base_url = base_url.rstrip("/")
        self._root_url = _strip_api_prefix(self._base_url)
        self._api_key = api_key
        self._client = httpx.Client(timeout=timeout_seconds, transport=transport)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TCKDBHttpClient":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # URL construction
    # ------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def root_url(self) -> str:
        return self._root_url

    def health_url(self) -> str:
        """``GET /health`` lives at the server root, not under ``/api/v1``."""
        return f"{self._root_url}/health"

    def scientific_url(self, path: str) -> str:
        """Build a URL under the configured ``/api/vN`` prefix."""
        if not path.startswith("/"):
            path = "/" + path
        return f"{self._base_url}{path}"

    # ------------------------------------------------------------------
    # Request methods
    # ------------------------------------------------------------------

    def get(
        self, url: str, *, params: Mapping[str, Any] | None = None
    ) -> Any:
        """GET ``url``. ``params`` are passed through to ``httpx``.

        ``None`` values are dropped, ``bool`` values are serialized as
        ``"true"`` / ``"false"`` (matching the backend's expectations),
        and empty lists are dropped (semantically equivalent to omitting
        the parameter). List values produce repeated query parameters
        (``?include=a&include=b``).
        """
        return self._request("GET", url, params=_clean_params(params))

    def post_json(self, url: str, body: Mapping[str, Any]) -> Any:
        return self._request("POST", url, json=dict(body))

    def _request(
        self,
        method: str,
        url: str,
        *,
        json: Mapping[str, Any] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        try:
            response = self._client.request(
                method,
                url,
                headers=self._headers(),
                json=json,
                params=params if params else None,
            )
        except httpx.HTTPError as exc:
            raise map_httpx_exception(exc) from exc

        parsed: Any
        try:
            parsed = response.json()
        except ValueError:
            parsed = None

        if response.is_success:
            return parsed if parsed is not None else {}

        detail = _extract_detail(parsed, response.status_code)
        raise map_http_status(response.status_code, detail)

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._api_key:
            headers[API_KEY_HEADER] = self._api_key
        return headers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean_params(params: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Drop ``None`` entries, lowercase bools, and skip empty lists.

    Mirrors the behavior of ``tckdb-client``'s helper so the wire format
    stays identical across clients.
    """
    if not params:
        return None
    out: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
            continue
        if isinstance(value, list) and not value:
            continue
        out[key] = value
    return out or None


def _strip_api_prefix(url: str) -> str:
    """Drop a trailing ``/api/vN`` segment from ``url``.

    Health is mounted at server root, but the documented MCP base URL
    includes ``/api/v1`` so scientific endpoints can be appended
    naturally. When ``url`` has no ``/api/vN`` suffix it is returned
    unchanged — useful for tests and for deployments that proxy at a
    different prefix.
    """
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    parts = path.split("/")
    if (
        len(parts) >= 2
        and parts[-2] == "api"
        and parts[-1].startswith("v")
        and parts[-1][1:].isdigit()
    ):
        new_path = "/".join(parts[:-2])
        return urlunparse(parsed._replace(path=new_path))
    return url.rstrip("/")


def _extract_detail(parsed: Any, status: int) -> str:
    """Pull a human-readable detail out of a server error envelope."""
    if isinstance(parsed, dict):
        d = parsed.get("detail")
        if isinstance(d, str) and d:
            return d
        if isinstance(d, list):
            return str(d)
        code = parsed.get("code")
        if isinstance(code, str) and code:
            return code
    return f"HTTP {status}"


def raise_for_status(status: int, detail: str) -> MCPToolError:
    """Re-export of ``map_http_status`` for callers that build errors directly."""
    return map_http_status(status, detail)


__all__ = [
    "API_KEY_HEADER",
    "TCKDBHttpClient",
    "raise_for_status",
]
