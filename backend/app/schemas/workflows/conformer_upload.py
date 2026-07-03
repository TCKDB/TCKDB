from typing import Self

from pydantic import Field, model_validator

from app.db.models.common import (
    CalculationType,
    RigidRotorKind,
    ScientificOriginKind,
    StatmechCalculationRole,
    StatmechTreatmentKind,
)
from app.schemas.common import SchemaBase
from app.schemas.entities.statmech import (
    StatmechSourceCalculationCreate,
    StatmechTorsionCreate,
)
from app.schemas.fragments.calculation import (
    CalculationWithResultsPayload,
)
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.fragments.refs import FreqScaleFactorRef, SoftwareReleaseRef, WorkflowToolReleaseRef
from app.schemas.utils import normalize_optional_text
from app.schemas.workflows.energy_correction_upload import (
    AppliedEnergyCorrectionUploadPayload,
)
from app.schemas.workflows.literature_upload import LiteratureUploadRequest
from app.schemas.workflows.transport_upload import TransportUploadPayload


class ElectronicLevelIn(SchemaBase):
    """One electronic energy level for the electronic partition function.

    Ordered (energy, degeneracy) pairs relative to the ground state
    (DR-0033). E.g. OH X²Π: level 1 (0 cm⁻¹, g=2), level 2 (~139 cm⁻¹,
    g=2). ``level_index`` is 1-based and unique within a statmech record.
    """

    level_index: int = Field(ge=1)
    energy_cm1: float = Field(ge=0)
    degeneracy: int = Field(ge=1)


class ConformerUploadStatmechPayload(SchemaBase):
    """Workflow-facing statmech payload nested under conformer upload.

    The backend resolves referenced software/workflow provenance, creates or
    reuses the owning ``Statmech`` row for the resolved species entry, and links
    the newly created upload calculation as a source calculation when requested.
    """

    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed

    literature: LiteratureUploadRequest | None = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None
    software_release: SoftwareReleaseRef | None = None

    external_symmetry: int | None = Field(default=None, ge=1)
    point_group: str | None = None

    is_linear: bool | None = None
    rigid_rotor_kind: RigidRotorKind | None = None
    statmech_treatment: StatmechTreatmentKind | None = None

    freq_scale_factor: FreqScaleFactorRef | None = None
    uses_projected_frequencies: bool | None = None
    optical_isomers: int | None = Field(default=None, ge=1)
    note: str | None = None

    uploaded_calculation_role: StatmechCalculationRole | None = None
    source_calculations: list[StatmechSourceCalculationCreate] = Field(
        default_factory=list
    )
    torsions: list[StatmechTorsionCreate] = Field(default_factory=list)
    electronic_levels: list[ElectronicLevelIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_optional_text_fields(self) -> Self:
        self.point_group = normalize_optional_text(self.point_group)
        self.note = normalize_optional_text(self.note)
        return self

    @model_validator(mode="after")
    def validate_electronic_levels(self) -> Self:
        indices = [lvl.level_index for lvl in self.electronic_levels]
        if len(set(indices)) != len(indices):
            raise ValueError(
                "electronic_levels level_index values must be unique."
            )
        return self


_ALLOWED_ADDITIONAL_TYPES = frozenset(
    {CalculationType.freq, CalculationType.sp}
)


class ConformerUploadRequest(SchemaBase):
    """Workflow-facing conformer upload payload.

    The backend resolves the species, species entry, geometry, and calculation
    provenance, then assigns or creates a conformer group and creates one new
    provenance-bearing observation row for this upload. If the geometry matches
    an existing basin, the group is reused but the observation is not silently
    deduplicated. Optionally, additional calculations (freq, sp) can be
    attached alongside the primary calculation, and they anchor to that same
    observation.
    """

    species_entry: SpeciesEntryIdentityPayload
    geometry: GeometryPayload
    calculation: CalculationWithResultsPayload
    additional_calculations: list[CalculationWithResultsPayload] = Field(
        default_factory=list
    )
    statmech: ConformerUploadStatmechPayload | None = None
    transport: TransportUploadPayload | None = None
    applied_energy_corrections: list[AppliedEnergyCorrectionUploadPayload] = Field(
        default_factory=list
    )

    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed
    note: str | None = None
    label: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def normalize_optional_text_fields(self) -> Self:
        self.note = normalize_optional_text(self.note)
        self.label = normalize_optional_text(self.label)
        return self

    @model_validator(mode="after")
    def validate_additional_calculation_types(self) -> Self:
        for calc in self.additional_calculations:
            if calc.type not in _ALLOWED_ADDITIONAL_TYPES:
                raise ValueError(
                    f"Additional calculation type '{calc.type.value}' is not "
                    f"allowed. Expected one of: "
                    f"{', '.join(t.value for t in sorted(_ALLOWED_ADDITIONAL_TYPES, key=lambda t: t.value))}."
                )
        return self
