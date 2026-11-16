"""The CHEMKIN export writes every rate parameter to nine significant figures.

Each block type the exporter writes is serialized from a stand-in record, the
numbers are parsed back out of the text, and they are compared with the stored
(converted) values. A Cantera k(T) check then compares the loaded file with the
stored fit.
"""

from __future__ import annotations

import math
import re
from types import SimpleNamespace

import pytest

from app.db.models.common import ArrheniusAUnits, KineticsModelKind
from app.services.scientific_read.chemkin_serialize import _KJ_PER_MOL_TO_CAL_PER_MOL
from tests.services.scientific_read.test_chemkin_serialize_kinetics import (
    _arr_entry,
    _forward_rate_sum,
    _kinetics,
    _reaction,
    _reaction_lines,
    _serialize,
    _species,
    _third_body_species,
)

#: Nine significant figures: half a unit of the ninth digit is at most 5e-9 relative.
NINE_FIGURES = 5.0e-9
#: kJ/mol; the gas constant in kJ/mol/K.
R_KJ = 8.314462618e-3

A_STORED = 1459.78
N_STORED = 3.46761
EA_KJ = 8.50629


def _numbers(line: str) -> list[float]:
    """Every number on a block line, in order (the equation's species names are not numbers)."""
    return [float(t) for t in re.findall(r"[-+]?\d\.\d+E[-+]\d+", line)]


def _body(species, reaction):
    export, _ = _serialize(species, [reaction])
    return _reaction_lines(export.files["chem.inp"])


def _hoh_species():
    return [_species(1, "[H]", "H"), _species(2, "[O][O]", "O2"), _species(3, "[O]O", "HO2")]


def _close(written, stored):
    assert written == pytest.approx(stored, rel=NINE_FIGURES, abs=0.0)


def _plain(t0_k=1.0):
    return _kinetics(
        model_kind=KineticsModelKind.modified_arrhenius,
        a=A_STORED,
        a_units=ArrheniusAUnits.cm3_mol_s,
        n=N_STORED,
        t0_k=t0_k,
        ea_kj_mol=EA_KJ,
    )


def test_the_arrhenius_line_reproduces_the_stored_parameters():
    body = _body(_hoh_species(), _reaction(["H-e", "O2-e"], ["HO2-e"], _plain(), ref="R1"))
    a, n, ea = _numbers(body[0])
    _close(a, A_STORED)
    _close(n, N_STORED)
    _close(ea, EA_KJ * _KJ_PER_MOL_TO_CAL_PER_MOL)


def test_a_reference_temperature_is_applied_before_the_digits_are_cut():
    t0 = 1000.0
    body = _body(_hoh_species(), _reaction(["H-e", "O2-e"], ["HO2-e"], _plain(t0), ref="R1"))
    a, n, ea = _numbers(body[0])
    _close(a, A_STORED / t0**N_STORED)
    _close(n, N_STORED)
    _close(ea, EA_KJ * _KJ_PER_MOL_TO_CAL_PER_MOL)


def test_every_multi_arrhenius_term_reproduces_its_stored_parameters():
    terms = [(1.23456789e12, -0.987654321, 12.3456789), (9.87654321e9, 1.23456789, 0.0123456789)]
    k = _kinetics(
        model_kind=KineticsModelKind.multi_arrhenius,
        a_units=ArrheniusAUnits.cm3_mol_s,
        arrhenius_entries=[_arr_entry(i + 1, *t) for i, t in enumerate(terms)],
    )
    body = _body(_hoh_species(), _reaction(["H-e", "O2-e"], ["HO2-e"], k, ref="R1"))
    lines = [ln for ln in body if "<=>" in ln]
    assert len(lines) == 2
    for line, (a, n, ea) in zip(lines, terms, strict=True):
        written = _numbers(line)
        _close(written[0], a)
        _close(written[1], n)
        _close(written[2], ea * _KJ_PER_MOL_TO_CAL_PER_MOL)


def test_falloff_low_troe_and_efficiencies_reproduce_the_stored_values():
    falloff = SimpleNamespace(
        low_a=1.23456789e20, low_a_units=ArrheniusAUnits.cm6_mol2_s, low_n=-1.23456789, low_ea_kj_mol=2.3456789,
        troe_alpha=0.123456789, troe_t3=123.456789, troe_t1=1234.56789, troe_t2=7654.32109,
        sri_a=None, sri_b=None, sri_c=None, sri_d=None, sri_e=None,
    )
    k = _kinetics(
        model_kind=KineticsModelKind.troe, a=1.0e13, a_units=ArrheniusAUnits.cm3_mol_s, n=0.0, ea_kj_mol=0.0,
        falloff=falloff,
        third_body_efficiencies=[SimpleNamespace(collider_species_id=4, efficiency=6.54321987)],
    )
    body = _body(_third_body_species(), _reaction(["H-e", "O2-e"], ["HO2-e"], k, ref="R1"))
    low = _numbers(next(ln for ln in body if ln.startswith("    LOW /")))
    _close(low[0], 1.23456789e20)
    _close(low[1], -1.23456789)
    _close(low[2], 2.3456789 * _KJ_PER_MOL_TO_CAL_PER_MOL)
    troe = _numbers(next(ln for ln in body if ln.startswith("    TROE /")))
    for written, stored in zip(troe, (0.123456789, 123.456789, 1234.56789, 7654.32109), strict=True):
        _close(written, stored)
    efficiency = next(ln for ln in body if "H2O1/" in ln)
    _close(_numbers(efficiency)[0], 6.54321987)


