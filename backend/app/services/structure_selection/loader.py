"""The structure candidate loader: a consistent, normalised snapshot of one entry's authorized population.

Two steps, so a population that is too large is refused before anything heavy is loaded:

1. :func:`scan_population` reads only ids and review states, applies the effective review floor, the terminal
   (rejected, deprecated) exclusion and the quality filter, counts what is left against the bound and counts
   the nested evidence it will need. Over a bound it raises a coded 422; it never returns a prefix.
2. :func:`load_population` loads that population with the eager-load graph the assessment needs and
   normalises each unit.

The never-borrow rule governs what is read. A unit's facts are what it links: its own columns and result rows,
its own declaration, its own determination's sources. Nothing is taken from a sibling record of the same
entry, and a role a determination pins that is not available to the request (hidden by the read profile,
terminal, below a floor) reads as unavailable. It is never filled from another calculation.

Visibility. A record the read profile hides is dropped without trace: it is not counted, not listed and not
named in a refusal, and a determination whose pinned calculation is hidden reads that role as unavailable with
one generic code that does not distinguish "hidden" from "absent".
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.errors import NotFoundError, not_found
from app.db.models.calculation import (
    Calculation,
    CalculationConstraint,
    CalculationDependency,
    CalculationFreqMode,
    CalculationHessian,
    CalculationInputGeometry,
    CalculationOutputGeometry,
)
from app.db.models.common import (
    CalculationType,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.geometry import Geometry
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.species import ConformerObservation, Species, SpeciesEntry
from app.db.models.structure_determination import (
    StructureDetermination,
    StructureDeterminationSource,
    StructureEvidenceFinding,
)
from app.db.models.transition_state import TransitionStateEntry, TransitionStateValidationEvidence
from app.schemas.reads.scientific_common import REVIEW_RANK
from app.services.scientific_read.common import fetch_review_badges, visible_statuses
from app.services.scientific_read.composite_binding import composite_scheme_summaries
from app.services.scientific_read.handles import canonical_level_of_theory_id
from app.services.structure_selection.bounds import check_candidates, check_nested_rows, traversal_too_deep
from app.services.structure_selection.models import (
    CurvatureFacts,
    EnergyFacts,
    FindingFacts,
    Grain,
    LevelFacts,
    LineageEdge,
    NormalizedCalculation,
    NormalizedDetermination,
    NormalizedSource,
    StructureRequest,
    StructureSubject,
)

CODE_UNKNOWN_MEMBER = "unknown_structure_member_ref"
#: The exclusion listing is bounded: under a strict floor nearly every row of a large population can be excluded,
#: and naming them all would make the response as big as the population. The total is always reported.
MAX_EXCLUDED_LISTED = 100
REASON_TERMINAL = "terminal_review_status"
REASON_BELOW_FLOOR = "below_review_floor"
REASON_QUALITY = "quality_not_permitted"
REASON_OWNER_BELOW_FLOOR = "owner_below_review_floor"
#: The one reason a hidden, absent or unusable pinned calculation reads as: it names no record.
REASON_EVIDENCE_UNAVAILABLE = "required_evidence_unavailable"
REASON_SOURCE_BELOW_FLOOR = "source_below_review_floor"
REASON_SOURCE_TERMINAL = "source_terminal_review_status"
REASON_SOURCE_QUALITY = "source_quality_not_permitted"

#: Calculation types that carry an energy a recorded minimum can compare.
ENERGY_TYPES = (CalculationType.sp, CalculationType.opt, CalculationType.composite)

_TERMINAL = frozenset({RecordReviewStatus.rejected, RecordReviewStatus.deprecated})



@dataclass(frozen=True)
class PopulationScan:
    """Ids and review states of one entry's candidate units, partitioned before anything is loaded."""

    grain: Grain
    entry_id: int
    effective_statuses: frozenset[RecordReviewStatus]
    profile_statuses: frozenset[RecordReviewStatus]
    unit_ids: tuple[int, ...]
    unit_status: dict[int, RecordReviewStatus]
    excluded: tuple[tuple[int, RecordReviewStatus | None, str], ...]
    total_units: int
    nested_rows: int
    #: determination id -> the ids of its observation, for a basin (None otherwise).
    observation_of: dict[int, int | None] = field(default_factory=dict)


@dataclass
class LoadedPopulation:
    """The loaded, normalised population, in id order."""

    subject: StructureSubject
    calculations: dict[int, NormalizedCalculation] = field(default_factory=dict)
    determinations: dict[int, NormalizedDetermination] = field(default_factory=dict)

    @property
    def calculations_by_ref(self) -> dict[str, NormalizedCalculation]:
        return {c.calculation_ref: c for c in self.calculations.values()}


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def _statuses(request: StructureRequest) -> tuple[frozenset[RecordReviewStatus], frozenset[RecordReviewStatus]]:
    effective = frozenset(
        visible_statuses(min_review_status=request.min_review_status, include_rejected=False, include_deprecated=False)
    )
    # What the read profile itself shows (under ``curated``: ``approved`` and nothing below). A record outside this
    # set is not part of this request's world at all.
    profile = frozenset(visible_statuses(min_review_status=None, include_rejected=True, include_deprecated=True))
    return effective, profile


