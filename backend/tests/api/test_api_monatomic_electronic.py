"""Single atoms on the upload routes: what is an atom (#608), and what an atom owes (#609).

Two defects, both measured on the real ARC run fixtures under
``tests/fixtures/arc_runs``:

* #608 -- ``statmech_has_rotational_structure`` called a statmech block an
  atom when it carried no rotational constants and no torsions. 26 of the 45
  polyatomic blocks in the fixtures carry neither, so a polyatomic with no
  ``freq`` source (rotor_scan_7's ``C[C](S)O``, opt only) drew no
  ``missing_statmech_frequency_source``.
* #609 -- the real ARC O atom (rotor_scan_10, scan_1) deposits S(298 K) =
  152.44 J/mol/K, translation plus R ln 3: spin multiplicity only. With the
  3P fine structure (J = 2, 1, 0 at 0, 158.265, 226.977 cm^-1) it is 161.06.
  Nothing on the route said so, and the bundle statmech blocks refused
  ``electronic_levels`` outright.

Every test posts a real fixture payload, edited only where the test says so,
through the real route, and asserts on ``(field, code)`` of the response.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models.statmech import Statmech, StatmechElectronicLevel

ARC = Path(__file__).resolve().parents[1] / "fixtures" / "arc_runs"

LEVELS_CODE = "missing_atomic_electronic_levels"
SOC_CODE = "missing_atomic_spin_orbit_correction"
DEGENERACY_CODE = "atomic_electronic_degeneracy_contradicts_term"
TERM_CODE = "term_symbol_contradicts_multiplicity"
FREQ_CODE = "missing_statmech_frequency_source"
ATOMIC_CODES = {LEVELS_CODE, SOC_CODE, DEGENERACY_CODE}

#: NIST ASD, neutral O I ground term 3P: J = 2, 1, 0.
O_LEVELS = [
    {"level_index": 1, "energy_cm1": 0.0, "degeneracy": 5},
    {"level_index": 2, "energy_cm1": 158.265, "degeneracy": 3},
    {"level_index": 3, "energy_cm1": 226.977, "degeneracy": 1},
]
#: NIST ASD, neutral Cl I ground term 2P: J = 3/2, 1/2.
CL_LEVELS = [
    {"level_index": 1, "energy_cm1": 0.0, "degeneracy": 4},
    {"level_index": 2, "energy_cm1": 882.3515, "degeneracy": 2},
]
#: Spin-orbit shift of O(3P2) from the J-weighted mean of the term, in hartree:
#: -(3*158.265 + 226.977) / 9 cm^-1.
O_SOC_HARTREE = -77.97 / 219474.63


def _load(scenario: str) -> dict:
    # Five real payloads carry a false zero TS bac_total and are refused on
    # purpose (task #264, see test_api_arc_run_fixtures). Where a trimmed copy
    # exists it is the same run with only that entry removed; the species are
    # untouched, and they are the subject here.
    trimmed = ARC / scenario / "tckdb_payloads_trimmed_264" / "computed_reaction"
    root = trimmed if trimmed.exists() else ARC / scenario / "tckdb_payloads" / "computed_reaction"
    (path,) = sorted(root.glob("*.payload.json"))
    return json.loads(path.read_text())


def _species(payload: dict, smiles: str) -> dict:
    (sp,) = [s for s in payload["species"] if s["species_entry"]["smiles"] == smiles][:1]
    return sp


def _post(client, payload: dict):
    resp = client.post("/api/v1/uploads/computed-reaction", json=payload)
    assert resp.status_code == 201, resp.text[:1500]
    return resp


def _pairs(resp) -> set[tuple[str, str]]:
    return {(w["field"], w["code"]) for w in resp.json()["warnings"]}


def _codes_for(resp, key: str) -> set[str]:
    prefix = f"species['{key}']"
    return {w["code"] for w in resp.json()["warnings"] if w["field"].startswith(prefix)}


def _soc_correction(template: dict) -> dict:
    ac = copy.deepcopy(template)
    ac["application_role"] = "soc_total"
    ac["scheme"] = {"kind": "soc", "name": "soc", "units": "hartree"}
    ac["value"] = O_SOC_HARTREE
    ac["components"] = []
    return ac


# ---------------------------------------------------------------------------
# #609: an O atom with no electronic information warns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", ["rotor_scan_10", "scan_1"])
def test_real_arc_oxygen_atom_warns_about_levels_and_spin_orbit(client, scenario):
    payload = _load(scenario)
    o = _species(payload, "[O]")
    assert "electronic_levels" not in o["statmech"]  # the real ARC shape

    pairs = _pairs(_post(client, payload))
    key = o["key"]
    assert (f"species['{key}'].statmech.electronic_levels", LEVELS_CODE) in pairs
    assert (f"species['{key}'].applied_energy_corrections", SOC_CODE) in pairs


def test_oxygen_atom_with_levels_and_soc_does_not_warn(client):
    payload = _load("rotor_scan_10")
    o = _species(payload, "[O]")
    o["statmech"]["electronic_levels"] = O_LEVELS
    o["applied_energy_corrections"].append(_soc_correction(o["applied_energy_corrections"][0]))

    resp = _post(client, payload)
    assert _codes_for(resp, o["key"]) & ATOMIC_CODES == set(), resp.json()["warnings"]


def test_oxygen_atom_with_levels_still_owes_spin_orbit_energy(client):
    """The two absences are independent: levels do not supply the energy."""
    payload = _load("rotor_scan_10")
    o = _species(payload, "[O]")
    o["statmech"]["electronic_levels"] = O_LEVELS

    codes = _codes_for(_post(client, payload), o["key"])
    assert LEVELS_CODE not in codes
    assert SOC_CODE in codes


@pytest.mark.parametrize("scenario", ["rotor_scan_3", "rotor_scan_5", "rotor_scan_8"])
def test_hydrogen_atom_never_warns(client, scenario):
    """H is 2S: spin multiplicity is its whole electronic partition function."""
    resp = _post(client, _load(scenario))
    assert {w["code"] for w in resp.json()["warnings"]} & ATOMIC_CODES == set()


def test_hydrogen_atom_with_its_levels_does_not_warn(client):
    payload = _load("rotor_scan_3")
    h = _species(payload, "[H]")
    h["statmech"]["electronic_levels"] = [{"level_index": 1, "energy_cm1": 0.0, "degeneracy": 2}]
    resp = _post(client, payload)
    assert {w["code"] for w in resp.json()["warnings"]} & ATOMIC_CODES == set()


def test_ionic_or_excited_atom_is_not_judged_against_the_neutral_ground_term(client):
    payload = _load("rotor_scan_10")
    o = _species(payload, "[O]")
    o["species_entry"]["electronic_state_kind"] = "excited"
    o["species_entry"]["multiplicity"] = 1
    resp = _post(client, payload)
    assert _codes_for(resp, o["key"]) & ATOMIC_CODES == set()


# ---------------------------------------------------------------------------
# #609: term symbol against multiplicity, degeneracy against term
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "smiles,scenario,term",
    [("[H]", "rotor_scan_3", "3P"), ("[O]", "rotor_scan_10", "1D"), ("[O]", "rotor_scan_10", "2P")],
)
def test_term_symbol_contradicting_multiplicity_is_flagged(client, smiles, scenario, term):
    payload = _load(scenario)
    sp = _species(payload, smiles)
    sp["species_entry"]["term_symbol"] = term

    pairs = _pairs(_post(client, payload))
    assert (f"species['{sp['key']}'].species_entry.term_symbol", TERM_CODE) in pairs


def test_molecular_term_symbol_contradicting_multiplicity_is_flagged(client):
    """The multiplicity rule stands alone: methane is no atom and has no NIST term."""
    payload = _load("rotor_scan_7")
    sp = _species(payload, "C")
    assert sp["species_entry"]["multiplicity"] == 1
    sp["species_entry"]["term_symbol"] = "3A1"

    pairs = _pairs(_post(client, payload))
    assert (f"species['{sp['key']}'].species_entry.term_symbol", TERM_CODE) in pairs


def test_correct_molecular_term_symbol_is_not_flagged(client):
    payload = _load("rotor_scan_7")
    sp = _species(payload, "C")
    sp["species_entry"]["term_symbol"] = "1A1"
    resp = _post(client, payload)
    assert TERM_CODE not in {w["code"] for w in resp.json()["warnings"]}


def test_correct_term_symbol_is_not_flagged(client):
    payload = _load("rotor_scan_10")
    o = _species(payload, "[O]")
    o["species_entry"]["term_symbol"] = "3P"
    resp = _post(client, payload)
    assert TERM_CODE not in {w["code"] for w in resp.json()["warnings"]}


def test_same_multiplicity_wrong_term_for_the_element_is_flagged(client):
    """O(3S) has the right multiplicity and is not oxygen's ground term."""
    payload = _load("rotor_scan_10")
    o = _species(payload, "[O]")
    o["species_entry"]["term_symbol"] = "3S"
    pairs = _pairs(_post(client, payload))
    assert (f"species['{o['key']}'].species_entry.term_symbol", TERM_CODE) in pairs


