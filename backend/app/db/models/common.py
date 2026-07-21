from __future__ import annotations

from enum import Enum


class MoleculeKind(str, Enum):
    molecule = "molecule"
    pseudo = "pseudo"


class StationaryPointKind(str, Enum):
    minimum = "minimum"
    vdw_complex = "vdw_complex"


class ConformerSelectionKind(str, Enum):
    display_default = "display_default"
    curator_pick = "curator_pick"
    lowest_energy = "lowest_energy"
    benchmark_reference = "benchmark_reference"
    preferred_for_thermo = "preferred_for_thermo"
    preferred_for_kinetics = "preferred_for_kinetics"
    representative_geometry = "representative_geometry"


class TransitionStateSelectionKind(str, Enum):
    """Curation-overlay roles for transition-state candidate selection.

    Values are machine tokens (see feedback_enum_values memory), not display
    labels. This is the transition-state analog of
    :class:`ConformerSelectionKind`: ``lowest_barrier`` replaces the conformer
    ``lowest_energy`` role, and there is deliberately no ``preferred_for_thermo``
    (transition states feed kinetics, not thermochemistry).
    """

    display_default = "display_default"
    curator_pick = "curator_pick"
    lowest_barrier = "lowest_barrier"
    benchmark_reference = "benchmark_reference"
    preferred_for_kinetics = "preferred_for_kinetics"
    representative_geometry = "representative_geometry"


class ScientificOriginKind(str, Enum):
    computed = "computed"
    experimental = "experimental"
    estimated = "estimated"


class PhaseKind(str, Enum):
    """Physical phase a thermochemistry record is referenced to.

    Values are machine tokens (see feedback_enum_values memory), not
    display labels. ``gas`` is the standard state for computed gas-phase
    thermochemistry; ``aqueous`` denotes a solvated standard state.
    """

    gas = "gas"
    liquid = "liquid"
    solid = "solid"
    aqueous = "aqueous"


class MolecularPropertyKind(str, Enum):
    """Molecular-property observation kinds (CCCBDB Schema Gap 1).

    Scalar/vector/tensor properties that don't belong on ``thermo``,
    ``statmech``, or ``transport``. Names are machine tokens, not
    display labels (see feedback_enum_values memory).
    """

    dipole_moment = "dipole_moment"
    quadrupole_moment = "quadrupole_moment"
    polarizability = "polarizability"
    polarizability_iso = "polarizability_iso"
    ionization_energy = "ionization_energy"
    electron_affinity = "electron_affinity"
    proton_affinity = "proton_affinity"
    enthalpy_of_formation = "enthalpy_of_formation"
    atomization_energy = "atomization_energy"
    homo_energy = "homo_energy"
    lumo_energy = "lumo_energy"
    homo_lumo_gap = "homo_lumo_gap"
    rotational_constant = "rotational_constant"
    spectroscopic_constant = "spectroscopic_constant"
    other = "other"


class SpeciesEntryReviewRole(str, Enum):
    curator = "curator"
    reviewer = "reviewer"
    validator = "validator"
    linker = "linker"


class ConformerAssignmentScopeKind(str, Enum):
    canonical = "canonical"
    imported = "imported"
    experimental = "experimental"
    custom = "custom"


class RigidRotorKind(str, Enum):
    atom = "atom"
    linear = "linear"
    spherical_top = "spherical_top"
    symmetric_top = "symmetric_top"
    asymmetric_top = "asymmetric_top"


class StatmechTreatmentKind(str, Enum):
    rrho = "rrho"
    rrho_1d = "rrho_1d"
    rrho_nd = "rrho_nd"
    rrho_1d_nd = "rrho_1d_nd"
    rrho_ad = "rrho_ad"
    rrao = "rrao"


class StatmechCalculationRole(str, Enum):
    opt = "opt"
    freq = "freq"
    sp = "sp"
    scan = "scan"
    composite = "composite"
    imported = "imported"


