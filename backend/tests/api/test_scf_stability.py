"""Tests for SCF wavefunction stability evidence on calculations.

Acceptance criteria covered:

* Calculation can persist stable / unstable / stabilized / inconclusive
  evidence (one test each).
* Missing row reads as ``status = "not_checked"`` with all evidence
  fields ``null`` — i.e. the projection is on the read endpoint, the
  parent does NOT 404 on a missing row.
* Stability evidence attaches to opt calculations.
* Stability evidence attaches to sp calculations (no calc-type
  restriction).
* Stability artifact can be linked via ``source_artifact_id``.
* No stability evidence is inferred automatically (e.g. presence of
  ``calc_opt_result.converged = True`` does not synthesize a row).
* Cross-field validators reject inconsistent producer payloads.
"""

from __future__ import annotations

import pytest
from sqlalchemy import update

from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationOptResult,
    CalculationSCFStability,
)
from app.db.models.common import ArtifactKind, SCFStabilityStatus
from app.schemas.fragments.calculation import SCFStabilityPayload


def _hydrogen_conformer_payload() -> dict:
    return {
        "species_entry": {
            "smiles": "[H]",
            "charge": 0,
            "multiplicity": 2,
        },
        "geometry": {
            "xyz_text": "1\nH atom\nH 0.0 0.0 0.0",
        },
        "calculation": {
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        "label": "scf-stability-test",
    }


def _get_calc_id(client, db_session=None, calc_type=None) -> int:
    client.post(
        "/api/v1/uploads/conformers", json=_hydrogen_conformer_payload()
    )
    calcs = client.get("/api/v1/calculations").json()["items"]
    calc_id = calcs[0]["id"]
    if calc_type is not None and db_session is not None:
        db_session.execute(
            update(Calculation)
            .where(Calculation.id == calc_id)
            .values(type=calc_type)
        )
        db_session.flush()
    return calc_id


# ---------------------------------------------------------------------------
# Read endpoint: not_checked projection
# ---------------------------------------------------------------------------


class TestNotCheckedProjection:
    """Absence of a calc_scf_stability row reads as ``not_checked``.

    This deliberately departs from the sibling endpoints (sp-result,
    geometry-validation, ...) which 404 on missing rows. ``not_checked``
    is the canonical encoding of "no evidence row exists" and the read
    API surfaces it as a stable, queryable shape so consumers don't
    need a "key missing" branch.
    """

    def test_missing_row_projects_not_checked(self, client):
        calc_id = _get_calc_id(client)
        resp = client.get(f"/api/v1/calculations/{calc_id}/scf-stability")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "not_checked"
        assert data["calculation_id"] == calc_id
        assert data["lowest_eigenvalue"] is None
        assert data["instability_count"] is None
        assert data["instability_type"] is None
        assert data["reoptimized_wavefunction"] is None
        assert data["source_calculation_id"] is None
        assert data["source_artifact_id"] is None

    def test_missing_calculation_returns_404(self, client):
        resp = client.get("/api/v1/calculations/999999/scf-stability")
        assert resp.status_code == 404

    def test_no_inference_from_opt_convergence(self, client, db_session):
        """A converged opt does NOT synthesize a stable scf_stability row."""
        calc_id = _get_calc_id(client, db_session, calc_type="opt")
        db_session.add(
            CalculationOptResult(
                calculation_id=calc_id,
                converged=True,
                n_steps=12,
                final_energy_hartree=-0.5,
            )
        )
        db_session.flush()

        resp = client.get(f"/api/v1/calculations/{calc_id}/scf-stability")
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_checked"


# ---------------------------------------------------------------------------
# Persistence: each status round-trips via raw ORM inserts
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_stable(self, client, db_session):
        calc_id = _get_calc_id(client, db_session, calc_type="opt")
        db_session.add(
            CalculationSCFStability(
                calculation_id=calc_id,
                status=SCFStabilityStatus.stable,
                lowest_eigenvalue=0.0123,
                instability_count=0,
                reoptimized_wavefunction=False,
            )
        )
        db_session.flush()

        resp = client.get(f"/api/v1/calculations/{calc_id}/scf-stability")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "stable"
        assert data["lowest_eigenvalue"] == pytest.approx(0.0123)
        assert data["instability_count"] == 0
        assert data["reoptimized_wavefunction"] is False

    def test_unstable(self, client, db_session):
        calc_id = _get_calc_id(client, db_session, calc_type="opt")
        db_session.add(
            CalculationSCFStability(
                calculation_id=calc_id,
                status=SCFStabilityStatus.unstable,
                lowest_eigenvalue=-0.05,
                instability_count=1,
                instability_type="RHF→UHF",
                reoptimized_wavefunction=False,
            )
        )
        db_session.flush()

        data = client.get(
            f"/api/v1/calculations/{calc_id}/scf-stability"
        ).json()
        assert data["status"] == "unstable"
        assert data["instability_count"] == 1
        assert data["instability_type"] == "RHF→UHF"

    def test_stabilized(self, client, db_session):
        calc_id = _get_calc_id(client, db_session, calc_type="opt")
        db_session.add(
            CalculationSCFStability(
                calculation_id=calc_id,
                status=SCFStabilityStatus.stabilized,
                instability_count=1,
                reoptimized_wavefunction=True,
            )
        )
        db_session.flush()

        data = client.get(
            f"/api/v1/calculations/{calc_id}/scf-stability"
        ).json()
        assert data["status"] == "stabilized"
        assert data["reoptimized_wavefunction"] is True
        assert data["instability_count"] == 1

    def test_inconclusive(self, client, db_session):
        calc_id = _get_calc_id(client, db_session, calc_type="opt")
        db_session.add(
            CalculationSCFStability(
                calculation_id=calc_id,
                status=SCFStabilityStatus.inconclusive,
                note="parser failed to read stability section",
            )
        )
        db_session.flush()

        data = client.get(
            f"/api/v1/calculations/{calc_id}/scf-stability"
        ).json()
        assert data["status"] == "inconclusive"
        assert data["note"].startswith("parser failed")


# ---------------------------------------------------------------------------
# Calc-type independence: opt, sp both accept the block
# ---------------------------------------------------------------------------


class TestAttachesToAnyCalcType:
    def test_attaches_to_opt(self, client, db_session):
        calc_id = _get_calc_id(client, db_session, calc_type="opt")
        db_session.add(
            CalculationSCFStability(
                calculation_id=calc_id,
                status=SCFStabilityStatus.stable,
            )
        )
        db_session.flush()
        assert (
            client.get(
                f"/api/v1/calculations/{calc_id}/scf-stability"
            ).json()["status"]
            == "stable"
        )

    def test_attaches_to_sp(self, client, db_session):
        calc_id = _get_calc_id(client, db_session, calc_type="sp")
        db_session.add(
            CalculationSCFStability(
                calculation_id=calc_id,
                status=SCFStabilityStatus.stable,
            )
        )
        db_session.flush()
        assert (
            client.get(
                f"/api/v1/calculations/{calc_id}/scf-stability"
            ).json()["status"]
            == "stable"
        )


# ---------------------------------------------------------------------------
# Artifact linkage via source_artifact_id
# ---------------------------------------------------------------------------


class TestArtifactLinkage:
    def test_source_artifact_id_persists(self, client, db_session):
        calc_id = _get_calc_id(client, db_session, calc_type="opt")
        artifact = CalculationArtifact(
            calculation_id=calc_id,
            kind=ArtifactKind.ancillary,
            uri="s3://bucket/stability.log",
            sha256="5" * 64,
            bytes=1,
            filename="stability.log",
            note="scf_stability",
        )
        db_session.add(artifact)
        db_session.flush()

        db_session.add(
            CalculationSCFStability(
                calculation_id=calc_id,
                status=SCFStabilityStatus.stable,
                source_artifact_id=artifact.id,
            )
        )
        db_session.flush()

        data = client.get(
            f"/api/v1/calculations/{calc_id}/scf-stability"
        ).json()
        assert data["source_artifact_id"] == artifact.id


# ---------------------------------------------------------------------------
# Pydantic cross-field validators on SCFStabilityPayload
# ---------------------------------------------------------------------------


class TestPayloadValidators:
    def test_stable_rejects_reopt_true(self):
        with pytest.raises(ValueError, match="stable"):
            SCFStabilityPayload(
                status=SCFStabilityStatus.stable,
                reoptimized_wavefunction=True,
            )

    def test_stabilized_rejects_zero_instabilities(self):
        with pytest.raises(ValueError, match="stabilized"):
            SCFStabilityPayload(
                status=SCFStabilityStatus.stabilized,
                instability_count=0,
            )

    def test_unstable_rejects_reopt_true(self):
        with pytest.raises(ValueError, match="unstable"):
            SCFStabilityPayload(
                status=SCFStabilityStatus.unstable,
                reoptimized_wavefunction=True,
            )

    def test_stable_minimal_payload_accepted(self):
        """No evidence-bearing fields are required for status=stable.

        Producer contract is documented (not enforced): producers must
        only emit ``stable`` when an actual stability analysis was
        observed. This permissiveness is deliberate — it lets producers
        record yes/no outcomes from messy logs without forcing a
        synthetic eigenvalue or artifact.
        """
        payload = SCFStabilityPayload(status=SCFStabilityStatus.stable)
        assert payload.status == SCFStabilityStatus.stable
        assert payload.lowest_eigenvalue is None
        assert payload.source_artifact_id is None

    def test_inconclusive_unconstrained(self):
        payload = SCFStabilityPayload(
            status=SCFStabilityStatus.inconclusive,
            instability_count=0,
            reoptimized_wavefunction=True,
        )
        assert payload.status == SCFStabilityStatus.inconclusive


# ---------------------------------------------------------------------------
# DB check constraints (defense-in-depth against direct ORM bypass)
# ---------------------------------------------------------------------------


class TestDBCheckConstraints:
    def test_stable_with_reopt_true_rejected_by_db(self, client, db_session):
        from sqlalchemy.exc import IntegrityError

        calc_id = _get_calc_id(client, db_session, calc_type="opt")
        db_session.add(
            CalculationSCFStability(
                calculation_id=calc_id,
                status=SCFStabilityStatus.stable,
                reoptimized_wavefunction=True,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_stabilized_with_zero_instabilities_rejected_by_db(
        self, client, db_session
    ):
        from sqlalchemy.exc import IntegrityError

        calc_id = _get_calc_id(client, db_session, calc_type="opt")
        db_session.add(
            CalculationSCFStability(
                calculation_id=calc_id,
                status=SCFStabilityStatus.stabilized,
                instability_count=0,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_negative_instability_count_rejected(self, client, db_session):
        from sqlalchemy.exc import IntegrityError

        calc_id = _get_calc_id(client, db_session, calc_type="opt")
        db_session.add(
            CalculationSCFStability(
                calculation_id=calc_id,
                status=SCFStabilityStatus.unstable,
                instability_count=-1,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()
