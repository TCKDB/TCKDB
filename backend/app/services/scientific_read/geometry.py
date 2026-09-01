"""Service implementation for /api/v1/scientific/geometries/{geometry_handle}.

Returns the full coordinate payload behind a geometry public ref plus a
compact provenance summary (which calculations produced or consumed
this geometry). Designed as a follow-up read after the search
endpoints expose ``geometry_ref`` handles.
"""

from __future__ import annotations

from sqlalchemy import Text, func, select
from sqlalchemy.orm import Session

from app.api.config import settings
from app.api.error_contract import CodedValueError
from app.api.errors import NotFoundError
from app.db.models.calculation import (
    Calculation,
    CalculationInputGeometry,
    CalculationOutputGeometry,
)
from app.db.models.common import SubmissionRecordType
from app.db.models.geometry import Geometry, GeometryAtom
from app.db.models.species import Species, SpeciesEntry
from app.db.models.submission import Submission, SubmissionRecordLink
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.schemas.reads.scientific_geometry import (
    GeometryAtomPayload,
    GeometryIdentity,
    GeometryIdentityOwnerRef,
    GeometryProvenance,
    GeometryProvenanceCalcLink,
    GeometryReadRequest,
    GeometrySpeciesIdentity,
    GeometryTransitionStateIdentity,
    RequestEcho,
    ScientificGeometryResponse,
)
from app.services.scientific_read.common import (
    validate_includes,
)
from app.services.scientific_read.handles import resolve_geometry_handle
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.scientific_read.species_identity import species_entry_label

_LEGAL_INCLUDE_TOKENS: set[str] = {
    "review",
    "provenance",
    "internal_ids",
    "all",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}


