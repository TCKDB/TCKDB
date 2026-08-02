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

import json as _json
import time
from collections.abc import Callable, Iterable, Iterator
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
from tckdb_client.pagination import iter_keyset_records, iter_paginated_records
from tckdb_client.retry import RetryPolicy
from tckdb_client.scientific_types import (
    ArtifactRecord,
    ArtifactSearchResponse,
    CalculationAnalyticsRecord,
    CalculationAnalyticsResponse,
    CalculationDetailResponse,
    CalculationSearchResponse,
    ConformerGroupDetailResponse,
    ConformerObservationDetailResponse,
    ConformerRecord,
    ConformerSearchResponse,
    EnergyCorrectionSchemeDetailResponse,
    EnergyCorrectionSchemeRecord,
    EnergyCorrectionSchemeSearchResponse,
    FrequencyScaleFactorDetailResponse,
    FrequencyScaleFactorRecord,
    FrequencyScaleFactorSearchResponse,
    JSONDict,
    KineticsAnalyticsRecord,
    KineticsAnalyticsResponse,
    KineticsRecord,
    KineticsSearchResponse,
    LiteratureDetailResponse,
    LiteratureLinkedRecord,
    LiteratureRecordsResponse,
    NetworkKineticsRecord,
    NetworkKineticsSearchResponse,
    NetworkRecord,
    NetworkSearchResponse,
    NetworkSolveRecord,
    NetworkSolveSearchResponse,
    ReactionKineticsResponse,
    ReactionRecord,
    ReactionSearchResponse,
    SpeciesCalculationRecord,
    SpeciesCalculationsSearchResponse,
    SpeciesRecord,
    SpeciesSearchResponse,
    SpeciesStructureRecord,
    SpeciesStructureSearchResponse,
    SpeciesThermoResponse,
    StatmechAnalyticsRecord,
    StatmechAnalyticsResponse,
    StatmechRecord,
    StatmechSearchResponse,
    ThermoAnalyticsRecord,
    ThermoAnalyticsResponse,
    ThermoRecord,
    ThermoSearchResponse,
    TransitionStateDetailResponse,
    TransitionStateEntryDetailResponse,
    TransitionStateEntryRecord,
    TransitionStateSearchResponse,
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

# Async job enqueue endpoints, keyed by the backend's ``UploadJobKind``.
# Deliberately not the same key set as ``UPLOAD_ENDPOINTS``: ``statmech`` and
# ``computed_species`` have synchronous upload endpoints but no job route, so
# enqueuing them must fail loudly rather than POST to a path that 404s.
JOB_ENDPOINTS: dict[str, str] = {
    "computed_reaction": "/jobs/computed-reaction",
    "conformer": "/jobs/conformer",
    "reaction": "/jobs/reaction",
    "kinetics": "/jobs/kinetics",
    "network": "/jobs/network",
    "network_pdep": "/jobs/network/pdep",
    "thermo": "/jobs/thermo",
    "transition_state": "/jobs/transition-state",
    "transport": "/jobs/transport",
}

# Mirrors the terminal members of the backend's ``UploadJobStatus``; the
# non-terminal ones (``queued``, ``processing``) are what ``wait_for_job``
# keeps polling through.
TERMINAL_JOB_STATUSES: frozenset[str] = frozenset({"complete", "failed"})


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
    retry:
        Optional :class:`~tckdb_client.retry.RetryPolicy`. Retrying is
        **off** unless one is supplied: a replayed write that the server
        already committed would duplicate a scientific record, so opting
        in is the caller's decision, not a default.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: float = 30.0,
        *,
        transport: httpx.BaseTransport | None = None,
        retry: RetryPolicy | None = None,
    ) -> None:
        if not isinstance(base_url, str) or not base_url:
            raise ValueError("base_url must be a non-empty string.")
        if retry is not None and not isinstance(retry, RetryPolicy):
            raise TypeError("retry must be a RetryPolicy instance or None.")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._retry = retry
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
        response = self._send(
            method,
            path,
            json=json,
            params=params,
            authenticated=authenticated,
            idempotency_key=idempotency_key,
            extra_headers=extra_headers,
        )
        return self._handle_response(response)

    def _request_raw(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        accept: str = "application/octet-stream",
        authenticated: bool = False,
    ) -> bytes:
        """Perform a request whose body is *not* JSON and return it verbatim.

        Artifact downloads and the Chemkin export return opaque bytes.
        Routing them through :meth:`request_json` would decode them as text
        and corrupt anything that is not UTF-8, so they take this path —
        the error mapping and retry behaviour are shared, only the success
        branch differs.
        """

        response = self._send(
            method,
            path,
            json=json,
            params=params,
            authenticated=authenticated,
            extra_headers={"Accept": accept},
        )
        if response.is_success:
            return response.content
        parsed: Any = None
        text: str | None = None
        try:
            parsed = response.json()
        except ValueError:
            text = response.text or None
        raise self._build_http_error(
            status_code=response.status_code,
            parsed=parsed,
            text=text,
            headers=response.headers,
        )

    def _send(
        self,
        method: str,
        path: str,
        *,
        json: Any,
        params: Mapping[str, Any] | None,
        authenticated: bool,
        idempotency_key: str | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """Issue the request, replaying it only when replay cannot duplicate work.

        Returns the raw response — including error responses — so the two
        callers can decode the body their own way. Transport failures still
        raise :class:`TCKDBConnectionError`, because there is no response
        to hand back.
        """

        url = self._full_url(path)
        headers = self._build_headers(
            authenticated=authenticated,
            json_body=json is not None,
            idempotency_key=idempotency_key,
            extra=extra_headers,
        )
        cleaned = _clean_params(params) if params else None
        policy = self._retry
        eligible = policy is not None and policy.method_is_retryable(
            method, has_idempotency_key=_carries_idempotency_key(headers)
        )
        max_attempts = policy.max_attempts if policy is not None and eligible else 1

        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._client.request(
                    method, url, json=json, params=cleaned, headers=headers
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if policy is None or not eligible or attempt >= max_attempts:
                    if isinstance(exc, httpx.TimeoutException):
                        raise TCKDBConnectionError(
                            f"Request timed out: {exc}"
                        ) from exc
                    raise TCKDBConnectionError(f"Network error: {exc}") from exc
                policy.sleep(policy.delay_for(attempt))
                continue

            if (
                policy is None
                or not eligible
                or attempt >= max_attempts
                or response.is_success
                or not policy.status_is_retryable(response.status_code)
                # A server asking for a longer pause than the caller
                # budgeted is not a transient blip; surface it now rather
                # than block for the maintenance window.
                or policy.retry_after_exceeds_budget(response.headers)
            ):
                return response
            policy.sleep(policy.delay_for(attempt, response.headers))

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

    def readyz(self) -> Any:
        """Readiness probe: ``/health`` says the process is up, this says
        its dependencies (database, object store) answered."""
        return self.request_json("GET", "/readyz", authenticated=False).data

    def get_meta(self) -> Any:
        """Deployment metadata — versions, limits, and enabled features.

        Read this before assuming a capability exists: the same client
        talks to hosted TCKDB and to self-hosted instances that may be
        running an older build.
        """
        return self.request_json("GET", "/meta", authenticated=False).data

    def me(self) -> dict:
        """Return the authenticated user profile (``GET /auth/me``)."""
        return self.request_json("GET", "/auth/me").data

    def get_json(self, path: str) -> Any:
        return self.request_json("GET", path).data

    def get_calculation(
        self,
        calculation_ref_or_id: str | int,
        *,
        include: list[str] | None = None,
        profile: str | None = None,
    ) -> CalculationDetailResponse:
        """Fetch one scientific calculation, optionally including its environment."""
        return self.request_json(
            "GET",
            f"/scientific/calculations/{calculation_ref_or_id}",
            params={"include": include, "profile": profile},
            authenticated=False,
        ).data

    def search_calculations(
        self,
        *,
        profile: str | None = None,
        method_http: _ScientificSearchMethod = "POST",
        **filters: Any,
    ) -> CalculationSearchResponse:
        """Search scientific calculations; ``include`` may request execution_environment."""
        return self._request_scientific_search(
            "/scientific/calculations/search",
            filters,
            method_http=method_http,
            profile=profile,
        )

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
    # Async job lifecycle
    # ------------------------------------------------------------------

    def enqueue_job(
        self,
        kind: str,
        payload: Any,
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        """Enqueue an upload as an async job and return the enqueue response.

        ``kind`` accepts a short name from :data:`JOB_ENDPOINTS` or an explicit
        path starting with ``/``. An unrecognised short name raises before any
        request is issued -- a kind with no job route (``statmech``,
        ``computed_species``) would otherwise POST to a path that 404s, and a
        404 arriving after the payload has crossed the wire reads like a server
        fault rather than a caller mistake.
        """
        if kind in JOB_ENDPOINTS:
            path = JOB_ENDPOINTS[kind]
        elif kind.startswith(("/", "http://", "https://")):
            path = kind
        else:
            raise ValueError(
                f"Unknown job kind: {kind!r}. Pass an explicit path starting "
                f"with '/' or one of {sorted(JOB_ENDPOINTS)}."
            )
        return self.post_json(path, payload, idempotency_key=idempotency_key)

    def get_job_status(self, job_id: str) -> Any:
        """Fetch one job's current status document."""
        if not job_id:
            raise ValueError("job_id must be a non-empty string.")
        return self.get_json(f"/jobs/{job_id}")

    def wait_for_job(
        self,
        job_id: str,
        *,
        timeout: float | None = None,
        poll_interval: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> Any:
        """Poll ``job_id`` until it reaches a terminal state, and return it.

        A ``failed`` job is *returned*, not raised: failure is a legitimate
        outcome the caller needs the error payload for, whereas never reaching
        a terminal state is not, and raises ``TimeoutError``.

        ``sleep`` and ``monotonic`` are injectable so the polling loop can be
        tested without real time passing.
        """
        deadline = None if timeout is None else monotonic() + timeout
        while True:
            status_doc = self.get_job_status(job_id)
            status = (status_doc or {}).get("status")
            if status in TERMINAL_JOB_STATUSES:
                return status_doc
            if deadline is not None and monotonic() >= deadline:
                raise TimeoutError(
                    f"Job {job_id!r} did not reach a terminal state within "
                    f"{timeout}s (last status: {status!r})."
                )
            sleep(poll_interval)

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
        profile: str | None = None,
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
            "profile": profile,
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
        profile: str | None = None,
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
                params={**common, "profile": profile},
                authenticated=False,
            ).data
        body = {k: v for k, v in common.items() if v is not None}
        return self.request_json(
            "POST",
            "/scientific/reactions/search",
            json=body,
            params={"profile": profile},
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
        profile: str | None = None,
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
            "profile": profile,
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
        profile: str | None = None,
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
            "profile": profile,
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
        profile: str | None = None,
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
                params={**body, "profile": profile},
                authenticated=False,
            ).data
        return self.request_json(
            "POST",
            "/scientific/thermo/search",
            json={k: v for k, v in body.items() if v is not None},
            params={"profile": profile},
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
        profile: str | None = None,
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
                params={**body, "profile": profile},
                authenticated=False,
            ).data
        return self.request_json(
            "POST",
            "/scientific/kinetics/search",
            json={k: v for k, v in body.items() if v is not None},
            params={"profile": profile},
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
        profile: str | None = None,
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
                params={**body, "profile": profile},
                authenticated=False,
            ).data
        return self.request_json(
            "POST",
            "/scientific/species-calculations/search",
            json={k: v for k, v in body.items() if v is not None},
            params={"profile": profile},
            authenticated=False,
        ).data

    def _request_scientific_search(
        self,
        path: str,
        parameters: Mapping[str, Any],
        *,
        method_http: _ScientificSearchMethod,
        profile: str | None = None,
    ) -> Any:
        """Dispatch one scientific search through its GET or POST form.

        ``profile`` rides the query string in **both** forms. The backend
        resolves it from a router-level dependency that only reads the
        query, so a profile smuggled into the JSON body would be silently
        ignored and the answer would come back under the wrong contract.
        """

        if not isinstance(method_http, str):
            raise ValueError("method_http must be 'GET' or 'POST'.")
        normalized_method = method_http.upper()
        if normalized_method not in {"GET", "POST"}:
            raise ValueError("method_http must be 'GET' or 'POST'.")
        if normalized_method == "GET":
            return self.request_json(
                "GET",
                path,
                params={**parameters, "profile": profile},
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
            params={"profile": profile},
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
        profile: str | None = None,
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
            profile=profile,
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
        profile: str | None = None,
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
            profile=profile,
        )

    def search_network_solves(
        self,
        *,
        network_solve_ref: str | None = None,
        network_ref: str | None = None,
        solve_method: str | None = None,
        temperature_min: float | None = None,
        temperature_max: float | None = None,
        pressure_min: float | None = None,
        pressure_max: float | None = None,
        has_bath_gas: bool | None = None,
        has_energy_transfer: bool | None = None,
        has_source_calculations: bool | None = None,
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
        min_review_status: str | None = None,
        include_rejected: bool | None = None,
        include_deprecated: bool | None = None,
        include: list[str] | None = None,
        offset: int | None = None,
        limit: int | None = None,
        profile: str | None = None,
        method_http: _ScientificSearchMethod = "POST",
    ) -> NetworkSolveSearchResponse:
        """Search pressure-dependence network solves and their evidence."""

        body = {
            "network_solve_ref": network_solve_ref,
            "network_ref": network_ref,
            "solve_method": solve_method,
            "temperature_min": temperature_min,
            "temperature_max": temperature_max,
            "pressure_min": pressure_min,
            "pressure_max": pressure_max,
            "has_bath_gas": has_bath_gas,
            "has_energy_transfer": has_energy_transfer,
            "has_source_calculations": has_source_calculations,
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
            "min_review_status": min_review_status,
            "include_rejected": include_rejected,
            "include_deprecated": include_deprecated,
            "include": include,
            "offset": offset,
            "limit": limit,
        }
        return self._request_scientific_search(
            "/scientific/network-solves/search",
            body,
            method_http=method_http,
            profile=profile,
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
        profile: str | None = None,
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
            profile=profile,
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
        profile: str | None = None,
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
            profile=profile,
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
        profile: str | None = None,
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
            profile=profile,
        )

    def search_species_structures(
        self,
        *,
        query_smiles: str | None = None,
        query_smarts: str | None = None,
        query_inchi: str | None = None,
        query_inchi_key: str | None = None,
        mode: str | None = None,
        similarity_threshold: float | None = None,
        min_review_status: str | None = None,
        include_rejected: bool | None = None,
        include_deprecated: bool | None = None,
        sort: str | None = None,
        include: list[str] | None = None,
        offset: int | None = None,
        limit: int | None = None,
        profile: str | None = None,
        method_http: _ScientificSearchMethod = "POST",
    ) -> SpeciesStructureSearchResponse:
        """Structure search: exact, substructure, or similarity.

        Distinct from ``search_species``, which matches identity handles.
        This one runs an RDKit query against the stored molecules, so
        ``query_smarts='[CX4H3]'`` finds every methyl-bearing species
        rather than requiring the caller to already know what it wants.
        Defaults to POST because SMARTS is full of characters that
        round-trip badly through a query string.
        """

        body = {
            "query_smiles": query_smiles,
            "query_smarts": query_smarts,
            "query_inchi": query_inchi,
            "query_inchi_key": query_inchi_key,
            "mode": mode,
            "similarity_threshold": similarity_threshold,
            "min_review_status": min_review_status,
            "include_rejected": include_rejected,
            "include_deprecated": include_deprecated,
            "sort": sort,
            "include": include,
            "offset": offset,
            "limit": limit,
        }
        return self._request_scientific_search(
            "/scientific/species/structure-search",
            body,
            method_http=method_http,
            profile=profile,
        )

    def search_conformers(
        self,
        *,
        species_ref: str | None = None,
        species_entry_ref: str | None = None,
        conformer_group_ref: str | None = None,
        conformer_observation_ref: str | None = None,
        selection_kind: str | None = None,
        has_selection: bool | None = None,
        assignment_scheme_ref: str | None = None,
        has_observations: bool | None = None,
        has_calculations: bool | None = None,
        has_geometries: bool | None = None,
        has_opt: bool | None = None,
        has_freq: bool | None = None,
        has_sp: bool | None = None,
        has_geometry_validation: bool | None = None,
        has_scf_stability: bool | None = None,
        scientific_origin: str | None = None,
        method: str | None = None,
        basis: str | None = None,
        software: str | None = None,
        software_version: str | None = None,
        workflow_tool: str | None = None,
        workflow_tool_version: str | None = None,
        min_review_status: str | None = None,
        include_rejected: bool | None = None,
        include_deprecated: bool | None = None,
        sort: str | None = None,
        include: list[str] | None = None,
        offset: int | None = None,
        limit: int | None = None,
        profile: str | None = None,
        method_http: _ScientificSearchMethod = "POST",
    ) -> ConformerSearchResponse:
        """Search conformer groups by species, evidence, or selection state."""

        body = {
            "species_ref": species_ref,
            "species_entry_ref": species_entry_ref,
            "conformer_group_ref": conformer_group_ref,
            "conformer_observation_ref": conformer_observation_ref,
            "selection_kind": selection_kind,
            "has_selection": has_selection,
            "assignment_scheme_ref": assignment_scheme_ref,
            "has_observations": has_observations,
            "has_calculations": has_calculations,
            "has_geometries": has_geometries,
            "has_opt": has_opt,
            "has_freq": has_freq,
            "has_sp": has_sp,
            "has_geometry_validation": has_geometry_validation,
            "has_scf_stability": has_scf_stability,
            "scientific_origin": scientific_origin,
            "method": method,
            "basis": basis,
            "software": software,
            "software_version": software_version,
            "workflow_tool": workflow_tool,
            "workflow_tool_version": workflow_tool_version,
            "min_review_status": min_review_status,
            "include_rejected": include_rejected,
            "include_deprecated": include_deprecated,
            "sort": sort,
            "include": include,
            "offset": offset,
            "limit": limit,
        }
        return self._request_scientific_search(
            "/scientific/conformers/search",
            body,
            method_http=method_http,
            profile=profile,
        )

    def search_transition_states(
        self,
        *,
        reaction_ref: str | None = None,
        reaction_entry_ref: str | None = None,
        transition_state_ref: str | None = None,
        transition_state_entry_ref: str | None = None,
        status: str | None = None,
        charge: int | None = None,
        multiplicity: int | None = None,
        has_calculations: bool | None = None,
        has_opt: bool | None = None,
        has_freq: bool | None = None,
        has_sp: bool | None = None,
        has_irc: bool | None = None,
        has_path_search: bool | None = None,
        has_geometry_validation: bool | None = None,
        has_scf_stability: bool | None = None,
        method: str | None = None,
        basis: str | None = None,
        software: str | None = None,
        software_version: str | None = None,
        workflow_tool: str | None = None,
        workflow_tool_version: str | None = None,
        min_review_status: str | None = None,
        include_rejected: bool | None = None,
        include_deprecated: bool | None = None,
        sort: str | None = None,
        include: list[str] | None = None,
        offset: int | None = None,
        limit: int | None = None,
        profile: str | None = None,
        method_http: _ScientificSearchMethod = "POST",
    ) -> TransitionStateSearchResponse:
        """Search transition-state *entries* by reaction and evidence.

        Entry-grained rather than identity-grained: calculations, IRC
        confirmation, and validation all attach to an entry, so a search
        over identities could not answer "which TS actually has an IRC".
        """

        body = {
            "reaction_ref": reaction_ref,
            "reaction_entry_ref": reaction_entry_ref,
            "transition_state_ref": transition_state_ref,
            "transition_state_entry_ref": transition_state_entry_ref,
            "status": status,
            "charge": charge,
            "multiplicity": multiplicity,
            "has_calculations": has_calculations,
            "has_opt": has_opt,
            "has_freq": has_freq,
            "has_sp": has_sp,
            "has_irc": has_irc,
            "has_path_search": has_path_search,
            "has_geometry_validation": has_geometry_validation,
            "has_scf_stability": has_scf_stability,
            "method": method,
            "basis": basis,
            "software": software,
            "software_version": software_version,
            "workflow_tool": workflow_tool,
            "workflow_tool_version": workflow_tool_version,
            "min_review_status": min_review_status,
            "include_rejected": include_rejected,
            "include_deprecated": include_deprecated,
            "sort": sort,
            "include": include,
            "offset": offset,
            "limit": limit,
        }
        return self._request_scientific_search(
            "/scientific/transition-states/search",
            body,
            method_http=method_http,
            profile=profile,
        )

    def search_energy_correction_schemes(
        self,
        *,
        energy_correction_scheme_ref: str | None = None,
        name: str | None = None,
        version: str | None = None,
        scheme_kind: str | None = None,
        method: str | None = None,
        basis: str | None = None,
        software: str | None = None,
        software_version: str | None = None,
        literature_ref: str | None = None,
        has_corrections: bool | None = None,
        used_by_thermo: bool | None = None,
        used_by_calculation: bool | None = None,
        min_review_status: str | None = None,
        include_rejected: bool | None = None,
        include_deprecated: bool | None = None,
        sort: str | None = None,
        include: list[str] | None = None,
        offset: int | None = None,
        limit: int | None = None,
        profile: str | None = None,
        method_http: _ScientificSearchMethod = "POST",
    ) -> EnergyCorrectionSchemeSearchResponse:
        """Search the reference library of energy-correction schemes."""

        body = {
            "energy_correction_scheme_ref": energy_correction_scheme_ref,
            "name": name,
            "version": version,
            "scheme_kind": scheme_kind,
            "method": method,
            "basis": basis,
            "software": software,
            "software_version": software_version,
            "literature_ref": literature_ref,
            "has_corrections": has_corrections,
            "used_by_thermo": used_by_thermo,
            "used_by_calculation": used_by_calculation,
            "min_review_status": min_review_status,
            "include_rejected": include_rejected,
            "include_deprecated": include_deprecated,
            "sort": sort,
            "include": include,
            "offset": offset,
            "limit": limit,
        }
        return self._request_scientific_search(
            "/scientific/energy-correction-schemes/search",
            body,
            method_http=method_http,
            profile=profile,
        )

    def search_frequency_scale_factors(
        self,
        *,
        frequency_scale_factor_ref: str | None = None,
        value: float | None = None,
        value_min: float | None = None,
        value_max: float | None = None,
        scale_kind: str | None = None,
        model_kind: str | None = None,
        method: str | None = None,
        basis: str | None = None,
        software: str | None = None,
        software_version: str | None = None,
        literature_ref: str | None = None,
        used_by_statmech: bool | None = None,
        min_review_status: str | None = None,
        include_rejected: bool | None = None,
        include_deprecated: bool | None = None,
        sort: str | None = None,
        include: list[str] | None = None,
        offset: int | None = None,
        limit: int | None = None,
        profile: str | None = None,
        method_http: _ScientificSearchMethod = "POST",
    ) -> FrequencyScaleFactorSearchResponse:
        """Search the reference library of frequency scale factors."""

        body = {
            "frequency_scale_factor_ref": frequency_scale_factor_ref,
            "value": value,
            "value_min": value_min,
            "value_max": value_max,
            "scale_kind": scale_kind,
            "model_kind": model_kind,
            "method": method,
            "basis": basis,
            "software": software,
            "software_version": software_version,
            "literature_ref": literature_ref,
            "used_by_statmech": used_by_statmech,
            "min_review_status": min_review_status,
            "include_rejected": include_rejected,
            "include_deprecated": include_deprecated,
            "sort": sort,
            "include": include,
            "offset": offset,
            "limit": limit,
        }
        return self._request_scientific_search(
            "/scientific/frequency-scale-factors/search",
            body,
            method_http=method_http,
            profile=profile,
        )

    # ------------------------------------------------------------------
    # Analytics reads (/api/v1/scientific/analytics/*)
    # ------------------------------------------------------------------
    #
    # Flat, numeric, one row per record — the surface for building a
    # dataset rather than answering a chemistry question. GET only: every
    # filter is a scalar and a cursor is a short token, so the POST twin
    # the chemistry-first searches carry would add surface without
    # answering a question.
    #
    # Each takes ``cursor`` and returns ``next_cursor``. Prefer the
    # ``iter_*`` companions, which follow the cursor: offset paging over a
    # live corpus can skip or duplicate rows without saying so.

    def search_kinetics_analytics(
        self,
        *,
        scientific_origin: str | None = None,
        direction: str | None = None,
        model_kind: str | None = None,
        tunneling_model: str | None = None,
        pressure_context: str | None = None,
        degeneracy_min: float | None = None,
        degeneracy_max: float | None = None,
        pressure_min_bar: float | None = None,
        pressure_max_bar: float | None = None,
        a_min: float | None = None,
        a_max: float | None = None,
        n_min: float | None = None,
        n_max: float | None = None,
        ea_min_kj_mol: float | None = None,
        ea_max_kj_mol: float | None = None,
        has_uncertainty: bool | None = None,
        ea_uncertainty_min_kj_mol: float | None = None,
        ea_uncertainty_max_kj_mol: float | None = None,
        temperature_min_k: float | None = None,
        temperature_max_k: float | None = None,
        has_literature: bool | None = None,
        workflow_tool: str | None = None,
        has_transition_state_provenance: bool | None = None,
        has_statmech_provenance: bool | None = None,
        min_review_status: str | None = None,
        include_rejected: bool | None = None,
        include_deprecated: bool | None = None,
        sort: str | None = None,
        include: list[str] | None = None,
        offset: int | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        profile: str | None = None,
    ) -> KineticsAnalyticsResponse:
        """``GET /scientific/analytics/kinetics`` — flat kinetics rows.

        ``temperature_min_k`` / ``temperature_max_k`` are *coverage*
        filters: they select records whose own stated range spans the
        window, and records with no stated range never match.
        """

        params = {
            "scientific_origin": scientific_origin,
            "direction": direction,
            "model_kind": model_kind,
            "tunneling_model": tunneling_model,
            "pressure_context": pressure_context,
            "degeneracy_min": degeneracy_min,
            "degeneracy_max": degeneracy_max,
            "pressure_min_bar": pressure_min_bar,
            "pressure_max_bar": pressure_max_bar,
            "a_min": a_min,
            "a_max": a_max,
            "n_min": n_min,
            "n_max": n_max,
            "ea_min_kj_mol": ea_min_kj_mol,
            "ea_max_kj_mol": ea_max_kj_mol,
            "has_uncertainty": has_uncertainty,
            "ea_uncertainty_min_kj_mol": ea_uncertainty_min_kj_mol,
            "ea_uncertainty_max_kj_mol": ea_uncertainty_max_kj_mol,
            "temperature_min_k": temperature_min_k,
            "temperature_max_k": temperature_max_k,
            "has_literature": has_literature,
            "workflow_tool": workflow_tool,
            "has_transition_state_provenance": has_transition_state_provenance,
            "has_statmech_provenance": has_statmech_provenance,
            "min_review_status": min_review_status,
            "include_rejected": include_rejected,
            "include_deprecated": include_deprecated,
            "sort": sort,
            "include": include,
            "offset": offset,
            "limit": limit,
            "cursor": cursor,
            "profile": profile,
        }
        return self.request_json(
            "GET",
            "/scientific/analytics/kinetics",
            params=params,
            authenticated=False,
        ).data

    def search_thermo_analytics(
        self,
        *,
        scientific_origin: str | None = None,
        phase: str | None = None,
        model_kind: str | None = None,
        reference_pressure_min_bar: float | None = None,
        reference_pressure_max_bar: float | None = None,
        h298_min_kj_mol: float | None = None,
        h298_max_kj_mol: float | None = None,
        s298_min_j_mol_k: float | None = None,
        s298_max_j_mol_k: float | None = None,
        enthalpy_formation_0k_min_kj_mol: float | None = None,
        enthalpy_formation_0k_max_kj_mol: float | None = None,
        has_uncertainty: bool | None = None,
        h298_uncertainty_min_kj_mol: float | None = None,
        h298_uncertainty_max_kj_mol: float | None = None,
        has_literature: bool | None = None,
        workflow_tool: str | None = None,
        has_statmech_provenance: bool | None = None,
        min_review_status: str | None = None,
        include_rejected: bool | None = None,
        include_deprecated: bool | None = None,
        sort: str | None = None,
        include: list[str] | None = None,
        offset: int | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        profile: str | None = None,
    ) -> ThermoAnalyticsResponse:
        """``GET /scientific/analytics/thermo`` — flat thermo rows."""

        params = {
            "scientific_origin": scientific_origin,
            "phase": phase,
            "model_kind": model_kind,
            "reference_pressure_min_bar": reference_pressure_min_bar,
            "reference_pressure_max_bar": reference_pressure_max_bar,
            "h298_min_kj_mol": h298_min_kj_mol,
            "h298_max_kj_mol": h298_max_kj_mol,
            "s298_min_j_mol_k": s298_min_j_mol_k,
            "s298_max_j_mol_k": s298_max_j_mol_k,
            "enthalpy_formation_0k_min_kj_mol": enthalpy_formation_0k_min_kj_mol,
            "enthalpy_formation_0k_max_kj_mol": enthalpy_formation_0k_max_kj_mol,
            "has_uncertainty": has_uncertainty,
            "h298_uncertainty_min_kj_mol": h298_uncertainty_min_kj_mol,
            "h298_uncertainty_max_kj_mol": h298_uncertainty_max_kj_mol,
            "has_literature": has_literature,
            "workflow_tool": workflow_tool,
            "has_statmech_provenance": has_statmech_provenance,
            "min_review_status": min_review_status,
            "include_rejected": include_rejected,
            "include_deprecated": include_deprecated,
            "sort": sort,
            "include": include,
            "offset": offset,
            "limit": limit,
            "cursor": cursor,
            "profile": profile,
        }
        return self.request_json(
            "GET",
            "/scientific/analytics/thermo",
            params=params,
            authenticated=False,
        ).data

    def search_statmech_analytics(
        self,
        *,
        scientific_origin: str | None = None,
        external_symmetry: int | None = None,
        is_linear: bool | None = None,
        point_group: str | None = None,
        statmech_treatment: str | None = None,
        rigid_rotor_kind: str | None = None,
        optical_isomers: int | None = None,
        rotational_constant_a_min_cm1: float | None = None,
        rotational_constant_a_max_cm1: float | None = None,
        rotational_constant_b_min_cm1: float | None = None,
        rotational_constant_b_max_cm1: float | None = None,
        rotational_constant_c_min_cm1: float | None = None,
        rotational_constant_c_max_cm1: float | None = None,
        has_frequency_scale_factor: bool | None = None,
        has_torsions: bool | None = None,
        has_electronic_levels: bool | None = None,
        electronic_level_count_min: int | None = None,
        electronic_level_count_max: int | None = None,
        min_review_status: str | None = None,
        include_rejected: bool | None = None,
        include_deprecated: bool | None = None,
        sort: str | None = None,
        include: list[str] | None = None,
        offset: int | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        profile: str | None = None,
    ) -> StatmechAnalyticsResponse:
        """``GET /scientific/analytics/statmech`` — flat statmech rows.

        ``external_symmetry`` and ``optical_isomers`` are exact matches:
        they are small integers where a range would mostly express a
        question nobody asks.
        """

        params = {
            "scientific_origin": scientific_origin,
            "external_symmetry": external_symmetry,
            "is_linear": is_linear,
            "point_group": point_group,
            "statmech_treatment": statmech_treatment,
            "rigid_rotor_kind": rigid_rotor_kind,
            "optical_isomers": optical_isomers,
            "rotational_constant_a_min_cm1": rotational_constant_a_min_cm1,
            "rotational_constant_a_max_cm1": rotational_constant_a_max_cm1,
            "rotational_constant_b_min_cm1": rotational_constant_b_min_cm1,
            "rotational_constant_b_max_cm1": rotational_constant_b_max_cm1,
            "rotational_constant_c_min_cm1": rotational_constant_c_min_cm1,
            "rotational_constant_c_max_cm1": rotational_constant_c_max_cm1,
            "has_frequency_scale_factor": has_frequency_scale_factor,
            "has_torsions": has_torsions,
            "has_electronic_levels": has_electronic_levels,
            "electronic_level_count_min": electronic_level_count_min,
            "electronic_level_count_max": electronic_level_count_max,
            "min_review_status": min_review_status,
            "include_rejected": include_rejected,
            "include_deprecated": include_deprecated,
            "sort": sort,
            "include": include,
            "offset": offset,
            "limit": limit,
            "cursor": cursor,
            "profile": profile,
        }
        return self.request_json(
            "GET",
            "/scientific/analytics/statmech",
            params=params,
            authenticated=False,
        ).data

    def search_calculation_analytics(
        self,
        *,
        calculation_type: str | None = None,
        electronic_energy_min_hartree: float | None = None,
        electronic_energy_max_hartree: float | None = None,
        zpe_min_hartree: float | None = None,
        zpe_max_hartree: float | None = None,
        n_imag: int | None = None,
        converged: bool | None = None,
        t1_min: float | None = None,
        t1_max: float | None = None,
        d1_min: float | None = None,
        d1_max: float | None = None,
        s_squared_min: float | None = None,
        s_squared_max: float | None = None,
        method: str | None = None,
        basis: str | None = None,
        lot_ref: str | None = None,
        software: str | None = None,
        min_review_status: str | None = None,
        include_rejected: bool | None = None,
        include_deprecated: bool | None = None,
        sort: str | None = None,
        include: list[str] | None = None,
        offset: int | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        profile: str | None = None,
    ) -> CalculationAnalyticsResponse:
        """``GET /scientific/analytics/calculations`` — flat calculation rows.

        The diagnostics filters (``t1_max``, ``d1_max``, ``s_squared_max``)
        are the reason this endpoint exists: selecting a training set on
        multireference character or spin contamination is not expressible
        through the chemistry-first searches.
        """

        params = {
            "calculation_type": calculation_type,
            "electronic_energy_min_hartree": electronic_energy_min_hartree,
            "electronic_energy_max_hartree": electronic_energy_max_hartree,
            "zpe_min_hartree": zpe_min_hartree,
            "zpe_max_hartree": zpe_max_hartree,
            "n_imag": n_imag,
            "converged": converged,
            "t1_min": t1_min,
            "t1_max": t1_max,
            "d1_min": d1_min,
            "d1_max": d1_max,
            "s_squared_min": s_squared_min,
            "s_squared_max": s_squared_max,
            "method": method,
            "basis": basis,
            "lot_ref": lot_ref,
            "software": software,
            "min_review_status": min_review_status,
            "include_rejected": include_rejected,
            "include_deprecated": include_deprecated,
            "sort": sort,
            "include": include,
            "offset": offset,
            "limit": limit,
            "cursor": cursor,
            "profile": profile,
        }
        return self.request_json(
            "GET",
            "/scientific/analytics/calculations",
            params=params,
            authenticated=False,
        ).data

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

    def iter_network_solves(
        self, **parameters: Any
    ) -> Iterator[NetworkSolveRecord]:
        """Lazily yield network-solve records matching the supplied filters."""

        return iter_paginated_records(self.search_network_solves, parameters)

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

    def iter_species_structures(
        self, **parameters: Any
    ) -> Iterator[SpeciesStructureRecord]:
        """Lazily yield every structure-search hit matching the filters."""

        return iter_paginated_records(self.search_species_structures, parameters)

    def iter_conformers(self, **parameters: Any) -> Iterator[ConformerRecord]:
        """Lazily yield conformer groups matching the supplied filters."""

        return iter_paginated_records(self.search_conformers, parameters)

    def iter_transition_states(
        self, **parameters: Any
    ) -> Iterator[TransitionStateEntryRecord]:
        """Lazily yield transition-state entries matching the filters."""

        return iter_paginated_records(self.search_transition_states, parameters)

    def iter_energy_correction_schemes(
        self, **parameters: Any
    ) -> Iterator[EnergyCorrectionSchemeRecord]:
        """Lazily yield energy-correction schemes matching the filters."""

        return iter_paginated_records(
            self.search_energy_correction_schemes, parameters
        )

    def iter_frequency_scale_factors(
        self, **parameters: Any
    ) -> Iterator[FrequencyScaleFactorRecord]:
        """Lazily yield frequency scale factors matching the filters."""

        return iter_paginated_records(
            self.search_frequency_scale_factors, parameters
        )

    def iter_literature_records(
        self, literature_ref_or_id: int | str, **parameters: Any
    ) -> Iterator[LiteratureLinkedRecord]:
        """Lazily yield every record linked to one literature reference.

        The reference is a path segment, not a filter, so it is bound here
        and stays fixed across pages.
        """

        def fetch_page(**page_parameters: Any) -> Any:
            return self.get_literature_records(
                literature_ref_or_id, **page_parameters
            )

        return iter_paginated_records(fetch_page, parameters)

    # Keyset iterators. These follow ``next_cursor`` rather than counting
    # offsets, so a dataset build over a corpus that is still being written
    # to neither skips nor duplicates rows.

    def iter_kinetics_analytics(
        self, **parameters: Any
    ) -> Iterator[KineticsAnalyticsRecord]:
        """Lazily yield kinetics analytics rows, traversing by cursor."""

        return iter_keyset_records(self.search_kinetics_analytics, parameters)

    def iter_thermo_analytics(
        self, **parameters: Any
    ) -> Iterator[ThermoAnalyticsRecord]:
        """Lazily yield thermo analytics rows, traversing by cursor."""

        return iter_keyset_records(self.search_thermo_analytics, parameters)

    def iter_statmech_analytics(
        self, **parameters: Any
    ) -> Iterator[StatmechAnalyticsRecord]:
        """Lazily yield statmech analytics rows, traversing by cursor."""

        return iter_keyset_records(self.search_statmech_analytics, parameters)

    def iter_calculation_analytics(
        self, **parameters: Any
    ) -> Iterator[CalculationAnalyticsRecord]:
        """Lazily yield calculation analytics rows, traversing by cursor."""

        return iter_keyset_records(self.search_calculation_analytics, parameters)

    def get_reaction_full(
        self,
        reaction_entry_id: int | str,
        *,
        include: list[str] | None = None,
        include_review: str | None = None,
        min_review_status: str | None = None,
        include_deprecated: bool | None = None,
        include_rejected: bool | None = None,
        profile: str | None = None,
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
            "profile": profile,
        }
        return self.request_json(
            "GET", path, params=params, authenticated=False
        ).data

    def get_geometry(
        self,
        geometry_handle: int | str,
        *,
        include: list[str] | None = None,
        profile: str | None = None,
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
        params = {"include": include, "profile": profile}
        return self.request_json(
            "GET", path, params=params, authenticated=False
        ).data

    def get_network_solve(
        self,
        network_solve_ref_or_id: int | str,
        *,
        include: list[str] | None = None,
        profile: str | None = None,
    ) -> Any:
        """``GET /scientific/network-solves/{network_solve_ref_or_id}``.

        ``network_solve_ref_or_id`` accepts either an integer database id or
        a public ``nsolve_…`` ref. Optional repeated ``include`` tokens expose
        bounded detail sections such as bath gas, energy transfer, source
        calculations, kinetics, and review history.
        """

        path = f"/scientific/network-solves/{network_solve_ref_or_id}"
        return self.request_json(
            "GET", path, params={"include": include, "profile": profile},
            authenticated=False,
        ).data

    def _get_scientific_detail(
        self,
        path: str,
        *,
        include: list[str] | None,
        profile: str | None,
    ) -> Any:
        """Fetch one ``{request, review_summary, record}`` detail envelope."""

        return self.request_json(
            "GET",
            path,
            params={"include": include, "profile": profile},
            authenticated=False,
        ).data

    def get_conformer_group(
        self,
        conformer_group_ref_or_id: int | str,
        *,
        include: list[str] | None = None,
        profile: str | None = None,
    ) -> ConformerGroupDetailResponse:
        """``GET /scientific/conformer-groups/{ref_or_id}``.

        ``include`` opens bounded sections — ``observations``,
        ``selections``, ``calculations``, ``geometries``,
        ``review_history`` — which are omitted by default because a group
        can carry hundreds of observations.
        """

        return self._get_scientific_detail(
            f"/scientific/conformer-groups/{conformer_group_ref_or_id}",
            include=include,
            profile=profile,
        )

    def get_conformer_observation(
        self,
        conformer_observation_ref_or_id: int | str,
        *,
        include: list[str] | None = None,
        profile: str | None = None,
    ) -> ConformerObservationDetailResponse:
        """``GET /scientific/conformer-observations/{ref_or_id}``."""

        return self._get_scientific_detail(
            "/scientific/conformer-observations/"
            f"{conformer_observation_ref_or_id}",
            include=include,
            profile=profile,
        )

    def get_transition_state(
        self,
        transition_state_ref_or_id: int | str,
        *,
        include: list[str] | None = None,
        profile: str | None = None,
    ) -> TransitionStateDetailResponse:
        """``GET /scientific/transition-states/{ref_or_id}`` — one TS identity."""

        return self._get_scientific_detail(
            f"/scientific/transition-states/{transition_state_ref_or_id}",
            include=include,
            profile=profile,
        )

    def get_transition_state_entry(
        self,
        transition_state_entry_ref_or_id: int | str,
        *,
        include: list[str] | None = None,
        profile: str | None = None,
    ) -> TransitionStateEntryDetailResponse:
        """``GET /scientific/transition-state-entries/{ref_or_id}``."""

        return self._get_scientific_detail(
            "/scientific/transition-state-entries/"
            f"{transition_state_entry_ref_or_id}",
            include=include,
            profile=profile,
        )

    def get_energy_correction_scheme(
        self,
        energy_correction_scheme_ref_or_id: int | str,
        *,
        include: list[str] | None = None,
        profile: str | None = None,
    ) -> EnergyCorrectionSchemeDetailResponse:
        """``GET /scientific/energy-correction-schemes/{ref_or_id}``."""

        return self._get_scientific_detail(
            "/scientific/energy-correction-schemes/"
            f"{energy_correction_scheme_ref_or_id}",
            include=include,
            profile=profile,
        )

    def get_frequency_scale_factor(
        self,
        frequency_scale_factor_ref_or_id: int | str,
        *,
        include: list[str] | None = None,
        profile: str | None = None,
    ) -> FrequencyScaleFactorDetailResponse:
        """``GET /scientific/frequency-scale-factors/{ref_or_id}``."""

        return self._get_scientific_detail(
            "/scientific/frequency-scale-factors/"
            f"{frequency_scale_factor_ref_or_id}",
            include=include,
            profile=profile,
        )

    def get_literature(
        self,
        literature_ref_or_id: int | str,
        *,
        include: list[str] | None = None,
        profile: str | None = None,
    ) -> LiteratureDetailResponse:
        """``GET /scientific/literature/{ref_or_id}`` — one reference."""

        return self._get_scientific_detail(
            f"/scientific/literature/{literature_ref_or_id}",
            include=include,
            profile=profile,
        )

    def get_literature_records(
        self,
        literature_ref_or_id: int | str,
        *,
        record_type: str | None = None,
        include_rejected: bool | None = None,
        include_deprecated: bool | None = None,
        sort: str | None = None,
        include: list[str] | None = None,
        offset: int | None = None,
        limit: int | None = None,
        profile: str | None = None,
    ) -> LiteratureRecordsResponse:
        """``GET /scientific/literature/{ref_or_id}/records``.

        Every scientific record that cites, or was derived from, this
        reference — the "what came out of this paper" read.
        """

        params = {
            "record_type": record_type,
            "include_rejected": include_rejected,
            "include_deprecated": include_deprecated,
            "sort": sort,
            "include": include,
            "offset": offset,
            "limit": limit,
            "profile": profile,
        }
        return self.request_json(
            "GET",
            f"/scientific/literature/{literature_ref_or_id}/records",
            params=params,
            authenticated=False,
        ).data

    def _get_calculation_points(
        self,
        calculation_ref_or_id: int | str,
        suffix: str,
        *,
        include_geometries: bool | None,
        include: list[str] | None,
        sort: str | None,
        offset: int | None,
        limit: int | None,
        profile: str | None,
    ) -> Any:
        """Fetch one point series hanging off a calculation."""

        params = {
            "include_geometries": include_geometries,
            "include": include,
            "sort": sort,
            "offset": offset,
            "limit": limit,
            "profile": profile,
        }
        return self.request_json(
            "GET",
            f"/scientific/calculations/{calculation_ref_or_id}/{suffix}",
            params=params,
            authenticated=False,
        ).data

    def get_calculation_irc(
        self,
        calculation_ref_or_id: int | str,
        *,
        include_geometries: bool | None = None,
        include: list[str] | None = None,
        sort: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
        profile: str | None = None,
    ) -> Any:
        """``GET /scientific/calculations/{ref_or_id}/irc`` — the IRC path.

        ``include_geometries=True`` attaches coordinates to every point,
        which is what makes the response large; it is off by default.
        """

        return self._get_calculation_points(
            calculation_ref_or_id,
            "irc",
            include_geometries=include_geometries,
            include=include,
            sort=sort,
            offset=offset,
            limit=limit,
            profile=profile,
        )

    def get_calculation_scan(
        self,
        calculation_ref_or_id: int | str,
        *,
        include_geometries: bool | None = None,
        include: list[str] | None = None,
        sort: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
        profile: str | None = None,
    ) -> Any:
        """``GET /scientific/calculations/{ref_or_id}/scan`` — scan points."""

        return self._get_calculation_points(
            calculation_ref_or_id,
            "scan",
            include_geometries=include_geometries,
            include=include,
            sort=sort,
            offset=offset,
            limit=limit,
            profile=profile,
        )

    def get_calculation_path_search(
        self,
        calculation_ref_or_id: int | str,
        *,
        include_geometries: bool | None = None,
        include: list[str] | None = None,
        sort: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
        profile: str | None = None,
    ) -> Any:
        """``GET /scientific/calculations/{ref_or_id}/path-search``."""

        return self._get_calculation_points(
            calculation_ref_or_id,
            "path-search",
            include_geometries=include_geometries,
            include=include,
            sort=sort,
            offset=offset,
            limit=limit,
            profile=profile,
        )

    def download_artifact(
        self, sha256: str, *, profile: str | None = None
    ) -> bytes:
        """``GET /scientific/artifacts/{sha256}/download`` — the raw file.

        Returns the bytes untouched. Artifacts are content-addressed, so
        the caller can verify what it received against the digest it
        asked for. A file whose owning record is not approved answers
        403 ``artifact_not_approved``.
        """

        digest = sha256.strip() if isinstance(sha256, str) else ""
        if not digest:
            raise ValueError("sha256 must be a non-empty content digest.")
        return self._request_raw(
            "GET",
            f"/scientific/artifacts/{digest}/download",
            params={"profile": profile},
        )

    # ------------------------------------------------------------------
    # Vocabularies
    # ------------------------------------------------------------------
    #
    # What this deployment actually holds, not what the schema permits.
    # A caller filtering on ``method='wb97xd'`` wants to know whether any
    # record uses that spelling before it gets an empty result set.

    def get_meta_methods(self, *, profile: str | None = None) -> Any:
        """``GET /scientific/meta/methods`` — methods present in the corpus."""

        return self.request_json(
            "GET",
            "/scientific/meta/methods",
            params={"profile": profile},
            authenticated=False,
        ).data

    def get_meta_basis_sets(self, *, profile: str | None = None) -> Any:
        """``GET /scientific/meta/basis-sets``."""

        return self.request_json(
            "GET",
            "/scientific/meta/basis-sets",
            params={"profile": profile},
            authenticated=False,
        ).data

    def get_meta_software(self, *, profile: str | None = None) -> Any:
        """``GET /scientific/meta/software``."""

        return self.request_json(
            "GET",
            "/scientific/meta/software",
            params={"profile": profile},
            authenticated=False,
        ).data

    def get_meta_reaction_families(self, *, profile: str | None = None) -> Any:
        """``GET /scientific/meta/reaction-families``."""

        return self.request_json(
            "GET",
            "/scientific/meta/reaction-families",
            params={"profile": profile},
            authenticated=False,
        ).data

    # ------------------------------------------------------------------
    # Bulk exports
    # ------------------------------------------------------------------

    def _export_ndjson(
        self, path: str, params: Mapping[str, Any]
    ) -> Iterator[JSONDict]:
        """Issue an NDJSON export and parse it one object per line."""

        payload = self._request_raw(
            "GET", path, params=params, accept="application/x-ndjson"
        )
        return _iter_ndjson(payload.decode("utf-8"))

    def export_ndjson(
        self,
        *,
        reaction_ref: list[str] | str | None = None,
        species_ref: list[str] | str | None = None,
        reaction_family: str | None = None,
        all: bool | None = None,
        min_review_status: str | None = None,
        collapse: str | None = None,
        selection_policy: str | None = None,
        profile: str | None = None,
    ) -> Iterator[JSONDict]:
        """``GET /scientific/export/ndjson`` — one JSON object per line.

        Streams the composed scientific document for each seed record.
        Returns an iterator so a large export does not have to be held in
        memory all at once.
        """

        params = {
            "reaction_ref": reaction_ref,
            "species_ref": species_ref,
            "reaction_family": reaction_family,
            "all": all,
            "min_review_status": min_review_status,
            "collapse": collapse,
            "selection_policy": selection_policy,
            "profile": profile,
        }
        return self._export_ndjson("/scientific/export/ndjson", params)

    def export_ml_species(
        self,
        *,
        species_ref: list[str] | str | None = None,
        all: bool | None = None,
        min_review_status: str | None = None,
        lot_ref: str | None = None,
        element: list[str] | str | None = None,
        include_hessian: bool | None = None,
        limit: int | None = None,
        offset: int | None = None,
        profile: str | None = None,
    ) -> Iterator[JSONDict]:
        """``GET /scientific/export/ml/species.ndjson`` — ML-shaped species rows.

        ``include_hessian`` is off by default: a Hessian is O(3N x 3N)
        floats per record and most consumers do not want it.
        """

        params = {
            "species_ref": species_ref,
            "all": all,
            "min_review_status": min_review_status,
            "lot_ref": lot_ref,
            "element": element,
            "include_hessian": include_hessian,
            "limit": limit,
            "offset": offset,
            "profile": profile,
        }
        return self._export_ndjson(
            "/scientific/export/ml/species.ndjson", params
        )

    def export_ml_reactions(
        self,
        *,
        reaction_ref: list[str] | str | None = None,
        reaction_family: str | None = None,
        all: bool | None = None,
        min_review_status: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        profile: str | None = None,
    ) -> Iterator[JSONDict]:
        """``GET /scientific/export/ml/reactions.ndjson``."""

        params = {
            "reaction_ref": reaction_ref,
            "reaction_family": reaction_family,
            "all": all,
            "min_review_status": min_review_status,
            "limit": limit,
            "offset": offset,
            "profile": profile,
        }
        return self._export_ndjson(
            "/scientific/export/ml/reactions.ndjson", params
        )

    def export_chemkin(
        self,
        seed: Mapping[str, Any],
        *,
        min_review_status: str | None = None,
        selection_policy: str | None = None,
        energy_units: str | None = None,
        include_transport: bool | None = None,
        naming_policy: str | None = None,
        profile: str | None = None,
    ) -> bytes:
        """``POST /scientific/export/chemkin`` — a Chemkin mechanism archive.

        ``seed`` selects what goes in the mechanism (explicit refs, a
        family, or everything); the remaining arguments control how it is
        rendered. Returns the archive bytes.
        """

        if not isinstance(seed, Mapping):
            raise TypeError(
                "seed must be a mapping describing what to export, e.g. "
                "{'reaction_refs': [...]} or {'all_reactions': True}."
            )
        body = {
            "seed": dict(seed),
            "min_review_status": min_review_status,
            "selection_policy": selection_policy,
            "energy_units": energy_units,
            "include_transport": include_transport,
            "naming_policy": naming_policy,
        }
        return self._request_raw(
            "POST",
            "/scientific/export/chemkin",
            json={k: v for k, v in body.items() if v is not None},
            params={"profile": profile},
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _carries_idempotency_key(headers: Mapping[str, str]) -> bool:
    """``True`` when the outgoing headers carry a usable idempotency key.

    A blank key is no key: the server cannot collapse a replay onto it, so
    treating it as present would enable exactly the unsafe retry the policy
    exists to prevent.
    """

    target = IDEMPOTENCY_HEADER.lower()
    for name, value in headers.items():
        if name.lower() == target:
            return isinstance(value, str) and bool(value.strip())
    return False


def _iter_ndjson(payload: str) -> Iterator[JSONDict]:
    """Parse one JSON object per line, ignoring blank separator lines."""

    for line in payload.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        yield _json.loads(stripped)


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