class TorsionTreatmentKind(str, Enum):
    hindered_rotor = "hindered_rotor"
    free_rotor = "free_rotor"
    rigid_top = "rigid_top"
    hindered_rotor_dos = "hindered_rotor_dos"


class ReactionRole(str, Enum):
    reactant = "reactant"
    product = "product"


class CalculationType(str, Enum):
    opt = "opt"
    freq = "freq"
    sp = "sp"
    irc = "irc"
    scan = "scan"
    path_search = "path_search"
    conf = "conf"


class PathSearchMethod(str, Enum):
    """Algorithmic family of a path-search calculation.

    A ``path_search`` calculation explores a reaction path between or
    from molecular endpoints to produce a TS guess. The specific
    algorithm (NEB, GSM, ...) is data on the result row, not a separate
    top-level calculation type.
    """

    neb = "neb"
    gsm = "gsm"
    growing_string = "growing_string"
    freezing_string = "freezing_string"
    other = "other"


class CalculationQuality(str, Enum):
    raw = "raw"
    curated = "curated"
    rejected = "rejected"


class CalculationGeometryRole(str, Enum):
    final = "final"
    initial = "initial"
    scan_point = "scan_point"
    irc_forward = "irc_forward"
    irc_reverse = "irc_reverse"
    path_search_point = "path_search_point"


class CalculationDependencyRole(str, Enum):
    optimized_from = "optimized_from"
    freq_on = "freq_on"
    single_point_on = "single_point_on"
    arkane_source = "arkane_source"
    irc_start = "irc_start"
    irc_followup = "irc_followup"
    scan_parent = "scan_parent"


class ValidationStatus(str, Enum):
    passed = "passed"
    warning = "warning"
    fail = "fail"


class IRCDirection(str, Enum):
    forward = "forward"
    reverse = "reverse"
    both = "both"


class ScanCoordinateKind(str, Enum):
    bond = "bond"
    angle = "angle"
    dihedral = "dihedral"
    improper = "improper"


class ConstraintKind(str, Enum):
    cartesian_atom = "cartesian_atom"
    bond = "bond"
    angle = "angle"
    dihedral = "dihedral"
    improper = "improper"


class ArtifactKind(str, Enum):
    input = "input"
    output_log = "output_log"
    checkpoint = "checkpoint"
    formatted_checkpoint = "formatted_checkpoint"
    hessian = "hessian"
    ancillary = "ancillary"


class HessianSource(str, Enum):
    """Where a stored Cartesian Hessian matrix was obtained from.

    Mirrors the "observation, not possibility" discipline of the rest of
    the calculation provenance: a row records how the matrix actually
    reached TCKDB, not a capability claim.
    """

    parsed_fchk = "parsed_fchk"
    parsed_hess = "parsed_hess"
    parsed_log = "parsed_log"
    uploaded = "uploaded"
    derived = "derived"


class SCFStabilityStatus(str, Enum):
    """SCF wavefunction stability evidence status persisted on
    ``calc_scf_stability``.

    ``not_checked`` is intentionally NOT a stored value — absence of a
    ``calc_scf_stability`` row encodes "not checked" and the read API
    projects this when no row exists. Stored values must reflect a
    stability analysis that was actually attempted.
    """

    stable = "stable"
    unstable = "unstable"
    stabilized = "stabilized"
    inconclusive = "inconclusive"


class ParameterSource(str, Enum):
    """Provenance of a ``calculation_parameter`` row.

    Replace-all on re-parse only deletes ``parser`` rows; ``upload`` and
    ``curated`` rows are preserved.
    """

    parser = "parser"
    upload = "upload"
    curated = "curated"


