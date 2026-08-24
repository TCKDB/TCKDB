"""Typed public scientific-query responses derived from hosted OpenAPI.

The client deliberately keeps wire values as dictionaries.  These
``TypedDict`` models add static guidance without introducing a runtime schema
dependency or changing the objects returned by existing methods.
"""

from __future__ import annotations

from typing import Any, Generic, Literal, NotRequired, Required, TypeAlias, TypeVar, TypedDict

JSONDict: TypeAlias = dict[str, Any]


class ReproducibilityAssessmentSummary(TypedDict):
    """Compact immutable assessment identity and freshness state."""

    state: Required[str]
    assessment_ref: str | None
    rubric: str | None
    rubric_version: str | None
    grade: str | None
    assessed_at: str | None


class PublicAssessmentSummary(TypedDict):
    deterministic_trust: Required[JSONDict]
    reproducibility: Required[ReproducibilityAssessmentSummary]


class Pagination(TypedDict):
    offset: int
    limit: int
    returned: int
    total: int
    post_collapse_total: NotRequired[int]


class ScientificRequestEcho(TypedDict, total=False):
    filter: JSONDict
    sort: str
    collapse: str
    ranking: str
    include: list[str]


class ReviewStatusSummary(TypedDict, total=False):
    approved: int
    under_review: int
    not_reviewed: int
    deprecated: int
    rejected: int
    total: int


class ErrorEnvelope(TypedDict):
    code: str
    detail: object
    context: dict[str, Any]


RecordT = TypeVar("RecordT")


class ScientificSearchResponse(TypedDict, Generic[RecordT]):
    request: ScientificRequestEcho
    review_summary: ReviewStatusSummary
    records: list[RecordT]
    pagination: Pagination


class ScientificDetailResponse(TypedDict, Generic[RecordT]):
    """One record, wrapped in the same envelope the searches return.

    The detail reads deliberately echo ``request`` and ``review_summary``
    too, so a caller that resolved a ref can read the answering profile
    without a second round trip.
    """

    request: ScientificRequestEcho
    review_summary: ReviewStatusSummary
    record: RecordT


class SpeciesRecord(TypedDict, total=False):
    species_ref: Required[str]
    canonical_smiles: Required[str]
    inchi_key: Required[str]
    charge: Required[int]
    multiplicity: Required[int]
    formula: str | None
    entries: list[JSONDict]
    species_id: int


class ReactionRecord(TypedDict, total=False):
    reaction_ref: Required[str]
    reaction_entry_ref: Required[str]
    equation: Required[str]
    matched_direction: Required[str]
    reversible: Required[bool]
    review: Required[JSONDict]
    reactants: Required[list[JSONDict]]
    products: Required[list[JSONDict]]
    availability: Required[JSONDict]
    family: str | None
    reaction_id: int
    reaction_entry_id: int


class SupersessionNotice(TypedDict):
    """Correction notice on a scientific product record that was replaced.

    Present (non-``None``) only when the record has been superseded. TCKDB
    never rewrites an accepted record, so an old citation keeps resolving --
    this block is what tells a reader the number they just fetched is no
    longer the current one.

    ``superseded_by``  public ref of the immediate successor.
    ``current``        public ref of the head of the chain -- what to follow.
                       Equal to ``superseded_by`` for a single correction;
                       different once a record has been corrected twice.
    ``reason``         why the immediate replacement was recorded.
    ``superseded_at``  when that edge was recorded (ISO 8601).
    ``chain_length``   recorded edges between this record and ``current``.
    """

    superseded_by: Required[str]
    current: Required[str]
    reason: Required[str]
    superseded_at: Required[str]
    chain_length: Required[int]


