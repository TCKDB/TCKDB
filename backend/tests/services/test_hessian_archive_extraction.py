"""The Hessian hook stores a Gaussian archive-block Hessian, or says why it did not (trimmed real n-butane log)."""

from __future__ import annotations

import base64
from pathlib import Path

from sqlalchemy import select

from app.db.models.calculation import Calculation, CalculationHessian
from app.db.models.common import ArtifactKind, HessianSource
from app.schemas.fragments.artifact import ArtifactIn
from app.services import hessian_extraction
from app.services.gaussian_archive_hessian import read_gaussian_archive_hessian
from tests.services.test_hessian_and_scan_geometry_checks import (
    _SOFTWARE,
    _LOT,
    _USER_ID,
    _bundle,
    _geom,
    _hessian_rows,
    _isolated_session,
    _upload,
)

BUTANE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "gaussian_archive" / "n_butane_g16_archive_only.log"
).read_text()


def _xyz(shift: float = 0.0) -> str:
    atoms = read_gaussian_archive_hessian(BUTANE).parsed.reference_coords_angstrom
    rows = "\n".join(f"{e} {x + shift:.8f} {y:.8f} {z:.8f}" for e, x, y, z in atoms)
    return f"{len(atoms)}\nn-butane\n{rows}"


def _butane_freq_calc(session, xyz: str) -> Calculation:
    _upload(
        session,
        _bundle(
            smiles="CCCC",
            multiplicity=1,
            conformer_xyz=xyz,
            additional=[
                {
                    "key": "freq0",
                    "type": "freq",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "freq_result": {"n_imag": 0, "zpe_hartree": 0.1},
                    "input_geometries": [_geom(xyz)],
                }
            ],
        ),
    )
    session.flush()
    return session.scalars(
        select(Calculation).where(Calculation.created_by == _USER_ID, Calculation.type == "freq")
    ).one()


def _artifact(text: str) -> ArtifactIn:
    return ArtifactIn(
        kind=ArtifactKind.output_log, filename="job.log", content_base64=base64.b64encode(text.encode()).decode()
    )


def _hook(session, calc, text: str):
    return hessian_extraction.try_extract_hessian_from_artifact_upload(session, calc, _artifact(text))


def test_the_archive_hessian_is_stored_with_its_source(db_conn) -> None:
    with _isolated_session(db_conn) as session:
        calc = _butane_freq_calc(session, _xyz())

        assert _hook(session, calc, BUTANE) is None

        row = session.scalars(select(CalculationHessian)).one()
        assert row.source is HessianSource.parsed_log_archive
        assert row.natoms == 14
        assert len(row.lower_triangle_hartree_bohr2) == 903


def test_an_archive_hessian_is_not_bound_to_a_geometry_in_another_frame(db_conn) -> None:
    with _isolated_session(db_conn) as session:
        calc = _butane_freq_calc(session, _xyz(shift=1.0))

        warning = _hook(session, calc, BUTANE)

        assert warning is not None
        assert warning.code == "hessian_not_stored_geometry_mismatch"
        assert warning.context["reason"] == "frame"
        assert _hessian_rows(session) == 0


def test_an_archive_whose_frame_cannot_be_established_warns(db_conn) -> None:
    text = "\n".join(line for line in BUTANE.splitlines() if "Input orientation" not in line) + "\n"
    with _isolated_session(db_conn) as session:
        calc = _butane_freq_calc(session, _xyz())

        warning = _hook(session, calc, text)

        assert warning is not None
        assert warning.code == "hessian_not_stored_unparseable"
        assert warning.context["reason"] == "archive_orientation_unverified"
        assert "job.log" not in warning.message
        assert _hessian_rows(session) == 0


def test_a_truncated_archive_matrix_warns(db_conn) -> None:
    lines = BUTANE.splitlines()
    start = max(i for i, line in enumerate(lines) if line.startswith(" 1\\1\\GINC"))
    del lines[start + 80 : start + 82]
    with _isolated_session(db_conn) as session:
        calc = _butane_freq_calc(session, _xyz())

        warning = _hook(session, calc, "\n".join(lines) + "\n")

        assert warning is not None
        assert warning.code == "hessian_not_stored_unparseable"
        assert warning.context["reason"] == "archive_block_unreadable"
        assert _hessian_rows(session) == 0
