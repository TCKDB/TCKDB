"""The network CHEMKIN export writes PLOG and Chebyshev values to nine significant figures."""

from __future__ import annotations

import math
import os
import re
import tempfile
from collections import Counter
from types import SimpleNamespace

import cantera as ct
import pytest
from cantera import ck2yaml

from app.services.scientific_read.chemkin_serialize import _nasa_card
from tests.api.scientific.test_api_network_export import A_, export, manifest_of
from tests.services.network_selection._protocol import protocol
from tests.services.network_selection._world import add_solve, build_world, fit_spec
from tests.services.scientific_read._chemkin_precision import written_rate_bound


@pytest.fixture
def world(db_session):
    return build_world(db_session)


#: (pressure bar, A cm3 mol-1 s-1, n, Ea kJ/mol), each with more than nine significant figures.
FULL_PLOG_ROWS = [
    (0.123456789012, 1.23456789012e13, -0.123456789012, 41.2345678901),
    (1.0, 2.34567890123e13, 1.23456789012, 42.3456789012),
    (9.87654321098, 3.45678901234e13, 0.0123456789012, 0.0123456789012),
]
KJ_PER_MOL_TO_CAL_PER_MOL = 1000.0 / 4.184


def _numbers_in(line: str) -> list[float]:
    return [float(t) for t in re.findall(r"[-+]?\d\.\d+E[-+]\d+", line)]


def _close(written: float, stored: float) -> None:
    assert written == pytest.approx(stored, rel=5.0e-9, abs=0.0)


def _chem_inp(client, db_session, world, **spec) -> str:
    solve = add_solve(db_session, world, fits=[fit_spec("assoc", **spec)], protocol=protocol(A_))
    det, fit = solve._dets["d_assoc"].public_ref, solve._fits[0].public_ref
    return export(client, world, manifest_of(client, world), det, [fit], format="chemkin").json()["files"]["chem.inp"]


def _lines(text: str, prefix: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith(prefix)]


def test_plog_rows_are_written_to_nine_significant_figures(client, db_session, world):
    text = _chem_inp(client, db_session, world, plog=FULL_PLOG_ROWS)
    lines = _lines(text, "    PLOG /")
    assert len(lines) == 3
    for line, (p, a, n, ea) in zip(lines, FULL_PLOG_ROWS, strict=True):
        written = _numbers_in(line)
        _close(written[0], p / 1.01325)
        _close(written[1], a)
        _close(written[2], n)
        _close(written[3], ea * KJ_PER_MOL_TO_CAL_PER_MOL)


def test_chebyshev_limits_and_coefficients_are_written_to_nine_significant_figures(client, db_session, world):
    coefficients = [[1.23456789012, 0.512345678901], [-0.256789012345, 0.125678901234]]
    text = _chem_inp(
        client, db_session, world, model="chebyshev", rep="cheb", plog=[], coeffs=coefficients,
        tmin=301.234567891, tmax=1999.87654321, pmin=0.0123456789012, pmax=98.7654321098,
    )
    tcheb = _numbers_in(_lines(text, "    TCHEB /")[0])
    _close(tcheb[0], 301.234567891)
    _close(tcheb[1], 1999.87654321)
    pcheb = _numbers_in(_lines(text, "    PCHEB /")[0])
    _close(pcheb[0], 0.0123456789012 / 1.01325)
    _close(pcheb[1], 98.7654321098 / 1.01325)
    cheb = _numbers_in(_lines(text, "    CHEB /")[0])
    for written, stored in zip(cheb, [v for row in coefficients for v in row], strict=True):
        _close(written, stored)


@pytest.mark.parametrize("temperature", [300.0, 1000.0])
def test_cantera_k_of_a_plog_row_is_the_stored_fit_to_the_written_digits(client, db_session, world, temperature):
    text = _chem_inp(client, db_session, world, plog=FULL_PLOG_ROWS)
    species = [ln.split()[0] for ln in text.split("SPECIES", 1)[1].split("END", 1)[0].splitlines() if ln.strip()]
    coefficients = [3.5, 0.0, 0.0, 0.0, 0.0, -1000.0, 5.0]
    nasa = SimpleNamespace(
        t_low=200.0, t_high=3500.0, t_mid=1000.0,
        **{f"{k}{i}": v for k in "ab" for i, v in enumerate(coefficients, start=1)},
    )
    selected = SimpleNamespace(nasa=nasa, thermo=SimpleNamespace(phase="gas", reference_pressure_bar=1.01325))
    # The shared world's reaction is not element-balanced and only its rate is under test, so each species gets a
    # synthetic helium composition that balances the reaction.
    equation = next(ln for ln in text.splitlines() if "=>" in ln and not ln.startswith(("!", " "))).split("   ")[0]
    reactants = [t.strip() for t in equation.split("=>")[0].split("+")]
    cards: list[str] = []
    for name in species:
        cards += _nasa_card(name, Counter({"He": 1 if name in reactants else len(reactants)}), selected)
    text = re.sub(r"ELEMENTS\n.*?\nEND", "ELEMENTS\nHe\nEND", text, count=1, flags=re.S)
    therm = "THERMO ALL\n   200.000  1000.000  3500.000\n" + "\n".join(cards) + "\nEND\n"

    # the 1 bar row: Cantera evaluates a row's own pressure exactly
    _, a, n, ea_kj = FULL_PLOG_ROWS[1]
    a_text, n_text, ea_text = _lines(text, "    PLOG /")[1].split()[3:6]
    with tempfile.TemporaryDirectory() as tmp:
        for name, content in (("chem.inp", text), ("therm.dat", therm)):
            with open(os.path.join(tmp, name), "w") as fh:
                fh.write(content)
        out = os.path.join(tmp, "mech.yaml")
        ck2yaml.convert(
            input_file=os.path.join(tmp, "chem.inp"), thermo_file=os.path.join(tmp, "therm.dat"),
            out_name=out, quiet=True, permissive=False,
        )
        gas = ct.Solution(out)
    gas.TP = temperature, 1.0e5
    loaded = gas.forward_rate_constants[0]
    stored = a * temperature**n * math.exp(-ea_kj / (8.314462618e-3 * temperature))
    bound = written_rate_bound(a_text, n_text, ea_text, temperature)
    # The written 1 bar pressure rounds to 1 - 2.9e-10 bar, so Cantera blends in ~2e-9 of the next PLOG row at 300 K;
    # the bound's other terms cover it.
    # cm3 mol-1 s-1 -> m3 kmol-1 s-1
    assert abs(math.log(loaded / (stored * 1.0e-3))) <= bound
    assert bound < 2.0e-7
