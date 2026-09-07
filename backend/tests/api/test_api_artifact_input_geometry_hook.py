"""Tests for the input-geometry extraction hook fired on artifact upload.

Hook contract (see :mod:`app.services.input_geometry_extraction`):

- Fires on ``kind='input'`` and ``kind='output_log'`` artifacts of
  ``type='opt'`` calculations only.
- Fill-when-absent-or-degenerate: acts only when the calculation has no
  input geometry, or one that duplicates its (single) output geometry --
  never when it already carries a distinct, real input geometry.
- The extracted geometry is minted/deduped like any other
  ``Geometry`` row and linked with ``source='extracted_from_artifact'``;
  the output link is never touched.
- Z-matrix (Gaussian) / ``* xyzfile`` / ``* int`` (ORCA) input decks are
  rejected, never guessed at -- the upload still succeeds and nothing is
  linked.
- Anything inside the hook failing must NEVER abort the artifact upload.

Mirrors ``test_api_artifact_hessian_hook.py``.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.calculation import (
    Calculation,
    CalculationInputGeometry,
    CalculationOutputGeometry,
)
from app.db.models.common import (
    AppUserRole,
    ArtifactKind,
    CalculationInputGeometrySource,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.services.input_geometry_extraction import (
    InputGeometryOutcomeKind,
    extract_and_link_input_geometry,
)
from app.services.record_review import ensure_record_review, set_record_review_status

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
INPUT_GEOMETRY_FIXTURES = FIXTURES / "input_geometry"

GAUSSIAN_GJF = (INPUT_GEOMETRY_FIXTURES / "gaussian_opt_input.gjf").read_bytes()
GAUSSIAN_GJF_ZMATRIX = (
    INPUT_GEOMETRY_FIXTURES / "gaussian_opt_input_zmatrix.gjf"
).read_bytes()
ORCA_INP = (INPUT_GEOMETRY_FIXTURES / "orca_opt_input.inp").read_bytes()
ORCA_INP_XYZFILE = (
    INPUT_GEOMETRY_FIXTURES / "orca_opt_input_xyzfile.inp"
).read_bytes()
ORCA_LOG_HEAD = (INPUT_GEOMETRY_FIXTURES / "orca_opt_output_head.out").read_bytes()
GAUSSIAN_OPT_LOG = (FIXTURES / "gaussian" / "opt_g09.log").read_bytes()  # 12 atoms

# opt_g09.log's SMILES, matching the existing test_geometry_validation.py
# fixture use of the same file (N2C3H7 doublet radical).
N2C3H7_SMILES = "[N]=NCCC"


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


def _artifact(content: bytes, *, kind: str, filename: str) -> dict:
    return {"kind": kind, "filename": filename, "content_base64": _b64(content)}


def _water_conformer_xyz() -> str:
    """A 3-atom O/H/H geometry -- deliberately *not* the .gjf/.inp/.log
    fixtures' own coordinates, so a successful extraction mints a
    genuinely new, distinct ``Geometry`` row (dedup-by-hash would
    otherwise collapse the two onto one row and the test would not tell
    "linked" from "already there")."""
    return "3\nplaceholder conformer geometry\nO 0.0 0.0 0.0\nH 5.0 0.0 0.0\nH 10.0 0.0 0.0"


def _conformer_payload(
    *,
    smiles: str,
    conformer_xyz: str,
    calc_type: str = "opt",
    software: str = "Gaussian",
    input_geometries: list[str] | None = None,
) -> dict:
    calc: dict = {
        "type": calc_type,
        "software_release": {"name": software, "version": "09"},
        "level_of_theory": {"method": "wb97xd", "basis": "def2tzvp"},
    }
    if calc_type == "opt":
        calc["opt_result"] = {"converged": True}
    if input_geometries is not None:
        calc["input_geometries"] = [{"xyz_text": g} for g in input_geometries]
    return {
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": 1},
        "geometry": {"xyz_text": conformer_xyz},
        "calculation": calc,
        "label": "input-geometry-hook",
    }


def _create_opt_calc(
    client,
    *,
    smiles: str = "O",
    conformer_xyz: str = _water_conformer_xyz(),
    calc_type: str = "opt",
    software: str = "Gaussian",
    input_geometries: list[str] | None = None,
) -> int:
    resp = client.post(
        "/api/v1/uploads/conformers",
        json=_conformer_payload(
            smiles=smiles,
            conformer_xyz=conformer_xyz,
            calc_type=calc_type,
            software=software,
            input_geometries=input_geometries,
        ),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["primary_calculation"]["calculation_id"]


def _input_links(db_session: Session, calc_id: int) -> list[CalculationInputGeometry]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(CalculationInputGeometry).where(
                CalculationInputGeometry.calculation_id == calc_id
            )
        ).all()
    )


def _output_geometry_id(db_session: Session, calc_id: int) -> int:
    db_session.expire_all()
    row = db_session.scalar(
        select(CalculationOutputGeometry).where(
            CalculationOutputGeometry.calculation_id == calc_id
        )
    )
    assert row is not None
    return row.geometry_id


class TestInputGeometryHookFillsAbsent:
    def test_gaussian_input_deck_fills_absent_input_geometry(
        self, client, db_session, stub_store_artifact
    ):
        calc_id = _create_opt_calc(client, smiles="O")
        assert _input_links(db_session, calc_id) == []

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_artifact(GAUSSIAN_GJF, kind="input", filename="input.gjf")]},
        )
        assert resp.status_code == 201, resp.text

        links = _input_links(db_session, calc_id)
        assert len(links) == 1
        assert links[0].input_order == 1
        assert links[0].source is CalculationInputGeometrySource.extracted_from_artifact
        # A new, distinct geometry -- not a duplicate of the output.
        assert links[0].geometry_id != _output_geometry_id(db_session, calc_id)

    def test_orca_input_deck_fills_absent_input_geometry(
        self, client, db_session, stub_store_artifact
    ):
        # A raw .inp carries no ORCA banner -- the declared software_release
        # is what resolves the parser (see _resolve_software_for_extraction).
        calc_id = _create_opt_calc(client, smiles="O", software="ORCA")

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_artifact(ORCA_INP, kind="input", filename="input.in")]},
        )
        assert resp.status_code == 201, resp.text

        links = _input_links(db_session, calc_id)
        assert len(links) == 1
        assert links[0].source is CalculationInputGeometrySource.extracted_from_artifact

    def test_gaussian_log_fallback_fills_when_no_input_file(
        self, client, db_session, stub_store_artifact
    ):
        # N2C3H7 has 12 atoms -- match the conformer/species to the real
        # fixture log's own composition.
        calc_id = _create_opt_calc(
            client,
            smiles=N2C3H7_SMILES,
            conformer_xyz=(
                "12\nplaceholder\n"
                + "\n".join(f"{el} {i * 5.0:.4f} 0.0 0.0" for i, el in enumerate(
                    ["N", "N", "C", "C", "C", "H", "H", "H", "H", "H", "H", "H"]
                ))
            ),
        )

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_artifact(GAUSSIAN_OPT_LOG, kind="output_log", filename="opt.log")]},
        )
        assert resp.status_code == 201, resp.text

        links = _input_links(db_session, calc_id)
        assert len(links) == 1
        assert links[0].source is CalculationInputGeometrySource.extracted_from_artifact
        assert links[0].geometry.natoms == 12

    def test_orca_log_fallback_fills_when_input_used_xyzfile(
        self, client, db_session, stub_store_artifact
    ):
        # The log's own INPUT FILE echo uses '* xyzfile' -- the log
        # fallback must still recover a geometry from the printed
        # CARTESIAN COORDINATES block.
        calc_id = _create_opt_calc(client, smiles="O")

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_artifact(ORCA_LOG_HEAD, kind="output_log", filename="opt.out")]},
        )
        assert resp.status_code == 201, resp.text

        links = _input_links(db_session, calc_id)
        assert len(links) == 1
        assert links[0].source is CalculationInputGeometrySource.extracted_from_artifact


class TestInputGeometryHookReplacesDegenerate:
    def test_declared_input_equal_to_output_is_replaced(
        self, client, db_session, stub_store_artifact
    ):
        conformer_xyz = _water_conformer_xyz()
        # The depositor declared the SAME coordinates as both the
        # conformer geometry and the calc's own input_geometries -- an
        # ARC-deposit artifact, canonicalising to one Geometry row shared
        # by both links.
        calc_id = _create_opt_calc(
            client,
            smiles="O",
            conformer_xyz=conformer_xyz,
            input_geometries=[conformer_xyz],
        )
        before = _input_links(db_session, calc_id)
        assert len(before) == 1
        output_id = _output_geometry_id(db_session, calc_id)
        assert before[0].geometry_id == output_id
        assert before[0].source is CalculationInputGeometrySource.deposited

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_artifact(GAUSSIAN_GJF, kind="input", filename="input.gjf")]},
        )
        assert resp.status_code == 201, resp.text

        after = _input_links(db_session, calc_id)
        assert len(after) == 1  # replaced in place, not appended
        assert after[0].input_order == 1
        assert after[0].source is CalculationInputGeometrySource.extracted_from_artifact
        assert after[0].geometry_id != output_id
        # The output link is untouched.
        assert _output_geometry_id(db_session, calc_id) == output_id


class TestInputGeometryHookNeverUpgradesRealInput:
    def test_calc_with_distinct_real_input_is_not_touched(
        self, client, db_session, stub_store_artifact
    ):
        distinct_xyz = (
            "3\nreal distinct input\nO 1.0 1.0 1.0\nH 2.0 1.0 1.0\nH 1.0 2.0 1.0"
        )
        calc_id = _create_opt_calc(
            client,
            smiles="O",
            conformer_xyz=_water_conformer_xyz(),
            input_geometries=[distinct_xyz],
        )
        before = _input_links(db_session, calc_id)
        assert len(before) == 1
        assert before[0].source is CalculationInputGeometrySource.deposited
        before_geom_id = before[0].geometry_id

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_artifact(GAUSSIAN_GJF, kind="input", filename="input.gjf")]},
        )
        assert resp.status_code == 201, resp.text

        after = _input_links(db_session, calc_id)
        assert len(after) == 1
        assert after[0].geometry_id == before_geom_id
        assert after[0].source is CalculationInputGeometrySource.deposited


class TestInputGeometryHookRejectsAmbiguousInput:
    def test_zmatrix_input_deck_is_rejected_and_upload_still_succeeds(
        self, client, db_session, stub_store_artifact
    ):
        calc_id = _create_opt_calc(client, smiles="O")

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_artifact(GAUSSIAN_GJF_ZMATRIX, kind="input", filename="input.gjf")]},
        )
        assert resp.status_code == 201, resp.text
        assert _input_links(db_session, calc_id) == []

    def test_orca_xyzfile_input_is_rejected_and_upload_still_succeeds(
        self, client, db_session, stub_store_artifact
    ):
        calc_id = _create_opt_calc(client, smiles="O", software="ORCA")

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_artifact(ORCA_INP_XYZFILE, kind="input", filename="input.in")]},
        )
        assert resp.status_code == 201, resp.text
        assert _input_links(db_session, calc_id) == []


class TestInputGeometryHookTypeAndKindGates:
    def test_sp_calc_type_is_skipped(self, client, db_session, stub_store_artifact):
        calc_id = _create_opt_calc(client, smiles="O", calc_type="sp")
        # sp already gets the freq/sp input fallback (the conformer
        # geometry) -- capture it so we can assert it is untouched.
        before = _input_links(db_session, calc_id)

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_artifact(GAUSSIAN_GJF, kind="input", filename="input.gjf")]},
        )
        assert resp.status_code == 201, resp.text
        assert _input_links(db_session, calc_id) == before

    def test_hessian_kind_gate_blocks_extraction(
        self, client, db_session, stub_store_artifact
    ):
        # kind='hessian' is not an eligible artifact kind for this hook.
        calc_id = _create_opt_calc(client, smiles="O")

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_artifact(GAUSSIAN_GJF, kind="hessian", filename="input.hess")]},
        )
        assert resp.status_code == 201, resp.text
        assert _input_links(db_session, calc_id) == []


class TestInputGeometryHookIdenticalToOutput:
    def test_extracted_geometry_identical_to_output_leaves_input_absent(
        self, client, db_session, stub_store_artifact
    ):
        # The conformer's own declared geometry IS the .gjf's geometry, so
        # extraction succeeds but resolves to the very same Geometry row
        # as the output -- nothing new to link.
        calc_id = _create_opt_calc(
            client,
            smiles="O",
            conformer_xyz=(
                "3\nsame as gjf\n"
                "O 0.000000 0.000000 0.118351\n"
                "H 0.000000 0.761187 -0.469725\n"
                "H 0.000000 -0.761187 -0.469725"
            ),
        )

        resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [_artifact(GAUSSIAN_GJF, kind="input", filename="input.gjf")]},
        )
        assert resp.status_code == 201, resp.text
        assert _input_links(db_session, calc_id) == []


class TestInputGeometryHookFrozenAfterApproval:
    def test_approved_calculation_is_never_written_to(self, client, db_session):
        """The upload hook is never exercised against an approved calc in
        practice -- the artifacts route itself refuses new artifacts on an
        approved calculation with a 409 before this hook would ever run.
        This calls the shared operation directly (as the backfill script
        would, which has no such route-level pre-check) to prove the
        accepted-science-immutability guard is honoured rather than
        surfacing as a raw trigger error."""
        calc_id = _create_opt_calc(client, smiles="O")

        # A real approval, through the same service the curation API uses
        # -- ``first_approved_at`` is guarded by its own trigger
        # (``tckdb_guard_record_review``) and cannot be set directly.
        curator = AppUser(username=f"input-geom-curator-{calc_id}", role=AppUserRole.curator)
        db_session.add(curator)
        db_session.flush()
        ensure_record_review(
            db_session,
            record_type=SubmissionRecordType.calculation,
            record_id=calc_id,
        )
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.calculation,
            record_id=calc_id,
            status=RecordReviewStatus.approved,
            actor=curator,
        )

        calculation = db_session.get(Calculation, calc_id)
        outcome = extract_and_link_input_geometry(
            db_session,
            calculation,
            candidates=[(ArtifactKind.input, GAUSSIAN_GJF.decode())],
        )
        assert outcome.kind is InputGeometryOutcomeKind.frozen_after_approval
        assert _input_links(db_session, calc_id) == []
