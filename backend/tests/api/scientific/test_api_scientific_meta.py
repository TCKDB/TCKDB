"""API tests for GET /api/v1/scientific/meta/* (Phase 7 vocabulary reads)."""

from __future__ import annotations

from tests.services.scientific_read._factories import (
    make_lot,
    make_software,
    make_software_release,
    make_workflow_tool_release,
)


def test_methods_lists_distinct_with_counts(client, db_session):
    make_lot(db_session, method="CCSD(T)", basis="cc-pVTZ")
    make_lot(db_session, method="B3LYP", basis="6-31G(d)")

    body = client.get("/api/v1/scientific/meta/methods").json()

    methods = {r["value"] for r in body["results"]}
    assert {"CCSD(T)", "B3LYP"} <= methods
    for r in body["results"]:
        assert r["count"] >= 1


def test_basis_sets_lists_distinct(client, db_session):
    make_lot(db_session, method="B3LYP", basis="6-311+G(3df,2p)")
    body = client.get("/api/v1/scientific/meta/basis-sets").json()
    assert "6-311+G(3df,2p)" in {r["value"] for r in body["results"]}


def test_software_lists_distinct(client, db_session):
    make_software(db_session, name="Orca")
    body = client.get("/api/v1/scientific/meta/software").json()
    assert "Orca" in {r["value"] for r in body["results"]}


def test_reaction_families_lists_canonical_vocabulary(client, db_session):
    # The seeded canonical families are always present (count >= 0).
    body = client.get("/api/v1/scientific/meta/reaction-families").json()
    assert isinstance(body["results"], list)
    assert len(body["results"]) >= 1
    assert all("value" in r and "count" in r for r in body["results"])


def test_workflow_tools_lists_exact_distinct_set(client, db_session):
    """The full result set, not a contains-check.

    A query that returned every row in the database (or a hard-coded
    static list) would also satisfy a mere ``in`` assertion; asserting
    the exact set is what actually exercises the ``GROUP BY name``.
    """
    make_workflow_tool_release(db_session, name="arc", version="1.2.3")
    make_workflow_tool_release(db_session, name="rmg", version="3.1.0")

    body = client.get("/api/v1/scientific/meta/workflow-tools").json()

    assert {(r["value"], r["count"]) for r in body["results"]} == {
        ("arc", 1),
        ("rmg", 1),
    }


def test_software_versions_returns_only_that_softwares_versions(client, db_session):
    """A ``software=`` filter that is accepted and ignored is the classic
    vacuous pass this endpoint exists to prevent: seed two software rows
    with different versions and confirm the other software's version is
    absent from a scoped response, not merely that the requested one is
    present."""
    make_software_release(db_session, name="gaussian", version="16")
    make_software_release(db_session, name="orca", version="5.0")

    body = client.get(
        "/api/v1/scientific/meta/software-versions", params={"software": "gaussian"}
    ).json()

    assert {r["value"] for r in body["results"]} == {"16"}


def test_software_versions_missing_parent_is_a_coded_refusal(client, db_session):
    """No ``software=`` is refused with a coded 422, not a bare FastAPI
    validation error and not an unscoped dump of every package's
    versions."""
    resp = client.get("/api/v1/scientific/meta/software-versions")

    assert resp.status_code == 422
    assert resp.json()["code"] == "missing_version_parent"


def test_software_versions_unknown_parent_is_empty_not_an_error(client, db_session):
    make_software_release(db_session, name="gaussian", version="16")

    resp = client.get(
        "/api/v1/scientific/meta/software-versions",
        params={"software": "no-such-package"},
    )

    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_software_versions_counts_distinguish_release_row_counts(client, db_session):
    """Two values with genuinely different counts — equal counts across
    the board cannot tell a correct count apart from a constant."""
    make_software_release(db_session, name="gaussian", version="16", revision="a")
    make_software_release(db_session, name="gaussian", version="16", revision="b")
    make_software_release(db_session, name="gaussian", version="17")

    body = client.get(
        "/api/v1/scientific/meta/software-versions", params={"software": "gaussian"}
    ).json()

    assert {(r["value"], r["count"]) for r in body["results"]} == {
        ("16", 2),
        ("17", 1),
    }


def test_workflow_tool_versions_returns_only_that_tools_versions(client, db_session):
    make_workflow_tool_release(db_session, name="arc", version="1.2.3")
    make_workflow_tool_release(db_session, name="rmg", version="3.1.0")

    body = client.get(
        "/api/v1/scientific/meta/workflow-tool-versions",
        params={"workflow_tool": "arc"},
    ).json()

    assert {r["value"] for r in body["results"]} == {"1.2.3"}


def test_workflow_tool_versions_missing_parent_is_a_coded_refusal(client, db_session):
    resp = client.get("/api/v1/scientific/meta/workflow-tool-versions")

    assert resp.status_code == 422
    assert resp.json()["code"] == "missing_version_parent"


def test_workflow_tool_versions_unknown_parent_is_empty_not_an_error(
    client, db_session
):
    make_workflow_tool_release(db_session, name="arc", version="1.2.3")

    resp = client.get(
        "/api/v1/scientific/meta/workflow-tool-versions",
        params={"workflow_tool": "no-such-tool"},
    )

    assert resp.status_code == 200
    assert resp.json()["results"] == []


def test_workflow_tool_versions_counts_distinguish_release_row_counts(
    client, db_session
):
    make_workflow_tool_release(
        db_session, name="arc", version="1.2.3", git_commit="a" * 40
    )
    make_workflow_tool_release(
        db_session, name="arc", version="1.2.3", git_commit="b" * 40
    )
    make_workflow_tool_release(db_session, name="arc", version="1.3.0")

    body = client.get(
        "/api/v1/scientific/meta/workflow-tool-versions",
        params={"workflow_tool": "arc"},
    ).json()

    assert {(r["value"], r["count"]) for r in body["results"]} == {
        ("1.2.3", 2),
        ("1.3.0", 1),
    }


def test_reaction_families_carry_a_readable_display_name(client, db_session):
    """``value`` stays the filter token; ``display_name`` is the readable form.

    A family whose meaning is unresolved keeps its identifier as its display
    name rather than being shown half-translated.
    """
    body = client.get("/api/v1/scientific/meta/reaction-families").json()
    by_value = {r["value"]: r["display_name"] for r in body["results"]}

    assert by_value["H_Abstraction"] == "Hydrogen Abstraction"
    assert by_value["R_Addition_MultipleBond"] == "Radical Addition Multiple Bond"
    assert by_value["Surface_Adsorption_Bidentate"] == "Surface Adsorption Bidentate"
    assert (
        by_value["Surface_Carbonate_2F_Decomposition"]
        == "Surface_Carbonate_2F_Decomposition"
    )
    assert all(r["display_name"] for r in body["results"])
