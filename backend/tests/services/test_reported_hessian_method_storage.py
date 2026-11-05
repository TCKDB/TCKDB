"""The Hessian method an ORCA log reports: stored beside the request, and warned about when the two disagree.

Every "silent" case has a twin that fires, so a check that does nothing cannot pass.
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.calculation import CalculationParameter
from app.db.models.common import ArtifactKind, ParameterSource, ParameterStatementKind
from app.services.parameter_reconciliation import hessian_method_disagreements
from tests.services.test_reported_settings_storage import _calc, _read, _typed

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_ANALYTIC_LOG = (FIXTURES / "orca" / "opt_orca.out").read_text(errors="replace")  # ORCA 6.1.0, "SCF HESSIAN"
_NUMERICAL_LOG = (FIXTURES / "orca" / "ts_opt_orca.out").read_text(errors="replace")  # ORCA 6.1.0, NumFreq
_CODE = "reported_setting_differs_from_requested"


def _echoing(log: str, old: str, new: str) -> str:
    """Change the input the log echoes, leaving everything it prints as it was."""
    lines = log.split("\n")
    for i, line in enumerate(lines):
        if re.match(r"\|\s+\d+> ", line) and old in line:
            lines[i] = line.replace(old, new)
            return "\n".join(lines)
    raise AssertionError(old)


def _hessian_rows(session, calc):
    return [
        r
        for r in session.scalars(select(CalculationParameter).where(CalculationParameter.calculation_id == calc.id))
        if r.canonical_key == "freq.hessian_method"
    ]


def _warnings(session, calc, text, kind=ArtifactKind.output_log):
    return [w for w in _read(session, calc, text, kind=kind) if w.code == _CODE and w.context["canonical_key"] == "freq.hessian_method"]


def test_a_log_stores_the_reported_method_as_a_reported_output_log_row(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _read(session, calc, _ANALYTIC_LOG)
        [row] = _hessian_rows(session, calc)
        assert (row.canonical_value, row.statement_kind, row.artifact_kind) == (
            "analytic",
            ParameterStatementKind.reported,
            ArtifactKind.output_log,
        )
        assert row.source is ParameterSource.parser


def test_a_reported_method_is_never_stored_as_a_request(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _read(session, calc, _ANALYTIC_LOG)  # echoes "!OPT FREQ": it requests nothing about the Hessian
        assert [r.statement_kind for r in _hessian_rows(session, calc)] == [ParameterStatementKind.reported]


def test_the_log_reporting_analytic_against_an_echo_requesting_numerical_warns_and_keeps_both(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        [warning] = _warnings(session, calc, _echoing(_ANALYTIC_LOG, "FREQ", "NumFreq"))
        assert warning.context["software"] == "orca"
        assert warning.context["depositor_supplied"] == {
            "requested_value": "finite_difference_gradient",
            "reported_value": "analytic",
        }
        assert "analytic" not in warning.message and "finite_difference" not in warning.message
        kinds = {(r.canonical_value, r.statement_kind) for r in _hessian_rows(session, calc)}
        assert kinds == {
            ("finite_difference_gradient", ParameterStatementKind.requested),
            ("analytic", ParameterStatementKind.reported),
        }


def test_agreement_is_silent_and_so_is_a_request_the_log_does_not_make(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        assert _warnings(session, calc, _echoing(_ANALYTIC_LOG, "FREQ", "AnFreq")) == []
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        assert _warnings(session, calc, _ANALYTIC_LOG) == []
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        # The numerical log agrees with its own NumFreq echo (the fixture requests it).
        assert _warnings(session, calc, _NUMERICAL_LOG) == []


def test_a_typed_parameter_that_the_log_contradicts_warns(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _typed(session, calc, "freq.hessian_method", "finite_difference_gradient")
        [warning] = _warnings(session, calc, _ANALYTIC_LOG)
        assert warning.context["depositor_supplied"]["requested_value"] == "finite_difference_gradient"
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _typed(session, calc, "freq.hessian_method", "analytic")
        assert _warnings(session, calc, _ANALYTIC_LOG) == []


def test_a_deck_that_arrives_after_the_log_warns_too_and_once(db_conn):
    deck = "!B3LYP DEF2-TZVP D3BJ\n!OPT NumFreq TightOPT\n* xyz 0 1\nH 0 0 0\n*\n"
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        assert _warnings(session, calc, _ANALYTIC_LOG) == []
        assert len(_warnings(session, calc, deck, kind=ArtifactKind.input)) == 1
        assert _warnings(session, calc, deck, kind=ArtifactKind.input) == []


def test_the_comparison_reads_only_method_tokens() -> None:
    assert hessian_method_disagreements("analytic", "finite_difference_gradient") == [
        ("freq.hessian_method", "finite_difference_gradient", "analytic")
    ]
    assert hessian_method_disagreements("finite_difference_gradient", "finite_difference_energy")
    assert hessian_method_disagreements("analytic", "analytic") == []
    assert hessian_method_disagreements("analytic", None) == []
    assert hessian_method_disagreements(None, "analytic") == []
    assert hessian_method_disagreements("analytic", "numer") == []  # a depositor's own spelling is not compared
