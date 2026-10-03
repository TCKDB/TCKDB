"""Assembled composites, end to end through the upload API (ADR 0021, P5).

``main`` (7404d9b5) refused ``assembly: "assembled"`` with
``composite_assembled_not_accepted`` and had no ``composite_scheme`` on
``LevelOfTheoryRef`` (it was an extra input). Held here, over HTTP so code and
context reach the client:

* worked payload (b), CCSD(T)/CBS from a TZ/QZ pair, and (c), a focal-point
  additive scheme with a core-valence difference, are stored, read back by ref,
  and the deposited total is what is stored (never the recomputed one);
* each block check at its tolerance boundary, each warning, each refusal by code.
"""

from __future__ import annotations

import copy

import pytest
from sqlalchemy import select

from app.db.models.calculation import (
    Calculation,
    CalculationCompositeInput,
    CalculationCompositeResult,
    CalculationDependency,
)
from app.db.models.common import CalculationDependencyRole, CompositeBindingSource, CompositeSchemeKind
from app.db.models.composite_scheme import CompositeScheme, LevelOfTheoryComposite
from app.db.models.level_of_theory import LevelOfTheory
from tests import composite_p5_fixtures as f

URL = "/api/v1/uploads/computed-species"


def _post(client, payload):
    return client.post(URL, json=payload)


def _ok(client, payload) -> dict:
    resp = _post(client, payload)
    assert resp.status_code in (200, 201), resp.text[:1500]
    return resp.json()


def _refused(client, payload, code: str, status: int = 422) -> dict:
    resp = _post(client, payload)
    assert resp.status_code == status, resp.text[:1500]
    body = resp.json()
    assert body["code"] == code, body
    return body


def _warning_codes(body: dict) -> list[str]:
    return [w["code"] for w in body.get("warnings", [])]


def _calc_by_label(db_session, body: dict, key: str) -> Calculation:
    """The persisted calculation of bundle key ``key`` (via the response's own refs)."""
    for conf in body["conformers"]:
        for calc in [conf["primary_calculation"], *conf["additional_calculations"]]:
            if calc.get("key") == key:
                return db_session.get(Calculation, calc["calculation_id"])
    raise KeyError(key)


def _replace(payload: dict, key: str, **changes) -> dict:
    out = copy.deepcopy(payload)
    for conf in out["conformers"]:
        for calc in [conf["primary_calculation"], *conf["additional_calculations"]]:
            if calc["key"] == key:
                calc.update(changes)
    return out


# ---------------------------------------------------------------------------
# Worked payload (b)
# ---------------------------------------------------------------------------


def test_worked_payload_b_is_stored_with_its_inputs_edges_and_the_deposited_total(client, db_session):
    body = _ok(client, f.bundle_b())
    assert "composite_total_unverifiable" not in _warning_codes(body)
    composite = _calc_by_label(db_session, body, "cbs")
    spt = _calc_by_label(db_session, body, "spt")
    spq = _calc_by_label(db_session, body, "spq")

    result = db_session.get(CalculationCompositeResult, composite.id)
    assert result.assembly.value == "assembled"
    # The deposited number, bit for bit: TCKDB recomputed it to check and stored nothing of its own.
    assert result.electronic_energy_hartree == f.TOTAL_B
    assert result.e0_hartree is None and result.recipe_zpe_hartree is None
    assert composite.software_release_id is None

    rows = db_session.scalars(
        select(CalculationCompositeInput)
        .where(CalculationCompositeInput.calculation_id == composite.id)
        .order_by(
            CalculationCompositeInput.term_position,
            CalculationCompositeInput.slot,
            CalculationCompositeInput.cardinal_number,
        )
    ).all()
    assert [(r.term_position, r.slot.value, r.cardinal_number, r.input_calculation_id) for r in rows] == [
        (0, "value", None, spq.id),
        (1, "cardinal", 3, spt.id),
        (1, "cardinal", 4, spq.id),
    ]

    # Every input row is mirrored by an edge: parent = the input, child = the composite.
    edges = db_session.scalars(
        select(CalculationDependency).where(
            CalculationDependency.child_calculation_id == composite.id,
            CalculationDependency.dependency_role == CalculationDependencyRole.composite_input,
        )
    ).all()
    assert {e.parent_calculation_id for e in edges} == {spt.id, spq.id}

    # The level is the generated label, bound to a declared extrapolation scheme.
    level = db_session.get(LevelOfTheory, composite.lot_id)
    assert level.method == f.LABEL_B
    assert (level.basis, level.aux_basis, level.dispersion, level.keywords, level.core_treatment) == (None,) * 5
    binding = db_session.get(LevelOfTheoryComposite, level.id)
    assert binding.binding_source is CompositeBindingSource.declared
    scheme = db_session.get(CompositeScheme, binding.scheme_id)
    assert scheme.kind is CompositeSchemeKind.extrapolation and scheme.name == f.LABEL_B


