"""Assemble a ``NetworkPDepUploadRequest`` from a parsed Arkane PDep run.

Mirrors the assembly approach of ``scripts/arc_ingestion/builder.py`` (build
plain dicts, then ``model_validate`` into the Pydantic request), but targets
the unified pressure-dependent-network schema instead of the single-reaction
computed-reaction bundle.

Data sourcing (per field):

- Identity (SMILES / multiplicity)        <- ``input.py`` + ``Data/<x>.py``
- Geometry (XYZ)                          <- ``supporting_information.csv``
- SP electronic energy (MRCI+Davidson)    <- CSV ``Electronic energy (J/mol)``
- Freq frequencies + ZPE                  <- CSV (unscaled) + E0-Eelec
- Hindered rotor presence (N2H4)          <- ``output.py`` conformer + ``Data``
- Topology (states / channels / solve)    <- ``input.py`` network/pdep
- Fitted Chebyshev k(T,P) per channel     <- ``output.py`` pdepreaction blocks

The three parser gotchas are handled here (see ``units.py``):

1. Grain size kcal/mol -> cm^-1 (``kcal_mol_to_cm_inv``).
2. Chebyshev pressure domain: ``output.py`` labels it *bar* (matching
   ``input.py``); ``chem.inp`` prints the same domain in *atm*. We read
   ``output.py`` and take bar directly -- no atm->bar conversion is applied,
   and the Chebyshev coefficients are unit-invariant under that relabelling.
3. ``Log(...)`` paths re-rooted at ``<run_dir>/Data`` (``resolve_log_path``).
"""

from __future__ import annotations

import base64
import hashlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from scripts.arc_ingestion.arkane_parser import map_a_units

from .arkane_pdep_parser import (
    ArkaneConformer,
    ChebyshevFit,
    DataFile,
    PlogFit,
    SupportingInfo,
    parse_all_conformers,
    parse_data_file,
    parse_input_file,
    parse_pdep_arrhenius_reactions,
    parse_pdep_reactions_with_skips,
    parse_supporting_information,
    resolve_log_path,
)
from .units import atm_to_bar, ea_to_kj_mol, j_mol_to_hartree, kcal_mol_to_cm_inv

# Arkane declares the ESS per level-of-theory as a lowercase token
# (``software="gaussian"``). Canonicalise that token to a software_release ref
# rather than hardcoding one program for the whole run: a cross-LOT run (e.g.
# CCSD(T)-F12 on ORCA) must carry its own declared software, not Gaussian.
#
# Entries reproduce the canonical release each token denotes. The Gaussian
# token carries version "09": absent an explicit version in the LOT
# declaration, that is the release this project's Arkane runs use (and it is
# what the ESS log headers report). Unknown tokens fall back to the raw token
# as the name (still derived from the run, never a fixed program).
_SOFTWARE_REGISTRY: dict[str, dict] = {
    "gaussian": {"name": "Gaussian", "version": "09"},
    "gaussian09": {"name": "Gaussian", "version": "09"},
    "gaussian16": {"name": "Gaussian", "version": "16"},
    "g09": {"name": "Gaussian", "version": "09"},
    "g16": {"name": "Gaussian", "version": "16"},
    "molpro": {"name": "Molpro"},
    "orca": {"name": "ORCA"},
    "qchem": {"name": "Q-Chem"},
    "psi4": {"name": "Psi4"},
    "terachem": {"name": "TeraChem"},
    "cfour": {"name": "CFOUR"},
}
_ARKANE = {"name": "Arkane", "version": "3.2.0"}


def _software_release(token: str | None) -> dict | None:
    """Map an Arkane software token to a software_release ref dict.

    Returns ``None`` when no software was declared (so the caller omits the
    optional ``software_release`` rather than inventing a program). A token not
    in the registry is passed through as the release ``name`` verbatim.
    """
    if not token:
        return None
    key = token.strip().lower()
    entry = _SOFTWARE_REGISTRY.get(key)
    if entry is not None:
        return dict(entry)
    return {"name": token.strip()}


@dataclass
class _ParsedRun:
    run_dir: Path
    inp: object
    conformers: dict[str, ArkaneConformer]
    fits: list[ChebyshevFit]
    pdep_skips: list
    csv: dict[str, SupportingInfo]
    data_files: dict[str, DataFile]


@dataclass
class GapReport:
    """What did and did not parse from a run (the deliverable gap trail)."""

    species_built: list[str] = field(default_factory=list)
    species_skipped: list[tuple[str, str]] = field(default_factory=list)
    species_with_statmech: list[str] = field(default_factory=list)
    ts_built: list[str] = field(default_factory=list)
    ts_stub_no_geometry: list[str] = field(default_factory=list)
    channels_built: int = 0
    channels_unmapped: list[tuple[str, str]] = field(default_factory=list)
    channels_duplicate: list[tuple[str, str]] = field(default_factory=list)
    pdep_non_chebyshev: list[tuple[str, str, str]] = field(default_factory=list)
    micro_reactions: int = 0
    torsions_emitted: list[str] = field(default_factory=list)
    unstorable_fields: list[str] = field(default_factory=list)
    followups: list[str] = field(default_factory=list)