class ThermoDetailRecord(TypedDict, total=False):
    """One thermo wire block, from the subresource or nested in a search row.

    The same block on both surfaces, which is why the search row below
    points at this type rather than at an opaque dict.

    **``trust`` may be absent, and that is not the same as ``null``.** It
    appears only when the request carried ``include=trust``; a *present*
    ``null`` would mean the rubric produced nothing for the record. Under
    ``total=False`` every optional key here is ``NotRequired``, so a type
    checker will now flag ``record["thermo"]["trust"]`` on a request that
    did not ask for it -- which is the point of typing the block, since
    this and its kinetics twin were the only sections a consumer could have
    been subscripting with no type error at all.

    ``include=all`` does **not** expand to ``trust`` on any search or detail
    surface: the evidence graph behind it is large and has to be asked for
    by name.
    """

    thermo_ref: Required[str]
    scientific_origin: Required[str]
    model_kind: Required[str]
    review: Required[JSONDict]
    evidence_completeness: Required[JSONDict]
    provenance: Required[JSONDict]
    supersession: SupersessionNotice | None
    thermo_id: int
    h298_kj_mol: float | None
    s298_j_mol_k: float | None
    h298_uncertainty_kj_mol: float | None
    s298_uncertainty_j_mol_k: float | None
    # Model blocks. Each is ``null`` when this record does not carry that
    # representation -- a fact about the chemistry, not about the request,
    # so these keys are on the wire whether or not anything was included.
    nasa: JSONDict | None
    nasa9: list[JSONDict] | None
    wilhoit: JSONDict | None
    points: list[JSONDict] | None
    group_additivity: JSONDict | None
    temperature_coverage: JSONDict | None
    trust: JSONDict | None
    assessments: PublicAssessmentSummary | None


class ThermoSearchRecord(TypedDict):
    """One composed thermo-search row with resolved species context."""

    species: JSONDict
    thermo: ThermoDetailRecord


class KineticsDetailRecord(TypedDict, total=False):
    """One kinetics wire block, from the subresource or nested in a search row.

    See :class:`ThermoDetailRecord` on ``trust`` -- absent unless requested,
    never reached by ``include=all``, and ``null`` only when the rubric
    genuinely produced nothing.
    """

    kinetics_ref: Required[str]
    scientific_origin: Required[str]
    model_kind: Required[str]
    review: Required[JSONDict]
    parameters: Required[JSONDict]
    uncertainty: Required[JSONDict]
    evidence_completeness: Required[JSONDict]
    provenance: Required[JSONDict]
    supersession: SupersessionNotice | None
    kinetics_id: int
    direction: str | None
    tunneling_model: str | None
    is_third_body: bool
    pressure_context: JSONDict | None
    pressure_bar: float | None
    pressure_coverage: JSONDict | None
    reaction_path_degeneracy: JSONDict | None
    interpretation_assignments: list[JSONDict] | None
    tunneling_application: JSONDict | None
    # Model blocks, ``null`` for a record of another kind. Facts about the
    # fit, not about the request.
    multi_arrhenius: list[JSONDict] | None
    plog_entries: list[JSONDict] | None
    chebyshev: JSONDict | None
    falloff: JSONDict | None
    third_body_efficiencies: list[JSONDict] | None
    temperature_coverage: JSONDict | None
    trust: JSONDict | None
    assessments: PublicAssessmentSummary | None


class KineticsSearchRecord(TypedDict):
    """One composed kinetics-search row with resolved reaction context."""

    reaction: JSONDict
    kinetics: KineticsDetailRecord


# Backward-compatible names for the composed search-row shapes published in
# tckdb-client 0.27.x. Detail methods now use explicit flat-record types.
ThermoRecord: TypeAlias = ThermoSearchRecord
KineticsRecord: TypeAlias = KineticsSearchRecord


class SpeciesCalculationRecord(TypedDict, total=False):
    species: Required[JSONDict]
    calculation: Required[JSONDict]
    geometry: Required[JSONDict]
    validation: Required[JSONDict]
    provenance: Required[JSONDict]
    energy: JSONDict | None
    level_of_theory: JSONDict | None
    software_release: JSONDict | None
    workflow_tool_release: JSONDict | None
    conformer: JSONDict | None


class CalculationAvailableSections(TypedDict):
    """Presence flags returned with scientific calculation records."""

    has_results: bool
    has_dependencies: bool
    has_parameters: bool
    has_constraints: bool
    has_artifacts: bool
    has_input_geometries: bool
    has_output_geometries: bool
    has_geometry_validation: bool
    has_scf_stability: bool
    has_wavefunction_diagnostic: bool
    has_spin_diagnostic: bool
    has_freq_modes: bool
    has_scan: bool
    has_irc: bool
    has_path_search: bool
    has_execution_environment: bool
    #: Whether an applied energy correction cites this calculation as the
    #: source of the energy it corrects. The energies the record serves are
    #: uncorrected, so this is how a caller learns an addend exists without
    #: asking for ``include=energy_corrections``.
    has_energy_corrections: bool


class ExecutionEnvironmentContentReference(TypedDict):
    """One content-addressed item in the public environment wire contract."""

    locator: str
    digest: str


