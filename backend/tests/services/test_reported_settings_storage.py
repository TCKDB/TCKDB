"""Reported grid and SCF values: stored beside the requested rows, ranked on read, and checked against presets.

Every "silent" case has a twin that fires or changes, so a rung that does nothing cannot pass.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.calculation import CalculationArtifact, CalculationParameter
from app.db.models.common import ArtifactKind, CalculationType, ParameterSource, ParameterStatementKind
from app.schemas.fragments.calculation import (
    CalculationParameterObservation,
    CalculationWithResultsPayload,
    OptResultPayload,
)
from app.schemas.reads.scientific_calculation import EffectiveSettingState
from app.services.calculation_parameter_extraction import extract_and_store_calculation_parameters
from app.services.calculation_resolution import (
    persist_calculation_parameters,
    resolve_and_persist_calculation_with_results,
)
from app.services.scientific_read.effective_settings import build_effective_settings
from tests.services.test_calculation_parameter_extraction import _create_species_entry, _next_inchi_key

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_ORCA_LOG = (FIXTURES / "orca" / "opt_orca.out").read_text(errors="replace")  # ORCA 6.1.0, defgrid2, TightSCF
_ORCA_DECK = "!B3LYP DEF2-TZVP D3BJ\n!OPT FREQ TightOPT TightSCF defgrid2\n* xyz 0 1\nH 0 0 0\n*\n"
_G09_LOG = (FIXTURES / "gaussian" / "freq_g09.log").read_text(errors="replace")  # integral=(grid=ultrafine


def _arc_gaussian_default_grid_log() -> str:
    payload = next((FIXTURES / "arc_runs" / "rotor_scan_8").glob("tckdb_payloads/computed_reaction/*.payload.json"))
    texts: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("kind") == "output_log" and "content_base64" in node:
                texts.append(base64.b64decode(node["content_base64"]).decode(errors="replace"))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(json.loads(payload.read_text()))
    return next(t for t in texts if "Entering Gaussian System" in t and "IRadAn=           0 IRanWt" in t)


def _calc(session: Session, *, software: str = "orca", version: str | None = "6.0.0", method: str = "B3LYP"):
    entry_id = _create_species_entry(session, inchi_key=_next_inchi_key("RPTD"))
    release = {"name": software}
    if version is not None:
        release["version"] = version
    upload = CalculationWithResultsPayload(
        type=CalculationType.opt,
        software_release=release,
        level_of_theory={"method": method, "basis": "def2-TZVP"},
        opt_result=OptResultPayload(converged=True),
    )
    return resolve_and_persist_calculation_with_results(session, upload, species_entry_id=entry_id)


def _read(session, calc, text, kind=ArtifactKind.output_log):
    warnings: list = []
    extract_and_store_calculation_parameters(session, calc, text, warnings=warnings, artifact_kind=kind)
    return warnings


def _attach_log_artifact(session, calc, text: str = "log") -> None:
    content = text.encode()
    sha = hashlib.sha256(content).hexdigest()
    session.add(
        CalculationArtifact(
            calculation_id=calc.id, kind=ArtifactKind.output_log, uri=f"s3://t/{sha}", sha256=sha, bytes=len(content), filename="job.log"
        )
    )
    session.flush()


def _typed(session, calc, key, value):
    persist_calculation_parameters(
        session, calc, [CalculationParameterObservation(raw_key="typed", raw_value=value, canonical_key=key, canonical_value=value)]
    )


def _rows(session, calc):
    return session.scalars(select(CalculationParameter).where(CalculationParameter.calculation_id == calc.id)).all()


def _by_setting(session, calc):
    return {s.setting: s for s in build_effective_settings(session, calc)}


def _stored(session, calc):
    return {
        (r.canonical_key or r.raw_key, r.canonical_value or r.raw_value, r.artifact_kind, r.statement_kind)
        for r in _rows(session, calc)
        if r.source is ParameterSource.parser
    }


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------


def test_a_log_stores_its_echo_as_requested_and_its_numbers_as_reported(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _read(session, calc, _ORCA_LOG)
        by_key = {(r.canonical_key, r.statement_kind): r for r in _rows(session, calc) if r.canonical_key}
        assert by_key[("grid.quality", ParameterStatementKind.requested)].canonical_value == "defgrid2"
        assert by_key[("scf.convergence", ParameterStatementKind.requested)].canonical_value == "tight"
        assert by_key[("grid.angular_max", ParameterStatementKind.reported)].canonical_value == "302"
        # A report never becomes a requested row, and no report writes grid.quality or scf.convergence.
        assert ("grid.quality", ParameterStatementKind.reported) not in by_key
        assert ("scf.convergence", ParameterStatementKind.reported) not in by_key
        assert all(
            r.artifact_kind is ArtifactKind.output_log
            for r in _rows(session, calc)
            if r.statement_kind is ParameterStatementKind.reported
        )


def test_a_deck_row_is_requested_and_a_typed_row_has_no_statement_kind(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _typed(session, calc, "grid.quality", "defgrid3")
        _read(session, calc, _ORCA_DECK, kind=ArtifactKind.input)
        rows = _rows(session, calc)
        assert {r.statement_kind for r in rows if r.source is ParameterSource.parser} == {ParameterStatementKind.requested}
        assert {r.statement_kind for r in rows if r.source is ParameterSource.upload} == {None}


def test_the_gaussian_grid_is_stored_as_the_raw_code_with_no_canonical_value(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software="gaussian", version="09")
        _read(session, calc, _G09_LOG)
        [row] = [r for r in _rows(session, calc) if r.raw_key == "IRadAn"]
        assert (row.raw_value, row.canonical_key, row.canonical_value) == ("5", None, None)
        assert row.statement_kind is ParameterStatementKind.reported


@pytest.mark.parametrize("deck_first", [True, False])
def test_deck_and_log_end_in_the_same_rows_whichever_arrives_first_including_reported(db_conn, deck_first):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        steps = [(_ORCA_DECK, ArtifactKind.input), (_ORCA_LOG, ArtifactKind.output_log)]
        for text, kind in steps if deck_first else steps[::-1]:
            _read(session, calc, text, kind=kind)
        stored = _stored(session, calc)
        # The log's echo outranks the deck: the deck adds no grid or SCF row; the report is kept beside the echo.
        assert ("grid.quality", "defgrid2", ArtifactKind.output_log, ParameterStatementKind.requested) in stored
        assert not [r for r in stored if r[2] is ArtifactKind.input and r[0] in ("grid.quality", "scf.convergence")]
        assert ("grid.angular_max", "302", ArtifactKind.output_log, ParameterStatementKind.reported) in stored
        keys = [(r[0], r[3]) for r in stored if r[0] in ("grid.quality", "scf.convergence", "grid.angular_max")]
        assert len(keys) == len(set(keys))


def test_both_orders_store_identical_rows(db_conn):
    results = []
    for deck_first in (True, False):
        with Session(db_conn) as session, session.begin():
            calc = _calc(session)
            steps = [(_ORCA_DECK, ArtifactKind.input), (_ORCA_LOG, ArtifactKind.output_log)]
            for text, kind in steps if deck_first else steps[::-1]:
                _read(session, calc, text, kind=kind)
            results.append(_stored(session, calc))
    assert results[0] == results[1]


def test_a_second_log_never_replaces_the_first_logs_reported_value(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _read(session, calc, _ORCA_LOG)
        other = _ORCA_LOG.replace("AngularGrid ... 4 (Lebedev-302)", "AngularGrid ... 6 (Lebedev-590)")
        warnings = _read(session, calc, other)
        assert "output_log_disagrees_with_earlier_output_log" in [w.code for w in warnings]
        reported = [r for r in _rows(session, calc) if r.canonical_key == "grid.angular_max"]
        assert [r.canonical_value for r in reported] == ["302"]


# ---------------------------------------------------------------------------
# the warning
# ---------------------------------------------------------------------------


def _with_echo(log: str, old: str, new: str) -> str:
    """Change the input the log echoes (the `|  2> !OPT ...` line), leaving every printed report as it was."""
    lines = log.split("\n")
    for i, line in enumerate(lines):
        if re.match(r"\|\s+\d+> ", line) and old in line:
            lines[i] = line.replace(old, new)
            break
    else:
        raise AssertionError(old)
    return "\n".join(lines)


def test_requested_defgrid3_against_reported_defgrid2_numbers_warns(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        warnings = _read(session, calc, _with_echo(_ORCA_LOG, "defgrid2", "defgrid3"))
        [warning] = [w for w in warnings if w.code == "reported_setting_differs_from_requested"]
        assert warning.context["canonical_key"] == "grid.angular_max"
        assert warning.context["depositor_supplied"] == {"requested_value": "defgrid3", "reported_value": "302"}
        assert "defgrid" not in warning.message.lower() and "302" not in warning.message
        # Both rows stay.
        stored = _stored(session, calc)
        assert ("grid.quality", "defgrid3", ArtifactKind.output_log, ParameterStatementKind.requested) in stored
        assert ("grid.angular_max", "302", ArtifactKind.output_log, ParameterStatementKind.reported) in stored


def test_requested_verytight_against_reported_tight_thresholds_warns(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        warnings = _read(session, calc, _with_echo(_ORCA_LOG, "TightSCF", "VeryTightSCF"))
        keys = {w.context["canonical_key"] for w in warnings if w.code == "reported_setting_differs_from_requested"}
        assert "scf.tol_energy_eh" in keys


def test_agreement_is_silent(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        assert "reported_setting_differs_from_requested" not in [w.code for w in _read(session, calc, _ORCA_LOG)]


def test_the_warning_fires_when_the_deck_arrives_after_the_log_too(db_conn):
    """Log first (echoes defgrid2, reports 302), then a deck requesting nothing the log echoes: silent; and a log whose
    echo is silent on the grid, with a deck that requests defgrid3, warns once the deck is read."""
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        silent_echo = _with_echo(_ORCA_LOG, "defgrid2", "")
        assert "reported_setting_differs_from_requested" not in [w.code for w in _read(session, calc, silent_echo)]
        deck = _ORCA_DECK.replace("defgrid2", "defgrid3")
        warnings = _read(session, calc, deck, kind=ArtifactKind.input)
        assert "reported_setting_differs_from_requested" in [w.code for w in warnings]


def test_gaussian_never_gets_the_warning(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software="gaussian", version="09")
        _typed(session, calc, "grid.quality", "superfine")
        assert "reported_setting_differs_from_requested" not in [w.code for w in _read(session, calc, _G09_LOG)]


# ---------------------------------------------------------------------------
# effective settings: reported > echo > deck > typed > assumed
# ---------------------------------------------------------------------------


def test_rung_1_a_reported_value_beats_a_requested_one(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _typed(session, calc, "grid.quality", "defgrid1")
        _read(session, calc, _ORCA_LOG)
        grid = _by_setting(session, calc)["grid"]
        assert grid.state is EffectiveSettingState.reported
        assert {(r.key, r.value) for r in grid.reported} == {("grid.integration_accuracy", "4.388"), ("grid.angular_max", "302")}
        assert (grid.requested_value, grid.requested_from) == ("defgrid2", "output_log")
        assert _by_setting(session, calc)["scf_convergence"].state is EffectiveSettingState.reported


def test_rung_2_the_logs_echo_beats_the_deck_which_beats_the_depositor(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _typed(session, calc, "grid.quality", "defgrid1")
        grid = _by_setting(session, calc)["grid"]
        assert (grid.state, grid.requested_value, grid.requested_from) == (EffectiveSettingState.requested, "defgrid1", "typed")
        _read(session, calc, _ORCA_DECK.replace("defgrid2", "defgrid3"), kind=ArtifactKind.input)
        grid = _by_setting(session, calc)["grid"]
        assert (grid.requested_value, grid.requested_from) == ("defgrid3", "input")
        _read(session, calc, _with_echo(_ORCA_LOG, "TightOPT", "TightOPT").replace("Lebedev-302", "Lebedev-590"))
        grid = _by_setting(session, calc)["grid"]
        assert (grid.requested_value, grid.requested_from) == ("defgrid2", "output_log")


def test_rung_2_without_a_report_the_requested_value_decides(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        _read(session, calc, _ORCA_DECK, kind=ArtifactKind.input)
        grid = _by_setting(session, calc)["grid"]
        assert (grid.state, grid.requested_value, grid.requested_from, grid.assumed_value) == (
            EffectiveSettingState.requested, "defgrid2", "input", None,
        )


def test_rung_3_with_nothing_stated_the_assumed_default_is_labelled_and_never_stored(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software="orca", version="6.0.0")
        grid = _by_setting(session, calc)["grid"]
        assert (grid.state, grid.assumed_value) == (EffectiveSettingState.assumed_default_no_output_log, "defgrid2")
        _attach_log_artifact(session, calc)  # a log exists, but says nothing about the grid
        grid = _by_setting(session, calc)["grid"]
        assert (grid.state, grid.assumed_value) == (EffectiveSettingState.assumed_default, "defgrid2")
        assert not _rows(session, calc)  # read-time only: nothing was written


@pytest.mark.parametrize(
    ("software", "version", "expected"),
    [("gaussian", "09", "fine"), ("gaussian", "16", "ultrafine"), ("orca", "5.0.4", "defgrid2")],
)
def test_the_assumed_default_follows_program_and_version(db_conn, software, version, expected):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software=software, version=version)
        assert _by_setting(session, calc)["grid"].assumed_value == expected


def test_rung_4_without_a_declared_version_nothing_is_guessed(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software="gaussian", version=None)
        settings = _by_setting(session, calc)
        assert settings["grid"].state is EffectiveSettingState.not_stated and settings["grid"].assumed_value is None
        assert settings["scf_convergence"].state is EffectiveSettingState.not_stated  # no SCF default is documented


def test_a_gaussian_grid_report_is_listed_but_does_not_decide_the_state(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software="gaussian", version="09")
        _read(session, calc, _arc_gaussian_default_grid_log())
        grid = _by_setting(session, calc)["grid"]
        assert [(r.key, r.value) for r in grid.reported] == [("IRadAn", "0")]
        assert grid.state is EffectiveSettingState.assumed_default and grid.assumed_value == "fine"
        # The SCF thresholds of the same log are numbers, and do decide.
        assert _by_setting(session, calc)["scf_convergence"].state is EffectiveSettingState.reported
        # A route that names the grid is the requested value, ranked above the assumed default.
        other = _calc(session, software="gaussian", version="09")
        _read(session, other, _G09_LOG)
        assert _by_setting(session, other)["grid"].state is EffectiveSettingState.requested


def test_the_read_ranks_an_echo_above_a_deck_even_when_both_rows_are_stored(db_conn):
    """Storage never keeps both for one setting, but the read must not depend on that: rows put there directly."""
    from app.db.models.calculation import CalculationParameterVocab

    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        if session.get(CalculationParameterVocab, "grid.quality") is None:
            session.add(CalculationParameterVocab(canonical_key="grid.quality"))
            session.flush()
        for kind, value in ((ArtifactKind.input, "defgrid1"), (ArtifactKind.output_log, "defgrid3")):
            session.add(
                CalculationParameter(
                    calculation_id=calc.id, raw_key="g", raw_value=value, canonical_key="grid.quality",
                    canonical_value=value, source=ParameterSource.parser, artifact_kind=kind,
                    statement_kind=ParameterStatementKind.requested,
                )
            )
        session.flush()
        grid = _by_setting(session, calc)["grid"]
        assert (grid.requested_value, grid.requested_from) == ("defgrid3", "output_log")


@pytest.mark.parametrize("method", ["HF", "MP2", "DLPNO-CCSD(T)", "CCSD(T)-F12"])
def test_no_assumed_grid_for_a_method_without_a_dft_grid(db_conn, method):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software="orca", version="5.0.4", method=method)
        grid = _by_setting(session, calc)["grid"]
        assert (grid.state, grid.assumed_value) == (EffectiveSettingState.not_stated, None)
    # ...and the same program and version with a DFT method does show it, so the omission is not vacuous.
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software="orca", version="5.0.4", method="B3LYP")
        assert _by_setting(session, calc)["grid"].assumed_value == "defgrid2"


def test_a_version_with_the_program_name_in_front_still_parses(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software="gaussian", version="Gaussian 16")
        assert _by_setting(session, calc)["grid"].assumed_value == "ultrafine"


# ---------------------------------------------------------------------------
# review fixes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("deck_first", [True, False])
def test_the_warning_is_given_once_whatever_the_upload_order_and_not_again_later(db_conn, deck_first):
    deck = _ORCA_DECK.replace("defgrid2", "defgrid3")
    log = _with_echo(_ORCA_LOG, "defgrid2", "defgrid3")
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        collected = []
        for text, kind in [(deck, ArtifactKind.input), (log, ArtifactKind.output_log)][:: 1 if deck_first else -1]:
            collected += _read(session, calc, text, kind=kind)
        mismatches = [w for w in collected if w.code == "reported_setting_differs_from_requested"]
        assert [w.context["canonical_key"] for w in mismatches] == ["grid.angular_max"]
        # An unrelated later upload (a second copy of the deck) repeats nothing.
        again = _read(session, calc, deck, kind=ArtifactKind.input)
        assert "reported_setting_differs_from_requested" not in [w.code for w in again]


def test_explicit_tolerance_beats_the_keyword_preset_and_raises_no_false_warning(db_conn):
    explicit = _ORCA_LOG.replace("|  4>     MaxIter 2000", "|  4>     TolE 1e-6\n|  4>     MaxIter 2000", 1)
    assert "TolE 1e-6" in explicit
    # The log still reports TolE 1e-8 (TightSCF) against an explicit 1e-6: a real contradiction, so it warns...
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        keys = {w.context["canonical_key"] for w in _read(session, calc, explicit) if w.code == "reported_setting_differs_from_requested"}
        assert keys == {"scf.tol_energy_eh"}
    # ...and when the log reports the explicit value, TightSCF's 1e-8 preset is not held against it.
    honoured = explicit.replace("TolE            ....  1.000e-08 Eh", "TolE            ....  1.000e-06 Eh")
    assert honoured != explicit
    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        assert "reported_setting_differs_from_requested" not in [w.code for w in _read(session, calc, honoured)]


def test_crlf_logs_are_read_like_lf_logs():
    from app.services.reported_settings_parser import parse_orca_reported

    assert parse_orca_reported(_ORCA_LOG.replace("\n", "\r\n")) == parse_orca_reported(_ORCA_LOG)
    assert parse_orca_reported(_ORCA_LOG)


def test_an_orca_file_with_new_job_in_its_echoed_input_reports_nothing():
    """Synthetic: a `$new_job` section spliced into a real fixture's echoed input (the fixture itself has one job)."""
    from app.services.reported_settings_parser import parse_orca_reported

    spliced = _ORCA_LOG.replace("|  2> !OPT FREQ", "|  2> $new_job\n|  2> !OPT FREQ", 1)
    assert spliced != _ORCA_LOG
    assert parse_orca_reported(_ORCA_LOG) and parse_orca_reported(spliced) == []
    assert parse_orca_reported(_ORCA_LOG.replace("|  2> !OPT FREQ", "|  2> %compound", 1).replace("%compound", "%Compound")) == []


