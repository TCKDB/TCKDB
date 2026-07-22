"""Service implementation for the scientific transport read surface.

One detail surface here; search ships in a sibling module:

- ``GET /scientific/transport/{ref_or_id}`` — one transport record.

Transport rows attach at the species_entry level (direct FK) and
carry fixed-unit scalar parameters (sigma_angstrom /
epsilon_over_k_k / dipole_debye / polarizability_angstrom3 /
rotational_relaxation). Source-calculation support is linked via
``transport_source_calculation`` by role (``full_transport`` /
``dipole`` / ``polarizability`` / ``supporting_geometry``).

See ``backend/docs/specs/scientific_transport_reads.md``.
"""

from __future__ import annotations

from sqlalchemy import and_, exists, select
from sqlalchemy.orm import Session, selectinload

from app.api.errors import NotFoundError
from app.db.models.calculation import Calculation
from app.db.models.common import (
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.literature import Literature
from app.db.models.record_review import RecordReview
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import Species, SpeciesEntry
from app.db.models.transport import Transport, TransportSourceCalculation
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.reads.scientific_common import (
    LevelOfTheorySummary,
    LiteratureSummary,
    RecordReviewBadge,
    SoftwareReleaseSummary,
    WorkflowToolReleaseSummary,
)
from app.schemas.reads.scientific_transport import (
    AvailableTransportSections,
    RequestEcho,
    ScientificTransportDetailResponse,
    ScientificTransportRecord,
    TransportCoreBlock,
    TransportEvidenceSummary,
    TransportReviewEntry,
    TransportSourceCalculationSummary,
    TransportSpeciesContext,
)
from app.services.scientific_read.common import (
    fetch_review_badges,
    review_summary,
    validate_includes,
)
from app.services.scientific_read.handles import resolve_transport_handle
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)
from app.services.trust import (
    TrustFragment,
    build_trust_fragment,
    evaluate_loaded_transport,
)

_LEGAL_INCLUDE_TOKENS: set[str] = {
    "source_calculations",
    "review",
    "internal_ids",
    "assessments",
    "all",
}
_DETAIL_LEGAL_INCLUDE_TOKENS: set[str] = _LEGAL_INCLUDE_TOKENS | {"trust"}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids", "assessments"}
_TRUST_EAGER_LOADS = (
    selectinload(Transport.species_entry),
    selectinload(Transport.source_calculations)
    .selectinload(TransportSourceCalculation.calculation)
    .selectinload(Calculation.artifacts),
    selectinload(Transport.source_calculations)
    .selectinload(TransportSourceCalculation.calculation)
    .selectinload(Calculation.geometry_validation),
    selectinload(Transport.source_calculations)
    .selectinload(TransportSourceCalculation.calculation)
    .selectinload(Calculation.sp_result),
    selectinload(Transport.source_calculations)
    .selectinload(TransportSourceCalculation.calculation)
    .selectinload(Calculation.opt_result),
    selectinload(Transport.source_calculations)
    .selectinload(TransportSourceCalculation.calculation)
    .selectinload(Calculation.freq_result),
    selectinload(Transport.source_calculations)
    .selectinload(TransportSourceCalculation.calculation)
    .selectinload(Calculation.irc_result),
    selectinload(Transport.source_calculations)
    .selectinload(TransportSourceCalculation.calculation)
    .selectinload(Calculation.scan_result),
    selectinload(Transport.source_calculations)
    .selectinload(TransportSourceCalculation.calculation)
    .selectinload(Calculation.path_search_result),
)

# Public seam for consumers that must load the same evidence graph before
# calling ``evaluate_loaded_transport``.
TRANSPORT_TRUST_EAGER_LOADS = _TRUST_EAGER_LOADS


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


