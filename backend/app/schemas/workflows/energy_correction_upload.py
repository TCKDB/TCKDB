"""Energy-correction upload fragments — nested inside other upload requests.

This module intentionally has **no standalone ``/uploads/energy-corrections``
route**. Every class here (``AppliedEnergyCorrectionUploadPayload``,
``EnergyCorrectionSchemeRef``, ``FrequencyScaleFactorRef``, and component
payloads) is consumed as a nested fragment by the conformer, thermo, and
computed-reaction upload flows. Scheme/FSF references are resolved and applied
corrections are persisted by ``app.services.energy_correction_resolution``
when a parent upload embeds them.

If standalone ingestion becomes a product requirement, wire a dedicated route
at ``app/api/routes/uploads.py`` and a workflow orchestrator alongside the
existing per-kind handlers; see ``docs/audits/backend_audit_2026-04-22.md``
for the product context.
"""

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
from app.schemas.common import SchemaBase
from app.schemas.fragments.refs import LevelOfTheoryRef
from app.schemas.utils import normalize_optional_text, normalize_required_text
from app.schemas.workflows.literature_upload import LiteratureUploadRequest

# ---------------------------------------------------------------------------
# Inline refs — resolved to DB rows by the service layer
# ---------------------------------------------------------------------------


class EnergyCorrectionSchemeRef(SchemaBase):
    """Upload-facing reference to a correction scheme.

    If a matching scheme already exists (by kind + name + LoT + version),
    it is reused. Otherwise a new scheme is created.
    """

    kind: EnergyCorrectionSchemeKind
    name: str = Field(min_length=1)
    level_of_theory: LevelOfTheoryRef | None = None
    source_literature: LiteratureUploadRequest | None = None
    version: str | None = None
    units: EnergyUnit | None = None
    note: str | None = None

    # Optional inline parameter definitions (used when creating a new scheme)
    atom_params: list["SchemeAtomParamPayload"] = Field(default_factory=list)
    bond_params: list["SchemeBondParamPayload"] = Field(default_factory=list)
    component_params: list["SchemeComponentParamPayload"] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_text_fields(self) -> Self:
        self.name = normalize_required_text(self.name)
        self.version = normalize_optional_text(self.version)
        self.note = normalize_optional_text(self.note)
        return self

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


class SchemeAtomParamPayload(SchemaBase):
    element: str = Field(min_length=1, max_length=3)
    value: float


class SchemeBondParamPayload(SchemaBase):
    bond_key: str = Field(min_length=1)
    value: float


class SchemeComponentParamPayload(SchemaBase):
    component_kind: MeliusBacComponentKind
    key: str = Field(min_length=1)
    value: float


class FrequencyScaleFactorRef(SchemaBase):
    """Upload-facing reference to a frequency scale factor.

    Resolved by (level_of_theory + scale_kind). If a match exists, reused;
    otherwise created.
    """

    level_of_theory: LevelOfTheoryRef
    scale_kind: FrequencyScaleKind
    value: float = Field(gt=0)
    source_literature: LiteratureUploadRequest | None = None
    note: str | None = None

    @model_validator(mode="after")
    def normalize_text_fields(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self


# ---------------------------------------------------------------------------
# Applied correction component payload
# ---------------------------------------------------------------------------


class AppliedCorrectionComponentPayload(SchemaBase):
    component_kind: AppliedCorrectionComponentKind
    key: str = Field(min_length=1)
    multiplicity: int = Field(default=1, ge=1)
    parameter_value: float
    contribution_value: float


# ---------------------------------------------------------------------------
# Applied energy correction upload payload
# ---------------------------------------------------------------------------

# Roles requiring frequency_scale_factor as provenance source.
_FSF_ROLES: frozenset[EnergyCorrectionApplicationRole] = frozenset(
    {
        EnergyCorrectionApplicationRole.zpe,
        EnergyCorrectionApplicationRole.thermal_correction_energy,
        EnergyCorrectionApplicationRole.thermal_correction_enthalpy,
        EnergyCorrectionApplicationRole.thermal_correction_gibbs,
        EnergyCorrectionApplicationRole.entropy_contribution,
    }
)

# Roles requiring scheme as provenance source.
_SCHEME_ROLES: frozenset[EnergyCorrectionApplicationRole] = frozenset(
    {
        EnergyCorrectionApplicationRole.bac_total,
        EnergyCorrectionApplicationRole.aec_total,
        EnergyCorrectionApplicationRole.soc_total,
        EnergyCorrectionApplicationRole.atomization_reference_adjustment,
    }
)


class AppliedEnergyCorrectionUploadPayload(SchemaBase):
    """Upload-facing payload for one applied energy correction.

    Exactly one of ``scheme`` or ``frequency_scale_factor`` must be provided.
    The ``source_conformer_key`` and ``source_calculation_key`` are local
    string keys that reference other objects in the same upload bundle;
    they are resolved to integer IDs by the workflow orchestrator.
    """

    # Provenance source — exactly one required
    scheme: EnergyCorrectionSchemeRef | None = None
    frequency_scale_factor: FrequencyScaleFactorRef | None = None

    application_role: EnergyCorrectionApplicationRole

    value: float
    value_unit: EnergyUnit
    temperature_k: float | None = Field(default=None, gt=0)
    note: str | None = None

    # Local keys resolved by the workflow
    source_conformer_key: str | None = None
    source_calculation_key: str | None = None

    components: list[AppliedCorrectionComponentPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_text_fields(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self

    @model_validator(mode="after")
    def validate_exactly_one_provenance_source(self) -> Self:
        has_scheme = self.scheme is not None
        has_fsf = self.frequency_scale_factor is not None
        if has_scheme == has_fsf:
            raise ValueError(
                "Exactly one of 'scheme' or 'frequency_scale_factor' must be provided."
            )
        return self

    @model_validator(mode="after")
    def validate_role_source_compatibility(self) -> Self:
        role = self.application_role
        if role in _FSF_ROLES and self.frequency_scale_factor is None:
            raise ValueError(
                f"application_role='{role.value}' requires "
                f"frequency_scale_factor, not scheme."
            )
        if role in _SCHEME_ROLES and self.scheme is None:
            raise ValueError(
                f"application_role='{role.value}' requires scheme, "
                f"not frequency_scale_factor."
            )
        return self

    @model_validator(mode="after")
    def validate_fsf_requires_source_calculation(self) -> Self:
        if (
            self.frequency_scale_factor is not None
            and self.source_calculation_key is None
        ):
            raise ValueError(
                "frequency_scale_factor requires source_calculation_key "
                "(the frequency calculation the scale factor was applied to)."
            )
        return self

    @model_validator(mode="after")
    def validate_unique_components(self) -> Self:
        keys = [(c.component_kind, c.key) for c in self.components]
        if len(set(keys)) != len(keys):
            raise ValueError("Components must be unique by (component_kind, key).")
        return self
