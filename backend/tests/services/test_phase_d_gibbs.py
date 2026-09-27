"""D4: Gibbs self-consistency, against hand formulas only.

Every thermo row here is persisted through the real upload workflow
(``persist_thermo_upload``), which resolves its species entry through
``resolve_species_entry`` -- so ions, isotopologues and unreadable
structures are exactly as the upload path stores them. Expected numbers
come from the hand NASA formulas and SI R, never from the production
evaluator. Every iterating test asserts its exact evaluated count first,
and every reason is asserted as an exact token.
"""
import json
import sys
from decimal import Decimal
from math import inf, isfinite, log, nan

import pytest

from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.consistency import engine
from app.services.consistency.gibbs import G_DEFINITION, RUNNER, STANDARD_STATE_NOTE, compare_gibbs
from app.workflows.thermo import persist_thermo_upload

R = 8.31446261815324
EPS = sys.float_info.epsilon
T298 = 298.15
DECLARED = "formation_298k"

LOW = (3.0, 2e-3, -4e-7, 3e-11, -1e-15, -9000.0, 7.0)
HIGH = (3.4, 1.1e-3, -2e-7, 1e-11, -3e-16, -8800.0, 5.0)
NASA9 = (20.0, -5.0, 3.0, 2e-3, -4e-7, 3e-11, -1e-15, -9100.0, 7.0)

PAYLOAD_KEYS = {
    "g_definition", "enthalpy_reference_kind", "reference_pressure_bar", "phase", "source",
    "temperature_k", "unit", "g_kj_mol", "h_kj_mol", "s_j_mol_k", "residual_definition",
    "residual_kj_mol", "within_float_precision", "float_precision_bound_kj_mol", "reason",
}
#: Sources whose H and S come from a representation other than G's own row.
NOTED = {"nasa7", "nasa9", "scalar298"}


# -- hand formulas --------------------------------------------------------------


def nasa7_h(a, t):
    """kJ/mol: H/R = a1 T + a2 T^2/2 + a3 T^3/3 + a4 T^4/4 + a5 T^5/5 + a6."""
    return R * (sum(a[k - 1] * t ** k / k for k in range(1, 6)) + a[5]) / 1000.0


def nasa7_s(a, t):
    """J/mol/K: S/R = a1 ln T + a2 T + a3 T^2/2 + a4 T^3/3 + a5 T^4/4 + a7."""
    return R * (a[0] * log(t) + sum(a[k - 1] * t ** (k - 1) / (k - 1) for k in range(2, 6)) + a[6])


def nasa9_h(a, t):
    """kJ/mol: H/R = -a1/T + a2 ln T + a3 T + a4 T^2/2 + a5 T^3/3 + a6 T^4/4 + a7 T^5/5 + a8."""
    return R * (-a[0] / t + a[1] * log(t) + a[2] * t + a[3] * t ** 2 / 2 + a[4] * t ** 3 / 3
                + a[5] * t ** 4 / 4 + a[6] * t ** 5 / 5 + a[7]) / 1000.0


def nasa9_s(a, t):
    """J/mol/K: S/R = -a1/(2T^2) - a2/T + a3 ln T + a4 T + a5 T^2/2 + a6 T^3/3 + a7 T^4/4 + a9."""
    return R * (-a[0] / (2 * t ** 2) - a[1] / t + a[2] * log(t) + a[3] * t + a[4] * t ** 2 / 2
                + a[5] * t ** 3 / 3 + a[6] * t ** 4 / 4 + a[8])


def gibbs(h, s, t):
    return h - t * s / 1000.0


# -- persisted fixtures -----------------------------------------------------------


def upload_thermo(session, *, smiles="C", charge=0, multiplicity=1, **fields):
    """Persist one thermo record exactly as ``POST /uploads/thermo`` would."""
    request = ThermoUploadRequest(
        species_entry={"smiles": smiles, "charge": charge, "multiplicity": multiplicity}, **fields)
    row = persist_thermo_upload(session, request, review_policy=None)
    session.flush()
    session.expire(row)
    return row