def test_ground_level_degeneracy_contradicting_the_term_is_flagged(client):
    payload = _load("rotor_scan_10")
    o = _species(payload, "[O]")
    bad = copy.deepcopy(O_LEVELS)
    bad[0]["degeneracy"] = 7  # no 2J+1 sum of a 3P term (5, 3, 1) gives 7
    o["statmech"]["electronic_levels"] = bad
    pairs = _pairs(_post(client, payload))
    assert (
        f"species['{o['key']}'].statmech.electronic_levels",
        DEGENERACY_CODE,
    ) in pairs


@pytest.mark.parametrize("g", [9, 5])  # the whole 3P term, and J = 2 alone
def test_legitimate_ground_degeneracies_are_not_flagged(client, g):
    payload = _load("rotor_scan_10")
    o = _species(payload, "[O]")
    o["statmech"]["electronic_levels"] = [{"level_index": 1, "energy_cm1": 0.0, "degeneracy": g}]
    codes = _codes_for(_post(client, payload), o["key"])
    assert DEGENERACY_CODE not in codes


# ---------------------------------------------------------------------------
# #609: electronic_levels through both bundles
# ---------------------------------------------------------------------------


def _levels_in_db(db_session, species_entry_id: int) -> list[tuple[int, float, int]]:
    rows = db_session.execute(
        select(
            StatmechElectronicLevel.level_index,
            StatmechElectronicLevel.energy_cm1,
            StatmechElectronicLevel.degeneracy,
        )
        .join(Statmech, Statmech.id == StatmechElectronicLevel.statmech_id)
        .where(Statmech.species_entry_id == species_entry_id)
        .order_by(StatmechElectronicLevel.level_index)
    ).all()
    return [tuple(r) for r in rows]


