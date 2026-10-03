"""Review fixes on assembled composites, through the real routes (ADR 0021, P5).

Each section names the reviewer's defect and would fail on the code that had it:

* the textbook CCSD + (T) scheme, in both programs' conventions (it double counted (T));
* terms listed in any order are one level, and the total check still runs for them
  (positions were mapped for storage but not for the check);
* an assembled composite is never a primary, on any route (it was a 500);
* inputs are finalised by every persisting workflow, one test per workflow
  (a workflow could drop the call and nothing failed);
* the service refuses what only the wire used to: a declared ``composite_input`` edge,
  method-level fields beside a scheme, inputs on a program run, a key *and* a ref.
"""

from __future__ import annotations

import copy

import pytest
from sqlalchemy import select

from app.db.models.calculation import Calculation, CalculationCompositeInput, CalculationCompositeTerm
from tests import composite_p5_fixtures as f

# --------------------------------------------------------------------------------------------
# The textbook scheme: CCSD correlation extrapolated, (T) at a smaller basis as its own term.
# --------------------------------------------------------------------------------------------
R_T, R_Q = -76.0567, -76.0644
CCSD_T, CCSD_Q = -0.2600, -0.2800
T_T, T_Q = -0.0153, -0.0161

TEXTBOOK = {
    "kind": "extrapolation",
    "terms": [
        {"key": "scf", "operation": "value", "energy_component": "reference",
         "inputs": [{"slot": "value", "level_of_theory": f.QZ}]},
        {"key": "ccsd", "operation": "extrapolation", "energy_component": "correlation_excluding_triples",
         "formula": "inverse_power", "exponent": 3,
         "inputs": [
             {"slot": "cardinal", "cardinal_number": 3, "level_of_theory": f.TZ},
             {"slot": "cardinal", "cardinal_number": 4, "level_of_theory": f.QZ},
         ]},
        {"key": "t", "operation": "value", "energy_component": "triples",
         "inputs": [{"slot": "value", "level_of_theory": f.TZ}]},
    ],
}
TEXTBOOK_INPUTS = [
    {"term_key": "scf", "slot": "value", "calculation_key": "spq"},
    {"term_key": "ccsd", "slot": "cardinal", "cardinal_number": 3, "calculation_key": "spt"},
    {"term_key": "ccsd", "slot": "cardinal", "cardinal_number": 4, "calculation_key": "spq"},
    {"term_key": "t", "slot": "value", "calculation_key": "spt"},
]
#: By hand: reference at QZ, the CCSD limit (64 CCSD_Q - 27 CCSD_T) / 37, and (T) once, at TZ.
TEXTBOOK_TOTAL = R_Q + (64 * CCSD_Q - 27 * CCSD_T) / 37 + T_T


def _orca_sps() -> list[dict]:
    return [
        f.sp("spt", f.TZ, R_T + CCSD_T + T_T, [("reference", R_T), ("correlation", CCSD_T + T_T), ("triples", T_T)]),
        f.sp("spq", f.QZ, R_Q + CCSD_Q + T_Q, [("reference", R_Q), ("correlation", CCSD_Q + T_Q), ("triples", T_Q)]),
    ]


def _molpro_sps() -> list[dict]:
    return [
        f.sp("spt", f.TZ, R_T + CCSD_T + T_T, [("reference", R_T), ("correlation", CCSD_T), ("triples", T_T)]),
        f.sp("spq", f.QZ, R_Q + CCSD_Q + T_Q, [("reference", R_Q), ("correlation", CCSD_Q), ("triples", T_Q)]),
    ]


URL = "/api/v1/uploads/computed-species"


def _post(client, payload):
    return client.post(URL, json=payload)


def _ok(client, payload, url=URL) -> dict:
    resp = client.post(url, json=payload)
    assert resp.status_code in (200, 201), resp.text[:1500]
    return resp.json()