def _entry_record_type(grain: Grain) -> SubmissionRecordType:
    return (
        SubmissionRecordType.transition_state_entry
        if grain is Grain.transition_state
        else SubmissionRecordType.species_entry
    )


def assert_subject_visible(session: Session, grain: Grain, entry_id: int, *, profile: frozenset[RecordReviewStatus]) -> None:
    """A target the read profile hides behaves exactly like one that does not exist."""
    model = TransitionStateEntry if grain is Grain.transition_state else SpeciesEntry
    if session.get(model, entry_id) is None:
        raise not_found("transition_state_entry" if grain is Grain.transition_state else "species_entry", row_id=entry_id)
    status = fetch_review_badges(session, record_type=_entry_record_type(grain), record_ids=[entry_id])[entry_id].status
    if status not in profile:
        raise not_found("transition_state_entry" if grain is Grain.transition_state else "species_entry", row_id=entry_id)


def _count_nested(session: Session, calc_ids: set[int], determination_ids: set[int], *, source_rows: int = 0) -> int:
    """Rows the units' necessary evidence will load: results, geometry links, constraints, sources and findings.

    Counted by aggregate queries over the whole authorized id set, before anything is loaded, so a population
    too heavy to decide is refused rather than truncated. ``calc_ids`` is only calculations the read profile shows
    and ``source_rows`` only the sources that name one: a hidden record adds nothing to the count, so neither the
    result nor a refusal reveals that one exists.
    """
    total = 0
    if calc_ids:
        from app.db.models.calculation import (
            CalculationCompositeResult,
            CalculationFreqResult,
            CalculationOptResult,
            CalculationSCFStability,
            CalculationSPResult,
        )

        total += (
            session.scalar(
                select(func.count()).select_from(CalculationFreqMode).where(
                    CalculationFreqMode.calculation_id.in_(calc_ids), CalculationFreqMode.is_imaginary.is_(True)
                )
            )
            or 0
        )
        models: tuple[Any, ...] = (
            CalculationSPResult, CalculationOptResult, CalculationCompositeResult, CalculationFreqResult,
            CalculationHessian, CalculationSCFStability, CalculationInputGeometry, CalculationOutputGeometry,
            CalculationConstraint,
        )
        for model in models:
            total += session.scalar(select(func.count()).select_from(model).where(model.calculation_id.in_(calc_ids))) or 0
        total += (
            session.scalar(
                select(func.count()).select_from(StructureEvidenceFinding).where(
                    StructureEvidenceFinding.subject_calculation_id.in_(calc_ids)
                )
            )
            or 0
        )
    total += source_rows
    if determination_ids:
        total += (
            session.scalar(
                select(func.count()).select_from(StructureEvidenceFinding).where(
                    StructureEvidenceFinding.subject_determination_id.in_(determination_ids)
                )
            )
            or 0
        )
    return total


def scan_population(session: Session, *, entry_id: int, request: StructureRequest) -> PopulationScan:
    """Partition the entry's candidate units by review state; load nothing else.

    A unit is in the population when every record that owns it passes the effective floor (the caller's
    ``min_review_status`` combined with the read profile's), is not rejected or deprecated, and (for a
    calculation) has a permitted quality. Units the caller's own floor, the terminal states or the quality filter
    exclude are listed with why; records the *profile* hides are dropped without trace.

    :raises NotFoundError: the entry does not exist or the profile hides it (the two are not told apart), or an
        explicit member names nothing in this entry's authorized population.
    :raises CodedValueError: ``structure_selection_population_too_large`` or
        ``structure_selection_evidence_too_large``; nothing is assessed.
    """
    effective, profile = _statuses(request)
    assert_subject_visible(session, request.grain, entry_id, profile=profile)
    if request.grain is Grain.calculation:
        scan = _scan_calculations(session, entry_id, request, effective, profile)
    else:
        scan = _scan_determinations(session, entry_id, request, effective, profile)
    return scan


def _scan_calculations(
    session: Session,
    entry_id: int,
    request: StructureRequest,
    effective: frozenset[RecordReviewStatus],
    profile: frozenset[RecordReviewStatus],
) -> PopulationScan:
    rows = session.execute(
        select(Calculation.id, Calculation.public_ref, Calculation.quality)
        .where(Calculation.species_entry_id == entry_id, Calculation.type.in_(ENERGY_TYPES))
        .order_by(Calculation.id)
    ).all()
    ids = [r.id for r in rows]
    refs = {r.id: r.public_ref for r in rows}
    quality = {r.id: r.quality for r in rows}
    badges = fetch_review_badges(session, record_type=SubmissionRecordType.calculation, record_ids=ids)
    owner_status = fetch_review_badges(session, record_type=SubmissionRecordType.species_entry, record_ids=[entry_id])[entry_id].status
    ids = [i for i in ids if badges[i].status in profile]
    if request.member_refs is not None:
        wanted = set(request.member_refs)
        by_ref = {refs[i]: i for i in ids}
        missing = sorted(wanted - set(by_ref))
        if missing:
            # One response for a ref that names nothing, names another entry's calculation, or names one the
            # profile hides: the refusal must not tell the caller which.
            raise NotFoundError(
                f"member_refs names no calculation in this entry's authorized population ({missing[0]!r}).",
                code=CODE_UNKNOWN_MEMBER,
                context={"field": "member_refs", "ref": missing[0]},
            )
        ids = [i for i in ids if refs[i] in wanted]
    status = {i: badges[i].status for i in ids}
    population: list[int] = []
    excluded: list[tuple[int, RecordReviewStatus | None, str]] = []
    for i in ids:
        if status[i] in _TERMINAL:
            excluded.append((i, status[i], REASON_TERMINAL))
        elif status[i] not in effective:
            excluded.append((i, status[i], REASON_BELOW_FLOOR))
        elif owner_status in _TERMINAL or owner_status not in effective:
            # The entry's floor applies at every grain: a calculation does not outrank the entry it belongs to.
            excluded.append((i, owner_status, REASON_OWNER_BELOW_FLOOR))
        elif quality[i] not in request.quality_set:
            excluded.append((i, status[i], REASON_QUALITY))
        else:
            population.append(i)
    check_candidates(request.bounds, len(population))
    nested = _count_nested(session, set(population), set())
    check_nested_rows(request.bounds, nested)
    return PopulationScan(
        grain=request.grain,
        entry_id=entry_id,
        effective_statuses=effective,
        profile_statuses=profile,
        unit_ids=tuple(population),
        unit_status=status,
        excluded=tuple(excluded),
        total_units=len(ids),
        nested_rows=nested,
    )


