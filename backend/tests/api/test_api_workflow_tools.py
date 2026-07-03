"""API tests for the workflow-tool and workflow-tool-release read endpoints.

Fixture data is inserted directly through the ORM — the upload flows do not
currently exercise the workflow-tool provenance tables, so going through
uploads is not the canonical way to seed these rows.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.db.models.workflow import WorkflowTool, WorkflowToolRelease

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def arc_tool(db_session) -> WorkflowTool:
    tool = WorkflowTool(name="ARC", description="Automated Rate Calculator")
    db_session.add(tool)
    db_session.flush()
    return tool


@pytest.fixture
def tandem_tool(db_session) -> WorkflowTool:
    tool = WorkflowTool(name="Tandem", description="Tandem workflow runner")
    db_session.add(tool)
    db_session.flush()
    return tool


def _release(
    tool: WorkflowTool,
    *,
    version: str | None = None,
    git_commit: str | None = None,
    release_date: date | None = None,
    notes: str | None = None,
) -> WorkflowToolRelease:
    return WorkflowToolRelease(
        workflow_tool_id=tool.id,
        version=version,
        git_commit=git_commit,
        release_date=release_date,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# GET /workflow-tools
# ---------------------------------------------------------------------------


class TestListWorkflowTools:
    def test_list_empty(self, client):
        resp = client.get("/api/v1/workflow-tools")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["items"] == []

    def test_list_returns_created(self, client, arc_tool, tandem_tool):
        resp = client.get("/api/v1/workflow-tools")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        names = [item["name"] for item in body["items"]]
        assert names == ["ARC", "Tandem"]  # name ASC

    def test_filter_by_name_partial_ci(self, client, arc_tool, tandem_tool):
        resp = client.get(
            "/api/v1/workflow-tools", params={"name": "arc"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["name"] == "ARC"

    def test_filter_by_name_no_match(self, client, arc_tool):
        resp = client.get(
            "/api/v1/workflow-tools", params={"name": "nonexistent"}
        )
        assert resp.json()["total"] == 0

    def test_list_deterministic_ordering(self, client, db_session):
        for name in ["zeta", "alpha", "Mu", "beta"]:
            db_session.add(WorkflowTool(name=name))
        db_session.flush()

        resp = client.get("/api/v1/workflow-tools")
        names = [item["name"] for item in resp.json()["items"]]
        # name ASC then id ASC; PostgreSQL default text sort is case-sensitive
        # (uppercase first), so this is stable and deterministic.
        assert names == sorted(names)


# ---------------------------------------------------------------------------
# GET /workflow-tools/{id}
# ---------------------------------------------------------------------------


class TestGetWorkflowTool:
    def test_returns_tool_with_releases(self, client, db_session, arc_tool):
        old = _release(arc_tool, version="1.0", release_date=date(2023, 1, 1))
        new = _release(arc_tool, version="2.0", release_date=date(2024, 6, 1))
        undated = _release(arc_tool, version="dev")
        db_session.add_all([old, new, undated])
        db_session.flush()

        resp = client.get(f"/api/v1/workflow-tools/{arc_tool.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == arc_tool.id
        assert body["name"] == "ARC"
        assert body["description"] == "Automated Rate Calculator"

        versions = [r["version"] for r in body["releases"]]
        # newest release_date first, NULLS LAST, then id DESC
        assert versions[0] == "2.0"
        assert versions[1] == "1.0"
        assert versions[2] == "dev"

    def test_not_found(self, client):
        resp = client.get("/api/v1/workflow-tools/999999")
        assert resp.status_code == 404

    def test_tool_with_no_releases(self, client, arc_tool):
        resp = client.get(f"/api/v1/workflow-tools/{arc_tool.id}")
        assert resp.status_code == 200
        assert resp.json()["releases"] == []


# ---------------------------------------------------------------------------
# GET /workflow-tool-releases
# ---------------------------------------------------------------------------


class TestListWorkflowToolReleases:
    def test_list_empty(self, client):
        resp = client.get("/api/v1/workflow-tool-releases")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["items"] == []

    def test_list_includes_parent_summary(
        self, client, db_session, arc_tool
    ):
        db_session.add(_release(arc_tool, version="1.0"))
        db_session.flush()

        resp = client.get("/api/v1/workflow-tool-releases")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert item["workflow_tool_id"] == arc_tool.id
        assert item["workflow_tool"] == {
            "id": arc_tool.id,
            "name": "ARC",
            "description": "Automated Rate Calculator",
        }

    def test_filter_by_workflow_tool_id(
        self, client, db_session, arc_tool, tandem_tool
    ):
        db_session.add_all(
            [
                _release(arc_tool, version="1.0"),
                _release(tandem_tool, version="1.0"),
            ]
        )
        db_session.flush()

        resp = client.get(
            "/api/v1/workflow-tool-releases",
            params={"workflow_tool_id": arc_tool.id},
        )
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["workflow_tool_id"] == arc_tool.id

    def test_filter_by_tool_name_partial_ci(
        self, client, db_session, arc_tool, tandem_tool
    ):
        db_session.add_all(
            [
                _release(arc_tool, version="1.0"),
                _release(tandem_tool, version="1.0"),
            ]
        )
        db_session.flush()

        resp = client.get(
            "/api/v1/workflow-tool-releases", params={"tool_name": "tand"}
        )
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["workflow_tool"]["name"] == "Tandem"

    def test_filter_by_version_partial_ci(
        self, client, db_session, arc_tool
    ):
        db_session.add_all(
            [
                _release(arc_tool, version="1.0.0"),
                _release(arc_tool, version="2.1.0"),
                _release(arc_tool, version="dev-nightly"),
            ]
        )
        db_session.flush()

        resp = client.get(
            "/api/v1/workflow-tool-releases", params={"version": "1.0"}
        )
        body = resp.json()
        # matches "1.0.0" and "2.1.0" both contain "1.0"
        assert body["total"] == 2

        resp = client.get(
            "/api/v1/workflow-tool-releases", params={"version": "NIGHT"}
        )
        assert resp.json()["total"] == 1

    def test_filter_by_git_commit_exact(
        self, client, db_session, arc_tool
    ):
        commit_a = "a" * 40
        commit_b = "b" * 40
        db_session.add_all(
            [
                _release(arc_tool, version="1.0", git_commit=commit_a),
                _release(arc_tool, version="2.0", git_commit=commit_b),
            ]
        )
        db_session.flush()

        resp = client.get(
            "/api/v1/workflow-tool-releases",
            params={"git_commit": commit_a},
        )
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["git_commit"] == commit_a

    def test_list_deterministic_ordering(
        self, client, db_session, arc_tool
    ):
        old = _release(arc_tool, version="1.0", release_date=date(2022, 3, 1))
        new = _release(arc_tool, version="3.0", release_date=date(2025, 4, 1))
        mid = _release(arc_tool, version="2.0", release_date=date(2023, 8, 1))
        undated = _release(arc_tool, version="dev")
        db_session.add_all([old, new, mid, undated])
        db_session.flush()

        resp = client.get("/api/v1/workflow-tool-releases")
        versions = [item["version"] for item in resp.json()["items"]]
        # newest release_date first, NULLs last
        assert versions == ["3.0", "2.0", "1.0", "dev"]


# ---------------------------------------------------------------------------
# GET /workflow-tool-releases/{id}
# ---------------------------------------------------------------------------


class TestGetWorkflowToolRelease:
    def test_returns_release_with_parent_summary(
        self, client, db_session, arc_tool
    ):
        release = _release(
            arc_tool,
            version="1.2.3",
            git_commit="c" * 40,
            release_date=date(2024, 1, 15),
            notes="patch release",
        )
        db_session.add(release)
        db_session.flush()

        resp = client.get(f"/api/v1/workflow-tool-releases/{release.id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == release.id
        assert body["version"] == "1.2.3"
        assert body["git_commit"] == "c" * 40
        assert body["release_date"] == "2024-01-15"
        assert body["notes"] == "patch release"
        assert body["workflow_tool"] == {
            "id": arc_tool.id,
            "name": "ARC",
            "description": "Automated Rate Calculator",
        }

    def test_not_found(self, client):
        resp = client.get("/api/v1/workflow-tool-releases/999999")
        assert resp.status_code == 404
