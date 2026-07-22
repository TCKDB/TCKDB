"""Tests for the Cartesian Hessian extraction hook fired on artifact upload.

Hook contract (see :mod:`app.services.hessian_extraction`):

- Fires on ``kind='output_log'`` (Gaussian/Molpro, matrix in the log) and
  ``kind='hessian'`` (ORCA ``.hess``, dispatched by kind) artifacts of
  frequency-bearing (``type='freq'`` / ``type='opt'``) calculations.
- Fill-when-absent: stores ``calc_hessian`` only when the calc has none.
- The matrix is bound to the calc's single input geometry, in native
  hartree/bohr² units, with the correct ``source`` and ``parser_version``.
- Refuses to store when binding is ambiguous (natoms mismatch).
- Anything inside the hook failing must NEVER abort the artifact upload.

Mirrors ``test_api_artifact_sp_energy_hook.py``.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.models.calculation import CalculationHessian
from app.db.models.common import HessianSource

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# Real ESS Hessian fixtures and their known dimensions.
GAUSSIAN_LOG = (FIXTURES / "gaussian" / "freq_g09.log").read_bytes()  # 12 atoms
GAUSSIAN_NATOMS = 12
MOLPRO_LOG = (FIXTURES / "molpro" / "molpro_TS_freq.out").read_bytes()  # 5 atoms
MOLPRO_NATOMS = 5
ORCA_HESS = (FIXTURES / "orca" / "Orca_TS_test.hess").read_bytes()  # 6 atoms
ORCA_NATOMS = 6

# Raw first fixture values (hartree/bohr²) that must survive verbatim.
GAUSSIAN_HEAD = 0.410282e-1
MOLPRO_HEAD = 0.3700857
ORCA_HEAD = -6.9820446273e-2


@pytest.fixture
def stub_store_artifact(monkeypatch) -> list[tuple[str, str]]:
    written: list[tuple[str, str]] = []

    def _fake_store(content: bytes, sha256: str) -> str:
        uri = f"s3://test-bucket/{sha256[:2]}/{sha256}"
        written.append((uri, sha256))
        return uri

    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact", _fake_store
    )
    return written


def _b64(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


def _xyz(natoms: int, *, element: str = "C") -> str:
    """A minimal, valid N-atom XYZ; atoms are spaced far apart so no bonds
    are perceived — only the atom count matters for Hessian binding."""
    lines = [str(natoms), "generated"]
    for i in range(natoms):
        lines.append(f"{element} {i * 5.0:.4f} 0.0000 0.0000")
    return "\n".join(lines)


def _artifact(content: bytes, *, kind: str, filename: str) -> dict:
    return {"kind": kind, "filename": filename, "content_base64": _b64(content)}


def _conformer_payload(*, natoms: int, calc_type: str = "freq") -> dict:
    # A freq (or sp) primary calc links the conformer geometry as its input
    # geometry via the standard fallback; opt does not (its real input is the
    # pre-opt xyz), so freq is the natural vehicle for a Hessian.
    calc: dict = {
        "type": calc_type,
        "software_release": {"name": "Gaussian", "version": "09"},
        "level_of_theory": {"method": "wb97xd", "basis": "def2tzvp"},
    }
    if calc_type == "opt":
        calc["opt_result"] = {"converged": True}
    return {
        "species_entry": {"smiles": "C", "charge": 0, "multiplicity": 1},
        "geometry": {"xyz_text": _xyz(natoms)},
        "calculation": calc,
        "label": "hessian-hook",
    }


def _create_calc(client, *, natoms: int, calc_type: str = "freq") -> int:
    resp = client.post(
        "/api/v1/uploads/conformers",
        json=_conformer_payload(natoms=natoms, calc_type=calc_type),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["primary_calculation"]["calculation_id"]


def _stored(db_session: Session, calc_id: int) -> CalculationHessian | None:
    db_session.expire_all()
    return db_session.get(CalculationHessian, calc_id)


class TestHessianHook:
    def test_gaussian_log_fills_hessian_bound_to_input_geometry(
        self, client, db_session, stub_store_artifact
    ):
        calc_id = _create_calc(client, natoms=GAUSSIAN_NATOMS)
        assert _stored(db_session, calc_id) is None

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_artifact(GAUSSIAN_LOG, kind="output_log", filename="freq.log")]},
        )
        assert resp.status_code == 201, resp.text

        row = _stored(db_session, calc_id)
        assert row is not None
        assert row.source is HessianSource.parsed_log
        assert row.natoms == GAUSSIAN_NATOMS
        assert row.parser_version == "hessian_v1"
        n3 = 3 * GAUSSIAN_NATOMS
        assert len(row.lower_triangle_hartree_bohr2) == n3 * (n3 + 1) // 2
        # Native atomic units, verbatim — no J/m² conversion.
        assert row.lower_triangle_hartree_bohr2[0] == GAUSSIAN_HEAD
        # Bound to the calculation's (single) input geometry.
        assert row.geometry_id is not None
        assert row.geometry.natoms == GAUSSIAN_NATOMS

    def test_molpro_log_fills_hessian(
        self, client, db_session, stub_store_artifact
    ):
        calc_id = _create_calc(client, natoms=MOLPRO_NATOMS)

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_artifact(MOLPRO_LOG, kind="output_log", filename="freq.out")]},
        )
        assert resp.status_code == 201, resp.text

        row = _stored(db_session, calc_id)
        assert row is not None
        assert row.source is HessianSource.parsed_log
        assert row.natoms == MOLPRO_NATOMS
        assert row.lower_triangle_hartree_bohr2[0] == MOLPRO_HEAD

    def test_orca_hess_fills_hessian_dispatched_by_kind(
        self, client, db_session, stub_store_artifact
    ):
        calc_id = _create_calc(client, natoms=ORCA_NATOMS)

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_artifact(ORCA_HESS, kind="hessian", filename="ts.hess")]},
        )
        assert resp.status_code == 201, resp.text

        row = _stored(db_session, calc_id)
        assert row is not None
        # A .hess has no banner -> the ORCA path is chosen by artifact kind.
        assert row.source is HessianSource.parsed_hess
        assert row.natoms == ORCA_NATOMS
        assert row.lower_triangle_hartree_bohr2[0] == ORCA_HEAD

    def test_natoms_mismatch_is_not_stored(
        self, client, db_session, stub_store_artifact
    ):
        # Calc geometry has the wrong atom count for the uploaded matrix.
        calc_id = _create_calc(client, natoms=MOLPRO_NATOMS)  # 5, not 12

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_artifact(GAUSSIAN_LOG, kind="output_log", filename="freq.log")]},
        )
        assert resp.status_code == 201, resp.text
        # Refused to bind a 12-atom Hessian to a 5-atom geometry.
        assert _stored(db_session, calc_id) is None

    def test_non_freq_opt_calc_type_is_skipped(
        self, client, db_session, stub_store_artifact
    ):
        # A single-point calc never carries a Hessian; the type gate skips it.
        calc_id = _create_calc(client, natoms=GAUSSIAN_NATOMS, calc_type="sp")

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_artifact(GAUSSIAN_LOG, kind="output_log", filename="freq.log")]},
        )
        assert resp.status_code == 201, resp.text
        assert _stored(db_session, calc_id) is None

    def test_input_kind_gate_blocks_hessian_bearing_log(
        self, client, db_session, stub_store_artifact
    ):
        # The real matrix-bearing log, but tagged 'input'. Only the kind gate
        # stops it — 'input' is not a Hessian-bearing artifact kind.
        calc_id = _create_calc(client, natoms=GAUSSIAN_NATOMS)

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_artifact(GAUSSIAN_LOG, kind="input", filename="freq.in")]},
        )
        assert resp.status_code == 201, resp.text
        assert _stored(db_session, calc_id) is None

    def test_fill_when_absent_does_not_overwrite(
        self, client, db_session, stub_store_artifact
    ):
        calc_id = _create_calc(client, natoms=GAUSSIAN_NATOMS)
        r1 = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_artifact(GAUSSIAN_LOG, kind="output_log", filename="a.log")]},
        )
        assert r1.status_code == 201
        first = _stored(db_session, calc_id)
        assert first is not None
        first_geom = first.geometry_id

        # A second upload (even a different program's matrix) must not replace
        # the existing row.
        r2 = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_artifact(GAUSSIAN_LOG, kind="output_log", filename="b.log")]},
        )
        assert r2.status_code == 201, r2.text
        row = _stored(db_session, calc_id)
        assert row is not None
        assert row.geometry_id == first_geom
        assert row.lower_triangle_hartree_bohr2[0] == GAUSSIAN_HEAD

    def test_non_hessian_log_does_not_raise_or_store(
        self, client, db_session, stub_store_artifact
    ):
        # A freq-type calc but the log carries no force-constant matrix -> no
        # row, and the upload still succeeds.
        calc_id = _create_calc(client, natoms=GAUSSIAN_NATOMS)
        body = b"***  PROGRAM SYSTEM MOLPRO  ***\nno matrix here\n"

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_artifact(body, kind="output_log", filename="nohess.out")]},
        )
        assert resp.status_code == 201, resp.text
        assert _stored(db_session, calc_id) is None


def _computed_species_bundle(*, natoms: int) -> dict:
    """A bundle whose additional freq calc carries its freq log INLINE.

    The freq calc links the conformer geometry as its input geometry (via the
    freq/sp fallback), so the inline Hessian binds to it."""
    return {
        "species_entry": {"smiles": "C", "charge": 0, "multiplicity": 1},
        "conformers": [
            {
                "key": "c0",
                "geometry": {"xyz_text": _xyz(natoms)},
                "primary_calculation": {
                    "key": "opt0",
                    "type": "opt",
                    "software_release": {"name": "Gaussian", "version": "09"},
                    "level_of_theory": {"method": "wb97xd", "basis": "def2tzvp"},
                    "opt_result": {"converged": True},
                },
                "additional_calculations": [
                    {
                        "key": "freq0",
                        "type": "freq",
                        "software_release": {"name": "Gaussian", "version": "09"},
                        "level_of_theory": {
                            "method": "wb97xd",
                            "basis": "def2tzvp",
                        },
                        "artifacts": [
                            _artifact(
                                GAUSSIAN_LOG, kind="output_log", filename="freq0.log"
                            )
                        ],
                    }
                ],
            }
        ],
    }


class TestHessianBundleHook:
    """The extraction must also fire on artifacts uploaded INLINE in a
    contribution bundle, bound to the inline geometry."""

    def test_bundle_inline_log_fills_hessian(
        self, client, db_session, stub_store_artifact
    ):
        resp = client.post(
            "/api/v1/uploads/computed-species",
            json=_computed_species_bundle(natoms=GAUSSIAN_NATOMS),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        calc_id = body["conformers"][0]["additional_calculations"][0][
            "calculation_id"
        ]

        row = _stored(db_session, calc_id)
        assert row is not None
        assert row.source is HessianSource.parsed_log
        assert row.natoms == GAUSSIAN_NATOMS
        assert row.geometry.natoms == GAUSSIAN_NATOMS
        assert row.lower_triangle_hartree_bohr2[0] == GAUSSIAN_HEAD
