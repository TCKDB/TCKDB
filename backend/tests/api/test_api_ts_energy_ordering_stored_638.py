"""What an energy-ordering record says about the stored energies, over HTTP (#638).

The comparison itself is pinned at the persistence seam in
``tests/services/test_ts_energy_ordering_stored_energies.py``. This is the round
trip: the refusal a producer receives, the warning for an energy that could not
be compared, and what a reader sees afterwards.
"""

from __future__ import annotations

from tests.api.test_api_ts_contract_621 import (
    STORED_ZPE_HARTREE,
    _bundle,
    _codes,
    _energy_ordering,
    _entry_read,
    _entry_ref,
    _evidence_by_kind,
    _ok,
    _post_bundle,
    state_stored_energies,
)

_NOT_COMPARED = "transition_state_energy_ordering_not_compared"


def _compared(client, db_session, entry_id: int) -> dict[tuple[str, str], tuple[str | None, str | None]]:
    record = _entry_read(client, _entry_ref(db_session, entry_id), "validation_evidence")
    return {
        (e["participant"], e["energy_kind"]): (e["stored_energy_comparison"], e["not_compared_reason"])
        for e in _evidence_by_kind(record)["energy_ordering"]["compared_energies"]
    }


def test_energies_that_match_the_stored_ones_read_back_as_agreeing(client, db_session) -> None:
    result = _ok(_post_bundle(client, _bundle([_energy_ordering()])))
    assert _NOT_COMPARED not in _codes(result)
    compared = _compared(client, db_session, result["transition_state_entry_id"])
    assert len(compared) == 8
    assert set(compared.values()) == {("agrees", None)}


def test_an_e0_whose_freq_stores_no_zpe_is_accepted_warned_and_read_back_as_not_compared(
    client, db_session
) -> None:
    payload = _bundle([_energy_ordering()])
    state_stored_energies(payload, zpe={k: v for k, v in STORED_ZPE_HARTREE.items() if k != "ts-freq"})
    for calculation in payload["transition_state"]["calculations"]:
        if calculation["key"] == "ts-freq":
            calculation.pop("freq_zpe_hartree", None)
    result = _ok(_post_bundle(client, payload))

    (warning,) = [w for w in result["warnings"] if w["code"] == _NOT_COMPARED]
    assert warning["field"] == "transition_state.validation_evidence[0].energies"
    compared = _compared(client, db_session, result["transition_state_entry_id"])
    assert compared[("ts", "e0")] == ("not_compared", "zpe_not_stated")
    assert {v for k, v in compared.items() if k != ("ts", "e0")} == {("agrees", None)}


def test_a_stated_e0_that_forgets_the_zpe_is_refused(client) -> None:
    record = _energy_ordering()
    # reactant:1's E0 stated as its electronic energy: the stored ZPE is missing from it.
    record["energies"][5] = dict(record["energies"][5], energy_hartree=-39.75)
    response = _post_bundle(client, _bundle([record]))
    assert response.status_code == 422, response.text[:800]
    body = response.json()
    assert body["code"] == "ts_energy_ordering_stated_energy_mismatch", body
    assert body["context"]["participant"] == "reactant:1", body
    assert body["context"]["energy_kind"] == "e0", body
    assert abs(body["context"]["stored_hartree"] - (-39.70)) < 1e-9, body
    assert body["context"]["stored_zpe_hartree"] == 0.05, body
