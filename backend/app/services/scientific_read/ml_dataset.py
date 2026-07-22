"""ML-dataset export core (the "living RDB7" surface).

TCKDB is a live, multi-level-of-theory, provenance-labelled source of
computational-chemistry data. Frozen ML datasets (RDB7, RGD1,
Transition1x, QM9, ANI-*) ship as one-shot Zenodo tarballs; this module
exposes the same *shape* of data as a streamed, filterable, trust-gated
export that always reflects the current database.

Two HTTP-free, streaming builders (kept HTTP-free so they can be unit
tested directly against a ``Session``), matching the conventions of the
sibling :mod:`app.services.scientific_read.export` module:

* :func:`iter_ml_species_ndjson` — a **species/conformer-centric** export.
  One record per ``(species_entry, distinct geometry)``: species identity
  (canonical SMILES, InChIKey, charge, multiplicity), the conformer
  geometry (elements + Cartesian coordinates), every electronic energy
  computed at that geometry with an explicit machine-readable
  level-of-theory label, harmonic frequencies where available, an optional
  Cartesian Hessian, and the species-level thermo summary
  (``h298_kj_mol`` / ``s298_j_mol_k``).

* :func:`iter_ml_reactions_ndjson` — a **reaction-centric** export,
  RDB7-compatible in spirit. One record per ``reaction_entry``: reactant /
  product SMILES, Arrhenius rate parameters with LOT labels, a best-effort
  electronic forward barrier (``E_TS − ΣE_reactants`` at a shared LOT), a
  reaction enthalpy from thermo, and the transition-state geometry +
  energy where available.

RDB7 column mapping (Grambow, Pattanaik & Green, *Sci. Data* 2020) is
documented on :class:`MLReactionRecord`.

Design choices (see the module docstring in ``export.py`` for the shared
selection/trust machinery this reuses):

* **Row grain.** Species records are per-geometry, not per-species, because
  an ML consumer wants one structure per training sample and multiple LOT
  energies can attach to the same nuclear configuration (content-addressed
  geometry dedup makes this a natural GROUP BY ``geometry_id``). Thermo is
  species-scoped and denormalised onto every geometry row of the species,
  flagged as such.
* **Trust gating.** ``min_review_status`` filters on the *identity*
  record's review badge (``species_entry`` / ``reaction_entry``), reusing
  the read API's :func:`visible_statuses` / :func:`fetch_review_badges`.
  The default is approved-class, so the export is clean-by-default. Thermo
  candidacy reuses the exact selection helpers behind the NDJSON/CHEMKIN
  export.
* **No integer PKs.** Every emitted identifier is a public ref
  (``spc_`` / ``spe_`` / ``geom_`` / ``lot_`` / ...) or a content hash,
  never a database primary key.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationFreqMode,
    CalculationFreqResult,
    CalculationHessian,
    CalculationInputGeometry,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationSPResult,
)
from app.db.models.common import (
    ReactionRole,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.geometry import Geometry, GeometryAtom
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.reaction import (
    ChemReaction,
    ReactionEntry,
    ReactionEntryStructureParticipant,
    ReactionFamily,
)
from app.db.models.species import (
    ConformerGroup,
    ConformerObservation,
    Species,
    SpeciesEntry,
)
from app.db.models.thermo import Thermo
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.schemas.reads.scientific_common import CollapseMode, SelectionPolicy
from app.services.scientific_read import export as export_core
from app.services.scientific_read.common import (
    fetch_review_badges,
    visible_statuses,
)

#: Schema tag stamped on every ML record so a consumer can branch on format.
ML_EXPORT_SCHEMA = "tckdb.ml.v0"

#: Hartree → kJ/mol (matches app/importers/cccbdb/normalizers/units.py).
HARTREE_TO_KJ_MOL = 2625.4996394798254

#: Default trust floor for the ML export: approved-class identity records.
DEFAULT_MIN_REVIEW_STATUS = RecordReviewStatus.approved

#: Hard cap on ``all`` exports, shared with the native export.
DEFAULT_ALL_CAP = export_core.DEFAULT_ALL_CAP


def _dumps(obj: dict) -> str:
    """Byte-stable single-line JSON (sorted keys, compact separators)."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


@dataclass
class MLFilters:
    """Shared read-time filters for both ML exports.

    ``min_review_status``  identity-record trust floor (default approved).
    ``lot_ref``            restrict energies to a single level of theory
                           (public ref of a ``level_of_theory`` row). When
                           set, species-geometry rows with no surviving
                           energy at that LOT are dropped.
    ``elements``           element-subset allow-list: a species-geometry row
                           is kept only when every atom's element is in this
                           set. ``None`` = no element filter.
    ``include_hessian``    emit the Cartesian Hessian block when present.
    ``limit`` / ``offset`` deterministic window over the identity ids
                           (ordered by id).
    """

    min_review_status: RecordReviewStatus | None = DEFAULT_MIN_REVIEW_STATUS
    lot_ref: str | None = None
    elements: frozenset[str] | None = None
    include_hessian: bool = False
    limit: int | None = None
    offset: int = 0

    def to_manifest(self) -> dict:
        return {
            "min_review_status": (
                self.min_review_status.value
                if self.min_review_status is not None
                else None
            ),
            "lot_ref": self.lot_ref,
            "elements": sorted(self.elements) if self.elements is not None else None,
            "include_hessian": self.include_hessian,
            "limit": self.limit,
            "offset": self.offset,
        }


# ---------------------------------------------------------------------------
# Level-of-theory label helpers
# ---------------------------------------------------------------------------


