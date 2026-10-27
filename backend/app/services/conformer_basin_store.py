"""Database reads and writes behind conformer basin identity: which observations define a basin, where their
structures are, and how a group row records its own shape.

Defining members
----------------
Only a *converged optimised structure* may define or link a basin. An observation is defining when its anchored
primary calculation is an ``opt`` or a ``composite``, has a ``final`` output geometry, and does not say it failed to
converge (``converged IS NOT FALSE``: a depositor who did not state convergence is not accused of failing it).
Everything else (a single point, a scan point, a partial optimisation, a coarse pre-optimisation that a later
optimisation of the same observation started from) is still filed into a basin, but it never acts as a comparison
target, so it can never join two basins. Otherwise one scan point lying between two minima would merge them.

"Primary" is the same calculation the upload fingerprinted: among the calculations anchored to the observation, the
lowest-id one that carries a geometry, with a coarse parent of another anchored optimisation never counting first.

Why this lives apart from the matcher
-------------------------------------
:mod:`app.chemistry.basin_matching` and :mod:`app.chemistry.basin_linkage` are pure: coordinates in, answers out.
Everything that touches a table is here, shared by the live upload path (:mod:`app.services.conformer_resolution`)
and the recompute (:mod:`app.services.conformer_reassignment`), so the two read the same facts.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.chemistry.basin_linkage import (
    BasinShape,
    FilingBasin,
    PairTable,
    basin_shape,
    claim_rows,
    extend_shape,
    file_non_defining,
)
from app.chemistry.basin_matching import BasinMatchParams
from app.services.conformer_basin_moves import (
    ReassignmentReport,
    Repairs,
    clear_representative,
    ensure_review,
    move_dependents,
    reset_review,
)
from app.services.public_refs import make_opaque_ref

logger = logging.getLogger(__name__)

#: Calculation types whose converged result is a minimum geometry.
DEFINING_CALCULATION_TYPES = ("opt", "composite")


def is_defining_calculation(calculation_type: str, *, has_final_geometry: bool, converged: bool | None) -> bool:
    """Whether an observation anchored on this primary calculation defines a basin.

    ``calculation_type`` is the calculation type's value (``"opt"``). ``converged`` is the depositor's statement
    (``None`` when not stated), and only a stated failure disqualifies.
    """
    return calculation_type in DEFINING_CALCULATION_TYPES and has_final_geometry and converged is not False


@dataclass
class StoredObservation:
    id: int
    group_id: int
    created_by: int | None
    scheme_id: int | None
    fingerprint_json: dict | None
    mapped_coords: list | None


def _primary_rows(session: Session, observation_ids: Sequence[int]):
    """``(obs_id, geometry_id, defining)`` of each observation's primary calculation, where it has one."""
    if not observation_ids:
        return []
    return session.execute(
        text(
            """
            WITH anchored AS (
                SELECT c.id AS calc_id, c.conformer_observation_id AS obs_id
                  FROM calculation c
                 WHERE c.conformer_observation_id = ANY(:obs_ids)
            ), coarse_parents AS (
                SELECT DISTINCT d.parent_calculation_id AS calc_id
                  FROM calculation_dependency d
                  JOIN anchored child ON child.calc_id = d.child_calculation_id
                  JOIN anchored parent ON parent.calc_id = d.parent_calculation_id AND parent.obs_id = child.obs_id
                 WHERE d.dependency_role = 'optimized_from'
            ), carried AS (
                SELECT a.obs_id, a.calc_id, 0 AS source, o.output_order AS position, o.geometry_id
                  FROM anchored a
                  JOIN calculation_output_geometry o ON o.calculation_id = a.calc_id AND o.role = 'final'
                UNION ALL
                SELECT a.obs_id, a.calc_id, 1 AS source, i.input_order AS position, i.geometry_id
                  FROM anchored a
                  JOIN calculation_input_geometry i ON i.calculation_id = a.calc_id
            ), chosen AS (
                SELECT DISTINCT ON (obs_id) obs_id, calc_id, source, geometry_id
                  FROM carried
                 ORDER BY obs_id,
                          (calc_id IN (SELECT calc_id FROM coarse_parents)),
                          calc_id,
                          source,
                          position
            )
            SELECT ch.obs_id, ch.geometry_id,
                   (ch.source = 0
                    AND c.type IN ('opt', 'composite')
                    AND r.converged IS NOT FALSE) AS defining
              FROM chosen ch
              JOIN calculation c ON c.id = ch.calc_id
              LEFT JOIN calc_opt_result r ON r.calculation_id = c.id
            """
        ),
        {"obs_ids": list(observation_ids)},
    ).all()