def test_worked_payload_b_reads_back_by_refs_only(client, db_session):
    body = _ok(client, f.bundle_b())
    composite = _calc_by_label(db_session, body, "cbs")
    spq = _calc_by_label(db_session, body, "spq")

    read = client.get(f"/api/v1/scientific/calculations/{composite.public_ref}", params={"include": "results"})
    assert read.status_code == 200, read.text[:800]
    block = read.json()["record"]["results"]["composite"]
    assert block["assembly"] == "assembled"
    assert block["electronic_energy_hartree"] == f.TOTAL_B
    assert [(i["term_position"], i["slot"], i["cardinal_number"]) for i in block["inputs"]] == [
        (0, "value", None),
        (1, "cardinal", 3),
        (1, "cardinal", 4),
    ]
    assert block["inputs"][0]["calculation_ref"] == spq.public_ref
    assert all(set(i) == {"term_position", "slot", "cardinal_number", "calculation_ref"} for i in block["inputs"])

    level_ref = read.json()["record"]["level_of_theory"]["composite_scheme"]["composite_scheme_ref"]
    scheme = client.get(f"/api/v1/scientific/composite-schemes/{level_ref}")
    assert scheme.status_code == 200, scheme.text[:800]
    record = scheme.json()["record"]
    assert record["composite_scheme"]["kind"] == "extrapolation"
    assert record["composite_scheme"]["name"] == f.LABEL_B
    terms = record["terms"]
    assert [(t["position"], t["operation"], t["energy_component"], t["formula"], t["exponent"]) for t in terms] == [
        (0, "value", "reference", None, None),
        (1, "extrapolation", "correlation", "inverse_power", 3.0),
    ]
    assert [(i["slot"], i["cardinal_number"], i["level_of_theory"]["basis"]) for i in terms[1]["inputs"]] == [
        ("cardinal", 3, "cc-pVTZ"),
        ("cardinal", 4, "cc-pVQZ"),
    ]
    assert len(record["bound_levels_of_theory"]) == 1
    assert record["bound_levels_of_theory"][0]["binding_source"] == "declared"


def test_the_same_recipe_deposited_twice_shares_one_scheme_and_one_level(client, db_session):
    first = _ok(client, f.bundle_b())
    second = _ok(client, f.bundle_b())
    a = _calc_by_label(db_session, first, "cbs")
    b = _calc_by_label(db_session, second, "cbs")
    assert a.lot_id == b.lot_id
    assert db_session.query(CompositeScheme).filter(CompositeScheme.kind == CompositeSchemeKind.extrapolation).count() == 1


# ---------------------------------------------------------------------------
# Worked payload (c)
# ---------------------------------------------------------------------------


