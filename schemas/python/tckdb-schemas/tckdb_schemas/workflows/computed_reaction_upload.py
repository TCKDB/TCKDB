"""Unified big-payload upload for elementary kinetics from one Arkane run.

A single request contains everything produced by one computational kinetics
workflow: species (with conformers, geometries, calculations, thermo),
a reaction, an optional transition state, and one or more kinetics fits.

All nested objects use local string keys so the backend can wire FK
relationships without exposing database IDs in the user-facing API.

Key uniqueness: calculation and geometry keys are globally unique.
Species and TS keys are unique within their own collections.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Self

from pydantic import Field, field_validator, model_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import (
    ActivationEnergyUnits,
    ArrheniusAUnits,
    CalculationType,
    KineticsCalculationRole,
    KineticsDegeneracyConvention,
    KineticsModelKind,
    KineticsUncertaintyKind,
    MoleculeKind,
    PressureContext,
    ReactionRole,
    TunnelingModel,
    RigidRotorKind,
    ScientificOriginKind,
    StatmechTreatmentKind,
    TorsionTreatmentKind,
)
from tckdb_schemas.fragments.calculation import (
    CalculationConstraintCreate,
    CalculationWithResultsPayload,
    IRCResultPayload,
    OutputGeometryEntry,
    PathSearchResultPayload,
)
from tckdb_schemas.fragments.geometry import GeometryPayload
from tckdb_schemas.fragments.reaction_atom_map import (
    AtomMapParticipantGeometry,
    ReactionAtomMapIn,
    validate_reaction_atom_map,
)
from tckdb_schemas.fragments.identity import (
    SpeciesEntryIdentityPayload,
    raise_for_atomless_structure,
)
from tckdb_schemas.fragments.refs import (
    FreqScaleFactorRef,
    SoftwareReleaseRef,
    WorkflowToolReleaseRef,
)
from tckdb_schemas.fragments.scan import CalculationScanResultCreate
from tckdb_schemas.fragments.ts_validation_evidence import (
    TransitionStateValidationEvidenceIn,
    validate_ts_evidence_set,
)
from tckdb_schemas.literature import LiteratureUploadRequest
from tckdb_schemas.reaction_family import find_canonical_reaction_family
from tckdb_schemas.shared.calculation_in import (
    CalculationIn as _BaseCalculationIn,
    GeometryIn,
    calculation_in_to_with_results_payload as _base_calc_to_payload,
    freq_evidence,
    frequency_completeness_findings,
    transition_state_frequency_findings,
)
from tckdb_schemas.stationary_point import (
    StationaryPointFinding,
    evaluate_species_entry_frequency,
    raise_for_blocking_findings,
)
from tckdb_schemas.statmech_bits import StatmechTorsionCoordinateIn
from tckdb_schemas.thermo import ThermoNASACreate, ThermoPointCreate
from tckdb_schemas.utils import normalize_optional_text, normalize_tunneling_model
from tckdb_schemas.workflows.computed_species_upload import (
    AppliedEnergyCorrectionInBundle,
    CalculationDependencyInBundle,
    StatmechSourceCalcInBundle,
    ThermoSourceCalcInBundle,
)


# ---------------------------------------------------------------------------
# Calculation payload (computed-reaction-specific extension)
# ---------------------------------------------------------------------------


class ComputedReactionCalculationIn(_BaseCalculationIn):
    """Bundle-local calculation block for the computed-reaction endpoint.

    Extends the shared ``CalculationIn`` with three producer-controlled
    provenance fields:

    * ``input_geometries`` — geometries this calculation actually ran on.
    * ``output_geometries`` — geometries this calculation produced or
      reported, each tagged with its scientific role.
    * ``depends_on`` — explicit local-key dependency edges (in addition
      to the workflow's auto-edges from additional calcs to their primary
      opt).

    The fields are not part of the shared ``CalculationIn`` because the
    network-PDep workflow does not currently honor them; accepting them
    there would silently drop producer-declared data. Lift into the
    shared shape only after network-PDep persists them too.
    """

    input_geometries: list[GeometryPayload] = Field(default_factory=list)
    output_geometries: list[OutputGeometryEntry] = Field(default_factory=list)
    depends_on: list[CalculationDependencyInBundle] = Field(default_factory=list)

    irc_result: IRCResultPayload | None = None
    path_search_result: PathSearchResultPayload | None = None
    scan_result: CalculationScanResultCreate | None = None

    constraints: list[CalculationConstraintCreate] = Field(
        default_factory=list,
        description=(
            "Coordinate constraints held fixed during this calculation. "
            "Generic across opt, freq, sp, irc, path_search, scan — "
            "input/provenance metadata that does not require a result "
            "block. For scan calcs, frozen coordinates may be declared "
            "here while the stepped coordinate is declared on "
            "scan_result.coordinates. The two lists must not duplicate "
            "the same constraint_index."
        ),
    )

    @model_validator(mode="after")
    def validate_result_matches_type(self) -> Self:
        """Mirror ``CalculationWithResultsPayload.validate_result_matches_type``
        for the ``irc_result`` / ``path_search_result`` / ``scan_result``
        fields. The base ``CalculationIn`` has no result-block matrix to
        validate, and the adapter uses ``model_copy(update=...)`` which
        bypasses pydantic validators — so this is the only place where
        mismatched ``(type, irc_result|path_search_result|scan_result)``
        pairs reject as 422 before hitting the persistence seam.

        ``scan_result`` is bundle-only and persisted by the workflow via
        ``persist_calculation_scan`` after the calculation row is created;
        the primitive payload does not carry it.
        """
        if self.irc_result is not None and self.type != CalculationType.irc:
            raise ValueError(
                f"irc_result is only allowed for calculation type 'irc', "
                f"got '{self.type.value}'."
            )
        if (
            self.path_search_result is not None
            and self.type != CalculationType.path_search
        ):
            raise ValueError(
                f"path_search_result is only allowed for calculation type "
                f"'path_search', got '{self.type.value}'."
            )
        if self.scan_result is not None and self.type != CalculationType.scan:
            raise ValueError(
                f"scan_result is only allowed for calculation type 'scan', "
                f"got '{self.type.value}'."
            )
        if self.type == CalculationType.scan:
            for forbidden in (
                "sp_electronic_energy_hartree",
                "opt_converged",
                "opt_n_steps",
                "opt_final_energy_hartree",
                "freq_n_imag",
                "freq_imag_freq_cm1",
                "freq_zpe_hartree",
                "freq_frequencies_cm1",
            ):
                if getattr(self, forbidden) is not None:
                    raise ValueError(
                        f"Field '{forbidden}' is not allowed for "
                        f"calculation type 'scan'. Use 'scan_result' "
                        f"to carry scan data."
                    )
            if (
                self.irc_result is not None
                or self.path_search_result is not None
            ):
                raise ValueError(
                    "irc_result/path_search_result are not allowed for "
                    "calculation type 'scan'. Use 'scan_result' instead."
                )
        return self

    @model_validator(mode="after")
    def validate_constraint_indices_union_unique(self) -> Self:
        """Enforce constraint_index uniqueness across the union of
        top-level ``constraints`` and ``scan_result.constraints``.

        Both lists land in the same ``calculation_constraint`` table and
        share the ``(calculation_id, constraint_index)`` composite PK,
        so a duplicate would otherwise surface as an opaque DB error at
        flush time.
        """
        seen: set[int] = set()
        for items in (
            self.constraints,
            self.scan_result.constraints if self.scan_result else [],
        ):
            for c in items:
                if c.constraint_index in seen:
                    raise ValueError(
                        f"constraint_index {c.constraint_index} is "
                        f"declared more than once across constraints + "
                        f"scan_result.constraints."
                    )
                seen.add(c.constraint_index)
        return self


def calculation_in_to_with_results_payload(
    calc_in: ComputedReactionCalculationIn,
) -> CalculationWithResultsPayload:
    """Adapt a computed-reaction ``ComputedReactionCalculationIn`` to the
    shared upload shape.

    Forwards the three producer-declared provenance fields onto the
    shared ``CalculationWithResultsPayload`` so the existing calculation
    persistence seam writes the corresponding rows. The base converter
    handles type/result/parameter mapping unchanged.
    """
    base = _base_calc_to_payload(calc_in)
    return base.model_copy(
        update={
            "input_geometries": list(calc_in.input_geometries),
            "output_geometries": list(calc_in.output_geometries),
            "irc_result": calc_in.irc_result,
            "path_search_result": calc_in.path_search_result,
            "constraints": list(calc_in.constraints),
        }
    )


class ConformerIn(SchemaBase):
    """A conformer in a computed-reaction bundle.

    Mirrors the network_pdep ``ConformerIn`` but binds the primary
    calculation to ``ComputedReactionCalculationIn`` so the producer can
    declare ``input_geometries``, ``output_geometries``, and
    ``depends_on`` on the primary opt as well.
    """

    key: str = Field(min_length=1)
    geometry: GeometryIn
    calculation: ComputedReactionCalculationIn
    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed
    label: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.label = normalize_optional_text(self.label)
        self.note = normalize_optional_text(self.note)
        return self

    @model_validator(mode="after")
    def validate_primary_calc_is_opt(self) -> Self:
        if self.calculation.type != CalculationType.opt:
            raise ValueError(
                f"Conformer '{self.key}' primary calculation must be type 'opt', "
                f"got '{self.calculation.type.value}'."
            )
        return self


# ---------------------------------------------------------------------------
# Thermo (inline, per-species)
# ---------------------------------------------------------------------------


class BundleThermoIn(SchemaBase):
    """Thermo data attached to a species in this bundle.

    The reaction route's thermo carries the same provenance the species
    route's ``ThermoInBundle`` carries, because it is the same claim about
    the same kind of row. The two models were written three days apart in
    April 2026 and diverged from the start — this one first, with eight
    fields, and never touched since; ``ThermoInBundle`` three days later
    with fifteen. Nothing recorded a reason, and the gap was not visible
    from either model, so a depositor putting thermo on a reaction bundle
    silently lost the record of which calculations produced it while the
    same deposit on the species route kept it.

    ``applied_energy_corrections`` is deliberately **not** here. The
    reaction bundle already declares applied corrections one level up, on
    :class:`BundleSpeciesIn`, against the same resolved species entry.
    Adding a second place to say it would let one deposit make the claim
    twice, and the answer to "which one counts" would have to be invented.

    :param scientific_origin: Scientific origin category.
    :param literature: Optional literature provenance for this thermo.
    :param software_release: Optional analysis-code provenance. Overrides
        the bundle-level ``analysis_software_release`` for this species.
    :param workflow_tool_release: Optional workflow-tool provenance.
        Overrides the bundle-level value for this species.
    :param h298_kj_mol: Enthalpy at 298 K in kJ/mol.
    :param s298_j_mol_k: Entropy at 298 K in J/(mol*K).
    :param h298_uncertainty_kj_mol: Optional uncertainty on ``h298_kj_mol``.
    :param s298_uncertainty_j_mol_k: Optional uncertainty on
        ``s298_j_mol_k``.
    :param tmin_k: Minimum temperature in K.
    :param tmax_k: Maximum temperature in K.
    :param nasa: Optional NASA polynomial coefficients.
    :param points: Optional tabulated thermo data points.
    :param source_calculations: Thermo → calc links by bundle-local
        calculation key. Each key must resolve in the bundle's global
        calc-key namespace and must name a calculation owned by this
        species entry; the workflow rejects both failures.
    :param note: Optional note.
    """

    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed

    literature: LiteratureUploadRequest | None = None
    software_release: SoftwareReleaseRef | None = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None

    h298_kj_mol: float | None = None
    s298_j_mol_k: float | None = None
    h298_uncertainty_kj_mol: float | None = Field(default=None, ge=0)
    s298_uncertainty_j_mol_k: float | None = Field(default=None, ge=0)
    tmin_k: float | None = Field(default=None, gt=0)
    tmax_k: float | None = Field(default=None, gt=0)
    nasa: ThermoNASACreate | None = None
    points: list[ThermoPointCreate] = Field(default_factory=list)
    source_calculations: list[ThermoSourceCalcInBundle] = Field(
        default_factory=list
    )
    note: str | None = None

    @model_validator(mode="after")
    def validate_unique_source_calculation_pairs(self) -> Self:
        """One (calculation, role) pair may be claimed once.

        Mirrors ``ThermoInBundle``: the same rule guards the same table
        constraint, and two upload routes disagreeing about it would mean
        one of them turns a nameable 422 into an ``IntegrityError``.
        """
        seen = {(sc.calculation_key, sc.role) for sc in self.source_calculations}
        if len(seen) != len(self.source_calculations):
            raise ValueError(
                "thermo.source_calculations must not repeat the same "
                "(calculation_key, role) pair."
            )
        return self

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self

    @model_validator(mode="after")
    def validate_temperature_range(self) -> Self:
        if (
            self.tmin_k is not None
            and self.tmax_k is not None
            and self.tmin_k > self.tmax_k
        ):
            raise ValueError("tmin_k must be <= tmax_k.")
        return self


# ---------------------------------------------------------------------------
# Statmech (inline, per-species)
# ---------------------------------------------------------------------------


class BundleStatmechTorsionIn(SchemaBase):
    """One torsional mode in a statmech record.

    Carries the slim metadata (index, symmetry, treatment kind) plus
    optional structured coordinate definitions so producers can persist
    rotor atom quartets through the bundle endpoint without falling back
    to ``/uploads/statmech``. ``coordinates`` is optional: omit it to
    keep current behavior (no ``statmech_torsion_definition`` rows).

    :param torsion_index: One-based torsion index.
    :param symmetry_number: Optional torsional symmetry number.
    :param treatment_kind: Optional torsion treatment.
    :param dimension: Number of coupled torsional coordinates.
    :param top_description: Optional description of the rotating top.
    :param source_scan_calculation_key: Optional bundle-local calc key
        that produced the rotor scan. Must resolve to a calc of type
        ``scan`` declared elsewhere in the bundle.
    :param coordinates: Atom-quartet definitions for each coordinate.
        When non-empty, ``len(coordinates)`` must equal ``dimension``
        and ``coordinate_index`` values must run contiguously
        ``1..dimension``.
    """

    torsion_index: int = Field(ge=1)
    symmetry_number: int | None = Field(default=None, ge=1)
    treatment_kind: TorsionTreatmentKind | None = None

    dimension: int = Field(default=1, ge=1)
    top_description: str | None = None
    source_scan_calculation_key: str | None = None

    coordinates: list[StatmechTorsionCoordinateIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_coordinates(self) -> Self:
        if not self.coordinates:
            return self
        if len(self.coordinates) != self.dimension:
            raise ValueError(
                "Number of torsion coordinates must equal dimension."
            )
        indices = [c.coordinate_index for c in self.coordinates]
        if len(set(indices)) != len(indices):
            raise ValueError("Torsion coordinate_index values must be unique.")
        if sorted(indices) != list(range(1, self.dimension + 1)):
            raise ValueError(
                "Torsion coordinate_index values must run contiguously "
                "from 1..dimension."
            )
        return self


class BundleStatmechIn(SchemaBase):
    """Statistical mechanics properties for a species in this bundle.

    The reaction route's statmech carries the same fields the species
    route's ``StatmechInBundle`` carries, for the reason
    :class:`BundleThermoIn` gives: it is the same claim about the same
    kind of row, written against the same table.

    The gap this closes had two distinct causes, and only one of them is
    the same as thermo's. This model was born narrow in ``17706cf7`` --
    congenital, like ``BundleThermoIn``. But the three rotational
    constants were added to ``StatmechInBundle`` **only**, by
    ``264519f1``, two days after ``0ea9182f`` had correctly updated both
    models in one commit. The habit was right and then it lapsed, and
    nothing in the tree could notice: no test compared the two field
    sets, so a one-sided addition read exactly like a deliberate one.
    ``tests/schemas/test_bundle_root_model_symmetry.py`` is now that
    test, and an intentional divergence has to be written down in its
    allowlist with a reason rather than merely happening.

    Nothing is deliberately withheld here. ``applied_energy_corrections``
    -- the one field :class:`BundleThermoIn` refuses, because
    :class:`BundleSpeciesIn` already declares it against the same species
    entry -- is not on ``StatmechInBundle`` either, so it is not part of
    this divergence and needs no exception.

    ``literature``, ``software_release`` and ``workflow_tool_release``
    are per-species overrides of the bundle-level
    ``analysis_software_release`` / ``workflow_tool_release`` /
    ``literature``, exactly as thermo's are. Before this, a reaction
    bundle's statmech took the bundle-level values with no way to say
    that one species' partition function came from a different analysis
    code -- which is the ordinary case when one participant is taken
    from a paper and the rest were computed here.

    :param scientific_origin: Scientific origin category.
    :param literature: Optional literature provenance for this statmech.
    :param software_release: Optional analysis-code provenance. Overrides
        the bundle-level ``analysis_software_release`` for this species.
    :param workflow_tool_release: Optional workflow-tool provenance.
        Overrides the bundle-level value for this species.
    :param is_linear: Whether the molecule is linear.
    :param rigid_rotor_kind: Rotational treatment classification.
    :param external_symmetry: External symmetry number.
    :param optical_isomers: Number of optical isomers (>= 1).
    :param point_group: Optional point-group label (e.g. ``"C2v"``).
    :param statmech_treatment: Overall statmech treatment classification.
    :param rotational_constant_a_cm1: First reported principal rotational
        constant (cm^-1), in source-provided order (conventionally
        descending A >= B >= C). Optional.
    :param rotational_constant_b_cm1: Second reported principal rotational
        constant (cm^-1). Optional.
    :param rotational_constant_c_cm1: Third reported principal rotational
        constant (cm^-1). Optional.
    :param freq_scale_factor: Frequency scale factor applied.
    :param uses_projected_frequencies: Whether projected frequencies were used.
    :param source_calculations: Statmech → calc links by bundle-local
        calculation key. Each referenced key must resolve into the
        bundle's global calc-key namespace and must be owned by this
        species entry (workflow-layer ownership check).
    :param torsions: Torsional modes.
    :param note: Optional note.
    """

    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed

    literature: LiteratureUploadRequest | None = None
    software_release: SoftwareReleaseRef | None = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None

    is_linear: bool | None = None
    rigid_rotor_kind: RigidRotorKind | None = None
    external_symmetry: int | None = Field(default=None, ge=1)
    optical_isomers: int | None = Field(default=None, ge=1)
    point_group: str | None = None
    statmech_treatment: StatmechTreatmentKind | None = None

    rotational_constant_a_cm1: float | None = Field(default=None, gt=0)
    rotational_constant_b_cm1: float | None = Field(default=None, gt=0)
    rotational_constant_c_cm1: float | None = Field(default=None, gt=0)

    freq_scale_factor: FreqScaleFactorRef | None = None
    uses_projected_frequencies: bool | None = None
    source_calculations: list[StatmechSourceCalcInBundle] = Field(default_factory=list)
    torsions: list[BundleStatmechTorsionIn] = Field(default_factory=list)
    note: str | None = None

    @model_validator(mode="after")
    def normalize_point_group(self) -> Self:
        self.point_group = normalize_optional_text(self.point_group)
        return self

    @model_validator(mode="after")
    def validate_unique_source_calculation_pairs(self) -> Self:
        pairs = [(sc.calculation_key, sc.role) for sc in self.source_calculations]
        if len(set(pairs)) != len(pairs):
            raise ValueError(
                "statmech.source_calculations must be unique by "
                "(calculation_key, role)."
            )
        return self

    @model_validator(mode="after")
    def validate_unique_torsion_indices(self) -> Self:
        indices = [t.torsion_index for t in self.torsions]
        if len(set(indices)) != len(indices):
            raise ValueError(
                "Statmech torsion_index values must be unique within a species."
            )
        return self

    @model_validator(mode="after")
    def validate_scientific_interpretation(self) -> Self:
        """Scope each requirement to the claim this record actually makes.

        ``statmech_treatment`` is deliberately NOT required. Real producers
        (ARC among them) deposit symmetry, rotational constants and a
        frequency scale factor without naming a treatment; an absent field is
        honest where an invented one would not be.

        Source calculations are likewise not required here. A monatomic
        species has no vibrational modes to point at, and an experimental,
        literature or imported statmech has no calculation at all — forcing
        either to fabricate one buys nothing. Their absence on a *computed*
        record is reported as a structured upload warning at the workflow
        seam, and is enforced as an error only where a rate coefficient
        actually depends on it (the kinetics interpretation seam).

        What IS enforced is the one claim that is self-contradictory when
        unsupported: a rotor-aware treatment is *defined* by the internal
        rotors it treats, so it must list them. With no torsions the correct
        treatment is plain ``rrho``.
        """
        rotor_aware = {"rrho_1d", "rrho_nd", "rrho_1d_nd"}
        if (
            self.statmech_treatment is not None
            and self.statmech_treatment.value in rotor_aware
            and not self.torsions
        ):
            raise ValueError(
                f"statmech_treatment='{self.statmech_treatment.value}' claims a "
                "rotor-aware treatment and must list the torsions it treated; "
                "use 'rrho' when the species has none."
            )
        return self


# ---------------------------------------------------------------------------
# Species (with conformers, calculations, thermo)
# ---------------------------------------------------------------------------


class BundleSpeciesIn(SchemaBase):
    """A species defined within this kinetics bundle.

    :param key: Local key for referencing this species in the reaction.
    :param species_entry: Species identity (SMILES, charge, multiplicity).
    :param conformers: Conformer observations (geometry + opt calculation). Each
        list item creates a distinct observation row, even when multiple items
        land in the same conformer group.
    :param calculations: Additional calculations (freq, sp at higher LOT). Their
        ``geometry_key`` must reference one of this species's conformer
        geometries so the backend can anchor them to the correct observation.
    :param thermo: Optional thermochemistry data.
    """

    key: str = Field(min_length=1)
    species_entry: SpeciesEntryIdentityPayload
    conformers: list[ConformerIn] = Field(default_factory=list)
    calculations: list[ComputedReactionCalculationIn] = Field(default_factory=list)
    thermo: BundleThermoIn | None = None
    statmech: BundleStatmechIn | None = None
    applied_energy_corrections: list[AppliedEnergyCorrectionInBundle] = Field(
        default_factory=list,
        description=(
            "Applied energy corrections targeting this species's resolved "
            "species_entry. Use for scheme-backed corrections such as AEC "
            "totals (application_role=aec_total) and BAC totals "
            "(application_role=bac_total). ``source_calculation_key`` "
            "resolves against the bundle's global calculation namespace; "
            "the workflow rejects 422 when the referenced calc is not "
            "owned by this species."
        ),
    )

    def structure_locations(self) -> list[str]:
        """Field paths at which this species actually supplied coordinates.

        Every way a geometry can reach the database through this model, in one
        place, so the atomless check below judges what would really be stored
        rather than the one field that prompted it. A conformer always carries
        a geometry, so its mere presence is structure; a calculation carries
        structure when it names one, ran on one, or reported one.
        """

        locations: list[str] = []
        for conformer in self.conformers:
            locations.append(f"conformers['{conformer.key}'].geometry")
        for conformer in self.conformers:
            locations.extend(
                self._calculation_structure_locations(
                    conformer.calculation,
                    path=f"conformers['{conformer.key}'].calculation",
                )
            )
        for calc in self.calculations:
            locations.extend(
                self._calculation_structure_locations(
                    calc, path=f"calculations['{calc.key}']"
                )
            )
        return locations

    @staticmethod
    def _calculation_structure_locations(
        calc: ComputedReactionCalculationIn, *, path: str
    ) -> list[str]:
        locations: list[str] = []
        if calc.geometry_key is not None:
            locations.append(f"{path}.geometry_key")
        if calc.input_geometries:
            locations.append(f"{path}.input_geometries")
        if calc.output_geometries:
            locations.append(f"{path}.output_geometries")
        return locations

    @model_validator(mode="after")
    def validate_atomless_species_carries_no_structure(self) -> Self:
        """Refuse a geometry deposited against a participant with no atoms.

        Definitional, therefore blocking (ADR 0008). This model is where a
        declared ``molecule_kind`` and the conformers deposited under it are
        both in hand for the first time, so it is the earliest place the
        contradiction can be named with the key that caused it.
        """

        raise_for_atomless_structure(
            self.species_entry.molecule_kind,
            subject=f"Species '{self.key}'",
            structure_locations=self.structure_locations(),
        )
        return self

    @model_validator(mode="after")
    def validate_calc_geometry_keys(self) -> Self:
        for calc in self.calculations:
            if calc.type != CalculationType.opt and calc.geometry_key is None:
                raise ValueError(
                    f"Species '{self.key}' calculation '{calc.key}' "
                    f"(type={calc.type.value}) requires geometry_key."
                )
        return self

    @model_validator(mode="after")
    def validate_calc_geometry_belongs_to_conformer(self) -> Self:
        """Require species-side calculations to reference one of this species's conformers."""
        conformer_geometry_keys = {conf.geometry.key for conf in self.conformers}
        for calc in self.calculations:
            if calc.geometry_key is None:
                continue
            if calc.geometry_key not in conformer_geometry_keys:
                raise ValueError(
                    f"Species '{self.key}' calculation '{calc.key}' geometry_key "
                    f"must reference one of that species's conformer geometries."
                )
        return self

    def stationary_point_findings(self) -> list[StationaryPointFinding]:
        """Judge this species's declared kind against its own frequency evidence.

        This sits on the *species* model rather than on the request
        because the bundle also carries a transition state, whose single
        imaginary mode is correct science; a request-level scan that
        ignored which entity owns the frequency would wrongly reject it.
        """
        kind = self.species_entry.species_entry_kind
        xyz_by_geometry_key = {
            conformer.geometry.key: conformer.geometry.xyz_text
            for conformer in self.conformers
        }
        findings: list[StationaryPointFinding] = []
        for conformer in self.conformers:
            location = (
                f"species['{self.key}'].conformers['{conformer.key}']"
                f".calculation"
            )
            n_imag, imag_freq_cm1 = freq_evidence(conformer.calculation)
            findings.extend(
                evaluate_species_entry_frequency(
                    kind, n_imag, imag_freq_cm1, location=location
                )
            )
            findings.extend(
                frequency_completeness_findings(
                    conformer.calculation,
                    location=f"{location}.freq_frequencies_cm1",
                    xyz_text=conformer.geometry.xyz_text,
                )
            )
        for calc in self.calculations:
            location = f"species['{self.key}'].calculations['{calc.key}']"
            n_imag, imag_freq_cm1 = freq_evidence(calc)
            findings.extend(
                evaluate_species_entry_frequency(
                    kind, n_imag, imag_freq_cm1, location=location
                )
            )
            findings.extend(
                frequency_completeness_findings(
                    calc,
                    location=f"{location}.freq_frequencies_cm1",
                    # ``validate_calc_geometry_belongs_to_conformer`` has
                    # already refused a key that names anything else, so a
                    # miss here means the calculation named no geometry at
                    # all and the check declines to speak.
                    xyz_text=xyz_by_geometry_key.get(calc.geometry_key or ""),
                )
            )
        return findings

    @model_validator(mode="after")
    def validate_n_imag_matches_species_entry_kind(self) -> Self:
        """Refuse frequency evidence that contradicts the declared kind.

        Definitional, therefore blocking (ADR 0008). This model is the
        earliest point at which one species entry's declared
        stationary-point kind and that same entry's own frequency
        evidence are both in hand.
        """
        raise_for_blocking_findings(self.stationary_point_findings())
        return self


