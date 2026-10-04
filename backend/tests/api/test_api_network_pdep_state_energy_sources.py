"""A state energy names every participant's source, and equals their sum (#678).

#675 made ``state_energies[].source_calculation_key`` accept the calculation of any one participant,
which left a bimolecular state's stored source as one summand of several. The rule, the
conventions that have a defined sum, and the tolerance are in ``app.services.network_energy_sources``.

The payload is the parallel-path network of ``test_network_pdep_upload`` with every state energy
restated as ``electronic_only`` on a declared zero, computed here from the single-point energies the
fixture stores:

* ``entrance`` = ethyl + O2, ``well_RO2``, ``exit`` = ethene + HO2, ``well_iso``.

The expected sums are written out here by hand from the fixture's hartree values, never derived from
the service. Every refusal is asserted by ``code`` and ``context``, and every accepted case checks the
stored rows, so a payload that silently dropped a citation cannot pass as "accepted".
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.chemistry.units import HARTREE_TO_KJ_MOL as H
from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation
from app.db.models.network_pdep import (
    NetworkSolve,
    NetworkSolveStateEnergy,
    NetworkSolveStateEnergySource,
)
from app.schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest
from app.workflows.network_pdep import persist_network_pdep_upload
from tests.workflows.test_network_pdep_upload import _parallel_path_payload

_PDEP_URL = "/api/v1/uploads/networks/pdep"
_MISMATCH = "network_state_energy_sum_mismatch"
_SUBJECT = "network_energy_source_subject_mismatch"
_NOT_COMPARED = "network_state_energy_sum_not_compared"
_PARTIAL = "network_state_energy_sources_partial"

# The hartree energies the fixture stores for each species' single point.
_E = {
    "ethyl": -79.8,
    "O2": -150.2,
    "ethylperoxy": -229.1,
    "ethene": -78.4,
    "HO2": -150.8,
    "ethylperoxy_isomer": -229.08,
}

# (species key, calculation key) per participant of each state, in payload order.
_SOURCES = {
    "entrance": [("ethyl", "ethyl_sp"), ("O2", "O2_sp")],
    "well_RO2": [("ethylperoxy", "etoo_sp")],
    "exit": [("ethene", "ethene_sp"), ("HO2", "HO2_sp")],
    "well_iso": [("ethylperoxy_isomer", "etoo_iso_sp")],
}
_ORDER = ["entrance", "well_RO2", "exit", "well_iso"]


def _sum_hartree(state: str) -> float:
    return sum(_E[species] for species, _ in _SOURCES[state])


def _entry(state: str, energy_kj_mol: float, *, zero: str, correction: str = "electronic_only") -> dict:
    return {
        "state_key": state,
        "energy_kj_mol": energy_kj_mol,
        "energy_zero_convention": zero,
        "correction_convention": correction,
        "source_calculation_keys": [
            {"species_key": species, "calculation_key": calc} for species, calc in _SOURCES[state]
        ],
    }


def _payload(*, zero: str = "absolute") -> dict:
    """The fixture network with every state energy a correct, fully sourced sum."""
    payload = deepcopy(_parallel_path_payload())
    lowest = min(_sum_hartree(state) for state in _ORDER)
    entries = []
    for state in _ORDER:
        hartree = _sum_hartree(state) if zero == "absolute" else _sum_hartree(state) - lowest
        entries.append(_entry(state, hartree * H, zero=zero))
    payload["solve"]["state_energies"] = entries
    return payload


def _post(client, payload: dict):
    return client.post(_PDEP_URL, json=payload)


def _row_for(db_session, energy_kj_mol: float) -> NetworkSolveStateEnergy:
    rows = db_session.scalars(select(NetworkSolveStateEnergy)).all()
    (row,) = [r for r in rows if abs(r.energy_kj_mol - energy_kj_mol) < 1e-9]
    return row


def _refused(resp, code: str) -> dict:
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body.get("code") == code, body
    assert "id" not in body["context"], body
    return body["context"]


def _codes(resp) -> list[str]:
    return [w["code"] for w in resp.json()["warnings"]]


# ---------------------------------------------------------------------------
# Accepted: every participant cited and the energy is their sum
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("zero", ["absolute", "lowest_state", "entrance_channel"])
def test_a_two_species_state_with_both_sources_and_the_summed_energy_is_accepted(
    client, db_session, zero: str
) -> None:
    payload = _payload(zero=zero)
    resp = _post(client, payload)
    assert resp.status_code == 201, resp.text
    assert _PARTIAL not in _codes(resp) and _NOT_COMPARED not in _codes(resp)

    solve = db_session.scalars(select(NetworkSolve)).one()
    energy_rows = db_session.scalars(select(NetworkSolveStateEnergy)).all()
    assert len(energy_rows) == 4
    assert {(r.source_sum_comparison, r.source_sum_not_compared_reason) for r in energy_rows} == {
        ("agrees", None)
    }
    # Only the one-participant states (the wells) also fill the older single slot, with the
    # source that covers their whole sum; a two-species state leaves it empty. No computed total
    # is stored.
    assert sorted(r.source_calculation_id is None for r in energy_rows) == [False, False, True, True]
    sources = db_session.scalars(select(NetworkSolveStateEnergySource)).all()
    assert len(sources) == 6  # 2 + 1 + 2 + 1 participants, one row each
    assert {s.solve_id for s in sources} == {solve.id}
    calc_by_id = {s.calculation_id: s for s in sources}
    assert len(calc_by_id) == 6


def test_the_stored_sources_are_each_participants_own_calculation(client, db_session) -> None:
    assert _post(client, _payload()).status_code == 201
    for source in db_session.scalars(select(NetworkSolveStateEnergySource)).all():
        assert db_session.get(Calculation, source.calculation_id).species_entry_id == source.species_entry_id


# ---------------------------------------------------------------------------
# The sum check
# ---------------------------------------------------------------------------


def test_an_energy_that_is_one_term_short_of_the_sum_is_refused(client) -> None:
    """Index 0: the entrance energy states ethyl alone although both species are cited."""
    payload = _payload()
    payload["solve"]["state_energies"][0]["energy_kj_mol"] = _E["ethyl"] * H
    context = _refused(_post(client, payload), _MISMATCH)
    assert context["field"] == "solve.state_energies[0].energy_kj_mol"
    assert context["state_key"] == "entrance"
    assert context["stated_energy_kj_mol"] == pytest.approx(_E["ethyl"] * H)
    assert context["stored_sum_kj_mol"] == pytest.approx(_sum_hartree("entrance") * H)


def test_a_later_state_one_term_short_is_refused_at_its_own_index(client) -> None:
    """Index 2, not 0: a check that looked only at the first energy would pass this."""
    payload = _payload()
    payload["solve"]["state_energies"][2]["energy_kj_mol"] = _E["HO2"] * H
    context = _refused(_post(client, payload), _MISMATCH)
    assert context["field"] == "solve.state_energies[2].energy_kj_mol"
    assert context["state_key"] == "exit"


def test_an_energy_one_term_too_many_is_refused(client) -> None:
    payload = _payload()
    payload["solve"]["state_energies"][1]["energy_kj_mol"] = (_E["ethylperoxy"] + _E["ethene"]) * H
    _refused(_post(client, payload), _MISMATCH)


def test_the_tolerance_is_the_printed_precision_one(client, db_session) -> None:
    """n = 1 + 1 + 1 = 3 rounded quantities: 1.5e-6 Eh. 1.2e-6 agrees."""
    payload = _payload()
    payload["solve"]["state_energies"][0]["energy_kj_mol"] += 1.2e-6 * H
    resp = _post(client, payload)
    assert resp.status_code == 201, resp.text
    row = _row_for(db_session, _payload()["solve"]["state_energies"][0]["energy_kj_mol"] + 1.2e-6 * H)
    assert (row.source_sum_comparison, row.source_sum_not_compared_reason) == ("agrees", None)


def test_just_outside_the_tolerance_is_not_compared_not_refused(client, db_session) -> None:
    """2.5e-6 Eh is beyond n = 3 printed precision but far inside honest rounding of a kJ/mol."""
    stated = _payload()["solve"]["state_energies"][0]["energy_kj_mol"] + 2.5e-6 * H
    payload = _payload()
    payload["solve"]["state_energies"][0]["energy_kj_mol"] = stated
    resp = _post(client, payload)
    assert resp.status_code == 201, resp.text
    row = _row_for(db_session, stated)
    assert (row.source_sum_comparison, row.source_sum_not_compared_reason) == (
        "not_compared",
        "stated_precision_unknown",
    )
    (warning,) = [w for w in resp.json()["warnings"] if w["code"] == _NOT_COMPARED]
    assert "stated_precision_unknown" in warning["message"] and "energy_precision_kj_mol" in warning["message"]


def _entrance_kj() -> float:
    return _payload()["solve"]["state_energies"][0]["energy_kj_mol"]


_ROUNDED_OR_CONVERTED = {
    "kj_to_one_decimal": lambda x: round(x, 1),
    "kj_to_whole_units": lambda x: float(round(x)),
    "kcal_to_two_decimals": lambda x: round(x / 4.184, 2) * 4.184,
    "hartree_times_2625_5": lambda x: (x / H) * 2625.5,
    "hartree_times_627_509_4_184": lambda x: (x / H) * 627.509 * 4.184,
}


@pytest.mark.parametrize("name", sorted(_ROUNDED_OR_CONVERTED))
def test_a_correctly_rounded_or_converted_energy_is_not_refused(client, db_session, name: str) -> None:
    """The reviewer's five: each is a right number written with less precision than the sum."""
    stated = _ROUNDED_OR_CONVERTED[name](_entrance_kj())
    payload = _payload()
    payload["solve"]["state_energies"][0]["energy_kj_mol"] = stated
    resp = _post(client, payload)
    assert resp.status_code == 201, resp.text
    row = _row_for(db_session, stated)
    assert (row.source_sum_comparison, row.source_sum_not_compared_reason) == (
        "not_compared",
        "stated_precision_unknown",
    )
    assert _NOT_COMPARED in _codes(resp)


