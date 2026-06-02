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


class ScientificOriginKind(str, Enum):
    computed = "computed"
    experimental = "experimental"
    estimated = "estimated"


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
    ancillary = "ancillary"


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
