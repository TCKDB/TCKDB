"""A declared statistical model: what a statmech record actually represents.

A statmech row is an interpretation container. This optional block says which model the container supplies:
the scope it covers (an atom, one conformational basin, or an explicit equilibrium ensemble), who stands behind
the declaration, the recipe (which energy zero, how the frequencies were scaled and projected, which rotors replaced
which modes, which symmetry, optical and electronic factors are already counted), the ensemble's members, and the
calculations or literature each part rests on.

Every fact is a claim stored as made. A field that is omitted is *not stated*; it is never read as one, as unscaled,
as projected or as counted. ``unknown`` is a value a producer may say. Nothing is inferred from the statmech row and
nothing is backfilled. References are local keys or public refs, never database ids.

Vocabulary house rule: it starts small and a value is added when a real deposit needs it. Adding an enum member or an
optional field is additive within version 1; removing or re-meaning one is a new version
(:data:`STATMECH_MODEL_VERSIONS`).
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any

from pydantic import Field, StringConstraints, field_validator, model_validator

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import StatmechModelScopeKind, StatmechModelSourceRole
from tckdb_schemas.fragments.refs import FreqScaleFactorRef, WorkflowToolReleaseRef
from tckdb_schemas.literature import LiteratureUploadRequest
from tckdb_schemas.local_key_codes import W_STATMECH_CALCULATION_KEY_UNDECLARED, undeclared_key_error

__all__ = [
    "STATMECH_MODEL_VERSIONS",
    "W_STATMECH_MODEL_INVALID",
    "W_STATMECH_MODEL_VERSION_UNSUPPORTED",
    "CountedState",
    "DividingSurface",
    "IsotopeMasses",
    "ModelEnergyZero",
    "ModelMember",
    "ModelRecipe",
    "ModelRotor",
    "ModelSource",
    "NuclearSpin",
    "RotorRepresentation",
    "TranslationalNormalization",
    "ScaleApplication",
    "ScaleContribution",
    "StatmechModelDeclaration",
    "VibrationalScheme",
    "assert_model_determination_key_declared",
    "assert_model_pin_keys_declared",
]

#: A declaration contradicts itself (a repeated key, a source with no or several locators, a rotor without the
#: potential it names, ...), or reached a service without passing request validation and fails it there.
W_STATMECH_MODEL_INVALID = "statmech_model_declaration_invalid"
#: A declaration carries a ``version`` this server does not accept.
W_STATMECH_MODEL_VERSION_UNSUPPORTED = "statmech_model_declaration_version_unsupported"

STATMECH_MODEL_VERSIONS: frozenset[int] = frozenset({1})

_Key = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")]
_Token = Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=64)]
_StatmechRef = Annotated[str, StringConstraints(pattern=r"^sm_[A-Za-z0-9]+$", max_length=40)]
_ConformerGroupRef = Annotated[str, StringConstraints(pattern=r"^cg_[A-Za-z0-9]+$", max_length=40)]
_CalculationRef = Annotated[str, StringConstraints(pattern=r"^calc_[A-Za-z0-9]+$", max_length=40)]
_GeometryRef = Annotated[str, StringConstraints(pattern=r"^geom_[A-Za-z0-9]+$", max_length=40)]
_DeterminationRef = Annotated[str, StringConstraints(pattern=r"^sdet_[A-Za-z0-9]+$", max_length=40)]


class ScaleApplication(str, Enum):
    """Whether a frequency scale factor was applied to the frequencies this model uses."""

    applied = "applied"
    not_applied = "not_applied"
    unknown = "unknown"


class ScaleContribution(str, Enum):
    """A contribution a scale factor was applied to."""

    fundamental = "fundamental"
    zero_point_energy = "zero_point_energy"
    vibrational_entropy = "vibrational_entropy"
    vibrational_heat_capacity = "vibrational_heat_capacity"
    vibrational_enthalpy = "vibrational_enthalpy"


class VibrationalScheme(str, Enum):
    """How the vibrational contribution is evaluated. ``anharmonic`` is recognised and not evaluated."""

    harmonic = "harmonic"
    quasi_harmonic = "quasi_harmonic"
    anharmonic = "anharmonic"


class RotorRepresentation(str, Enum):
    """Where a rotor's potential comes from: a local scan, an imported potential, or none (a free rotor)."""

    local_scan = "local_scan"
    imported_potential = "imported_potential"
    free_rotor = "free_rotor"