def _lot_label(lot: LevelOfTheory) -> str:
    """Build a compact, deterministic, human+machine LOT label.

    ``method/basis`` core with parenthesised annotations for the parts of
    the LOT identity that survive round-tripping (dispersion, solvent). The
    stable machine key is ``lot_hash``; this label is the readable form.
    """
    core = lot.method if not lot.basis else f"{lot.method}/{lot.basis}"
    extra: list[str] = []
    if lot.dispersion:
        extra.append(f"disp={lot.dispersion}")
    if lot.solvent:
        solvent = lot.solvent
        if lot.solvent_model:
            solvent = f"{lot.solvent_model}:{solvent}"
        extra.append(f"solvent={solvent}")
    if extra:
        core = f"{core} ({', '.join(extra)})"
    return core


def _lot_block(
    session: Session, lot_id: int | None, cache: dict[int, dict | None]
) -> dict | None:
    """Return the machine-readable LOT block for *lot_id* (memoised)."""
    if lot_id is None:
        return None
    if lot_id in cache:
        return cache[lot_id]
    lot = session.get(LevelOfTheory, lot_id)
    block: dict | None
    if lot is None:  # pragma: no cover - FK guarantees presence
        block = None
    else:
        block = {
            "level_of_theory_ref": lot.public_ref,
            "lot_hash": lot.lot_hash,
            "method": lot.method,
            "basis": lot.basis,
            "aux_basis": lot.aux_basis,
            "cabs_basis": lot.cabs_basis,
            "dispersion": lot.dispersion,
            "solvent": lot.solvent,
            "solvent_model": lot.solvent_model,
            "spin_treatment": (
                lot.spin_treatment.value if lot.spin_treatment is not None else None
            ),
            "label": _lot_label(lot),
        }
    cache[lot_id] = block
    return block


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


@dataclass
class _GeometryPayload:
    geometry_ref: str
    geom_hash: str
    natoms: int
    symbols: list[str]
    coords: list[list[float]]

    def element_set(self) -> frozenset[str]:
        return frozenset(self.symbols)

    def to_dict(self) -> dict:
        return {
            "geometry_ref": self.geometry_ref,
            "geom_hash": self.geom_hash,
            "natoms": self.natoms,
            "coordinate_unit": "angstrom",
            "symbols": list(self.symbols),
            "coords": [list(c) for c in self.coords],
        }


def _geometry_payload(
    session: Session, geometry_id: int, cache: dict[int, _GeometryPayload]
) -> _GeometryPayload | None:
    """Load a geometry's atoms into an ordered coordinate payload (memoised)."""
    if geometry_id in cache:
        return cache[geometry_id]
    geometry = session.get(Geometry, geometry_id)
    if geometry is None:  # pragma: no cover - FK guarantees presence
        return None
    atoms = session.scalars(
        select(GeometryAtom)
        .where(GeometryAtom.geometry_id == geometry_id)
        .order_by(GeometryAtom.atom_index)
    ).all()
    # GeometryAtom.element is CHAR(2): single-letter symbols are space-padded.
    symbols = [(a.element or "").strip() for a in atoms]
    payload = _GeometryPayload(
        geometry_ref=geometry.public_ref,
        geom_hash=geometry.geom_hash,
        natoms=geometry.natoms,
        symbols=symbols,
        coords=[[a.x, a.y, a.z] for a in atoms],
    )
    cache[geometry_id] = payload
    return payload


def _resolved_geometry_id(
    calc_id: int,
    output_links: dict[int, list[tuple[int, int]]],
    input_links: dict[int, list[tuple[int, int]]],
) -> int | None:
    """Pick the geometry an energy/frequency belongs to for one calculation.

    Prefer the lowest-ordered output geometry (the optimised structure),
    else the lowest-ordered input geometry (single-point / frequency runs
    carry only an input geometry). ``None`` when the calculation has no
    geometry link at all — such a calculation has no structure to anchor an
    ML sample to and is skipped.
    """
    outs = output_links.get(calc_id)
    if outs:
        return min(outs, key=lambda t: t[1])[0]
    ins = input_links.get(calc_id)
    if ins:
        return min(ins, key=lambda t: t[1])[0]
    return None


def _load_geometry_links(
    session: Session, calc_ids: list[int]
) -> tuple[dict[int, list[tuple[int, int]]], dict[int, list[tuple[int, int]]]]:
    """Bulk-load ``calc_id -> [(geometry_id, order)]`` for outputs and inputs."""
    output_links: dict[int, list[tuple[int, int]]] = {}
    input_links: dict[int, list[tuple[int, int]]] = {}
    if not calc_ids:
        return output_links, input_links
    for row in session.execute(
        select(
            CalculationOutputGeometry.calculation_id,
            CalculationOutputGeometry.geometry_id,
            CalculationOutputGeometry.output_order,
        ).where(CalculationOutputGeometry.calculation_id.in_(calc_ids))
    ).all():
        output_links.setdefault(row.calculation_id, []).append(
            (row.geometry_id, row.output_order)
        )
    for row in session.execute(
        select(
            CalculationInputGeometry.calculation_id,
            CalculationInputGeometry.geometry_id,
            CalculationInputGeometry.input_order,
        ).where(CalculationInputGeometry.calculation_id.in_(calc_ids))
    ).all():
        input_links.setdefault(row.calculation_id, []).append(
            (row.geometry_id, row.input_order)
        )
    return output_links, input_links


# ---------------------------------------------------------------------------
# Species-centric export
# ---------------------------------------------------------------------------


