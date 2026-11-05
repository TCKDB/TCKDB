"""Tau follows the Hessian method an output log reports, ahead of what was requested, ahead of an assumed default.

Order under test: reported, then recorded / deck, then the program's assumed default, then ``protocol_not_recorded``.
Each rung is tested with the rung below it present and different, so a rung that does nothing cannot pass.
"""

from __future__ import annotations

import base64

import pytest
from tckdb_schemas.enums import HessianMethod
from tckdb_schemas.hessian_method_assumption import (
    calculation_reported_method,
    resolve_tau_for_calculation,
    resolve_tau_with_assumption,
)
from tckdb_schemas.shared.calculation_in import CalculationIn
from tckdb_schemas.stationary_point import TauBasis

_ANALYTIC = HessianMethod.analytic
_FD = HessianMethod.finite_difference_gradient
_FD_ENERGY = HessianMethod.finite_difference_energy

_VIB = "-----------------------\nVIBRATIONAL FREQUENCIES\n-----------------------\n"
_LOG_ANALYTIC = "                               ORCA SCF HESSIAN\n-----\n" + _VIB
_LOG_NUMERICAL = (
    "                           ORCA NUMERICAL FREQUENCIES\n-----\n"
    "\t<< Calculating gradient on displaced geometry  10 (of  52) >>\n" + _VIB
)


def _recorded(value: str) -> list[tuple[str, str]]:
    return [("freq.hessian_method", value)]


# ---------------------------------------------------------------------------
# the function
# ---------------------------------------------------------------------------


def test_assumed_default_when_nothing_else_is_known() -> None:
    tau = resolve_tau_with_assumption([], "ORCA", "B3LYP", "6.1.0")
    assert tau.basis is TauBasis.assumed_analytic_default


def test_a_reported_method_beats_the_assumed_default() -> None:
    tau = resolve_tau_with_assumption([], "ORCA", "B3LYP", "6.1.0", reported_method=_FD)
    assert (tau.basis, tau.tau_cm1) == (TauBasis.finite_difference_gradient, 50.0)


def test_a_reported_method_beats_a_recorded_one() -> None:
    tau = resolve_tau_with_assumption(_recorded("finite_difference_gradient"), "ORCA", "B3LYP", "6.1.0", reported_method=_ANALYTIC)
    assert tau.basis is TauBasis.analytic_default
    # Without the report the recorded value stands.
    assert resolve_tau_with_assumption(_recorded("finite_difference_gradient"), "ORCA", "B3LYP", "6.1.0").basis is (
        TauBasis.finite_difference_gradient
    )


def test_a_reported_method_beats_a_deck_and_an_unrecognised_recorded_value() -> None:
    tau = resolve_tau_with_assumption([], "ORCA", "B3LYP", "6.1.0", deck_method=_FD, reported_method=_ANALYTIC)
    assert tau.basis is TauBasis.analytic_default
    tau = resolve_tau_with_assumption(_recorded("numer"), "ORCA", "B3LYP", "6.1.0", reported_method=_ANALYTIC)
    assert tau.basis is TauBasis.analytic_default


def test_an_echoed_method_beats_a_recorded_one_and_a_deck_but_not_a_report() -> None:
    args = (_recorded("analytic"), "ORCA", "B3LYP", "6.1.0")
    assert resolve_tau_with_assumption(*args, echo_method=_FD).basis is TauBasis.finite_difference_gradient
    assert resolve_tau_with_assumption(*args, deck_method=_ANALYTIC, echo_method=_FD).basis is TauBasis.finite_difference_gradient
    assert resolve_tau_with_assumption(*args, echo_method=_FD, reported_method=_ANALYTIC).basis is TauBasis.analytic_default


def test_a_deck_still_beats_the_assumed_default_and_a_recorded_value_still_beats_a_deck() -> None:
    assert resolve_tau_with_assumption([], "ORCA", "B3LYP", "6.1.0", deck_method=_FD).basis is TauBasis.finite_difference_gradient
    tau = resolve_tau_with_assumption(_recorded("analytic"), "ORCA", "B3LYP", "6.1.0", deck_method=_FD)
    assert tau.basis is TauBasis.analytic_default


