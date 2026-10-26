"""The candidate loader: a consistent, normalised snapshot of one species entry's thermo population.

Two steps, so a population that is too large is refused before anything heavy is loaded:

1. :func:`scan_population` reads only ids and review states, applies the effective review
   floor and the rejected/deprecated exclusion, and counts what is left.
2. :func:`load_population` loads that population with the same eager-load graph the evidence
   evaluation needs and normalises each record.

The never-borrow rule (#645/#646) governs what is read. A thermo record's evidence is what
it links: its own columns, its own declared energy level of theory, its own source
calculations. Nothing is taken from a sibling record of the same species entry, from an
entry-wide pick, or from the energy corrections attached to the species entry (which are not
linked to any one thermo record). Statmech-derived evidence is not folded in here either.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rdkit import Chem
from rdkit.Chem import rdMolDescriptors
from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.thermo_declarations import StoredThermoProtocolDeclaration

from app.api.errors import not_found
from app.chemistry.composite_methods import composite_method_for
from app.db.models.common import RecordReviewStatus, ScientificOriginKind, SubmissionRecordType
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.species import ConformerGroup, Species, SpeciesEntry
from app.db.models.thermo import (
    Thermo,
    ThermoNASA,
    ThermoNASA9Interval,
    ThermoPoint,
    ThermoSourceCalculation,
    ThermoWilhoit,
)
from app.services.scientific_read.common import fetch_review_badges, visible_statuses
from app.services.scientific_read.ref_guard import unavailable_refs
from app.services.scientific_read.thermo import THERMO_TRUST_EAGER_LOADS, _classify_model_kind
from app.services.selection_filters import filter_by_provenance, resolve_level_of_theory_ref
from app.services.selection_kernel.requested import review_default
from app.services.thermo_selection.models import H298Request, NormalizedCandidate, Subject, ThermoFilters

REASON_TERMINAL = "terminal_review_status"
REASON_BELOW_FLOOR = "below_review_floor"

_TERMINAL = frozenset({RecordReviewStatus.rejected, RecordReviewStatus.deprecated})


@dataclass(frozen=True)
class PopulationScan:
    """Ids and review states of one entry's thermo rows, partitioned before anything is loaded.

    ``considered`` counts the rows the read profile shows; ``removed_by_filters`` those the caller's filters (and own
    review floor) removed; ``terminal_excluded`` those a not-lifted review default removed, by default name.
    """

    species_entry_id: int
    effective_statuses: frozenset[RecordReviewStatus]
    population_ids: tuple[int, ...]
    review_status: dict[int, RecordReviewStatus]
    excluded: tuple[tuple[int, RecordReviewStatus, str], ...]
    total_rows: int
    cap: int
    considered: int = 0
    removed_by_filters: int = 0
    removed_by_floor: int = 0
    terminal_excluded: dict[str, int] = field(default_factory=dict)

    @property
    def over_cap(self) -> bool:
        return len(self.population_ids) > self.cap


@dataclass
class LoadedPopulation:
    """The loaded, normalised population."""

    entry: SpeciesEntry
    subject: Subject
    rows: list[Thermo]
    candidates: dict[int, NormalizedCandidate] = field(default_factory=dict)
    #: Calculation refs the profile hides (or that name no row): evidence that reads as unavailable (see ``unavailable_evidence``).
    unavailable: frozenset[str] = frozenset()


def molecular_formula(smiles: str) -> str | None:
    """Hill formula of a SMILES, or ``None`` if RDKit cannot read it."""
    mol = Chem.MolFromSmiles(smiles)
    return rdMolDescriptors.CalcMolFormula(mol) if mol is not None else None


def model_kinds(session: Session, ids: list[int]) -> dict[int, str]:
    """The model form of each thermo row, by the browse's own classification (stored kind, else the child rows present)."""
    rows = list(session.scalars(select(Thermo).where(Thermo.id.in_(ids))))
    has_nasa = set(session.scalars(select(ThermoNASA.thermo_id).where(ThermoNASA.thermo_id.in_(ids))))
    has_nasa9 = set(session.scalars(select(ThermoNASA9Interval.thermo_id).where(ThermoNASA9Interval.thermo_id.in_(ids))))
    has_wilhoit = set(session.scalars(select(ThermoWilhoit.thermo_id).where(ThermoWilhoit.thermo_id.in_(ids))))
    has_points = set(session.scalars(select(ThermoPoint.thermo_id).where(ThermoPoint.thermo_id.in_(ids))))
    return {
        t.id: _classify_model_kind(
            t,
            has_nasa=t.id in has_nasa,
            has_nasa9=t.id in has_nasa9,
            has_wilhoit=t.id in has_wilhoit,
            has_points=t.id in has_points,
        ).value
        for t in rows
    }