@dataclass
class MLEnergyRecord:
    calculation_ref: str
    calculation_type: str
    energy_type: str  # single_point | optimization
    electronic_energy_hartree: float | None
    electronic_energy_uncertainty_hartree: float | None
    level_of_theory: dict | None

    def to_dict(self) -> dict:
        return {
            "calculation_ref": self.calculation_ref,
            "calculation_type": self.calculation_type,
            "energy_type": self.energy_type,
            "electronic_energy_hartree": self.electronic_energy_hartree,
            "electronic_energy_uncertainty_hartree": (
                self.electronic_energy_uncertainty_hartree
            ),
            "level_of_theory": self.level_of_theory,
        }


@dataclass
class MLSpeciesRecord:
    species: Species
    species_entry: SpeciesEntry
    review_status: RecordReviewStatus
    geometry: _GeometryPayload
    conformer_group_refs: list[str]
    conformer_observation_refs: list[str]
    energies: list[MLEnergyRecord]
    frequencies: dict | None
    hessian: dict | None
    thermo: dict | None

    def to_ndjson(self) -> dict:
        sp = self.species
        se = self.species_entry
        return {
            "record_type": "ml_species",
            "schema": ML_EXPORT_SCHEMA,
            "species_ref": sp.public_ref,
            "species_entry_ref": se.public_ref,
            "review_status": self.review_status.value,
            "smiles": sp.smiles,
            "inchi_key": sp.inchi_key,
            "charge": sp.charge,
            "multiplicity": sp.multiplicity,
            "conformer_group_refs": list(self.conformer_group_refs),
            "conformer_observation_refs": list(self.conformer_observation_refs),
            "geometry": self.geometry.to_dict(),
            "energies": [e.to_dict() for e in self.energies],
            "frequencies": self.frequencies,
            "hessian": self.hessian,
            "thermo": self.thermo,
        }


def _resolve_species_entry_ids(
    session: Session,
    *,
    species_refs: Sequence[str] | None,
    all_species: bool,
    limit: int | None,
    offset: int,
    all_cap: int,
) -> list[int]:
    """Resolve the ML-species seed to an ordered ``species_entry`` id list.

    :raises ValueError: 422 for an empty/unresolvable seed or an ``all``
        request over the cap.
    """
    if not species_refs and not all_species:
        raise ValueError(
            "ml_export_seed_empty: supply species_refs or all_species"
        )

    ids: list[int] = []
    if species_refs:
        for ref in species_refs:
            se_id = session.scalar(
                select(SpeciesEntry.id).where(SpeciesEntry.public_ref == ref)
            )
            if se_id is None:
                raise ValueError(
                    f"ml_export_seed_unresolved: species ref not found: {ref!r}"
                )
            ids.append(se_id)

    if all_species:
        all_ids = session.scalars(
            select(SpeciesEntry.id).order_by(SpeciesEntry.id)
        ).all()
        if len(all_ids) > all_cap:
            raise ValueError(
                "ml_export_all_cap_exceeded: refusing to export "
                f"{len(all_ids)} species entries (cap {all_cap})"
            )
        ids.extend(all_ids)

    ordered = sorted(set(ids))
    if offset:
        ordered = ordered[offset:]
    if limit is not None:
        ordered = ordered[:limit]
    return ordered


def _species_entry_review(
    session: Session, species_entry_id: int
) -> RecordReviewStatus:
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.species_entry,
        record_ids=[species_entry_id],
    )
    return badges[species_entry_id].status


def _frequencies_block(
    session: Session, calculation: Calculation, lot_block: dict | None
) -> dict | None:
    """Build a frequency block from a freq calculation, if it has one."""
    freq = session.get(CalculationFreqResult, calculation.id)
    if freq is None:
        return None
    modes = session.scalars(
        select(CalculationFreqMode)
        .where(CalculationFreqMode.calculation_id == calculation.id)
        .order_by(CalculationFreqMode.mode_index)
    ).all()
    return {
        "calculation_ref": calculation.public_ref,
        "level_of_theory": lot_block,
        "n_imag": freq.n_imag,
        "imag_freq_cm1": freq.imag_freq_cm1,
        "zpe_hartree": freq.zpe_hartree,
        # Signed convention: imaginary modes are negative wavenumbers.
        "frequencies_cm1": [m.frequency_cm1 for m in modes],
    }


def _hessian_block(
    session: Session, calculation: Calculation, lot_block: dict | None
) -> dict | None:
    hessian = session.get(CalculationHessian, calculation.id)
    if hessian is None:
        return None
    return {
        "calculation_ref": calculation.public_ref,
        "level_of_theory": lot_block,
        "natoms": hessian.natoms,
        "units": "hartree/bohr^2",
        "source": hessian.source.value,
        # Packed lower triangle (with diagonal) of the symmetric 3N×3N matrix.
        "lower_triangle_hartree_bohr2": list(hessian.lower_triangle_hartree_bohr2),
    }


def _species_thermo_block(
    session: Session,
    species_entry_id: int,
    *,
    min_review_status: RecordReviewStatus | None,
) -> dict | None:
    """Species-scoped thermo summary, selected with the read-time trust policy.

    Reuses the exact candidacy machinery behind the native/CHEMKIN export so
    the trust semantics are identical (``collapse=first`` → the single
    policy-preferred approved-class thermo).
    """
    chosen, status_by_id = export_core.select_candidate_ids(
        session,
        model=Thermo,
        parent_column=Thermo.species_entry_id,
        parent_id=species_entry_id,
        record_type=SubmissionRecordType.thermo,
        min_review_status=min_review_status,
        collapse=CollapseMode.first,
        selection_policy=SelectionPolicy.default,
    )
    if not chosen:
        return None
    thermo = session.get(Thermo, chosen[0])
    if thermo is None:  # pragma: no cover - race with delete
        return None
    return {
        "thermo_ref": thermo.public_ref,
        "review_status": status_by_id[chosen[0]].value,
        "scientific_origin": thermo.scientific_origin.value,
        "h298_kj_mol": thermo.h298_kj_mol,
        "s298_j_mol_k": thermo.s298_j_mol_k,
        "h298_uncertainty_kj_mol": thermo.h298_uncertainty_kj_mol,
        "s298_uncertainty_j_mol_k": thermo.s298_uncertainty_j_mol_k,
    }


