"""Typed public scientific-query responses derived from hosted OpenAPI.

The client deliberately keeps wire values as dictionaries.  These
``TypedDict`` models add static guidance without introducing a runtime schema
dependency or changing the objects returned by existing methods.
"""

from __future__ import annotations

from typing import Any, Generic, Literal, NotRequired, Required, TypeAlias, TypeVar, TypedDict

JSONDict: TypeAlias = dict[str, Any]


class MadeUnder(TypedDict):
    """Which rubric judged an assessment, at which version, and whether that is still the current one.

    ``state`` is ``current`` (the version in force, and the inputs were compared with today's), ``inputs_unchecked``
    (the version in force, but this route does not compare the inputs: a list or search read never says ``current``),
    ``stale`` (another version, or the same version over inputs that have changed) or ``unassessed`` (nothing to name).
    ``assessed_version`` 0 means "stamped before the 2026-10 versioning reset" and is never current.
    ``stale_reason`` is ``rubric_version``, ``prompt_version`` (a stored machine review) or ``inputs_changed`` when
    ``state`` is ``stale``. ``inputs_checked`` is true only on a route that compares the inputs, ``None`` when
    there is nothing to compare. ``inputs_unchecked_reason`` is ``curated_profile`` when a curated read withholds
    the comparison (a hidden record's edit must not move a visible verdict).
    """

    state: Literal["current", "stale", "unassessed", "inputs_unchecked"]
    rubric: str | None
    assessed_version: int | None
    current_rubric: str | None
    current_version: int | None
    stale_reason: Literal["rubric_version", "prompt_version", "inputs_changed"] | None
    inputs_checked: bool | None
    inputs_unchecked_reason: Literal["curated_profile"] | None


class ReproducibilityAssessmentSummary(TypedDict):
    """The latest stored reproducibility assessment: its identity and what it was made under.

    ``state`` mirrors ``made_under.state``. Thermo and kinetics lists compare inputs only when asked
    (``check_inputs=True``, at most 10 records); statmech and transport by-ref reads always do.
    """

    state: Required[Literal["current", "stale", "unassessed", "inputs_unchecked"]]
    made_under: MadeUnder | None
    assessment_ref: str | None
    rubric: str | None
    rubric_version: int | None
    grade: str | None
    assessed_at: str | None


class DeterministicTrustSummary(TypedDict):
    """The code-defined evidence assessment, evaluated at read time (so ``made_under.state`` is always ``current``)."""

    rubric: str
    rubric_version: int
    grade: str
    hard_fail: str | None
    made_under: MadeUnder | None


class PublicAssessmentSummary(TypedDict):
    deterministic_trust: DeterministicTrustSummary
    reproducibility: ReproducibilityAssessmentSummary


class Pagination(TypedDict):
    offset: int
    limit: int
    returned: int
    total: int


class OrderKeyEcho(TypedDict):
    """One key of a display order: its name, direction and plain meaning."""

    key: str
    direction: Literal["asc", "desc"]
    meaning: str


class OrderEcho(TypedDict):
    """The display order a browse or list response applied. It is display order, never a scientific preference."""

    kind: Literal["display_order"]
    keys: list[OrderKeyEcho]
    statement: str
    #: Set when the request itself asked for this order (``ranking=lowest_energy``, ``mode=similarity``); ``None``
    #: for the default display order.
    requested_by: NotRequired[str | None]


class ScientificRequestEcho(TypedDict, total=False):
    filter: JSONDict
    sort: OrderEcho
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
    """One species, with its resolved entries nested under ``entries``.

    ``canonical_smiles``, ``inchi_key``, ``charge`` and ``multiplicity`` are
    the *graph* identity and are shared by every entry beneath it. What
    separates one entry from another lives on the entry: ``stereo_label``,
    ``electronic_state_kind``, ``electronic_state_label``, ``term_symbol``
    and ``isotope_key``, plus the one-string ``species_entry_label`` derived
    from them. Never pick an entry by position or by the species-level
    fields -- ``N=N`` carries a ``Z`` entry (cis-diazene) and an ``E`` entry
    (trans-diazene) with different thermochemistry, and they agree on every
    species-level field there is.

    ``stereo_kind`` is the species' own: ``achiral`` means a null
    ``stereo_label`` on an entry has nothing to label, while ``ez_isomer``
    or ``enantiomer`` means stereoisomers exist and this entry has simply
    not been labelled.
    """

    species_ref: Required[str]
    canonical_smiles: Required[str]
    inchi_key: Required[str]
    charge: Required[int]
    multiplicity: Required[int]
    stereo_kind: Required[str]
    formula: str | None
    entries: list[JSONDict]
    species_id: int


class ReactionParticipantRecord(TypedDict, total=False):
    """One reactant/product participant, from ``reactions/search`` or ``/browse``.

    ``formula`` is the RDKit Hill-notation formula derived from ``smiles``
    (``null`` when the SMILES fails to parse); ``stoichiometry`` is the
    graph-identity coefficient ("this equation consumes two of this
    species"), not a property of any one deposit. Same shape as
    ``ReactionFullSpeciesParticipant`` on ``get_reaction_full``'s
    ``species.reactants`` / ``species.products``.
    """

    species_entry_ref: Required[str]
    smiles: Required[str]
    participant_index: Required[int]
    stoichiometry: Required[int]
    species_entry_id: int
    species_entry_label: str | None
    formula: str | None


