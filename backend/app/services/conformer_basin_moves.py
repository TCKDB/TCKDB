"""What moves when a conformer basin changes, and how: shared by the live upload path and the recompute.

A basin row is pointed at by observations, thermo targets, statmech basin declarations and ensemble members, and
conformer selections. When a basin is merged into another (a bridging upload, a recompute) or split, these follow by
D5: each goes to the basin holding the observation its own calculations came from, else to the basin holding the old
group's representative. Review of every group whose membership changed is reset to ``not_reviewed`` with an event.

Frozen rows
-----------
``conformer_observation`` and ``thermo`` are accepted-science roots (the statmech declaration and its members are
frozen with their statmech record): once approved, the database refuses to UPDATE them. The only sanctioned
in-place change is a *declared repair* (``accepted_science_repair``, revision ``e2c9a4f7b163``): declared in the
same transaction, limited to named columns, every changed accepted row recorded old -> new. It needs a role that
owns the table, which is why only the owner-role recompute ever declares one. A live upload runs as the API role,
cannot declare, and so moves only rows that are not accepted (``Repairs(enabled=False)`` declares nothing, and the
trigger refuses the write if a row turns out to be accepted). The trigger is never disabled or bypassed.

Writes use SQL, not the ORM: the rows touched include accepted-science rows, and a declared repair covers exactly
the columns named, so the statements say exactly which columns they set.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

#: Columns a reassignment of observations may change (and so the columns its repair declares). The fingerprint and
#: the species-canonical mapped coordinates are derived from the geometry and the scheme, so they are regenerated
#: with the scheme link; no scientific value moves.
OBSERVATION_COLUMNS = (
    "conformer_group_id",
    "assignment_scheme_id",
    "torsion_fingerprint_json",
    "mapped_coords_json",
)
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
    mapped_coords_filled: int = 0
    wide_basins: int = 0
    merges_pending_completed: int = 0
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


class Repairs:
    """Declares each accepted-science repair once per transaction, the first time its table is written."""

    def __init__(
        self, session: Session, revision: str, report: ReassignmentReport, *, enabled: bool = True
    ) -> None:
        self._session = session
        self.revision = revision
        self._report = report
        #: ``False`` for the live upload path: it runs as the API role, which owns no table and so cannot declare a
        #: repair. It then writes only rows that are not accepted, and the database refuses the write otherwise.
        self.enabled = enabled
        self._declared: set[str] = set()

    def declare(self, table: str, columns: Sequence[str]) -> None:
        if not self.enabled or table in self._declared:
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


def ensure_review(session: Session, group_id: int, note: str) -> None:
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


def reset_review(session: Session, group_id: int, report: ReassignmentReport, *, repairs_revision: str) -> None:
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
        "Conformer group membership changed (basins are recomputed as connected components of the optimised "
        "structures); the review of the previous membership no longer applies."
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


def clear_representative(session: Session, group_id: int, *, merged_into: int | None = None) -> None:
    """Take a group out of matching: no representative, no shape. ``merged_into`` names where its members went."""
    session.execute(
        text(
            "UPDATE conformer_group SET representative_fingerprint_json = NULL, representative_coords_json = NULL, "
            "representative_observation_id = NULL, basin_is_clique = NULL, basin_span_deg = NULL, "
            "merged_into_conformer_group_id = :merged WHERE id = :id"
        ),
        {"id": group_id, "merged": merged_into},
    )


def calculation_observations(session: Session, calculation_ids: set[int]) -> dict[int, int]:
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


def move_statmech_basins(
    session: Session, group_ids: Sequence[int], target_for: Any, repairs: Repairs, report: ReassignmentReport
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
    obs_of_calc = calculation_observations(session, {c for cs in statmech_calcs.values() for c in cs})
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
        refresh_declaration_checksums(session, sorted(touched), repairs)
    return moved


def refresh_declaration_checksums(session: Session, declaration_ids: Sequence[int], repairs: Repairs) -> None:
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


def move_dependents(
    session: Session,
    group_ids: Sequence[int],
    *,
    target_for: Any,
    repairs: Repairs,
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
    obs_of_calc = calculation_observations(session, {c for cs in thermo_calcs.values() for c in cs})
    for t in thermo:
        target = target_for(t.g, thermo_calcs.get(t.id, []), obs_of_calc)
        if target != t.g:
            repairs.declare("thermo", THERMO_COLUMNS)
            session.execute(
                text("UPDATE thermo SET target_conformer_group_id = :g WHERE id = :id"), {"g": target, "id": t.id}
            )
            report.thermo_moved += 1
            moved = True

    moved = move_statmech_basins(session, group_ids, target_for, repairs, report) or moved

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
