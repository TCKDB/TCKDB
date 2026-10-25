"""The statmech candidate loader: a consistent, normalised snapshot of one target's authorized population.

Two steps, so a population that is too large is refused before anything heavy is loaded:

1. :func:`scan_population` reads only ids and review states of the target's statmech records, applies the effective
   review floor and the terminal (rejected, deprecated) exclusion, counts what is left against the bound and counts the
   nested rows it will need. Over a bound it raises a coded 422; it never returns a prefix.
2. :func:`load_population` loads that population with its declarations, members, sources, torsions, levels and the
   calculations they cite, and normalises each candidate into :mod:`~app.services.statmech_selection.facts`.

The never-borrow rule governs what is read. A candidate's facts are what it links: its own columns, its own declaration,
its own sources. Nothing is taken from a sibling record of the same entry, and a source that is hidden, absent or unusable
reads as unavailable without a ref. Visibility: a record the read profile hides is dropped without trace, from the
candidates, the counts, the exclusion listing and any member or source it would have been.

A declared model and its record are one candidate (the declaration pins what the record is); an undeclared record is a
legacy candidate read as it is. Neither is ever given a membership, a scale application or a postprocessing setting it does
not state.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.error_contract import CodedValueError
from app.api.errors import NotFoundError, not_found
from app.db.models.calculation import Calculation
from app.db.models.common import RecordReviewStatus, StatmechTreatmentKind, SubmissionRecordType
from app.db.models.energy_correction import FrequencyScaleFactor
from app.db.models.geometry import Geometry
from app.db.models.literature import Literature
from app.db.models.species import ConformerGroup, Species, SpeciesEntry
from app.db.models.statmech import Statmech, StatmechElectronicLevel, StatmechSourceCalculation, StatmechTorsion
from app.db.models.statmech_declaration import StatmechModelDeclaration, StatmechModelMember, StatmechModelSource
from app.db.models.structure_determination import StructureDetermination, StructureDeterminationSource
from app.db.models.transition_state import TransitionStateEntry
from app.schemas.reads.scientific_selection_common import ELIGIBILITY_ENTRY_STATUS
from app.services.monatomic import subject_atom_count
from app.services.scientific_read.common import fetch_review_badges, visible_statuses
from app.services.scientific_read.ref_guard import unavailable_refs
from app.services.scientific_read.supersession import fetch_supersession_notices
from app.services.selection_filters import filter_by_provenance
from app.services.selection_kernel.requested import review_default
from app.services.statmech_selection.bounds import (
    check_candidates,
    check_nested_rows,
    traversal_too_deep,
    unsupported,
)
from app.services.statmech_selection.facts import (
    CandidateFacts,
    DeclarationFacts,
    DeterminationFacts,
    MemberFacts,
    RecordFacts,
    ScaleFactorFacts,
    SourceFacts,
    SubjectFacts,
    TorsionFacts,
)
from app.services.statmech_selection.inventory import load_legacy_facts
from app.services.statmech_selection.models import StatmechFilters, StatmechRequest
from app.services.statmech_selection.vocabulary import CandidateKind, OwnerKind, PopulationKind
from app.services.structure_selection.bounds import CODE_TRAVERSAL_TOO_DEEP as STRUCTURE_TRAVERSAL_TOO_DEEP
from app.services.structure_selection.loader import (
    _CALC_OPTIONS,
    _count_nested,
    _geometry_refs,
    _normalize_calculations,
    findings_by_subject,
)
from app.services.structure_selection.models import (
    Grain,
    Intent,
    NormalizedCalculation,
    StructureRequest,
)
from app.services.structure_selection.models import (
    SelectionBounds as StructureBounds,
)

CODE_UNKNOWN_CANDIDATE = "unknown_statmech_candidate_ref"
#: The exclusion listing is bounded; the total is always reported.
MAX_EXCLUDED_LISTED = 100
REASON_TERMINAL = "terminal_review_status"
REASON_BELOW_FLOOR = "below_review_floor"
REASON_OWNER_BELOW_FLOOR = "owner_below_review_floor"

_TERMINAL = frozenset({RecordReviewStatus.rejected, RecordReviewStatus.deprecated})


@dataclass(frozen=True)
class PopulationScan:
    """Ids and review states of one target's candidates, partitioned before anything is loaded."""

    owner_kind: OwnerKind
    owner_id: int
    effective_statuses: frozenset[RecordReviewStatus]
    profile_statuses: frozenset[RecordReviewStatus]
    statmech_ids: tuple[int, ...]
    status: dict[int, RecordReviewStatus]
    excluded: tuple[tuple[int, RecordReviewStatus | None, str], ...]
    total: int
    nested_rows: int
    #: What a candidate's own review status may be: ``effective_statuses`` plus the terminal statuses the request lifted.
    candidate_statuses: frozenset[RecordReviewStatus] = frozenset()
    #: Rows the read profile shows, before any step; those the explicit population and the filters removed; and those a
    #: not-lifted review default kept out, by default name.
    considered: int = 0
    removed_by_filters: int = 0
    removed_by_floor: int = 0
    terminal_excluded: dict[str, int] = field(default_factory=dict)


