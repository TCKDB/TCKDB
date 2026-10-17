"""Read-only report: what the owner-scoped statmech rubric does to the stored corpus.

Before the owner-scoped rubric every transition-state-owned statmech was graded by the species
rubric and read ``hard_failed``. This report says, for the database it is pointed at, how many
records change badge and how, whether any species-owned record moved (it must not), and which
stored machine reviews read as stale once the change is deployed.

**Read-only by construction.** Nothing here writes. The report script
(``scripts/ops/statmech_trust_owner_report.py``) runs it inside a ``REPEATABLE READ`` /
``READ ONLY`` transaction, so the database refuses a write even if a later edit adds one. There is
no ``--apply`` mode.

**What ``species_entry_unchanged`` does and does not prove.** It compares two evaluations that both run this
code: the live router against the species rubric driven explicitly. So it detects a species-owned record being
*routed* to the other rubric. It cannot detect an edit to a check that both rubrics share (for example a runner
that starts returning ``missing``): both sides would move together and the flag would still read ``true``. The
checks that anchor species records to the code on ``main`` are (1) every ``species_entry`` row of
``machine_review_currency`` reading ``current`` (a moved species badge changes the context hash its stored review
was made under), and (2) the frozen-literal test ``tests/services/test_trust_statmech_species_frozen.py``, which
holds the species check maps captured from ``main`` as literals.

Counts only: no record id, public ref or free text leaves the report.

How "from" and "to" are obtained:

* ``to`` is the live evaluator (:func:`evaluate_loaded_statmech`).
* ``from`` is what the record read before this change, reconstructed by running the same record
  under the species rubric (:data:`COMPUTED_STATMECH_V2`) with the legacy owner check, via
  :func:`evaluate_loaded_statmech_under`. Both are still in the code, so nothing is guessed.

Review currency is derived exactly as the admin trigger derives it (same fragment, same context
hash, same recipe for the rubric that evaluated the record, same currency query), over the latest
reviewer-family row per record. Historical rows are never rewritten.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.common import SubmissionRecordType
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.db.models.statmech import Statmech
from app.services.trust.evaluator import evaluate_loaded_statmech, evaluate_loaded_statmech_under
from app.services.trust.models import EvidenceEvaluation
from app.services.trust.rubrics import COMPUTED_STATMECH_V2

#: Records are evaluated in pages of this many, each page with the read layer's eager-load chain.
_BATCH = 200

OWNER_SPECIES = "species_entry"
OWNER_TRANSITION_STATE = "transition_state_entry"
#: Impossible under ``statmech_exactly_one_subject``; kept so a violated CHECK is counted, not hidden.
OWNER_NONE = "unowned"


#: Every owner bucket a count is reported under. ``unowned`` is impossible under the CHECK and is always listed, so
#: a violated CHECK shows up in each count instead of vanishing from it.
_OWNERS = (OWNER_SPECIES, OWNER_TRANSITION_STATE, OWNER_NONE)


def _owner(statmech: Statmech) -> str:
    if statmech.species_entry_id is not None and statmech.transition_state_entry_id is None:
        return OWNER_SPECIES
    if statmech.transition_state_entry_id is not None and statmech.species_entry_id is None:
        return OWNER_TRANSITION_STATE
    return OWNER_NONE


def _hard_fail(evaluation: EvidenceEvaluation) -> str | None:
    reason = evaluation.hard_fail_reason
    return reason.value if reason is not None else None


def _identical(before: EvidenceEvaluation, after: EvidenceEvaluation) -> bool:
    """Same rubric, badge, completeness and ordered check map, to the last field the evaluator returns."""
    return before.model_dump(mode="json") == after.model_dump(mode="json")


def _batches(ids: list[int]) -> Iterable[list[int]]:
    for start in range(0, len(ids), _BATCH):
        yield ids[start : start + _BATCH]


def _load(session: Session, ids: list[int]) -> list[Statmech]:
    # Imported here: the read layer imports the trust package, so a module-level import would be circular.
    from app.services.scientific_read.statmech import STATMECH_TRUST_EAGER_LOADS

    return list(
        session.scalars(
            select(Statmech).where(Statmech.id.in_(ids)).options(*STATMECH_TRUST_EAGER_LOADS).order_by(Statmech.id)
        )
    )


def _stored_review_rows_by_owner(session: Session) -> dict[str, int]:
    """Row counts of every stored statmech machine review (all families), split by owner."""
    rows = session.execute(
        select(Statmech.species_entry_id.is_not(None), Statmech.transition_state_entry_id.is_not(None), func.count())
        .join(RecordMachineReviewRow, RecordMachineReviewRow.record_id == Statmech.id)
        .where(RecordMachineReviewRow.record_type == SubmissionRecordType.statmech)
        .group_by(Statmech.species_entry_id.is_not(None), Statmech.transition_state_entry_id.is_not(None))
    ).all()
    counts: Counter[str] = Counter()
    for has_species, has_ts, n in rows:
        owner = (
            OWNER_SPECIES
            if has_species and not has_ts
            else OWNER_TRANSITION_STATE
            if has_ts and not has_species
            else OWNER_NONE
        )
        counts[owner] += int(n)
    return {owner: counts.get(owner, 0) for owner in _OWNERS}


def _review_currency(session: Session, owner_by_id: dict[int, str]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Currency of the latest reviewer-family review per record, under the recipe the live code would stamp."""
    from app.services.machine_review.admin_trigger import (
        active_rubric_versions_for_fragment,
        resolve_record_trust_fragment,
    )
    from app.services.machine_review.context_adapter import build_machine_review_evidence_context_from_trust
    from app.services.machine_review.context_hash import build_machine_review_context_hash
    from app.services.machine_review.query import (
        get_latest_record_machine_review_row,
        get_record_machine_review_currency_for_record,
    )
    from app.services.machine_review.recipe import ACTIVE_MACHINE_REVIEW_PROMPT_VERSION

    currency: Counter[tuple[str, str, tuple[str, ...]]] = Counter()
    unreviewed: Counter[str] = Counter()
    for statmech_id, owner in sorted(owner_by_id.items()):
        latest = get_latest_record_machine_review_row(session, record_type="statmech", record_id=statmech_id)
        if latest is None:
            unreviewed[owner] += 1
            continue
        fragment = resolve_record_trust_fragment(session, record_type="statmech", record_id=statmech_id)
        context = build_machine_review_evidence_context_from_trust(
            record_type="statmech", record_ref=str(statmech_id), trust_fragment=fragment
        )
        classification = get_record_machine_review_currency_for_record(
            session,
            record_type="statmech",
            record_id=statmech_id,
            current_context=build_machine_review_context_hash(context),
            active_prompt_version=ACTIVE_MACHINE_REVIEW_PROMPT_VERSION,
            active_rubric_versions=active_rubric_versions_for_fragment("statmech", fragment),
        )
        reasons = tuple(sorted(reason.value for reason in classification.stale_reasons))
        currency[(owner, classification.state.value, reasons)] += 1
    rows = [
        {"owner": owner, "state": state, "stale_reasons": list(reasons), "count": count}
        for (owner, state, reasons), count in sorted(currency.items())
    ]
    return rows, {owner: unreviewed.get(owner, 0) for owner in _OWNERS}


