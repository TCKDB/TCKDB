"""Tests for Phase 2 calculation sub-resource endpoints.

All test data is seeded via raw ORM inserts (no upload workflow creates
scan/IRC/NEB/parameter/artifact/validation data). A conformer upload
provides the parent calculation row; the helper then patches the
calculation type to match the child family being tested so the test
data is scientifically coherent, not just mechanically valid.
"""

from __future__ import annotations

from sqlalchemy import update

from app.db.models.calculation import (
    Calculation,
    CalculationArtifact,
    CalculationGeometryValidation,
    CalculationIRCPoint,
    CalculationIRCResult,
    CalculationNEBImageResult,
    CalculationParameter,
    CalculationScanCoordinate,
    CalculationScanPoint,
    CalculationScanPointCoordinateValue,
    CalculationScanResult,
)
from app.db.models.common import ArtifactKind, IRCDirection, ValidationStatus


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
        "label": "phase2-test",
    }


def _get_calc_id(client, db_session=None, calc_type=None) -> int:
    """Upload a conformer and return the calculation id.

    When *calc_type* is given (e.g. "scan", "irc", "neb"), the parent
    calculation's type column is patched so the test data is scientifically
    coherent with the child rows being inserted.
    """
    client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
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
# Scan family
# ---------------------------------------------------------------------------


class TestScanResult:
    def test_scan_result_404_when_absent(self, client):
        calc_id = _get_calc_id(client)
        resp = client.get(f"/api/v1/calculations/{calc_id}/scan-result")
        assert resp.status_code == 404

    def test_scan_result_parent_404(self, client):
        resp = client.get("/api/v1/calculations/999999/scan-result")
        assert resp.status_code == 404

    def test_scan_result_nests_children(self, client, db_session):
        calc_id = _get_calc_id(client, db_session, calc_type="scan")

        db_session.add(CalculationScanResult(
            calculation_id=calc_id, dimension=1, is_relaxed=True,
        ))
        db_session.add(CalculationScanCoordinate(
            calculation_id=calc_id, coordinate_index=1,
            coordinate_kind="bond", atom1_index=1, atom2_index=2,
            step_count=5, step_size=0.1, start_value=0.8, end_value=1.2,
        ))
        db_session.add(CalculationScanPoint(
            calculation_id=calc_id, point_index=1,
            electronic_energy_hartree=-1.0,
        ))
        db_session.add(CalculationScanPointCoordinateValue(
            calculation_id=calc_id, point_index=1,
            coordinate_index=1, coordinate_value=0.8,
        ))
        db_session.flush()

        resp = client.get(f"/api/v1/calculations/{calc_id}/scan-result")
        assert resp.status_code == 200
        data = resp.json()
        assert data["dimension"] == 1
        assert data["is_relaxed"] is True
        assert len(data["coordinates"]) == 1
        assert len(data["points"]) == 1
        assert len(data["points"][0]["coordinate_values"]) == 1


class TestScanCoordinates:
    def test_empty(self, client):
        calc_id = _get_calc_id(client)
        resp = client.get(f"/api/v1/calculations/{calc_id}/scan-coordinates")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_ordered_by_coordinate_index(self, client, db_session):
        calc_id = _get_calc_id(client, db_session, calc_type="scan")
        for idx in [2, 1]:
            db_session.add(CalculationScanCoordinate(
                calculation_id=calc_id, coordinate_index=idx,
                coordinate_kind="bond", atom1_index=1, atom2_index=idx + 1,
            ))
        db_session.flush()

        resp = client.get(f"/api/v1/calculations/{calc_id}/scan-coordinates")
        assert resp.status_code == 200
        coords = resp.json()
        assert len(coords) == 2
        assert coords[0]["coordinate_index"] == 1
        assert coords[1]["coordinate_index"] == 2


class TestScanPoints:
    def test_empty(self, client):
        calc_id = _get_calc_id(client)
        resp = client.get(f"/api/v1/calculations/{calc_id}/scan-points")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_with_coordinate_values(self, client, db_session):
        calc_id = _get_calc_id(client, db_session, calc_type="scan")
        db_session.add(CalculationScanCoordinate(
            calculation_id=calc_id, coordinate_index=1,
            coordinate_kind="bond", atom1_index=1, atom2_index=2,
        ))
        db_session.add(CalculationScanPoint(
            calculation_id=calc_id, point_index=1,
            electronic_energy_hartree=-1.1,
        ))
        db_session.add(CalculationScanPointCoordinateValue(
            calculation_id=calc_id, point_index=1,
            coordinate_index=1, coordinate_value=0.9,
        ))
        db_session.flush()

        resp = client.get(f"/api/v1/calculations/{calc_id}/scan-points")
        assert resp.status_code == 200
        points = resp.json()
        assert len(points) == 1
        assert points[0]["electronic_energy_hartree"] == -1.1
        assert len(points[0]["coordinate_values"]) == 1
        assert points[0]["coordinate_values"][0]["coordinate_value"] == 0.9


# ---------------------------------------------------------------------------
# IRC family
# ---------------------------------------------------------------------------


class TestIRCResult:
    def test_irc_result_404_when_absent(self, client):
        calc_id = _get_calc_id(client)
        resp = client.get(f"/api/v1/calculations/{calc_id}/irc-result")
        assert resp.status_code == 404

    def test_irc_result_nests_points(self, client, db_session):
        calc_id = _get_calc_id(client, db_session, calc_type="irc")
        db_session.add(CalculationIRCResult(
            calculation_id=calc_id, direction=IRCDirection.both,
            has_forward=True, has_reverse=True, point_count=3,
        ))
        for idx in [0, 1, 2]:
            db_session.add(CalculationIRCPoint(
                calculation_id=calc_id, point_index=idx,
                direction=IRCDirection.forward if idx > 0 else None,
                is_ts=(idx == 0),
                electronic_energy_hartree=-1.0 + idx * 0.01,
            ))
        db_session.flush()

        resp = client.get(f"/api/v1/calculations/{calc_id}/irc-result")
        assert resp.status_code == 200
        data = resp.json()
        assert data["direction"] == "both"
        assert data["point_count"] == 3
        assert len(data["points"]) == 3
        assert data["points"][0]["is_ts"] is True