def test_a_stated_precision_makes_a_rounded_energy_agree(client, db_session) -> None:
    stated = round(_entrance_kj(), 1)
    payload = _payload()
    entry = payload["solve"]["state_energies"][0]
    entry["energy_kj_mol"] = stated
    entry["energy_precision_kj_mol"] = 0.1
    resp = _post(client, payload)
    assert resp.status_code == 201, resp.text
    row = _row_for(db_session, stated)
    assert (row.source_sum_comparison, row.source_sum_not_compared_reason) == ("agrees", None)
    assert row.energy_precision_kj_mol == 0.1
    assert _NOT_COMPARED not in _codes(resp)


def test_a_stated_precision_tightens_the_bound(client) -> None:
    """1.5 kJ/mol off: inside the default 1 kcal/mol allowance, outside half of a stated 0.1 kJ/mol."""
    without = _payload()
    without["solve"]["state_energies"][0]["energy_kj_mol"] += 1.5
    assert _post(client, without).status_code == 201

    stated = _payload()
    entry = stated["solve"]["state_energies"][0]
    entry["energy_kj_mol"] += 1.5
    entry["energy_precision_kj_mol"] = 0.1
    context = _refused(_post(client, stated), _MISMATCH)
    assert context["field"] == "solve.state_energies[0].energy_kj_mol"
    assert context["allowance_kj_mol"] < 1.5