class ExecutionEnvironmentClosureEntry(ExecutionEnvironmentContentReference):
    role: Literal["runtime", "executable", "lockfile", "module_closure", "dependency_manifest"]


class ExecutionEnvironmentExecutable(TypedDict):
    """The executable's location, and its digest only when the uploader had one."""

    locator: str
    digest: NotRequired[str | None]


class ExecutionEnvironmentModuleDescription(TypedDict):
    name: str
    version: str


class DescribedExecutionRuntime(TypedDict):
    """A named-but-unpinned environment: the ordinary shared-cluster case."""

    runtime_kind: Literal["described"]
    description: str
    modules: NotRequired[list[ExecutionEnvironmentModuleDescription]]


class ContainerExecutionRuntime(TypedDict):
    runtime_kind: Literal["container"]
    image: str


class CondaExecutionRuntime(TypedDict):
    runtime_kind: Literal["conda"]
    lockfile: ExecutionEnvironmentContentReference


class HPCModuleExecutionRuntime(TypedDict):
    runtime_kind: Literal["hpc_module"]
    modules: list[ExecutionEnvironmentModuleDescription]
    resolved_environment_digest: str
    dependency_manifest_digest: str


ExecutionEnvironmentRuntime: TypeAlias = (
    DescribedExecutionRuntime | ContainerExecutionRuntime | CondaExecutionRuntime | HPCModuleExecutionRuntime
)


class ScientificSoftwareReleaseIdentity(TypedDict):
    """Portable scientific-software identity bound into a manifest."""

    name: str
    version: NotRequired[str | None]
    revision: NotRequired[str | None]
    build: NotRequired[str | None]


class WorkflowToolReleaseIdentity(TypedDict):
    """Portable optional workflow-tool identity bound into a manifest."""

    name: str
    version: NotRequired[str | None]
    git_commit: NotRequired[str | None]


class ExecutionEnvironmentManifestRecord(TypedDict):
    """Canonical nested public execution-environment response."""

    schema_version: Required[str]
    runtime: Required[ExecutionEnvironmentRuntime]
    software_release: Required[ScientificSoftwareReleaseIdentity]
    workflow_tool_release: NotRequired[WorkflowToolReleaseIdentity | None]
    executable: Required[ExecutionEnvironmentExecutable]
    #: Empty for a ``described`` runtime; populated for the pinned tiers.
    closure: Required[list[ExecutionEnvironmentClosureEntry]]
    environment_ref: Required[str]


class AppliedEnergyCorrectionComponent(TypedDict):
    """One term of an applied energy correction's breakdown."""

    component_kind: str
    key: str
    multiplicity: int
    parameter_value: float
    contribution_value: float


class AppliedEnergyCorrection(TypedDict, total=False):
    """One applied energy correction, as served beside a calculation.

    The energy the calculation record serves is the **uncorrected** one,
    so ``applied_value`` is an addend the consumer applies, not an
    adjustment already folded in.

    ``applied_value`` / ``applied_value_unit`` are the stored pair — the
    unit genuinely varies by scheme (kcal/mol for a Petersson BAC,
    hartree for an atom-energy total), so it is never assumed.
    ``applied_value_hartree`` is the same quantity in the unit of
    ``electronic_energy_hartree``, so the two can be summed directly; it
    is ``None``, never ``0.0``, when the server could not convert.
    """

    applied_energy_correction_id: NotRequired[int | None]
    application_role: Required[str]
    applied_value: Required[float]
    applied_value_unit: Required[str]
    applied_value_hartree: float | None
    temperature_k: float | None
    note: str | None
    target_record_type: Required[str]
    target_record_ref: str | None
    target_record_id: NotRequired[int | None]
    target_endpoint: str | None
    energy_correction_scheme_ref: str | None
    energy_correction_scheme_name: str | None
    energy_correction_scheme_kind: str | None
    frequency_scale_factor_ref: str | None
    component_count: Required[int]
    components_truncated: bool
    components: list[AppliedEnergyCorrectionComponent]


class CalculationRecord(TypedDict, total=False):
    """Scientific calculation detail/search record."""

    calculation: Required[JSONDict]
    owner: Required[JSONDict]
    provenance: Required[JSONDict]
    available_sections: Required[CalculationAvailableSections]
    execution_environment: ExecutionEnvironmentManifestRecord | None
    #: ``include=energy_corrections``. Absent when the caller did not ask;
    #: an empty list when they did and the calculation has none.
    energy_corrections: list[AppliedEnergyCorrection] | None


