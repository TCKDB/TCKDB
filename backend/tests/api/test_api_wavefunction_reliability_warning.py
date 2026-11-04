"""Wavefunction reliability warnings on a stored calculation: closed-shell T1, <S^2> contamination, printed ideal <S^2>.

``t1_diagnostic_above_closed_shell_threshold`` is raised for a calculation whose entry has multiplicity 1, whose method
is a restricted coupled-cluster method with singles, and whose stored ``t1_diagnostic`` is strictly above 0.02 (Lee and
Taylor 1989). ``spin_contamination_above_conventional_threshold`` is raised when the stored ``s_squared`` is strictly
more than 0.1 from S(S+1) of the entry's multiplicity, and ``spin_expected_contradicts_multiplicity`` when the printed
ideal value is not that S(S+1). All are warnings on the calculation only: the value, the review status, the evidence
label and every selection are untouched, an open-shell or unrestricted T1 and a D1 earn nothing, and no sentence says
the system *is* multireference.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select
from tckdb_schemas.deposit_warning_messages import render_message

from app.db.models.calculation import Calculation, CalculationSpinDiagnostic, CalculationWavefunctionDiagnostic
from app.db.models.common import SubmissionRecordType
from app.db.models.deposit_warning import DepositWarning
from app.services.wavefunction_reliability_warnings import is_coupled_cluster_method
from tests.api.test_api_auth import _hydrogen_conformer_payload

CODE = "t1_diagnostic_above_closed_shell_threshold"
_CONFORMERS = "/api/v1/uploads/conformers"
_SOFTWARE = {"name": "Molpro", "version": "2024.1"}
_H2_XYZ = "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"
_H2_ENTRY = {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}


def _sp(method: str = "CCSD(T)", *, wavefunction: dict | None = None, spin: dict | None = None) -> dict:
    calc: dict = {
        "type": "sp",
        "software_release": _SOFTWARE,
        "level_of_theory": {"method": method, "basis": "cc-pVTZ"},
        "sp_result": {"electronic_energy_hartree": -1.17},
    }
    if wavefunction is not None:
        calc["wavefunction_diagnostic"] = wavefunction
    if spin is not None:
        calc["spin_diagnostic"] = spin
    return calc


def _conformer(calc: dict, *, entry: dict | None = None, xyz: str = _H2_XYZ, label: str | None = None) -> dict:
    payload = _hydrogen_conformer_payload()
    if label is not None:
        payload["label"] = label
    payload["species_entry"] = dict(entry or _H2_ENTRY)
    payload["geometry"] = {"xyz_text": xyz}
    payload["calculation"] = calc
    return payload


def _found(response) -> list[dict]:
    assert response.status_code in (200, 201), response.text[:800]
    return [w for w in response.json()["warnings"] if w["code"] == CODE]


def _post(client, calc: dict, **kwargs) -> list[dict]:
    return _found(client.post(_CONFORMERS, json=_conformer(calc, **kwargs)))


# ---------------------------------------------------------------------------
# the rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("t1", [0.0199, 0.02, 0.0, 0.0001])
def test_a_closed_shell_t1_at_or_below_the_line_earns_nothing(client, t1) -> None:
    assert _post(client, _sp(wavefunction={"t1_diagnostic": t1})) == []


@pytest.mark.parametrize("t1", [0.0201, 0.06, 0.5])
def test_a_closed_shell_t1_above_the_line_earns_the_warning(client, t1) -> None:
    (warning,) = _post(client, _sp(wavefunction={"t1_diagnostic": t1}))
    assert warning["context"] == {"t1_diagnostic": pytest.approx(t1), "threshold": 0.02, "multiplicity": 1}
    assert warning["field"] == "wavefunction_diagnostic.t1_diagnostic"


@pytest.mark.parametrize(
    "method",
    [
        "CCSD",
        "CCSD(T)",
        "ccsd(t)-f12",
        "CCSD(T)-F12a",
        "RCCSD(T)-F12",
        "RHF-RCCSD(T)",
        "DLPNO-CCSD(T)-F12D",
        "CCSD(T)-F12/RI",
        "LCCSD(T0)",
        "PNO-LCCSD(T0)",
        "LPNO-CCSD",
        "CCSD[T]",
        "ccsd(t)/cbs",
        "DLPNO-CCSD(T)",
        "dlpno-ccsd(t1)",
        "PNO-LCCSD(T)-F12",
        "QCISD",
        "QCISD(T)",
        "CCSDT",
    ],
)
def test_every_coupled_cluster_spelling_is_in_scope(client, method) -> None:
    assert len(_post(client, _sp(method, wavefunction={"t1_diagnostic": 0.03}))) == 1


@pytest.mark.parametrize(
    "method",
    [
        "MP2",
        "B3LYP",
        "CCD",
        "CC2",
        "CC3",
        "CISD",
        "CASSCF",
        "CBS-QB3",
        "W1",
        "HF",
        "bd(t)",
        "BCCD(T)",
        "EOM-CCSD",
        "UCCSD(T)",
        "UCCSD",
        "UHF-UCCSD(T)",
        "ROCCSD(T)",
    ],
)
def test_a_method_that_is_not_coupled_cluster_with_singles_earns_nothing(client, method) -> None:
    """A T1 stored beside another method is a producer's number; it is not Lee and Taylor's quantity."""
    assert _post(client, _sp(method, wavefunction={"t1_diagnostic": 0.5})) == []


def test_the_method_scope_is_one_function() -> None:
    assert is_coupled_cluster_method("CCSD(T)-F12")
    assert not is_coupled_cluster_method("MP2")
    assert not is_coupled_cluster_method(None)
    assert not is_coupled_cluster_method("  ")


def test_an_open_shell_t1_earns_nothing_at_any_value(client) -> None:
    doublet = {"smiles": "[H]", "charge": 0, "multiplicity": 2}
    for t1 in (0.08, 0.5):
        assert (
            _post(client, _sp(wavefunction={"t1_diagnostic": t1}), entry=doublet, xyz="1\nH atom\nH 0.0 0.0 0.0") == []
        )


def test_a_d1_alone_earns_nothing_at_any_value(client) -> None:
    assert _post(client, _sp(wavefunction={"d1_diagnostic": 5.0})) == []


def test_the_other_amplitudes_alone_earn_nothing(client) -> None:
    assert _post(client, _sp(wavefunction={"t1_norm": 5.0, "largest_t2_amplitude": 5.0})) == []


def test_a_d1_beside_a_low_t1_earns_nothing(client) -> None:
    assert _post(client, _sp(wavefunction={"t1_diagnostic": 0.01, "d1_diagnostic": 0.9})) == []


def test_an_absent_diagnostic_earns_nothing(client) -> None:
    assert _post(client, _sp()) == []


def test_a_calculation_with_no_level_of_theory_method_in_scope_earns_nothing_even_with_a_spin_row(client) -> None:
    """A clean spin row beside an out-of-scope method and a large T1 earns no T1 warning."""
    calc = _sp("MP2", wavefunction={"t1_diagnostic": 0.5}, spin={"s_squared": 0.0})
    assert _post(client, calc) == []


# ---------------------------------------------------------------------------
# what the warning is, and what it is not
# ---------------------------------------------------------------------------


def test_the_sentence_names_its_source_and_does_not_judge(client) -> None:
    (warning,) = _post(client, _sp(wavefunction={"t1_diagnostic": 0.0612}))
    message = warning["message"]
    assert message == render_message(CODE, warning["context"])
    assert "declared as a singlet and so treated as closed shell" in message
    assert "0.0612" in message and "0.02" in message
    assert "Lee and Taylor (1989)" in message
    assert "does not by itself establish multireference character" in message
    assert "stored as deposited" in message
    assert "is multireference" not in message and "are multireference" not in message


def test_the_stored_value_is_untouched_and_the_warning_sits_on_the_calculation(client) -> None:
    (warning,) = _post(client, _sp(wavefunction={"t1_diagnostic": 0.06}))
    session = client._db_session
    (row,) = session.scalars(select(CalculationWavefunctionDiagnostic)).all()
    assert row.t1_diagnostic == pytest.approx(0.06)
    (calc_id,) = session.scalars(select(Calculation.id)).all()
    (stored,) = session.scalars(select(DepositWarning).where(DepositWarning.code == CODE)).all()
    assert stored.record_type is SubmissionRecordType.calculation
    assert stored.record_id == calc_id
    # TCKDB words only: no id in the sentence, nothing the depositor typed.
    assert not re.search(rf"\b{calc_id}\b", warning["message"].replace("0.06", ""))
    assert "depositor_supplied" not in (warning["context"] or {})


def _read_without_warnings(client, response) -> dict:
    """The calculation read for an upload's primary calculation, minus what differs by construction."""
    ref = response.json()["primary_calculation"]["calculation_ref"]
    read = client.get(
        f"/api/v1/scientific/calculations/{ref}", params={"include": "wavefunction_diagnostic,trust,review"}
    )
    assert read.status_code == 200, read.text[:500]
    return read.json()