def defining_observation_ids(session: Session, observation_ids: Sequence[int]) -> set[int]:
    """The observations among ``observation_ids`` that define a basin (see the module docstring)."""
    return {row.obs_id for row in _primary_rows(session, observation_ids) if row.defining}


def observation_geometry_atoms(
    session: Session, observation_ids: Sequence[int]
) -> dict[int, tuple[tuple[str, float, float, float], ...]]:
    """The geometry each observation was deposited with, as atom tuples, where it can be determined.

    This is the geometry the upload path fingerprinted. An upload attaches the geometry it was given to the
    observation's *primary* calculation (the first one created, so the lowest id): as that calculation's final
    output when it is an optimisation, otherwise as its input (a frequency or single point has no output unless
    it declares one). So: among the calculations anchored to the observation, take the lowest-id one that carries
    a geometry (a coarse parent of another anchored optimisation never counts first), and use its final output
    geometry if it has one, else its first input geometry. A later optimisation of the same observation is
    deliberately not used: it may be a different structure. An observation with no such calculation is
    undeterminable and is left alone by the caller. Coordinates are read from ``geometry_atom``, as for every
    stored geometry; the upload parsed the same numbers from ``xyz_text``.
    """
    rows = _primary_rows(session, observation_ids)
    geometry_of: dict[int, int] = {row.obs_id: row.geometry_id for row in rows}
    if not geometry_of:
        return {}
    atoms_by_geometry: dict[int, list[tuple[str, float, float, float]]] = defaultdict(list)
    for row in session.execute(
        text(
            "SELECT geometry_id, element, x, y, z FROM geometry_atom "
            "WHERE geometry_id = ANY(:ids) ORDER BY geometry_id, atom_index"
        ),
        {"ids": sorted(set(geometry_of.values()))},
    ):
        atoms_by_geometry[row.geometry_id].append((row.element.strip(), row.x, row.y, row.z))
    return {oid: tuple(atoms_by_geometry[gid]) for oid, gid in geometry_of.items() if atoms_by_geometry.get(gid)}


def load_entry_observations(session: Session, entry_id: int) -> list[StoredObservation]:
    """Every observation filed under a group of this species entry, oldest first."""
    return [
        StoredObservation(
            r.id, r.conformer_group_id, r.created_by, r.assignment_scheme_id, r.torsion_fingerprint_json, r.mapped_coords_json
        )
        for r in session.execute(
            text(
                "SELECT o.id, o.conformer_group_id, o.created_by, o.assignment_scheme_id, o.torsion_fingerprint_json, "
                "o.mapped_coords_json FROM conformer_observation o "
                "JOIN conformer_group g ON g.id = o.conformer_group_id "
                "WHERE g.species_entry_id = :e ORDER BY o.id"
            ),
            {"e": entry_id},
        )
    ]


def write_group_shape(
    session: Session,
    group_id: int,
    *,
    representative_observation_id: int | None,
    fingerprint_json: dict | None,
    coords: Sequence | None,
    shape: BasinShape | None,
) -> None:
    """Record a group's derived representative and shape. A group that has members is, by definition, not merged away."""
    session.execute(
        text(
            "UPDATE conformer_group SET representative_observation_id = :rep, "
            "representative_fingerprint_json = CAST(:fp AS jsonb), representative_coords_json = CAST(:coords AS jsonb), "
            "basin_is_clique = :clique, basin_span_deg = :span, basin_distance_sums_json = CAST(:sums AS jsonb), "
            "merged_into_conformer_group_id = NULL WHERE id = :id"
        ),
        {
            "id": group_id,
            "rep": representative_observation_id,
            "fp": json.dumps(fingerprint_json) if fingerprint_json is not None else None,
            "coords": json.dumps([list(c) for c in coords]) if coords is not None else None,
            "clique": shape.is_clique if shape is not None else None,
            "span": shape.span_deg if shape is not None else None,
            "sums": json.dumps({str(k): v for k, v in shape.distance_sums.items()}) if shape is not None else None,
        },
    )