@dataclass
class LoadedPopulation:
    subject: SubjectFacts
    candidates: dict[int, CandidateFacts] = field(default_factory=dict)
    calculations: dict[int, NormalizedCalculation] = field(default_factory=dict)

    @property
    def calculations_by_ref(self) -> dict[str, NormalizedCalculation]:
        return {c.calculation_ref: c for c in self.calculations.values()}


def _statuses(request: StatmechRequest) -> tuple[frozenset[RecordReviewStatus], frozenset[RecordReviewStatus]]:
    effective = frozenset(
        visible_statuses(min_review_status=request.min_review_status, include_rejected=False, include_deprecated=False)
    )
    # What the read profile itself shows. A record outside this set is not part of this request's world at all.
    profile = frozenset(visible_statuses(min_review_status=None, include_rejected=True, include_deprecated=True))
    return effective, profile


def candidate_statuses(request: StatmechRequest) -> frozenset[RecordReviewStatus]:
    """What a candidate's own review status may be: the caller's floor over the profile's, plus the lifted terminal statuses.

    Under a read profile with a review floor ``visible_statuses`` ignores the lift: ``curated`` cannot be overridden.
    """
    e = request.eligibility
    return frozenset(
        visible_statuses(
            min_review_status=request.min_review_status,
            include_rejected=e.include_rejected,
            include_deprecated=e.include_deprecated,
        )
    )


def apply_filters(session: Session, ids: list[int], filters: StatmechFilters) -> list[int]:
    """The ids the caller's filters keep: the statmech search's matching, through each record's source calculations."""
    if not ids or not filters.active:
        return ids
    keep = set(ids)
    if filters.model_kind is not None:
        keep = set(
            session.scalars(
                select(Statmech.id).where(
                    Statmech.id.in_(keep), Statmech.statmech_treatment == StatmechTreatmentKind(filters.model_kind)
                )
            )
        )
    provenance = {k: v for k, v in asdict(filters).items() if k != "model_kind"}
    if keep and any(v is not None for v in provenance.values()):
        links: dict[int, set[int]] = {i: set() for i in keep}
        for statmech_id, calculation_id in session.execute(
            select(StatmechSourceCalculation.statmech_id, StatmechSourceCalculation.calculation_id).where(
                StatmechSourceCalculation.statmech_id.in_(keep)
            )
        ):
            links[statmech_id].add(calculation_id)
        keep = filter_by_provenance(session, links, **provenance)
    return [i for i in ids if i in keep]


def _owner_model(kind: OwnerKind) -> type:
    return TransitionStateEntry if kind is OwnerKind.transition_state_entry else SpeciesEntry


def _owner_record_type(kind: OwnerKind) -> SubmissionRecordType:
    return (
        SubmissionRecordType.transition_state_entry
        if kind is OwnerKind.transition_state_entry
        else SubmissionRecordType.species_entry
    )


