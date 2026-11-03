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


@pytest.mark.parametrize("deck_grid", ["int=ultrafine", "int=(grid=ultrafine)", "integral=ultrafinegrid"])
def test_spellings_of_the_same_grid_do_not_warn(db_conn, deck_grid):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _typed(session, calc, "grid.quality", "ultrafine")
        _, warnings = _read(session, calc, f"#P b3lyp/6-31g(d) opt {deck_grid}\n")
        assert "parameter_disagrees_with_artifact" not in _codes(warnings)


def test_a_numeric_grid_is_a_different_grid_from_the_named_one(db_conn):
    """Grid=99590 is unpruned; UltraFine is pruned (99,590): not the same grid, so a typed UltraFine disagrees."""
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _typed(session, calc, "grid.quality", "ultrafine")
        _, warnings = _read(session, calc, "#P b3lyp/6-31g(d) opt int=(grid=99590)\n")
        assert "parameter_disagrees_with_artifact" in _codes(warnings)


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
        # The comparison happens: a method that differs warns, so the pass above is not vacuous.
        _, twin = _read(session, calc, "#P mp2(full)/6-31g(d) sp\n")
        assert "level_of_theory_disagrees_with_artifact" in _codes(twin)


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


def test_a_deck_that_names_a_reference_method_beside_the_declared_one_is_not_a_mismatch(db_conn):
    """DLPNO-CCSD(T)-F12 decks also name HF; the declaration matches one of the deck's methods."""
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software="orca", method="DLPNO-CCSD(T)-F12", basis="cc-pVTZ-F12")
        _, warnings = _read(session, calc, _orca_deck("DLPNO-CCSD(T)-F12 cc-pVTZ-F12 HF"))
        assert "level_of_theory_disagrees_with_artifact" not in _codes(warnings)
        _, other = _read(session, calc, _orca_deck("PBE0 cc-pVTZ-F12 HF"))
        assert "level_of_theory_disagrees_with_artifact" in _codes(other)


# ---------------------------------------------------------------------------
# typed values are folded before they are compared
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "typed", "route"),
    [
        ("grid.quality", "UltraFine", "int=ultrafine"),
        ("grid.quality", "UltraFineGrid", "int=ultrafine"),
        ("scf.convergence", "Tight", "scf=tight"),
        ("scf.convergence", "VeryTight", "scf=verytight"),
        ("dispersion.correction", "GD3BJ", "EmpiricalDispersion=GD3BJ"),
        ("dispersion.correction", "D3BJ", "EmpiricalDispersion=GD3BJ"),
        ("solvation.model", "SMD", "scrf=(smd,solvent=water)"),
        ("solvation.model", "PCM", "scrf=(pcm,solvent=water)"),
        ("solvation.solvent", "Water", "scrf=(smd,solvent=water)"),
    ],
)
def test_a_typed_value_in_another_case_or_alias_spelling_agrees(db_conn, key, typed, route):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _typed(session, calc, key, typed)
        _, warnings = _read(session, calc, f"#P b3lyp/6-31g(d) opt {route}\n")
        assert "parameter_disagrees_with_artifact" not in _codes(warnings)


@pytest.mark.parametrize(
    ("key", "typed", "route"),
    [
        ("grid.quality", "Fine", "int=ultrafine"),
        ("scf.convergence", "Loose", "scf=tight"),
        ("dispersion.correction", "GD3", "EmpiricalDispersion=GD3BJ"),
        ("solvation.model", "CPCM", "scrf=(smd,solvent=water)"),
        ("solvation.solvent", "Toluene", "scrf=(smd,solvent=water)"),
    ],
)
def test_a_typed_value_that_really_differs_still_warns_after_folding(db_conn, key, typed, route):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _typed(session, calc, key, typed)
        _, warnings = _read(session, calc, f"#P b3lyp/6-31g(d) opt {route}\n")
        assert "parameter_disagrees_with_artifact" in _codes(warnings)


