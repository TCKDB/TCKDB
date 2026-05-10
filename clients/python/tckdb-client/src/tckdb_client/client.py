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
        min_review_status: str | None = None,
        include_deprecated: bool | None = None,
        include_rejected: bool | None = None,
        include: list[str] | None = None,
        collapse: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
    ) -> Any:
        """``GET /scientific/species/search`` — discover species by identity.

        At least one identifier (``smiles``, ``inchi``, ``inchi_key``,
        ``formula``) must be supplied. Returns the parsed
        ``ScientificSpeciesSearchResponse`` JSON envelope.
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
            "min_review_status": min_review_status,
            "include_deprecated": include_deprecated,
            "include_rejected": include_rejected,
            "include": include,
            "collapse": collapse,
            "offset": offset,
            "limit": limit,
        }
        return self.request_json(
            "GET", "/scientific/species/search", params=params
        ).data

    def search_reactions(
        self,
        *,
        reactants: list[str] | None = None,
        products: list[str] | None = None,
        direction: str | None = None,
        family: str | None = None,
        min_review_status: str | None = None,
        include_deprecated: bool | None = None,
        include_rejected: bool | None = None,
        include: list[str] | None = None,
        collapse: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
        method: str = "POST",
    ) -> Any:
        """``GET|POST /scientific/reactions/search`` — discover reaction entries.

        Defaults to ``POST`` because reactant/product SMILES often contain
        characters (``[`` ``]`` ``+``) that round-trip awkwardly through
        query strings. Pass ``method="GET"`` to force the GET form. Returns
        the parsed ``ScientificReactionSearchResponse`` JSON envelope.
        """
        if method.upper() == "GET":
            params = {
                "reactants": reactants,
                "products": products,
                "direction": direction,
                "family": family,
                "min_review_status": min_review_status,
                "include_deprecated": include_deprecated,
                "include_rejected": include_rejected,
                "include": include,
                "collapse": collapse,
                "offset": offset,
                "limit": limit,
            }
            return self.request_json(
                "GET", "/scientific/reactions/search", params=params
            ).data
        body = {
            k: v
            for k, v in {
                "reactants": reactants,
                "products": products,
                "direction": direction,
                "family": family,
                "min_review_status": min_review_status,
                "include_deprecated": include_deprecated,
                "include_rejected": include_rejected,
                "include": include,
                "collapse": collapse,
                "offset": offset,
                "limit": limit,
            }.items()
            if v is not None
        }
        return self.request_json(
            "POST", "/scientific/reactions/search", json=body
        ).data

    def get_reaction_kinetics(
        self,
        reaction_entry_id: int,
        *,
        temperature_min: float | None = None,
        temperature_max: float | None = None,
        pressure: float | None = None,
        model_kind: str | None = None,
        level_of_theory_id: int | None = None,
        software: str | None = None,
        min_review_status: str | None = None,
        include_deprecated: bool | None = None,
        include_rejected: bool | None = None,
        include: list[str] | None = None,
        collapse: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
    ) -> Any:
        """``GET /scientific/reaction-entries/{reaction_entry_id}/kinetics``.

        ``reaction_entry_id`` is strictly ``reaction_entry.id``; supplying a
        ``chem_reaction.id`` returns 404. Returns the parsed
        ``ScientificReactionKineticsResponse`` JSON envelope. Provenance
        keys are always present in each record; TS-chain fields are
        ``null`` for non-TS-backed kinetics.
        """
        path = f"/scientific/reaction-entries/{reaction_entry_id}/kinetics"
        params = {
            "temperature_min": temperature_min,
            "temperature_max": temperature_max,
            "pressure": pressure,
            "model_kind": model_kind,
            "level_of_theory_id": level_of_theory_id,
            "software": software,
            "min_review_status": min_review_status,
            "include_deprecated": include_deprecated,
            "include_rejected": include_rejected,
            "include": include,
            "collapse": collapse,
            "offset": offset,
            "limit": limit,
        }
        return self.request_json("GET", path, params=params).data

    def get_species_thermo(
        self,
        species_entry_id: int,
        *,
        temperature_min: float | None = None,
        temperature_max: float | None = None,
        model_kind: str | None = None,
        level_of_theory_id: int | None = None,
        software: str | None = None,
        min_review_status: str | None = None,
        include_deprecated: bool | None = None,
        include_rejected: bool | None = None,
        include: list[str] | None = None,
        collapse: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
    ) -> Any:
        """``GET /scientific/species-entries/{species_entry_id}/thermo``.

        ``species_entry_id`` is strictly ``species_entry.id``; supplying a
        ``species.id`` returns 404. Returns the parsed
        ``ScientificSpeciesThermoResponse`` JSON envelope.
        """
        path = f"/scientific/species-entries/{species_entry_id}/thermo"
        params = {
            "temperature_min": temperature_min,
            "temperature_max": temperature_max,
            "model_kind": model_kind,
            "level_of_theory_id": level_of_theory_id,
            "software": software,
            "min_review_status": min_review_status,
            "include_deprecated": include_deprecated,
            "include_rejected": include_rejected,
            "include": include,
            "collapse": collapse,
            "offset": offset,
            "limit": limit,
        }
        return self.request_json("GET", path, params=params).data

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
        temperature_min: float | None = None,
        temperature_max: float | None = None,
        model_kind: str | None = None,
        level_of_theory_id: int | None = None,
        software: str | None = None,
        min_review_status: str | None = None,
        include_deprecated: bool | None = None,
        include_rejected: bool | None = None,
        include: list[str] | None = None,
        collapse: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
        method: str = "POST",
    ) -> Any:
        """``GET|POST /scientific/thermo/search`` — chemistry-first thermo search.

        Returns thermo records along with the resolved species/species_entry
        identity context, so callers don't have to chain
        ``search_species`` → ``get_species_thermo`` themselves. Defaults to
        POST (mirrors ``search_reactions``); pass ``method="GET"`` for the
        query-string form.
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
            "temperature_min": temperature_min,
            "temperature_max": temperature_max,
            "model_kind": model_kind,
            "level_of_theory_id": level_of_theory_id,
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
                "GET", "/scientific/thermo/search", params=body
            ).data
        return self.request_json(
            "POST",
            "/scientific/thermo/search",
            json={k: v for k, v in body.items() if v is not None},
        ).data

    def search_kinetics(
        self,
        *,
        reactants: list[str] | None = None,
        products: list[str] | None = None,
        direction: str | None = None,
        family: str | None = None,
        temperature_min: float | None = None,
        temperature_max: float | None = None,
        pressure: float | None = None,
        model_kind: str | None = None,
        level_of_theory_id: int | None = None,
        software: str | None = None,
        min_review_status: str | None = None,
        include_deprecated: bool | None = None,
        include_rejected: bool | None = None,
        include: list[str] | None = None,
        collapse: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
        method: str = "POST",
    ) -> Any:
        """``GET|POST /scientific/kinetics/search`` — chemistry-first kinetics search.

        Returns kinetics records along with the resolved reaction/reaction_entry
        identity context. Defaults to POST because reactant/product SMILES
        encode awkwardly in URL query strings; pass ``method="GET"`` for the
        repeated-query-param form. Non-TS-backed kinetics surface with null
        TS-chain provenance fields, exactly like the entry-id detail endpoint.
        """
        body = {
            "reactants": reactants,
            "products": products,
            "direction": direction,
            "family": family,
            "temperature_min": temperature_min,
            "temperature_max": temperature_max,
            "pressure": pressure,
            "model_kind": model_kind,
            "level_of_theory_id": level_of_theory_id,
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
                "GET", "/scientific/kinetics/search", params=body
            ).data
        return self.request_json(
            "POST",
            "/scientific/kinetics/search",
            json={k: v for k, v in body.items() if v is not None},
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
        calculation_type: str | None = None,
        level_of_theory_id: int | None = None,
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
    ) -> Any:
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
            "calculation_type": calculation_type,
            "level_of_theory_id": level_of_theory_id,
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
            ).data
        return self.request_json(
            "POST",
            "/scientific/species-calculations/search",
            json={k: v for k, v in body.items() if v is not None},
        ).data

    def get_reaction_full(
        self,
        reaction_entry_id: int,
        *,
        include: list[str] | None = None,
        include_review: str | None = None,
        min_review_status: str | None = None,
        include_deprecated: bool | None = None,
        include_rejected: bool | None = None,
    ) -> Any:
        """``GET /scientific/reaction-entries/{reaction_entry_id}/full``.

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
        return self.request_json("GET", path, params=params).data


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
]