def _fit_bounds_bar_kelvin(fit: ChebyshevFit) -> tuple[float, float]:
    """Return (pmin_bar, pmax_bar), honouring the fit's labelled pressure unit.

    Nit (Fable): do not assume bar. ``output.py`` labels this run's Chebyshev
    domain in bar; the generic path must convert atm and reject anything else,
    and must verify the temperature axis is Kelvin.
    """
    if fit.temperature_units not in ("K", "kelvin"):
        raise ValueError(
            f"Chebyshev temperature units must be K, got {fit.temperature_units!r}."
        )
    if fit.pressure_units == "bar":
        return fit.pmin_value, fit.pmax_value
    if fit.pressure_units == "atm":
        return atm_to_bar(fit.pmin_value), atm_to_bar(fit.pmax_value)
    raise ValueError(
        f"Chebyshev pressure units must be bar or atm, got {fit.pressure_units!r}."
    )


def _resolve_data_file(run_dir: Path, data_file: str) -> Path | None:
    """Resolve a declared species/TS ``Data/<x>.py`` data-file onto disk.

    Mirrors :func:`resolve_log_path`'s re-rooting idea for the *data-file*
    reference itself: many runs record an absolute author-home path
    (``/home/.../PES.../data/NN.py``) that does not exist on this box. Tried in
    order: the path as given relative to ``run_dir``; the path as an existing
    absolute path; then the basename under ``<run_dir>/Data`` or
    ``<run_dir>/data``. Returns ``None`` when nothing resolves.
    """
    as_given = run_dir / data_file
    if as_given.exists():
        return as_given
    absolute = Path(data_file)
    if absolute.is_absolute() and absolute.exists():
        return absolute
    base = Path(data_file).name
    for sub in ("Data", "data"):
        candidate = run_dir / sub / base
        if candidate.exists():
            return candidate
    return None


def _load_run(run_dir: Path) -> _ParsedRun:
    run_dir = Path(run_dir)
    inp = parse_input_file((run_dir / "input.py").read_text())
    out_text = (run_dir / "output.py").read_text()
    conformers = parse_all_conformers(out_text)
    fits, pdep_skips = parse_pdep_reactions_with_skips(out_text)
    csv_path = run_dir / "supporting_information.csv"
    csv = parse_supporting_information(csv_path) if csv_path.exists() else {}

    # Only ``.py`` data-files carry the regex/Log statmech this parser reads;
    # ``.yml`` (Arkane YAML statmech) references are recognised at the input
    # level but not parsed here (recorded fail-loud in the builder instead).
    data_files: dict[str, DataFile] = {}
    for label, sp in inp.species.items():
        if sp.data_file and sp.data_file.endswith(".py"):
            p = _resolve_data_file(run_dir, sp.data_file)
            if p is not None:
                data_files[label] = parse_data_file(p.read_text())
    for label, ts in inp.transition_states.items():
        if ts.data_file and ts.data_file.endswith(".py"):
            p = _resolve_data_file(run_dir, ts.data_file)
            if p is not None:
                data_files[label] = parse_data_file(p.read_text())
    return _ParsedRun(run_dir, inp, conformers, fits, pdep_skips, csv, data_files)


def _lot_opt(inp) -> dict:
    return {
        "method": inp.opt_method or "wb97xd",
        "basis": inp.opt_basis or "def2tzvp",
    }


def _lot_energy(inp) -> dict:
    return {
        "method": inp.energy_method or "MRCI+Davidson",
        "basis": inp.energy_basis or "aug-cc-pV(T+d)Z",
    }


def _artifact(path: Path) -> dict | None:
    if not path.exists():
        return None
    content = path.read_bytes()
    return {
        "kind": "output_log",
        "filename": path.name,
        "content_base64": base64.b64encode(content).decode("ascii"),
        "sha256": hashlib.sha256(content).hexdigest(),
        "bytes": len(content),
    }