def test_a_flagged_calculation_changes_no_review_status_or_evidence_label(client) -> None:
    """The same upload with a T1 below and above the line differs by the warning and the T1 value, nothing else."""
    low_response = client.post(_CONFORMERS, json=_conformer(_sp(wavefunction={"t1_diagnostic": 0.01}), label="low"))
    high_response = client.post(_CONFORMERS, json=_conformer(_sp(wavefunction={"t1_diagnostic": 0.06}), label="high"))
    assert [w["code"] for w in high_response.json()["warnings"] if w["code"] == CODE] == [CODE]
    assert not [w for w in low_response.json()["warnings"] if w["code"] == CODE]
    low, high = _read_without_warnings(client, low_response), _read_without_warnings(client, high_response)

    # The review status, the evidence label and its checks are the same, and nothing was reviewed or selected.
    assert low["record"]["trust"]["review_status"] == high["record"]["trust"]["review_status"] == "not_reviewed"
    assert low["record"]["trust"]["evidence"]["label"] == high["record"]["trust"]["evidence"]["label"]
    assert low["record"]["trust"]["evidence"]["checks"] == high["record"]["trust"]["evidence"]["checks"]
    assert (
        low["review_summary"]
        == high["review_summary"]
        == {
            "approved": 0,
            "under_review": 0,
            "not_reviewed": 1,
            "deprecated": 0,
            "rejected": 0,
            "total": 1,
        }
    )
    assert low["record"]["review_history"] == high["record"]["review_history"]
    assert [row["status"] for row in high["record"]["review_history"]] == ["not_reviewed"]
    # The stored diagnostic differs only in its value.
    (low_wfn,), (high_wfn,) = low["record"]["wavefunction_diagnostic"], high["record"]["wavefunction_diagnostic"]
    assert low_wfn["t1_diagnostic"] == pytest.approx(0.01) and high_wfn["t1_diagnostic"] == pytest.approx(0.06)
    assert {k: v for k, v in low_wfn.items() if k != "t1_diagnostic"} == {
        k: v for k, v in high_wfn.items() if k != "t1_diagnostic"
    }