def nasa7_payload(low=LOW, high=HIGH, t_low=200.0, t_mid=1000.0, t_high=3000.0):
    payload = {"t_low": t_low, "t_mid": t_mid, "t_high": t_high}
    payload.update({f"a{i}": v for i, v in enumerate(low, 1)})
    payload.update({f"b{i}": v for i, v in enumerate(high, 1)})
    return payload


def nasa9_payload(index, t_min, t_max, a=NASA9):
    return {"interval_index": index, "t_min_k": t_min, "t_max_k": t_max, **{f"a{i}": v for i, v in enumerate(a, 1)}}


def run(session, thermo):
    """As the service does: no autoflush, so an in-memory edit is read, never written."""
    with session.no_autoflush:
        return compare_gibbs(thermo)


def rows_of(result):
    rows = [json.loads(f.message) for f in result.findings]
    assert rows, "zero findings"
    assert all(f.severity.value == "info" and f.category.value == "consistency" for f in result.findings)
    assert result.runner == RUNNER
    return rows


def by_key(rows):
    keyed = {(r["source"], r["temperature_k"]): r for r in rows}
    assert len(keyed) == len(rows), "duplicate (source, temperature) findings"
    return keyed


# -- D4-1: the point identity ------------------------------------------------------


def test_d4_1_point_identity_is_exact_and_flagged_within_float_precision(db_session):
    # 500 * 200 / 1000 = 100 exactly, so g = -50 - 100 = -150 exactly.
    thermo = upload_thermo(db_session, enthalpy_reference_kind=DECLARED,
                           points=[{"temperature_k": 500.0, "h_kj_mol": -50.0, "s_j_mol_k": 200.0,
                                    "g_kj_mol": -150.0}])
    rows = rows_of(run(db_session, thermo))
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == PAYLOAD_KEYS
    assert (row["source"], row["temperature_k"], row["reason"]) == ("point", 500.0, None)
    assert row["residual_kj_mol"] == 0.0
    assert row["within_float_precision"] is True
    assert row["float_precision_bound_kj_mol"] == pytest.approx(16 * EPS * (150.0 + 50.0 + 100.0), rel=1e-12)
    assert row["g_definition"] == G_DEFINITION == "formation_298k H(T) - T*S(T)"
    assert row["enthalpy_reference_kind"] == DECLARED
    # The recorded pressure is null (no default since #529); it is shown and does not gate.
    assert "reference_pressure_bar" in row and row["reference_pressure_bar"] is None
    assert row["phase"] == "gas"


def test_d4_1b_a_rounding_only_residual_is_nonzero_but_within_float_precision(db_session):
    # A producer computing in decimal writes the exact decimal G; the double
    # nearest to it is not bit-identical to h - T*s/1000 evaluated in doubles.
    h, s = -74.6, 219.3
    g_exact = Decimal("-74.6") - Decimal("298.15") * Decimal("219.3") / 1000
    assert g_exact == Decimal("-139.984295")
    g = float(g_exact)
    hand_residual = g - gibbs(h, s, T298)
    assert hand_residual != 0.0  # premise: rounding really leaves a residual
    thermo = upload_thermo(db_session, enthalpy_reference_kind=DECLARED,
                           points=[{"temperature_k": T298, "h_kj_mol": h, "s_j_mol_k": s, "g_kj_mol": g}])
    rows = rows_of(run(db_session, thermo))
    assert len(rows) == 1
    assert rows[0]["reason"] is None
    assert rows[0]["residual_kj_mol"] == hand_residual
    assert abs(hand_residual) < 1e-12
    assert rows[0]["within_float_precision"] is True


# -- D4-2: a G on another convention is reported raw, never relabelled -------------