class SoftwareReconciliationStatus(str, Enum):
    """Outcome of reconciling user-declared vs parser-observed software
    provenance on a ``calculation`` (DR-0008).

    The user declares a software release in the upload payload; the ESS
    output parser observes a version banner. This status records how the
    two sources related when the calculation was ingested:

    * ``matched`` — both sources agree.
    * ``enriched`` — user gave partial info, the parser filled the gaps.
    * ``mismatch`` — the sources disagree on at least one field. The
      declared value still takes precedence (this is provenance, not a
      trust gate); the disagreement is recorded, never rejected.
    * ``declared_only`` — user declared, no parseable banner was observed.
    * ``parsed_only`` — no user declaration, only a parsed banner.

    A NULL value means reconciliation was never run for the calculation
    (e.g. importer-created rows with neither a declared release nor a
    parsed banner). Values mirror
    ``SoftwareReconciliationResult.match_status`` exactly.
    """

    matched = "matched"
    enriched = "enriched"
    mismatch = "mismatch"
    declared_only = "declared_only"
    parsed_only = "parsed_only"


class TransitionStateEntryStatus(str, Enum):
    guess = "guess"
    optimized = "optimized"
    validated = "validated"
    rejected = "rejected"


class ThermoCalculationRole(str, Enum):
    opt = "opt"
    freq = "freq"
    sp = "sp"
    composite = "composite"
    imported = "imported"


class ThermoModelKind(str, Enum):
    """Explicit representation kind for a thermo record.

    Previously the representation was implied by which child rows existed;
    this enum makes it explicit so NASA-9 (arbitrary interval count) and
    Wilhoit forms can be distinguished from NASA-7 / tabulated / scalar.
    """

    nasa7 = "nasa7"
    nasa9 = "nasa9"
    wilhoit = "wilhoit"
    tabulated = "tabulated"
    scalar = "scalar"


class ActivationEnergyUnits(str, Enum):
    j_mol = "j_mol"
    kj_mol = "kj_mol"
    cal_mol = "cal_mol"
    kcal_mol = "kcal_mol"


class ArrheniusAUnits(str, Enum):
    per_s = "per_s"
    cm3_mol_s = "cm3_mol_s"
    cm3_molecule_s = "cm3_molecule_s"
    m3_mol_s = "m3_mol_s"
    cm6_mol2_s = "cm6_mol2_s"
    cm6_molecule2_s = "cm6_molecule2_s"
    m6_mol2_s = "m6_mol2_s"


class KineticsModelKind(str, Enum):
    arrhenius = "arrhenius"
    modified_arrhenius = "modified_arrhenius"
    # Sum-of-modified-Arrhenius form (DR-0036). Represents a Chemkin
    # ``DUPLICATE`` channel whose rate coefficient is the *sum* of N
    # modified-Arrhenius terms. The individual terms live in
    # ``kinetics_arrhenius_entry``; the parent ``a``/``n``/``ea_kj_mol``
    # columns stay null.
    multi_arrhenius = "multi_arrhenius"
    # Pressure-dependent falloff forms (DR-0032 Part B). The k∞ Arrhenius
    # lives on the kinetics row; k0 and broadening params live in
    # ``kinetics_falloff``.
    lindemann = "lindemann"
    troe = "troe"
    sri = "sri"
    # Standalone pressure-dependent fits (DR-0032 Part C) — parameters in
    # ``kinetics_plog`` / ``kinetics_chebyshev`` with no master-equation
    # solve required (for literature fits).
    plog = "plog"
    chebyshev = "chebyshev"


class KineticsDirection(str, Enum):
    """Direction a reaction-level kinetics fit describes (DR-0036).

    Graph-level ``chem_reaction.reversible`` only says a reaction *can* run
    both ways; it cannot tell apart a forward-rate fit from a reverse-rate
    fit deposited against the same ``reaction_entry``. This per-record enum
    disambiguates the two so both can coexist distinctly (needed for
    Chemkin/Cantera round-trip, where forward and reverse rates are given
    as separate expressions).

    ``forward`` / ``reverse`` are relative to the reaction_entry's stored
    reactant→product orientation. ``net`` marks a rate that already folds
    both directions (e.g. an apparent/observed net rate). A ``NULL`` column
    means the producer did not specify — the historical default, so existing
    rows are unaffected.
    """

    forward = "forward"
    reverse = "reverse"
    net = "net"


