from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.db.models.common import (
    AppliedCorrectionComponentKind,
    EnergyCorrectionApplicationRole,
    EnergyCorrectionSchemeKind,
    EnergyUnit,
    FrequencyScaleKind,
    MeliusBacComponentKind,
)
from app.schemas.common import (
    ORMBaseSchema,
    SchemaBase,
    TimestampedCreatedByReadSchema,
)

# ---------------------------------------------------------------------------
# Reference layer — scheme atom params
# ---------------------------------------------------------------------------


class EnergyCorrectionSchemeAtomParamBase(BaseModel):
    element: str
    value: float


class EnergyCorrectionSchemeAtomParamCreate(
    EnergyCorrectionSchemeAtomParamBase, SchemaBase
):
    pass


class EnergyCorrectionSchemeAtomParamRead(
    EnergyCorrectionSchemeAtomParamBase, ORMBaseSchema
):
    scheme_id: int


# ---------------------------------------------------------------------------
# Reference layer — scheme bond params
# ---------------------------------------------------------------------------


class EnergyCorrectionSchemeBondParamBase(BaseModel):
    bond_key: str
    value: float


class EnergyCorrectionSchemeBondParamCreate(
    EnergyCorrectionSchemeBondParamBase, SchemaBase
):
    pass


class EnergyCorrectionSchemeBondParamRead(
    EnergyCorrectionSchemeBondParamBase, ORMBaseSchema
):
    scheme_id: int


# ---------------------------------------------------------------------------
# Reference layer — scheme component params (Melius BAC)
# ---------------------------------------------------------------------------


class EnergyCorrectionSchemeComponentParamBase(BaseModel):
    component_kind: MeliusBacComponentKind
    key: str
    value: float


class EnergyCorrectionSchemeComponentParamCreate(
    EnergyCorrectionSchemeComponentParamBase, SchemaBase
):
    pass


class EnergyCorrectionSchemeComponentParamRead(
    EnergyCorrectionSchemeComponentParamBase, ORMBaseSchema
):
    scheme_id: int


# ---------------------------------------------------------------------------
# Reference layer — energy correction scheme
# ---------------------------------------------------------------------------


class EnergyCorrectionSchemeBase(BaseModel):
    kind: EnergyCorrectionSchemeKind
    name: str
    level_of_theory_id: int | None = None
    source_literature_id: int | None = None
    version: str | None = None
    units: EnergyUnit | None = None
    note: str | None = None


class EnergyCorrectionSchemeCreate(EnergyCorrectionSchemeBase, SchemaBase):
    atom_params: list[EnergyCorrectionSchemeAtomParamCreate] = Field(
        default_factory=list
    )
    bond_params: list[EnergyCorrectionSchemeBondParamCreate] = Field(
        default_factory=list
    )
    component_params: list[EnergyCorrectionSchemeComponentParamCreate] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_unique_atom_params(self) -> Self:
        elements = [p.element for p in self.atom_params]
        if len(set(elements)) != len(elements):
            raise ValueError("Atom params must be unique by element.")
        return self

    @model_validator(mode="after")
    def validate_unique_bond_params(self) -> Self:
        keys = [p.bond_key for p in self.bond_params]
        if len(set(keys)) != len(keys):
            raise ValueError("Bond params must be unique by bond_key.")
        return self

    @model_validator(mode="after")
    def validate_unique_component_params(self) -> Self:
        keys = [(p.component_kind, p.key) for p in self.component_params]
        if len(set(keys)) != len(keys):
            raise ValueError(
                "Component params must be unique by (component_kind, key)."
            )
        return self


class EnergyCorrectionSchemeUpdate(SchemaBase):
    kind: EnergyCorrectionSchemeKind | None = None
    name: str | None = None
    level_of_theory_id: int | None = None
    source_literature_id: int | None = None
    version: str | None = None
    units: EnergyUnit | None = None
    note: str | None = None


class EnergyCorrectionSchemeRead(
    EnergyCorrectionSchemeBase, TimestampedCreatedByReadSchema
):
    atom_params: list[EnergyCorrectionSchemeAtomParamRead] = Field(default_factory=list)
    bond_params: list[EnergyCorrectionSchemeBondParamRead] = Field(default_factory=list)
    component_params: list[EnergyCorrectionSchemeComponentParamRead] = Field(
        default_factory=list
    )


# ---------------------------------------------------------------------------
# Reference layer — frequency scale factor
# ---------------------------------------------------------------------------


class FrequencyScaleFactorBase(BaseModel):
    level_of_theory_id: int
    scale_kind: FrequencyScaleKind
    value: float = Field(gt=0)
    source_literature_id: int | None = None
    note: str | None = None


class FrequencyScaleFactorCreate(FrequencyScaleFactorBase, SchemaBase):
    pass


class FrequencyScaleFactorUpdate(SchemaBase):
    scale_kind: FrequencyScaleKind | None = None
    value: float | None = Field(default=None, gt=0)
    source_literature_id: int | None = None
    note: str | None = None


class FrequencyScaleFactorRead(
    FrequencyScaleFactorBase, TimestampedCreatedByReadSchema
):
    software_id: int | None = None
    workflow_tool_release_id: int | None = None


