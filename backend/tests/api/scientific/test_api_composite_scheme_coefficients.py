"""``GET /scientific/composite-schemes/{ref}`` reports the linear coefficients of a scheme (ADR 0021, P7a).

On ``main`` (fea447ec) the scheme read had terms and inputs and nothing about how the energies
combine. Now each term says whether it is a fixed linear combination of its inputs' energies
(``linearity``) and, when it is, each input carries its ``coefficient``: ``+1`` for a value or base
input, ``+1`` / ``-1`` for the high / low input of a difference, and the closed-form weights for a
two-point extrapolation. ``exponential_three_point`` is not linear, so it says so and gives none.

The expected numbers are written out here from the formulas by hand (``f(n)`` of each form), not
taken from the module under test. Each scheme is also *uploaded with the total those hand coefficients
give*: the server accepts it only if its own arithmetic agrees, which checks the read's numbers
against an independent computation.
"""

from __future__ import annotations

import math

import pytest

from app.services.scientific_read.composite_schemes import scheme_linearity, term_coefficients, term_linearity
from tests import composite_p5_fixtures as f
from tests.api.test_api_composite_assembled import _calc_by_label, _ok

DZ = {"method": "CCSD(T)", "basis": "cc-pVDZ"}
E = {2: -76.30, 3: -76.36, 4: -76.38, 5: -76.385}


def _levels(*cardinals: int) -> dict[int, dict]:
    names = {2: "cc-pVDZ", 3: "cc-pVTZ", 4: "cc-pVQZ", 5: "cc-pV5Z"}
    return {n: {"method": "CCSD(T)", "basis": names[n]} for n in cardinals}


def _extrapolation_bundle(formula: str, exponent: float | None, cardinals: tuple[int, ...], total: float) -> dict:
    levels = _levels(*cardinals)
    term = {
        "key": "x",
        "operation": "extrapolation",
        "energy_component": "total",
        "formula": formula,
        "inputs": [{"slot": "cardinal", "cardinal_number": n, "level_of_theory": levels[n]} for n in cardinals],
    }
    if exponent is not None:
        term["exponent"] = exponent
    scheme = {"kind": "extrapolation", "terms": [term]}
    inputs = [
        {"term_key": "x", "slot": "cardinal", "cardinal_number": n, "calculation_key": f"sp{n}"} for n in cardinals
    ]
    sps = [f.sp(f"sp{n}", levels[n], E[n]) for n in cardinals]
    return f.bundle([*sps, f.assembled(scheme, inputs, total)])


def _scheme_record(client, db_session, bundle: dict) -> dict:
    body = _ok(client, bundle)
    composite = _calc_by_label(db_session, body, "cbs")
    calc = client.get(f"/api/v1/scientific/calculations/{composite.public_ref}").json()["record"]
    ref = calc["level_of_theory"]["composite_scheme"]["composite_scheme_ref"]
    resp = client.get(f"/api/v1/scientific/composite-schemes/{ref}")
    assert resp.status_code == 200, resp.text[:800]
    return resp.json()["record"]


def _coefficients(record: dict, term: int) -> list[float | None]:
    return [i["coefficient"] for i in record["terms"][term]["inputs"]]


# ---------------------------------------------------------------------------
# The worked payloads
# ---------------------------------------------------------------------------


def test_worked_payload_b_reports_one_for_the_reference_and_the_x_minus_three_weights(client, db_session):
    record = _scheme_record(client, db_session, f.bundle_b())
    assert [t["linearity"] for t in record["terms"]] == ["linear", "linear"]
    assert record["linear_in_energies"] is True
    assert _coefficients(record, 0) == [1.0]
    # (4^3 E_4 - 3^3 E_3) / (4^3 - 3^3): the cardinal-3 input is -27/37, the cardinal-4 input 64/37.
    assert _coefficients(record, 1) == pytest.approx([-27 / 37, 64 / 37], abs=1e-15)
    assert [i["cardinal_number"] for i in record["terms"][1]["inputs"]] == [3, 4]


def test_worked_payload_c_reports_plus_and_minus_one_for_every_difference(client, db_session):
    body = _ok(client, f.bundle_c())
    composite = _calc_by_label(db_session, body, "fpa")
    ref = client.get(f"/api/v1/scientific/calculations/{composite.public_ref}").json()["record"]["level_of_theory"][
        "composite_scheme"
    ]["composite_scheme_ref"]
    record = client.get(f"/api/v1/scientific/composite-schemes/{ref}").json()["record"]
    # The stored order of terms is the scheme's canonical one, not the producer's: compare as a set of terms.
    got = sorted(
        (t["operation"], t["energy_component"], [(i["slot"], i["coefficient"]) for i in t["inputs"]])
        for t in record["terms"]
    )
    assert got == sorted(
        [
            ("base", "total", [("value", 1.0)]),
            ("difference", "total", [("high", 1.0), ("low", -1.0)]),  # dcv
            ("difference", "total", [("high", 1.0), ("low", -1.0)]),  # dtq
            ("difference", "total", [("high", 1.0), ("low", -1.0)]),  # drel
            ("value", "dboc", [("value", 1.0)]),
        ]
    )
    assert {t["linearity"] for t in record["terms"]} == {"linear"}
    assert record["linear_in_energies"] is True