class TunnelingModel(str, Enum):
    """Tunneling correction applied to a rate coefficient (DR-0032).

    Machine tokens, per the project enum policy (replaces the former
    free-text ``kinetics.tunneling_model`` column). ``other`` is the
    escape hatch for anything not yet in this set.
    """

    none = "none"
    wigner = "wigner"
    eckart = "eckart"
    sct = "sct"
    other = "other"


class SpinTreatment(str, Enum):
    """Spin treatment of the electronic-structure method (DR-0034).

    Restricted (R), unrestricted (U), and restricted-open-shell (RO) treatments
    of the same method/basis are genuinely different levels of theory —
    e.g. UCCSD(T) vs ROCCSD(T), or UB3LYP vs ROB3LYP — and differ at the
    kJ/mol level for radicals. Part of the level-of-theory identity so they
    do not collapse to one row. ``unknown`` = not specified by the producer.
    """

    restricted = "restricted"
    unrestricted = "unrestricted"
    restricted_open = "restricted_open"
    unknown = "unknown"


class PressureContext(str, Enum):
    """What a rate coefficient means with respect to pressure (DR-0032).

    ``high_p_limit``: the high-pressure-limit rate k∞.
    ``apparent_at_pressure``: an apparent rate at a specific pressure
        (requires ``kinetics.pressure_bar``).
    ``pressure_dependent``: a record whose full P-dependence lives in an
        associated model (a network solve, or a falloff/PLOG table).
    NULL (unset) means the pressure context was not specified.
    """

    high_p_limit = "high_p_limit"
    apparent_at_pressure = "apparent_at_pressure"
    pressure_dependent = "pressure_dependent"


class KineticsUncertaintyKind(str, Enum):
    """Interpretation of a kinetics scalar uncertainty.

    ``multiplicative``: factor f, where the true value lies in
    ``[value/f, value*f]`` (must be >= 1). The convention for Arrhenius A.

    ``additive``: same units as the value; symmetric ±delta. Rare for A
    but supported for completeness.
    """

    additive = "additive"
    multiplicative = "multiplicative"


class KineticsCalculationRole(str, Enum):
    reactant_energy = "reactant_energy"
    product_energy = "product_energy"
    ts_energy = "ts_energy"
    freq = "freq"
    irc = "irc"
    master_equation = "master_equation"
    fit_source = "fit_source"


class NetworkSpeciesRole(str, Enum):
    well = "well"
    reactant = "reactant"
    product = "product"
    bath_gas = "bath_gas"


class LiteratureKind(str, Enum):
    article = "article"
    book = "book"
    thesis = "thesis"
    report = "report"
    dataset = "dataset"
    webpage = "webpage"


class AppUserRole(str, Enum):
    user = "user"
    curator = "curator"
    admin = "admin"


class StereoKind(str, Enum):
    unspecified = "unspecified"
    achiral = "achiral"
    enantiomer = "enantiomer"
    diastereomer = "diastereomer"
    ez_isomer = "ez_isomer"


class SpeciesEntryStateKind(str, Enum):
    ground = "ground"
    excited = "excited"


class NetworkStateKind(str, Enum):
    well = "well"
    bimolecular = "bimolecular"
    termolecular = "termolecular"


class NetworkChannelKind(str, Enum):
    isomerization = "isomerization"
    association = "association"
    dissociation = "dissociation"
    stabilization = "stabilization"
    exchange = "exchange"


class NetworkKineticsModelKind(str, Enum):
    chebyshev = "chebyshev"
    plog = "plog"
    tabulated = "tabulated"


class NetworkSolveCalculationRole(str, Enum):
    well_energy = "well_energy"
    barrier_energy = "barrier_energy"
    well_freq = "well_freq"
    barrier_freq = "barrier_freq"
    master_equation_run = "master_equation_run"
    fit_source = "fit_source"