def stored_group_shape(session: Session, group_id: int):
    return session.execute(
        text(
            "SELECT representative_observation_id AS rep, representative_fingerprint_json AS fp, "
            "representative_coords_json AS coords, basin_is_clique AS clique, basin_span_deg AS span, "
            "basin_distance_sums_json AS sums FROM conformer_group WHERE id = :id"
        ),
        {"id": group_id},
    ).first()


def refresh_group_shape(
    session: Session,
    group_id: int,
    *,
    smiles: str,
    params: BasinMatchParams,
    compute_missing: Callable[[list[int]], dict[int, tuple[dict | None, list | None]]],
    overrides: dict[int, tuple[bool, Sequence | None]] | None = None,
    added: int | None = None,
) -> BasinShape | None:
    """Re-derive a group's representative (medoid), clique flag and span from its members, and store them.

    ``compute_missing(observation_ids) -> {id: (fingerprint_json_or_None, coords)}`` supplies the structure of a
    member that has no stored ``mapped_coords_json`` (read from its geometry). ``overrides`` gives ``(defining,
    coords)`` for observations whose calculations are not anchored yet (the upload that has just been filed).

    A group with defining members is represented by their medoid. A group with none (a provisional basin of
    partly optimised structures) is represented by the first member filed, and carries no shape.

    ``added`` names a defining member that has just joined: when the group's stored per-member distance sums cover
    exactly the other defining members, the new shape is built from the ``k`` new comparisons alone
    (:func:`app.chemistry.basin_linkage.extend_shape`) and equals a full rebuild; otherwise (a merge changed the
    members, the sums were never stored) the pairs are all compared.
    """
    rows = session.execute(
        text(
            "SELECT id, torsion_fingerprint_json AS fp, mapped_coords_json AS coords "
            "FROM conformer_observation WHERE conformer_group_id = :g ORDER BY id"
        ),
        {"g": group_id},
    ).all()
    if not rows:
        return None
    overrides = overrides or {}
    ids = [r.id for r in rows]
    defining = defining_observation_ids(session, [i for i in ids if i not in overrides])
    for oid, (flag, _coords) in overrides.items():
        if oid in ids and flag:
            defining.add(oid)
    coords: dict[int, Sequence] = {}
    fingerprints: dict[int, dict | None] = {}
    missing: list[int] = []
    for r in rows:
        fingerprints[r.id] = r.fp
        if r.id in overrides and overrides[r.id][1] is not None:
            coords[r.id] = overrides[r.id][1]
        elif r.coords is not None:
            coords[r.id] = r.coords
        else:
            missing.append(r.id)
    if missing:
        for oid, (fp, structure) in compute_missing(missing).items():
            if structure is not None:
                coords[oid] = structure
                if fingerprints.get(oid) is None:
                    fingerprints[oid] = fp
    defining_with_coords = sorted(i for i in defining if i in coords)
    if defining_with_coords:
        table = PairTable(smiles, params, {i: [tuple(c) for c in coords[i]] for i in defining_with_coords})
        shape = None
        if added is not None and added in defining_with_coords:
            stored = stored_group_shape(session, group_id)
            prior = {int(k): float(v) for k, v in (stored.sums or {}).items()}
            if stored.clique is not None and stored.span is not None and set(prior) == set(defining_with_coords) - {added}:
                shape = extend_shape(
                    table,
                    BasinShape(
                        medoid_key=stored.rep or added,
                        is_clique=stored.clique,
                        span_deg=stored.span,
                        distance_sums=prior,
                    ),
                    added,
                )
        if shape is None:
            shape = basin_shape(table, defining_with_coords)
        rep = shape.medoid_key
    else:
        shape = None
        rep = next((i for i in ids if i in coords), None)
        if rep is None:
            return None
    write_group_shape(
        session,
        group_id,
        representative_observation_id=rep,
        fingerprint_json=fingerprints.get(rep),
        coords=coords[rep],
        shape=shape,
    )
    if shape is not None and not shape.is_clique:
        logger.warning(
            "conformer group %s holds optimised structures that are not all within tolerance of each other "
            "(span %.1f degrees); a wide basin is reported, never split",
            group_id, shape.span_deg,
        )
    return shape


def next_group_label(session: Session, entry_id: int) -> str:
    """The next unused ``conformer_N`` label for an entry (one past the highest in use, not the row count)."""
    labels = session.execute(
        text("SELECT label FROM conformer_group WHERE species_entry_id = :e"), {"e": entry_id}
    ).scalars().all()
    highest = 0
    for label in labels:
        if label and label.startswith("conformer_") and label[len("conformer_") :].isdigit():
            highest = max(highest, int(label[len("conformer_") :]))
    return f"conformer_{max(highest, len(labels)) + 1}"