def test_d4_2_an_offset_g_is_the_raw_residual_and_is_never_relabelled(db_session):
    h, s = -74.6, 186.3
    g = gibbs(h, s, T298) + 79.6  # the size of the T*sum(n S_e) gap between G_conv and dfG for CH4
    thermo = upload_thermo(db_session, enthalpy_reference_kind=DECLARED,
                           points=[{"temperature_k": T298, "h_kj_mol": h, "s_j_mol_k": s, "g_kj_mol": g}])
    result = run(db_session, thermo)
    rows = rows_of(result)
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == PAYLOAD_KEYS
    assert row["reason"] is None
    assert row["residual_kj_mol"] == pytest.approx(79.6, abs=1e-9)
    assert row["within_float_precision"] is False
    assert row["g_definition"] == G_DEFINITION
    assert row["g_kj_mol"] == g
    for text in (f.message.lower() for f in result.findings):
        for guess in ("dfg", "delta", "looks", "formation_gibbs", "formation gibbs", "likely", "probably"):
            assert guess not in text


# -- D4-3: a rounded G is a small residual, flag false -------------------------------


def test_d4_3_a_rounded_g_leaves_the_hand_residual_and_is_not_within_float_precision(db_session):
    # 298.15 * 186.25 / 1000 = 55.5304375; h - T s/1000 = -130.1304375; g rounded to -130.1.
    thermo = upload_thermo(db_session, enthalpy_reference_kind=DECLARED,
                           points=[{"temperature_k": T298, "h_kj_mol": -74.6, "s_j_mol_k": 186.25,
                                    "g_kj_mol": -130.1}])
    rows = rows_of(run(db_session, thermo))
    assert len(rows) == 1
    assert rows[0]["reason"] is None
    assert rows[0]["residual_kj_mol"] == pytest.approx(0.0304375, rel=1e-9)
    assert rows[0]["within_float_precision"] is False


def test_d4_3b_a_residual_far_below_any_accuracy_concern_is_still_not_float_precision(db_session):
    # Exact -130.1304375 written to six decimals: residual +5e-7 kJ/mol, about
    # a million times the float-precision bound (~1e-12) -- an absolute
    # tolerance cannot tell these apart, a relative ulp bound can.
    thermo = upload_thermo(db_session, enthalpy_reference_kind=DECLARED,
                           points=[{"temperature_k": T298, "h_kj_mol": -74.6, "s_j_mol_k": 186.25,
                                    "g_kj_mol": -130.130437}])
    rows = rows_of(run(db_session, thermo))
    assert len(rows) == 1
    assert rows[0]["reason"] is None
    assert rows[0]["residual_kj_mol"] == pytest.approx(5e-7, rel=1e-6)
    assert rows[0]["float_precision_bound_kj_mol"] < 1e-11
    assert rows[0]["within_float_precision"] is False


# -- D4-4: fits and scalars give residuals only; H and S from one source ---------------


