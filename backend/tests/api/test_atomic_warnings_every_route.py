"""The atomic electronic-state warnings fire on every route that accepts an atom (#836).

``missing_atomic_electronic_levels``, ``missing_atomic_spin_orbit_correction`` and
``atomic_electronic_degeneracy_contradicts_term`` used to come only from
``/uploads/statmech``, ``/uploads/computed-species`` and ``/uploads/computed-reaction``.
A single atom could be deposited through four other routes and hear nothing:

* ``POST /uploads/thermo`` (and its job, and the thermo records of ``POST /bundles/submit``);
* ``POST /uploads/conformers`` (and its job);
* ``POST /uploads/networks/pdep`` (and its job).

Each case posts a real payload. Every "does not warn" check first requires a positive
result on the same payload shape (status 201 and a named code) so an empty list cannot pass.
"""

from __future__ import annotations

import itertools

import pytest
from sqlalchemy import select

from app.db.models.common import SubmissionRecordType
from app.db.models.deposit_warning import DepositWarning
from app.db.models.statmech import Statmech
from tests.api.test_api_bundle_upload_warnings import SUBMIT, _thermo_bundle
from tests.api.test_api_network_reads import _pdep_payload
from tests.api.test_api_scheme_frequency_level import _correction
from tests.api.test_api_uploads import _hydrogen_conformer_payload

LEVELS = "missing_atomic_electronic_levels"
SOC = "missing_atomic_spin_orbit_correction"
DEGENERACY = "atomic_electronic_degeneracy_contradicts_term"
ATOMIC = {LEVELS, SOC, DEGENERACY}

#: Carbon's J ordering on oxygen: the lowest level's degeneracy (1) is not one O(3P) can have.
WRONG_O_LEVELS = [
    {"level_index": 1, "energy_cm1": 0.0, "degeneracy": 1},
    {"level_index": 2, "energy_cm1": 158.265, "degeneracy": 3},
    {"level_index": 3, "energy_cm1": 226.977, "degeneracy": 5},
]
O_LEVELS = [
    {"level_index": 1, "energy_cm1": 0.0, "degeneracy": 5},
    {"level_index": 2, "energy_cm1": 158.265, "degeneracy": 3},
    {"level_index": 3, "energy_cm1": 226.977, "degeneracy": 1},
]
H_LEVELS = [{"level_index": 1, "energy_cm1": 0.0, "degeneracy": 2}]

ATOMS = {
    "O": ("[O]", 3, "1\nO\nO 0.0 0.0 0.0"),
    "H": ("[H]", 2, "1\nH\nH 0.0 0.0 0.0"),
    "C": ("[C]", 3, "1\nC\nC 0.0 0.0 0.0"),
}


_TEMPERATURES = itertools.count()

PDEP_PRODUCTS = {
    "O": ("[O-][O+]=O", 1, "3\nO3\nO 0.0 0.0 0.0\nO 1.28 0.0 0.0\nO 1.9 1.1 0.0"),
    "H": ("O[O]", 2, "3\nHO2\nO 0.0 0.0 0.0\nO 1.33 0.0 0.0\nH 1.7 0.9 0.0"),
    "C": ("O=C=O", 1, "3\nCO2\nC 0.0 0.0 0.0\nO 1.16 0.0 0.0\nO -1.16 0.0 0.0"),
}


def _aec(*, soc: bool = False) -> dict:
    ac = _correction("aec_total")
    ac.pop("source_calculation_key")
    # Corrections dedupe on (entry, scheme, role, temperature): a test that deposits twice needs distinct ones.
    ac["temperature_k"] = 300.0 + next(_TEMPERATURES)
    if soc:
        ac["application_role"] = "soc_total"
        ac["scheme"] = {"kind": "soc", "name": "soc", "units": "hartree"}
        ac["value"] = -3.5e-4
    return ac