class ReactionRecord(TypedDict, total=False):
    reaction_ref: Required[str]
    reaction_entry_ref: Required[str]
    equation: Required[str]
    matched_direction: Required[str]
    reversible: Required[bool]
    review: Required[JSONDict]
    reactants: Required[list[ReactionParticipantRecord]]
    products: Required[list[ReactionParticipantRecord]]
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
    #: Deposition time (when the row was created in TCKDB): the second key of the display order.
    created_at: Required[str]
    scientific_origin: Required[str]
    model_kind: Required[str]
    review: Required[JSONDict]
    evidence_completeness: Required[JSONDict]
    provenance: Required[JSONDict]
    supersession: SupersessionNotice | None
    thermo_id: int
    # Declared reference for every enthalpy this record carries, and the
    # standard-state it is referenced to. Plain sibling fields -- not a
    # nested ``reference`` block, which existed only briefly and was
    # reverted (unrelated to and unannounced alongside the enthalpy
    # declaration itself).
    phase: str | None
    reference_pressure_bar: float | None
    enthalpy_reference_kind: str | None
    # What the depositor declared the values describe: ``{"kind":
    # "equilibrium_ensemble" | "single_conformer", "conformer_group_ref": ...}``
    # (the ref is ``null`` for an equilibrium target). ``null`` means none was
    # declared; it is never inferred from a statmech link.
    thermodynamic_target: JSONDict | None
    # The depositor's versioned protocol declaration (recipe, formation
    # reference, thermal approximation, departures, supporting calculations
    # by public ref). ``null`` means none was declared. An attributed claim.
    protocol: JSONDict | None
    # ``true`` when a stored protocol no longer validates: ``protocol`` is then ``null``.
    protocol_unreadable: bool
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
    #: Deposition time (when the row was created in TCKDB): the second key of the display order.
    created_at: Required[str]
    scientific_origin: Required[str]
    model_kind: Required[str]
    review: Required[JSONDict]
    parameters: Required[JSONDict]
    uncertainty: Required[JSONDict]
    evidence_completeness: Required[JSONDict]
    # Geometry/frequency/energy levels of theory derived at read time from
    # this record's resolved TS chain. Always present (never behind an
    # ``include=`` token); all-null with ``energy_source: None`` for a
    # record with no TS chain (experimental/literature kinetics).
    levels: Required[JSONDict]
    provenance: Required[JSONDict]
    supersession: SupersessionNotice | None
    kinetics_id: int
    direction: str | None
    # What the depositor declared (attributed claims, not verified facts): the determination
    # this record is a representation of, what the coefficient is, and how the rate was
    # produced. ``null`` means not stated, never "standalone" or "universally valid". Under ``profile="curated"``
    # ``determination`` is also ``null`` when its target (a transition-state entry or a network channel) is not
    # approved, ``tunneling_application`` is ``null`` when it cites an unapproved entry or calculation, and
    # ``provenance["irc"]`` is ``null`` when its calculation is not approved.
    determination: JSONDict | None
    applicability: JSONDict | None
    protocol: JSONDict | None
    # ``True`` when a stored declaration no longer validates (written outside the upload
    # path): the declaration reads ``null`` and the rest of the record is served.
    declaration_unreadable: bool
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
    #: The two per-result blocks. Each is ``null`` when this kind of
    #: calculation produces no such result -- ``energy`` on a ``freq``
    #: record, ``frequency`` on an ``sp`` or ``opt`` one -- and a present
    #: object with ``null`` fields when the result belongs there and was
    #: never parsed. Facts about the calculation, not about the request.
    energy: JSONDict | None
    frequency: JSONDict | None
    #: Include-gated by ``include=freq_modes``: absent unless asked for,
    #: ``[]`` when asked for and this calculation has no parsed modes.
    freq_modes: list[JSONDict] | None
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

    schema_name: Required[str]
    schema_version: Required[int]
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
    # ``None`` when the read used ``profile="curated"`` and the network this solve belongs to is not approved: the solve
    # is shown, the network is not named (the key is always present).
    network: Required[JSONDict | None]
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
    # ``None`` when the read used ``profile="curated"`` and the network is not approved: the fit is shown (it is
    # reviewed through its solve), the network is not named (the key is always present).
    network: Required[JSONDict | None]
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


class NetworkKineticsEvaluatedPoint(TypedDict):
    """One server-evaluated (T, P) -> k point.

    ``in_range`` is ``False`` whenever the point falls outside the
    fit's own stated validity range on either axis -- the value is
    still computed (extrapolation is well-defined for both supported
    forms) but must never be read as an interpolated value. See
    ``app/chemistry/network_kinetics_eval.py`` in the backend for the
    exact Chebyshev/PLOG formulas -- this client never reimplements
    them, matching the "evaluation happens server-side" design.
    """

    temperature_k: float
    pressure_bar: float
    k: float
    in_range: bool


class NetworkKineticsEvaluateResponse(TypedDict):
    """Response envelope for
    ``GET /scientific/network-kinetics/{ref}/evaluate``.

    Flat, not a ``ScientificSearchResponse`` -- this endpoint evaluates
    one stored fit at a requested (T, P) grid rather than searching a
    corpus, so it carries no ``request``/``review_summary``/
    ``pagination`` envelope.
    """

    network_kinetics_ref: str
    model_kind: str
    k_units: str
    tmin_k: float | None
    tmax_k: float | None
    pmin_bar: float | None
    pmax_bar: float | None
    points: list[NetworkKineticsEvaluatedPoint]


class NetworkKineticsBatchFitEvaluation(TypedDict):
    """One evaluated stored fit within a
    :class:`NetworkKineticsBatchEvaluateResponse`.

    ``channel_key`` + the composition-hash pair are carried so a caller
    can group/label this entry against a channel it already has from a
    network detail call, without a second request. Two entries can share
    the same ``channel_key`` (a Chebyshev and a PLOG fit on one channel
    is the norm on a real network, not an edge case) -- they are never
    collapsed into one value, so a caller must not assume one entry per
    channel.
    """

    network_kinetics_ref: str
    channel_key: str
    channel_kind: str
    source_state_composition_hash: str
    sink_state_composition_hash: str
    network_solve_ref: str
    model_kind: str
    k_units: str
    tmin_k: float | None
    tmax_k: float | None
    pmin_bar: float | None
    pmax_bar: float | None
    points: list[NetworkKineticsEvaluatedPoint]


class NetworkKineticsBatchEvaluateResponse(TypedDict):
    """Response envelope for
    ``POST /scientific/networks/{ref}/kinetics/evaluate``.

    Flat, like :class:`NetworkKineticsEvaluateResponse` -- evaluates
    every stored fit for one network at a shared grid rather than
    searching a corpus, so it carries no ``request``/``review_summary``/
    ``pagination`` envelope. ``fits`` is ``[]`` (not a 404) for a network
    that exists but has no stored kinetics yet.
    """

    network_ref: str
    fits: list[NetworkKineticsBatchFitEvaluation]