def apply_filters(session: Session, ids: list[int], filters: ThermoFilters) -> list[int]:
    """The ids the caller's filters keep: the per-entry thermo browse's matching, applied to the profile-visible rows."""
    if not ids or not filters.active:
        return ids
    keep = set(ids)
    if filters.model_kind is not None:
        kinds = model_kinds(session, ids)
        keep = {i for i in keep if kinds.get(i) == filters.model_kind}
    if keep and filters.scientific_origin is not None:
        keep = set(
            session.scalars(
                select(Thermo.id).where(
                    Thermo.id.in_(keep), Thermo.scientific_origin == ScientificOriginKind(filters.scientific_origin)
                )
            )
        )
    if keep and filters.has_source_constraint:
        lot_id = None
        if filters.level_of_theory_ref is not None:
            lot_id = resolve_level_of_theory_ref(session, filters.level_of_theory_ref)
            if lot_id is None:
                return []  # an unknown level of theory keeps nothing
        links: dict[int, set[int]] = {i: set() for i in keep}
        for record_id, calculation_id in session.execute(
            select(ThermoSourceCalculation.thermo_id, ThermoSourceCalculation.calculation_id).where(ThermoSourceCalculation.thermo_id.in_(keep))
        ):
            links[record_id].add(calculation_id)
        keep = filter_by_provenance(session, links, level_of_theory_id=lot_id, **filters.provenance())
    return [i for i in ids if i in keep]


def scan_population(session: Session, *, species_entry_id: int, request: H298Request) -> PopulationScan:
    """Partition the entry's thermo rows by the caller's filters and review state; load nothing else.

    In order: rows the read profile hides are not part of this request's world (never counted, never listed); the
    caller's filters narrow the rest; a rejected or deprecated row is kept out unless the request lifted that default;
    a row below the caller's own review floor is kept out; the remainder is the population. Rows kept out by review are
    listed with why, so a disclosure never silently drops one.

    :raises NotFoundError: if the species entry does not exist.
    """
    if session.get(SpeciesEntry, species_entry_id) is None:
        raise not_found("species_entry", row_id=species_entry_id)
    elig = request.eligibility
    effective = frozenset(
        visible_statuses(
            min_review_status=request.min_review_status,
            include_rejected=elig.include_rejected,
            include_deprecated=elig.include_deprecated,
        )
    )
    profile_visible = frozenset(visible_statuses(min_review_status=None, include_rejected=True, include_deprecated=True))
    all_ids = list(session.scalars(select(Thermo.id).where(Thermo.species_entry_id == species_entry_id).order_by(Thermo.id)))
    badges = fetch_review_badges(session, record_type=SubmissionRecordType.thermo, record_ids=all_ids)
    ids = [i for i in all_ids if badges[i].status in profile_visible]
    status = {i: badges[i].status for i in ids}
    kept = apply_filters(session, ids, request.filters)
    population: list[int] = []
    excluded: list[tuple[int, RecordReviewStatus, str]] = []
    terminal_excluded: dict[str, int] = {}
    below_floor = 0
    for i in kept:
        default = review_default(status[i])
        if default is not None and not elig.lifts(default):
            excluded.append((i, status[i], REASON_TERMINAL))
            terminal_excluded[default] = terminal_excluded.get(default, 0) + 1
        elif status[i] not in effective:
            excluded.append((i, status[i], REASON_BELOW_FLOOR))
            below_floor += 1
        else:
            population.append(i)
    return PopulationScan(
        species_entry_id=species_entry_id,
        effective_statuses=effective,
        population_ids=tuple(population),
        review_status=status,
        excluded=tuple(excluded),
        total_rows=len(ids),
        cap=request.max_candidates,
        considered=len(ids),
        removed_by_filters=len(ids) - len(kept),
        removed_by_floor=below_floor,
        terminal_excluded=terminal_excluded,
    )


def unavailable_evidence(session: Session, rows: list[Thermo]) -> frozenset[str]:
    """The calculations these records cite that the reader cannot open: hidden under the profile, or naming no row.

    Hidden == unknown: such evidence is treated exactly like a reference to nothing, so a hidden supporting calculation
    (a source link, a protocol citation) does not count under ``curated``. Empty, with no statement, without a review
    floor: an exploratory selection is unchanged.
    """
    refs: set[str] = set()
    for t in rows:
        refs.update(link.calculation.public_ref for link in t.source_calculations if link.calculation is not None)
        for support in (t.protocol_declaration or {}).get("supporting_calculations") or ():
            if isinstance(support, dict) and support.get("calculation_ref"):
                refs.add(support["calculation_ref"])
    return frozenset(unavailable_refs(session, refs))


def _protocol(thermo: Thermo, unavailable: frozenset[str] = frozenset()) -> tuple[str, dict | None]:
    raw = thermo.protocol_declaration
    if raw is None:
        return "absent", None
    try:
        dumped = StoredThermoProtocolDeclaration.model_validate(raw).model_dump(mode="json")
    except ValueError:
        return "unreadable", None
    if unavailable and dumped.get("supporting_calculations"):
        dumped["supporting_calculations"] = [
            s for s in dumped["supporting_calculations"] if s.get("calculation_ref") not in unavailable
        ]
    return "valid", dumped