# ---------------------------------------------------------------------------
# Reaction participants
# ---------------------------------------------------------------------------


class BundleReactionParticipant(SchemaBase):
    """A participant in the reaction, referenced by species key.

    :param species_key: Local key referencing a species in the bundle.
    :param note: Optional note.
    """

    species_key: str = Field(min_length=1)
    note: str | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self


# ---------------------------------------------------------------------------
# Transition state
# ---------------------------------------------------------------------------


class BundleTransitionStateIn(SchemaBase):
    """Transition state for the reaction in this bundle.

    :param charge: Net charge of the TS structure.
    :param multiplicity: Spin multiplicity.
    :param unmapped_smiles: Optional SMILES for the TS.
    :param geometry: Saddle-point geometry.
    :param calculation: Primary opt calculation.
    :param calculations: Additional calculations (freq, sp, irc).
    :param label: Optional label.
    :param note: Optional note.
    """

    charge: int
    multiplicity: int = Field(ge=1)
    unmapped_smiles: str | None = None
    geometry: GeometryIn
    calculation: ComputedReactionCalculationIn
    calculations: list[ComputedReactionCalculationIn] = Field(default_factory=list)
    applied_energy_corrections: list[AppliedEnergyCorrectionInBundle] = Field(
        default_factory=list,
        description=(
            "Applied energy corrections targeting the resolved "
            "transition_state_entry directly. TS-side corrections are "
            "never stored as reaction-entry corrections. "
            "``source_calculation_key`` resolves against the bundle's "
            "global calculation namespace; the workflow rejects 422 "
            "when the referenced calc is not owned by this transition "
            "state."
        ),
    )
    validation_evidence: list[TransitionStateValidationEvidenceIn] = Field(
        default_factory=list,
        description=(
            "Structured IRC evidence that this saddle point connects the "
            "bundle's declared reactants and products. Optional but strongly "
            "recommended: a deposit without it succeeds and returns a "
            "'transition_state_missing_irc_evidence' upload warning. "
            "``source_calculation_key`` names an irc calculation owned by this "
            "transition state."
        ),
    )
    label: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.label = normalize_optional_text(self.label)
        self.note = normalize_optional_text(self.note)
        self.unmapped_smiles = normalize_optional_text(self.unmapped_smiles)
        return self

    @model_validator(mode="after")
    def validate_primary_is_opt(self) -> Self:
        if self.calculation.type != CalculationType.opt:
            raise ValueError(
                f"TS primary calculation must be type 'opt', "
                f"got '{self.calculation.type.value}'."
            )
        return self

    def stationary_point_findings(self) -> list[StationaryPointFinding]:
        """Judge this transition state against its own frequency evidence."""
        findings: list[StationaryPointFinding] = []
        for calc in (self.calculation, *self.calculations):
            location = f"transition_state.calculations['{calc.key}']"
            findings.extend(
                transition_state_frequency_findings(calc, location=location)
            )
            findings.extend(
                frequency_completeness_findings(
                    calc,
                    location=f"{location}.freq_frequencies_cm1",
                    xyz_text=self.geometry.xyz_text,
                )
            )
        return findings

    @model_validator(mode="after")
    def validate_reaction_coordinate_contract(self) -> Self:
        """Refuse frequency evidence with no usable reaction coordinate.

        Definitional, therefore blocking (ADR 0008). A transition state
        does not carry a ``stationary_point_kind`` — the entity is the
        claim — so this model is where the declaration and the evidence
        meet. ADR 0012 narrowed what is definitional here: not the
        *count* of imaginary modes, but that there is at least one, that
        exactly one is designated the reaction coordinate, and that no
        undeclared mode is stiff enough to make that designation
        meaningless.
        """
        raise_for_blocking_findings(self.stationary_point_findings())
        return self

    @model_validator(mode="after")
    def validate_evidence_source_is_a_ts_irc_calculation(self) -> Self:
        """Evidence must name an ``irc`` calculation owned by this TS.

        Participant/atom completeness needs the bundle's reaction, which this
        nested model cannot see; the enclosing request validates that.
        """
        if not self.validation_evidence:
            return self
        own_types = {
            self.calculation.key: self.calculation.type,
            **{calc.key: calc.type for calc in self.calculations},
        }
        for record in self.validation_evidence:
            if record.source_calculation_key is None:
                raise ValueError(
                    "transition_state.validation_evidence requires "
                    "source_calculation_key naming its irc calculation."
                )
            calculation_type = own_types.get(record.source_calculation_key)
            if calculation_type is None:
                raise ValueError(
                    "transition_state.validation_evidence references "
                    f"calculation_key '{record.source_calculation_key}', which is "
                    "not one of this transition state's own calculations."
                )
            if calculation_type != CalculationType.irc:
                raise ValueError(
                    "transition_state.validation_evidence requires an irc "
                    f"calculation; '{record.source_calculation_key}' is "
                    f"'{calculation_type.value}'."
                )
        return self