class CalculationDetailResponse(TypedDict):
    request: Required[ScientificRequestEcho]
    review_summary: Required[ReviewStatusSummary]
    record: Required[CalculationRecord]


class NetworkStateCompositionParticipant(TypedDict):
    #: ``canonical_smiles`` is species-level and is shared by every entry of
    #: one species, so it does not identify a participant on its own.
    #: ``species_entry_label`` is the discriminator that tells two entries of
    #: one species apart in prose; it is ``None`` when there is nothing to
    #: disambiguate. ``species_entry_ref`` remains the machine identity.
    species_entry_ref: str
    species_ref: str
    canonical_smiles: str
    species_entry_label: str | None
    stoichiometry: int


class NetworkStateComposition(TypedDict):
    #: ``state_label`` is the server's rendering of ``participants`` --
    #: ``"N=N (E) + [H][H]"`` -- and is what to print. Building a label from
    #: ``canonical_smiles`` instead collapses two wells of one species onto
    #: one string. It ends in ``" + ..."`` when ``participants_truncated``.
    participants: list[NetworkStateCompositionParticipant]
    participant_count_total: int
    participants_truncated: bool
    state_label: str


class NetworkStateSummary(TypedDict, total=False):
    composition_hash: Required[str]
    kind: Required[str]
    participant_count: Required[int]
    composition: Required[NetworkStateComposition]
    label: str | None
    network_state_id: int


class NetworkRecord(TypedDict, total=False):
    network: Required[JSONDict]
    evidence_summary: Required[JSONDict]
    available_sections: Required[JSONDict]
    species: list[JSONDict] | None
    reactions: list[JSONDict] | None
    states: list[NetworkStateSummary] | None
    channels: list[JSONDict] | None
    solves: list[JSONDict] | None
    kinetics: list[JSONDict] | None
    source_calculations: list[JSONDict] | None
    review_history: list[JSONDict] | None
    software_release: JSONDict | None
    workflow_tool_release: JSONDict | None
    literature: JSONDict | None


class NetworkSolveRecord(TypedDict, total=False):
    """One scientific network-solve record from search or detail reads."""

    network_solve: Required[JSONDict]
    network: Required[JSONDict]
    evidence_summary: Required[JSONDict]
    available_sections: Required[JSONDict]
    bath_gas: list[JSONDict] | None
    energy_transfer: list[JSONDict] | None
    source_calculations: list[JSONDict] | None
    kinetics: list[JSONDict] | None
    review_history: list[JSONDict] | None
    software_release: JSONDict | None
    workflow_tool_release: JSONDict | None
    literature: JSONDict | None


class NetworkKineticsRecord(TypedDict, total=False):
    network_kinetics: Required[JSONDict]
    network: Required[JSONDict]
    network_solve: Required[JSONDict]
    network_channel: Required[JSONDict]
    evidence_summary: Required[JSONDict]
    available_sections: Required[JSONDict]
    coefficients: JSONDict | None
    plog: list[JSONDict] | None
    plog_entry_count_total: int | None
    plog_entries_truncated: bool | None
    points: list[JSONDict] | None
    point_count_total: int | None
    points_truncated: bool | None
    source_calculations: list[JSONDict] | None
    review_history: list[JSONDict] | None


class StatmechRecord(TypedDict, total=False):
    statmech: Required[JSONDict]
    species: Required[JSONDict]
    evidence_summary: Required[JSONDict]
    available_sections: Required[JSONDict]
    frequency_scale_factor: JSONDict | None
    source_calculations: list[JSONDict] | None
    conformers: list[JSONDict] | None
    torsions: list[JSONDict] | None
    electronic_levels: list[JSONDict] | None
    frequencies: JSONDict | None
    review_history: list[JSONDict] | None
    software_release: JSONDict | None
    workflow_tool_release: JSONDict | None
    literature: JSONDict | None
    trust: JSONDict | None
    assessments: PublicAssessmentSummary | None


class TransportRecord(TypedDict, total=False):
    transport: Required[JSONDict]
    species: Required[JSONDict]
    evidence_summary: Required[JSONDict]
    available_sections: Required[JSONDict]
    source_calculations: list[JSONDict] | None
    review_history: list[JSONDict] | None
    software_release: JSONDict | None
    workflow_tool_release: JSONDict | None
    literature: JSONDict | None
    trust: JSONDict | None
    assessments: PublicAssessmentSummary | None


