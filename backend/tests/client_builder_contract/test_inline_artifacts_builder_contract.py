"""Contract: a builder deposit sends its files inline, so the server's evidence checks see them.

Before this, the builders uploaded the calculation and then posted its output log in a second
request, so a converged ``opt`` with its own energy and a log was reported as
``converged_opt_no_usable_energy`` (no artifact yet) and the warning stayed stored after the
log arrived. Sent inline, the artifact is stored in the same request and the check passes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select

from tests._ci_dependency import require_module

# Skips locally without the client; fails on CI, which installs it.
require_module("tckdb_client.builders", install="pip install -e clients/python")

from tckdb_client.builders import (
    Calculation,
    ComputedSpeciesUpload,
    Geometry,
    LevelOfTheory,
    SoftwareRelease,
    Species,
)

from app.db.models.calculation import CalculationArtifact
from app.db.models.deposit_warning import DepositWarning
from app.db.models.level_of_theory import LevelOfTheory as LevelOfTheoryRow
from app.schemas.workflows.computed_species_upload import ComputedSpeciesUploadRequest

STALE = "converged_opt_no_usable_energy"
LOG = Path(__file__).resolve().parent.parent / "fixtures" / "gaussian" / "water_default_g16.log"
WATER_XYZ = "3\nwater\nO 0.0 0.0 0.117\nH 0.0 0.757 -0.469\nH 0.0 -0.757 -0.469"


@pytest.fixture
def stub_store_artifact(monkeypatch):
    """No live object store in this suite: the persistence layer's write is replaced."""
    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact", lambda content, sha256: f"s3://test-bucket/{sha256[:2]}/{sha256}"
    )


def _water_upload(*, spin_treatment: str | None = None) -> ComputedSpeciesUpload:
    opt = Calculation.opt(
        SoftwareRelease(software="Gaussian", version="16"),
        LevelOfTheory(method="b3lyp", basis="def2svp", spin_treatment=spin_treatment),
        output_geometry=Geometry.from_xyz(WATER_XYZ),
        final_energy_hartree=-76.358,
        converged=True,
        label="opt",
    )
    opt.add_artifact(LOG, kind="output_log")
    return ComputedSpeciesUpload(
        species=Species(smiles="O", charge=0, multiplicity=1, label="water"),
        calculations=[opt],
        primary_calculation=opt,
    )


def _stored_codes(session) -> list[str | None]:
    return [row.code for row in session.scalars(select(DepositWarning))]


def test_inline_deposit_stores_the_log_and_leaves_no_stale_warning(client, stub_store_artifact):
    upload = _water_upload()
    response = client.post("/api/v1/uploads/computed-species", json=upload.to_payload())
    assert response.status_code == 201, response.text
    body = response.json()

    assert STALE not in [w["code"] for w in body["warnings"]]
    assert STALE not in _stored_codes(client._db_session)
    stored = client._db_session.scalar(select(func.count()).select_from(CalculationArtifact))
    assert stored == 1
    # Everything went in this request: the plan has nothing left to send.
    assert upload.artifact_plan(body) == []


def test_spin_treatment_reaches_the_stored_level_of_theory(client, stub_store_artifact):
    response = client.post(
        "/api/v1/uploads/computed-species", json=_water_upload(spin_treatment="unrestricted").to_payload()
    )
    assert response.status_code == 201, response.text
    levels = client._db_session.scalars(select(LevelOfTheoryRow.spin_treatment)).all()
    assert [v.value for v in levels if v is not None] == ["unrestricted"]


def test_inline_payload_validates_against_the_bundle_schema():
    validated = ComputedSpeciesUploadRequest.model_validate(_water_upload().to_payload())
    artifacts = validated.conformers[0].primary_calculation.artifacts
    assert [(a.kind.value, a.filename) for a in artifacts] == [("output_log", LOG.name)]