def _build_statmech(
    inp,
    info: SupportingInfo,
    conf: ArkaneConformer | None,
    scan_key: str | None,
    *,
    freq_key: str | None,
    sp_key: str | None,
) -> dict | None:
    """Build a ``StatmechInBundle`` dict for one reactive species.

    Carries the statistical-mechanics scalars parsed from the CSV/output.py
    (external symmetry, optical isomers, point group, rotor kind, treatment,
    frequency scale factor) and links source calculations owned by THIS
    species. N2H4's hindered rotor is emitted as a torsion referencing the
    species-local ``scan``-type calculation.
    """
    statmech: dict = {"scientific_origin": "computed"}
    if info.symmetry_number is not None:
        statmech["external_symmetry"] = info.symmetry_number
    if info.optical_isomers is not None:
        statmech["optical_isomers"] = info.optical_isomers
    if info.point_group:
        statmech["point_group"] = info.point_group
    # Principal rotational constants (cm^-1), stored in source-provided order
    # (conventionally descending A >= B >= C). NULLs for absent entries.
    rot = info.rotational_constants_cm_inv or []
    if len(rot) >= 1:
        statmech["rotational_constant_a_cm1"] = rot[0]
    if len(rot) >= 2:
        statmech["rotational_constant_b_cm1"] = rot[1]
    if len(rot) >= 3:
        statmech["rotational_constant_c_cm1"] = rot[2]
    if conf is not None:
        if conf.is_linear is not None:
            statmech["is_linear"] = conf.is_linear
        if conf.rigid_rotor_kind:
            statmech["rigid_rotor_kind"] = conf.rigid_rotor_kind
        if conf.statmech_treatment:
            statmech["statmech_treatment"] = conf.statmech_treatment
    # Arkane statmech uses projected frequencies (external/torsional modes
    # removed from the harmonic list before partition functions).
    statmech["uses_projected_frequencies"] = True

    if inp.freq_scale_factor is not None:
        fsf: dict = {
            "level_of_theory": _lot_opt(inp),
            "scale_kind": "fundamental",
            "value": inp.freq_scale_factor,
        }
        # The scale factor is a property of the geometry/frequency LOT, so it is
        # attributed to that LOT's software (opt/freq software), derived from the
        # run rather than assumed to be Gaussian.
        opt_sw = _software_release(inp.opt_software)
        if opt_sw is not None:
            fsf["software"] = {"name": opt_sw["name"]}
        statmech["freq_scale_factor"] = fsf

    source_calcs: list[dict] = []
    if freq_key:
        source_calcs.append({"calculation_key": freq_key, "role": "freq"})
    if sp_key:
        source_calcs.append({"calculation_key": sp_key, "role": "sp"})
    if source_calcs:
        statmech["source_calculations"] = source_calcs

    # Torsions: one per output.py HinderedRotor, referencing the species's own
    # scan calculation (present only when a scanLog existed in Data/<x>.py).
    if conf and conf.hindered_rotors and scan_key:
        torsions = []
        for i, hr in enumerate(conf.hindered_rotors):
            torsions.append(
                {
                    "torsion_index": i + 1,
                    "symmetry_number": hr.symmetry_number,
                    "treatment_kind": hr.treatment,  # 'hindered_rotor'/'free_rotor'
                    "dimension": 1,
                    "source_scan_calculation_key": scan_key,
                }
            )
        statmech["torsions"] = torsions

    # Nothing worth persisting beyond scientific_origin/projected-flag?
    meaningful = any(
        k in statmech
        for k in (
            "external_symmetry",
            "optical_isomers",
            "point_group",
            "rigid_rotor_kind",
            "rotational_constant_a_cm1",
            "source_calculations",
            "torsions",
        )
    )
    return statmech if meaningful else None


def _state_key(labels: list[str]) -> str:
    return "st_" + "_".join(sorted(labels))


def _multiset_key(labels: list[str]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(Counter(labels).items()))


def build_network_pdep_request(run_dir: Path, *, include_artifacts: bool = False):
    """Build a validated ``NetworkPDepUploadRequest`` from an Arkane run dir.

    :param run_dir: The ``Final_MRCI_PDep`` directory (contains ``input.py``,
        ``output.py``, ``supporting_information.csv``, and ``Data/``).
    :param include_artifacts: When True, attach ESS log files (sp.out /
        freq.out) as base64 artifacts on each calculation. Off by default so
        large files (e.g. the 3 MB hindered-rotor ``scan.out``) are not read.
    :returns: A ``NetworkPDepUploadRequest`` (schema-validated).
    """
    request_dict, gap = build_network_pdep_payload(
        run_dir, include_artifacts=include_artifacts
    )
    # Imported lazily so the parser modules stay importable without the app.
    from app.schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest

    try:
        return NetworkPDepUploadRequest.model_validate(request_dict)
    except ValueError as exc:
        # Fail loud when the network is invalid *because* declared species were
        # dropped (e.g. unresolved ``.yml`` statmech files): name them instead
        # of surfacing the cryptic downstream "species never referenced" error.
        dropped = [f"{label} ({reason})" for label, reason in gap.species_skipped]
        if dropped:
            raise ValueError(
                "Network cannot be built: "
                f"{len(dropped)} declared species were dropped and truncated "
                f"the network -> {dropped}. Resolve or remove them (and any "
                "channels/reactions that reference them). "
                f"Underlying validation error: {exc}"
            ) from exc
        raise