class ArtifactRecord(TypedDict, total=False):
    artifact: Required[JSONDict]
    calculation: Required[JSONDict]
    available_sections: Required[JSONDict]
    owner: JSONDict | None


class SpeciesStructureRecord(TypedDict, total=False):
    """One RDKit structure-search hit, flat rather than nested.

    Unlike the identity searches this row carries ``match``: which query
    matched and, for similarity mode, how well. Without it a ranked result
    set is indistinguishable from an unranked one.
    """

    species_ref: Required[str]
    species_entry_ref: Required[str]
    smiles: Required[str]
    inchi_key: Required[str]
    charge: Required[int]
    multiplicity: Required[int]
    species_entry_kind: Required[str]
    electronic_state_kind: Required[str]
    match: Required[JSONDict]
    review: Required[JSONDict]
    endpoint: Required[str]
    species_id: int | None
    species_entry_id: int | None


class ConformerRecord(TypedDict, total=False):
    """One conformer *group* — the unit both the search and detail return."""

    conformer_group: Required[JSONDict]
    species: Required[JSONDict]
    observations_summary: Required[JSONDict]
    evidence_summary: Required[JSONDict]
    available_sections: Required[JSONDict]
    selection_summary: list[JSONDict] | None
    observations: list[JSONDict] | None
    selections: list[JSONDict] | None
    calculations: list[JSONDict] | None
    geometries: list[JSONDict] | None
    review_history: list[JSONDict] | None


class ConformerObservationRecord(TypedDict, total=False):
    """One observation, with the group and species it belongs to attached."""

    conformer_observation: Required[JSONDict]
    conformer_group: Required[JSONDict]
    species: Required[JSONDict]
    evidence_summary: Required[JSONDict]
    available_sections: Required[JSONDict]
    assignment_scheme: JSONDict | None
    selections: list[JSONDict] | None
    calculations: list[JSONDict] | None
    geometries: list[JSONDict] | None
    review_history: list[JSONDict] | None


class TransitionStateEntryRecord(TypedDict, total=False):
    """One TS *entry* — what the transition-state search returns.

    An entry is a specific electronic/conformational realisation of the
    transition state; the search is entry-grained because that is the
    level at which calculations and validation attach.
    """

    transition_state_entry: Required[JSONDict]
    transition_state: Required[JSONDict]
    reaction: Required[JSONDict]
    evidence_summary: Required[JSONDict]
    validation: Required[JSONDict]
    available_sections: Required[JSONDict]
    calculations: list[JSONDict] | None
    geometries: list[JSONDict] | None
    review_history: list[JSONDict] | None
    validation_evidence: list[JSONDict] | None
    trust: JSONDict | None


class TransitionStateRecord(TypedDict, total=False):
    """One transition-state identity, with its entries summarised."""

    transition_state: Required[JSONDict]
    reaction: Required[JSONDict]
    entries_summary: Required[JSONDict]
    evidence_summary: Required[JSONDict]
    available_sections: Required[JSONDict]
    entries: list[JSONDict] | None
    calculations: list[JSONDict] | None
    geometries: list[JSONDict] | None
    review_history: list[JSONDict] | None


class EnergyCorrectionSchemeRecord(TypedDict, total=False):
    energy_correction_scheme: Required[JSONDict]
    evidence_summary: Required[JSONDict]
    available_sections: Required[JSONDict]
    level_of_theory: JSONDict | None
    literature: JSONDict | None
    corrections: list[JSONDict] | None
    used_by: list[JSONDict] | None


class FrequencyScaleFactorRecord(TypedDict, total=False):
    frequency_scale_factor: Required[JSONDict]
    evidence_summary: Required[JSONDict]
    available_sections: Required[JSONDict]
    level_of_theory: JSONDict | None
    software_release: JSONDict | None
    workflow_tool_release: JSONDict | None
    literature: JSONDict | None
    used_by: list[JSONDict] | None


class LiteratureRecord(TypedDict, total=False):
    literature: Required[JSONDict]
    identifiers: Required[JSONDict]
    record_counts: Required[JSONDict]
    available_sections: Required[JSONDict]
    authors: list[JSONDict] | None