def test_beyond_honest_rounding_is_refused(client) -> None:
    """5 kJ/mol off is more than a kcal/mol of rounding and a conversion constant can explain."""
    payload = _payload()
    payload["solve"]["state_energies"][0]["energy_kj_mol"] += 5.0
    context = _refused(_post(client, payload), _MISMATCH)
    assert context["allowance_kj_mol"] < 5.0


def test_a_non_positive_or_non_finite_precision_is_a_wire_error(client) -> None:
    for bad in (0, -0.1):
        payload = _payload()
        payload["solve"]["state_energies"][0]["energy_precision_kj_mol"] = bad
        assert _post(client, payload).status_code == 422


@pytest.mark.parametrize("zero", ["lowest_state", "entrance_channel"])
def test_a_shared_zero_compares_states_with_each_other(client, zero: str) -> None:
    """One state's energy 50 kJ/mol off, on a zero it shares with three correct states.

    The perturbation keeps ``entrance`` the lowest state, so it stays the reference.
    """
    payload = _payload(zero=zero)
    payload["solve"]["state_energies"][2]["energy_kj_mol"] += 50.0
    context = _refused(_post(client, payload), _MISMATCH)
    assert context["field"] == "solve.state_energies[2].energy_kj_mol"
    assert context["energy_zero_convention"] == zero
    assert context["compared_with_state_key"] in {"entrance", "well_RO2", "well_iso"}
    assert context["compared_with_field"].endswith(".energy_kj_mol")
    assert set(context["inconsistent_state_keys"]) == {"entrance", "well_RO2", "well_iso"}


