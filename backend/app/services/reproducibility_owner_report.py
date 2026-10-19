"""Read-only report: what the owner-scoped reproducibility rubric does to a database's statmech records (#760).

Before the owner-scoped rubric every transition-state-owned statmech was graded by the shared rubric and read
``insufficient`` on ``scientific_context``, whatever its evidence. This report says, for the database it is pointed
at, how many records of each owner kind there are, how the latest stored assessment of each reads now (``current`` or
``stale`` and why), how grades move, and whether any species-owned record moved (it must not).

**Read-only by construction.** Nothing here writes, and nothing re-assesses. The script
(``scripts/ops/reproducibility_ts_statmech_report.py``) runs it in a ``REPEATABLE READ`` / ``READ ONLY`` transaction.
There is no apply mode: re-assessment is a curator's per-record ``POST .../evaluate``, never a bulk or automatic pass.

**What ``species_entry_unchanged`` does and does not prove.** It compares the live router against the shared rubric
driven explicitly, both from this code. It detects a species-owned record being *routed* away from the shared rubric.
It cannot detect an edit to a check both rubrics share. The anchors for species records are (1) every
``species_entry`` row of ``assessment_currency`` reading ``current`` and (2) the frozen-literal test
``tests/services/test_reproducibility_statmech_species_frozen.py``.

Counts only by default. ``include_stale_list=True`` (script flag ``--list-stale``) adds, for transition-state-owned
records whose latest assessment reads stale, the record id and the assessment ref: operator-only output, because it
carries identifiers. It makes the set a curator may choose to re-assess readable on any deployment; it re-assesses
nothing.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.common import SubmissionRecordType
from app.db.models.statmech import Statmech
from app.services.reproducibility_assessment import (
    get_latest_reproducibility_assessment,
    is_reproducibility_assessment_context_current,
)
from app.services.reproducibility_rubric import SHARED, evaluate_reproducibility

OWNER_SPECIES = "species_entry"
OWNER_TRANSITION_STATE = "transition_state_entry"
#: Impossible under ``statmech_exactly_one_subject``; kept so a violated CHECK is counted, not hidden.
OWNER_NONE = "unowned"
_OWNERS = (OWNER_SPECIES, OWNER_TRANSITION_STATE, OWNER_NONE)


def _owner(statmech: Statmech) -> str:
    if statmech.species_entry_id is not None and statmech.transition_state_entry_id is None:
        return OWNER_SPECIES
    if statmech.transition_state_entry_id is not None and statmech.species_entry_id is None:
        return OWNER_TRANSITION_STATE
    return OWNER_NONE


def reproducibility_owner_report(session: Session, *, include_stale_list: bool = False) -> dict[str, Any]:
    """Return the impact of the owner-scoped reproducibility rubric on statmech records. Writes nothing."""
    ids = list(session.scalars(select(Statmech.id).order_by(Statmech.id)))
    by_owner: Counter[str] = Counter()
    unassessed: Counter[str] = Counter()
    assessed: Counter[str] = Counter()
    currency: Counter[tuple[str, str, tuple[str, ...]]] = Counter()
    transitions: Counter[tuple[str, str, str]] = Counter()
    species_unchanged = True
    species_checked = 0
    stale_ts: list[dict[str, Any]] = []

    for statmech_id in ids:
        statmech = session.get(Statmech, statmech_id)
        assert statmech is not None
        owner = _owner(statmech)
        by_owner[owner] += 1
        live = evaluate_reproducibility(session, record_type=SubmissionRecordType.statmech, record_id=statmech_id)
        # What the record read before the owner-scoped rubric: the shared rubric, driven explicitly.
        before = evaluate_reproducibility(
            session, record_type=SubmissionRecordType.statmech, record_id=statmech_id, rubric=SHARED
        )
        transitions[(owner, before.grade.value, live.grade.value)] += 1
        if owner == OWNER_SPECIES:
            species_checked += 1
            species_unchanged = species_unchanged and (
                (before.rubric_name, before.rubric_version, before.grade, before.context_json)
                == (live.rubric_name, live.rubric_version, live.grade, live.context_json)
            )

        latest = get_latest_reproducibility_assessment(
            session, record_type=SubmissionRecordType.statmech, record_id=statmech_id
        )
        if latest is None:
            unassessed[owner] += 1
            continue
        assessed[owner] += 1
        reasons: list[str] = []
        if (latest.rubric_name, latest.rubric_version) != (live.rubric_name, live.rubric_version):
            reasons.append("rubric_identity_mismatch")
        if not is_reproducibility_assessment_context_current(latest, current_context_json=live.context_json):
            reasons.append("context_hash_mismatch")
        state = "stale" if reasons else "current"
        currency[(owner, state, tuple(reasons))] += 1
        if include_stale_list and owner == OWNER_TRANSITION_STATE and reasons:
            stale_ts.append(
                {
                    "statmech_id": statmech_id,
                    "assessment_ref": latest.public_ref,
                    "stored_grade": latest.grade.value,
                    "stored_rubric": [latest.rubric_name, latest.rubric_version],
                    "fresh_grade": live.grade.value,
                    "stale_reasons": reasons,
                }
            )

    report: dict[str, Any] = {
        "by_owner": {owner: {"n": by_owner.get(owner, 0)} for owner in (OWNER_SPECIES, OWNER_TRANSITION_STATE)},
        "unowned_statmech": by_owner.get(OWNER_NONE, 0),
        "statmech_with_assessment": {owner: assessed.get(owner, 0) for owner in _OWNERS},
        "statmech_without_assessment": {owner: unassessed.get(owner, 0) for owner in _OWNERS},
        "assessment_currency": [
            {"owner": owner, "state": state, "stale_reasons": list(reasons), "count": count}
            for (owner, state, reasons), count in sorted(currency.items())
        ],
        "grade_transitions": [
            {"owner": owner, "from": before, "to": after, "count": count}
            for (owner, before, after), count in sorted(transitions.items())
        ],
        # Routing only: see the module docstring.
        "species_entry_unchanged": species_unchanged,
        "species_entry_records_compared": species_checked,
    }
    if include_stale_list:
        report["stale_transition_state_records"] = stale_ts
    return report


__all__ = ["reproducibility_owner_report"]
