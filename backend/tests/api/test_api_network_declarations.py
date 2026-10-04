"""Network declarations through the live upload route and the scientific read route (chunk 1).

The route turns a wire refusal into a coded 422 whose ``context.field`` names the declaration, and
an accepted upload reads back through ``/scientific/network-solves/{ref}`` carrying what was
deposited. No response may carry a database id inside a refusal.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.network_pdep import NetworkSolve
from tests.workflows.test_network_declarations import _declared_payload

_PDEP_URL = "/api/v1/uploads/networks/pdep"


def test_a_declared_upload_is_accepted_and_reads_back_through_the_solve_route(client, db_session) -> None:
    response = client.post(_PDEP_URL, json=_declared_payload())
    assert response.status_code == 201, response.text

    solve = db_session.scalars(select(NetworkSolve)).one()
    read = client.get(f"/api/v1/scientific/network-solves/{solve.public_ref}")
    assert read.status_code == 200, read.text
    core = read.json()["record"]["network_solve"]
    assert core["target"]["bath_scope"] == "specified_collider"
    assert core["target"]["partition"]["retained"] and all(
        len(h) == 64 for h in core["target"]["partition"]["retained"]
    )  # composition hashes, never the depositor's local keys
    assert core["protocol"]["reduction_method"] == "chemically_significant_eigenvalues"
    assert core["validation"]["entries"][0]["value"] == 0.02
    assert [d["determination_key"] for d in core["determinations"]] == ["d_assoc", "d_diss"]
    assert core["declarations_unreadable"] is False
    assert "solve_id" not in core["determinations"][0]


def test_an_upload_without_declarations_still_reads_back_as_not_stated(client, db_session) -> None:
    from tests.workflows.test_network_pdep_upload import _full_payload

    assert client.post(_PDEP_URL, json=_full_payload(include_solve=True)).status_code == 201
    solve = db_session.scalars(select(NetworkSolve)).one()
    core = client.get(f"/api/v1/scientific/network-solves/{solve.public_ref}").json()["record"]["network_solve"]
    assert core["target"] is None and core["protocol"] is None and core["validation"] is None
    assert core["determinations"] == []


def test_a_contradictory_declaration_is_a_coded_422_naming_its_field(client) -> None:
    payload = _declared_payload()
    payload["solve"]["target"]["bath_scope"] = "fixed_mixture"
    response = client.post(_PDEP_URL, json=payload)
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "network_declaration_invalid"
    assert body["context"]["field"] == "solve.target"
    assert "fixed_mixture contradicts a solve with 1 bath species" in str(body["detail"])
    assert "id" not in body["context"]


def test_an_unsupported_declaration_version_is_its_own_coded_422(client) -> None:
    payload = _declared_payload()
    payload["solve"]["protocol"]["version"] = 2
    response = client.post(_PDEP_URL, json=payload)
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "network_declaration_version_unsupported"
    assert body["context"]["supported_versions"] == [1]
