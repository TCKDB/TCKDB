"""Verify `created_by` attribution and submission-table isolation for the
direct ``/uploads/*`` endpoints.

Two contracts are exercised here:

1. Authenticated direct uploads stamp ``created_by`` with the calling
   ``app_user.id`` on every parent table that declares the column.
2. Direct uploads MUST NOT create rows in the moderation tables
   (``submission``, ``submission_audit_event``, ``submission_record_link``).
   Those tables are reserved for the ``/bundles/*`` moderated path.

Only tables that actually have a ``created_by`` column (i.e. inherit
``CreatedByMixin``) are asserted on; child/link tables without the column
(e.g. ``ThermoPoint``, ``KineticsSourceCalculation``) are intentionally
excluded.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models.calculation import Calculation, CalculationArtifact
from app.db.models.kinetics import Kinetics
from app.db.models.reaction import ReactionEntry
from app.db.models.species import (
    ConformerGroup,
    ConformerObservation,
    SpeciesEntry,
)
from app.db.models.statmech import Statmech
from app.db.models.submission import (
    Submission,
    SubmissionAuditEvent,
    SubmissionRecordLink,
)
from app.db.models.thermo import Thermo
from app.db.models.transition_state import (
    TransitionState,
    TransitionStateEntry,
)

from tests.api.test_api_kfir_rxn import _BUNDLE as _COMPUTED_REACTION_BUNDLE

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
GAUSSIAN_OPT_LOG = FIXTURES / "gaussian" / "opt_g09.log"


def _b64(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


def _conformer_payload() -> dict:
    return {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
        "calculation": {
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        "label": "h-conf-attr",
    }


def _computed_species_payload_with_thermo_and_statmech() -> dict:
    """A computed-species bundle that exercises species/conformer/calc/thermo/statmech."""
    return {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "conformers": [
            {
                "key": "c0",
                "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
                "primary_calculation": {
                    "key": "opt0",
                    "type": "opt",
                    "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                    "software_release": {"name": "Gaussian", "version": "16"},
                    "opt_result": {"converged": True},
                },
                "additional_calculations": [
                    {
                        "key": "freq0",
                        "type": "freq",
                        "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                        "software_release": {"name": "Gaussian", "version": "16"},
                        "freq_result": {"n_imag": 0},
                    },
                    {
                        "key": "sp0",
                        "type": "sp",
                        "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                        "software_release": {"name": "Gaussian", "version": "16"},
                        "sp_result": {"electronic_energy_hartree": -0.5},
                    },
                ],
            }
        ],
        "thermo": {
            "h298_kj_mol": 217.998,
            "source_calculations": [
                {"calculation_key": "sp0", "role": "sp"},
                {"calculation_key": "freq0", "role": "freq"},
                {"calculation_key": "opt0", "role": "opt"},
            ],
        },
        "statmech": {
            "is_linear": False,
            "external_symmetry": 1,
            "rigid_rotor_kind": "asymmetric_top",
            "statmech_treatment": "rrho",
            "source_calculations": [
                {"calculation_key": "freq0", "role": "freq"},
                {"calculation_key": "opt0", "role": "opt"},
            ],
        },
    }


def _assert_submission_tables_empty(db_session) -> None:
    for model in (Submission, SubmissionAuditEvent, SubmissionRecordLink):
        rows = db_session.execute(select(model)).all()
        assert rows == [], (
            f"{model.__tablename__} must be empty after a direct /uploads/* "
            f"call; found {len(rows)} row(s)"
        )


def _all_share_created_by(db_session, model, expected_user_id: int) -> None:
    """Assert every row of `model` (with a non-null FK column) has
    ``created_by == expected_user_id``. Empty tables are tolerated only
    when the caller knows the test path doesn't produce that row type."""
    rows = db_session.scalars(select(model)).all()
    assert rows, f"expected at least one {model.__tablename__} row"
    for row in rows:
        assert row.created_by == expected_user_id, (
            f"{model.__tablename__} id={row.id} created_by={row.created_by!r} "
            f"!= expected {expected_user_id}"
        )


