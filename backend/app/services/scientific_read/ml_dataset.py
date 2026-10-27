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
import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.enthalpy_reference import (
    ENTHALPY_REFERENCE_MIXED,
    ENTHALPY_REFERENCE_UNRECORDED,
    shared_enthalpy_reference,
)

from app.api.error_contract import CodedValueError
from app.chemistry.geometry import resolve_element_symbol
from app.chemistry.level_label import render_level_label
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
from app.schemas.reads.scientific_common import REVIEW_RANK
from app.services.document_formats import ML_EXPORT, current_header
from app.services.scientific_read import export as export_core
from app.services.scientific_read.common import (
    fetch_review_badges,
    linked_evidence_predicate,
    review_floor_predicate,
    visible_statuses,
    where_visible,
)
from app.services.scientific_read.export_withhold import WithheldBatcher
from app.services.scientific_read.handles import canonical_level_of_theory_id
from app.services.scientific_read.profile import (
    ResolvedReadProfile,
    current_read_profile,
)

logger = logging.getLogger(__name__)

#: Schema tag stamped on every ML record so a consumer can branch on format.
ML_EXPORT_FORMAT = current_header(ML_EXPORT)

#: Hartree → kJ/mol (matches app/importers/cccbdb/normalizers/units.py).
HARTREE_TO_KJ_MOL = 2625.4996394798254

#: Default trust floor for the ML export: approved-class identity records.
DEFAULT_MIN_REVIEW_STATUS = RecordReviewStatus.approved

#: Hard cap on ``all`` exports, shared with the native export.
DEFAULT_ALL_CAP = export_core.DEFAULT_ALL_CAP


def _profile_manifest_block(resolved: ResolvedReadProfile) -> dict:
    """The read-profile echo carried by every dataset manifest."""
    return {
        "profile": resolved.profile.value,
        "profile_recommendation": resolved.recommendation.value,
        "profile_release_ref": resolved.release_ref,
    }


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
                           set. ``None`` = no element filter. Both sides of
                           that comparison are canonicalised through
                           :func:`app.chemistry.geometry.resolve_element_symbol`
                           in :meth:`canonical_elements` — see it for why.
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

    def canonical_elements(self) -> frozenset[str] | None:
        """The allow-list as it is actually applied.

        A filter is a *comparison*, which is where ADR 0008's rule about
        element symbols bites: a blocking or dropping decision may not turn on
        an unenforced spelling convention. ``geometry_atom.element`` is
        canonicalised at ingestion and the stored corpus is canonical, but no
        CHECK holds it so, and ``D`` and ``T`` are deliberately left alone
        there — so a deuterated geometry stores ``D`` and would be dropped
        from an ``element=H,C,O`` export without a word. The query string is
        worse: nothing canonicalises what a caller types, so ``element=cl``
        matched nothing at all.

        Both ends go through :func:`resolve_element_symbol`, which settles
        case and reads ``D``/``T`` as the hydrogen they are. On already-canonical
        input it is a no-op.

        :returns: The canonicalised allow-list, or ``None`` when unfiltered.
        """

        if self.elements is None:
            return None
        return frozenset(resolve_element_symbol(e) for e in self.elements)

    def to_manifest(self) -> dict:
        canonical = self.canonical_elements()
        return {
            "min_review_status": (
                self.min_review_status.value
                if self.min_review_status is not None
                else None
            ),
            "lot_ref": self.lot_ref,
            # The manifest states the filter that ran, not the one that was
            # typed: a reader reproducing this export needs the former.
            "elements": sorted(canonical) if canonical is not None else None,
            "include_hessian": self.include_hessian,
            "limit": self.limit,
            "offset": self.offset,
        }


# ---------------------------------------------------------------------------
# Level-of-theory label helpers
# ---------------------------------------------------------------------------