# ---------------------------------------------------------------------------
# Kinetics fit
# ---------------------------------------------------------------------------


class KineticsSourceCalculationIn(SchemaBase):
    """A producer-declared link from a kinetics fit to a supporting calc.

    The calculation is identified by its bundle-local key. ``role`` ties
    the calculation to the scientific role it plays in supporting the
    fit (reactant_energy, ts_energy, freq, irc, master_equation,
    fit_source, ...). Role/type/owner compatibility is enforced at the
    workflow layer.
    """

    calculation_key: str = Field(min_length=1)
    role: KineticsCalculationRole


class BundleKineticsIn(SchemaBase):
    """One kinetics fit (Arrhenius parameters) within the bundle.

    The reaction direction is determined by ``reactant_keys`` / ``product_keys``
    which reference species keys. For the forward direction, these match the
    bundle's reaction; for the reverse, they are swapped.

    :param reactant_keys: Species keys on the reactant side of this fit.
    :param product_keys: Species keys on the product side of this fit.
    :param scientific_origin: Scientific origin category.
    :param model_kind: Kinetics functional form.
    :param is_third_body: True for a simple ``+M`` third-body reaction (no falloff).
    :param a: Arrhenius pre-exponential factor.
    :param a_units: Units for A.
    :param n: Temperature exponent.
    :param reported_ea: Activation energy in reported units.
    :param reported_ea_units: Units for Ea.
    :param tmin_k: Minimum valid temperature.
    :param tmax_k: Maximum valid temperature.
    :param tunneling_model: Optional tunneling model label.
    :param degeneracy: Optional finite, strictly positive multiplicative
        reaction-path degeneracy associated with the reported kinetics
        expression. ``None`` means no claim is made; do not interpret it as
        ``1.0``.
    :param degeneracy_convention: Whether degeneracy is already included in
        the reported rate. Defaults to ``unknown`` for legacy producers.
    :param note: Optional note.
    """

    reactant_keys: list[str] = Field(min_length=1)
    product_keys: list[str] = Field(min_length=1)

    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed
    model_kind: KineticsModelKind = KineticsModelKind.modified_arrhenius
    is_third_body: bool = False

    a: float | None = None
    a_units: ArrheniusAUnits | None = None
    n: float | None = None
    reported_ea: float | None = None
    reported_ea_units: ActivationEnergyUnits | None = None

    a_uncertainty: float | None = None
    a_uncertainty_kind: KineticsUncertaintyKind | None = None
    n_uncertainty: float | None = None
    d_reported_ea: float | None = None

    tmin_k: float | None = Field(default=None, gt=0)
    tmax_k: float | None = Field(default=None, gt=0)

    degeneracy: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    degeneracy_convention: KineticsDegeneracyConvention = (
        KineticsDegeneracyConvention.unknown
    )
    tunneling_model: TunnelingModel | None = None
    pressure_context: PressureContext | None = None
    pressure_bar: float | None = Field(default=None, gt=0)
    note: str | None = None

    source_calculations: list[KineticsSourceCalculationIn] = Field(
        default_factory=list,
        description=(
            "Producer-declared kinetics provenance: each entry references "
            "a calculation by bundle-local key with a scientific role. "
            "When non-empty, the workflow writes exactly these "
            "kinetics_source_calculation rows and skips the legacy "
            "auto-link fallback. When empty, the workflow falls back to "
            "auto-linking species-owned SP calculations as "
            "reactant_energy / product_energy (legacy convenience)."
        ),
    )

    @field_validator("tunneling_model", mode="before")
    @classmethod
    def _normalize_tunneling(cls, v):
        return normalize_tunneling_model(v)

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self

    @model_validator(mode="after")
    def validate_pressure_context(self) -> Self:
        if (
            self.pressure_context == PressureContext.apparent_at_pressure
            and self.pressure_bar is None
        ):
            raise ValueError(
                "pressure_context='apparent_at_pressure' requires pressure_bar."
            )
        return self

    @model_validator(mode="after")
    def validate_unique_source_calculation_pairs(self) -> Self:
        seen: set[tuple[str, KineticsCalculationRole]] = set()
        for entry in self.source_calculations:
            pair = (entry.calculation_key, entry.role)
            if pair in seen:
                raise ValueError(
                    f"Duplicate kinetics source_calculations entry "
                    f"(calculation_key='{entry.calculation_key}', "
                    f"role='{entry.role.value}'). Each "
                    f"(calculation_key, role) pair must be declared at "
                    f"most once per kinetics fit."
                )
            seen.add(pair)
        return self

    @model_validator(mode="after")
    def validate_ea_pair(self) -> Self:
        if (self.reported_ea is None) != (self.reported_ea_units is None):
            raise ValueError(
                "reported_ea and reported_ea_units must both be provided or both omitted."
            )
        return self

    @model_validator(mode="after")
    def validate_a_uncertainty_kind(self) -> Self:
        has_value = self.a_uncertainty is not None
        has_kind = self.a_uncertainty_kind is not None
        if has_value != has_kind:
            raise ValueError(
                "a_uncertainty and a_uncertainty_kind must both be provided "
                "or both omitted."
            )
        if (
            self.a_uncertainty_kind == KineticsUncertaintyKind.multiplicative
            and self.a_uncertainty is not None
            and self.a_uncertainty < 1.0
        ):
            raise ValueError(
                "Multiplicative a_uncertainty must be >= 1.0 (factor f, "
                "with the true value within [A/f, A*f])."
            )
        return self

    @model_validator(mode="after")
    def validate_temperature_range(self) -> Self:
        if (
            self.tmin_k is not None
            and self.tmax_k is not None
            and self.tmin_k > self.tmax_k
        ):
            raise ValueError("tmin_k must be <= tmax_k.")
        return self

    @model_validator(mode="after")
    def validate_model_kind_is_representable(self) -> Self:
        """Reject functional forms the bundle payload cannot carry.

        ``BundleKineticsIn`` only holds the *scalar* Arrhenius fields
        (``a``/``n``/``reported_ea``). The pressure-dependent and
        multi-term forms (``multi_arrhenius``, ``plog``, ``troe``,
        ``sri``, ``lindemann``, ``chebyshev``) each require child data
        (sum-of-Arrhenius terms, PLOG entries, falloff broadening,
        Chebyshev coefficients) for which this schema has no fields.
        Accepting them here would persist a self-contradictory
        ``kinetics`` row — tagged e.g. ``plog`` but carrying zero PLOG
        entries. Direct those uploads to the dedicated single-reaction
        kinetics endpoint, whose schema carries the child rows.
        """
        representable = {
            KineticsModelKind.arrhenius,
            KineticsModelKind.modified_arrhenius,
        }
        if self.model_kind not in representable:
            unsupported = ", ".join(
                k.value for k in KineticsModelKind if k not in representable
            )
            raise ValueError(
                f"model_kind='{self.model_kind.value}' is not supported on the "
                f"bundle kinetics payload, which carries only scalar Arrhenius "
                f"parameters. Pressure-dependent and multi-term forms "
                f"({unsupported}) require child data; upload them via the "
                f"single-reaction kinetics endpoint instead."
            )
        return self


