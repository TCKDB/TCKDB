"""Pin :mod:`app.chemistry.network_kinetics_eval` against hand-computed
reference values.

The Chebyshev matrix and PLOG table below are the *real* stored fit for
one channel of the hydrazine network (`net_o6bt63kjeyvhvxx26w6kdi433a`,
channel N=N (E) + H2 -> 2 NH2, `nkin_x7zslyyxvslnagxwun3flcob3i` /
`nkin_lsoxdf26irod3nqr6tgjp4tlt4`), pulled 2026-09-08 via
``GET /scientific/network-kinetics/search?network_ref=...&include=coefficients,plog``
against the hosted instance. Both records describe the *same* physical
channel — one as a Chebyshev fit, one as a PLOG table — which is why the
two model kinds are cross-checked against each other below in addition
to each being pinned against an independent hand/calculator computation.
"""

from __future__ import annotations

import math

import pytest

from app.chemistry.network_kinetics_eval import (
    EvaluatedKPoint,
    KineticsEvaluationError,
    PlogEntry,
    chebyshev_reduced_pressure,
    chebyshev_reduced_temperature,
    evaluate_chebyshev,
    evaluate_plog,
    modified_arrhenius_k,
)

# ---------------------------------------------------------------------------
# Real hydrazine-network fixture data (both are the SAME channel)
# ---------------------------------------------------------------------------

# network_kinetics_ref = nkin_x7zslyyxvslnagxwun3flcob3i, model_kind=chebyshev
_CHEB_MATRIX = [
    [-11.0588, -0.016371, -0.011291, -0.0061746],
    [17.589, 0.0118079, 0.0081, 0.00438967],
    [0.2061, -0.000396769, -0.000233379, -9.12421e-05],
    [0.0494353, 0.000901544, 0.000603299, 0.000313357],
    [0.0230819, -1.84333e-05, -4.53189e-06, 4.75099e-06],
    [0.00686308, 6.35813e-05, 4.01076e-05, 1.86553e-05],
]
_CHEB_BOUNDS = {"tmin_k": 300.0, "tmax_k": 2000.0, "pmin_bar": 0.01, "pmax_bar": 100.0}

# network_kinetics_ref = nkin_lsoxdf26irod3nqr6tgjp4tlt4, model_kind=plog
_PLOG_ENTRIES = [
    PlogEntry(pressure_bar=0.01, a=59828.5, n=2.40236, ea_kj_mol=225.147),
    PlogEntry(pressure_bar=0.1, a=59747.1, n=2.40253, ea_kj_mol=225.147),
    PlogEntry(pressure_bar=1.0, a=58937.7, n=2.40426, ea_kj_mol=225.144),
    PlogEntry(pressure_bar=10.0, a=51368.2, n=2.42166, ea_kj_mol=225.117),
    PlogEntry(pressure_bar=100.0, a=13579.5, n=2.58976, ea_kj_mol=224.788),
]


# ---------------------------------------------------------------------------
# Chebyshev — hand-computed pin
# ---------------------------------------------------------------------------


def test_chebyshev_reduced_variables_at_t1000_p1() -> None:
    """T=1000 K sits exactly at reduced T=11/17; P=1 bar sits exactly at
    reduced P=0, both computed by hand from the reduced-variable formulas
    (not the implementation under test):

        T~ = (2/T - 1/Tmin - 1/Tmax) / (1/Tmax - 1/Tmin)

    With Tmin=300, Tmax=2000, T=1000, using a common denominator of 6000:
    2/1000=12/6000, 1/300=20/6000, 1/2000=3/6000, so
    T~ = (12-20-3)/(3-20) = -11/-17 = 11/17.

        P~ = (2 log10 P - log10 Pmin - log10 Pmax) / (log10 Pmax - log10 Pmin)

    With Pmin=0.01, Pmax=100, P=1: log10(1)=0, log10(0.01)=-2, log10(100)=2,
    so P~ = (0 - (-2) - 2) / (2 - (-2)) = 0/4 = 0.
    """
    t_tilde = chebyshev_reduced_temperature(1000.0, tmin_k=300.0, tmax_k=2000.0)
    p_tilde = chebyshev_reduced_pressure(1.0, pmin_bar=0.01, pmax_bar=100.0)
    assert t_tilde == pytest.approx(11.0 / 17.0, rel=1e-12)
    assert p_tilde == pytest.approx(0.0, abs=1e-12)