def test_electronic_levels_round_trip_through_the_reaction_bundle(client, db_session):
    payload = _load("rotor_scan_10")
    o = _species(payload, "[O]")
    o["statmech"]["electronic_levels"] = O_LEVELS

    resp = _post(client, payload)
    entry_ids = resp.json()["species_entry_ids"]
    stored = [lv for eid in entry_ids for lv in _levels_in_db(db_session, eid)]
    assert stored == [(1, 0.0, 5), (2, 158.265, 3), (3, 226.977, 1)]


def _cl_species_bundle(levels) -> dict:
    """A one-atom species bundle, Cl(2P), in the shape ARC's atoms take."""
    return {
        "species_entry": {"smiles": "[Cl]", "charge": 0, "multiplicity": 2},
        "conformers": [
            {
                "key": "c0",
                "geometry": {"xyz_text": "1\nCl\nCl 0.0 0.0 0.0"},
                "primary_calculation": {
                    "key": "opt0",
                    "type": "opt",
                    "software_release": {"name": "Gaussian", "version": "16"},
                    "level_of_theory": {"method": "wb97xd", "basis": "def2tzvp"},
                    "opt_result": {"converged": True},
                },
            }
        ],
        "statmech": {
            "scientific_origin": "computed",
            "statmech_treatment": "rrho",
            "external_symmetry": 1,
            "electronic_levels": levels,
        },
    }