def _kinds(grain: Grain) -> tuple[str, ...]:
    return ("conformer_basin", "geometry") if grain is Grain.conformer else ("saddle_point", "geometry")


def _scan_determinations(
    session: Session,
    entry_id: int,
    request: StructureRequest,
    effective: frozenset[RecordReviewStatus],
    profile: frozenset[RecordReviewStatus],
) -> PopulationScan:
    owner_column = (
        StructureDetermination.transition_state_entry_id
        if request.grain is Grain.transition_state
        else StructureDetermination.species_entry_id
    )
    rows = session.execute(
        select(
            StructureDetermination.id,
            StructureDetermination.public_ref,
            StructureDetermination.conformer_observation_id,
        )
        .where(owner_column == entry_id, StructureDetermination.target_kind.in_(_kinds(request.grain)))
        .order_by(StructureDetermination.id)
    ).all()
    ids = [r.id for r in rows]
    refs = {r.id: r.public_ref for r in rows}
    observation_of = {r.id: r.conformer_observation_id for r in rows}

    owner_badge = fetch_review_badges(session, record_type=_entry_record_type(request.grain), record_ids=[entry_id])[entry_id]
    observation_ids = {o for o in observation_of.values() if o is not None}
    observation_badges = fetch_review_badges(
        session, record_type=SubmissionRecordType.conformer_observation, record_ids=observation_ids
    )
    # A determination whose own observation the profile hides is not part of this request's world.
    ids = [i for i in ids if observation_of[i] is None or observation_badges[observation_of[i]].status in profile]
    if request.member_refs is not None:
        wanted = set(request.member_refs)
        by_ref = {refs[i]: i for i in ids}
        missing = sorted(wanted - set(by_ref))
        if missing:
            raise NotFoundError(
                f"member_refs names no determination in this entry's authorized population ({missing[0]!r}).",
                code=CODE_UNKNOWN_MEMBER,
                context={"field": "member_refs", "ref": missing[0]},
            )
        ids = [i for i in ids if refs[i] in wanted]

    population: list[int] = []
    excluded: list[tuple[int, RecordReviewStatus | None, str]] = []
    unit_status: dict[int, RecordReviewStatus] = {}
    for i in ids:
        owners = [owner_badge.status]
        if observation_of[i] is not None:
            owners.append(observation_badges[observation_of[i]].status)
        weakest = max(owners, key=lambda s: REVIEW_RANK[s])
        unit_status[i] = weakest
        if weakest in _TERMINAL:
            excluded.append((i, weakest, REASON_TERMINAL))
        elif weakest not in effective:
            excluded.append((i, weakest, REASON_OWNER_BELOW_FLOOR))
        else:
            population.append(i)
    check_candidates(request.bounds, len(population))
    pinned = list(
        session.scalars(
            select(StructureDeterminationSource.calculation_id).where(
                StructureDeterminationSource.determination_id.in_(population)
            )
        )
    )
    pinned_badges = fetch_review_badges(session, record_type=SubmissionRecordType.calculation, record_ids=set(pinned))
    # A pinned calculation the profile hides is not counted: it reads as absent everywhere.
    shown = [c for c in pinned if pinned_badges[c].status in profile]
    source_calc_ids = set(shown)
    nested = _count_nested(session, source_calc_ids, set(population), source_rows=len(shown))
    check_nested_rows(request.bounds, nested)
    return PopulationScan(
        grain=request.grain,
        entry_id=entry_id,
        effective_statuses=effective,
        profile_statuses=profile,
        unit_ids=tuple(population),
        unit_status=unit_status,
        excluded=tuple(excluded),
        total_units=len(ids),
        nested_rows=nested,
        observation_of=observation_of,
    )


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

_CALC_OPTIONS = (
    selectinload(Calculation.lot),
    selectinload(Calculation.software_release),
    selectinload(Calculation.sp_result),
    selectinload(Calculation.opt_result),
    selectinload(Calculation.composite_result),
    selectinload(Calculation.freq_result),
    # Existence and the geometry the Hessian belongs to are all the assessment reads; the matrix itself is not loaded.
    selectinload(Calculation.hessian).load_only(CalculationHessian.calculation_id, CalculationHessian.geometry_id),
    selectinload(Calculation.scf_stability),
    selectinload(Calculation.geometry_validation),
    selectinload(Calculation.constraints),
    selectinload(Calculation.input_geometries),
    selectinload(Calculation.output_geometries),
    selectinload(Calculation.conformer_observation),
)