def test_chebyshev_evaluate_at_t1000_p1_matches_hand_computation() -> None:
    """Independent hand/calculator computation (not the module under
    test) for T=1000 K, P=1 bar:

    T~ = 11/17 = 0.6470588235294118, P~ = 0 exactly.

    At P~=0, only even-order pressure Chebyshev polynomials are nonzero
    (T_0(0)=1, T_1(0)=0, T_2(0)=-1, T_3(0)=0), so only the j=0 and j=2
    columns of the matrix contribute, with signs [+1, -1].

    Working the T-axis polynomials by the T_n(x)=2x*T_{n-1}(x)-T_{n-2}(x)
    recurrence at x=11/17 (exact fractions, then decimal):
        T0=1
        T1=11/17 = 0.6470588235294118
        T2=2x^2-1 = 2*(121/289)-1 = -47/289 = -0.16262975778546713
        T3=2x*T2-T1 = -4213/4913 = -0.8575209851210259
        T4=2x*T3-T2 = -0.9471030497730306
        T5=2x*T4-T3 = -0.3681404999339087

    log10(k) = sum_i coeffs[i][0]*T_i(T~) - sum_i coeffs[i][2]*T_i(T~)
             = -11.0588*1 + 17.589*0.647058823529 + 0.2061*(-0.16262975778546713)
               + 0.0494353*(-0.8575209851210259) + 0.0230819*(-0.9471030497730306)
               + 0.00686308*(-0.3681404999339087)
               - [ -0.011291*1 + 0.0081*0.647058823529 + (-0.000233379)*(-0.16262975778546713)
                   + 0.000603299*(-0.8575209851210259) + (-4.53189e-06)*(-0.9471030497730306)
                   + 4.01076e-05*(-0.3681404999339087) ]

    Computed with a plain calculator (not this module): log10(k) =
    0.22856001, so k = 10**0.22856001 = 1.692622 cm3/mol/s (rate_units
    is cm3_mol_s on the real record this matrix is pulled from).
    """
    result = evaluate_chebyshev(
        1000.0,
        1.0,
        coefficients=_CHEB_MATRIX,
        stores_log10_k=True,
        **_CHEB_BOUNDS,
    )
    assert isinstance(result, EvaluatedKPoint)
    assert result.in_range is True
    # log10(k) pinned to 1e-6; k itself checked to 4 significant figures.
    assert math.log10(result.k) == pytest.approx(0.22856001, abs=1e-6)
    assert result.k == pytest.approx(1.692622, rel=1e-5)


def test_chebyshev_evaluate_at_lower_corner_matches_hand_computation() -> None:
    """T=300 K (=Tmin), P=0.01 bar (=Pmin): reduced variables are exactly
    (-1, -1) by construction (both bounds collapse to the fit's own
    edge), so T_i(-1) = (-1)**i and P_j(-1) = (-1)**j -- the sum becomes
    an alternating-sign sum of every coefficient. Calculator value:
    log10(k) = -28.45464680, k = 3.510372e-29 cm3/mol/s.
    """
    result = evaluate_chebyshev(
        300.0,
        0.01,
        coefficients=_CHEB_MATRIX,
        stores_log10_k=True,
        **_CHEB_BOUNDS,
    )
    assert result.in_range is True
    assert math.log10(result.k) == pytest.approx(-28.45464680, abs=1e-5)
    assert result.k == pytest.approx(3.510372e-29, rel=1e-5)


def test_chebyshev_extrapolation_is_computed_and_flagged_out_of_range() -> None:
    """Past Tmax/Pmax the reduced variables exceed [-1, 1]; the fit is
    still evaluated (the Chebyshev recurrence stays real-valued there),
    but the point must come back flagged, never silently presented as
    an interpolated value."""
    result = evaluate_chebyshev(
        2500.0,  # > tmax_k=2000
        1.0,
        coefficients=_CHEB_MATRIX,
        stores_log10_k=True,
        **_CHEB_BOUNDS,
    )
    assert result.in_range is False
    assert math.isfinite(result.k)
    assert result.k > 0


def test_chebyshev_empty_matrix_refuses_rather_than_returns_zero() -> None:
    """A channel/fit with no coefficients must not evaluate to k=0 --
    that is indistinguishable from a genuinely tiny, deposited rate."""
    with pytest.raises(KineticsEvaluationError):
        evaluate_chebyshev(1000.0, 1.0, coefficients=[], **_CHEB_BOUNDS)
    with pytest.raises(KineticsEvaluationError):
        evaluate_chebyshev(1000.0, 1.0, coefficients=[[]], **_CHEB_BOUNDS)