def test_a_typed_non_token_for_a_closed_set_key_is_never_judged(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _typed(session, calc, "dispersion.correction", "some dispersion I use")
        _, warnings = _read(session, calc, "#P b3lyp/6-31g(d) opt EmpiricalDispersion=GD3BJ\n")
        assert "parameter_disagrees_with_artifact" not in _codes(warnings)


# ---------------------------------------------------------------------------
# method and basis spellings, several jobs, reference tokens
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("software", "method", "basis", "text"),
    [
        ("gaussian", "B3LYP", "6-31G(d)", "#P ub3lyp/6-31g(d) opt\n"),
        ("gaussian", "HF", "6-31G(d)", "#P rhf/6-31g(d) opt\n"),
        ("gaussian", "PBE0", "6-31G(d)", "#P pbe1pbe/6-31g(d) opt\n"),
        ("gaussian", "wB97X-D", "def2-TZVP", "#P wb97xd/def2tzvp/auto opt\n"),
        ("gaussian", "CAM-B3LYP", "6-31G(d)", "#P cam-b3lyp/6-31g(d) opt\n"),
        ("gaussian", "ROCCSD(T)", "cc-pVTZ", "#P roccsd(t)/cc-pvtz sp\n"),
        ("gaussian", "B3LYP-D3BJ", "6-31G(d)", "#P b3lyp/6-31g(d) EmpiricalDispersion=GD3BJ opt\n"),
        ("orca", "CCSD(T)-F12/RI", "cc-pVTZ-F12", _orca_deck("CCSD(T)-F12 cc-pVTZ-F12")),
        ("orca", "RI-MP2", "def2-TZVP", _orca_deck("MP2 RI def2-TZVP")),
    ],
)
def test_alias_spellings_of_the_same_level_agree(db_conn, software, method, basis, text):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software=software, method=method, basis=basis)
        _, warnings = _read(session, calc, text)
        assert "level_of_theory_disagrees_with_artifact" not in _codes(warnings)


@pytest.mark.parametrize(
    ("method", "route"),
    [
        ("CCSD(T)", "#P ccsd/6-31g(d) sp\n"),
        ("CAM-B3LYP", "#P b3lyp/6-31g(d) sp\n"),
        ("wB97X-D", "#P wb97x/6-31g(d) sp\n"),
        ("ROCCSD(T)", "#P rohf/6-31g(d) sp\n"),
    ],
)
def test_the_methods_the_old_pattern_could_not_read_are_now_compared(db_conn, method, route):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, method=method)
        _, warnings = _read(session, calc, route)
        assert "level_of_theory_disagrees_with_artifact" in _codes(warnings)


_TWO_JOBS = "#P b3lyp/6-31g(d) opt\n\ntitle\n\n0 1\nH 0 0 0\n\n--Link1--\n#P mp2/cc-pvtz sp\n\ntitle\n\n0 1\nH 0 0 0\n\n"


def test_a_declaration_matching_any_job_of_a_link1_file_agrees(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, method="MP2", basis="cc-pVTZ")
        _, warnings = _read(session, calc, _TWO_JOBS)
        assert "level_of_theory_disagrees_with_artifact" not in _codes(warnings)
        # ...but the method of one job with the basis of the other is no job's pair.
        mixed = _calc(session, method="MP2", basis="6-31G(d)")
        _, mixed_warnings = _read(session, mixed, _TWO_JOBS)
        assert "level_of_theory_disagrees_with_artifact" in _codes(mixed_warnings)
        none = _calc(session, method="CCSD", basis="cc-pVTZ")
        _, none_warnings = _read(session, none, _TWO_JOBS)
        assert "level_of_theory_disagrees_with_artifact" in _codes(none_warnings)


def test_orca_new_job_decks_are_compared_job_by_job(db_conn):
    deck = _orca_deck("B3LYP def2-SVP Opt") + "$new_job\n" + _orca_deck("DLPNO-CCSD(T) def2-TZVP TightPNO")
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software="orca", method="DLPNO-CCSD(T)", basis="def2-TZVP")
        _, warnings = _read(session, calc, deck)
        assert "level_of_theory_disagrees_with_artifact" not in _codes(warnings)
        wrong = _calc(session, software="orca", method="DLPNO-CCSD(T)", basis="def2-SVP")
        _, wrong_warnings = _read(session, wrong, deck)
        assert "level_of_theory_disagrees_with_artifact" in _codes(wrong_warnings)


def test_a_bare_reference_token_beside_a_correlated_method_is_ignored(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software="orca", method="HF", basis="cc-pVTZ-F12")
        _, warnings = _read(session, calc, _orca_deck("DLPNO-CCSD(T)-F12 cc-pVTZ-F12 HF"))
        # HF is named in the deck but only as the reference of the correlated method, so it does not match HF.
        assert "level_of_theory_disagrees_with_artifact" in _codes(warnings)


# ---------------------------------------------------------------------------
# a deck re-read with fewer settings, and a second log
# ---------------------------------------------------------------------------


def _store_log(session, monkeypatch, calc, text: str, name: str) -> None:
    """A log artifact row whose bytes the re-read can load (storage itself is out of scope here)."""
    import hashlib

    from app.db.models.calculation import CalculationArtifact
    from app.services import calculation_parameter_extraction as cpe

    content = text.encode()
    sha = hashlib.sha256(content).hexdigest()
    session.add(
        CalculationArtifact(
            calculation_id=calc.id,
            kind=ArtifactKind.output_log,
            uri=f"s3://test/{sha}",
            sha256=sha,
            bytes=len(content),
            filename=name,
        )
    )
    session.flush()
    stored = getattr(cpe, "_test_bytes", {})
    stored[sha] = content
    monkeypatch.setattr(cpe, "_test_bytes", stored, raising=False)
    monkeypatch.setattr(cpe, "load_artifact_bytes", lambda digest, **_: stored[digest])