def test_electronic_levels_round_trip_through_the_species_bundle(client, db_session):
    resp = client.post("/api/v1/uploads/computed-species", json=_cl_species_bundle(CL_LEVELS))
    assert resp.status_code == 201, resp.text[:1500]
    assert _levels_in_db(db_session, resp.json()["species_entry_id"]) == [
        (1, 0.0, 4),
        (2, 882.3515, 2),
    ]
    assert LEVELS_CODE not in {w["code"] for w in resp.json()["warnings"]}


def test_species_bundle_chlorine_without_levels_warns(client):
    resp = client.post("/api/v1/uploads/computed-species", json=_cl_species_bundle([]))
    assert resp.status_code == 201, resp.text[:1500]
    assert ("statmech.electronic_levels", LEVELS_CODE) in _pairs(resp)


def test_duplicate_level_index_is_refused_on_the_reaction_bundle(client):
    payload = _load("rotor_scan_10")
    o = _species(payload, "[O]")
    o["statmech"]["electronic_levels"] = [O_LEVELS[0], {**O_LEVELS[1], "level_index": 1}]
    resp = client.post("/api/v1/uploads/computed-reaction", json=payload)
    assert resp.status_code == 422
    assert "level_index" in resp.text


def test_duplicate_level_index_is_refused_on_the_species_bundle(client):
    dup = [CL_LEVELS[0], {**CL_LEVELS[1], "level_index": 1}]
    resp = client.post("/api/v1/uploads/computed-species", json=_cl_species_bundle(dup))
    assert resp.status_code == 422
    assert "level_index" in resp.text


def test_standalone_statmech_for_an_atom_warns_without_levels_and_not_with(client):
    base = {
        "species_entry": {"smiles": "[Cl]", "charge": 0, "multiplicity": 2},
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 1,
    }
    bare = client.post("/api/v1/uploads/statmech", json=base)
    assert bare.status_code == 201, bare.text[:800]
    assert ("statmech.electronic_levels", LEVELS_CODE) in _pairs(bare)

    full = client.post("/api/v1/uploads/statmech", json={**base, "electronic_levels": CL_LEVELS})
    assert full.status_code == 201, full.text[:800]
    assert LEVELS_CODE not in {w["code"] for w in full.json()["warnings"]}


# ---------------------------------------------------------------------------
# #608: what is an atom
# ---------------------------------------------------------------------------


def test_polyatomic_without_constants_or_freq_now_warns(client):
    """rotor_scan_7's C[C](S)O: opt source only, no rotational constants."""
    payload = _load("rotor_scan_7")
    sp = _species(payload, "C[C](S)O")
    assert not any(k.startswith("rotational_constant") for k in sp["statmech"])
    assert {s["role"] for s in sp["statmech"]["source_calculations"]} == {"opt"}

    pairs = _pairs(_post(client, payload))
    assert (f"species['{sp['key']}'].statmech.source_calculations", FREQ_CODE) in pairs


@pytest.mark.parametrize(
    "scenario,smiles",
    [("rotor_scan_10", "[O]"), ("rotor_scan_3", "[H]"), ("scan_1", "[O]")],
)
def test_atoms_still_do_not_get_the_frequency_source_warning(client, scenario, smiles):
    """The atom's statmech has opt and sp sources and, correctly, no freq."""
    payload = _load(scenario)
    sp = _species(payload, smiles)
    assert "freq" not in {s["role"] for s in sp["statmech"]["source_calculations"]}

    resp = _post(client, payload)
    assert FREQ_CODE not in {
        w["code"] for w in resp.json()["warnings"] if w["field"].startswith(f"species['{sp['key']}']")
    }