def test_chebyshev_ragged_matrix_refuses() -> None:
    with pytest.raises(KineticsEvaluationError):
        evaluate_chebyshev(
            1000.0,
            1.0,
            coefficients=[[1.0, 2.0], [3.0]],
            **_CHEB_BOUNDS,
        )


def test_chebyshev_stores_log10_k_false_treats_sum_as_k_directly() -> None:
    """A degenerate 1x1 matrix at the fit's own reduced-variable origin
    means T_0(x)=1 for every axis regardless of (T, P), so the double
    sum equals the single coefficient exactly. With
    stores_log10_k=False, k must equal that coefficient verbatim (not
    10**coefficient)."""
    result = evaluate_chebyshev(
        1000.0,
        1.0,
        coefficients=[[42.0]],
        stores_log10_k=False,
        **_CHEB_BOUNDS,
    )
    assert result.k == pytest.approx(42.0)

    result_log = evaluate_chebyshev(
        1000.0,
        1.0,
        coefficients=[[2.0]],
        stores_log10_k=True,
        **_CHEB_BOUNDS,
    )
    assert result_log.k == pytest.approx(100.0)


def test_chebyshev_stores_log10_k_none_defaults_to_log10_convention() -> None:
    """Unrecorded stores_log10_k reads under the near-universal PDep
    Chebyshev convention (log10 k), the same 'assumed program default,
    stated as such' policy already applied elsewhere in this codebase to
    an unrecorded Hessian method."""
    with_none = evaluate_chebyshev(
        1000.0, 1.0, coefficients=[[2.0]], stores_log10_k=None, **_CHEB_BOUNDS
    )
    with_true = evaluate_chebyshev(
        1000.0, 1.0, coefficients=[[2.0]], stores_log10_k=True, **_CHEB_BOUNDS
    )
    assert with_none.k == pytest.approx(with_true.k)


# ---------------------------------------------------------------------------
# PLOG — hand-computed pin
# ---------------------------------------------------------------------------


def test_modified_arrhenius_k_matches_hand_computation() -> None:
    """k = A * T^n * exp(-Ea/RT) at T=1000K for the P=1 bar PLOG entry
    (a=58937.7, n=2.40426, ea_kj_mol=225.144), R=8.314462618 J/mol/K.

    Independent calculator working (corrected: an earlier version of
    this comment carried Ea/R as 27080.0 and the chain product as
    1.6798, neither of which reproduces the pinned value below --
    the pin itself was always correct, only the shown working was
    wrong; this is the actual working that reproduces it):

    Ea/R = 225144 / 8.314462618 = 27078.599104238583 K
    Ea/(R*T) = 27078.599104238583 / 1000 = 27.078599104238585
    exp(-27.078599104238585) = 1.7374560647980963e-12
    1000**2.40426 = 16322249.043535251
    k = 58937.7 * 16322249.043535251 * 1.7374560647980963e-12
      = 1.6714254673444082
    """
    k = modified_arrhenius_k(1000.0, a=58937.7, n=2.40426, ea_kj_mol=225.144)
    assert k == pytest.approx(1.671425, rel=1e-5)


def test_plog_evaluate_at_fitted_pressure_matches_hand_computation() -> None:
    """P=1 bar is exactly a fitted pressure, so no interpolation is
    involved -- k(1000K, 1bar) must equal the single Arrhenius
    evaluation at that entry (pinned above), and it must be flagged
    in_range=True (1 bar is strictly inside [0.01, 100])."""
    result = evaluate_plog(1000.0, 1.0, entries=_PLOG_ENTRIES)
    assert result.in_range is True
    assert result.k == pytest.approx(1.671425, rel=1e-5)


