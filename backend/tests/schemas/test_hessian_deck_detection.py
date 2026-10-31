"""What an input deck says about the frequency job's Hessian: the single owner."""

from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError
from tckdb_schemas.enums import HessianMethod
from tckdb_schemas.hessian_method_detection import (
    W_HESSIAN_METHOD_CONTRADICTS_INPUT_DECK,
    deck_hessian_method,
    detect_hessian_method,
)

_FD = HessianMethod.finite_difference_gradient


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("#P B3LYP/6-31G(d) Freq=Numer\n\nt\n\n0 1\nH 0 0 0\n", _FD),
        ("# B3LYP/6-31G(d) freq=numerical\n\nt\n", _FD),
        ("# B3LYP/6-31G(d) Freq=(Numer,NoRaman)\n\nt\n", _FD),
        ("# B3LYP/6-31G(d) Freq=(NoRaman,EnOnly)\n\nt\n", HessianMethod.finite_difference_energy),
        ("# B3LYP/6-31G(d) Freq\n\nt\n", None),
        ("# B3LYP/6-31G(d) Opt=(Tight) Freq=NoRaman\n\nt\n", None),
        ("# B3LYP/6-31G(d) Opt=(Numer)\n\nt\n", None),
    ],
)
def test_gaussian_route_directives(text, expected) -> None:
    assert detect_hessian_method("gaussian", text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("! B3LYP def2-SVP NumFreq\n* xyz 0 1\nH 0 0 0\n*\n", _FD),
        ("! B3LYP def2-SVP AnFreq\n", HessianMethod.analytic),
        ("! B3LYP def2-SVP Opt Freq\n", None),
        ("! B3LYP def2-SVP Opt # NumFreq later\n", None),
        ("! B3LYP\n! def2-SVP NumFreq\n", _FD),
        # NumFreq on numerical gradients is a Hessian from energies
        ("! B3LYP def2-SVP NumFreq NumGrad\n", HessianMethod.finite_difference_energy),
        ("! B3LYP def2-SVP NumGrad\n! Opt NumFreq\n", HessianMethod.finite_difference_energy),
        ("! B3LYP def2-SVP Opt NumGrad\n", None),
        ("! B3LYP def2-SVP AnFreq NumGrad\n", HessianMethod.analytic),
    ],
)
def test_orca_keywords(text, expected) -> None:
    assert detect_hessian_method("orca", text) is expected


def test_another_program_states_nothing() -> None:
    assert detect_hessian_method("molpro", "! NumFreq\n") is None


def _art(text: str, kind: str = "input") -> dict:
    class _A:
        pass

    a = _A()
    a.kind = kind
    a.content_base64 = base64.b64encode(text.encode()).decode()
    return a


def test_only_input_decks_count_and_a_later_deck_replaces_an_earlier_one() -> None:
    numfreq = "! B3LYP NumFreq\n"
    plain = "! B3LYP Freq\n"
    assert deck_hessian_method("ORCA", [_art(numfreq)]) is _FD
    assert deck_hessian_method("ORCA", [_art(numfreq, "output_log")]) is None
    assert deck_hessian_method("ORCA", [_art(numfreq), _art(plain)]) is None
    assert deck_hessian_method("ORCA", [_art(plain), _art(numfreq)]) is _FD
    assert deck_hessian_method("psi4", [_art(numfreq)]) is None


def test_a_deck_with_bad_base64_says_nothing() -> None:
    bad = _art("x")
    bad.content_base64 = "not base64!!"
    assert deck_hessian_method("ORCA", [bad]) is None


def _bundle_calc(deck: str | None, recorded: str | None):
    from tckdb_schemas.shared.calculation_in import CalculationIn

    extra = {}
    if deck is not None:
        extra["artifacts"] = [
            {
                "kind": "input",
                "filename": "job.in",
                "content_base64": base64.b64encode(deck.encode()).decode(),
            }
        ]
    if recorded is not None:
        extra["parameters"] = [
            {
                "raw_key": "freq",
                "raw_value": recorded,
                "canonical_key": "freq.hessian_method",
                "canonical_value": recorded,
            }
        ]
    return CalculationIn(
        key="f",
        type="freq",
        geometry_key="g",
        software_release={"name": "ORCA", "version": "6.0.1"},
        level_of_theory={"method": "B3LYP", "basis": "def2-SVP"},
        **extra,
    )


@pytest.mark.parametrize(
    ("deck", "recorded"),
    [
        ("! B3LYP AnFreq\n", "numerical"),
        ("! B3LYP NumFreq\n", "analytic"),
        ("! B3LYP NumFreq\n", "finite_difference_energy"),
    ],
)
def test_the_bundle_refuses_a_recorded_method_the_deck_contradicts(deck, recorded) -> None:
    with pytest.raises(ValidationError) as exc:
        _bundle_calc(deck, recorded)
    error = exc.value.errors()[0]["ctx"]["error"]
    assert error.code == W_HESSIAN_METHOD_CONTRADICTS_INPUT_DECK
    assert "AnFreq" not in str(error) and "NumFreq" not in str(error)
    assert error.context["calculation_key"] == "f"


@pytest.mark.parametrize(
    ("deck", "recorded"),
    [
        ("! B3LYP Freq\n", "numerical"),  # no directive: nothing to contradict
        ("! B3LYP NumFreq\n", "numerical"),
        ("! B3LYP NumFreq\n", "finite_difference_gradient"),
        ("! B3LYP AnFreq\n", "analytic"),
        ("! B3LYP AnFreq\n", "mystery"),  # unreadable recorded value is not compared
        ("! B3LYP NumFreq\n", None),
        (None, "analytic"),
    ],
)
def test_the_bundle_accepts_agreeing_or_silent_decks(deck, recorded) -> None:
    _bundle_calc(deck, recorded)