def test_worked_payload_c_focal_point_with_a_core_valence_difference(client, db_session):
    body = _ok(client, f.bundle_c())
    assert "composite_total_unverifiable" not in _warning_codes(body)
    composite = _calc_by_label(db_session, body, "fpa")
    result = db_session.get(CalculationCompositeResult, composite.id)
    assert result.electronic_energy_hartree == f.TOTAL_C

    level = db_session.get(LevelOfTheory, composite.lot_id)
    assert level.method.startswith("Additive[")
    assert "dE:CCSD(T)/cc-pCVTZ ae - CCSD(T)/cc-pCVTZ fc" in level.method
    rows = db_session.scalars(
        select(CalculationCompositeInput).where(CalculationCompositeInput.calculation_id == composite.id)
    ).all()
    assert len(rows) == 8
    # The core-valence pair is two distinct levels of theory, told apart by core_treatment alone.
    ae = _calc_by_label(db_session, body, "sp_ae")
    fc = _calc_by_label(db_session, body, "sp_fc")
    lot_ae, lot_fc = db_session.get(LevelOfTheory, ae.lot_id), db_session.get(LevelOfTheory, fc.lot_id)
    assert lot_ae.id != lot_fc.id
    assert (lot_ae.core_treatment.value, lot_fc.core_treatment.value) == ("all_electron", "frozen_core")
    assert (lot_ae.method, lot_ae.basis) == (lot_fc.method, lot_fc.basis)
    scheme = db_session.get(CompositeScheme, db_session.get(LevelOfTheoryComposite, level.id).scheme_id)
    assert scheme.kind is CompositeSchemeKind.additive
    assert len(scheme.terms) == 5


def test_a_stored_core_treatment_is_visible_on_the_scheme_read(client, db_session):
    body = _ok(client, f.bundle_c())
    composite = _calc_by_label(db_session, body, "fpa")
    calc = client.get(f"/api/v1/scientific/calculations/{composite.public_ref}").json()["record"]
    scheme_ref = calc["level_of_theory"]["composite_scheme"]["composite_scheme_ref"]
    record = client.get(f"/api/v1/scientific/composite-schemes/{scheme_ref}").json()["record"]
    dcv = next(t for t in record["terms"] if any(i["level_of_theory"]["core_treatment"] for i in t["inputs"]))
    cores = {i["slot"]: i["level_of_theory"]["core_treatment"] for i in dcv["inputs"]}
    assert cores == {"high": "all_electron", "low": "frozen_core"}


# ---------------------------------------------------------------------------
# composite_total_mismatch at its tolerance boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("offset", [0.0, 0.9 * f.TOL_B, -0.9 * f.TOL_B])
def test_b_total_within_tolerance_is_accepted(client, offset):
    _ok(client, f.bundle_b(f.TOTAL_B + offset))


@pytest.mark.parametrize("offset", [1.1 * f.TOL_B, -1.1 * f.TOL_B, 1e-3])
def test_b_total_beyond_tolerance_is_refused_with_both_totals(client, offset):
    body = _refused(client, f.bundle_b(f.TOTAL_B + offset), "composite_total_mismatch")
    ctx = body["context"]
    assert ctx["deposited_hartree"] == pytest.approx(f.TOTAL_B + offset)
    assert ctx["recomputed_hartree"] == pytest.approx(f.TOTAL_B, abs=1e-12)
    assert ctx["tolerance_hartree"] == pytest.approx(f.TOL_B)


@pytest.mark.parametrize("offset", [0.0, 0.9 * f.TOL_C, -0.9 * f.TOL_C])
def test_c_total_within_tolerance_is_accepted(client, offset):
    _ok(client, f.bundle_c(f.TOTAL_C + offset))


@pytest.mark.parametrize("offset", [1.1 * f.TOL_C, -1.1 * f.TOL_C])
def test_c_total_beyond_tolerance_is_refused(client, offset):
    body = _refused(client, f.bundle_c(f.TOTAL_C + offset), "composite_total_mismatch")
    assert body["context"]["tolerance_hartree"] == pytest.approx(f.TOL_C)


# ---------------------------------------------------------------------------
# unverifiable (warn)
# ---------------------------------------------------------------------------


def test_a_missing_input_energy_warns_the_total_is_unverifiable_and_stores_the_deposit(client, db_session):
    payload = f.bundle_b()
    payload = _replace(payload, "spt", sp_result={}, sp_energy_components=[])
    body = _ok(client, payload)
    assert "composite_total_unverifiable" in _warning_codes(body)
    composite = _calc_by_label(db_session, body, "cbs")
    assert db_session.get(CalculationCompositeResult, composite.id).electronic_energy_hartree == f.TOTAL_B