def _entry(atom: str) -> dict:
    smiles, mult, _xyz = ATOMS[atom]
    return {"smiles": smiles, "charge": 0, "multiplicity": mult}


# ---------------------------------------------------------------------------
# Payload builders: (atom, levels | None, corrections) -> the route's request.
# ``levels=None`` means "no statmech block"; ``[]`` means a statmech with no levels.
# ---------------------------------------------------------------------------


def thermo_payload(atom="O", levels=None, corrections=(), origin="computed") -> dict:
    return {
        "enthalpy_reference_kind": "formation_298k",
        "species_entry": _entry(atom),
        "scientific_origin": origin,
        "h298_kj_mol": 249.2,
        "applied_energy_corrections": list(corrections),
    }


def bundle_payload(atom="O", levels=None, corrections=(), origin="computed") -> dict:
    bundle = _thermo_bundle()
    record = bundle["records"]["thermo_uploads"][0]
    record.clear()
    record.update(thermo_payload(atom, levels, corrections, origin))
    return bundle


def conformer_payload(atom="O", levels=None, corrections=(), origin="computed", statmech_origin=None) -> dict:
    payload = _hydrogen_conformer_payload()
    payload["species_entry"] = _entry(atom)
    payload["geometry"] = {"xyz_text": ATOMS[atom][2]}
    payload["scientific_origin"] = origin
    if levels is not None:
        payload["statmech"] = {"statmech_treatment": "rrho", "external_symmetry": 1, "electronic_levels": levels}
        if statmech_origin is not None:
            payload["statmech"]["scientific_origin"] = statmech_origin
    payload["applied_energy_corrections"] = list(corrections)
    return payload


def pdep_payload(atom="O", levels=None, corrections=(), origin="computed") -> dict:
    payload = _pdep_payload()
    first = payload["species"][0]
    first["species_entry"] = _entry(atom)
    first["conformers"][0]["geometry"]["xyz_text"] = ATOMS[atom][2]
    first["conformers"][0]["calculation"]["type"] = "sp"
    if levels is not None:
        first["statmech"] = {"statmech_treatment": "rrho", "scientific_origin": origin, "electronic_levels": levels}
    # The reaction stays element-balanced for any atom: atom + O2 -> the product below.
    smiles, mult, xyz = PDEP_PRODUCTS[atom]
    ox = payload["species"][2]
    ox["species_entry"] = {"smiles": smiles, "charge": 0, "multiplicity": mult}
    ox["conformers"][0]["geometry"]["xyz_text"] = xyz
    return payload


ROUTES = {
    "thermo": (thermo_payload, "/api/v1/uploads/thermo", "/api/v1/jobs/thermo"),
    "conformer": (conformer_payload, "/api/v1/uploads/conformers", "/api/v1/jobs/conformer"),
    "pdep": (pdep_payload, "/api/v1/uploads/networks/pdep", "/api/v1/jobs/network/pdep"),
}
#: Routes that carry a statmech block (and so levels); thermo carries only corrections.
STATMECH_ROUTES = ["conformer", "pdep"]
#: Routes that carry applied energy corrections.
CORRECTION_ROUTES = ["thermo", "conformer"]


def _post(client, url: str, payload: dict):
    resp = client.post(url, json=payload)
    assert resp.status_code == 201, resp.text[:1500]
    return resp


def _codes(resp) -> set[str]:
    return {w["code"] for w in resp.json()["warnings"]}


def _pairs(resp) -> set[tuple[str, str]]:
    return {(w["field"], w["code"]) for w in resp.json()["warnings"]}


# ---------------------------------------------------------------------------
# Warns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", STATMECH_ROUTES)
def test_oxygen_with_computed_statmech_and_no_levels_warns(client, route):
    build, url, _job = ROUTES[route]
    resp = _post(client, url, build("O", levels=[]))
    field = "statmech.electronic_levels" if route == "conformer" else "species['ethyl'].statmech.electronic_levels"
    assert (field, LEVELS) in _pairs(resp), resp.json()["warnings"]