def group_dependents_need_declared_repair(session: Session, group_id: int) -> bool:
    """Whether moving what points at this group would write a row the API role may not write.

    Only the rows a trigger refuses count: a statmech basin declaration or ensemble member (refused in every state), an
    approved thermo target (an accepted root), a selection an accepted kinetics record cites. An unapproved thermo
    target and an ordinary selection are moved by the API role, as an inline merge already does.
    """
    return bool(
        session.execute(
            text(
                """
                SELECT EXISTS (SELECT 1 FROM statmech_model_declaration WHERE conformer_group_id = :g)
                    OR EXISTS (SELECT 1 FROM statmech_model_member WHERE conformer_group_id = :g)
                    OR EXISTS (
                        SELECT 1 FROM thermo t
                          JOIN record_review r ON r.record_type = 'thermo' AND r.record_id = t.id
                         WHERE t.target_conformer_group_id = :g AND r.first_approved_at IS NOT NULL)
                    OR EXISTS (
                        SELECT 1 FROM conformer_selection s
                          JOIN kinetics_interpretation_assignment a ON a.conformer_selection_id = s.id
                          JOIN record_review r ON r.record_type = 'kinetics' AND r.record_id = a.kinetics_id
                         WHERE s.conformer_group_id = :g AND r.first_approved_at IS NOT NULL)
                """
            ),
            {"g": group_id},
        ).scalar()
    )