def test_plog_evaluate_agrees_with_chebyshev_for_the_same_real_channel() -> None:
    """These two fixtures are the SAME stored channel
    (source=N=N(E)+H2, sink=2 NH2) fit two different ways -- the
    strongest cross-check available (real archived data, two
    independent representations of one physical rate).

    Measured (independent calculator, scanning T in [300, 2000] every
    20 K and P as a dense log grid in [0.01, 100] bar -- 91x41 points
    on this one channel): worst relative deviation is **15.20%**, at
    (T=580K, P=100bar): cheb=1.2794e-9, plog=1.1106e-9 cm3/mol/s. The
    three corner/mid points originally pinned here individually agree
    much tighter (1.3%, 4.3%, 0.8%), which is why an earlier version of
    this test carried a much tighter rel=0.05 tolerance -- that
    tolerance was only ever exercised at those three easy points, not
    at the worst point on the surface, and was already at 4.31% of its
    own 5% budget on the loosest of the three.

    Why 15% and not <1%: this is a global 6x4 Chebyshev polynomial
    fit (24 coefficients spanning the WHOLE T/P rectangle at once)
    being compared against a 5-pressure PLOG table that log-linearly
    interpolates ONLY between its two immediate bracketing isobars.
    They are both real fits of the same underlying master-equation
    solution, not the same function evaluated two ways -- disagreement
    is fit residual, concentrated (as measured above) in the
    mid-temperature / high-pressure region where this channel's k(T,P)
    surface has the most curvature (a falloff-like transition), which
    is exactly where a 4th-order pressure polynomial and a 5-point
    log-linear table are least likely to agree with each other even
    though both are individually reasonable fits of the true surface.
    rel=0.20 is chosen to sit just above the measured 15.20% worst
    case with headroom, while still catching a formula-level bug (which
    produces disagreement of orders of magnitude, not tens of percent
    -- see the other tests in this file for exactly that failure mode).
    """
    for t, p in [(1000.0, 1.0), (300.0, 0.01), (2000.0, 100.0), (580.0, 100.0)]:
        cheb = evaluate_chebyshev(
            t, p, coefficients=_CHEB_MATRIX, stores_log10_k=True, **_CHEB_BOUNDS
        )
        plog = evaluate_plog(
            t, p, entries=_PLOG_ENTRIES, tmin_k=300.0, tmax_k=2000.0
        )
        assert cheb.k == pytest.approx(plog.k, rel=0.20), (t, p, cheb.k, plog.k)


def test_plog_log_log_interpolation_matches_hand_computation() -> None:
    """P=0.5 bar sits between the fitted 0.1 and 1.0 bar entries.
    Interpolation is linear in log10(k) against log10(P):

        frac = (log10(0.5) - log10(0.1)) / (log10(1.0) - log10(0.1))
             = (-0.30103 - (-1)) / (0 - (-1)) = 0.69897

    k(0.1 bar) and k(1.0 bar) at T=1000K computed via the same Arrhenius
    formula pinned above; interpolating log10(k) between them and
    exponentiating gives k(1000K, 0.5bar) = 1.672094 cm3/mol/s
    (calculator value).
    """
    result = evaluate_plog(1000.0, 0.5, entries=_PLOG_ENTRIES)
    assert result.in_range is True
    assert result.k == pytest.approx(1.672094, rel=1e-5)


def test_plog_interpolates_log_of_k_not_k_linearly() -> None:
    """PLOG interpolation must be linear in log10(k) against log10(P),
    never linear in k itself -- a plausible, easy-to-write-by-accident
    bug ("interpolate the two rate constants like any other pair of
    numbers") that still returns a well-shaped, plausible k.

    At T=1000K between the fitted 10 bar and 100 bar entries (a bracket
    with real curvature: k(10bar)=1.6481566 and k(100bar)=1.4476807,
    computed via the same Arrhenius formula pinned above), P=50 bar
    with frac=(log10(50)-log10(10))/(log10(100)-log10(10))=0.69897:

        correct (log-linear):
            log10(k) = log10(1.6481566) + frac*(log10(1.4476807)-log10(1.6481566))
            k = 1.5053189 cm3/mol/s
        wrong (linear-in-k, same frac):
            k = 1.6481566 + frac*(1.4476807-1.6481566) = 1.5080300 cm3/mol/s

    The two differ by ~0.18% -- small (the bracket is nearly flat) but
    well above float noise, and unambiguously distinguishes the two
    formulas rather than merely restating one of them.
    """
    result = evaluate_plog(1000.0, 50.0, entries=_PLOG_ENTRIES)
    assert result.k == pytest.approx(1.5053188741906032, rel=1e-6)

    k_at_10 = modified_arrhenius_k(1000.0, a=51368.2, n=2.42166, ea_kj_mol=225.117)
    k_at_100 = modified_arrhenius_k(1000.0, a=13579.5, n=2.58976, ea_kj_mol=224.788)
    frac = (math.log10(50.0) - math.log10(10.0)) / (
        math.log10(100.0) - math.log10(10.0)
    )
    wrong_k_linear_in_k = k_at_10 + frac * (k_at_100 - k_at_10)
    assert result.k != pytest.approx(wrong_k_linear_in_k, rel=1e-5)


