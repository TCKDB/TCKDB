"""The output-log software-banner hook at ingest (issue #305, owner decision (c)).

When an uploaded output log's banner names the *same* program as the
calculation's declared release, and supplies a version that release left
NULL, the calculation is re-pointed at the versioned release the banner
describes. Everything else is recorded, never re-pointed:

- a declared version the banner disagrees with stays, flagged ``mismatch``;
- a banner naming a different program changes nothing here;
- a calculation bound to an execution-environment manifest keeps the
  manifest's release (the manifest is immutable and names it).

Driven through the real upload routes with real ORCA, Gaussian and Molpro
output logs (the fixtures #560 pinned the banner parsers on).
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models.calculation import Calculation
from app.db.models.common import SoftwareReconciliationStatus
from app.db.models.execution_environment import ExecutionEnvironmentManifest
from app.db.models.software import Software, SoftwareRelease

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# ORCA 5.0.4 DLPNO-CCSD(T) single point, doublet, neutral.
ORCA_LOG = (FIXTURES / "orca" / "sp_dlpno_ccsdt_orca.out").read_bytes()
# Gaussian 16 Rev C.02 UB3LYP single point on triplet O.
GAUSSIAN_LOG = (FIXTURES / "gaussian" / "sp_ub3lyp_g16.log").read_bytes()
# Molpro 2026.1 CCSD(T)-F12 single point on closed-shell CH4.
MOLPRO_LOG = (FIXTURES / "molpro" / "ch4_closed_shell" / "input.out").read_bytes()
# A banner-less ORCA input deck.
ORCA_INPUT = b"! B3LYP def2-SVP\n* xyz 0 2\nH 0.0 0.0 0.0\n*\n"

W_FILLED = "software_release_version_filled_from_artifact"

XYZ_CH4 = (
    "5\nmethane\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.629 -0.629 -0.629"
)


@pytest.fixture
def stub_store_artifact(monkeypatch) -> list[str]:
    written: list[str] = []

    def _fake_store(content: bytes, sha256: str) -> str:
        written.append(sha256)
        return f"s3://test-bucket/{sha256[:2]}/{sha256}"

    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact", _fake_store
    )
    return written


def _artifact(content: bytes, *, kind: str = "output_log", filename: str = "job.out") -> dict:
    return {
        "kind": kind,
        "filename": filename,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }


def _conformer_payload(*, smiles: str, multiplicity: int, xyz: str, software_release: dict) -> dict:
    return {
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": multiplicity},
        "geometry": {"xyz_text": xyz},
        "calculation": {
            "type": "sp",
            "software_release": software_release,
            "level_of_theory": {"method": "B3LYP", "basis": "def2-SVP"},
        },
        "label": "banner-hook",
    }


def _create_calc(client, **kwargs) -> int:
    resp = client.post("/api/v1/uploads/conformers", json=_conformer_payload(**kwargs))
    assert resp.status_code == 201, resp.text
    return resp.json()["primary_calculation"]["calculation_id"]


def _post_artifacts(client, calc_id: int, *artifacts: dict):
    resp = client.post(
        f"/api/v1/calculations/{calc_id}/artifacts",
        json={"artifacts": list(artifacts)},
    )
    assert resp.status_code == 201, resp.text
    return resp


def _release_of(db_session, calc_id: int) -> tuple:
    calc = db_session.get(Calculation, calc_id)
    db_session.refresh(calc)
    release = db_session.get(SoftwareRelease, calc.software_release_id)
    return (release.software.name, release.version, release.revision, release.build)


def _version_less(db_session, name: str) -> SoftwareRelease | None:
    return db_session.scalar(
        select(SoftwareRelease)
        .join(Software, Software.id == SoftwareRelease.software_id)
        .where(Software.name == name, SoftwareRelease.version.is_(None))
    )


def _codes(resp) -> list[str]:
    return [w["code"] for w in resp.json().get("warnings", [])]


class TestVersionLessDeclarationIsRepointed:
    def test_orca_output_log_repoints_to_the_banner_version(
        self, client, db_session, stub_store_artifact
    ):
        calc_id = _create_calc(
            client, smiles="[H]", multiplicity=2, xyz="1\nH\nH 0 0 0",
            software_release={"name": "ORCA"},
        )
        assert _release_of(db_session, calc_id) == ("ORCA", None, None, None)
        version_less = _version_less(db_session, "ORCA")
        before_ref = version_less.public_ref

        resp = _post_artifacts(client, calc_id, _artifact(ORCA_LOG))

        assert _release_of(db_session, calc_id) == ("ORCA", "5.0.4", None, None)
        calc = db_session.get(Calculation, calc_id)
        assert calc.software_reconciliation_status is SoftwareReconciliationStatus.enriched
        assert calc.observed_software_banner == "orca 5.0.4"
        assert calc.declared_software_banner is None
        assert W_FILLED in _codes(resp)
        # The version-less row is re-pointed away from, never filled in place.
        db_session.refresh(version_less)
        assert (version_less.version, version_less.public_ref) == (None, before_ref)

    def test_a_banner_less_input_after_the_log_does_not_erase_the_observation(
        self, client, db_session, stub_store_artifact
    ):
        """The input deck arrives after the log, in a later request. The input
        path reconciles too, with no banner; it must not downgrade
        ``enriched``. (Within one batch the log hook runs last anyway.)"""
        calc_id = _create_calc(
            client, smiles="[H]", multiplicity=2, xyz="1\nH\nH 0 0 0",
            software_release={"name": "ORCA"},
        )

        _post_artifacts(client, calc_id, _artifact(ORCA_LOG))
        _post_artifacts(client, calc_id, _artifact(ORCA_INPUT, kind="input", filename="job.in"))

        calc = db_session.get(Calculation, calc_id)
        db_session.refresh(calc)
        assert _release_of(db_session, calc_id) == ("ORCA", "5.0.4", None, None)
        assert calc.software_reconciliation_status is SoftwareReconciliationStatus.enriched
        assert calc.observed_software_banner == "orca 5.0.4"

    def test_gaussian_log_inline_in_a_bundle_repoints_with_revision_and_build(
        self, client, db_session, stub_store_artifact
    ):
        """The contribution-bundle path (ARC's bundle mode) runs the hook too,
        and the target is the exact version/revision/build tuple."""
        resp = client.post(
            "/api/v1/uploads/computed-species",
            json={
                "species_entry": {"smiles": "[O]", "charge": 0, "multiplicity": 3},
                "conformers": [
                    {
                        "key": "c0",
                        "geometry": {"xyz_text": "1\nO\nO 0 0 0"},
                        "primary_calculation": {
                            "key": "opt0",
                            "type": "opt",
                            "opt_result": {"converged": True},
                            "software_release": {"name": "Gaussian"},
                            "level_of_theory": {"method": "UB3LYP", "basis": "6-31G(d)"},
                            "artifacts": [_artifact(GAUSSIAN_LOG, filename="opt0.log")],
                        },
                    }
                ],
            },
        )
        assert resp.status_code == 201, resp.text
        calc_id = resp.json()["conformers"][0]["primary_calculation"]["calculation_id"]

        assert _release_of(db_session, calc_id) == (
            "Gaussian", "16", "C.02", "ES64L-G16RevC.02",
        )
        assert W_FILLED in _codes(resp)


def test_a_declared_version_the_banner_disagrees_with_is_kept_and_flagged(
    client, db_session, stub_store_artifact
):
    calc_id = _create_calc(
        client, smiles="C", multiplicity=1, xyz=XYZ_CH4,
        software_release={"name": "Molpro", "version": "2022.1"},
    )

    resp = _post_artifacts(client, calc_id, _artifact(MOLPRO_LOG))

    assert _release_of(db_session, calc_id) == ("Molpro", "2022.1", None, None)
    calc = db_session.get(Calculation, calc_id)
    db_session.refresh(calc)
    assert calc.software_reconciliation_status is SoftwareReconciliationStatus.mismatch
    assert calc.observed_software_banner == "molpro 2026.1"
    assert W_FILLED not in _codes(resp)


def test_a_banner_naming_a_different_program_repoints_nothing(
    client, db_session, stub_store_artifact
):
    calc_id = _create_calc(
        client, smiles="C", multiplicity=1, xyz=XYZ_CH4,
        software_release={"name": "ORCA"},
    )

    resp = _post_artifacts(client, calc_id, _artifact(MOLPRO_LOG))

    assert _release_of(db_session, calc_id) == ("ORCA", None, None, None)
    calc = db_session.get(Calculation, calc_id)
    db_session.refresh(calc)
    assert calc.software_reconciliation_status is SoftwareReconciliationStatus.declared_only
    assert calc.observed_software_banner is None
    assert calc.declared_software_banner is None
    assert W_FILLED not in _codes(resp)


def test_a_manifest_bound_calculation_records_the_banner_and_keeps_its_release(
    client, db_session, stub_store_artifact
):
    """The manifest names the version-less release and is immutable; the
    binding trigger requires the calculation to cite the same one. The banner
    is recorded, both releases stay as declared, and the upload succeeds."""
    environment = {
        "schema_version": "tckdb.execution-environment.v1",
        "software_release": {"name": "ORCA"},
        "runtime": {"runtime_kind": "container", "image": "registry.example/orca@sha256:" + "a" * 64},
        "executable": {"locator": "file:///opt/orca/orca", "digest": "sha256:" + "b" * 64},
        "closure": [
            {"role": "runtime", "locator": "registry.example/orca@sha256:" + "a" * 64, "digest": "sha256:" + "a" * 64},
            {"role": "executable", "locator": "file:///opt/orca/orca", "digest": "sha256:" + "b" * 64},
        ],
    }
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json={
            "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
            "conformers": [
                {
                    "key": "c0",
                    "geometry": {"xyz_text": "1\nH\nH 0 0 0"},
                    "primary_calculation": {
                        "key": "opt0",
                        "type": "opt",
                        "opt_result": {"converged": True},
                        "software_release": {"name": "ORCA"},
                        "level_of_theory": {"method": "B3LYP", "basis": "def2-SVP"},
                        "execution_environment": environment,
                        "artifacts": [_artifact(ORCA_LOG)],
                    },
                }
            ],
        },
    )
    assert resp.status_code == 201, resp.text
    calc_id = resp.json()["conformers"][0]["primary_calculation"]["calculation_id"]

    calc = db_session.get(Calculation, calc_id)
    db_session.refresh(calc)
    manifest = db_session.get(ExecutionEnvironmentManifest, calc.execution_environment_manifest_id)
    assert manifest is not None
    assert calc.software_release_id == manifest.software_release_id
    assert _release_of(db_session, calc_id) == ("ORCA", None, None, None)
    assert calc.software_reconciliation_status is SoftwareReconciliationStatus.enriched
    assert calc.observed_software_banner == "orca 5.0.4"
    assert W_FILLED not in _codes(resp)


# Review findings on #565 ----------------------------------------------------

# Molpro 2015.1.37 TS frequency run. ARC reads the ``NAME : 2015.1.37`` header;
# the banner line TCKDB parses says ``Version 2015.1``.
MOLPRO_2015_LOG = (FIXTURES / "molpro" / "molpro_TS_freq.out").read_bytes()
# ORCA 6.1.0 optimisation (a different ORCA version from ORCA_LOG's 5.0.4).
ORCA_610_LOG = (FIXTURES / "orca" / "opt_orca.out").read_bytes()


def test_a_banner_that_is_a_coarser_reading_of_the_declared_version_is_a_match(
    client, db_session, stub_store_artifact
):
    """Finding 1. ``2015.1`` is ``2015.1.37`` read to fewer components: the
    same release, not a contradiction. ``mismatch`` would fail the
    reproducibility rubric's ``calculation_metadata`` check for a deposit
    that is internally consistent."""
    calc_id = _create_calc(
        client, smiles="C", multiplicity=1, xyz=XYZ_CH4,
        software_release={"name": "Molpro", "version": "2015.1.37"},
    )

    _post_artifacts(client, calc_id, _artifact(MOLPRO_2015_LOG))

    calc = db_session.get(Calculation, calc_id)
    db_session.refresh(calc)
    assert calc.software_reconciliation_status is SoftwareReconciliationStatus.matched
    assert calc.observed_software_banner == "molpro 2015.1"
    assert _release_of(db_session, calc_id) == ("Molpro", "2015.1.37", None, None)


def test_a_declared_build_the_banner_contradicts_is_a_mismatch_not_a_fill(
    client, db_session, stub_store_artifact
):
    """Finding 2. A declared G09 build with no version, and a G16 C.02 log:
    filling the version would mint ``("16", "C.02", "EM64L-G09RevD.01")``,
    a release that never existed."""
    calc_id = _create_calc(
        client, smiles="[O]", multiplicity=3, xyz="1\nO\nO 0 0 0",
        software_release={"name": "Gaussian", "build": "EM64L-G09RevD.01"},
    )

    resp = _post_artifacts(client, calc_id, _artifact(GAUSSIAN_LOG))

    assert _release_of(db_session, calc_id) == ("Gaussian", None, None, "EM64L-G09RevD.01")
    calc = db_session.get(Calculation, calc_id)
    db_session.refresh(calc)
    assert calc.software_reconciliation_status is SoftwareReconciliationStatus.mismatch
    assert W_FILLED not in _codes(resp)


@pytest.mark.parametrize("order", ["5.0.4 first", "6.1.0 first"])
def test_two_disagreeing_logs_in_one_batch_fill_nothing_in_either_order(
    client, db_session, stub_store_artifact, order
):
    """Finding 3. Each banner is compared with the *declared* release. Two
    logs naming different versions of the same program contradict each
    other, so neither fills the version, whichever arrives first."""
    calc_id = _create_calc(
        client, smiles="[H]", multiplicity=2, xyz="1\nH\nH 0 0 0",
        software_release={"name": "ORCA"},
    )
    logs = [_artifact(ORCA_LOG, filename="a.out"), _artifact(ORCA_610_LOG, filename="b.out")]
    if order == "6.1.0 first":
        logs.reverse()

    resp = _post_artifacts(client, calc_id, *logs)

    assert _release_of(db_session, calc_id) == ("ORCA", None, None, None)
    calc = db_session.get(Calculation, calc_id)
    db_session.refresh(calc)
    assert calc.software_reconciliation_status is SoftwareReconciliationStatus.mismatch
    assert calc.observed_software_banner == "orca 5.0.4 | orca 6.1.0"
    assert W_FILLED not in _codes(resp)


def test_two_agreeing_logs_in_one_batch_fill_once(client, db_session, stub_store_artifact):
    calc_id = _create_calc(
        client, smiles="[H]", multiplicity=2, xyz="1\nH\nH 0 0 0",
        software_release={"name": "ORCA"},
    )

    _post_artifacts(
        client, calc_id, _artifact(ORCA_LOG, filename="a.out"), _artifact(ORCA_LOG, filename="b.out")
    )

    assert _release_of(db_session, calc_id) == ("ORCA", "5.0.4", None, None)
    calc = db_session.get(Calculation, calc_id)
    db_session.refresh(calc)
    assert calc.software_reconciliation_status is SoftwareReconciliationStatus.enriched
