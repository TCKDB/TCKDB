"""Parity checks for the accepted-science trigger registry."""

from __future__ import annotations

import ast
import re
import runpy
from collections import defaultdict
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

#: ``d4e9b1c7a253`` narrows the regime instead of extending it: it removes one
#: registered column from ``c6f2a9d4e7b1``'s ``calc_scf_stability`` guard. It
#: is read separately from ``_EXTENSION_REVISIONS`` because it adds no trigger
#: -- it rewrites an existing one's arguments under the same name -- so it
#: subtracts from the registry the assertions below reason about rather than
#: adding to it. A second narrowing revision joins by being added here.
_CORRECTION_REVISIONS = (_VERSIONS / "d4e9b1c7a253_scf_stability_provenance_is_not_ownership.py",)


def _revision_namespace() -> dict:
    return runpy.run_path(str(_REVISION))


def _extension_namespaces() -> list[dict]:
    return [runpy.run_path(str(path)) for path in _EXTENSION_REVISIONS]


def _removed_children() -> set[tuple[str, str, str]]:
    """``(table, record_type, column)`` entries the corrections took back out."""

    removed: set[tuple[str, str, str]] = set()
    for path in _CORRECTION_REVISIONS:
        removed.update(runpy.run_path(str(path))["_REMOVED_CHILDREN"])
    return removed


#: A ``CREATE TRIGGER`` / ``CREATE OR REPLACE TRIGGER`` site and the name it
#: mints. The name is frequently an f-string placeholder rather than a literal,
#: which is the whole reason this scan resolves module constants below.
_TRIGGER_SITE = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?TRIGGER\s+(\S+)", re.IGNORECASE)

#: ``{_TRIGGER}`` — a whole name supplied by one module-level constant.
_PLACEHOLDER = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")

#: ``trg_as_child_{index:02d}`` — a name minted from a positional sequence.
_POSITIONAL_FAMILY = re.compile(r"^(trg_as_(?:child|via|truncate))_\{[a-z_]*index")

#: ``trg_as_child_19`` — a single member of such a sequence, named outright.
_POSITIONAL_MEMBER = re.compile(r"^(trg_as_(?:child|via|truncate))_\d{2}$")

_LITERAL_NAME = re.compile(r"^trg_[a-z0-9_]+$")

#: The revision that owns the positional sequences. Named once so the assertion
#: below reads as the convention it enforces rather than as a filename match.
_POSITIONAL_OWNER = _REVISION.name

