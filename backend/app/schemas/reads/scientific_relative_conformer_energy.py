"""Read schemas for the relative conformer energy routes.

``POST /scientific/species-entries/{ref}/conformers/relative-energies`` and its ``/manifest`` download.

How much higher or lower in energy each conformer of one species entry is than a conformer the caller *names*. A
read: nothing is stored. See ``backend/docs/specs/relative_conformer_energy.md`` for the contract and
``docs/guides/relative_conformer_energies.md`` for how to read an answer.

The request is strict: unknown fields are refused, references are public refs (never a database id), and the four
scientific choices (the reference conformer, the energy kind, the level and the certificate) have no default, so
nothing is chosen for the caller. There is no sort expression, no pagination, no rule and no bound: the bounds are
the server's. Every violation of the request's own rules is an ordinary 422 naming the field.

The response lists members ordered by conformer ref. There is deliberately no rank field: the order is not a
ranking. An absent member carries a coded reason and ``null`` values, never zero.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from tckdb_schemas.structure_declarations import ActualProtocolDeclaration

from app.db.models.common import CalculationQuality, RecordReviewStatus
from app.schemas.reads.scientific_common import ProfiledRequestEcho
from app.services.relative_conformer_energy.models import (
    SUPPORTED_KINDS,
    Certificate,
    EnergyKind,
    LevelSelector,
    RelativeEnergyOutcome,
    RelativeEnergyRequest,
)
from app.services.structure_selection.models import CoverageRequirement, RepeatPolicy


class RelativeEnergyLevel(BaseModel):
    """The one level a comparison is made at: exactly one of a level of theory or a composite scheme."""

    model_config = ConfigDict(extra="forbid")

    level_of_theory_ref: str | None = Field(
        default=None, min_length=1, max_length=64, description="A public lot_ ref (merged levels resolve to the kept one)."
    )
    composite_scheme_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="A public csch_ ref. Name this, not the lot_ ref, for a level bound to a composite scheme.",
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> RelativeEnergyLevel:
        if (self.level_of_theory_ref is None) == (self.composite_scheme_ref is None):
            raise ValueError("level names exactly one of level_of_theory_ref and composite_scheme_ref")
        return self


class RelativeConformerEnergyRequest(BaseModel):
    """Relative conformer energies of one species entry against a named reference conformer."""

    model_config = ConfigDict(extra="forbid")

    reference_conformer_ref: str = Field(
        min_length=1, max_length=64, description="The named reference conformer group (cg_ ref) of this entry. Required."
    )
    energy_kind: EnergyKind = Field(
        description=(
            "electronic_energy or zero_kelvin_energy. Required; there is no default. enthalpy and gibbs_energy are "
            "recognised and refused with 422 relative_conformer_energy_unsupported."
        )
    )
    temperature_k: float | None = Field(
        default=None, gt=0, description="For enthalpy and gibbs_energy only (both refused in this release)."
    )
    level: RelativeEnergyLevel = Field(description="The one level of theory (or composite scheme) compared. Required.")
    certificate: Certificate = Field(
        description=(
            "The curvature evidence each member needs: local_minimum_any_surface or local_minimum_on_energy_surface. "
            "Required; there is no default. 'none' is recognised and refused with 422 relative_conformer_energy_unsupported."
        )
    )
    recipe: ActualProtocolDeclaration | None = Field(
        default=None,
        description="Narrows within the level. The units' own stated facts must establish it; a request never fills a missing fact.",
    )
    member_refs: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=500,
        description="The conformer groups (cg_ refs) compared. Omitted: every visible conformer group of the entry. The reference is always included.",
    )
    coverage_requirement: CoverageRequirement = Field(
        default=CoverageRequirement.known_values,
        description="known_values lists what is known; all_requested_members is a complete claim.",
    )
    repeat_policy: RepeatPolicy = RepeatPolicy.retain_alternates
    min_review_status: RecordReviewStatus | None = Field(
        default=None, description="A stricter review floor than the read profile's."
    )
    permitted_quality: list[CalculationQuality] | None = Field(
        default=None, max_length=3, description="Calculation qualities that qualify. Omitted: raw and curated."
    )
    require_stable_reference: bool = False

    @field_validator("member_refs")
    @classmethod
    def _members_distinct(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("member_refs names a member twice")
        return value

    @model_validator(mode="after")
    def _temperature_belongs_to_the_deferred_kinds(self) -> RelativeConformerEnergyRequest:
        if self.energy_kind in SUPPORTED_KINDS and self.temperature_k is not None:
            raise ValueError("temperature_k belongs to enthalpy and gibbs_energy only")
        if self.energy_kind not in SUPPORTED_KINDS and self.temperature_k is None:
            raise ValueError("temperature_k is required for enthalpy and gibbs_energy")
        return self

    def service_request(self, *, level: LevelSelector | None = None) -> RelativeEnergyRequest:
        """The service's request. ``level`` carries the canonical refs once the route has resolved them."""
        return RelativeEnergyRequest(
            reference_conformer_ref=self.reference_conformer_ref,
            energy_kind=self.energy_kind,
            level=level
            or LevelSelector(
                level_of_theory_ref=self.level.level_of_theory_ref, composite_scheme_ref=self.level.composite_scheme_ref
            ),
            certificate=self.certificate,
            recipe=self.recipe.model_dump(mode="json", exclude_none=True) if self.recipe is not None else None,
            member_refs=tuple(self.member_refs) if self.member_refs is not None else None,
            coverage=self.coverage_requirement,
            repeat_policy=self.repeat_policy,
            min_review_status=self.min_review_status,
            permitted_quality=frozenset(self.permitted_quality) if self.permitted_quality is not None else None,
            require_stable_reference=self.require_stable_reference,
            temperature_k=self.temperature_k,
        )

    @model_validator(mode="after")
    def _service_rules(self) -> RelativeConformerEnergyRequest:
        self.service_request()
        return self


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class RelativeEnergyRequestEcho(ProfiledRequestEcho):
    """The normalised request: what was asked, with the defaults that were applied and the canonical level refs."""

    entry_ref: str
    reference_conformer_ref: str
    energy_kind: EnergyKind
    temperature_k: float | None
    level: dict[str, str | None]
    certificate: Certificate
    recipe: dict[str, Any] | None
    member_refs: list[str] | None
    coverage_requirement: CoverageRequirement
    repeat_policy: RepeatPolicy
    min_review_status: RecordReviewStatus | None
    permitted_quality: list[CalculationQuality] | None
    require_stable_reference: bool


