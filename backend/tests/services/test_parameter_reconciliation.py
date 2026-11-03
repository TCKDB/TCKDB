"""What a depositor typed, what the deck says, what the log says.

Positives first: every "no warning" case below has a twin that does warn, so a reconciliation that never
fires cannot pass.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.calculation import CalculationParameter
from app.db.models.common import ArtifactKind, CalculationType, ParameterSource
from app.schemas.fragments.calculation import (
    CalculationParameterObservation,
    CalculationWithResultsPayload,
    OptResultPayload,
)
from app.services.calculation_parameter_extraction import extract_and_store_calculation_parameters
from app.services.calculation_resolution import (
    persist_calculation_parameters,
    resolve_and_persist_calculation_with_results,
)
from tests.services.test_calculation_parameter_extraction import _create_species_entry, _next_inchi_key

_DASHES = "-" * 70


def _gaussian_log(route: str) -> str:
    """Just enough of a Gaussian log for the banner sniff and the route echo."""
    return f" Gaussian 16:  ES64L-G16RevC.01 3-Jul-2019\n {_DASHES}\n {route}\n {_DASHES}\n"


def _orca_deck(keywords: str) -> str:
    return f"! {keywords}\n* xyz 0 1\nH 0 0 0\n*\n"


def _calc(session: Session, *, software: str = "gaussian", method: str = "B3LYP", basis: str = "6-31G(d)"):
    entry_id = _create_species_entry(session, inchi_key=_next_inchi_key("RECN"))
    upload = CalculationWithResultsPayload(
        type=CalculationType.opt,
        software_release={"name": software, "version": "16", "revision": "C.02"},
        level_of_theory={"method": method, "basis": basis},
        opt_result=OptResultPayload(converged=True),
    )
    return resolve_and_persist_calculation_with_results(session, upload, species_entry_id=entry_id)


def _typed(session: Session, calc, key: str, value: str, raw_key: str = "typed") -> None:
    persist_calculation_parameters(
        session,
        calc,
        [CalculationParameterObservation(raw_key=raw_key, raw_value=value, canonical_key=key, canonical_value=value)],
    )


def _read(session, calc, text, *, kind=ArtifactKind.input):
    warnings: list = []
    rows = extract_and_store_calculation_parameters(session, calc, text, warnings=warnings, artifact_kind=kind)
    return rows, warnings


def _codes(warnings) -> list[str]:
    return [w.code for w in warnings]


def _stored(session, calc) -> set[tuple]:
    rows = session.scalars(
        select(CalculationParameter).where(
            CalculationParameter.calculation_id == calc.id, CalculationParameter.source == ParameterSource.parser
        )
    )
    return {(r.canonical_key, r.canonical_value, r.artifact_kind) for r in rows if r.canonical_key}


# ---------------------------------------------------------------------------
# typed against read
# ---------------------------------------------------------------------------


def test_a_typed_value_the_deck_contradicts_warns_with_the_key_and_marks_the_values_as_depositor_text(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _typed(session, calc, "grid.quality", "ultrafine")
        _, warnings = _read(session, calc, "#P b3lyp/6-31g(d) opt int=fine\n")
        [warning] = [w for w in warnings if w.code == "parameter_disagrees_with_artifact"]
        assert warning.context["canonical_key"] == "grid.quality"
        assert warning.context["observed_in"] == "input"
        assert warning.context["software"] == "gaussian"
        assert warning.context["depositor_supplied"] == {"declared_value": ["ultrafine"], "observed_value": ["fine"]}
        # The sentence is TCKDB's: it names the key and not the values.
        assert "grid.quality" in warning.message and "ultrafine" not in warning.message and "fine" not in warning.message.replace("define", "")


@pytest.mark.parametrize("deck_grid", ["int=ultrafine", "int=(grid=99590)", "integral=ultrafinegrid"])
def test_spellings_of_the_same_grid_do_not_warn(db_conn, deck_grid):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _typed(session, calc, "grid.quality", "ultrafine")
        _, warnings = _read(session, calc, f"#P b3lyp/6-31g(d) opt {deck_grid}\n")
        assert "parameter_disagrees_with_artifact" not in _codes(warnings)


def test_a_setting_the_deck_does_not_state_is_not_a_disagreement(db_conn):
    """Absent means 'not stated', never 'the default'."""
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _typed(session, calc, "grid.quality", "superfine")
        _, warnings = _read(session, calc, "#P b3lyp/6-31g(d) opt scf=tight\n")
        assert "parameter_disagrees_with_artifact" not in _codes(warnings)


def test_a_setting_the_depositor_did_not_type_is_not_a_disagreement(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        rows, warnings = _read(session, calc, "#P b3lyp/6-31g(d) opt int=fine\n")
        assert ("grid.quality", "fine", ArtifactKind.input) in _stored(session, calc)
        assert "parameter_disagrees_with_artifact" not in _codes(warnings)


def test_a_declaration_is_not_judged_against_another_programs_log(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software="orca")
        _typed(session, calc, "grid.quality", "ultrafine")
        _, warnings = _read(session, calc, _gaussian_log("#P b3lyp/6-31g(d) opt int=fine"), kind=ArtifactKind.output_log)
        assert "parameter_disagrees_with_artifact" not in _codes(warnings)
        # ...and the same declaration against the same program's log does warn.
        twin = _calc(session, software="gaussian")
        _typed(session, twin, "grid.quality", "ultrafine")
        _, twin_warnings = _read(session, twin, _gaussian_log("#P b3lyp/6-31g(d) opt int=fine"), kind=ArtifactKind.output_log)
        assert "parameter_disagrees_with_artifact" in _codes(twin_warnings)
        assert next(w for w in twin_warnings if w.code == "parameter_disagrees_with_artifact").context["observed_in"] == "output_log"


def test_orca_keywords_are_reconciled_too(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software="orca")
        _typed(session, calc, "ri.approximation", "rijk")
        _typed(session, calc, "dispersion.correction", "d3_bj", raw_key="disp")
        _, warnings = _read(session, calc, _orca_deck("B3LYP def2-SVP RIJCOSX D3BJ"))
        keys = [w.context["canonical_key"] for w in warnings if w.code == "parameter_disagrees_with_artifact"]
        assert keys == ["ri.approximation"]


def test_the_hessian_method_is_left_to_its_own_check(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _typed(session, calc, "freq.hessian_method", "analytic")
        _, warnings = _read(session, calc, "#P b3lyp/6-31g(d) freq=numer\n")
        assert "parameter_disagrees_with_artifact" not in _codes(warnings)


# ---------------------------------------------------------------------------
# deck and log together
# ---------------------------------------------------------------------------

_DECK = "#P b3lyp/6-31g(d) opt int=ultrafine scf=tight\n"
_LOG_SAME = _gaussian_log("#P b3lyp/6-31g(d) opt int=ultrafine scf=tight EmpiricalDispersion=GD3BJ")
_LOG_DIFFERENT = _gaussian_log("#P b3lyp/6-31g(d) opt int=fine scf=tight EmpiricalDispersion=GD3BJ")


@pytest.mark.parametrize("deck_first", [True, False])
def test_deck_and_log_end_in_the_same_rows_whichever_arrives_first(db_conn, deck_first):
    """Deck's rows, plus the log's rows for what the deck is silent about; one row per setting."""
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        steps = [(_DECK, ArtifactKind.input), (_LOG_SAME, ArtifactKind.output_log)]
        for text, kind in steps if deck_first else steps[::-1]:
            _read(session, calc, text, kind=kind)
        stored = _stored(session, calc)
        assert stored == {
            ("grid.quality", "ultrafine", ArtifactKind.input),
            ("scf.convergence", "tight", ArtifactKind.input),
            ("output.verbosity", "full", ArtifactKind.input),
            ("dispersion.correction", "d3_bj", ArtifactKind.output_log),
        }
        keys = [r[0] for r in stored]
        assert len(keys) == len(set(keys))


@pytest.mark.parametrize("deck_first", [True, False])
def test_deck_and_log_that_disagree_warn_once_and_the_deck_wins(db_conn, deck_first):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        steps = [(_DECK, ArtifactKind.input), (_LOG_DIFFERENT, ArtifactKind.output_log)]
        collected = []
        for text, kind in steps if deck_first else steps[::-1]:
            collected.extend(_read(session, calc, text, kind=kind)[1])
        [warning] = [w for w in collected if w.code == "input_deck_disagrees_with_output_log"]
        assert warning.context["canonical_key"] == "grid.quality"
        assert warning.context["depositor_supplied"] == {"deck_value": ["ultrafine"], "log_value": ["fine"]}
        assert ("grid.quality", "ultrafine", ArtifactKind.input) in _stored(session, calc)
        assert ("grid.quality", "fine", ArtifactKind.output_log) not in _stored(session, calc)


def test_agreeing_deck_and_log_do_not_warn(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _, first = _read(session, calc, _DECK)
        _, second = _read(session, calc, _LOG_SAME, kind=ArtifactKind.output_log)
        assert "input_deck_disagrees_with_output_log" not in _codes(first + second)


def test_rereading_a_deck_leaves_the_log_only_rows_alone_and_replaces_its_own(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _read(session, calc, _LOG_SAME, kind=ArtifactKind.output_log)
        _read(session, calc, "#P b3lyp/6-31g(d) opt int=fine\n")
        _read(session, calc, "#P b3lyp/6-31g(d) opt int=superfine\n")
        assert _stored(session, calc) == {
            ("grid.quality", "superfine", ArtifactKind.input),
            ("output.verbosity", "full", ArtifactKind.input),
            ("scf.convergence", "tight", ArtifactKind.output_log),
            ("dispersion.correction", "d3_bj", ArtifactKind.output_log),
        }


def test_a_log_alone_is_read_and_tagged_output_log(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        rows, _ = _read(session, calc, _LOG_SAME, kind=ArtifactKind.output_log)
        assert rows and all(r.artifact_kind is ArtifactKind.output_log and r.source is ParameterSource.parser for r in rows)


def test_uploaded_rows_never_carry_an_artifact_kind(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _typed(session, calc, "grid.quality", "fine")
        _read(session, calc, _DECK)
        typed = session.scalars(
            select(CalculationParameter).where(
                CalculationParameter.calculation_id == calc.id, CalculationParameter.source == ParameterSource.upload
            )
        ).all()
        assert typed and all(r.artifact_kind is None for r in typed)


# ---------------------------------------------------------------------------
# method and basis
# ---------------------------------------------------------------------------


def test_a_method_that_differs_from_the_declared_level_of_theory_warns(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, method="wB97X-D", basis="def2-TZVP")
        _, warnings = _read(session, calc, "#P b3lyp/def2tzvp opt\n")
        [warning] = [w for w in warnings if w.code == "level_of_theory_disagrees_with_artifact"]
        assert warning.context["component"] == "method"
        assert warning.context["depositor_supplied"] == {"declared_value": "wB97X-D", "observed_value": "b3lyp"}
        assert "b3lyp" not in warning.message.lower() and "wb97x" not in warning.message.lower()


def test_a_basis_that_differs_from_the_declared_level_of_theory_warns(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, method="wB97X-D", basis="def2-TZVP")
        _, warnings = _read(session, calc, "#P uwb97xd/def2svp opt\n")
        [warning] = [w for w in warnings if w.code == "level_of_theory_disagrees_with_artifact"]
        assert warning.context["component"] == "basis"


@pytest.mark.parametrize(
    "route",
    [
        "#P uwb97xd/def2tzvp opt",  # spin prefix and Gaussian's hyphen-free basis spelling
        "#P wb97xd/Def2TZVP opt",
        "#P uwb97xd/gen opt",  # a general-basis marker names no basis
    ],
)
def test_spellings_of_the_same_level_do_not_warn(db_conn, route):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, method="wB97X-D", basis="def2-TZVP")
        _, warnings = _read(session, calc, route + "\n")
        assert "level_of_theory_disagrees_with_artifact" not in _codes(warnings)


def test_frozen_core_option_and_polarisation_spelling_do_not_make_a_method_or_basis_mismatch(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, method="CCSD(T)", basis="6-31G*")
        _, warnings = _read(session, calc, "#P ccsd(t,full)/6-31g(d) sp\n")
        assert "level_of_theory_disagrees_with_artifact" not in _codes(warnings)


def test_orca_method_and_basis_are_compared(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software="orca", method="B3LYP", basis="def2-TZVP")
        _, same = _read(session, calc, _orca_deck("B3LYP def2-TZVP TightSCF"))
        assert "level_of_theory_disagrees_with_artifact" not in _codes(same)
        _, different = _read(session, calc, _orca_deck("PBE0 def2-TZVP TightSCF"))
        assert "level_of_theory_disagrees_with_artifact" in _codes(different)


def test_a_declaration_that_states_no_basis_is_not_contradicted(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, method="B3LYP", basis=None)
        _, warnings = _read(session, calc, "#P b3lyp/6-31g(d) opt\n")
        assert "level_of_theory_disagrees_with_artifact" not in _codes(warnings)