#: Trigger names deliberately minted by more than one revision, each mapped to
#: the exact set of revisions allowed to mint it. Anything else that appears
#: twice is the defect this test exists to catch: a second revision silently
#: taking over a name an earlier one already installed, which PostgreSQL
#: accepts without complaint when the second drops before it creates.
#:
#: Both entries are checked for staleness as well as for breadth — a name
#: listed here that is no longer minted twice fails, so an allowance cannot
#: outlive the reuse that justified it.
_DECLARED_REUSE: dict[str, frozenset[str]] = {
    # ``d4e9b1c7a253`` narrows this guard to the ownership column alone. It
    # reuses ``c6f2a9d4e7b1``'s name on purpose, and says why in its docstring:
    # minting a new one would make the positional sequence depend on two files
    # at once. This is the reuse that motivated the whole check.
    "trg_as_child_19": frozenset(
        {
            _POSITIONAL_OWNER,
            "d4e9b1c7a253_scf_stability_provenance_is_not_ownership.py",
        }
    ),
    # ``6a9d2e4c7b1f`` drops this trigger for the duration of a public-ref
    # backfill and recreates it identically before the upgrade finishes. The
    # name is reused because it is the same guard, restored.
    "trg_repro_assessment_append_only": frozenset(
        {
            "b4e8c1f6a2d9_add_reproducibility_assessments.py",
            "6a9d2e4c7b1f_add_repro_assessment_public_ref.py",
        }
    ),
}


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-level ``NAME = "..."`` bindings, annotated or not."""

    constants: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            constants[node.target.id] = node.value.value
    return constants


def _minted_trigger_names() -> tuple[dict[str, set[str]], dict[str, set[str]], int, int]:
    """Scan every revision for the trigger names it creates.

    Returns ``(names, families, sites, files)`` where ``names`` maps a resolved
    trigger name to the revision filenames that create it, and ``families``
    maps a positional prefix such as ``trg_as_child`` to the revisions that
    mint names from its sequence.

    Names are resolved from string literals and from module-level string
    constants. They are deliberately *not* resolved by executing the revision:
    a name built inside a loop from table names cannot be recovered statically,
    and those are exactly the names the table-suffixed convention makes safe
    anyway. ``sites`` and ``files`` are returned so the caller can prove the
    scan found the corpus before asserting anything about it.
    """

    names: dict[str, set[str]] = defaultdict(set)
    families: dict[str, set[str]] = defaultdict(set)
    sites = 0
    files = 0

    for path in sorted(_VERSIONS.glob("*.py")):
        source = path.read_text()
        targets = _TRIGGER_SITE.findall(source)
        if not targets:
            continue
        files += 1
        sites += len(targets)
        constants = _module_string_constants(ast.parse(source))
        for target in targets:
            if _LITERAL_NAME.fullmatch(target):
                names[target].add(path.name)
                continue
            placeholder = _PLACEHOLDER.match(target)
            if placeholder and placeholder.group(1) in constants:
                resolved = constants[placeholder.group(1)]
                if _LITERAL_NAME.fullmatch(resolved):
                    names[resolved].add(path.name)
                continue
            family = _POSITIONAL_FAMILY.match(target)
            if family:
                families[family.group(1)].add(path.name)

    # A revision that mints a whole positional sequence mints every member of
    # it, but never spells one: ``c6f2a9d4e7b1`` writes
    # ``trg_as_child_{index:02d}``, so the string ``trg_as_child_19`` appears
    # in that file only inside a comment. Crediting the sequence's owner with
    # each member found elsewhere is what lets the reuse show up as a reuse.
    # Without this the scan sees ``trg_as_child_19`` claimed by one revision
    # and reports no collision at all — the vacuous answer.
    for name in list(names):
        member = _POSITIONAL_MEMBER.fullmatch(name)
        if member:
            names[name].update(families.get(member.group(1), set()))

    return dict(names), dict(families), sites, files


def test_trigger_names_have_exactly_one_minting_revision() -> None:
    """No revision may take over a trigger name another revision installed.

    ``c6f2a9d4e7b1`` names its accepted-science child guards
    ``trg_as_child_NN``, where ``NN`` is the position of a ``(table,
    record_type)`` group in its own registry. ``b6c1f4a8e703`` and
    ``a1f6c3e9b527`` both decline to extend that sequence and say why in their
    ``_trigger_name`` docstrings: appending to it from a second file would make
    each revision's numbering depend on the other's registry. Until now that
    convention was written down in three places and enforced in none.

    PostgreSQL only refuses the collision when the second revision uses a plain
    ``CREATE TRIGGER`` against a name already present *on the same table*. It
    accepts a drop-then-create, which is how a guard gets silently replaced
    with one that enforces something else, and it accepts the same name on a
    different table outright. This test refuses the reuse at the source, before
    either migration runs, so the answer does not depend on which of those
    three paths a future revision happens to take.
    """

    names, families, sites, files = _minted_trigger_names()

    # Prove the scan found the corpus before asserting anything about it. A
    # regex that silently stopped matching would otherwise turn every
    # assertion below into a pass over an empty set. Floors sit below the
    # measured 27 sites across 11 files minting 11 statically resolvable
    # names, so ordinary growth does not touch them.
    assert sites >= 20, sites
    assert files >= 9, files
    assert len(names) >= 9, sorted(names)

    # The positional sequences belong to one revision each, and that revision
    # is ``c6f2a9d4e7b1``. This is the convention stated in the two
    # ``_trigger_name`` docstrings, asserted.
    assert families == {
        "trg_as_child": {_POSITIONAL_OWNER},
        "trg_as_via": {_POSITIONAL_OWNER},
        "trg_as_truncate": {_POSITIONAL_OWNER},
    }, families

    # A name minted by two revisions must be one of the declared reuses, and a
    # declared reuse must still be minted by exactly the revisions it names.
    # The second half is what keeps this list from outliving its reasons.
    reused = {name: owners for name, owners in names.items() if len(owners) > 1}
    assert reused == {name: set(owners) for name, owners in _DECLARED_REUSE.items()}, reused

    # A member of a positional sequence spelled out in some other revision is
    # the specific hazard: the number has to be counted out of
    # ``c6f2a9d4e7b1``'s registry by hand, and nothing re-counts it if that
    # registry ever moves. Allowed only where declared.
    for name, owners in names.items():
        if not _POSITIONAL_MEMBER.fullmatch(name):
            continue
        outsiders = owners - {_POSITIONAL_OWNER}
        assert not outsiders or name in _DECLARED_REUSE, (name, sorted(outsiders))


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

    removed = _removed_children()
    direct_children = tuple(entry for entry in revision["_DIRECT_CHILDREN"] if entry not in removed)
    via_children = revision["_VIA_CHILDREN"]
    for extension in extensions:
        direct_children = direct_children + extension["_DIRECT_CHILDREN"]
        via_children = via_children + extension["_VIA_CHILDREN"]

    # A correction may only take back out something that was in. An entry that
    # matches nothing is a typo that would silently narrow no guard while
    # reading as though it had.
    assert removed <= set(revision["_DIRECT_CHILDREN"])

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

    # Every guarded column must be NOT NULL, and the rule is now stated that
    # way rather than per ``(table, record_type)`` group.
    #
    # This comment previously recorded the opposite. It read: a nullable
    # column can be a deliberate second binding beside a NOT NULL one --
    # ``calc_scf_stability`` is guarded on both its own ``calculation_id`` and
    # the optional ``source_calculation_id`` it was derived from -- and so the
    # NOT NULL requirement was asserted per group, which the pair satisfied
    # through ``calculation_id`` alone. That reading was wrong, and writing it
    # down here is what kept it from being questioned for three revisions.
    #
    # ``source_calculation_id`` is not a second way of naming the row's owner.
    # It names a *different* calculation: the one the stability analysis was
    # read out of. Because ``tckdb_guard_accepted_child`` treats every argument
    # column alike, registering it meant an unapproved calculation could not
    # record SCF-stability evidence citing an approved one, while the identical
    # row citing nothing was accepted -- the database refusing provenance that
    # points at accepted science. ``c6f2a9d4e7b1`` had excluded cross-domain
    # provenance FKs from the regime in the sentence directly above its own
    # registry, and ``a1f6c3e9b527`` excluded the identically shaped
    # ``network_solve_state_energy.source_calculation_id`` by name;
    # ``d4e9b1c7a253`` removes the entry, and
    # ``tests/db/test_scf_stability_provenance_guard.py`` holds the behaviour
    # that a registry-derived assertion cannot.
    #
    # Nullability now carries the distinction the prose used to: an ownership
    # FK is the column without which the row has no subject, so it is NOT
    # NULL, and a guarded column that is nullable is a provenance pointer
    # miscategorised. Asserted per column, so a repeat needs this comment
    # rewritten again rather than merely being absorbed by a group.
    for table, record_type, column in direct_children:
        assert not tables[table].c[column].nullable, (table, record_type, column)

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


def _corrected_direct_groups(
    revision: dict, removed: set[tuple[str, str, str]]
) -> list[tuple[str, str, tuple[str, ...]]]:
    """``c6f2a9d4e7b1``'s child groups with the corrections' entries taken out.

    The grouping — and therefore every ``trg_as_child_NN`` index — is computed
    from the *original* registry, because a correction that narrows a group's
    argument list must not renumber the triggers either side of it. Only the
    columns inside a group shrink.
    """

    grouped: dict[tuple[str, str], list[str]] = {}
    for entry in revision["_DIRECT_CHILDREN"]:
        table, record_type, column = entry
        columns = grouped.setdefault((table, record_type), [])
        if entry not in removed:
            columns.append(column)
    return [(table, record_type, tuple(columns)) for (table, record_type), columns in grouped.items()]


def _guard_call(function: str, arguments: tuple[str, ...]) -> str:
    return f"{function}({', '.join(repr(argument) for argument in arguments)})"


def test_database_trigger_definitions_match_registry(db_session) -> None:
    """Pin what each child guard *enforces*, not merely that it exists.

    ``test_database_trigger_set_matches_registry`` compares ``(table, name)``
    pairs. That catches a guard added or removed, and it catches a name
    appearing on a table it does not belong to. It cannot catch a guard being
    replaced by a different one under the same name on the same table, because
    the pair is unchanged — and a drop-then-create is precisely how
    ``d4e9b1c7a253`` legitimately rewrote ``trg_as_child_19``'s argument list.
    Run that way by mistake, or by a revision that miscounts the positional
    sequence and lands on an occupied number, the registry and the database
    disagree about which columns are guarded and every existing test passes.

    The argument list is the guard. ``tckdb_guard_accepted_child`` treats each
    argument as a column that binds the row to an accepted root, so adding one
    refuses writes that should be allowed and dropping one allows writes that
    should be refused. Both are silent until a contributor hits them.
    """

    revision = _revision_namespace()
    removed = _removed_children()

    expected: dict[tuple[str, str], str] = {}
    for index, (table, record_type, columns) in enumerate(_corrected_direct_groups(revision, removed)):
        # A group emptied by a correction would mean the trigger should have
        # been dropped rather than narrowed; it would also render an empty
        # argument list, which the guard function cannot act on.
        assert columns, (table, record_type)
        expected[(table, f"trg_as_child_{index:02d}")] = _guard_call(
            "tckdb_guard_accepted_child", (record_type, *columns)
        )
    for index, item in enumerate(revision["_VIA_CHILDREN"]):
        table, record_type, child_column, parent_table, parent_pk, root_column = item
        expected[(table, f"trg_as_via_{index:02d}")] = _guard_call(
            "tckdb_guard_accepted_via_child",
            (record_type, child_column, parent_table, parent_pk, root_column),
        )

    for extension in _extension_namespaces():
        trigger_name = extension["_trigger_name"]
        for table, record_type, columns in extension["_child_groups"]():
            assert columns, (table, record_type)
            expected[(table, trigger_name("as_child", table))] = _guard_call(
                "tckdb_guard_accepted_child", (record_type, *columns)
            )
        for item in extension["_VIA_CHILDREN"]:
            table, record_type, child_column, parent_table, parent_pk, root_column = item
            expected[(table, trigger_name("as_via", table))] = _guard_call(
                "tckdb_guard_accepted_via_child",
                (record_type, child_column, parent_table, parent_pk, root_column),
            )

    actual = {
        (row.table_name, row.trigger_name): re.sub(
            r"\s+", " ", row.definition.split("EXECUTE FUNCTION ", 1)[1]
        ).strip()
        for row in db_session.execute(
            text(
                """
                SELECT relation.relname AS table_name,
                       trigger.tgname AS trigger_name,
                       pg_get_triggerdef(trigger.oid) AS definition
                FROM pg_trigger AS trigger
                JOIN pg_class AS relation ON relation.oid = trigger.tgrelid
                JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                WHERE NOT trigger.tgisinternal
                  AND namespace.nspname = 'public'
                  AND (
                      trigger.tgname LIKE 'trg_as_child_%'
                      OR trigger.tgname LIKE 'trg_as_via_%'
                  )
                """
            )
        )
    }

    # Prove the query found the guards before comparing what they say. A
    # predicate that stopped matching would make the comparison below a pass
    # over two empty dicts. The measured population is 61 child and via
    # guards; the floor sits under it so the registry can grow without
    # touching this number.
    assert len(actual) >= 55, len(actual)
    assert len(expected) >= 55, len(expected)

    assert actual == expected