class StatmechRecord(TypedDict, total=False):
    statmech: Required[JSONDict]
    # ``None`` on a transition-state-owned record (it has a ``transition_state`` block instead). Under
    # ``profile="curated"`` BOTH ``species`` and ``transition_state`` are ``None`` when the record's subject is a
    # transition-state entry that is not approved: the subject is withheld, not absent.
    species: JSONDict | None
    transition_state: JSONDict | None
    supersession: SupersessionNotice | None
    levels: JSONDict
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

    ``smiles``, ``inchi_key``, ``charge``, ``multiplicity`` and
    ``stereo_kind`` describe the *parent species*, so two rows for two
    entries of one species agree on all of them. ``stereo_label``,
    ``electronic_state_kind``, ``electronic_state_label``, ``term_symbol``
    and ``isotope_key`` are the entry's own and are what tell those rows
    apart; ``species_entry_label`` is the same five rendered as one short
    string. Each is ``None`` when the entry carries no such label -- never
    an empty string.
    """

    species_ref: Required[str]
    species_entry_ref: Required[str]
    smiles: Required[str]
    inchi_key: Required[str]
    charge: Required[int]
    multiplicity: Required[int]
    stereo_kind: Required[str]
    species_entry_kind: Required[str]
    electronic_state_kind: Required[str]
    stereo_label: str | None
    electronic_state_label: str | None
    term_symbol: str | None
    isotope_key: str | None
    species_entry_label: str | None
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
    """One observation, with the group and species it belongs to attached.

    ``conformer_group`` is ``None`` when the read used ``profile="curated"`` and the observation's group is not
    approved: the observation is shown, the group it sits in is not named.
    """

    conformer_observation: Required[JSONDict]
    conformer_group: Required[JSONDict | None]
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
    # ``None`` when the read used ``profile="curated"`` and the owning transition state is not approved: the entry is
    # shown, the transition state it belongs to is not named (the key is always present).
    transition_state: Required[JSONDict | None]
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


class LevelOfTheoryRecord(TypedDict, total=False):
    """One ``level_of_theory`` row -- the methods-surface reference page.

    ``correction_schemes`` reuses the same per-scheme shape as
    ``EnergyCorrectionSchemeRecord`` (each entry carries its own
    ``corrections`` parameter table). ``frequency_scale_factors`` is
    deduplicated by ``(scale_kind, value)``; each group still lists every
    underlying ``frequency_scale_factor_ref`` and its own provenance --
    see the backend schema's docstring for why the ten near-duplicate
    rows deposited for one live level of theory do not collapse to one.
    ``software`` (``include=software``) is the LOT-scoped
    software/workflow-tool usage breakdown: ``{"software": [...],
    "workflow_tools": [...]}``, each row ``{software|workflow_tool,
    version, calculation_count}``. A package or tool with zero
    calculations at this level of theory is absent from its list, never
    present with a zero count; ``version`` is ``null``, never ``""``,
    when no version was recorded.
    """

    level_of_theory: Required[JSONDict]
    evidence_summary: Required[JSONDict]
    available_sections: Required[JSONDict]
    correction_schemes: list[JSONDict] | None
    frequency_scale_factors: list[JSONDict] | None
    used_by: list[JSONDict] | None
    software: JSONDict | None


class CompositeSchemeRecord(TypedDict):
    """One composite scheme -- the recipe behind a composite level of theory.

    ``composite_scheme`` holds the identity (``composite_scheme_ref``,
    ``kind``, ``name``, ``definition_hash``), the levels the recipe runs
    internally (``geometry_level_of_theory``, ``frequency_level_of_theory``;
    ``None`` when the source does not state them), and
    ``recipe_zpe_scale_factor`` (``None`` unless cited: never substitute 1.0).
    ``terms`` is empty for a ``named_method`` scheme: the catalogue holds no
    term list, so an empty list means "none recorded", not "no steps".
    ``bound_levels_of_theory`` lists the levels whose energy the scheme names.
    Refs only: no database id.
    """

    composite_scheme: JSONDict
    terms: list[JSONDict]
    bound_levels_of_theory: list[JSONDict]


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
    sort: Required[OrderEcho]
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
# The backend's ScientificSpeciesBrowseResponse is a subclass of
# ScientificSpeciesSearchResponse that adds no fields (verified against
# app/schemas/reads/scientific_species.py, not assumed from a docstring):
# request / review_summary / records / pagination, same SpeciesRecord
# shape underneath. A distinct alias for the same underlying generic
# instantiation, not a reuse of SpeciesSearchResponse by name, mirrors
# the backend's own choice to declare browse's envelope as its own class
# "so the OpenAPI document and generated clients name the two surfaces
# separately even though nothing about the shape differs."
SpeciesBrowseResponse: TypeAlias = ScientificSearchResponse[SpeciesRecord]
ReactionSearchResponse: TypeAlias = ScientificSearchResponse[ReactionRecord]
ThermoSearchResponse: TypeAlias = ScientificSearchResponse[ThermoSearchRecord]


class SpeciesThermoResponse(ScientificSearchResponse[ThermoDetailRecord]):
    species_entry_ref: str
    species_entry_id: NotRequired[int]


class SelectionEligibilityIn(TypedDict, total=False):
    """The eligibility defaults a select request may lift; every one is applied unless set ``true``.

    Which of these a route offers depends on the record kind: a network solve offers only the two review ones, a statmech
    candidate and a structure unit add ``include_confirmed_findings``, thermo and kinetics offer all four. The read
    profile's review floor (``curated``) is not one of them and no request lifts it.
    """

    include_rejected: bool
    include_deprecated: bool
    include_hard_failed_evidence: bool
    include_confirmed_findings: bool


class SelectionFilters(TypedDict, total=False):
    """The filters a select request may carry, with the matching search's vocabulary (a route accepts its own subset).

    thermo and kinetics: ``model_kind``, ``level_of_theory_ref``, ``software``. network and statmech: ``model_kind`` and
    the provenance fields. structure: the provenance fields. An unknown filter is a 422, never ignored.
    """

    model_kind: str
    level_of_theory_ref: str
    method: str
    basis: str
    software: str
    software_version: str
    workflow_tool: str
    workflow_tool_version: str


class EligibilityDefaultEcho(TypedDict):
    """One named eligibility default, as applied to the answer: ``source`` is ``default``, ``request`` or ``read_profile``."""

    name: str
    override_field: str | None
    applied: bool
    source: Literal["default", "request", "read_profile"]
    excluded: int
    admitted_by_override: int


class SelectionFunnel(TypedDict):
    """How many candidates each step removed (``unit`` says what is counted)."""

    unit: str
    considered: int
    removed_by_filters: int
    removed_by_floor: int
    removed_by_eligibility: int
    remaining: int


class SelectionFilterAnswer(TypedDict):
    applied: JSONDict
    removed: int
    note: str | None


class ThermoSelectionTargetIn(TypedDict, total=False):
    """The thermodynamic target of a selection request.

    ``kind`` is ``"equilibrium_ensemble"`` or ``"single_conformer"``; the latter
    requires ``conformer_group_ref`` (a public ``cg_...`` ref), the former refuses it.
    """

    kind: Required[Literal["equilibrium_ensemble", "single_conformer"]]
    conformer_group_ref: str


class ThermoSelectionRequest(TypedDict, total=False):
    """Body of ``POST .../thermo/select``. Only ``target`` is required.

    ``quantity``, ``temperature_k`` and ``phase`` may be omitted; if given they must
    be ``"formation_enthalpy_298k"``, 298.15 and ``"gas"``, and anything else is refused.
    """

    target: Required[ThermoSelectionTargetIn]
    quantity: Literal["formation_enthalpy_298k"]
    temperature_k: float
    phase: Literal["gas"]
    policy: Literal["default", "most_reviewed", "latest"]
    result_mode: Literal["all", "first"]
    min_review_status: str
    filters: SelectionFilters
    eligibility: SelectionEligibilityIn


ThermoSelectionOutcomeToken: TypeAlias = Literal[
    "incomparable_alternatives",
    "sole_eligible_candidate",
    "no_applicable_candidate",
]


class ThermoSelectionPick(TypedDict):
    """The one record the response names, and exactly why.

    ``administrative`` is true when the record is first in display order among several eligible
    alternatives: that is not a claim it is better, preferred or method-superior.
    """

    thermo_ref: str
    basis: Literal["sole_eligible_candidate", "administrative_first"]
    administrative: bool
    explanation: str


class ThermoSelectionCandidate(TypedDict):
    thermo_ref: str
    review_status: str
    created_at: str
    scientific_origin: str
    phase: str | None
    enthalpy_reference_kind: str | None
    target_kind: str | None
    target_group_ref: str | None
    protocol_state: str
    protocol: JSONDict | None
    linked_recipe_keys: list[str]
    applicability: str
    reasons: list[JSONDict]
    answer_representation: str | None
    value_kj_mol: float | None
    representations: list[JSONDict]
    blocking: list[str]
    advisory: list[str]
    eligible: bool
    blocked_by: list[str]
    passed_only_by_override: list[str]


class ThermoSelectionDisclosures(TypedDict):
    unresolved_refs: list[str]
    unsupported_refs: list[str]
    visible_candidates: int
    excluded_by_review: list[JSONDict]
    excluded_by_review_withheld: bool
    notes: list[str]


class ThermoSelectionResponse(TypedDict):
    """The decision, its basis, and everything it rests on, as public refs only.

    ``outcome`` is the selection basis. Preserve it and ``basis`` verbatim when
    reporting a result: ``incomparable_alternatives`` means several records are eligible and TCKDB
    ranks none above another.
    """

    request: JSONDict
    review: JSONDict
    policy: JSONDict
    outcome: ThermoSelectionOutcomeToken
    basis: str
    selection: ThermoSelectionPick | None
    fronts: list[list[str]]
    administrative_order: list[str]
    candidates: list[ThermoSelectionCandidate]
    disclosures: ThermoSelectionDisclosures
    filters: SelectionFilterAnswer
    eligibility: list[EligibilityDefaultEcho]
    funnel: SelectionFunnel


class KineticsSelectionTargetIn(TypedDict, total=False):
    """The rate asked for: ``{"kind": "whole_reaction"}`` or a resolved channel.

    A ``resolved_channel`` names either ``transition_state_entry_ref`` (a public ``tse_...`` ref) or
    ``network_ref`` (``net_...``) with ``channel_key``; a ``whole_reaction`` names none of them.
    """

    kind: Required[Literal["whole_reaction", "resolved_channel"]]
    transition_state_entry_ref: str
    network_ref: str
    channel_key: str


class KineticsSelectionPressureIn(TypedDict, total=False):
    """Pressure as asked. ``finite`` needs ``min_bar`` and ``max_bar`` (equal for a point); the others carry none."""

    kind: Required[Literal["independent", "high_pressure_limit", "finite"]]
    min_bar: float
    max_bar: float


class KineticsSelectionColliderComponent(TypedDict, total=False):
    species_ref: Required[str]
    mole_fraction: float


class KineticsSelectionColliderIn(TypedDict):
    """One component without a fraction is a specified collider; two or more with fractions summing to one is a
    mixture (never renormalised)."""

    components: list[KineticsSelectionColliderComponent]


class KineticsSelectionRequest(TypedDict, total=False):
    """Body of ``POST .../kinetics/select``. ``direction``, ``target``, ``coefficient_basis``, the temperature
    window and ``pressure`` are required; a ``collider`` is required for a finite pressure and for a
    composition-effective coefficient."""

    direction: Required[Literal["forward", "reverse"]]
    target: Required[KineticsSelectionTargetIn]
    coefficient_basis: Required[
        Literal["elementary_coefficient", "third_body_kernel", "composition_effective_coefficient"]
    ]
    temperature_min_k: Required[float]
    temperature_max_k: Required[float]
    pressure: Required[KineticsSelectionPressureIn]
    collider: KineticsSelectionColliderIn
    quantity: Literal["rate_coefficient"]
    phase: Literal["gas"]
    policy: Literal["default", "most_reviewed", "latest"]
    mode: Literal["all", "first"]
    min_review_status: str
    filters: SelectionFilters
    eligibility: SelectionEligibilityIn


KineticsSelectionOutcomeToken: TypeAlias = Literal[
    "incomparable_alternatives",
    "sole_eligible_candidate",
    "no_applicable_candidate",
]


class KineticsSelectionPick(TypedDict):
    """The determination the response names (with every eligible fitted representation of it), and exactly why.

    ``administrative`` is true when it is first in display order among several eligible alternatives: that is not a
    claim it is better, preferred or method-superior. Alternate fits of one determination are not independent
    confirmation.
    """

    determination_ref: str
    kinetics_refs: list[str]
    basis: Literal["sole_eligible_candidate", "administrative_first"]
    administrative: bool
    explanation: str


class KineticsSelectionCandidate(TypedDict):
    kinetics_ref: str
    review_status: str
    created_at: str
    scientific_origin: str
    model_kind: str
    direction: str | None
    determination_ref: str | None
    representation_role: str | None
    applicability_state: str
    applicability_declaration: JSONDict | None
    protocol_state: str
    protocol: JSONDict | None
    applicability: str
    reasons: list[JSONDict]
    blocking: list[str]
    advisory: list[str]
    eligible: bool
    blocked_by: list[str]
    passed_only_by_override: list[str]


class KineticsSelectionDisclosures(TypedDict):
    unresolved_refs: list[str]
    unsupported_refs: list[str]
    visible_candidates: int
    excluded_by_review: list[JSONDict]
    excluded_count: int
    excluded_by_review_withheld: bool
    notes: list[str]


class KineticsSelectionResponse(TypedDict):
    """The decision, its basis, and everything it rests on, as public refs only.

    ``outcome`` is the selection basis. Preserve it and ``basis`` verbatim when reporting a result:
    ``incomparable_alternatives`` means several are eligible and TCKDB ranks none above another, and a
    ``sole_eligible_candidate`` is not a comparative accuracy claim. The answer is scoped to the eligible population;
    ``disclosures`` lists the candidates that did not compete.
    """

    request: JSONDict
    review: JSONDict
    policy: JSONDict
    outcome: KineticsSelectionOutcomeToken
    basis: str
    selection: KineticsSelectionPick | None
    fronts: list[list[str]]
    administrative_order: list[str]
    determinations: list[JSONDict]
    candidates: list[KineticsSelectionCandidate]
    disclosures: KineticsSelectionDisclosures
    filters: SelectionFilterAnswer
    eligibility: list[EligibilityDefaultEcho]
    funnel: SelectionFunnel


class NetworkSelectionBathComponent(TypedDict, total=False):
    species_ref: Required[str]
    mole_fraction: float


class NetworkSelectionBathIn(TypedDict):
    """One component without a fraction is a specified collider; two or more with fractions summing to one is a
    mixture (never renormalised). Components are public ``spe_...`` species-entry refs."""

    components: list[NetworkSelectionBathComponent]


class NetworkSelectionPartitionIn(TypedDict, total=False):
    """How the observable treats the network's states, by composition hash. At least one retained state or lump."""

    retained: list[str]
    eliminated: list[str]
    lumps: list[list[str]]