def _conformer_refs(
    session: Session, observation_ids: list[int]
) -> tuple[list[str], list[str]]:
    """Resolve conformer observation ids to (group_refs, observation_refs)."""
    if not observation_ids:
        return [], []
    rows = session.execute(
        select(
            ConformerObservation.public_ref,
            ConformerGroup.public_ref,
        )
        .join(
            ConformerGroup,
            ConformerGroup.id == ConformerObservation.conformer_group_id,
        )
        .where(ConformerObservation.id.in_(observation_ids))
        .order_by(ConformerObservation.id)
    ).all()
    observation_refs = [r[0] for r in rows]
    group_refs = sorted({r[1] for r in rows})
    return group_refs, observation_refs


def _build_species_records(
    session: Session,
    species_entry_id: int,
    *,
    filters: MLFilters,
    lot_id_filter: int | None,
    lot_cache: dict[int, dict | None],
    geom_cache: dict[int, _GeometryPayload],
) -> list[MLSpeciesRecord]:
    """Build the per-geometry ML records for one species entry."""
    entry = session.get(SpeciesEntry, species_entry_id)
    if entry is None:  # pragma: no cover - race with delete
        return []
    species = session.get(Species, entry.species_id)
    if species is None:  # pragma: no cover - FK guarantees presence
        return []
    review_status = _species_entry_review(session, species_entry_id)

    calcs = session.scalars(
        select(Calculation)
        .where(Calculation.species_entry_id == species_entry_id)
        .order_by(Calculation.id)
    ).all()
    if not calcs:
        return []

    calc_ids = [c.id for c in calcs]
    output_links, input_links = _load_geometry_links(session, calc_ids)

    # Group calculations by the geometry they were evaluated at.
    by_geometry: dict[int, list[Calculation]] = {}
    for calc in calcs:
        gid = _resolved_geometry_id(calc.id, output_links, input_links)
        if gid is None:
            continue
        by_geometry.setdefault(gid, []).append(calc)

    thermo_block = _species_thermo_block(
        session, species_entry_id, min_review_status=filters.min_review_status
    )

    records: list[MLSpeciesRecord] = []
    for geometry_id in sorted(by_geometry):
        geometry = _geometry_payload(session, geometry_id, geom_cache)
        if geometry is None:  # pragma: no cover - FK guarantees presence
            continue
        if (
            filters.elements is not None
            and not geometry.element_set() <= filters.elements
        ):
            continue

        group = by_geometry[geometry_id]
        energies: list[MLEnergyRecord] = []
        freq_block: dict | None = None
        hessian_block: dict | None = None
        conformer_obs_ids: set[int] = set()

        for calc in group:
            if calc.conformer_observation_id is not None:
                conformer_obs_ids.add(calc.conformer_observation_id)
            if lot_id_filter is not None and calc.lot_id != lot_id_filter:
                continue
            lot_block = _lot_block(session, calc.lot_id, lot_cache)

            sp = session.get(CalculationSPResult, calc.id)
            if sp is not None and sp.electronic_energy_hartree is not None:
                energies.append(
                    MLEnergyRecord(
                        calculation_ref=calc.public_ref,
                        calculation_type=calc.type.value,
                        energy_type="single_point",
                        electronic_energy_hartree=sp.electronic_energy_hartree,
                        electronic_energy_uncertainty_hartree=(
                            sp.electronic_energy_uncertainty_hartree
                        ),
                        level_of_theory=lot_block,
                    )
                )
            opt = session.get(CalculationOptResult, calc.id)
            if opt is not None and opt.final_energy_hartree is not None:
                energies.append(
                    MLEnergyRecord(
                        calculation_ref=calc.public_ref,
                        calculation_type=calc.type.value,
                        energy_type="optimization",
                        electronic_energy_hartree=opt.final_energy_hartree,
                        electronic_energy_uncertainty_hartree=None,
                        level_of_theory=lot_block,
                    )
                )
            if freq_block is None:
                freq_block = _frequencies_block(session, calc, lot_block)
            if filters.include_hessian and hessian_block is None:
                hessian_block = _hessian_block(session, calc, lot_block)

        # With a LOT filter, a geometry with no surviving energy is dropped.
        if lot_id_filter is not None and not energies:
            continue

        conformer_group_refs, conformer_observation_refs = _conformer_refs(
            session, sorted(conformer_obs_ids)
        )

        records.append(
            MLSpeciesRecord(
                species=species,
                species_entry=entry,
                review_status=review_status,
                geometry=geometry,
                conformer_group_refs=conformer_group_refs,
                conformer_observation_refs=conformer_observation_refs,
                energies=energies,
                frequencies=freq_block,
                hessian=hessian_block,
                thermo=thermo_block,
            )
        )
    return records


