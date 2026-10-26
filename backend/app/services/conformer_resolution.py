"""Service helpers for conformer group resolution.

Implements DR-0005: torsional basin matching for conformer grouping.
Falls back to label matching when torsion data is unavailable.

A basin is a connected component
--------------------------------
Matching is symmetry-aware (:mod:`app.chemistry.basin_matching`): two geometries share a basin when some graph
symmetry of the molecule puts every rotor within its tolerance. Which structures belong together is then decided by
:mod:`app.chemistry.basin_linkage`: a basin is a *connected component* of the species entry's **defining**
observations (converged optimisations; see :mod:`app.services.conformer_basin_store`), two of them linked when they
share a basin. The partition is a function of the set of structures, so the order they were deposited in cannot
change it. Version 3 of ``torsion_basin`` is the first to use this rule; earlier versions compared a new structure
with the first one a basin ever received, which made the partition depend on the order of deposition.

A live upload (:func:`resolve_conformer_group`)
-----------------------------------------------
* A *defining* structure is compared with every defining member of the entry. Matches in no group: a new group
  (or the provisional group it matches, below). In one group: it joins it. In several: it joins the oldest and the
  younger groups are merged into it.
* A *non-defining* structure (a single point, a scan point, a partial optimisation) is filed into the basin whose
  representative it matches best (ties: the older group). It never links two basins. If none matches it starts a
  *provisional* basin that has no defining member and is matched by its own geometry until an optimised deposit
  arrives.
* A merge that would write a row the database refuses to edit cannot be made by the API role: the accepted-science
  roots once approved (an observation, a thermo target), EVERY statmech basin declaration or ensemble member (approved
  or not), and a selection an accepted kinetics record cites. Only a declared repair may move them, and only an owner
  role may declare one. The new observation is filed under the oldest group and a ``conformer_group_merge_pending``
  row records the merge owed; the owner-role reassignment completes it. The test is "would the trigger refuse this
  write?", not "is it approved?" (:func:`app.services.conformer_basin_store.merge_needs_declared_repair`).
* After every upload the structures that define nothing are filed again against the groups as they now stand
  (:func:`app.services.conformer_basin_store.refile_non_defining`), by the function the recompute uses, so a recompute
  straight after an upload changes nothing.

The representative of a group is derived, not identity: its medoid, stored for reads. Energy appears nowhere in
matching, linkage or the medoid.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.chemistry.basin_matching import (
    BasinMatchParams,
    compare_basins_symmetric,
    comparison_distance,
    symmetry_model_for,
)
from app.chemistry.torsion_fingerprint import (
    TorsionFingerprint,
    resolve_atom_mapping,
)
from app.db.models.common import SubmissionRecordType
from app.db.models.species import (
    ConformerAssignmentScheme,
    ConformerGroup,
    ConformerObservation,
    SpeciesEntry,
)
from app.services.conformer_basin_moves import (
    ReassignmentReport,
    Repairs,
    clear_representative,
    move_dependents,
    reset_review,
)
from app.services.conformer_basin_store import (
    StoredObservation,
    defining_observation_ids,
    is_defining_calculation,
    load_entry_observations,
    merge_needs_declared_repair,
    observation_geometry_atoms,
    record_pending_merge,
    refile_non_defining,
    refresh_group_shape,
)
from app.services.record_review import RecordRef

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _get_default_scheme(session: Session) -> ConformerAssignmentScheme | None:
    """Find the default conformer assignment scheme."""
    return session.scalar(
        select(ConformerAssignmentScheme).where(
            ConformerAssignmentScheme.is_default.is_(True)
        )
    )


TORSION_BASIN_SCHEME_NAME = "torsion_basin"
TORSION_BASIN_SCHEME_VERSION = 3

#: The parameters of ``torsion_basin`` version 3, which is what this code implements. They are stored on
#: the scheme row too (the revision that adds version 3 writes this same content); this copy only fills in
#: a key a stored row lacks. Earlier versions' parameters are not kept here: nothing in the code can run them,
#: and each version's revision reassigned every observation under it.
TORSION_BASIN_PARAMS: dict = {
    # Same basin iff SOME graph symmetry puts EVERY rotor within its own tolerance.
    "matching_rule": "symmetry_existence",
    "require_all_comparable_torsions_within_threshold": True,
    "use_circular_difference": True,
    # Heavy-atom rotors, and methyl tops (measured on their three-fold folded angle).
    "torsion_tolerance_heavy_degrees": 20,
    # One- and two-hydrogen tops: O-H, S-H, O-O-H, N-O-H, -NH2, radical -CH2. The two hydrogens of a
    # two-hydrogen top are compared as a pair under one assignment.
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
    # A basin is a connected component of its defining members, linked by the matching rule above.
    "linkage": "connected_components",
    # The stored representative is the member with the smallest summed best-mapping torsion distance to the others.
    "reference": "medoid",
    # Only converged optimised structures (opt or composite with a final geometry, not stated as unconverged)
    # define or link basins.
    "defining_members": "converged_opt",
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
    """Pick the candidate whose representative ``new_coords`` matches best, or ``None``.

    Each candidate is ``(handle, representative_fingerprint_json, representative_coords)``. A candidate with
    no stored fingerprint cannot be compared and is skipped. Among matching candidates the
    one with the smallest total torsion difference wins (``tie_break``); on an exact tie the earlier
    candidate wins, so callers pass them in a stable order.

    This files a structure that does not define a basin (it may match a representative but never links two
    basins) and finds the provisional basin an optimised structure joins. Linkage between defining structures
    is :mod:`app.chemistry.basin_linkage`.
    """
    if new_coords is None:
        return None
    match_params = BasinMatchParams.from_scheme_params(scheme_params)
    rotor_count = symmetry_model_for(smiles, match_params).rotor_count
    best: T | None = None
    best_distance = float("inf")
    for handle, rep_fingerprint, rep_coords in candidates:
        if rep_fingerprint is None or rep_coords is None:
            continue
        if rep_fingerprint.get("rotor_count") != rotor_count:
            continue
        comparison = compare_basins_symmetric(smiles, rep_coords, new_coords, match_params)
        if comparison.same_basin and comparison_distance(comparison) < best_distance:
            best, best_distance = handle, comparison_distance(comparison)
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


@dataclass
class BasinPlacement:
    """Where ``resolve_conformer_group`` put a structure, carried to ``settle_conformer_basin`` on the same request."""

    mapped_coords: list
    defining: bool
    params: dict
    species_entry_id: int
    #: Younger groups a defining structure linked to the group it joined, whose merge an approved record forbids
    #: this role to make: recorded as pending once the observation exists.
    pending_absorbed: tuple[int, ...] = ()


def is_defining_primary(calculation_payload: object) -> bool:
    """Whether an upload's primary calculation makes its structure a defining member of a basin.

    The same predicate the recompute applies to the stored calculation
    (:func:`app.services.conformer_basin_store.is_defining_calculation`), fed with what the upload is about to
    store: the type, a stated failure to converge, and whether a ``final`` output geometry will be attached. The
    upload attaches the conformer's geometry as the final output when it declares no output geometries of its own
    (``attach_calculation_output_geometries``); when it declares some, there is a final one only if one of them has
    that role. An optimisation whose declared outputs are all ``initial`` therefore has no final geometry, is not a
    defining member, and is filed without linking anything -- exactly as the recompute reads it back.
    """
    opt_result = getattr(calculation_payload, "opt_result", None)
    declared = list(getattr(calculation_payload, "output_geometries", None) or [])
    has_final = not declared or any(str(getattr(e.role, "value", e.role)) == "final" for e in declared)
    return is_defining_calculation(
        str(getattr(calculation_payload.type, "value", calculation_payload.type)),
        has_final_geometry=has_final,
        converged=getattr(opt_result, "converged", None),
    )


def _member_coords(
    session: Session,
    observations: Sequence[StoredObservation],
    *,
    smiles: str,
    params: dict,
) -> dict[int, list]:
    """Species-canonical coordinates of each observation: stored, else re-derived from its geometry."""
    coords: dict[int, list] = {}
    missing: list[int] = []
    for obs in observations:
        if obs.mapped_coords is not None:
            coords[obs.id] = obs.mapped_coords
        else:
            missing.append(obs.id)
    if missing:
        for oid, atoms in observation_geometry_atoms(session, missing).items():
            _fp, structure = compute_fingerprint_for_observation(smiles, atoms, params)
            if structure is not None:
                coords[oid] = [list(c) for c in structure]
    return coords


def _structures_of(session: Session, smiles: str, params: dict):
    """A ``compute_missing`` callable for :func:`refresh_group_shape`: ``{id: (fingerprint_json, coords)}``."""

    def compute(observation_ids: list[int]) -> dict[int, tuple[dict | None, list | None]]:
        out: dict[int, tuple[dict | None, list | None]] = {}
        for oid, atoms in observation_geometry_atoms(session, observation_ids).items():
            fingerprint, structure = compute_fingerprint_for_observation(smiles, atoms, params)
            out[oid] = (
                fingerprint.to_dict() if fingerprint is not None else None,
                [list(c) for c in structure] if structure is not None else None,
            )
        return out

    return compute


def resolve_conformer_group(
    session: Session,
    species_entry: SpeciesEntry,
    *,
    label: str | None,
    created_by: int | None = None,
    smiles: str | None = None,
    xyz_atoms: tuple[tuple[str, float, float, float], ...] | None = None,
    defining: bool,
) -> tuple[ConformerGroup, TorsionFingerprint | None, ConformerAssignmentScheme | None]:
    """Resolve or create a conformer group for an uploaded observation.

    Uses torsional basin matching (DR-0005) when geometry data is available.
    Falls back to label matching when torsion data cannot be computed.
    This function resolves only the basin-level ``ConformerGroup``; callers
    must still create a fresh ``ConformerObservation`` row for each distinct
    provenance-bearing upload event, and then call :func:`settle_conformer_basin`
    with it so the group's representative and shape follow the new member.

    :param session: Active SQLAlchemy session.
    :param species_entry: Resolved species entry that owns the conformer group.
    :param label: Optional user-supplied conformer label.
    :param created_by: Optional application user id for new rows.
    :param smiles: SMILES string for the species (needed for torsion extraction).
    :param xyz_atoms: Parsed XYZ atoms (needed for torsion extraction).
    :param defining: Whether this structure is a defining member (see :func:`is_defining_primary`). A structure
        that is not (a single point, a scan point, a partial optimisation) is filed into a basin but never links two.
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
    if new_fp is not None and new_coords is not None and smiles is not None:
        match_params = BasinMatchParams.from_scheme_params(params)
        # Ordered, so a tie between two equally close groups resolves the same way every time.
        existing_groups = list(
            session.scalars(
                select(ConformerGroup)
                .where(ConformerGroup.species_entry_id == species_entry.id)
                .order_by(ConformerGroup.id)
            ).all()
        )
        observations = load_entry_observations(session, species_entry.id)
        defining_ids = defining_observation_ids(session, [o.id for o in observations])
        groups_with_defining = {o.group_id for o in observations if o.id in defining_ids}
        pending_absorbed: tuple[int, ...] = ()
        target: ConformerGroup | None = None

        if defining:
            members = [o for o in observations if o.id in defining_ids]
            member_coords = _member_coords(session, members, smiles=smiles, params=params)
            linked_groups: set[int] = set()
            for member in members:
                coords = member_coords.get(member.id)
                if coords is None or member.group_id in linked_groups:
                    continue
                if compare_basins_symmetric(smiles, coords, new_coords, match_params).same_basin:
                    linked_groups.add(member.group_id)
            if linked_groups:
                kept_id = min(linked_groups)
                target = next(g for g in existing_groups if g.id == kept_id)
                absorbed = sorted(linked_groups - {kept_id})
                if absorbed:
                    if merge_needs_declared_repair(session, absorbed):
                        pending_absorbed = tuple(absorbed)
                    else:
                        _merge_inline(session, kept_id, absorbed, scheme_id=scheme.id if scheme is not None else None)
        if target is None:
            # A defining structure may join a provisional basin (one with no defining member) it matches by its own
            # geometry; a structure that does not define anything is filed into whichever basin represents it best.
            pool = [g for g in existing_groups if not defining or g.id not in groups_with_defining]
            target = find_matching_basin(
                smiles,
                new_coords,
                ((group, group.representative_fingerprint_json, group.representative_coords_json) for group in pool),
                params,
            )
        if target is not None:
            new_fp.placement = BasinPlacement(
                mapped_coords=[list(c) for c in new_coords],
                defining=defining,
                params=params,
                species_entry_id=species_entry.id,
                pending_absorbed=pending_absorbed,
            )
            return target, new_fp, scheme

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
    if new_fp is not None and new_coords is not None:
        new_fp.placement = BasinPlacement(
            mapped_coords=[list(c) for c in new_coords],
            defining=defining,
            params=params,
            species_entry_id=species_entry.id,
        )

    return new_group, new_fp, scheme