def entry_visible(session: Session, kind: OwnerKind, owner_id: int, *, profile: frozenset[RecordReviewStatus]) -> bool:
    """Whether the read profile shows this entry (an entry that does not exist is not shown)."""
    if session.get(_owner_model(kind), owner_id) is None:
        return False
    if kind is OwnerKind.species_entry:
        return True  # a scoping parent: its own review status never hides what is under it
    return fetch_review_badges(session, record_type=_owner_record_type(kind), record_ids=[owner_id])[owner_id].status in profile


def assert_subject_visible(
    session: Session, kind: OwnerKind, owner_id: int, *, profile: frozenset[RecordReviewStatus], ref: str | None = None
) -> None:
    """A target the read profile hides behaves exactly like one that does not exist: the same ``handle_not_found``.

    ``ref`` is the public ref the caller supplied; it is echoed back, as an unknown ref's 404 echoes it, so the two
    bodies are identical.
    """
    if not entry_visible(session, kind, owner_id, profile=profile):
        raise not_found(kind.value, ref=ref, code="handle_not_found", row_id=None if ref else owner_id)


def _owner_column(kind: OwnerKind):
    return Statmech.transition_state_entry_id if kind is OwnerKind.transition_state_entry else Statmech.species_entry_id


def _calc_ids_of(session: Session, statmech_ids: Collection[int]) -> set[int]:
    """Every calculation a population's records cite: legacy links, declared sources and torsion scans."""
    ids = list(statmech_ids)
    if not ids:
        return set()
    found: set[int] = set()
    found.update(
        session.scalars(select(StatmechSourceCalculation.calculation_id).where(StatmechSourceCalculation.statmech_id.in_(ids)))
    )
    found.update(
        c
        for c in session.scalars(
            select(StatmechTorsion.source_scan_calculation_id).where(
                StatmechTorsion.statmech_id.in_(ids), StatmechTorsion.source_scan_calculation_id.is_not(None)
            )
        )
        if c is not None
    )
    found.update(
        c
        for c in session.scalars(
            select(StatmechModelSource.calculation_id)
            .join(StatmechModelDeclaration, StatmechModelDeclaration.id == StatmechModelSource.declaration_id)
            .where(StatmechModelDeclaration.statmech_id.in_(ids), StatmechModelSource.calculation_id.is_not(None))
        )
        if c is not None
    )
    return found


def _nested_rows(session: Session, statmech_ids: Collection[int], profile: frozenset[RecordReviewStatus]) -> int:
    """Rows the population's necessary evidence will load, counted by aggregate queries before anything is loaded.

    Calculation rows count only for calculations the read profile shows, so a hidden record adds nothing and neither the
    result nor a refusal reveals that one exists.
    """
    ids = list(statmech_ids)
    if not ids:
        return 0

    def count(stmt) -> int:
        return int(session.scalar(stmt) or 0)

    total = count(select(func.count()).select_from(StatmechTorsion).where(StatmechTorsion.statmech_id.in_(ids)))
    total += count(select(func.count()).select_from(StatmechElectronicLevel).where(StatmechElectronicLevel.statmech_id.in_(ids)))
    total += count(select(func.count()).select_from(StatmechSourceCalculation).where(StatmechSourceCalculation.statmech_id.in_(ids)))
    total += count(select(func.count()).select_from(StatmechModelDeclaration).where(StatmechModelDeclaration.statmech_id.in_(ids)))
    declared = select(StatmechModelDeclaration.id).where(StatmechModelDeclaration.statmech_id.in_(ids))
    total += count(select(func.count()).select_from(StatmechModelMember).where(StatmechModelMember.declaration_id.in_(declared)))
    total += count(select(func.count()).select_from(StatmechModelSource).where(StatmechModelSource.declaration_id.in_(declared)))
    # The recipe's own arrays (scale contributions, projected and excluded modes, rotors and the modes each replaces) are
    # nested rows too. Only the candidate count has been bounded so far, so reading one JSON object per declaration is bounded.
    for recipe in session.scalars(select(StatmechModelDeclaration.recipe).where(StatmechModelDeclaration.statmech_id.in_(ids))):
        total += _recipe_array_items(recipe)
    calc_ids = _calc_ids_of(session, ids)
    if calc_ids:
        badges = fetch_review_badges(session, record_type=SubmissionRecordType.calculation, record_ids=calc_ids)
        visible = {c for c in calc_ids if badges[c].status in profile}
        total += _count_nested(session, visible, set())
    return total


