"""Service helpers for conformer group resolution.

Implements DR-0005: torsional basin matching for conformer grouping.
Falls back to label matching when torsion data is unavailable.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.chemistry.torsion_fingerprint import (
    TorsionFingerprint,
    compare_conformers,
    resolve_atom_mapping,
)
from app.db.models.species import (
    ConformerAssignmentScheme,
    ConformerGroup,
    SpeciesEntry,
)


def _get_default_scheme(session: Session) -> ConformerAssignmentScheme | None:
    """Find the default conformer assignment scheme."""
    return session.scalar(
        select(ConformerAssignmentScheme).where(
            ConformerAssignmentScheme.is_default.is_(True)
        )
    )


_DEFAULT_SCHEME_PARAMS: dict = {
    "require_all_comparable_torsions_within_threshold": True,
    "torsion_match_threshold_degrees": 15,
    "use_circular_difference": True,
    "exclude_methyl_rotors": False,
    "exclude_terminal_noisy_rotors": True,
    "methyl_symmetry_fold": 3,
    "quantization_bin_degrees": 15,
    "rigid_fallback_use_rmsd": True,
    "rmsd_threshold_angstrom": 0.5,
    "tie_break": "closest_torsional_distance",
}


def _get_scheme_params(scheme: ConformerAssignmentScheme | None) -> dict:
    """Extract parameters from a scheme, with defaults for any missing keys."""
    if scheme is None or scheme.parameters_json is None:
        return dict(_DEFAULT_SCHEME_PARAMS)
    # Merge: scheme values override defaults, defaults fill any gaps
    merged = dict(_DEFAULT_SCHEME_PARAMS)
    merged.update(scheme.parameters_json)
    return merged


def compute_fingerprint_for_observation(
    smiles: str,
    xyz_atoms: tuple[tuple[str, float, float, float], ...],
    scheme_params: dict,
) -> tuple[TorsionFingerprint | None, list[tuple[float, float, float]] | None]:
    """Compute a torsion fingerprint + mapped coords, returning (None, None) on failure.

    Uses resolve_atom_mapping (SMILES as graph truth) to get both the
    canonical fingerprint and the mapped coordinates in species-canonical order.
    """
    try:
        result = resolve_atom_mapping(
            smiles,
            xyz_atoms,
            exclude_methyl=scheme_params.get("exclude_methyl_rotors", False),
            exclude_terminal_noisy=scheme_params.get("exclude_terminal_noisy_rotors", True),
            methyl_symmetry_fold=scheme_params.get("methyl_symmetry_fold", 3),
            bin_width_deg=scheme_params.get("quantization_bin_degrees", 15),
        )
        if result.status in ("unique", "equivalent", "canonicalized") and result.fingerprint is not None:
            return result.fingerprint, result.mapped_coords
        return None, None
    except (ValueError, RuntimeError):
        return None, None


def resolve_conformer_group(
    session: Session,
    species_entry: SpeciesEntry,
    *,
    label: str | None,
    created_by: int | None = None,
    smiles: str | None = None,
    xyz_atoms: tuple[tuple[str, float, float, float], ...] | None = None,
) -> tuple[ConformerGroup, TorsionFingerprint | None, ConformerAssignmentScheme | None]:
    """Resolve or create a conformer group for an uploaded observation.

    Uses torsional basin matching (DR-0005) when geometry data is available.
    Falls back to label matching when torsion data cannot be computed.
    This function resolves only the basin-level ``ConformerGroup``; callers
    must still create a fresh ``ConformerObservation`` row for each distinct
    provenance-bearing upload event.

    :param session: Active SQLAlchemy session.
    :param species_entry: Resolved species entry that owns the conformer group.
    :param label: Optional user-supplied conformer label.
    :param created_by: Optional application user id for new rows.
    :param smiles: SMILES string for the species (needed for torsion extraction).
    :param xyz_atoms: Parsed XYZ atoms (needed for torsion extraction).
    :returns: (conformer_group, fingerprint_or_none, scheme_or_none).
    """
    scheme = _get_default_scheme(session)
    params = _get_scheme_params(scheme)
    threshold = params["torsion_match_threshold_degrees"]
    use_rmsd = params["rigid_fallback_use_rmsd"]
    rmsd_threshold = params["rmsd_threshold_angstrom"] if use_rmsd else None

    # Compute fingerprint + mapped coords for the new observation
    new_fp: TorsionFingerprint | None = None
    new_coords: list[tuple[float, float, float]] | None = None
    if smiles is not None and xyz_atoms is not None:
        new_fp, new_coords = compute_fingerprint_for_observation(smiles, xyz_atoms, params)

    # Try matching against existing groups
    if new_fp is not None:
        existing_groups = session.scalars(
            select(ConformerGroup).where(
                ConformerGroup.species_entry_id == species_entry.id,
                ConformerGroup.representative_fingerprint_json.isnot(None),
            )
        ).all()

        best_group = None
        best_total_delta = float("inf")

        for group in existing_groups:
            rep_fp_data = group.representative_fingerprint_json
            if rep_fp_data is None:
                continue

            rep_fp = _reconstruct_fingerprint(rep_fp_data)
            if rep_fp is None:
                continue

            # Load representative coords for RMSD (rigid molecules + sanity check)
            rep_coords = group.representative_coords_json if use_rmsd else None

            result = compare_conformers(
                new_fp, rep_fp,
                threshold_deg=threshold,
                coords1=rep_coords,
                coords2=new_coords if use_rmsd else None,
                rmsd_threshold=rmsd_threshold,
            )
            if result.same_basin:
                total_delta = sum(result.torsion_deltas) if result.torsion_deltas else 0.0
                if total_delta < best_total_delta:
                    best_total_delta = total_delta
                    best_group = group

        if best_group is not None:
            return best_group, new_fp, scheme

    # Fallback for trivial cases (single atoms, fingerprint computation failures):
    # If we have no fingerprint, join the first existing group that also has no
    # fingerprint. This prevents single-atom species from spawning a new group
    # on every upload.
    if new_fp is None:
        existing_no_fp = session.scalar(
            select(ConformerGroup).where(
                ConformerGroup.species_entry_id == species_entry.id,
                ConformerGroup.representative_fingerprint_json.is_(None),
            ).order_by(ConformerGroup.id)
        )
        if existing_no_fp is not None:
            return existing_no_fp, None, scheme

    # No match found — create new group with auto-generated label
    next_index = (session.scalar(
        select(func.count(ConformerGroup.id)).where(
            ConformerGroup.species_entry_id == species_entry.id,
        )
    ) or 0) + 1

    new_group = ConformerGroup(
        species_entry_id=species_entry.id,
        label=f"conformer_{next_index}",
        representative_fingerprint_json=new_fp.to_dict() if new_fp is not None else None,
        representative_coords_json=new_coords,
        created_by=created_by,
    )
    session.add(new_group)
    session.flush()

    return new_group, new_fp, scheme


def _reconstruct_fingerprint(fp_data: dict) -> TorsionFingerprint | None:
    """Reconstruct a TorsionFingerprint from stored JSONB data for comparison."""
    try:
        from app.chemistry.torsion_fingerprint import RotorSlot

        rotor_keys = fp_data.get("canonical_rotor_keys", [])
        folded = fp_data.get("folded_torsions_deg", [])
        raw = fp_data.get("raw_torsions_deg", [])
        bins = fp_data.get("quantized_bins", [])
        bin_width = fp_data.get("bin_width_deg", 15.0)

        if len(rotor_keys) != len(folded):
            return None

        # Reconstruct minimal RotorSlot objects from canonical keys.
        # The key format is "R_{rank_lo}_{rank_hi}" and canonical_key
        # is computed from canonical_rank_begin/end, so we must set those.
        slots = []
        for key in rotor_keys:
            parts = key.split("_")
            if len(parts) != 3:
                return None
            rank_lo, rank_hi = int(parts[1]), int(parts[2])
            slots.append(RotorSlot(
                bond_begin=0,
                bond_end=0,
                terminal_a=0,
                terminal_d=0,
                canonical_rank_begin=rank_lo,
                canonical_rank_end=rank_hi,
                symmetry_fold=1,  # stored implicitly in folded values
            ))

        return TorsionFingerprint(
            rotor_slots=slots,
            raw_torsions_deg=raw,
            folded_torsions_deg=folded,
            quantized_bins=bins,
            bin_width_deg=bin_width,
        )
    except (KeyError, ValueError, IndexError):
        return None
