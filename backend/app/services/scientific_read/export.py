"""Bulk-export selection + closure core (docs/specs/bulk_export_design.md).

This module is the shared engine behind both export targets (native
NDJSON and CHEMKIN). It is deliberately HTTP-free so it can be unit
tested directly against a ``Session``.

Responsibilities (spec §4):

1. **Seed resolution** — turn a :class:`SeedSelection` (explicit reaction
   refs, explicit species refs, a ``reaction_family`` filter, or the
   ``all`` escape hatch) into a set of ``reaction_entry`` ids and
   standalone ``species_entry`` ids.
2. **Closure** — pull in every participant ``species_entry`` of every
   selected reaction entry, so a mechanism is never emitted with a
   dangling species.
3. **Singular selection** — for each record kind (thermo, transport,
   kinetics) apply the same read-time selection policy the scientific
   read API uses (``collapse=first`` → one value; ``collapse=all`` →
   every qualifying candidate), reusing the shared trust/visibility and
   ``simple_selection_sort_key`` helpers rather than reinventing them.
4. **Trust filter + gaps** — drop records below ``min_review_status``
   (default: approved-class). A species/reaction left with no qualifying
   value is recorded as a *gap*, never silently dropped, so a consumer
   knows the mechanism is incomplete.

The result is an in-memory :class:`ExportRecordSet` (used by the CHEMKIN
serializer, which needs the whole mechanism at once) plus a streaming
generator, :func:`iter_export_ndjson`, that yields one JSON object per
line without materializing the full mechanism.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.common import (
    ReactionRole,
    RecordReviewStatus,
    SubmissionRecordType,
    ThermoModelKind,
)
from app.db.models.kinetics import Kinetics
from app.db.models.reaction import (
    ChemReaction,
    ReactionEntry,
    ReactionEntryStructureParticipant,
    ReactionFamily,
)
from app.db.models.species import Species, SpeciesEntry
from app.db.models.statmech import Statmech
from app.db.models.thermo import (
    Thermo,
    ThermoNASA,
    ThermoNASA9Interval,
    ThermoPoint,
    ThermoWilhoit,
)
from app.db.models.transport import Transport
from app.schemas.reads.scientific_common import (
    CollapseMode,
    SelectionPolicy,
    simple_selection_sort_key,
)
from app.services.scientific_read.common import (
    fetch_review_badges,
    visible_statuses,
)

#: Schema tag stamped on every export so a re-ingester can branch on format.
EXPORT_SCHEMA = "tckdb.export.v0"

#: Hard cap on ``all`` exports (guards the "no silent bulk scan" posture).
DEFAULT_ALL_CAP = 50_000

#: Default trust floor. "approved-class" per the spec — only records whose
#: review status is at or above ``approved`` are eligible; a lower value is
#: reported as a gap, not exported.
DEFAULT_MIN_REVIEW_STATUS = RecordReviewStatus.approved


# ---------------------------------------------------------------------------
# Seed + result value objects
# ---------------------------------------------------------------------------


@dataclass
class SeedSelection:
    """A bulk-export seed (spec §4). At least one field must be truthy.

    ``reaction_refs``   explicit ``reaction_entry`` or ``chem_reaction``
                        public refs (a chem-reaction ref expands to all of
                        its entries).
    ``species_refs``    explicit ``species_entry`` public refs, for a
                        species-only export.
    ``reaction_family`` a canonical reaction-family name filter.
    ``all_reactions``   export every reaction entry (curator-gated, capped).
    """

    reaction_refs: list[str] | None = None
    species_refs: list[str] | None = None
    reaction_family: str | None = None
    all_reactions: bool = False

    def is_empty(self) -> bool:
        return not (
            self.reaction_refs
            or self.species_refs
            or self.reaction_family
            or self.all_reactions
        )

    def to_manifest(self) -> dict:
        return {
            "reaction_refs": list(self.reaction_refs or []),
            "species_refs": list(self.species_refs or []),
            "reaction_family": self.reaction_family,
            "all_reactions": bool(self.all_reactions),
        }


@dataclass
class ExportGap:
    """A record dropped from the export for lack of a qualifying value."""

    kind: str  # thermo | transport | kinetics | thermo_nasa | composition
    ref: str  # public ref of the species_entry / reaction_entry
    detail: str

    def to_dict(self) -> dict:
        return {"kind": self.kind, "ref": self.ref, "detail": self.detail}


@dataclass
class SelectedThermo:
    thermo: Thermo
    nasa: ThermoNASA | None
    points: list[ThermoPoint]
    model_kind: str  # nasa | nasa9 | wilhoit | points | scalar
    review_status: RecordReviewStatus
    nasa9_intervals: list[ThermoNASA9Interval] = field(default_factory=list)
    wilhoit: ThermoWilhoit | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "thermo_ref": self.thermo.public_ref,
            "review_status": self.review_status.value,
            "scientific_origin": self.thermo.scientific_origin.value,
            "model_kind": self.model_kind,
            "h298_kj_mol": self.thermo.h298_kj_mol,
            "s298_j_mol_k": self.thermo.s298_j_mol_k,
            "nasa": None,
            "nasa9": None,
            "wilhoit": None,
            "points": None,
        }
        if self.nasa is not None:
            d["nasa"] = {
                "t_low": self.nasa.t_low,
                "t_mid": self.nasa.t_mid,
                "t_high": self.nasa.t_high,
                # TCKDB convention (tckdb_schemas.thermo + read serialization):
                # a1..a7 = LOW-temperature coefficients, b1..b7 = HIGH.
                "high_coefficients": [
                    self.nasa.b1, self.nasa.b2, self.nasa.b3, self.nasa.b4,
                    self.nasa.b5, self.nasa.b6, self.nasa.b7,
                ],
                "low_coefficients": [
                    self.nasa.a1, self.nasa.a2, self.nasa.a3, self.nasa.a4,
                    self.nasa.a5, self.nasa.a6, self.nasa.a7,
                ],
            }
        if self.nasa9_intervals:
            d["nasa9"] = [
                {
                    "interval_index": iv.interval_index,
                    "t_min_k": iv.t_min_k,
                    "t_max_k": iv.t_max_k,
                    "a1": iv.a1, "a2": iv.a2, "a3": iv.a3,
                    "a4": iv.a4, "a5": iv.a5, "a6": iv.a6,
                    "a7": iv.a7, "a8": iv.a8, "a9": iv.a9,
                }
                for iv in self.nasa9_intervals
            ]
        if self.wilhoit is not None:
            w = self.wilhoit
            d["wilhoit"] = {
                "cp0_j_mol_k": w.cp0_j_mol_k,
                "cp_inf_j_mol_k": w.cp_inf_j_mol_k,
                "b_k": w.b_k,
                "a0": w.a0,
                "a1": w.a1,
                "a2": w.a2,
                "a3": w.a3,
                "h0_kj_mol": w.h0_kj_mol,
                "s0_j_mol_k": w.s0_j_mol_k,
            }
        if self.points:
            d["points"] = [
                {
                    "temperature_k": p.temperature_k,
                    "cp_j_mol_k": p.cp_j_mol_k,
                    "h_kj_mol": p.h_kj_mol,
                    "s_j_mol_k": p.s_j_mol_k,
                    "g_kj_mol": p.g_kj_mol,
                }
                for p in self.points
            ]
        return d


@dataclass
class SelectedTransport:
    transport: Transport
    review_status: RecordReviewStatus

    def to_dict(self) -> dict:
        t = self.transport
        return {
            "transport_ref": t.public_ref,
            "review_status": self.review_status.value,
            "scientific_origin": t.scientific_origin.value,
            "sigma_angstrom": t.sigma_angstrom,
            "epsilon_over_k_k": t.epsilon_over_k_k,
            "dipole_debye": t.dipole_debye,
            "polarizability_angstrom3": t.polarizability_angstrom3,
            "rotational_relaxation": t.rotational_relaxation,
        }


@dataclass
class SelectedKinetics:
    kinetics: Kinetics
    review_status: RecordReviewStatus

    def to_dict(self) -> dict:
        k = self.kinetics
        return {
            "kinetics_ref": k.public_ref,
            "review_status": self.review_status.value,
            "scientific_origin": k.scientific_origin.value,
            "model_kind": k.model_kind.value,
            "a": k.a,
            "a_units": k.a_units.value if k.a_units is not None else None,
            "n": k.n,
            "ea_kj_mol": k.ea_kj_mol,
            "tmin_k": k.tmin_k,
            "tmax_k": k.tmax_k,
            "degeneracy": k.degeneracy,
        }


@dataclass
class SpeciesExportRecord:
    species_entry: SpeciesEntry
    species: Species
    is_linear: bool | None
    thermos: list[SelectedThermo] = field(default_factory=list)
    transports: list[SelectedTransport] = field(default_factory=list)

    def to_ndjson(self) -> dict:
        se = self.species_entry
        sp = self.species
        return {
            "record_type": "species",
            "species_ref": sp.public_ref,
            "species_entry_ref": se.public_ref,
            "smiles": sp.smiles,
            "inchi_key": sp.inchi_key,
            "charge": sp.charge,
            "multiplicity": sp.multiplicity,
            "is_linear": self.is_linear,
            "thermos": [t.to_dict() for t in self.thermos],
            "transports": [t.to_dict() for t in self.transports],
        }


@dataclass
class ReactionExportRecord:
    reaction_entry: ReactionEntry
    reaction: ChemReaction
    reaction_family: str | None
    reactant_refs: list[str]
    product_refs: list[str]
    kinetics: list[SelectedKinetics] = field(default_factory=list)

    def to_ndjson(self) -> dict:
        return {
            "record_type": "reaction",
            "reaction_ref": self.reaction.public_ref,
            "reaction_entry_ref": self.reaction_entry.public_ref,
            "reversible": self.reaction.reversible,
            "reaction_family": self.reaction_family,
            "reactants": list(self.reactant_refs),
            "products": list(self.product_refs),
            "kinetics": [k.to_dict() for k in self.kinetics],
        }


@dataclass
class ExportRecordSet:
    seed: SeedSelection
    min_review_status: RecordReviewStatus | None
    collapse: CollapseMode
    selection_policy: SelectionPolicy
    generated_at: datetime
    species_records: list[SpeciesExportRecord] = field(default_factory=list)
    reaction_records: list[ReactionExportRecord] = field(default_factory=list)
    gaps: list[ExportGap] = field(default_factory=list)

    def manifest(self) -> dict:
        return {
            "record_type": "manifest",
            "schema": EXPORT_SCHEMA,
            "generated_at": self.generated_at.isoformat(),
            "seed": self.seed.to_manifest(),
            "collapse": self.collapse.value,
            "selection_policy": self.selection_policy.value,
            "min_review_status": (
                self.min_review_status.value
                if self.min_review_status is not None
                else None
            ),
            "counts": {
                "species": len(self.species_records),
                "reactions": len(self.reaction_records),
                "gaps": len(self.gaps),
            },
            "gaps": [g.to_dict() for g in self.gaps],
        }

    def iter_ndjson_lines(self) -> Iterator[str]:
        """Yield JSON strings (no trailing newline) for a materialized set.

        Order: manifest, then species, then reactions. Deterministic for a
        fixed snapshot (records are ordered by id at build time).
        """
        yield _dumps(self.manifest())
        for sr in self.species_records:
            yield _dumps(sr.to_ndjson())
        for rr in self.reaction_records:
            yield _dumps(rr.to_ndjson())


def _dumps(obj: dict) -> str:
    # ``sort_keys`` gives byte-stable output for the same data snapshot.
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Seed resolution + closure
# ---------------------------------------------------------------------------


def resolve_seed(
    session: Session, seed: SeedSelection, *, all_cap: int
) -> tuple[list[int], list[int]]:
    """Resolve a seed to ``(reaction_entry_ids, standalone_species_entry_ids)``.

    :raises ValueError: 422 when a ref does not resolve or the ``all`` cap
        is exceeded.
    """
    if seed.is_empty():
        raise ValueError(
            "export_seed_empty: supply reaction_refs, species_refs, "
            "reaction_family, or all_reactions"
        )

    reaction_entry_ids: set[int] = set()
    species_entry_ids: set[int] = set()

    for ref in seed.reaction_refs or []:
        re_id = session.scalar(
            select(ReactionEntry.id).where(ReactionEntry.public_ref == ref)
        )
        if re_id is not None:
            reaction_entry_ids.add(re_id)
            continue
        cr_id = session.scalar(
            select(ChemReaction.id).where(ChemReaction.public_ref == ref)
        )
        if cr_id is not None:
            entry_ids = session.scalars(
                select(ReactionEntry.id).where(ReactionEntry.reaction_id == cr_id)
            ).all()
            reaction_entry_ids.update(entry_ids)
            continue
        raise ValueError(f"export_seed_unresolved: reaction ref not found: {ref!r}")

    if seed.reaction_family:
        family_entry_ids = session.scalars(
            select(ReactionEntry.id)
            .join(ChemReaction, ChemReaction.id == ReactionEntry.reaction_id)
            .join(
                ReactionFamily,
                ReactionFamily.id == ChemReaction.reaction_family_id,
            )
            .where(ReactionFamily.name == seed.reaction_family)
        ).all()
        reaction_entry_ids.update(family_entry_ids)

    if seed.all_reactions:
        all_ids = session.scalars(select(ReactionEntry.id)).all()
        if len(all_ids) > all_cap:
            raise ValueError(
                "export_all_cap_exceeded: refusing to export "
                f"{len(all_ids)} reaction entries (cap {all_cap})"
            )
        reaction_entry_ids.update(all_ids)

    for ref in seed.species_refs or []:
        se_id = session.scalar(
            select(SpeciesEntry.id).where(SpeciesEntry.public_ref == ref)
        )
        if se_id is None:
            raise ValueError(
                f"export_seed_unresolved: species ref not found: {ref!r}"
            )
        species_entry_ids.add(se_id)

    return sorted(reaction_entry_ids), sorted(species_entry_ids)


def _closure_species_entry_ids(
    session: Session, reaction_entry_ids: list[int]
) -> dict[int, list[ReactionEntryStructureParticipant]]:
    """Return ``{reaction_entry_id: [structure participants]}``.

    The set of participant ``species_entry`` ids across all values is the
    closure that must accompany the exported reactions.
    """
    if not reaction_entry_ids:
        return {}
    rows = session.scalars(
        select(ReactionEntryStructureParticipant)
        .where(
            ReactionEntryStructureParticipant.reaction_entry_id.in_(
                reaction_entry_ids
            )
        )
        .order_by(
            ReactionEntryStructureParticipant.reaction_entry_id,
            ReactionEntryStructureParticipant.role,
            ReactionEntryStructureParticipant.participant_index,
        )
    ).all()
    grouped: dict[int, list[ReactionEntryStructureParticipant]] = {
        re_id: [] for re_id in reaction_entry_ids
    }
    for row in rows:
        grouped[row.reaction_entry_id].append(row)
    return grouped


# ---------------------------------------------------------------------------
# Per-record selection helpers (reuse the read-time policy)
# ---------------------------------------------------------------------------


def select_candidate_ids(
    session: Session,
    *,
    model,
    parent_column,
    parent_id: int,
    record_type: SubmissionRecordType,
    min_review_status: RecordReviewStatus | None,
    collapse: CollapseMode,
    selection_policy: SelectionPolicy,
) -> tuple[list[int], dict[int, RecordReviewStatus]]:
    """Return the ranked, trust-filtered candidate ids for one parent.

    Mirrors ``species_transport.get_species_transport``: fetch candidate
    ids + created_at, filter by visibility, rank with
    ``simple_selection_sort_key``, then keep one (``collapse=first``) or
    all (``collapse=all``). The default trust posture never surfaces
    rejected/deprecated here — export is a clean-by-default artifact.
    """
    rows = session.execute(
        select(model.id, model.created_at).where(parent_column == parent_id)
    ).all()
    if not rows:
        return [], {}
    created_at = {r.id: r.created_at for r in rows}
    ids = [r.id for r in rows]
    badges = fetch_review_badges(
        session, record_type=record_type, record_ids=ids
    )
    visible = visible_statuses(
        min_review_status=min_review_status,
        include_rejected=False,
        include_deprecated=False,
    )
    visible_ids = [i for i in ids if badges[i].status in visible]
    if not visible_ids:
        return [], {}
    review_by = {i: badges[i].status for i in visible_ids}
    ranked = sorted(
        visible_ids,
        key=lambda i: simple_selection_sort_key(
            i,
            policy=selection_policy,
            review_status_by_id=review_by,
            created_at_by_id=created_at,
        ),
    )
    chosen = ranked[:1] if collapse is CollapseMode.first else ranked
    return chosen, {i: badges[i].status for i in chosen}


#: Stored ``thermo.model_kind`` → export ``model_kind`` vocab. Like the read
#: service (``scientific_read/thermo.py``) the stored column wins when present;
#: the legacy-NULL fallback in ``_classify_export_kind`` is a local fit-first
#: order that differs cosmetically from the read service's derivation but is
#: equivalent under the one-fit-per-record upload invariant. The export vocab
#: keeps the legacy "nasa"/"points"/"scalar" tokens and adds "nasa9"/"wilhoit".
_STORED_KIND_TO_EXPORT: dict[ThermoModelKind, str] = {
    ThermoModelKind.nasa7: "nasa",
    ThermoModelKind.nasa9: "nasa9",
    ThermoModelKind.wilhoit: "wilhoit",
    ThermoModelKind.tabulated: "points",
    ThermoModelKind.scalar: "scalar",
}


def _classify_export_kind(
    thermo: Thermo,
    *,
    has_nasa: bool,
    has_nasa9: bool,
    has_wilhoit: bool,
    has_points: bool,
) -> str:
    """Classify the export ``model_kind`` for one thermo record.

    Prefer the stored ``thermo.model_kind`` column when non-NULL (mapped to
    the export vocab). When it is NULL (legacy rows the backfill could not
    classify), derive with fit-precedence so a real fit is never lost:
    nasa9 → wilhoit → nasa → points → scalar.
    """
    if thermo.model_kind is not None:
        return _STORED_KIND_TO_EXPORT[thermo.model_kind]
    if has_nasa9:
        return "nasa9"
    if has_wilhoit:
        return "wilhoit"
    if has_nasa:
        return "nasa"
    if has_points:
        return "points"
    return "scalar"


def _load_selected_thermos(
    session: Session,
    chosen: list[int],
    status_by_id: dict[int, RecordReviewStatus],
) -> list[SelectedThermo]:
    out: list[SelectedThermo] = []
    for tid in chosen:
        thermo = session.get(Thermo, tid)
        if thermo is None:  # pragma: no cover - race with delete
            continue
        nasa = session.get(ThermoNASA, tid)
        nasa9_intervals = list(
            session.scalars(
                select(ThermoNASA9Interval)
                .where(ThermoNASA9Interval.thermo_id == tid)
                .order_by(ThermoNASA9Interval.interval_index)
            ).all()
        )
        wilhoit = session.get(ThermoWilhoit, tid)
        points = list(
            session.scalars(
                select(ThermoPoint).where(ThermoPoint.thermo_id == tid)
            ).all()
        )
        kind = _classify_export_kind(
            thermo,
            has_nasa=nasa is not None,
            has_nasa9=bool(nasa9_intervals),
            has_wilhoit=wilhoit is not None,
            has_points=bool(points),
        )
        out.append(
            SelectedThermo(
                thermo=thermo,
                nasa=nasa,
                points=points,
                nasa9_intervals=nasa9_intervals,
                wilhoit=wilhoit,
                model_kind=kind,
                review_status=status_by_id[tid],
            )
        )
    return out


def _entry_is_linear(session: Session, species_entry_id: int) -> bool | None:
    """Best-effort linearity from any statmech row for the entry.

    Used for the CHEMKIN transport geometry index. Deterministic (lowest
    statmech id with a non-null ``is_linear``); ``None`` if no statmech
    states it, letting the serializer fall back to structure inference.
    """
    rows = session.execute(
        select(Statmech.id, Statmech.is_linear)
        .where(Statmech.species_entry_id == species_entry_id)
        .order_by(Statmech.id)
    ).all()
    for _sid, is_linear in rows:
        if is_linear is not None:
            return is_linear
    return None


def _build_species_record(
    session: Session,
    species_entry_id: int,
    *,
    min_review_status: RecordReviewStatus | None,
    collapse: CollapseMode,
    selection_policy: SelectionPolicy,
) -> tuple[SpeciesExportRecord | None, list[ExportGap]]:
    entry = session.get(SpeciesEntry, species_entry_id)
    if entry is None:  # pragma: no cover - race with delete
        return None, []
    species = session.get(Species, entry.species_id)
    if species is None:  # pragma: no cover - FK guarantees presence
        return None, []

    gaps: list[ExportGap] = []

    thermo_ids, thermo_status = select_candidate_ids(
        session,
        model=Thermo,
        parent_column=Thermo.species_entry_id,
        parent_id=species_entry_id,
        record_type=SubmissionRecordType.thermo,
        min_review_status=min_review_status,
        collapse=collapse,
        selection_policy=selection_policy,
    )
    thermos = _load_selected_thermos(session, thermo_ids, thermo_status)
    if not thermos:
        gaps.append(
            ExportGap(
                kind="thermo",
                ref=entry.public_ref,
                detail="no qualifying thermo record at or above min_review_status",
            )
        )

    transport_ids, transport_status = select_candidate_ids(
        session,
        model=Transport,
        parent_column=Transport.species_entry_id,
        parent_id=species_entry_id,
        record_type=SubmissionRecordType.transport,
        min_review_status=min_review_status,
        collapse=collapse,
        selection_policy=selection_policy,
    )
    transports = [
        SelectedTransport(
            transport=session.get(Transport, tid),
            review_status=transport_status[tid],
        )
        for tid in transport_ids
        if session.get(Transport, tid) is not None
    ]

    record = SpeciesExportRecord(
        species_entry=entry,
        species=species,
        is_linear=_entry_is_linear(session, species_entry_id),
        thermos=thermos,
        transports=transports,
    )
    return record, gaps


def _build_reaction_record(
    session: Session,
    reaction_entry_id: int,
    participants: list[ReactionEntryStructureParticipant],
    *,
    min_review_status: RecordReviewStatus | None,
    collapse: CollapseMode,
    selection_policy: SelectionPolicy,
    species_entry_ref_by_id: dict[int, str],
) -> tuple[ReactionExportRecord | None, list[ExportGap]]:
    entry = session.get(ReactionEntry, reaction_entry_id)
    if entry is None:  # pragma: no cover - race with delete
        return None, []
    reaction = session.get(ChemReaction, entry.reaction_id)
    family = None
    if reaction is not None and reaction.reaction_family_id is not None:
        family = session.scalar(
            select(ReactionFamily.name).where(
                ReactionFamily.id == reaction.reaction_family_id
            )
        )

    reactant_refs: list[str] = []
    product_refs: list[str] = []
    for p in participants:
        ref = species_entry_ref_by_id.get(p.species_entry_id)
        if ref is None:
            continue
        if p.role is ReactionRole.reactant:
            reactant_refs.append(ref)
        else:
            product_refs.append(ref)

    gaps: list[ExportGap] = []
    kin_ids, kin_status = select_candidate_ids(
        session,
        model=Kinetics,
        parent_column=Kinetics.reaction_entry_id,
        parent_id=reaction_entry_id,
        record_type=SubmissionRecordType.kinetics,
        min_review_status=min_review_status,
        collapse=collapse,
        selection_policy=selection_policy,
    )
    kinetics: list[SelectedKinetics] = []
    for kid in kin_ids:
        k = session.get(Kinetics, kid)
        if k is None:  # pragma: no cover - race with delete
            continue
        kinetics.append(SelectedKinetics(kinetics=k, review_status=kin_status[kid]))
    if not kinetics:
        gaps.append(
            ExportGap(
                kind="kinetics",
                ref=entry.public_ref,
                detail="no qualifying kinetics record at or above min_review_status",
            )
        )

    record = ReactionExportRecord(
        reaction_entry=entry,
        reaction=reaction,
        reaction_family=family,
        reactant_refs=reactant_refs,
        product_refs=product_refs,
        kinetics=kinetics,
    )
    return record, gaps


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def build_export_record_set(
    session: Session,
    *,
    seed: SeedSelection,
    min_review_status: RecordReviewStatus | None = DEFAULT_MIN_REVIEW_STATUS,
    collapse: CollapseMode = CollapseMode.first,
    selection_policy: SelectionPolicy = SelectionPolicy.default,
    all_cap: int = DEFAULT_ALL_CAP,
) -> ExportRecordSet:
    """Build the full in-memory export record set (M1).

    Resolves the seed, computes the species closure, and runs read-time
    selection per record. Used by the CHEMKIN serializer (which needs the
    whole mechanism) and directly by the M1 unit tests.

    :raises ValueError: 422 for an empty/unresolvable seed or an ``all``
        request over the cap.
    """
    reaction_entry_ids, standalone_species_ids = resolve_seed(
        session, seed, all_cap=all_cap
    )
    participants_by_entry = _closure_species_entry_ids(session, reaction_entry_ids)

    species_entry_ids: set[int] = set(standalone_species_ids)
    for parts in participants_by_entry.values():
        for p in parts:
            species_entry_ids.add(p.species_entry_id)

    ordered_species_ids = sorted(species_entry_ids)
    species_entry_ref_by_id = {
        row.id: row.public_ref
        for row in session.execute(
            select(SpeciesEntry.id, SpeciesEntry.public_ref).where(
                SpeciesEntry.id.in_(ordered_species_ids)
            )
        ).all()
    } if ordered_species_ids else {}

    record_set = ExportRecordSet(
        seed=seed,
        min_review_status=min_review_status,
        collapse=collapse,
        selection_policy=selection_policy,
        generated_at=datetime.now(timezone.utc),
    )

    for se_id in ordered_species_ids:
        record, gaps = _build_species_record(
            session,
            se_id,
            min_review_status=min_review_status,
            collapse=collapse,
            selection_policy=selection_policy,
        )
        if record is not None:
            record_set.species_records.append(record)
        record_set.gaps.extend(gaps)

    for re_id in reaction_entry_ids:
        record, gaps = _build_reaction_record(
            session,
            re_id,
            participants_by_entry.get(re_id, []),
            min_review_status=min_review_status,
            collapse=collapse,
            selection_policy=selection_policy,
            species_entry_ref_by_id=species_entry_ref_by_id,
        )
        if record is not None:
            record_set.reaction_records.append(record)
        record_set.gaps.extend(gaps)

    return record_set


def iter_export_ndjson(
    session: Session,
    *,
    seed: SeedSelection,
    min_review_status: RecordReviewStatus | None = DEFAULT_MIN_REVIEW_STATUS,
    collapse: CollapseMode = CollapseMode.first,
    selection_policy: SelectionPolicy = SelectionPolicy.default,
    all_cap: int = DEFAULT_ALL_CAP,
) -> Iterator[str]:
    """Stream an NDJSON export (M2) without materializing the whole set.

    Emits a header ``manifest`` line (seed + policy, no gaps yet), then one
    ``species`` line and one ``reaction`` line per record computed lazily,
    then a trailing ``export_summary`` line carrying the accumulated gaps
    and counts. Each yielded string ends in a newline.

    Seed resolution (and its :class:`ValueError` for an empty/unresolvable
    seed or an over-cap ``all`` request) happens **eagerly**, before the
    generator is returned, so the route can surface a 422 before the
    streaming response has started.
    """
    reaction_entry_ids, standalone_species_ids = resolve_seed(
        session, seed, all_cap=all_cap
    )
    participants_by_entry = _closure_species_entry_ids(session, reaction_entry_ids)

    species_entry_ids: set[int] = set(standalone_species_ids)
    for parts in participants_by_entry.values():
        for p in parts:
            species_entry_ids.add(p.species_entry_id)
    ordered_species_ids = sorted(species_entry_ids)
    species_entry_ref_by_id = {
        row.id: row.public_ref
        for row in session.execute(
            select(SpeciesEntry.id, SpeciesEntry.public_ref).where(
                SpeciesEntry.id.in_(ordered_species_ids)
            )
        ).all()
    } if ordered_species_ids else {}

    return _stream_ndjson(
        session,
        reaction_entry_ids=reaction_entry_ids,
        ordered_species_ids=ordered_species_ids,
        participants_by_entry=participants_by_entry,
        species_entry_ref_by_id=species_entry_ref_by_id,
        seed=seed,
        min_review_status=min_review_status,
        collapse=collapse,
        selection_policy=selection_policy,
    )


def _stream_ndjson(
    session: Session,
    *,
    reaction_entry_ids: list[int],
    ordered_species_ids: list[int],
    participants_by_entry: dict[int, list[ReactionEntryStructureParticipant]],
    species_entry_ref_by_id: dict[int, str],
    seed: SeedSelection,
    min_review_status: RecordReviewStatus | None,
    collapse: CollapseMode,
    selection_policy: SelectionPolicy,
) -> Iterator[str]:
    generated_at = datetime.now(timezone.utc)
    header = {
        "record_type": "manifest",
        "schema": EXPORT_SCHEMA,
        "generated_at": generated_at.isoformat(),
        "seed": seed.to_manifest(),
        "collapse": collapse.value,
        "selection_policy": selection_policy.value,
        "min_review_status": (
            min_review_status.value if min_review_status is not None else None
        ),
    }
    yield _dumps(header) + "\n"

    gaps: list[ExportGap] = []
    n_species = 0
    n_reactions = 0

    for se_id in ordered_species_ids:
        record, rec_gaps = _build_species_record(
            session,
            se_id,
            min_review_status=min_review_status,
            collapse=collapse,
            selection_policy=selection_policy,
        )
        gaps.extend(rec_gaps)
        if record is not None:
            n_species += 1
            yield _dumps(record.to_ndjson()) + "\n"

    for re_id in reaction_entry_ids:
        record, rec_gaps = _build_reaction_record(
            session,
            re_id,
            participants_by_entry.get(re_id, []),
            min_review_status=min_review_status,
            collapse=collapse,
            selection_policy=selection_policy,
            species_entry_ref_by_id=species_entry_ref_by_id,
        )
        gaps.extend(rec_gaps)
        if record is not None:
            n_reactions += 1
            yield _dumps(record.to_ndjson()) + "\n"

    summary = {
        "record_type": "export_summary",
        "schema": EXPORT_SCHEMA,
        "generated_at": generated_at.isoformat(),
        "counts": {
            "species": n_species,
            "reactions": n_reactions,
            "gaps": len(gaps),
        },
        "gaps": [g.to_dict() for g in gaps],
    }
    yield _dumps(summary) + "\n"


__all__ = [
    "DEFAULT_ALL_CAP",
    "DEFAULT_MIN_REVIEW_STATUS",
    "EXPORT_SCHEMA",
    "ExportGap",
    "ExportRecordSet",
    "ReactionExportRecord",
    "SeedSelection",
    "SelectedKinetics",
    "SelectedThermo",
    "SelectedTransport",
    "SpeciesExportRecord",
    "build_export_record_set",
    "iter_export_ndjson",
    "resolve_seed",
    "select_candidate_ids",
]