def _linked_recipe_keys(
    thermo: Thermo, declared_lot_methods: dict[int, str], unavailable: frozenset[str] = frozenset()
) -> tuple[str, ...]:
    """Named composite methods the record's own declared energy level and own source calculations carry."""
    methods: list[str] = []
    if thermo.energy_level_of_theory_id is not None:
        method = declared_lot_methods.get(thermo.energy_level_of_theory_id)
        if method:
            methods.append(method)
    for link in thermo.source_calculations:
        if link.calculation is not None and link.calculation.public_ref in unavailable:
            continue
        lot = link.calculation.lot if link.calculation is not None else None
        if lot is not None and lot.method:
            methods.append(lot.method)
    keys = {entry.key for m in methods if (entry := composite_method_for(m)) is not None}
    return tuple(sorted(keys))


def load_subject(session: Session, species_entry_id: int) -> tuple[SpeciesEntry, Subject]:
    """The species entry and the identity facts the rules read; one entry and one species row."""
    entry = session.get(SpeciesEntry, species_entry_id)
    if entry is None:
        raise not_found("species_entry", row_id=species_entry_id)
    species = session.get(Species, entry.species_id)
    assert species is not None
    return entry, Subject(
        species_entry_ref=entry.public_ref,
        inchi_key=species.inchi_key,
        molecular_formula=molecular_formula(species.smiles),
        charge=species.charge,
        multiplicity=species.multiplicity,
        isotope_key=entry.isotope_key,
        electronic_state_kind=entry.electronic_state_kind.value,
        entry_kind=entry.kind.value,
    )


def load_population(session: Session, scan: PopulationScan) -> LoadedPopulation:
    """Load and normalise the scanned population. Refuses an over-cap scan: nothing heavy is loaded for it."""
    if scan.over_cap:
        raise ValueError("the visible population exceeds the cap; it must not be loaded")
    entry, subject = load_subject(session, scan.species_entry_id)
    if not scan.population_ids:
        return LoadedPopulation(entry=entry, subject=subject, rows=[])

    rows = list(
        session.scalars(
            select(Thermo)
            .where(Thermo.id.in_(scan.population_ids))
            .options(*THERMO_TRUST_EAGER_LOADS)
            .order_by(Thermo.id)
        ).all()
    )
    unavailable = unavailable_evidence(session, rows)
    candidates = normalize_rows(session, rows, scan.review_status, unavailable)
    return LoadedPopulation(entry=entry, subject=subject, rows=rows, candidates=candidates, unavailable=unavailable)


def normalize_rows(
    session: Session,
    rows: list[Thermo],
    review_status: dict[int, RecordReviewStatus],
    unavailable: frozenset[str] = frozenset(),
) -> dict[int, NormalizedCandidate]:
    """Normalise loaded rows (trust eager-loads required) into engine inputs; ``id_rank`` follows the given order.

    The rows must be ordered by id for ``id_rank`` to be an id ordinal.
    """
    group_ids = {t.target_conformer_group_id for t in rows if t.target_conformer_group_id is not None}
    group_refs: dict[int, str] = {}
    if group_ids:
        for group_id, group_ref in session.execute(
            select(ConformerGroup.id, ConformerGroup.public_ref).where(ConformerGroup.id.in_(group_ids))
        ):
            group_refs[group_id] = group_ref
    lot_ids = {t.energy_level_of_theory_id for t in rows if t.energy_level_of_theory_id is not None}
    declared: dict[int, str] = {}
    if lot_ids:
        for lot_id, method in session.execute(
            select(LevelOfTheory.id, LevelOfTheory.method).where(LevelOfTheory.id.in_(lot_ids))
        ):
            declared[lot_id] = method
    candidates: dict[int, NormalizedCandidate] = {}
    for rank, thermo in enumerate(rows, start=1):
        state, protocol = _protocol(thermo, unavailable)
        candidates[thermo.id] = NormalizedCandidate(
            thermo_ref=thermo.public_ref,
            review_status=review_status[thermo.id],
            created_at=thermo.created_at,
            id_rank=rank,
            scientific_origin=thermo.scientific_origin.value,
            phase=thermo.phase.value if thermo.phase is not None else None,
            enthalpy_reference_kind=(
                thermo.enthalpy_reference_kind.value if thermo.enthalpy_reference_kind is not None else None
            ),
            target_kind=(
                thermo.thermodynamic_target_kind.value if thermo.thermodynamic_target_kind is not None else None
            ),
            target_group_ref=(
                group_refs[thermo.target_conformer_group_id] if thermo.target_conformer_group_id is not None else None
            ),
            protocol_state=state,
            protocol=protocol,
            linked_recipe_keys=_linked_recipe_keys(thermo, declared, unavailable),
        )
    return candidates
