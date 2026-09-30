"""The energies an ``energy_ordering`` record compared are frozen with their record.

``a1f6c3e9b527`` froze ``transition_state_validation_evidence`` under its
transition-state entry once that entry is approved. ``a7d3f1c95e28`` adds
``transition_state_validation_energy``, whose rows are the numbers that
evidence row's verdict rests on. Left writable, an energy could be rewritten
under an accepted record and the stored "the saddle point lies above both
wells" would stop following from the numbers recorded beside it, with no
supersession edge and no review event.

The table reaches its accepted root through its evidence row, so the guard is
``tckdb_guard_accepted_via_child``. Both halves of the rule are pinned, because
a guard that is present but never fires is the failure mode that matters:

* under an **approved** entry a row cannot be updated, deleted or inserted;
* under an **unapproved** entry it is fully editable, so the freeze is
  acceptance-triggered and not a blanket write ban.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import DBAPIError

from app.db.models.common import SubmissionRecordType
from app.db.models.transition_state import (
    TransitionStateValidationEnergy,
    TransitionStateValidationEvidence,
)
from tests.db.test_evidence_immutability import _approve, _curator, _reaction_entry
from tests.services.scientific_read._factories import (
    make_calculation,
    make_transition_state,
    make_transition_state_entry,
)

#: ``object_not_in_prerequisite_state``: the accepted-science guard's refusal.
#: Asserted, because a bare ``DBAPIError`` would also be satisfied by a typo in
#: the test's own insert.
_FROZEN = "55000"


def _ordering(db_session, tag: str):
    reaction_entry, _, _ = _reaction_entry(db_session, tag)
    ts_entry = make_transition_state_entry(
        db_session,
        transition_state=make_transition_state(db_session, reaction_entry=reaction_entry),
    )
    calculation = make_calculation(db_session, transition_state_entry_id=ts_entry.id)
    evidence = TransitionStateValidationEvidence(
        transition_state_entry_id=ts_entry.id,
        kind="energy_ordering",
        passed=True,
        rationale="TS above both wells",
        reconstruction_calculation_id=None,
    )
    db_session.add(evidence)
    db_session.flush()
    energy = TransitionStateValidationEnergy(
        evidence_id=evidence.id,
        participant="ts",
        energy_kind="electronic",
        energy_hartree=-40.2,
        source_calculation_id=calculation.id,
    )
    db_session.add(energy)
    db_session.flush()
    return ts_entry, evidence, energy, calculation


def test_compared_energies_of_an_approved_entry_are_frozen(db_session) -> None:
    actor = _curator(db_session, "energy-freeze-curator")
    ts_entry, _, energy, _ = _ordering(db_session, "ENFRZ")
    _approve(
        db_session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_id=ts_entry.id,
        actor=actor,
    )

    with pytest.raises(DBAPIError) as updated, db_session.begin_nested():
        energy.energy_hartree = -41.0
        db_session.flush()
    assert updated.value.orig.sqlstate == _FROZEN

    with pytest.raises(DBAPIError) as deleted, db_session.begin_nested():
        db_session.delete(energy)
        db_session.flush()
    assert deleted.value.orig.sqlstate == _FROZEN


def test_an_energy_cannot_be_added_to_an_approved_entrys_evidence(db_session) -> None:
    actor = _curator(db_session, "energy-late-curator")
    ts_entry, evidence, _, calculation = _ordering(db_session, "ENLATE")
    _approve(
        db_session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_id=ts_entry.id,
        actor=actor,
    )

    with pytest.raises(DBAPIError) as inserted, db_session.begin_nested():
        db_session.add(
            TransitionStateValidationEnergy(
                evidence_id=evidence.id,
                participant="reactant:1",
                energy_kind="electronic",
                energy_hartree=-39.0,
                source_calculation_id=calculation.id,
            )
        )
        db_session.flush()
    assert inserted.value.orig.sqlstate == _FROZEN


def test_compared_energies_of_an_unapproved_entry_stay_editable(db_session) -> None:
    _, _, energy, _ = _ordering(db_session, "ENOPEN")

    energy.energy_hartree = -40.25
    db_session.flush()
    assert energy.energy_hartree == -40.25

    db_session.delete(energy)
    db_session.flush()