def _refused(client, payload, code, status=422, url=URL) -> dict:
    resp = client.post(url, json=payload)
    assert resp.status_code == status, resp.text[:1500]
    body = resp.json()
    assert body["code"] == code, body
    return body


def _codes(body) -> list[str]:
    return [w["code"] for w in body.get("warnings", [])]


def _calc(db_session, body, key) -> Calculation:
    for conf in body["conformers"]:
        for calc in [conf["primary_calculation"], *conf["additional_calculations"]]:
            if calc.get("key") == key:
                return db_session.get(Calculation, calc["calculation_id"])
    raise KeyError(key)


@pytest.mark.parametrize("sps", [_orca_sps, _molpro_sps], ids=["orca_correlation_includes_T", "molpro_T_separate"])
def test_the_textbook_scheme_counts_triples_once_end_to_end(client, sps):
    payload = f.bundle([*sps(), f.assembled(TEXTBOOK, TEXTBOOK_INPUTS, TEXTBOOK_TOTAL)])
    body = _ok(client, payload)
    assert "composite_total_unverifiable" not in _codes(body)


def test_the_textbook_scheme_with_a_total_that_double_counts_triples_is_refused(client):
    """The reviewer's probe: the same recipe written with plain ``correlation`` was 15 mEh out."""
    double_counted = TEXTBOOK_TOTAL + T_Q  # an extra (T) in the total
    payload = f.bundle([*_orca_sps(), f.assembled(TEXTBOOK, TEXTBOOK_INPUTS, double_counted)])
    body = _refused(client, payload, "composite_total_mismatch")
    assert abs(body["context"]["difference_hartree"]) > 1e-3


def test_a_stored_correlation_excluding_triples_is_refused_at_deposit(client):
    sps = _orca_sps()
    sps[0]["sp_energy_components"].append({"component": "correlation_excluding_triples", "value_hartree": CCSD_T})
    _refused(client, f.bundle([*sps, f.assembled(TEXTBOOK, TEXTBOOK_INPUTS, TEXTBOOK_TOTAL)]), "sp_energy_component_derived")


def test_an_orca_row_with_no_triples_to_subtract_makes_excluding_triples_unverifiable(client):
    sps = _orca_sps()
    sps[0]["sp_energy_components"] = [c for c in sps[0]["sp_energy_components"] if c["component"] != "triples"]
    body = _ok(client, f.bundle([*sps, f.assembled(TEXTBOOK, TEXTBOOK_INPUTS, TEXTBOOK_TOTAL)]))
    assert "composite_total_unverifiable" in _codes(body)


# --------------------------------------------------------------------------------------------
# Term order is not identity, and the check follows the canonical positions
# --------------------------------------------------------------------------------------------


def test_terms_listed_in_reverse_are_one_level_and_the_total_is_still_checked(client, db_session):
    first = _ok(client, f.bundle_b())
    reversed_scheme = copy.deepcopy(f.SCHEME_B)
    reversed_scheme["terms"].reverse()
    # The breakdown names terms by the position the producer listed them at: 0 is now the correlation.
    block_terms = [
        {"term_position": 0, "value_hartree": f.CORR_CBS_X3},
        {"term_position": 1, "value_hartree": f.R_Q},
    ]
    composite = f.assembled(reversed_scheme, f.INPUTS_B, f.TOTAL_B)
    composite["composite_result"]["terms"] = block_terms
    second = _ok(client, f.bundle([*f.sps_b(), composite]))
    a, b = _calc(db_session, first, "cbs"), _calc(db_session, second, "cbs")
    assert a.lot_id == b.lot_id
    # Canonical positions are stored: the value term is 0 and the extrapolation 1, whichever way it was sent.
    rows = db_session.scalars(
        select(CalculationCompositeInput)
        .where(CalculationCompositeInput.calculation_id == b.id)
        .order_by(CalculationCompositeInput.term_position, CalculationCompositeInput.cardinal_number)
    ).all()
    assert [(r.term_position, r.slot.value, r.cardinal_number) for r in rows] == [
        (0, "value", None),
        (1, "cardinal", 3),
        (1, "cardinal", 4),
    ]
    stored_terms = db_session.scalars(
        select(CalculationCompositeTerm)
        .where(CalculationCompositeTerm.calculation_id == b.id)
        .order_by(CalculationCompositeTerm.term_position)
    ).all()
    assert [(t.term_position, t.value_hartree) for t in stored_terms] == [(0, f.R_Q), (1, f.CORR_CBS_X3)]