def test_a_missing_component_warns(client):
    payload = _replace(f.bundle_b(), "spt", sp_energy_components=[{"component": "reference", "value_hartree": f.R_T}])
    body = _ok(client, payload)
    warning = next(w for w in body["warnings"] if w["code"] == "composite_total_unverifiable")
    assert "component" in warning["message"]


def test_an_assembled_composite_must_deposit_its_total(client):
    """Owner decision 5: the total is deposited and only checked; there is no unverifiable-by-absence."""
    _refused(client, f.bundle_b(total=None), "composite_total_required")


# ---------------------------------------------------------------------------
# input checks (block)
# ---------------------------------------------------------------------------


def test_an_input_at_the_wrong_level_is_refused_naming_both_levels(client):
    payload = _replace(
        f.bundle_b(),
        "spt",
        level_of_theory={"method": "CCSD(T)", "basis": "cc-pVDZ"},
    )
    body = _refused(client, payload, "composite_input_level_mismatch")
    assert body["context"]["expected_level_of_theory"] == "CCSD(T)/cc-pVTZ"
    assert body["context"]["actual_level_of_theory"] == "CCSD(T)/cc-pVDZ"
    assert body["context"]["term_key"] == "corr"


def test_an_input_that_is_not_an_sp_or_opt_is_refused(client):
    freq = {
        "key": "fq",
        "type": "freq",
        "software_release": f.SOFTWARE,
        "level_of_theory": f.TZ,
        "freq_result": {"n_imag": 0},
    }
    inputs = [{**row, "calculation_key": "fq"} if row.get("cardinal_number") == 3 else row for row in f.INPUTS_B]
    payload = f.bundle([*f.sps_b(), freq, f.assembled(f.SCHEME_B, inputs, f.TOTAL_B)])
    body = _refused(client, payload, "composite_input_type_invalid")
    assert body["context"]["calculation_type"] == "freq"


def test_an_opt_is_an_accepted_input(client):
    """An optimisation's final energy is the single-point value at its own level."""
    opt_lot = {"method": "CCSD(T)", "basis": "cc-pVTZ"}
    opt_as_tz = {
        "key": "opt_tz",
        "type": "opt",
        "software_release": f.SOFTWARE,
        "level_of_theory": opt_lot,
        "opt_result": {"converged": True, "final_energy_hartree": f.R_T + f.C_T},
    }
    # An opt carries no components, so a scheme that reads only totals is the one it can feed.
    scheme = {
        "kind": "extrapolation",
        "terms": [
            {
                "key": "e",
                "operation": "extrapolation",
                "energy_component": "total",
                "formula": "inverse_power",
                "exponent": 3,
                "inputs": [
                    {"slot": "cardinal", "cardinal_number": 3, "level_of_theory": f.TZ},
                    {"slot": "cardinal", "cardinal_number": 4, "level_of_theory": f.QZ},
                ],
            }
        ],
    }
    inputs = [
        {"term_key": "e", "slot": "cardinal", "cardinal_number": 3, "calculation_key": "opt_tz"},
        {"term_key": "e", "slot": "cardinal", "cardinal_number": 4, "calculation_key": "spq"},
    ]
    e_t, e_q = f.R_T + f.C_T, f.R_Q + f.C_Q
    total = (64 * e_q - 27 * e_t) / 37
    payload = f.bundle([opt_as_tz, f.sps_b()[1], f.assembled(scheme, inputs, total)])
    body = _ok(client, payload)
    assert "composite_total_unverifiable" not in _warning_codes(body)