@pytest.mark.parametrize("route", STATMECH_ROUTES)
def test_oxygen_with_carbons_level_ordering_warns(client, route):
    build, url, _job = ROUTES[route]
    resp = _post(client, url, build("O", levels=WRONG_O_LEVELS))
    assert DEGENERACY in _codes(resp), resp.json()["warnings"]
    assert LEVELS not in _codes(resp)


@pytest.mark.parametrize("route", CORRECTION_ROUTES)
def test_oxygen_with_computed_energy_and_no_spin_orbit_correction_warns(client, route):
    build, url, _job = ROUTES[route]
    resp = _post(client, url, build("O", levels=O_LEVELS if route == "conformer" else None, corrections=[_aec()]))
    assert ("applied_energy_corrections", SOC) in _pairs(resp), resp.json()["warnings"]


def test_a_computed_thermo_with_no_corrections_at_all_still_owes_spin_orbit_energy(client):
    build, url, _job = ROUTES["thermo"]
    assert ("applied_energy_corrections", SOC) in _pairs(_post(client, url, build("O")))


def test_bundle_submit_thermo_record_for_an_atom_warns(client):
    resp = client.post(SUBMIT, json=bundle_payload("O", corrections=[_aec()]))
    assert resp.status_code == 201, resp.text[:1500]
    pairs = {
        (m["field"], m["code"])
        for m in resp.json()["messages"]
        if m["level"] == "warning" and m.get("local_ref") == "thermo_uploads[0]"
    }
    assert ("applied_energy_corrections", SOC) in pairs, pairs


# ---------------------------------------------------------------------------
# Does not warn
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", STATMECH_ROUTES)
def test_oxygen_with_its_levels_is_silent_about_levels(client, route):
    build, url, _job = ROUTES[route]
    # The positive control: the same payload without levels warns.
    assert LEVELS in _codes(_post(client, url, build("O", levels=[])))
    assert ATOMIC.isdisjoint(_codes(_post(client, url, build("O", levels=O_LEVELS))))


@pytest.mark.parametrize("route", ["thermo", "conformer"])
def test_a_spin_orbit_correction_silences_the_spin_orbit_warning(client, route):
    build, url, _job = ROUTES[route]
    levels = O_LEVELS if route == "conformer" else None
    assert SOC in _codes(_post(client, url, build("O", levels, corrections=[_aec()])))
    assert SOC not in _codes(_post(client, url, build("O", levels, corrections=[_aec(), _aec(soc=True)])))


@pytest.mark.parametrize("route", sorted(ROUTES))
def test_an_s_term_atom_gets_neither_levels_nor_spin_orbit_warning(client, route):
    build, url, _job = ROUTES[route]
    # Control: oxygen on the same route warns, so the route is wired and hydrogen's silence means something.
    oxygen = build("O", levels=[] if route != "thermo" else None, corrections=[_aec()] if route != "pdep" else [])
    assert ATOMIC & _codes(_post(client, url, oxygen))
    hydrogen = build("H", levels=[] if route != "thermo" else None, corrections=[_aec()] if route != "pdep" else [])
    assert ATOMIC.isdisjoint(_codes(_post(client, url, hydrogen)))


@pytest.mark.parametrize("route", STATMECH_ROUTES)
def test_hydrogen_with_its_two_fold_level_is_silent(client, route):
    build, url, _job = ROUTES[route]
    assert ATOMIC.isdisjoint(_codes(_post(client, url, build("H", levels=H_LEVELS))))


