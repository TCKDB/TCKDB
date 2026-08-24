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
from sqlalchemy.orm import Session, aliased

from app.api.errors import not_found
from app.db.models.calculation import (
    Calculation,
    CalculationDependency,
    CalculationGeometryValidation,
    CalculationOutputGeometry,
    CalculationSCFStability,
)
from app.db.models.common import (
    CalculationDependencyRole,
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
    ConformerCalculationSummary,
    ConformerEvidenceCoverage,
    ConformerGeometryLink,
    ConformerGroupCoreBlock,
    ConformerGroupEvidenceSummary,
    ConformerObservationCoreBlock,
    ConformerObservationEvidenceSummary,
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
from app.services.scientific_read import levels_of_theory
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
from app.services.scientific_read.species_identity import (
    species_entry_label_for,
)

# ---------------------------------------------------------------------------
# Include policy
# ---------------------------------------------------------------------------


# Same legal set on both detail surfaces, and every token on it now
# produces a field on both. ``observations`` used to be a documented no-op
# on the observation surface, on the reading that the record already *is*
# an observation; it populates the sibling observations in the same
# conformer group there, which is the one thing an observation-grained
# record cannot say about itself. ``selections`` belong to the parent
# group and are exposed from it on both surfaces.
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
        raise not_found("conformer_group", row_id=cg_id, code="handle_not_found")

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
    levels_map: levels_of_theory.LevelsOfTheoryMap | None = None,
) -> ScientificConformerGroupRecord:
    """Project one conformer group into the public scientific record shape.

    Exported so the conformer search service can produce records with
    the same shape as the group detail endpoint — search and detail
    return identical per-record payloads for the same include set.

    The caller is responsible for handing in the resolved include set
    (post-`validate_includes`, post-Phase-D) and the group's review
    badge. A caller building a whole page may also hand in the group's
    ``levels_map``, resolved for every group on the page in one statement;
    a caller that does not gets it resolved here, which is the right
    answer for one detail read and the wrong one inside a loop.

    The default block (`species` / `observations_summary` /
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
    evidence_summary = _build_group_evidence_summary(
        session, obs_ids, levels_map=levels_map
    )
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
        # Without ``observations`` in the nested set, every embedded
        # observation would resolve the same sibling list this block is.
        nested_includes = includes - {"observations"}
        # One statement for the block, not one per observation.
        obs_levels = levels_of_theory.for_conformer_observations(
            session, obs_ids
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
                includes=nested_includes,
                levels_index=obs_levels,
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
    ``include=observations`` returns the sibling observations in this
    record's conformer group, in the shape the group surface returns;
    ``include=selections`` returns the parent group's selections.
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
        raise not_found("conformer_observation", row_id=obs_id, code="handle_not_found")

    cg = session.get(ConformerGroup, obs.conformer_group_id)
    if cg is None:  # pragma: no cover — FK guarantees existence
        raise not_found(
            "conformer_group for the requested conformer_observation",
            row_id=obs.conformer_group_id,
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
    levels_index: levels_of_theory.LevelsOfTheoryIndex | None = None,
) -> ScientificConformerObservationRecord:
    """Project one conformer observation into the public detail shape.

    Evidence-and-available-sections summaries are scoped to **this
    observation only** (calcs where ``conformer_observation_id ==
    observation.id``).

    ``include=observations`` populates the sibling observations in the same
    conformer group, in the shape the group surface returns under the same
    token. It used to be documented as a no-op here on the reading that the
    record already *is* an observation; what it can say is which other
    observations share the basin, which an observation-grained record has
    no other way to report.
    """
    obs_ids = [observation.id]
    evidence = _build_observation_evidence_summary(
        session, observation.id, levels_index=levels_index
    )
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

    observations_block: list[ScientificConformerObservationRecord] | None = None
    if "observations" in includes:
        observations_block = _build_sibling_observation_records(
            session,
            observation=observation,
            cg_core=cg_core,
            species_context=species_context,
            includes=includes,
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
        observations=observations_block,
        selections=selections_block,
        calculations=calculations_block,
        geometries=geometries_block,
        review_history=review_block,
    )


def _build_sibling_observation_records(
    session: Session,
    *,
    observation: ConformerObservation,
    cg_core: ConformerGroupCoreBlock,
    species_context: ConformerSpeciesContext,
    includes: set[str],
) -> list[ScientificConformerObservationRecord]:
    """Every observation in *observation*'s group, this one included.

    The nested records are built without ``observations`` in their include
    set. That is not a size optimisation: a sibling that resolved its own
    siblings would resolve this record again, without end.
    """
    siblings = session.scalars(
        select(ConformerObservation)
        .where(
            ConformerObservation.conformer_group_id
            == observation.conformer_group_id
        )
        .order_by(ConformerObservation.id.asc())
    ).all()
    sibling_ids = [o.id for o in siblings]
    badges = (
        fetch_review_badges(
            session,
            record_type=SubmissionRecordType.conformer_observation,
            record_ids=sibling_ids,
        )
        if sibling_ids
        else {}
    )
    nested_includes = includes - {"observations"}
    sibling_levels = levels_of_theory.for_conformer_observations(
        session, sibling_ids
    )
    return [
        _build_observation_record(
            session,
            observation=sibling,
            cg_core=cg_core,
            species_context=species_context,
            observation_badge=badges.get(
                sibling.id,
                RecordReviewBadge(status=RecordReviewStatus.not_reviewed),
            ),
            includes=nested_includes,
            levels_index=sibling_levels,
        )
        for sibling in siblings
    ]


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
            # The entry's identity columns, so the context can say which
            # entry of the species this record belongs to. Selected here
            # under their own names because species_entry_label_for()
            # reads them by attribute.
            SpeciesEntry.stereo_label,
            SpeciesEntry.electronic_state_kind,
            SpeciesEntry.electronic_state_label,
            SpeciesEntry.term_symbol,
            SpeciesEntry.isotope_key,
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
        species_entry_label=species_entry_label_for(row),
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


#: An ``opt`` calculation is *scaffolding* when a ``calculation_dependency``
#: row with ``dependency_role = 'optimized_from'`` names it as the parent of
#: another calculation anchored to the **same** conformer observation. That
#: is the coarse pre-optimisation of a staged geometry optimisation: it
#: belongs to the basin, but the refinement it fed is what the basin
#: actually got, so counting both reports one optimisation as two pieces of
#: evidence.
#:
#: Three properties of this predicate are load-bearing.
#:
#: *It is guarded on the role.* Only ``optimized_from`` collapses. A
#: ``freq_on`` or ``single_point_on`` edge also joins two calculations, but
#: a frequency job on an optimised geometry is genuinely different evidence
#: from the optimisation that produced the geometry -- folding those
#: together would be a scientific error, not a tidier number. Same for
#: ``scan_parent``. On the deployed database (measured 2026-08-24) there
#: are 63 both-anchored ``freq_on`` pairs, 65 ``single_point_on`` and 46
#: ``scan_parent``; every one of them must keep counting twice.
#:
#: *It is correct for a chain of any length.* The test is local -- "does
#: this row feed a refinement?" -- so in a coarse-medium-fine chain both
#: coarse and medium answer yes and only ``fine`` survives. Nothing here
#: assumes two stages. (The deployed database has no chain longer than two
#: nodes today; the code does not depend on that staying true.)
#:
#: *It does not collapse across observations.* The child must be anchored
#: to the same observation as the parent. A chain whose two ends sit on two
#: different observations describes two provenance rows, and silently
#: crediting one of them to the other would erase a distinction the
#: observation table exists to make. No such pair occurs on the deployed
#: database -- all 20 both-anchored ``optimized_from`` pairs are within one
#: observation -- and if one ever does, both ends count.
#:
#: The surviving end is the **refinement**, never the coarse stage, because
#: it is the one whose level of theory, geometry and convergence are what
#: the basin actually obtained; the coarse stage is scaffolding that was
#: thrown away. ``NULL`` never equals the parent's observation id, so a
#: chain with only its refinement anchored also counts once -- which is
#: precisely what makes anchoring an orphaned coarse stage later a no-op
#: for this number.
def _feeds_a_refinement_on_the_same_observation():
    """SQL predicate: this calculation is a superseded optimisation stage."""
    refinement = aliased(Calculation)
    return exists(
        select(1)
        .select_from(CalculationDependency)
        .join(
            refinement,
            refinement.id == CalculationDependency.child_calculation_id,
        )
        .where(
            CalculationDependency.dependency_role
            == CalculationDependencyRole.optimized_from,
            CalculationDependency.parent_calculation_id == Calculation.id,
            refinement.conformer_observation_id
            == Calculation.conformer_observation_id,
        )
        .correlate(Calculation)
    )


def _build_group_evidence_summary(
    session: Session,
    observation_ids: list[int],
    *,
    levels_map: levels_of_theory.LevelsOfTheoryMap | None = None,
) -> ConformerGroupEvidenceSummary:
    """Compute the group-scope calculation-evidence summary.

    ``calculation_count`` and ``geometry_count`` are aggregates over the
    calculations whose ``conformer_observation_id`` is in
    *observation_ids* (geometry over distinct
    ``calculation_output_geometry.geometry_id`` reached through that calc
    set).

    ``optimization_chain_count`` is the one number in this block that
    counts optimisation *chains* rather than rows: a staged optimisation
    (coarse pre-opt feeding a refinement, joined by ``optimized_from``)
    contributes ``1``. See
    :func:`_feeds_a_refinement_on_the_same_observation` for why only that
    role collapses, why the predicate is correct for chains longer than
    two, and why it stops at the observation boundary. It is computed in
    the same statement as the other two aggregates -- a ``FILTER`` clause,
    not a second round trip -- because this builder runs once per record
    on a paginated search page.

    ``calculation_count`` and ``geometry_count`` stay **row** counts on
    purpose. Both are inventories of what is on file rather than measures
    of evidence: ``calculation_count`` is the length of the list
    ``include=calculations`` returns, and ``geometry_count`` counts
    distinct geometry rows, of which a coarse pre-optimisation has a
    genuine one that ``include=geometries`` will hand back. Making either
    disagree with the list it summarises would trade one confusion for a
    worse one. The reader who compares ``calculation_count`` against
    ``evidence_coverage`` and finds them inconsistent -- the confusion
    that started this work -- is answered by ``optimization_chain_count``,
    not by quietly redefining an inventory.

    ``evidence_coverage`` is deliberately **not** a calculation count.
    Each value is the number of *observations* in *observation_ids* with
    at least one calculation of that kind — an observation carrying three
    ``freq`` calculations contributes ``1``. That is what makes the value
    readable against the shared ``observation_count`` denominator, and it
    is why the coverage queries count ``DISTINCT
    Calculation.conformer_observation_id`` rather than
    ``Calculation.id``.

    This block replaced a set of ``has_*`` booleans that OR-ed every
    calculation under the group together; see
    :class:`ConformerGroupEvidenceSummary` for why. ``count > 0``
    reproduces the retired boolean exactly.

    ``levels_of_theory`` is the **union** over the observations: at basin
    grain the honest statement is "these are the levels used somewhere in
    this basin". It is what turns a complete ``freq`` coverage count into
    an answerable question about comparability instead of an unanswerable
    one -- which is precisely the limitation
    :class:`ConformerEvidenceCoverage`'s docstring wrote down.

    ``levels_map`` is the batched form. The search surface resolves the
    whole page in one statement keyed by *group* -- one join through the
    observation table, rather than a statement per record -- and hands in
    the finished map. It is passed already-merged rather than as an index
    because a page-scoped index is keyed by group id while this function
    only holds observation ids, and an index whose key means something
    different depending on who built it is a bug waiting to be written.
    """
    if not observation_ids:
        # No observations: every coverage value is 0 out of 0. Nothing is
        # vacuously "covered" here — a caller reading ``freq == 0`` against
        # ``observation_count == 0`` sees an empty basin, not a complete one.
        return ConformerGroupEvidenceSummary(
            observation_count=0,
            calculation_count=0,
            optimization_chain_count=0,
            evidence_coverage=ConformerEvidenceCoverage(
                opt=0,
                freq=0,
                sp=0,
                geometry_validation=0,
                scf_stability=0,
            ),
            geometry_count=0,
            # No observations, so no calculations, so no observed types.
            # ``{}`` matches the zeroed counts beside it.
            levels_of_theory={},
        )

    # One pass gives all three per-type aggregates: the calculation total
    # (COUNT(id)), the observation coverage (COUNT(DISTINCT
    # observation_id)), and the chain count (COUNT(id) FILTER (WHERE this
    # row is not a superseded optimisation stage)). Three columns rather
    # than three statements, because this function runs once per record on
    # a paginated page -- the slope
    # ``tests/services/scientific_read/test_record_builder_statement_cost.py``
    # fails on.
    type_rows = session.execute(
        select(
            Calculation.type,
            func.count(Calculation.id),
            func.count(func.distinct(Calculation.conformer_observation_id)),
            func.count(Calculation.id).filter(
                ~_feeds_a_refinement_on_the_same_observation()
            ),
        )
        .where(Calculation.conformer_observation_id.in_(observation_ids))
        .group_by(Calculation.type)
    ).all()
    calc_counts: dict[CalculationType, int] = {
        row[0]: row[1] for row in type_rows
    }
    obs_coverage: dict[CalculationType, int] = {
        row[0]: row[2] for row in type_rows
    }
    chain_counts: dict[CalculationType, int] = {
        row[0]: row[3] for row in type_rows
    }
    total = sum(calc_counts.values())

    geometry_validation_coverage = _observation_coverage_via_join(
        session,
        observation_ids,
        joined=CalculationGeometryValidation,
        onclause=(
            CalculationGeometryValidation.calculation_id == Calculation.id
        ),
    )
    scf_stability_coverage = _observation_coverage_via_join(
        session,
        observation_ids,
        joined=CalculationSCFStability,
        onclause=CalculationSCFStability.calculation_id == Calculation.id,
    )
    geometry_count = _distinct_geometry_count(session, observation_ids)

    pooled_levels = levels_map
    if pooled_levels is None:
        pooled_levels = levels_of_theory.for_conformer_observations(
            session, observation_ids
        ).merged(observation_ids)

    return ConformerGroupEvidenceSummary(
        observation_count=len(observation_ids),
        calculation_count=total,
        # ``opt`` only. The filter is applied to every type in the one
        # statement above, but the predicate can only be true of an
        # ``optimized_from`` parent, and this field promises optimisation
        # chains -- so the value is read from the ``opt`` bucket by name
        # rather than summed, which would silently start meaning
        # "calculations that are not superseded opt stages".
        optimization_chain_count=chain_counts.get(CalculationType.opt, 0),
        evidence_coverage=ConformerEvidenceCoverage(
            opt=obs_coverage.get(CalculationType.opt, 0),
            freq=obs_coverage.get(CalculationType.freq, 0),
            sp=obs_coverage.get(CalculationType.sp, 0),
            geometry_validation=geometry_validation_coverage,
            scf_stability=scf_stability_coverage,
        ),
        geometry_count=geometry_count,
        levels_of_theory=pooled_levels,
    )


def _observation_coverage_via_join(
    session: Session,
    observation_ids: list[int],
    *,
    joined,
    onclause,
) -> int:
    """Count observations with >=1 calculation carrying a joined evidence row.

    DISTINCT is on ``conformer_observation_id``, so an observation with
    three qualifying calculations still counts once.
    """
    return int(
        session.scalar(
            select(
                func.count(
                    func.distinct(Calculation.conformer_observation_id)
                )
            )
            .select_from(Calculation)
            .join(joined, onclause)
            .where(Calculation.conformer_observation_id.in_(observation_ids))
        )
        or 0
    )


def _distinct_geometry_count(
    session: Session, observation_ids: list[int]
) -> int:
    return int(
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


def _build_observation_evidence_summary(
    session: Session,
    observation_id: int,
    *,
    levels_index: levels_of_theory.LevelsOfTheoryIndex | None = None,
) -> ConformerObservationEvidenceSummary:
    """Compute the observation-scope calculation-evidence summary.

    Scope is one provenance row, so the ``has_*`` booleans here are
    unambiguous — there is no second observation for a ``true`` to hide.
    They are kept as booleans for exactly that reason; the group surface
    reports counts instead (see
    :class:`ConformerGroupEvidenceSummary`).
    """
    observation_ids = [observation_id]
    type_rows = session.execute(
        select(Calculation.type, func.count(Calculation.id))
        .where(Calculation.conformer_observation_id.in_(observation_ids))
        .group_by(Calculation.type)
    ).all()
    type_counts: dict[CalculationType, int] = {row[0]: row[1] for row in type_rows}

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

    if levels_index is None:
        levels_index = levels_of_theory.for_conformer_observations(
            session, observation_ids
        )

    return ConformerObservationEvidenceSummary(
        observation_count=len(observation_ids),
        calculation_count=sum(type_counts.values()),
        has_opt=type_counts.get(CalculationType.opt, 0) > 0,
        has_freq=type_counts.get(CalculationType.freq, 0) > 0,
        has_sp=type_counts.get(CalculationType.sp, 0) > 0,
        has_geometry_validation=has_geom_val,
        has_scf_stability=has_scf,
        geometry_count=_distinct_geometry_count(session, observation_ids),
        levels_of_theory=levels_index.for_owner(observation_id),
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
