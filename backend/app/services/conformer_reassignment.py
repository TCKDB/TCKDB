"""Recompute which conformational basin every existing conformer observation belongs to.

Basin identity is a function of a scheme (``conformer_assignment_scheme``): change the rule and every stored
assignment is a statement about the old rule. This module re-derives them under the current default scheme,
for every species entry, and moves whatever pointed at a basin that changed.

It exists because a new scheme version does nothing for data already deposited. Run inside the Alembic
revision that adds the version, so a normal deploy reassigns without anyone remembering to.

What it does, per species entry
-------------------------------
Under the entry's per-entry advisory lock (the one live uploads take, so the two cannot interleave):

1. Recompute each observation's mapped geometry and fingerprint from the geometry its own calculations
   produced, and group the observations exactly as live uploads would have: in id order, each joining the
   closest existing basin that matches and otherwise starting one. The matcher is
   :func:`app.services.conformer_resolution.find_matching_basin`, the same function the upload path calls.
2. Give each resulting basin a row. A basin keeps the oldest existing row whose *representative*
   observation it contains, so a merge lands on the older group and a split leaves the original row with
   the part that holds the original representative; parts with no such claim get new rows.
3. Move the observations (their basin link, scheme and stored fingerprint; nothing else).
4. Move what pointed at a basin that changed, by D5: each thermo target goes to the basin holding the
   observation its own calculations came from; where that cannot be determined (no calculations, none
   anchored to a basin of this entry, or they span several new basins) it goes with the basin that holds
   the old group's representative. Conformer selections carry no calculations and follow the representative,
   re-stamped with the new scheme; one that would collide with a selection already on the target is left where
   it is, because a selection is a curation row and nothing may delete it.
5. Reset the review of every group whose membership changed (gained, lost, or emptied) to ``not_reviewed``, with a
   ``record_review_event``.
6. Never delete a group. A group a merge empties keeps its row, review history and public ref, with its
   representative cleared so nothing matches against it.

Statmech basin declarations and ensemble members
------------------------------------------------
``statmech_model_declaration.conformer_group_id`` and ``statmech_model_member.conformer_group_id`` name a basin.
Those two tables are immutable by their own trigger (``tckdb_statmech_model_immutable``) except for that column
under a declared repair (revision ``a5d9c3e7b1f2``): the same mechanism as the thermo target, for the same reason
(which basin is identity, not a stated value). They move by D5 like a thermo target, using the calculations of the
declaring statmech record (its source calculations and the declaration's own sources; for a member, the member
record's source calculations). A declaration's ``content_hash`` covers its basin and each member's basin, so it is
recomputed in the same repair for every declaration whose own basin or a member's basin moved.

Frozen rows
-----------
``conformer_observation`` and ``thermo`` are accepted-science roots (and the statmech declaration and its members are
frozen with their statmech record): once approved, the database refuses to
UPDATE them. The only sanctioned in-place change is a *declared repair*
(``accepted_science_repair``, revision ``e2c9a4f7b163``): declared in the same transaction, limited to
named columns, every changed accepted row recorded old -> new. This change qualifies because it corrects
which basin a record is filed under and alters no scientific value. It is declared per table, the first time
a write to that table is needed, and the trigger is never disabled or bypassed. A declaration needs a role
that owns the table, which is why this runs in a migration and not in the API process.

Idempotent: the grouping is a deterministic function of the observations, so a second run changes nothing.

Writes use SQL, not the ORM: the rows touched include accepted-science rows, and a declared repair covers exactly
the columns named, so the statements say exactly which columns they set. The migration that runs this must not
be coupled to the ORM models of a later revision, either.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.chemistry.torsion_fingerprint import TorsionFingerprint
from app.services.conformer_resolution import (
    TORSION_BASIN_PARAMS,
    TORSION_BASIN_SCHEME_NAME,
    TORSION_BASIN_SCHEME_VERSION,
    compute_fingerprint_for_observation,
    find_matching_basin,
    lock_basin_resolution,
)
from app.services.public_refs import make_opaque_ref

logger = logging.getLogger(__name__)

#: Columns a reassignment of observations may change (and so the columns its repair declares). The fingerprint is
#: derived from the geometry and the scheme, so it is regenerated with the scheme link; no scientific value moves.
OBSERVATION_COLUMNS = ("conformer_group_id", "assignment_scheme_id", "torsion_fingerprint_json")
THERMO_COLUMNS = ("target_conformer_group_id",)
STATMECH_MEMBER_COLUMNS = ("conformer_group_id",)
#: A declaration's checksum covers its basin and each member's basin, so it is declared with the basin and follows it.
STATMECH_DECLARATION_COLUMNS = ("conformer_group_id", "content_hash")

_REASON = (
    "Reassign conformer observations to their basins under the new default conformer_assignment_scheme "
    "({revision}). Changes which conformer group a record is filed under (columns: {columns}); no "
    "measured, computed or declared scientific value changes."
)


@dataclass
class ReassignmentReport:
    """What a reassignment did. Counts only; nothing here is a result."""

    entries_examined: int = 0
    entries_changed: int = 0
    observations_examined: int = 0
    observations_unresolved: int = 0
    observations_moved: int = 0
    observations_restamped: int = 0
    groups_created: int = 0
    groups_emptied_kept: int = 0
    groups_review_reset: int = 0
    thermo_moved: int = 0
    statmech_declarations_moved: int = 0
    statmech_members_moved: int = 0
    selections_moved: int = 0
    selections_left_duplicate: int = 0
    symmetry_cap_hits: int = 0
    repairs_declared: list[str] = field(default_factory=list)
    #: Why nothing was done, when the step declined to run (the default scheme is not the one this code implements).
    skipped_reason: str | None = None

    def summary(self) -> str:
        counts = {k: v for k, v in self.__dict__.items() if isinstance(v, int)}
        text_ = ", ".join(f"{name}={value}" for name, value in counts.items())
        text_ += f", repairs_declared={self.repairs_declared}, skipped_reason={self.skipped_reason!r}"
        return text_


@dataclass
class _Observation:
    id: int
    group_id: int
    created_by: int | None
    scheme_id: int | None
    fingerprint_json: dict | None
    fingerprint: TorsionFingerprint | None = None
    coords: list[tuple[float, float, float]] | None = None

    @property
    def eligible(self) -> bool:
        return self.fingerprint is not None and self.coords is not None


@dataclass
class _Basin:
    """A basin as the recompute finds it, before it is given a row."""

    index: int
    rep: _Observation
    members: list[_Observation] = field(default_factory=list)

    @property
    def fingerprint_json(self) -> dict:
        assert self.rep.fingerprint is not None
        return self.rep.fingerprint.to_dict()


class _Repairs:
    """Declares each accepted-science repair once per transaction, the first time its table is written."""

    def __init__(self, session: Session, revision: str, report: ReassignmentReport) -> None:
        self._session = session
        self.revision = revision
        self._report = report
        self._declared: set[str] = set()

    def declare(self, table: str, columns: Sequence[str]) -> None:
        if table in self._declared:
            return
        already = self._session.execute(
            text(
                "SELECT 1 FROM accepted_science_repair WHERE target_table = :t "
                "AND xact_id = (pg_current_xact_id())::text::bigint"
            ),
            {"t": table},
        ).first()
        if already is None:
            self._session.execute(
                text(
                    "INSERT INTO accepted_science_repair (target_table, declared_columns, alembic_revision, reason) "
                    "VALUES (:t, CAST(:cols AS text[]), :rev, :reason)"
                ),
                {
                    "t": table,
                    "cols": "{" + ",".join(columns) + "}",
                    "rev": self.revision,
                    "reason": _REASON.format(revision=self.revision, columns=", ".join(columns)),
                },
            )
            self._report.repairs_declared.append(table)
        self._declared.add(table)


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
        # This code implements exactly one rule. Run against a database whose default is another scheme (a later
        # version, with its own revision and its own data step) it would stamp observations with a rule it did not
        # apply, so it declines.
        report.skipped_reason = (
            f"the default conformer_assignment_scheme is ({scheme.name}, {scheme.version}); this code implements "
            f"({TORSION_BASIN_SCHEME_NAME}, {TORSION_BASIN_SCHEME_VERSION})"
        )
        logger.warning("conformer reassignment skipped: %s", report.skipped_reason)
        return report
    params = dict(TORSION_BASIN_PARAMS)
    params.update(scheme.parameters_json or {})
    repairs = _Repairs(session, repair_revision, report)

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


def _observation_geometry_atoms(
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
    if not observation_ids:
        return {}
    rows = session.execute(
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
            )
            SELECT DISTINCT ON (obs_id) obs_id, geometry_id
              FROM carried
             ORDER BY obs_id,
                      (calc_id IN (SELECT calc_id FROM coarse_parents)),
                      calc_id,
                      source,
                      position
            """
        ),
        {"obs_ids": list(observation_ids)},
    ).all()
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