def test_d4_4_each_source_supplies_its_own_h_and_s_at_the_points_temperature(db_session):
    t1, t2 = 600.0, 1500.0
    h1, s1 = nasa7_h(LOW, t1), nasa7_s(LOW, t1)
    h2, s2 = nasa7_h(HIGH, t2), nasa7_s(HIGH, t2)
    h298, s298 = -74.6, 186.3
    points = [
        # Same-row h and s deliberately differ from the fit's: point residual
        # 0.5 - 1.0 + 600*2/1000 = +0.7; the fit's own residual stays +0.5.
        {"temperature_k": t1, "h_kj_mol": h1 + 1.0, "s_j_mol_k": s1 + 2.0, "g_kj_mol": gibbs(h1, s1, t1) + 0.5},
        # High branch; the point has no entropy of its own.
        {"temperature_k": t2, "h_kj_mol": h2, "g_kj_mol": gibbs(h2, s2, t2) + 0.5},
        # At 298.15 the scalars are a source: residual +0.25 against them. The
        # row's own s differs from s298 by 2 J/mol/K, so the point residual is
        # 0.25 + 298.15*2/1000 = +0.8463 and a scalar source that read S from
        # the row instead of s298 would show here.
        {"temperature_k": T298, "h_kj_mol": h298, "s_j_mol_k": s298 + 2.0,
         "g_kj_mol": gibbs(h298, s298, T298) + 0.25},
    ]
    thermo = upload_thermo(db_session, enthalpy_reference_kind=DECLARED, h298_kj_mol=h298, s298_j_mol_k=s298,
                           nasa=nasa7_payload(), points=points)
    rows = by_key(rows_of(run(db_session, thermo)))
    assert len(rows) == 7  # three stored G x (point, nasa7), plus scalar298 at 298.15 only
    expected = {
        ("point", t1): (0.7, None), ("nasa7", t1): (0.5, None),
        ("point", t2): (None, "gibbs_point_missing_enthalpy_or_entropy"), ("nasa7", t2): (0.5, None),
        ("point", T298): (0.25 + T298 * 2.0 / 1000.0, None), ("scalar298", T298): (0.25, None),
        ("nasa7", T298): (gibbs(h298, s298, T298) + 0.25 - gibbs(nasa7_h(LOW, T298), nasa7_s(LOW, T298), T298), None),
    }
    assert (rows["scalar298", T298]["h_kj_mol"], rows["scalar298", T298]["s_j_mol_k"]) == (h298, s298)
    for (source, _), row in rows.items():
        assert row.get("standard_state_note") == (STANDARD_STATE_NOTE if source in NOTED else None), source
    assert set(rows) == set(expected)
    for key, (residual, reason) in expected.items():
        row = rows[key]
        assert row["reason"] == reason, key
        if residual is None:
            assert row["residual_kj_mol"] is None, key
        else:
            assert row["residual_kj_mol"] == pytest.approx(residual, abs=1e-9), key
    # Fit H and S are the hand values at the point's own temperature.
    assert rows["nasa7", t1]["h_kj_mol"] == pytest.approx(h1, rel=1e-12)
    assert rows["nasa7", t1]["s_j_mol_k"] == pytest.approx(s1, rel=1e-12)
    assert rows["nasa7", t2]["h_kj_mol"] == pytest.approx(h2, rel=1e-12)
    assert rows["nasa7", t2]["s_j_mol_k"] == pytest.approx(s2, rel=1e-12)
    # Only the point source is an identity with a flag; every other source is residual only.
    flagged = [k for k, r in rows.items() if r["within_float_precision"] is not None]
    assert set(flagged) == {("point", t1), ("point", T298)}
    assert all(rows[k]["within_float_precision"] is False for k in flagged)
    assert all(r["float_precision_bound_kj_mol"] is None for k, r in rows.items() if k[0] != "point")


def test_d4_4b_nasa9_residual_from_the_interval_that_owns_the_temperature(db_session):
    t = 600.0
    h, s = nasa9_h(NASA9, t), nasa9_s(NASA9, t)
    thermo = upload_thermo(db_session, enthalpy_reference_kind=DECLARED,
                           nasa9_intervals=[nasa9_payload(1, 200.0, 2000.0)],
                           points=[{"temperature_k": t, "g_kj_mol": gibbs(h, s, t) + 0.5, "h_kj_mol": h}])
    rows = by_key(rows_of(run(db_session, thermo)))
    assert len(rows) == 2
    assert rows["point", t]["reason"] == "gibbs_point_missing_enthalpy_or_entropy"
    assert rows["nasa9", t]["reason"] is None
    assert rows["nasa9", t]["residual_kj_mol"] == pytest.approx(0.5, abs=1e-9)
    assert rows["nasa9", t]["within_float_precision"] is None
    assert rows["nasa9", t]["standard_state_note"] == STANDARD_STATE_NOTE
    assert "standard_state_note" not in rows["point", t]


# -- the scalar source adds at most one row per record -----------------------------------------

_TABLE = [300.0 + 20.0 * i for i in range(50)]