def iter_ml_species_ndjson(
    session: Session,
    *,
    species_refs: Sequence[str] | None = None,
    all_species: bool = False,
    filters: MLFilters | None = None,
    all_cap: int = DEFAULT_ALL_CAP,
) -> Iterator[str]:
    """Stream the species/conformer-centric ML export as NDJSON.

    Emits a ``manifest`` header line, one ``ml_species`` line per
    ``(species_entry, geometry)`` computed lazily, then an
    ``export_summary`` footer with counts. Each yielded string ends in a
    newline.

    Seed resolution (and its :class:`ValueError` for an empty / unresolvable
    seed or an over-cap ``all_species`` request) happens **eagerly**, before
    the generator is returned, so the route can surface a 422 before the
    streaming response has started.
    """
    filters = filters or MLFilters()
    species_entry_ids = _resolve_species_entry_ids(
        session,
        species_refs=species_refs,
        all_species=all_species,
        limit=filters.limit,
        offset=filters.offset,
        all_cap=all_cap,
    )
    lot_id_filter = _resolve_lot_ref(session, filters.lot_ref)
    visible = visible_statuses(
        min_review_status=filters.min_review_status,
        include_rejected=False,
        include_deprecated=False,
    )
    return _stream_species(
        session,
        species_entry_ids=species_entry_ids,
        filters=filters,
        lot_id_filter=lot_id_filter,
        visible=visible,
    )


def _stream_species(
    session: Session,
    *,
    species_entry_ids: list[int],
    filters: MLFilters,
    lot_id_filter: int | None,
    visible: set[RecordReviewStatus],
) -> Iterator[str]:
    generated_at = datetime.now(timezone.utc)
    yield _dumps(
        {
            "record_type": "manifest",
            "schema": ML_EXPORT_SCHEMA,
            "dataset": "species",
            "generated_at": generated_at.isoformat(),
            "filters": filters.to_manifest(),
            "seed_species_entry_count": len(species_entry_ids),
        }
    ) + "\n"

    lot_cache: dict[int, dict | None] = {}
    geom_cache: dict[int, _GeometryPayload] = {}
    n_species = 0
    n_skipped_untrusted = 0

    for se_id in species_entry_ids:
        if _species_entry_review(session, se_id) not in visible:
            n_skipped_untrusted += 1
            continue
        records = _build_species_records(
            session,
            se_id,
            filters=filters,
            lot_id_filter=lot_id_filter,
            lot_cache=lot_cache,
            geom_cache=geom_cache,
        )
        for record in records:
            n_species += 1
            yield _dumps(record.to_ndjson()) + "\n"

    yield _dumps(
        {
            "record_type": "export_summary",
            "schema": ML_EXPORT_SCHEMA,
            "dataset": "species",
            "generated_at": generated_at.isoformat(),
            "counts": {
                "records": n_species,
                "skipped_below_min_review_status": n_skipped_untrusted,
            },
        }
    ) + "\n"


# ---------------------------------------------------------------------------
# Reaction-centric export (RDB7-compatible in spirit)
# ---------------------------------------------------------------------------


@dataclass
class MLReactionRecord:
    """One reaction-centric ML record.

    RDB7 column mapping (Grambow, Pattanaik & Green, *Sci. Data* 7, 137,
    2020 — the reaction dataset behind the "living RDB7" framing):

    ======================  =====================================================
    RDB7 field              TCKDB ML field
    ======================  =====================================================
    ``idx``                 ``reaction_entry_ref`` (stable public ref, not an int)
    ``rsmi``                ``reactants_smiles`` (join with ``.`` for RDB7's dot form)
    ``psmi``                ``products_smiles``
    ``ea`` (kcal/mol)       ``barrier.electronic_forward_kj_mol`` (÷ 4.184 for kcal;
                            electronic ``E_TS − ΣE_reactants`` at a shared LOT — the
                            true RDB7 barrier analog; ``null`` when a shared-LOT set
                            is unavailable). ``kinetics[].ea_kj_mol`` is the separate
                            *Arrhenius* activation energy and is not the same quantity.
    ``dh`` (kcal/mol)       ``delta_h298_kj_mol`` (÷ 4.184 for kcal; ΣH298(products) −
                            ΣH298(reactants) from selected thermo)
    reactant xyz            geometry of each reactant ``species_entry`` (fetch via the
                            species export using ``reactant_refs``; not inlined here)
    ts xyz                  ``transition_state.geometry`` (elements + coords)
    product xyz             geometry of each product ``species_entry`` (via ``product_refs``)
    ======================  =====================================================

    Units differ from RDB7 (kJ/mol vs kcal/mol) per the TCKDB fixed-unit
    policy; the mapping notes the 4.184 conversion.
    """

    reaction_entry: ReactionEntry
    reaction: ChemReaction
    reaction_family: str | None
    review_status: RecordReviewStatus
    reactant_refs: list[str]
    product_refs: list[str]
    reactant_smiles: list[str]
    product_smiles: list[str]
    kinetics: list[dict]
    barrier: dict | None
    delta_h298_kj_mol: float | None
    transition_state: dict | None

    def to_ndjson(self) -> dict:
        return {
            "record_type": "ml_reaction",
            "schema": ML_EXPORT_SCHEMA,
            "reaction_ref": self.reaction.public_ref,
            "reaction_entry_ref": self.reaction_entry.public_ref,
            "review_status": self.review_status.value,
            "reversible": self.reaction.reversible,
            "reaction_family": self.reaction_family,
            "reactant_refs": list(self.reactant_refs),
            "product_refs": list(self.product_refs),
            "reactants_smiles": list(self.reactant_smiles),
            "products_smiles": list(self.product_smiles),
            "kinetics": list(self.kinetics),
            "barrier": self.barrier,
            "delta_h298_kj_mol": self.delta_h298_kj_mol,
            "transition_state": self.transition_state,
        }


