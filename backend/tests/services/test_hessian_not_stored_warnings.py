"""The Hessian hook tells the depositor when it did not store a Hessian (#691).

The hook is best-effort and must never refuse an upload; what it owes the
depositor is a warn-tier ``hessian_not_stored_*`` warning naming the reason.
Every case also asserts that no ``calc_hessian`` row exists and that the
caller's session is still usable, so a warning cannot stand in for a refusal.
"""

from __future__ import annotations

import base64

import pytest
from sqlalchemy import delete, select

from app.db.models.calculation import Calculation, CalculationInputGeometry
from app.db.models.common import ArtifactKind, CalculationType
from app.schemas.fragments.artifact import ArtifactIn
from app.services import hessian_extraction
from tests.services.test_hessian_and_scan_geometry_checks import (
    _H2,
    _HN,
    _artifact,
    _freq_calc_with_one_input_geometry,
    _hessian_rows,
    _isolated_session,
    _patch_parser,
    _relink_input_geometry,
)


def _hook(session, calc, artifact=None):
    return hessian_extraction.try_extract_hessian_from_artifact_upload(
        session, calc, artifact if artifact is not None else _artifact()
    )


def test_a_stored_hessian_returns_no_warning(db_conn, monkeypatch) -> None:
    _patch_parser(monkeypatch)
    with _isolated_session(db_conn) as session:
        calc = _freq_calc_with_one_input_geometry(session)
        assert _hook(session, calc) is None
        assert _hessian_rows(session) == 1


@pytest.mark.parametrize(
    ("xyz", "isotopes"),
    [(_H2, {1: 2, 2: 2}), (_HN, None)],
    ids=["wrong_isotope", "wrong_element"],
)
def test_a_composition_or_isotope_refusal_warns(db_conn, monkeypatch, xyz, isotopes) -> None:
    _patch_parser(monkeypatch)
    with _isolated_session(db_conn) as session:
        calc = _freq_calc_with_one_input_geometry(session)
        _relink_input_geometry(session, calc, xyz, isotopes)
        warning = _hook(session, calc)

        assert warning is not None
        assert warning.code == "hessian_not_stored_geometry_mismatch"
        assert warning.context["depositor_supplied"] == {"artifact_filename": "freq.log"}
        assert "freq.log" not in warning.message
        assert _hessian_rows(session) == 0
        session.flush()  # the transaction is still usable


def test_no_unique_input_geometry_warns(db_conn, monkeypatch) -> None:
    _patch_parser(monkeypatch)
    with _isolated_session(db_conn) as session:
        calc = _freq_calc_with_one_input_geometry(session)
        session.execute(
            delete(CalculationInputGeometry).where(CalculationInputGeometry.calculation_id == calc.id)
        )
        session.flush()
        warning = _hook(session, calc)

        assert warning is not None
        assert warning.code == "hessian_not_stored_no_unique_input_geometry"
        assert _hessian_rows(session) == 0


def test_an_unreadable_hess_file_warns_but_a_matrixless_log_does_not(db_conn) -> None:
    with _isolated_session(db_conn) as session:
        calc = _freq_calc_with_one_input_geometry(session)

        def art(kind, body):
            return ArtifactIn(
                kind=kind, filename="x.hess" if kind is ArtifactKind.hessian else "x.log", content_base64=base64.b64encode(body).decode()
            )

        hess = _hook(session, calc, art(ArtifactKind.hessian, b"$not_a_hessian\n"))
        assert hess is not None and hess.code == "hessian_not_stored_unparseable"

        log = _hook(session, calc, art(ArtifactKind.output_log, b"no matrix, no banner\n"))
        assert log is None


def test_a_log_with_a_force_constant_block_but_no_matrix_warns(db_conn) -> None:
    """A truncated block (or one the parser declines) is not the same as a log with no block (#744)."""
    from app.services.hessian_parsing import GAUSSIAN_HESSIAN_MARKER

    truncated = (
        b" Entering Gaussian System, Link 0=g16\n Gaussian 16: ES64L-G16RevC.01\n"
        + GAUSSIAN_HESSIAN_MARKER.encode()
        + b"\n                1             2\n      1  0.5D+00\n"
    )
    with _isolated_session(db_conn) as session:
        calc = _freq_calc_with_one_input_geometry(session)
        art = ArtifactIn(
            kind=ArtifactKind.output_log, filename="cut.log", content_base64=base64.b64encode(truncated).decode()
        )
        warning = _hook(session, calc, art)
        assert warning is not None and warning.code == "hessian_not_stored_unparseable"
        assert warning.context["depositor_supplied"] == {"artifact_filename": "cut.log"}
        assert "cut.log" not in warning.message
        assert _hessian_rows(session) == 0


