"""Synchronous TCKDB API client.

This module is intentionally small. It owns:

- base-URL normalization and path joining
- API-key + ``Idempotency-Key`` header injection
- request/response wrapping (so callers can see the
  ``Idempotency-Replayed`` header without re-parsing)
- HTTP status to structured exception mapping

It does not own payload construction, schema validation, or any
chemistry semantics — those belong in producer-specific adapters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import httpx

from tckdb_client.errors import (
    TCKDBAuthenticationError,
    TCKDBConflictError,
    TCKDBConnectionError,
    TCKDBForbiddenError,
    TCKDBHTTPError,
    TCKDBIdempotencyConflictError,
    TCKDBValidationError,
)
from tckdb_client.idempotency import validate_idempotency_key

API_KEY_HEADER = "X-API-Key"
IDEMPOTENCY_HEADER = "Idempotency-Key"
IDEMPOTENCY_REPLAYED_HEADER = "Idempotency-Replayed"

UPLOAD_ENDPOINTS: dict[str, str] = {
    "conformer": "/uploads/conformers",
    "reaction": "/uploads/reactions",
    "kinetics": "/uploads/kinetics",
    "thermo": "/uploads/thermo",
    "statmech": "/uploads/statmech",
    "transport": "/uploads/transport",
    "transition_state": "/uploads/transition-states",
    "network": "/uploads/networks",
    "network_pdep": "/uploads/networks/pdep",
    "computed_reaction": "/uploads/computed-reaction",
}


@dataclass(frozen=True)
class TCKDBResponse:
    """Lightweight wrapper exposing status, headers, JSON, and replay flag.

    Returned by :meth:`TCKDBClient.request_json`. Convenience methods
    (``post_json``, ``upload``, ``bundle_*``) unwrap to ``data`` so the
    common case stays a one-liner; reach for the wrapper when you need
    to inspect the replay flag or other headers.
    """

    data: Any
    status_code: int
    headers: Mapping[str, str]

    @property
    def idempotency_replayed(self) -> bool:
        """``True`` when the server replayed a previously stored response."""
        target = IDEMPOTENCY_REPLAYED_HEADER.lower()
        for name, value in self.headers.items():
            if name.lower() == target:
                return isinstance(value, str) and value.lower() == "true"
        return False


class TCKDBClient:
    """Synchronous client for the TCKDB HTTP API.

    Parameters
    ----------
    base_url:
        API root, e.g. ``http://localhost:8000/api/v1``. Trailing
        slashes are stripped; path joining never duplicates ``/``.
    api_key:
        Optional API key. Required for authenticated endpoints; pass
        ``None`` for health checks against an open instance.
    timeout:
        Per-request timeout in seconds. Network/timeout failures are
        surfaced as :class:`TCKDBConnectionError`.
    transport:
        Optional ``httpx`` transport, primarily for tests
        (``httpx.MockTransport``). Production callers should leave this
        unset.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 30.0,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not isinstance(base_url, str) or not base_url:
            raise ValueError("base_url must be a non-empty string.")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TCKDBClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # URL / header construction
    # ------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        return self._base_url

    def _full_url(self, path: str) -> str:
        if not isinstance(path, str) or not path:
            raise ValueError("path must be a non-empty string.")
        if path.startswith(("http://", "https://")):
            return path
        suffix = path if path.startswith("/") else "/" + path
        return self._base_url + suffix

    def _build_headers(
        self,
        *,
        authenticated: bool,
        json_body: bool,
        idempotency_key: str | None,
        extra: Mapping[str, str] | None = None,
    ) -> dict[str, str]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if json_body:
            headers["Content-Type"] = "application/json"
        if authenticated:
            if not self._api_key:
                raise TCKDBAuthenticationError(
                    "API key required for this request but none was configured.",
                    status_code=None,
                )
            headers[API_KEY_HEADER] = self._api_key
        if idempotency_key is not None:
            headers[IDEMPOTENCY_HEADER] = validate_idempotency_key(idempotency_key)
        if extra:
            headers.update(extra)
        return headers

    # ------------------------------------------------------------------
    # Core request
    # ------------------------------------------------------------------

    def request_json(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        authenticated: bool = True,
        idempotency_key: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> TCKDBResponse:
        """Perform an HTTP request and return a :class:`TCKDBResponse`.

        Network failures and timeouts raise :class:`TCKDBConnectionError`;
        non-success responses raise the appropriate
        :class:`TCKDBHTTPError` subclass.
        """
        url = self._full_url(path)
        headers = self._build_headers(
            authenticated=authenticated,
            json_body=json is not None,
            idempotency_key=idempotency_key,
            extra=extra_headers,
        )
        try:
            response = self._client.request(
                method, url, json=json, headers=headers
            )
        except httpx.TimeoutException as exc:
            raise TCKDBConnectionError(f"Request timed out: {exc}") from exc
        except httpx.TransportError as exc:
            raise TCKDBConnectionError(f"Network error: {exc}") from exc

        return self._handle_response(response)

    def _handle_response(self, response: httpx.Response) -> TCKDBResponse:
        parsed: Any = None
        text: str | None = None
        try:
            parsed = response.json()
        except ValueError:
            text = response.text or None

        if response.is_success:
            return TCKDBResponse(
                data=parsed if parsed is not None else text,
                status_code=response.status_code,
                headers=dict(response.headers),
            )

        raise self._build_http_error(
            status_code=response.status_code,
            parsed=parsed,
            text=text,
            headers=response.headers,
        )

    @staticmethod
    def _build_http_error(
        *,
        status_code: int,
        parsed: Any,
        text: str | None,
        headers: Mapping[str, str],
    ) -> TCKDBHTTPError:
        code: str | None = None
        detail: object | None = None
        if isinstance(parsed, dict):
            raw_code = parsed.get("code")
            code = raw_code if isinstance(raw_code, str) else None
            detail = parsed.get("detail", parsed)
        elif parsed is not None:
            detail = parsed

        message = (
            detail if isinstance(detail, str) and detail
            else f"HTTP {status_code}"
        )

        kwargs = dict(
            status_code=status_code,
            code=code,
            detail=detail,
            response_json=parsed,
            response_text=text,
            headers=headers,
        )

        if status_code == 401:
            return TCKDBAuthenticationError(message, **kwargs)
        if status_code == 403:
            return TCKDBForbiddenError(message, **kwargs)
        if status_code == 422:
            return TCKDBValidationError(message, **kwargs)
        if status_code == 409:
            if code == "idempotency_conflict":
                return TCKDBIdempotencyConflictError(message, **kwargs)
            return TCKDBConflictError(message, **kwargs)
        return TCKDBHTTPError(message, **kwargs)

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def health(self) -> dict:
        """Unauthenticated health probe."""
        return self.request_json(
            "GET", "/health", authenticated=False
        ).data

    def me(self) -> dict:
        """Return the authenticated user profile (``GET /auth/me``)."""
        return self.request_json("GET", "/auth/me").data

    def get_json(self, path: str) -> Any:
        return self.request_json("GET", path).data

    def post_json(
        self,
        path: str,
        payload: Any,
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        return self.request_json(
            "POST", path, json=payload, idempotency_key=idempotency_key
        ).data

    def upload(
        self,
        endpoint: str,
        payload: Any,
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        """POST a payload to an upload endpoint.

        ``endpoint`` accepts:

        - a known short name from :data:`UPLOAD_ENDPOINTS`
          (e.g. ``"thermo"``, ``"kinetics"``, ``"conformer"``),
        - an explicit path beginning with ``/``
          (e.g. ``"/uploads/thermo"`` or a future endpoint),
        - or an absolute URL for advanced use.

        Unknown short names are rejected client-side rather than being
        silently rewritten to ``/uploads/<name>`` — that would mask
        typos and could collide with future endpoints.
        """
        if endpoint in UPLOAD_ENDPOINTS:
            path = UPLOAD_ENDPOINTS[endpoint]
        elif endpoint.startswith(("/", "http://", "https://")):
            path = endpoint
        else:
            raise ValueError(
                f"Unknown upload endpoint: {endpoint!r}. "
                f"Pass an explicit path starting with '/' or one of "
                f"{sorted(UPLOAD_ENDPOINTS)}."
            )
        return self.post_json(path, payload, idempotency_key=idempotency_key)

    def bundle_dry_run(self, bundle: Any) -> Any:
        """POST a contribution bundle to ``/bundles/dry-run`` (no idempotency)."""
        return self.post_json("/bundles/dry-run", bundle)

    def bundle_submit(
        self,
        bundle: Any,
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        """POST a contribution bundle to ``/bundles/submit``."""
        return self.post_json(
            "/bundles/submit", bundle, idempotency_key=idempotency_key
        )


__all__ = [
    "TCKDBClient",
    "TCKDBResponse",
    "UPLOAD_ENDPOINTS",
    "API_KEY_HEADER",
    "IDEMPOTENCY_HEADER",
    "IDEMPOTENCY_REPLAYED_HEADER",
]