def get_transport(
    session: Session,
    *,
    transport_handle: str,
    include: list[str] | None = None,
) -> ScientificTransportDetailResponse:
    """Resolve a transport handle and return its scientific projection.

    Path-handle semantics match the rest of the scientific read API.
    """
    includes = validate_includes(
        include or [],
        _DETAIL_LEGAL_INCLUDE_TOKENS,
        "/scientific/transport/{transport_ref_or_id}",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS | {"trust"},
    )
    includes = filter_internal_ids_from_resolved(includes)

    tr_id = resolve_transport_handle(session, transport_handle)
    if "trust" in includes:
        tr = session.scalars(
            select(Transport)
            .where(Transport.id == tr_id)
            .options(*_TRUST_EAGER_LOADS)
        ).one_or_none()
    else:
        tr = session.get(Transport, tr_id)
    if tr is None:  # pragma: no cover — defended by resolver 404
        raise NotFoundError(
            f"transport not found (transport_id={tr_id})",
            code="handle_not_found",
        )

    badge = _load_review_badge(session, tr.id)
    record = build_transport_record(
        session, tr=tr, badge=badge, includes=includes
    )

    return ScientificTransportDetailResponse(
        request=RequestEcho(include=sorted(includes)),
        review_summary=review_summary([badge]),
        record=record,
    )


# ---------------------------------------------------------------------------
# Shared per-record builder (reused by search)
# ---------------------------------------------------------------------------


def build_transport_record(
    session: Session,
    *,
    tr: Transport,
    badge: RecordReviewBadge,
    includes: set[str],
) -> ScientificTransportRecord:
    """Project one Transport row into the public scientific record shape.

    Exported so the search service produces records with the same
    shape as the detail endpoint.
    """
    species_context = _build_species_context(session, tr.species_entry_id)
    source_rows = _load_source_rows(session, tr.id)

    has_lj = tr.sigma_angstrom is not None and tr.epsilon_over_k_k is not None
    has_dipole = tr.dipole_debye is not None
    has_polar = tr.polarizability_angstrom3 is not None
    has_rotrelax = tr.rotational_relaxation is not None
    has_literature = tr.literature_id is not None

    evidence = TransportEvidenceSummary(
        source_calculation_count=len(source_rows),
        has_source_calculations=bool(source_rows),
        has_lj_parameters=has_lj,
        has_dipole_moment=has_dipole,
        has_polarizability=has_polar,
        has_rotational_relaxation=has_rotrelax,
        has_literature_source=has_literature,
    )
    available = AvailableTransportSections(
        has_source_calculations=bool(source_rows),
        has_review=_exists_review_for(
            session, SubmissionRecordType.transport, tr.id
        ),
    )

    sw_summary = _build_software_summary(session, tr.software_release_id)
    wf_summary = _build_workflow_summary(session, tr.workflow_tool_release_id)
    lit_summary = _build_literature_summary(session, tr.literature_id)

    core = TransportCoreBlock(
        transport_id=tr.id,
        transport_ref=tr.public_ref,
        scientific_origin=tr.scientific_origin,
        sigma_angstrom=tr.sigma_angstrom,
        epsilon_over_k_k=tr.epsilon_over_k_k,
        dipole_debye=tr.dipole_debye,
        polarizability_angstrom3=tr.polarizability_angstrom3,
        rotational_relaxation=tr.rotational_relaxation,
        note=tr.note,
        created_at=tr.created_at,
        review=badge,
    )

    source_block: list[TransportSourceCalculationSummary] | None = None
    if "source_calculations" in includes:
        source_block = _build_source_calculations(session, source_rows)

    review_block: list[TransportReviewEntry] | None = None
    if "review" in includes:
        review_block = _build_review_history(session, tr.id)

    trust_block: TrustFragment | None = None
    if "trust" in includes:
        trust_block = build_transport_trust_fragment(
            tr,
            review_status=badge.status,
        )

    return ScientificTransportRecord(
        transport=core,
        species=species_context,
        software_release=sw_summary,
        workflow_tool_release=wf_summary,
        literature=lit_summary,
        evidence_summary=evidence,
        available_sections=available,
        source_calculations=source_block,
        review_history=review_block,
        trust=trust_block,
    )


def build_transport_trust_fragment(
    transport: Transport,
    review_status: RecordReviewStatus | None = None,
) -> TrustFragment:
    """Build the read-layer trust fragment for a transport record."""
    evaluation = evaluate_loaded_transport(transport)
    return build_trust_fragment(evaluation, review_status=review_status)


# ---------------------------------------------------------------------------
# Loaders + builders
# ---------------------------------------------------------------------------


def _load_source_rows(
    session: Session, transport_id: int
) -> list[TransportSourceCalculation]:
    return session.scalars(
        select(TransportSourceCalculation)
        .where(TransportSourceCalculation.transport_id == transport_id)
        .order_by(
            TransportSourceCalculation.role.asc(),
            TransportSourceCalculation.calculation_id.asc(),
        )
    ).all()


