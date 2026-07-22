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

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping

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
from tckdb_client.pagination import iter_paginated_records
from tckdb_client.scientific_types import (
    ArtifactRecord,
    ArtifactSearchResponse,
    KineticsRecord,
    KineticsSearchResponse,
    NetworkKineticsRecord,
    NetworkKineticsSearchResponse,
    NetworkRecord,
    NetworkSearchResponse,
    ReactionKineticsResponse,
    ReactionRecord,
    ReactionSearchResponse,
    SpeciesCalculationRecord,
    SpeciesCalculationsSearchResponse,
    SpeciesRecord,
    SpeciesSearchResponse,
    SpeciesThermoResponse,
    StatmechRecord,
    StatmechSearchResponse,
    ThermoRecord,
    ThermoSearchResponse,
    TransportRecord,
    TransportSearchResponse,
)

API_KEY_HEADER = "X-API-Key"
IDEMPOTENCY_HEADER = "Idempotency-Key"
IDEMPOTENCY_REPLAYED_HEADER = "Idempotency-Replayed"

# Sentinel used by :meth:`TCKDBClient.upload` to distinguish the
# builder-form ``upload(builder)`` call from the legacy
# ``upload(endpoint, payload)`` call without confusing it with a
# caller-provided ``None`` payload.
_UNSET: Any = object()

# Client-identity headers sent on every request so the server can
# enforce a minimum supported ``tckdb-client`` version on writes.
# See backend/app/api/client_version.py for the matching server check.
CLIENT_NAME_HEADER = "X-TCKDB-Client-Name"
CLIENT_VERSION_HEADER = "X-TCKDB-Client-Version"
CLIENT_NAME = "tckdb-client"

_ScientificSearchMethod = Literal["GET", "POST"]


def _legacy_detail_code(detail: object) -> str | None:
    """Recover the stable prefix used by pre-structured-error servers."""

    if not isinstance(detail, str):
        return None
    prefix, separator, _tail = detail.partition(": ")
    if not separator or not prefix:
        return None
    if not all(ch.islower() or ch.isdigit() or ch == "_" for ch in prefix):
        return None
    return prefix


def _resolve_client_version() -> str:
    """Return the installed ``tckdb-client`` package version.

    Lazily imports the package-level ``__version__`` to avoid an
    ``__init__`` ↔ ``client`` import cycle at module load time.
    """
    from tckdb_client import __version__

    return __version__

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
    "computed_species": "/uploads/computed-species",
}