def test_the_lowest_state_can_be_the_one_that_is_wrong(client) -> None:
    """The outlier is named by majority, not by which state happens to be lowest."""
    payload = _payload(zero="lowest_state")
    payload["solve"]["state_energies"][0]["energy_kj_mol"] -= 50.0  # entrance, already the lowest
    context = _refused(_post(client, payload), _MISMATCH)
    assert context["field"] == "solve.state_energies[0].energy_kj_mol"
    assert context["state_key"] == "entrance"


def test_a_shared_zero_with_one_comparable_state_is_not_compared(client, db_session) -> None:
    payload = _payload(zero="lowest_state")
    for entry in payload["solve"]["state_energies"][1:]:
        entry.pop("source_calculation_keys")  # states with no source take no part in the comparison
    payload["solve"]["state_energies"][0]["energy_kj_mol"] = 123.0  # any number: nothing to compare against
    resp = _post(client, payload)
    assert resp.status_code == 201, resp.text
    row = _row_for(db_session, 123.0)
    assert (row.source_sum_comparison, row.source_sum_not_compared_reason) == (
        "not_compared",
        "no_second_state_on_the_same_zero",
    )
    assert _NOT_COMPARED in _codes(resp)


# ---------------------------------------------------------------------------
# Whose calculation it is, per participant
# ---------------------------------------------------------------------------


def test_a_source_from_a_species_outside_the_state_is_refused_at_its_slot(client) -> None:
    payload = _payload()
    payload["solve"]["state_energies"][0]["source_calculation_keys"][1]["calculation_key"] = "ethene_sp"
    context = _refused(_post(client, payload), _SUBJECT)
    assert context["field"] == "solve.state_energies[0].source_calculation_keys[1].calculation_key"
    assert context["stated_value"] == "O2"
    assert context["state_key"] == "entrance"


def test_a_source_from_the_other_participant_of_the_state_is_refused(client) -> None:
    """Both species are in the state, so #675 would accept it; the slot names the species."""
    payload = _payload()
    keys = payload["solve"]["state_energies"][2]["source_calculation_keys"]
    keys[0]["calculation_key"], keys[1]["calculation_key"] = keys[1]["calculation_key"], keys[0]["calculation_key"]
    context = _refused(_post(client, payload), _SUBJECT)
    assert context["field"] == "solve.state_energies[2].source_calculation_keys[0].calculation_key"
    assert context["stated_value"] == "ethene"


def test_a_species_that_is_not_a_participant_of_the_state_is_refused(client) -> None:
    payload = _payload()
    payload["solve"]["state_energies"][0]["source_calculation_keys"][1] = {
        "species_key": "ethene",
        "calculation_key": "ethene_sp",
    }
    context = _refused(_post(client, payload), _SUBJECT)
    assert context["field"] == "solve.state_energies[0].source_calculation_keys[1].species_key"
    assert context["stated_value"] == "ethene"


def test_a_source_of_the_wrong_type_is_refused_at_its_slot(client) -> None:
    payload = _payload()
    payload["solve"]["state_energies"][2]["source_calculation_keys"][1]["calculation_key"] = "ethyl_freq"
    context = _refused(_post(client, payload), "network_energy_source_type_mismatch")
    assert context["field"] == "solve.state_energies[2].source_calculation_keys[1].calculation_key"
    assert context["actual_calculation_type"] == "freq"