class CountedState(str, Enum):
    """Whether a statistical factor is already counted in the supplied model."""

    counted = "counted"
    not_counted = "not_counted"
    unknown = "unknown"


class NuclearSpin(str, Enum):
    """The nuclear-spin convention of the partition function."""

    included = "included"
    not_included = "not_included"


class ModelEnergyZero(str, Enum):
    """The zero the model's energies are measured from."""

    electronic = "electronic"
    e0 = "e0"


class IsotopeMasses(str, Enum):
    """The mass convention the model's rotational constants and frequencies were computed with."""

    entry_documented_masses = "entry_documented_masses"
    natural_abundance_average = "natural_abundance_average"


class TranslationalNormalization(str, Enum):
    """How a full partition function's translational part is normalised."""

    fixed_volume = "fixed_volume"
    standard_state_pressure = "standard_state_pressure"
    standard_state_concentration = "standard_state_concentration"


class DividingSurface(str, Enum):
    """The dividing surface of a transition-state model. ``variational`` is recognised and not evaluated."""

    conventional_saddle_point = "conventional_saddle_point"
    variational = "variational"


class ModelRotor(SchemaBase):
    """One internal rotor and the harmonic modes it replaces.

    :param torsion_index: The ``torsion_index`` of the statmech record's torsion this describes.
    :param replaces_mode_indices: Harmonic modes this rotor replaces (so they are not counted twice).
    :param potential_source_key: The ``source_key`` of the ``torsional_surface`` source holding an imported potential.
    :param region_key: The configurational region the rotor integrates over, to diagnose overlap with explicit members.
    """

    torsion_index: int = Field(ge=1)
    representation: RotorRepresentation
    dimension: int = Field(default=1, ge=1, le=8)
    coupling_group: _Key | None = None
    replaces_mode_indices: list[int] = Field(default_factory=list, max_length=64)
    potential_source_key: _Key | None = None
    region_key: _Key | None = None

    @model_validator(mode="after")
    def validate_representation(self) -> ModelRotor:
        """An imported potential names its source; a free rotor names none; mode indices are one-based and distinct."""
        imported = self.representation is RotorRepresentation.imported_potential
        if imported != (self.potential_source_key is not None):
            raise ValueError("An imported_potential rotor names its potential_source_key, and no other representation does.")
        if any(i < 1 for i in self.replaces_mode_indices) or len(set(self.replaces_mode_indices)) != len(
            self.replaces_mode_indices
        ):
            raise ValueError("replaces_mode_indices are one-based and distinct.")
        return self