def _reaction_entry_review(
    session: Session, reaction_entry_id: int
) -> RecordReviewStatus:
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.reaction_entry,
        record_ids=[reaction_entry_id],
    )
    return badges[reaction_entry_id].status


def _species_entry_smiles(session: Session, species_entry_id: int) -> str | None:
    return session.scalar(
        select(Species.smiles)
        .join(SpeciesEntry, SpeciesEntry.species_id == Species.id)
        .where(SpeciesEntry.id == species_entry_id)
    )


def _species_entry_energy_at_lot(
    session: Session, species_entry_id: int, lot_id: int
) -> float | None:
    """Lowest electronic energy for a species entry at a specific LOT.

    Prefers single-point energies, falls back to optimisation final
    energies. Used to assemble a same-LOT reactant energy sum for the
    electronic barrier.
    """
    values: list[float] = []
    sp_rows = session.execute(
        select(CalculationSPResult.electronic_energy_hartree)
        .join(Calculation, Calculation.id == CalculationSPResult.calculation_id)
        .where(
            Calculation.species_entry_id == species_entry_id,
            Calculation.lot_id == lot_id,
            CalculationSPResult.electronic_energy_hartree.is_not(None),
        )
    ).all()
    values.extend(r[0] for r in sp_rows)
    if not values:
        opt_rows = session.execute(
            select(CalculationOptResult.final_energy_hartree)
            .join(
                Calculation, Calculation.id == CalculationOptResult.calculation_id
            )
            .where(
                Calculation.species_entry_id == species_entry_id,
                Calculation.lot_id == lot_id,
                CalculationOptResult.final_energy_hartree.is_not(None),
            )
        ).all()
        values.extend(r[0] for r in opt_rows)
    return min(values) if values else None


def _ts_energy_candidates(
    session: Session, ts_entry_id: int
) -> list[tuple[float, int | None, Calculation]]:
    """Best TS electronic energy per LOT, ordered by energy ascending.

    Returns one ``(energy_hartree, lot_id, calculation)`` candidate per
    distinct ``lot_id`` (including ``None``). Within one LOT, single-point
    energies are preferred over optimisation final energies, and the lowest
    value wins. Callers walk this list to find a LOT with complete reactant
    coverage for the electronic barrier.
    """
    sp_rows = session.execute(
        select(CalculationSPResult.electronic_energy_hartree, Calculation)
        .join(Calculation, Calculation.id == CalculationSPResult.calculation_id)
        .where(
            Calculation.transition_state_entry_id == ts_entry_id,
            CalculationSPResult.electronic_energy_hartree.is_not(None),
        )
    ).all()
    opt_rows = session.execute(
        select(CalculationOptResult.final_energy_hartree, Calculation)
        .join(Calculation, Calculation.id == CalculationOptResult.calculation_id)
        .where(
            Calculation.transition_state_entry_id == ts_entry_id,
            CalculationOptResult.final_energy_hartree.is_not(None),
        )
    ).all()

    best_sp: dict[int | None, tuple[float, Calculation]] = {}
    for energy, calc in sp_rows:
        current = best_sp.get(calc.lot_id)
        if current is None or energy < current[0]:
            best_sp[calc.lot_id] = (energy, calc)
    best_opt: dict[int | None, tuple[float, Calculation]] = {}
    for energy, calc in opt_rows:
        current = best_opt.get(calc.lot_id)
        if current is None or energy < current[0]:
            best_opt[calc.lot_id] = (energy, calc)

    candidates: list[tuple[float, int | None, Calculation]] = []
    for lot_id in set(best_sp) | set(best_opt):
        energy, calc = best_sp.get(lot_id) or best_opt[lot_id]
        candidates.append((energy, lot_id, calc))
    candidates.sort(key=lambda t: t[0])
    return candidates


def _select_ts_entry(
    session: Session, reaction_entry_id: int
) -> TransitionStateEntry | None:
    """Pick a representative TS entry for a reaction entry (lowest id, stable)."""
    return session.scalars(
        select(TransitionStateEntry)
        .join(
            TransitionState,
            TransitionState.id == TransitionStateEntry.transition_state_id,
        )
        .where(TransitionState.reaction_entry_id == reaction_entry_id)
        .order_by(TransitionStateEntry.id)
    ).first()


def _kinetics_blocks(
    session: Session,
    reaction_entry_id: int,
    *,
    min_review_status: RecordReviewStatus | None,
) -> list[dict]:
    """Selected Arrhenius kinetics for a reaction entry (read-time trust policy)."""
    from app.db.models.kinetics import Kinetics

    chosen, status_by_id = export_core.select_candidate_ids(
        session,
        model=Kinetics,
        parent_column=Kinetics.reaction_entry_id,
        parent_id=reaction_entry_id,
        record_type=SubmissionRecordType.kinetics,
        min_review_status=min_review_status,
        collapse=CollapseMode.all,
        selection_policy=SelectionPolicy.default,
    )
    out: list[dict] = []
    for kid in chosen:
        k = session.get(Kinetics, kid)
        if k is None:  # pragma: no cover - race with delete
            continue
        out.append(
            {
                "kinetics_ref": k.public_ref,
                "review_status": status_by_id[kid].value,
                "scientific_origin": k.scientific_origin.value,
                "model_kind": k.model_kind.value,
                "a": k.a,
                "a_units": k.a_units.value if k.a_units is not None else None,
                "n": k.n,
                "ea_kj_mol": k.ea_kj_mol,
                "tmin_k": k.tmin_k,
                "tmax_k": k.tmax_k,
                "degeneracy": k.degeneracy,
                "degeneracy_convention": k.degeneracy_convention.value,
            }
        )
    return out