# ---------------------------------------------------------------------------
# Top-level bundle request
# ---------------------------------------------------------------------------


class ComputedReactionUploadRequest(SchemaBase):
    """Unified upload for elementary kinetics from one computational workflow.

    One payload = one Arkane run:
    - Species with conformers, calculations, and thermo
    - The reaction (reactants + products by species key)
    - Optional transition state with geometry and calculations
    - One or more kinetics fits (forward/reverse, with/without tunneling)
    - Provenance (literature, software, workflow tool)
    """

    # Provenance (shared across the bundle)
    literature: LiteratureUploadRequest | None = None
    software_release: SoftwareReleaseRef | None = None  # ESS software (e.g. Gaussian)
    analysis_software_release: SoftwareReleaseRef | None = None  # kinetics/thermo analysis code (e.g. Arkane, MESS)
    workflow_tool_release: WorkflowToolReleaseRef | None = None

    # Species definitions
    species: list[BundleSpeciesIn] = Field(min_length=1)

    # Reaction
    reversible: bool = True
    reaction_family: str | None = None
    reaction_family_source_note: str | None = None
    reactant_keys: list[str] = Field(min_length=1)
    product_keys: list[str] = Field(min_length=1)

    # Transition state
    transition_state: BundleTransitionStateIn | None = None

    # Kinetics fits (empty when Arkane fitting didn't complete)
    kinetics: list[BundleKineticsIn] = Field(default_factory=list)

    # Atom correspondence across the reaction (ADR 0011)
    atom_map: ReactionAtomMapIn | None = Field(
        default=None,
        description=(
            "Which atom of each reactant and product is which atom of the "
            "transition state. Supplied by the depositor and never derived "
            "here: TCKDB does not run a mapping algorithm, because several "
            "chemically distinct maps are usually consistent with the same "
            "reactants and products and choosing one by algorithm would "
            "manufacture provenance (ADR 0011). Optional — a reaction "
            "deposited without one succeeds and returns a "
            "'reaction_atom_map_absent' upload warning, because an unmapped "
            "reaction is incomplete rather than false. Requires a transition "
            "state: both legs of the map run toward the saddle point."
        ),
    )

    def atom_map_participants(self) -> list[AtomMapParticipantGeometry]:
        """Describe every declared participant and the geometries it may use.

        Assembled here because the atom map is the one part of the bundle that
        needs the reaction's participant slots *and* each species's geometries
        at the same time; neither nested model can see both.
        """

        species_by_key = {species.key: species for species in self.species}
        participants: list[AtomMapParticipantGeometry] = []
        for side, keys in (
            (ReactionRole.reactant, self.reactant_keys),
            (ReactionRole.product, self.product_keys),
        ):
            for position, species_key in enumerate(keys, start=1):
                species = species_by_key.get(species_key)
                if species is None:
                    # An undefined species key is ``validate_species_key_refs``'s
                    # to report; do not pre-empt it with a KeyError.
                    continue
                xyz_by_key = {
                    conformer.geometry.key: conformer.geometry.xyz_text
                    for conformer in species.conformers
                }
                participants.append(
                    AtomMapParticipantGeometry(
                        side=side,
                        species_key=species_key,
                        participant_index=position,
                        geometry_keys=frozenset(xyz_by_key),
                        xyz_by_geometry_key=xyz_by_key,
                        molecule_kind=species.species_entry.molecule_kind,
                    )
                )
        return participants

    @model_validator(mode="after")
    def validate_atom_map(self) -> Self:
        """Refuse a self-contradictory atom map; accept an incomplete one.

        Runs here rather than on a nested model because the map spans the
        reaction's participant slots, every participant's geometry, and the
        transition state's geometry — the same reason
        ``validate_ts_validation_evidence`` sits at this level.
        """
        if self.atom_map is None:
            return self
        validate_reaction_atom_map(
            self.atom_map,
            participants=self.atom_map_participants(),
            ts_geometry_key=(
                None
                if self.transition_state is None
                else self.transition_state.geometry.key
            ),
            ts_xyz_text=(
                None
                if self.transition_state is None
                else self.transition_state.geometry.xyz_text
            ),
        )
        return self

    def stationary_point_findings(self) -> list[StationaryPointFinding]:
        """Collect every entity's stationary-point findings in this bundle.

        Each species judges its own frequency evidence against its own
        declared kind, and the transition state judges its own — so the
        transition state's single imaginary mode never counts against a
        species and vice versa. The blocking half already fired from the
        nested models' validators by the time this is callable; the route
        layer calls it to harvest the warning half.
        """
        findings: list[StationaryPointFinding] = []
        for species in self.species:
            findings.extend(species.stationary_point_findings())
        if self.transition_state is not None:
            findings.extend(self.transition_state.stationary_point_findings())
        return findings

    def participant_molecule_kinds(
        self, species_keys: Sequence[str]
    ) -> list[MoleculeKind]:
        """The declared ``molecule_kind`` of each named participant, in order.

        Assembled here for the same reason ``atom_map_participants`` is: the
        reaction's participant slots live on this model and the kinds live on
        the nested species, and the evidence check needs them side by side to
        tell a participant that legitimately has no atoms (a free electron)
        from one whose atoms were simply left out.

        A key naming no declared species is reported by
        ``validate_species_key_refs``; it is treated as an ordinary molecule
        here so that this validator does not pre-empt that message with a
        harder-to-read one.
        """

        species_by_key = {species.key: species for species in self.species}
        return [
            species_by_key[key].species_entry.molecule_kind
            if key in species_by_key
            else MoleculeKind.molecule
            for key in species_keys
        ]

    @model_validator(mode="after")
    def validate_ts_validation_evidence(self) -> Self:
        """Check TS evidence against the bundle's reaction and TS geometry.

        Participant/atom completeness needs the reaction's participants, their
        declared kinds and the TS atom count, none of which the nested TS model
        can see.
        """
        if self.transition_state is None or not self.transition_state.validation_evidence:
            return self
        validate_ts_evidence_set(
            self.transition_state.validation_evidence,
            subject_label=self.transition_state.label or "transition state",
            xyz_text=self.transition_state.geometry.xyz_text,
            reactant_kinds=self.participant_molecule_kinds(self.reactant_keys),
            product_kinds=self.participant_molecule_kinds(self.product_keys),
        )
        return self

    @field_validator("reaction_family", "reaction_family_source_note")
    @classmethod
    def normalize_family(cls, value: str | None) -> str | None:
        return normalize_optional_text(value)

    @model_validator(mode="after")
    def validate_reaction_family(self) -> Self:
        if self.reaction_family is None:
            if self.reaction_family_source_note is not None:
                raise ValueError(
                    "reaction_family_source_note requires reaction_family."
                )
            return self
        if find_canonical_reaction_family(self.reaction_family) is None:
            if self.reaction_family_source_note is None:
                raise ValueError(
                    "reaction_family_source_note is required when reaction_family "
                    "is not a supported canonical family."
                )
        return self

    @model_validator(mode="after")
    def validate_unique_keys(self) -> Self:
        species_keys = [s.key for s in self.species]
        if len(set(species_keys)) != len(species_keys):
            raise ValueError("Species keys must be unique.")

        # Calc + geometry keys are globally unique
        all_calc_keys: list[str] = []
        all_geom_keys: list[str] = []
        for sp in self.species:
            for conf in sp.conformers:
                all_calc_keys.append(conf.calculation.key)
                all_geom_keys.append(conf.geometry.key)
            for calc in sp.calculations:
                all_calc_keys.append(calc.key)
        if self.transition_state:
            all_calc_keys.append(self.transition_state.calculation.key)
            all_geom_keys.append(self.transition_state.geometry.key)
            for calc in self.transition_state.calculations:
                all_calc_keys.append(calc.key)

        if len(set(all_calc_keys)) != len(all_calc_keys):
            raise ValueError("Calculation keys must be globally unique.")
        if len(set(all_geom_keys)) != len(all_geom_keys):
            raise ValueError("Geometry keys must be globally unique.")

        return self

    @model_validator(mode="after")
    def validate_species_key_refs(self) -> Self:
        species_keys = {s.key for s in self.species}
        for key in self.reactant_keys + self.product_keys:
            if key not in species_keys:
                raise ValueError(
                    f"Reaction references species key '{key}' which is not "
                    f"defined in the species list."
                )
        for kin in self.kinetics:
            for key in kin.reactant_keys + kin.product_keys:
                if key not in species_keys:
                    raise ValueError(
                        f"Kinetics fit references species key '{key}' which "
                        f"is not defined in the species list."
                    )
        return self

    @model_validator(mode="after")
    def validate_calculation_key_refs(self) -> Self:
        """Validate every local-key reference to a calculation resolves.

        Covers the three cross-reference surfaces introduced for the
        producer-controlled provenance work:

        * ``CalculationIn.depends_on[].parent_calculation_key`` — must
          name a calculation defined elsewhere in the bundle, and must
          not equal the calculation it sits on (no self-edges).
        * ``BundleKineticsIn.source_calculations[].calculation_key`` —
          must name a calculation defined in the bundle.

        Per-key uniqueness is enforced separately by
        ``validate_unique_keys``; this validator only checks that every
        reference resolves into the bundle's calc namespace.
        """
        all_calc_keys: set[str] = set()
        for sp in self.species:
            for conf in sp.conformers:
                all_calc_keys.add(conf.calculation.key)
            for calc in sp.calculations:
                all_calc_keys.add(calc.key)
        if self.transition_state:
            all_calc_keys.add(self.transition_state.calculation.key)
            for calc in self.transition_state.calculations:
                all_calc_keys.add(calc.key)

        # depends_on edges: parent must exist; child cannot equal parent.
        def _check_depends_on(calc: ComputedReactionCalculationIn) -> None:
            for dep in calc.depends_on:
                if dep.parent_calculation_key not in all_calc_keys:
                    raise ValueError(
                        f"Calculation '{calc.key}' depends_on references "
                        f"unknown parent_calculation_key="
                        f"'{dep.parent_calculation_key}'."
                    )
                if dep.parent_calculation_key == calc.key:
                    raise ValueError(
                        f"Calculation '{calc.key}' depends_on cannot "
                        f"reference itself."
                    )

        for sp in self.species:
            for conf in sp.conformers:
                _check_depends_on(conf.calculation)
            for calc in sp.calculations:
                _check_depends_on(calc)
        if self.transition_state:
            _check_depends_on(self.transition_state.calculation)
            for calc in self.transition_state.calculations:
                _check_depends_on(calc)

        for kin in self.kinetics:
            for entry in kin.source_calculations:
                if entry.calculation_key not in all_calc_keys:
                    raise ValueError(
                        f"Kinetics source_calculations references "
                        f"unknown calculation_key="
                        f"'{entry.calculation_key}'."
                    )

        # Per-species statmech source_calculation keys must resolve into
        # the bundle's calc namespace. Owner-consistency (same species
        # entry) is enforced at the workflow layer where calc → species
        # entry mapping is known; here we only catch typos / undefined
        # keys so producers get a clean schema-level 422.
        all_calc_keys_to_types: dict[str, CalculationType] = {}
        for sp in self.species:
            for conf in sp.conformers:
                all_calc_keys_to_types[conf.calculation.key] = conf.calculation.type
            for calc in sp.calculations:
                all_calc_keys_to_types[calc.key] = calc.type
        if self.transition_state:
            all_calc_keys_to_types[self.transition_state.calculation.key] = (
                self.transition_state.calculation.type
            )
            for calc in self.transition_state.calculations:
                all_calc_keys_to_types[calc.key] = calc.type

        # Per-species thermo source_calculation keys, same contract as
        # statmech's above: typos caught here as a 422 that can name the
        # key, owner-consistency left to the workflow, which is the layer
        # that knows which species entry each calculation resolved to.
        for sp in self.species:
            if sp.thermo is None:
                continue
            for i, sc in enumerate(sp.thermo.source_calculations):
                if sc.calculation_key not in all_calc_keys:
                    raise ValueError(
                        f"species[{sp.key!r}].thermo.source_calculations[{i}]."
                        f"calculation_key references undefined "
                        f"calculation_key '{sc.calculation_key}'."
                    )

        for sp in self.species:
            if sp.statmech is None:
                continue
            for i, sc in enumerate(sp.statmech.source_calculations):
                if sc.calculation_key not in all_calc_keys:
                    raise ValueError(
                        f"species[{sp.key!r}].statmech.source_calculations[{i}]."
                        f"calculation_key references undefined "
                        f"calculation_key '{sc.calculation_key}'."
                    )
            for i, t in enumerate(sp.statmech.torsions):
                key = t.source_scan_calculation_key
                if key is None:
                    continue
                if key not in all_calc_keys_to_types:
                    raise ValueError(
                        f"species[{sp.key!r}].statmech.torsions[{i}]."
                        f"source_scan_calculation_key '{key}' references "
                        f"undefined calculation_key."
                    )
                if all_calc_keys_to_types[key] != CalculationType.scan:
                    raise ValueError(
                        f"species[{sp.key!r}].statmech.torsions[{i}]."
                        f"source_scan_calculation_key '{key}' must reference "
                        f"a scan-type calculation."
                    )

        # Applied-correction source_calculation_key references must
        # resolve into the bundle's calc namespace. The workflow layer
        # also enforces owner-consistency (species correction → species-
        # owned calc; TS correction → TS-owned calc); here we only check
        # the key exists at all so producers get a clean schema-level
        # 422 for typos before the workflow runs.
        for sp in self.species:
            for i, ac in enumerate(sp.applied_energy_corrections):
                if (
                    ac.source_calculation_key is not None
                    and ac.source_calculation_key not in all_calc_keys
                ):
                    raise ValueError(
                        f"species[{sp.key!r}].applied_energy_corrections[{i}]."
                        f"source_calculation_key references undefined "
                        f"calculation_key '{ac.source_calculation_key}'."
                    )
        if self.transition_state is not None:
            for i, ac in enumerate(
                self.transition_state.applied_energy_corrections
            ):
                if (
                    ac.source_calculation_key is not None
                    and ac.source_calculation_key not in all_calc_keys
                ):
                    raise ValueError(
                        f"transition_state.applied_energy_corrections[{i}]."
                        f"source_calculation_key references undefined "
                        f"calculation_key '{ac.source_calculation_key}'."
                    )
        return self
