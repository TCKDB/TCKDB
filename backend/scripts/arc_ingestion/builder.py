"""Build a ComputedReactionUploadRequest from extracted ARC run data.

Maps ARCRunData → TCKDB bundle schema, handling:
- Species identity and conformer payloads
- Calculation provenance (software, LOT, workflow tool)
- Thermo (NASA polynomials, tabulated Cp, H298, S298)
- Transition state with geometry and calculations
- Kinetics (modified Arrhenius from Arkane output)
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from app.chemistry.species import derive_term_symbol

from .arkane_conformer_parser import ArkaneConformer, parse_arkane_conformer_from_file
from .arkane_parser import (
    ArkaneKinetics,
    map_a_units,
    map_ea_units,
    parse_arkane_kinetics_from_file,
)
from .extractor import ARCRunData, SpeciesInfo, TSInfo


def _read_xyz_text(xyz_path: Path) -> str:
    """Read an XYZ file and return the raw text (stripped)."""
    return xyz_path.read_text().strip()


# Map common SMILES for monoatomic species to their element symbol
_MONOATOMIC_SMILES: dict[str, str] = {
    "[H]": "H", "[He]": "He",
    "[C]": "C", "[N]": "N", "[O]": "O", "[F]": "F", "[Ne]": "Ne",
    "[Si]": "Si", "[P]": "P", "[S]": "S", "[Cl]": "Cl", "[Ar]": "Ar",
    "[Br]": "Br", "[I]": "I",
}


def _monoatomic_xyz(smiles: str) -> str | None:
    """Generate a trivial XYZ block for a single atom at the origin.

    Returns None if the SMILES is not recognised as monoatomic.
    """
    # Try direct lookup first
    base = smiles.split(":")[0]  # strip any atom-map
    for pattern, symbol in _MONOATOMIC_SMILES.items():
        if base == pattern or base == pattern.rstrip("]") + "-]" or base == pattern.rstrip("]") + "+]":
            return f"1\nmonoatomic\n{symbol}  0.00000  0.00000  0.00000"
    # Fallback: parse [X] pattern
    import re
    m = re.fullmatch(r"\[([A-Z][a-z]?)[+-]?\d*\]", base)
    if m:
        symbol = m.group(1)
        return f"1\nmonoatomic\n{symbol}  0.00000  0.00000  0.00000"
    return None


def _make_artifact(path: Path, kind: str) -> dict:
    """Build an ArtifactIn dict for a calculation file.

    Reads the file, base64-encodes the content, and computes the SHA-256.
    The server handles storage — we just transport the content.
    """
    content = path.read_bytes()
    sha = hashlib.sha256(content).hexdigest()
    return {
        "kind": kind,
        "filename": path.name,
        "content_base64": base64.b64encode(content).decode("ascii"),
        "sha256": sha,
        "bytes": len(content),
    }


def _make_software_release(run: ARCRunData) -> dict:
    """Build a SoftwareReleaseRef dict."""
    parts = {"name": run.software_name}
    if run.software_version:
        parts["version"] = run.software_version
    if run.software_revision:
        parts["revision"] = run.software_revision
    return parts


def _make_workflow_tool_release(run: ARCRunData) -> dict:
    """Build a WorkflowToolReleaseRef dict for ARC."""
    parts = {"name": "ARC"}
    if run.arc_version and run.arc_version != "unknown":
        parts["version"] = run.arc_version
    if run.arc_git_commit:
        parts["git_commit"] = run.arc_git_commit
    return parts


def _make_analysis_software_release(run: ARCRunData) -> dict | None:
    """Build a SoftwareReleaseRef dict for the kinetics/thermo analysis code (RMG-Py/Arkane).

    Other possible values here: MESS, MultiWell, VariFlex, etc.
    Returns None if no version information is available.
    """
    if not run.rmg_git_commit:
        return None
    # Name is "Arkane" — the specific kinetics/thermo analysis module.
    # The git commit comes from the RMG-Py repo (Arkane lives there),
    # stored as revision since no separate version number is logged for these runs.
    return {"name": "Arkane", "revision": run.rmg_git_commit}


def _make_level_of_theory(lot) -> dict:
    """Build a LevelOfTheoryRef dict from a LevelOfTheory dataclass."""
    ref: dict = {"method": lot.method}
    if lot.basis:
        ref["basis"] = lot.basis
    return ref


def _make_opt_calculation(
    key: str,
    geometry_key: str | None,
    run: ARCRunData,
    sp_info: SpeciesInfo | TSInfo,
    include_artifacts: bool = True,
) -> dict:
    """Build a CalculationIn dict for an optimization calculation."""
    calc: dict = {
        "key": key,
        "type": "opt",
        "software_release": _make_software_release(run),
        "level_of_theory": _make_level_of_theory(run.opt_level),
        "workflow_tool_release": _make_workflow_tool_release(run),
    }
    if geometry_key:
        calc["geometry_key"] = geometry_key

    if sp_info.opt_result:
        calc["opt_converged"] = sp_info.opt_result.converged
        calc["opt_n_steps"] = sp_info.opt_result.n_steps
        calc["opt_final_energy_hartree"] = sp_info.opt_result.final_energy_hartree

    if include_artifacts:
        artifacts = []
        if sp_info.paths.sp_log and sp_info.paths.sp_log.exists():
            artifacts.append(_make_artifact(sp_info.paths.sp_log, "output_log"))
        if artifacts:
            calc["artifacts"] = artifacts

    return calc


def _make_freq_calculation(
    key: str,
    geometry_key: str,
    run: ARCRunData,
    sp_info: SpeciesInfo | TSInfo,
    include_artifacts: bool = True,
) -> dict:
    """Build a CalculationIn dict for a frequency calculation."""
    calc: dict = {
        "key": key,
        "type": "freq",
        "geometry_key": geometry_key,
        "software_release": _make_software_release(run),
        "level_of_theory": _make_level_of_theory(run.freq_level),
        "workflow_tool_release": _make_workflow_tool_release(run),
    }
    if sp_info.freq_result:
        calc["freq_n_imag"] = sp_info.freq_result.n_imag
        calc["freq_imag_freq_cm1"] = sp_info.freq_result.imag_freq_cm1
        calc["freq_zpe_hartree"] = sp_info.freq_result.zpe_hartree
        if sp_info.freq_result.frequencies_cm1:
            calc["freq_frequencies_cm1"] = list(sp_info.freq_result.frequencies_cm1)

    if include_artifacts:
        artifacts = []
        if sp_info.paths.freq_log and sp_info.paths.freq_log.exists():
            artifacts.append(_make_artifact(sp_info.paths.freq_log, "output_log"))
        if artifacts:
            calc["artifacts"] = artifacts

    return calc


def _make_sp_calculation(
    key: str,
    geometry_key: str,
    run: ARCRunData,
    sp_info: SpeciesInfo,
    include_artifacts: bool = True,
) -> dict:
    """Build a CalculationIn dict for a single-point energy calculation.

    Only used when SP level differs from opt level.
    """
    calc: dict = {
        "key": key,
        "type": "sp",
        "geometry_key": geometry_key,
        "software_release": _make_software_release(run),
        "level_of_theory": _make_level_of_theory(run.sp_level),
        "workflow_tool_release": _make_workflow_tool_release(run),
    }
    if sp_info.opt_result:
        # When SP is separate, the SP log has the energy
        calc["sp_electronic_energy_hartree"] = sp_info.opt_result.final_energy_hartree

    if include_artifacts:
        artifacts = []
        if sp_info.paths.sp_log and sp_info.paths.sp_log.exists():
            artifacts.append(_make_artifact(sp_info.paths.sp_log, "output_log"))
        if artifacts:
            calc["artifacts"] = artifacts

    return calc


def _make_freq_scale_factor_ref(
    run: ARCRunData,
    freq_scale_factor: float | None,
) -> dict | None:
    """Build a FreqScaleFactorRef dict for the statmech payload.

    ``freq_scale_factor=None`` means restart.yml had no entry; for older ARC
    runs this means no scaling was applied (explicitly 1.0), not "unknown".

    The ref includes:
    - level_of_theory: the frequency LOT
    - software: the ESS software (Gaussian, etc.)
    - workflow_tool_release: ARC (the proximate source of the value)
    - value: the factor applied (1.0 if absent from restart.yml)
    """
    value = freq_scale_factor if freq_scale_factor is not None else 1.0
    ref: dict = {
        "scale_kind": "fundamental",
        "value": value,
        "level_of_theory": _make_level_of_theory(run.freq_level),
    }
    # Software context (e.g. Gaussian) — which program the LOT ran on
    sw_name = run.freq_level.software or run.software_name
    if sw_name:
        ref["software"] = {"name": sw_name}
    # ARC is the proximate source (looked it up from freq_scale_factors.yml)
    ref["workflow_tool_release"] = _make_workflow_tool_release(run)
    if run.freq_scale_factor_source_note:
        ref["note"] = run.freq_scale_factor_source_note
    return ref


def _build_statmech_payload(
    conformer: ArkaneConformer,
    freq_scale_factor_ref: dict | None,
    note: str | None = None,
) -> dict:
    """Build a BundleStatmechIn dict from parsed conformer data."""
    rrk_map = {
        "atom": "atom",
        "linear": "linear",
        "spherical_top": "spherical_top",
        "symmetric_top": "symmetric_top",
        "asymmetric_top": "asymmetric_top",
    }
    stm_map = {
        "rrho": "rrho",
        "rrho_1d": "rrho_1d",
        "rrho_nd": "rrho_nd",
        "rrho_1d_nd": "rrho_1d_nd",
    }
    torsion_map = {
        "hindered_rotor": "hindered_rotor",
        "free_rotor": "free_rotor",
    }

    result: dict = {
        "scientific_origin": "computed",
        "is_linear": conformer.is_linear,
        "rigid_rotor_kind": rrk_map.get(conformer.rigid_rotor_kind) if conformer.rigid_rotor_kind else None,
        "external_symmetry": conformer.external_symmetry,
        "optical_isomers": conformer.optical_isomers,
        "statmech_treatment": stm_map.get(conformer.statmech_treatment) if conformer.statmech_treatment else None,
        "freq_scale_factor": freq_scale_factor_ref,
    }
    if note:
        result["note"] = note

    if conformer.hindered_rotors:
        result["torsions"] = [
            {
                "torsion_index": i + 1,
                "symmetry_number": hr.symmetry_number,
                "treatment_kind": torsion_map.get(hr.treatment, "hindered_rotor"),
            }
            for i, hr in enumerate(conformer.hindered_rotors)
        ]

    return result


def _build_species_payload(
    label: str,
    sp_info: SpeciesInfo,
    run: ARCRunData,
    include_artifacts: bool = True,
) -> dict:
    """Build a BundleSpeciesIn dict for one species."""
    # Use SMILES from YAML data if available, otherwise from restart/input
    smiles = sp_info.smiles
    if sp_info.yaml_data and sp_info.yaml_data.smiles:
        smiles = sp_info.yaml_data.smiles

    species_entry = {
        "smiles": smiles,
        "charge": sp_info.charge,
        "multiplicity": sp_info.multiplicity,
    }

    # Geometry
    geom_key = f"{label}_geom"
    xyz_text = ""
    if sp_info.xyz_file and sp_info.xyz_file.exists():
        xyz_text = _read_xyz_text(sp_info.xyz_file)
    if not xyz_text:
        # Monoatomic species (e.g. [H]) may not have an XYZ file from ARC
        mono = _monoatomic_xyz(smiles or "")
        if mono:
            xyz_text = mono

    # Conformer (opt calculation + geometry)
    opt_key = f"{label}_opt"
    conformer = {
        "key": f"{label}_conf",
        "geometry": {"key": geom_key, "xyz_text": xyz_text},
        "calculation": _make_opt_calculation(opt_key, None, run, sp_info, include_artifacts),
    }

    # Additional calculations
    calculations = []

    # Freq calculation
    if sp_info.freq_result:
        freq_key = f"{label}_freq"
        calculations.append(
            _make_freq_calculation(freq_key, geom_key, run, sp_info, include_artifacts)
        )

    # SP calculation (only if SP level differs from opt)
    if not run.sp_is_same_as_opt:
        sp_key = f"{label}_sp"
        calculations.append(
            _make_sp_calculation(sp_key, geom_key, run, sp_info, include_artifacts)
        )

    # Thermo
    thermo = None
    if sp_info.yaml_data and sp_info.yaml_data.thermo:
        t = sp_info.yaml_data.thermo
        thermo_dict: dict = {
            "h298_kj_mol": t.h298_kj_mol,
            "s298_j_mol_k": t.s298_j_mol_k,
            "tmin_k": t.tmin_k,
            "tmax_k": t.tmax_k,
        }

        # NASA polynomials
        low = t.low_poly
        high = t.high_poly
        thermo_dict["nasa"] = {
            "t_low": low.tmin_k,
            "t_mid": low.tmax_k,  # polynomial1.Tmax == polynomial2.Tmin
            "t_high": high.tmax_k,
            "a1": low.coeffs[0],
            "a2": low.coeffs[1],
            "a3": low.coeffs[2],
            "a4": low.coeffs[3],
            "a5": low.coeffs[4],
            "a6": low.coeffs[5],
            "a7": low.coeffs[6],
            "b1": high.coeffs[0],
            "b2": high.coeffs[1],
            "b3": high.coeffs[2],
            "b4": high.coeffs[3],
            "b5": high.coeffs[4],
            "b6": high.coeffs[5],
            "b7": high.coeffs[6],
        }

        # Tabulated Cp points
        if t.points:
            thermo_dict["points"] = [
                {"temperature_k": p.temperature_k, "cp_j_mol_k": p.cp_j_mol_k}
                for p in t.points
            ]

        if run.energy_correction_note:
            thermo_dict["note"] = run.energy_correction_note

        thermo = thermo_dict

    # Statmech
    statmech = None
    arkane_conformer = None
    if sp_info.arkane_output_path is not None:
        try:
            arkane_conformer = parse_arkane_conformer_from_file(sp_info.arkane_output_path)
            fsf_ref = _make_freq_scale_factor_ref(run, run.freq_scale_factor)
            statmech = _build_statmech_payload(
                arkane_conformer, fsf_ref, note=run.energy_correction_note
            )
        except Exception as e:
            print(f"  Warning: failed to parse statmech for {label}: {e}")

    # Derive term symbol from multiplicity + linearity
    # TODO: pass point_group once output.yml parsing is wired up
    term = derive_term_symbol(
        sp_info.multiplicity,
        is_linear=arkane_conformer.is_linear if arkane_conformer else None,
    )
    if term is not None:
        species_entry["term_symbol"] = term

    return {
        "key": label,
        "species_entry": species_entry,
        "conformers": [conformer],
        "calculations": calculations,
        "thermo": thermo,
        "statmech": statmech,
    }


def _build_ts_payload(
    ts_info: TSInfo,
    run: ARCRunData,
    include_artifacts: bool = True,
) -> dict:
    """Build a BundleTransitionStateIn dict."""
    geom_key = f"{ts_info.label}_geom"
    xyz_text = ""
    if ts_info.xyz_file:
        xyz_text = _read_xyz_text(ts_info.xyz_file)

    opt_key = f"{ts_info.label}_opt"
    primary_calc = _make_opt_calculation(opt_key, None, run, ts_info, include_artifacts)

    calculations = []
    if ts_info.freq_result:
        freq_key = f"{ts_info.label}_freq"
        calculations.append(
            _make_freq_calculation(freq_key, geom_key, run, ts_info, include_artifacts)
        )

    return {
        "charge": ts_info.charge,
        "multiplicity": ts_info.multiplicity,
        "geometry": {"key": geom_key, "xyz_text": xyz_text},
        "calculation": primary_calc,
        "calculations": calculations,
        "label": ts_info.label,
    }


def _build_kinetics_payload(
    kinetics: ArkaneKinetics,
    reactant_keys: list[str],
    product_keys: list[str],
) -> dict:
    """Build a BundleKineticsIn dict from parsed Arkane kinetics."""
    payload: dict = {
        "reactant_keys": reactant_keys,
        "product_keys": product_keys,
        "model_kind": "modified_arrhenius",
        "a": kinetics.a,
        "a_units": map_a_units(kinetics.a_units),
        "n": kinetics.n,
        "reported_ea": kinetics.ea,
        "reported_ea_units": map_ea_units(kinetics.ea_units),
        "tmin_k": kinetics.tmin_k,
        "tmax_k": kinetics.tmax_k,
    }
    if kinetics.a_uncertainty is not None:
        payload["a_uncertainty"] = kinetics.a_uncertainty
        # ARC/Arkane reports A-uncertainty as a multiplicative factor f
        # (true A within [A/f, A*f]); see arkane_parser.ParsedKineticsRow.
        payload["a_uncertainty_kind"] = "multiplicative"
    if kinetics.n_uncertainty is not None:
        payload["n_uncertainty"] = kinetics.n_uncertainty
    if kinetics.ea_uncertainty is not None:
        payload["d_reported_ea"] = kinetics.ea_uncertainty
    if kinetics.comment:
        payload["note"] = kinetics.comment
    return payload


def build_payload(run: ARCRunData, arc_dir: "Path | str", include_artifacts: bool = True) -> dict:
    """Build the full ComputedReactionUploadRequest dict from ARC run data.

    Returns a dict that can be passed to
    ``ComputedReactionUploadRequest.model_validate(payload)``
    or serialized to JSON for API submission.

    :param include_artifacts: When False, skip reading and encoding Gaussian log
        files as artifacts.  The calculation metadata (type, LOT, results) is
        still stored — only the raw file bytes are omitted.  Use ``--no-artifacts``
        for a fast first-pass ingest when log files are on a slow filesystem.
    """
    arc_dir = Path(arc_dir)
    if not run.reactions:
        raise ValueError("No reactions found in ARC run data.")

    rxn = run.reactions[0]  # single-reaction ARC runs

    # Build species payloads
    species_payloads = []
    for label in rxn.reactant_labels + rxn.product_labels:
        sp_info = run.species.get(label)
        if sp_info is None:
            raise ValueError(f"Species '{label}' referenced in reaction but not found.")
        if not sp_info.converged:
            print(f"  Warning: species '{label}' did not converge, including anyway.")
        species_payloads.append(_build_species_payload(label, sp_info, run, include_artifacts))

    # Build TS payload
    ts_payload = None
    ts_info = run.transition_states.get(rxn.ts_label)
    if ts_info and ts_info.converged:
        ts_payload = _build_ts_payload(ts_info, run, include_artifacts)

    # Parse kinetics from Arkane output (may be absent or incomplete)
    arkane_output = arc_dir / "output" / "rxns" / rxn.ts_label / "arkane" / "output.py"
    kinetics_payloads = []
    if arkane_output.exists():
        try:
            kinetics_data = parse_arkane_kinetics_from_file(arkane_output)
            kinetics_payloads.append(
                _build_kinetics_payload(
                    kinetics_data,
                    reactant_keys=rxn.reactant_labels,
                    product_keys=rxn.product_labels,
                )
            )
        except ValueError:
            pass  # Arkane ran but kinetics fitting didn't complete

    # Assemble the bundle
    payload: dict = {
        "workflow_tool_release": _make_workflow_tool_release(run),
        "species": species_payloads,
        "reversible": True,
        "reactant_keys": rxn.reactant_labels,
        "product_keys": rxn.product_labels,
        "kinetics": kinetics_payloads,
    }

    analysis_release = _make_analysis_software_release(run)
    if analysis_release is not None:
        payload["analysis_software_release"] = analysis_release

    if rxn.family:
        payload["reaction_family"] = rxn.family

    if ts_payload:
        payload["transition_state"] = ts_payload

    return payload