def _ts_and_barrier(
    session: Session,
    reaction_entry_id: int,
    *,
    reactant_ids: list[int],
    lot_cache: dict[int, dict | None],
    geom_cache: dict[int, _GeometryPayload],
) -> tuple[dict | None, dict | None]:
    """Build the TS geometry+energy block and the electronic forward barrier.

    LOT selection walks the per-LOT TS energy candidates (lowest TS energy
    first) and picks the **first LOT for which every reactant has an energy**
    via :func:`_species_entry_energy_at_lot`, so one LOT lacking reactant
    coverage does not null the barrier when another LOT is complete. The TS
    energy block is emitted at that same chosen LOT so the record stays
    internally consistent. When no LOT completes the reactant set, the TS
    block falls back to the overall lowest-energy candidate and the barrier
    is ``null``.
    """
    ts_entry = _select_ts_entry(session, reaction_entry_id)
    if ts_entry is None:
        return None, None

    candidates = _ts_energy_candidates(session, ts_entry.id)

    chosen: tuple[float, int | None, Calculation] | None = None
    reactant_energies: list[float] | None = None
    if reactant_ids:
        for cand_energy, cand_lot_id, cand_calc in candidates:
            if cand_lot_id is None:
                continue
            energies = [
                _species_entry_energy_at_lot(session, se_id, cand_lot_id)
                for se_id in reactant_ids
            ]
            if all(e is not None for e in energies):
                chosen = (cand_energy, cand_lot_id, cand_calc)
                reactant_energies = [e for e in energies if e is not None]
                break
    if chosen is None and candidates:
        # No LOT has full reactant coverage: emit the TS block at the
        # overall lowest-energy candidate; the barrier stays null.
        chosen = candidates[0]

    energy_hartree: float | None
    lot_id: int | None
    calc: Calculation | None
    if chosen is not None:
        energy_hartree, lot_id, calc = chosen
    else:
        energy_hartree, lot_id, calc = None, None, None
    lot_block = _lot_block(session, lot_id, lot_cache)

    ts_geometry: dict | None = None
    if calc is not None:
        output_links, input_links = _load_geometry_links(session, [calc.id])
        gid = _resolved_geometry_id(calc.id, output_links, input_links)
        if gid is not None:
            payload = _geometry_payload(session, gid, geom_cache)
            if payload is not None:
                ts_geometry = payload.to_dict()

    ts_block = {
        "transition_state_ref": session.scalar(
            select(TransitionState.public_ref).where(
                TransitionState.id == ts_entry.transition_state_id
            )
        ),
        "transition_state_entry_ref": ts_entry.public_ref,
        "charge": ts_entry.charge,
        "multiplicity": ts_entry.multiplicity,
        "status": ts_entry.status.value,
        "energy": (
            None
            if energy_hartree is None
            else {
                "calculation_ref": calc.public_ref if calc is not None else None,
                "electronic_energy_hartree": energy_hartree,
                "level_of_theory": lot_block,
            }
        ),
        "geometry": ts_geometry,
    }

    barrier: dict | None = None
    if energy_hartree is not None and reactant_energies is not None:
        delta_hartree = energy_hartree - sum(reactant_energies)
        barrier = {
            "electronic_forward_kj_mol": delta_hartree * HARTREE_TO_KJ_MOL,
            "level_of_theory": lot_block,
            "definition": (
                "E_TS - sum(E_reactants) at a shared level of theory; "
                "reactant energies are the minimum over available "
                "calculations (lowest-conformer convention) at that LOT"
            ),
        }
    return ts_block, barrier


def _delta_h298(
    session: Session,
    *,
    reactant_ids: list[int],
    product_ids: list[int],
    min_review_status: RecordReviewStatus | None,
) -> float | None:
    """ΣH298(products) − ΣH298(reactants) from selected thermo (RDB7 ``dh``)."""

    def _sum(ids: list[int]) -> float | None:
        total = 0.0
        for se_id in ids:
            block = _species_thermo_block(
                session, se_id, min_review_status=min_review_status
            )
            if block is None or block.get("h298_kj_mol") is None:
                return None
            total += block["h298_kj_mol"]
        return total

    if not reactant_ids or not product_ids:
        return None
    reactant_sum = _sum(reactant_ids)
    product_sum = _sum(product_ids)
    if reactant_sum is None or product_sum is None:
        return None
    return product_sum - reactant_sum