def _recipe_array_items(recipe: Any) -> int:
    """Items in the recipe's arrays: contributions, projected and excluded modes, rotors and each rotor's replaced modes."""
    if not isinstance(recipe, dict):
        return 0
    total = 0
    for key in ("scale_contributions", "projected_mode_indices", "excluded_mode_indices"):
        items = recipe.get(key)
        total += len(items) if isinstance(items, list) else 0
    rotors = recipe.get("rotors")
    if isinstance(rotors, list):
        total += len(rotors)
        for rotor in rotors:
            replaced = rotor.get("replaces_mode_indices") if isinstance(rotor, dict) else None
            total += len(replaced) if isinstance(replaced, list) else 0
    return total


def scan_population(session: Session, *, request: StatmechRequest, owner_id: int) -> PopulationScan:
    """Partition the target's candidates by review state; load nothing else.

    :raises NotFoundError: the target does not exist or the profile hides it (the two are not told apart), or an explicit
        candidate names nothing in this target's authorized population (unknown, another target's and hidden read alike).
    :raises CodedValueError: a bound exceeded, or a population this release does not evaluate.
    """
    kind = request.target.owner_kind
    effective, profile = _statuses(request)
    own = candidate_statuses(request)
    assert_subject_visible(session, kind, owner_id, profile=profile, ref=request.target.owner_ref)
    # A species entry is a scoping parent and never excludes; a transition-state entry is a gated kind and still does.
    entry_gates = kind is OwnerKind.transition_state_entry
    owner_status = fetch_review_badges(session, record_type=_owner_record_type(kind), record_ids=[owner_id])[owner_id].status
    if request.population.kind is PopulationKind.release:
        raise unsupported("release_population", "a pinned release population is not evaluated by this release")

    rows = session.execute(
        select(Statmech.id, Statmech.public_ref).where(_owner_column(kind) == owner_id).order_by(Statmech.id)
    ).all()
    ids = [r.id for r in rows]
    refs = {r.id: r.public_ref for r in rows}
    declaration_ref = dict(
        session.execute(
            select(StatmechModelDeclaration.statmech_id, StatmechModelDeclaration.public_ref).where(
                StatmechModelDeclaration.statmech_id.in_(ids)
            )
        ).tuples().all()
    ) if ids else {}
    badges = fetch_review_badges(session, record_type=SubmissionRecordType.statmech, record_ids=ids)
    ids = [i for i in ids if badges[i].status in profile]
    considered = len(ids)

    if request.population.kind is PopulationKind.model_revisions:
        by_candidate = {declaration_ref.get(i, refs[i]): i for i in ids}
        wanted = list(request.population.model_refs)
        missing = [w for w in wanted if w not in by_candidate]
        if missing:
            # One response for a ref that names nothing, another target's candidate, a hidden one, or a record whose
            # candidate is its declaration: the refusal must not tell the caller which.
            raise NotFoundError(
                f"model_refs names no candidate in this target's authorized population ({missing[0]!r}).",
                code=CODE_UNKNOWN_CANDIDATE,
                context={"field": "population.model_refs", "ref": missing[0]},
            )
        chosen = {by_candidate[w] for w in wanted}
        ids = [i for i in ids if i in chosen]

    status = {i: badges[i].status for i in ids}
    kept = apply_filters(session, ids, request.filters)
    population: list[int] = []
    excluded: list[tuple[int, RecordReviewStatus | None, str]] = []
    terminal_excluded: dict[str, int] = {}
    below_floor = 0
    for i in kept:
        default = review_default(status[i])
        if default is not None and not request.eligibility.lifts(default):
            excluded.append((i, status[i], REASON_TERMINAL))
            terminal_excluded[default] = terminal_excluded.get(default, 0) + 1
        elif status[i] not in own:
            excluded.append((i, status[i], REASON_BELOW_FLOOR))
            below_floor += 1
        elif entry_gates and (owner_status in _TERMINAL or owner_status not in effective):
            excluded.append((i, owner_status, REASON_OWNER_BELOW_FLOOR))
            terminal_excluded[ELIGIBILITY_ENTRY_STATUS] = terminal_excluded.get(ELIGIBILITY_ENTRY_STATUS, 0) + 1
        else:
            population.append(i)
    check_candidates(request.bounds, len(population))
    nested = _nested_rows(session, population, profile)
    check_nested_rows(request.bounds, nested)
    return PopulationScan(
        owner_kind=kind,
        owner_id=owner_id,
        effective_statuses=effective,
        profile_statuses=profile,
        statmech_ids=tuple(population),
        status=status,
        excluded=tuple(excluded),
        total=len(ids),
        nested_rows=nested,
        candidate_statuses=own,
        considered=considered,
        removed_by_filters=considered - len(kept),
        removed_by_floor=below_floor,
        terminal_excluded=terminal_excluded,
    )