def test_an_input_that_is_another_species_calculation_is_refused(client):
    first = _ok(client, f.bundle(f.sps_b()))
    other_ref = next(
        c["calculation_ref"]
        for conf in first["conformers"]
        for c in [conf["primary_calculation"], *conf["additional_calculations"]]
        if c.get("key") == "spt"
    )
    # A different molecule deposits the composite and names the water single point by ref.
    ethane = {
        "species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1},
        "conformers": [
            {
                "key": "c0",
                "geometry": {"xyz_text": "2\nh2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"},
                "primary_calculation": f.opt(),
                "additional_calculations": [
                    f.sps_b()[1],
                    f.assembled(
                        f.SCHEME_B,
                        [
                            {"term_key": "ref", "slot": "value", "calculation_key": "spq"},
                            {"term_key": "corr", "slot": "cardinal", "cardinal_number": 3, "calculation_ref": other_ref},
                            {"term_key": "corr", "slot": "cardinal", "cardinal_number": 4, "calculation_key": "spq"},
                        ],
                        f.TOTAL_B,
                    ),
                ],
            }
        ],
    }
    body = _refused(client, ethane, "composite_input_owner_mismatch")
    assert body["context"]["target"] == "composite"


def test_inputs_declaring_different_geometries_are_refused(client):
    payload = _replace(
        f.bundle_b(),
        "spt",
        input_geometries=[{"xyz_text": f.OTHER_WATER_XYZ}],
    )
    body = _refused(client, payload, "composite_input_geometry_mismatch")
    assert set(body["context"]["inputs"]) >= {"spt", "spq"}


def test_inputs_at_one_geometry_are_accepted_and_the_composite_declaring_it_too(client):
    payload = _replace(f.bundle_b(), "cbs", input_geometries=[{"xyz_text": f.WATER_XYZ}])
    _ok(client, payload)


def test_a_composite_declaring_another_geometry_than_its_inputs_is_refused(client):
    payload = _replace(f.bundle_b(), "cbs", input_geometries=[{"xyz_text": f.OTHER_WATER_XYZ}])
    _refused(client, payload, "composite_input_geometry_mismatch")


# ---------------------------------------------------------------------------
# naming the inputs
# ---------------------------------------------------------------------------


def test_an_input_can_name_a_calculation_deposited_earlier_by_ref(client, db_session):
    first = _ok(client, f.bundle(f.sps_b()))
    refs = {
        c["key"]: c["calculation_ref"]
        for conf in first["conformers"]
        for c in [conf["primary_calculation"], *conf["additional_calculations"]]
        if c.get("key")
    }
    inputs = [
        {"term_key": "ref", "slot": "value", "calculation_ref": refs["spq"]},
        {"term_key": "corr", "slot": "cardinal", "cardinal_number": 3, "calculation_ref": refs["spt"]},
        {"term_key": "corr", "slot": "cardinal", "cardinal_number": 4, "calculation_ref": refs["spq"]},
    ]
    composite_only = f.bundle([f.assembled(f.SCHEME_B, inputs, f.TOTAL_B)])
    body = _ok(client, composite_only)
    assert "composite_total_unverifiable" not in _warning_codes(body)


def test_an_unknown_ref_is_a_404_with_its_code(client):
    inputs = copy.deepcopy(f.INPUTS_B)
    inputs[0] = {"term_key": "ref", "slot": "value", "calculation_ref": "calc_" + "a" * 26}
    body = _refused(client, f.bundle_b(inputs=inputs), "unknown_calculation_ref", status=404)
    assert body["context"]["field"] == "composite_result.inputs[0].calculation_ref"


def test_an_undeclared_key_is_refused_with_the_calculation_key_code(client):
    inputs = copy.deepcopy(f.INPUTS_B)
    inputs[0]["calculation_key"] = "nope"
    _refused(client, f.bundle_b(inputs=inputs), "calculation_key_undeclared")


@pytest.mark.parametrize(
    ("edit", "code"),
    [
        (lambda rows: rows.pop(), "composite_input_missing"),
        (lambda rows: rows.append(dict(rows[0])), "composite_input_duplicate"),
        (lambda rows: rows[0].update(term_key="zzz"), "composite_input_slot_unknown"),
    ],
    ids=["missing", "duplicate", "unknown_term"],
)
def test_inputs_that_do_not_fill_the_scheme_exactly_once_are_refused(client, edit, code):
    inputs = copy.deepcopy(f.INPUTS_B)
    edit(inputs)
    _refused(client, f.bundle_b(inputs=inputs), code)


