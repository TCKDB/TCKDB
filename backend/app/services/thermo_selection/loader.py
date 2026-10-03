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
from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.species import ConformerGroup, Species, SpeciesEntry
from app.db.models.thermo import Thermo
from app.services.scientific_read.common import fetch_review_badges, visible_statuses
from app.services.scientific_read.thermo import THERMO_TRUST_EAGER_LOADS
from app.services.thermo_selection.models import H298Request, NormalizedCandidate, Subject

REASON_TERMINAL = "terminal_review_status"
REASON_BELOW_FLOOR = "below_review_floor"

_TERMINAL = frozenset({RecordReviewStatus.rejected, RecordReviewStatus.deprecated})


@dataclass(frozen=True)
class PopulationScan:
    """Ids and review states of one entry's thermo rows, partitioned before anything is loaded."""

    species_entry_id: int
    effective_statuses: frozenset[RecordReviewStatus]
    population_ids: tuple[int, ...]
    review_status: dict[int, RecordReviewStatus]
    excluded: tuple[tuple[int, RecordReviewStatus, str], ...]
    total_rows: int
    cap: int

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


def molecular_formula(smiles: str) -> str | None:
    """Hill formula of a SMILES, or ``None`` if RDKit cannot read it."""
    mol = Chem.MolFromSmiles(smiles)
    return rdMolDescriptors.CalcMolFormula(mol) if mol is not None else None


def scan_population(session: Session, *, species_entry_id: int, request: H298Request) -> PopulationScan:
    """Partition the entry's thermo rows by review state; load nothing else.

    The population is every row whose review status passes the effective floor (the caller's
    ``min_review_status`` combined with the read profile's floor) and is not rejected or
    deprecated. Rows outside it are listed with why, so a disclosure never silently drops one.

    :raises NotFoundError: if the species entry does not exist.
    """
    if session.get(SpeciesEntry, species_entry_id) is None:
        raise not_found("species_entry", row_id=species_entry_id)
    effective = frozenset(
        visible_statuses(
            min_review_status=request.min_review_status, include_rejected=False, include_deprecated=False
        )
    )
    ids = list(session.scalars(select(Thermo.id).where(Thermo.species_entry_id == species_entry_id).order_by(Thermo.id)))
    badges = fetch_review_badges(session, record_type=SubmissionRecordType.thermo, record_ids=ids)
    status = {i: badges[i].status for i in ids}
    population = tuple(i for i in ids if status[i] in effective)
    excluded = tuple(
        (i, status[i], REASON_TERMINAL if status[i] in _TERMINAL else REASON_BELOW_FLOOR)
        for i in ids
        if status[i] not in effective
    )
    return PopulationScan(
        species_entry_id=species_entry_id,
        effective_statuses=effective,
        population_ids=population,
        review_status=status,
        excluded=excluded,
        total_rows=len(ids),
        cap=request.max_candidates,
    )


def _protocol(thermo: Thermo) -> tuple[str, dict | None]:
    raw = thermo.protocol_declaration
    if raw is None:
        return "absent", None
    try:
        return "valid", StoredThermoProtocolDeclaration.model_validate(raw).model_dump(mode="json")
    except ValueError:
        return "unreadable", None


def _linked_recipe_keys(thermo: Thermo, declared_lot_methods: dict[int, str]) -> tuple[str, ...]:
    """Named composite methods the record's own declared energy level and own source calculations carry."""
    methods: list[str] = []
    if thermo.energy_level_of_theory_id is not None:
        method = declared_lot_methods.get(thermo.energy_level_of_theory_id)
        if method:
            methods.append(method)
    for link in thermo.source_calculations:
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
    candidates = normalize_rows(session, rows, scan.review_status)
    return LoadedPopulation(entry=entry, subject=subject, rows=rows, candidates=candidates)


def normalize_rows(
    session: Session, rows: list[Thermo], review_status: dict[int, RecordReviewStatus]
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
        state, protocol = _protocol(thermo)
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
            linked_recipe_keys=_linked_recipe_keys(thermo, declared),
        )
    return candidates