# ---------------------------------------------------------------------------
# spin: contamination against S(S+1) of the entry, and the printed ideal value against the entry
# ---------------------------------------------------------------------------

SPIN_CODE = "spin_contamination_above_conventional_threshold"
EXPECTED_CODE = "spin_expected_contradicts_multiplicity"

_ENTRIES = {
    1: (dict(_H2_ENTRY), _H2_XYZ),
    2: ({"smiles": "[H]", "charge": 0, "multiplicity": 2}, "1\nH atom\nH 0.0 0.0 0.0"),
    3: ({"smiles": "[O][O]", "charge": 0, "multiplicity": 3}, "2\nO2\nO 0.0 0.0 0.0\nO 0.0 0.0 1.21"),
    4: ({"smiles": "[N]", "charge": 0, "multiplicity": 4}, "1\nN atom\nN 0.0 0.0 0.0"),
}


def _spin_post(client, multiplicity: int, spin: dict | None, method: str = "UB3LYP") -> dict[str, list[dict]]:
    """The spin warnings by code for one upload to an entry of the given multiplicity."""
    entry, xyz = _ENTRIES[multiplicity]
    response = client.post(_CONFORMERS, json=_conformer(_sp(method, spin=spin), entry=entry, xyz=xyz))
    assert response.status_code in (200, 201), response.text[:800]
    found: dict[str, list[dict]] = {SPIN_CODE: [], EXPECTED_CODE: []}
    for warning in response.json()["warnings"]:
        if warning["code"] in found:
            found[warning["code"]].append(warning)
    return found