class NetworkSelectionBoundaryIn(TypedDict):
    state: str
    kind: str


class NetworkSelectionRegimeIn(TypedDict, total=False):
    kind: Literal["time_independent", "initial_population_restricted"]
    initial_state_hashes: list[str]


class NetworkSelectionOutputIn(TypedDict):
    channel_key: str
    observable: str


class NetworkSelectionRequest(TypedDict, total=False):
    """Body of ``POST .../networks/{ref}/kinetics/select``. The network is named by the path, never the body.

    ``coefficient_basis``, the temperature and pressure windows, ``bath`` and ``partition`` are required. A
    single-channel question names ``channel_key`` and ``observable``; a bundle or full-network question lists
    ``outputs`` instead.
    """

    coefficient_basis: Required[Literal["kernel", "composition_effective"]]
    temperature_min_k: Required[float]
    temperature_max_k: Required[float]
    pressure_min_bar: Required[float]
    pressure_max_bar: Required[float]
    bath: Required[NetworkSelectionBathIn]
    partition: Required[NetworkSelectionPartitionIn]
    scope: Literal["single_channel", "projected_bundle", "full_network"]
    channel_key: str
    observable: str
    outputs: list[NetworkSelectionOutputIn]
    degeneracy_applied: bool
    boundaries: list[NetworkSelectionBoundaryIn]
    regime: NetworkSelectionRegimeIn
    source_composition_hash: str
    sink_composition_hash: str
    quantity: Literal["rate_coefficient"]
    phase: Literal["gas"]
    policy: Literal["default", "most_reviewed", "latest"]
    mode: Literal["all", "first"]
    min_review_status: str
    filters: SelectionFilters
    eligibility: SelectionEligibilityIn