@dataclass(frozen=True)
class ArtifactUploadBatchResult:
    """One server response from a batched artifact upload.

    Returned (one per ``calculation_id`` group) by
    :meth:`TCKDBClient.upload_artifacts` when
    ``batch_by_calculation=True``. Carries the server's
    ``ArtifactsUploadResult`` body verbatim alongside the
    bundle-local ``calculation_keys`` the builder layer minted, so
    producers can map a batch result back to their plan without
    re-walking the original list.

    Frozen so producers can safely store, sort, or aggregate batch
    results across multiple uploads. ``calculation_keys`` is a tuple
    in the same order as the items dispatched to the batch; the
    first entry is the one used in the idempotency key (see
    :meth:`TCKDBClient.upload_artifacts`).
    """

    calculation_id: int
    calculation_keys: tuple[str, ...]
    artifact_count: int
    response: Any


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
        API root, e.g. ``http://localhost:8010/api/v1``. Trailing
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
        """Build outgoing request headers.

        Auth policy:

        - ``authenticated=True`` (default for writes/admin): an API
          key is required client-side. Missing key → ``TCKDBAuthenticationError``
          before the request goes out.
        - ``authenticated=False`` (scientific reads, health probe):
          the API key is attached **if available** so authenticated
          deployments can still bill the request against a user
          quota, but the request is not gated client-side. A missing
          key produces no client error — the backend decides whether
          the path is anonymously accessible and surfaces 401/403 if
          not.

        Public reads being anonymous-friendly in the client is not an
        abuse-control mechanism. Hosted deployments should enforce
        abuse limits server-side (rate limits, pagination caps, query
        timeouts, monitoring).
        """
        headers: dict[str, str] = {
            "Accept": "application/json",
            CLIENT_NAME_HEADER: CLIENT_NAME,
            CLIENT_VERSION_HEADER: _resolve_client_version(),
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        if authenticated and not self._api_key:
            raise TCKDBAuthenticationError(
                "API key required for this request but none was configured.",
                status_code=None,
            )
        if self._api_key:
            headers[API_KEY_HEADER] = self._api_key
        if idempotency_key is not None:
            headers[IDEMPOTENCY_HEADER] = validate_idempotency_key(idempotency_key)
        if extra:
            # Caller-provided headers take precedence so advanced users
            # can override e.g. ``X-API-Key`` for a single request, but
            # the client-identity headers stay attached so server-side
            # compat checks still see them.
            headers.update(extra)
            headers[CLIENT_NAME_HEADER] = CLIENT_NAME
            headers[CLIENT_VERSION_HEADER] = _resolve_client_version()
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
        params: Mapping[str, Any] | None = None,
        authenticated: bool = True,
        idempotency_key: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> TCKDBResponse:
        """Perform an HTTP request and return a :class:`TCKDBResponse`.

        ``params`` is forwarded to ``httpx``: list values produce repeated
        query parameters (``?include=a&include=b``), ``None`` values are
        dropped, ``bool`` values are serialized as ``"true"``/``"false"``.

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
                method,
                url,
                json=json,
                params=_clean_params(params) if params else None,
                headers=headers,
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
            if code is None:
                code = _legacy_detail_code(detail)
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
        target: Any,
        payload: Any = _UNSET,
        *,
        idempotency_key: str | None = None,
        warn_on_dropped_fields: bool = False,
    ) -> Any:
        """POST an upload payload.

        Two argument forms are supported:

        - ``client.upload(endpoint, payload_dict)`` — the long-standing
          raw-dict form. ``endpoint`` accepts a short name from
          :data:`UPLOAD_ENDPOINTS`, an explicit path starting with
          ``/``, or an absolute URL.
        - ``client.upload(builder_object)`` — Phase-1 builder form.
          ``builder_object`` must expose ``upload_kind`` (matching a
          key in :data:`UPLOAD_ENDPOINTS`) and a ``to_payload()``
          method. The builder is asked once for its payload, which is
          posted to the resolved endpoint.

        The two forms are kept structurally distinct on purpose: a raw
        dict is **not** accepted by the single-arg form. Passing a
        dict to the single-arg form raises ``TypeError`` rather than
        guessing an endpoint from the payload shape — see
        ``clients/python/docs/builder_api_mvp.md`` §7.

        ``warn_on_dropped_fields`` applies only to the builder form.
        When True, the client calls ``upload_object.emission_diagnostics()``
        (if defined) and re-emits any ``level="warning"`` entry through
        :func:`warnings.warn` before dispatch. Use this on producer
        code paths that aggregate user input — a builder object that
        carries data the backend won't persist is usually a portability
        risk worth surfacing.
        """
        if payload is _UNSET:
            return self._upload_builder_object(
                target,
                idempotency_key=idempotency_key,
                warn_on_dropped_fields=warn_on_dropped_fields,
            )

        if not isinstance(target, str):
            raise TypeError(
                "client.upload(endpoint, payload) requires endpoint to be "
                f"a string, got {type(target).__name__}. For builder "
                "objects, call client.upload(builder_object) with a "
                "single argument."
            )

        endpoint = target
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

    def _upload_builder_object(
        self,
        obj: Any,
        *,
        idempotency_key: str | None,
        warn_on_dropped_fields: bool = False,
    ) -> Any:
        """Dispatch a builder upload object to its registered endpoint."""
        if isinstance(obj, dict):
            raise TypeError(
                "client.upload(...) does not accept raw dicts in the "
                "single-argument form. Use client.upload(endpoint, "
                "payload_dict) for raw payloads."
            )
        if not hasattr(obj, "upload_kind") or not hasattr(obj, "to_payload"):
            raise TypeError(
                "Builder upload object must define an 'upload_kind' "
                "string and a 'to_payload()' method. Got "
                f"{type(obj).__name__}."
            )
        kind = obj.upload_kind
        if not isinstance(kind, str) or kind not in UPLOAD_ENDPOINTS:
            raise TypeError(
                f"Unknown upload_kind {kind!r}; expected one of "
                f"{sorted(UPLOAD_ENDPOINTS)}."
            )
        if warn_on_dropped_fields and hasattr(obj, "emission_diagnostics"):
            # Surface each warning-level diagnostic via the standard
            # ``warnings`` machinery so producer pipelines can filter,
            # capture, or escalate them with the usual tools.
            import warnings as _warnings

            for diag in obj.emission_diagnostics():
                if diag.level == "warning":
                    _warnings.warn(
                        f"[{diag.code}] {diag.path}: {diag.message}",
                        UserWarning,
                        stacklevel=3,
                    )
        payload = obj.to_payload()
        if not isinstance(payload, dict):
            raise TypeError(
                f"{type(obj).__name__}.to_payload() must return a dict, "
                f"got {type(payload).__name__}."
            )
        return self.post_json(
            UPLOAD_ENDPOINTS[kind], payload, idempotency_key=idempotency_key
        )

    # ------------------------------------------------------------------
    # Artifact upload (second-phase)
    # ------------------------------------------------------------------

    def upload_artifact(
        self,
        calculation_id: int,
        path: "str | Path",
        kind: str,
        *,
        sha256: str | None = None,
        bytes: int | None = None,
        filename: str | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        """POST a single artifact to a calculation.

        Reads the local file at ``path``, base64-encodes its contents,
        and posts the resulting ``ArtifactIn`` payload to
        ``/api/v1/calculations/{calculation_id}/artifacts``. The
        endpoint accepts an inline batch wrapper — this helper sends a
        single-item batch.

        ``filename`` defaults to the path's basename; supply an
        explicit value when uploading from a temp file with a synthetic
        name. The server's filename validation (extension allowlist,
        no path separators, NFC-normalized) still applies.
        """
        import base64
        import pathlib

        src = pathlib.Path(path)
        if not src.exists():
            raise ValueError(f"artifact file does not exist: {src}")
        if not src.is_file():
            raise ValueError(f"artifact path is not a file: {src}")
        content = src.read_bytes()
        artifact_in: dict[str, Any] = {
            "kind": kind,
            "filename": filename or src.name,
            "content_base64": base64.b64encode(content).decode("ascii"),
        }
        if sha256 is not None:
            artifact_in["sha256"] = sha256
        if bytes is not None:
            artifact_in["bytes"] = bytes

        return self.post_json(
            f"/calculations/{calculation_id}/artifacts",
            {"artifacts": [artifact_in]},
            idempotency_key=idempotency_key,
        )

    def upload_artifacts(
        self,
        plan: "Iterable[Any]",
        *,
        idempotency_key_prefix: str | None = None,
        batch_by_calculation: bool = False,
    ) -> "list[Any] | list[ArtifactUploadBatchResult]":
        """Execute a builder-produced artifact plan.

        Each entry in ``plan`` must expose ``calculation_id``,
        ``path``, ``kind``, and the optional ``sha256``, ``bytes``,
        ``label``, ``calculation_key`` fields — i.e. the shape of
        :class:`tckdb_client.builders.PlannedArtifactUpload`.

        Two dispatch modes:

        - **``batch_by_calculation=False`` (default).** Each item is
          uploaded in its own POST, in caller order. The first
          failure propagates immediately; partial progress is the
          caller's responsibility to handle. Returns a list of
          per-artifact server responses, one per plan item.
        - **``batch_by_calculation=True``.** Items are grouped by
          ``calculation_id`` (insertion-order-preserving) and each
          group is sent in a single POST to
          ``/calculations/{calculation_id}/artifacts``. The
          per-calculation request is **atomic** server-side: any
          per-artifact validation failure in the batch rejects the
          whole batch with 422 and no DB rows or S3 writes survive,
          and a pass-2 storage failure compensates earlier S3 writes
          before returning 503. Returns a list of
          :class:`ArtifactUploadBatchResult` records, one per
          ``calculation_id`` group, preserving caller-supplied group
          order and intra-group item order.

        **Pre-dispatch validation** (both modes): every plan item
        must be a ``PlannedArtifactUpload``-like object with a
        non-empty string ``kind``, an int ``calculation_id``, and a
        local ``path`` that exists and is a regular file. Any failure
        raises ``TypeError`` / ``ValueError`` *before* the first HTTP
        request is issued so a malformed plan cannot leave the
        server in a half-uploaded state.

        **Idempotency keys**:

        - ``batch_by_calculation=False``:
          ``f"{prefix}:{calculation_key}:{kind}"`` per artifact, same
          as before.
        - ``batch_by_calculation=True``:
          ``f"{prefix}:{first_calculation_key}:artifact-batch"`` per
          group — one key per batch POST. The ``first_calculation_key``
          is the ``calculation_key`` of the first item in the group's
          caller-order slice; deterministic for the same plan.

        Server-side atomicity is the load-bearing premise for batch
        mode. See the backend route
        ``POST /api/v1/calculations/{calculation_id}/artifacts`` and
        its tests in
        ``backend/tests/api/test_api_calculation_artifacts.py``
        (``TestBatchAtomicity`` + ``TestStorageFailure``).
        """
        import base64
        import pathlib

        from tckdb_client.idempotency import validate_idempotency_key

        items = list(plan)

        # --- Pre-dispatch validation (both modes) -------------------
        # Validate every item up front so a malformed plan never
        # leaves the server in a half-uploaded state.
        for i, item in enumerate(items):
            calc_id = getattr(item, "calculation_id", None)
            if not isinstance(calc_id, int) or isinstance(calc_id, bool):
                raise TypeError(
                    f"upload_artifacts: plan[{i}].calculation_id must "
                    f"be an int, got {type(calc_id).__name__}."
                )
            kind = getattr(item, "kind", None)
            if not isinstance(kind, str) or not kind.strip():
                raise ValueError(
                    f"upload_artifacts: plan[{i}].kind must be a "
                    f"non-empty string, got {kind!r}."
                )
            raw_path = getattr(item, "path", None)
            if raw_path is None:
                raise ValueError(
                    f"upload_artifacts: plan[{i}].path is required."
                )
            src = pathlib.Path(raw_path)
            if not src.exists():
                raise ValueError(
                    f"upload_artifacts: plan[{i}] artifact file does "
                    f"not exist: {src}"
                )
            if not src.is_file():
                raise ValueError(
                    f"upload_artifacts: plan[{i}] artifact path is "
                    f"not a regular file: {src}"
                )

        if not batch_by_calculation:
            return self._upload_artifacts_sequential(
                items, idempotency_key_prefix=idempotency_key_prefix
            )

        # --- Batch mode --------------------------------------------
        # Group by calculation_id; insertion-order-preserving (Py 3.7+
        # dict iteration order) so caller-supplied group order is
        # stable across runs.
        groups: dict[int, list[Any]] = {}
        for item in items:
            groups.setdefault(item.calculation_id, []).append(item)

        results: list[ArtifactUploadBatchResult] = []
        for calc_id, group_items in groups.items():
            artifact_payloads: list[dict[str, Any]] = []
            calc_keys: list[str] = []
            for item in group_items:
                src = pathlib.Path(item.path)
                content = src.read_bytes()
                artifact_in: dict[str, Any] = {
                    "kind": item.kind,
                    "filename": (
                        getattr(item, "filename", None) or src.name
                    ),
                    "content_base64": (
                        base64.b64encode(content).decode("ascii")
                    ),
                }
                if getattr(item, "sha256", None) is not None:
                    artifact_in["sha256"] = item.sha256
                if getattr(item, "bytes", None) is not None:
                    artifact_in["bytes"] = item.bytes
                artifact_payloads.append(artifact_in)
                calc_keys.append(
                    getattr(item, "calculation_key", str(calc_id))
                )

            idem: str | None = None
            if idempotency_key_prefix is not None:
                first_key = calc_keys[0] if calc_keys else str(calc_id)
                idem = validate_idempotency_key(
                    f"{idempotency_key_prefix}:{first_key}:artifact-batch"
                )

            response = self.post_json(
                f"/calculations/{calc_id}/artifacts",
                {"artifacts": artifact_payloads},
                idempotency_key=idem,
            )
            results.append(
                ArtifactUploadBatchResult(
                    calculation_id=calc_id,
                    calculation_keys=tuple(calc_keys),
                    artifact_count=len(artifact_payloads),
                    response=response,
                )
            )
        return results

    def _upload_artifacts_sequential(
        self,
        items: "list[Any]",
        *,
        idempotency_key_prefix: str | None,
    ) -> list[Any]:
        """One POST per artifact (legacy default path).

        Pre-dispatch validation in ``upload_artifacts`` has already
        confirmed every item is well-formed and points at a real
        file; this helper just dispatches.
        """
        from tckdb_client.idempotency import validate_idempotency_key

        results: list[Any] = []
        for item in items:
            calc_id = item.calculation_id
            idem: str | None = None
            if idempotency_key_prefix is not None:
                idem = validate_idempotency_key(
                    f"{idempotency_key_prefix}:"
                    f"{getattr(item, 'calculation_key', calc_id)}:"
                    f"{item.kind}"
                )
            results.append(
                self.upload_artifact(
                    calc_id,
                    item.path,
                    item.kind,
                    sha256=getattr(item, "sha256", None),
                    bytes=getattr(item, "bytes", None),
                    idempotency_key=idem,
                )
            )
        return results

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

    # ------------------------------------------------------------------
    # Scientific read/query methods (/api/v1/scientific/*)
    # ------------------------------------------------------------------
    #
    # Thin wrappers over the backend scientific read API. They serialize
    # parameters and return parsed JSON; they do NOT rank, select, or
    # interpret responses, and they hold no ARC/RMG-specific logic. See
    # docs/specs/read_api_mvp.md for the response contract.

    def search_species(
        self,
        *,
        smiles: str | None = None,
        inchi: str | None = None,
        inchi_key: str | None = None,
        formula: str | None = None,
        charge: int | None = None,
        multiplicity: int | None = None,
        electronic_state_kind: str | None = None,
        species_entry_kind: str | None = None,
        species_ref: str | None = None,
        species_entry_ref: str | None = None,
        min_review_status: str | None = None,
        include_deprecated: bool | None = None,
        include_rejected: bool | None = None,
        include: list[str] | None = None,
        collapse: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
    ) -> SpeciesSearchResponse:
        """``GET /scientific/species/search`` — discover species by identity.

        At least one identifier (``smiles``, ``inchi``, ``inchi_key``,
        ``formula``, ``species_ref``, ``species_entry_ref``) must be
        supplied. Returns the parsed ``ScientificSpeciesSearchResponse``
        JSON envelope.
        """
        params = {
            "smiles": smiles,
            "inchi": inchi,
            "inchi_key": inchi_key,
            "formula": formula,
            "charge": charge,
            "multiplicity": multiplicity,
            "electronic_state_kind": electronic_state_kind,
            "species_entry_kind": species_entry_kind,
            "species_ref": species_ref,
            "species_entry_ref": species_entry_ref,
            "min_review_status": min_review_status,
            "include_deprecated": include_deprecated,
            "include_rejected": include_rejected,
            "include": include,
            "collapse": collapse,
            "offset": offset,
            "limit": limit,
        }
        return self.request_json(
            "GET",
            "/scientific/species/search",
            params=params,
            authenticated=False,
        ).data

    def search_reactions(
        self,
        *,
        reactants: list[str] | None = None,
        products: list[str] | None = None,
        direction: str | None = None,
        family: str | None = None,
        reaction_ref: str | None = None,
        reaction_entry_ref: str | None = None,
        min_review_status: str | None = None,
        include_deprecated: bool | None = None,
        include_rejected: bool | None = None,
        include: list[str] | None = None,
        collapse: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
        method: str = "POST",
    ) -> ReactionSearchResponse:
        """``GET|POST /scientific/reactions/search`` — discover reaction entries.

        Defaults to ``POST`` because reactant/product SMILES often contain
        characters (``[`` ``]`` ``+``) that round-trip awkwardly through
        query strings. Pass ``method="GET"`` to force the GET form. Returns
        the parsed ``ScientificReactionSearchResponse`` JSON envelope.

        Phase C: ``reaction_ref`` / ``reaction_entry_ref`` may be supplied
        as standalone identity filters (no SMILES required).
        """
        common = {
            "reactants": reactants,
            "products": products,
            "direction": direction,
            "family": family,
            "reaction_ref": reaction_ref,
            "reaction_entry_ref": reaction_entry_ref,
            "min_review_status": min_review_status,
            "include_deprecated": include_deprecated,
            "include_rejected": include_rejected,
            "include": include,
            "collapse": collapse,
            "offset": offset,
            "limit": limit,
        }
        if method.upper() == "GET":
            return self.request_json(
                "GET",
                "/scientific/reactions/search",
                params=common,
                authenticated=False,
            ).data
        body = {k: v for k, v in common.items() if v is not None}
        return self.request_json(
            "POST",
            "/scientific/reactions/search",
            json=body,
            authenticated=False,
        ).data

    def get_reaction_kinetics(
        self,
        reaction_entry_id: int | str,
        *,
        temperature_min: float | None = None,
        temperature_max: float | None = None,
        pressure_bar: float | None = None,
        pressure: float | None = None,
        model_kind: str | None = None,
        level_of_theory_id: int | None = None,
        level_of_theory_ref: str | None = None,
        software: str | None = None,
        min_review_status: str | None = None,
        include_deprecated: bool | None = None,
        include_rejected: bool | None = None,
        include: list[str] | None = None,
        collapse: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
    ) -> ReactionKineticsResponse:
        """``GET /scientific/reaction-entries/{reaction_entry_id}/kinetics``.

        Phase C: ``reaction_entry_id`` accepts the integer
        ``reaction_entry.id`` or a public ref of the form ``rxe_...``.
        Supplying a ``chem_reaction.id`` or a wrong-prefix ref returns
        422 / 404. Returns the parsed ``ScientificReactionKineticsResponse``
        JSON envelope. Provenance keys are always present in each record;
        TS-chain fields are ``null`` for non-TS-backed kinetics.
        ``pressure_bar`` is canonical; ``pressure`` is a deprecated alias.
        """
        path = f"/scientific/reaction-entries/{reaction_entry_id}/kinetics"
        params = {
            "temperature_min": temperature_min,
            "temperature_max": temperature_max,
            "pressure_bar": pressure_bar,
            "pressure": pressure,
            "model_kind": model_kind,
            "level_of_theory_id": level_of_theory_id,
            "level_of_theory_ref": level_of_theory_ref,
            "software": software,
            "min_review_status": min_review_status,
            "include_deprecated": include_deprecated,
            "include_rejected": include_rejected,
            "include": include,
            "collapse": collapse,
            "offset": offset,
            "limit": limit,
        }
        return self.request_json(
            "GET", path, params=params, authenticated=False
        ).data

    def get_species_thermo(
        self,
        species_entry_id: int | str,
        *,
        temperature_min: float | None = None,
        temperature_max: float | None = None,
        model_kind: str | None = None,
        level_of_theory_id: int | None = None,
        level_of_theory_ref: str | None = None,
        software: str | None = None,
        min_review_status: str | None = None,
        include_deprecated: bool | None = None,
        include_rejected: bool | None = None,
        include: list[str] | None = None,
        collapse: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
    ) -> SpeciesThermoResponse:
        """``GET /scientific/species-entries/{species_entry_id}/thermo``.

        Phase C: ``species_entry_id`` accepts the integer
        ``species_entry.id`` or a public ref of the form ``spe_...``.
        Supplying a ``species.id`` or a wrong-prefix ref returns 422 / 404.
        Returns the parsed ``ScientificSpeciesThermoResponse`` JSON
        envelope.
        """
        path = f"/scientific/species-entries/{species_entry_id}/thermo"
        params = {
            "temperature_min": temperature_min,
            "temperature_max": temperature_max,
            "model_kind": model_kind,
            "level_of_theory_id": level_of_theory_id,
            "level_of_theory_ref": level_of_theory_ref,
            "software": software,
            "min_review_status": min_review_status,
            "include_deprecated": include_deprecated,
            "include_rejected": include_rejected,
            "include": include,
            "collapse": collapse,
            "offset": offset,
            "limit": limit,
        }
        return self.request_json(
            "GET", path, params=params, authenticated=False
        ).data

    def search_thermo(
        self,
        *,
        smiles: str | None = None,
        inchi: str | None = None,
        inchi_key: str | None = None,
        formula: str | None = None,
        charge: int | None = None,
        multiplicity: int | None = None,
        electronic_state_kind: str | None = None,
        species_entry_kind: str | None = None,
        species_ref: str | None = None,
        species_entry_ref: str | None = None,
        temperature_min: float | None = None,
        temperature_max: float | None = None,
        model_kind: str | None = None,
        level_of_theory_id: int | None = None,
        level_of_theory_ref: str | None = None,
        software: str | None = None,
        min_review_status: str | None = None,
        include_deprecated: bool | None = None,
        include_rejected: bool | None = None,
        include: list[str] | None = None,
        collapse: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
        method: str = "POST",
    ) -> ThermoSearchResponse:
        """``GET|POST /scientific/thermo/search`` — chemistry-first thermo search.

        Returns thermo records along with the resolved species/species_entry
        identity context, so callers don't have to chain
        ``search_species`` → ``get_species_thermo`` themselves. Defaults to
        POST (mirrors ``search_reactions``); pass ``method="GET"`` for the
        query-string form. Phase C: ``species_ref``, ``species_entry_ref``,
        and ``level_of_theory_ref`` may be supplied as filter handles.
        """
        body = {
            "smiles": smiles,
            "inchi": inchi,
            "inchi_key": inchi_key,
            "formula": formula,
            "charge": charge,
            "multiplicity": multiplicity,
            "electronic_state_kind": electronic_state_kind,
            "species_entry_kind": species_entry_kind,
            "species_ref": species_ref,
            "species_entry_ref": species_entry_ref,
            "temperature_min": temperature_min,
            "temperature_max": temperature_max,
            "model_kind": model_kind,
            "level_of_theory_id": level_of_theory_id,
            "level_of_theory_ref": level_of_theory_ref,
            "software": software,
            "min_review_status": min_review_status,
            "include_deprecated": include_deprecated,
            "include_rejected": include_rejected,
            "include": include,
            "collapse": collapse,
            "offset": offset,
            "limit": limit,
        }
        if method.upper() == "GET":
            return self.request_json(
                "GET",
                "/scientific/thermo/search",
                params=body,
                authenticated=False,
            ).data
        return self.request_json(
            "POST",
            "/scientific/thermo/search",
            json={k: v for k, v in body.items() if v is not None},
            authenticated=False,
        ).data

    def search_kinetics(
        self,
        *,
        reactants: list[str] | None = None,
        products: list[str] | None = None,
        direction: str | None = None,
        family: str | None = None,
        reaction_ref: str | None = None,
        reaction_entry_ref: str | None = None,
        temperature_min: float | None = None,
        temperature_max: float | None = None,
        pressure_bar: float | None = None,
        pressure: float | None = None,
        model_kind: str | None = None,
        level_of_theory_id: int | None = None,
        level_of_theory_ref: str | None = None,
        software: str | None = None,
        min_review_status: str | None = None,
        include_deprecated: bool | None = None,
        include_rejected: bool | None = None,
        include: list[str] | None = None,
        collapse: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
        method: str = "POST",
    ) -> KineticsSearchResponse:
        """``GET|POST /scientific/kinetics/search`` — chemistry-first kinetics search.

        Returns kinetics records along with the resolved reaction/reaction_entry
        identity context. Defaults to POST because reactant/product SMILES
        encode awkwardly in URL query strings; pass ``method="GET"`` for the
        repeated-query-param form. Non-TS-backed kinetics surface with null
        TS-chain provenance fields, exactly like the entry-id detail endpoint.
        ``pressure_bar`` is canonical; ``pressure`` is a deprecated alias.
        """
        body = {
            "reactants": reactants,
            "products": products,
            "direction": direction,
            "family": family,
            "reaction_ref": reaction_ref,
            "reaction_entry_ref": reaction_entry_ref,
            "temperature_min": temperature_min,
            "temperature_max": temperature_max,
            "pressure_bar": pressure_bar,
            "pressure": pressure,
            "model_kind": model_kind,
            "level_of_theory_id": level_of_theory_id,
            "level_of_theory_ref": level_of_theory_ref,
            "software": software,
            "min_review_status": min_review_status,
            "include_deprecated": include_deprecated,
            "include_rejected": include_rejected,
            "include": include,
            "collapse": collapse,
            "offset": offset,
            "limit": limit,
        }
        if method.upper() == "GET":
            return self.request_json(
                "GET",
                "/scientific/kinetics/search",
                params=body,
                authenticated=False,
            ).data
        return self.request_json(
            "POST",
            "/scientific/kinetics/search",
            json={k: v for k, v in body.items() if v is not None},
            authenticated=False,
        ).data

    def search_species_calculations(
        self,
        *,
        smiles: str | None = None,
        inchi: str | None = None,
        inchi_key: str | None = None,
        formula: str | None = None,
        charge: int | None = None,
        multiplicity: int | None = None,
        electronic_state_kind: str | None = None,
        species_entry_kind: str | None = None,
        species_id: int | None = None,
        species_entry_id: int | None = None,
        species_ref: str | None = None,
        species_entry_ref: str | None = None,
        calculation_type: str | None = None,
        level_of_theory_id: int | None = None,
        level_of_theory_ref: str | None = None,
        method: str | None = None,
        basis: str | None = None,
        software: str | None = None,
        workflow_tool: str | None = None,
        scientific_origin: str | None = None,
        calculation_quality: str | None = None,
        min_review_status: str | None = None,
        include_deprecated: bool | None = None,
        include_rejected: bool | None = None,
        include_rejected_quality: bool | None = None,
        ranking: str | None = None,
        include: list[str] | None = None,
        collapse: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
        method_http: str = "POST",
    ) -> SpeciesCalculationsSearchResponse:
        """``GET|POST /scientific/species-calculations/search`` — chemistry-first
        species calculation/conformer search.

        Returns calculation-centered records that include resolved species
        identity, energy (when applicable), level of theory, software,
        conformer context (when present), geometry IDs, validation, review
        state, and provenance. ``ranking='lowest_energy'`` requires
        ``calculation_type='sp'`` or ``calculation_type='opt'``.

        Defaults to POST. Pass ``method_http='GET'`` to use the
        query-string form. (The kwarg is named ``method_http`` rather than
        ``method`` to avoid colliding with the LoT ``method`` filter.)
        """
        body = {
            "smiles": smiles,
            "inchi": inchi,
            "inchi_key": inchi_key,
            "formula": formula,
            "charge": charge,
            "multiplicity": multiplicity,
            "electronic_state_kind": electronic_state_kind,
            "species_entry_kind": species_entry_kind,
            "species_id": species_id,
            "species_entry_id": species_entry_id,
            "species_ref": species_ref,
            "species_entry_ref": species_entry_ref,
            "calculation_type": calculation_type,
            "level_of_theory_id": level_of_theory_id,
            "level_of_theory_ref": level_of_theory_ref,
            "method": method,
            "basis": basis,
            "software": software,
            "workflow_tool": workflow_tool,
            "scientific_origin": scientific_origin,
            "calculation_quality": calculation_quality,
            "min_review_status": min_review_status,
            "include_deprecated": include_deprecated,
            "include_rejected": include_rejected,
            "include_rejected_quality": include_rejected_quality,
            "ranking": ranking,
            "include": include,
            "collapse": collapse,
            "offset": offset,
            "limit": limit,
        }
        if method_http.upper() == "GET":
            return self.request_json(
                "GET",
                "/scientific/species-calculations/search",
                params=body,
                authenticated=False,
            ).data
        return self.request_json(
            "POST",
            "/scientific/species-calculations/search",
            json={k: v for k, v in body.items() if v is not None},
            authenticated=False,
        ).data

    def _request_scientific_search(
        self,
        path: str,
        parameters: Mapping[str, Any],
        *,
        method_http: _ScientificSearchMethod,
    ) -> Any:
        """Dispatch one scientific search through its GET or POST form."""

        if not isinstance(method_http, str):
            raise ValueError("method_http must be 'GET' or 'POST'.")
        normalized_method = method_http.upper()
        if normalized_method not in {"GET", "POST"}:
            raise ValueError("method_http must be 'GET' or 'POST'.")
        if normalized_method == "GET":
            return self.request_json(
                "GET",
                path,
                params=parameters,
                authenticated=False,
            ).data
        return self.request_json(
            "POST",
            path,
            json={
                key: value
                for key, value in parameters.items()
                if value is not None
            },
            authenticated=False,
        ).data

    def search_networks(
        self,
        *,
        network_ref: str | None = None,
        species_ref: str | None = None,
        species_entry_ref: str | None = None,
        reaction_ref: str | None = None,
        reaction_entry_ref: str | None = None,
        has_species: bool | None = None,
        has_reactions: bool | None = None,
        has_states: bool | None = None,
        has_channels: bool | None = None,
        has_solves: bool | None = None,
        has_kinetics: bool | None = None,
        has_chebyshev: bool | None = None,
        has_plog: bool | None = None,
        has_point_kinetics: bool | None = None,
        method: str | None = None,
        basis: str | None = None,
        software: str | None = None,
        software_version: str | None = None,
        workflow_tool: str | None = None,
        workflow_tool_version: str | None = None,
        temperature_min: float | None = None,
        temperature_max: float | None = None,
        pressure_min: float | None = None,
        pressure_max: float | None = None,
        min_review_status: str | None = None,
        include_rejected: bool | None = None,
        include_deprecated: bool | None = None,
        include: list[str] | None = None,
        offset: int | None = None,
        limit: int | None = None,
        method_http: _ScientificSearchMethod = "POST",
    ) -> NetworkSearchResponse:
        """Search pressure-dependence networks by chemistry and provenance."""

        body = {
            "network_ref": network_ref,
            "species_ref": species_ref,
            "species_entry_ref": species_entry_ref,
            "reaction_ref": reaction_ref,
            "reaction_entry_ref": reaction_entry_ref,
            "has_species": has_species,
            "has_reactions": has_reactions,
            "has_states": has_states,
            "has_channels": has_channels,
            "has_solves": has_solves,
            "has_kinetics": has_kinetics,
            "has_chebyshev": has_chebyshev,
            "has_plog": has_plog,
            "has_point_kinetics": has_point_kinetics,
            "method": method,
            "basis": basis,
            "software": software,
            "software_version": software_version,
            "workflow_tool": workflow_tool,
            "workflow_tool_version": workflow_tool_version,
            "temperature_min": temperature_min,
            "temperature_max": temperature_max,
            "pressure_min": pressure_min,
            "pressure_max": pressure_max,
            "min_review_status": min_review_status,
            "include_rejected": include_rejected,
            "include_deprecated": include_deprecated,
            "include": include,
            "offset": offset,
            "limit": limit,
        }
        return self._request_scientific_search(
            "/scientific/networks/search",
            body,
            method_http=method_http,
        )

    def search_network_kinetics(
        self,
        *,
        network_kinetics_ref: str | None = None,
        network_ref: str | None = None,
        network_solve_ref: str | None = None,
        source_species_entry_refs: list[str] | None = None,
        sink_species_entry_refs: list[str] | None = None,
        source_smiles: list[str] | None = None,
        sink_smiles: list[str] | None = None,
        model_kind: str | None = None,
        temperature_min: float | None = None,
        temperature_max: float | None = None,
        pressure_min: float | None = None,
        pressure_max: float | None = None,
        has_chebyshev: bool | None = None,
        has_plog: bool | None = None,
        has_points: bool | None = None,
        has_source_calculations: bool | None = None,
        method: str | None = None,
        basis: str | None = None,
        software: str | None = None,
        software_version: str | None = None,
        workflow_tool: str | None = None,
        workflow_tool_version: str | None = None,
        min_review_status: str | None = None,
        include_rejected: bool | None = None,
        include_deprecated: bool | None = None,
        include: list[str] | None = None,
        offset: int | None = None,
        limit: int | None = None,
        method_http: _ScientificSearchMethod = "POST",
    ) -> NetworkKineticsSearchResponse:
        """Search PDep kinetics with stoichiometric source/sink filters."""

        body = {
            "network_kinetics_ref": network_kinetics_ref,
            "network_ref": network_ref,
            "network_solve_ref": network_solve_ref,
            "source_species_entry_refs": source_species_entry_refs,
            "sink_species_entry_refs": sink_species_entry_refs,
            "source_smiles": source_smiles,
            "sink_smiles": sink_smiles,
            "model_kind": model_kind,
            "temperature_min": temperature_min,
            "temperature_max": temperature_max,
            "pressure_min": pressure_min,
            "pressure_max": pressure_max,
            "has_chebyshev": has_chebyshev,
            "has_plog": has_plog,
            "has_points": has_points,
            "has_source_calculations": has_source_calculations,
            "method": method,
            "basis": basis,
            "software": software,
            "software_version": software_version,
            "workflow_tool": workflow_tool,
            "workflow_tool_version": workflow_tool_version,
            "min_review_status": min_review_status,
            "include_rejected": include_rejected,
            "include_deprecated": include_deprecated,
            "include": include,
            "offset": offset,
            "limit": limit,
        }
        return self._request_scientific_search(
            "/scientific/network-kinetics/search",
            body,
            method_http=method_http,
        )

    def search_statmech(
        self,
        *,
        species_ref: str | None = None,
        species_entry_ref: str | None = None,
        statmech_ref: str | None = None,
        conformer_group_ref: str | None = None,
        conformer_observation_ref: str | None = None,
        model_kind: str | None = None,
        has_source_calculations: bool | None = None,
        has_freq_calculation: bool | None = None,
        has_rotor_scans: bool | None = None,
        has_torsions: bool | None = None,
        method: str | None = None,
        basis: str | None = None,
        software: str | None = None,
        software_version: str | None = None,
        workflow_tool: str | None = None,
        workflow_tool_version: str | None = None,
        min_review_status: str | None = None,
        include_rejected: bool | None = None,
        include_deprecated: bool | None = None,
        include: list[str] | None = None,
        offset: int | None = None,
        limit: int | None = None,
        method_http: _ScientificSearchMethod = "POST",
    ) -> StatmechSearchResponse:
        """Search statmech records by species, model, evidence, or provenance."""

        body = {
            "species_ref": species_ref,
            "species_entry_ref": species_entry_ref,
            "statmech_ref": statmech_ref,
            "conformer_group_ref": conformer_group_ref,
            "conformer_observation_ref": conformer_observation_ref,
            "model_kind": model_kind,
            "has_source_calculations": has_source_calculations,
            "has_freq_calculation": has_freq_calculation,
            "has_rotor_scans": has_rotor_scans,
            "has_torsions": has_torsions,
            "method": method,
            "basis": basis,
            "software": software,
            "software_version": software_version,
            "workflow_tool": workflow_tool,
            "workflow_tool_version": workflow_tool_version,
            "min_review_status": min_review_status,
            "include_rejected": include_rejected,
            "include_deprecated": include_deprecated,
            "include": include,
            "offset": offset,
            "limit": limit,
        }
        return self._request_scientific_search(
            "/scientific/statmech/search",
            body,
            method_http=method_http,
        )

    def search_transport(
        self,
        *,
        species_ref: str | None = None,
        species_entry_ref: str | None = None,
        transport_ref: str | None = None,
        model_kind: str | None = None,
        has_source_calculations: bool | None = None,
        has_lj_parameters: bool | None = None,
        has_dipole_moment: bool | None = None,
        has_polarizability: bool | None = None,
        has_rotational_relaxation: bool | None = None,
        method: str | None = None,
        basis: str | None = None,
        software: str | None = None,
        software_version: str | None = None,
        workflow_tool: str | None = None,
        workflow_tool_version: str | None = None,
        min_review_status: str | None = None,
        include_rejected: bool | None = None,
        include_deprecated: bool | None = None,
        include: list[str] | None = None,
        offset: int | None = None,
        limit: int | None = None,
        method_http: _ScientificSearchMethod = "POST",
    ) -> TransportSearchResponse:
        """Search transport records by species, parameters, or provenance."""

        body = {
            "species_ref": species_ref,
            "species_entry_ref": species_entry_ref,
            "transport_ref": transport_ref,
            "model_kind": model_kind,
            "has_source_calculations": has_source_calculations,
            "has_lj_parameters": has_lj_parameters,
            "has_dipole_moment": has_dipole_moment,
            "has_polarizability": has_polarizability,
            "has_rotational_relaxation": has_rotational_relaxation,
            "method": method,
            "basis": basis,
            "software": software,
            "software_version": software_version,
            "workflow_tool": workflow_tool,
            "workflow_tool_version": workflow_tool_version,
            "min_review_status": min_review_status,
            "include_rejected": include_rejected,
            "include_deprecated": include_deprecated,
            "include": include,
            "offset": offset,
            "limit": limit,
        }
        return self._request_scientific_search(
            "/scientific/transport/search",
            body,
            method_http=method_http,
        )

    def search_artifacts(
        self,
        *,
        artifact_kind: str | None = None,
        filename: str | None = None,
        filename_contains: str | None = None,
        sha256: str | None = None,
        has_sha256: bool | None = None,
        has_bytes: bool | None = None,
        bytes_min: int | None = None,
        bytes_max: int | None = None,
        calculation_ref: str | None = None,
        calculation_type: str | None = None,
        quality: str | None = None,
        method: str | None = None,
        basis: str | None = None,
        software: str | None = None,
        software_version: str | None = None,
        workflow_tool: str | None = None,
        workflow_tool_version: str | None = None,
        species_entry_ref: str | None = None,
        transition_state_entry_ref: str | None = None,
        conformer_observation_ref: str | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        min_review_status: str | None = None,
        include_rejected: bool | None = None,
        include_deprecated: bool | None = None,
        include: list[str] | None = None,
        offset: int | None = None,
        limit: int | None = None,
        method_http: _ScientificSearchMethod = "POST",
    ) -> ArtifactSearchResponse:
        """Search artifact metadata; raw artifact bodies are never returned."""

        body = {
            "artifact_kind": artifact_kind,
            "filename": filename,
            "filename_contains": filename_contains,
            "sha256": sha256,
            "has_sha256": has_sha256,
            "has_bytes": has_bytes,
            "bytes_min": bytes_min,
            "bytes_max": bytes_max,
            "calculation_ref": calculation_ref,
            "calculation_type": calculation_type,
            "quality": quality,
            "method": method,
            "basis": basis,
            "software": software,
            "software_version": software_version,
            "workflow_tool": workflow_tool,
            "workflow_tool_version": workflow_tool_version,
            "species_entry_ref": species_entry_ref,
            "transition_state_entry_ref": transition_state_entry_ref,
            "conformer_observation_ref": conformer_observation_ref,
            "created_after": created_after,
            "created_before": created_before,
            "min_review_status": min_review_status,
            "include_rejected": include_rejected,
            "include_deprecated": include_deprecated,
            "include": include,
            "offset": offset,
            "limit": limit,
        }
        return self._request_scientific_search(
            "/scientific/artifacts/search",
            body,
            method_http=method_http,
        )

    def iter_species(self, **parameters: Any) -> Iterator[SpeciesRecord]:
        """Lazily yield every species record matching ``search_species``."""

        return iter_paginated_records(self.search_species, parameters)

    def iter_reactions(self, **parameters: Any) -> Iterator[ReactionRecord]:
        """Lazily yield every reaction record matching ``search_reactions``."""

        return iter_paginated_records(self.search_reactions, parameters)

    def iter_thermo(self, **parameters: Any) -> Iterator[ThermoRecord]:
        """Lazily yield every thermo record matching ``search_thermo``."""

        return iter_paginated_records(self.search_thermo, parameters)

    def iter_kinetics(self, **parameters: Any) -> Iterator[KineticsRecord]:
        """Lazily yield every kinetics record matching ``search_kinetics``."""

        return iter_paginated_records(self.search_kinetics, parameters)

    def iter_species_calculations(
        self, **parameters: Any
    ) -> Iterator[SpeciesCalculationRecord]:
        """Lazily yield matching species-calculation records."""

        return iter_paginated_records(self.search_species_calculations, parameters)

    def iter_networks(self, **parameters: Any) -> Iterator[NetworkRecord]:
        """Lazily yield every network matching ``search_networks``."""

        return iter_paginated_records(self.search_networks, parameters)

    def iter_network_kinetics(
        self, **parameters: Any
    ) -> Iterator[NetworkKineticsRecord]:
        """Lazily yield PDep kinetics records matching the supplied filters."""

        return iter_paginated_records(self.search_network_kinetics, parameters)

    def iter_statmech(self, **parameters: Any) -> Iterator[StatmechRecord]:
        """Lazily yield statmech records matching the supplied filters."""

        return iter_paginated_records(self.search_statmech, parameters)

    def iter_transport(self, **parameters: Any) -> Iterator[TransportRecord]:
        """Lazily yield transport records matching the supplied filters."""

        return iter_paginated_records(self.search_transport, parameters)

    def iter_artifacts(self, **parameters: Any) -> Iterator[ArtifactRecord]:
        """Lazily yield artifact metadata records matching the filters."""

        return iter_paginated_records(self.search_artifacts, parameters)

    def get_reaction_full(
        self,
        reaction_entry_id: int | str,
        *,
        include: list[str] | None = None,
        include_review: str | None = None,
        min_review_status: str | None = None,
        include_deprecated: bool | None = None,
        include_rejected: bool | None = None,
    ) -> Any:
        """``GET /scientific/reaction-entries/{reaction_entry_id}/full``.

        Phase C: ``reaction_entry_id`` accepts the integer
        ``reaction_entry.id`` or a public ref of the form ``rxe_...``.
        Composite scientific read joining species, kinetics, transition
        states, calculations, and review summary into one document. Returns
        the parsed ``ScientificReactionFullResponse`` JSON envelope. The
        backend never fabricates TS links for non-TS-backed kinetics.
        """
        path = f"/scientific/reaction-entries/{reaction_entry_id}/full"
        params = {
            "include": include,
            "include_review": include_review,
            "min_review_status": min_review_status,
            "include_deprecated": include_deprecated,
            "include_rejected": include_rejected,
        }
        return self.request_json(
            "GET", path, params=params, authenticated=False
        ).data

    def get_geometry(
        self,
        geometry_handle: int | str,
        *,
        include: list[str] | None = None,
    ) -> Any:
        """``GET /scientific/geometries/{geometry_handle}``.

        Follow-up read that returns the full coordinate payload behind
        a geometry ref. ``geometry_handle`` accepts the integer
        ``geometry.id`` or a public ref of the form ``geom_…`` —
        wrong-prefix refs return 422, unknown refs return 404. The
        response surfaces ``symbols`` + ``coords`` (Ångström,
        Cartesian) plus a compact provenance summary listing every
        calculation that produced or consumed the geometry.

        Phase D: ``geometry_id`` is hidden by default and restored
        only when ``include=internal_ids`` is supplied and the
        deployment allows it.
        """
        path = f"/scientific/geometries/{geometry_handle}"
        params = {"include": include}
        return self.request_json(
            "GET", path, params=params, authenticated=False
        ).data


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _clean_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """Drop ``None`` entries and serialize ``bool`` as lowercase string.

    Lists are passed through (``httpx`` repeats them as separate query
    parameters). Empty lists are dropped — supplying ``include=[]`` is
    semantically equivalent to omitting the parameter.
    """
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
    return out


__all__ = [
    "TCKDBClient",
    "TCKDBResponse",
    "UPLOAD_ENDPOINTS",
    "API_KEY_HEADER",
    "IDEMPOTENCY_HEADER",
    "IDEMPOTENCY_REPLAYED_HEADER",
    "CLIENT_NAME",
    "CLIENT_NAME_HEADER",
    "CLIENT_VERSION_HEADER",
]