@pytest.mark.parametrize(
    "multiplicity, s_squared",
    [
        (1, 0.0),
        (1, 0.05),
        (2, 0.7501),
        (2, 0.75),
        (2, 0.85),  # exactly 0.1 from 0.75: on the line, strict
        (2, 0.65),
        (3, 2.0),
        (3, 2.1),  # exactly 0.1 from 2.0
        (3, 2.05),
        (4, 3.75),
        (4, 3.85),
    ],
)
def test_a_spin_value_within_the_conventional_criterion_earns_nothing(client, multiplicity, s_squared) -> None:
    found = _spin_post(client, multiplicity, {"s_squared": s_squared})
    assert found == {SPIN_CODE: [], EXPECTED_CODE: []}


@pytest.mark.parametrize(
    "multiplicity, s_squared, ideal",
    [
        (1, 0.2, 0.0),
        (2, 0.86, 0.75),
        (2, 1.10, 0.75),
        (2, 0.6, 0.75),
        (3, 2.15, 2.0),
        (3, 2.3, 2.0),
        (4, 3.9, 3.75),
        (4, 4.5, 3.75),
    ],
)
def test_a_spin_value_past_the_criterion_earns_the_contamination_warning(
    client, multiplicity, s_squared, ideal
) -> None:
    found = _spin_post(client, multiplicity, {"s_squared": s_squared})
    assert found[EXPECTED_CODE] == []
    (warning,) = found[SPIN_CODE]
    assert warning["field"] == "spin_diagnostic.s_squared"
    assert warning["context"] == {
        "s_squared": pytest.approx(s_squared),
        "ideal_s_squared": pytest.approx(ideal),
        "multiplicity": multiplicity,
        "threshold": 0.1,
    }


def test_the_expectation_is_the_entry_and_never_the_value_or_the_printed_ideal(client) -> None:
    """A doublet entry with a quartet-like <S^2> and a quartet-like printed ideal still warns on both counts."""
    found = _spin_post(client, 2, {"s_squared": 3.76, "s_squared_expected": 3.75})
    assert len(found[SPIN_CODE]) == 1 and len(found[EXPECTED_CODE]) == 1
    assert found[SPIN_CODE][0]["context"]["ideal_s_squared"] == pytest.approx(0.75)


def test_s_squared_annihilated_is_not_used(client) -> None:
    found = _spin_post(client, 2, {"s_squared": 0.76, "s_squared_annihilated": 2.0})
    assert found == {SPIN_CODE: [], EXPECTED_CODE: []}


