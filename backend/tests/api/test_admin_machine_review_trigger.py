"""Tests for the admin-only explicit fake machine-review trigger endpoint.

``POST /api/v1/admin/machine-review/records/{record_type}/{record_id}/run-fake``
explicitly runs **fake** machine review for one record and appends a
``record_machine_review`` row only when the active recipe says one is needed
(``run_not_reviewed`` / ``run_stale``); an already-current record is skipped, so
re-running an unchanged recipe is idempotent. It is a maintainer/debug surface
(policy ``record_machine_review_policy.md`` §5.3), **admin-only**, and never
public scientific trust: it uses only the fake producer, is not wired into
uploads or any public read, emits no ``trust.machine_review``, and mutates
nothing outside ``record_machine_review``.

These follow the existing admin-route testing pattern (mirroring
``test_admin_machine_review_inspection.py``): the ``client`` fixture's default
actor is role=user (the 403 path), ``login_as`` swaps roles, and a fresh app
with no auth override exercises the anonymous 401 path. A real ``Calculation``
is seeded (direct ORM inserts, rolled back per test) so the live deterministic
trust fragment is built from a real record.
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.app import create_app
from app.api.deps import get_db, get_write_db
from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationGeometryValidation,
    CalculationInputGeometry,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationParameter,
)
from app.db.models.common import (
    ArtifactKind,
    CalculationGeometryRole,
    CalculationQuality,
    CalculationType,
    MoleculeKind,
    ParameterSource,
    RecordReviewStatus,
    StereoKind,
    SubmissionRecordType,
    ValidationStatus,
)
from app.db.models.geometry import Geometry
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.record_machine_review import RecordMachineReviewRow
from app.db.models.record_review import RecordReview
from app.db.models.software import Software, SoftwareRelease
from app.db.models.species import Species, SpeciesEntry
from app.services.trust import build_trust_fragment
from app.services.trust.models import EvidenceBadge, EvidenceEvaluation

_BASE = "/api/v1/admin/machine-review/records"

_INCHI_COUNTER = iter(range(10_000))
_GEOM_COUNTER = iter(range(10_000))


def _url(record_type: str, record_id: int) -> str:
    return f"{_BASE}/{record_type}/{record_id}/run-fake"


# --------------------------------------------------------------------------- #
# Seeding helpers — direct ORM inserts, rolled back per test
# --------------------------------------------------------------------------- #


def _next_inchi_key() -> str:
    return f"ADMIN-MR-INCHI-KEY-{next(_INCHI_COUNTER):03d}A"[:27].ljust(27, "X")


def _next_geom_hash() -> str:
    return hashlib.sha256(f"admin-mr-geom-{next(_GEOM_COUNTER)}".encode()).hexdigest()


def _make_opt_calc(db_session: Session, *, artifact: bool = True) -> Calculation:
    """Build a minimal real opt calculation with enough provenance to evaluate."""
    species = Species(
        kind=MoleculeKind.molecule,
        smiles="CCO",
        inchi_key=_next_inchi_key(),
        charge=0,
        multiplicity=1,
        stereo_kind=StereoKind.achiral,
    )
    db_session.add(species)
    db_session.flush()
    entry = SpeciesEntry(species_id=species.id, unmapped_smiles=species.smiles)
    db_session.add(entry)
    db_session.flush()

    lot = LevelOfTheory(
        method="wb97xd",
        basis="def2tzvp",
        lot_hash=hashlib.sha256(f"admin-mr-lot-{next(_INCHI_COUNTER)}".encode()).hexdigest(),
    )
    db_session.add(lot)
    sw = Software(name=f"admin-mr-sw-{next(_INCHI_COUNTER)}")
    db_session.add(sw)
    db_session.flush()
    release = SoftwareRelease(software_id=sw.id, version="1.0")
    db_session.add(release)
    db_session.flush()

    calc = Calculation(
        type=CalculationType.opt,
        quality=CalculationQuality.raw,
        species_entry_id=entry.id,
        lot_id=lot.id,
        software_release_id=release.id,
    )
    db_session.add(calc)
    db_session.flush()

    in_g = Geometry(natoms=3, geom_hash=_next_geom_hash(), xyz_text="dummy")
    out_g = Geometry(natoms=3, geom_hash=_next_geom_hash(), xyz_text="dummy")
    db_session.add_all([in_g, out_g])
    db_session.flush()
    db_session.add_all(
        [
            CalculationInputGeometry(
                calculation_id=calc.id, geometry_id=in_g.id, input_order=1
            ),
            CalculationOutputGeometry(
                calculation_id=calc.id,
                geometry_id=out_g.id,
                output_order=1,
                role=CalculationGeometryRole.final,
            ),
            CalculationOptResult(
                calculation_id=calc.id,
                final_energy_hartree=-100.0,
                converged=True,
            ),
            CalculationParameter(
                calculation_id=calc.id,
                raw_key="opt",
                raw_value="tight",
                source=ParameterSource.parser,
            ),
            CalculationGeometryValidation(
                calculation_id=calc.id,
                validation_status=ValidationStatus.passed,
                species_smiles="CCO",
                is_isomorphic=True,
            ),
        ]
    )
    if artifact:
        db_session.add(
            CalculationArtifact(
                calculation_id=calc.id,
                kind=ArtifactKind.output_log,
                uri="s3://test/log",
                sha256="4" * 64,
                bytes=1,
                filename="log.out",
            )
        )
    db_session.flush()
    db_session.refresh(calc)
    return calc


def _count_rows(db_session: Session, record_id: int) -> int:
    return db_session.scalar(
        select(func.count())
        .select_from(RecordMachineReviewRow)
        .where(RecordMachineReviewRow.record_id == record_id)
    )


# --------------------------------------------------------------------------- #
# Access control
# --------------------------------------------------------------------------- #


@pytest.fixture
def anon_client(db_session: Session):
    """A client with DB overrides but no auth override -> real auth runs."""
    app = create_app()
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_write_db] = lambda: db_session
    with TestClient(app) as c:
        yield c


def test_admin_fake_machine_review_requires_auth(anon_client, db_session):
    """Anonymous callers are rejected with 401 before anything runs."""
    calc = _make_opt_calc(db_session)

    resp = anon_client.post(_url("calculation", calc.id))

    assert resp.status_code == 401, resp.text
    assert _count_rows(db_session, calc.id) == 0


def test_admin_fake_machine_review_requires_admin(
    client, db_session, login_as, _api_curator_user
):
    """Normal users and curators are forbidden; the gate is admin-only."""
    calc = _make_opt_calc(db_session)

    # Default actor is role=user.
    assert client.post(_url("calculation", calc.id)).status_code == 403

    # Curators are also forbidden — this debugging surface is admin-only.
    login_as(_api_curator_user)
    assert client.post(_url("calculation", calc.id)).status_code == 403

    assert _count_rows(db_session, calc.id) == 0


# --------------------------------------------------------------------------- #
# Validation / lookup
# --------------------------------------------------------------------------- #


def test_admin_fake_machine_review_404_for_missing_record(
    client, login_as, _api_admin_user
):
    """A supported record type with no live row yields 404 — no row appended."""
    login_as(_api_admin_user)

    resp = client.post(_url("calculation", 999_999))

    assert resp.status_code == 404, resp.text


def test_admin_fake_machine_review_rejects_unknown_record_type(
    client, db_session, login_as, _api_admin_user
):
    """An unsupported record_type is a 400 before any record lookup."""
    calc = _make_opt_calc(db_session)
    login_as(_api_admin_user)

    # ``species`` is a valid SubmissionRecordType but has no machine-review home.
    resp = client.post(_url("species", calc.id))
    assert resp.status_code == 400, resp.text

    # A wholly unknown token is also a 400.
    assert client.post(_url("not_a_record_type", calc.id)).status_code == 400


# --------------------------------------------------------------------------- #
# Append / skip per currency state
# --------------------------------------------------------------------------- #


def test_admin_fake_machine_review_appends_when_not_reviewed(
    client, db_session, login_as, _api_admin_user
):
    """First run on an unreviewed record appends exactly one row."""
    calc = _make_opt_calc(db_session)
    login_as(_api_admin_user)

    resp = client.post(_url("calculation", calc.id))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "appended"
    assert body["decision"] == "run_not_reviewed"
    assert body["execution_status"] == "appended"
    assert body["appended_review_id"] is not None
    assert body["record_type"] == "calculation"
    assert body["record_id"] == calc.id
    assert _count_rows(db_session, calc.id) == 1


def test_admin_fake_machine_review_skips_when_current(
    client, db_session, login_as, _api_admin_user
):
    """A second run against the same recipe/evidence is skipped — no new row."""
    calc = _make_opt_calc(db_session)
    login_as(_api_admin_user)

    first = client.post(_url("calculation", calc.id)).json()
    assert first["status"] == "appended"

    second = client.post(_url("calculation", calc.id)).json()
    assert second["status"] == "skipped_current"
    assert second["decision"] == "skip_current"
    assert second["appended_review_id"] is None
    assert _count_rows(db_session, calc.id) == 1


def test_admin_fake_machine_review_appends_when_stale(
    client, db_session, login_as, _api_admin_user
):
    """Changing the live evidence context stales the latest review -> append."""
    calc = _make_opt_calc(db_session, artifact=True)
    login_as(_api_admin_user)

    first = client.post(_url("calculation", calc.id)).json()
    assert first["status"] == "appended"
    first_hash = first["context_hash"]

    # The human ``review_status`` is a read-only context input folded into the
    # context hash. Recording a review row changes it from ``not_reviewed`` to
    # ``under_review``, so the live deterministic context hash changes -> the
    # latest machine review stales (it does not mutate any machine-owned field).
    db_session.add(
        RecordReview(
            record_type=SubmissionRecordType.calculation,
            record_id=calc.id,
            status=RecordReviewStatus.under_review,
            created_by=_api_admin_user,
        )
    )
    db_session.flush()

    second = client.post(_url("calculation", calc.id)).json()
    assert second["context_hash"] != first_hash
    assert second["status"] == "appended"
    assert second["decision"] == "run_stale"
    assert _count_rows(db_session, calc.id) == 2


def test_admin_fake_machine_review_is_idempotent_for_unchanged_recipe(
    client, db_session, login_as, _api_admin_user
):
    """Repeated runs with no evidence/recipe change append exactly once."""
    calc = _make_opt_calc(db_session)
    login_as(_api_admin_user)

    statuses = [
        client.post(_url("calculation", calc.id)).json()["status"] for _ in range(3)
    ]

    assert statuses == ["appended", "skipped_current", "skipped_current"]
    assert _count_rows(db_session, calc.id) == 1


# --------------------------------------------------------------------------- #
# Producer provenance & response shape
# --------------------------------------------------------------------------- #


def test_admin_fake_machine_review_uses_fake_producer(
    client, db_session, login_as, _api_admin_user
):
    """The appended row carries the obvious fake provenance — never a real verdict."""
    calc = _make_opt_calc(db_session)
    login_as(_api_admin_user)

    body = client.post(_url("calculation", calc.id)).json()

    row = db_session.get(RecordMachineReviewRow, body["appended_review_id"])
    assert row.provider == "fake"
    assert row.model == "fake-test"


def test_admin_fake_machine_review_response_contains_context_hash(
    client, db_session, login_as, _api_admin_user
):
    """The response echoes the live currency key the run acted on."""
    calc = _make_opt_calc(db_session)
    login_as(_api_admin_user)

    body = client.post(_url("calculation", calc.id)).json()

    assert isinstance(body["context_hash"], str) and len(body["context_hash"]) == 64
    assert body["context_schema_version"]
    assert body["prompt_version"] == "machine_review_v1"
    assert body["rubric_versions"] == {"computed_calculation_v1": "1"}

    # The stored row's currency key matches what the response reported.
    row = db_session.get(RecordMachineReviewRow, body["appended_review_id"])
    assert row.context_hash == body["context_hash"]
    assert row.prompt_version == body["prompt_version"]
    assert dict(row.rubric_versions_json) == body["rubric_versions"]


def test_admin_fake_trigger_uses_shared_active_recipe(
    client, db_session, login_as, _api_admin_user
):
    """The trigger stamps the shared recipe (recipe.py), not a local copy.

    The response prompt/rubric versions are sourced from the shared
    ``machine_review.recipe`` module, so a recipe change flows through without a
    second source of truth (readiness-audit risk R1).
    """
    from app.services.machine_review.recipe import (
        ACTIVE_MACHINE_REVIEW_PROMPT_VERSION,
        ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS,
    )

    calc = _make_opt_calc(db_session)
    login_as(_api_admin_user)

    body = client.post(_url("calculation", calc.id)).json()

    assert body["prompt_version"] == ACTIVE_MACHINE_REVIEW_PROMPT_VERSION
    # Only the rubric relevant to the record type is stamped, taken from the
    # shared recipe (not re-derived locally).
    assert body["rubric_versions"] == {
        "computed_calculation_v1": ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS[
            "computed_calculation_v1"
        ]
    }


# --------------------------------------------------------------------------- #
# Non-interference: submission status, human review, public trust shape
# --------------------------------------------------------------------------- #


def test_admin_fake_machine_review_does_not_mutate_submission_status(
    client, db_session, login_as, _api_admin_user
):
    """Running the trigger perturbs no submission lifecycle/moderation state."""
    from app.db.models.common import SubmissionKind
    from app.services.submission import create_submission

    submission = create_submission(
        db_session,
        created_by=_api_admin_user,
        submission_kind=SubmissionKind.thermo,
        title="admin trigger non-interference",
        summary="baseline",
    )
    db_session.flush()
    before = (
        submission.status,
        submission.approved_at,
        submission.approved_by,
        submission.rejected_at,
        submission.rejected_by,
        submission.rejection_reason,
    )

    calc = _make_opt_calc(db_session)
    login_as(_api_admin_user)
    assert client.post(_url("calculation", calc.id)).status_code == 200

    db_session.refresh(submission)
    after = (
        submission.status,
        submission.approved_at,
        submission.approved_by,
        submission.rejected_at,
        submission.rejected_by,
        submission.rejection_reason,
    )
    assert after == before


def test_admin_fake_machine_review_does_not_change_public_trust_shape(
    client, db_session, login_as, _api_admin_user
):
    """The public TrustFragment keeps its frozen shape with no machine_review key."""
    calc = _make_opt_calc(db_session)
    login_as(_api_admin_user)

    assert client.post(_url("calculation", calc.id)).status_code == 200

    evaluation = EvidenceEvaluation(
        record_type="calculation",
        record_id=calc.id,
        rubric="computed_calculation",
        rubric_version=1,
        label=EvidenceBadge.partial,
        passed_checks=("opt_converged",),
        missing_checks=("source_artifact_present",),
        warning_checks=(),
        not_applicable_checks=(),
        passed_count=1,
        possible_count=2,
        evidence_completeness=0.5,
    )
    dumped = build_trust_fragment(evaluation).model_dump(mode="json")
    assert "machine_review" not in dumped
    assert set(dumped) == {
        "review_status",
        "trust_status",
        "evidence",
        "llm_precheck",
        "is_certified",
    }


# --------------------------------------------------------------------------- #
# Public API boundary: scientific reads never expose machine_review
# --------------------------------------------------------------------------- #


def test_public_calculation_read_does_not_expose_machine_review(
    client, db_session, login_as, _api_admin_user
):
    """A public scientific calculation read with include=trust carries no
    machine_review, even after the admin trigger appended a private row."""
    calc = _make_opt_calc(db_session)
    login_as(_api_admin_user)

    # Append a private machine-review row via the admin trigger.
    assert client.post(_url("calculation", calc.id)).status_code == 200

    resp = client.get(f"/api/v1/scientific/calculations/{calc.id}?include=trust")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "machine_review" not in resp.text
    assert "trust" in body["record"]
    assert "machine_review" not in body["record"]["trust"]


def test_public_calculation_read_rejects_machine_review_include_token(
    client, db_session, login_as, _api_admin_user
):
    """No new public include token is accepted: include=machine_review and
    include=trust,machine_review are rejected (422), not silently honored."""
    calc = _make_opt_calc(db_session)
    login_as(_api_admin_user)

    only = client.get(f"/api/v1/scientific/calculations/{calc.id}?include=machine_review")
    assert only.status_code == 422, only.text

    combined = client.get(
        f"/api/v1/scientific/calculations/{calc.id}?include=trust,machine_review"
    )
    assert combined.status_code == 422, combined.text