class TransportCalculationRole(str, Enum):
    full_transport = "full_transport"
    dipole = "dipole"
    polarizability = "polarizability"
    supporting_geometry = "supporting_geometry"


# ---------------------------------------------------------------------------
# Energy corrections & frequency scale factors
# ---------------------------------------------------------------------------


class EnergyCorrectionSchemeKind(str, Enum):
    atom_energy = "atom_energy"
    atom_hf = "atom_hf"
    atom_thermal = "atom_thermal"
    soc = "soc"
    bac_petersson = "bac_petersson"
    bac_melius = "bac_melius"
    isodesmic = "isodesmic"
    other = "other"


class MeliusBacComponentKind(str, Enum):
    """Melius-type BAC parameter sub-types (reference layer)."""

    atom_corr = "atom_corr"
    bond_corr_length = "bond_corr_length"
    bond_corr_neighbor = "bond_corr_neighbor"
    mol_corr = "mol_corr"


class FrequencyScaleKind(str, Enum):
    fundamental = "fundamental"
    zpe = "zpe"
    enthalpy = "enthalpy"
    entropy = "entropy"
    heat_capacity = "heat_capacity"


class AppliedCorrectionComponentKind(str, Enum):
    """Broad categories of what correction contribution was applied."""

    atom = "atom"
    bond = "bond"
    molecular = "molecular"
    zpe_scale = "zpe_scale"
    soc = "soc"
    other = "other"


class EnergyCorrectionApplicationRole(str, Enum):
    """Semantic role of an applied energy correction result."""

    zpe = "zpe"
    thermal_correction_energy = "thermal_correction_energy"
    thermal_correction_enthalpy = "thermal_correction_enthalpy"
    thermal_correction_gibbs = "thermal_correction_gibbs"
    entropy_contribution = "entropy_contribution"
    bac_total = "bac_total"
    aec_total = "aec_total"
    soc_total = "soc_total"
    atomization_reference_adjustment = "atomization_reference_adjustment"
    composite_delta = "composite_delta"
    custom = "custom"


class GroupAdditivityComponentKind(str, Enum):
    """Broad category of one group-additivity contribution.

    Benson-style group additivity estimates a property as the sum of
    per-group contributions plus a set of corrections. ``group`` is a
    Benson group-value contribution (e.g. ``C/C/H3``); the remaining
    members are the standard correction families applied on top of the
    bare group sum. ``other`` is an escape hatch for scheme-specific
    corrections not covered here.
    """

    group = "group"
    ring_correction = "ring_correction"
    gauche_correction = "gauche_correction"
    cis_correction = "cis_correction"
    symmetry_correction = "symmetry_correction"
    other = "other"


class CoordinateUnit(str, Enum):
    angstrom = "angstrom"
    degree = "degree"


class EnergyUnit(str, Enum):
    hartree = "hartree"
    kj_mol = "kj_mol"
    kcal_mol = "kcal_mol"


class PressureUnit(str, Enum):
    bar = "bar"
    atm = "atm"


class TemperatureUnit(str, Enum):
    kelvin = "kelvin"


class UploadJobStatus(str, Enum):
    queued = "queued"
    processing = "processing"
    complete = "complete"
    failed = "failed"


class UploadJobKind(str, Enum):
    computed_reaction = "computed_reaction"
    conformer = "conformer"
    reaction = "reaction"
    kinetics = "kinetics"
    network = "network"
    network_pdep = "network_pdep"
    thermo = "thermo"
    transition_state = "transition_state"
    transport = "transport"


# ---------------------------------------------------------------------------
# Submission moderation
# ---------------------------------------------------------------------------


class SubmissionStatus(str, Enum):
    """Lifecycle state of a user submission in the moderation system."""

    pending = "pending"
    precheck_passed = "precheck_passed"
    auto_flagged = "auto_flagged"
    approved = "approved"
    rejected = "rejected"
    superseded = "superseded"
    # System-set terminal state for an upload event whose ingestion failed
    # (e.g. an async job that exhausted retries, or a synchronous upload that
    # raised during persistence). Distinct from ``rejected``, which is a
    # curator decision and carries reviewer/reason invariants. ``failed`` is
    # never curator-approvable and never public.
    failed = "failed"