def load_subject(session: Session, grain: Grain, entry_id: int) -> StructureSubject:
    """The entry every unit belongs to, with the identity facts a claim is judged against."""
    if grain is Grain.transition_state:
        ts = session.get(TransitionStateEntry, entry_id)
        if ts is None:
            raise not_found("transition_state_entry", row_id=entry_id)
        return StructureSubject(
            kind="transition_state_entry",
            entry_ref=ts.public_ref,
            stationary_point_kind=None,
            electronic_state_kind=None,
            isotope_key=None,
            charge=ts.charge,
            multiplicity=ts.multiplicity,
        )
    entry = session.get(SpeciesEntry, entry_id)
    if entry is None:
        raise not_found("species_entry", row_id=entry_id)
    species = session.get(Species, entry.species_id)
    return StructureSubject(
        kind="species_entry",
        entry_ref=entry.public_ref,
        stationary_point_kind=entry.kind.value,
        electronic_state_kind=entry.electronic_state_kind.value,
        isotope_key=entry.isotope_key,
        charge=species.charge if species is not None else None,
        multiplicity=species.multiplicity if species is not None else None,
    )


def _level_facts(session: Session, calcs: list[Calculation]) -> dict[int, LevelFacts]:
    canonical = {c.id: canonical_level_of_theory_id(session, c.lot_id) for c in calcs if c.lot_id is not None}
    lot_ids = set(canonical.values())
    lots = {lot.id: lot for lot in session.scalars(select(LevelOfTheory).where(LevelOfTheory.id.in_(lot_ids)))} if lot_ids else {}
    schemes = composite_scheme_summaries(session, lot_ids)
    out: dict[int, LevelFacts] = {}
    for c in calcs:
        if c.lot_id is None or canonical[c.id] not in lots:
            out[c.id] = LevelFacts(None, None, None, None, None, None, None, None, None)
            continue
        lot = lots[canonical[c.id]]
        scheme = schemes.get(lot.id)
        out[c.id] = LevelFacts(
            level_ref=lot.public_ref,
            method=lot.method,
            basis=lot.basis,
            aux_basis=lot.aux_basis,
            dispersion=lot.dispersion,
            solvent=lot.solvent,
            solvent_model=lot.solvent_model,
            spin_treatment=lot.spin_treatment.value if lot.spin_treatment is not None else None,
            core_treatment=lot.core_treatment.value if lot.core_treatment is not None else None,
            composite_scheme_ref=scheme.composite_scheme_ref if scheme is not None else None,
        )
    return out


def _has_cycle(start: int, edges: list[tuple[int, int, str]]) -> bool:
    """Whether the explored edges contain a directed cycle reachable from ``start``.

    A *diamond* (one calculation that is the parent of two others on the path) is not a cycle: only an edge back
    to a node still on the current path is. Iterative depth-first search with the usual three colours.
    """
    parents: dict[int, list[int]] = defaultdict(list)
    for child, parent, _ in edges:
        parents[child].append(parent)
    on_path: set[int] = {start}
    done: set[int] = set()
    stack: list[tuple[int, Any]] = [(start, iter(sorted(parents.get(start, ()))))]
    while stack:
        node, remaining = stack[-1]
        advanced = False
        for parent in remaining:
            if parent in on_path:
                return True
            if parent in done:
                continue
            on_path.add(parent)
            stack.append((parent, iter(sorted(parents.get(parent, ())))))
            advanced = True
            break
        if not advanced:
            stack.pop()
            on_path.discard(node)
            done.add(node)
    return False


def _lineage(
    session: Session, calc_ids: set[int], *, depth_limit: int, profile: frozenset[RecordReviewStatus]
) -> tuple[dict[int, list[tuple[int, int, str]]], set[int], set[int]]:
    """Typed parents of each calculation, breadth first, ``depth_limit`` hops.

    Returns ``(edges_by_start, truncated_starts, cyclic_starts)`` where an edge is ``(child_id, parent_id, role)``.
    A start whose frontier is still non-empty after ``depth_limit`` hops is *truncated*; a start whose explored
    edges contain a real directed cycle is *cyclic* (a shared parent reached by two paths is not). Both are
    reported, never silently cut.

    A parent the read profile hides is treated as absent: its edge is not recorded, it is not walked past, and it
    cannot make the traversal exceed the depth bound, so the result is the one a caller who could not see it would
    get.
    """
    edges_by_start: dict[int, list[tuple[int, int, str]]] = {i: [] for i in calc_ids}
    truncated: set[int] = set()
    cyclic: set[int] = set()
    parents_cache: dict[int, list[tuple[int, str]]] = {}
    shown: dict[int, bool] = dict.fromkeys(calc_ids, True)

    def parents_of(ids: set[int]) -> None:
        need = ids - parents_cache.keys()
        if not need:
            return
        raw: dict[int, list[tuple[int, str]]] = {child_id: [] for child_id in need}
        for dep in session.scalars(select(CalculationDependency).where(CalculationDependency.child_calculation_id.in_(need))):
            raw[dep.child_calculation_id].append((dep.parent_calculation_id, dep.dependency_role.value))
        unknown = {p for lst in raw.values() for p, _ in lst} - shown.keys()
        if unknown:
            badges = fetch_review_badges(session, record_type=SubmissionRecordType.calculation, record_ids=unknown)
            for pid in unknown:
                shown[pid] = badges[pid].status in profile
        for child_id, lst in raw.items():
            parents_cache[child_id] = [(p, r) for p, r in lst if shown[p]]

    for start in sorted(calc_ids):
        frontier = {start}
        seen = {start}
        for _ in range(depth_limit):
            parents_of(frontier)
            nxt: set[int] = set()
            for node in sorted(frontier):
                for parent, role in sorted(parents_cache[node]):
                    edges_by_start[start].append((node, parent, role))
                    if parent not in seen:
                        seen.add(parent)
                        nxt.add(parent)
            frontier = nxt
            if not frontier:
                break
        if frontier:
            parents_of(frontier)
            if any(parents_cache[n] for n in frontier):
                truncated.add(start)
        if _has_cycle(start, edges_by_start[start]):
            cyclic.add(start)
    return edges_by_start, truncated, cyclic


