"""Read schemas for /api/v1/scientific/geometries/{geometry_handle}.

Detail endpoint that returns the full coordinate payload behind a
``geometry_ref`` returned by ``species-calculations/search`` or other
geometry-bearing scientific responses. Designed as a follow-up read:
search responses identify which geometry was used; this endpoint
delivers the coordinates and a small provenance summary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.reads.scientific_common import (
    CollapseMode,
    ProfiledRequestEcho,
)


class GeometryReadRequest(BaseModel):
    """Service-layer request model for the geometry detail read.

    The path-parameter ``geometry_handle`` is supplied separately to
    the service function; this model carries only the optional
    ``include=`` set so the service can validate it consistently with
    other scientific reads.
    """

    include: list[str] = Field(default_factory=list)


class RequestEcho(ProfiledRequestEcho):
    """Echo of the parsed query for the geometry detail endpoint.

    The endpoint has no scientific filters, so ``filter`` is always
    ``{}`` and ``sort`` / ``collapse`` are present only for shape
    consistency with the other scientific read envelopes (so callers
    can rely on a stable response top-level).
    """

    filter: dict[str, object] = Field(default_factory=dict)
    sort: str = ""
    collapse: CollapseMode = CollapseMode.all
    include: list[str] = Field(default_factory=list)


class GeometryAtomPayload(BaseModel):
    """One atom row inside a geometry's coordinate payload."""

    atom_index: int
    element: str
    x: float
    y: float
    z: float


class GeometryProvenanceCalcLink(BaseModel):
    """One calculation that consumed or produced this geometry.

    ``role`` is populated only for output links (the
    ``CalculationOutputGeometry.role`` enum) and is always ``None`` for
    input links since ``CalculationInputGeometry`` has no role column.
    """

    calculation_id: int
    calculation_ref: str
    calculation_type: str
    role: str | None = None


class GeometryProvenance(BaseModel):
    """Compact provenance for a geometry detail response.

    ``produced_by`` lists every calculation that emitted this geometry
    as an output (with role); ``used_as_input_by`` lists every
    calculation that consumed it. The v0 endpoint returns the full
    cross-reference set unfiltered — geometries are not usually shared
    across thousands of calculations.
    """

    produced_by: list[GeometryProvenanceCalcLink] = Field(default_factory=list)
    used_as_input_by: list[GeometryProvenanceCalcLink] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Molecular identity
# ---------------------------------------------------------------------------


class GeometrySpeciesIdentity(BaseModel):
    """Identity of a species-owned geometry's owning entry.

    ``formula`` is derived server-side (RDKit cartridge, Hill notation)
    from ``canonical_smiles`` and is ``null`` only if that SMILES fails
    to parse — see ``app.services.scientific_read.species._formula_expr``
    for the same derivation used elsewhere on the read surface.
    ``canonical_smiles`` / ``inchi_key`` / ``charge`` / ``multiplicity``
    belong to the parent ``species`` row (shared by every entry under
    it); ``species_entry_label`` says *which* entry — two stereoisomers
    under one species would otherwise report identical identity blocks.
    """

    species_id: int
    species_ref: str
    species_entry_id: int
    species_entry_ref: str
    species_entry_label: str | None = None
    formula: str | None = None
    canonical_smiles: str
    inchi_key: str
    charge: int
    multiplicity: int


class GeometryTransitionStateIdentity(BaseModel):
    """Identity of a transition-state-owned geometry's owning entry.

    Transition states have no canonical SMILES the way species do:
    ``unmapped_smiles`` is an optional, un-atom-mapped label a depositor
    may have supplied, not a deduped identity key the way
    ``species.smiles`` is. It is served honestly under its own name
    rather than folded into a ``canonical_smiles``-shaped field, which
    would claim a precision this value does not have — so there is no
    ``canonical_smiles`` or ``inchi_key`` on this block, ever.
    ``formula`` is a best-effort RDKit parse of ``unmapped_smiles`` and
    is ``null`` whenever that label is absent or does not parse.
    """

    transition_state_id: int
    transition_state_ref: str
    transition_state_entry_id: int
    transition_state_entry_ref: str
    formula: str | None = None
    unmapped_smiles: str | None = None
    charge: int
    multiplicity: int


class GeometryIdentityOwnerRef(BaseModel):
    """One distinct owner found when a geometry resolves to more than one."""

    kind: Literal["species_entry", "transition_state_entry"]
    ref: str


class GeometryIdentity(BaseModel):
    """Molecular identity of the record that owns a geometry.

    Exactly one of ``species_entry`` / ``transition_state_entry`` is
    non-null in the ordinary case, mirroring ``kind``. ``kind`` is
    ``null`` in exactly one situation: the geometry (deduped by content
    hash, so it can be shared) is reachable from calculations that
    belong to *more than one* distinct owning entry — e.g. two
    isotopologues whose plain-element coordinates happen to be
    identical. Rather than silently pick one, every per-owner field is
    left null and ``ambiguous_owners`` lists every distinct owner found,
    so a reader can tell "ambiguous" apart from "none" and go look at
    ``provenance`` to disambiguate by calculation.

    A geometry with no owning calculation at all (nothing in either
    ``provenance.produced_by`` or ``provenance.used_as_input_by`` names
    a calculation with an owner) serves ``identity: null`` on the
    response — this object is never emitted empty.
    """

    kind: Literal["species_entry", "transition_state_entry"] | None = None
    species_entry: GeometrySpeciesIdentity | None = None
    transition_state_entry: GeometryTransitionStateIdentity | None = None
    ambiguous_owners: list[GeometryIdentityOwnerRef] = Field(default_factory=list)


class ScientificGeometryResponse(BaseModel):
    """Response envelope for ``/api/v1/scientific/geometries/{geometry_handle}``.

    Phase D: ``geometry_id`` is hidden in the default response (Phase D
    internal-id visibility rules apply). ``geometry_ref`` is the
    public stable handle.

    ``submission_ref`` — which upload produced this geometry, resolved
    via the submission of its (single, unambiguous) producing
    calculation — is served only to an authenticated caller; anonymous
    callers do not receive the key at all (see
    ``app.services.scientific_read.auth_visibility``). ``submission_id``
    follows the separate Phase D internal-id policy on top of that.
    """

    request: RequestEcho
    geometry_id: int
    geometry_ref: str
    natoms: int
    geom_hash: str
    format: Literal["cartesian"] = "cartesian"
    coordinate_units: Literal["angstrom"] = "angstrom"
    symbols: list[str] = Field(default_factory=list)
    coords: list[list[float]] = Field(default_factory=list)
    atoms: list[GeometryAtomPayload] = Field(default_factory=list)
    xyz_text: str | None = None
    created_at: datetime
    provenance: GeometryProvenance = Field(default_factory=GeometryProvenance)
    identity: GeometryIdentity | None = None
    submission_id: int | None = None
    submission_ref: str | None = None
