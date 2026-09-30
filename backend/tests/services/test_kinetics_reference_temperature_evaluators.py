"""Every consumer that evaluates k(T) from a stored row honours ``t0_k`` (#620).

A row stores ``a``, ``n``, ``ea_kj_mol`` and ``t0_k`` meaning
``k = A * (T/T0)**n * exp(-Ea/RT)``. Cantera's ``ArrheniusRate`` and CHEMKIN's
``A n Ea`` line have no reference temperature, so a consumer that copies ``a``
across unchanged is wrong by ``T0**n``: for A = 1e10, n = 2, T0 = 298 that is a
factor of 88 804.

The shared fixture is the one the issue named: A = 1e10, n = 2, Ea = 0 (so the
exponential is exactly 1 and the ratio is pure algebra), evaluated at 1000 K
with T0 = 298 and with T0 = 1. The expected ratio is ``1 / 298**2``, derived
here and not read back from the code under test.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from app.chemistry.arrhenius import GAS_CONSTANT_J_MOL_K, a_at_unit_t0, arrhenius_k
from app.db.models.kinetics import Kinetics

A = 1.0e10
N = 2.0
T = 1000.0
T0 = 298.0

#: k(T0=298) / k(T0=1) at any T when Ea = 0: [A (T/298)^2] / [A T^2] = 1 / 298^2.
EXPECTED_RATIO = 1.0 / 298.0**2


def test_arrhenius_k_ratio_is_the_algebraic_one():
    ratio = arrhenius_k(T, a=A, n=N, ea_kj_mol=0.0, t0_k=T0) / arrhenius_k(
        T, a=A, n=N, ea_kj_mol=0.0, t0_k=1.0
    )
    assert ratio == pytest.approx(EXPECTED_RATIO, rel=1e-12)


def test_arrhenius_k_matches_a_hand_computed_value_with_an_activation_energy():
    """Independent of the ratio trick: A (T/T0)^n exp(-Ea/RT) written out."""
    ea = 50.0
    expected = A * (T / T0) ** N * math.exp(-ea * 1000.0 / (GAS_CONSTANT_J_MOL_K * T))
    assert arrhenius_k(T, a=A, n=N, ea_kj_mol=ea, t0_k=T0) == pytest.approx(expected, rel=1e-12)


def test_a_at_unit_t0_is_the_same_rate_written_at_one_kelvin():
    """``A/T0**n`` at T0 = 1 K reproduces the original rate at every T."""
    a_one = a_at_unit_t0(A, N, T0)
    for temperature in (300.0, 1000.0, 2500.0):
        assert arrhenius_k(temperature, a=a_one, n=N, ea_kj_mol=12.0) == pytest.approx(
            arrhenius_k(temperature, a=A, n=N, ea_kj_mol=12.0, t0_k=T0), rel=1e-12
        )


def test_an_unset_t0_is_one_kelvin_so_old_rows_and_unflushed_rows_are_unchanged():
    assert a_at_unit_t0(A, N, None) == A
    assert a_at_unit_t0(A, N, 1.0) == A
    assert a_at_unit_t0(A, None, T0) == A  # n = None is n = 0: T0 cancels


@pytest.mark.parametrize("bad", [0.0, -1.0, math.inf, math.nan])
def test_a_non_temperature_t0_is_refused_not_propagated(bad):
    with pytest.raises(ValueError):
        a_at_unit_t0(A, N, bad)


def _row(t0_k: float) -> Kinetics:
    """A D3-eligible elementary rate, as the consistency engine reads it."""
    return Kinetics(
        model_kind="modified_arrhenius",
        a=A,
        a_units="cm3_mol_s",
        n=N,
        t0_k=t0_k,
        ea_kj_mol=0.0,
        tmin_k=300.0,
        tmax_k=2000.0,
        is_third_body=False,
        pressure_context="high_p_limit",
        degeneracy_convention="already_applied",
    )


def test_the_thermo_kinetics_check_evaluates_k_at_t0():
    """D3 builds a Cantera rate from the row; Cantera has no T0 of its own."""
    ct = pytest.importorskip("cantera")
    from app.services.consistency.kinetics import _rate

    rate_t0, reason_t0 = _rate(_row(T0), 2, ct)
    rate_one, reason_one = _rate(_row(1.0), 2, ct)
    assert reason_t0 is None and reason_one is None
    assert float(rate_t0(T)) / float(rate_one(T)) == pytest.approx(EXPECTED_RATIO, rel=1e-9)


def test_the_thermo_kinetics_check_is_unchanged_for_a_row_at_one_kelvin():
    """No behaviour change for every existing row: T0 = 1 K gives the old rate."""
    ct = pytest.importorskip("cantera")
    from app.services.consistency.kinetics import _rate

    rate, _ = _rate(_row(1.0), 2, ct)
    # cm3/mol/s -> m3/kmol/s is 1e-3; Ea = 0.
    assert float(rate(T)) == pytest.approx(A * T**N * 1e-3, rel=1e-9)


def _chemkin_main_line(kinetics: Kinetics) -> list[str]:
    from app.services.scientific_read.chemkin_serialize import ChemkinOptions, _kinetics_lines

    rr = SimpleNamespace(
        reaction=SimpleNamespace(reversible=False),
        reactant_refs=["a", "b"],
        product_refs=["c"],
    )
    blocks = _kinetics_lines(
        rr,
        SimpleNamespace(kinetics=kinetics),
        {"a": "A", "b": "B", "c": "C"},
        {},
        ChemkinOptions(energy_units="kj/mol"),
    )
    assert len(blocks) == 1
    return blocks[0]


def _chemkin_a(lines: list[str]) -> float:
    return float(lines[0].split("=>")[1].split()[-3].replace("D", "E"))


def test_the_chemkin_export_writes_the_a_at_one_kelvin():
    """CHEMKIN's line is ``A T**n``: a row at T0 = 298 must be written as
    ``A / 298**n`` or every consumer of the mechanism file is off by ``T0**n``."""
    a_t0 = _chemkin_a(_chemkin_main_line(_row(T0)))
    a_one = _chemkin_a(_chemkin_main_line(_row(1.0)))
    assert a_one == pytest.approx(A, rel=1e-4)
    assert a_t0 / a_one == pytest.approx(EXPECTED_RATIO, rel=1e-4)


def test_the_chemkin_export_of_a_falloff_rate_rescales_its_high_pressure_limit():
    """For a falloff record the main line is k-infinity, which T0 applies to."""
    from app.db.models.kinetics import KineticsFalloff

    row = _row(T0)
    row.model_kind = "troe"
    row.falloff = KineticsFalloff(
        low_a=1.0e18,
        low_a_units="cm6_mol2_s",
        low_n=-1.5,  # non-zero, or rescaling the LOW line by T0 would change nothing
        troe_alpha=0.5,
        troe_t3=100.0,
        troe_t1=1000.0,
    )
    lines = _chemkin_main_line(row)
    assert _chemkin_a(lines) == pytest.approx(A * EXPECTED_RATIO, rel=1e-4)
    # The LOW line is at T0 = 1 K and is NOT rescaled.
    low = next(line for line in lines if line.strip().startswith("LOW"))
    low_a, low_n = (float(token) for token in low.split("/")[1].split()[:2])
    assert low_a == pytest.approx(1.0e18, rel=1e-4)
    assert low_n == pytest.approx(-1.5)


def test_a_default_t0_leaves_every_stored_digest_unchanged_and_a_departure_changes_it():
    """Adding a column to ``kinetics`` must not restale reviews of existing rows.

    Two digests are built from every mapped column: the advisory-consistency
    input hash and the reproducibility-assessment target snapshot. At
    T0 = 1 K (every stored row) ``t0_k`` is left out of both, so the digest is
    what it was before the column existed; at any other T0 it is in, so the
    digest differs, because the row then means something else.
    """
    from app.services.consistency.core import encoded, snapshot
    from app.services.reproducibility_rubric import _mapped_columns

    assert "t0_k" not in snapshot(_row(1.0))
    assert "t0_k" not in _mapped_columns(_row(1.0))
    assert "t0_k" not in snapshot(Kinetics(a=A, n=N))  # an unflushed row: None
    assert snapshot(_row(T0))["t0_k"] == T0
    assert _mapped_columns(_row(T0))["t0_k"] == T0
    assert encoded(snapshot(_row(T0))) != encoded(snapshot(_row(1.0)))