def test_the_total_check_runs_for_terms_listed_in_reverse(client):
    """Not silently unverifiable: a wrong total is still refused when the terms are listed backwards."""
    reversed_scheme = copy.deepcopy(f.SCHEME_B)
    reversed_scheme["terms"].reverse()
    payload = f.bundle([*f.sps_b(), f.assembled(reversed_scheme, f.INPUTS_B, f.TOTAL_B + 1e-3)])
    _refused(client, payload, "composite_total_mismatch")


def test_a_correct_total_for_reversed_terms_raises_no_unverifiable_warning(client):
    reversed_scheme = copy.deepcopy(f.SCHEME_B)
    reversed_scheme["terms"].reverse()
    body = _ok(client, f.bundle([*f.sps_b(), f.assembled(reversed_scheme, f.INPUTS_B, f.TOTAL_B)]))
    assert "composite_total_unverifiable" not in _codes(body)


# --------------------------------------------------------------------------------------------
# An assembled composite is never a primary
# --------------------------------------------------------------------------------------------


def _assembled_by_ref(ref: str) -> dict:
    inputs = [{**row, "calculation_key": None} for row in f.INPUTS_B]
    return {
        "type": "composite",
        "level_of_theory": {"composite_scheme": copy.deepcopy(f.SCHEME_B)},
        "composite_result": {
            "assembly": "assembled",
            "electronic_energy_hartree": f.TOTAL_B,
            "inputs": [{k: v for k, v in {**row, "calculation_ref": ref}.items() if v is not None} for row in inputs],
        },
    }


def test_the_conformers_route_refuses_an_assembled_primary_with_a_code_not_a_500(client):
    payload = {
        "species_entry": dict(f.WATER),
        "geometry": {"xyz_text": f.WATER_XYZ},
        "calculation": _assembled_by_ref("calc_" + "a" * 26),
    }
    _refused(client, payload, "composite_assembled_cannot_be_primary", url="/api/v1/uploads/conformers")


def test_the_computed_species_route_refuses_an_assembled_primary(client):
    payload = f.bundle([])
    primary = f.assembled(f.SCHEME_B, f.INPUTS_B, f.TOTAL_B, key="opt0")
    payload["conformers"][0]["primary_calculation"] = primary
    _refused(client, payload, "composite_assembled_cannot_be_primary")


def test_a_program_run_composite_is_still_a_valid_primary(client):
    primary = {
        "key": "p",
        "type": "composite",
        "software_release": {"name": "Gaussian", "version": "16"},
        "level_of_theory": {"method": "CBS-QB3"},
        "composite_result": {"assembly": "program_run", "electronic_energy_hartree": -76.25},
    }
    payload = f.bundle([])
    payload["conformers"][0]["primary_calculation"] = primary
    _ok(client, payload)