def test_a_hess_file_on_a_non_frequency_calculation_warns_without_parsing(db_conn, monkeypatch) -> None:
    def must_not_parse(*args, **kwargs):
        raise AssertionError("the calculation-type gate needs no parse to warn")

    monkeypatch.setattr(hessian_extraction, "parse_hessian_from_artifact", must_not_parse)
    with _isolated_session(db_conn) as session:
        calc = _freq_calc_with_one_input_geometry(session)
        calc.type = CalculationType.opt
        art = ArtifactIn(
            kind=ArtifactKind.hessian, filename="x.hess", content_base64=base64.b64encode(b"$hessian\n").decode()
        )
        warning = _hook(session, calc, art)
        assert warning is not None and warning.code == "hessian_not_stored_not_frequency_calculation"
        assert warning.field == "artifacts"
        # An output log on the same calculation stays silent: its freq copy stores the matrix.
        log = ArtifactIn(
            kind=ArtifactKind.output_log, filename="x.log", content_base64=base64.b64encode(b"log").decode()
        )
        assert _hook(session, calc, log) is None


def test_the_warning_field_can_name_the_calculation(db_conn, monkeypatch) -> None:
    _patch_parser(monkeypatch)
    with _isolated_session(db_conn) as session:
        calc = _freq_calc_with_one_input_geometry(session)
        session.execute(
            delete(CalculationInputGeometry).where(CalculationInputGeometry.calculation_id == calc.id)
        )
        session.flush()
        warning = hessian_extraction.try_extract_hessian_from_artifact_upload(
            session, calc, _artifact(), field="calculations['freq7'].artifacts"
        )
        assert warning is not None and warning.field == "calculations['freq7'].artifacts"


class _Orig(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


@pytest.mark.parametrize(
    ("sqlstate", "expected"),
    [("23505", None), ("23514", "hessian_not_stored_failed")],
    ids=["lost_the_race", "check_constraint"],
)
def test_only_a_unique_violation_is_a_lost_race(db_conn, monkeypatch, sqlstate, expected) -> None:
    """Losing the race to store the same Hessian is silent; any other integrity error is a failure.

    Mutation: treat every ``IntegrityError`` as a lost race and the second case goes silent; make the
    race branch warn and the first case warns.
    """
    from sqlalchemy.exc import IntegrityError

    _patch_parser(monkeypatch)

    def refuse(*args, **kwargs):
        raise IntegrityError("INSERT", {}, _Orig(sqlstate))

    monkeypatch.setattr(hessian_extraction, "CalculationHessian", refuse)
    with _isolated_session(db_conn) as session:
        calc = _freq_calc_with_one_input_geometry(session)
        warning = _hook(session, calc)
        assert (warning.code if warning else None) == expected
        assert _hessian_rows(session) == 0
        session.flush()  # the transaction is still usable


def test_no_reason_text_can_carry_a_row_id() -> None:
    """The ``_not_stored`` reasons are user-facing text and sit outside the error-body sweep.

    Static: no argument of any ``_not_stored`` call may interpolate an identifier that names an id
    (``calculation.id``, ``geometry_id``); ``len(...)`` of an id list is a count, not an id.
    Mutation: put ``{calculation.id}`` in one reason and this fails.
    """
    import ast
    import inspect
    import re

    tree = ast.parse(inspect.getsource(hessian_extraction))
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_not_stored"
    ]
    assert len(calls) >= 8, "the sweep found too few _not_stored calls to mean anything"
    id_name = re.compile(r"(^|_)ids?$")
    offenders = []
    for call in calls:
        counted = {id(a) for n in ast.walk(call) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "len" for a in n.args}
        for node in ast.walk(call):
            name = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None
            if name and id_name.search(name) and id(node) not in counted:
                offenders.append(f"line {node.lineno}: {name}")
    assert offenders == []


def test_an_existing_hessian_is_not_a_failure(db_conn, monkeypatch) -> None:
    _patch_parser(monkeypatch)
    with _isolated_session(db_conn) as session:
        calc = _freq_calc_with_one_input_geometry(session)
        assert _hook(session, calc) is None
        session.expire(calc, ["hessian"])
        assert _hook(session, calc) is None  # fill-when-absent: nothing to say


def test_an_unexpected_failure_warns_and_does_not_raise(db_conn, monkeypatch) -> None:
    _patch_parser(monkeypatch)

    def boom(*args, **kwargs):
        raise RuntimeError("simulated")

    monkeypatch.setattr(hessian_extraction, "_resolve_input_geometry_id", boom)
    with _isolated_session(db_conn) as session:
        calc = _freq_calc_with_one_input_geometry(session)
        warning = _hook(session, calc)

        assert warning is not None
        assert warning.code == "hessian_not_stored_failed"
        assert session.get(Calculation, calc.id) is not None
        assert session.scalars(select(Calculation)).first() is not None