def test_sri_coefficients_reproduce_the_stored_values():
    stored = (0.123456789, 123.456789, 1234.56789, 1.23456789, 0.987654321)
    falloff = SimpleNamespace(
        low_a=1.0e20, low_a_units=ArrheniusAUnits.cm6_mol2_s, low_n=0.0, low_ea_kj_mol=0.0,
        troe_alpha=None, troe_t3=None, troe_t1=None, troe_t2=None,
        sri_a=stored[0], sri_b=stored[1], sri_c=stored[2], sri_d=stored[3], sri_e=stored[4],
    )
    k = _kinetics(
        model_kind=KineticsModelKind.sri, a=1.0e13, a_units=ArrheniusAUnits.cm3_mol_s, n=0.0, ea_kj_mol=0.0,
        falloff=falloff,
    )
    body = _body(_third_body_species(), _reaction(["H-e", "O2-e"], ["HO2-e"], k, ref="R1"))
    written = _numbers(next(ln for ln in body if ln.startswith("    SRI /")))
    for w, s in zip(written, stored, strict=True):
        _close(w, s)


def test_plog_pressure_and_parameters_reproduce_the_stored_values():
    rows = [(0.0123456789, 1.23456789e12, -0.123456789, 4.56789123), (12.3456789, 2.34567891e12, 1.23456789, 0.0)]
    k = _kinetics(
        model_kind=KineticsModelKind.plog, a=1.0e13, a_units=ArrheniusAUnits.cm3_mol_s, n=0.0, ea_kj_mol=0.0,
        plog_entries=[
            SimpleNamespace(entry_index=i + 1, pressure_bar=p, a=a, a_units=ArrheniusAUnits.cm3_mol_s, n=n, ea_kj_mol=ea)
            for i, (p, a, n, ea) in enumerate(rows)
        ],
    )
    body = _body(_hoh_species(), _reaction(["H-e", "O2-e"], ["HO2-e"], k, ref="R1"))
    lines = [ln for ln in body if ln.startswith("    PLOG /")]
    assert len(lines) == 2
    for line, (p, a, n, ea) in zip(lines, rows, strict=True):
        written = _numbers(line)
        _close(written[0], p / 1.01325)
        _close(written[1], a)
        _close(written[2], n)
        _close(written[3], ea * _KJ_PER_MOL_TO_CAL_PER_MOL)


def test_chebyshev_limits_and_coefficients_reproduce_the_stored_values():
    coefficients = [[8.12345678, 0.123456789], [-0.234567891, 0.0123456789]]
    cheb = SimpleNamespace(
        n_temperature=2, n_pressure=2, tmin_k=299.123456, tmax_k=2000.98765, pmin_bar=0.0987654321, pmax_bar=98.7654321,
        coefficients=coefficients,
    )
    k = _kinetics(model_kind=KineticsModelKind.chebyshev, chebyshev=cheb)
    body = _body(_hoh_species(), _reaction(["H-e", "O2-e"], ["HO2-e"], k, ref="R1"))
    tcheb = _numbers(next(ln for ln in body if ln.startswith("    TCHEB /")))
    _close(tcheb[0], 299.123456)
    _close(tcheb[1], 2000.98765)
    pcheb = _numbers(next(ln for ln in body if ln.startswith("    PCHEB /")))
    _close(pcheb[0], 0.0987654321 / 1.01325)
    _close(pcheb[1], 98.7654321 / 1.01325)
    cheb_line = next(ln for ln in body if ln.startswith("    CHEB /"))
    for written, stored in zip(_numbers(cheb_line), [v for row in coefficients for v in row], strict=True):
        _close(written, stored)


@pytest.mark.parametrize("t0_k", [1.0, 300.0])
@pytest.mark.parametrize("temperature", [300.0, 1000.0, 2000.0])
def test_cantera_k_from_the_file_is_the_stored_fit(t0_k, temperature):
    """k = A (T/T0)^n exp(-Ea/RT) from the stored row against Cantera's evaluation of the written file."""
    rxn = _reaction(["H-e", "O2-e"], ["HO2-e"], _plain(t0_k), ref="R1")
    export, _ = _serialize(_hoh_species(), [rxn])
    gas, loaded = _forward_rate_sum(export.files, {"H1", "O2"}, temperature=temperature)
    stored = A_STORED * (temperature / t0_k) ** N_STORED * math.exp(-EA_KJ / (R_KJ * temperature))
    # cm3 mol-1 s-1 -> m3 kmol-1 s-1
    assert loaded == pytest.approx(stored * 1.0e-3, rel=1.0e-8)
