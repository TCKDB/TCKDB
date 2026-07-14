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

from typing import Self

from pydantic import Field, field_validator, model_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import (
    ActivationEnergyUnits,
    ArrheniusAUnits,
    CalculationType,
    KineticsCalculationRole,
    KineticsModelKind,
    KineticsUncertaintyKind,
    PressureContext,
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
from tckdb_schemas.fragments.identity import SpeciesEntryIdentityPayload
from tckdb_schemas.fragments.refs import (
    FreqScaleFactorRef,
    SoftwareReleaseRef,
    WorkflowToolReleaseRef,
)
from tckdb_schemas.fragments.scan import CalculationScanResultCreate
from tckdb_schemas.literature import LiteratureUploadRequest
from tckdb_schemas.reaction_family import find_canonical_reaction_family
from tckdb_schemas.shared.calculation_in import (
    CalculationIn as _BaseCalculationIn,
    GeometryIn,
    calculation_in_to_with_results_payload as _base_calc_to_payload,
)
from tckdb_schemas.statmech_bits import StatmechTorsionCoordinateIn
from tckdb_schemas.thermo import ThermoNASACreate, ThermoPointCreate
from tckdb_schemas.utils import normalize_optional_text, normalize_tunneling_model
from tckdb_schemas.workflows.computed_species_upload import (
    AppliedEnergyCorrectionInBundle,
    CalculationDependencyInBundle,
    StatmechSourceCalcInBundle,
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

    :param scientific_origin: Scientific origin category.
    :param h298_kj_mol: Enthalpy at 298 K in kJ/mol.
    :param s298_j_mol_k: Entropy at 298 K in J/(mol*K).
    :param tmin_k: Minimum temperature in K.
    :param tmax_k: Maximum temperature in K.
    :param nasa: Optional NASA polynomial coefficients.
    :param points: Optional tabulated thermo data points.
    :param note: Optional note.
    """

    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed
    h298_kj_mol: float | None = None
    s298_j_mol_k: float | None = None
    tmin_k: float | None = Field(default=None, gt=0)
    tmax_k: float | None = Field(default=None, gt=0)
    nasa: ThermoNASACreate | None = None
    points: list[ThermoPointCreate] = Field(default_factory=list)
    note: str | None = None

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

    :param scientific_origin: Scientific origin category.
    :param is_linear: Whether the molecule is linear.
    :param rigid_rotor_kind: Rotational treatment classification.
    :param external_symmetry: External symmetry number.
    :param optical_isomers: Number of optical isomers (>= 1).
    :param point_group: Optional point-group label (e.g. ``"C2v"``).
    :param statmech_treatment: Overall statmech treatment classification.
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
    is_linear: bool | None = None
    rigid_rotor_kind: RigidRotorKind | None = None
    external_symmetry: int | None = Field(default=None, ge=1)
    optical_isomers: int | None = Field(default=None, ge=1)
    point_group: str | None = None
    statmech_treatment: StatmechTreatmentKind | None = None
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
    :param degeneracy: Optional multiplicative reaction-path degeneracy
        associated with the reported kinetics expression. ``None`` means
        no claim is made; do not interpret it as ``1.0``.
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

    degeneracy: float | None = Field(default=None, gt=0)
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
