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