@pytest.mark.parametrize(
    "multiplicity, expected, fires",
    [
        (2, 0.75, False),
        (2, 0.7505, False),
        (2, 0.7509, False),
        (2, 0.7511, True),
        (2, 0.7489, True),
        (2, 2.0, True),
        (3, 2.0, False),
        (3, 0.75, True),
        (1, 0.0, False),
        (1, 0.75, True),
        (4, 3.75, False),
        (4, 2.0, True),
    ],
)
def test_a_printed_ideal_that_contradicts_the_entry_earns_only_the_expected_finding(
    client, multiplicity, expected, fires
) -> None:
    ideal = {1: 0.0, 2: 0.75, 3: 2.0, 4: 3.75}[multiplicity]
    found = _spin_post(client, multiplicity, {"s_squared": ideal, "s_squared_expected": expected})
    assert found[SPIN_CODE] == []
    if not fires:
        assert found[EXPECTED_CODE] == []
        return
    (warning,) = found[EXPECTED_CODE]
    assert warning["field"] == "spin_diagnostic.s_squared_expected"
    assert warning["context"] == {
        "s_squared_expected": pytest.approx(expected),
        "expected_from_entry": pytest.approx(ideal),
        "multiplicity": multiplicity,
    }


def test_both_findings_are_two_separate_warnings(client) -> None:
    found = _spin_post(client, 2, {"s_squared": 1.1, "s_squared_expected": 2.0})
    assert len(found[SPIN_CODE]) == 1 and len(found[EXPECTED_CODE]) == 1
    assert found[SPIN_CODE][0]["message"] != found[EXPECTED_CODE][0]["message"]


def test_no_spin_row_earns_no_spin_warning_and_a_row_without_the_printed_ideal_earns_no_expected_one(client) -> None:
    assert _spin_post(client, 2, None) == {SPIN_CODE: [], EXPECTED_CODE: []}
    found = _spin_post(client, 2, {"s_squared": 1.1})
    assert len(found[SPIN_CODE]) == 1 and found[EXPECTED_CODE] == []


def test_the_spin_sentences_are_tckdb_words_that_do_not_judge_the_system(client) -> None:
    found = _spin_post(client, 2, {"s_squared": 1.1, "s_squared_expected": 2.0})
    contamination, expected = found[SPIN_CODE][0], found[EXPECTED_CODE][0]
    assert contamination["message"] == render_message(SPIN_CODE, contamination["context"])
    assert expected["message"] == render_message(EXPECTED_CODE, expected["context"])
    assert "conventional criterion for spin contamination" in contamination["message"]
    assert "a convention of the field, not a finding about this system" in contamination["message"]
    assert "stored as deposited" in contamination["message"]
    assert "1.1" in contamination["message"] and "0.75" in contamination["message"]
    assert "cannot tell which" in expected["message"] and "2" in expected["message"]
    for warning in (contamination, expected):
        assert "depositor_supplied" not in warning["context"]


def test_the_spin_row_is_stored_untouched_and_the_warnings_sit_on_the_calculation(client) -> None:
    _spin_post(client, 2, {"s_squared": 1.1, "s_squared_expected": 2.0})
    session = client._db_session
    (row,) = session.scalars(select(CalculationSpinDiagnostic)).all()
    assert row.s_squared == pytest.approx(1.1) and row.s_squared_expected == pytest.approx(2.0)
    (calc_id,) = session.scalars(select(Calculation.id)).all()
    stored = session.scalars(select(DepositWarning).where(DepositWarning.code.in_([SPIN_CODE, EXPECTED_CODE]))).all()
    assert {w.code for w in stored} == {SPIN_CODE, EXPECTED_CODE}
    assert all(w.record_type is SubmissionRecordType.calculation and w.record_id == calc_id for w in stored)


def test_a_spin_row_earns_no_t1_warning_and_a_t1_row_no_spin_warning(client) -> None:
    """The checks are independent: a closed-shell CCSD(T) with both a high T1 and a clean spin row warns once."""
    entry, xyz = _ENTRIES[1]
    calc = _sp(wavefunction={"t1_diagnostic": 0.06}, spin={"s_squared": 0.0})
    response = client.post(_CONFORMERS, json=_conformer(calc, entry=entry, xyz=xyz))
    codes = [w["code"] for w in response.json()["warnings"]]
    assert CODE in codes and SPIN_CODE not in codes and EXPECTED_CODE not in codes


