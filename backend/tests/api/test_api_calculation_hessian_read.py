"""Tests for ``GET /calculations/{calculation_id}/hessian`` (C-Q2).

Before this route existed there was no public read for a stored Hessian
at all -- ``grep -rn "CalculationHessian" backend/app/api/`` returned
nothing. Mirrors the sibling Tier-B reads (``sp-result``, ``opt-result``,
``freq-result``): 404 on a missing row, exact field-for-field body when a
row exists. No re-derivation -- the packed lower triangle comes back
exactly as stored.
"""

from __future__ import annotations

from app.db.models.calculation import Calculation, CalculationHessian
from app.db.models.common import HessianSource
from tests.services.scientific_read._factories import attach_hessian, make_geometry


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
            "type": "freq",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        "label": "hessian-read-test",
    }


def _get_calc_id(client) -> int:
    resp = client.post("/api/v1/uploads/conformers", json=_hydrogen_conformer_payload())
    assert resp.status_code == 201, resp.text
    return resp.json()["primary_calculation"]["calculation_id"]


class TestHessianReadMissing:
    def test_no_stored_hessian_returns_404_with_catalogued_code(self, client):
        calc_id = _get_calc_id(client)
        resp = client.get(f"/api/v1/calculations/{calc_id}/hessian")
        assert resp.status_code == 404
        assert resp.json().get("code") == "hessian_not_found"

    def test_missing_calculation_returns_404(self, client):
        resp = client.get("/api/v1/calculations/999999/hessian")
        assert resp.status_code == 404


class TestHessianReadPresent:
    def test_returns_the_stored_matrix_exactly(self, client, db_session):
        calc_id = _get_calc_id(client)
        calc = db_session.get(Calculation, calc_id)
        assert calc is not None

        geometry = make_geometry(db_session, natoms=2)
        lower_triangle = [float(i) * 0.1 for i in range((3 * 2) * (3 * 2 + 1) // 2)]
        row = attach_hessian(
            db_session,
            calculation=calc,
            geometry=geometry,
            natoms=2,
            source=HessianSource.uploaded,
            lower_triangle=lower_triangle,
        )
        db_session.flush()

        resp = client.get(f"/api/v1/calculations/{calc_id}/hessian")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["calculation_id"] == calc_id
        assert body["geometry_id"] == geometry.id
        assert body["natoms"] == 2
        assert body["lower_triangle_hartree_bohr2"] == lower_triangle
        assert body["source"] == "uploaded"
        assert body["parser_version"] == row.parser_version
        assert body["note"] == row.note

    def test_no_re_derivation_packed_list_is_verbatim(self, client, db_session):
        """The route returns the stored packed lower triangle, unmodified --
        no re-derivation of frequencies, imaginary counts, or anything
        else. Distinct floats at every position guard against a
        transposition/re-derivation bug that would still pass a
        same-value smoke test."""
        calc_id = _get_calc_id(client)
        calc = db_session.get(Calculation, calc_id)
        geometry = make_geometry(db_session, natoms=1)
        n = 3 * 1
        lower_triangle = [1.0 + i for i in range(n * (n + 1) // 2)]
        attach_hessian(
            db_session,
            calculation=calc,
            geometry=geometry,
            natoms=1,
            lower_triangle=lower_triangle,
        )
        db_session.flush()

        resp = client.get(f"/api/v1/calculations/{calc_id}/hessian")
        assert resp.status_code == 200
        assert resp.json()["lower_triangle_hartree_bohr2"] == lower_triangle

    def test_row_count_is_unaffected_by_a_second_read(self, client, db_session):
        calc_id = _get_calc_id(client)
        calc = db_session.get(Calculation, calc_id)
        geometry = make_geometry(db_session, natoms=1)
        attach_hessian(db_session, calculation=calc, geometry=geometry, natoms=1)
        db_session.flush()

        r1 = client.get(f"/api/v1/calculations/{calc_id}/hessian")
        r2 = client.get(f"/api/v1/calculations/{calc_id}/hessian")
        assert r1.status_code == r2.status_code == 200
        assert r1.json() == r2.json()
        count = (
            db_session.query(CalculationHessian)
            .filter(CalculationHessian.calculation_id == calc_id)
            .count()
        )
        assert count == 1
