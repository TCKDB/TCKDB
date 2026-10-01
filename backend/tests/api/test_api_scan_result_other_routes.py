"""``scan_result`` is a field of the shared calculation payload (#621).

It was added so a transition-state scan can carry its points, and it lives on
``CalculationWithResultsPayload``, which every standalone route uses for its
calculations. That is a cross-route widening, and this file says what it does
to the other routes instead of leaving it to be discovered:

* where a route already accepted a ``scan`` calculation (the conformer route
  does not restrict its *primary* calculation's type), the scan's points are
  now stored, where before the same payload could not carry them;
* where a route does not allow a ``scan`` (conformer additional calculations),
  nothing changes;
* ``scan_result`` on any other calculation type is refused by the payload.
"""

from __future__ import annotations

from tests.api.test_api_auth import _hydrogen_conformer_payload
from tests.workflows.test_computed_reaction_upload import _ch4_scan_result_payload

_CONFORMERS = "/api/v1/uploads/conformers"


def test_a_conformer_scan_primary_now_stores_its_points(client):
    payload = _hydrogen_conformer_payload()
    payload["calculation"]["type"] = "scan"
    payload["calculation"]["scan_result"] = _ch4_scan_result_payload(points=3)
    response = client.post(_CONFORMERS, json=payload)
    assert response.status_code == 201, response.text[:800]
    ref = response.json()["primary_calculation"]["calculation_ref"]

    read = client.get(f"/api/v1/scientific/calculations/{ref}?include=results")
    assert read.status_code == 200, read.text[:800]
    results = read.json()["record"]["results"]
    assert results["kind"] == "scan"
    assert results["scan"]["dimension"] == 1


def test_a_scan_result_on_a_conformer_sp_is_refused_by_the_payload(client):
    payload = _hydrogen_conformer_payload()
    payload["calculation"]["scan_result"] = _ch4_scan_result_payload()
    response = client.post(_CONFORMERS, json=payload)
    assert response.status_code == 422, response.text[:800]
    assert "scan_result" in str(response.json()["detail"])


def test_a_conformer_additional_scan_is_still_not_an_allowed_type(client):
    payload = _hydrogen_conformer_payload()
    payload["additional_calculations"] = [
        {
            "type": "scan",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
            "scan_result": _ch4_scan_result_payload(),
        }
    ]
    response = client.post(_CONFORMERS, json=payload)
    assert response.status_code == 422, response.text[:800]
