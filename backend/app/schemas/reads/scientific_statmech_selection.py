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

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import ProfiledRequestEcho, SelectionPolicy
from app.services.public_refs import PREFIXES, PUBLIC_REF_BODY_LEN
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
    Outcome,
    OwnerKind,
    PopulationKind,
    ResultMode,
    ScopeKind,
    StereochemicalScope,
    Task,
    ZpeInclusion,
)


def _ref_pattern(model: str) -> str:
    """The public-ref grammar of one record type: its registered prefix, then a base32 body of the registered length."""
    return rf"^{PREFIXES[model]}_[a-z2-7]{{{PUBLIC_REF_BODY_LEN}}}$"


SpeciesEntryRef = Annotated[str, StringConstraints(pattern=_ref_pattern("SpeciesEntry"))]
TransitionStateEntryRef = Annotated[str, StringConstraints(pattern=_ref_pattern("TransitionStateEntry"))]
ConformerGroupRef = Annotated[str, StringConstraints(pattern=_ref_pattern("ConformerGroup"))]
DeterminationRef = Annotated[str, StringConstraints(pattern=_ref_pattern("StructureDetermination"))]
ModelRef = Annotated[str, StringConstraints(pattern=_ref_pattern("StatmechModelDeclaration"))]
CandidateRef = Annotated[
    str,
    StringConstraints(pattern=rf"^(?:{PREFIXES['Statmech']}|{PREFIXES['StatmechModelDeclaration']})_[a-z2-7]{{{PUBLIC_REF_BODY_LEN}}}$"),
]
ReleaseRef = Annotated[str, StringConstraints(pattern=_ref_pattern("DatasetRelease"))]


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
    model_refs: list[CandidateRef] = Field(min_length=1, max_length=500)


class ReleasePopulation(_Strict):
    kind: Literal["release"]
    release_ref: ReleaseRef


PopulationBody = Annotated[
    AuthorizedMatchingModels | ExplicitModelRevisions | ReleasePopulation, Field(discriminator="kind")
]


class StatmechSelectionRequest(_Strict):
    """Qualify a supplied statistical model for a declared use, or compare applicable whole recipes."""

    contract_version: Literal[1] = 1
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


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


class StatmechSelectionRequestEcho(ProfiledRequestEcho):
    """The normalised request the decision was made under, with the read profile it was made under.

    Every key the normalised request carries is kept (``extra=allow``), including the server's own ``bounds``: a caller
    can neither set nor raise one.
    """

    model_config = ConfigDict(extra="allow")

    contract_version: int
    task: str
    intent: str
    observable: str | None = None
    target: dict[str, Any]
    conditions: dict[str, Any]
    population: dict[str, Any]
    coverage_requirement: str
    comparison_objective: str | None = None
    reference_model_ref: str | None = None
    administrative_policy: str
    result_mode: str
    min_review_status: RecordReviewStatus | None = None
    bounds: dict[str, Any] = Field(
        description="The server's engineering bounds this decision was made under (never settable by the caller)."
    )


class StatmechSelectionReview(BaseModel):
    effective_floor: RecordReviewStatus
    effective_statuses: list[RecordReviewStatus]


class StatmechSelectionReason(BaseModel):
    code: str
    applicability: str
    category: str


class StatmechSelectionCandidate(BaseModel):
    """One assessed candidate: a declared model (``smd_``) or a legacy record read as stored (``sm_``)."""

    candidate_ref: str
    kind: str
    owner_ref: str
    review_status: RecordReviewStatus
    created_at: datetime
    applicability: str
    reasons: list[StatmechSelectionReason]
    advisory: list[str] = Field(
        description="Disclosed, never excluding: e.g. imported_parameters_not_revalidated:<role>, "
        "source_literature_unidentified:<role>."
    )
    comparative_unknown: list[str]
    evidence: dict[str, list[str]]
    recipe: dict[str, Any] | None = None
    supersession: dict[str, Any] | None = None
    eligible: bool