def test_a_named_method_states_no_terms_and_nothing_about_linearity(client, db_session):
    # The named-method scheme exists once a CBS-QB3 level is deposited; deposit one the plain way.
    resp = client.post(
        "/api/v1/uploads/conformers",
        json={
            "species_entry": dict(f.WATER),
            "geometry": {"xyz_text": f.WATER_XYZ},
            "calculation": {
                "type": "composite",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "CBS-QB3"},
                "composite_result": {"assembly": "program_run", "e0_hartree": -76.0},
            },
        },
    )
    assert resp.status_code == 201, resp.text[:600]
    from app.db.models.calculation import Calculation

    calc = db_session.get(Calculation, resp.json()["primary_calculation"]["calculation_id"])
    record = client.get(f"/api/v1/scientific/calculations/{calc.public_ref}").json()["record"]
    scheme_ref = record["level_of_theory"]["composite_scheme"]["composite_scheme_ref"]
    scheme = client.get(f"/api/v1/scientific/composite-schemes/{scheme_ref}").json()["record"]
    assert scheme["terms"] == []
    assert scheme["linear_in_energies"] is None


# ---------------------------------------------------------------------------
# Each extrapolation formula, with coefficients written out by hand
# ---------------------------------------------------------------------------


def test_inverse_power_with_a_non_integer_exponent(client, db_session):
    x = 2.46
    f2, f3 = 2.0**-x, 3.0**-x
    c2, c3 = f3 / (f3 - f2), -f2 / (f3 - f2)
    total = c2 * E[2] + c3 * E[3]
    record = _scheme_record(client, db_session, _extrapolation_bundle("inverse_power", x, (2, 3), total))
    assert record["terms"][0]["exponent"] == x
    assert _coefficients(record, 0) == pytest.approx([c2, c3], abs=1e-12)
    assert sum(_coefficients(record, 0)) == pytest.approx(1.0, abs=1e-12)


def test_the_shifted_half_form_uses_n_plus_a_half(client, db_session):
    x = 4.0
    ratio = (4.5 / 3.5) ** x  # f(3)/f(4) with f(n) = (n + 1/2)^-x
    c3 = 1.0 / (1.0 - ratio)
    c4 = 1.0 - c3
    total = c3 * E[3] + c4 * E[4]
    record = _scheme_record(client, db_session, _extrapolation_bundle("inverse_power_shifted_half", x, (3, 4), total))
    assert _coefficients(record, 0) == pytest.approx([c3, c4], abs=1e-12)
    # The plain n^-x weights would differ: the shift is real.
    plain_c3 = 1.0 / (1.0 - (4.0 / 3.0) ** x)
    assert abs(_coefficients(record, 0)[0] - plain_c3) > 1e-3


def test_karton_martin_scf_uses_its_own_basis_function(client, db_session):
    def g(n: int) -> float:
        return (n + 1) * math.exp(-9.0 * math.sqrt(n))

    c3 = g(4) / (g(4) - g(3))
    c4 = -g(3) / (g(4) - g(3))
    total = c3 * E[3] + c4 * E[4]
    record = _scheme_record(client, db_session, _extrapolation_bundle("karton_martin_scf", None, (3, 4), total))
    assert record["terms"][0]["exponent"] is None
    assert _coefficients(record, 0) == pytest.approx([c3, c4], rel=1e-9)


def test_the_exponential_three_point_is_not_linear_and_gives_no_coefficients(client, db_session):
    # d1 = E2 - E3 = 0.06, d2 = E3 - E4 = 0.02, so the limit is E4 - d2^2 / (d1 - d2).
    total = E[4] - 0.02**2 / (0.06 - 0.02)
    record = _scheme_record(client, db_session, _extrapolation_bundle("exponential_three_point", None, (2, 3, 4), total))
    (term,) = record["terms"]
    assert term["linearity"] == "nonlinear"
    assert _coefficients(record, 0) == [None, None, None]
    assert record["linear_in_energies"] is False