def build_network_pdep_payload(
    run_dir: Path, *, include_artifacts: bool = False
) -> tuple[dict, GapReport]:
    """Build the request as a plain dict plus a :class:`GapReport`.

    Split out from :func:`build_network_pdep_request` so callers (the CLI and
    tests) can inspect coverage without importing the app / validating.
    """
    run = _load_run(run_dir)
    inp = run.inp
    gap = GapReport()

    # Per-calc software is derived from the run's declared level(s) of theory,
    # not hardcoded: opt/freq/scan run at the geometry/frequency LOT's software,
    # the single point at the energy LOT's software. For a bare single-LOT run
    # both are the same declared program.
    opt_software = _software_release(inp.opt_software)
    energy_software = _software_release(inp.energy_software)

    # ------------------------------------------------------------------
    # Species (reactive: full evidence; bath gas: identity-only)
    # ------------------------------------------------------------------
    species_payloads: list[dict] = []
    # Track the sp/freq calc keys per species for solve source_calculations.
    species_sp_key: dict[str, str] = {}
    species_freq_key: dict[str, str] = {}

    for label, sp in inp.species.items():
        if not sp.reactive:
            # Bath gas: identity only, referenced by solve.bath_gas.
            if sp.smiles is None:
                gap.species_skipped.append((label, "no SMILES for bath gas"))
                continue
            species_payloads.append(
                {
                    "key": label,
                    "species_entry": {
                        "smiles": sp.smiles,
                        "charge": 0,
                        "multiplicity": sp.spin_multiplicity or 1,
                    },
                    "label": label,
                }
            )
            gap.species_built.append(label)
            continue

        info = run.csv.get(label)
        data = run.data_files.get(label)
        conf = run.conformers.get(label)
        if info is None or info.xyz_text is None:
            if sp.data_file and sp.data_file.endswith(".yml"):
                # Recognised but not parsed: name it loudly so the truncated
                # network is diagnosable rather than surfacing as a confusing
                # downstream orphan-species schema error.
                reason = (
                    f"YAML statmech data-file {sp.data_file!r} not parsed "
                    "(.yml statmech unsupported); species dropped"
                )
            else:
                reason = "no CSV geometry/scalars"
            gap.species_skipped.append((label, reason))
            continue

        mult = (
            (data.spin_multiplicity if data else None)
            or (conf.spin_multiplicity if conf else None)
            or 1
        )
        geom_key = f"{label}_geom"
        opt_key = f"{label}_opt"

        opt_calc: dict = {
            "key": opt_key,
            "type": "opt",
            "software_release": opt_software,
            "level_of_theory": _lot_opt(inp),
            "opt_converged": True,
        }
        conformer = {
            "key": f"{label}_conf",
            "geometry": {"key": geom_key, "xyz_text": info.xyz_text},
            "calculation": opt_calc,
        }
        if conf is not None:
            conformer["label"] = conf.label

        calculations: list[dict] = []

        # Freq calc (skip for monoatomic species with no vibrations).
        if info.frequencies_cm_inv:
            zpe_ha = None
            if info.e0_zpe_j_mol is not None and info.electronic_energy_j_mol is not None:
                zpe_ha = j_mol_to_hartree(
                    info.e0_zpe_j_mol - info.electronic_energy_j_mol
                )
            freq_key = f"{label}_freq"
            species_freq_key[label] = freq_key
            freq_calc: dict = {
                "key": freq_key,
                "type": "freq",
                "geometry_key": geom_key,
                "software_release": opt_software,
                "level_of_theory": _lot_opt(inp),
                "freq_n_imag": info.n_imag,
                "freq_frequencies_cm1": info.frequencies_cm_inv,
            }
            if zpe_ha is not None:
                freq_calc["freq_zpe_hartree"] = zpe_ha
            if include_artifacts and data and data.frequencies_log:
                art = _artifact(resolve_log_path(data.frequencies_log, run.run_dir))
                if art:
                    freq_calc["artifacts"] = [art]
            calculations.append(freq_calc)

        # SP calc (MRCI+Davidson electronic energy).
        if info.electronic_energy_j_mol is not None:
            sp_key = f"{label}_sp"
            species_sp_key[label] = sp_key
            sp_calc: dict = {
                "key": sp_key,
                "type": "sp",
                "geometry_key": geom_key,
                "software_release": energy_software,
                "level_of_theory": _lot_energy(inp),
                "sp_electronic_energy_hartree": j_mol_to_hartree(
                    info.electronic_energy_j_mol
                ),
            }
            if include_artifacts and data and data.energy_log:
                art = _artifact(resolve_log_path(data.energy_log, run.run_dir))
                if art:
                    sp_calc["artifacts"] = [art]
            calculations.append(sp_calc)

        # Hindered-rotor scan calc (N2H4 only, from Data rotors + output.py).
        scan_key: str | None = None
        if data and data.scan_logs:
            scan_key = f"{label}_scan"
            scan_calc: dict = {
                "key": scan_key,
                "type": "scan",
                "geometry_key": geom_key,
                "software_release": opt_software,
                "level_of_theory": _lot_opt(inp),
            }
            if include_artifacts:
                art = _artifact(resolve_log_path(data.scan_logs[0], run.run_dir))
                if art:
                    scan_calc["artifacts"] = [art]
            calculations.append(scan_calc)

        # Statmech interpretation (PR #19 added NetworkSpeciesIn.statmech).
        statmech = _build_statmech(
            inp,
            info,
            conf,
            scan_key,
            freq_key=species_freq_key.get(label),
            sp_key=species_sp_key.get(label),
        )
        if statmech is not None and statmech.get("torsions"):
            gap.torsions_emitted.append(label)

        species_dict: dict = {
            "key": label,
            "species_entry": {
                "smiles": sp.smiles,
                "charge": 0,
                "multiplicity": mult,
            },
            "label": label,
            "conformers": [conformer],
            "calculations": calculations,
        }
        if statmech is not None:
            species_dict["statmech"] = statmech
            gap.species_with_statmech.append(label)
        species_payloads.append(species_dict)
        gap.species_built.append(label)

    built_species = {p["key"] for p in species_payloads}

    # ------------------------------------------------------------------
    # Micro reactions (all 6 elementary steps; TS5/TS6 are stubs)
    # ------------------------------------------------------------------
    micro_reactions: list[dict] = []
    rxn_key_by_index: dict[int, str] = {}
    for i, rxn in enumerate(inp.reactions):
        if not (set(rxn.reactants) <= built_species and set(rxn.products) <= built_species):
            continue
        key = f"rxn{i + 1}"
        rxn_key_by_index[i] = key
        micro_reactions.append(
            {
                "key": key,
                "reversible": True,
                "reactants": [{"species_key": s} for s in rxn.reactants],
                "products": [{"species_key": s} for s in rxn.products],
                "label": rxn.label or None,
            }
        )
    gap.micro_reactions = len(micro_reactions)

    # ------------------------------------------------------------------
    # Transition states (TS1-TS4 full ab-initio; TS5/TS6 -> no TS row)
    # ------------------------------------------------------------------
    transition_states: list[dict] = []
    for i, rxn in enumerate(inp.reactions):
        if i not in rxn_key_by_index:
            continue
        ts_label = rxn.transition_state
        if not ts_label:
            continue
        ts_meta = inp.transition_states.get(ts_label)
        if not (rxn.has_ab_initio_ts and ts_meta and ts_meta.data_file):
            if ts_label:
                gap.ts_stub_no_geometry.append(ts_label)
            continue
        info = run.csv.get(ts_label)
        data = run.data_files.get(ts_label)
        if info is None or info.xyz_text is None:
            gap.ts_stub_no_geometry.append(ts_label)
            continue

        mult = (data.spin_multiplicity if data else None) or 1
        geom_key = f"{ts_label}_geom"
        primary = {
            "key": f"{ts_label}_opt",
            "type": "opt",
            "software_release": opt_software,
            "level_of_theory": _lot_opt(inp),
            "opt_converged": True,
        }
        ts_calcs: list[dict] = []
        # Freq (imaginary mode expected).
        imag = next((f for f in info.frequencies_cm_inv if f < 0), None)
        zpe_ha = None
        if info.e0_zpe_j_mol is not None and info.electronic_energy_j_mol is not None:
            zpe_ha = j_mol_to_hartree(
                info.e0_zpe_j_mol - info.electronic_energy_j_mol
            )
        ts_freq_key = f"{ts_label}_freq"
        freq_calc = {
            "key": ts_freq_key,
            "type": "freq",
            "geometry_key": geom_key,
            "software_release": opt_software,
            "level_of_theory": _lot_opt(inp),
            "freq_n_imag": info.n_imag,
            "freq_frequencies_cm1": info.frequencies_cm_inv,
        }
        if imag is not None:
            freq_calc["freq_imag_freq_cm1"] = imag
        if zpe_ha is not None:
            freq_calc["freq_zpe_hartree"] = zpe_ha
        if include_artifacts and data and data.frequencies_log:
            art = _artifact(resolve_log_path(data.frequencies_log, run.run_dir))
            if art:
                freq_calc["artifacts"] = [art]
        ts_calcs.append(freq_calc)
        # SP (barrier energy).
        ts_sp_key = f"{ts_label}_sp"
        if info.electronic_energy_j_mol is not None:
            sp_calc = {
                "key": ts_sp_key,
                "type": "sp",
                "geometry_key": geom_key,
                "software_release": energy_software,
                "level_of_theory": _lot_energy(inp),
                "sp_electronic_energy_hartree": j_mol_to_hartree(
                    info.electronic_energy_j_mol
                ),
            }
            if include_artifacts and data and data.energy_log:
                art = _artifact(resolve_log_path(data.energy_log, run.run_dir))
                if art:
                    sp_calc["artifacts"] = [art]
            ts_calcs.append(sp_calc)

        transition_states.append(
            {
                "key": ts_label.lower(),
                "micro_reaction_key": rxn_key_by_index[i],
                "charge": 0,
                "multiplicity": mult,
                "geometry": {"key": geom_key, "xyz_text": info.xyz_text},
                "calculation": primary,
                "calculations": ts_calcs,
                "label": ts_label,
            }
        )
        gap.ts_built.append(ts_label)

    # ------------------------------------------------------------------
    # States (wells + bimolecular reactant channels)
    # ------------------------------------------------------------------
    states: list[dict] = []
    state_key_by_multiset: dict[tuple, str] = {}
    state_kind: dict[str, str] = {}

    def _add_state(labels: list[str], kind: str) -> str | None:
        if not all(s in built_species for s in labels):
            return None
        key = _state_key(labels)
        ms = _multiset_key(labels)
        if ms in state_key_by_multiset:
            return state_key_by_multiset[ms]
        counts = Counter(labels)
        participants = [
            {"species_key": s, "stoichiometry": c} for s, c in sorted(counts.items())
        ]
        states.append({"key": key, "kind": kind, "participants": participants})
        state_key_by_multiset[ms] = key
        state_kind[key] = kind
        return key

    for iso in inp.isomers:
        _add_state([iso], "well")
    for channel in inp.reactant_channels:
        _add_state(list(channel), "bimolecular")

    def _lookup_state(labels: list[str]) -> str | None:
        return state_key_by_multiset.get(_multiset_key(labels))

    # ------------------------------------------------------------------
    # Channels + channel_kinetics (one per fitted pdepreaction)
    # ------------------------------------------------------------------
    _KIND = {
        ("well", "well"): "isomerization",
        ("bimolecular", "well"): "association",
        ("well", "bimolecular"): "dissociation",
        ("bimolecular", "bimolecular"): "exchange",
    }
    channels: list[dict] = []
    channel_kinetics: list[dict] = []
    seen_channel_pairs: set[tuple[str, str]] = set()

    for fit in run.fits:
        src = _lookup_state(fit.reactants)
        snk = _lookup_state(fit.products)
        if src is None or snk is None or src == snk:
            gap.channels_unmapped.append(
                ("+".join(fit.reactants), "+".join(fit.products))
            )
            continue
        pair = (src, snk)
        if pair in seen_channel_pairs:
            gap.channels_duplicate.append(pair)
            continue
        seen_channel_pairs.add(pair)
        pmin_bar, pmax_bar = _fit_bounds_bar_kelvin(fit)  # honours atm/bar; K
        kind = _KIND.get((state_kind[src], state_kind[snk]), "isomerization")
        channels.append(
            {"source_state_key": src, "sink_state_key": snk, "kind": kind}
        )
        channel_kinetics.append(
            {
                "source_state_key": src,
                "sink_state_key": snk,
                "model_kind": "chebyshev",
                "chebyshev": {
                    "n_temperature": fit.n_temperature,
                    "n_pressure": fit.n_pressure,
                    "coefficients": fit.coefficients,
                },
                "tmin_k": fit.tmin_value,
                "tmax_k": fit.tmax_value,
                "pmin_bar": pmin_bar,
                "pmax_bar": pmax_bar,
                "rate_units": map_a_units(fit.kunits),
                "pressure_units": "bar",  # normalised to bar above
                "temperature_units": "kelvin",
                "stores_log10_k": True,
            }
        )
    gap.channels_built = len(channels)
    for sk in run.pdep_skips:
        gap.pdep_non_chebyshev.append(
            ("+".join(sk.reactants), "+".join(sk.products), sk.reason)
        )

    # ------------------------------------------------------------------
    # Solve
    # ------------------------------------------------------------------
    pd = inp.pressure_dependence
    solve: dict = {
        "tmin_k": pd.tmin_k if pd else 300.0,
        "tmax_k": pd.tmax_k if pd else 2000.0,
        "pmin_bar": pd.pmin_bar if pd else 0.01,
        "pmax_bar": pd.pmax_bar if pd else 100.0,
        "me_method": pd.method if pd else None,
        "interpolation_model": (pd.interpolation_model if pd else None),
        "workflow_tool_release": _ARKANE,
        "channel_kinetics": channel_kinetics,
    }
    if pd and pd.grain_size_units == "kcal/mol":
        solve["grain_size_cm_inv"] = kcal_mol_to_cm_inv(pd.grain_size_value)
    elif pd and pd.grain_size_units:
        # Fail-loud: only kcal/mol is convertible here.
        gap.followups.append(
            f"grain size units {pd.grain_size_units!r} not converted "
            f"(only kcal/mol supported); grain_size_cm_inv omitted"
        )
    if pd and pd.grain_count:
        solve["grain_count"] = pd.grain_count

    # Bath gas (only components that were built as species).
    bath = [
        {"species_key": label, "mole_fraction": frac}
        for label, frac in inp.bath_gas.items()
        if label in built_species
    ]
    if bath:
        solve["bath_gas"] = bath

    if inp.energy_transfer:
        et = inp.energy_transfer
        solve["energy_transfer"] = {
            "model": et.model,
            "alpha0_cm_inv": et.alpha0_cm_inv,
            "t_exponent": et.t_exponent,
            "t_ref_k": et.t_ref_k,
        }

    # Source calculations: species sp -> well_energy, freq -> well_freq;
    # TS sp -> barrier_energy, freq -> barrier_freq.
    source_calcs: list[dict] = []
    for label, key in species_sp_key.items():
        source_calcs.append({"calculation_key": key, "role": "well_energy"})
    for label, key in species_freq_key.items():
        source_calcs.append({"calculation_key": key, "role": "well_freq"})
    for ts in transition_states:
        for calc in ts["calculations"]:
            if calc["type"] == "sp":
                source_calcs.append(
                    {"calculation_key": calc["key"], "role": "barrier_energy"}
                )
            elif calc["type"] == "freq":
                source_calcs.append(
                    {"calculation_key": calc["key"], "role": "barrier_freq"}
                )
    if source_calcs:
        solve["source_calculations"] = source_calcs

    network_name = inp.network_label or "pdep_network"
    request_dict = {
        "name": network_name,
        "description": (
            f"Pressure-dependent network '{network_name}' "
            f"({inp.energy_method or 'ab-initio'}//{inp.opt_method or 'DFT'}), "
            "parsed from an Arkane run by scripts/pdep_ingestion."
        ),
        "workflow_tool_release": _ARKANE,
        "species": species_payloads,
        "transition_states": transition_states,
        "micro_reactions": micro_reactions,
        "states": states,
        "channels": channels,
        "solve": solve,
    }
    return request_dict, gap