def test_one_source_form_only_and_one_entry_per_participant(client) -> None:
    both = _payload()
    both["solve"]["state_energies"][0]["source_calculation_key"] = "ethyl_sp"
    assert _post(client, both).status_code == 422

    twice = _payload()
    twice["solve"]["state_energies"][0]["source_calculation_keys"][1]["species_key"] = "ethyl"
    assert _post(client, twice).status_code == 422


def test_an_undeclared_source_calculation_key_is_refused_at_its_index(client) -> None:
    payload = _payload()
    payload["solve"]["state_energies"][2]["source_calculation_keys"][1]["calculation_key"] = "nope_sp"
    resp = _post(client, payload)
    assert resp.status_code == 422, resp.text
    assert resp.json()["context"]["field"] == (
        "solve.state_energies[2].source_calculation_keys[1].calculation_key"
    )


# ---------------------------------------------------------------------------
# Stoichiometry
# ---------------------------------------------------------------------------


def _with_dimer_state(payload: dict, energy_kj_mol: float) -> dict:
    """``entrance`` becomes 2 ethyl + O2: ethyl is one participant with coefficient 2."""
    entrance = next(state for state in payload["states"] if state["key"] == "entrance")
    entrance["participants"] = [{"species_key": "ethyl", "stoichiometry": 2}, {"species_key": "O2"}]
    entry = payload["solve"]["state_energies"][0]
    entry["energy_kj_mol"] = energy_kj_mol
    return payload


_TWO_ETHYL_PLUS_O2 = (2 * _E["ethyl"] + _E["O2"]) * H


def test_a_state_with_coefficient_two_is_the_sum_with_the_term_twice(client, db_session) -> None:
    payload = _with_dimer_state(_payload(), _TWO_ETHYL_PLUS_O2)
    resp = _post(client, payload)
    assert resp.status_code == 201, resp.text
    row = _row_for(db_session, _TWO_ETHYL_PLUS_O2)
    assert (row.source_sum_comparison, row.source_sum_not_compared_reason) == ("agrees", None)
    # One participant row per species, so one source row each: the coefficient is not a copy index.
    sources = [s for s in db_session.scalars(select(NetworkSolveStateEnergySource)).all() if s.state_id == row.state_id]
    assert len(sources) == 2


def test_a_state_with_coefficient_two_summed_once_is_refused(client) -> None:
    payload = _with_dimer_state(_payload(), (_E["ethyl"] + _E["O2"]) * H)
    context = _refused(_post(client, payload), _MISMATCH)
    assert context["state_key"] == "entrance"
    assert context["stored_sum_kj_mol"] == pytest.approx(_TWO_ETHYL_PLUS_O2)


def test_the_tolerance_counts_the_coefficient_as_weight(client, db_session) -> None:
    """2 ethyl + O2: n = 1 + 2 + 1 = 4 -> 2e-6 Eh. Counting summands (n = 3) would not agree at 1.8e-6."""
    stated = _TWO_ETHYL_PLUS_O2 + 1.8e-6 * H
    resp = _post(client, _with_dimer_state(_payload(), stated))
    assert resp.status_code == 201, resp.text
    row = _row_for(db_session, stated)
    assert (row.source_sum_comparison, row.source_sum_not_compared_reason) == ("agrees", None)


def test_a_coefficient_two_state_past_its_weighted_tolerance_is_not_compared(client, db_session) -> None:
    stated = _TWO_ETHYL_PLUS_O2 + 2.5e-6 * H
    resp = _post(client, _with_dimer_state(_payload(), stated))
    assert resp.status_code == 201, resp.text
    row = _row_for(db_session, stated)
    assert row.source_sum_not_compared_reason == "stated_precision_unknown"


# ---------------------------------------------------------------------------
# The shared-zero difference branch (the one the production ingester uses)
# ---------------------------------------------------------------------------