def test_a_reported_method_gives_the_same_tau_as_the_same_method_recorded() -> None:
    for method in (_ANALYTIC, _FD, _FD_ENERGY):
        reported = resolve_tau_with_assumption([], "ORCA", "B3LYP", "6.1.0", reported_method=method)
        recorded = resolve_tau_with_assumption(_recorded(method.value), "ORCA", "B3LYP", "6.1.0")
        assert (reported.tau_cm1, reported.basis) == (recorded.tau_cm1, recorded.basis)


def test_a_reported_method_applies_where_no_default_is_assumed() -> None:
    # An unknown level of theory has no assumed default; a report still decides.
    assert resolve_tau_with_assumption([], "ORCA", "MyNewMethod", "6.1.0").basis is TauBasis.protocol_not_recorded
    assert resolve_tau_with_assumption([], "ORCA", "MyNewMethod", "6.1.0", reported_method=_ANALYTIC).basis is TauBasis.analytic_default


# ---------------------------------------------------------------------------
# the bundle payload
# ---------------------------------------------------------------------------


def _calc(
    *, log: str | None = None, deck: str | None = None, recorded: str | None = None, logs: int = 1, software="ORCA", more_logs=()
):
    artifacts = []
    if deck is not None:
        artifacts.append({"kind": "input", "filename": "job.in", "content_base64": base64.b64encode(deck.encode()).decode()})
    if log is not None:
        for i in range(logs):
            artifacts.append(
                {"kind": "output_log", "filename": f"job{i}.out", "content_base64": base64.b64encode(log.encode()).decode()}
            )
    for i, text in enumerate(more_logs):
        artifacts.append({"kind": "output_log", "filename": f"more{i}.out", "content_base64": base64.b64encode(text.encode()).decode()})
    extra = {"artifacts": artifacts}
    if recorded is not None:
        extra["parameters"] = [
            {"raw_key": "freq", "raw_value": recorded, "canonical_key": "freq.hessian_method", "canonical_value": recorded}
        ]
    return CalculationIn(
        key="f",
        type="freq",
        geometry_key="g",
        software_release={"name": software, "version": "6.1.0"},
        level_of_theory={"method": "B3LYP", "basis": "def2-SVP"},
        **extra,
    )


def test_the_bundle_reads_the_reported_method_from_its_inline_output_log() -> None:
    assert calculation_reported_method(_calc(log=_LOG_NUMERICAL)) is _FD
    assert calculation_reported_method(_calc(log=_LOG_ANALYTIC)) is _ANALYTIC
    assert calculation_reported_method(_calc()) is None


def test_the_bundle_tau_follows_the_log_over_the_deck_and_the_recorded_parameter() -> None:
    assert resolve_tau_for_calculation(_calc(log=_LOG_ANALYTIC, deck="! B3LYP NumFreq\n")).basis is TauBasis.analytic_default
    # The same deck with no log: the deck decides.
    assert resolve_tau_for_calculation(_calc(deck="! B3LYP NumFreq\n")).basis is TauBasis.finite_difference_gradient
    # A recorded parameter that contradicts the deck is still refused (the existing rule is untouched).
    with pytest.raises(ValueError):
        _calc(log=_LOG_ANALYTIC, deck="! B3LYP NumFreq\n", recorded="analytic")


def test_several_logs_use_the_report_they_agree_on_and_none_when_they_differ() -> None:
    # Storage keeps a reported row from every log; validation uses the report when the logs that report agree.
    assert calculation_reported_method(_calc(log=_LOG_NUMERICAL, logs=2)) is _FD
    assert calculation_reported_method(_calc(log=_LOG_NUMERICAL, more_logs=["no listing here\n"])) is _FD
    assert resolve_tau_for_calculation(_calc(log=_LOG_NUMERICAL, logs=2)).basis is TauBasis.finite_difference_gradient
    differ = _calc(log=_LOG_NUMERICAL, more_logs=[_LOG_ANALYTIC])
    assert calculation_reported_method(differ) is None
    assert resolve_tau_for_calculation(differ).basis is TauBasis.assumed_analytic_default