def statmech_trust_owner_report(session: Session) -> dict[str, Any]:
    """Return the badge and review-currency impact of the owner-scoped statmech rubric. Writes nothing."""
    ids = list(session.scalars(select(Statmech.id).order_by(Statmech.id)))

    owner_by_id: dict[int, str] = {}
    transitions: Counter[tuple[str, str, str | None, str, str | None]] = Counter()
    species_unchanged = True
    species_checked = 0
    for batch in _batches(ids):
        for statmech in _load(session, batch):
            owner = _owner(statmech)
            owner_by_id[statmech.id] = owner
            before = evaluate_loaded_statmech_under(statmech, COMPUTED_STATMECH_V2)
            after = evaluate_loaded_statmech(statmech)
            transitions[(owner, before.label.value, _hard_fail(before), after.label.value, _hard_fail(after))] += 1
            if owner == OWNER_SPECIES:
                species_checked += 1
                species_unchanged = species_unchanged and _identical(before, after)

    by_owner = Counter(owner_by_id.values())
    review_rows, unreviewed = _review_currency(session, owner_by_id)

    return {
        "by_owner": {owner: {"n": by_owner.get(owner, 0)} for owner in (OWNER_SPECIES, OWNER_TRANSITION_STATE)},
        "unowned_statmech": by_owner.get(OWNER_NONE, 0),
        "badge_transitions": [
            {
                "owner": owner,
                "from": before,
                "from_hard_fail": before_reason,
                "to": after,
                "to_hard_fail": after_reason,
                "count": count,
            }
            for (owner, before, before_reason, after, after_reason), count in sorted(
                transitions.items(), key=lambda item: (item[0][0], item[0][1], item[0][3], str(item[0][2]), str(item[0][4]))
            )
        ],
        # Routing only: see the module docstring. Read it together with the species rows of ``machine_review_currency``.
        "species_entry_unchanged": species_unchanged,
        "species_entry_records_compared": species_checked,
        "machine_review_currency": review_rows,
        "statmech_without_machine_review": unreviewed,
        "stored_review_rows_by_owner": _stored_review_rows_by_owner(session),
    }


__all__ = ["statmech_trust_owner_report"]
