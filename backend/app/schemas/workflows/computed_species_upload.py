"""Bundle upload schemas for ``POST /api/v1/uploads/computed-species``.

The bundle is a single self-contained payload that carries identity +
conformers + per-conformer calculations + artifacts + optional thermo.
All cross-references inside the bundle are local string keys; **no
database FK ids are accepted anywhere** (DR-0029 Requirement 1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.db.models.common import (
    CalculationDependencyRole,
    CalculationQuality,
    CalculationType,
    ScientificOriginKind,
    ThermoCalculationRole,
)
from app.schemas.common import SchemaBase
from app.schemas.entities.thermo import ThermoNASACreate, ThermoPointCreate
from app.schemas.fragments.artifact import ArtifactIn
from app.schemas.fragments.calculation import (
    CalculationParameterObservation,
    FreqResultPayload,
    IRCResultPayload,
    NEBResultPayload,
    OptResultPayload,
    SPResultPayload,
)
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.fragments.refs import (
    LevelOfTheoryRef,
    SoftwareReleaseRef,
    WorkflowToolReleaseRef,
)
from app.schemas.upload_warning import UploadWarning
from app.schemas.workflows.energy_correction_upload import (
    AppliedEnergyCorrectionUploadPayload,
)
from app.schemas.workflows.literature_upload import LiteratureUploadRequest


# Field names that are forbidden anywhere in the bundle payload tree.
# DR-0029 Requirement 1: the bundle is self-contained — every cross-
# reference is a local string key. A producer accidentally serializing a
# DB FK id (e.g. ``existing_calculation_id`` or ``source_calculation_id``)
# inside ``parameters_json`` would otherwise leak past Pydantic's
# ``extra="forbid"`` because ``parameters_json`` is typed ``dict``.
_FORBIDDEN_DB_ID_FIELDS: frozenset[str] = frozenset(
    {
        "existing_calculation_id",
        "existing_conformer_id",
        "existing_conformer_observation_id",
        "existing_species_entry_id",
        "source_calculation_id",
        "source_conformer_observation_id",
    }
)


def _walk_for_forbidden_fields(value: Any, path: str) -> None:
    """Recursively walk a JSON-like value, rejecting forbidden FK ids.

    Pydantic's ``extra="forbid"`` only catches unknown keys at the model
    boundary; ``parameters_json`` is opaque ``dict``, so a producer could
    embed ``existing_calculation_id`` inside it and bypass the structural
    rejection. This walk closes that gap.
    """
    if isinstance(value, dict):
        for k, v in value.items():
            if k in _FORBIDDEN_DB_ID_FIELDS:
                raise ValueError(
                    f"{path}.{k}: bundle payload must not include database "
                    f"identifier fields (use local string keys instead)."
                )
            _walk_for_forbidden_fields(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _walk_for_forbidden_fields(item, f"{path}[{i}]")


# ---------------------------------------------------------------------------
# Calculation block
# ---------------------------------------------------------------------------


class CalculationDependencyInBundle(SchemaBase):
    """A calculation_dependency edge declared by local keys.

    Auto-creation for additional_calculations → primary opt continues to
    fire (per
    ``app.services.calculation_resolution._DEPENDENCY_ROLE_FOR_TYPE``).
    This explicit list is for non-auto edges (e.g., an opt restart that
    optimized_from another opt in the same bundle).
    """

    parent_calculation_key: str = Field(min_length=1)
    role: CalculationDependencyRole


class CalculationInBundle(SchemaBase):
    """One calculation within a conformer's calc list.

    Carries everything the primitive ``CalculationWithResultsPayload``
    carries plus a local ``key``, plus optional ``depends_on`` and
    ``artifacts``. Crucially does NOT carry ``existing_calculation_id``
    (DR-0029 Requirement 1) — the bundle is self-contained.
    """

    key: str = Field(min_length=1)
    type: CalculationType
    quality: CalculationQuality = CalculationQuality.raw

    software_release: SoftwareReleaseRef
    workflow_tool_release: WorkflowToolReleaseRef | None = None
    level_of_theory: LevelOfTheoryRef
    literature: LiteratureUploadRequest | None = None

    parameters_json: dict | None = None
    parameters: list[CalculationParameterObservation] | None = None
    parameters_parser_version: str | None = None
    parameters_extracted_at: datetime | None = None

    opt_result: OptResultPayload | None = None
    freq_result: FreqResultPayload | None = None
    sp_result: SPResultPayload | None = None
    irc_result: IRCResultPayload | None = None
    neb_result: NEBResultPayload | None = None

    depends_on: list[CalculationDependencyInBundle] = Field(default_factory=list)

    artifacts: list[ArtifactIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_result_matches_type(self) -> Self:
        """One result block, matching ``type`` (mirrors
        ``CalculationWithResultsPayload.validate_result_matches_type``)."""
        allowed = {
            CalculationType.opt: "opt_result",
            CalculationType.freq: "freq_result",
            CalculationType.sp: "sp_result",
            CalculationType.irc: "irc_result",
            CalculationType.neb: "neb_result",
        }
        allowed_field = allowed.get(self.type)
        for field_name in (
            "opt_result",
            "freq_result",
            "sp_result",
            "irc_result",
            "neb_result",
        ):
            value = getattr(self, field_name)
            if value is not None and field_name != allowed_field:
                raise ValueError(
                    f"Result block '{field_name}' is not allowed for "
                    f"calculation type '{self.type.value}'. "
                    f"Expected '{allowed_field}' or no result."
                )
        return self

    @model_validator(mode="after")
    def reject_database_id_fields(self) -> Self:
        """DR-0029 Requirement 1: bundle must not carry DB FK ids.

        Walks ``parameters_json`` recursively to catch FK ids that would
        bypass the model's ``extra="forbid"`` (which only enforces
        structural keys, not opaque ``dict`` payloads).
        """
        if self.parameters_json is not None:
            _walk_for_forbidden_fields(
                self.parameters_json, f"calculation '{self.key}'.parameters_json"
            )
        return self


# ---------------------------------------------------------------------------
# Conformer block
# ---------------------------------------------------------------------------


class ConformerInBundle(SchemaBase):
    """One conformer with its primary opt + additional calcs."""

    key: str = Field(min_length=1)
    label: str | None = Field(default=None, max_length=64)
    geometry: GeometryPayload
    primary_calculation: CalculationInBundle
    additional_calculations: list[CalculationInBundle] = Field(default_factory=list)
    note: str | None = None

    @model_validator(mode="after")
    def validate_primary_is_opt(self) -> Self:
        if self.primary_calculation.type is not CalculationType.opt:
            raise ValueError(
                "ConformerInBundle.primary_calculation.type must be 'opt'."
            )
        return self


# ---------------------------------------------------------------------------
# Thermo block
# ---------------------------------------------------------------------------


class ThermoSourceCalcInBundle(SchemaBase):
    """Thermo → calc link by local key.

    Only ``calculation_key`` is allowed inside a bundle.
    ``existing_calculation_id`` (DR-0028) is the primitive-endpoint
    mechanism and is intentionally not present here (DR-0029 Requirement 1).
    """

    calculation_key: str = Field(min_length=1)
    role: ThermoCalculationRole


class AppliedEnergyCorrectionInBundle(AppliedEnergyCorrectionUploadPayload):
    """Same shape as the primitive applied-correction payload but with
    bundle-level local-key references.

    The base class's ``source_calculation_key`` already points at a local
    string key; in the bundle context, that key resolves against the
    bundle's global calc-key namespace, not against an inline calcs list
    in the same upload.
    """


class ThermoInBundle(SchemaBase):
    """Thermo block within a bundle. Lives at bundle level (one thermo
    per species_entry); references calcs from any conformer via the
    bundle's global calc-key namespace.
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
    note: str | None = None

    nasa: ThermoNASACreate | None = None
    points: list[ThermoPointCreate] = Field(default_factory=list)

    source_calculations: list[ThermoSourceCalcInBundle] = Field(default_factory=list)
    applied_energy_corrections: list[AppliedEnergyCorrectionInBundle] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_temperature_range(self) -> Self:
        if (
            self.tmin_k is not None
            and self.tmax_k is not None
            and self.tmin_k > self.tmax_k
        ):
            raise ValueError("tmin_k must be less than or equal to tmax_k.")
        return self

    @model_validator(mode="after")
    def validate_unique_points(self) -> Self:
        temps = [p.temperature_k for p in self.points]
        if len(set(temps)) != len(temps):
            raise ValueError("Thermo points must be unique by temperature_k.")
        return self

    @model_validator(mode="after")
    def validate_unique_source_calculation_pairs(self) -> Self:
        pairs = [(sc.calculation_key, sc.role) for sc in self.source_calculations]
        if len(set(pairs)) != len(pairs):
            raise ValueError(
                "thermo.source_calculations must be unique by "
                "(calculation_key, role)."
            )
        return self

    @model_validator(mode="after")
    def validate_has_scientific_content(self) -> Self:
        has_scalar = self.h298_kj_mol is not None or self.s298_j_mol_k is not None
        has_nasa = self.nasa is not None
        has_points = bool(self.points)
        if not (has_scalar or has_nasa or has_points):
            raise ValueError(
                "Thermo block must include at least one of: a scalar "
                "thermo value (h298_kj_mol or s298_j_mol_k), a NASA block, "
                "or one or more thermo points."
            )
        return self


