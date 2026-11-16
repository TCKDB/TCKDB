from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.workflow import WorkflowTool
from app.schemas.fragments.refs import WorkflowToolReleaseRef
from app.services import calculation_resolution
from app.services.calculation_resolution import resolve_workflow_tool_release_ref


def test_case_variants_of_a_workflow_tool_name_resolve_to_one_tool(db_conn) -> None:
    with Session(db_conn) as session:
        with session.begin():
            first = resolve_workflow_tool_release_ref(session, WorkflowToolReleaseRef(name="geomeTRIC", version="1.0"))
            second = resolve_workflow_tool_release_ref(session, WorkflowToolReleaseRef(name="GeomeTRIC", version="1.0"))

            assert first.id == second.id
            assert first.workflow_tool.name == "geomeTRIC"
            count = session.scalar(
                select(func.count()).select_from(WorkflowTool).where(func.lower(WorkflowTool.name) == "geometric")
            )
            assert count == 1


def test_insert_conflict_retry_finds_the_existing_case_variant(db_conn, monkeypatch) -> None:
    real_find = calculation_resolution.find_workflow_tool_by_name
    with Session(db_conn) as session:
        with session.begin():
            existing = resolve_workflow_tool_release_ref(session, WorkflowToolReleaseRef(name="geomeTRIC"))
            calls = {"n": 0}

            def blind_first_lookup(sess, name):
                calls["n"] += 1
                return None if calls["n"] == 1 else real_find(sess, name)

            monkeypatch.setattr(calculation_resolution, "find_workflow_tool_by_name", blind_first_lookup)
            again = resolve_workflow_tool_release_ref(session, WorkflowToolReleaseRef(name="GeomeTRIC"))

            assert calls["n"] == 2
            assert again.workflow_tool_id == existing.workflow_tool_id


def test_insert_conflict_with_no_matching_row_raises_instead_of_returning_none(db_conn, monkeypatch) -> None:
    with Session(db_conn) as session:
        with session.begin():
            resolve_workflow_tool_release_ref(session, WorkflowToolReleaseRef(name="geomeTRIC"))
            monkeypatch.setattr(calculation_resolution, "find_workflow_tool_by_name", lambda sess, name: None)
            with pytest.raises(RuntimeError, match="uniqueness constraint"):
                resolve_workflow_tool_release_ref(session, WorkflowToolReleaseRef(name="GeomeTRIC"))
