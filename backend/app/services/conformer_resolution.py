"""Service helpers for conformer group resolution.

Implements DR-0005: torsional basin matching for conformer grouping.
Falls back to label matching when torsion data is unavailable.

Matching is symmetry-aware (:mod:`app.chemistry.basin_matching`): two
geometries share a basin when some graph symmetry of the molecule puts every
rotor within its tolerance. The rule, its tolerances and its bounds are the
parameters of the default ``conformer_assignment_scheme`` row, read here at
call time; version 2 of ``torsion_basin`` is the first to use this rule.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.chemistry.basin_matching import BasinMatchParams, compare_basins, symmetry_model_for
from app.chemistry.torsion_fingerprint import (
    TorsionFingerprint,
    resolve_atom_mapping,
)
from app.db.models.species import (
    ConformerAssignmentScheme,
    ConformerGroup,
    SpeciesEntry,
)

T = TypeVar("T")


def _get_default_scheme(session: Session) -> ConformerAssignmentScheme | None:
    """Find the default conformer assignment scheme."""
    return session.scalar(
        select(ConformerAssignmentScheme).where(
            ConformerAssignmentScheme.is_default.is_(True)
        )
    )


TORSION_BASIN_SCHEME_NAME = "torsion_basin"
TORSION_BASIN_SCHEME_VERSION = 2

#: The parameters of ``torsion_basin`` version 2, which is what this code implements. They are stored on
#: the scheme row too (the revision that adds version 2 writes this same content); this copy only fills in
#: a key a stored row lacks. Version 1's parameters are not kept here: nothing in the code can run them,
#: and that revision reassigned every observation under version 2.
TORSION_BASIN_PARAMS: dict = {
    # Same basin iff SOME graph symmetry puts EVERY rotor within its own tolerance.
    "matching_rule": "symmetry_existence",
    "require_all_comparable_torsions_within_threshold": True,
    "use_circular_difference": True,
    # Heavy-atom rotors, and methyl tops (measured on their three-fold folded angle).
    "torsion_tolerance_heavy_degrees": 20,
    # One- and two-hydrogen tops: O-H, S-H, O-O-H, N-O-H, -NH2, radical -CH2.
    "torsion_tolerance_terminal_h_degrees": 30,
    "exclude_methyl_rotors": False,
    # False keeps the one- and two-hydrogen tops that version 1 dropped.
    "exclude_terminal_noisy_rotors": False,
    "methyl_symmetry_fold": 3,
    # Hard cap on the heavy-atom symmetry search; past it the search keeps the first N in a fixed order.
    "max_symmetry_mappings": 4096,
    # The stored fingerprint's bins are display only.
    "quantization_bin_degrees": 15,
    "rigid_fallback_use_rmsd": True,
    "rmsd_threshold_angstrom": 0.5,
    "tie_break": "closest_torsional_distance",
}


def _get_scheme_params(scheme: ConformerAssignmentScheme | None) -> dict:
    """Extract parameters from a scheme, with defaults for any missing keys."""
    if scheme is None or scheme.parameters_json is None:
        return dict(TORSION_BASIN_PARAMS)
    # Merge: scheme values override defaults, defaults fill any gaps
    merged = dict(TORSION_BASIN_PARAMS)
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
            exclude_terminal_noisy=scheme_params.get("exclude_terminal_noisy_rotors", False),
            methyl_symmetry_fold=scheme_params.get("methyl_symmetry_fold", 3),
            bin_width_deg=scheme_params.get("quantization_bin_degrees", 15),
        )
        if result.status in ("unique", "equivalent", "canonicalized") and result.fingerprint is not None:
            # Record how much of the molecule's symmetry the matcher searched, and whether the cap cut it
            # short, so a split caused by the cap is visible on the observation rather than silent.
            model = symmetry_model_for(smiles, BasinMatchParams.from_scheme_params(scheme_params))
            result.fingerprint.symmetry_mappings = model.n_mappings
            result.fingerprint.symmetry_cap_hit = model.cap_hit
            return result.fingerprint, result.mapped_coords
        return None, None
    except (ValueError, RuntimeError):
        return None, None


def find_matching_basin(
    smiles: str,
    new_coords: list[tuple[float, float, float]] | None,
    candidates: Iterable[tuple[T, dict | None, list | None]],
    scheme_params: dict,
) -> T | None:
    """Pick the basin among ``candidates`` that ``new_coords`` belongs to, or ``None``.

    Each candidate is ``(handle, representative_fingerprint_json, representative_coords)``. A candidate with
    no stored fingerprint cannot be compared and is skipped. Among matching candidates the
    one with the smallest total torsion difference wins (``tie_break``); on an exact tie the earlier
    candidate wins, so callers pass them in a stable order.

    This is the one place the live upload path and the reassignment of existing observations decide
    "same basin", so the two cannot drift apart.
    """
    if new_coords is None:
        return None
    match_params = BasinMatchParams.from_scheme_params(scheme_params)
    rotor_count = symmetry_model_for(smiles, match_params).rotor_count
    best: T | None = None
    best_total = float("inf")
    for handle, rep_fingerprint, rep_coords in candidates:
        if rep_fingerprint is None:
            continue
        if rep_fingerprint.get("rotor_count") != rotor_count:
            continue
        comparison = compare_basins(smiles, rep_coords, new_coords, match_params)
        if comparison.same_basin and comparison.total_delta < best_total:
            best, best_total = handle, comparison.total_delta
    return best


def lock_basin_resolution(session: Session, species_entry_id: int) -> None:
    """Serialize basin resolution per species entry for the rest of the transaction.

    Closes a read-then-create race: two concurrent uploads of the same physical basin would both SELECT
    existing groups, both find no match, and both INSERT -- silently forking one basin into duplicate
    conformer_group rows (or colliding on the (species_entry_id, label) unique constraint via a racy
    COUNT-based index). A transaction-scoped advisory lock makes the second caller block until the first
    commits, so it then sees the committed group and dedups correctly. The xact variant auto-releases at
    commit/rollback (cannot leak); we take it inside the caller's transaction and never open our own. The
    key is namespaced by species_entry.id so distinct entries never block each other (cross-entry hash
    collisions only cause rare, harmless extra blocking).

    Reassignment of existing observations (``app.services.conformer_reassignment``) takes the same lock,
    so a live upload and a recompute of the same entry cannot interleave.

    Do NOT remove this lock without a DB-level basin-identity unique constraint.
    """
    session.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(f"conformer_group_resolve:{species_entry_id}", 0)
            )
        )
    )


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
    lock_basin_resolution(session, species_entry.id)

    scheme = _get_default_scheme(session)
    params = _get_scheme_params(scheme)

    # Compute fingerprint + mapped coords for the new observation
    new_fp: TorsionFingerprint | None = None
    new_coords: list[tuple[float, float, float]] | None = None
    if smiles is not None and xyz_atoms is not None:
        new_fp, new_coords = compute_fingerprint_for_observation(smiles, xyz_atoms, params)

    # Try matching against existing groups
    if new_fp is not None:
        # Ordered, so a tie between two equally close groups resolves the same way every time.
        existing_groups = session.scalars(
            select(ConformerGroup)
            .where(
                ConformerGroup.species_entry_id == species_entry.id,
                ConformerGroup.representative_fingerprint_json.isnot(None),
            )
            .order_by(ConformerGroup.id)
        ).all()

        best_group = find_matching_basin(
            smiles,  # type: ignore[arg-type]  # new_fp is only set when smiles is
            new_coords,
            ((group, group.representative_fingerprint_json, group.representative_coords_json) for group in existing_groups),
            params,
        )

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
    next_label = _next_group_label(session, species_entry.id)

    new_group = ConformerGroup(
        species_entry_id=species_entry.id,
        label=next_label,
        representative_fingerprint_json=new_fp.to_dict() if new_fp is not None else None,
        representative_coords_json=new_coords,
        created_by=created_by,
    )
    session.add(new_group)
    session.flush()

    return new_group, new_fp, scheme


def _next_group_label(session: Session, species_entry_id: int) -> str:
    """The next unused ``conformer_N`` label for an entry.

    One past the highest number in use, not the row count: a group that was merged away leaves a gap, and
    counting would then hand out a label that is still taken.
    """
    labels = session.scalars(
        select(ConformerGroup.label).where(ConformerGroup.species_entry_id == species_entry_id)
    ).all()
    highest = 0
    for label in labels:
        if label and label.startswith("conformer_") and label[len("conformer_"):].isdigit():
            highest = max(highest, int(label[len("conformer_"):]))
    return f"conformer_{max(highest, len(labels)) + 1}"