# ---------------------------------------------------------------------------
# every route that persists a calculation runs the check (behaviour, not source text)
# ---------------------------------------------------------------------------

_T1 = {"t1_diagnostic": 0.06}


def _route_conformer():
    return _CONFORMERS, _conformer(_sp(wavefunction=_T1))


def _route_computed_species():
    calc = {"key": "sp0", **_sp(wavefunction=_T1)}
    opt = {
        "key": "opt0",
        "type": "opt",
        "software_release": _SOFTWARE,
        "level_of_theory": {"method": "ccsd(t)", "basis": "cc-pVTZ"},
        "opt_result": {"converged": True},
    }
    payload = {
        "species_entry": dict(_H2_ENTRY),
        "conformers": [
            {
                "key": "c0",
                "geometry": {"xyz_text": _H2_XYZ},
                "primary_calculation": opt,
                "additional_calculations": [calc],
            }
        ],
    }
    return "/api/v1/uploads/computed-species", payload


def _route_computed_reaction():
    from tests.workflows.test_computed_reaction_upload import _minimal_payload

    payload = _minimal_payload()
    species = payload["species"][2]
    calc = _sp(wavefunction=_T1)
    calc["sp_electronic_energy_hartree"] = calc.pop("sp_result")["electronic_energy_hartree"]
    species["calculations"].append({"key": "ch4-cc", "geometry_key": "ch4-geom", **calc})
    return "/api/v1/uploads/computed-reaction", payload


def _inline() -> list[dict]:
    return [{"key": "cc0", "calculation": _sp(wavefunction=_T1)}]


def _route_thermo():
    payload = {
        "enthalpy_reference_kind": "formation_298k",
        "species_entry": dict(_H2_ENTRY),
        "scientific_origin": "computed",
        "h298_kj_mol": 0.0,
        "calculations": _inline(),
    }
    return "/api/v1/uploads/thermo", payload


def _route_statmech():
    payload = {
        "species_entry": dict(_H2_ENTRY),
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 2,
        "calculations": _inline(),
    }
    return "/api/v1/uploads/statmech", payload


def _route_transport():
    payload = {
        "species_entry": dict(_H2_ENTRY),
        "scientific_origin": "computed",
        "sigma_angstrom": 2.05,
        "epsilon_over_k_k": 145.0,
        "dipole_debye": 0.0,
        "polarizability_angstrom3": 0.667,
        "rotational_relaxation": 0.0,
        "calculations": _inline(),
    }
    return "/api/v1/uploads/transport", payload


def _route_transition_state():
    from tests.api.test_api_uploads import _transition_state_payload

    payload = _transition_state_payload(n_imag=1, imag_freq_cm1=-1500.0)
    payload["multiplicity"] = 1
    payload["additional_calculations"].append(_sp(wavefunction=_T1))
    return "/api/v1/uploads/transition-states", payload


def _route_network():
    from tests.api.test_api_network_reads import _XYZ_AR, _pdep_payload

    payload = _pdep_payload()
    argon = next(s for s in payload["species"] if s["key"] == "Ar")
    argon.setdefault("calculations", []).append(
        {
            "key": "Ar_cc",
            "type": "sp",
            "geometry_key": "Ar_geom",
            "software_release": _SOFTWARE,
            "level_of_theory": {"method": "CCSD(T)", "basis": "cc-pVTZ"},
            "sp_electronic_energy_hartree": -527.0,
            "wavefunction_diagnostic": _T1,
        }
    )
    assert _XYZ_AR
    return "/api/v1/uploads/networks/pdep", payload