def _lot_label(lot: LevelOfTheory) -> str:
    """Build a compact, deterministic, human+machine LOT label.

    ``method/basis`` with parenthesised annotations for every stated part of the level's identity (auxiliary
    and CABS basis, dispersion, solvent and model, spin and core treatment), written by the one renderer the
    notation of a record's levels uses (:func:`app.chemistry.level_label.render_level_label`), so the two
    cannot disagree. Raw ``keywords`` are not written. The stable machine key is ``lot_hash``; this label is the
    readable form and is not a key.
    """
    return render_level_label(
        method=lot.method,
        basis=lot.basis,
        aux_basis=lot.aux_basis,
        cabs_basis=lot.cabs_basis,
        dispersion=lot.dispersion,
        solvent=lot.solvent,
        solvent_model=lot.solvent_model,
        spin_treatment=lot.spin_treatment,
        core_treatment=lot.core_treatment,
    )


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
            # ``None`` means the producer did not say, never "frozen core by default" (ADR 0021).
            "core_treatment": (
                lot.core_treatment.value if lot.core_treatment is not None else None
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
        """The elements present, canonicalised for comparison only.

        ``symbols`` stays exactly as deposited — it is emitted in the record
        and a depositor who wrote ``D`` should read ``D`` back. This is the
        matching key; see :meth:`MLFilters.canonical_elements`.
        """
        return frozenset(resolve_element_symbol(s) for s in self.symbols)

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
    #: Every frequency result of every calculation at this geometry, each keyed by its ``calculation_ref`` and
    #: ``level_of_theory``. Several levels can have one; none is preferred, so none is picked.
    frequency_results: list[dict]
    #: Every Cartesian Hessian likewise (only when ``include_hessian``).
    hessians: list[dict]
    #: Every qualifying thermo record of the species entry, in display order. Nothing is picked.
    thermos: list[dict]

    def to_ndjson(self) -> dict:
        sp = self.species
        se = self.species_entry
        return {
            "record_type": "ml_species",
            "format": ML_EXPORT_FORMAT,
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
            "frequency_results": list(self.frequency_results),
            "hessians": list(self.hessians),
            "thermos": list(self.thermos),
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
            # The ML surface's own cap, and the same disclosure call as
            # its native sibling ``export_all_cap_exceeded``: publish the
            # configured cap, log the count. ``len(all_ids)`` is an
            # unfiltered ``SELECT`` over ``species_entry`` -- the total
            # number of species entries TCKDB holds, which is the
            # enumeration signal ``docs/specs/public_identifier_policy.md``
            # rejects sequential integer primary keys for.
            logger.info(
                "ML export refused as ml_export_all_cap_exceeded; %d "
                "species entries against a cap of %d",
                len(all_ids),
                all_cap,
            )
            raise CodedValueError(
                "ml_export_all_cap_exceeded",
                "refusing to export every species entry: the corpus is "
                f"larger than the export cap of {all_cap}",
                context={"cap": all_cap, "record_type": "species_entry"},
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


def _species_thermo_blocks(
    session: Session,
    species_entry_id: int,
    *,
    min_review_status: RecordReviewStatus | None,
    profile: ResolvedReadProfile,
) -> list[dict]:
    """Every qualifying species-scoped thermo summary, in display order (nothing is picked).

    Reuses the exact candidacy machinery behind the native/CHEMKIN export so
    the trust semantics are identical.
    """
    chosen, status_by_id = export_core.select_candidate_ids(
        session,
        model=Thermo,
        parent_column=Thermo.species_entry_id,
        parent_id=species_entry_id,
        record_type=SubmissionRecordType.thermo,
        min_review_status=min_review_status,
        profile=profile,
    )
    blocks: list[dict] = []
    for thermo_id in chosen:
        thermo = session.get(Thermo, thermo_id)
        if thermo is None:  # pragma: no cover - race with delete
            continue
        blocks.append(
            {
                "thermo_ref": thermo.public_ref,
                "review_status": status_by_id[thermo_id].value,
                "scientific_origin": thermo.scientific_origin.value,
                # ``NULL`` here means the reference was never recorded (a legacy
                # deposit predating the declaration rule), never that it is a
                # formation quantity by default -- see Thermo.enthalpy_reference_kind
                # and _delta_h298 below, the one place this repo currently sums
                # h298 values across records and therefore the one place mixing
                # bases silently would be wrong.
                "enthalpy_reference_kind": (
                    thermo.enthalpy_reference_kind.value
                    if thermo.enthalpy_reference_kind is not None
                    else None
                ),
                "h298_kj_mol": thermo.h298_kj_mol,
                "s298_j_mol_k": thermo.s298_j_mol_k,
                "h298_uncertainty_kj_mol": thermo.h298_uncertainty_kj_mol,
                "s298_uncertainty_j_mol_k": thermo.s298_uncertainty_j_mol_k,
            }
        )
    return blocks


def ml_energy_row_floor(profile: ResolvedReadProfile) -> Any:
    """The review floor on the ML export's ENERGY ROWS: a named exception to the evidence rule (owner decision Q7, ADR 0023).

    The ML export's rows are calculations (a geometry with the energies, frequencies and Hessian computed at it), so a
    calculation is a RECORD of the dataset, not linked evidence of another record, and it keeps the ``approved`` floor under
    ``curated``: a row is left out whatever its review state below it, and the barrier and reactant sums read only those rows.
    Everything else nested in an ML record follows the evidence rule (conformer observations and groups:
    :func:`_conformer_refs`; the listed thermo, kinetics and transition-state records keep the record floor as in every list).
    Dataset releases are unchanged. ``None`` without a review floor (exploratory), so nothing is added to the statement.
    """
    return review_floor_predicate(Calculation.id, SubmissionRecordType.calculation, profile=profile)


def _conformer_refs(
    session: Session, observation_ids: list[int], profile: ResolvedReadProfile
) -> tuple[list[str], list[str]]:
    """Resolve conformer observation ids to (group_refs, observation_refs).

    A conformer observation is linked evidence of the calculation that ran it (ADR 0023), so under ``curated`` it is
    resolved unless it is rejected or deprecated; not-reviewed observations are named. A group is named only THROUGH a
    resolved observation: a visible group reached by a withheld observation would otherwise be named beside an empty
    observation list, which says the withheld observation exists. (A withheld group reached by a resolved observation
    still arrives here; the document withhold drops its ref.) The calculation itself keeps the record floor.
    """
    if not observation_ids:
        return [], []
    rows = session.execute(
        where_visible(
            select(
                ConformerObservation.public_ref,
                ConformerGroup.public_ref,
            )
            .join(
                ConformerGroup,
                ConformerGroup.id == ConformerObservation.conformer_group_id,
            )
            .where(ConformerObservation.id.in_(observation_ids)),
            linked_evidence_predicate(
                ConformerObservation.id, SubmissionRecordType.conformer_observation, profile=profile
            ),
        ).order_by(ConformerObservation.id)
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
    profile: ResolvedReadProfile,
) -> list[MLSpeciesRecord]:
    """Build the per-geometry ML records for one species entry."""
    entry = session.get(SpeciesEntry, species_entry_id)
    if entry is None:  # pragma: no cover - race with delete
        return []
    species = session.get(Species, entry.species_id)
    if species is None:  # pragma: no cover - FK guarantees presence
        return []
    review_status = _species_entry_review(session, species_entry_id)

    # A calculation the profile hides is not part of this species' world: its energies, frequencies, Hessian,
    # geometry and conformer links are left out, not blanked (the ML export's energy rows keep their calculations
    # approved-only, ADR 0023 Q7).
    calcs = session.scalars(
        where_visible(
            select(Calculation).where(Calculation.species_entry_id == species_entry_id),
            ml_energy_row_floor(profile),
        ).order_by(Calculation.id)
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

    thermo_blocks = _species_thermo_blocks(
        session, species_entry_id, min_review_status=filters.min_review_status, profile=profile
    )

    records: list[MLSpeciesRecord] = []
    for geometry_id in sorted(by_geometry):
        geometry = _geometry_payload(session, geometry_id, geom_cache)
        if geometry is None:  # pragma: no cover - FK guarantees presence
            continue
        allowed_elements = filters.canonical_elements()
        if (
            allowed_elements is not None
            and not geometry.element_set() <= allowed_elements
        ):
            continue

        group = by_geometry[geometry_id]
        energies: list[MLEnergyRecord] = []
        frequency_results: list[dict] = []
        hessians: list[dict] = []
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
            freq_block = _frequencies_block(session, calc, lot_block)
            if freq_block is not None:
                frequency_results.append(freq_block)
            if filters.include_hessian:
                hessian_block = _hessian_block(session, calc, lot_block)
                if hessian_block is not None:
                    hessians.append(hessian_block)

        # With a LOT filter, a geometry with no surviving energy is dropped.
        if lot_id_filter is not None and not energies:
            continue

        conformer_group_refs, conformer_observation_refs = _conformer_refs(
            session, sorted(conformer_obs_ids), profile
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
                frequency_results=frequency_results,
                hessians=hessians,
                thermos=thermo_blocks,
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
    # Captured here, eagerly: the body of the generator below runs when the response is iterated, which can be after
    # the request context (and the profile in it) is gone.
    profile = current_read_profile()
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
        profile=profile,
    )


def _stream_species(
    session: Session,
    *,
    species_entry_ids: list[int],
    filters: MLFilters,
    lot_id_filter: int | None,
    visible: set[RecordReviewStatus],
    profile: ResolvedReadProfile,
) -> Iterator[str]:
    generated_at = datetime.now(timezone.utc)
    # ``profile`` was captured before the generator was returned: a streaming generator can outlive the
    # request-scoped context the profile lives in, so the context variable is never read in here.
    resolved_profile = profile
    batcher = WithheldBatcher(session, profile)
    yield _dumps(
        {
            "record_type": "manifest",
            "format": ML_EXPORT_FORMAT,
            "dataset": "species",
            # The resolved read profile is echoed in every dataset
            # manifest, not only in enveloped JSON responses.
            **_profile_manifest_block(resolved_profile),
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
            profile=profile,
        )
        for record in records:
            n_species += 1
            yield from batcher.add(record.to_ndjson())

    yield from batcher.flush()

    yield _dumps(
        {
            "record_type": "export_summary",
            "format": ML_EXPORT_FORMAT,
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
    ``ea`` (kcal/mol)       ``barriers[].electronic_forward_kj_mol`` (÷ 4.184 for kcal;
                            electronic ``E_TS − ΣE_reactants`` at ONE level of theory,
                            for every transition-state entry and every level at which
                            the TS and all reactants have an energy -- the true RDB7
                            barrier analog; ``barriers_unavailable`` names each entry
                            with no such level). The export never compares absolute
                            energies across methods, so it never chooses one level.
                            ``kinetics[].ea_kj_mol`` is the separate *Arrhenius*
                            activation energy and is not the same quantity.
    ``dh`` (kcal/mol)       ``delta_h298_kj_mol`` (÷ 4.184 for kcal; ΣH298(products) −
                            ΣH298(reactants) from selected thermo; ``null`` when a
                            participant has no candidate thermo, OR when the
                            contributing thermo terms do not all declare the same
                            ``enthalpy_reference_kind`` -- including any term left
                            over from before that declaration was required, which
                            declares none at all. See ``export_summary.counts``'s
                            ``delta_h298_declined_*`` fields for why, in aggregate.)
    reactant xyz            geometry of each reactant ``species_entry`` (fetch via the
                            species export using ``reactant_refs``; not inlined here)
    ts xyz                  ``transition_states[].energies[].geometry`` (elements + coords),
                            one per calculation that has an energy, keyed by its level
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
    #: Every transition-state entry the profile shows, each with its energies and geometry per calculation and level.
    transition_states: list[dict]
    #: One barrier per transition-state entry and per level at which the TS and every reactant has an energy.
    barriers: list[dict]
    #: Each transition-state entry with no such level, with the reason (``null`` is never silent).
    barriers_unavailable: list[dict]
    delta_h298_kj_mol: float | None

    def to_ndjson(self) -> dict:
        return {
            "record_type": "ml_reaction",
            "format": ML_EXPORT_FORMAT,
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
            "transition_states": list(self.transition_states),
            "barriers": list(self.barriers),
            "barriers_unavailable": list(self.barriers_unavailable),
            "delta_h298_kj_mol": self.delta_h298_kj_mol,
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
    session: Session, species_entry_id: int, lot_id: int, profile: ResolvedReadProfile
) -> tuple[float, str, str] | None:
    """Lowest electronic energy for a species entry at ONE level of theory, with where it came from.

    Returns ``(energy_hartree, energy_type, calculation_ref)`` or ``None``. Single-point energies are used before
    optimisation final energies, and the lowest value wins (the lowest-conformer convention, inside one level). Used to
    assemble a same-level reactant energy sum for the electronic barrier; the barrier record carries the calculation
    each reactant energy came from.
    """
    shown = ml_energy_row_floor(profile)
    sp_rows = session.execute(
        where_visible(
            select(CalculationSPResult.electronic_energy_hartree, Calculation.public_ref)
            .join(Calculation, Calculation.id == CalculationSPResult.calculation_id)
            .where(
                Calculation.species_entry_id == species_entry_id,
                Calculation.lot_id == lot_id,
                CalculationSPResult.electronic_energy_hartree.is_not(None),
            ),
            shown,
        )
    ).all()
    if sp_rows:
        energy, ref = min(sp_rows, key=lambda r: (r[0], r[1]))
        return energy, "single_point", ref
    opt_rows = session.execute(
        where_visible(
            select(CalculationOptResult.final_energy_hartree, Calculation.public_ref)
            .join(Calculation, Calculation.id == CalculationOptResult.calculation_id)
            .where(
                Calculation.species_entry_id == species_entry_id,
                Calculation.lot_id == lot_id,
                CalculationOptResult.final_energy_hartree.is_not(None),
            ),
            shown,
        )
    ).all()
    if opt_rows:
        energy, ref = min(opt_rows, key=lambda r: (r[0], r[1]))
        return energy, "optimization", ref
    return None


def _ts_calculation_energies(
    session: Session, ts_entry_id: int, profile: ResolvedReadProfile
) -> list[tuple[float, str, Calculation]]:
    """EVERY electronic energy of EVERY shown calculation of one TS entry: ``(energy_hartree, energy_type, calculation)``.

    Nothing is dropped and nothing is chosen: two single points at one level are two entries, and an optimisation final
    energy is an entry beside them (as the species-side ``energies`` list is). Callers group by level of theory; the
    lowest-within-a-level convention is applied only to compute a barrier, and each barrier names the calculation it
    used.
    """
    # Hidden calculations are not candidates: their energies would otherwise set the barrier.
    shown = ml_energy_row_floor(profile)
    sp_rows = session.execute(
        where_visible(
            select(CalculationSPResult.electronic_energy_hartree, Calculation)
            .join(Calculation, Calculation.id == CalculationSPResult.calculation_id)
            .where(
                Calculation.transition_state_entry_id == ts_entry_id,
                CalculationSPResult.electronic_energy_hartree.is_not(None),
            ),
            shown,
        )
    ).all()
    opt_rows = session.execute(
        where_visible(
            select(CalculationOptResult.final_energy_hartree, Calculation)
            .join(Calculation, Calculation.id == CalculationOptResult.calculation_id)
            .where(
                Calculation.transition_state_entry_id == ts_entry_id,
                CalculationOptResult.final_energy_hartree.is_not(None),
            ),
            shown,
        )
    ).all()
    return [(e, "single_point", c) for e, c in sp_rows] + [(e, "optimization", c) for e, c in opt_rows]


def _ts_entries(
    session: Session, reaction_entry_id: int, profile: ResolvedReadProfile
) -> list[TransitionStateEntry]:
    """EVERY transition-state entry of a reaction entry that the profile shows, in DISPLAY order.

    Review rank, then deposition time newest first, then id descending (``docs/specs/browse_order.md``): a listing
    order, never a pick; all entries are returned. A hidden entry is not in the population, so it is neither listed nor
    named.
    """
    entries = list(
        session.scalars(
            where_visible(
                select(TransitionStateEntry)
                .join(
                    TransitionState,
                    TransitionState.id == TransitionStateEntry.transition_state_id,
                )
                .where(TransitionState.reaction_entry_id == reaction_entry_id),
                review_floor_predicate(
                    TransitionStateEntry.id, SubmissionRecordType.transition_state_entry, profile=profile
                ),
            )
        ).all()
    )
    badges = fetch_review_badges(
        session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_ids=[e.id for e in entries],
    )
    entries.sort(key=lambda e: (REVIEW_RANK[badges[e.id].status], -e.created_at.timestamp(), -e.id))
    return entries


def _kinetics_blocks(
    session: Session,
    reaction_entry_id: int,
    *,
    min_review_status: RecordReviewStatus | None,
    profile: ResolvedReadProfile,
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
        profile=profile,
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
                "t0_k": k.t0_k,
                "ea_kj_mol": k.ea_kj_mol,
                "tmin_k": k.tmin_k,
                "tmax_k": k.tmax_k,
                "degeneracy": k.degeneracy,
                "degeneracy_convention": k.degeneracy_convention.value,
            }
        )
    return out


BARRIER_DEFINITION = (
    "E_TS - sum(E_reactants) at ONE level of theory, for this transition-state entry. Within that level the TS "
    "energy is the lowest single-point energy of the entry's calculations at the level (the lowest optimisation "
    "final energy when it has no single point) and each reactant energy is chosen the same way (lowest-conformer "
    "convention); the barrier names the TS calculation (ts_calculation_ref) and every reactant calculation it used. "
    "Every calculation's energy is listed under transition_states[].energies regardless. Energies at different "
    "levels are never compared."
)


def _level_sort_key(lot_block: dict | None, calculation_ref: str) -> tuple:
    """A stable listing order for per-level entries: by the level's own label, then the calculation. Not by energy."""
    if lot_block is None:
        return (1, "", calculation_ref)
    return (0, str(lot_block.get("label") or lot_block.get("method") or ""), calculation_ref)


def _ts_blocks_and_barriers(
    session: Session,
    reaction_entry_id: int,
    *,
    reactant_ids: list[int],
    reactant_refs: list[str],
    lot_cache: dict[int, dict | None],
    geom_cache: dict[int, _GeometryPayload],
    profile: ResolvedReadProfile,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Every TS entry with its per-level energies and geometry, every per-level barrier, and each entry with none.

    Nothing is chosen between transition-state entries or between levels of theory. A barrier is emitted for every
    (TS entry, level) at which the TS and every reactant have an energy; an entry with no such level is listed in
    ``barriers_unavailable`` with the reason (``no_level_with_all_reactant_energies``, ``no_reactants``,
    ``no_energies``) instead of silently getting a ``null``.
    """
    blocks: list[dict] = []
    barriers: list[dict] = []
    unavailable: list[dict] = []
    reactant_energy_cache: dict[tuple[int, int], tuple[float, str, str] | None] = {}

    def reactant_energy(se_id: int, lot_id: int) -> tuple[float, str, str] | None:
        key = (se_id, lot_id)
        if key not in reactant_energy_cache:
            reactant_energy_cache[key] = _species_entry_energy_at_lot(session, se_id, lot_id, profile)
        return reactant_energy_cache[key]

    for ts_entry in _ts_entries(session, reaction_entry_id, profile):
        every_energy = _ts_calculation_energies(session, ts_entry.id, profile)
        energy_blocks: list[dict] = []
        by_level: dict[int | None, list[tuple[float, str, Calculation]]] = {}
        for energy, energy_type, calc in every_energy:
            lot_block = _lot_block(session, calc.lot_id, lot_cache)
            geometry: dict | None = None
            output_links, input_links = _load_geometry_links(session, [calc.id])
            gid = _resolved_geometry_id(calc.id, output_links, input_links)
            if gid is not None:
                payload = _geometry_payload(session, gid, geom_cache)
                if payload is not None:
                    geometry = payload.to_dict()
            energy_blocks.append(
                {
                    "calculation_ref": calc.public_ref,
                    "energy_type": energy_type,
                    "electronic_energy_hartree": energy,
                    "level_of_theory": lot_block,
                    "geometry": geometry,
                }
            )
            by_level.setdefault(calc.lot_id, []).append((energy, energy_type, calc))

        entry_barriers: list[dict] = []
        for lot_id, candidates in by_level.items():
            if lot_id is None or not reactant_ids:
                continue
            # The barrier's TS energy at this level: the lowest single point, else the lowest optimisation energy.
            single_points = [c for c in candidates if c[1] == "single_point"]
            energy, energy_type, calc = min(single_points or candidates, key=lambda c: (c[0], c[2].public_ref))
            reactant_energies = [reactant_energy(se_id, lot_id) for se_id in reactant_ids]
            if any(e is None for e in reactant_energies):
                continue
            entry_barriers.append(
                {
                    "transition_state_entry_ref": ts_entry.public_ref,
                    "ts_calculation_ref": calc.public_ref,
                    "ts_energy_type": energy_type,
                    "level_of_theory": _lot_block(session, lot_id, lot_cache),
                    "electronic_forward_kj_mol": (energy - sum(e[0] for e in reactant_energies if e is not None))
                    * HARTREE_TO_KJ_MOL,
                    "reactant_energies": [
                        {
                            "species_entry_ref": ref,
                            "calculation_ref": e[2],
                            "energy_type": e[1],
                            "electronic_energy_hartree": e[0],
                        }
                        for ref, e in zip(reactant_refs, reactant_energies, strict=True)
                        if e is not None
                    ],
                    "definition": BARRIER_DEFINITION,
                }
            )
        energy_blocks.sort(key=lambda b: _level_sort_key(b["level_of_theory"], b["calculation_ref"]))
        entry_barriers.sort(key=lambda b: _level_sort_key(b["level_of_theory"], b["ts_calculation_ref"]))
        barriers.extend(entry_barriers)
        if not entry_barriers:
            if not energy_blocks:
                reason = "no_energies"
            elif not reactant_ids:
                reason = "no_reactants"
            else:
                reason = "no_level_with_all_reactant_energies"
            unavailable.append({"transition_state_entry_ref": ts_entry.public_ref, "reason": reason})
        blocks.append(
            {
                "transition_state_ref": session.scalar(
                    select(TransitionState.public_ref).where(TransitionState.id == ts_entry.transition_state_id)
                ),
                "transition_state_entry_ref": ts_entry.public_ref,
                "charge": ts_entry.charge,
                "multiplicity": ts_entry.multiplicity,
                "status": ts_entry.status.value,
                "energies": energy_blocks,
            }
        )
    return blocks, barriers, unavailable


#: ``_delta_h298`` decline reasons, surfaced only as an ``export_summary``
#: count (see ``_stream_reactions``) -- never invented as a new per-record
#: JSON field, matching how every other missing term in this module (the
#: barrier, the TS block) is represented: the value is simply ``null``.
#: The values are the wire package's shared-basis reasons
#: (``tckdb_schemas.enthalpy_reference.shared_enthalpy_reference``), which
#: ``_delta_h298`` returns verbatim.
DELTA_H298_UNRECORDED_REFERENCE = ENTHALPY_REFERENCE_UNRECORDED
DELTA_H298_MIXED_REFERENCE = ENTHALPY_REFERENCE_MIXED
#: A contributing species has more than one qualifying thermo record, and the export does not pick one to sum.
DELTA_H298_AMBIGUOUS_THERMO = "ambiguous_thermo"


def _delta_h298(
    session: Session,
    *,
    reactant_ids: list[int],
    product_ids: list[int],
    min_review_status: RecordReviewStatus | None,
    profile: ResolvedReadProfile,
) -> tuple[float | None, str | None]:
    """ΣH298(products) − ΣH298(reactants) from selected thermo (RDB7 ``dh``).

    Declines -- returns ``(None, reason)`` -- instead of summing across
    mixed or unrecorded enthalpy bases. Every contributing thermo term must
    declare ``enthalpy_reference_kind`` (see ``Thermo.enthalpy_reference_kind``):
    a record deposited before that rule existed carries an enthalpy with no
    declaration, and TCKDB never backfills one, so an undeclared enthalpy
    means the reference was never recorded, not that it defaults to a
    formation quantity. Summing it in with declared terms would be wrong by
    a large, plausible-looking amount with nothing to flag it.

    The check requires every contributing term to declare the *same*
    reference, not merely *some* reference: two terms each declaring a
    different basis would be exactly as wrong to sum as an undeclared one,
    only less visibly so. ``formation_298k`` is the only value the schema
    accepts today, so "all declared" and "all the same" coincide -- but the
    stricter check is the one that stays correct if a second value is ever
    added, and costs nothing while there is only one.

    :returns: ``(delta_h298_kj_mol, None)`` when every term shares one
        declared reference; ``(None, None)`` for the pre-existing "a term
        has no thermo candidate at all" gap; ``(None,
        DELTA_H298_UNRECORDED_REFERENCE)`` when a contributing term's
        reference was never recorded; ``(None, DELTA_H298_MIXED_REFERENCE)``
        when contributing terms declare more than one distinct reference.
    """

    ambiguous = False

    def _blocks(ids: list[int]) -> list[dict] | None:
        nonlocal ambiguous
        blocks: list[dict] = []
        for se_id in ids:
            candidates = _species_thermo_blocks(
                session, se_id, min_review_status=min_review_status, profile=profile
            )
            if len(candidates) > 1:
                ambiguous = True
                return None
            block = candidates[0] if candidates else None
            if block is None or block.get("h298_kj_mol") is None:
                return None
            blocks.append(block)
        return blocks

    if not reactant_ids or not product_ids:
        return None, None
    reactant_blocks = _blocks(reactant_ids)
    product_blocks = _blocks(product_ids) if not ambiguous else None
    if ambiguous:
        return None, DELTA_H298_AMBIGUOUS_THERMO
    if reactant_blocks is None or product_blocks is None:
        return None, None

    # The shared-basis rule lives in the wire package so the Phase D Hess
    # check applies the identical rule; both terms lists are non-empty here.
    _, reference_reason = shared_enthalpy_reference(
        block.get("enthalpy_reference_kind")
        for block in reactant_blocks + product_blocks
    )
    if reference_reason is not None:
        return None, reference_reason

    reactant_sum = sum(block["h298_kj_mol"] for block in reactant_blocks)
    product_sum = sum(block["h298_kj_mol"] for block in product_blocks)
    return product_sum - reactant_sum, None


def _build_reaction_record(
    session: Session,
    reaction_entry_id: int,
    *,
    filters: MLFilters,
    lot_cache: dict[int, dict | None],
    geom_cache: dict[int, _GeometryPayload],
    profile: ResolvedReadProfile,
) -> tuple[MLReactionRecord | None, str | None]:
    """Build one reaction record.

    :returns: ``(record, delta_h298_decline_reason)`` -- the second element
        is the reason ``_delta_h298`` declined (see
        ``DELTA_H298_UNRECORDED_REFERENCE`` / ``DELTA_H298_MIXED_REFERENCE``),
        or ``None`` when ``dh`` was computed or was null for an unrelated,
        pre-existing cause. It is aggregated into ``export_summary`` by
        ``_stream_reactions``, never emitted on the record itself -- the
        record's own ``delta_h298_kj_mol`` stays a plain ``null`` like every
        other value this module cannot produce.
    """
    entry = session.get(ReactionEntry, reaction_entry_id)
    if entry is None:  # pragma: no cover - race with delete
        return None, None
    reaction = session.get(ChemReaction, entry.reaction_id)
    if reaction is None:  # pragma: no cover - FK guarantees presence
        return None, None
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
        session, reaction_entry_id, min_review_status=filters.min_review_status, profile=profile
    )

    ts_blocks, barriers, barriers_unavailable = _ts_blocks_and_barriers(
        session,
        reaction_entry_id,
        reactant_ids=reactant_ids,
        reactant_refs=reactant_refs,
        lot_cache=lot_cache,
        geom_cache=geom_cache,
        profile=profile,
    )
    delta_h, delta_h_decline_reason = _delta_h298(
        session,
        reactant_ids=reactant_ids,
        product_ids=product_ids,
        min_review_status=filters.min_review_status,
        profile=profile,
    )

    record = MLReactionRecord(
        reaction_entry=entry,
        reaction=reaction,
        reaction_family=family,
        review_status=review_status,
        reactant_refs=reactant_refs,
        product_refs=product_refs,
        reactant_smiles=reactant_smiles,
        product_smiles=product_smiles,
        kinetics=kinetics,
        transition_states=ts_blocks,
        barriers=barriers,
        barriers_unavailable=barriers_unavailable,
        delta_h298_kj_mol=delta_h,
    )
    return record, delta_h_decline_reason


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
    # Captured here, eagerly (see iter_ml_species_ndjson).
    profile = current_read_profile()
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
        profile=profile,
    )


def _stream_reactions(
    session: Session,
    *,
    reaction_entry_ids: list[int],
    filters: MLFilters,
    visible: set[RecordReviewStatus],
    profile: ResolvedReadProfile,
) -> Iterator[str]:
    generated_at = datetime.now(timezone.utc)
    # ``profile`` was captured before the generator was returned (see _stream_species).
    resolved_profile = profile
    batcher = WithheldBatcher(session, profile)
    yield _dumps(
        {
            "record_type": "manifest",
            "format": ML_EXPORT_FORMAT,
            "dataset": "reactions",
            # The resolved read profile is echoed in every dataset
            # manifest, not only in enveloped JSON responses.
            **_profile_manifest_block(resolved_profile),
            "generated_at": generated_at.isoformat(),
            "filters": filters.to_manifest(),
            "seed_reaction_entry_count": len(reaction_entry_ids),
        }
    ) + "\n"

    lot_cache: dict[int, dict | None] = {}
    geom_cache: dict[int, _GeometryPayload] = {}
    n_reactions = 0
    n_skipped_untrusted = 0
    n_delta_h298_unrecorded_reference = 0
    n_delta_h298_mixed_reference = 0
    n_delta_h298_ambiguous_thermo = 0
    n_barriers = 0
    n_ts_without_barrier = 0

    for re_id in reaction_entry_ids:
        if _reaction_entry_review(session, re_id) not in visible:
            n_skipped_untrusted += 1
            continue
        record, delta_h_decline_reason = _build_reaction_record(
            session,
            re_id,
            filters=filters,
            lot_cache=lot_cache,
            geom_cache=geom_cache,
            profile=profile,
        )
        if delta_h_decline_reason == DELTA_H298_UNRECORDED_REFERENCE:
            n_delta_h298_unrecorded_reference += 1
        elif delta_h_decline_reason == DELTA_H298_MIXED_REFERENCE:
            n_delta_h298_mixed_reference += 1
        elif delta_h_decline_reason == DELTA_H298_AMBIGUOUS_THERMO:
            n_delta_h298_ambiguous_thermo += 1
        if record is not None:
            n_reactions += 1
            n_barriers += len(record.barriers)
            n_ts_without_barrier += len(record.barriers_unavailable)
            yield from batcher.add(record.to_ndjson())

    yield from batcher.flush()
    yield _dumps(
        {
            "record_type": "export_summary",
            "format": ML_EXPORT_FORMAT,
            "dataset": "reactions",
            "generated_at": generated_at.isoformat(),
            "counts": {
                "records": n_reactions,
                # One barrier per transition-state entry and level at which the TS and every reactant have an energy.
                "barriers": n_barriers,
                # Transition-state entries with no such level; each is named, with its reason, on its record.
                "transition_state_entries_without_barrier": n_ts_without_barrier,
                "skipped_below_min_review_status": n_skipped_untrusted,
                # Why delta_h298_kj_mol came back null on some record(s)
                # beyond the pre-existing "no thermo candidate at all" gap
                # (which this export has never separately counted): at
                # least one contributing thermo term's enthalpy reference
                # was never recorded, or contributing terms declared more
                # than one distinct reference. See _delta_h298.
                "delta_h298_declined_unrecorded_reference": (
                    n_delta_h298_unrecorded_reference
                ),
                "delta_h298_declined_mixed_reference": n_delta_h298_mixed_reference,
                # A contributing species has several qualifying thermo records; the sum is not taken from a pick.
                "delta_h298_declined_ambiguous_thermo": n_delta_h298_ambiguous_thermo,
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
    # A merged row's ref names the row it was merged into (#574).
    return canonical_level_of_theory_id(session, lot_id)


__all__ = [
    "DEFAULT_ALL_CAP",
    "DEFAULT_MIN_REVIEW_STATUS",
    "DELTA_H298_AMBIGUOUS_THERMO",
    "DELTA_H298_MIXED_REFERENCE",
    "DELTA_H298_UNRECORDED_REFERENCE",
    "HARTREE_TO_KJ_MOL",
    "ML_EXPORT_FORMAT",
    "MLEnergyRecord",
    "MLFilters",
    "MLReactionRecord",
    "MLSpeciesRecord",
    "iter_ml_reactions_ndjson",
    "iter_ml_species_ndjson",
]