NetworkSelectionOutcomeToken: TypeAlias = Literal[
    "incomparable_alternatives",
    "sole_eligible_candidate",
    "no_applicable_candidate",
]


class NetworkSelectionMember(TypedDict):
    determination_ref: str
    channel_key: str | None
    kinetics_refs: list[str]


class NetworkSelectionPick(TypedDict):
    """The node the response names: a determination of one channel, or a declared product set of one solve.

    ``administrative`` is true when it is first in display order among several eligible alternatives: that is not a
    claim it is better, preferred or method-superior. Alternate fits of one determination are not independent
    confirmation.
    """

    node_ref: str
    scope: Literal["single_channel", "projected_bundle", "full_network"]
    solve_ref: str
    members: list[NetworkSelectionMember]
    basis: Literal["sole_eligible_candidate", "administrative_first"]
    administrative: bool
    explanation: str


class NetworkSelectionDisclosures(TypedDict):
    unresolved_refs: list[str]
    unsupported_refs: list[str]
    ungrouped_fit_refs: list[str]
    population: dict[str, int]
    excluded_by_review: list[dict[str, str]]
    excluded_count: int
    excluded_by_review_withheld: bool
    notes: list[str]


class NetworkSelectionResponse(TypedDict):
    """The decision, its basis and everything it rests on, as public refs only.

    ``outcome`` is the selection basis. Preserve it and ``basis`` verbatim when reporting a result:
    ``incomparable_alternatives`` means several are eligible and TCKDB ranks none above another, and a
    ``sole_eligible_candidate`` is not a comparative accuracy claim. The answer is scoped to the eligible population;
    ``disclosures`` lists what did not compete.
    """

    request: JSONDict
    review: JSONDict
    policy: JSONDict
    outcome: NetworkSelectionOutcomeToken
    basis: str
    selection: NetworkSelectionPick | None
    fronts: list[list[str]]
    administrative_order: list[str]
    determinations: list[JSONDict]
    bundles: list[JSONDict]
    solves: list[JSONDict]
    filters: SelectionFilterAnswer
    eligibility: list[EligibilityDefaultEcho]
    funnel: SelectionFunnel
    representations: list[JSONDict]
    disclosures: NetworkSelectionDisclosures


class NetworkSelectedKineticsExportRequest(TypedDict, total=False):
    """Body of ``POST .../networks/{ref}/kinetics/export-selected``.

    ``manifest`` is the document saved from ``.../select/manifest``, exactly as downloaded. ``node_ref`` is the chosen
    node and ``representation_refs`` names exactly one eligible fit for every member of it. There is no field for a
    rule, a verdict the server should believe or a database id.
    """

    manifest: Required[JSONDict]
    node_ref: Required[str]
    representation_refs: Required[list[str]]
    format: Literal["native", "chemkin"]
    allow_administrative_choice: bool
    energy_units: Literal["cal/mol", "kcal/mol", "j/mol", "kj/mol", "k"]
    naming_policy: Literal["formula", "public_ref"]
    include_reported: bool


class NetworkSelectedKineticsExport(TypedDict):
    """One verified selection, serialised. CHEMKIN output is forward-only and carries no thermodynamics; read
    ``assumptions`` and ``provenance`` before relying on it. ``administrative`` is true when the caller accepted one
    of several unranked alternatives, which is not a method claim."""

    request: JSONDict
    format: Literal["native", "chemkin"]
    network_ref: str
    node_ref: str
    solve_ref: str
    selection_basis: str
    administrative: bool
    provenance: JSONDict
    assumptions: list[str]
    members: list[JSONDict]
    files: dict[str, str] | None
    equation_collisions: list[JSONDict]