class LiteratureLinkedRecord(TypedDict, total=False):
    """One scientific record that cites, or was derived from, a reference.

    ``endpoint`` is the read path for the linked record, so a caller can
    follow the citation without knowing how each record type is addressed.
    """

    record_type: Required[str]
    record_ref: Required[str]
    endpoint: Required[str]
    record_id: int | None
    relationship_kind: str
    role: str | None
    title: str | None
    label: str | None
    species_ref: str | None
    species_entry_ref: str | None
    reaction_ref: str | None
    reaction_entry_ref: str | None
    calculation_ref: str | None
    network_ref: str | None
    network_solve_ref: str | None
    # Whether a linked network solve derived its k(T,P) here ("computed") or
    # transcribed them from this very publication ("reported") -- see ADR 0010.
    # Present only for ``record_type == "network_solve"``.
    #
    # Optional here, unlike the server, where the field is required and has no
    # default. The asymmetry is deliberate and follows the same principle in
    # both directions: never claim what you do not know. A server always knows
    # a solve's origin, so defaulting there would fabricate a provenance claim.
    # A client can be talking to an older deployment that does not send the
    # field at all, and for that client "unknown" is simply true.
    network_solve_kind: str | None
    review: JSONDict | None
    created_at: str | None


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
#
# Flat rows, one scalar per column: the analytics surface exists to be
# turned into a dataframe, so nothing here nests. Every row carries its own
# ``review_status`` because a dataset built from these must be able to state
# the curation floor it was drawn from.


class AnalyticsRequestEcho(TypedDict, total=False):
    filter: Required[JSONDict]
    sort: Required[str]
    pagination_mode: Required[str]
    include: list[str]
    profile: str
    profile_recommendation: str
    profile_release_ref: str | None


class WatermarkEcho(TypedDict, total=False):
    """When the answering snapshot was taken, and from which release."""

    taken_at: Required[str]
    release_ref: str | None


class KineticsAnalyticsRecord(TypedDict, total=False):
    kinetics_ref: Required[str]
    reaction_entry_ref: Required[str]
    scientific_origin: Required[str]
    model_kind: Required[str]
    is_third_body: Required[bool]
    degeneracy_convention: Required[str]
    has_literature: Required[bool]
    has_workflow_tool: Required[bool]
    has_transition_state_provenance: Required[bool]
    has_statmech_provenance: Required[bool]
    review_status: Required[str]
    kinetics_id: int
    reaction_entry_id: int
    direction: str | None
    a: float | None
    a_units: str | None
    n: float | None
    ea_kj_mol: float | None
    a_uncertainty: float | None
    a_uncertainty_kind: str | None
    n_uncertainty: float | None
    ea_uncertainty_kj_mol: float | None
    tmin_k: float | None
    tmax_k: float | None
    degeneracy: float | None
    tunneling_model: str | None
    pressure_context: str | None
    pressure_bar: float | None
    created_at: str | None


class ThermoAnalyticsRecord(TypedDict, total=False):
    thermo_ref: Required[str]
    species_entry_ref: Required[str]
    scientific_origin: Required[str]
    has_literature: Required[bool]
    has_workflow_tool: Required[bool]
    has_statmech_provenance: Required[bool]
    review_status: Required[str]
    thermo_id: int
    species_entry_id: int
    model_kind: str | None
    phase: str | None
    reference_pressure_bar: float | None
    h298_kj_mol: float | None
    s298_j_mol_k: float | None
    enthalpy_formation_0k_kj_mol: float | None
    h298_uncertainty_kj_mol: float | None
    s298_uncertainty_j_mol_k: float | None
    enthalpy_formation_0k_uncertainty_kj_mol: float | None
    tmin_k: float | None
    tmax_k: float | None
    created_at: str | None


class StatmechAnalyticsRecord(TypedDict, total=False):
    statmech_ref: Required[str]
    scientific_origin: Required[str]
    has_frequency_scale_factor: Required[bool]
    torsion_count: Required[int]
    electronic_level_count: Required[int]
    review_status: Required[str]
    statmech_id: int
    #: Exactly one owner side is populated: statmech hangs off a species
    #: entry or off a transition-state entry, never both.
    species_entry_id: int | None
    species_entry_ref: str | None
    transition_state_entry_id: int | None
    transition_state_entry_ref: str | None
    external_symmetry: int | None
    is_linear: bool | None
    point_group: str | None
    statmech_treatment: str | None
    rigid_rotor_kind: str | None
    optical_isomers: int | None
    rotational_constant_a_cm1: float | None
    rotational_constant_b_cm1: float | None
    rotational_constant_c_cm1: float | None
    created_at: str | None