def _finding_facts(
    session: Session, *, geometry_ids: set[int], calc_ids: set[int], determination_ids: set[int]
) -> dict[tuple[str, int], list[StructureEvidenceFinding]]:
    """Findings about the given subjects, plus every finding that supersedes one of them (transitively)."""
    conditions = []
    if geometry_ids:
        conditions.append(StructureEvidenceFinding.subject_geometry_id.in_(geometry_ids))
    if calc_ids:
        conditions.append(StructureEvidenceFinding.subject_calculation_id.in_(calc_ids))
    if determination_ids:
        conditions.append(StructureEvidenceFinding.subject_determination_id.in_(determination_ids))
    if not conditions:
        return {}
    from sqlalchemy import or_

    found = {f.id: f for f in session.scalars(select(StructureEvidenceFinding).where(or_(*conditions)))}
    frontier = set(found)
    while frontier:
        more = session.scalars(
            select(StructureEvidenceFinding).where(StructureEvidenceFinding.supersedes_finding_id.in_(frontier))
        ).all()
        frontier = {f.id for f in more if f.id not in found}
        found.update({f.id: f for f in more})
    by_subject: dict[tuple[str, int], list[StructureEvidenceFinding]] = defaultdict(list)
    for f in found.values():
        if f.subject_geometry_id is not None:
            by_subject[("geometry", f.subject_geometry_id)].append(f)
        if f.subject_calculation_id is not None:
            by_subject[("calculation", f.subject_calculation_id)].append(f)
        if f.subject_determination_id is not None:
            by_subject[("determination", f.subject_determination_id)].append(f)
    return by_subject


def _to_finding_facts(
    session: Session, findings: list[StructureEvidenceFinding], refs: dict[tuple[str, int], str]
) -> tuple[FindingFacts, ...]:
    """Public-ref form of findings, with every referenced finding's ref (including superseded ones)."""
    by_id = {f.id: f for f in findings}
    out: list[FindingFacts] = []
    for f in sorted(findings, key=lambda x: x.id):
        if f.subject_geometry_id is not None:
            subject = refs.get(("geometry", f.subject_geometry_id), "")
        elif f.subject_calculation_id is not None:
            subject = refs.get(("calculation", f.subject_calculation_id), "")
        else:
            subject = refs.get(("determination", f.subject_determination_id), "")  # type: ignore[arg-type]
        out.append(
            FindingFacts(
                finding_ref=f.public_ref,
                kind=f.kind.value,
                scope=f.scope.value,
                subject_ref=subject,
                role=f.role.value if f.role is not None else None,
                verdict=f.verdict.value,
                authority=f.authority.value,
                semantic_version=f.semantic_version,
                supersedes_ref=by_id[f.supersedes_finding_id].public_ref if f.supersedes_finding_id in by_id else None,
            )
        )
    return tuple(out)