def test_declared_atom_geometry_outranks_an_absent_constant_heuristic(client):
    """Evidence is the geometry, not what the depositor left out."""
    payload = _load("rotor_scan_7")
    sp = _species(payload, "C[C](S)O")
    sp["statmech"]["rigid_rotor_kind"] = "atom"  # a wrong claim; the geometry has 9 atoms
    pairs = _pairs(_post(client, payload))
    assert (f"species['{sp['key']}'].statmech.source_calculations", FREQ_CODE) in pairs


# ---------------------------------------------------------------------------
# Review round: J-less terms with correct levels, energy order, parity forms
# ---------------------------------------------------------------------------


def _levels(*pairs):
    return [{"level_index": i + 1, "energy_cm1": e, "degeneracy": g} for i, (e, g) in enumerate(pairs)]


def _standalone(client, smiles, mult, term, levels):
    body = {
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": mult, "term_symbol": term},
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 1,
        "electronic_levels": levels,
    }
    resp = client.post("/api/v1/uploads/statmech", json=body)
    assert resp.status_code == 201, resp.text[:800]
    return {w["code"] for w in resp.json()["warnings"]}


@pytest.mark.parametrize(
    "smiles,mult,term,levels",
    [
        ("[H]", 2, "2S", [(0.0, 2)]),
        ("[N]", 4, "4S", [(0.0, 4)]),
        ("[Cl]", 2, "2P", [(0.0, 4), (882.3515, 2)]),
        ("[O]", 3, "3P", [(0.0, 5), (158.265, 3), (226.977, 1)]),
        ("[F]", 2, "2P", [(0.0, 4), (404.141, 2)]),
        ("[B]", 2, "2P", [(0.0, 2), (15.287, 4)]),
        ("[O]", 3, "3P", [(0.0, 9)]),  # the unsplit term
        ("[O]", 3, "3P2", [(0.0, 5), (158.265, 3), (226.977, 1)]),
        ("[Cl]", 2, "2P3/2", [(0.0, 4), (882.3515, 2)]),
    ],
)
def test_correct_levels_beside_a_term_symbol_do_not_warn(client, smiles, mult, term, levels):
    codes = _standalone(client, smiles, mult, term, _levels(*levels))
    assert codes & {DEGENERACY_CODE, TERM_CODE, LEVELS_CODE} == set(), codes


@pytest.mark.parametrize(
    "smiles,mult,term,levels",
    [
        # Oxygen with carbon's ordering (J = 0 lowest), and with J = 1 lowest.
        ("[O]", 3, "3P", [(0.0, 1), (158.265, 3), (226.977, 5)]),
        ("[O]", 3, "3P", [(0.0, 3), (158.265, 5), (226.977, 1)]),
        # Carbon with oxygen's ordering (J = 2 lowest).
        ("[C]", 3, "3P", [(0.0, 5), (16.4167, 3), (43.4135, 1)]),
        # Chlorine with J = 1/2 lowest.
        ("[Cl]", 2, "2P", [(0.0, 2), (882.3515, 4)]),
        # H with N's degeneracy.
        ("[H]", 2, "2S", [(0.0, 4)]),
    ],
)
def test_wrong_level_ordering_is_flagged(client, smiles, mult, term, levels):
    codes = _standalone(client, smiles, mult, term, _levels(*levels))
    assert DEGENERACY_CODE in codes, codes


def test_carbon_in_its_own_order_is_accepted(client):
    codes = _standalone(client, "[C]", 3, "3P", _levels((0.0, 1), (16.4167, 3), (43.4135, 5)))
    assert codes & {DEGENERACY_CODE, TERM_CODE} == set()


def test_declared_j_is_compared_with_the_nist_ground_j(client):
    """O(3P0) is not oxygen's ground level (J = 2)."""
    assert TERM_CODE in _standalone(client, "[O]", 3, "3P0", _levels((0.0, 1)))
    assert TERM_CODE not in _standalone(client, "[O]", 3, "3P2", _levels((0.0, 5)))