class CalculationAnalyticsRecord(TypedDict, total=False):
    calculation_ref: Required[str]
    calculation_type: Required[str]
    quality: Required[str]
    review_status: Required[str]
    calculation_id: int
    electronic_energy_hartree: float | None
    final_energy_hartree: float | None
    converged: bool | None
    zpe_hartree: float | None
    n_imag: int | None
    imag_freq_cm1: float | None
    t1_diagnostic: float | None
    d1_diagnostic: float | None
    s_squared: float | None
    s_squared_expected: float | None
    method: str | None
    basis: str | None
    level_of_theory_ref: str | None
    software: str | None
    software_version: str | None
    created_at: str | None


#: Any one of the four analytics row shapes.
AnalyticsRecord: TypeAlias = (
    KineticsAnalyticsRecord
    | ThermoAnalyticsRecord
    | StatmechAnalyticsRecord
    | CalculationAnalyticsRecord
)


class AnalyticsResponse(TypedDict, Generic[RecordT], total=False):
    """A search envelope that can also be traversed by keyset.

    ``next_cursor`` and ``watermark`` are what distinguish this from
    :class:`ScientificSearchResponse`: offset paging over a live corpus can
    skip or duplicate rows, so a reproducible dataset build follows the
    cursor and records the watermark it was taken at.
    """

    request: Required[AnalyticsRequestEcho]
    review_summary: Required[ReviewStatusSummary]
    records: Required[list[RecordT]]
    pagination: Required[Pagination]
    next_cursor: str | None
    watermark: WatermarkEcho | None


SpeciesSearchResponse: TypeAlias = ScientificSearchResponse[SpeciesRecord]
ReactionSearchResponse: TypeAlias = ScientificSearchResponse[ReactionRecord]
ThermoSearchResponse: TypeAlias = ScientificSearchResponse[ThermoSearchRecord]


class SpeciesThermoResponse(ScientificSearchResponse[ThermoDetailRecord]):
    species_entry_ref: str
    species_entry_id: NotRequired[int]


KineticsSearchResponse: TypeAlias = ScientificSearchResponse[KineticsSearchRecord]


class ReactionKineticsResponse(ScientificSearchResponse[KineticsDetailRecord]):
    reaction_entry_ref: str
    reaction_entry_id: NotRequired[int]


SpeciesCalculationsSearchResponse: TypeAlias = ScientificSearchResponse[
    SpeciesCalculationRecord
]
CalculationSearchResponse: TypeAlias = ScientificSearchResponse[CalculationRecord]
NetworkSearchResponse: TypeAlias = ScientificSearchResponse[NetworkRecord]
NetworkSolveSearchResponse: TypeAlias = ScientificSearchResponse[NetworkSolveRecord]
NetworkKineticsSearchResponse: TypeAlias = ScientificSearchResponse[
    NetworkKineticsRecord
]
StatmechSearchResponse: TypeAlias = ScientificSearchResponse[StatmechRecord]
TransportSearchResponse: TypeAlias = ScientificSearchResponse[TransportRecord]
ArtifactSearchResponse: TypeAlias = ScientificSearchResponse[ArtifactRecord]
SpeciesStructureSearchResponse: TypeAlias = ScientificSearchResponse[
    SpeciesStructureRecord
]
ConformerSearchResponse: TypeAlias = ScientificSearchResponse[ConformerRecord]
ConformerGroupDetailResponse: TypeAlias = ScientificDetailResponse[ConformerRecord]
ConformerObservationDetailResponse: TypeAlias = ScientificDetailResponse[
    ConformerObservationRecord
]
TransitionStateSearchResponse: TypeAlias = ScientificSearchResponse[
    TransitionStateEntryRecord
]
TransitionStateDetailResponse: TypeAlias = ScientificDetailResponse[
    TransitionStateRecord
]
TransitionStateEntryDetailResponse: TypeAlias = ScientificDetailResponse[
    TransitionStateEntryRecord
]
EnergyCorrectionSchemeSearchResponse: TypeAlias = ScientificSearchResponse[
    EnergyCorrectionSchemeRecord
]
EnergyCorrectionSchemeDetailResponse: TypeAlias = ScientificDetailResponse[
    EnergyCorrectionSchemeRecord
]
FrequencyScaleFactorSearchResponse: TypeAlias = ScientificSearchResponse[
    FrequencyScaleFactorRecord
]
FrequencyScaleFactorDetailResponse: TypeAlias = ScientificDetailResponse[
    FrequencyScaleFactorRecord
]
LiteratureDetailResponse: TypeAlias = ScientificDetailResponse[LiteratureRecord]
LiteratureRecordsResponse: TypeAlias = ScientificSearchResponse[
    LiteratureLinkedRecord
]
KineticsAnalyticsResponse: TypeAlias = AnalyticsResponse[KineticsAnalyticsRecord]
ThermoAnalyticsResponse: TypeAlias = AnalyticsResponse[ThermoAnalyticsRecord]
StatmechAnalyticsResponse: TypeAlias = AnalyticsResponse[StatmechAnalyticsRecord]
CalculationAnalyticsResponse: TypeAlias = AnalyticsResponse[
    CalculationAnalyticsRecord
]