def test_a_molecule_gets_none_of_them(client):
    # Positive control first: the atom on the same route warns.
    assert LEVELS in _codes(_post(client, "/api/v1/uploads/conformers", conformer_payload("O", levels=[])))
    payload = _hydrogen_conformer_payload()
    payload["species_entry"] = {"smiles": "O", "charge": 0, "multiplicity": 1}
    payload["geometry"] = {"xyz_text": "3\nwater\nO 0 0 0.117\nH 0 0.757 -0.469\nH 0 -0.757 -0.469"}
    payload["statmech"] = {"statmech_treatment": "rrho", "external_symmetry": 2}
    payload["applied_energy_corrections"] = [_aec()]
    assert ATOMIC.isdisjoint(_codes(_post(client, "/api/v1/uploads/conformers", payload)))

    thermo = thermo_payload("O", corrections=[_aec()])
    thermo["species_entry"] = {"smiles": "O", "charge": 0, "multiplicity": 1}
    assert ATOMIC.isdisjoint(_codes(_post(client, "/api/v1/uploads/thermo", thermo)))


def test_a_thermo_that_is_not_computed_and_carries_no_corrections_is_not_judged(client):
    build, url, _job = ROUTES["thermo"]
    assert SOC in _codes(_post(client, url, build("O", origin="computed")))
    assert SOC not in _codes(_post(client, url, build("O", origin="experimental")))


def test_a_thermo_citing_a_statmech_it_cannot_see_into_is_not_judged_for_spin_orbit(client):
    """The cited record may carry the spin-orbit energy: the request alone cannot say it was not applied."""
    build, url, _job = ROUTES["thermo"]
    statmech = client.post(
        "/api/v1/uploads/statmech",
        json={
            "species_entry": _entry("O"),
            "scientific_origin": "computed",
            "statmech_treatment": "rrho",
            "external_symmetry": 1,
            "electronic_levels": O_LEVELS,
        },
    )
    assert statmech.status_code == 201, statmech.text[:800]
    # Control: the same thermo without the link is judged.
    assert SOC in _codes(_post(client, url, build("O", corrections=[_aec()])))
    linked = build("O", corrections=[_aec()])
    linked["existing_statmech_id"] = statmech.json()["id"]
    assert SOC not in _codes(_post(client, url, linked))


def test_the_network_route_cannot_see_corrections_and_does_not_judge_spin_orbit(client):
    build, url, _job = ROUTES["pdep"]
    codes = _codes(_post(client, url, build("O", levels=[])))
    assert LEVELS in codes
    assert SOC not in codes


# ---------------------------------------------------------------------------
# Placement: each warning is stored on its exact record, not on the submission alone
# ---------------------------------------------------------------------------


def _rows(db_session, submission_id: int, code: str) -> list[DepositWarning]:
    return list(
        db_session.scalars(
            select(DepositWarning).where(DepositWarning.submission_id == submission_id, DepositWarning.code == code)
        )
    )


def test_thermo_warnings_land_on_the_thermo(client, db_session):
    resp = _post(client, "/api/v1/uploads/thermo", thermo_payload("O", corrections=[_aec()]))
    (row,) = _rows(db_session, resp.json()["submission_id"], SOC)
    assert (row.record_type, row.record_id) == (SubmissionRecordType.thermo, resp.json()["id"])


def test_bundle_thermo_warnings_land_on_the_thermo(client, db_session):
    resp = client.post(SUBMIT, json=bundle_payload("O", corrections=[_aec()]))
    assert resp.status_code == 201, resp.text[:1500]
    body = resp.json()
    (thermo,) = [r for r in body["records"] if r["record_type"] == "thermo"]
    (row,) = _rows(db_session, body["submission_id"], SOC)
    assert (row.record_type, row.record_id) == (SubmissionRecordType.thermo, thermo["record_id"])


def test_conformer_levels_warning_lands_on_the_statmech_and_spin_orbit_on_the_observation(client, db_session):
    before = set(db_session.scalars(select(Statmech.id)))
    resp = _post(client, "/api/v1/uploads/conformers", conformer_payload("O", levels=[], corrections=[_aec()]))
    sid = resp.json()["submission_id"]
    (statmech_id,) = set(db_session.scalars(select(Statmech.id))) - before
    (levels,) = _rows(db_session, sid, LEVELS)
    assert (levels.record_type, levels.record_id) == (SubmissionRecordType.statmech, statmech_id)
    (soc,) = _rows(db_session, sid, SOC)
    assert (soc.record_type, soc.record_id) == (SubmissionRecordType.conformer_observation, resp.json()["id"])


