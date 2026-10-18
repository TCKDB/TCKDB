"""The undesignated-imaginary-modes warning (#729) on the transition-state read surfaces.

``no_frequency_result_has_undesignated_imaginary_modes`` is a zero-weight advisory check: it appears in
``trust.evidence.checks`` when an attached frequency result reports several imaginary modes without designating the
reaction coordinate. Each test names the mutation it kills.
"""

from __future__ import annotations

from app.db.models.common import CalculationType, RecordReviewStatus, SubmissionRecordType
from tests.services.scientific_read._factories import (
    attach_freq_result,
    make_calculation,
    make_chem_reaction,
    make_lot,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
    set_review,
)

BASE = "/api/v1/scientific"
WARNING = "no_frequency_result_has_undesignated_imaginary_modes"


def _reaction_entry(db_session, tag):
    rs = make_species(db_session, smiles="A", inchi_key=next_inchi_key(tag + "1"))
    ps = make_species(db_session, smiles="B", inchi_key=next_inchi_key(tag + "2"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    return make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )


def _ts_with_freqs(db_session, tag, freqs):
    """A TS entry with one freq calculation per entry of *freqs* (a list of frequency lists, cm-1)."""
    rxn = _reaction_entry(db_session, tag)
    ts = make_transition_state(db_session, reaction_entry=rxn, label=f"ts_{tag}")
    tse = make_transition_state_entry(db_session, transition_state=ts)
    calcs = []
    lot = make_lot(db_session)
    for frequencies in freqs:
        calc = make_calculation(
            db_session, type=CalculationType.freq, transition_state_entry_id=tse.id, lot_id=lot.id
        )
        attach_freq_result(db_session, calculation=calc, frequencies_cm1=frequencies)
        calcs.append(calc)
    db_session.refresh(tse)
    return rxn, ts, tse, calcs


def _trust(client, tse, **params):
    r = client.get(f"{BASE}/transition-state-entries/{tse.public_ref}", params={"include": "trust", **params})
    assert r.status_code == 200, r.text
    return r.json()["record"]["trust"]


def test_detail_carries_the_warning_for_an_undesignated_multi_mode_result(client, db_session):
    """Kills: the new check missing from the served rubric (a stale v3 check set)."""
    _, _, tse, _ = _ts_with_freqs(db_session, "UDW1", [[-300.0, -200.0, -100.0, 500.0]])
    trust = _trust(client, tse)
    assert trust["evidence"]["checks"][WARNING] == "warning"
    assert trust["evidence"]["rubric_version"] == 4
    assert trust["is_certified"] is False
    assert trust["trust_status"] != "hard_failed"


def test_detail_has_no_warning_for_one_mode_zero_modes_or_a_designated_result(client, db_session):
    """Kills: firing regardless of count or designation; the zero-mode case keeps only its own warning."""
    _, _, one, _ = _ts_with_freqs(db_session, "UDW2", [[-300.0, 500.0]])
    assert WARNING not in _trust(client, one)["evidence"]["checks"]
    _, _, zero, _ = _ts_with_freqs(db_session, "UDW3", [[100.0, 500.0]])
    zero_checks = _trust(client, zero)["evidence"]["checks"]
    assert WARNING not in zero_checks
    assert zero_checks["no_frequency_result_lacks_an_imaginary_mode"] == "warning"


def test_full_and_detail_carry_the_same_warning(client, db_session):
    """Kills: one surface missing the new check."""
    rxn, _, tse, _ = _ts_with_freqs(db_session, "UDW4", [[-300.0, -200.0, 500.0]])
    detail = _trust(client, tse)["evidence"]["checks"]
    full = client.get(f"{BASE}/reaction-entries/{rxn.public_ref}/full", params={"include": "trust"}).json()
    embedded = next(
        t["trust"] for t in full["transition_states"] if t["transition_state_entry_ref"] == tse.public_ref
    )["evidence"]["checks"]
    assert detail[WARNING] == "warning"
    assert embedded == detail


def test_curated_ignores_a_hidden_result_with_several_modes(client, db_session):
    """Kills: the runner fed the unfiltered graph / the curated floor bypassed (hidden must equal absent)."""
    _, ts, tse, (shown, hidden) = _ts_with_freqs(
        db_session, "UDW5", [[-300.0, 500.0], [-300.0, -200.0, -100.0, 500.0]]
    )
    set_review(db_session, record_type=SubmissionRecordType.calculation, record_id=shown.id, status=RecordReviewStatus.approved)
    set_review(db_session, record_type=SubmissionRecordType.transition_state_entry, record_id=tse.id, status=RecordReviewStatus.approved)
    set_review(db_session, record_type=SubmissionRecordType.transition_state, record_id=ts.id, status=RecordReviewStatus.approved)

    assert client.get(f"{BASE}/calculations/{hidden.public_ref}", params={"profile": "curated"}).status_code == 404
    assert _trust(client, tse)["evidence"]["checks"][WARNING] == "warning"  # exploratory sees the attached result
    assert WARNING not in _trust(client, tse, profile="curated")["evidence"]["checks"]


def test_curated_with_only_a_hidden_result_has_nothing_to_warn_about(client, db_session):
    """Kills: a hidden result's count leaking through as 'passed'/'warning' instead of not_applicable."""
    _, ts, tse, (hidden,) = _ts_with_freqs(db_session, "UDW6", [[-300.0, -200.0, 500.0]])
    set_review(db_session, record_type=SubmissionRecordType.transition_state_entry, record_id=tse.id, status=RecordReviewStatus.approved)
    set_review(db_session, record_type=SubmissionRecordType.transition_state, record_id=ts.id, status=RecordReviewStatus.approved)
    assert _trust(client, tse, profile="curated")["evidence"]["checks"][WARNING] == "not_applicable"
