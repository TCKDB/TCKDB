"""Read schemas for /api/v1/scientific/thermo/search.

Chemistry-first thermo lookup: callers supply species identifiers and get
back fully-shaped thermo records with the resolved species/species_entry
identity attached. Reuses the per-record ``ThermoRecord`` shape from
``scientific_thermo`` so workflow tools see the same thermo block as the
entry-id detail endpoint.

See docs/specs/browse_order.md and docs/guides/workflow_tool_scientific_reads.md.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.db.models.common import (
    RecordReviewStatus,
    SpeciesEntryStateKind,
    StationaryPointKind,
)
from app.schemas.reads._field_bounds import (
    MAX_FORMULA_LENGTH as _MAX_FORMULA_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_INCHI_KEY_LENGTH as _MAX_INCHI_KEY_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_INCHI_LENGTH as _MAX_INCHI_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_PUBLIC_REF_LENGTH as _MAX_PUBLIC_REF_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_SMILES_LENGTH as _MAX_SMILES_LENGTH,
)
from app.schemas.reads._field_bounds import (
    MAX_SOFTWARE_NAME_LENGTH as _MAX_SOFTWARE_NAME_LENGTH,
)
from app.schemas.reads.removed_parameters import THERMO as _REMOVED_PARAMETERS
from app.schemas.reads.scientific_assessment import refuse_check_inputs_in_body
from app.schemas.reads.scientific_common import (
    CursorToken,
    OrderEcho,
    Pagination,
    ProfiledRequestEcho,
    RecordReviewBadge,
    ReviewStatusSummary,
    ScientificResponse,
    WatermarkEcho,
)
from app.schemas.reads.scientific_thermo import ThermoModelKindQuery, ThermoRecord

# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class ThermoSearchRequest(BaseModel):
    """Service-layer request for chemistry-first thermo search.

    At least one species identifier (``smiles`` / ``inchi`` / ``inchi_key`` /
    ``formula``) must be supplied; multiple identifiers AND-combine.
    Inconsistent identifiers return an empty result set, not a 422. Every matching record is returned in display
    order; ``collapse`` and ``selection_policy`` are refused (422).
    """

    @model_validator(mode="before")
    @classmethod
    def _refuse_check_inputs(cls, data):
        return refuse_check_inputs_in_body(data)


    @model_validator(mode="before")
    @classmethod
    def _refuse_removed_parameters(cls, data):
        return _REMOVED_PARAMETERS.guard_body(data)

    # Species identity filters
    smiles: str | None = Field(default=None, max_length=_MAX_SMILES_LENGTH)
    inchi: str | None = Field(default=None, max_length=_MAX_INCHI_LENGTH)
    inchi_key: str | None = Field(default=None, max_length=_MAX_INCHI_KEY_LENGTH)
    formula: str | None = Field(default=None, max_length=_MAX_FORMULA_LENGTH)
    charge: int | None = None
    multiplicity: int | None = None
    electronic_state_kind: SpeciesEntryStateKind | None = None
    species_entry_kind: StationaryPointKind | None = None

    # Phase C: optional explicit handles for follow-up lookups.
    species_ref: str | None = Field(default=None, max_length=_MAX_PUBLIC_REF_LENGTH)
    species_entry_ref: str | None = Field(default=None, max_length=_MAX_PUBLIC_REF_LENGTH)

    # The window ``temperature_coverage`` is computed against. Not a filter: records outside it are still returned.
    coverage_temperature_min_k: float | None = None
    coverage_temperature_max_k: float | None = None

    # Thermo filters. ``level_of_theory_*`` and ``software`` match when ANY linked source calculation matches.
    model_kind: ThermoModelKindQuery | None = None
    level_of_theory_id: int | None = None
    level_of_theory_ref: str | None = Field(
        default=None, max_length=_MAX_PUBLIC_REF_LENGTH
    )
    software: str | None = Field(default=None, max_length=_MAX_SOFTWARE_NAME_LENGTH)

    # Trust filters
    min_review_status: RecordReviewStatus | None = None
    include_rejected: bool = False
    include_deprecated: bool = False

    # v0 forbids client-supplied sort.
    sort: str | None = None

    include: list[str] = Field(default_factory=list)
    offset: int = 0
    cursor: CursorToken = None
    limit: int = 50


# ---------------------------------------------------------------------------
# Per-record + envelope
# ---------------------------------------------------------------------------


class ThermoSearchSpeciesContext(BaseModel):
    """Resolved species/species-entry identity context for a thermo record.

    Phase B: ``species_ref`` and ``species_entry_ref`` are the public
    stable handles alongside the integer IDs.

    ``species_entry_label`` is the short discriminator that says *which*
    entry of that species this is -- ``"E"``, ``"Z"``, ``"excited T1"``.
    ``canonical_smiles`` is the species' graph identity and is shared by
    every entry under it, so without the label a search that returns
    records for two entries of one species labels them identically. That
    is not hypothetical: a statmech search for the deployed ``N=N``
    species returns eight records across cis- and trans-diazene,
    molecules with different thermochemistry, and every one of them
    reads ``N=N``. ``None`` for the plain ground-state, all-standard,
    stereo-unlabelled entry -- never ``""``. Derived by
    :func:`app.services.scientific_read.species_identity.species_entry_label`,
    the one definition every surface shares.
    """

    species_id: int
    species_ref: str
    canonical_smiles: str
    inchi_key: str
    charge: int
    multiplicity: int
    species_entry_id: int
    species_entry_ref: str
    species_entry_label: str | None = None
    species_entry_kind: StationaryPointKind
    electronic_state_kind: SpeciesEntryStateKind
    species_entry_review: RecordReviewBadge


class ThermoSearchRecord(BaseModel):
    """One result row: resolved species context + the thermo record itself.

    The ``thermo`` field is the same ``ThermoRecord`` shape returned by the
    entry-id detail endpoint, so workflow tools have a single thermo schema
    to depend on regardless of how they discovered the record.
    """

    species: ThermoSearchSpeciesContext
    thermo: ThermoRecord


class RequestEcho(ProfiledRequestEcho):
    """Echo of the parsed query."""

    filter: dict[str, object]
    sort: OrderEcho
    include: list[str]


class ScientificThermoSearchResponse(ScientificResponse):
    """Response envelope for /api/v1/scientific/thermo/search."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ThermoSearchRecord]
    pagination: Pagination
    #: Where the next page starts: pass it back as ``cursor``. ``null`` on the last page. See ``Pagination``.
    next_cursor: str | None = None
    #: The snapshot a cursor traversal is pinned to (set on every page that can continue a traversal).
    watermark: WatermarkEcho | None = None