def test_pdep_levels_warning_lands_on_the_species_statmech(client, db_session):
    before = set(db_session.scalars(select(Statmech.id)))
    resp = _post(client, "/api/v1/uploads/networks/pdep", pdep_payload("O", levels=[]))
    (statmech_id,) = set(db_session.scalars(select(Statmech.id))) - before
    (row,) = _rows(db_session, resp.json()["submission_id"], LEVELS)
    assert (row.record_type, row.record_id) == (SubmissionRecordType.statmech, statmech_id)


# ---------------------------------------------------------------------------
# Review round 1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_origin,statmech_origin,expect_levels",
    [("computed", "experimental", False), ("experimental", "computed", True), ("computed", "computed", True)],
)
def test_the_conformer_levels_warning_follows_the_statmechs_own_origin(
    client, request_origin, statmech_origin, expect_levels
):
    """The origin stored on the statmech row is the statmech payload's, not the request's."""
    payload = conformer_payload("O", levels=[], origin=request_origin, statmech_origin=statmech_origin)
    assert (LEVELS in _codes(_post(client, "/api/v1/uploads/conformers", payload))) is expect_levels


def _cited_calculation(client) -> dict:
    """An O calculation already deposited for the same species entry, to cite from a thermo."""
    resp = _post(client, "/api/v1/uploads/conformers", conformer_payload("O"))
    return resp.json()["primary_calculation"]


def _thermo_citing(client, how: str) -> dict:
    calc = _cited_calculation(client)
    payload = thermo_payload("O", corrections=[_aec()])
    if how == "existing_calculation_id":
        payload["source_calculations"] = [{"existing_calculation_id": calc["calculation_id"], "role": "sp"}]
    elif how == "calculation_ref":
        payload["protocol"] = {"version": 1, "supporting_calculations": [{"calculation_ref": calc["calculation_ref"]}]}
    return payload


@pytest.mark.parametrize("how", ["existing_calculation_id", "calculation_ref"])
def test_a_thermo_citing_a_deposited_calculation_is_not_judged_for_spin_orbit(client, how):
    """Either way the thermo names a calculation whose corrections the request cannot see."""
    assert SOC in _codes(_post(client, "/api/v1/uploads/thermo", _thermo_citing(client, "none")))
    assert SOC not in _codes(_post(client, "/api/v1/uploads/thermo", _thermo_citing(client, how)))


def _entropy_only(payload: dict) -> dict:
    payload.pop("h298_kj_mol")
    payload.pop("enthalpy_reference_kind")  # refused beside a record with no enthalpy
    payload["s298_j_mol_k"] = 161.0
    return payload


def test_an_entropy_only_computed_thermo_has_no_energy_to_lack_spin_orbit_in(client):
    url = "/api/v1/uploads/thermo"
    assert SOC in _codes(_post(client, url, thermo_payload("O")))
    assert SOC not in _codes(_post(client, url, _entropy_only(thermo_payload("O"))))
    # Corrections present still count as computed energy.
    assert SOC in _codes(_post(client, url, _entropy_only(thermo_payload("O", corrections=[_aec()]))))


def test_an_entropy_only_bundle_thermo_record_is_not_judged_either(client):
    def pairs(bundle):
        resp = client.post(SUBMIT, json=bundle)
        assert resp.status_code == 201, resp.text[:1500]
        return {m["code"] for m in resp.json()["messages"] if m["level"] == "warning"}

    assert SOC in pairs(bundle_payload("O"))
    entropy_only = bundle_payload("O")
    _entropy_only(entropy_only["records"]["thermo_uploads"][0])
    assert SOC not in pairs(entropy_only)