class StatmechSelectionSelection(BaseModel):
    """The three mutually exclusive references. At most one is set; each carries its basis and population limit."""

    scientific_ref: str | None = None
    sole_eligible_ref: str | None = None
    administrative_ref: str | None = None
    basis: str | None = None
    limitation: str | None = None


class StatmechSelectionPopulation(BaseModel):
    visible_candidates: int
    nested_rows: int
    excluded_by_review: list[dict[str, str]]
    excluded_count: int
    excluded_by_review_withheld: bool


class StatmechSelectionConflicts(BaseModel):
    opposing_pairs: list[dict[str, Any]]
    cycles: list[list[str]]
    overridden_edges: list[dict[str, Any]]


class StatmechSelectionManifestDescriptor(BaseModel):
    """Where the complete decision manifest is, and what it is: a fresh snapshot, not a stored decision."""

    manifest_format_version: int
    manifest_sha256: str
    canonical_bytes: int
    path: str
    note: str


class StatmechSelectionIntegrity(BaseModel):
    algorithm: str
    manifest_sha256: str
    note: str


class StatmechSelectionResponse(BaseModel):
    """The outcome of a statmech selection and everything it rests on, public refs only.

    ``outcome`` is the scientific outcome (a valid negative answer is HTTP 200). ``selection`` carries at most one
    reference; ``intent=qualify`` and every unresolved outcome carry none. ``coverage`` always says it is the caller's
    authorized population only, never a physical-search certificate. No computed thermodynamic value is returned.
    ``advisories`` and ``notices`` surface the disclosures that do not exclude a candidate. ``integrity`` carries the
    manifest checksum: a checksum, not a signature.
    """

    request: StatmechSelectionRequestEcho
    review: StatmechSelectionReview
    versions: dict[str, Any]
    outcome: Outcome
    basis: str
    selection: StatmechSelectionSelection
    summary: dict[str, Any] | None = None
    population: StatmechSelectionPopulation
    coverage: dict[str, Any]
    candidates: list[StatmechSelectionCandidate]
    edges: list[dict[str, Any]]
    fronts: list[list[str]]
    conflicts: StatmechSelectionConflicts
    undecided: list[dict[str, Any]]
    advisories: dict[str, list[str]]
    notices: list[str]
    protocol: dict[str, Any]
    manifest: StatmechSelectionManifestDescriptor
    snapshot_isolation: str
    integrity: StatmechSelectionIntegrity


class StatmechSelectionManifestRequest(ProfiledRequestEcho):
    """The manifest's normalised request; every key the decision recorded is kept."""

    model_config = ConfigDict(extra="allow")


class StatmechSelectionManifest(BaseModel):
    """The replayable decision manifest of the ``/manifest`` download.

    Public refs only: the normalised request, read profile, snapshot isolation, subject, every candidate's facts and
    source calculation, each assessment, the registry entries consulted and the decision. ``reproduce_statmech_ordering``,
    ``reassess_statmech_captured_inputs`` and ``replay_statmech_decision`` recompute it offline. The digests in
    ``integrity`` are checksums, not signatures: anyone who edits a manifest can recompute them, so replay shows the
    reasoning follows from the captured inputs and proves neither that they are true nor that the population was
    complete. The download is a new snapshot of the current data, not a retrieval of an earlier decision.
    """

    model_config = ConfigDict(extra="allow")

    manifest_format_version: int
    versions: dict[str, Any]
    assessment_semantics: dict[str, Any]
    request: StatmechSelectionManifestRequest
    effective_review_statuses: list[RecordReviewStatus]
    read_profile: dict[str, Any]
    snapshot_isolation: str
    subject: dict[str, Any]
    population: dict[str, Any]
    candidates: list[dict[str, Any]]
    calculations: list[dict[str, Any]]
    assessments: list[dict[str, Any]]
    decision: dict[str, Any]
    outcome: Outcome
    integrity: StatmechSelectionIntegrity