# ---------------------------------------------------------------------------
# level of theory: refusals
# ---------------------------------------------------------------------------


def test_a_named_method_with_an_inline_definition_is_refused(client):
    calc = f.assembled(f.SCHEME_B, f.INPUTS_B, f.TOTAL_B)
    calc["level_of_theory"] = {"method": "CBS-QB3", "composite_scheme": f.SCHEME_B}
    _refused(client, f.bundle([*f.sps_b(), calc]), "level_of_theory_method_with_composite_scheme")


def test_a_level_with_neither_method_nor_scheme_is_refused(client):
    calc = f.assembled(f.SCHEME_B, f.INPUTS_B, f.TOTAL_B)
    calc["level_of_theory"] = {"basis": "cc-pVQZ"}
    _refused(client, f.bundle([*f.sps_b(), calc]), "level_of_theory_requires_method_or_composite_scheme")


def test_a_nested_composite_input_level_is_refused(client):
    scheme = copy.deepcopy(f.SCHEME_B)
    scheme["terms"][0]["inputs"][0]["level_of_theory"] = {"composite_scheme": f.SCHEME_B}
    _refused(client, f.bundle([*f.sps_b(), f.assembled(scheme, f.INPUTS_B, f.TOTAL_B)]), "composite_scheme_nested")


def test_a_named_composite_method_as_an_input_level_is_refused_without_creating_it(client, db_session):
    scheme = copy.deepcopy(f.SCHEME_B)
    scheme["terms"][0]["inputs"][0]["level_of_theory"] = {"method": "CBS-QB3"}
    _refused(client, f.bundle([*f.sps_b(), f.assembled(scheme, f.INPUTS_B, f.TOTAL_B)]), "composite_scheme_nested")
    assert db_session.scalar(select(LevelOfTheory).where(LevelOfTheory.method == "CBS-QB3")) is None


def test_a_malformed_formula_is_refused(client):
    scheme = copy.deepcopy(f.SCHEME_B)
    del scheme["terms"][1]["exponent"]
    body = _refused(client, f.bundle([*f.sps_b(), f.assembled(scheme, f.INPUTS_B, f.TOTAL_B)]), "composite_scheme_malformed")
    assert body["context"]["rule"] == "exponent_required"


def test_an_assembled_composite_at_a_plain_method_is_not_accepted(client):
    calc = f.assembled(f.SCHEME_B, f.INPUTS_B, f.TOTAL_B)
    calc["level_of_theory"] = {"method": "CCSD(T)", "basis": "cc-pVQZ"}
    _refused(client, f.bundle([*f.sps_b(), calc]), "composite_assembled_not_accepted")


def test_inputs_on_a_program_run_composite_are_refused(client):
    calc = {
        "key": "cbs",
        "type": "composite",
        "software_release": {"name": "Gaussian", "version": "16"},
        "level_of_theory": {"method": "CBS-QB3"},
        "composite_result": {"assembly": "program_run", "inputs": f.INPUTS_B},
    }
    _refused(client, f.bundle([calc]), "composite_inputs_require_assembled")


def test_depends_on_cannot_declare_a_composite_input_edge(client):
    payload = f.bundle_b()
    payload["conformers"][0]["additional_calculations"][-1]["depends_on"] = [
        {"parent_calculation_key": "spt", "role": "composite_input"}
    ]
    _refused(client, payload, "composite_input_edge_is_derived")


def test_every_other_calculation_still_needs_software(client):
    payload = f.bundle_b()
    del payload["conformers"][0]["additional_calculations"][0]["software_release"]
    _refused(client, payload, "calculation_software_release_required")


def test_a_program_run_without_software_is_still_refused(client):
    calc = {
        "key": "cbs",
        "type": "composite",
        "level_of_theory": {"method": "CBS-QB3"},
        "composite_result": {"assembly": "program_run", "electronic_energy_hartree": -76.0},
    }
    _refused(client, f.bundle([calc]), "calculation_software_release_required")