def _relative_payload(*, two_ethyl: bool = False, sourced: tuple[str, ...] = tuple(_ORDER)) -> dict:
    """``lowest_state`` energies, correct, for the fixture network.

    ``two_ethyl`` turns ``entrance`` into 2 ethyl + O2. States not in ``sourced`` cite nothing, so
    they take no part in the comparison.
    """
    payload = deepcopy(_parallel_path_payload())
    sums = {state: _sum_hartree(state) for state in _ORDER}
    if two_ethyl:
        entrance = next(state for state in payload["states"] if state["key"] == "entrance")
        entrance["participants"] = [{"species_key": "ethyl", "stoichiometry": 2}, {"species_key": "O2"}]
        sums["entrance"] = 2 * _E["ethyl"] + _E["O2"]
    lowest = min(sums.values())
    entries = []
    for state in _ORDER:
        entry = _entry(state, (sums[state] - lowest) * H, zero="lowest_state")
        if state not in sourced:
            entry.pop("source_calculation_keys")
        entries.append(entry)
    payload["solve"]["state_energies"] = entries
    return payload


def test_a_coefficient_two_entrance_on_a_shared_zero_summed_correctly_is_accepted(client, db_session) -> None:
    resp = _post(client, _relative_payload(two_ethyl=True))
    assert resp.status_code == 201, resp.text
    rows = db_session.scalars(select(NetworkSolveStateEnergy)).all()
    assert {(r.source_sum_comparison, r.source_sum_not_compared_reason) for r in rows} == {("agrees", None)}


def test_a_coefficient_two_entrance_on_a_shared_zero_summed_once_is_refused(client) -> None:
    payload = _relative_payload(two_ethyl=True)
    # The entrance energy as if it were ethyl + O2 (one ethyl term short of 2 ethyl + O2), on the
    # zero set by the 2 ethyl + O2 sum: the other three are unchanged.
    once = -_E["ethyl"] * H
    payload["solve"]["state_energies"][0]["energy_kj_mol"] = once
    context = _refused(_post(client, payload), _MISMATCH)
    assert context["state_key"] == "entrance"


def _shift(payload: dict, index: int, hartree: float) -> float:
    entry = payload["solve"]["state_energies"][index]
    entry["energy_kj_mol"] += hartree * H
    return entry["energy_kj_mol"]


@pytest.mark.parametrize(
    ("gap_hartree", "band"),
    [(1.9e-6, ("agrees", None)), (2.4e-6, ("not_compared", "stated_precision_unknown"))],
)
def test_the_difference_tolerance_of_two_single_participant_states(
    client, db_session, gap_hartree: float, band: tuple
) -> None:
    """Two one-species states on a shared zero: n = 2 + 1 + 1 = 4 -> 2e-6 Eh between them."""
    payload = _relative_payload(sourced=("well_RO2", "well_iso"))
    stated = _shift(payload, 3, gap_hartree)
    resp = _post(client, payload)
    assert resp.status_code == 201, resp.text
    row = _row_for(db_session, stated)
    assert (row.source_sum_comparison, row.source_sum_not_compared_reason) == band


@pytest.mark.parametrize(
    ("gap_hartree", "band"),
    [(2.8e-6, ("agrees", None)), (3.3e-6, ("not_compared", "stated_precision_unknown"))],
)
def test_the_difference_tolerance_weighs_a_coefficient_two_state(
    client, db_session, gap_hartree: float, band: tuple
) -> None:
    """2 ethyl + O2 against one species: n = 2 + 3 + 1 = 6 -> 3e-6 Eh. Ignoring the coefficient gives 2.5e-6."""
    payload = _relative_payload(two_ethyl=True, sourced=("entrance", "well_RO2"))
    stated = _shift(payload, 1, gap_hartree)
    resp = _post(client, payload)
    assert resp.status_code == 201, resp.text
    row = _row_for(db_session, stated)
    assert (row.source_sum_comparison, row.source_sum_not_compared_reason) == band


# ---------------------------------------------------------------------------
# Not compared, with the reason: never guessed
# ---------------------------------------------------------------------------


_NOT_COMPARABLE = [
    # (zero, correction, reason)
    ("absolute", "electronic_plus_zpe", "zpe_not_in_source"),
    ("absolute", "atom_and_bond_corrected", "convention_not_summable"),
    ("absolute", "thermal_enthalpy_298k", "convention_not_summable"),
    ("absolute", "other", "convention_not_summable"),
    ("separated_reactants", "electronic_only", "energy_zero_not_comparable"),
    ("other", "electronic_only", "energy_zero_not_comparable"),
]