@pytest.mark.parametrize("with_298", [False, True])
def test_scalar_source_adds_one_row_per_record_not_one_per_point(db_session, with_298):
    temperatures = ([T298] if with_298 else []) + _TABLE
    points = [{"temperature_k": t, "h_kj_mol": -74.6, "s_j_mol_k": 186.3, "g_kj_mol": gibbs(-74.6, 186.3, t)}
              for t in temperatures]
    thermo = upload_thermo(db_session, enthalpy_reference_kind=DECLARED, h298_kj_mol=-74.6, s298_j_mol_k=186.3,
                           points=points)
    rows = by_key(rows_of(run(db_session, thermo)))
    assert len(points) == 50 + with_298
    assert len(rows) == len(points) + 1
    assert sum(r["source"] == "point" for r in rows.values()) == len(points)
    scalar = [r for r in rows.values() if r["source"] == "scalar298"]
    assert len(scalar) == 1 and scalar[0]["temperature_k"] == T298
    if with_298:
        assert scalar[0]["reason"] is None and scalar[0]["residual_kj_mol"] == pytest.approx(0.0, abs=1e-12)
    else:
        assert scalar[0]["reason"] == "no_exact_matching_point"
        assert scalar[0]["g_kj_mol"] is None and scalar[0]["residual_kj_mol"] is None


# -- pressure never gates D4 ------------------------------------------------------------


@pytest.mark.parametrize("pressure", [None, 1.0, 1.01325, nan, 0.0, -2.0, inf])
def test_recorded_pressure_is_shown_and_never_gates_or_changes_the_residual(db_session, pressure):
    t = 600.0
    h, s = nasa7_h(LOW, t), nasa7_s(LOW, t)
    thermo = upload_thermo(db_session, enthalpy_reference_kind=DECLARED, nasa=nasa7_payload(),
                           points=[{"temperature_k": t, "h_kj_mol": h, "s_j_mol_k": s, "g_kj_mol": gibbs(h, s, t) + 0.5}])
    thermo.reference_pressure_bar = pressure  # in memory only: invalid values cannot be uploaded
    rows = by_key(rows_of(run(db_session, thermo)))
    assert len(rows) == 2
    for source in ("point", "nasa7"):
        assert rows[source, t]["reason"] is None
        assert rows[source, t]["residual_kj_mol"] == pytest.approx(0.5, abs=1e-9)
        shown = rows[source, t]["reference_pressure_bar"]
        if pressure is None or isfinite(pressure):
            assert shown == pressure
        else:
            assert shown == {"nonfinite_float": repr(pressure)}


def test_g_is_not_made_a_pressure_independent_quantity():
    assert engine.PRESSURE_INDEPENDENT_QUANTITIES == frozenset({"cp", "h"})


# -- the empty eligible set ----------------------------------------------------------------


@pytest.mark.parametrize("points", [[], [{"temperature_k": 500.0, "h_kj_mol": -50.0, "s_j_mol_k": 200.0}]])
def test_no_stored_gibbs_values_is_exactly_one_finding(db_session, points):
    fields = {"enthalpy_reference_kind": DECLARED, "points": points}
    if not points:
        fields["h298_kj_mol"] = -74.6
    thermo = upload_thermo(db_session, **fields)
    rows = rows_of(run(db_session, thermo))
    assert len(rows) == 1
    assert rows[0]["reason"] == "no_stored_gibbs_values"
    assert rows[0]["points_examined"] == len(points)
    assert rows[0]["g_definition"] == G_DEFINITION


# -- D4-5: one case per reason ---------------------------------------------------------------

_POINT = {"temperature_k": 500.0, "h_kj_mol": -50.0, "s_j_mol_k": 200.0, "g_kj_mol": -150.0}
_POINT_298 = {"temperature_k": T298, "h_kj_mol": -74.6, "s_j_mol_k": 186.25, "g_kj_mol": -130.1}


def _set_thermo(column, value):
    def mutate(thermo):
        setattr(thermo, column, value)
    return mutate


def _set_point(column, value):
    def mutate(thermo):
        setattr(thermo.points[0], column, value)
    return mutate


def _set_nasa(column, value):
    def mutate(thermo):
        setattr(thermo.nasa, column, value)
    return mutate