# ---------------------------------------------------------------------------
# Dual-form build: one network carrying BOTH Chebyshev and PLOG kinetics
# ---------------------------------------------------------------------------


def _plog_pressure_to_bar(value: float, units: str) -> float:
    """Convert a PLOG pressure to bar.

    Unlike Chebyshev, PLOG pressures are physical values (the fit's rate is
    evaluated at these pressures), so an atm label must be converted, not
    relabelled. This run labels them 'bar' -> pass-through.
    """
    if units == "bar":
        return value
    if units == "atm":
        return atm_to_bar(value)
    raise ValueError(f"Unexpected PLOG pressure units {units!r} (expected bar or atm).")


def _plog_channel_kinetics_entry(fit: PlogFit, src: str, snk: str) -> dict:
    """Build one ``model_kind=plog`` channel_kinetics dict from a parsed fit."""
    entries: list[dict] = []
    for e in fit.entries:
        entries.append(
            {
                "pressure_bar": _plog_pressure_to_bar(e.pressure_value, fit.pressure_units),
                "a": e.a_value,
                "a_units": map_a_units(e.a_units),
                "n": e.n,
                "ea_kj_mol": ea_to_kj_mol(e.ea_value, e.ea_units),
            }
        )
    pressures_bar = [en["pressure_bar"] for en in entries]
    # All terms of one PLOG fit share molecularity, hence one rate-unit token.
    # Assert that invariant rather than silently trusting the first entry: a
    # fit whose per-pressure A-units disagree is malformed and must fail loud.
    rate_unit_tokens = {map_a_units(e.a_units) for e in fit.entries}
    if len(rate_unit_tokens) != 1:
        raise ValueError(
            f"PLOG fit {src}->{snk} has inconsistent A-unit tokens across its "
            f"pressure entries: {sorted(rate_unit_tokens)}."
        )
    (rate_units,) = rate_unit_tokens
    entry: dict = {
        "source_state_key": src,
        "sink_state_key": snk,
        "model_kind": "plog",
        "plog": {"entries": entries},
        "pmin_bar": min(pressures_bar),
        "pmax_bar": max(pressures_bar),
        "rate_units": rate_units,
        "pressure_units": "bar",
    }
    if fit.tmin_value is not None and fit.tmax_value is not None:
        if fit.temperature_units not in ("K", "kelvin", None):
            raise ValueError(
                f"PLOG temperature units must be K, got {fit.temperature_units!r}."
            )
        entry["tmin_k"] = fit.tmin_value
        entry["tmax_k"] = fit.tmax_value
        entry["temperature_units"] = "kelvin"
    return entry