def observation_is_evidence_of_a_frozen_dependent(session: Session, observation_id: int, group_id: int) -> bool:
    """Whether a statmech declaration or member, or an approved thermo, filed on this group rests on this observation.

    By D5 a dependent follows the observation its own calculations came from, so a recompute that finds the observation
    elsewhere moves the dependent with it. A statmech record is frozen in every state and an approved thermo is an
    accepted root, so the API role cannot move them; an UNAPPROVED thermo it can.
    """
    return bool(
        session.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM thermo t
                      JOIN record_review r ON r.record_type = 'thermo' AND r.record_id = t.id
                      JOIN thermo_source_calculation x ON x.thermo_id = t.id
                      JOIN calculation c ON c.id = x.calculation_id
                     WHERE t.target_conformer_group_id = :g AND c.conformer_observation_id = :o
                       AND r.first_approved_at IS NOT NULL
                ) OR EXISTS (
                    SELECT 1 FROM statmech_model_declaration d
                      JOIN statmech_source_calculation x ON x.statmech_id = d.statmech_id
                      JOIN calculation c ON c.id = x.calculation_id
                     WHERE d.conformer_group_id = :g AND c.conformer_observation_id = :o
                ) OR EXISTS (
                    SELECT 1 FROM statmech_model_declaration d
                      JOIN statmech_model_source s ON s.declaration_id = d.id
                      JOIN calculation c ON c.id = s.calculation_id
                     WHERE d.conformer_group_id = :g AND c.conformer_observation_id = :o
                ) OR EXISTS (
                    SELECT 1 FROM statmech_model_member m
                      JOIN statmech_source_calculation x ON x.statmech_id = m.member_statmech_id
                      JOIN calculation c ON c.id = x.calculation_id
                     WHERE m.conformer_group_id = :g AND c.conformer_observation_id = :o
                )
                """
            ),
            {"g": group_id, "o": observation_id},
        ).scalar()
    )


def observation_is_accepted(session: Session, observation_id: int) -> bool:
    """Whether the observation was ever approved (and so can change only through a declared repair)."""
    return bool(
        session.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM record_review WHERE record_type = 'conformer_observation' "
                "AND record_id = :o AND first_approved_at IS NOT NULL)"
            ),
            {"o": observation_id},
        ).scalar()
    )


def live_move_refused(session: Session, observation_id: int, group_id: int, *, old_rep: int | None) -> str | None:
    """Why this role may not move a structure that defines nothing out of its group now, or ``None`` if it may.

    The same principle as for a merge: a move is owed only when a write it makes would be refused by a trigger. It writes
    the observation's group (refused once the observation was ever approved) and, by D5, whatever rests on it moves with
    it: statmech records (frozen in every state) and approved thermo targets (accepted roots). An unapproved thermo
    target and a selection are moved here, the way an inline merge moves them. What follows the observation is what rests
    on it as evidence, and, when the observation is the group's representative, everything with no evidence of its own.
    """
    if observation_is_accepted(session, observation_id):
        return "approved"
    if observation_is_evidence_of_a_frozen_dependent(session, observation_id, group_id):
        return "evidence_of_a_frozen_dependent"
    if old_rep == observation_id and group_dependents_need_declared_repair(session, group_id):
        return "representative_of_a_group_with_frozen_dependents"
    return None


def refile_non_defining(
    session: Session,
    entry_id: int,
    *,
    smiles: str,
    params: BasinMatchParams,
    compute_missing: Callable[[list[int]], dict[int, tuple[dict | None, list | None]]],
    defining_overrides: dict[int, bool] | None = None,
) -> int:
    """Re-file the entry's non-defining observations the way the recompute would, after the basins changed.

    A structure that defines nothing belongs to the basin whose representative it matches best, and a basin's
    representative moves when an optimised structure arrives (or when the partition changes). So after every upload
    the structures that define nothing are filed again, by the same function the recompute uses
    (:func:`app.chemistry.basin_linkage.file_non_defining`), against the groups as they now stand, so that a recompute
    straight afterwards changes nothing. A structure that must move but cannot be moved by this role (it was
    approved, or a thermo target or statmech declaration points at its group) stays where it is and is recorded in
    ``conformer_group_merge_pending`` for the owner-role reassignment.

    :returns: how many observations were moved.
    """
    observations = load_entry_observations(session, entry_id)
    overrides = defining_overrides or {}
    defining_ids = defining_observation_ids(session, [o.id for o in observations if o.id not in overrides])
    defining_ids |= {oid for oid, flag in overrides.items() if flag}
    non_defining = [o for o in observations if o.id not in defining_ids]
    if not non_defining:
        return 0
    coords: dict[int, Sequence] = {}
    missing: list[int] = []
    for o in observations:
        if o.mapped_coords is not None:
            coords[o.id] = o.mapped_coords
        else:
            missing.append(o.id)
    if missing:
        for oid, (_fp, structure) in compute_missing(missing).items():
            if structure is not None:
                coords[oid] = structure
    groups = session.execute(
        text(
            "SELECT id, created_by, representative_observation_id AS rep FROM conformer_group "
            "WHERE species_entry_id = :e ORDER BY id"
        ),
        {"e": entry_id},
    ).all()
    by_id = {o.id: o for o in observations}
    members_of: dict[int, list] = {g.id: [] for g in groups}
    for o in observations:
        members_of[o.group_id].append(o)

    basins: list[FilingBasin] = []
    for g in groups:
        defining_here = sorted(o.id for o in members_of[g.id] if o.id in defining_ids and o.id in coords)
        if defining_here:
            rep = g.rep if g.rep in defining_here else defining_here[0]
            basins.append(FilingBasin(index=len(basins), members=defining_here, rep=rep, has_defining=True, row=g.id))
    keys = [o.id for o in non_defining if o.id in coords]
    where = file_non_defining(smiles, params, basins, keys, coords)

    def old_rep(group_id: int) -> int | None:
        g = next(g for g in groups if g.id == group_id)
        if g.rep in by_id and g.rep in coords:
            return g.rep
        return next((o.id for o in members_of[group_id] if o.id in coords), None)

    claim_rows(
        basins,
        [g.id for g in groups],
        lambda gid: [where[o.id] for o in members_of[gid] if o.id in where and not where[o.id].has_defining],
        lambda gid: where.get(old_rep(gid)) if old_rep(gid) in where else None,
    )
    # Decide what may move BEFORE any row is created: a row made for a move that then turns out to be owed would stay
    # empty (and without a fingerprint) for ever, and the next upload would make another.
    movers: list[tuple] = []
    owed: dict[int, int] = {}
    for o in non_defining:
        basin = where.get(o.id)
        if basin is None or (basin.row is not None and basin.row == o.group_id):
            continue
        reason = live_move_refused(session, o.id, o.group_id, old_rep=old_rep(o.group_id))
        if reason is None:
            movers.append((o, basin))
        else:
            owed[o.id] = o.group_id
    # a re-file that is no longer needed (the structure now belongs where it is) is closed; the rest are updated in
    # place, one open row per observation
    session.execute(
        text(
            "UPDATE conformer_group_merge_pending SET completed_at = now(), completed_by_revision = 'live upload' "
            "WHERE species_entry_id = :e AND kept_conformer_group_id IS NULL AND completed_at IS NULL "
            "AND NOT (conformer_observation_id = ANY(:owed))"
        ),
        {"e": entry_id, "owed": list(owed)},
    )
    for obs_id, group_id in owed.items():
        record_pending_refile(session, entry_id=entry_id, observation_id=obs_id, current_group=group_id)

    created_by = next((g.created_by for g in groups), None)
    for _o, basin in movers:
        if basin.row is None:
            rep = by_id[basin.rep]
            basin.row = session.execute(
                text(
                    "INSERT INTO conformer_group (species_entry_id, label, created_by, representative_observation_id, "
                    "representative_fingerprint_json, representative_coords_json, public_ref) "
                    "VALUES (:e, :label, :cb, :rep, CAST(:fp AS jsonb), CAST(:coords AS jsonb), :ref) RETURNING id"
                ),
                {
                    "e": entry_id,
                    "label": next_group_label(session, entry_id),
                    "cb": created_by,
                    "rep": rep.id,
                    "fp": json.dumps(rep.fingerprint_json) if rep.fingerprint_json is not None else None,
                    "coords": json.dumps([list(c) for c in coords[rep.id]]),
                    "ref": make_opaque_ref("cg"),
                },
            ).scalar_one()
            ensure_review(session, basin.row, "Created by a live upload: a structure that defines no basin matched none.")

    report = ReassignmentReport()
    moved = 0
    touched: set[int] = set()
    new_group_of = {o.id: o.group_id for o in observations}
    old_group_of = dict(new_group_of)
    for o, basin in movers:
        session.execute(
            text("UPDATE conformer_observation SET conformer_group_id = :g WHERE id = :id"),
            {"g": basin.row, "id": o.id},
        )
        new_group_of[o.id] = basin.row
        touched |= {o.group_id, basin.row}
        moved += 1
    if not moved:
        return 0
    # What pointed at a group the moved structures left follows by D5, exactly as the recompute moves it: each record
    # goes to the group holding the observation its own calculations came from, else with the group's representative
    # (so a selection on an emptied group goes where the representative went).
    fallback = {g.id: (new_group_of[old_rep(g.id)] if old_rep(g.id) in new_group_of else g.id) for g in groups}

    def target_for(group_id: int, calculation_ids: Sequence[int], calc_observation: dict[int, int]) -> int:
        holders = {
            new_group_of[calc_observation[c]]
            for c in calculation_ids
            if c in calc_observation
            and calc_observation[c] in new_group_of
            and old_group_of.get(calc_observation[c]) == group_id
        }
        return next(iter(holders)) if len(holders) == 1 else fallback[group_id]

    scheme_id = session.execute(
        text("SELECT id FROM conformer_assignment_scheme WHERE is_default ORDER BY id DESC LIMIT 1")
    ).scalar()
    move_dependents(
        session,
        sorted({o.group_id for o, _ in movers}),
        target_for=target_for,
        repairs=Repairs(session, "live upload", report, enabled=False),
        report=report,
        scheme_id=scheme_id if scheme_id is not None else 0,
    )
    for gid in sorted(touched):
        remaining = session.execute(
            text("SELECT count(*) FROM conformer_observation WHERE conformer_group_id = :g"), {"g": gid}
        ).scalar()
        if not remaining:
            rep = old_rep(gid)
            new_home = next((b.row for b in basins if rep in b.members), None)
            clear_representative(session, gid, merged_into=new_home if new_home != gid else None)
        else:
            member_ids = {
                r[0]
                for r in session.execute(
                    text("SELECT id FROM conformer_observation WHERE conformer_group_id = :g"), {"g": gid}
                )
            }
            has_defining_member = bool(member_ids & defining_ids)
            if not has_defining_member:
                refresh_group_shape(session, gid, smiles=smiles, params=params, compute_missing=compute_missing)
        reset_review(session, gid, report, repairs_revision="live upload")
    session.flush()
    session.expire_all()
    return moved


# ---------------------------------------------------------------------------
# Merges that touch approved records
# ---------------------------------------------------------------------------


def merge_needs_declared_repair(session: Session, group_ids: Sequence[int]) -> bool:
    """Whether moving the members and dependents of these groups would write a row the database refuses to edit.

    The test is "would the trigger refuse this write?", not "is it approved?". A write the API role cannot make
    needs a declared repair, which only an owner role may declare. The rows a merge writes, and what guards them:

    * ``conformer_observation`` and ``thermo``: accepted-science roots, refused once approved at least once;
    * ``statmech_model_declaration`` and ``statmech_model_member``: refused in EVERY state
      (``tckdb_statmech_model_immutable``), approved or not, except ``conformer_group_id`` under a declared repair;
    * a ``conformer_selection`` that an accepted kinetics record cites: a curation row the trigger does not guard, but
      moving what an accepted record cites is not something this role decides alone;
    * ``conformer_group`` and ``record_review`` are not guarded.
    """
    if not group_ids:
        return False
    return bool(
        session.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1 FROM conformer_observation o
                      JOIN record_review r ON r.record_type = 'conformer_observation' AND r.record_id = o.id
                     WHERE o.conformer_group_id = ANY(:g) AND r.first_approved_at IS NOT NULL
                ) OR EXISTS (
                    SELECT 1 FROM thermo t
                      JOIN record_review r ON r.record_type = 'thermo' AND r.record_id = t.id
                     WHERE t.target_conformer_group_id = ANY(:g) AND r.first_approved_at IS NOT NULL
                ) OR EXISTS (
                    SELECT 1 FROM statmech_model_declaration d WHERE d.conformer_group_id = ANY(:g)
                ) OR EXISTS (
                    SELECT 1 FROM statmech_model_member m WHERE m.conformer_group_id = ANY(:g)
                ) OR EXISTS (
                    SELECT 1 FROM conformer_selection s
                      JOIN kinetics_interpretation_assignment a ON a.conformer_selection_id = s.id
                      JOIN record_review r ON r.record_type = 'kinetics' AND r.record_id = a.kinetics_id
                     WHERE s.conformer_group_id = ANY(:g) AND r.first_approved_at IS NOT NULL
                )
                """
            ),
            {"g": list(group_ids)},
        ).scalar()
    )