class ModelRecipe(SchemaBase):
    """The statistical recipe. Every field is a claim; an omitted one is not stated.

    :param scale_application: Whether a frequency scale factor was applied; the factor itself is a ``scale_factor`` source.
    :param scale_contributions: What it was applied to (required when ``applied``, refused otherwise).
    :param projected_mode_indices: Modes projected out. A flag alone is not a mode map.
    :param excluded_mode_indices: Modes left out of the vibrational sum, other than projected ones.
    :param optical_multiplier: The optical-isomer factor the model applies; ``optical_state`` says if it is counted.
    :param electronic_spin_multiplier: A spin multiplier applied on top of any electronic levels.
    :param validity_min_k: Lower bound of the temperatures the model is stated to be valid for.
    :param validity_max_k: Upper bound of the same.
    :param translation_included: Whether the model is a full partition function (translation included) or an internal one.
    :param translational_normalization: How the translational part is normalised; goes with the matching reference value.
    :param reference_volume_m3: The fixed volume, in cubic metres.
    :param reference_pressure_pa: The standard-state pressure, in pascal.
    :param reference_concentration_mol_m3: The standard-state concentration, in mol per cubic metre.
    :param dividing_surface: A transition-state model's dividing surface.
    :param reaction_coordinate_excluded: Whether the reaction coordinate is left out of the transverse partition function.
    :param reaction_coordinate_mode_index: The one-based mode that is the reaction coordinate.
    """

    energy_zero: ModelEnergyZero | None = None
    zpe_included_in_energies: bool | None = None
    scale_application: ScaleApplication | None = None
    scale_contributions: list[ScaleContribution] = Field(default_factory=list, max_length=8)
    vibrational_scheme: VibrationalScheme | None = None
    low_frequency_cutoff_cm1: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    projected_mode_indices: list[int] = Field(default_factory=list, max_length=4096)
    projection_prescription: _Token | None = None
    excluded_mode_indices: list[int] = Field(default_factory=list, max_length=4096)
    rotors: list[ModelRotor] = Field(default_factory=list, max_length=64)
    external_symmetry_state: CountedState | None = None
    optical_state: CountedState | None = None
    optical_multiplier: int | None = Field(default=None, ge=1)
    electronic_levels_state: CountedState | None = None
    electronic_spin_multiplier: int | None = Field(default=None, ge=1)
    nuclear_spin: NuclearSpin | None = None
    validity_min_k: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    validity_max_k: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    isotope_masses: IsotopeMasses | None = None
    translation_included: bool | None = None
    translational_normalization: TranslationalNormalization | None = None
    reference_volume_m3: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    reference_pressure_pa: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    reference_concentration_mol_m3: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    dividing_surface: DividingSurface | None = None
    reaction_coordinate_excluded: bool | None = None
    reaction_coordinate_mode_index: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_recipe(self) -> ModelRecipe:
        """Scale contributions go with an applied scale; a quasi-harmonic scheme states its cutoff; indices are distinct."""
        applied = self.scale_application is ScaleApplication.applied
        if applied != bool(self.scale_contributions):
            raise ValueError("scale_contributions are stated exactly when scale_application is 'applied'.")
        if self.vibrational_scheme is VibrationalScheme.quasi_harmonic and self.low_frequency_cutoff_cm1 is None:
            raise ValueError("A quasi_harmonic scheme states its low_frequency_cutoff_cm1.")
        for name in ("projected_mode_indices", "excluded_mode_indices"):
            values = getattr(self, name)
            if any(i < 1 for i in values) or len(set(values)) != len(values):
                raise ValueError(f"{name} are one-based and distinct.")
        if set(self.projected_mode_indices) & set(self.excluded_mode_indices):
            raise ValueError("A mode is projected or excluded, not both.")
        if len(set(self.scale_contributions)) != len(self.scale_contributions):
            raise ValueError("scale_contributions names a contribution twice.")
        rotor_indices = [r.torsion_index for r in self.rotors]
        if len(set(rotor_indices)) != len(rotor_indices):
            raise ValueError("rotors names a torsion_index twice.")
        low, high = self.validity_min_k, self.validity_max_k
        if (low is None) != (high is None) or (low is not None and high is not None and low > high):
            raise ValueError("validity_min_k and validity_max_k are stated together and ordered.")
        wanted = {
            TranslationalNormalization.fixed_volume: "reference_volume_m3",
            TranslationalNormalization.standard_state_pressure: "reference_pressure_pa",
            TranslationalNormalization.standard_state_concentration: "reference_concentration_mol_m3",
        }
        given = {n for n in wanted.values() if getattr(self, n) is not None}
        if self.translational_normalization is None:
            if given:
                raise ValueError("A reference volume, pressure or concentration goes with a translational_normalization.")
        else:
            if self.translation_included is not True:
                raise ValueError("A translational_normalization is stated only when translation_included is true.")
            if given != {wanted[self.translational_normalization]}:
                raise ValueError(
                    f"translational_normalization {self.translational_normalization.value} takes "
                    f"{wanted[self.translational_normalization]} and no other reference value."
                )
        if self.reaction_coordinate_mode_index is not None and self.reaction_coordinate_excluded is not True:
            raise ValueError("A reaction_coordinate_mode_index is named only when reaction_coordinate_excluded is true.")
        return self