def _normalize_calculations(
    session: Session,
    calcs: list[Calculation],
    status: dict[int, RecordReviewStatus],
    owner_refs: dict[int, str],
    *,
    request: StructureRequest,
    with_lineage: bool,
    findings: dict[tuple[str, int], list[StructureEvidenceFinding]],
    geometry_refs: dict[int, str],
    profile: frozenset[RecordReviewStatus],
) -> dict[int, NormalizedCalculation]:
    levels = _level_facts(session, calcs)
    imaginary_modes = _imaginary_modes(session, {c.id for c in calcs if c.freq_result is not None})
    # A calculation anchored to an observation the profile hides names no observation: it reads as unanchored.
    observation_ids = {c.conformer_observation_id for c in calcs if c.conformer_observation_id is not None}
    observation_badges = fetch_review_badges(
        session, record_type=SubmissionRecordType.conformer_observation, record_ids=observation_ids
    )
    shown_observations = {o for o in observation_ids if observation_badges[o].status in profile}
    ids = {c.id for c in calcs}
    edges: dict[int, list[tuple[int, int, str]]] = {}
    truncated: set[int] = set()
    cyclic: set[int] = set()
    ref_by_id: dict[int, str] = {c.id: c.public_ref for c in calcs}
    if with_lineage and ids:
        edges, truncated, cyclic = _lineage(session, ids, depth_limit=request.bounds.dependency_depth, profile=profile)
        if truncated:
            raise traversal_too_deep(request.bounds)
        parent_ids = {p for lst in edges.values() for _, p, _ in lst} | {c for lst in edges.values() for c, _, _ in lst}
        missing = parent_ids - ref_by_id.keys()
        if missing:
            ref_by_id.update(
                dict(session.execute(select(Calculation.id, Calculation.public_ref).where(Calculation.id.in_(missing))).tuples().all())
            )
    out: dict[int, NormalizedCalculation] = {}
    for rank, c in enumerate(sorted(calcs, key=lambda x: x.id), start=1):
        sp, opt, comp, freq, hess = c.sp_result, c.opt_result, c.composite_result, c.freq_result, c.hessian
        unit_geometries = {link.geometry_id for link in c.input_geometries} | {link.geometry_id for link in c.output_geometries}
        related = list(findings.get(("calculation", c.id), []))
        for g in unit_geometries:
            related.extend(findings.get(("geometry", g), []))
        # Findings that supersede one of these may be about another subject; they were loaded with the closure.
        rel_ids = {f.id for f in related}
        for fl in list(findings.values()):
            for f in fl:
                if f.supersedes_finding_id in rel_ids and f.id not in rel_ids:
                    related.append(f)
                    rel_ids.add(f.id)
        refs_for_findings = {("calculation", c.id): c.public_ref, **{("geometry", g): geometry_refs.get(g, "") for g in unit_geometries}}
        out[c.id] = NormalizedCalculation(
            calculation_ref=c.public_ref,
            type=c.type.value,
            quality=c.quality.value,
            review_status=status[c.id],
            created_at=c.created_at,
            id_rank=rank,
            owner_ref=owner_refs[c.id],
            level=levels[c.id],
            software=(
                f"{c.software_release.software.name}@{c.software_release.version}"
                if c.software_release is not None and c.software_release.software is not None
                else None
            ),
            declaration_state=_declaration_state(c.actual_protocol_declaration)[0],
            declaration=_declaration_state(c.actual_protocol_declaration)[1],
            constraint_rows=len(c.constraints),
            energy=EnergyFacts(
                sp_electronic_hartree=sp.electronic_energy_hartree if sp is not None else None,
                sp_uncertainty_hartree=sp.electronic_energy_uncertainty_hartree if sp is not None else None,
                opt_final_hartree=opt.final_energy_hartree if opt is not None else None,
                opt_converged=opt.converged if opt is not None else None,
                composite_assembly=comp.assembly.value if comp is not None else None,
                composite_electronic_hartree=comp.electronic_energy_hartree if comp is not None else None,
                composite_e0_hartree=comp.e0_hartree if comp is not None else None,
                composite_recipe_zpe_hartree=comp.recipe_zpe_hartree if comp is not None else None,
            ),
            curvature=CurvatureFacts(
                has_freq_result=freq is not None,
                n_imag=freq.n_imag if freq is not None else None,
                reaction_coordinate_mode_index=freq.reaction_coordinate_mode_index if freq is not None else None,
                structural_flag=freq.imaginary_mode_structural_flag if freq is not None else None,
                tau_cm1=freq.imaginary_mode_tau_cm1 if freq is not None else None,
                tau_basis=freq.imaginary_mode_tau_basis if freq is not None else None,
                has_hessian=hess is not None,
                hessian_geometry_ref=geometry_refs.get(hess.geometry_id) if hess is not None else None,
                imag_freq_cm1=freq.imag_freq_cm1 if freq is not None else None,
                imaginary_modes=imaginary_modes.get(c.id, ()),
            ),
            input_geometry_refs=tuple(sorted(geometry_refs[link.geometry_id] for link in c.input_geometries)),
            output_geometry_refs=tuple(sorted(geometry_refs[link.geometry_id] for link in c.output_geometries)),
            scf_stability=c.scf_stability.status.value if c.scf_stability is not None else None,
            geometry_validation=(
                c.geometry_validation.validation_status.value if c.geometry_validation is not None else None
            ),
            conformer_observation_ref=(
                c.conformer_observation.public_ref
                if c.conformer_observation is not None and c.conformer_observation_id in shown_observations
                else None
            ),
            lineage=tuple(
                LineageEdge(ref_by_id[child], ref_by_id[parent], role) for child, parent, role in edges.get(c.id, [])
            ),
            lineage_truncated=c.id in truncated,
            lineage_cyclic=c.id in cyclic,
            findings=_to_finding_facts(session, related, refs_for_findings),
        )
    return out


def _imaginary_modes(session: Session, calc_ids: set[int]) -> dict[int, tuple[tuple[int, float, str | None], ...]]:
    """The stored *imaginary* modes of each frequency calculation (the rest of the spectrum is never loaded)."""
    if not calc_ids:
        return {}
    out: dict[int, list[tuple[int, float, str | None]]] = defaultdict(list)
    for mode in session.scalars(
        select(CalculationFreqMode)
        .where(CalculationFreqMode.calculation_id.in_(calc_ids), CalculationFreqMode.is_imaginary.is_(True))
        .order_by(CalculationFreqMode.calculation_id, CalculationFreqMode.mode_index)
    ):
        out[mode.calculation_id].append(
            (
                mode.mode_index,
                mode.frequency_cm1,
                mode.imaginary_disposition.value if mode.imaginary_disposition is not None else None,
            )
        )
    return {k: tuple(v) for k, v in out.items()}


