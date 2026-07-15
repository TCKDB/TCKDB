"""Wire-contract enum mirror for TCKDB upload payloads.

These enums mirror the backend DB enums in ``app.db.models.common``.
The package boundary forbids importing the backend, so the wire enums
live here as an independent module. A backend drift test
(``backend/tests/schemas/test_tckdb_schemas_enum_drift.py``) keeps the
``.value`` sets in lockstep with the backend; do not edit one side
without updating the other.

Only upload-facing enums are mirrored. Moderation, auth, submission,
upload-job, and read-only enums stay backend-side.
"""

from __future__ import annotations

from enum import Enum


class MoleculeKind(str, Enum):
    molecule = "molecule"
    pseudo = "pseudo"


class StationaryPointKind(str, Enum):
    minimum = "minimum"
    vdw_complex = "vdw_complex"


class ScientificOriginKind(str, Enum):
    computed = "computed"
    experimental = "experimental"
    estimated = "estimated"


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


class CalculationType(str, Enum):
    opt = "opt"
    freq = "freq"
    sp = "sp"
    irc = "irc"
    scan = "scan"
    path_search = "path_search"
    conf = "conf"


class PathSearchMethod(str, Enum):
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


class IRCDirection(str, Enum):
    forward = "forward"
    reverse = "reverse"
    both = "both"


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
    """Where a stored Cartesian Hessian matrix was obtained from."""

    parsed_fchk = "parsed_fchk"
    parsed_hess = "parsed_hess"
    parsed_log = "parsed_log"
    uploaded = "uploaded"
    derived = "derived"


class SCFStabilityStatus(str, Enum):
    stable = "stable"
    unstable = "unstable"
    stabilized = "stabilized"
    inconclusive = "inconclusive"


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
    multi_arrhenius = "multi_arrhenius"
    lindemann = "lindemann"
    troe = "troe"
    sri = "sri"
    plog = "plog"
    chebyshev = "chebyshev"


class KineticsDirection(str, Enum):
    """Direction a reaction-level kinetics fit describes (DR-0036)."""

    forward = "forward"
    reverse = "reverse"
    net = "net"


class TunnelingModel(str, Enum):
    """Tunneling correction applied to a rate coefficient (DR-0032)."""

    none = "none"
    wigner = "wigner"
    eckart = "eckart"
    sct = "sct"
    other = "other"


class SpinTreatment(str, Enum):
    """Spin treatment of the electronic-structure method (DR-0034)."""

    restricted = "restricted"
    unrestricted = "unrestricted"
    restricted_open = "restricted_open"
    unknown = "unknown"


class PressureContext(str, Enum):
    """What a rate coefficient means w.r.t. pressure (DR-0032).

    ``high_p_limit`` = k∞; ``apparent_at_pressure`` requires a
    ``pressure_bar``; ``pressure_dependent`` defers to an associated model.
    """

    high_p_limit = "high_p_limit"
    apparent_at_pressure = "apparent_at_pressure"
    pressure_dependent = "pressure_dependent"


class KineticsUncertaintyKind(str, Enum):
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


class LiteratureKind(str, Enum):
    article = "article"
    book = "book"
    thesis = "thesis"
    report = "report"
    dataset = "dataset"
    webpage = "webpage"


class StereoKind(str, Enum):
    unspecified = "unspecified"
    achiral = "achiral"
    enantiomer = "enantiomer"
    diastereomer = "diastereomer"
    ez_isomer = "ez_isomer"


class SpeciesEntryStateKind(str, Enum):
    ground = "ground"
    excited = "excited"


class NetworkChannelKind(str, Enum):
    isomerization = "isomerization"
    association = "association"
    dissociation = "dissociation"
    stabilization = "stabilization"
    exchange = "exchange"


class NetworkSolveCalculationRole(str, Enum):
    well_energy = "well_energy"
    barrier_energy = "barrier_energy"
    well_freq = "well_freq"
    barrier_freq = "barrier_freq"
    master_equation_run = "master_equation_run"
    fit_source = "fit_source"


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
    atom = "atom"
    bond = "bond"
    molecular = "molecular"
    zpe_scale = "zpe_scale"
    soc = "soc"
    other = "other"


class EnergyCorrectionApplicationRole(str, Enum):
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


__all__ = (
    "ActivationEnergyUnits",
    "AppliedCorrectionComponentKind",
    "ArrheniusAUnits",
    "ArtifactKind",
    "CalculationDependencyRole",
    "CalculationGeometryRole",
    "CalculationQuality",
    "CalculationType",
    "ConstraintKind",
    "CoordinateUnit",
    "EnergyCorrectionApplicationRole",
    "EnergyCorrectionSchemeKind",
    "EnergyUnit",
    "FrequencyScaleKind",
    "IRCDirection",
    "KineticsCalculationRole",
    "KineticsDirection",
    "KineticsModelKind",
    "KineticsUncertaintyKind",
    "LiteratureKind",
    "MeliusBacComponentKind",
    "MoleculeKind",
    "NetworkChannelKind",
    "NetworkSolveCalculationRole",
    "PathSearchMethod",
    "RigidRotorKind",
    "SCFStabilityStatus",
    "ScientificOriginKind",
    "SpeciesEntryStateKind",
    "StatmechCalculationRole",
    "StatmechTreatmentKind",
    "StationaryPointKind",
    "StereoKind",
    "ThermoCalculationRole",
    "TorsionTreatmentKind",
)
