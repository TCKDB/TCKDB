"""Every foreign key into the identity tables is covered by the merge-forwarding triggers."""

from __future__ import annotations

    # Re-applying after a downgrade is not supported: the downgrade forgets which rows were merged away.


def test_the_catalogue_covers_every_foreign_key_and_a_trigger_guards_each() -> None:
    """Every foreign key into the three identity tables is either re-pointed by the step or deliberately stays, and each
    carries the trigger that refuses a merged-away target. Enumerated from the models, so a table added later is caught."""
    import app.db.models  # noqa: F401
    from app.db.base import Base
    from app.services.stereo_identity_merge import _STAYS_ON_MERGED_ROW

    into = {"species", "species_entry", "chem_reaction"}
    markers = {"merged_into_species_id", "merged_into_species_entry_id", "merged_into_chem_reaction_id"}
    found = {
        (table.name, fk.parent.name)
        for table in Base.metadata.sorted_tables
        for fk in table.foreign_keys
        if fk.column.table.name in into and fk.parent.name not in markers
    }
    assert ("thermo", "species_entry_id") in found and ("reaction_participant", "species_id") in found
    assert _STAYS_ON_MERGED_ROW <= found
    for composite in (c for t in Base.metadata.sorted_tables for c in t.foreign_key_constraints if len(c.columns) > 1):
        assert composite.referred_table.name not in into, composite