def _declaration_state(raw: dict | None) -> tuple[str, dict | None]:
    from tckdb_schemas.structure_declarations import ActualProtocolDeclaration

    if raw is None:
        return "absent", None
    try:
        return "valid", ActualProtocolDeclaration.model_validate(raw).model_dump(mode="json", exclude_none=True)
    except ValueError:
        return "unreadable", None


def _geometry_refs(session: Session, calcs: list[Calculation], extra_ids: set[int]) -> dict[int, str]:
    ids = set(extra_ids)
    for c in calcs:
        ids |= {link.geometry_id for link in c.input_geometries} | {link.geometry_id for link in c.output_geometries}
        if c.hessian is not None:
            ids.add(c.hessian.geometry_id)
    if not ids:
        return {}
    return dict(session.execute(select(Geometry.id, Geometry.public_ref).where(Geometry.id.in_(ids))).tuples().all())


def load_population(session: Session, scan: PopulationScan, request: StructureRequest) -> LoadedPopulation:
    """Load and normalise the scanned population."""
    subject = load_subject(session, scan.grain, scan.entry_id)
    loaded = LoadedPopulation(subject=subject)
    if not scan.unit_ids:
        return loaded
    if scan.grain is Grain.calculation:
        calcs = list(
            session.scalars(select(Calculation).where(Calculation.id.in_(scan.unit_ids)).options(*_CALC_OPTIONS).order_by(Calculation.id)).all()
        )
        geometry_refs = _geometry_refs(session, calcs, set())
        geometry_ids = set(geometry_refs)
        findings = _finding_facts(session, geometry_ids=geometry_ids, calc_ids={c.id for c in calcs}, determination_ids=set())
        loaded.calculations = _normalize_calculations(
            session,
            calcs,
            scan.unit_status,
            dict.fromkeys((c.id for c in calcs), subject.entry_ref),
            request=request,
            with_lineage=False,
            findings=findings,
            geometry_refs=geometry_refs,
            profile=scan.profile_statuses,
        )
        return loaded
    _load_determinations(session, scan, request, subject, loaded)
    return loaded


