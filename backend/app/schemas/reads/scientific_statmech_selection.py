"""The request schema of the statmech selection operation (``POST /scientific/statmech/select``).

Which supplied statistical model supports a declared use, and is any applicable whole recipe preferred by scoped
evidence? A read: nothing is stored, no endorsement is created, browse order is untouched. See
``backend/docs/specs/statmech_selection_contract.md``.

The request is strict. Unknown fields are refused; references are typed public refs (never a database id); there
is no pagination, no sort expression and no caller-authored rule; and the engineering bounds are the server's:
the schema has no field for one. A misspelled token is a validation error. A *recognised deferred* task is valid
and is answered with a scientific ``unsupported_task`` outcome. Every cross-field rule lives in
:class:`~app.services.statmech_selection.models.StatmechRequest`, which this schema builds, so the two cannot
disagree.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import SelectionPolicy
from app.services.statmech_selection.models import (
    Conditions,
    EnergyReference,
    Population,
    Scope,
    StatmechRequest,
    Target,
)
from app.services.statmech_selection.vocabulary import (
    CoverageRequirement,
    DeferredTask,
    DividingSurface,
    ElectronicScope,
    EnergyZero,
    EnvironmentKind,
    Intent,
    IsotopeConvention,
    Normalization,
    Objective,
    Observable,
    OwnerKind,
    PopulationKind,
    ResultMode,
    ScopeKind,
    StereochemicalScope,
    Task,
    ZpeInclusion,
)

SpeciesEntryRef = Annotated[str, StringConstraints(pattern=r"^spe_[A-Za-z0-9]+$", max_length=64)]
TransitionStateEntryRef = Annotated[str, StringConstraints(pattern=r"^tse_[A-Za-z0-9]+$", max_length=64)]
ConformerGroupRef = Annotated[str, StringConstraints(pattern=r"^cg_[A-Za-z0-9]+$", max_length=64)]
DeterminationRef = Annotated[str, StringConstraints(pattern=r"^sdet_[A-Za-z0-9]+$", max_length=64)]
ModelRef = Annotated[str, StringConstraints(pattern=r"^smd_[A-Za-z0-9]+$", max_length=64)]
ReleaseRef = Annotated[str, StringConstraints(pattern=r"^rel_[A-Za-z0-9]+$", max_length=64)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AtomScope(_Strict):
    kind: Literal["atom"]


class BasinScope(_Strict):
    kind: Literal["basin"]
    conformer_group_ref: ConformerGroupRef


class EnsembleScope(_Strict):
    kind: Literal["explicit_equilibrium_ensemble"]
    ensemble_declaration_ref: ModelRef = Field(
        description="The immutable model declaration whose members are the ensemble. A filtered context is not one."
    )


class TsDeterminationScope(_Strict):
    kind: Literal["ts_determination"]
    determination_ref: DeterminationRef
    dividing_surface: DividingSurface | None = None
    reaction_coordinate_excluded: bool | None = Field(
        default=None, description="True: the reaction coordinate is excluded from the transverse partition."
    )


ScopeBody = Annotated[
    AtomScope | BasinScope | EnsembleScope | TsDeterminationScope, Field(discriminator="kind")
]


class _TargetBase(_Strict):
    scope: ScopeBody
    isotope_convention: IsotopeConvention
    electronic_scope: ElectronicScope
    stereochemical_scope: StereochemicalScope


class SpeciesEntryTarget(_TargetBase):
    kind: Literal["species_entry"]
    species_entry_ref: SpeciesEntryRef


class TransitionStateEntryTarget(_TargetBase):
    kind: Literal["transition_state_entry"]
    transition_state_entry_ref: TransitionStateEntryRef


TargetBody = Annotated[SpeciesEntryTarget | TransitionStateEntryTarget, Field(discriminator="kind")]


class Domain(_Strict):
    min: float = Field(allow_inf_nan=False)
    max: float = Field(allow_inf_nan=False)


class EnvironmentBody(_Strict):
    kind: EnvironmentKind = EnvironmentKind.gas


class EnergyReferenceBody(_Strict):
    zero: EnergyZero
    zpe: ZpeInclusion


class ConditionsBody(_Strict):
    temperature_domain_k: Domain | None = None
    energy_domain_kj_mol: Domain | None = Field(
        default=None, description="Energy-resolved (deferred) tasks only; states its energy_reference."
    )
    energy_reference: EnergyReferenceBody | None = None
    environment: EnvironmentBody = Field(default_factory=EnvironmentBody)
    normalization: Normalization | None = None
    pressure_pa: float | None = Field(default=None, allow_inf_nan=False)
    concentration_mol_m3: float | None = Field(default=None, allow_inf_nan=False)
    volume_m3: float | None = Field(default=None, allow_inf_nan=False)


class AuthorizedMatchingModels(_Strict):
    kind: Literal["authorized_matching_models"]


class ExplicitModelRevisions(_Strict):
    kind: Literal["model_revisions"]
    model_refs: list[ModelRef] = Field(min_length=1, max_length=500)


class ReleasePopulation(_Strict):
    kind: Literal["release"]
    release_ref: ReleaseRef


PopulationBody = Annotated[
    AuthorizedMatchingModels | ExplicitModelRevisions | ReleasePopulation, Field(discriminator="kind")
]


class StatmechSelectionRequest(_Strict):
    """Qualify a supplied statistical model for a declared use, or compare applicable whole recipes."""

    contract_version: Literal["statmech-selection-v1"] = "statmech-selection-v1"
    intent: Intent
    task: Task | DeferredTask
    observable: Observable | None = Field(
        default=None, description="Omitted for a component task, whose observable is implied."
    )
    target: TargetBody
    conditions: ConditionsBody = Field(default_factory=ConditionsBody)
    population: PopulationBody = Field(default_factory=lambda: AuthorizedMatchingModels(kind="authorized_matching_models"))
    coverage_requirement: CoverageRequirement = CoverageRequirement.entire_requested_domain
    comparison_objective: Objective | None = None
    reference_model_ref: ModelRef | None = None
    administrative_policy: SelectionPolicy = SelectionPolicy.default
    result_mode: ResultMode = ResultMode.all
    min_review_status: RecordReviewStatus | None = Field(
        default=None, description="A stricter record-review floor than the read profile's; it never relaxes it."
    )

    @model_validator(mode="after")
    def _service_rules(self) -> StatmechSelectionRequest:
        self.service_request()
        return self

    def service_request(self) -> StatmechRequest:
        """The normalised request. Raises ``ValueError`` (a 422 naming the problem) for a contradictory one."""
        t = self.target
        scope_body = t.scope
        scope = Scope(
            kind=ScopeKind(scope_body.kind),
            conformer_group_ref=getattr(scope_body, "conformer_group_ref", None),
            ensemble_declaration_ref=getattr(scope_body, "ensemble_declaration_ref", None),
            determination_ref=getattr(scope_body, "determination_ref", None),
            dividing_surface=getattr(scope_body, "dividing_surface", None),
            reaction_coordinate_excluded=getattr(scope_body, "reaction_coordinate_excluded", None),
        )
        if isinstance(t, SpeciesEntryTarget):
            owner_kind, owner_ref = OwnerKind.species_entry, t.species_entry_ref
        else:
            owner_kind, owner_ref = OwnerKind.transition_state_entry, t.transition_state_entry_ref
        target = Target(
            owner_kind=owner_kind,
            owner_ref=owner_ref,
            scope=scope,
            isotope_convention=t.isotope_convention,
            electronic_scope=t.electronic_scope,
            stereochemical_scope=t.stereochemical_scope,
        )
        c = self.conditions

        def pair(d: Domain | None) -> tuple[float, float] | None:
            return (d.min, d.max) if d is not None else None

        conditions = Conditions(
            temperature_domain_k=pair(c.temperature_domain_k),
            energy_domain_kj_mol=pair(c.energy_domain_kj_mol),
            energy_reference=EnergyReference(c.energy_reference.zero, c.energy_reference.zpe)
            if c.energy_reference
            else None,
            environment=c.environment.kind,
            normalization=c.normalization,
            pressure_pa=c.pressure_pa,
            concentration_mol_m3=c.concentration_mol_m3,
            volume_m3=c.volume_m3,
        )
        p = self.population
        population = Population(
            kind=PopulationKind(p.kind),
            model_refs=tuple(getattr(p, "model_refs", ())),
            release_ref=getattr(p, "release_ref", None),
        )
        return StatmechRequest(
            task=self.task,
            intent=self.intent,
            target=target,
            observable=self.observable,
            conditions=conditions,
            population=population,
            coverage=self.coverage_requirement,
            objective=self.comparison_objective,
            reference_model_ref=self.reference_model_ref,
            admin_policy=self.administrative_policy,
            result_mode=self.result_mode,
            min_review_status=self.min_review_status,
        )