@pytest.mark.parametrize("term", ["3Po", "3P°", "3P*", "^3P_2", "^3P", "3P_{2}", "³P₂"])
def test_parity_caret_and_brace_forms_parse_as_atomic_terms(client, term):
    """Correct forms of oxygen's own term stay silent."""
    assert TERM_CODE not in _standalone(client, "[O]", 3, term, _levels((0.0, 5)))


@pytest.mark.parametrize("term", ["1Po", "^1D", "3S°", "2P_{3/2}"])
def test_parity_and_caret_forms_of_the_wrong_atomic_term_are_flagged(client, term):
    assert TERM_CODE in _standalone(client, "[O]", 3, term, _levels((0.0, 5)))


@pytest.mark.parametrize("term", ["X2Pi", "3A1", "2Π"])
def test_molecular_terms_on_an_atom_stay_silent(client, term):
    """Not atomic notation: silence, not a false contradiction."""
    assert TERM_CODE not in _standalone(client, "[O]", 3, term, _levels((0.0, 5)))


def test_missing_levels_message_gives_the_right_sign(client):
    resp = client.post(
        "/api/v1/uploads/statmech",
        json={
            "species_entry": {"smiles": "[O]", "charge": 0, "multiplicity": 3},
            "scientific_origin": "computed",
            "statmech_treatment": "rrho",
            "external_symmetry": 1,
        },
    )
    (msg,) = [w["message"] for w in resp.json()["warnings"] if w["code"] == LEVELS_CODE]
    assert "free energy too high" in msg and "2.00 kJ/mol" in msg
    assert "will be low" not in msg


def test_spin_orbit_message_carries_the_magnitude(client):
    payload = _load("rotor_scan_10")
    o = _species(payload, "[O]")
    resp = _post(client, payload)
    (msg,) = [
        w["message"]
        for w in resp.json()["warnings"]
        if w["code"] == SOC_CODE and w["field"].startswith(f"species['{o['key']}']")
    ]
    assert "-0.93 kJ/mol" in msg


def test_identity_outranks_a_wrong_atom_claim_without_a_geometry(client):
    """Water declared ``atom``: the SMILES says three atoms, and identity is evidence."""
    lot = {"method": "wb97xd", "basis": "def2tzvp"}
    body = {
        "species_entry": {"smiles": "O", "charge": 0, "multiplicity": 1},
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 2,
        "rigid_rotor_kind": "atom",
        "calculations": [
            {
                "key": "sp1",
                "calculation": {
                    "type": "sp",
                    "software_release": {"name": "Gaussian", "version": "16"},
                    "level_of_theory": lot,
                    "sp_result": {"electronic_energy_hartree": -76.4},
                },
            }
        ],
        "source_calculations": [{"calculation_key": "sp1", "role": "sp"}],
    }
    resp = client.post("/api/v1/uploads/statmech", json=body)
    assert resp.status_code == 201, resp.text[:800]
    assert FREQ_CODE in {w["code"] for w in resp.json()["warnings"]}


def test_network_pdep_species_and_ts_statmech_accept_and_persist_electronic_levels(client, db_session):
    """Network species and TS reach ``_persist_statmech_block``; levels are stored there too."""
    from tests.workflows.test_network_pdep_upload import _full_payload

    payload = _full_payload(include_solve=False)
    ethyl = next(sp for sp in payload["species"] if sp["key"] == "ethyl")
    ethyl["statmech"] = {"statmech_treatment": "rrho", "electronic_levels": _levels((0.0, 2))}
    payload["transition_states"][0]["statmech"] = {
        "statmech_treatment": "rrho",
        "electronic_levels": _levels((0.0, 2), (100.0, 2)),
    }
    resp = client.post("/api/v1/uploads/networks/pdep", json=payload)
    assert resp.status_code == 201, resp.text[:1500]
    per_statmech = db_session.execute(
        select(StatmechElectronicLevel.statmech_id, StatmechElectronicLevel.degeneracy)
    ).all()
    counts = sorted({sid: 0 for sid, _ in per_statmech}.keys())
    assert len(per_statmech) == 3 and len(counts) == 2