def test_two_logs_with_different_gaussian_grid_codes_disagree(db_conn):
    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software="gaussian", version="09")
        _read(session, calc, _G09_LOG)
        other = _G09_LOG.replace("IRadAn=           5 IRanWt", "IRadAn=           0 IRanWt")
        assert other != _G09_LOG
        codes = [w.code for w in _read(session, calc, other)]
        assert "output_log_disagrees_with_earlier_output_log" in codes
        [warning] = [w for w in _read(session, calc, other.replace("IRadAn=           0", "IRadAn=           4")) if w.code == "output_log_disagrees_with_earlier_output_log"]
        assert warning.context["raw_key"] == "IRadAn" and "canonical_key" not in warning.context
        assert [r.raw_value for r in _rows(session, calc) if r.raw_key == "IRadAn"] == ["5"]


def test_a_rung_with_conflicting_values_is_marked_and_does_not_fall_through(db_conn):
    from app.db.models.calculation import CalculationParameterVocab

    with Session(db_conn) as session, session.begin():
        calc = _calc(session)
        if session.get(CalculationParameterVocab, "grid.quality") is None:
            session.add(CalculationParameterVocab(canonical_key="grid.quality"))
            session.flush()
        for kind, value in ((ArtifactKind.output_log, "defgrid1"), (ArtifactKind.output_log, "defgrid3"), (ArtifactKind.input, "defgrid2")):
            session.add(
                CalculationParameter(
                    calculation_id=calc.id, raw_key="g", raw_value=value, canonical_key="grid.quality", canonical_value=value,
                    source=ParameterSource.parser, artifact_kind=kind, statement_kind=ParameterStatementKind.requested,
                )
            )
        session.flush()
        grid = _by_setting(session, calc)["grid"]
        assert (grid.state, grid.requested_from, grid.requested_value, grid.conflicting) == (
            EffectiveSettingState.requested, "output_log", None, True,
        )
        # Without the conflict the same rung gives a value, so the marker is not vacuous.
        session.query(CalculationParameter).filter(CalculationParameter.canonical_value == "defgrid1").delete()
        session.expire_all()
        grid = _by_setting(session, calc)["grid"]
        assert (grid.requested_value, grid.conflicting) == ("defgrid3", False)