# ---------------------------------------------------------------------------
# /uploads/conformers
# ---------------------------------------------------------------------------


class TestConformerUploadAttribution:
    def test_conformer_upload_stamps_created_by(
        self, client, db_session, _api_test_user
    ):
        resp = client.post("/api/v1/uploads/conformers", json=_conformer_payload())
        assert resp.status_code == 201, resp.text

        for model in (
            SpeciesEntry,
            ConformerGroup,
            ConformerObservation,
            Calculation,
        ):
            _all_share_created_by(db_session, model, _api_test_user)

        _assert_submission_tables_empty(db_session)


# ---------------------------------------------------------------------------
# /uploads/computed-species
# ---------------------------------------------------------------------------


class TestComputedSpeciesUploadAttribution:
    def test_computed_species_upload_stamps_created_by_on_all_parent_tables(
        self, client, db_session, _api_test_user
    ):
        resp = client.post(
            "/api/v1/uploads/computed-species",
            json=_computed_species_payload_with_thermo_and_statmech(),
        )
        assert resp.status_code == 201, resp.text

        for model in (
            SpeciesEntry,
            ConformerGroup,
            ConformerObservation,
            Calculation,
            Thermo,
            Statmech,
        ):
            _all_share_created_by(db_session, model, _api_test_user)

        _assert_submission_tables_empty(db_session)


# ---------------------------------------------------------------------------
# /uploads/computed-reaction
# ---------------------------------------------------------------------------


class TestComputedReactionUploadAttribution:
    def test_computed_reaction_upload_stamps_created_by_on_all_parent_tables(
        self, client, db_session, _api_test_user
    ):
        resp = client.post(
            "/api/v1/uploads/computed-reaction", json=_COMPUTED_REACTION_BUNDLE
        )
        assert resp.status_code == 201, resp.text

        # Tables expected to receive rows for this bundle (which contains
        # 4 species, conformers, sp/opt/freq calcs, a TS, kinetics, and
        # thermo per species).
        for model in (
            SpeciesEntry,
            ConformerGroup,
            ConformerObservation,
            Calculation,
            ReactionEntry,
            TransitionState,
            TransitionStateEntry,
            Kinetics,
            Thermo,
        ):
            _all_share_created_by(db_session, model, _api_test_user)

        _assert_submission_tables_empty(db_session)


# ---------------------------------------------------------------------------
# /calculations/{id}/artifacts
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_store_artifact(monkeypatch):
    written: list[tuple[str, str]] = []

    def _fake_store(content: bytes, sha256: str) -> str:
        uri = f"s3://test-bucket/{sha256[:2]}/{sha256}"
        written.append((uri, sha256))
        return uri

    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact", _fake_store
    )
    return written


class TestCalculationArtifactUploadAttribution:
    def test_artifact_upload_stamps_created_by(
        self, client, db_session, _api_test_user, stub_store_artifact
    ):
        # Step 1: create a calculation via the conformer endpoint so we
        # have a target for the artifact upload.
        conf_resp = client.post(
            "/api/v1/uploads/conformers", json=_conformer_payload()
        )
        assert conf_resp.status_code == 201, conf_resp.text
        calc_id = conf_resp.json()["primary_calculation"]["calculation_id"]

        # Step 2: post an artifact to that calculation.
        log_bytes = GAUSSIAN_OPT_LOG.read_bytes()
        artifact_resp = client.post(
            f"/api/v1/calculations/{calc_id}/artifacts",
            json={
                "artifacts": [
                    {
                        "kind": "output_log",
                        "filename": "opt.log",
                        "content_base64": _b64(log_bytes),
                        "sha256": hashlib.sha256(log_bytes).hexdigest(),
                    }
                ]
            },
        )
        assert artifact_resp.status_code == 201, artifact_resp.text

        artifact_rows = db_session.scalars(select(CalculationArtifact)).all()
        assert len(artifact_rows) == 1
        assert artifact_rows[0].created_by == _api_test_user

        _assert_submission_tables_empty(db_session)
