"""Builders shared by the structure-selection tests.

Plain normalised units and requests for the pure assessor tests (no database), and ORM builders for the tests
that run against real rows.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationQuality,
    CalculationType,
    ProfileRecommendation,
    ReadProfile,
    RecordReviewStatus,
    StructureDeterminationQuantity,
    StructureDeterminationTargetKind,
    StructureFindingAuthority,
    StructureFindingKind,
    StructureFindingScope,
    StructureFindingVerdict,
    StructureSourceRole,
    SubmissionRecordType,
)
from app.db.models.geometry import Geometry
from app.db.models.structure_determination import (
    StructureDetermination,
    StructureDeterminationSource,
    StructureEvidenceFinding,
)
from app.services.scientific_read.profile import (
    ResolvedReadProfile,
    reset_current_read_profile,
    set_current_read_profile,
)
from app.services.structure_selection.models import (
    CurvatureFacts,
    EnergyFacts,
    FindingFacts,
    Grain,
    Intent,
    LevelFacts,
    NormalizedCalculation,
    NormalizedDetermination,
    NormalizedSource,
    Quantity,
    StructureRequest,
    StructureSubject,
)
from tests.services.scientific_read._factories import (
    attach_freq_result,
    attach_geometry_validation,
    attach_hessian,
    attach_input_geometry,
    attach_opt_result,
    attach_output_geometry,
    attach_scf_stability,
    attach_sp_result,
    make_calculation,
    make_geometry,
    make_lot,
    make_species,
    make_species_entry,
    make_workflow_tool_release,
    next_inchi_key,
    set_review,
)

T0 = datetime(2026, 6, 1, 12, 0, 0)

#: A declaration that establishes every fact a numerical comparison needs, on its own.
FULL_DECLARATION: dict[str, Any] = {
    "version": 1,
    "source": {"origin": "producer_declared", "producer": "arc"},
    "electronic_state": {"state": "known", "root": 0},
    "spin_treatment": {"state": "known", "value": "restricted"},
    "relativistic_treatment": {"state": "known", "value": "none"},
    "effective_core_potential": {"state": "not_applicable"},
    "core_treatment": {"state": "known", "value": "frozen_core"},
    "solvation": {"state": "known", "kind": "gas_phase"},
    "numerical_approximations": [],
    "included_corrections": [],
}


def declaration(**changes: Any) -> dict[str, Any]:
    return {**FULL_DECLARATION, **changes}


# ---------------------------------------------------------------------------
# Pure normalised units
# ---------------------------------------------------------------------------

G1, G2 = "geom_g1", "geom_g2"
LOT_A = "lot_a"


def level(ref: str | None = LOT_A, **changes: Any) -> LevelFacts:
    base: dict[str, Any] = {
        "level_ref": ref,
        "method": "ccsd(t)",
        "basis": "cc-pvtz",
        "aux_basis": None,
        "dispersion": None,
        "solvent": None,
        "solvent_model": None,
        "spin_treatment": None,
        "core_treatment": None,
    }
    base.update(changes)
    return LevelFacts(**base)


def calc(
    ref: str = "calc_a",
    *,
    type: str = "sp",
    rank: int = 1,
    age_days: float = 0,
    energy: float | EnergyFacts | None = -76.4,
    declared: dict[str, Any] | str | None = "full",
    **changes: Any,
) -> NormalizedCalculation:
    """A calculation that answers a recorded-minimum request completely; every keyword changes one fact."""
    declared_value = declaration() if declared == "full" else declared
    if isinstance(energy, EnergyFacts):
        energy_facts_override: EnergyFacts | None = energy
        energy = None
    else:
        energy_facts_override = None
    energy_facts = energy_facts_override or {
        "sp": EnergyFacts(sp_electronic_hartree=energy),
        "opt": EnergyFacts(opt_final_hartree=energy, opt_converged=True),
        "composite": EnergyFacts(
            composite_assembly="program_run",
            composite_electronic_hartree=energy,
            composite_e0_hartree=None if energy is None else energy + 0.02,
            composite_recipe_zpe_hartree=0.02,
        ),
        "freq": EnergyFacts(),
    }[type]
    base: dict[str, Any] = {
        "calculation_ref": ref,
        "type": type,
        "quality": "raw",
        "review_status": RecordReviewStatus.approved,
        "created_at": T0 - timedelta(days=age_days),
        "id_rank": rank,
        "owner_ref": "spe_a",
        "level": level(),
        "software": "orca@6.0.1",
        "declaration_state": "absent" if declared_value is None else "valid",
        "declaration": declared_value,
        "constraint_rows": 0,
        "energy": energy_facts,
        "curvature": CurvatureFacts(),
        "input_geometry_refs": (G1,) if type in ("sp", "freq") else (),
        "output_geometry_refs": (G1,) if type in ("opt", "composite") else (),
    }
    base.update(changes)
    return NormalizedCalculation(**base)


def freq(ref: str = "calc_f", *, n_imag: int | None = 0, geometry: str = G1, **changes: Any) -> NormalizedCalculation:
    curvature = changes.pop("curvature", None) or CurvatureFacts(has_freq_result=True, n_imag=n_imag)
    return calc(ref, type="freq", energy=None, curvature=curvature, input_geometry_refs=(geometry,), **changes)


def source(role: str, ref: str | None, geometry: str | None = G1, unavailable: str | None = None) -> NormalizedSource:
    return NormalizedSource(role=role, geometry_ref=geometry, calculation_ref=ref, unavailable_reason=unavailable)


def det(
    ref: str = "sdet_a",
    *,
    sources: tuple[NormalizedSource, ...] | None = None,
    quantity: str | None = "electronic_energy",
    target_kind: str = "geometry",
    owner_kind: str = "species_entry",
    entry_kind: str | None = "minimum",
    rank: int = 1,
    **changes: Any,
) -> NormalizedDetermination:
    base: dict[str, Any] = {
        "determination_ref": ref,
        "target_kind": target_kind,
        "quantity": quantity,
        "energy_convention": None,
        "actual_recipe": None,
        "key": "k",
        "owner_ref": "spe_a",
        "owner_kind": owner_kind,
        "conformer_observation_ref": None,
        "conformer_group_ref": None,
        "evaluated_geometry_ref": G1,
        "review_status": RecordReviewStatus.approved,
        "created_at": T0,
        "id_rank": rank,
        "entry_kind": entry_kind,
        "sources": sources
        if sources is not None
        else (source("geometry_optimization", "calc_o"), source("energy", "calc_a"), source("curvature", "calc_f")),
    }
    base.update(changes)
    return NormalizedDetermination(**base)


def subject(**changes: Any) -> StructureSubject:
    base: dict[str, Any] = {
        "kind": "species_entry",
        "entry_ref": "spe_a",
        "stationary_point_kind": "minimum",
        "electronic_state_kind": "ground",
        "isotope_key": None,
        "charge": 0,
        "multiplicity": 1,
    }
    base.update(changes)
    return StructureSubject(**base)


def finding_facts(
    ref: str = "sfnd_a",
    *,
    kind: str = "identity_incompatibility",
    scope: str = "calculation",
    subject_ref: str = "calc_a",
    verdict: str = "invalidates",
    authority: str = "authorized_adjudication",
    role: str | None = None,
    version: int = 1,
    supersedes: str | None = None,
) -> FindingFacts:
    return FindingFacts(ref, kind, scope, subject_ref, role, verdict, authority, version, supersedes)


def request(**changes: Any) -> StructureRequest:
    """A recorded-minimum request for electronic energy at the calculation grain."""
    base: dict[str, Any] = {
        "grain": Grain.calculation,
        "intent": Intent.recorded_minimum,
        "quantity": Quantity.electronic_energy,
    }
    base.update(changes)
    return StructureRequest(**base)


def minimum_request(**changes: Any) -> StructureRequest:
    base: dict[str, Any] = {
        "grain": Grain.conformer,
        "intent": Intent.validated_minimum,
        "quantity": Quantity.electronic_energy,
    }
    base.update(changes)
    return StructureRequest(**base)


def saddle_request(**changes: Any) -> StructureRequest:
    base: dict[str, Any] = {
        "grain": Grain.transition_state,
        "intent": Intent.validated_saddle,
        "quantity": Quantity.electronic_energy,
    }
    base.update(changes)
    return StructureRequest(**base)


def codes(assessment, applicability=None) -> list[str]:
    return [r.code for r in assessment.reasons if applicability is None or r.applicability is applicability]


# ---------------------------------------------------------------------------
# Real rows
# ---------------------------------------------------------------------------


@dataclass
class World:
    """One species entry with a geometry and a level, plus the means to add evidence to it."""

    session: Session
    entry: Any
    geometry: Geometry
    lot: Any
    calcs: dict[str, Calculation] = field(default_factory=dict)
    ts: bool = False
    #: Review statuses to apply in :meth:`settle`. An accepted calculation is frozen, so a review that approves
    #: one is applied only after everything that belongs under it (results, links, dependencies) is written.
    pending: dict[int, RecordReviewStatus] = field(default_factory=dict)
    entry_status: RecordReviewStatus | None = None

    @property
    def owner(self) -> dict[str, int]:
        """The ownership column the entry fills on every calculation and determination."""
        return {"transition_state_entry_id" if self.ts else "species_entry_id": self.entry.id}

    def sp(self, name: str, energy: float | None = -76.4, *, geometry: Geometry | None = None, lot=None, observation=None, **kw) -> Calculation:
        c = make_calculation(
            self.session, type=CalculationType.sp, **self.owner, lot_id=(lot or self.lot).id,
            conformer_observation_id=observation.id if observation is not None else None,
        )
        if energy is not None:
            attach_sp_result(self.session, calculation=c, electronic_energy_hartree=energy)
        attach_input_geometry(self.session, calculation=c, geometry=geometry or self.geometry)
        self._finish(name, c, **kw)
        return c

    def opt(
        self, name: str, energy: float | None = -76.4, *, converged: bool = True, geometry: Geometry | None = None, lot=None,
        observation=None, **kw
    ) -> Calculation:
        c = make_calculation(
            self.session, type=CalculationType.opt, **self.owner, lot_id=(lot or self.lot).id,
            conformer_observation_id=observation.id if observation is not None else None,
        )
        attach_opt_result(self.session, calculation=c, final_energy_hartree=energy, converged=converged)
        attach_output_geometry(self.session, calculation=c, geometry=geometry or self.geometry)
        self._finish(name, c, **kw)
        return c

    def freq(
        self, name: str, *, frequencies=(100.0, 200.0), geometry: Geometry | None = None, lot=None, observation=None, **kw
    ) -> Calculation:
        flag = kw.pop("structural_flag", None)
        rc = kw.pop("reaction_coordinate_mode_index", None)
        c = make_calculation(
            self.session, type=CalculationType.freq, **self.owner, lot_id=(lot or self.lot).id,
            conformer_observation_id=observation.id if observation is not None else None,
        )
        attach_freq_result(
            self.session,
            calculation=c,
            frequencies_cm1=list(frequencies),
            imaginary_mode_structural_flag=flag,
            reaction_coordinate_mode_index=rc,
        )
        attach_input_geometry(self.session, calculation=c, geometry=geometry or self.geometry)
        self._finish(name, c, **kw)
        return c

    def _finish(
        self,
        name: str,
        c: Calculation,
        *,
        declared: dict[str, Any] | None = None,
        status: RecordReviewStatus | None = RecordReviewStatus.approved,
        quality: CalculationQuality | None = None,
        scf=None,
        validation=None,
    ) -> None:
        if declared is not None:
            c.actual_protocol_declaration = declared
        if quality is not None:
            c.quality = quality
        if status is not None:
            self.pending[c.id] = status
        if scf is not None:
            attach_scf_stability(self.session, calculation=c, status=scf)
        if validation is not None:
            attach_geometry_validation(self.session, calculation=c, status=validation)
        self.session.flush()
        self.calcs[name] = c

    def settle(self, *, entry: bool = True) -> None:
        """Apply the review statuses chosen so far (call after every child row of a calculation exists)."""
        for calc_id, status in list(self.pending.items()):
            set_review(self.session, record_type=SubmissionRecordType.calculation, record_id=calc_id, status=status)
        self.pending.clear()
        if entry and self.entry_status is not None:
            set_review(
                self.session,
                record_type=SubmissionRecordType.transition_state_entry if self.ts else SubmissionRecordType.species_entry,
                record_id=self.entry.id,
                status=self.entry_status,
            )
            self.entry_status = None
        self.session.flush()

    def hessian(self, calc: Calculation, geometry: Geometry | None = None) -> None:
        attach_hessian(self.session, calculation=calc, geometry=geometry or self.geometry, natoms=3)

    def determination(
        self,
        sources: list[tuple[str, Calculation]],
        *,
        evaluated: Geometry | None = None,
        quantity: StructureDeterminationQuantity | None = StructureDeterminationQuantity.electronic_energy,
        target: StructureDeterminationTargetKind = StructureDeterminationTargetKind.geometry,
        observation=None,
        recipe: dict[str, Any] | None = None,
        key: str | None = None,
    ) -> StructureDetermination:
        wtr = make_workflow_tool_release(self.session)
        row = StructureDetermination(
            **self.owner,
            conformer_observation_id=observation.id if observation is not None else None,
            target_kind=target,
            quantity=quantity,
            evaluated_geometry_id=(evaluated or self.geometry).id,
            workflow_tool_release_id=wtr.id,
            determination_key=key or uuid4().hex[:12],
            energy_convention=(
                {"zero_point_treatment": "composite_recipe"}
                if quantity is StructureDeterminationQuantity.zero_kelvin_energy
                else None
            ),
            actual_recipe=recipe,
            identity_hash=hashlib.sha256(uuid4().bytes).hexdigest(),
            content_hash=hashlib.sha256(uuid4().bytes).hexdigest(),
        )
        self.session.add(row)
        self.session.flush()
        self.session.execute(
            text("SELECT set_config('tckdb.structure_determination_writing', :id, true)"), {"id": str(row.id)}
        )
        for role, calc in sources:
            self.session.add(
                StructureDeterminationSource(
                    determination_id=row.id,
                    role=StructureSourceRole(role),
                    calculation_id=calc.id,
                    conformer_observation_id=row.conformer_observation_id,
                    **self.owner,
                )
            )
        self.session.flush()
        self.session.execute(text("SELECT set_config('tckdb.structure_determination_writing', '', true)"))
        self.settle(entry=False)  # the entry is accepted last: nothing may be added under it afterwards
        self.session.refresh(row)
        return row

    def finding(
        self,
        *,
        kind: StructureFindingKind = StructureFindingKind.identity_incompatibility,
        verdict: StructureFindingVerdict = StructureFindingVerdict.invalidates,
        authority: StructureFindingAuthority = StructureFindingAuthority.authorized_adjudication,
        calculation: Calculation | None = None,
        geometry: Geometry | None = None,
        determination: StructureDetermination | None = None,
        role: StructureSourceRole | None = None,
        supersedes: StructureEvidenceFinding | None = None,
        version: int = 1,
    ) -> StructureEvidenceFinding:
        scope = (
            StructureFindingScope.calculation
            if calculation is not None
            else StructureFindingScope.geometry
            if geometry is not None
            else StructureFindingScope.determination
        )
        row = StructureEvidenceFinding(
            kind=kind,
            scope=scope,
            subject_calculation_id=calculation.id if calculation is not None else None,
            subject_geometry_id=geometry.id if geometry is not None else None,
            subject_determination_id=determination.id if determination is not None else None,
            role=role,
            verdict=verdict,
            authority=authority,
            rationale="a stated reason",
            semantic_version=version,
            supersedes_finding_id=supersedes.id if supersedes is not None else None,
        )
        self.session.add(row)
        self.session.flush()
        return row


def make_world(
    session: Session, *, kind=None, entry_status: RecordReviewStatus | None = RecordReviewStatus.approved
) -> World:
    """A species entry that is approved (applied by :meth:`World.settle`, after its calculations exist)."""
    species = make_species(session, inchi_key=next_inchi_key())
    entry = make_species_entry(session, species) if kind is None else make_species_entry(session, species, kind=kind)
    return World(
        session=session, entry=entry, geometry=make_geometry(session), lot=make_lot(session), entry_status=entry_status
    )


def make_ts_world(session: Session, *, entry_status: RecordReviewStatus | None = RecordReviewStatus.approved) -> World:
    from tests.services.scientific_read._factories import (
        make_chem_reaction,
        make_reaction_entry,
        make_transition_state,
        make_transition_state_entry,
    )

    reactant = make_species(session, inchi_key=next_inchi_key())
    product = make_species(session, inchi_key=next_inchi_key())
    reactant_entry, product_entry = make_species_entry(session, reactant), make_species_entry(session, product)
    reaction = make_chem_reaction(session, reactants=[reactant], products=[product])
    reaction_entry = make_reaction_entry(
        session, reaction=reaction, reactant_entries=[reactant_entry], product_entries=[product_entry]
    )
    entry = make_transition_state_entry(session, transition_state=make_transition_state(session, reaction_entry=reaction_entry))
    return World(
        session=session,
        entry=entry,
        geometry=make_geometry(session),
        lot=make_lot(session),
        ts=True,
        entry_status=entry_status,
    )


def curated():
    """The curated read profile: the floor is ``approved`` and nothing below it is part of the request's world."""
    return set_current_read_profile(
        ResolvedReadProfile(profile=ReadProfile.curated, recommendation=ProfileRecommendation.approved_floor_only)
    )


__all__ = [
    "FULL_DECLARATION",
    "G1",
    "G2",
    "World",
    "calc",
    "codes",
    "curated",
    "declaration",
    "det",
    "finding_facts",
    "freq",
    "level",
    "make_ts_world",
    "make_world",
    "minimum_request",
    "request",
    "reset_current_read_profile",
    "saddle_request",
    "source",
    "subject",
]