def test_refresh_rebuilds_deck_rows_from_the_deck_the_caller_chose(db_conn, monkeypatch):
    from app.services import calculation_parameter_extraction as cpe

    store: dict[str, bytes] = {}
    monkeypatch.setattr(cpe, "load_artifact_bytes", lambda digest, **_: store[digest])

    def add_deck(session, calc, text, name):
        content = text.encode()
        sha = hashlib.sha256(content).hexdigest()
        store[sha] = content
        artifact = CalculationArtifact(
            calculation_id=calc.id, kind=ArtifactKind.input, uri=f"s3://t/{sha}", sha256=sha, bytes=len(content), filename=name
        )
        session.add(artifact)
        session.flush()
        return artifact

    def scf(session, calc):
        return {r.canonical_value for r in _rows(session, calc) if r.canonical_key == "scf.convergence"}

    with Session(db_conn) as session, session.begin():
        calc = _calc(session, software="gaussian", version="16")
        deck_a = add_deck(session, calc, "#P b3lyp/6-31g(d) opt scf=tight\n", "a.gjf")
        deck_b = add_deck(session, calc, "#P b3lyp/6-31g(d) opt scf=verytight\n", "b.gjf")
        _read(session, calc, deck_a.sha256 and store[deck_a.sha256].decode(), kind=ArtifactKind.input)
        _read(session, calc, store[deck_b.sha256].decode(), kind=ArtifactKind.input)
        assert scf(session, calc) == {"very_tight"}
        cpe.refresh_output_log_rows(session, calc, deck_artifact=deck_a)  # the operator chose deck A
        assert scf(session, calc) == {"tight"}
        cpe.refresh_output_log_rows(session, calc, deck_artifact=deck_b)
        assert scf(session, calc) == {"very_tight"}
        cpe.refresh_output_log_rows(session, calc)  # default: the deck an upload leaves standing, the latest
        assert scf(session, calc) == {"very_tight"}
