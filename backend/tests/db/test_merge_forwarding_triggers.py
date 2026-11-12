"""Every foreign key into the identity tables carries the trigger that refuses a merged-away target."""

from __future__ import annotations

import re

from sqlalchemy import text

from app.db import models as _models  # noqa: F401
from app.db.base import Base

_INTO = {"species", "species_entry", "chem_reaction"}
_MARKERS = {"merged_into_species_id", "merged_into_species_entry_id", "merged_into_chem_reaction_id"}


def _guarded(db_session) -> set[tuple[str, str]]:
    rows = db_session.execute(
        text(
            "SELECT c.relname, pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_proc p ON p.oid = t.tgfoid WHERE NOT t.tgisinternal AND p.proname = 'tckdb_refuse_merged_target'"
        )
    ).all()
    guarded = set()
    for table, definition in rows:
        target, column, _marker = re.findall(r"'([^']*)'", definition[definition.rindex("(") :])
        assert target in _INTO, definition
        guarded.add((table, column))
    return guarded


def test_every_foreign_key_into_the_identity_tables_has_a_merged_target_trigger(db_session) -> None:
    found = {
        (table.name, fk.parent.name)
        for table in Base.metadata.sorted_tables
        for fk in table.foreign_keys
        if fk.column.table.name in _INTO and fk.parent.name not in _MARKERS
    }
    assert ("thermo", "species_entry_id") in found and ("reaction_participant", "species_id") in found
    missing = found - _guarded(db_session)
    assert not missing, f"foreign keys into an identity table with no merged-target trigger: {sorted(missing)}"


def test_no_composite_foreign_key_references_an_identity_table() -> None:
    for table in Base.metadata.sorted_tables:
        for constraint in table.foreign_key_constraints:
            if len(constraint.columns) > 1:
                assert constraint.referred_table.name not in _INTO, constraint