REASON_CASES = [
    # (id, upload fields, species (smiles, charge, multiplicity), in-memory mutation, expected {source: reason})
    ("enthalpy_reference_unrecorded",
     {"points": [{"temperature_k": 500.0, "s_j_mol_k": 200.0, "g_kj_mol": -150.0}]},
     ("C", 0, 1), None, {"point": "enthalpy_reference_unrecorded"}),
    ("charged_species_out_of_scope", {"enthalpy_reference_kind": DECLARED, "points": [_POINT]},
     ("[NH4+]", 1, 1), None, {"point": "charged_species_out_of_scope"}),
    ("isotope_labelled_species_out_of_scope", {"enthalpy_reference_kind": DECLARED, "points": [_POINT]},
     ("[2H]C([2H])([2H])[2H]", 0, 1), None, {"point": "isotope_labelled_species_out_of_scope"}),
    ("unusable_species_composition", {"enthalpy_reference_kind": DECLARED, "points": [_POINT]},
     ("*C", 0, 1), None, {"point": "unusable_species_composition"}),
    ("phase_not_recorded",
     {"enthalpy_reference_kind": DECLARED, "scientific_origin": "experimental", "points": [_POINT]},
     ("C", 0, 1), None, {"point": "phase_not_recorded"}),
    ("non_gas_phase_unsupported", {"enthalpy_reference_kind": DECLARED, "phase": "liquid", "points": [_POINT]},
     ("C", 0, 1), None, {"point": "non_gas_phase_unsupported"}),
    ("gibbs_point_missing_enthalpy_or_entropy",
     {"enthalpy_reference_kind": DECLARED, "points": [{"temperature_k": 500.0, "h_kj_mol": -50.0, "g_kj_mol": -150.0}]},
     ("C", 0, 1), None, {"point": "gibbs_point_missing_enthalpy_or_entropy"}),
    # The upload path refuses non-finite numbers; these model rows written any other way.
    ("nonfinite_stored_value:g", {"enthalpy_reference_kind": DECLARED, "points": [_POINT]},
     ("C", 0, 1), _set_point("g_kj_mol", nan), {"point": "nonfinite_stored_value"}),
    ("nonfinite_stored_value:h", {"enthalpy_reference_kind": DECLARED, "points": [_POINT]},
     ("C", 0, 1), _set_point("h_kj_mol", inf), {"point": "nonfinite_stored_value"}),
    ("nonfinite_stored_value:g-precedes-sources",
     {"enthalpy_reference_kind": DECLARED, "nasa": nasa7_payload(), "points": [_POINT]},
     ("C", 0, 1), _set_point("g_kj_mol", -inf), {"point": "nonfinite_stored_value", "nasa7": "nonfinite_stored_value"}),
    ("nonfinite_stored_value:s", {"enthalpy_reference_kind": DECLARED, "points": [_POINT]},
     ("C", 0, 1), _set_point("s_j_mol_k", inf), {"point": "nonfinite_stored_value"}),
    ("nonfinite_stored_value:h298",
     {"enthalpy_reference_kind": DECLARED, "h298_kj_mol": -74.6, "s298_j_mol_k": 186.25, "points": [_POINT_298]},
     ("C", 0, 1), _set_thermo("h298_kj_mol", nan), {"point": None, "scalar298": "nonfinite_stored_value"}),
    ("no_exact_s298_value",
     {"enthalpy_reference_kind": DECLARED, "h298_kj_mol": -74.6, "points": [_POINT_298]},
     ("C", 0, 1), None, {"point": None, "scalar298": "no_exact_s298_value"}),
    ("no_exact_h298_value",
     {"enthalpy_reference_kind": DECLARED, "s298_j_mol_k": 186.25, "points": [_POINT_298]},
     ("C", 0, 1), None, {"point": None, "scalar298": "no_exact_h298_value"}),
    # Scalars present, no stored G at 298.15: one row for the record, at 298.15.
    ("no_exact_matching_point",
     {"enthalpy_reference_kind": DECLARED, "h298_kj_mol": -74.6, "s298_j_mol_k": 186.3, "points": [_POINT]},
     ("C", 0, 1), None, {"point": None, "scalar298": "no_exact_matching_point"}),
    ("unsupported_representation",
     {"enthalpy_reference_kind": DECLARED, "points": [_POINT],
      "wilhoit": {"cp0_j_mol_k": 33.0, "cp_inf_j_mol_k": 80.0, "b_k": 500.0, "a0": 0.0, "a1": 0.0, "a2": 0.0,
                  "a3": 0.0, "h0_kj_mol": -80.0, "s0_j_mol_k": 100.0}},
     ("C", 0, 1), None, {"point": None, "wilhoit": "unsupported_representation"}),
    ("temperature_outside_record_range",
     {"enthalpy_reference_kind": DECLARED, "nasa": nasa7_payload(), "tmin_k": 600.0, "tmax_k": 2000.0,
      "points": [_POINT]},
     ("C", 0, 1), None, {"point": None, "nasa7": "temperature_outside_record_range"}),
    ("temperature_outside_fit_range",
     {"enthalpy_reference_kind": DECLARED, "nasa": nasa7_payload(t_low=600.0), "points": [_POINT]},
     ("C", 0, 1), None, {"point": None, "nasa7": "temperature_outside_fit_range"}),
    ("temperature_outside_fit_or_in_gap",
     {"enthalpy_reference_kind": DECLARED, "points": [_POINT],
      # Below the only interval: the upload path refuses a gap between intervals.
      "nasa9_intervals": [nasa9_payload(1, 600.0, 2000.0)]},
     ("C", 0, 1), None, {"point": None, "nasa9": "temperature_outside_fit_or_in_gap"}),
    ("nonfinite_engine_result", {"enthalpy_reference_kind": DECLARED, "nasa": nasa7_payload(), "points": [_POINT]},
     ("C", 0, 1), _set_nasa("a1", 1e308), {"point": None, "nasa7": "nonfinite_engine_result"}),
]