__all__ = [
    "AnalyticsRecord",
    "AnalyticsRequestEcho",
    "AnalyticsResponse",
    "AppliedEnergyCorrection",
    "AppliedEnergyCorrectionComponent",
    "ArtifactRecord",
    "ArtifactSearchResponse",
    "CalculationAnalyticsRecord",
    "CalculationAnalyticsResponse",
    "CalculationAvailableSections",
    "CalculationDetailResponse",
    "CalculationRecord",
    "CalculationSearchResponse",
    "CondaExecutionRuntime",
    "ConformerGroupDetailResponse",
    "ConformerObservationDetailResponse",
    "ConformerObservationRecord",
    "ConformerRecord",
    "ConformerSearchResponse",
    "ContainerExecutionRuntime",
    "DescribedExecutionRuntime",
    "EnergyCorrectionSchemeDetailResponse",
    "EnergyCorrectionSchemeRecord",
    "EnergyCorrectionSchemeSearchResponse",
    "ErrorEnvelope",
    "ExecutionEnvironmentClosureEntry",
    "ExecutionEnvironmentContentReference",
    "ExecutionEnvironmentExecutable",
    "ExecutionEnvironmentManifestRecord",
    "ExecutionEnvironmentModuleDescription",
    "ExecutionEnvironmentRuntime",
    "FrequencyScaleFactorDetailResponse",
    "FrequencyScaleFactorRecord",
    "FrequencyScaleFactorSearchResponse",
    "HPCModuleExecutionRuntime",
    "JSONDict",
    "KineticsAnalyticsRecord",
    "KineticsAnalyticsResponse",
    "KineticsDetailRecord",
    "KineticsRecord",
    "KineticsSearchRecord",
    "KineticsSearchResponse",
    "LiteratureDetailResponse",
    "LiteratureLinkedRecord",
    "LiteratureRecord",
    "LiteratureRecordsResponse",
    "NetworkKineticsRecord",
    "NetworkKineticsSearchResponse",
    "NetworkRecord",
    "NetworkSearchResponse",
    "NetworkSolveRecord",
    "NetworkSolveSearchResponse",
    "NetworkStateComposition",
    "NetworkStateCompositionParticipant",
    "NetworkStateSummary",
    "Pagination",
    "PublicAssessmentSummary",
    "ReactionKineticsResponse",
    "ReactionRecord",
    "ReactionSearchResponse",
    "ReproducibilityAssessmentSummary",
    "ReviewStatusSummary",
    "ScientificDetailResponse",
    "ScientificRequestEcho",
    "ScientificSearchResponse",
    "ScientificSoftwareReleaseIdentity",
    "SpeciesCalculationRecord",
    "SpeciesCalculationsSearchResponse",
    "SpeciesRecord",
    "SpeciesSearchResponse",
    "SpeciesStructureRecord",
    "SpeciesStructureSearchResponse",
    "SpeciesThermoResponse",
    "StatmechAnalyticsRecord",
    "StatmechAnalyticsResponse",
    "StatmechRecord",
    "StatmechSearchResponse",
    "SupersessionNotice",
    "ThermoAnalyticsRecord",
    "ThermoAnalyticsResponse",
    "ThermoDetailRecord",
    "ThermoRecord",
    "ThermoSearchRecord",
    "ThermoSearchResponse",
    "TransitionStateDetailResponse",
    "TransitionStateEntryDetailResponse",
    "TransitionStateEntryRecord",
    "TransitionStateRecord",
    "TransitionStateSearchResponse",
    "TransportRecord",
    "TransportSearchResponse",
    "WatermarkEcho",
    "WorkflowToolReleaseIdentity",
]