def test_plog_flat_extrapolation_below_and_above_table_range() -> None:
    """Outside [0.01, 100] bar, PLOG uses the nearest single bracketing
    Arrhenius expression unchanged (flat extrapolation), flagged
    in_range=False -- never log-log extrapolated past the table."""
    below = evaluate_plog(1000.0, 0.001, entries=_PLOG_ENTRIES)
    at_min = evaluate_plog(1000.0, 0.01, entries=_PLOG_ENTRIES)
    assert below.in_range is False
    assert at_min.in_range is True
    assert below.k == pytest.approx(at_min.k)

    above = evaluate_plog(1000.0, 1000.0, entries=_PLOG_ENTRIES)
    at_max = evaluate_plog(1000.0, 100.0, entries=_PLOG_ENTRIES)
    assert above.in_range is False
    assert at_max.in_range is True
    assert above.k == pytest.approx(at_max.k)


def test_plog_in_range_considers_temperature_not_only_pressure() -> None:
    """Live-reachable regression: a PLOG record whose table is only
    ever fit to [300, 2000] K must not report in_range=True for a
    temperature outside that range merely because the *pressure*
    happens to fall inside the table's pressure span. Before this was
    fixed, ``evaluate_plog`` had no ``tmin_k``/``tmax_k`` parameter at
    all and computed ``in_range`` from pressure alone -- so
    nkin_lsoxdf26irod3nqr6tgjp4tlt4 (stored 300-2000K) returned
    ``in_range: true`` for 5000 K, 3000 K, 250 K and 100 K at 1 bar,
    while the Chebyshev fit of the SAME physical channel correctly
    flagged the same points as extrapolated -- two representations of
    one rate disagreeing about validity, with the PLOG one wrong.

    Checked here at 2001 K / 299 K (just outside each bound) and at
    exactly 2000 K / 300 K (the inclusive boundary itself), all at
    P=1 bar (comfortably inside the table's pressure span, so pressure
    contributes nothing to in_range in this test -- isolating the
    temperature axis specifically).
    """
    just_above_tmax = evaluate_plog(
        2001.0, 1.0, entries=_PLOG_ENTRIES, tmin_k=300.0, tmax_k=2000.0
    )
    just_below_tmin = evaluate_plog(
        299.0, 1.0, entries=_PLOG_ENTRIES, tmin_k=300.0, tmax_k=2000.0
    )
    at_tmax = evaluate_plog(
        2000.0, 1.0, entries=_PLOG_ENTRIES, tmin_k=300.0, tmax_k=2000.0
    )
    at_tmin = evaluate_plog(
        300.0, 1.0, entries=_PLOG_ENTRIES, tmin_k=300.0, tmax_k=2000.0
    )
    assert just_above_tmax.in_range is False
    assert just_below_tmin.in_range is False
    assert at_tmax.in_range is True
    assert at_tmin.in_range is True

    # The value is still computed at the out-of-range points (never
    # refused outright), matching the module's general extrapolation
    # contract.
    assert math.isfinite(just_above_tmax.k) and just_above_tmax.k > 0
    assert math.isfinite(just_below_tmin.k) and just_below_tmin.k > 0


def test_plog_in_range_temperature_unbounded_when_bounds_not_supplied() -> None:
    """When the caller has no ``tmin_k``/``tmax_k`` to pass (the
    record never recorded them), the temperature axis is treated as
    unbounded -- never the reason a point is refused or flagged -- so a
    genuinely wild temperature is still judged purely on pressure."""
    result = evaluate_plog(50000.0, 1.0, entries=_PLOG_ENTRIES)
    assert result.in_range is True  # 1 bar is within [0.01, 100]