def test_a_scheme_with_a_nonlinear_term_still_gives_the_coefficients_of_its_linear_terms(client, db_session):
    levels = _levels(2, 3, 4)
    exp3 = {
        "key": "x",
        "operation": "extrapolation",
        "energy_component": "total",
        "formula": "exponential_three_point",
        "inputs": [{"slot": "cardinal", "cardinal_number": n, "level_of_theory": levels[n]} for n in (2, 3, 4)],
    }
    shift = {
        "key": "d",
        "operation": "difference",
        "energy_component": "total",
        "inputs": [{"slot": "high", "level_of_theory": levels[4]}, {"slot": "low", "level_of_theory": levels[3]}],
    }
    scheme = {"kind": "additive", "terms": [exp3, shift]}
    inputs = [
        *({"term_key": "x", "slot": "cardinal", "cardinal_number": n, "calculation_key": f"sp{n}"} for n in (2, 3, 4)),
        {"term_key": "d", "slot": "high", "calculation_key": "sp4"},
        {"term_key": "d", "slot": "low", "calculation_key": "sp3"},
    ]
    total = (E[4] - 0.02**2 / (0.06 - 0.02)) + (E[4] - E[3])
    sps = [f.sp(f"sp{n}", levels[n], E[n]) for n in (2, 3, 4)]
    record = _scheme_record(client, db_session, f.bundle([*sps, f.assembled(scheme, inputs, total)]))
    assert [t["linearity"] for t in record["terms"]] == ["nonlinear", "linear"]
    assert record["linear_in_energies"] is False
    assert _coefficients(record, 0) == [None, None, None]
    assert _coefficients(record, 1) == [1.0, -1.0]


# ---------------------------------------------------------------------------
# The derivation on rows no upload validated
# ---------------------------------------------------------------------------


class _Input:
    """A stored term input as the read sees it, built directly (nothing validated it)."""

    _next = 0

    def __init__(self, slot: str, cardinal: int | None = None):
        from app.db.models.common import CompositeInputSlot

        _Input._next += 1
        self.id = _Input._next
        self.slot = CompositeInputSlot(slot)
        self.cardinal_number = cardinal


def _op(name: str):
    from app.db.models.common import CompositeTermOperation

    return CompositeTermOperation(name)


def test_term_coefficients_on_unvalidated_stored_shapes():
    value = _Input("value")
    assert term_coefficients(_op("base"), None, None, [value]) == {value.id: 1.0}
    high, low = _Input("high"), _Input("low")
    assert term_coefficients(_op("difference"), None, None, [high, low]) == {high.id: 1.0, low.id: -1.0}
    # An input in a slot the operation does not read gets nothing, rather than a guess.
    stray = _Input("cardinal", 3)
    assert term_coefficients(_op("base"), None, None, [stray]) == {}
    assert term_coefficients(_op("empirical"), None, None, [value]) == {}


def test_an_extrapolation_stored_in_a_shape_its_formula_cannot_read_gets_no_coefficients():
    from app.db.models.common import CompositeExtrapolationFormula as F

    one = _Input("cardinal", 3)
    assert term_coefficients(_op("extrapolation"), F.inverse_power, 3.0, [one]) == {}  # one point
    a, b = _Input("cardinal", 3), _Input("cardinal", 3)
    assert term_coefficients(_op("extrapolation"), F.inverse_power, 3.0, [a, b]) == {}  # equal cardinals
    c, d = _Input("cardinal", 3), _Input("cardinal", 4)
    assert term_coefficients(_op("extrapolation"), F.inverse_power, None, [c, d]) == {}  # no exponent
    assert term_coefficients(_op("extrapolation"), None, None, [c, d]) == {}  # no formula
    # The same two inputs with a valid formula do get them, ordered by cardinal whatever the row order.
    got = term_coefficients(_op("extrapolation"), F.inverse_power, 3.0, [d, c])
    assert got[c.id] == pytest.approx(-27 / 37) and got[d.id] == pytest.approx(64 / 37)


def test_term_linearity_by_operation_and_formula():
    from app.db.models.common import CompositeExtrapolationFormula as F
    from app.db.models.common import CompositeTermLinearity as L

    assert term_linearity(_op("value"), None) is L.linear
    assert term_linearity(_op("base"), None) is L.linear
    assert term_linearity(_op("difference"), None) is L.linear
    assert term_linearity(_op("extrapolation"), F.inverse_power) is L.linear
    assert term_linearity(_op("extrapolation"), F.karton_martin_scf) is L.linear
    assert term_linearity(_op("extrapolation"), F.exponential_three_point) is L.nonlinear
    assert term_linearity(_op("extrapolation"), None) is L.not_applicable
    assert term_linearity(_op("empirical"), None) is L.not_applicable


def test_scheme_linearity_rule():
    from app.db.models.common import CompositeTermLinearity as L

    assert scheme_linearity([]) is None
    assert scheme_linearity([L.not_applicable]) is None
    assert scheme_linearity([L.linear, L.linear]) is True
    assert scheme_linearity([L.linear, L.nonlinear]) is False
    assert scheme_linearity([L.linear, L.not_applicable]) is True
    assert scheme_linearity([L.nonlinear, L.not_applicable]) is False