def _exists_review_for(
    session: Session,
    record_type: SubmissionRecordType,
    record_id: int,
) -> bool:
    return bool(
        session.scalar(
            select(
                exists().where(
                    and_(
                        RecordReview.record_type == record_type,
                        RecordReview.record_id == record_id,
                    )
                )
            )
        )
    )


def _build_species_context(
    session: Session, species_entry_id: int
) -> TransportSpeciesContext:
    row = session.execute(
        select(
            SpeciesEntry.id.label("entry_id"),
            SpeciesEntry.public_ref.label("entry_ref"),
            Species.id.label("species_id"),
            Species.public_ref.label("species_ref"),
            Species.smiles.label("smiles"),
            Species.inchi_key.label("inchi_key"),
            Species.charge.label("charge"),
            Species.multiplicity.label("multiplicity"),
        )
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(SpeciesEntry.id == species_entry_id)
    ).one_or_none()
    if row is None:  # pragma: no cover — FK guarantees existence
        return TransportSpeciesContext(species_ref="", species_entry_ref="")
    return TransportSpeciesContext(
        species_id=row.species_id,
        species_ref=row.species_ref,
        species_entry_id=row.entry_id,
        species_entry_ref=row.entry_ref,
        canonical_smiles=row.smiles,
        inchi_key=row.inchi_key,
        charge=row.charge,
        multiplicity=row.multiplicity,
    )


def _build_software_summary(
    session: Session, software_release_id: int | None
) -> SoftwareReleaseSummary | None:
    if software_release_id is None:
        return None
    row = session.execute(
        select(
            SoftwareRelease.id,
            SoftwareRelease.public_ref,
            SoftwareRelease.version,
            Software.name,
        )
        .join(Software, Software.id == SoftwareRelease.software_id)
        .where(SoftwareRelease.id == software_release_id)
    ).one_or_none()
    if row is None:
        return None
    return SoftwareReleaseSummary(
        software_release_id=row.id,
        software_release_ref=row.public_ref,
        software=row.name,
        version=row.version,
    )


def _build_workflow_summary(
    session: Session, workflow_tool_release_id: int | None
) -> WorkflowToolReleaseSummary | None:
    if workflow_tool_release_id is None:
        return None
    row = session.execute(
        select(
            WorkflowToolRelease.id,
            WorkflowToolRelease.public_ref,
            WorkflowToolRelease.version,
            WorkflowTool.name,
        )
        .join(
            WorkflowTool,
            WorkflowTool.id == WorkflowToolRelease.workflow_tool_id,
        )
        .where(WorkflowToolRelease.id == workflow_tool_release_id)
    ).one_or_none()
    if row is None:
        return None
    return WorkflowToolReleaseSummary(
        workflow_tool_release_id=row.id,
        workflow_tool_release_ref=row.public_ref,
        workflow_tool=row.name,
        version=row.version,
    )


def _build_literature_summary(
    session: Session, literature_id: int | None
) -> LiteratureSummary | None:
    if literature_id is None:
        return None
    lit = session.get(Literature, literature_id)
    if lit is None:
        return None
    return LiteratureSummary(
        id=lit.id,
        literature_ref=lit.public_ref,
        title=getattr(lit, "title", None),
        year=getattr(lit, "year", None),
        doi=getattr(lit, "doi", None),
    )


# ---------------------------------------------------------------------------
# Source-calc summaries (include=source_calculations)
# ---------------------------------------------------------------------------


