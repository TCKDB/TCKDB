"""Trust verdicts that rest only on the evidence the active read profile shows (owner decision D5).

A trust fragment is a checklist over the evidence graph of one record ("is there a transition-state optimisation, an
IRC, a geometry validation?"). The evaluators are pure over the ORM graph they are handed, and that graph includes
calculations below the review floor. Handed as it is, a curated fragment would say ``ts_optimization_present: passed``
for an entry whose only optimisation is hidden: the same existence oracle the ``has_opt`` flag was.

Under a floor the evaluators are therefore run on the same graph with the hidden calculations taken out of the
collections they walk, and the collections are put back afterwards (``set_committed_value`` records no change, so
nothing is flushed and the session's objects are exactly as they were). With no floor nothing is looked up and nothing
is swapped, so the exploratory verdict is the one it always was.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session, object_session
from sqlalchemy.orm.attributes import set_committed_value

from app.db.models.common import SubmissionRecordType
from app.db.models.kinetics import Kinetics
from app.db.models.transition_state import TransitionStateEntry
from app.services.scientific_read.common import review_hidden_under_profile
from app.services.scientific_read.profile import current_read_profile
from app.services.trust import EvidenceEvaluation, evaluate_loaded_kinetics, evaluate_loaded_transition_state_entry


def _hidden_calculation_ids(session: Session | None, calculations: Iterator[Any] | list[Any]) -> set[int]:
    if session is None:
        return set()
    ids = {c.id for c in calculations if c is not None}
    return review_hidden_under_profile(session, record_type=SubmissionRecordType.calculation, record_ids=ids)[1]


@contextmanager
def _swapped(swaps: list[tuple[Any, str, Any]]) -> Iterator[None]:
    """Replace loaded collections for the duration of the block, then restore them."""
    saved = [(obj, attr, getattr(obj, attr)) for obj, attr, _ in swaps]
    try:
        for obj, attr, value in swaps:
            set_committed_value(obj, attr, value)
        yield
    finally:
        for obj, attr, value in reversed(saved):
            set_committed_value(obj, attr, value)


@contextmanager
def transition_state_entry_without_hidden_calculations(entry: TransitionStateEntry) -> Iterator[None]:
    """The entry's calculation graph with the calculations the profile hides taken out (see the module docstring)."""
    if current_read_profile().review_floor is None:
        yield
        return
    direct = list(entry.calculations)
    related: list[Any] = list(direct)
    for calc in direct:
        related.extend(dep.parent_calculation for dep in calc.child_dependencies)
        related.extend(dep.child_calculation for dep in calc.parent_dependencies)
    hidden = _hidden_calculation_ids(object_session(entry), related)
    if not hidden:
        yield
        return
    visible = [c for c in direct if c.id not in hidden]
    swaps: list[tuple[Any, str, Any]] = [(entry, "calculations", visible)]
    for calc in visible:
        swaps.append(
            (calc, "child_dependencies", [d for d in calc.child_dependencies if d.parent_calculation_id not in hidden])
        )
        swaps.append(
            (calc, "parent_dependencies", [d for d in calc.parent_dependencies if d.child_calculation_id not in hidden])
        )
    with _swapped(swaps):
        yield


@contextmanager
def kinetics_without_hidden_calculations(kinetics: Kinetics) -> Iterator[None]:
    """The kinetics record's source-calculation links with those to hidden calculations taken out."""
    if current_read_profile().review_floor is None:
        yield
        return
    links = list(kinetics.source_calculations)
    hidden = _hidden_calculation_ids(object_session(kinetics), [link.calculation for link in links])
    if not hidden:
        yield
        return
    with _swapped([(kinetics, "source_calculations", [lk for lk in links if lk.calculation_id not in hidden])]):
        yield


def evaluate_shown_transition_state_entry(entry: TransitionStateEntry | None) -> EvidenceEvaluation:
    """:func:`evaluate_loaded_transition_state_entry` over the calculations the active profile shows."""
    if entry is None:
        return evaluate_loaded_transition_state_entry(entry)
    with transition_state_entry_without_hidden_calculations(entry):
        return evaluate_loaded_transition_state_entry(entry)


def evaluate_shown_kinetics(kinetics: Kinetics | None) -> EvidenceEvaluation:
    """:func:`evaluate_loaded_kinetics` over the source calculations the active profile shows."""
    if kinetics is None:
        return evaluate_loaded_kinetics(kinetics)
    with kinetics_without_hidden_calculations(kinetics):
        return evaluate_loaded_kinetics(kinetics)


__all__: list[str] = [
    "evaluate_shown_kinetics",
    "evaluate_shown_transition_state_entry",
    "kinetics_without_hidden_calculations",
    "transition_state_entry_without_hidden_calculations",
]