class SubmissionActorKind(str, Enum):
    """Category of actor recorded on an audit event.

    Human curator/admin actions must remain distinguishable from automated
    (llm/system) actions — only ``curator`` and ``admin`` events count as
    human approval.
    """

    user = "user"
    curator = "curator"
    admin = "admin"
    llm = "llm"
    system = "system"


class SubmissionAuditEventKind(str, Enum):
    """Kind of moderation/lifecycle event appended to the audit log."""

    submission_created = "submission_created"
    ingestion_succeeded = "ingestion_succeeded"
    ingestion_failed = "ingestion_failed"
    llm_precheck_passed = "llm_precheck_passed"
    llm_precheck_flagged = "llm_precheck_flagged"
    llm_precheck_recorded = "llm_precheck_recorded"
    curator_approved = "curator_approved"
    curator_rejected = "curator_rejected"
    correction_window_opened = "correction_window_opened"
    correction_uploaded = "correction_uploaded"
    submission_superseded = "submission_superseded"
    status_changed = "status_changed"
    public_visibility_changed = "public_visibility_changed"


class SubmissionSourceKind(str, Enum):
    """How the submission entered the system."""

    api = "api"
    web = "web"
    bulk_import = "bulk_import"
    system = "system"
    migration = "migration"


class SubmissionKind(str, Enum):
    """Family/category of a submission (thermo, reaction, network, …).

    This is the submission-layer classification of the contribution itself
    — it is *not* provenance-tool identity; see ``workflow_tool`` /
    ``workflow_tool_release`` for that.

    Most values overlap with :class:`UploadJobKind` so the same token may be
    persisted in both places. The overlap is not total: ``computed_species``
    and ``statmech`` are direct-upload kinds that have no async-job counterpart
    (they are not enqueueable via ``/jobs/*``), and ``other`` covers
    submissions that do not map onto any upload pipeline. Adding a value here
    that is also an async kind should keep the two enums aligned.
    """

    computed_reaction = "computed_reaction"
    computed_species = "computed_species"
    conformer = "conformer"
    reaction = "reaction"
    kinetics = "kinetics"
    network = "network"
    network_pdep = "network_pdep"
    statmech = "statmech"
    thermo = "thermo"
    transition_state = "transition_state"
    transport = "transport"
    other = "other"


class SubmissionPrecheckLabel(str, Enum):
    """Result label for an automated LLM/system precheck pass."""

    passed = "passed"
    flagged = "flagged"


class SubmissionRecordType(str, Enum):
    """Domain tables a submission may create records in.

    Kept as a controlled vocabulary so ``submission_record_link`` stays
    lightweight — it is a traceability index, not a substitute for real FKs
    inside the domain model. The same vocabulary is reused by
    ``record_review`` so the two tables share one shape.
    """

    species = "species"
    species_entry = "species_entry"
    conformer_group = "conformer_group"
    conformer_observation = "conformer_observation"
    reaction = "reaction"
    reaction_entry = "reaction_entry"
    transition_state = "transition_state"
    transition_state_entry = "transition_state_entry"
    calculation = "calculation"
    statmech = "statmech"
    thermo = "thermo"
    kinetics = "kinetics"
    transport = "transport"
    network = "network"
    network_solve = "network_solve"
    applied_energy_correction = "applied_energy_correction"
    # Uploaded evidence file attached to a calculation. Linked to a submission
    # for traceability (role="artifact") but never carries a record_review row:
    # artifacts are contribution evidence, not reviewable scientific results.
    artifact = "artifact"