def test_a_deck_reread_with_fewer_settings_restores_the_log_rows_it_had_shadowed(db_conn, monkeypatch):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _read(session, calc, "#P b3lyp/6-31g(d) opt int=ultrafine scf=tight\n")
        log = _gaussian_log("#P b3lyp/6-31g(d) opt int=ultrafine scf=tight EmpiricalDispersion=GD3BJ")
        _store_log(session, monkeypatch, calc, log, "job.log")
        _read(session, calc, log, kind=ArtifactKind.output_log)
        assert ("scf.convergence", "tight", ArtifactKind.input) in _stored(session, calc)
        assert ("scf.convergence", "tight", ArtifactKind.output_log) not in _stored(session, calc)

        _read(session, calc, "#P b3lyp/6-31g(d) opt int=ultrafine\n")
        stored = _stored(session, calc)
        assert ("scf.convergence", "tight", ArtifactKind.output_log) in stored
        assert ("grid.quality", "ultrafine", ArtifactKind.input) in stored
        keys = [row[0] for row in stored]
        assert len(keys) == len(set(keys))


def test_a_second_output_log_is_checked_against_the_first_and_never_silently_replaces_it(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _, first = _read(session, calc, _gaussian_log("#P b3lyp/6-31g(d) opt int=ultrafine"), kind=ArtifactKind.output_log)
        assert "output_log_disagrees_with_earlier_output_log" not in _codes(first)
        _, second = _read(session, calc, _gaussian_log("#P b3lyp/6-31g(d) opt int=fine scf=tight"), kind=ArtifactKind.output_log)
        [warning] = [w for w in second if w.code == "output_log_disagrees_with_earlier_output_log"]
        assert warning.context["canonical_key"] == "grid.quality"
        assert warning.context["depositor_supplied"] == {"earlier_value": ["ultrafine"], "later_value": ["fine"]}
        stored = _stored(session, calc)
        assert ("grid.quality", "ultrafine", ArtifactKind.output_log) in stored
        assert ("grid.quality", "fine", ArtifactKind.output_log) not in stored
        # What the earlier log did not state is added.
        assert ("scf.convergence", "tight", ArtifactKind.output_log) in stored


def test_the_same_output_log_uploaded_twice_changes_nothing_and_warns_nothing(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        log = _gaussian_log("#P b3lyp/6-31g(d) opt int=ultrafine")
        _read(session, calc, log, kind=ArtifactKind.output_log)
        before = _stored(session, calc)
        _, again = _read(session, calc, log, kind=ArtifactKind.output_log)
        assert "output_log_disagrees_with_earlier_output_log" not in _codes(again)
        assert _stored(session, calc) == before


def test_a_dropped_log_observation_is_still_judged_against_what_was_typed_in_either_order(db_conn):
    """Typed ultrafine; the deck states ultrafine too; the log says fine. The log's grid is dropped for the deck's,
    but the typed value and the log still disagree, whichever of the two is read first."""
    codes_by_order = {}
    for deck_first in (True, False):
        with Session(db_conn) as session, session.begin():
            calc = _calc(session)
            _typed(session, calc, "grid.quality", "ultrafine")
            steps = [("#P b3lyp/6-31g(d) opt int=ultrafine\n", ArtifactKind.input),
                     (_gaussian_log("#P b3lyp/6-31g(d) opt int=fine"), ArtifactKind.output_log)]
            collected = []
            for text, kind in steps if deck_first else steps[::-1]:
                collected += _read(session, calc, text, kind=kind)[1]
            codes_by_order[deck_first] = sorted(_codes(collected))
    assert codes_by_order[True] == codes_by_order[False]
    assert "parameter_disagrees_with_artifact" in codes_by_order[True]


def test_the_log_that_a_deck_does_not_shadow_is_judged_the_same_in_either_order(db_conn):
    """Typed ultrafine; the deck is silent on the grid; the log says fine: warns in both orders."""
    for deck_first in (True, False):
        with Session(db_conn) as session, session.begin():
            calc = _calc(session)
            _typed(session, calc, "grid.quality", "ultrafine")
            steps = [("#P b3lyp/6-31g(d) opt scf=tight\n", ArtifactKind.input),
                     (_gaussian_log("#P b3lyp/6-31g(d) opt int=fine"), ArtifactKind.output_log)]
            collected = []
            for text, kind in steps if deck_first else steps[::-1]:
                collected += _read(session, calc, text, kind=kind)[1]
            assert _codes(collected).count("parameter_disagrees_with_artifact") == 1, deck_first


def test_a_first_deck_never_reads_storage_for_the_logs(db_conn, monkeypatch):
    from app.services import calculation_parameter_extraction as cpe

    calls = []

    def spy(*args, **kwargs):
        calls.append(args)
        raise AssertionError("storage must not be read on a calculation's first deck")

    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _store_log(session, monkeypatch, calc, _gaussian_log("#P b3lyp/6-31g(d) opt int=fine"), "job.log")
        monkeypatch.setattr(cpe, "load_artifact_bytes", spy)
        _read(session, calc, "#P b3lyp/6-31g(d) opt int=ultrafine\n")
        assert calls == []
        # A re-read of the deck does read the stored log (the spy proves the path is live, so the empty list above
        # is not vacuous).
        _read(session, calc, "#P b3lyp/6-31g(d) opt int=ultrafine\n")
        assert len(calls) == 1


def test_a_reread_uses_bytes_from_this_request_before_storage_and_survives_an_outage(db_conn, monkeypatch, caplog):
    from app.services import calculation_parameter_extraction as cpe
    from app.services.artifact_storage import ArtifactStorageUnavailable

    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _read(session, calc, "#P b3lyp/6-31g(d) opt int=ultrafine scf=tight\n")
        log = _gaussian_log("#P b3lyp/6-31g(d) opt int=ultrafine scf=tight")
        _store_log(session, monkeypatch, calc, log, "job.log")
        _read(session, calc, log, kind=ArtifactKind.output_log)

        def outage(*args, **kwargs):
            raise ArtifactStorageUnavailable("storage down")

        # In memory for this request: storage is not touched.
        monkeypatch.setattr(cpe, "load_artifact_bytes", outage)
        cpe.remember_artifact_bytes(session, log.encode())
        _read(session, calc, "#P b3lyp/6-31g(d) opt int=ultrafine\n")
        assert ("scf.convergence", "tight", ArtifactKind.output_log) in _stored(session, calc)

        # Not in memory and storage down: soft failure, the deck's rows stand and a reason is logged.
        session.info.pop("parameter_artifact_bytes", None)
        session.query(CalculationParameter).filter(
            CalculationParameter.calculation_id == calc.id,
            CalculationParameter.artifact_kind == ArtifactKind.output_log,
        ).delete()
        with caplog.at_level("WARNING"):
            _read(session, calc, "#P b3lyp/6-31g(d) opt int=ultrafine\n")
        assert ("grid.quality", "ultrafine", ArtifactKind.input) in _stored(session, calc)
        assert "not re-read" in caplog.text


def test_refresh_rederives_every_log_row_from_the_stored_logs(db_conn, monkeypatch):
    from app.services.calculation_parameter_extraction import refresh_output_log_rows

    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        log = _gaussian_log("#P b3lyp/6-31g(d) opt int=ultrafine scf=tight")
        _store_log(session, monkeypatch, calc, log, "job.log")
        _read(session, calc, log, kind=ArtifactKind.output_log)
        session.query(CalculationParameter).filter(CalculationParameter.calculation_id == calc.id).update(
            {"canonical_value": "stale"}
        )
        refresh_output_log_rows(session, calc)
        assert _stored(session, calc) >= {("scf.convergence", "tight", ArtifactKind.output_log)}
        assert not [row for row in _stored(session, calc) if row[1] == "stale"]


@pytest.mark.parametrize(
    ("declared", "deck"),
    [
        ("RPBE", "#P pbe/6-31g(d) opt\n"),
        ("wB97X-D3", "#P wb97x/6-31g(d) opt\n"),
        ("B97-D3", "#P b97/6-31g(d) opt\n"),
        ("B97", "#P b97-d3/6-31g(d) opt\n"),
    ],
)
def test_a_prefix_or_suffix_that_is_part_of_a_functional_name_is_not_stripped(db_conn, declared, deck):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, method=declared)
        _, warnings = _read(session, calc, deck)
        assert "level_of_theory_disagrees_with_artifact" in _codes(warnings)


def test_the_same_functional_with_its_dispersion_stated_apart_agrees(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, method="B3LYP-D3BJ")
        _, warnings = _read(session, calc, "#P b3lyp/6-31g(d) EmpiricalDispersion=GD3BJ opt\n")
        assert "level_of_theory_disagrees_with_artifact" not in _codes(warnings)
        # Without the dispersion stated, the suffix is part of the declared name and the deck does not have it.
        other = _calc(session, method="B3LYP-D3BJ")
        _, no_disp = _read(session, other, "#P b3lyp/6-31g(d) opt\n")
        assert "level_of_theory_disagrees_with_artifact" in _codes(no_disp)