def _build_source_calculations(
    session: Session, source_rows: list[TransportSourceCalculation]
) -> list[TransportSourceCalculationSummary]:
    if not source_rows:
        return []
    calc_ids = [r.calculation_id for r in source_rows]
    calcs = session.scalars(
        select(Calculation).where(Calculation.id.in_(calc_ids))
    ).all()
    calc_by_id = {c.id: c for c in calcs}
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.calculation,
        record_ids=calc_ids,
    )
    lot_by_id = _bulk_lot_summaries(
        session, {c.lot_id for c in calcs if c.lot_id is not None}
    )
    sw_by_id = _bulk_software_summaries(
        session,
        {
            c.software_release_id
            for c in calcs
            if c.software_release_id is not None
        },
    )
    wf_by_id = _bulk_workflow_summaries(
        session,
        {
            c.workflow_tool_release_id
            for c in calcs
            if c.workflow_tool_release_id is not None
        },
    )
    out: list[TransportSourceCalculationSummary] = []
    for r in source_rows:
        calc = calc_by_id.get(r.calculation_id)
        if calc is None:  # pragma: no cover — race with delete
            continue
        out.append(
            TransportSourceCalculationSummary(
                role=r.role,
                calculation_id=calc.id,
                calculation_ref=calc.public_ref,
                calculation_type=calc.type,
                quality=(
                    calc.quality.value
                    if hasattr(calc.quality, "value")
                    else str(calc.quality)
                ),
                created_at=calc.created_at,
                review=badges.get(
                    calc.id,
                    RecordReviewBadge(status=RecordReviewStatus.not_reviewed),
                ),
                level_of_theory=lot_by_id.get(calc.lot_id),
                software_release=sw_by_id.get(calc.software_release_id),
                workflow_tool_release=wf_by_id.get(
                    calc.workflow_tool_release_id
                ),
            )
        )
    return out


def _bulk_lot_summaries(
    session: Session, lot_ids: set[int]
) -> dict[int, LevelOfTheorySummary]:
    if not lot_ids:
        return {}
    rows = session.scalars(
        select(LevelOfTheory).where(LevelOfTheory.id.in_(lot_ids))
    ).all()
    return {
        lot.id: LevelOfTheorySummary(
            level_of_theory_id=lot.id,
            level_of_theory_ref=lot.public_ref,
            method=lot.method,
            basis=lot.basis,
            dispersion=lot.dispersion,
            solvent=lot.solvent,
            label=None,
        )
        for lot in rows
    }


def _bulk_software_summaries(
    session: Session, release_ids: set[int]
) -> dict[int, SoftwareReleaseSummary]:
    if not release_ids:
        return {}
    rows = session.execute(
        select(
            SoftwareRelease.id,
            SoftwareRelease.public_ref,
            SoftwareRelease.version,
            Software.name,
        )
        .join(Software, Software.id == SoftwareRelease.software_id)
        .where(SoftwareRelease.id.in_(release_ids))
    ).all()
    return {
        row.id: SoftwareReleaseSummary(
            software_release_id=row.id,
            software_release_ref=row.public_ref,
            software=row.name,
            version=row.version,
        )
        for row in rows
    }


def _bulk_workflow_summaries(
    session: Session, release_ids: set[int]
) -> dict[int, WorkflowToolReleaseSummary]:
    if not release_ids:
        return {}
    rows = session.execute(
        select(
            WorkflowToolRelease.id,
            WorkflowToolRelease.public_ref,
            WorkflowToolRelease.version,
            WorkflowTool.name,
        )
        .join(
            WorkflowTool,
            WorkflowTool.id == WorkflowToolRelease.workflow_tool_id,
        )
        .where(WorkflowToolRelease.id.in_(release_ids))
    ).all()
    return {
        row.id: WorkflowToolReleaseSummary(
            workflow_tool_release_id=row.id,
            workflow_tool_release_ref=row.public_ref,
            workflow_tool=row.name,
            version=row.version,
        )
        for row in rows
    }


# ---------------------------------------------------------------------------
# Review loaders
# ---------------------------------------------------------------------------


def _build_review_history(
    session: Session, transport_id: int
) -> list[TransportReviewEntry]:
    rows = session.scalars(
        select(RecordReview)
        .where(
            RecordReview.record_type == SubmissionRecordType.transport,
            RecordReview.record_id == transport_id,
        )
        .order_by(RecordReview.reviewed_at.asc().nulls_last())
    ).all()
    return [
        TransportReviewEntry(
            status=(
                row.status.value
                if hasattr(row.status, "value")
                else str(row.status)
            ),
            reviewed_at=row.reviewed_at,
            reviewed_by=row.reviewed_by,
            note=row.note,
        )
        for row in rows
    ]


def _load_review_badge(
    session: Session, transport_id: int
) -> RecordReviewBadge:
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.transport,
        record_ids=[transport_id],
    )
    return badges.get(
        transport_id, RecordReviewBadge(status=RecordReviewStatus.not_reviewed)
    )


__all__ = [
    "_DETAIL_LEGAL_INCLUDE_TOKENS",
    "_INTERNAL_INCLUDE_TOKENS",
    "_LEGAL_INCLUDE_TOKENS",
    "build_transport_record",
    "build_transport_trust_fragment",
    "get_transport",
]