class RecordReviewStatus(str, Enum):
    """Consumer-facing trust/review state of one scientific record.

    Distinct from :class:`SubmissionStatus` (lifecycle of a contribution
    event). One ``record_review`` row per ``(record_type, record_id)``
    holds the current state.
    """

    not_reviewed = "not_reviewed"
    under_review = "under_review"
    approved = "approved"
    rejected = "rejected"
    deprecated = "deprecated"


class RecordReviewEventKind(str, Enum):
    """Kind of append-only event recorded on a ``record_review`` row.

    ``created`` marks the first appearance of a review row (its initial
    status); ``status_change`` records a subsequent transition between two
    :class:`RecordReviewStatus` values.
    """

    created = "created"
    status_change = "status_change"


class ReproducibilityGrade(str, Enum):
    """Highest reproducibility claim supported by one assessment snapshot.

    This vocabulary is independent of record-review state and deterministic
    trust badges.  The values form an increasing evidence ladder, but the
    persisted assessment also records its passed, missing, and warning checks
    so consumers do not have to infer evidence from the grade alone.
    """

    described = "described"
    auditable = "auditable"
    rerunnable = "rerunnable"


class ReproducibilityAssessorKind(str, Enum):
    """Who produced a reproducibility assessment snapshot."""

    system = "system"
    curator = "curator"


class MachineReviewStatus(str, Enum):
    """DB-layer mirror of the advisory machine-review status vocabulary.

    The authoritative vocabulary lives in the service layer as
    :class:`app.services.machine_review.schemas.MachineReviewStatus`. DB
    models must not import service-layer Pydantic schemas (see
    ``.claude/rules/schema-rules.md``), so the token set is mirrored here for
    the persisted *denormalised snapshot* column on
    ``machine_review_curator_task``. The two are kept in lock-step by a
    drift-guard test; this enum is display/ranking metadata only and is never
    authoritative for anything the human-review or evidence layers own.
    """

    not_run = "not_run"
    machine_screened_pass = "machine_screened_pass"
    machine_screened_warning = "machine_screened_warning"
    machine_screened_needs_attention = "machine_screened_needs_attention"
    machine_review_failed = "machine_review_failed"


class MachineReviewSeverity(str, Enum):
    """DB-layer mirror of the machine-review finding severity vocabulary.

    Mirrors :class:`app.services.machine_review.schemas.MachineReviewSeverity`
    for the persisted ``highest_severity`` snapshot column. Kept in lock-step
    with the service-layer twin by a drift-guard test. See
    :class:`MachineReviewStatus` for why the token set is duplicated rather
    than imported.
    """

    info = "info"
    warning = "warning"
    critical = "critical"


class MachineReviewCuratorTaskState(str, Enum):
    """Human triage state over an exact, mapped machine-review finding.

    This is the *fourth* review axis and is intentionally distinct from
    :class:`MachineReviewStatus` (advisory machine output),
    :class:`RecordReviewStatus` (authoritative human review), and
    :class:`SubmissionStatus` (submission lifecycle / moderation). A curator
    task tracks *whether a human has handled a machine finding* — it never
    approves, rejects, certifies, or otherwise mutates a record. See
    ``backend/docs/specs/machine_review_curator_task_queue.md`` §2/§5.

    The first three are **open** states (work outstanding); the last three
    are **terminal/resolved** states (see :meth:`is_terminal`).
    """

    untriaged = "untriaged"
    needs_curator_review = "needs_curator_review"
    in_curator_review = "in_curator_review"
    resolved_no_action = "resolved_no_action"
    resolved_human_reviewed = "resolved_human_reviewed"
    dismissed_machine_finding = "dismissed_machine_finding"

    @classmethod
    def terminal_states(cls) -> "frozenset[MachineReviewCuratorTaskState]":
        """The resolved/terminal states (a human has handled the finding)."""
        return frozenset(
            {
                cls.resolved_no_action,
                cls.resolved_human_reviewed,
                cls.dismissed_machine_finding,
            }
        )

    @property
    def is_terminal(self) -> bool:
        return self in self.terminal_states()