def build_dual_form_payload(
    cheb_run_dir: Path,
    plog_run_dir: Path,
    *,
    include_artifacts: bool = False,
) -> tuple[dict, GapReport]:
    """Build a dual-form request dict (Chebyshev + PLOG) from two Arkane runs.

    The Chebyshev run is the base: it defines topology (species, states,
    channels), the solve, and the 21 Chebyshev ``channel_kinetics`` entries
    exactly as :func:`build_network_pdep_payload`. The PLOG run is a SECOND
    Arkane fit of the SAME network (only ``interpolationModel`` differs); its
    ``pdepreaction`` / ``PDepArrhenius`` blocks are parsed into PLOG
    ``channel_kinetics`` entries attached to the same channels by
    ``(source_state_key, sink_state_key)``.

    A topology-match check enforces that the two runs are the same network:
    every PLOG fit must map (via the same multiset-of-species -> state-key
    mapping the Chebyshev build uses) to a channel that the Chebyshev build
    produced, and the two channel sets must be identical. Any mismatch raises
    ``ValueError`` rather than silently dropping or misaligning a channel.
    """
    request_dict, gap = build_network_pdep_payload(
        cheb_run_dir, include_artifacts=include_artifacts
    )
    solve = request_dict.get("solve")
    if not solve or not solve.get("channel_kinetics"):
        raise ValueError(
            "Chebyshev run produced no solve/channel_kinetics; cannot attach PLOG."
        )
    cheb_kinetics: list[dict] = solve["channel_kinetics"]
    cheb_pairs = {
        (ck["source_state_key"], ck["sink_state_key"]) for ck in cheb_kinetics
    }

    # State lookup keyed by the multiset of participant species labels, rebuilt
    # from the Chebyshev payload's states so PLOG fits attach to the same keys.
    state_lookup: dict[tuple, str] = {}
    for st in request_dict["states"]:
        labels: list[str] = []
        for p in st["participants"]:
            labels.extend([p["species_key"]] * int(p.get("stoichiometry", 1)))
        state_lookup[_multiset_key(labels)] = st["key"]

    plog_fits = parse_pdep_arrhenius_reactions(
        (Path(plog_run_dir) / "output.py").read_text()
    )

    plog_kinetics: list[dict] = []
    plog_pairs: set[tuple[str, str]] = set()
    unmapped: list[tuple[str, str]] = []
    for fit in plog_fits:
        src = state_lookup.get(_multiset_key(fit.reactants))
        snk = state_lookup.get(_multiset_key(fit.products))
        if src is None or snk is None or src == snk:
            unmapped.append(("+".join(fit.reactants), "+".join(fit.products)))
            continue
        # Duplicate directed-pair guard (mirrors the Chebyshev channel build):
        # two PLOG fits collapsing onto the same (source, sink) would otherwise
        # emit two plog rows for one channel and slip past the set-based
        # topology check below.
        if (src, snk) in plog_pairs:
            gap.channels_duplicate.append((src, snk))
            continue
        plog_pairs.add((src, snk))
        plog_kinetics.append(_plog_channel_kinetics_entry(fit, src, snk))

    # --- Topology-match check: same network or STOP ---
    if unmapped or plog_pairs != cheb_pairs:
        missing_in_plog = cheb_pairs - plog_pairs
        extra_in_plog = plog_pairs - cheb_pairs
        raise ValueError(
            "PLOG run topology does not match the Chebyshev run "
            f"(cheb channels={len(cheb_pairs)}, plog channels={len(plog_pairs)}). "
            f"PLOG fits that did not map to a Chebyshev channel: {unmapped}. "
            f"Chebyshev channels with no PLOG fit: {sorted(missing_in_plog)}. "
            f"PLOG channels absent from Chebyshev: {sorted(extra_in_plog)}. "
            "The two runs must be the same network (same set of "
            "(source, sink) channel pairs)."
        )

    solve["channel_kinetics"] = cheb_kinetics + plog_kinetics
    return request_dict, gap


def build_dual_form_request(
    cheb_run_dir: Path,
    plog_run_dir: Path,
    *,
    include_artifacts: bool = False,
):
    """Build a validated dual-form ``NetworkPDepUploadRequest`` from two runs.

    See :func:`build_dual_form_payload`. The result carries both a Chebyshev
    and a PLOG ``channel_kinetics`` entry per channel (2N total for N channels)
    and validates against the relaxed
    ``(source_state_key, sink_state_key, model_kind)`` uniqueness rule.
    """
    request_dict, _ = build_dual_form_payload(
        cheb_run_dir, plog_run_dir, include_artifacts=include_artifacts
    )
    from app.schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest

    return NetworkPDepUploadRequest.model_validate(request_dict)