def _merge_inline(session: Session, kept_id: int, absorbed_ids: Sequence[int], *, scheme_id: int | None) -> None:
    """Merge younger groups into the oldest one the upload linked, when no approved record is involved.

    Every observation, thermo target, statmech basin declaration and ensemble member, and selection of an absorbed
    group goes to the kept group (each of those would, by D5, follow an observation that now lives there). The
    emptied row is kept, with no representative and ``merged_into_conformer_group_id`` naming the survivor, so a
    cited group ref still resolves. Reviews of both groups reset: neither is the group that was reviewed.
    """
    report = ReassignmentReport()
    repairs = Repairs(session, "live upload", report, enabled=False)
    session.flush()
    for absorbed in absorbed_ids:
        session.execute(
            text("UPDATE conformer_observation SET conformer_group_id = :kept WHERE conformer_group_id = :old"),
            {"kept": kept_id, "old": absorbed},
        )
    move_dependents(
        session,
        list(absorbed_ids),
        target_for=lambda _group, _calculations, _observations: kept_id,
        repairs=repairs,
        report=report,
        scheme_id=scheme_id if scheme_id is not None else 0,
    )
    for absorbed in absorbed_ids:
        clear_representative(session, absorbed, merged_into=kept_id)
        reset_review(session, absorbed, report, repairs_revision="live upload")
    reset_review(session, kept_id, report, repairs_revision="live upload")
    # The statements above are SQL, so anything the session already holds about these rows is stale.
    session.expire_all()