# ---------------------------------------------------------------------------
# Application layer — applied correction components
# ---------------------------------------------------------------------------


class AppliedEnergyCorrectionComponentBase(BaseModel):
    component_kind: AppliedCorrectionComponentKind
    key: str
    multiplicity: int = Field(default=1, ge=1)
    parameter_value: float
    contribution_value: float


class AppliedEnergyCorrectionComponentCreate(
    AppliedEnergyCorrectionComponentBase, SchemaBase
):
    pass


class AppliedEnergyCorrectionComponentRead(
    AppliedEnergyCorrectionComponentBase, ORMBaseSchema
):
    id: int
    applied_correction_id: int


# ---------------------------------------------------------------------------
# Application layer — applied energy correction
# ---------------------------------------------------------------------------


# Roles that require frequency_scale_factor_id as their provenance source.
_FSF_ROLES: frozenset[EnergyCorrectionApplicationRole] = frozenset(
    {
        EnergyCorrectionApplicationRole.zpe,
        EnergyCorrectionApplicationRole.thermal_correction_energy,
        EnergyCorrectionApplicationRole.thermal_correction_enthalpy,
        EnergyCorrectionApplicationRole.thermal_correction_gibbs,
        EnergyCorrectionApplicationRole.entropy_contribution,
    }
)

# Roles that require scheme_id as their provenance source.
_SCHEME_ROLES: frozenset[EnergyCorrectionApplicationRole] = frozenset(
    {
        EnergyCorrectionApplicationRole.bac_total,
        EnergyCorrectionApplicationRole.aec_total,
        EnergyCorrectionApplicationRole.soc_total,
        EnergyCorrectionApplicationRole.atomization_reference_adjustment,
    }
)

# Roles that accept either source (composite_delta, custom).
# Not listed above — no role-source enforcement applied.


class AppliedEnergyCorrectionBase(BaseModel):
    # Exactly one target required
    target_species_entry_id: int | None = None
    target_reaction_entry_id: int | None = None

    # Source provenance (optional)
    source_conformer_observation_id: int | None = None
    source_calculation_id: int | None = None

    # Exactly one correction provenance source required
    scheme_id: int | None = None
    frequency_scale_factor_id: int | None = None

    application_role: EnergyCorrectionApplicationRole

    value: float
    value_unit: EnergyUnit
    temperature_k: float | None = Field(default=None, gt=0)
    note: str | None = None

    @model_validator(mode="after")
    def validate_exactly_one_target(self) -> Self:
        has_species = self.target_species_entry_id is not None
        has_reaction = self.target_reaction_entry_id is not None
        if has_species == has_reaction:
            raise ValueError(
                "Exactly one of target_species_entry_id or "
                "target_reaction_entry_id must be set."
            )
        return self

    @model_validator(mode="after")
    def validate_exactly_one_provenance_source(self) -> Self:
        has_scheme = self.scheme_id is not None
        has_fsf = self.frequency_scale_factor_id is not None
        if has_scheme == has_fsf:
            raise ValueError(
                "Exactly one of scheme_id or " "frequency_scale_factor_id must be set."
            )
        return self

    @model_validator(mode="after")
    def validate_role_source_compatibility(self) -> Self:
        role = self.application_role
        has_scheme = self.scheme_id is not None
        has_fsf = self.frequency_scale_factor_id is not None
        if role in _FSF_ROLES and not has_fsf:
            raise ValueError(
                f"application_role='{role.value}' requires "
                f"frequency_scale_factor_id, not scheme_id."
            )
        if role in _SCHEME_ROLES and not has_scheme:
            raise ValueError(
                f"application_role='{role.value}' requires "
                f"scheme_id, not frequency_scale_factor_id."
            )
        return self

    @model_validator(mode="after")
    def validate_fsf_requires_source_calculation(self) -> Self:
        if (
            self.frequency_scale_factor_id is not None
            and self.source_calculation_id is None
        ):
            raise ValueError(
                "frequency_scale_factor_id requires source_calculation_id "
                "(the frequency calculation the scale factor was applied to)."
            )
        return self


class AppliedEnergyCorrectionCreate(AppliedEnergyCorrectionBase, SchemaBase):
    components: list[AppliedEnergyCorrectionComponentCreate] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_unique_components(self) -> Self:
        keys = [(c.component_kind, c.key) for c in self.components]
        if len(set(keys)) != len(keys):
            raise ValueError("Components must be unique by (component_kind, key).")
        return self


class AppliedEnergyCorrectionUpdate(SchemaBase):
    target_species_entry_id: int | None = None
    target_reaction_entry_id: int | None = None

    source_conformer_observation_id: int | None = None
    source_calculation_id: int | None = None

    scheme_id: int | None = None
    frequency_scale_factor_id: int | None = None
    application_role: EnergyCorrectionApplicationRole | None = None

    value: float | None = None
    value_unit: EnergyUnit | None = None
    temperature_k: float | None = Field(default=None, gt=0)
    note: str | None = None


class AppliedEnergyCorrectionRead(
    AppliedEnergyCorrectionBase, TimestampedCreatedByReadSchema
):
    components: list[AppliedEnergyCorrectionComponentRead] = Field(default_factory=list)