# ---------------------------------------------------------------------------
# the echoed input
# ---------------------------------------------------------------------------

_DASHES = " " + "-" * 70


def _orca_echo(*lines: str) -> str:
    return "INPUT FILE\n" + "\n".join(f"|{i + 1:>3}> {line}" for i, line in enumerate(lines)) + "\n****END OF INPUT****\n"


def _gaussian_echo(*routes: str) -> str:
    return " Entering Gaussian System, Link 0=g16\n" + "".join(f"{_DASHES}\n {r}\n{_DASHES}\n" for r in routes)


def test_the_input_a_log_echoes_is_read_ahead_of_the_deck_and_the_assumed_default() -> None:
    from tckdb_schemas.hessian_method_assumption import calculation_echo_method

    orca = _calc(log=_orca_echo("! B3LYP def2-SVP NumFreq", "* xyz 0 1", "H 0 0 0", "*"))
    assert calculation_echo_method(orca) is _FD
    assert resolve_tau_for_calculation(orca).basis is TauBasis.finite_difference_gradient
    # Echo outranks the deck.
    both = _calc(log=_orca_echo("! B3LYP def2-SVP NumFreq"), deck="! B3LYP def2-SVP AnFreq\n")
    assert resolve_tau_for_calculation(both).basis is TauBasis.finite_difference_gradient
    # Without the echo, the deck decides (the twin).
    assert resolve_tau_for_calculation(_calc(deck="! B3LYP def2-SVP AnFreq\n")).basis is TauBasis.analytic_default
    gaussian = _calc(log=_gaussian_echo("#p freq=numer b3lyp/def2tzvp"), software="Gaussian")
    assert calculation_echo_method(gaussian) is _FD
    assert resolve_tau_for_calculation(gaussian).basis is TauBasis.finite_difference_gradient


def test_a_report_outranks_the_echo() -> None:
    log = _orca_echo("! B3LYP def2-SVP NumFreq") + _LOG_ANALYTIC
    assert resolve_tau_for_calculation(_calc(log=log)).basis is TauBasis.analytic_default


def test_an_echo_that_cannot_be_bound_to_one_job_reads_nothing() -> None:
    from tckdb_schemas.hessian_method_assumption import calculation_echo_method

    assert calculation_echo_method(_calc(log=_orca_echo("! B3LYP NumFreq", "$new_job", "! B3LYP AnFreq"))) is None
    assert calculation_echo_method(_calc(log=_orca_echo("! B3LYP NumFreq", "%compound x.cmp"))) is None
    two_routes = _gaussian_echo("#p freq=numer b3lyp/def2tzvp", "#p sp b3lyp/def2tzvp")
    assert calculation_echo_method(_calc(log=two_routes, software="Gaussian")) is None
    assert calculation_echo_method(_calc(log=_gaussian_echo("#p freq=numer b3lyp/def2tzvp"), software="Gaussian")) is _FD


def test_several_logs_must_agree_on_the_echo() -> None:
    from tckdb_schemas.hessian_method_assumption import calculation_echo_method

    numfreq = _orca_echo("! B3LYP NumFreq")
    assert calculation_echo_method(_calc(log=numfreq, logs=2)) is _FD
    assert calculation_echo_method(_calc(log=numfreq, more_logs=[_orca_echo("! B3LYP AnFreq")])) is None


def test_a_program_with_no_reader_reports_nothing() -> None:
    gaussian = " Entering Gaussian System, Link 0=g09\n Gaussian 09, Revision D.01,\n" + _LOG_NUMERICAL
    assert calculation_reported_method(_calc(log=gaussian, software="Gaussian")) is None
    # With no banner in the text the declared program is the fallback, so these ORCA section headers are read.
    assert calculation_reported_method(_calc(log=_LOG_NUMERICAL, software="Gaussian")) is _FD
