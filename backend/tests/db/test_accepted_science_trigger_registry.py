"""Parity checks for the accepted-science trigger registry."""

from __future__ import annotations

import runpy
from pathlib import Path

from sqlalchemy import text

from app.db import models as _models  # noqa: F401
from app.db.base import Base

_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"

_REVISION = _VERSIONS / "c6f2a9d4e7b1_enforce_accepted_science_immutability.py"

#: ``b6c1f4a8e703`` extends the same regime to the atom-map tables, which
#: ``d3a7f1c9b284`` added after ``c6f2a9d4e7b1`` shipped and did not join. Its
#: registry is checked here too, and its triggers are part of the expected set
#: below: this test asserts set *equality* against ``pg_trigger``, so a guard
#: added anywhere without being declared here fails, and one declared here
#: without being created fails as well.
_ATOM_MAP_REVISION = _VERSIONS / "b6c1f4a8e703_freeze_declared_atom_maps.py"


def _revision_namespace() -> dict:
    return runpy.run_path(str(_REVISION))


def _atom_map_revision_namespace() -> dict:
    return runpy.run_path(str(_ATOM_MAP_REVISION))


def test_registry_references_real_metadata_and_short_identifiers() -> None:
    revision = _revision_namespace()
    atom_map_revision = _atom_map_revision_namespace()
    tables = Base.metadata.tables
    root_types = revision["_ROOT_TYPES"]

    for table in root_types.values():
        assert table in tables
        assert "id" in tables[table].c
    for table, _, column in revision["_DIRECT_CHILDREN"] + atom_map_revision["_DIRECT_CHILDREN"]:
        assert table in tables
        assert column in tables[table].c
    for table, _, child_column, parent, parent_pk, root_column in (
        revision["_VIA_CHILDREN"] + atom_map_revision["_VIA_CHILDREN"]
    ):
        assert table in tables
        assert child_column in tables[table].c
        assert parent in tables
        assert parent_pk in tables[parent].c
        assert root_column in tables[parent].c

    # A guard may only name a record type ``tckdb_lock_scientific_record``
    # knows how to lock. One that does not raises 22023 at runtime rather than
    # protecting anything, and the failure would only surface the first time a
    # guarded row was written.
    for _, record_type, *_rest in atom_map_revision["_DIRECT_CHILDREN"] + atom_map_revision["_VIA_CHILDREN"]:
        assert record_type in root_types
    for table in atom_map_revision["_TRUNCATE_TABLES"]:
        assert table in tables

    constraints = tables["scientific_record_supersession"].constraints
    assert all(constraint.name is None or len(constraint.name) <= 63 for constraint in constraints)


def test_database_trigger_set_matches_registry(db_session) -> None:
    revision = _revision_namespace()
    root_types = revision["_ROOT_TYPES"]
    direct_groups = revision["_direct_child_groups"]()
    via_children = revision["_VIA_CHILDREN"]
    trigger_name = revision["_trigger_name"]

    expected: set[tuple[str, str]] = {("record_review", "trg_guard_record_review")}
    expected.update((table, trigger_name("as_root", table)) for table in root_types.values())
    expected.update((table, f"trg_as_child_{index:02d}") for index, (table, _, _) in enumerate(direct_groups))
    expected.update((item[0], f"trg_as_via_{index:02d}") for index, item in enumerate(via_children))
    expected.update(
        {
            ("geometry", "trg_as_geometry"),
            ("geometry_atom", "trg_as_geometry_atom"),
            (
                "scientific_record_supersession",
                "trg_scientific_supersession_validate",
            ),
            (
                "record_review_event",
                trigger_name("append_only", "record_review_event"),
            ),
            (
                "scientific_record_supersession",
                trigger_name("append_only", "scientific_record_supersession"),
            ),
        }
    )
    truncate_tables = sorted(
        set(root_types.values())
        | {table for table, _, _ in revision["_DIRECT_CHILDREN"]}
        | {item[0] for item in via_children}
        | {
            "geometry",
            "geometry_atom",
            "record_review",
            "record_review_event",
            "record_reproducibility_assessment",
            "scientific_record_supersession",
        }
    )
    expected.update((table, f"trg_as_truncate_{index:02d}") for index, table in enumerate(truncate_tables))

    atom_map_revision = _atom_map_revision_namespace()
    atom_map_trigger_name = atom_map_revision["_trigger_name"]
    expected.update(
        (table, atom_map_trigger_name("as_child", table)) for table, _, _ in atom_map_revision["_child_groups"]()
    )
    expected.update((item[0], atom_map_trigger_name("as_via", item[0])) for item in atom_map_revision["_VIA_CHILDREN"])
    expected.update(
        (table, atom_map_trigger_name("as_truncate", table)) for table in atom_map_revision["_TRUNCATE_TABLES"]
    )

    actual = {
        (row.table_name, row.trigger_name)
        for row in db_session.execute(
            text(
                """
                SELECT relation.relname AS table_name, trigger.tgname AS trigger_name
                FROM pg_trigger AS trigger
                JOIN pg_class AS relation ON relation.oid = trigger.tgrelid
                WHERE NOT trigger.tgisinternal
                  AND (
                      trigger.tgname LIKE 'trg_as_%'
                      OR trigger.tgname LIKE 'trg_append_only_%'
                      OR trigger.tgname = 'trg_guard_record_review'
                      OR trigger.tgname = 'trg_scientific_supersession_validate'
                  )
                """
            )
        )
    }
    assert actual == expected
    assert all(len(name) <= 63 for _, name in actual)