def load_subject(session: Session, kind: OwnerKind, owner_id: int) -> SubjectFacts:
    """The entry every candidate belongs to, with the identity facts a claim is judged against."""
    if kind is OwnerKind.transition_state_entry:
        ts = session.get(TransitionStateEntry, owner_id)
        if ts is None:
            raise not_found("transition_state_entry", row_id=owner_id)
        return SubjectFacts(kind, ts.public_ref, None, None, None, ts.charge, ts.multiplicity, False)
    entry = session.get(SpeciesEntry, owner_id)
    if entry is None:
        raise not_found("species_entry", row_id=owner_id)
    species = session.get(Species, entry.species_id)
    count = subject_atom_count(smiles=species.smiles) if species is not None else None
    return SubjectFacts(
        kind,
        entry.public_ref,
        entry.kind.value,
        entry.electronic_state_kind.value,
        entry.isotope_key,
        species.charge if species is not None else None,
        species.multiplicity if species is not None else None,
        None if count is None else count == 1,
    )


def _scale_factor(fsf: FrequencyScaleFactor | None) -> ScaleFactorFacts | None:
    return None if fsf is None else ScaleFactorFacts(fsf.public_ref, fsf.scale_kind.value, fsf.value)


def _refs(session: Session, model: Any, ids: Collection[int]) -> dict[int, str]:
    if not ids:
        return {}
    return dict(session.execute(select(model.id, model.public_ref).where(model.id.in_(list(ids)))).tuples().all())


def _determination_ties(session: Session, statmech_ids: Collection[int]) -> dict[int, set[int]]:
    """The structure determinations that pin one of each record's own source calculations (a legacy TS record's tie)."""
    ties: dict[int, set[int]] = defaultdict(set)
    ids = list(statmech_ids)
    if not ids:
        return ties
    for sid, det in session.execute(
        select(StatmechSourceCalculation.statmech_id, StructureDeterminationSource.determination_id)
        .join(
            StructureDeterminationSource,
            StructureDeterminationSource.calculation_id == StatmechSourceCalculation.calculation_id,
        )
        .where(StatmechSourceCalculation.statmech_id.in_(ids))
    ):
        ties[sid].add(det)
    return ties