class TestIRCPoints:
    def test_empty(self, client):
        calc_id = _get_calc_id(client)
        resp = client.get(f"/api/v1/calculations/{calc_id}/irc-points")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_ordered_including_zero_index(self, client, db_session):
        calc_id = _get_calc_id(client, db_session, calc_type="irc")
        for idx in [2, 0, 1]:
            db_session.add(CalculationIRCPoint(
                calculation_id=calc_id, point_index=idx,
                is_ts=(idx == 0),
                electronic_energy_hartree=-1.0 + idx * 0.01,
            ))
        db_session.flush()

        resp = client.get(f"/api/v1/calculations/{calc_id}/irc-points")
        assert resp.status_code == 200
        points = resp.json()
        assert len(points) == 3
        assert [p["point_index"] for p in points] == [0, 1, 2]


# ---------------------------------------------------------------------------
# NEB family
# ---------------------------------------------------------------------------


class TestNEBImages:
    def test_empty(self, client):
        calc_id = _get_calc_id(client)
        resp = client.get(f"/api/v1/calculations/{calc_id}/neb-images")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_ordered_including_zero_index(self, client, db_session):
        calc_id = _get_calc_id(client, db_session, calc_type="neb")
        for idx in [2, 0, 1]:
            db_session.add(CalculationNEBImageResult(
                calculation_id=calc_id, image_index=idx,
                electronic_energy_hartree=-1.0 + idx * 0.01,
                is_climbing_image=(idx == 1),
            ))
        db_session.flush()

        resp = client.get(f"/api/v1/calculations/{calc_id}/neb-images")
        assert resp.status_code == 200
        images = resp.json()
        assert len(images) == 3
        assert [i["image_index"] for i in images] == [0, 1, 2]
        assert images[1]["is_climbing_image"] is True


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


class TestParameters:
    def test_empty(self, client):
        calc_id = _get_calc_id(client)
        resp = client.get(f"/api/v1/calculations/{calc_id}/parameters")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_ordered_by_index_then_id(self, client, db_session):
        calc_id = _get_calc_id(client)
        # parameter_index=1, parameter_index=None, parameter_index=2
        db_session.add(CalculationParameter(
            calculation_id=calc_id, raw_key="maxcycle",
            raw_value="100", parameter_index=2,
        ))
        db_session.add(CalculationParameter(
            calculation_id=calc_id, raw_key="scf_conv",
            raw_value="tight", parameter_index=1,
        ))
        db_session.add(CalculationParameter(
            calculation_id=calc_id, raw_key="memory",
            raw_value="4gb", parameter_index=None,
        ))
        db_session.flush()

        resp = client.get(f"/api/v1/calculations/{calc_id}/parameters")
        assert resp.status_code == 200
        params = resp.json()
        assert len(params) == 3
        # index=1 first, index=2 second, NULL last
        assert params[0]["raw_key"] == "scf_conv"
        assert params[0]["parameter_index"] == 1
        assert params[1]["raw_key"] == "maxcycle"
        assert params[1]["parameter_index"] == 2
        assert params[2]["raw_key"] == "memory"
        assert params[2]["parameter_index"] is None


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


class TestArtifacts:
    def test_empty(self, client):
        calc_id = _get_calc_id(client)
        resp = client.get(f"/api/v1/calculations/{calc_id}/artifacts")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_ordered_by_id(self, client, db_session):
        calc_id = _get_calc_id(client)
        db_session.add(CalculationArtifact(
            calculation_id=calc_id, kind=ArtifactKind.input,
            uri="s3://bucket/input.gjf",
        ))
        db_session.add(CalculationArtifact(
            calculation_id=calc_id, kind=ArtifactKind.output_log,
            uri="s3://bucket/output.log", sha256="a" * 64, bytes=12345,
        ))
        db_session.flush()

        resp = client.get(f"/api/v1/calculations/{calc_id}/artifacts")
        assert resp.status_code == 200
        arts = resp.json()
        assert len(arts) == 2
        assert arts[0]["id"] < arts[1]["id"]
        assert arts[0]["kind"] == "input"
        assert arts[1]["sha256"] == "a" * 64


# ---------------------------------------------------------------------------
# Geometry validation
# ---------------------------------------------------------------------------


class TestGeometryValidation:
    def test_404_when_absent(self, client):
        calc_id = _get_calc_id(client)
        resp = client.get(
            f"/api/v1/calculations/{calc_id}/geometry-validation"
        )
        assert resp.status_code == 404

    def test_present(self, client, db_session):
        calc_id = _get_calc_id(client, db_session, calc_type="opt")
        db_session.add(CalculationGeometryValidation(
            calculation_id=calc_id,
            species_smiles="[H]",
            is_isomorphic=True,
            rmsd=0.001,
            n_mappings=1,
            validation_status=ValidationStatus.passed,
            validation_reason="Isomorphic with low RMSD",
        ))
        db_session.flush()

        resp = client.get(
            f"/api/v1/calculations/{calc_id}/geometry-validation"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_isomorphic"] is True
        assert data["validation_status"] == "passed"
        assert data["rmsd"] == 0.001
        assert data["species_smiles"] == "[H]"
