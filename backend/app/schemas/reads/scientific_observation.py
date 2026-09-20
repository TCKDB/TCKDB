"""Read schemas for GET /api/v1/scientific/species-entries/{id}/observations.

Mirrors ``scientific_transport_search.py``: one record shape, one search-style
envelope, reused for the sole (species-entry-scoped) surface this record type
has today. ``molecular_property_observation`` is not a *product* with
candidacy/selection semantics (unlike thermo/statmech/transport) — it is a
raw external observation, so this surface carries no ``collapse`` /
``selection_policy`` knob. See
``docs/research/tckdb-phase-c-implementation-plan.md`` C5.

Identity is resolved by construction: every record returned here was found by
its ``species_entry_id``, so there is no separate "identity status" field —
the route itself is the proof. Unresolved observations (nullable
``species_entry_id``, see the model's module docstring) are structurally
unreachable through this route; they have no owning entry to be listed under.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.db.models.common import (
    MolecularPropertyKind,
    ObservedStateBasis,
    ObservedUncertaintyAssessor,
    ObservedUncertaintyKind,
    ScientificOriginKind,
)
from app.schemas.reads._field_bounds import (
    MAX_PUBLIC_REF_LENGTH as _MAX_PUBLIC_REF_LENGTH,
)
from app.schemas.reads.scientific_common import (
    Pagination,
    ProfiledRequestEcho,
    RecordReviewBadge,
    ReviewStatusSummary,
)


class ObservationUncertainty(BaseModel):
    """Typed uncertainty on ``scalar_value`` (Phase C-E1 contract).

    ``kind`` may be ``None`` even when ``value`` is present: legacy
    CCCBDB-imported rows carry a bare ``scalar_uncertainty`` with no stated
    meaning (see the model's ``ck_mpo_uncertainty_kind_iff_value`` CHECK,
    which only requires the pairing for ``heat_capacity_cp`` rows). A present
    ``kind`` with no ``value`` never occurs.
    """

    value: float | None = None
    kind: ObservedUncertaintyKind | None = None
    coverage_factor: float | None = None
    level_of_confidence_pct: float | None = None
    assessor: ObservedUncertaintyAssessor | None = None


class ObservationExternalSource(BaseModel):
    """Where this observation's bytes came from.

    Two provenance shapes coexist on the underlying table (see
    ``app/db/models/molecular_property_observation.py`` and
    ``app/db/models/external_source.py``): legacy CCCBDB rows carry
    flattened ``external_source_*`` columns with no schema identifier /
    ``mapping_version``; new rows (Phase C-E1 onward) cite an
    ``external_source_record`` custody row that carries those two plus a
    content digest. This fragment is the union of both — whichever fields
    the row's provenance shape actually has are populated, the rest are
    ``null``.

    ``schema_label`` mirrors the ORM column ``external_source_record.
    schema_id`` under a different wire name on purpose: the column holds a
    free-text schema identifier (e.g. ``"thermoml.v1"``), not a database
    primary key, but any field ending in ``_id``/``_ids`` is stripped from
    every public response by the Phase D internal-ID policy
    (``app/services/scientific_read/internal_ids.py::is_internal_id_key``).
    Naming it ``schema_id`` on the wire would silently vanish it from every
    unauthenticated response.
    """

    name: str | None = None
    release: str | None = None
    doi: str | None = None
    record_key: str | None = None
    content_sha256: str | None = None
    schema_label: str | None = None
    parser_version: str | None = None
    mapping_version: str | None = None


class MolecularPropertyObservationRecord(BaseModel):
    """One molecular-property observation, scoped to a species entry."""

    observation_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    property_kind: MolecularPropertyKind
    property_label: str | None = None

    scalar_value: float | None = None
    scalar_unit: str | None = None
    vector: dict | None = None
    tensor: dict | None = None
    uncertainty: ObservationUncertainty | None = None

    temperature_k: float | None = None
    pressure_bar: float | None = None
    wavelength_nm: float | None = None
    state_basis: ObservedStateBasis | None = None
    state_label_raw: str | None = None
    method_note: str | None = None
    scientific_origin: ScientificOriginKind

    literature_ref: str | None = None
    reference_label: str | None = None
    external_source: ObservationExternalSource | None = None

    review: RecordReviewBadge


class RequestEcho(ProfiledRequestEcho):
    """Echo of the parsed request for the observations-by-entry read."""

    filter: dict[str, object]
    sort: str
    include: list[str] = Field(default_factory=list)


class ScientificSpeciesObservationsResponse(BaseModel):
    """Response envelope for GET .../species-entries/{id}/observations."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[MolecularPropertyObservationRecord]
    pagination: Pagination


__all__ = [
    "MolecularPropertyObservationRecord",
    "ObservationExternalSource",
    "ObservationUncertainty",
    "RequestEcho",
    "ScientificSpeciesObservationsResponse",
]
