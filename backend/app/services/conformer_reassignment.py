"""Recompute which conformational basin every existing conformer observation belongs to.

Basin identity is a function of a scheme (``conformer_assignment_scheme``): change the rule and every stored
assignment is a statement about the old rule. This module re-derives them under the current default scheme,
for every species entry, and moves whatever pointed at a basin that changed.

It exists because a new scheme version does nothing for data already deposited, and because a live upload cannot
complete a merge that touches approved records (it records a ``conformer_group_merge_pending`` row instead). Run
inside the Alembic revision that adds a version, so a normal deploy reassigns without anyone remembering to; run it
over the entries that owe a merge (:func:`app.services.conformer_basin_store.entries_with_pending_merges`) to
complete those.

What it does, per species entry
-------------------------------
Under the entry's per-entry advisory lock (the one live uploads take, so the two cannot interleave):

1. Recompute each observation's mapped geometry and fingerprint from the geometry its own calculations
   produced.
2. Form the basins: the **connected components** of the entry's *defining* observations (converged optimisations,
   :mod:`app.services.conformer_basin_store`), two linked when the matcher says they share a basin. A basin is a
   function of the set, so there is no order to reproduce. A component that is not a clique (a chain whose ends
   are further apart than the tolerance) is a *wide basin*: counted and logged, never split. Observations that do
   not define anything are filed into the basin whose representative they match best (ties: the older group); one
   matching none starts a *provisional* basin, matched by its own geometry.
3. Give each basin a row. A basin keeps the oldest existing row that holds any of its defining members and whose
   previous representative it contains (the older group claims first, so a merge lands on the older group and a
   split leaves the original row with the part that holds the original representative); a basin with no claimant
   gets a new row.
4. Move the observations (their basin link, scheme, stored fingerprint and mapped coordinates; nothing else).
5. Move what pointed at a basin that changed, by D5: each thermo target goes to the basin holding the
   observation its own calculations came from; where that cannot be determined (no calculations, none
   anchored to a basin of this entry, or they span several new basins) it goes with the basin that holds
   the old group's representative. Conformer selections carry no calculations and follow the representative,
   re-stamped with the new scheme; one that would collide with a selection already on the target is left where
   it is, because a selection is a curation row and nothing may delete it. Statmech basin declarations and ensemble
   members follow their own statmech record's calculations, and a declaration's ``content_hash`` is recomputed in
   the same repair. See :mod:`app.services.conformer_basin_moves`.
6. Store each group's representative (the medoid of its defining members, derived and never identity) with the
   observation it came from, and whether the group is a clique and how far it spans.
7. Reset the review of every group whose membership changed (gained, lost, or emptied) to ``not_reviewed``, with a
   ``record_review_event``.
8. Never delete a group. A group a merge empties keeps its row, review history and public ref, with its
   representative cleared so nothing matches against it, and ``merged_into_conformer_group_id`` naming the group
   that took its members. Pending merges of the entry are stamped complete.

Frozen rows
-----------
``conformer_observation`` and ``thermo`` are accepted-science roots, so once approved the database refuses to UPDATE
them; the only sanctioned in-place change is a *declared repair* (``accepted_science_repair``), made here and only
here because it needs a role that owns the table. This change qualifies because it corrects which basin a record is
filed under and alters no scientific value. The trigger is never disabled or bypassed.

Idempotent: the grouping is a deterministic function of the observations, so a second run changes nothing.

Writes use SQL, not the ORM: the rows touched include accepted-science rows, and a declared repair covers exactly
the columns named. The migration that runs this must not be coupled to the ORM models of a later revision, either.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.chemistry.basin_linkage import BasinShape, PairTable, basin_shape, connected_components
from app.chemistry.basin_matching import BasinMatchParams, compare_basins_symmetric, comparison_distance
from app.chemistry.torsion_fingerprint import TorsionFingerprint
from app.services.conformer_basin_moves import (
    OBSERVATION_COLUMNS,
    ReassignmentReport,
    Repairs,
    clear_representative,
    ensure_review,
    move_dependents,
    reset_review,
)
from app.services.conformer_basin_store import (
    defining_observation_ids,
    load_entry_observations,
    observation_geometry_atoms,
    write_group_shape,
)
from app.services.conformer_resolution import (
    TORSION_BASIN_PARAMS,
    TORSION_BASIN_SCHEME_NAME,
    TORSION_BASIN_SCHEME_VERSION,
    compute_fingerprint_for_observation,
    lock_basin_resolution,
)
from app.services.public_refs import make_opaque_ref

logger = logging.getLogger(__name__)

__all__ = ["ReassignmentReport", "reassign_conformer_groups"]


@dataclass
class _Observation:
    id: int
    group_id: int
    created_by: int | None
    scheme_id: int | None
    fingerprint_json: dict | None
    stored_coords: list | None
    defining: bool = False
    fingerprint: TorsionFingerprint | None = None
    coords: list[tuple[float, float, float]] | None = None

    @property
    def eligible(self) -> bool:
        return self.fingerprint is not None and self.coords is not None

    @property
    def mapped_json(self) -> list:
        assert self.coords is not None
        return [list(c) for c in self.coords]


@dataclass
class _Basin:
    """A basin as the recompute finds it, before it is given a row."""

    index: int
    members: list[_Observation] = field(default_factory=list)
    shape: BasinShape | None = None
    #: The representative: the medoid of the defining members, else the first member filed.
    rep: _Observation | None = None

    @property
    def has_defining(self) -> bool:
        return any(o.defining for o in self.members)


def reassign_conformer_groups(
    session: Session,
    *,
    repair_revision: str,
    species_entry_ids: Sequence[int] | None = None,
) -> ReassignmentReport:
    """Reassign every observation of the given species entries (default: all) under the default scheme.

    :param session: A session inside the transaction that owns the repair declarations; the caller commits.
    :param repair_revision: Recorded as ``alembic_revision`` on each declared repair.
    :param species_entry_ids: Restrict to these entries; ``None`` means every entry that has a conformer group.
    :raises RuntimeError: If no default conformer_assignment_scheme exists.
    """
    report = ReassignmentReport()
    scheme = session.execute(
        text(
            "SELECT id, name, version, parameters_json FROM conformer_assignment_scheme "
            "WHERE is_default ORDER BY id DESC LIMIT 1"
        )
    ).first()
    if scheme is None:
        raise RuntimeError("No default conformer_assignment_scheme to reassign under")
    if (scheme.name, scheme.version) != (TORSION_BASIN_SCHEME_NAME, TORSION_BASIN_SCHEME_VERSION):
        # This code implements exactly one rule. Run against a database whose default is another scheme (an earlier
        # version, whose own revision already ran its own step, or a later one) it would stamp observations with a
        # rule it did not apply, so it declines.
        report.skipped_reason = (
            f"the default conformer_assignment_scheme is ({scheme.name}, {scheme.version}); this code implements "
            f"({TORSION_BASIN_SCHEME_NAME}, {TORSION_BASIN_SCHEME_VERSION})"
        )
        logger.warning("conformer reassignment skipped: %s", report.skipped_reason)
        return report
    params = dict(TORSION_BASIN_PARAMS)
    params.update(scheme.parameters_json or {})
    repairs = Repairs(session, repair_revision, report)

    if species_entry_ids is None:
        entry_ids = [
            row[0]
            for row in session.execute(
                text("SELECT DISTINCT species_entry_id FROM conformer_group ORDER BY species_entry_id")
            )
        ]
    else:
        entry_ids = sorted(set(species_entry_ids))

    for entry_id in entry_ids:
        _reassign_entry(session, entry_id, scheme_id=scheme.id, params=params, repairs=repairs, report=report)
    return report


def _normalised(value) -> str:
    return json.dumps(value, sort_keys=True)


def _reassign_entry(
    session: Session,
    entry_id: int,
    *,
    scheme_id: int,
    params: dict,
    repairs: Repairs,
    report: ReassignmentReport,
) -> None:
    lock_basin_resolution(session, entry_id)
    report.entries_examined += 1

    groups = session.execute(
        text(
            "SELECT id, label, created_by, representative_observation_id AS rep_obs, "
            "representative_fingerprint_json AS fp, representative_coords_json AS coords, "
            "basin_is_clique AS clique, basin_span_deg AS span, merged_into_conformer_group_id AS merged_into "
            "FROM conformer_group WHERE species_entry_id = :e ORDER BY id"
        ),
        {"e": entry_id},
    ).all()
    group_ids = [g.id for g in groups]
    observations = [
        _Observation(o.id, o.group_id, o.created_by, o.scheme_id, o.fingerprint_json, o.mapped_coords)
        for o in load_entry_observations(session, entry_id)
    ]
    if not observations:
        return
    report.observations_examined += len(observations)

    smiles = session.execute(
        text("SELECT sp.smiles FROM species_entry se JOIN species sp ON sp.id = se.species_id WHERE se.id = :e"),
        {"e": entry_id},
    ).scalar()
    ids = [o.id for o in observations]
    atoms = observation_geometry_atoms(session, ids)
    defining_ids = defining_observation_ids(session, ids)
    if smiles is not None:
        for obs in observations:
            if obs.id in atoms:
                obs.fingerprint, obs.coords = compute_fingerprint_for_observation(smiles, atoms[obs.id], params)
            obs.defining = obs.id in defining_ids
    report.observations_unresolved += sum(1 for o in observations if not o.eligible)
    eligible = [o for o in observations if o.eligible]
    for obs in eligible:
        assert obs.fingerprint is not None
        if obs.fingerprint.symmetry_cap_hit:
            report.symmetry_cap_hits += 1
    by_id = {o.id: o for o in observations}
    match_params = BasinMatchParams.from_scheme_params(params)

    # --- 1. Basins: connected components of the defining members. ---
    basins: list[_Basin] = []
    basin_of: dict[int, _Basin] = {}
    defining = [o for o in eligible if o.defining]
    if defining:
        assert smiles is not None
        table = PairTable(smiles, match_params, {o.id: o.coords for o in defining})  # type: ignore[misc]
        for component in connected_components(table, [o.id for o in defining]):
            basin = _Basin(index=len(basins), members=[by_id[i] for i in component])
            basin.shape = basin_shape(table, component)
            basin.rep = by_id[basin.shape.medoid_key]
            basins.append(basin)
            for obs in basin.members:
                basin_of[obs.id] = basin

    # --- 2. Rows for the basins that hold defining members: the older group claims first. ---
    old_rep_obs: dict[int, int | None] = {}
    members_of_group: dict[int, list[_Observation]] = defaultdict(list)
    for obs in observations:
        members_of_group[obs.group_id].append(obs)
    for g in groups:
        first_eligible = next((o.id for o in members_of_group[g.id] if o.eligible), None)
        old_rep_obs[g.id] = g.rep_obs if g.rep_obs in by_id and by_id[g.rep_obs].eligible else first_eligible

    row_of_basin: dict[int, int] = {}  # basin.index -> group id

    def claim(candidate_basins_of) -> None:
        for g in groups:  # ascending id: the older group claims a basin first
            candidates = {b.index: b for b in candidate_basins_of(g) if b.index not in row_of_basin}
            if not candidates:
                continue
            held = basin_of.get(old_rep_obs[g.id]) if old_rep_obs[g.id] is not None else None
            if held is not None and held.index in candidates:
                chosen = held
            else:
                chosen = min(candidates.values(), key=lambda b: min(o.id for o in b.members))
            row_of_basin[chosen.index] = g.id

    claim(lambda g: [basin_of[o.id] for o in members_of_group[g.id] if o.defining and o.eligible])

    # --- 3. File the observations that define nothing: into the basin whose representative they match best. ---
    def distance_to(basin: _Basin, obs: _Observation) -> float | None:
        assert basin.rep is not None and smiles is not None
        comparison = compare_basins_symmetric(smiles, basin.rep.coords, obs.coords, match_params)  # type: ignore[arg-type]
        return comparison_distance(comparison) if comparison.same_basin else None

    def older_rank(basin: _Basin) -> tuple[int, int]:
        row = row_of_basin.get(basin.index)
        return (0, row) if row is not None else (1, min(o.id for o in basin.members))

    for obs in (o for o in eligible if not o.defining):
        scored = [(d, older_rank(b), b) for b in basins if (d := distance_to(b, obs)) is not None]
        if scored:
            target = min(scored, key=lambda t: (t[0], t[1]))[2]
        else:
            target = _Basin(index=len(basins), rep=obs)
            basins.append(target)
        target.members.append(obs)
        basin_of[obs.id] = target
    claim(lambda g: [basin_of[o.id] for o in members_of_group[g.id] if o.eligible and not basin_of[o.id].has_defining])

    # Unclaimed basins get a provisional (negative) row id until it is certain this entry will be written.
    provisional = {basin.index: -(basin.index + 1) for basin in basins if basin.index not in row_of_basin}
    row_of_basin.update(provisional)
    new_group_of_obs: dict[int, int] = {o.id: row_of_basin[basin_of[o.id].index] for o in eligible}
    old_group_of_obs: dict[int, int] = {o.id: o.group_id for o in observations}
    fallback_of_group: dict[int, int] = {}

    def refresh_fallbacks() -> None:
        for g in groups:
            rep = old_rep_obs[g.id]
            fallback_of_group[g.id] = new_group_of_obs[rep] if rep is not None else g.id

    refresh_fallbacks()

    def target_for(group_id: int, calculation_ids: Sequence[int], calc_observation: dict[int, int]) -> int:
        """The basin holding the observation a dependent's own calculations came from; else the representative's."""
        holders = {
            new_group_of_obs[calc_observation[c]]
            for c in calculation_ids
            if c in calc_observation
            and calc_observation[c] in new_group_of_obs
            and old_group_of_obs.get(calc_observation[c]) == group_id
        }
        if len(holders) == 1:
            return next(iter(holders))
        return fallback_of_group[group_id]

    def final_group(obs: _Observation) -> int:
        return new_group_of_obs.get(obs.id, obs.group_id)

    # --- 4. Write: new rows first, then observations, then what pointed at a basin. ---
    taken_numbers = {
        int(g.label[len("conformer_") :])
        for g in groups
        if g.label and g.label.startswith("conformer_") and g.label[len("conformer_") :].isdigit()
    }
    next_number = max(taken_numbers | {len(groups)}) + 1
    new_rows: list[int] = []
    for basin in basins:
        if basin.index not in provisional:
            continue
        assert basin.rep is not None and basin.rep.fingerprint is not None
        created = session.execute(
            text(
                "INSERT INTO conformer_group (species_entry_id, label, created_by, representative_fingerprint_json, "
                "representative_coords_json, public_ref) "
                "VALUES (:e, :label, :cb, CAST(:fp AS jsonb), CAST(:coords AS jsonb), :ref) RETURNING id"
            ),
            {
                "e": entry_id,
                "label": f"conformer_{next_number}",
                "cb": basin.rep.created_by,
                "fp": json.dumps(basin.rep.fingerprint.to_dict()),
                "coords": json.dumps(basin.rep.mapped_json),
                # The ORM mints this on insert; raw SQL would leave the server-side placeholder.
                "ref": make_opaque_ref("cg"),
            },
        ).scalar_one()
        next_number += 1
        row_of_basin[basin.index] = created
        new_rows.append(created)
        report.groups_created += 1
        ensure_review(session, created, "Created by conformer reassignment: a basin split out of an existing group.")
    for o in eligible:
        new_group_of_obs[o.id] = row_of_basin[basin_of[o.id].index]
    refresh_fallbacks()

    changed = False
    for obs in eligible:
        assert obs.fingerprint is not None
        target = new_group_of_obs[obs.id]
        new_fp_json = obs.fingerprint.to_dict()
        coords_json = obs.mapped_json
        same_coords = obs.stored_coords is not None and _normalised(obs.stored_coords) == _normalised(coords_json)
        if (
            target == obs.group_id
            and obs.scheme_id == scheme_id
            and obs.fingerprint_json == new_fp_json
            and same_coords
        ):
            continue
        repairs.declare("conformer_observation", OBSERVATION_COLUMNS)
        session.execute(
            text(
                "UPDATE conformer_observation SET conformer_group_id = :g, assignment_scheme_id = :s, "
                "torsion_fingerprint_json = CAST(:fp AS jsonb), mapped_coords_json = CAST(:coords AS jsonb) "
                "WHERE id = :id"
            ),
            {"g": target, "s": scheme_id, "fp": json.dumps(new_fp_json), "coords": json.dumps(coords_json), "id": obs.id},
        )
        if target != obs.group_id:
            report.observations_moved += 1
        else:
            report.observations_restamped += 1
        if not same_coords:
            report.mapped_coords_filled += 1
        changed = True

    moved_any = move_dependents(
        session, group_ids, target_for=target_for, repairs=repairs, report=report, scheme_id=scheme_id
    )
    changed = changed or moved_any

    # --- 5. Representatives and shapes, review resets, emptied groups, pending merges. ---
    members_after: dict[int, set[int]] = defaultdict(set)
    for obs in observations:
        members_after[final_group(obs)].add(obs.id)
    members_before: dict[int, set[int]] = defaultdict(set)
    for obs in observations:
        members_before[obs.group_id].add(obs.id)

    stored = {g.id: g for g in groups}
    for basin in basins:
        row = row_of_basin[basin.index]
        assert basin.rep is not None and basin.rep.fingerprint is not None
        fingerprint_json = basin.rep.fingerprint.to_dict()
        coords_json = basin.rep.mapped_json
        shape = basin.shape if basin.has_defining else None
        if row in stored:
            before = stored[row]
            unchanged = (
                before.rep_obs == basin.rep.id
                and before.merged_into is None
                and _normalised(before.fp) == _normalised(fingerprint_json)
                and _normalised(before.coords) == _normalised(coords_json)
                and before.clique == (shape.is_clique if shape else None)
                and (before.span == shape.span_deg if shape else before.span is None)
            )
            if unchanged:
                continue
        write_group_shape(
            session,
            row,
            representative_observation_id=basin.rep.id,
            fingerprint_json=fingerprint_json,
            coords=coords_json,
            shape=shape,
        )
        if shape is not None and not shape.is_clique:
            # A wide basin: a chain of optimised structures whose ends are further apart than the tolerance. It is
            # reported and stored (``basin_is_clique = false``), never split.
            report.wide_basins += 1
            logger.warning(
                "species entry %s: conformer group %s holds %d optimised structures that are not all within tolerance "
                "of each other (span %.1f degrees); not split",
                entry_id, row, sum(1 for o in basin.members if o.defining), shape.span_deg,
            )
        changed = True

    for g in groups:
        before = members_before[g.id]
        after = members_after.get(g.id, set())
        if before == after:
            continue
        changed = True
        if not after:
            # Emptied. The row stays: deleting it would orphan its review and event history and retire a public
            # ref someone may have cited. It keeps no representative, so nothing is matched against it, and it
            # names the group that took its members.
            survivor = fallback_of_group[g.id]
            clear_representative(session, g.id, merged_into=survivor if survivor != g.id else None)
            report.groups_emptied_kept += 1
        elif not any(o.eligible and final_group(o) == g.id for o in observations):
            # Only members the recompute could not place remain: the old representative no longer describes them.
            clear_representative(session, g.id)
        reset_review(session, g.id, report, repairs_revision=repairs.revision)

    completed = session.execute(
        text(
            "UPDATE conformer_group_merge_pending SET completed_at = now(), completed_by_revision = :rev "
            "WHERE species_entry_id = :e AND completed_at IS NULL"
        ),
        {"e": entry_id, "rev": repairs.revision[:64]},
    ).rowcount
    if completed:
        report.merges_pending_completed += completed
        changed = True

    if changed:
        report.entries_changed += 1