def _reassign_entry(
    session: Session,
    entry_id: int,
    *,
    scheme_id: int,
    params: dict,
    repairs: _Repairs,
    report: ReassignmentReport,
) -> None:
    lock_basin_resolution(session, entry_id)
    report.entries_examined += 1

    groups = session.execute(
        text(
            "SELECT id, label, created_by, representative_fingerprint_json AS fp, "
            "representative_coords_json AS coords FROM conformer_group WHERE species_entry_id = :e ORDER BY id"
        ),
        {"e": entry_id},
    ).all()
    group_ids = [g.id for g in groups]
    observations = [
        _Observation(r.id, r.conformer_group_id, r.created_by, r.assignment_scheme_id, r.torsion_fingerprint_json)
        for r in session.execute(
            text(
                "SELECT id, conformer_group_id, created_by, assignment_scheme_id, torsion_fingerprint_json "
                "FROM conformer_observation WHERE conformer_group_id = ANY(:g) ORDER BY id"
            ),
            {"g": group_ids},
        )
    ]
    if not observations:
        return
    report.observations_examined += len(observations)

    smiles = session.execute(
        text("SELECT sp.smiles FROM species_entry se JOIN species sp ON sp.id = se.species_id WHERE se.id = :e"),
        {"e": entry_id},
    ).scalar()
    atoms = _observation_geometry_atoms(session, [o.id for o in observations])
    if smiles is not None:
        for obs in observations:
            if obs.id in atoms:
                obs.fingerprint, obs.coords = compute_fingerprint_for_observation(smiles, atoms[obs.id], params)
    report.observations_unresolved += sum(1 for o in observations if not o.eligible)

    # --- 1. Group the observations as live uploads would have: in id order, joining the closest match. ---
    basins: list[_Basin] = []
    basin_of: dict[int, _Basin] = {}
    for obs in (o for o in observations if o.eligible):
        assert smiles is not None and obs.fingerprint is not None
        found = find_matching_basin(
            smiles,
            obs.coords,
            ((b, b.fingerprint_json, b.rep.coords) for b in basins),
            params,
        )
        if found is None:
            found = _Basin(index=len(basins), rep=obs)
            basins.append(found)
        found.members.append(obs)
        basin_of[obs.id] = found
        if obs.fingerprint.symmetry_cap_hit:
            report.symmetry_cap_hits += 1

    # --- 2. Give each basin a row. ---
    by_group: dict[int, list[_Observation]] = defaultdict(list)
    for obs in observations:
        by_group[obs.group_id].append(obs)
    rep_of_group: dict[int, _Observation | None] = {
        g.id: next((o for o in by_group[g.id] if o.eligible), None) for g in groups
    }
    row_of_basin: dict[int, int] = {}  # basin.index -> group id
    absorbed: set[int] = set()
    for g in groups:  # ascending id: the older group claims a basin first
        rep = rep_of_group[g.id]
        if rep is None:
            continue
        basin = basin_of[rep.id]
        if basin.index in row_of_basin:
            absorbed.add(g.id)
        else:
            row_of_basin[basin.index] = g.id

    # Unclaimed basins get a provisional (negative) row id until it is certain this entry will be written.
    provisional = {basin.index: -(basin.index + 1) for basin in basins if basin.index not in row_of_basin}
    row_of_basin.update(provisional)
    new_group_of_obs: dict[int, int] = {o.id: row_of_basin[basin_of[o.id].index] for o in observations if o.eligible}
    old_group_of_obs: dict[int, int] = {o.id: o.group_id for o in observations}
    fallback_of_group: dict[int, int] = {}

    def refresh_fallbacks() -> None:
        for g in groups:
            rep = rep_of_group[g.id]
            fallback_of_group[g.id] = new_group_of_obs[rep.id] if rep is not None else g.id

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

    # --- 3. Write: new rows first, then observations, then what pointed at a basin. ---
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
                "fp": json.dumps(basin.fingerprint_json),
                "coords": json.dumps(basin.rep.coords),
                # The ORM mints this on insert; raw SQL would leave the server-side placeholder.
                "ref": make_opaque_ref("cg"),
            },
        ).scalar_one()
        next_number += 1
        row_of_basin[basin.index] = created
        new_rows.append(created)
        report.groups_created += 1
        _ensure_review(session, created, "Created by conformer reassignment: a basin split out of an existing group.")
    for o in observations:
        if o.eligible:
            new_group_of_obs[o.id] = row_of_basin[basin_of[o.id].index]
    refresh_fallbacks()

    changed = False
    for obs in observations:
        if not obs.eligible:
            continue
        target = new_group_of_obs[obs.id]
        new_fp_json = obs.fingerprint.to_dict()  # type: ignore[union-attr]
        if target == obs.group_id and obs.scheme_id == scheme_id and obs.fingerprint_json == new_fp_json:
            continue
        repairs.declare("conformer_observation", OBSERVATION_COLUMNS)
        session.execute(
            text(
                "UPDATE conformer_observation SET conformer_group_id = :g, assignment_scheme_id = :s, "
                "torsion_fingerprint_json = CAST(:fp AS jsonb) WHERE id = :id"
            ),
            {"g": target, "s": scheme_id, "fp": json.dumps(new_fp_json), "id": obs.id},
        )
        if target != obs.group_id:
            report.observations_moved += 1
        else:
            report.observations_restamped += 1
        changed = True

    moved_any = _move_dependents(
        session, group_ids, target_for=target_for, repairs=repairs, report=report, scheme_id=scheme_id
    )
    changed = changed or moved_any

    # --- 5. Representatives, review resets, emptied groups. ---
    members_after: dict[int, set[int]] = defaultdict(set)
    for obs in observations:
        members_after[final_group(obs)].add(obs.id)
    members_before: dict[int, set[int]] = defaultdict(set)
    for obs in observations:
        members_before[obs.group_id].add(obs.id)

    stored_rep = {g.id: (g.fp, g.coords) for g in groups}
    for basin in basins:
        row = row_of_basin[basin.index]
        if row in new_rows:
            continue
        fingerprint_json = basin.fingerprint_json
        coords_json = json.loads(json.dumps(basin.rep.coords))
        if stored_rep[row] == (fingerprint_json, coords_json):
            continue
        session.execute(
            text(
                "UPDATE conformer_group SET representative_fingerprint_json = CAST(:fp AS jsonb), "
                "representative_coords_json = CAST(:coords AS jsonb) WHERE id = :id"
            ),
            {"fp": json.dumps(fingerprint_json), "coords": json.dumps(coords_json), "id": row},
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
            # ref someone may have cited. It keeps no representative, so nothing is matched against it.
            _clear_representative(session, g.id)
            report.groups_emptied_kept += 1
        elif not any(o.eligible and final_group(o) == g.id for o in observations):
            # Only members the recompute could not place remain: the old representative no longer describes them.
            _clear_representative(session, g.id)
        _reset_review(session, g.id, report, repairs_revision=repairs.revision)

    if changed:
        report.entries_changed += 1


def _ensure_review(session: Session, group_id: int, note: str) -> None:
    """Give a freshly created group its ``not_reviewed`` row and creation event."""
    review_id = session.execute(
        text(
            "INSERT INTO record_review (record_type, record_id, status, note) "
            "VALUES (CAST('conformer_group' AS submission_record_type), :id, "
            "CAST('not_reviewed' AS record_review_status), :note) "
            "ON CONFLICT ON CONSTRAINT uq_record_review_record DO NOTHING RETURNING id"
        ),
        {"id": group_id, "note": note},
    ).scalar_one_or_none()
    if review_id is not None:
        session.execute(
            text(
                "INSERT INTO record_review_event (record_review_id, event_kind, from_status, to_status, reason) "
                "VALUES (:rid, CAST('created' AS record_review_event_kind), NULL, "
                "CAST('not_reviewed' AS record_review_status), :note)"
            ),
            {"rid": review_id, "note": note},
        )


def _reset_review(session: Session, group_id: int, report: ReassignmentReport, *, repairs_revision: str) -> None:
    """Reset a group whose membership changed to ``not_reviewed``, with an audit event.

    ``record_review`` is not an accepted-science root and ``tckdb_guard_record_review`` permits this: the natural
    key and ``first_approved_at`` are untouched. The ordinary transition table forbids approved -> not_reviewed
    because a person is asking; this is the system withdrawing a review of a group that is no longer the group
    that was reviewed.
    """
    row = session.execute(
        text(
            "SELECT id, status::text AS status FROM record_review "
            "WHERE record_type = CAST('conformer_group' AS submission_record_type) AND record_id = :id FOR UPDATE"
        ),
        {"id": group_id},
    ).first()
    if row is None or row.status == "not_reviewed":
        return
    reason = (
        "Conformer group membership changed under the new conformer_assignment_scheme; the review of the previous "
        "membership no longer applies."
    )
    session.execute(
        text(
            "UPDATE record_review SET status = CAST('not_reviewed' AS record_review_status), reviewed_by = NULL, "
            "reviewed_at = NULL WHERE id = :id"
        ),
        {"id": row.id},
    )
    session.execute(
        text(
            "INSERT INTO record_review_event (record_review_id, event_kind, from_status, to_status, reason, details_json) "
            "VALUES (:rid, CAST('status_change' AS record_review_event_kind), CAST(:from AS record_review_status), "
            "CAST('not_reviewed' AS record_review_status), :reason, CAST(:details AS jsonb))"
        ),
        {
            "rid": row.id,
            "from": row.status,
            "reason": reason,
            "details": json.dumps({"revision": repairs_revision, "cause": "conformer_group_membership_changed"}),
        },
    )
    report.groups_review_reset += 1


def _clear_representative(session: Session, group_id: int) -> None:
    session.execute(
        text(
            "UPDATE conformer_group SET representative_fingerprint_json = NULL, representative_coords_json = NULL "
            "WHERE id = :id"
        ),
        {"id": group_id},
    )


def _calculation_observations(session: Session, calculation_ids: set[int]) -> dict[int, int]:
    """``calculation id -> conformer observation id`` for those calculations that are anchored to one."""
    if not calculation_ids:
        return {}
    return {
        r.id: r.conformer_observation_id
        for r in session.execute(
            text(
                "SELECT id, conformer_observation_id FROM calculation "
                "WHERE id = ANY(:ids) AND conformer_observation_id IS NOT NULL"
            ),
            {"ids": sorted(calculation_ids)},
        )
    }


def _move_statmech_basins(
    session: Session, group_ids: Sequence[int], target_for: Any, repairs: _Repairs, report: ReassignmentReport
) -> bool:
    """Re-point statmech basin declarations and ensemble members; only ``conformer_group_id`` is written."""
    members = session.execute(
        text(
            "SELECT id, declaration_id, member_statmech_id, conformer_group_id AS g FROM statmech_model_member "
            "WHERE conformer_group_id = ANY(:g) ORDER BY id"
        ),
        {"g": list(group_ids)},
    ).all()
    declarations = session.execute(
        text(
            "SELECT id, statmech_id, conformer_group_id AS g FROM statmech_model_declaration "
            "WHERE conformer_group_id = ANY(:g) ORDER BY id"
        ),
        {"g": list(group_ids)},
    ).all()
    if not members and not declarations:
        return False
    statmech_calcs: dict[int, list[int]] = defaultdict(list)
    for r in session.execute(
        text("SELECT statmech_id, calculation_id FROM statmech_source_calculation WHERE statmech_id = ANY(:ids)"),
        {"ids": sorted({m.member_statmech_id for m in members} | {d.statmech_id for d in declarations})},
    ):
        statmech_calcs[r.statmech_id].append(r.calculation_id)
    if declarations:
        for r in session.execute(
            text(
                "SELECT d.statmech_id, s.calculation_id FROM statmech_model_source s "
                "JOIN statmech_model_declaration d ON d.id = s.declaration_id "
                "WHERE s.calculation_id IS NOT NULL AND d.id = ANY(:ids)"
            ),
            {"ids": [d.id for d in declarations]},
        ):
            statmech_calcs[r.statmech_id].append(r.calculation_id)
    obs_of_calc = _calculation_observations(session, {c for cs in statmech_calcs.values() for c in cs})
    moved = False
    touched: set[int] = set()
    for m in members:
        target = target_for(m.g, statmech_calcs.get(m.member_statmech_id, []), obs_of_calc)
        if target != m.g:
            repairs.declare("statmech_model_member", STATMECH_MEMBER_COLUMNS)
            session.execute(
                text("UPDATE statmech_model_member SET conformer_group_id = :g WHERE id = :id"),
                {"g": target, "id": m.id},
            )
            report.statmech_members_moved += 1
            touched.add(m.declaration_id)
            moved = True
    for d in declarations:
        target = target_for(d.g, statmech_calcs.get(d.statmech_id, []), obs_of_calc)
        if target != d.g:
            repairs.declare("statmech_model_declaration", STATMECH_DECLARATION_COLUMNS)
            session.execute(
                text("UPDATE statmech_model_declaration SET conformer_group_id = :g WHERE id = :id"),
                {"g": target, "id": d.id},
            )
            report.statmech_declarations_moved += 1
            touched.add(d.id)
            moved = True
    if touched:
        _refresh_declaration_checksums(session, sorted(touched), repairs)
    return moved


def _refresh_declaration_checksums(session: Session, declaration_ids: Sequence[int], repairs: _Repairs) -> None:
    """Recompute the checksum of each declaration whose own basin or a member's basin just moved.

    The checksum covers the declaration's basin and every member's basin by public ref, so it follows a move even
    when only a member moved. The moves above used SQL, so the session's cached objects are stale and are expired
    first; the new value is written inside the same declared repair (``content_hash`` is declared with the basin and
    the trigger lets it change only because a basin moved in this transaction).
    """
    from app.db.models.statmech_declaration import StatmechModelDeclaration
    from app.services.statmech_declaration_resolution import recompute_content_hash

    session.flush()
    session.expire_all()
    for declaration_id in declaration_ids:
        row = session.get(StatmechModelDeclaration, declaration_id)
        assert row is not None
        fresh = recompute_content_hash(session, row)
        if fresh != row.content_hash:
            repairs.declare("statmech_model_declaration", STATMECH_DECLARATION_COLUMNS)
            session.execute(
                text("UPDATE statmech_model_declaration SET content_hash = :h WHERE id = :id"),
                {"h": fresh, "id": declaration_id},
            )
    session.expire_all()


def _move_dependents(
    session: Session,
    group_ids: Sequence[int],
    *,
    target_for: Any,
    repairs: _Repairs,
    report: ReassignmentReport,
    scheme_id: int,
) -> bool:
    """Re-point thermo targets, statmech basins and conformer selections. Returns whether anything moved."""
    if not group_ids:
        return False
    moved = False

    # Thermo targets.
    thermo = session.execute(
        text("SELECT id, target_conformer_group_id AS g FROM thermo WHERE target_conformer_group_id = ANY(:g) ORDER BY id"),
        {"g": list(group_ids)},
    ).all()
    thermo_calcs: dict[int, list[int]] = defaultdict(list)
    if thermo:
        for r in session.execute(
            text("SELECT thermo_id, calculation_id FROM thermo_source_calculation WHERE thermo_id = ANY(:ids)"),
            {"ids": [t.id for t in thermo]},
        ):
            thermo_calcs[r.thermo_id].append(r.calculation_id)
    obs_of_calc = _calculation_observations(session, {c for cs in thermo_calcs.values() for c in cs})
    for t in thermo:
        target = target_for(t.g, thermo_calcs.get(t.id, []), obs_of_calc)
        if target != t.g:
            repairs.declare("thermo", THERMO_COLUMNS)
            session.execute(
                text("UPDATE thermo SET target_conformer_group_id = :g WHERE id = :id"), {"g": target, "id": t.id}
            )
            report.thermo_moved += 1
            moved = True

    moved = _move_statmech_basins(session, group_ids, target_for, repairs, report) or moved

    # Conformer selections carry no calculations: they follow the old group's representative, and a scheme-stamped
    # selection is re-stamped with the scheme that now defines the groups. A selection is a curation row and
    # there is no supersede or append mechanism for it (a duplicate (group, scheme, kind) is refused), so one
    # that would collide with a selection already on the target is NOT deleted: it stays on its current group.
    selections = session.execute(
        text(
            "SELECT id, conformer_group_id AS g, assignment_scheme_id AS s, selection_kind::text AS k "
            "FROM conformer_selection WHERE conformer_group_id = ANY(:g) ORDER BY id"
        ),
        {"g": list(group_ids)},
    ).all()
    targets = {s.id: target_for(s.g, [], {}) for s in selections}

    def restamped(row) -> int | None:
        return scheme_id if row.s is not None else None

    placed = {(s.g, s.s, s.k) for s in selections if targets[s.id] == s.g}
    present = {(s.g, s.s, s.k) for s in selections}
    pending = [s for s in selections if targets[s.id] != s.g]
    while pending:
        progressed = False
        for s in list(pending):
            slot = (targets[s.id], restamped(s), s.k)
            if slot in placed:
                report.selections_left_duplicate += 1  # stays where it is
            elif slot in present:
                continue  # held by a selection that is itself moving away; settle on a later pass
            else:
                session.execute(
                    text(
                        "UPDATE conformer_selection SET conformer_group_id = :g, assignment_scheme_id = :s WHERE id = :id"
                    ),
                    {"g": targets[s.id], "s": restamped(s), "id": s.id},
                )
                present.discard((s.g, s.s, s.k))
                present.add(slot)
                placed.add(slot)
                report.selections_moved += 1
                moved = True
            pending.remove(s)
            progressed = True
        if not progressed:
            raise RuntimeError("Conformer selections form a cycle of moves; refusing to guess")
    return moved