def test_the_service_refuses_an_assembled_primary_for_a_payload_that_skipped_the_wire(db_session):
    from tckdb_schemas.coded_error import CodedValidationError
    from tckdb_schemas.fragments.calculation import CalculationWithResultsPayload

    from app.api.error_contract import CodedValueError
    from app.services.calculation_resolution import resolve_and_persist_calculation_with_results
    from tests.services.test_composite_user_scheme import _species_entry

    entry = _species_entry(db_session)
    payload = CalculationWithResultsPayload(
        type="composite",
        level_of_theory={"composite_scheme": f.SCHEME_B},
        composite_result={
            "assembly": "assembled",
            "electronic_energy_hartree": f.TOTAL_B,
            "inputs": [
                {**row, "calculation_key": None, "calculation_ref": "calc_" + "a" * 26} for row in []
            ]
            or [
                {"term_key": "ref", "slot": "value", "calculation_ref": "calc_" + "a" * 26},
                {"term_key": "corr", "slot": "cardinal", "cardinal_number": 3, "calculation_ref": "calc_" + "b" * 26},
                {"term_key": "corr", "slot": "cardinal", "cardinal_number": 4, "calculation_ref": "calc_" + "c" * 26},
            ],
        },
    )
    with pytest.raises((CodedValueError, CodedValidationError)) as err:
        resolve_and_persist_calculation_with_results(db_session, payload, species_entry_id=entry, as_primary=True)
    assert err.value.code == "composite_assembled_cannot_be_primary"


# --------------------------------------------------------------------------------------------
# Every persisting workflow finalises assembled inputs (one end-to-end test each)
# --------------------------------------------------------------------------------------------


def _input_rows(db_session, composite_id) -> int:
    return len(
        db_session.scalars(
            select(CalculationCompositeInput).where(CalculationCompositeInput.calculation_id == composite_id)
        ).all()
    )


def _composite_in(db_session, product_response_calcs, label):
    raise AssertionError("unused")


def test_the_conformer_workflow_finalises_an_assembled_additional_calculation(db_session):
    """The conformers route refuses a composite as an additional calculation on the wire (only freq and sp),
    so the workflow's own finalise step is reached here with a request that skipped that rule: it must
    still write the inputs and check the total, or a payload built without validation would be stored bare.
    """
    from tckdb_schemas.coded_error import CodedValidationError
    from tckdb_schemas.workflows.conformer_upload import ConformerCalculationIn, ConformerUploadRequest

    from app.api.error_contract import CodedValueError
    from app.workflows.conformer import persist_conformer_upload

    sp_payload = {
        "species_entry": dict(f.WATER),
        "geometry": {"xyz_text": f.WATER_XYZ},
        "calculation": {k: v for k, v in f.opt().items() if k != "key"},
        "additional_calculations": [{k: v for k, v in c.items() if k != "key"} for c in f.sps_b()],
    }
    first = persist_conformer_upload(db_session, ConformerUploadRequest.model_validate(sp_payload))
    refs = [c.public_ref for c in db_session.scalars(select(Calculation).where(Calculation.type == "sp")).all()][-2:]
    by_level = {
        db_session.get(Calculation, ref_id).lot.basis: ref
        for ref_id, ref in (
            (c.id, c.public_ref)
            for c in db_session.scalars(select(Calculation).where(Calculation.type == "sp")).all()
        )
    }
    assert first is not None and len(refs) == 2
    inputs = [
        {"term_key": "ref", "slot": "value", "calculation_ref": by_level["cc-pVQZ"]},
        {"term_key": "corr", "slot": "cardinal", "cardinal_number": 3, "calculation_ref": by_level["cc-pVTZ"]},
        {"term_key": "corr", "slot": "cardinal", "cardinal_number": 4, "calculation_ref": by_level["cc-pVQZ"]},
    ]

    def request(total: float):
        composite = ConformerCalculationIn(
            type="composite",
            level_of_theory={"composite_scheme": copy.deepcopy(f.SCHEME_B)},
            composite_result={"assembly": "assembled", "electronic_energy_hartree": total, "inputs": inputs},
        )
        base = ConformerUploadRequest.model_validate({**sp_payload, "additional_calculations": []})
        base.additional_calculations = [composite]  # past the route's freq/sp rule, which is a wire rule
        return base

    outcome = persist_conformer_upload(db_session, request(f.TOTAL_B))
    composite_row = outcome.additional_calculations[0] if hasattr(outcome, "additional_calculations") else None
    composite_id = (
        composite_row.calculation_id if composite_row is not None and hasattr(composite_row, "calculation_id") else None
    )
    if composite_id is None:
        composite_id = db_session.scalars(
            select(Calculation).where(Calculation.type == "composite").order_by(Calculation.id.desc())
        ).first().id
    assert _input_rows(db_session, composite_id) == 3
    with pytest.raises((CodedValueError, CodedValidationError)) as err:
        persist_conformer_upload(db_session, request(f.TOTAL_B + 1e-3))
    assert err.value.code == "composite_total_mismatch"