@pytest.mark.parametrize(("zero", "correction", "reason"), _NOT_COMPARABLE)
def test_a_sum_that_cannot_be_formed_is_stored_as_not_compared_and_warned(
    client, db_session, zero: str, correction: str, reason: str
) -> None:
    payload = _payload()
    nonsense = 1234.5  # a contradiction if it were compared; it must not be
    entry = payload["solve"]["state_energies"][2]
    entry.update(
        energy_kj_mol=nonsense,
        energy_zero_convention=zero,
        correction_convention=correction,
        convention_note="stated by the test" if "other" in (zero, correction) else None,
    )
    resp = _post(client, payload)
    assert resp.status_code == 201, resp.text
    row = _row_for(db_session, nonsense)
    assert (row.source_sum_comparison, row.source_sum_not_compared_reason) == ("not_compared", reason)
    (warning,) = [w for w in resp.json()["warnings"] if w["code"] == _NOT_COMPARED]
    assert "'exit'" in warning["message"] and reason in warning["message"]
    # The other three states, correctly summed on an absolute zero, still agree.
    others = [r for r in db_session.scalars(select(NetworkSolveStateEnergy)).all() if r is not row]
    assert {r.source_sum_comparison for r in others} == {"agrees"}


def test_no_source_at_all_is_stored_as_not_compared_without_a_warning(client, db_session) -> None:
    payload = _payload()
    payload["solve"]["state_energies"][1].pop("source_calculation_keys")
    payload["solve"]["state_energies"][1]["energy_kj_mol"] = 99.0
    resp = _post(client, payload)
    assert resp.status_code == 201, resp.text
    row = _row_for(db_session, 99.0)
    assert (row.source_sum_comparison, row.source_sum_not_compared_reason) == (
        "not_compared",
        "no_source_stated",
    )
    assert _NOT_COMPARED not in _codes(resp) and _PARTIAL not in _codes(resp)


# ---------------------------------------------------------------------------
# The legacy single source
# ---------------------------------------------------------------------------


def _legacy_payload() -> dict:
    payload = _payload()
    entry = payload["solve"]["state_energies"][0]
    entry.pop("source_calculation_keys")
    entry["source_calculation_key"] = "ethyl_sp"
    entry["energy_kj_mol"] = 7.0
    return payload


def test_a_single_source_on_a_two_species_state_warns_and_is_not_compared(client, db_session) -> None:
    resp = _post(client, _legacy_payload())
    assert resp.status_code == 201, resp.text
    (warning,) = [w for w in resp.json()["warnings"] if w["code"] == _PARTIAL]
    assert warning["field"] == "solve.state_energies[0]"
    assert "'O2'" in warning["message"]
    row = _row_for(db_session, 7.0)
    assert row.source_calculation_id is not None
    assert (row.source_sum_comparison, row.source_sum_not_compared_reason) == ("not_compared", "sources_incomplete")
    # The partial warning is not repeated as a not-compared one.
    assert _NOT_COMPARED not in _codes(resp)
    # The single slot wrote no participant rows for this state: nothing is invented for O2.
    listed = db_session.scalars(
        select(NetworkSolveStateEnergySource).where(NetworkSolveStateEnergySource.state_id == row.state_id)
    ).all()
    assert listed == []


def test_a_legacy_single_source_reads_back_as_partial(client, db_session) -> None:
    assert _post(client, _legacy_payload()).status_code == 201
    solve = db_session.scalars(select(NetworkSolve)).one()
    body = client.get(f"/api/v1/scientific/network-solves/{solve.public_ref}?include=state_energies").json()
    energies = body["record"]["state_energies"]
    partial = [e for e in energies if e["partial_sources"]]
    assert len(partial) == 1
    (legacy,) = partial
    assert legacy["energy_kj_mol"] == 7.0
    assert len(legacy["sources"]) == 1  # the one stored source; the O2 source is not invented
    assert legacy["sources"][0]["stoichiometry"] == 1
    assert legacy["source_calculation_ref"] == legacy["sources"][0]["calculation_ref"]
    assert legacy["source_sum_comparison"] == "not_compared"
    assert legacy["source_sum_not_compared_reason"] == "sources_incomplete"