class ObservationRecord(TypedDict, total=False):
    """One ``molecular_property_observation`` row (Phase C-E5).

    Not a *product* with candidacy/selection semantics like
    :class:`ThermoDetailRecord` -- a raw external measurement, so there is
    no ``collapse``/``selection_policy``/``trust``/``assessments`` here.
    Identity is resolved by construction: every row on this surface was
    found by its owning species entry, so unresolved observations
    (nullable ``species_entry_id`` on the backend) are structurally
    unreachable -- there is no *status* field asking "does this row have an
    identity". ``identity_basis`` (review round 3, R2) answers the
    different question of *how* it got one: an importer's automatic
    single-InChIKey match (``"external_identifier_match"``) or a curator's
    later judgement call through the identity-attach service
    (``"curator_attached"``). See
    ``app.db.models.common.ObservationIdentityBasis`` on the backend.
    """

    observation_ref: Required[str]
    property_kind: Required[str]
    property_label: str | None
    scalar_value: float | None
    scalar_unit: str | None
    vector: JSONDict | None
    tensor: JSONDict | None
    uncertainty: JSONDict | None
    temperature_k: float | None
    pressure_bar: float | None
    wavelength_nm: float | None
    state_basis: str | None
    state_label_raw: str | None
    method_note: str | None
    scientific_origin: Required[str]
    literature_ref: str | None
    reference_label: str | None
    external_source: JSONDict | None
    identity_basis: Required[str]
    review: Required[JSONDict]