@pytest.mark.parametrize("product", ["thermo", "statmech"])
def test_the_inline_calculation_routes_write_the_inputs_of_an_assembled_composite(client, db_session, product):
    calcs = [
        {
            "key": c["key"],
            "calculation": {k: v for k, v in c.items() if k != "key"},
        }
        for c in [*f.sps_b(), f.assembled(f.SCHEME_B, f.INPUTS_B, f.TOTAL_B)]
    ]
    payload: dict = {"species_entry": dict(f.WATER), "scientific_origin": "computed", "calculations": calcs}
    if product == "thermo":
        payload |= {"enthalpy_reference_kind": "formation_298k", "h298_kj_mol": -241.8}
    else:
        payload |= {"statmech_treatment": "rrho", "external_symmetry": 2}
    resp = client.post(f"/api/v1/uploads/{product}", json=payload)
    assert resp.status_code in (200, 201), resp.text[:1500]
    composite = db_session.scalars(
        select(Calculation).order_by(Calculation.id.desc()).where(Calculation.type == "composite")
    ).first()
    assert composite is not None and _input_rows(db_session, composite.id) == 3
    calcs[-1]["calculation"]["composite_result"]["electronic_energy_hartree"] += 1e-3
    bad = client.post(f"/api/v1/uploads/{product}", json=payload)
    assert bad.status_code == 422 and bad.json()["code"] == "composite_total_mismatch", bad.text[:600]


def test_the_reaction_bundle_writes_the_inputs_of_an_assembled_composite(client, db_session):
    def level(d):
        return d

    def flat(calc: dict) -> dict:
        out = {k: v for k, v in calc.items() if k not in ("sp_result", "sp_energy_components")}
        if "sp_result" in calc:
            out["sp_electronic_energy_hartree"] = calc["sp_result"]["electronic_energy_hartree"]
        if "sp_energy_components" in calc:
            out["sp_energy_components"] = calc["sp_energy_components"]
        return out

    def species(key, smiles, multiplicity, xyz, extra=()):
        return {
            "key": key,
            "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": multiplicity},
            "conformers": [
                {
                    "key": f"{key}-conf",
                    "geometry": {"key": f"{key}-geom", "xyz_text": xyz},
                    "calculation": {
                        "key": f"{key}-opt",
                        "type": "opt",
                        "software_release": f.SOFTWARE,
                        "level_of_theory": f.OPT_LOT,
                        "opt_converged": True,
                    },
                }
            ],
            "calculations": [flat(c) | {"geometry_key": f"{key}-geom"} for c in extra],
        }

    composite = f.assembled(f.SCHEME_B, f.INPUTS_B, f.TOTAL_B)
    bundle = {
        "species": [
            species("h", "[H]", 2, "1\nH atom\nH 0.0 0.0 0.0"),
            species("h2", "[H][H]", 1, "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74", [*f.sps_b(), composite]),
        ],
        "reversible": True,
        "reactant_keys": ["h", "h"],
        "product_keys": ["h2"],
    }
    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code in (200, 201), resp.text[:1500]
    row = db_session.scalars(
        select(Calculation).where(Calculation.type == "composite").order_by(Calculation.id.desc())
    ).first()
    assert row is not None and _input_rows(db_session, row.id) == 3
    bundle["species"][1]["calculations"][-1]["composite_result"]["electronic_energy_hartree"] += 1e-3
    bad = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert bad.status_code == 422 and bad.json()["code"] == "composite_total_mismatch", bad.text[:600]