def _load_determinations(
    session: Session, scan: PopulationScan, request: StructureRequest, subject: StructureSubject, loaded: LoadedPopulation
) -> None:
    dets = list(
        session.scalars(
            select(StructureDetermination)
            .where(StructureDetermination.id.in_(scan.unit_ids))
            .options(selectinload(StructureDetermination.sources))
            .order_by(StructureDetermination.id)
        ).all()
    )
    source_rows = [s for d in dets for s in d.sources]
    source_calc_ids = {s.calculation_id for s in source_rows}
    # Review of every pinned calculation, so each role reads available or unavailable by what the request may see.
    badges = fetch_review_badges(session, record_type=SubmissionRecordType.calculation, record_ids=source_calc_ids)
    calcs = list(
        session.scalars(select(Calculation).where(Calculation.id.in_(source_calc_ids)).options(*_CALC_OPTIONS).order_by(Calculation.id)).all()
    ) if source_calc_ids else []
    by_id = {c.id: c for c in calcs}
    usable_status: dict[int, str | None] = {}
    for cid in source_calc_ids:
        status = badges[cid].status
        calc = by_id[cid]
        if status not in scan.profile_statuses:
            usable_status[cid] = REASON_EVIDENCE_UNAVAILABLE
        elif status in _TERMINAL:
            usable_status[cid] = REASON_SOURCE_TERMINAL
        elif status not in scan.effective_statuses:
            usable_status[cid] = REASON_SOURCE_BELOW_FLOOR
        elif calc.quality not in request.quality_set:
            usable_status[cid] = REASON_SOURCE_QUALITY
        else:
            usable_status[cid] = None
    usable_calcs = [by_id[c] for c, reason in usable_status.items() if reason is None]
    geom_ids_extra = {d.evaluated_geometry_id for d in dets} | {s.geometry_id for s in source_rows if s.geometry_id is not None}
    geometry_refs = _geometry_refs(session, usable_calcs, geom_ids_extra)
    findings = _finding_facts(
        session,
        geometry_ids=set(geometry_refs),
        calc_ids={c.id for c in usable_calcs},
        determination_ids={d.id for d in dets},
    )
    owner_ref = subject.entry_ref
    loaded.calculations = _normalize_calculations(
        session,
        usable_calcs,
        {c.id: badges[c.id].status for c in usable_calcs},
        dict.fromkeys((c.id for c in usable_calcs), owner_ref),
        request=request,
        with_lineage=True,
        findings=findings,
        geometry_refs=geometry_refs,
        profile=scan.profile_statuses,
    )
    calc_ref = {c.id: c.public_ref for c in usable_calcs}

    observation_ids = {o for o in scan.observation_of.values() if o is not None}
    observations = (
        {o.id: o for o in session.scalars(select(ConformerObservation).where(ConformerObservation.id.in_(observation_ids)))}
        if observation_ids
        else {}
    )
    group_refs: dict[int, str] = {}
    if observations:
        from app.db.models.species import ConformerGroup

        group_refs = dict(
            session.execute(
                select(ConformerGroup.id, ConformerGroup.public_ref).where(
                    ConformerGroup.id.in_({o.conformer_group_id for o in observations.values()})
                )
            ).tuples().all()
        )
    evidence_rows: dict[int, list[dict]] = defaultdict(list)
    if scan.grain is Grain.transition_state:
        evidence = list(
            session.scalars(
                select(TransitionStateValidationEvidence).where(
                    TransitionStateValidationEvidence.transition_state_entry_id == scan.entry_id
                )
            )
        )
        # The calculation an evidence row reconstructs is named only when the profile shows it; a hidden one reads
        # exactly like an absent one, so a hidden IRC calculation neither appears nor certifies connectivity.
        evidence_calc_ids = {ev.reconstruction_calculation_id for ev in evidence if ev.reconstruction_calculation_id is not None}
        outside = evidence_calc_ids - calc_ref.keys()
        outside_badges = fetch_review_badges(session, record_type=SubmissionRecordType.calculation, record_ids=outside)
        outside_refs = (
            dict(
                session.execute(
                    select(Calculation.id, Calculation.public_ref).where(
                        Calculation.id.in_([c for c in outside if outside_badges[c].status in scan.profile_statuses])
                    )
                ).tuples().all()
            )
            if outside
            else {}
        )
        # The entry's own saddle geometry is content the visible entry owns, so its ref is not a hidden record's.
        evidence_geometry_refs = (
            dict(
                session.execute(
                    select(Geometry.id, Geometry.public_ref).where(
                        Geometry.id.in_({ev.transition_state_geometry_id for ev in evidence if ev.transition_state_geometry_id})
                    )
                ).tuples().all()
            )
            if evidence
            else {}
        )
        for ev in evidence:
            evidence_rows[scan.entry_id].append(
                {
                    "kind": ev.kind,
                    "passed": ev.passed,
                    "calculation_ref": (
                        calc_ref.get(ev.reconstruction_calculation_id) or outside_refs.get(ev.reconstruction_calculation_id)
                        if ev.reconstruction_calculation_id is not None
                        else None
                    ),
                    "geometry_ref": (
                        geometry_refs.get(ev.transition_state_geometry_id) or evidence_geometry_refs.get(ev.transition_state_geometry_id)
                        if ev.transition_state_geometry_id is not None
                        else None
                    ),
                }
            )
    for rank, d in enumerate(dets, start=1):
        sources = tuple(
            NormalizedSource(
                role=s.role.value,
                geometry_ref=geometry_refs.get(s.geometry_id) if s.geometry_id is not None else None,
                calculation_ref=calc_ref.get(s.calculation_id) if usable_status[s.calculation_id] is None else None,
                unavailable_reason=usable_status[s.calculation_id],
            )
            for s in sorted(d.sources, key=lambda x: x.id)
        )
        related = list(findings.get(("determination", d.id), []))
        related.extend(findings.get(("geometry", d.evaluated_geometry_id), []))
        for s in d.sources:
            if usable_status[s.calculation_id] is None:
                related.extend(findings.get(("calculation", s.calculation_id), []))
        seen_ids: set[int] = set()
        unique = []
        for f in related:
            if f.id not in seen_ids:
                unique.append(f)
                seen_ids.add(f.id)
        for fl in list(findings.values()):
            for f in fl:
                if f.supersedes_finding_id in seen_ids and f.id not in seen_ids:
                    unique.append(f)
                    seen_ids.add(f.id)
        refs_for = {("determination", d.id): d.public_ref, ("geometry", d.evaluated_geometry_id): geometry_refs.get(d.evaluated_geometry_id, "")}
        refs_for.update({("calculation", cid): calc_ref[cid] for cid in calc_ref})
        refs_for.update({("geometry", g): r for g, r in geometry_refs.items()})
        obs = observations.get(d.conformer_observation_id) if d.conformer_observation_id is not None else None
        loaded.determinations[d.id] = NormalizedDetermination(
            determination_ref=d.public_ref,
            target_kind=d.target_kind.value,
            quantity=d.quantity.value if d.quantity is not None else None,
            energy_convention=d.energy_convention,
            actual_recipe=d.actual_recipe,
            key=d.determination_key,
            owner_ref=owner_ref,
            owner_kind=subject.kind,
            conformer_observation_ref=obs.public_ref if obs is not None else None,
            conformer_group_ref=group_refs.get(obs.conformer_group_id) if obs is not None else None,
            evaluated_geometry_ref=geometry_refs.get(d.evaluated_geometry_id) or _geometry_ref_of(session, d.evaluated_geometry_id) or "",
            review_status=scan.unit_status[d.id],
            created_at=d.created_at,
            id_rank=rank,
            entry_kind=subject.stationary_point_kind,
            sources=sources,
            findings=_to_finding_facts(session, unique, refs_for),
            validation_evidence=tuple(evidence_rows.get(scan.entry_id, ())),
        )


def _geometry_ref_of(session: Session, geometry_id: int | None) -> str | None:
    if geometry_id is None:
        return None
    return session.scalar(select(Geometry.public_ref).where(Geometry.id == geometry_id))


#: Public names for the two finding loaders, for consumers outside this package (``source_findings``).
findings_by_subject = _finding_facts
to_finding_facts = _to_finding_facts