# ---------------------------------------------------------------------------
# Top-level request
# ---------------------------------------------------------------------------


class ComputedSpeciesUploadRequest(SchemaBase):
    """Bundle upload payload for one computed species result."""

    species_entry: SpeciesEntryIdentityPayload

    conformers: list[ConformerInBundle] = Field(min_length=1)
    thermo: ThermoInBundle | None = None

    workflow_tool_release: WorkflowToolReleaseRef | None = None
    note: str | None = None

    @model_validator(mode="after")
    def validate_unique_conformer_keys(self) -> Self:
        keys = [c.key for c in self.conformers]
        if len(set(keys)) != len(keys):
            raise ValueError("conformers must have unique keys.")
        return self

    @model_validator(mode="after")
    def validate_unique_calculation_keys_global(self) -> Self:
        """Calc keys are GLOBAL across the bundle.

        Thermo source links and applied-correction source links reference
        any calc from any conformer; per-conformer scoping would force
        every reference to be a (conformer_key, calculation_key) tuple.
        Producers that want disambiguation can prefix keys
        (``conf0_opt``, ``conf1_opt``).
        """
        all_keys = self._all_calc_keys_list()
        if len(set(all_keys)) != len(all_keys):
            raise ValueError("calculation keys must be unique across the bundle.")
        return self

    @model_validator(mode="after")
    def validate_dependency_keys_resolve(self) -> Self:
        defined = self._all_calc_keys()
        for conf in self.conformers:
            for calc in (conf.primary_calculation, *conf.additional_calculations):
                for dep in calc.depends_on:
                    if dep.parent_calculation_key not in defined:
                        raise ValueError(
                            f"calculation '{calc.key}' depends_on undefined "
                            f"calculation_key '{dep.parent_calculation_key}'."
                        )
        return self

    @model_validator(mode="after")
    def validate_thermo_source_keys_resolve(self) -> Self:
        if self.thermo is None:
            return self
        defined = self._all_calc_keys()
        for sc in self.thermo.source_calculations:
            if sc.calculation_key not in defined:
                raise ValueError(
                    f"thermo.source_calculations references undefined "
                    f"calculation_key '{sc.calculation_key}'."
                )
        for i, ac in enumerate(self.thermo.applied_energy_corrections):
            if (
                ac.source_calculation_key is not None
                and ac.source_calculation_key not in defined
            ):
                raise ValueError(
                    f"thermo.applied_energy_corrections[{i}].source_calculation_key "
                    f"references undefined calculation_key "
                    f"'{ac.source_calculation_key}'."
                )
        return self

    def _all_calc_keys_list(self) -> list[str]:
        keys: list[str] = []
        for conf in self.conformers:
            keys.append(conf.primary_calculation.key)
            keys.extend(c.key for c in conf.additional_calculations)
        return keys

    def _all_calc_keys(self) -> set[str]:
        return set(self._all_calc_keys_list())


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class CalculationUploadRefInBundle(SchemaBase):
    """Bundle-flavored CalculationUploadRef carrying the local key plus
    the assigned id."""

    key: str
    calculation_id: int
    type: CalculationType
    role: Literal["primary", "additional"]


class ConformerUploadRefInBundle(SchemaBase):
    """Per-conformer ref in the bundle response."""

    key: str
    conformer_group_id: int
    conformer_observation_id: int
    primary_calculation: CalculationUploadRefInBundle
    additional_calculations: list[CalculationUploadRefInBundle] = Field(
        default_factory=list
    )


class ThermoUploadRefInBundle(SchemaBase):
    thermo_id: int


class ComputedSpeciesUploadResult(BaseModel):
    species_entry_id: int
    type: str = "computed_species"
    conformers: list[ConformerUploadRefInBundle]
    thermo: ThermoUploadRefInBundle | None = None
    warnings: list[UploadWarning] = []
