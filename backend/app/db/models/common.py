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
    neb = "neb"
    conf = "conf"


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
    neb_image = "neb_image"


class CalculationDependencyRole(str, Enum):
    optimized_from = "optimized_from"
    freq_on = "freq_on"
    single_point_on = "single_point_on"
    arkane_source = "arkane_source"
    irc_start = "irc_start"
    irc_followup = "irc_followup"
    scan_parent = "scan_parent"
    neb_parent = "neb_parent"


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
    ``workflow_tool_release`` for that. Values are intentionally aligned
    with :class:`UploadJobKind` so the same token may be persisted in both
    places; ``other`` is added to cover submissions that do not map onto
    the async upload pipeline.
    """

    computed_reaction = "computed_reaction"
    conformer = "conformer"
    reaction = "reaction"
    kinetics = "kinetics"
    network = "network"
    network_pdep = "network_pdep"
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