def load_population(session: Session, scan: PopulationScan, request: StatmechRequest) -> LoadedPopulation:
    """Load and normalise the scanned population."""
    subject = load_subject(session, scan.owner_kind, scan.owner_id)
    loaded = LoadedPopulation(subject=subject)
    if not scan.statmech_ids:
        return loaded
    rows = list(
        session.scalars(
            select(Statmech)
            .where(Statmech.id.in_(scan.statmech_ids))
            .options(
                selectinload(Statmech.torsions),
                selectinload(Statmech.electronic_levels),
                selectinload(Statmech.source_calculations),
                selectinload(Statmech.frequency_scale_factor),
                selectinload(Statmech.model_declaration).selectinload(StatmechModelDeclaration.members),
                selectinload(Statmech.model_declaration).selectinload(StatmechModelDeclaration.sources),
            )
            .order_by(Statmech.id)
        ).all()
    )
    ids = [r.id for r in rows]
    profile = scan.profile_statuses

    # --- the calculations, once, at their own review states -------------------------------------------------------
    calc_ids = _calc_ids_of(session, ids)
    badges = (
        fetch_review_badges(session, record_type=SubmissionRecordType.calculation, record_ids=calc_ids) if calc_ids else {}
    )
    visible_calcs = {c for c in calc_ids if badges[c].status in profile}
    calcs = (
        list(session.scalars(select(Calculation).where(Calculation.id.in_(visible_calcs)).options(*_CALC_OPTIONS).order_by(Calculation.id)))
        if visible_calcs
        else []
    )
    geometry_refs = _geometry_refs(session, calcs, set())
    findings = findings_by_subject(session, geometry_ids=set(geometry_refs), calc_ids={c.id for c in calcs}, determination_ids=set())
    shim = StructureRequest(
        grain=Grain.calculation,
        intent=Intent.recorded_minimum,
        bounds=StructureBounds(dependency_depth=request.bounds.dependency_depth),
    )
    owners = dict.fromkeys((c.id for c in calcs), subject.entry_ref)
    try:
        normalized = _normalize_calculations(
            session,
            calcs,
            {c.id: badges[c.id].status for c in calcs},
            owners,
            request=shim,
            with_lineage=True,
            findings=findings,
            geometry_refs=geometry_refs,
            profile=profile,
        )
    except CodedValueError as refusal:
        # The shared walk reports its own bound; this service answers in its own code.
        if refusal.code == STRUCTURE_TRAVERSAL_TOO_DEEP:
            raise traversal_too_deep(request.bounds) from refusal
        raise
    # A traversal that stops at the bound with parents unexplored is not a complete closure.
    if any(c.lineage_truncated for c in normalized.values()):
        raise traversal_too_deep(request.bounds)
    loaded.calculations = normalized
    calc_ref_by_id = {cid: n.calculation_ref for cid, n in normalized.items()}

    # --- who the candidate's calculations are anchored to ----------------------------------------------------------
    observation_group = {
        c.id: c.conformer_observation.conformer_group_id for c in calcs if c.conformer_observation is not None
    }
    group_refs = _refs(session, ConformerGroup, set(observation_group.values()))

    # --- declarations: members' records, scale factors, groups, determinations -----------------------------------
    declarations = [r.model_declaration for r in rows if r.model_declaration is not None]
    member_statmech_ids = {m.member_statmech_id for d in declarations for m in d.members}
    member_badges = (
        fetch_review_badges(session, record_type=SubmissionRecordType.statmech, record_ids=member_statmech_ids)
        if member_statmech_ids
        else {}
    )
    member_refs = _refs(session, Statmech, {i for i in member_statmech_ids if member_badges[i].status in profile})
    declared_group_ids = {d.conformer_group_id for d in declarations if d.conformer_group_id is not None} | {
        m.conformer_group_id for d in declarations for m in d.members if m.conformer_group_id is not None
    }
    group_refs.update(_refs(session, ConformerGroup, declared_group_ids - set(group_refs)))
    # Hidden == unknown. A group the reader cannot open is not evidence of anything: a record anchored only in it reads as
    # unanchored and a member declared in it reads as stating no group (as a reference to nothing does). The group a
    # declaration is ABOUT is its target, not evidence; that one stays and is a placeholder token.
    unavailable_groups = unavailable_refs(session, set(group_refs.values()))
    fsf_ids = {s.frequency_scale_factor_id for d in declarations for s in d.sources if s.frequency_scale_factor_id is not None}
    fsfs = {f.id: f for f in session.scalars(select(FrequencyScaleFactor).where(FrequencyScaleFactor.id.in_(fsf_ids)))} if fsf_ids else {}
    literature_ids = {s.literature_id for d in declarations for s in d.sources if s.literature_id is not None}
    literature_refs = _refs(session, Literature, literature_ids)
    identified_literature = (
        {
            lit_id
            for lit_id, doi, isbn in session.execute(
                select(Literature.id, Literature.doi, Literature.isbn).where(Literature.id.in_(literature_ids))
            )
            if (doi or "").strip() or (isbn or "").strip()
        }
        if literature_ids
        else set()
    )
    geometry_ref_by_id = _refs(session, Geometry, {s.geometry_id for d in declarations for s in d.sources if s.geometry_id is not None})
    ties = _determination_ties(session, ids) if scan.owner_kind is OwnerKind.transition_state_entry else {}
    determination_ids = {d.structure_determination_id for d in declarations if d.structure_determination_id is not None}
    for tie in ties.values():
        if len(tie) == 1:
            determination_ids |= tie
    determinations = (
        {
            d.id: d
            for d in session.scalars(select(StructureDetermination).where(StructureDetermination.id.in_(determination_ids)))
        }
        if determination_ids
        else {}
    )
    owner_status = fetch_review_badges(
        session, record_type=_owner_record_type(scan.owner_kind), record_ids=[scan.owner_id]
    )[scan.owner_id].status
    legacy = load_legacy_facts(session, ids)
    notices = fetch_supersession_notices(session, record_type=SubmissionRecordType.statmech, record_ids=ids)
    torsion_scan_refs = _refs(session, Calculation, {t.source_scan_calculation_id for r in rows for t in r.torsions if t.source_scan_calculation_id is not None and t.source_scan_calculation_id in visible_calcs})

    def det_facts(det_id: int | None) -> DeterminationFacts | None:
        if det_id is None or det_id not in determinations:
            return None
        d = determinations[det_id]
        geometry_ref = _refs(session, Geometry, {d.evaluated_geometry_id}).get(d.evaluated_geometry_id, "")
        return DeterminationFacts(d.public_ref, subject.entry_ref, d.target_kind.value, geometry_ref, owner_status)

    for rank, r in enumerate(rows, start=1):
        decl_row = r.model_declaration
        anchors = sorted(
            {
                group_refs[observation_group[cid]]
                for _, cid in ((s.role, s.calculation_id) for s in r.source_calculations)
                if cid in observation_group
                and observation_group[cid] in group_refs
                and group_refs[observation_group[cid]] not in unavailable_groups
            }
        )
        record = RecordFacts(
            statmech_ref=r.public_ref,
            scientific_origin=r.scientific_origin.value,
            rigid_rotor_kind=r.rigid_rotor_kind.value if r.rigid_rotor_kind else None,
            is_linear=r.is_linear,
            n_rotational_constants=sum(
                v is not None for v in (r.rotational_constant_a_cm1, r.rotational_constant_b_cm1, r.rotational_constant_c_cm1)
            ),
            external_symmetry=r.external_symmetry,
            optical_isomers=r.optical_isomers,
            statmech_treatment=r.statmech_treatment.value if r.statmech_treatment else None,
            uses_projected_frequencies=r.uses_projected_frequencies,
            scale_factor=_scale_factor(r.frequency_scale_factor),
            electronic_levels=tuple((lvl.level_index, lvl.energy_cm1, lvl.degeneracy) for lvl in sorted(r.electronic_levels, key=lambda x: x.level_index)),
            torsions=tuple(
                TorsionFacts(
                    t.torsion_index,
                    t.treatment_kind.value if t.treatment_kind else None,
                    t.dimension,
                    t.symmetry_number,
                    t.invalidated_reason,
                    torsion_scan_refs.get(t.source_scan_calculation_id) if t.source_scan_calculation_id else None,
                )
                for t in sorted(r.torsions, key=lambda x: x.torsion_index)
            ),
            source_links=tuple(
                sorted((s.role.value, calc_ref_by_id[s.calculation_id]) for s in r.source_calculations if s.calculation_id in calc_ref_by_id)
            ),
        )
        declaration: DeclarationFacts | None = None
        if decl_row is not None:
            members = tuple(
                MemberFacts(
                    member_key=m.member_key,
                    statmech_ref=member_refs.get(m.member_statmech_id),
                    conformer_group_ref=(
                        group_refs.get(m.conformer_group_id)
                        if m.conformer_group_id and group_refs.get(m.conformer_group_id) not in unavailable_groups
                        else None
                    ),
                    counting_multiplicity=m.counting_multiplicity,
                    relative_energy_kj_mol=m.relative_energy_kj_mol,
                    relative_energy_convention=(
                        m.relative_energy_convention.value if m.relative_energy_convention else None
                    ),
                    region_key=m.region_key,
                    review_status=member_badges[m.member_statmech_id].status if m.member_statmech_id in member_refs else None,
                )
                for m in sorted(decl_row.members, key=lambda x: x.member_key)
            )
            sources = tuple(
                SourceFacts(
                    source_key=s.source_key,
                    role=s.role.value,
                    member_key=s.member_key,
                    locator="calculation" if s.calculation_id else "literature" if s.literature_id else "scale_factor",
                    calculation_ref=calc_ref_by_id.get(s.calculation_id) if s.calculation_id else None,
                    geometry_ref=geometry_ref_by_id.get(s.geometry_id) if s.geometry_id else None,
                    literature_ref=literature_refs.get(s.literature_id) if s.literature_id else None,
                    literature_identified=(s.literature_id in identified_literature) if s.literature_id else None,
                    scale_factor=_scale_factor(fsfs.get(s.frequency_scale_factor_id)) if s.frequency_scale_factor_id else None,
                    unavailable_reason=(
                        "required_evidence_unavailable"
                        if s.calculation_id and s.calculation_id not in calc_ref_by_id
                        else None
                    ),
                )
                for s in sorted(decl_row.sources, key=lambda x: x.source_key)
            )
            declaration = DeclarationFacts(
                ref=decl_row.public_ref,
                schema_version=decl_row.schema_version,
                scope_kind=decl_row.scope_kind.value,
                conformer_group_ref=group_refs.get(decl_row.conformer_group_id) if decl_row.conformer_group_id else None,
                determination_ref=determinations[decl_row.structure_determination_id].public_ref
                if decl_row.structure_determination_id in determinations
                else None,
                recipe=dict(decl_row.recipe),
                content_hash=decl_row.content_hash,
                members=members,
                sources=sources,
            )
        tie = ties.get(r.id, set())
        determination = det_facts(
            decl_row.structure_determination_id if decl_row is not None else (next(iter(tie)) if len(tie) == 1 else None)
        )
        notice = notices.get(r.id)
        candidate_ref = decl_row.public_ref if decl_row is not None else r.public_ref
        loaded.candidates[r.id] = CandidateFacts(
            candidate_ref=candidate_ref,
            kind=CandidateKind.declared_model if decl_row is not None else CandidateKind.legacy_record,
            review_status=scan.status[r.id],
            created_at=r.created_at if isinstance(r.created_at, datetime) else datetime.fromisoformat(str(r.created_at)),
            id_rank=rank,
            owner_ref=subject.entry_ref,
            record=record,
            declaration=declaration,
            legacy=None if decl_row is not None else legacy.get(r.id),
            anchor_group_refs=tuple(anchors),
            determination=determination,
            supersession=notice.model_dump(mode="json") if notice is not None else None,
        )
    return loaded
