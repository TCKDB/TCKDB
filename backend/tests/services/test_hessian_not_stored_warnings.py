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
from app.db.models.common import ArtifactKind
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
        assert "freq.log" in warning.message
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
