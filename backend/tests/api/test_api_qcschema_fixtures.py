"""Backend acceptance test for the QCSchema adapter's corpus (C-Q2).

``backend/tests/fixtures/qcschema/<case>/`` holds the eight route-outcome
payloads (energy/gradient/hessian/optimization x QCSchema families v1/v2,
water at HF/STO-3G, Psi4-via-qcengine + geomeTRIC) emitted by running the
real ``tckdb_qcschema`` mapper over its own adapter-side fixtures --
see ``clients/python/adapters/qcschema/scripts/emit_backend_corpus.py``.
Mirrors ``test_api_arc_run_fixtures.py``: every case here is one real
mapped document, posted through the actual upload routes exactly as
``tckdb-qcschema import --upload`` would, so a payload the adapter
produces but the backend refuses is caught here rather than by a live
depositor.

Per case:

1. ``POST /uploads/conformers`` with ``payload.json`` under a stable
   ``Idempotency-Key``; assert 201 and the calculation type.
2. ``POST /calculations/{id}/artifacts`` with ``artifact.json`` (wrapped
   in the route's batch envelope) under a second stable key; assert 201.
3. Assert what actually landed: the SP/opt/Hessian result row and its
   pinned numeric value(s), zero derived ``calc_freq_mode`` rows and a
   null ``n_imag`` for a Hessian-only record, the geometry rows for an
   optimization plus its ``workflow_tool_release``/``software_release``,
   the artifact row's declared sha256, ``parameters_json["tckdb_qcschema"]``
   carrying the pinned adapter/qcelemental versions, and no ``username``
   key anywhere in the stored JSON (the mapper drops it -- see
   ``tckdb_qcschema.mapping._filter_provenance`` -- but only the string
   ``"provenance.username"`` naming the dropped field survives into the
   mapping report; this test asserts no *key* named ``username``, not
   merely that the substring is absent, which would also flag that
   report entry).
4. Replay both POSTs with the identical key + body; assert the same
   response body and zero new rows.

This is the corpus test named throughout the Phase C plan: it must fail
loudly on an empty corpus (``test_corpus_is_not_empty``), never collect
zero cases and report a hollow green.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationFreqMode,
    CalculationHessian,
    CalculationInputGeometry,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationSPResult,
)
from app.db.models.common import CalculationGeometryRole
from app.db.models.idempotency import IdempotencyRecord
from app.db.models.software import Software, SoftwareRelease
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease

CORPUS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "qcschema"


def _discover_cases() -> list[str]:
    if not CORPUS_DIR.exists():
        return []
    return sorted(
        d.name
        for d in CORPUS_DIR.iterdir()
        if d.is_dir()
        and (d / "payload.json").exists()
        and (d / "artifact.json").exists()
        and (d / "meta.json").exists()
    )


CASES = _discover_cases()


def _load(case: str) -> tuple[dict, dict, dict]:
    case_dir = CORPUS_DIR / case
    payload = json.loads((case_dir / "payload.json").read_text())
    artifact = json.loads((case_dir / "artifact.json").read_text())
    meta = json.loads((case_dir / "meta.json").read_text())
    return payload, artifact, meta


def _assert_no_username_key(node: object, *, path: str = "$") -> None:
    """Recursively assert no dict in ``node`` carries a literal ``"username"`` key.

    Deliberately a key check, not a substring check on the serialized
    JSON: the mapping report legitimately names the dropped field as the
    *string* ``"provenance.username"`` inside
    ``parameters_json["tckdb_qcschema"]["mapping_report"]["unsupported"]``
    (see the module docstring), and a substring assertion over
    ``json.dumps(...)`` would wrongly flag that audit entry. What must
    never appear is an actual ``{"username": ...}`` entry -- the operator
    identity itself.
    """
    if isinstance(node, dict):
        assert "username" not in node, f"{path}: a 'username' key leaked into stored JSON: {node}"
        for key, value in node.items():
            _assert_no_username_key(value, path=f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _assert_no_username_key(value, path=f"{path}[{i}]")


@pytest.fixture
def stub_store_artifact(monkeypatch) -> list[tuple[str, str]]:
    written: list[tuple[str, str]] = []

    def _fake_store(content: bytes, sha256: str) -> str:
        uri = f"s3://test-bucket/{sha256[:2]}/{sha256}"
        written.append((uri, sha256))
        return uri

    monkeypatch.setattr("app.services.artifact_persistence.store_artifact", _fake_store)
    return written


def test_corpus_is_not_empty() -> None:
    """Must be red, never vacuously green, on a missing/emptied corpus."""
    assert CASES, (
        "no QCSchema backend corpus discovered under "
        "backend/tests/fixtures/qcschema/ -- generate it with "
        "clients/python/adapters/qcschema/scripts/emit_backend_corpus.py "
        "(conda env tckdb_qcschema_psi4)."
    )
    # Exactly the eight route cases named in the plan: energy/gradient/
    # hessian/optimization x QCSchema families v1/v2. Not "at least 8" --
    # an accidental ninth case here would be a case this test was never
    # written to check.
    assert len(CASES) == 8, f"expected exactly 8 corpus cases, found {len(CASES)}: {CASES}"


def _calculation_count(db_session: Session) -> int:
    return db_session.scalar(select(func.count()).select_from(Calculation)) or 0


def _idempotency_count(db_session: Session) -> int:
    return db_session.scalar(select(func.count()).select_from(IdempotencyRecord)) or 0


def _artifact_count(db_session: Session, calculation_id: int) -> int:
    return (
        db_session.scalar(
            select(func.count())
            .select_from(CalculationArtifact)
            .where(CalculationArtifact.calculation_id == calculation_id)
        )
        or 0
    )


@pytest.mark.parametrize("case", CASES, ids=CASES)
def test_qcschema_corpus_case_accepted_and_persisted(
    case: str, client, db_session: Session, stub_store_artifact
) -> None:
    payload, artifact, meta = _load(case)
    expected_type = meta["expected_calculation_type"]
    pins = meta["pins"]

    conformers_key = f"qcschema-corpus-{case}-conformers-v1"
    resp = client.post(
        "/api/v1/uploads/conformers",
        json=payload,
        headers={"Idempotency-Key": conformers_key},
    )
    assert resp.status_code == 201, f"{case}: {resp.status_code}\n{resp.text[:2000]}"
    body = resp.json()
    calc_id = body["primary_calculation"]["calculation_id"]
    assert body["primary_calculation"]["type"] == expected_type, (
        f"{case}: expected primary_calculation.type={expected_type!r}, "
        f"got {body['primary_calculation']['type']!r}"
    )

    artifact_key = f"qcschema-corpus-{case}-artifact-v1"
    artifact_resp = client.post(
        f"/api/v1/calculations/{calc_id}/artifacts",
        json={"artifacts": [artifact]},
        headers={"Idempotency-Key": artifact_key},
    )
    assert artifact_resp.status_code == 201, (
        f"{case}: {artifact_resp.status_code}\n{artifact_resp.text[:2000]}"
    )

    db_session.expire_all()
    calc = db_session.get(Calculation, calc_id)
    assert calc is not None
    assert calc.type.value == expected_type

    # --- per-type result + pin assertions -------------------------------
    if expected_type == "sp":
        sp = db_session.get(CalculationSPResult, calc_id)
        assert sp is not None, f"{case}: no calc_sp_result row"
        assert sp.electronic_energy_hartree == pytest.approx(
            pins["electronic_energy_hartree"], abs=1e-12
        ), f"{case}: sp electronic_energy_hartree pin mismatch"

    elif expected_type == "freq":
        hess = db_session.get(CalculationHessian, calc_id)
        assert hess is not None, f"{case}: no calc_hessian row"
        assert len(hess.lower_triangle_hartree_bohr2) == pins["hessian_length"]
        assert hess.lower_triangle_hartree_bohr2[0] == pytest.approx(
            pins["hessian_first_value"], abs=1e-15
        ), f"{case}: hessian first packed value pin mismatch"
        # No freq_result / calc_freq_mode rows: the mapper stores an
        # uploaded matrix, never a derived spectrum -- see the plan's
        # "Why a Hessian becomes a freq record with no spectrum".
        assert calc.freq_result is None or calc.freq_result.n_imag is None, (
            f"{case}: expected no freq_result (or a null n_imag), got "
            f"{calc.freq_result!r}"
        )
        mode_count = db_session.scalar(
            select(func.count())
            .select_from(CalculationFreqMode)
            .where(CalculationFreqMode.calculation_id == calc_id)
        )
        assert mode_count == 0, f"{case}: expected 0 calc_freq_mode rows, got {mode_count}"

    elif expected_type == "opt":
        opt = db_session.get(CalculationOptResult, calc_id)
        assert opt is not None, f"{case}: no calc_opt_result row"
        assert opt.converged is True
        assert opt.final_energy_hartree == pytest.approx(
            pins["final_energy_hartree"], abs=1e-12
        ), f"{case}: opt final_energy_hartree pin mismatch"

        input_geoms = db_session.scalars(
            select(CalculationInputGeometry).where(
                CalculationInputGeometry.calculation_id == calc_id
            )
        ).all()
        assert len(input_geoms) == 1, f"{case}: expected 1 input geometry, got {len(input_geoms)}"

        output_geoms = db_session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == calc_id
            )
        ).all()
        assert len(output_geoms) == 1, f"{case}: expected 1 output geometry, got {len(output_geoms)}"
        assert output_geoms[0].role == CalculationGeometryRole.final

        assert calc.workflow_tool_release_id is not None
        wtr = db_session.get(WorkflowToolRelease, calc.workflow_tool_release_id)
        assert wtr is not None
        assert wtr.workflow_tool.name == "geomeTRIC", (
            f"{case}: expected workflow_tool_release for geomeTRIC, got "
            f"{wtr.workflow_tool.name!r}"
        )
    else:
        pytest.fail(f"{case}: unexpected calculation type {expected_type!r}")

    # --- software_release (Psi4) — the ESS, present for every route case
    assert calc.software_release_id is not None
    sr = db_session.get(SoftwareRelease, calc.software_release_id)
    assert sr is not None
    assert sr.software.name == "Psi4", f"{case}: expected software_release for Psi4, got {sr.software.name!r}"

    # --- artifact row ----------------------------------------------------
    assert _artifact_count(db_session, calc_id) == 1
    stored_artifact = db_session.scalar(
        select(CalculationArtifact).where(CalculationArtifact.calculation_id == calc_id)
    )
    assert stored_artifact is not None
    assert stored_artifact.sha256 == artifact["sha256"] == meta["raw_sha256"]

    # --- parameters_json / mapping provenance ----------------------------
    pj = calc.parameters_json
    assert pj is not None and "tckdb_qcschema" in pj, f"{case}: parameters_json missing tckdb_qcschema"
    qc_block = pj["tckdb_qcschema"]
    assert qc_block["adapter_version"] == meta["generator"]["adapter_version"]
    assert qc_block["qcelemental_version"] == meta["generator"]["qcelemental_version"]
    _assert_no_username_key(pj)

    # --- idempotency replay: same key + same body -> same body, no new rows
    before_calc_count = _calculation_count(db_session)
    before_idem_count = _idempotency_count(db_session)
    before_artifact_count = _artifact_count(db_session, calc_id)

    replay_resp = client.post(
        "/api/v1/uploads/conformers",
        json=payload,
        headers={"Idempotency-Key": conformers_key},
    )
    assert replay_resp.status_code == 201
    assert replay_resp.json() == body, f"{case}: conformer replay body differs from the original"

    replay_artifact_resp = client.post(
        f"/api/v1/calculations/{calc_id}/artifacts",
        json={"artifacts": [artifact]},
        headers={"Idempotency-Key": artifact_key},
    )
    assert replay_artifact_resp.status_code == 201
    assert replay_artifact_resp.json() == artifact_resp.json(), (
        f"{case}: artifact replay body differs from the original"
    )

    assert _calculation_count(db_session) == before_calc_count, f"{case}: replay created new calculation rows"
    assert _idempotency_count(db_session) == before_idem_count, f"{case}: replay created new idempotency rows"
    assert _artifact_count(db_session, calc_id) == before_artifact_count, (
        f"{case}: replay created new artifact rows"
    )


def test_no_duplicate_software_rows_across_the_whole_corpus(client, db_session: Session) -> None:
    """``normalize_software_name("Psi4")``/``("geomeTRIC")`` must not fragment.

    Every one of the 8 corpus documents names its ESS as ``"Psi4"``
    (``provenance.creator``, or ``trajectory[-1].provenance.creator`` for
    the two optimization cases) and its optimizer as ``"geomeTRIC"`` --
    spelled identically by the real tools that produced them (measured:
    grep every fixture). Neither name is in
    ``tckdb_schemas.software._SOFTWARE_NAME_ALIASES``, so
    ``normalize_software_name`` returns each unchanged; ``resolve_software``
    dedupes on exact ``Software.name`` equality. Uploading all 8 cases into
    one transaction and counting rows is the empirical check the plan
    calls for -- not merely reasoning about the alias table -- because a
    per-case parametrized test (the one above) never shares a session
    across cases and so could never observe a fragmentation bug.
    """
    for case in CASES:
        payload, artifact, _meta = _load(case)
        resp = client.post(
            "/api/v1/uploads/conformers",
            json=payload,
            headers={"Idempotency-Key": f"qcschema-corpus-{case}-conformers-dedupe"},
        )
        assert resp.status_code == 201, f"{case}: {resp.status_code}\n{resp.text[:500]}"
        calc_id = resp.json()["primary_calculation"]["calculation_id"]
        artifact_resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={"artifacts": [artifact]},
            headers={"Idempotency-Key": f"qcschema-corpus-{case}-artifact-dedupe"},
        )
        assert artifact_resp.status_code == 201

    db_session.expire_all()
    psi4_count = db_session.scalar(
        select(func.count()).select_from(Software).where(Software.name == "Psi4")
    )
    assert psi4_count == 1, f"expected exactly 1 'Psi4' Software row, got {psi4_count}"
    # No case-insensitive stray either (a differently-cased 'psi4'/'PSI4'
    # row would mean two identities for the same real program).
    stray_count = db_session.scalar(
        select(func.count())
        .select_from(Software)
        .where(func.lower(Software.name) == "psi4", Software.name != "Psi4")
    )
    assert stray_count == 0, f"expected no case-variant 'psi4' Software rows, got {stray_count}"

    geometric_count = db_session.scalar(
        select(func.count()).select_from(WorkflowTool).where(WorkflowTool.name == "geomeTRIC")
    )
    assert geometric_count == 1, f"expected exactly 1 'geomeTRIC' WorkflowTool row, got {geometric_count}"