ROUTES = {
    "conformer": _route_conformer,
    "computed_species": _route_computed_species,
    "computed_reaction": _route_computed_reaction,
    "thermo": _route_thermo,
    "statmech": _route_statmech,
    "transport": _route_transport,
    "transition_state": _route_transition_state,
    "network_pdep": _route_network,
}


@pytest.mark.parametrize("route", list(ROUTES))
def test_every_route_that_persists_a_calculation_runs_the_check(client, route) -> None:
    url, payload = ROUTES[route]()
    response = client.post(url, json=payload)
    (warning,) = _found(response)
    assert warning["context"]["t1_diagnostic"] == pytest.approx(0.06)

    session = client._db_session
    flagged = set(session.scalars(select(CalculationWavefunctionDiagnostic.calculation_id)))
    assert len(flagged) == 1
    (row,) = session.scalars(select(DepositWarning).where(DepositWarning.code == CODE)).all()
    assert row.record_type is SubmissionRecordType.calculation
    assert row.record_id in flagged


def test_the_difference_in_the_sentence_visibly_exceeds_the_threshold(client) -> None:
    """0.85001 against 0.75 is 0.10001: the sentence must not read 'a difference of 0.1, which exceeds 0.1'."""
    (warning,) = _spin_post(client, 2, {"s_squared": 0.85001})[SPIN_CODE]
    assert "<S^2> of this calculation is 0.85001," in warning["message"]
    assert "a difference of 0.10001, which exceeds 0.1" in warning["message"]


def test_spin_is_judged_against_a_transition_state_entrys_multiplicity(client) -> None:
    from tests.api.test_api_uploads import _transition_state_payload

    for s_squared, fires in ((0.76, False), (0.86, True)):
        payload = _transition_state_payload(n_imag=1, imag_freq_cm1=-1500.0, label=f"ts-spin-{s_squared}")
        assert payload["multiplicity"] == 2
        payload["additional_calculations"].append(_sp("UB3LYP", spin={"s_squared": s_squared}))
        response = client.post("/api/v1/uploads/transition-states", json=payload)
        assert response.status_code in (200, 201), response.text[:500]
        found = [w for w in response.json()["warnings"] if w["code"] == SPIN_CODE]
        assert len(found) == int(fires)
        if fires:
            assert found[0]["context"]["ideal_s_squared"] == pytest.approx(0.75)
            assert found[0]["context"]["multiplicity"] == 2


def test_the_spin_warnings_are_read_back_with_register_entry_52(client) -> None:
    entry, xyz = _ENTRIES[2]
    calc = _sp("UB3LYP", spin={"s_squared": 1.1, "s_squared_expected": 2.0})
    uploaded = client.post(_CONFORMERS, json=_conformer(calc, entry=entry, xyz=xyz))
    assert uploaded.status_code == 201, uploaded.text[:500]
    ref = uploaded.json()["primary_calculation"]["calculation_ref"]
    read = client.get(f"/api/v1/scientific/calculations/{ref}")
    assert read.status_code == 200, read.text[:500]
    (fragment,) = [w for w in read.json()["record_warnings"] if w["record_ref"] == ref]
    assert fragment["status"] == "recorded"
    items = {item["code"]: item for item in fragment["items"]}
    assert set(items) == {SPIN_CODE, EXPECTED_CODE}
    assert all(item["register_entry"] == 52 for item in items.values())
    for code, item in items.items():
        assert item["message"] == render_message(code, item["context"])


def test_the_t1_warning_is_read_back_with_register_entry_52(client) -> None:
    uploaded = client.post(_CONFORMERS, json=_conformer(_sp(wavefunction={"t1_diagnostic": 0.06})))
    ref = uploaded.json()["primary_calculation"]["calculation_ref"]
    read = client.get(f"/api/v1/scientific/calculations/{ref}")
    (fragment,) = [w for w in read.json()["record_warnings"] if w["record_ref"] == ref]
    (item,) = [i for i in fragment["items"] if i["code"] == CODE]
    assert item["register_entry"] == 52