class ModelMember(SchemaBase):
    """One member of an explicit equilibrium ensemble: an existing statmech record, in a basin.

    :param statmech_ref: The member's own statistical interpretation (an existing record of the same species entry).
    :param conformer_group_ref: The basin the member is assigned to.
    :param counting_multiplicity: The depositor's attributed multiplicity; omitted means not stated, never one.
    :param relative_energy_kj_mol: The member's energy relative to the ensemble's reference, in kJ/mol.
    :param region_key: The configurational region the member covers, to diagnose overlap with a rotor's integral.
    """

    member_key: _Key
    statmech_ref: _StatmechRef
    conformer_group_ref: _ConformerGroupRef | None = None
    counting_multiplicity: int | None = Field(default=None, ge=1)
    relative_energy_kj_mol: float | None = Field(default=None, allow_inf_nan=False)
    region_key: _Key | None = None


class ModelSource(SchemaBase):
    """What one part of the model rests on: a calculation, literature, or a frequency scale factor.

    :param calculation_key: A calculation of this upload (by its local ``key``).
    :param calculation_ref: A calculation already deposited (public ref).
    :param geometry_ref: With a calculation: the one geometry its result describes.
    :param literature: Imported or experimental parameters, with their provenance, instead of a calculation.
    :param freq_scale_factor: The scale factor (role ``scale_factor`` only).
    :param member_key: The ensemble member this source is for (omit for the model as a whole).
    """

    source_key: _Key
    role: StatmechModelSourceRole
    calculation_key: _Key | None = None
    calculation_ref: _CalculationRef | None = None
    geometry_ref: _GeometryRef | None = None
    literature: LiteratureUploadRequest | None = None
    freq_scale_factor: FreqScaleFactorRef | None = None
    member_key: _Key | None = None

    @model_validator(mode="after")
    def validate_locator(self) -> ModelSource:
        """Exactly one of calculation_key, calculation_ref, literature or freq_scale_factor; the scale_factor role takes
        only a scale factor; a geometry_ref goes with a calculation."""
        locators = [
            n
            for n in ("calculation_key", "calculation_ref", "literature", "freq_scale_factor")
            if getattr(self, n) is not None
        ]
        if len(locators) != 1:
            raise ValueError(
                "A source names exactly one of calculation_key, calculation_ref, literature or freq_scale_factor."
            )
        if (self.role is StatmechModelSourceRole.scale_factor) != (self.freq_scale_factor is not None):
            raise ValueError("The scale_factor role takes a freq_scale_factor, and no other role does.")
        if self.geometry_ref is not None and locators[0] not in ("calculation_key", "calculation_ref"):
            raise ValueError("A geometry_ref goes with a calculation.")
        return self