def _build_reaction_record(
    session: Session,
    reaction_entry_id: int,
    *,
    filters: MLFilters,
    lot_cache: dict[int, dict | None],
    geom_cache: dict[int, _GeometryPayload],
) -> MLReactionRecord | None:
    entry = session.get(ReactionEntry, reaction_entry_id)
    if entry is None:  # pragma: no cover - race with delete
        return None
    reaction = session.get(ChemReaction, entry.reaction_id)
    if reaction is None:  # pragma: no cover - FK guarantees presence
        return None
    review_status = _reaction_entry_review(session, reaction_entry_id)

    family = None
    if reaction.reaction_family_id is not None:
        family = session.scalar(
            select(ReactionFamily.name).where(
                ReactionFamily.id == reaction.reaction_family_id
            )
        )

    participants = session.scalars(
        select(ReactionEntryStructureParticipant)
        .where(
            ReactionEntryStructureParticipant.reaction_entry_id == reaction_entry_id
        )
        .order_by(
            ReactionEntryStructureParticipant.role,
            ReactionEntryStructureParticipant.participant_index,
        )
    ).all()
    reactant_ids: list[int] = []
    product_ids: list[int] = []
    reactant_refs: list[str] = []
    product_refs: list[str] = []
    reactant_smiles: list[str] = []
    product_smiles: list[str] = []
    for p in participants:
        se = session.get(SpeciesEntry, p.species_entry_id)
        if se is None:  # pragma: no cover - FK guarantees presence
            continue
        smiles = _species_entry_smiles(session, p.species_entry_id) or ""
        if p.role is ReactionRole.reactant:
            reactant_ids.append(p.species_entry_id)
            reactant_refs.append(se.public_ref)
            reactant_smiles.append(smiles)
        else:
            product_ids.append(p.species_entry_id)
            product_refs.append(se.public_ref)
            product_smiles.append(smiles)

    kinetics = _kinetics_blocks(
        session, reaction_entry_id, min_review_status=filters.min_review_status
    )

    ts_block, barrier = _ts_and_barrier(
        session,
        reaction_entry_id,
        reactant_ids=reactant_ids,
        lot_cache=lot_cache,
        geom_cache=geom_cache,
    )
    delta_h = _delta_h298(
        session,
        reactant_ids=reactant_ids,
        product_ids=product_ids,
        min_review_status=filters.min_review_status,
    )

    return MLReactionRecord(
        reaction_entry=entry,
        reaction=reaction,
        reaction_family=family,
        review_status=review_status,
        reactant_refs=reactant_refs,
        product_refs=product_refs,
        reactant_smiles=reactant_smiles,
        product_smiles=product_smiles,
        kinetics=kinetics,
        barrier=barrier,
        delta_h298_kj_mol=delta_h,
        transition_state=ts_block,
    )


def iter_ml_reactions_ndjson(
    session: Session,
    *,
    reaction_refs: Sequence[str] | None = None,
    reaction_family: str | None = None,
    all_reactions: bool = False,
    filters: MLFilters | None = None,
    all_cap: int = DEFAULT_ALL_CAP,
) -> Iterator[str]:
    """Stream the reaction-centric (RDB7-compatible) ML export as NDJSON.

    Reuses the native export's seed resolution (``reaction_refs`` /
    ``reaction_family`` / ``all_reactions``). Emits a ``manifest`` header,
    one ``ml_reaction`` line per reaction entry (lazily), and an
    ``export_summary`` footer. Seed errors surface eagerly as
    :class:`ValueError` (422) before streaming starts.
    """
    filters = filters or MLFilters()
    seed = export_core.SeedSelection(
        reaction_refs=list(reaction_refs) if reaction_refs else None,
        reaction_family=reaction_family,
        all_reactions=all_reactions,
    )
    reaction_entry_ids, _standalone = export_core.resolve_seed(
        session, seed, all_cap=all_cap
    )
    if filters.offset:
        reaction_entry_ids = reaction_entry_ids[filters.offset:]
    if filters.limit is not None:
        reaction_entry_ids = reaction_entry_ids[: filters.limit]

    visible = visible_statuses(
        min_review_status=filters.min_review_status,
        include_rejected=False,
        include_deprecated=False,
    )
    return _stream_reactions(
        session,
        reaction_entry_ids=reaction_entry_ids,
        filters=filters,
        visible=visible,
    )


def _stream_reactions(
    session: Session,
    *,
    reaction_entry_ids: list[int],
    filters: MLFilters,
    visible: set[RecordReviewStatus],
) -> Iterator[str]:
    generated_at = datetime.now(timezone.utc)
    yield _dumps(
        {
            "record_type": "manifest",
            "schema": ML_EXPORT_SCHEMA,
            "dataset": "reactions",
            "generated_at": generated_at.isoformat(),
            "filters": filters.to_manifest(),
            "seed_reaction_entry_count": len(reaction_entry_ids),
        }
    ) + "\n"

    lot_cache: dict[int, dict | None] = {}
    geom_cache: dict[int, _GeometryPayload] = {}
    n_reactions = 0
    n_skipped_untrusted = 0

    for re_id in reaction_entry_ids:
        if _reaction_entry_review(session, re_id) not in visible:
            n_skipped_untrusted += 1
            continue
        record = _build_reaction_record(
            session,
            re_id,
            filters=filters,
            lot_cache=lot_cache,
            geom_cache=geom_cache,
        )
        if record is not None:
            n_reactions += 1
            yield _dumps(record.to_ndjson()) + "\n"

    yield _dumps(
        {
            "record_type": "export_summary",
            "schema": ML_EXPORT_SCHEMA,
            "dataset": "reactions",
            "generated_at": generated_at.isoformat(),
            "counts": {
                "records": n_reactions,
                "skipped_below_min_review_status": n_skipped_untrusted,
            },
        }
    ) + "\n"


def _resolve_lot_ref(session: Session, lot_ref: str | None) -> int | None:
    """Resolve a ``level_of_theory`` public ref to an id.

    :raises ValueError: 422 when the ref does not resolve.
    """
    if lot_ref is None:
        return None
    lot_id = session.scalar(
        select(LevelOfTheory.id).where(LevelOfTheory.public_ref == lot_ref)
    )
    if lot_id is None:
        raise ValueError(f"ml_export_lot_unresolved: lot ref not found: {lot_ref!r}")
    return lot_id


__all__ = [
    "DEFAULT_ALL_CAP",
    "DEFAULT_MIN_REVIEW_STATUS",
    "HARTREE_TO_KJ_MOL",
    "ML_EXPORT_SCHEMA",
    "MLEnergyRecord",
    "MLFilters",
    "MLReactionRecord",
    "MLSpeciesRecord",
    "iter_ml_reactions_ndjson",
    "iter_ml_species_ndjson",
]
