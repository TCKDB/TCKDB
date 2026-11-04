"""A closed-shell coupled-cluster T1 above the conventional rule of thumb earns a warning (T1/D1 assessment, Option D).

``t1_diagnostic_above_closed_shell_threshold`` is raised for a calculation whose entry has multiplicity 1, whose method
is coupled cluster with singles, and whose stored ``t1_diagnostic`` is strictly above 0.02 (Lee and Taylor 1989). It is
a warning on the calculation only: the value, the review status and every selection are untouched, an open-shell T1
and a D1 earn nothing, and the sentence never says the system *is* multireference.
"""

from __future__ import annotations

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


def _conformer(calc: dict, *, entry: dict | None = None, xyz: str = _H2_XYZ) -> dict:
    payload = _hydrogen_conformer_payload()
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
        "UCCSD(T)",
        "RCCSD(T)-F12",
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


@pytest.mark.parametrize("method", ["MP2", "B3LYP", "CCD", "CC2", "CISD", "CASSCF", "CBS-QB3", "W1", "HF", "bd(t)"])
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
    """The spin row is stored and shown; this check does not look at it."""
    calc = _sp("MP2", wavefunction={"t1_diagnostic": 0.5}, spin={"s_squared": 1.1})
    assert _post(client, calc) == []


# ---------------------------------------------------------------------------
# what the warning is, and what it is not
# ---------------------------------------------------------------------------


def test_the_sentence_names_its_source_and_does_not_judge(client) -> None:
    (warning,) = _post(client, _sp(wavefunction={"t1_diagnostic": 0.0612}))
    message = warning["message"]
    assert message == render_message(CODE, warning["context"])
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
    assert str(calc_id) not in warning["message"].replace("0.06", "")
    assert "depositor_supplied" not in (warning["context"] or {})


def test_a_flagged_calculation_changes_no_other_response_field(client) -> None:
    """The same upload with a T1 below the line differs only by the warning."""
    low = client.post(_CONFORMERS, json=_conformer(_sp(wavefunction={"t1_diagnostic": 0.01}))).json()
    high = client.post(_CONFORMERS, json=_conformer(_sp(wavefunction={"t1_diagnostic": 0.06}))).json()
    assert [w["code"] for w in high["warnings"] if w["code"] == CODE] == [CODE]
    assert not [w for w in low["warnings"] if w["code"] == CODE]
    assert high.get("status") == low.get("status")


def test_the_spin_row_is_stored_and_unjudged_today(client) -> None:
    """A contaminated doublet is accepted with its row; the <S^2> check is not part of this warning."""
    doublet = {"smiles": "[H]", "charge": 0, "multiplicity": 2}
    calc = _sp("UCCSD(T)", spin={"s_squared": 1.10, "s_squared_expected": 0.75})
    assert _post(client, calc, entry=doublet, xyz="1\nH atom\nH 0.0 0.0 0.0") == []
    assert client._db_session.scalars(select(CalculationSpinDiagnostic)).all()


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