def get_geometry(
    session: Session,
    *,
    geometry_handle: str,
    request: GeometryReadRequest,
) -> ScientificGeometryResponse:
    """Resolve *geometry_handle* and return its coordinate payload.

    Path-handle semantics match the rest of the scientific read API:

    - Integer ``geometry.id`` string: SELECT by id.
    - Public ref ``geom_…``: SELECT by ``public_ref``.
    - Wrong prefix: 422 ``handle_type_mismatch``.
    - Malformed: 422 ``invalid_handle``.
    - Missing row: 404.

    The response includes the geometry's natoms / geom_hash, an
    ordered list of atom rows (element + x/y/z, sorted by
    ``atom_index``), the parallel ``symbols`` + ``coords`` shape for
    convenience, the original ``xyz_text`` blob if persisted, and a
    compact provenance summary listing every calculation that
    consumed or produced this geometry.

    :param session: SQLAlchemy session bound to the read DB.
    :param geometry_handle: path string — integer id or ``geom_…`` ref.
    :param request: parsed request model carrying the ``include`` set.
    :raises NotFoundError: 404 when the geometry does not exist.
    :raises ValueError: 422 for malformed or wrong-prefix handles, or
        unknown ``include=`` tokens.
    """
    includes = validate_includes(
        request.include,
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/geometries/{geometry_handle}",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    geometry_id = resolve_geometry_handle(session, geometry_handle)
    geometry = session.get(Geometry, geometry_id)
    if geometry is None:  # pragma: no cover — defended by resolver 404
        raise NotFoundError("geometry not found")

    # Hosted abuse-control cap: refuse to materialize huge coordinate
    # payloads anonymously. See
    # ``docs/specs/public_read_abuse_controls.md``.
    cap = settings.max_geometry_atoms_public
    if cap and geometry.natoms is not None and geometry.natoms > cap:
        # A relationship code as of 2026-08-18: a supplied geometry's
        # size and a configured cap, neither named by the code. Both
        # numbers are publishable.
        #
        # ``max_atoms`` is TCKDB's own configuration. ``atoms`` looks at
        # first like the measured value the disclosure line in
        # ``app.api.code_catalogue.Shape`` withholds, and it is not: it
        # is the atom count of *one* record the caller named by handle —
        # chemistry, not a count of TCKDB's holdings. It carries no
        # enumeration signal, and it is strictly less than this endpoint
        # discloses for any request that succeeds, since a geometry
        # under the cap returns every atom individually.
        raise CodedValueError(
            "geometry_too_large",
            f"geometry has {geometry.natoms} atoms which exceeds the "
            f"public cap of {cap}. Contact a curator for bulk access.",
            context={"max_atoms": cap, "atoms": geometry.natoms},
        )

    atoms = _load_atoms(session, geometry_id)
    provenance = _build_provenance(session, geometry_id)

    all_calc_ids = {link.calculation_id for link in provenance.produced_by} | {
        link.calculation_id for link in provenance.used_as_input_by
    }
    identity = _build_identity(session, all_calc_ids)

    producing_calc_ids = {link.calculation_id for link in provenance.produced_by}
    submission_id, submission_ref = _load_submission(session, producing_calc_ids)

    # GeometryAtom.element is stored as PostgreSQL ``CHAR(2)``; single-letter
    # symbols come back padded with a trailing space. Strip on read so the
    # public payload is the natural symbol form ("O", "H", "C", …).
    elements = [(a.element or "").strip() for a in atoms]

    return ScientificGeometryResponse(
        request=RequestEcho(include=sorted(includes)),
        geometry_id=geometry.id,
        geometry_ref=geometry.public_ref,
        natoms=geometry.natoms,
        geom_hash=geometry.geom_hash,
        symbols=elements,
        coords=[[a.x, a.y, a.z] for a in atoms],
        atoms=[
            GeometryAtomPayload(
                atom_index=a.atom_index, element=elem, x=a.x, y=a.y, z=a.z
            )
            for a, elem in zip(atoms, elements, strict=False)
        ],
        xyz_text=geometry.xyz_text,
        created_at=geometry.created_at,
        provenance=provenance,
        identity=identity,
        submission_id=submission_id,
        submission_ref=submission_ref,
    )


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_atoms(session: Session, geometry_id: int) -> list[GeometryAtom]:
    """Return GeometryAtom rows for *geometry_id* ordered by atom_index."""
    return list(
        session.scalars(
            select(GeometryAtom)
            .where(GeometryAtom.geometry_id == geometry_id)
            .order_by(GeometryAtom.atom_index)
        ).all()
    )


def _build_provenance(session: Session, geometry_id: int) -> GeometryProvenance:
    """Build a small produced-by / used-as-input-by cross-reference.

    Each link carries the calculation's public ref, its integer id
    (stripped later if internal_ids isn't allowed), and the calculation
    type. Output links additionally carry the ``CalculationGeometryRole``
    declared at upload time; input links have no role column.
    """
    output_rows = session.execute(
        select(
            CalculationOutputGeometry.calculation_id,
            CalculationOutputGeometry.role,
            Calculation.public_ref,
            Calculation.type,
        )
        .join(
            Calculation,
            Calculation.id == CalculationOutputGeometry.calculation_id,
        )
        .where(CalculationOutputGeometry.geometry_id == geometry_id)
        .order_by(
            CalculationOutputGeometry.calculation_id,
            CalculationOutputGeometry.output_order,
        )
    ).all()
    produced_by = [
        GeometryProvenanceCalcLink(
            calculation_id=row.calculation_id,
            calculation_ref=row.public_ref,
            calculation_type=row.type.value,
            role=row.role.value if row.role is not None else None,
        )
        for row in output_rows
    ]

    input_rows = session.execute(
        select(
            CalculationInputGeometry.calculation_id,
            Calculation.public_ref,
            Calculation.type,
        )
        .join(
            Calculation,
            Calculation.id == CalculationInputGeometry.calculation_id,
        )
        .where(CalculationInputGeometry.geometry_id == geometry_id)
        .order_by(
            CalculationInputGeometry.calculation_id,
            CalculationInputGeometry.input_order,
        )
    ).all()
    used_as_input_by = [
        GeometryProvenanceCalcLink(
            calculation_id=row.calculation_id,
            calculation_ref=row.public_ref,
            calculation_type=row.type.value,
        )
        for row in input_rows
    ]

    return GeometryProvenance(
        produced_by=produced_by,
        used_as_input_by=used_as_input_by,
    )


# ---------------------------------------------------------------------------
# Molecular identity
# ---------------------------------------------------------------------------


def _formula_expr(smiles_column):
    """Hill-notation formula for *smiles_column*, derived by the RDKit cartridge.

    Same expression as ``app.services.scientific_read.species._formula_expr``
    (see that docstring for the full rationale) applied over whichever
    SMILES-shaped column the caller has on hand. ``mol_from_smiles()``
    returns SQL NULL for an unparseable string, so an unparseable
    ``unmapped_smiles`` yields a NULL formula rather than raising — the
    same graceful-degradation behaviour as the species surface.
    """
    return func.mol_formula(func.mol_from_smiles(smiles_column)).cast(Text)


def _build_identity(
    session: Session, calculation_ids: set[int]
) -> GeometryIdentity | None:
    """Resolve the geometry's owning species/TS entry from its calculations.

    *calculation_ids* is every calculation that produced or consumed the
    geometry (``provenance.produced_by`` ∪ ``provenance.used_as_input_by``).
    Each ``Calculation`` has exactly one scientific owner (species_entry
    XOR transition_state_entry, or occasionally neither). Geometries are
    deduplicated by content hash and therefore reusable, so it is possible
    — not merely theoretical — for two calculations that share a geometry
    to belong to two different owning entries (e.g. two isotopologues whose
    plain-element coordinates are identical). That case returns an
    ambiguous identity rather than picking one silently; see
    :class:`GeometryIdentity`.
    """
    if not calculation_ids:
        return None
    owner_rows = session.execute(
        select(Calculation.species_entry_id, Calculation.transition_state_entry_id)
        .where(Calculation.id.in_(calculation_ids))
    ).all()
    owners: set[tuple[str, int]] = set()
    for species_entry_id, transition_state_entry_id in owner_rows:
        if species_entry_id is not None:
            owners.add(("species_entry", species_entry_id))
        elif transition_state_entry_id is not None:
            owners.add(("transition_state_entry", transition_state_entry_id))
        # else: this calculation has no owner (schema allows it; the
        # calculation-detail endpoint 404s on it, but a geometry can still
        # be reached through it) — contributes nothing to the owner set.

    if not owners:
        return None
    if len(owners) > 1:
        return _build_ambiguous_identity(session, owners)

    (kind, owner_id) = next(iter(owners))
    if kind == "species_entry":
        return GeometryIdentity(
            kind="species_entry",
            species_entry=_build_species_identity(session, owner_id),
        )
    return GeometryIdentity(
        kind="transition_state_entry",
        transition_state_entry=_build_ts_identity(session, owner_id),
    )


def _build_species_identity(
    session: Session, species_entry_id: int
) -> GeometrySpeciesIdentity:
    row = session.execute(
        select(
            SpeciesEntry.id.label("entry_id"),
            SpeciesEntry.public_ref.label("entry_ref"),
            SpeciesEntry.stereo_label,
            SpeciesEntry.electronic_state_kind,
            SpeciesEntry.electronic_state_label,
            SpeciesEntry.term_symbol,
            SpeciesEntry.isotope_key,
            Species.id.label("species_id"),
            Species.public_ref.label("species_ref"),
            Species.smiles,
            Species.inchi_key,
            Species.charge,
            Species.multiplicity,
            _formula_expr(Species.smiles).label("formula"),
        )
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(SpeciesEntry.id == species_entry_id)
    ).one()
    return GeometrySpeciesIdentity(
        species_id=row.species_id,
        species_ref=row.species_ref,
        species_entry_id=row.entry_id,
        species_entry_ref=row.entry_ref,
        species_entry_label=species_entry_label(
            stereo_label=row.stereo_label,
            electronic_state_kind=row.electronic_state_kind,
            electronic_state_label=row.electronic_state_label,
            term_symbol=row.term_symbol,
            isotope_key=row.isotope_key,
        ),
        formula=row.formula,
        canonical_smiles=row.smiles,
        inchi_key=row.inchi_key,
        charge=row.charge,
        multiplicity=row.multiplicity,
    )


def _build_ts_identity(
    session: Session, transition_state_entry_id: int
) -> GeometryTransitionStateIdentity:
    row = session.execute(
        select(
            TransitionStateEntry.id.label("entry_id"),
            TransitionStateEntry.public_ref.label("entry_ref"),
            TransitionStateEntry.transition_state_id,
            TransitionStateEntry.charge,
            TransitionStateEntry.multiplicity,
            TransitionStateEntry.unmapped_smiles,
            _formula_expr(TransitionStateEntry.unmapped_smiles).label("formula"),
        )
        .where(TransitionStateEntry.id == transition_state_entry_id)
    ).one()
    ts_ref = session.scalar(
        select(TransitionState.public_ref).where(
            TransitionState.id == row.transition_state_id
        )
    )
    return GeometryTransitionStateIdentity(
        transition_state_id=row.transition_state_id,
        transition_state_ref=ts_ref,
        transition_state_entry_id=row.entry_id,
        transition_state_entry_ref=row.entry_ref,
        formula=row.formula,
        unmapped_smiles=row.unmapped_smiles,
        charge=row.charge,
        multiplicity=row.multiplicity,
    )


def _build_ambiguous_identity(
    session: Session, owners: set[tuple[str, int]]
) -> GeometryIdentity:
    """Build the ``kind: null`` identity block for a multi-owner geometry."""
    species_entry_ids = sorted(oid for kind, oid in owners if kind == "species_entry")
    ts_entry_ids = sorted(
        oid for kind, oid in owners if kind == "transition_state_entry"
    )
    owner_refs: list[GeometryIdentityOwnerRef] = []
    if species_entry_ids:
        refs = session.scalars(
            select(SpeciesEntry.public_ref)
            .where(SpeciesEntry.id.in_(species_entry_ids))
            .order_by(SpeciesEntry.id)
        ).all()
        owner_refs.extend(
            GeometryIdentityOwnerRef(kind="species_entry", ref=ref) for ref in refs
        )
    if ts_entry_ids:
        refs = session.scalars(
            select(TransitionStateEntry.public_ref)
            .where(TransitionStateEntry.id.in_(ts_entry_ids))
            .order_by(TransitionStateEntry.id)
        ).all()
        owner_refs.extend(
            GeometryIdentityOwnerRef(kind="transition_state_entry", ref=ref)
            for ref in refs
        )
    return GeometryIdentity(ambiguous_owners=owner_refs)


# ---------------------------------------------------------------------------
# Submission reference
# ---------------------------------------------------------------------------


def _load_submission(
    session: Session, producing_calculation_ids: set[int]
) -> tuple[int | None, str | None]:
    """Return ``(submission_id, submission_ref)`` for the geometry's deposit.

    "Which upload produced this geometry" is answered by the submission
    of the calculation(s) that produced it (``provenance.produced_by``),
    not the calculations that merely consumed it as an input — a
    calculation that reads a shared geometry did not deposit it.

    Deduplicated geometries can in principle be produced by calculations
    from more than one submission (the same coordinates re-derived
    independently). When that happens there is no single deposit to
    name, so this returns ``(None, None)`` rather than picking one
    silently — the same policy :func:`_build_identity` applies to
    ownership one level up. A geometry produced by several calculations
    that all cite the *same* submission still resolves to that one
    submission.
    """
    if not producing_calculation_ids:
        return None, None
    submission_ids = set(
        session.scalars(
            select(SubmissionRecordLink.submission_id)
            .where(
                SubmissionRecordLink.record_type == SubmissionRecordType.calculation,
                SubmissionRecordLink.record_id.in_(producing_calculation_ids),
            )
            .distinct()
        ).all()
    )
    if len(submission_ids) != 1:
        return None, None
    (submission_id,) = submission_ids
    submission_ref = session.scalar(
        select(Submission.public_ref).where(Submission.id == submission_id)
    )
    return submission_id, submission_ref