def settle_conformer_basin(
    session: Session,
    observation: ConformerObservation,
    fingerprint: TorsionFingerprint | None,
    *,
    smiles: str | None,
) -> None:
    """Finish what ``resolve_conformer_group`` started, once the observation exists.

    Stores the observation's species-canonical coordinates, records any merge this role could not make (the owner
    role completes it), and re-derives the group's representative (medoid) and shape from its members. Call it right
    after the observation is flushed; the calculations need not be anchored yet.
    """
    placement = getattr(fingerprint, "placement", None)
    if placement is None or smiles is None:
        return
    assert isinstance(placement, BasinPlacement)
    observation.mapped_coords_json = placement.mapped_coords
    session.flush()
    for absorbed in placement.pending_absorbed:
        record_pending_merge(
            session,
            entry_id=placement.species_entry_id,
            kept=observation.conformer_group_id,
            absorbed=absorbed,
            observation_id=observation.id,
            reason=(
                "an upload links these two conformer groups, and approved records point at the younger one; "
                "only a declared repair may move them"
            ),
        )
    match_params = BasinMatchParams.from_scheme_params(placement.params)
    compute_missing = _structures_of(session, smiles, placement.params)
    refresh_group_shape(
        session,
        observation.conformer_group_id,
        smiles=smiles,
        params=match_params,
        compute_missing=compute_missing,
        overrides={observation.id: (placement.defining, placement.mapped_coords)},
        added=observation.id if placement.defining else None,
    )
    # The representative of a basin may have moved, or the partition changed: file the structures that define
    # nothing again, by the function the recompute uses, so that a recompute straight after changes nothing.
    refile_non_defining(
        session,
        placement.species_entry_id,
        smiles=smiles,
        params=match_params,
        compute_missing=compute_missing,
        defining_overrides={observation.id: placement.defining},
    )


def current_conformer_group_targets(session: Session, targets: Sequence[RecordRef]) -> list[RecordRef]:
    """``targets`` with the conformer-group refs replaced by where each conformer observation is NOW.

    A bundle collects its review targets as it goes, but a later conformer of the same request can link two basins
    and merge the younger into the older, and settling an upload can re-file a structure that defines nothing. The
    group captured when an observation was filed may then be an emptied row; the review belongs to the group that
    holds the observation at the end.
    """
    observation_ids = [t.record_id for t in targets if t.record_type is SubmissionRecordType.conformer_observation]
    groups = (
        sorted(
            set(
                session.scalars(
                    select(ConformerObservation.conformer_group_id).where(ConformerObservation.id.in_(observation_ids))
                )
            )
        )
        if observation_ids
        else []
    )
    kept = [t for t in targets if t.record_type is not SubmissionRecordType.conformer_group]
    return [*kept, *(RecordRef(SubmissionRecordType.conformer_group, g) for g in groups)]


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