class StatmechModelDeclaration(SchemaBase):
    """The model a statmech record supplies. Optional; a record deposited without one is read as it is.

    :param version: The declaration schema version (1).
    :param scope_kind: ``atom``, ``basin``, ``explicit_equilibrium_ensemble`` or (a transition-state record)
        ``ts_determination``.
    :param conformer_group_ref: The basin, for ``basin`` scope. Omitted inside a conformer deposit it is that
        conformer's own group.
    :param determination_ref: The saddle determination (public ref) of the record's own transition state entry, for
        ``ts_determination`` scope.
    :param determination_key: Or the ``key`` of a structure determination declared in the same transition-state deposit.
    :param literature: Who stands behind the declaration (a paper) ...
    :param workflow_tool_release: ... or the tool that produced it. At least one is required.
    """

    version: int = 1
    scope_kind: StatmechModelScopeKind
    conformer_group_ref: _ConformerGroupRef | None = None
    determination_ref: _DeterminationRef | None = None
    determination_key: _Key | None = None
    literature: LiteratureUploadRequest | None = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None
    recipe: ModelRecipe = Field(default_factory=ModelRecipe)
    members: list[ModelMember] = Field(default_factory=list, max_length=64)
    sources: list[ModelSource] = Field(default_factory=list, max_length=128)

    @field_validator("version", mode="before")
    @classmethod
    def validate_version(cls, value: Any) -> Any:
        """The version is exactly a supported integer."""
        if type(value) is not int or value not in STATMECH_MODEL_VERSIONS:
            raise CodedValidationError(
                W_STATMECH_MODEL_VERSION_UNSUPPORTED,
                f"version must be one of {sorted(STATMECH_MODEL_VERSIONS)}; this server does not read version {value!r}.",
                context={"field": "model_declaration.version", "supported": sorted(STATMECH_MODEL_VERSIONS)},
                message_prefix=False,
            )
        return value

    @model_validator(mode="after")
    def validate_declaration(self) -> StatmechModelDeclaration:
        """Attribution is stated; only an ensemble has members (and has at least one); keys are unique and resolve."""
        if self.literature is None and self.workflow_tool_release is None:
            raise ValueError("A declaration states who stands behind it: literature or workflow_tool_release.")
        kind = self.scope_kind
        if self.conformer_group_ref is not None and kind is not StatmechModelScopeKind.basin:
            raise ValueError("conformer_group_ref belongs to a basin scope.")
        named = (self.determination_ref is not None) + (self.determination_key is not None)
        if (kind is StatmechModelScopeKind.ts_determination) != (named == 1) or named > 1:
            raise ValueError(
                "A ts_determination scope names exactly one of determination_ref or determination_key, "
                "and no other scope names one."
            )
        if (kind is StatmechModelScopeKind.explicit_equilibrium_ensemble) != bool(self.members):
            raise ValueError("An explicit_equilibrium_ensemble has members, and no other scope does.")
        member_keys = [m.member_key for m in self.members]
        if len(set(member_keys)) != len(member_keys):
            raise ValueError("members names a member_key twice.")
        if len({m.statmech_ref for m in self.members}) != len(self.members):
            raise ValueError("members names a statmech record twice: a repeated record is evidence, not another state.")
        source_keys = [s.source_key for s in self.sources]
        if len(set(source_keys)) != len(source_keys):
            raise ValueError("sources names a source_key twice.")
        for s in self.sources:
            if s.member_key is not None and s.member_key not in member_keys:
                raise ValueError("A source's member_key names a member of this declaration.")
        roles = {s.source_key: s.role for s in self.sources}
        for rotor in self.recipe.rotors:
            if rotor.potential_source_key is None:
                continue
            if rotor.potential_source_key not in roles:
                raise ValueError("A rotor's potential_source_key names a source of this declaration.")
            if roles[rotor.potential_source_key] is not StatmechModelSourceRole.torsional_surface:
                raise ValueError("A rotor's potential_source_key names a source whose role is torsional_surface.")
        return self


def assert_model_determination_key_declared(
    declaration: StatmechModelDeclaration | None, declared: set[str], *, field: str = "statmech.model_declaration"
) -> None:
    """A ``determination_key`` names a structure determination declared in the same transition-state deposit."""
    if declaration is None or declaration.determination_key is None:
        return
    if declaration.determination_key not in declared:
        raise CodedValidationError(
            W_STATMECH_MODEL_INVALID,
            f"{field}.determination_key '{declaration.determination_key}' does not name a structure determination "
            "declared in this transition-state deposit.",
            context={"field": f"{field}.determination_key", "key": declaration.determination_key, "declared_keys": sorted(declared)},
            message_prefix=False,
        )


def assert_model_pin_keys_declared(
    declaration: StatmechModelDeclaration | None, declared: set[str], *, field: str = "statmech.model_declaration"
) -> None:
    """Every ``calculation_key`` a declaration's sources name is declared by the same upload.

    The request validator and the workflow both call this, so both refuse an undeclared key with the same code
    (``statmech_calculation_key_undeclared``) and context. A ``calculation_ref`` names a calculation already
    deposited, which only the database can confirm.
    """
    if declaration is None:
        return
    for index, source in enumerate(declaration.sources):
        if source.calculation_key is not None and source.calculation_key not in declared:
            where = f"{field}.sources[{index}].calculation_key"
            raise undeclared_key_error(
                W_STATMECH_CALCULATION_KEY_UNDECLARED,
                f"{where} '{source.calculation_key}' does not name a calculation declared in this upload. "
                "Put a matching 'key' on the calculation it means.",
                field=where,
                key=source.calculation_key,
                declared=declared,
            )
