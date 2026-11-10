"""The backend parsers and upload validation read a deck the same way.

Validation judges a deposit with ``tckdb_schemas.hessian_method_detection``;
the workflow later stores the parameter rows the backend parsers emit. For
the stored tau to equal the judged tau the two must agree on every deck.
"""

from __future__ import annotations

import pytest
from tckdb_schemas.hessian_method_detection import detect_hessian_method
from tckdb_schemas.stationary_point import resolve_tau_from_parameters

from app.services.calculation_parameter_extraction import _parse_gaussian_text
from app.services.orca_parameter_parser import parse_orca_log

_GAUSSIAN_DECKS = [
    "#P B3LYP/6-31G(d) Freq=Numer\n\nt\n\n0 1\nH 0 0 0\n",
    "# B3LYP/6-31G(d) freq=numerical\n\nt\n",
    "# B3LYP/6-31G(d) Freq=(Numer,NoRaman)\n\nt\n",
    "# B3LYP/6-31G(d) Freq=(NoRaman,EnOnly)\n\nt\n",
    "# B3LYP/6-31G(d) Freq\n\nt\n",
    "# B3LYP/6-31G(d) Opt=(Tight) Freq=NoRaman\n\nt\n",
    "# B3LYP/6-31G(d) Opt=(Numer)\n\nt\n",
    "%mem=2GB\n#N wB97XD/def2TZVP Opt Freq=EnOnly IOp(2/9=2000)\n\nt\n",
    "# B3LYP/6-31G(d) Freq=(EnOnly,Numer)\n\nt\n",
    "# B3LYP/6-31G(d) Freq=(Numer,EnOnly)\n\nt\n",
    "# B3LYP/6-31G(d) freq(numer)\n\nt\n",
    "# B3LYP/6-31G(d) freq (numer)\n\nt\n",
    "# B3LYP/6-31G(d) freq (numer,noraman)\n\nt\n",
    "# B3LYP/6-31G(d) Frequency=Numer\n\nt\n",
    "# B3LYP/6-31G(d) freq = numer\n\nt\n",
    "# opt freq=(numer)\n\nt\n",
    "# B3LYP/6-31G(d) freqchk=numer\n\nt\n",
    "# B3LYP/6-31G(d) anharmonicfreq=numer\n\nt\n",
    "# B3LYP/6-31G(d) opt=calcnumerfc freq\n\nt\n",
    "# B3LYP/6-31G(d) freq scf=(numer)\n\nt\n",
    "# B3LYP/6-31G(d) freq IOp(7/33=1)\n\nt\n",
    "# B3LYP/6-31G(d) freq opt (numer)\n\nt\n",
]
_ORCA_DECKS = [
    "! B3LYP def2-SVP NumFreq\n* xyz 0 1\nH 0 0 0\n*\n",
    "! B3LYP def2-SVP AnFreq\n",
    "! B3LYP def2-SVP Opt Freq\n",
    "! B3LYP def2-SVP Opt # NumFreq later\n",
    "! B3LYP\n! def2-SVP NumFreq\n",
    "! B3LYP NumFreq AnFreq\n",
    "! B3LYP def2-SVP NumFreq NumGrad\n",
    "! B3LYP def2-SVP NumGrad\n! Opt NumFreq\n",
    "! B3LYP def2-SVP Opt NumGrad\n",
    "! B3LYP AnFreq NumFreq\n",
    "! B3LYP AnFreq NumFreq NumGrad\n",
    "! B3LYP Freq # note # NumFreq\n",
    "! B3LYP Freq # NumFreq\n",
    "! B3LYP Freq # a # AnFreq # b # NumFreq\n",
]


def _stored_method(rows):
    return resolve_tau_from_parameters(
        (r.get("canonical_key"), r.get("canonical_value")) for r in rows
    ).hessian_method


@pytest.mark.parametrize("deck", _GAUSSIAN_DECKS)
def test_gaussian_parser_rows_agree_with_the_validation_reading(deck) -> None:
    assert _stored_method(_parse_gaussian_text(deck)["parameters"]) == detect_hessian_method("gaussian", deck)


@pytest.mark.parametrize("deck", _ORCA_DECKS)
def test_orca_parser_rows_agree_with_the_validation_reading(deck) -> None:
    assert _stored_method(parse_orca_log(text=deck)["parameters"]) == detect_hessian_method("orca", deck)


_KEY = "freq.hessian_method"


def _hessian_rows(rows):
    return [r for r in rows if r.get("canonical_key") == _KEY]


@pytest.mark.parametrize(
    ("deck", "raw_key", "canonical"),
    [
        ("# B3LYP/6-31G(d) Freq=Numerical\n\nt\n", "Numerical", "finite_difference_gradient"),
        ("# B3LYP/6-31G(d) Freq=(NoRaman,Numer)\n\nt\n", "Numer", "finite_difference_gradient"),
        ("# B3LYP/6-31G(d) Freq=(EnOnly,Numer)\n\nt\n", "EnOnly", "finite_difference_energy"),
        ("# B3LYP/6-31G(d) Freq=(Numer,EnOnly)\n\nt\n", "EnOnly", "finite_difference_energy"),
        ("# B3LYP/6-31G(d) Freq(Numer)\n\nt\n", "Numer", "finite_difference_gradient"),
        ("# B3LYP/6-31G(d) Frequency = Numer\n\nt\n", "Numer", "finite_difference_gradient"),
    ],
)
def test_gaussian_rows_keep_the_decks_own_text_and_one_carries_the_canonical_value(deck, raw_key, canonical) -> None:
    parsed = _parse_gaussian_text(deck)
    (row,) = _hessian_rows(parsed["parameters"])
    assert (row["raw_key"], row["canonical_value"]) == (raw_key, canonical)
    # No row's raw text contradicts a canonical value: the losing option keeps its text, not a canonical one.
    for other in parsed["parameters"]:
        if other.get("section") == "freq" and other is not row:
            assert other.get("canonical_key") != _KEY


def test_the_freq_section_snapshot_is_as_written() -> None:
    parsed = _parse_gaussian_text("# B3LYP/6-31G(d) Freq=(NoRaman,EnOnly,Numer)\n\nt\n")
    assert parsed["parameters_json"]["sections"]["freq"] == {
        "NoRaman": "true",
        "EnOnly": "true",
        "Numer": "true",
    }


@pytest.mark.parametrize(
    ("deck", "raw_key", "canonical"),
    [
        ("! B3LYP NumFreq AnFreq\n", "NumFreq", "finite_difference_gradient"),
        ("! B3LYP AnFreq NumFreq\n", "NumFreq", "finite_difference_gradient"),
        ("! B3LYP numfreq NumGrad\n", "numfreq NumGrad", "finite_difference_energy"),
        ("! B3LYP AnFreq NumFreq NumGrad\n", "NumFreq NumGrad", "finite_difference_energy"),
        ("! B3LYP AnFreq\n", "AnFreq", "analytic"),
    ],
)
def test_orca_emits_one_hessian_row_whose_raw_text_matches_its_canonical_value(deck, raw_key, canonical) -> None:
    rows = parse_orca_log(text=deck)["parameters"]
    (row,) = _hessian_rows(rows)
    assert (row["raw_key"], row["canonical_value"]) == (raw_key, canonical)
    # No other freq-section row carries a contradicting value.
    assert [r for r in rows if r.get("section") == "freq"] == [row]
