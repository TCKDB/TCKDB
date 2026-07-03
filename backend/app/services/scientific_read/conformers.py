"""Service implementations for the scientific conformer read surface.

Two detail surfaces:

- ``GET /scientific/conformer-groups/{ref_or_id}`` — basin identity.
- ``GET /scientific/conformer-observations/{ref_or_id}`` — provenance row.

Search ships in a later slice (Phase 2 of the spec).
``conformer_group`` is the deduplicated basin under one species_entry;
``conformer_observation`` is the provenance-bearing row anchored to a
group; ``conformer_selection`` is curation metadata keyed by
``selection_kind``.

See ``backend/docs/specs/scientific_conformer_reads.md``.
"""

from __future__ import annotations

from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session

from app.api.errors import NotFoundError
from app.db.models.calculation import (
    Calculation,
    CalculationGeometryValidation,
    CalculationOutputGeometry,
    CalculationSCFStability,
)
from app.db.models.common import (
    CalculationType,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.geometry import Geometry
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.record_review import RecordReview
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import (
    ConformerAssignmentScheme,
    ConformerGroup,
    ConformerObservation,
    ConformerSelection,
    Species,
    SpeciesEntry,
)
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.reads.scientific_calculation import (
    CalculationGeometryLinkSummary,
)
from app.schemas.reads.scientific_common import (
    LevelOfTheorySummary,
    RecordReviewBadge,
    SoftwareReleaseSummary,
    WorkflowToolReleaseSummary,
)
from app.schemas.reads.scientific_conformer import (
    AvailableConformerSections,
    ConformerAssignmentSchemeSummary,
    ConformerCalculationEvidenceSummary,
    ConformerCalculationSummary,
    ConformerGeometryLink,
    ConformerGroupCoreBlock,
    ConformerObservationCoreBlock,
    ConformerObservationsSummary,
    ConformerReviewEntry,
    ConformerSelectionSummary,
    ConformerSpeciesContext,
    RequestEcho,
    ScientificConformerGroupDetailResponse,
    ScientificConformerGroupRecord,
    ScientificConformerObservationDetailResponse,
    ScientificConformerObservationRecord,
)
from app.services.scientific_read.common import (
    fetch_review_badges,
    review_summary,
    validate_includes,
)
from app.services.scientific_read.handles import (
    resolve_conformer_group_handle,
    resolve_conformer_observation_handle,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)

# ---------------------------------------------------------------------------
# Include policy
# ---------------------------------------------------------------------------


# Same legal set on both detail surfaces. ``observations`` and
# ``selections`` are no-op on the observation surface (the record IS an
# observation; selections belong to the parent group); kept legal so a
# generic client can pass the same include set everywhere — mirrors the
# TS surface's ``entries`` token policy.
_LEGAL_INCLUDE_TOKENS: set[str] = {
    "observations",
    "selections",
    "calculations",
    "geometries",
    "review",
    "internal_ids",
    "all",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}


# ---------------------------------------------------------------------------
# Conformer-group detail
# ---------------------------------------------------------------------------


def get_conformer_group(
    session: Session,
    *,
    conformer_group_handle: str,
    include: list[str] | None = None,
) -> ScientificConformerGroupDetailResponse:
    """Resolve a conformer-group handle and return its scientific projection.

    Path-handle semantics match every other ``/scientific/*`` detail:

    - Integer string: SELECT by id.
    - Public ref ``cg_…``: SELECT by ``public_ref``.
    - Wrong prefix: 422 ``handle_type_mismatch``.
    - Malformed: 422 ``invalid_handle``.
    - Missing row: 404.

    Default response surfaces the core block + parent species context +
    bounded observations / evidence / available_sections summaries.
    Heavy include blocks (``observations`` / ``selections`` /
    ``calculations`` / ``geometries`` / ``review``) expand the response
    without paginating.
    """
    includes = validate_includes(
        include or [],
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/conformer-groups/{conformer_group_ref_or_id}",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    cg_id = resolve_conformer_group_handle(session, conformer_group_handle)
    cg = session.get(ConformerGroup, cg_id)
    if cg is None:  # pragma: no cover — defended by resolver 404
        raise NotFoundError(
            f"conformer_group not found (conformer_group_id={cg_id})",
            code="handle_not_found",
        )

    cg_badge = _load_review_badge(
        session, SubmissionRecordType.conformer_group, cg.id
    )
    record = build_group_record(
        session, cg=cg, cg_badge=cg_badge, includes=includes
    )
    return ScientificConformerGroupDetailResponse(
        request=RequestEcho(include=sorted(includes)),
        review_summary=review_summary([cg_badge]),
        record=record,
    )


def build_group_record(
    session: Session,
    *,
    cg: ConformerGroup,
    cg_badge: RecordReviewBadge,
    includes: set[str],
) -> ScientificConformerGroupRecord:
    """Project one conformer group into the public scientific record shape.

    Exported so the conformer search service can produce records with
    the same shape as the group detail endpoint — search and detail
    return identical per-record payloads for the same include set.

    The caller is responsible for handing in the resolved include set
    (post-`validate_includes`, post-Phase-D) and the group's review
    badge. The default block (`species` / `observations_summary` /
    `selection_summary` / `evidence_summary` / `available_sections`)
    is always populated; heavy include blocks are populated only when
    their tokens are present in *includes*.
    """
    species_context = _build_species_context(session, cg.species_entry_id)

    obs_rows = session.scalars(
        select(ConformerObservation)
        .where(ConformerObservation.conformer_group_id == cg.id)
        .order_by(ConformerObservation.id.asc())
    ).all()
    obs_ids = [o.id for o in obs_rows]

    observations_summary = _build_observations_summary(obs_rows)
    evidence_summary = _build_evidence_summary(session, obs_ids)
    selection_rows = _load_selection_rows(session, cg.id)
    selection_summary = _build_selection_summary_list(session, selection_rows)
    available = _build_available_sections(
        session,
        obs_ids=obs_ids,
        group_ids_for_review=[cg.id],
        selection_count=len(selection_rows),
    )

    cg_core = _build_group_core_block(cg, cg_badge)

    observations_block: list[ScientificConformerObservationRecord] | None = None
    if "observations" in includes:
        obs_badges = (
            fetch_review_badges(
                session,
                record_type=SubmissionRecordType.conformer_observation,
                record_ids=obs_ids,
            )
            if obs_ids
            else {}
        )
        observations_block = [
            _build_observation_record(
                session,
                observation=o,
                cg_core=cg_core,
                species_context=species_context,
                observation_badge=obs_badges.get(
                    o.id,
                    RecordReviewBadge(status=RecordReviewStatus.not_reviewed),
                ),
                includes=includes,
            )
            for o in obs_rows
        ]

    selections_block: list[ConformerSelectionSummary] | None = None
    if "selections" in includes:
        selections_block = selection_summary

    calculations_block: list[ConformerCalculationSummary] | None = None
    if "calculations" in includes:
        calculations_block = _build_calculations_summary(session, obs_ids)

    geometries_block: list[ConformerGeometryLink] | None = None
    if "geometries" in includes:
        geometries_block = _build_output_geometry_links(session, obs_ids)

    review_block: list[ConformerReviewEntry] | None = None
    if "review" in includes:
        review_block = _build_review_history(
            session, SubmissionRecordType.conformer_group, cg.id
        )

    return ScientificConformerGroupRecord(
        conformer_group=cg_core,
        species=species_context,
        observations_summary=observations_summary,
        selection_summary=selection_summary,
        evidence_summary=evidence_summary,
        available_sections=available,
        observations=observations_block,
        selections=selections_block,
        calculations=calculations_block,
        geometries=geometries_block,
        review_history=review_block,
    )


# ---------------------------------------------------------------------------
# Conformer-observation detail
# ---------------------------------------------------------------------------


def get_conformer_observation(
    session: Session,
    *,
    conformer_observation_handle: str,
    include: list[str] | None = None,
) -> ScientificConformerObservationDetailResponse:
    """Resolve a conformer-observation handle and return its projection.

    Same handle / 422 / 404 contract as :func:`get_conformer_group`.
    Returns the observation core block + parent group + species
    context + bounded evidence/available_sections summaries.
    ``include=observations`` and ``include=selections`` are silently a
    no-op on this surface (the record IS an observation; selections
    belong to the parent group) — kept legal so a generic client can
    pass the same include set across all conformer detail surfaces.
    """
    includes = validate_includes(
        include or [],
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/conformer-observations/{conformer_observation_ref_or_id}",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    obs_id = resolve_conformer_observation_handle(
        session, conformer_observation_handle
    )
    obs = session.get(ConformerObservation, obs_id)
    if obs is None:  # pragma: no cover — defended by resolver 404
        raise NotFoundError(
            "conformer_observation not found "
            f"(conformer_observation_id={obs_id})",
            code="handle_not_found",
        )

    cg = session.get(ConformerGroup, obs.conformer_group_id)
    if cg is None:  # pragma: no cover — FK guarantees existence
        raise NotFoundError(
            "conformer_group not found for observation "
            f"(conformer_observation_id={obs.id})",
            code="handle_not_found",
        )

    cg_badge = _load_review_badge(
        session, SubmissionRecordType.conformer_group, cg.id
    )
    obs_badge = _load_review_badge(
        session, SubmissionRecordType.conformer_observation, obs.id
    )
    species_context = _build_species_context(session, cg.species_entry_id)
    cg_core = _build_group_core_block(cg, cg_badge)

    record = _build_observation_record(
        session,
        observation=obs,
        cg_core=cg_core,
        species_context=species_context,
        observation_badge=obs_badge,
        includes=includes,
    )

    return ScientificConformerObservationDetailResponse(
        request=RequestEcho(include=sorted(includes)),
        review_summary=review_summary([obs_badge]),
        record=record,
    )


# ---------------------------------------------------------------------------
# Observation record builder (shared)
# ---------------------------------------------------------------------------


def _build_observation_record(
    session: Session,
    *,
    observation: ConformerObservation,
    cg_core: ConformerGroupCoreBlock,
    species_context: ConformerSpeciesContext,
    observation_badge: RecordReviewBadge,
    includes: set[str],
) -> ScientificConformerObservationRecord:
    """Project one conformer observation into the public detail shape.

    Evidence-and-available-sections summaries are scoped to **this
    observation only** (calcs where ``conformer_observation_id ==
    observation.id``).
    """
    obs_ids = [observation.id]
    evidence = _build_evidence_summary(session, obs_ids)
    available = _build_available_sections(
        session,
        obs_ids=obs_ids,
        group_ids_for_review=[],  # review history is per-observation here
        selection_count=0,
    )
    # ``has_review`` for the observation surface tracks the observation's
    # own review_record rows (not the parent group's), so recompute it.
    available = available.model_copy(
        update={
            "has_review": _exists_review_for(
                session,
                SubmissionRecordType.conformer_observation,
                observation.id,
            ),
        }
    )

    scheme_summary = _build_assignment_scheme_summary(
        session, observation.assignment_scheme_id
    )

    selections_block: list[ConformerSelectionSummary] | None = None
    if "selections" in includes:
        # Selections live on the parent group, so expose those.
        selection_rows = _load_selection_rows(
            session, observation.conformer_group_id
        )
        selections_block = _build_selection_summary_list(session, selection_rows)

    calculations_block: list[ConformerCalculationSummary] | None = None
    if "calculations" in includes:
        calculations_block = _build_calculations_summary(session, obs_ids)

    geometries_block: list[ConformerGeometryLink] | None = None
    if "geometries" in includes:
        geometries_block = _build_output_geometry_links(session, obs_ids)

    review_block: list[ConformerReviewEntry] | None = None
    if "review" in includes:
        review_block = _build_review_history(
            session,
            SubmissionRecordType.conformer_observation,
            observation.id,
        )

    return ScientificConformerObservationRecord(
        conformer_observation=ConformerObservationCoreBlock(
            conformer_observation_id=observation.id,
            conformer_observation_ref=observation.public_ref,
            scientific_origin=observation.scientific_origin,
            note=observation.note,
            created_at=observation.created_at,
            review=observation_badge,
        ),
        conformer_group=cg_core,
        species=species_context,
        assignment_scheme=scheme_summary,
        evidence_summary=evidence,
        available_sections=available,
        selections=selections_block,
        calculations=calculations_block,
        geometries=geometries_block,
        review_history=review_block,
    )


# ---------------------------------------------------------------------------
# Core block builders
# ---------------------------------------------------------------------------


def _build_group_core_block(
    cg: ConformerGroup, badge: RecordReviewBadge
) -> ConformerGroupCoreBlock:
    return ConformerGroupCoreBlock(
        conformer_group_id=cg.id,
        conformer_group_ref=cg.public_ref,
        label=cg.label,
        note=cg.note,
        created_at=cg.created_at,
        review=badge,
    )


def _build_species_context(
    session: Session, species_entry_id: int
) -> ConformerSpeciesContext:
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
        return ConformerSpeciesContext(
            species_ref="",
            species_entry_ref="",
        )
    return ConformerSpeciesContext(
        species_id=row.species_id,
        species_ref=row.species_ref,
        species_entry_id=row.entry_id,
        species_entry_ref=row.entry_ref,
        canonical_smiles=row.smiles,
        inchi_key=row.inchi_key,
        charge=row.charge,
        multiplicity=row.multiplicity,
    )


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------


def _build_observations_summary(
    observations: list[ConformerObservation],
) -> ConformerObservationsSummary:
    by_origin: dict[str, int] = {}
    for o in observations:
        key = (
            o.scientific_origin.value
            if hasattr(o.scientific_origin, "value")
            else str(o.scientific_origin)
        )
        by_origin[key] = by_origin.get(key, 0) + 1
    return ConformerObservationsSummary(
        total=len(observations), by_scientific_origin=by_origin
    )


def _build_evidence_summary(
    session: Session, observation_ids: list[int]
) -> ConformerCalculationEvidenceSummary:
    """Compute the calculation-evidence summary for a set of observations.

    Counts and ``has_*`` booleans are aggregates over the calculations
    whose ``conformer_observation_id`` is in *observation_ids*. The
    geometry count is over distinct
    ``calculation_output_geometry.geometry_id`` rows reached through
    that calc set.
    """
    if not observation_ids:
        return ConformerCalculationEvidenceSummary(
            observation_count=len(observation_ids),
            calculation_count=0,
            has_opt=False,
            has_freq=False,
            has_sp=False,
            has_geometry_validation=False,
            has_scf_stability=False,
            geometry_count=0,
        )

    type_rows = session.execute(
        select(Calculation.type, func.count(Calculation.id))
        .where(Calculation.conformer_observation_id.in_(observation_ids))
        .group_by(Calculation.type)
    ).all()
    type_counts: dict[CalculationType, int] = {row[0]: row[1] for row in type_rows}
    total = sum(type_counts.values())

    has_geom_val = bool(
        session.scalar(
            select(
                exists().where(
                    and_(
                        CalculationGeometryValidation.calculation_id
                        == Calculation.id,
                        Calculation.conformer_observation_id.in_(
                            observation_ids
                        ),
                    )
                )
            )
        )
    )
    has_scf = bool(
        session.scalar(
            select(
                exists().where(
                    and_(
                        CalculationSCFStability.calculation_id
                        == Calculation.id,
                        Calculation.conformer_observation_id.in_(
                            observation_ids
                        ),
                    )
                )
            )
        )
    )
    geometry_count = int(
        session.scalar(
            select(
                func.count(func.distinct(CalculationOutputGeometry.geometry_id))
            )
            .select_from(CalculationOutputGeometry)
            .join(
                Calculation,
                Calculation.id == CalculationOutputGeometry.calculation_id,
            )
            .where(
                Calculation.conformer_observation_id.in_(observation_ids)
            )
        )
        or 0
    )

    return ConformerCalculationEvidenceSummary(
        observation_count=len(observation_ids),
        calculation_count=total,
        has_opt=type_counts.get(CalculationType.opt, 0) > 0,
        has_freq=type_counts.get(CalculationType.freq, 0) > 0,
        has_sp=type_counts.get(CalculationType.sp, 0) > 0,
        has_geometry_validation=has_geom_val,
        has_scf_stability=has_scf,
        geometry_count=geometry_count,
    )


def _build_available_sections(
    session: Session,
    *,
    obs_ids: list[int],
    group_ids_for_review: list[int],
    selection_count: int,
) -> AvailableConformerSections:
    has_observations = len(obs_ids) > 0
    has_calcs = False
    has_geoms = False
    if obs_ids:
        has_calcs = bool(
            session.scalar(
                select(
                    exists().where(
                        Calculation.conformer_observation_id.in_(obs_ids)
                    )
                )
            )
        )
        if has_calcs:
            has_geoms = bool(
                session.scalar(
                    select(
                        exists().where(
                            and_(
                                CalculationOutputGeometry.calculation_id
                                == Calculation.id,
                                Calculation.conformer_observation_id.in_(
                                    obs_ids
                                ),
                            )
                        )
                    )
                )
            )
    has_review = False
    if group_ids_for_review:
        has_review = bool(
            session.scalar(
                select(
                    exists().where(
                        and_(
                            RecordReview.record_id.in_(group_ids_for_review),
                            RecordReview.record_type
                            == SubmissionRecordType.conformer_group,
                        )
                    )
                )
            )
        )
    return AvailableConformerSections(
        has_observations=has_observations,
        has_selections=selection_count > 0,
        has_calculations=has_calcs,
        has_geometries=has_geoms,
        has_review=has_review,
    )


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


# ---------------------------------------------------------------------------
# Selection / assignment-scheme loaders
# ---------------------------------------------------------------------------


def _load_selection_rows(
    session: Session, conformer_group_id: int
) -> list[ConformerSelection]:
    return session.scalars(
        select(ConformerSelection)
        .where(ConformerSelection.conformer_group_id == conformer_group_id)
        .order_by(
            ConformerSelection.selection_kind.asc(),
            ConformerSelection.id.asc(),
        )
    ).all()


def _build_selection_summary_list(
    session: Session, rows: list[ConformerSelection]
) -> list[ConformerSelectionSummary]:
    scheme_ids = {r.assignment_scheme_id for r in rows if r.assignment_scheme_id}
    scheme_by_id = _bulk_assignment_scheme_summaries(session, scheme_ids)
    return [
        ConformerSelectionSummary(
            conformer_selection_id=r.id,
            selection_kind=r.selection_kind,
            note=r.note,
            created_at=r.created_at,
            assignment_scheme=scheme_by_id.get(r.assignment_scheme_id),
        )
        for r in rows
    ]


def _bulk_assignment_scheme_summaries(
    session: Session, scheme_ids: set[int]
) -> dict[int, ConformerAssignmentSchemeSummary]:
    if not scheme_ids:
        return {}
    rows = session.scalars(
        select(ConformerAssignmentScheme).where(
            ConformerAssignmentScheme.id.in_(scheme_ids)
        )
    ).all()
    return {
        r.id: ConformerAssignmentSchemeSummary(
            assignment_scheme_id=r.id,
            assignment_scheme_ref=r.public_ref,
            name=r.name,
            version=r.version,
            scope=r.scope,
            is_default=r.is_default,
        )
        for r in rows
    }


def _build_assignment_scheme_summary(
    session: Session, scheme_id: int | None
) -> ConformerAssignmentSchemeSummary | None:
    if scheme_id is None:
        return None
    scheme = session.get(ConformerAssignmentScheme, scheme_id)
    if scheme is None:
        return None
    return ConformerAssignmentSchemeSummary(
        assignment_scheme_id=scheme.id,
        assignment_scheme_ref=scheme.public_ref,
        name=scheme.name,
        version=scheme.version,
        scope=scheme.scope,
        is_default=scheme.is_default,
    )


# ---------------------------------------------------------------------------
# Calculation summary loader (include=calculations)
# ---------------------------------------------------------------------------


def _build_calculations_summary(
    session: Session, observation_ids: list[int]
) -> list[ConformerCalculationSummary]:
    if not observation_ids:
        return []
    calcs = session.scalars(
        select(Calculation)
        .where(Calculation.conformer_observation_id.in_(observation_ids))
        .order_by(Calculation.created_at.asc(), Calculation.id.asc())
    ).all()
    if not calcs:
        return []
    calc_ids = [c.id for c in calcs]
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.calculation,
        record_ids=calc_ids,
    )
    lot_summaries = _bulk_lot_summaries(
        session, {c.lot_id for c in calcs if c.lot_id is not None}
    )
    sw_summaries = _bulk_software_summaries(
        session,
        {
            c.software_release_id
            for c in calcs
            if c.software_release_id is not None
        },
    )
    wf_summaries = _bulk_workflow_summaries(
        session,
        {
            c.workflow_tool_release_id
            for c in calcs
            if c.workflow_tool_release_id is not None
        },
    )
    out: list[ConformerCalculationSummary] = []
    for c in calcs:
        out.append(
            ConformerCalculationSummary(
                calculation_id=c.id,
                calculation_ref=c.public_ref,
                type=c.type.value if hasattr(c.type, "value") else str(c.type),
                quality=(
                    c.quality.value
                    if hasattr(c.quality, "value")
                    else str(c.quality)
                ),
                created_at=c.created_at,
                review=badges.get(
                    c.id,
                    RecordReviewBadge(status=RecordReviewStatus.not_reviewed),
                ),
                level_of_theory=lot_summaries.get(c.lot_id),
                software_release=sw_summaries.get(c.software_release_id),
                workflow_tool_release=wf_summaries.get(
                    c.workflow_tool_release_id
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
# Geometry loader (include=geometries) — output geometries only
# ---------------------------------------------------------------------------


def _build_output_geometry_links(
    session: Session, observation_ids: list[int]
) -> list[ConformerGeometryLink]:
    """Return lightweight output-geometry links reached via supporting calcs.

    Output geometries only (input geometries are intentionally not
    surfaced; the conformer concept identifies a final structure).
    Full coordinate data lives behind
    ``GET /scientific/geometries/{geometry_ref}`` and is never inlined.
    """
    if not observation_ids:
        return []
    rows = session.execute(
        select(
            Geometry.id.label("geometry_id"),
            Geometry.public_ref.label("geometry_ref"),
            Geometry.natoms.label("natoms"),
            Geometry.geom_hash.label("geom_hash"),
            CalculationOutputGeometry.output_order.label("output_order"),
            CalculationOutputGeometry.role.label("role"),
            Calculation.id.label("calculation_id"),
            Calculation.public_ref.label("calculation_ref"),
        )
        .join(
            CalculationOutputGeometry,
            CalculationOutputGeometry.geometry_id == Geometry.id,
        )
        .join(
            Calculation,
            Calculation.id == CalculationOutputGeometry.calculation_id,
        )
        .where(Calculation.conformer_observation_id.in_(observation_ids))
        .order_by(
            Calculation.id.asc(),
            CalculationOutputGeometry.output_order.asc(),
        )
    ).all()
    return [
        ConformerGeometryLink(
            calculation_id=row.calculation_id,
            calculation_ref=row.calculation_ref,
            geometry=CalculationGeometryLinkSummary(
                geometry_id=row.geometry_id,
                geometry_ref=row.geometry_ref,
                input_order=None,
                output_order=row.output_order,
                role=row.role,
                natoms=row.natoms,
                geom_hash=row.geom_hash,
            ),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Review loaders
# ---------------------------------------------------------------------------


def _build_review_history(
    session: Session,
    record_type: SubmissionRecordType,
    record_id: int,
) -> list[ConformerReviewEntry]:
    rows = session.scalars(
        select(RecordReview)
        .where(
            RecordReview.record_type == record_type,
            RecordReview.record_id == record_id,
        )
        .order_by(RecordReview.reviewed_at.asc().nulls_last())
    ).all()
    return [
        ConformerReviewEntry(
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
    session: Session,
    record_type: SubmissionRecordType,
    record_id: int,
) -> RecordReviewBadge:
    badges = fetch_review_badges(
        session, record_type=record_type, record_ids=[record_id]
    )
    return badges.get(
        record_id, RecordReviewBadge(status=RecordReviewStatus.not_reviewed)
    )


__all__ = [
    "_INTERNAL_INCLUDE_TOKENS",
    "_LEGAL_INCLUDE_TOKENS",
    "build_group_record",
    "get_conformer_group",
    "get_conformer_observation",
]
