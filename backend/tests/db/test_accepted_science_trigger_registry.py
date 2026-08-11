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

#: ``a1f6c3e9b527`` extends it again, to the five evidence tables that carry an
#: ownership foreign key to an accepted root and had no guard — the IRC
#: evidence backing the same claim ``b6c1f4a8e703``'s atom maps elaborate, the
#: barriers and state energies a network solve ran with, and the interpretation
#: and tunneling rows a rate constant was computed under. Checked here on the
#: same terms.
_EVIDENCE_REVISION = _VERSIONS / "a1f6c3e9b527_freeze_evidence_under_accepted_roots.py"

#: The revisions that extend ``c6f2a9d4e7b1``'s regime by adding guards under
#: their own ``_trigger_name``. Both halves of this test iterate this list, so
#: a fourth such revision is wired in by adding it here once.
_EXTENSION_REVISIONS = (_ATOM_MAP_REVISION, _EVIDENCE_REVISION)


def _revision_namespace() -> dict:
    return runpy.run_path(str(_REVISION))


def _extension_namespaces() -> list[dict]:
    return [runpy.run_path(str(path)) for path in _EXTENSION_REVISIONS]


def _referenced_tables(tables, table: str, column: str, *, hops: int) -> set[str]:
    """Tables a column's value identifies, following foreign keys ``hops`` deep."""

    reached: set[str] = set()
    frontier = {(table, column)}
    for _ in range(hops):
        next_frontier: set[tuple[str, str]] = set()
        for current_table, current_column in frontier:
            for foreign_key in tables[current_table].c[current_column].foreign_keys:
                referent = foreign_key.column
                reached.add(referent.table.name)
                next_frontier.add((referent.table.name, referent.name))
        frontier = next_frontier
    return reached


def test_registry_references_real_metadata_and_short_identifiers() -> None:
    revision = _revision_namespace()
    extensions = _extension_namespaces()
    tables = Base.metadata.tables
    root_types = revision["_ROOT_TYPES"]

    direct_children = revision["_DIRECT_CHILDREN"]
    via_children = revision["_VIA_CHILDREN"]
    for extension in extensions:
        direct_children = direct_children + extension["_DIRECT_CHILDREN"]
        via_children = via_children + extension["_VIA_CHILDREN"]

    for table in root_types.values():
        assert table in tables
        assert "id" in tables[table].c
    for table, _, column in direct_children:
        assert table in tables
        assert column in tables[table].c
    for table, _, child_column, parent, parent_pk, root_column in via_children:
        assert table in tables
        assert child_column in tables[table].c
        assert parent in tables
        assert parent_pk in tables[parent].c
        assert root_column in tables[parent].c

    # A guard may only name a record type ``tckdb_lock_scientific_record``
    # knows how to lock. One that does not raises 22023 at runtime rather than
    # protecting anything, and the failure would only surface the first time a
    # guarded row was written.
    for extension in extensions:
        for _, record_type, *_rest in extension["_DIRECT_CHILDREN"] + extension["_VIA_CHILDREN"]:
            assert record_type in root_types
        for table in extension["_TRUNCATE_TABLES"]:
            assert table in tables

    # Every guarded column must actually hold an id of the root table it
    # claims. Without this, a guard naming a column of the wrong table would
    # fail only at runtime, on the first write it was supposed to refuse.
    #
    # One hop is allowed because ``calc_scan_point_coordinate_value`` reaches
    # ``calculation`` through the composite foreign keys into
    # ``calc_scan_point`` and ``calc_scan_coordinate`` rather than declaring a
    # second single-column one of its own. The column is still a calculation
    # id; only the declaration is indirect.
    for table, record_type, column in direct_children:
        target = root_types[record_type]
        assert target in _referenced_tables(tables, table, column, hops=2), (table, column, target)

    # ``tckdb_guard_accepted_child`` reads each column it is given and does
    # nothing where the value is NULL, so a group whose columns are *all*
    # nullable yields a trigger that skips whole rows. At least one column per
    # ``(table, record_type)`` group must therefore be NOT NULL. It is asserted
    # per group rather than per column because a nullable column can be a
    # deliberate second binding beside a NOT NULL one --
    # ``calc_scf_stability`` is guarded on both its own ``calculation_id`` and
    # the optional ``source_calculation_id`` it was derived from, and both
    # land in one trigger.
    grouped_columns: dict[tuple[str, str], list[str]] = {}
    for table, record_type, column in direct_children:
        grouped_columns.setdefault((table, record_type), []).append(column)
    for (table, record_type), columns in grouped_columns.items():
        assert any(not tables[table].c[column].nullable for column in columns), (table, record_type, columns)

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
            # ``e2c9a4f7b163``'s recorded repair path. The declaration and its
            # change record are append-only and un-truncatable on the same
            # terms as the review and supersession history, and each is
            # validated on insert. Listed here so removing one of the four
            # fails this test as well as the behavioural ones.
            (
                "accepted_science_repair",
                "trg_accepted_science_repair_validate",
            ),
            (
                "accepted_science_repair_change",
                "trg_accepted_science_repair_change_validate",
            ),
            ("accepted_science_repair", "trg_append_only_accepted_science_repair"),
            (
                "accepted_science_repair_change",
                "trg_append_only_accepted_science_repair_change",
            ),
            ("accepted_science_repair", "trg_as_truncate_accepted_science_repair"),
            (
                "accepted_science_repair_change",
                "trg_as_truncate_accepted_science_repair_change",
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

    for extension in _extension_namespaces():
        extension_trigger_name = extension["_trigger_name"]
        expected.update(
            (table, extension_trigger_name("as_child", table)) for table, _, _ in extension["_child_groups"]()
        )
        expected.update((item[0], extension_trigger_name("as_via", item[0])) for item in extension["_VIA_CHILDREN"])
        expected.update((table, extension_trigger_name("as_truncate", table)) for table in extension["_TRUNCATE_TABLES"])

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
                      OR trigger.tgname LIKE 'trg_accepted_science_repair%'
                  )
                """
            )
        )
    }
    assert actual == expected
    assert all(len(name) <= 63 for _, name in actual)
