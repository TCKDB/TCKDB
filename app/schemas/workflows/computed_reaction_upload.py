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

from app.db.models.common import (
    ActivationEnergyUnits,
    ArrheniusAUnits,
    CalculationType,
    KineticsModelKind,
    RigidRotorKind,
    ScientificOriginKind,
    StatmechTreatmentKind,
    TorsionTreatmentKind,
)
from app.schemas.common import SchemaBase
from app.schemas.entities.thermo import ThermoNASACreate, ThermoPointCreate
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.reaction_family import find_canonical_reaction_family
from app.schemas.fragments.refs import (
    FreqScaleFactorRef,
    LevelOfTheoryRef,
    SoftwareReleaseRef,
    WorkflowToolReleaseRef,
)
from app.schemas.utils import normalize_optional_text
from app.schemas.workflows.literature_upload import LiteratureUploadRequest

# Reuse the PDep upload building blocks for calculations and geometries
from app.schemas.workflows.network_pdep_upload import (
    CalculationIn,
    ConformerIn,
    GeometryIn,
)


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

    :param torsion_index: One-based torsion index.
    :param symmetry_number: Optional torsional symmetry number.
    :param treatment_kind: Optional torsion treatment.
    """

    torsion_index: int = Field(ge=1)
    symmetry_number: int | None = Field(default=None, ge=1)
    treatment_kind: TorsionTreatmentKind | None = None


class BundleStatmechIn(SchemaBase):
    """Statistical mechanics properties for a species in this bundle.

    :param scientific_origin: Scientific origin category.
    :param is_linear: Whether the molecule is linear.
    :param rigid_rotor_kind: Rotational treatment classification.
    :param external_symmetry: External symmetry number.
    :param statmech_treatment: Overall statmech treatment classification.
    :param freq_scale_factor: Frequency scale factor applied.
    :param uses_projected_frequencies: Whether projected frequencies were used.
    :param torsions: Torsional modes.
    :param note: Optional note.
    """

    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed
    is_linear: bool | None = None
    rigid_rotor_kind: RigidRotorKind | None = None
    external_symmetry: int | None = Field(default=None, ge=1)
    statmech_treatment: StatmechTreatmentKind | None = None
    freq_scale_factor: FreqScaleFactorRef | None = None
    uses_projected_frequencies: bool | None = None
    torsions: list[BundleStatmechTorsionIn] = Field(default_factory=list)
    note: str | None = None


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
    calculations: list[CalculationIn] = Field(default_factory=list)
    thermo: BundleThermoIn | None = None
    statmech: BundleStatmechIn | None = None

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
    calculation: CalculationIn
    calculations: list[CalculationIn] = Field(default_factory=list)
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


class BundleKineticsIn(SchemaBase):
    """One kinetics fit (Arrhenius parameters) within the bundle.

    The reaction direction is determined by ``reactant_keys`` / ``product_keys``
    which reference species keys. For the forward direction, these match the
    bundle's reaction; for the reverse, they are swapped.

    :param reactant_keys: Species keys on the reactant side of this fit.
    :param product_keys: Species keys on the product side of this fit.
    :param scientific_origin: Scientific origin category.
    :param model_kind: Kinetics functional form.
    :param a: Arrhenius pre-exponential factor.
    :param a_units: Units for A.
    :param n: Temperature exponent.
    :param reported_ea: Activation energy in reported units.
    :param reported_ea_units: Units for Ea.
    :param tmin_k: Minimum valid temperature.
    :param tmax_k: Maximum valid temperature.
    :param tunneling_model: Optional tunneling model label.
    :param note: Optional note.
    """

    reactant_keys: list[str] = Field(min_length=1)
    product_keys: list[str] = Field(min_length=1)

    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed
    model_kind: KineticsModelKind = KineticsModelKind.modified_arrhenius

    a: float | None = None
    a_units: ArrheniusAUnits | None = None
    n: float | None = None
    reported_ea: float | None = None
    reported_ea_units: ActivationEnergyUnits | None = None

    a_uncertainty: float | None = None
    n_uncertainty: float | None = None
    d_reported_ea: float | None = None

    tmin_k: float | None = Field(default=None, gt=0)
    tmax_k: float | None = Field(default=None, gt=0)

    tunneling_model: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.tunneling_model = normalize_optional_text(self.tunneling_model)
        self.note = normalize_optional_text(self.note)
        return self

    @model_validator(mode="after")
    def validate_ea_pair(self) -> Self:
        if (self.reported_ea is None) != (self.reported_ea_units is None):
            raise ValueError(
                "reported_ea and reported_ea_units must both be provided or both omitted."
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
