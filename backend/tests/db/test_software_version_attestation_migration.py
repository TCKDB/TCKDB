"""Disposable-database round trip for ``86ffcd9d3c65`` (issue #305, decision (a)).

Upgrade to the revision's parent, then upgrade -> downgrade -> upgrade on a
scratch ``tckdb_test*`` database, checking at each step that the two
attestation tables, their enum, their validation function and their
append-only triggers are all present or all gone. Rows written between the
steps prove the upgraded schema is usable, not merely present.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.db._migration_chain import revision_under_test
from tests.db.test_merge_composite_gaussian_release_migration import _MigrationHarness

_MIGRATION = revision_under_test("86ffcd9d3c65")

_TABLES = ("software_version_attestation", "software_version_attestation_calculation")
_TRIGGERS = {
    ("software_version_attestation", "trg_sva_validate"),
    ("software_version_attestation", "trg_sva_append_only"),
    ("software_version_attestation", "trg_sva_no_truncate"),
    ("software_version_attestation_calculation", "trg_sva_calculation_append_only"),
    ("software_version_attestation_calculation", "trg_sva_calculation_no_truncate"),
    ("software_version_attestation_calculation", "trg_sva_calculation_validate"),
}


@pytest.fixture
def harness():
    created = _MigrationHarness("sva_round_trip")
    yield created
    created.close()


def _state(harness) -> dict:
    with harness.engine.connect() as conn:
        tables = set(
            conn.scalars(
                text("SELECT tablename FROM pg_tables WHERE tablename = ANY(:names)"),
                {"names": list(_TABLES)},
            )
        )
        triggers = {
            (row.table_name, row.trigger_name)
            for row in conn.execute(
                text(
                    """
                    SELECT relation.relname AS table_name, trigger.tgname AS trigger_name
                    FROM pg_trigger AS trigger
                    JOIN pg_class AS relation ON relation.oid = trigger.tgrelid
                    WHERE NOT trigger.tgisinternal AND trigger.tgname LIKE 'trg_sva_%'
                    """
                )
            )
        }
        enum = conn.scalar(
            text("SELECT count(*) FROM pg_type WHERE typname = 'software_version_evidence_kind'")
        )
        function = conn.scalar(
            text(
                "SELECT count(*) FROM pg_proc "
                "WHERE proname IN ('tckdb_validate_sva', 'tckdb_validate_sva_calculation')"
            )
        )
    return {"tables": tables, "triggers": triggers, "enum": enum, "function": function}


_ABSENT = {"tables": set(), "triggers": set(), "enum": 0, "function": 0}
_PRESENT = {"tables": set(_TABLES), "triggers": _TRIGGERS, "enum": 1, "function": 2}


def _seed_and_attest(harness) -> None:
    """One owner, an ORCA NULL release, a calculation re-pointed to ORCA 6."""
    with harness.engine.begin() as conn:
        user = conn.scalar(text("INSERT INTO app_user (username) VALUES ('sva-owner') RETURNING id"))
        software = conn.scalar(text("INSERT INTO software (name) VALUES ('ORCA') RETURNING id"))
        null_release = conn.scalar(
            text("INSERT INTO software_release (software_id) VALUES (:s) RETURNING id"), {"s": software}
        )
        orca_6 = conn.scalar(
            text("INSERT INTO software_release (software_id, version) VALUES (:s, '6') RETURNING id"),
            {"s": software},
        )
        species = conn.scalar(
            text(
                "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
                "VALUES ('molecule', '[H]', 'YZCKVEUIGOORGS-UHFFFAOYSA-N', 0, 2, 'unspecified') RETURNING id"
            )
        )
        entry = conn.scalar(
            text("INSERT INTO species_entry (species_id) VALUES (:s) RETURNING id"), {"s": species}
        )
        calc = conn.scalar(
            text(
                "INSERT INTO calculation (type, species_entry_id, software_release_id, created_by) "
                "VALUES ('sp', :e, :r, :u) RETURNING id"
            ),
            {"e": entry, "r": orca_6, "u": user},
        )
        attestation = conn.scalar(
            text(
                "INSERT INTO software_version_attestation "
                "(software_release_id, attested_version, statement, evidence_kind, attested_by, "
                "covers_depositor, attested_at) "
                "VALUES (:r, '6', 'ORCA 6 for my runs', 'owner_attestation', :u, :u, '2026-09-12') "
                "RETURNING id"
            ),
            {"r": null_release, "u": user},
        )
        conn.execute(
            text(
                "INSERT INTO software_version_attestation_calculation "
                "(attestation_id, calculation_id, before_software_release_id, after_software_release_id) "
                "VALUES (:a, :c, :b, :after)"
            ),
            {"a": attestation, "c": calc, "b": null_release, "after": orca_6},
        )


def test_upgrade_downgrade_upgrade_round_trip(harness) -> None:
    harness.run("upgrade", _MIGRATION.parent)
    assert _state(harness) == _ABSENT

    harness.run("upgrade", _MIGRATION.revision)
    assert _state(harness) == _PRESENT
    _seed_and_attest(harness)
    with harness.engine.connect() as conn:
        with pytest.raises(DBAPIError) as excinfo:
            conn.execute(text("DELETE FROM software_version_attestation_calculation"))
        assert excinfo.value.orig.sqlstate == "55000"

    # Downgrade drops populated append-only tables cleanly (a DROP is DDL,
    # which the row triggers do not see).
    harness.run("downgrade", _MIGRATION.parent)
    assert _state(harness) == _ABSENT

    harness.run("upgrade", _MIGRATION.revision)
    assert _state(harness) == _PRESENT
    with harness.engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM software_version_attestation")) == 0