def test_plog_duplicate_entries_with_disagreeing_a_units_refuses() -> None:
    """Two entries sharing one fitted pressure must not be silently
    summed if they disagree about what unit their sum would even be
    in -- a guard the evaluator owns, since the invariant is enforced
    on ingestion by only one ingester and nothing prevents a caller of
    this pure function from handing it inconsistent entries directly."""
    with pytest.raises(KineticsEvaluationError):
        evaluate_plog(
            1000.0,
            1.0,
            entries=[
                PlogEntry(
                    pressure_bar=1.0, a=1e10, n=0.0, ea_kj_mol=50.0,
                    a_units="cm3_mol_s",
                ),
                PlogEntry(
                    pressure_bar=1.0, a=1e10, n=0.0, ea_kj_mol=50.0,
                    a_units="cm3_molecule_s",
                ),
            ],
        )
    # Agreeing (or unrecorded) a_units must not be refused.
    ok = evaluate_plog(
        1000.0,
        1.0,
        entries=[
            PlogEntry(
                pressure_bar=1.0, a=1e10, n=0.0, ea_kj_mol=50.0,
                a_units="cm3_mol_s",
            ),
            PlogEntry(
                pressure_bar=1.0, a=1e10, n=0.0, ea_kj_mol=50.0,
                a_units="cm3_mol_s",
            ),
        ],
    )
    assert math.isfinite(ok.k)


def test_plog_negative_duplicate_a_refuses_with_coded_error_not_a_crash() -> None:
    """A Chemkin DUPLICATE pair fitting curvature with a negative
    pre-exponential factor is a standard, uploadable construct (the
    schema places no sign constraint on ``a``). If the two entries at
    one bracket pressure sum to a non-positive rate coefficient, taking
    log10 of it is undefined -- this must be refused with
    KineticsEvaluationError (which the service layer turns into a
    proper coded 422), never let ``math.log10`` raise an uncaught
    ``ValueError: math domain error`` that surfaces as a generic,
    uncoded validation failure."""
    entries = [
        # At 1 bar: a large positive term plus a nearly-cancelling
        # negative term drives the summed k slightly negative.
        PlogEntry(pressure_bar=1.0, a=1.0e10, n=0.0, ea_kj_mol=50.0),
        PlogEntry(pressure_bar=1.0, a=-1.05e10, n=0.0, ea_kj_mol=50.0),
        # A normal, single positive entry at the other bracket pressure
        # so interpolation is actually attempted (not just a
        # single-fitted-pressure lookup).
        PlogEntry(pressure_bar=10.0, a=1.0e10, n=0.0, ea_kj_mol=50.0),
    ]
    with pytest.raises(KineticsEvaluationError):
        evaluate_plog(1000.0, 5.0, entries=entries)


def test_chebyshev_and_plog_reject_infinite_temperature_identically() -> None:
    """Chebyshev and PLOG must behave the same way when asked to
    evaluate at a non-finite temperature: both refuse. Before this was
    fixed, ``not (t > 0)`` (True for ``+inf`` being > 0) let ``inf``
    through Chebyshev's own bound check silently, while PLOG's
    Arrhenius evaluator already rejected it via its own
    ``math.isfinite`` guard -- an asymmetry between the two model
    kinds for the identical malformed input."""
    with pytest.raises(KineticsEvaluationError):
        evaluate_chebyshev(
            float("inf"), 1.0, coefficients=_CHEB_MATRIX, **_CHEB_BOUNDS
        )
    with pytest.raises(KineticsEvaluationError):
        evaluate_plog(float("inf"), 1.0, entries=_PLOG_ENTRIES)


def test_plog_duplicate_pressure_entries_are_summed() -> None:
    """Two entries sharing one fitted pressure (the entry_index
    discriminator) contribute additively -- the Chemkin DUPLICATE PLOG
    convention -- not by overwrite or average."""
    single = evaluate_plog(
        1000.0,
        1.0,
        entries=[PlogEntry(pressure_bar=1.0, a=1e10, n=0.0, ea_kj_mol=50.0)],
    )
    duplicated = evaluate_plog(
        1000.0,
        1.0,
        entries=[
            PlogEntry(pressure_bar=1.0, a=1e10, n=0.0, ea_kj_mol=50.0),
            PlogEntry(pressure_bar=1.0, a=1e10, n=0.0, ea_kj_mol=50.0),
        ],
    )
    assert duplicated.k == pytest.approx(2.0 * single.k)


def test_plog_empty_entries_refuses_rather_than_returns_zero() -> None:
    with pytest.raises(KineticsEvaluationError):
        evaluate_plog(1000.0, 1.0, entries=[])


def test_arrhenius_rejects_non_positive_temperature() -> None:
    with pytest.raises(KineticsEvaluationError):
        modified_arrhenius_k(0.0, a=1.0, n=0.0, ea_kj_mol=0.0)
    with pytest.raises(KineticsEvaluationError):
        modified_arrhenius_k(-10.0, a=1.0, n=0.0, ea_kj_mol=0.0)