def record_pending_merge(
    session: Session, *, entry_id: int, kept: int, absorbed: int, observation_id: int, reason: str
) -> None:
    """Record a merge a live upload decided on but could not carry out (see ``ConformerGroupMergePending``)."""
    session.execute(
        text(
            "INSERT INTO conformer_group_merge_pending (species_entry_id, kept_conformer_group_id, "
            "absorbed_conformer_group_id, conformer_observation_id, reason) "
            "VALUES (:e, :k, :a, :o, :reason) ON CONFLICT ON CONSTRAINT uq_conformer_group_merge_pending_merge DO NOTHING"
        ),
        {"e": entry_id, "k": kept, "a": absorbed, "o": observation_id, "reason": reason},
    )


def record_pending_refile(session: Session, *, entry_id: int, observation_id: int, current_group: int) -> None:
    """Record that a structure that defines no basin must be re-filed by the owner role. One open row per observation.

    There is no target group: the basin it belongs in may have no row yet, and none is created until the owner role
    completes the re-file. A later upload that still finds the move owed updates this row; it never adds another.
    """
    session.execute(
        text(
            "INSERT INTO conformer_group_merge_pending (species_entry_id, kept_conformer_group_id, "
            "absorbed_conformer_group_id, conformer_observation_id, reason) "
            "VALUES (:e, NULL, :g, :o, :reason) "
            "ON CONFLICT (conformer_observation_id) WHERE kept_conformer_group_id IS NULL AND completed_at IS NULL "
            "DO UPDATE SET absorbed_conformer_group_id = EXCLUDED.absorbed_conformer_group_id, reason = EXCLUDED.reason"
        ),
        {
            "e": entry_id,
            "g": current_group,
            "o": observation_id,
            "reason": (
                "a structure that defines no basin belongs in a different basin than the group it sits in, and an "
                "approved record or a record resting on it forbids this role to move it; the owner-role "
                "reassignment re-files it"
            ),
        },
    )


def entries_with_pending_merges(session: Session) -> list[int]:
    """Species entries that owe a merge: the input of the owner-role job that completes them."""
    return [
        r[0]
        for r in session.execute(
            text(
                "SELECT DISTINCT species_entry_id FROM conformer_group_merge_pending "
                "WHERE completed_at IS NULL ORDER BY species_entry_id"
            )
        )
    ]
