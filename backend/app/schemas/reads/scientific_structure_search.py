"""Read schemas for /api/v1/scientific/species/structure-search.

Public chemical-structure search over species entries, backed by the
PostgreSQL RDKit cartridge. Three search modes are supported:

- ``substructure``: SMARTS or SMILES query, returns species entries whose
  stored molecule contains the query as a substructure
  (``stored_mol @> query_mol``)
- ``similarity``: SMILES or InChI query, returns species entries with
  Tanimoto similarity (Morgan fingerprints) above ``similarity_threshold``
- ``exact``: SMILES / InChI / InChIKey query, returns species entries
  whose canonical InChIKey matches the query's InChIKey (computed
  client-side in the service via RDKit)

The endpoint returns species-entry grain records. Each record carries
the parent species' graph identity (SMILES, InChIKey, charge,
multiplicity) plus a per-record ``match`` block describing which mode
matched and (for similarity) the Tanimoto score. Heavy entity payloads
(thermo / kinetics / statmech / transport / conformers) are explicitly
out of v0 scope to keep the search surface focused on discovery; the
returned ``species_entry_ref`` is the bridge to the existing per-entry
detail endpoints when callers want to drill in.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from app.db.models.common import (
    RecordReviewStatus,
    SpeciesEntryStateKind,
    StationaryPointKind,
)
from app.schemas.reads.scientific_common import (
    Pagination,
    RecordReviewBadge,
    ReviewStatusSummary,
)


# ---------------------------------------------------------------------------
# Search-mode and query-kind enums
# ---------------------------------------------------------------------------


class StructureSearchMode(str, Enum):
    """Search algorithm used for structure matching.

    ``substructure`` uses the RDKit cartridge ``@>`` operator against the
    query molecule (SMARTS or SMILES). ``similarity`` uses
    ``tanimoto_sml`` over Morgan-bit fingerprints (SMILES / InChI). ``exact``
    matches on canonical InChIKey (computed for the query via RDKit
    when SMILES or InChI is supplied; passed verbatim when the caller
    supplies an InChIKey directly).
    """

    substructure = "substructure"
    similarity = "similarity"
    exact = "exact"


class StructureQueryKind(str, Enum):
    """Identifies which query field the match came from.

    Echoed in :class:`StructureMatchSummary` so callers can attribute
    the result back to the query input without re-parsing the request.
    """

    smiles = "smiles"
    smarts = "smarts"
    inchi = "inchi"
    inchi_key = "inchi_key"


# Pagination + default thresholds. Defaults mirror the project's other
# scientific search surfaces; the similarity default of 0.5 follows the
# RDKit-cartridge documentation example and is lenient enough to surface
# useful hits in tests without being overwhelming.
DEFAULT_SIMILARITY_THRESHOLD = 0.5


# ---------------------------------------------------------------------------
# Per-record fragments
# ---------------------------------------------------------------------------


class StructureMatchSummary(BaseModel):
    """Per-record description of how the query matched this entry.

    ``similarity_score`` is populated only when ``mode == similarity``.
    ``matched_query`` echoes the caller-supplied query string verbatim
    so the record is self-describing; ``matched_query_kind`` is the
    request field the query came from.
    """

    mode: StructureSearchMode
    similarity_score: float | None = None
    matched_query: str
    matched_query_kind: StructureQueryKind


class ScientificSpeciesStructureSearchRecord(BaseModel):
    """One species-entry-grain structure-search hit.

    Carries the parent species' graph identity (``smiles``, ``inchi_key``,
    ``charge``, ``multiplicity``) and the entry's identity fields. No
    heavy scientific payloads (thermo / kinetics / statmech / transport
    / conformer summaries) are inlined; callers chain to existing
    per-entry detail endpoints via ``species_entry_ref``.

    The ``endpoint`` field gives the canonical detail URL fragment for
    this entry, so a UI consumer can build a deep link without knowing
    the routing convention.
    """

    species_ref: str
    species_id: int | None = None
    species_entry_ref: str
    species_entry_id: int | None = None

    smiles: str
    inchi_key: str
    charge: int
    multiplicity: int
    species_entry_kind: StationaryPointKind
    electronic_state_kind: SpeciesEntryStateKind

    match: StructureMatchSummary
    review: RecordReviewBadge
    endpoint: str


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class ScientificSpeciesStructureSearchRequest(BaseModel):
    """Service-layer request for /scientific/species/structure-search.

    Exactly one of ``query_smiles`` / ``query_smarts`` / ``query_inchi``
    / ``query_inchi_key`` must be supplied. Combinations of query field
    and ``mode`` are validated in the service: e.g. ``mode=substructure``
    rejects ``query_inchi_key``; ``mode=similarity`` rejects
    ``query_smarts`` and ``query_inchi_key``; ``mode=exact`` rejects
    ``query_smarts``.
    """

    query_smiles: str | None = Field(default=None, max_length=4096)
    query_smarts: str | None = Field(default=None, max_length=4096)
    query_inchi: str | None = Field(default=None, max_length=4096)
    query_inchi_key: str | None = Field(default=None, max_length=27)

    mode: StructureSearchMode = StructureSearchMode.substructure
    similarity_threshold: float | None = Field(
        default=None, ge=0.0, le=1.0
    )

    # Trust / review filters mirror the other scientific search surfaces.
    min_review_status: RecordReviewStatus | None = None
    include_rejected: bool = False
    include_deprecated: bool = False

    # Sort / include / pagination. ``sort`` is rejected with 422 when
    # supplied; the per-mode default sort always applies.
    sort: str | None = None
    include: list[str] = Field(default_factory=list)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class RequestEcho(BaseModel):
    """Echo of the parsed request — surfaced in the response envelope."""

    filter: dict[str, object]
    mode: StructureSearchMode
    sort: str
    include: list[str] = Field(default_factory=list)


class ScientificSpeciesStructureSearchResponse(BaseModel):
    """Response envelope for /scientific/species/structure-search."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    records: list[ScientificSpeciesStructureSearchRecord]
    pagination: Pagination


__all__ = [
    "DEFAULT_SIMILARITY_THRESHOLD",
    "RequestEcho",
    "ScientificSpeciesStructureSearchRecord",
    "ScientificSpeciesStructureSearchRequest",
    "ScientificSpeciesStructureSearchResponse",
    "StructureMatchSummary",
    "StructureQueryKind",
    "StructureSearchMode",
]