def test_a_complete_state_reads_back_every_source_and_is_not_partial(client, db_session) -> None:
    assert _post(client, _payload()).status_code == 201
    solve = db_session.scalars(select(NetworkSolve)).one()
    body = client.get(f"/api/v1/scientific/network-solves/{solve.public_ref}?include=state_energies").json()
    energies = body["record"]["state_energies"]
    assert [e["partial_sources"] for e in energies] == [False] * 4
    assert sorted(len(e["sources"]) for e in energies) == [1, 1, 2, 2]
    assert {e["source_sum_comparison"] for e in energies} == {"agrees"}
    # Refs only: no database id anywhere in the state energy.
    assert all("id" not in key.split("_") for e in energies for key in e)
    for energy in energies:
        for source in energy["sources"]:
            assert source["calculation_ref"].startswith("calc_")
            assert source["species_entry_ref"].startswith("spe")


def test_a_single_source_on_a_single_species_state_is_complete_and_compared(client, db_session) -> None:
    payload = _payload()
    entry = payload["solve"]["state_energies"][1]
    entry.pop("source_calculation_keys")
    entry["source_calculation_key"] = "etoo_sp"
    resp = _post(client, payload)
    assert resp.status_code == 201, resp.text
    assert _PARTIAL not in _codes(resp)
    row = _row_for(db_session, _sum_hartree("well_RO2") * H)
    assert row.source_calculation_id is not None
    assert row.source_sum_comparison == "agrees"


def test_a_single_source_energy_that_contradicts_its_calculation_is_refused(client) -> None:
    payload = _payload()
    entry = payload["solve"]["state_energies"][3]
    entry.pop("source_calculation_keys")
    entry["source_calculation_key"] = "etoo_iso_sp"
    entry["energy_kj_mol"] += 5.0
    context = _refused(_post(client, payload), _MISMATCH)
    assert context["field"] == "solve.state_energies[3].energy_kj_mol"


# ---------------------------------------------------------------------------
# The service re-checks; it does not rely on the wire schema
# ---------------------------------------------------------------------------


def _persist(db_conn, request: NetworkPDepUploadRequest) -> None:
    with Session(db_conn) as session, session.begin():
        actor = AppUser(username="network_state_sum_tester")
        session.add(actor)
        session.flush()
        persist_network_pdep_upload(session, request, created_by=actor.id)


def _unvalidated(payload: dict, index: int, **update) -> NetworkPDepUploadRequest:
    """A valid request, then one state energy edited with ``model_copy`` so no validator sees it."""
    request = NetworkPDepUploadRequest(**payload)
    assert request.solve is not None
    energies = [e.model_copy(update=update) if i == index else e for i, e in enumerate(request.solve.state_energies)]
    return request.model_copy(update={"solve": request.solve.model_copy(update={"state_energies": energies})})


def test_service_refuses_an_unvalidated_sum_mismatch(db_conn) -> None:
    with pytest.raises(CodedValueError) as raised:
        _persist(db_conn, _unvalidated(_payload(), 2, energy_kj_mol=_E["ethene"] * H))
    assert raised.value.code == _MISMATCH
    assert raised.value.context["field"] == "solve.state_energies[2].energy_kj_mol"


def test_service_refuses_an_unvalidated_wrong_participant_source(db_conn) -> None:
    request = NetworkPDepUploadRequest(**_payload())
    sources = list(request.solve.state_energies[2].source_calculation_keys)
    sources[1] = sources[1].model_copy(update={"calculation_key": "ethene_sp"})
    with pytest.raises(CodedValueError) as raised:
        _persist(db_conn, _unvalidated(_payload(), 2, source_calculation_keys=sources))
    assert raised.value.code == _SUBJECT
    assert raised.value.context["field"] == "solve.state_energies[2].source_calculation_keys[1].calculation_key"


def test_service_refuses_both_source_forms_given_to_it(db_conn) -> None:
    with pytest.raises(ValueError, match="not both"):
        _persist(db_conn, _unvalidated(_payload(), 0, source_calculation_key="ethyl_sp"))


def test_service_accepts_the_same_request_without_the_edit(db_conn) -> None:
    """The negative half of the three tests above."""
    _persist(db_conn, NetworkPDepUploadRequest(**_payload()))