def test_an_entropy_only_species_bundle_thermo_is_not_judged_either(client):
    from tests.api.test_api_monatomic_electronic import _cl_species_bundle

    def codes(thermo):
        bundle = _cl_species_bundle(
            [{"level_index": 1, "energy_cm1": 0.0, "degeneracy": 4}, {"level_index": 2, "energy_cm1": 882.3515, "degeneracy": 2}]
        )
        bundle["thermo"] = thermo
        return _codes(_post(client, "/api/v1/uploads/computed-species", bundle))

    assert SOC in codes({"scientific_origin": "computed", "h298_kj_mol": 121.3, "enthalpy_reference_kind": "formation_298k"})
    assert SOC not in codes({"scientific_origin": "computed", "s298_j_mol_k": 165.2})


_WILHOIT = {"cp0_j_mol_k": 20.8, "cp_inf_j_mol_k": 30.0, "b_k": 500.0, "a0": 0.0, "a1": 0.0, "a2": 0.0, "a3": 0.0}

#: (payload, carries enthalpy). ``enthalpy_formation_0k_kj_mol`` is the one entry the schema's own
#: 298 K declaration rule does not count (it has its own reference), so it is pinned separately.
ENTHALPY_CASES = [
    ({"h298_kj_mol": 1.0}, True),
    ({"s298_j_mol_k": 1.0}, False),
    ({"nasa": {"t_low": 200.0}}, True),
    ({"nasa9_intervals": [{"t_min_k": 200.0}]}, True),
    ({"nasa9_intervals": []}, False),
    ({"wilhoit": {**_WILHOIT, "h0_kj_mol": -5.0}}, True),
    ({"wilhoit": dict(_WILHOIT)}, False),
    ({"points": [{"temperature_k": 300, "cp_j_mol_k": 20.8}]}, False),
    ({"points": [{"temperature_k": 300, "s_j_mol_k": 160.0}]}, False),
    ({"points": [{"temperature_k": 300, "h_kj_mol": 6.2}]}, True),
    ({"points": [{"temperature_k": 300, "g_kj_mol": -9.0}]}, True),
    ({"points": [{"temperature_k": 300, "cp_j_mol_k": 20.8}, {"temperature_k": 400, "h_kj_mol": 9.0}]}, True),
]


def _namespace(value):
    from types import SimpleNamespace

    if isinstance(value, dict):
        return SimpleNamespace(**{k: _namespace(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_namespace(v) for v in value]
    return value


@pytest.mark.parametrize("thermo,carries", ENTHALPY_CASES)
def test_what_counts_as_a_thermo_carrying_energy(thermo, carries):
    from app.services.atomic_electronic_warnings import thermo_carries_enthalpy

    assert thermo_carries_enthalpy(_namespace(thermo)) is carries


def test_a_0k_formation_enthalpy_is_energy():
    from app.services.atomic_electronic_warnings import thermo_carries_enthalpy

    assert thermo_carries_enthalpy(_namespace({"enthalpy_formation_0k_kj_mol": 246.8})) is True


@pytest.mark.parametrize("thermo,carries", ENTHALPY_CASES)
def test_the_energy_definition_agrees_with_the_schemas_enthalpy_declaration_rule(thermo, carries):
    """``enthalpy_reference_error`` asks for a declaration exactly when the record has enthalpy content.

    The two lists are kept by hand; this pins them together so one cannot grow without the other.
    """
    from tckdb_schemas.enthalpy_reference import W_ENTHALPY_DECLARATION_ABSENT, enthalpy_reference_error

    from app.services.atomic_electronic_warnings import thermo_carries_enthalpy

    refusal = enthalpy_reference_error(thermo)
    schema_says = refusal is not None and refusal[0] == W_ENTHALPY_DECLARATION_ABSENT
    assert schema_says is carries
    assert thermo_carries_enthalpy(_namespace(thermo)) is schema_says