SpeciesObservationsResponse: TypeAlias = ScientificSearchResponse[ObservationRecord]


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
LevelOfTheorySearchResponse: TypeAlias = ScientificSearchResponse[
    LevelOfTheoryRecord
]
LevelOfTheoryDetailResponse: TypeAlias = ScientificDetailResponse[
    LevelOfTheoryRecord
]
CompositeSchemeDetailResponse: TypeAlias = ScientificDetailResponse[
    CompositeSchemeRecord
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
    "CompositeSchemeDetailResponse",
    "CompositeSchemeRecord",
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
    "KineticsSelectionCandidate",
    "KineticsSelectionColliderComponent",
    "KineticsSelectionColliderIn",
    "KineticsSelectionDisclosures",
    "KineticsSelectionOutcomeToken",
    "KineticsSelectionPick",
    "KineticsSelectionPressureIn",
    "EligibilityDefaultEcho",
    "KineticsSelectionRequest",
    "KineticsSelectionResponse",
    "KineticsSelectionTargetIn",
    "LevelOfTheoryDetailResponse",
    "LevelOfTheoryRecord",
    "LevelOfTheorySearchResponse",
    "LiteratureDetailResponse",
    "LiteratureLinkedRecord",
    "LiteratureRecord",
    "LiteratureRecordsResponse",
    "NetworkKineticsBatchEvaluateResponse",
    "NetworkKineticsBatchFitEvaluation",
    "NetworkKineticsEvaluateResponse",
    "NetworkKineticsEvaluatedPoint",
    "NetworkKineticsRecord",
    "NetworkKineticsSearchResponse",
    "NetworkRecord",
    "NetworkSearchResponse",
    "NetworkSelectedKineticsExport",
    "NetworkSelectedKineticsExportRequest",
    "NetworkSelectionBathComponent",
    "NetworkSelectionBathIn",
    "NetworkSelectionBoundaryIn",
    "NetworkSelectionDisclosures",
    "NetworkSelectionMember",
    "NetworkSelectionOutcomeToken",
    "NetworkSelectionOutputIn",
    "NetworkSelectionPartitionIn",
    "NetworkSelectionPick",
    "NetworkSelectionRegimeIn",
    "NetworkSelectionRequest",
    "NetworkSelectionResponse",
    "NetworkSolveRecord",
    "NetworkSolveSearchResponse",
    "NetworkStateComposition",
    "NetworkStateCompositionParticipant",
    "NetworkStateSummary",
    "ObservationRecord",
    "Pagination",
    "PublicAssessmentSummary",
    "ReactionKineticsResponse",
    "ReactionParticipantRecord",
    "ReactionRecord",
    "ReactionSearchResponse",
    "DeterministicTrustSummary",
    "MadeUnder",
    "ReproducibilityAssessmentSummary",
    "ReviewStatusSummary",
    "ScientificDetailResponse",
    "OrderEcho",
    "OrderKeyEcho",
    "ScientificRequestEcho",
    "ScientificSearchResponse",
    "ScientificSoftwareReleaseIdentity",
    "SpeciesBrowseResponse",
    "SpeciesCalculationRecord",
    "SpeciesCalculationsSearchResponse",
    "SpeciesObservationsResponse",
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
    "ThermoSelectionCandidate",
    "ThermoSelectionDisclosures",
    "ThermoSelectionOutcomeToken",
    "ThermoSelectionPick",
    "SelectionEligibilityIn",
    "SelectionFilterAnswer",
    "SelectionFilters",
    "SelectionFunnel",
    "ThermoSelectionRequest",
    "ThermoSelectionResponse",
    "ThermoSelectionTargetIn",
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


# ---------------------------------------------------------------------------
# Structure selection (calculations, conformer basins, transition-state evidence)
# ---------------------------------------------------------------------------

StructureSelectionOutcomeToken: TypeAlias = Literal[
    "no_candidates",
    "energy_unavailable",
    "unresolved_comparability",
    "no_applicable_candidate",
    "recorded_minimum",
    "representative_minimum",
    "qualified_evidence",
    "validated_corpus_minimum",
    "sole_eligible_candidate",
    "incomparable_alternatives",
    "evidence_conflict",
]
"""The 11 outcomes of a structure selection. ``incomparable_alternatives`` means more than one cohort (or protocol)
survived and none is named: total energies of different protocols are not on one scale. ``representative_minimum`` is
the lowest administrative *representative* per target and is never the minimum of all stored values."""


class _StructureSelectionRequestBase(TypedDict, total=False):
    """Fields every structure selection request may carry. Nothing here is a database id, a bound or a sort.

    ``quantity`` is ``electronic_energy`` (the server default) or ``zero_kelvin_energy``; send ``None`` explicitly (JSON
    null) only for evidence-only qualification. A recognised deferred quantity (enthalpy, Gibbs energy, barrier, rate,
    ...) is a 422 ``structure_selection_unsupported``.
    """

    quantity: str | None
    coverage_requirement: Literal["known_values", "all_requested_members"]
    validation_claim: Literal["local_minimum", "first_order_saddle", "higher_order_saddle", "reactive_connectivity"]
    min_review_status: str
    permitted_quality: list[Literal["raw", "curated", "rejected"]]
    filters: SelectionFilters
    eligibility: SelectionEligibilityIn
    geometry_ref: str
    member_refs: list[str]
    recipe: JSONDict
    require_stable_reference: bool
    administrative_policy: Literal["default", "latest", "earliest"]
    result_mode: Literal["all", "first"]
    repeat_policy: Literal["retain_alternates", "administrative_representative"]


class CalculationSelectionRequest(_StructureSelectionRequestBase, total=False):
    """Body of ``POST .../species-entries/{ref}/calculations/select``."""

    intent: Literal["recorded_minimum", "protocol_coverage"]


class ConformerSelectionRequest(_StructureSelectionRequestBase, total=False):
    """Body of ``POST .../species-entries/{ref}/conformers/select``."""

    intent: Literal["validated_minimum", "qualify_evidence", "protocol_coverage"]


class TransitionStateEvidenceSelectionRequest(_StructureSelectionRequestBase, total=False):
    """Body of ``POST .../transition-state-entries/{ref}/evidence/select``.

    One transition state *entry*: comparison across several entries needs a validated same-path declaration that is not
    taken yet.
    """

    intent: Literal["validated_saddle", "qualify_evidence", "protocol_coverage"]
    require_connectivity: bool


class StructureSelectionUnit(TypedDict):
    """One assessed unit (a calculation, or a basin or saddle determination) and every finding behind its verdict."""

    unit_ref: str
    review_status: str
    created_at: str
    applicability: str
    reasons: list[JSONDict]
    blocking: list[str]
    advisory: list[str]
    comparative_unknown: list[str]
    energy: JSONDict | None
    recipe: JSONDict | None
    claim: JSONDict | None
    eligible: bool
    blocked_by: list[str]
    passed_only_by_override: list[str]


class StructureSelectionDisclosures(TypedDict):
    unresolved_refs: list[str]
    unsupported_refs: list[str]
    visible_units: int
    excluded_by_review: list[JSONDict]
    excluded_count: int
    excluded_by_review_withheld: bool
    notes: list[str]


class StructureSelectionResponse(TypedDict):
    """A structure selection's outcome and everything it rests on, as public refs only.

    Report ``outcome`` and ``basis`` verbatim. ``selected_refs`` holds every tied minimum, the qualified determinations or
    the selected protocol's cohort id, and is empty where nothing is selected. ``coverage`` always says it is the
    caller's authorized population, never a global-search certificate. ``integrity`` carries the manifest checksum: a
    checksum, not a signature.
    """

    request: JSONDict
    review: JSONDict
    outcome: StructureSelectionOutcomeToken
    basis: str
    selected_refs: list[str]
    administrative_first: dict[str, str] | None
    cohorts: list[JSONDict]
    contested_targets: list[JSONDict]
    unresolved: list[JSONDict]
    coverage: JSONDict
    representative_policy: JSONDict | None
    protocol: JSONDict | None
    administrative_key: JSONDict
    search_completeness: str
    notes: list[str]
    units: list[StructureSelectionUnit]
    disclosures: StructureSelectionDisclosures
    filters: SelectionFilterAnswer
    eligibility: list[EligibilityDefaultEcho]
    funnel: SelectionFunnel
    versions: JSONDict
    snapshot_isolation: str
    integrity: JSONDict


# ---------------------------------------------------------------------------
# Relative conformer energies (one species entry, against a conformer the caller names)
# ---------------------------------------------------------------------------

RelativeEnergyOutcomeToken: TypeAlias = Literal[
    "relative_energies",
    "reference_only",
    "reference_unavailable",
    "multiple_recipes_at_level",
    "incomplete_coverage",
    "no_members",
]
"""The outcomes of a relative conformer energy read. Report ``outcome`` and ``basis`` verbatim."""

RelativeEnergyComparisonOutcomeToken: TypeAlias = Literal["compared", "no_pairs", "incomplete_pairing"]

RelativeEnergyPairing: TypeAlias = Literal["same_geometry", "same_basin"]
"""``same_geometry``: both values evaluated at the same geometry. ``same_basin``: each protocol's own geometry of the basin."""


class RelativeEnergyMember(TypedDict):
    """One conformer group. There is no rank: members are ordered by ref and the order is not a ranking.

    A member with no single value has ``status`` ``absent`` or ``unresolved``, a coded ``absent_reason`` and ``None``
    values, never zero.
    """

    conformer_ref: str
    status: str
    relative_energy_kj_mol: float | None
    relative_energy_hartree: float | None
    value_hartree: float | None
    value_uncertainty_hartree: float | None
    representative: bool
    determination_refs: list[str]
    energy_calculation_refs: list[str]
    evaluated_geometry_refs: list[str]
    value_geometry_ref: str | None
    certificate: JSONDict | None
    alternates: list[JSONDict]
    absent_reason: str | None
    detail: list[str]


class RelativeConformerEnergyResponse(TypedDict):
    """Relative conformer energies against the named reference conformer: a read, nothing is stored.

    Report ``outcome`` and ``basis`` verbatim. The relative energy is the member minus the reference, so a negative
    value is below the reference. ``integrity`` carries the manifest checksum: a checksum, not a signature.
    """

    request: JSONDict
    review: JSONDict
    outcome: RelativeEnergyOutcomeToken
    basis: str
    energy_kind: str
    temperature_k: float | None
    units: JSONDict
    level: JSONDict
    reference: JSONDict
    members: list[RelativeEnergyMember]
    coverage: JSONDict
    unresolved: list[JSONDict]
    cohorts: list[JSONDict]
    notes: list[str]
    search_completeness: str
    versions: JSONDict
    snapshot_isolation: str
    integrity: JSONDict


class RelativeConformerEnergyManifest(TypedDict, total=False):
    """The replayable manifest of the relative-energy ``/manifest`` download, exactly as the server sent it.

    The server allows extra keys, so this lists the documented ones only. The client does not normalise, reorder or
    drop any key: replay needs the manifest as served.
    """

    format: JSONDict
    versions: JSONDict
    request: JSONDict
    structure_request: JSONDict
    effective_review_statuses: list[str]
    read_profile: JSONDict
    snapshot_isolation: str
    subject: JSONDict
    population: JSONDict
    calculations: list[JSONDict]
    determinations: list[JSONDict]
    assessments: list[JSONDict]
    result: JSONDict
    outcome: RelativeEnergyOutcomeToken
    integrity: JSONDict


class RelativeEnergyComparePair(TypedDict):
    """One conformer's two values and their deviation. An unusable pair has ``excluded_reason`` and no deviation.

    ``excluded_reason`` is a code from a closed vocabulary (typed ``str`` because the server's type is ``str``):
    ``neither_protocol_has_value``, ``candidate_value_missing``, ``reference_protocol_value_missing``,
    ``geometry_not_recorded``, ``geometry_differs``, ``reference_conformer_value_missing``,
    ``reference_conformer_geometry_not_recorded`` and ``reference_conformer_geometry_differs``. Never a zero deviation.
    """

    conformer_ref: str
    candidate_kj_mol: float | None
    reference_kj_mol: float | None
    deviation_kj_mol: float | None
    excluded_reason: str | None
    candidate_uncertainty_hartree: float | None
    reference_uncertainty_hartree: float | None
    candidate_geometry_ref: str | None
    reference_geometry_ref: str | None


class RelativeEnergyCompareClaims(TypedDict):
    """What the comparison claims: description only. ``missing_evidence`` lists what a superiority claim would need."""

    kind: str
    superiority: str
    ranking: str
    missing_evidence: list[str]
    statement: str


class RelativeEnergyCompareReferenceConformer(TypedDict):
    """What the named reference conformer allows; every relative energy is taken from it.

    ``problem`` is ``None`` or one of ``reference_conformer_value_missing``, ``reference_conformer_geometry_not_recorded``
    and ``reference_conformer_geometry_differs`` (the last two under ``same_geometry`` only). With a problem no pair is
    usable.
    """

    conformer_ref: str
    problem: str | None
    candidate_value_present: bool
    reference_value_present: bool
    candidate_geometry_ref: str | None
    reference_geometry_ref: str | None


class RelativeConformerEnergyCompareResponse(TypedDict):
    """A descriptive comparison of two stored protocols against one named reference conformer.

    Report ``outcome`` and ``claims["statement"]`` verbatim. The comparison is descriptive only: it names no better
    protocol and ranks nothing, and every deviation depends on the named reference conformer
    (``statistics["reference_conformer_dependence"]``). ``statistics`` is ``None`` for ``incomplete_pairing``.
    """

    request: JSONDict
    review: JSONDict
    outcome: RelativeEnergyComparisonOutcomeToken
    pairing: RelativeEnergyPairing
    coverage_requirement: str
    units: JSONDict
    statistics: JSONDict | None
    reference_conformer: RelativeEnergyCompareReferenceConformer
    pairs: list[RelativeEnergyComparePair]
    candidate: JSONDict
    reference: JSONDict
    claims: RelativeEnergyCompareClaims
    comparison_version: str
    notes: list[str]
    versions: JSONDict
    snapshot_isolation: str
    integrity: JSONDict


class RelativeConformerEnergyCompareManifest(TypedDict, total=False):
    """The replayable manifest of the ``/compare/manifest`` download, exactly as the server sent it (extra keys allowed)."""

    format: JSONDict
    versions: JSONDict
    request: JSONDict
    read_profile: JSONDict
    snapshot_isolation: str
    runs: JSONDict
    result: JSONDict
    outcome: RelativeEnergyComparisonOutcomeToken
    integrity: JSONDict


# ---------------------------------------------------------------------------
# Statmech selection (qualify supplied statistical models; list the qualified ones in display order)
# ---------------------------------------------------------------------------

StatmechSelectionOutcomeToken: TypeAlias = Literal[
    "qualified_models",
    "no_candidates",
    "no_applicable_candidate",
    "unresolved_applicability",
    "missing_required_data",
    "unsupported_task",
    "unsupported_model",
    "evidence_conflict",
    "sole_eligible_candidate",
    "incomparable_alternatives",
]
"""The 10 outcomes of a statmech selection. ``incomparable_alternatives`` means more than one applicable whole model and
TCKDB ranks none above another. A valid negative outcome is an HTTP 200."""


class StatmechSelectionRequest(TypedDict, total=False):
    """Body of ``POST /scientific/statmech/select``: one typed target union (a species or transition-state entry).

    ``target`` is ``{"kind": "species_entry", "species_entry_ref": "spe_...", "scope": {...}, ...}`` or
    ``{"kind": "transition_state_entry", "transition_state_entry_ref": "tse_...", "scope": {...}, ...}``; every ref is a
    public ref. Nothing here is a database id, a bound, a page or a sort.
    """

    contract_version: Literal[1]
    intent: Literal["qualify", "protocol_coverage"]
    task: str
    observable: str
    target: JSONDict
    conditions: JSONDict
    population: JSONDict
    coverage_requirement: Literal["entire_requested_domain", "eligible_population"]
    administrative_policy: Literal["default", "latest", "most_reviewed"]
    result_mode: Literal["all", "first"]
    min_review_status: str
    filters: SelectionFilters
    eligibility: SelectionEligibilityIn


class StatmechSelectionCandidate(TypedDict):
    """One assessed candidate: a declared model (``smd_``) or a legacy record read as stored (``sm_``)."""

    candidate_ref: str
    kind: Literal["declared_model", "legacy_record"]
    owner_ref: str
    review_status: str
    created_at: str
    applicability: Literal["applicable", "incompatible", "unresolved", "unsupported"]
    reasons: list[JSONDict]
    advisory: list[str]
    comparative_unknown: list[str]
    evidence: dict[str, list[str]]
    recipe: JSONDict | None
    supersession: JSONDict | None
    eligible: bool
    blocked_by: list[str]
    passed_only_by_override: list[str]


class StatmechSelectionSelection(TypedDict):
    """The two mutually exclusive references; at most one is set, with its basis and population limit."""

    sole_eligible_ref: str | None
    administrative_ref: str | None
    basis: str | None
    limitation: str | None


class StatmechSelectionResponse(TypedDict):
    """A statmech selection's outcome and everything it rests on, as public refs only. No computed value.

    Report ``outcome`` and ``basis`` verbatim. ``selection`` carries at most one reference. ``advisories`` and ``notices``
    surface the disclosures that do not exclude a candidate (``imported_parameters_not_revalidated:<role>``,
    ``source_literature_unidentified:<role>``). ``coverage`` always says it is the caller's authorized population, never a
    physical-search certificate. ``integrity`` carries the manifest checksum: a checksum, not a signature.
    """

    request: JSONDict
    review: JSONDict
    versions: JSONDict
    outcome: StatmechSelectionOutcomeToken
    basis: str
    selection: StatmechSelectionSelection
    summary: JSONDict | None
    population: JSONDict
    coverage: JSONDict
    candidates: list[StatmechSelectionCandidate]
    fronts: list[list[str]]
    filters: SelectionFilterAnswer
    eligibility: list[EligibilityDefaultEcho]
    funnel: SelectionFunnel
    administrative_order: list[str]
    administrative_key: JSONDict
    advisories: dict[str, list[str]]
    notices: list[str]
    manifest: JSONDict
    snapshot_isolation: str
    integrity: JSONDict