class RelativeEnergyReview(BaseModel):
    effective_floor: RecordReviewStatus
    effective_statuses: list[RecordReviewStatus]


class RelativeEnergyUnits(BaseModel):
    relative: str
    stored: str
    hartree_to_kj_mol: float


class RelativeEnergyLevelBlock(BaseModel):
    level_of_theory_ref: str | None
    composite_scheme_ref: str | None
    recipe_cohort_id: str | None
    recipe_facts: list[dict[str, Any]]


class RelativeEnergyReference(BaseModel):
    conformer_ref: str
    status: str
    value_hartree: float | None
    value_uncertainty_hartree: float | None
    determination_refs: list[str]
    absent_reason: str | None


class RelativeEnergyCertificate(BaseModel):
    claim: str
    supported: bool
    surface_level_refs: list[str]
    on_energy_surface: bool
    witness_refs: list[str]


class RelativeEnergyAlternate(BaseModel):
    determination_ref: str
    value_hartree: float


class RelativeEnergyMember(BaseModel):
    """One conformer group. There is no rank field: members are ordered by ref and the order is not a ranking."""

    conformer_ref: str
    status: str = Field(description="reference, present, absent or unresolved.")
    relative_energy_kj_mol: float | None = Field(
        description="The member minus the reference; negative is below it. Null unless both have a value."
    )
    relative_energy_hartree: float | None
    value_hartree: float | None = Field(description="The stored value the difference was taken from.")
    value_uncertainty_hartree: float | None = Field(description="As stored with the value; never combined with another.")
    representative: bool
    determination_refs: list[str]
    energy_calculation_refs: list[str]
    evaluated_geometry_refs: list[str]
    certificate: RelativeEnergyCertificate | None
    alternates: list[RelativeEnergyAlternate]
    absent_reason: str | None = Field(description="A coded reason when the member has no single value; never zero.")
    detail: list[str]


class RelativeEnergyIntegrity(BaseModel):
    algorithm: str
    manifest_sha256: str
    note: str


class RelativeConformerEnergyResponse(BaseModel):
    """The relative energies, built from the replayable manifest so the two cannot disagree."""

    request: RelativeEnergyRequestEcho
    review: RelativeEnergyReview
    outcome: RelativeEnergyOutcome
    basis: str
    energy_kind: EnergyKind
    temperature_k: float | None
    units: RelativeEnergyUnits
    level: RelativeEnergyLevelBlock
    reference: RelativeEnergyReference
    members: list[RelativeEnergyMember]
    coverage: dict[str, Any]
    unresolved: list[dict[str, Any]]
    cohorts: list[dict[str, Any]]
    notes: list[str]
    search_completeness: str
    versions: dict[str, Any]
    snapshot_isolation: str
    integrity: RelativeEnergyIntegrity


class RelativeConformerEnergyManifest(BaseModel):
    """The replayable manifest of the ``/manifest`` download.

    Public refs only: the normalised request, read profile, snapshot isolation, subject, the compared groups and
    determinations, every normalised calculation and determination, each assessment and the computed answer. The
    digests in ``integrity`` are checksums, not signatures. The download is a new snapshot of the current data, not a
    retrieval of an earlier answer. Replay it with ``app.services.relative_conformer_energy.replay_relative_energies``.
    """

    model_config = ConfigDict(extra="allow")

    manifest_format: str
    manifest_format_version: int
    versions: dict[str, Any]
    request: dict[str, Any]
    structure_request: dict[str, Any]
    effective_review_statuses: list[RecordReviewStatus]
    read_profile: dict[str, Any]
    snapshot_isolation: str
    subject: dict[str, Any]
    population: dict[str, Any]
    calculations: list[dict[str, Any]]
    determinations: list[dict[str, Any]]
    assessments: list[dict[str, Any]]
    result: dict[str, Any]
    outcome: RelativeEnergyOutcome
    integrity: RelativeEnergyIntegrity