@pytest.mark.parametrize("case_id,fields,species,mutate,expected", REASON_CASES, ids=[c[0] for c in REASON_CASES])
def test_d4_5_one_case_per_reason(db_session, case_id, fields, species, mutate, expected):
    smiles, charge, multiplicity = species
    thermo = upload_thermo(db_session, smiles=smiles, charge=charge, multiplicity=multiplicity, **fields)
    if mutate is not None:
        mutate(thermo)
    rows = rows_of(run(db_session, thermo))
    assert len(rows) == len(expected)
    by_source = {row["source"]: row for row in rows}
    assert len(by_source) == len(rows)
    assert {source: row["reason"] for source, row in by_source.items()} == expected
    for source, row in by_source.items():
        assert set(row) == PAYLOAD_KEYS | ({"standard_state_note"} if source in NOTED else set()), source
        assert (row["residual_kj_mol"] is None) is (row["reason"] is not None), source


def test_every_reason_d4_emits_has_a_case():
    tokens = {reason for *_, expected in REASON_CASES for reason in expected.values() if reason}
    assert len(REASON_CASES) == 20
    assert tokens | {"no_stored_gibbs_values"} == {
        "no_exact_matching_point",
        "enthalpy_reference_unrecorded", "charged_species_out_of_scope", "isotope_labelled_species_out_of_scope",
        "unusable_species_composition", "phase_not_recorded", "non_gas_phase_unsupported",
        "gibbs_point_missing_enthalpy_or_entropy", "nonfinite_stored_value", "no_exact_s298_value",
        "no_exact_h298_value", "unsupported_representation", "temperature_outside_record_range",
        "temperature_outside_fit_range", "temperature_outside_fit_or_in_gap", "nonfinite_engine_result",
        "no_stored_gibbs_values",
    }


def test_a_declared_kind_other_than_formation_298k_is_refused_not_relabelled(db_session):
    thermo = upload_thermo(db_session, enthalpy_reference_kind=DECLARED, points=[_POINT])
    thermo.enthalpy_reference_kind = "some_future_basis"  # in memory only; the enum has one member today
    with pytest.raises(ValueError, match="formation_298k"):
        run(db_session, thermo)
