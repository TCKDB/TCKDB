"""Round-trip coverage for ``e3a7c1f9b2d4``, the ``frequency_scale_factor``
release-grain migration (correction-scheme-provenance plan v2 §6 -- the
sibling of ``c24ce2d9c198``, which did the same thing for
``energy_correction_scheme``).

``frequency_scale_factor`` is an already-deployed table, so the migration
rules require both ``upgrade()`` and ``downgrade()`` to be implemented
and correct.

Same red-first shape as
``test_energy_correction_scheme_provenance_migration.py``'s
``c24ce2d9c198`` coverage, minus the ``units`` criteria (a scale factor
is dimensionless -- plan §6 explicitly does not add a ``units`` member
here), plus a fixture in the *live* archive's own shape: 10 of the 12
deployed rows share ``(level_of_theory_id, scale_kind, value,
source_literature_id)`` and are distinguished only by
``workflow_tool_release_id`` -- already part of the identity both before
and after this revision -- so they must remain 10 distinct rows across
the upgrade.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from tests.db._migration_chain import revision_under_test

REPO_ROOT = Path(__file__).resolve().parents[2]

_MIGRATION = revision_under_test("e3a7c1f9b2d4")


def _set_db_env(monkeypatch, db_name: str) -> None:
    monkeypatch.setenv("DB_NAME", db_name)
    monkeypatch.setenv("DB_USER", "tckdb")
    monkeypatch.setenv("DB_PASSWORD", "tckdb")
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "5432")


def _engine_for(db_engine):
    url = db_engine.url.render_as_string(hide_password=False)
    db_engine.dispose()
    return create_engine(url)


# ---------------------------------------------------------------------------
# Pre-flight collision check
# ---------------------------------------------------------------------------


def test_preflight_check_aborts_on_software_only_collision(db_engine, monkeypatch):
    """Two factors distinguished today only by ``software_id`` must abort
    the upgrade, naming both offending public refs, rather than let the
    new unique index merge or pick between them.

    *Mutation*: delete ``_refuse_software_only_collisions`` from the
    revision's ``upgrade()`` -- this fixture must then fail with a raw
    ``IntegrityError`` from ``CREATE UNIQUE INDEX`` instead of the
    legible ``RuntimeError`` this check exists to raise instead.
    """
    _set_db_env(monkeypatch, db_engine.url.database)
    engine = _engine_for(db_engine)
    config = Config(str(REPO_ROOT / "alembic.ini"))

    inserted_fsf_ids: list[int] = []
    inserted_lot_ids: list[int] = []
    inserted_software_ids: list[int] = []
    try:
        command.downgrade(config, _MIGRATION.parent)

        with engine.begin() as connection:
            suffix = uuid4().hex[:8]
            inserted_lot_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO level_of_theory (method, lot_hash)
                        VALUES ('fsf-preflight-collision', :h)
                        RETURNING id
                    """),
                    {"h": f"fsfpreflight{suffix}"},
                )
            )
            inserted_software_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO software (name)
                        VALUES ('FSF-Preflight-Gaussian'),
                               ('FSF-Preflight-ORCA')
                        RETURNING id
                    """)
                )
            )
            gaussian_id, orca_id = inserted_software_ids
            inserted_fsf_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO frequency_scale_factor
                            (level_of_theory_id, software_id, scale_kind, value)
                        VALUES
                            (:lot_id, :gaussian_id, 'fundamental', 0.987),
                            (:lot_id, :orca_id, 'fundamental', 0.987)
                        RETURNING id
                    """),
                    {
                        "lot_id": inserted_lot_ids[0],
                        "gaussian_id": gaussian_id,
                        "orca_id": orca_id,
                    },
                )
            )
            refs = {
                row.id: row.public_ref
                for row in connection.execute(
                    text(
                        "SELECT id, public_ref FROM frequency_scale_factor "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": inserted_fsf_ids},
                )
            }

        with pytest.raises(RuntimeError) as excinfo:
            command.upgrade(config, _MIGRATION.revision)
        message = str(excinfo.value)
        for ref in refs.values():
            assert ref in message, f"{ref!r} not named in abort message: {message}"

        with engine.connect() as connection:
            has_release_column = connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'frequency_scale_factor' "
                    "AND column_name = 'software_release_id'"
                )
            )
            assert has_release_column == 0
    finally:
        with engine.begin() as connection:
            if inserted_fsf_ids:
                connection.execute(
                    text(
                        "DELETE FROM frequency_scale_factor WHERE id = ANY(:ids)"
                    ),
                    {"ids": inserted_fsf_ids},
                )
            if inserted_software_ids:
                connection.execute(
                    text("DELETE FROM software WHERE id = ANY(:ids)"),
                    {"ids": inserted_software_ids},
                )
            if inserted_lot_ids:
                connection.execute(
                    text("DELETE FROM level_of_theory WHERE id = ANY(:ids)"),
                    {"ids": inserted_lot_ids},
                )
        engine.dispose()
        command.upgrade(config, "head")


@pytest.mark.parametrize(
    "differing_column",
    [
        "scale_kind",
        "value",
        "level_of_theory_id",
        "source_literature_id",
        "workflow_tool_release_id",
    ],
)
def test_preflight_check_does_not_fire_for_a_legitimate_difference(
    db_engine, monkeypatch, differing_column
):
    """The same shape, differing by one of the five columns the widened
    identity still checks instead of by ``software_id``, must upgrade
    cleanly.

    *Mutation*: delete any column from ``_COLLISION_QUERY``'s ``GROUP
    BY`` -- the case named after that column must then fail with the
    revision's ``RuntimeError``.
    """
    _set_db_env(monkeypatch, db_engine.url.database)
    engine = _engine_for(db_engine)
    config = Config(str(REPO_ROOT / "alembic.ini"))

    inserted_fsf_ids: list[int] = []
    created: dict[str, list[int]] = {}
    try:
        command.downgrade(config, _MIGRATION.parent)

        with engine.begin() as connection:
            suffix = uuid4().hex[:8]
            lot_id = connection.scalar(
                text("""
                    INSERT INTO level_of_theory (method, lot_hash)
                    VALUES ('fsf-preflight-legit', :h) RETURNING id
                """),
                {"h": f"fsflegit{suffix}"},
            )
            created.setdefault("level_of_theory", []).append(lot_id)

            base: dict[str, object] = {
                "level_of_theory_id": lot_id,
                "scale_kind": "fundamental",
                "value": 0.977,
                "source_literature_id": None,
                "workflow_tool_release_id": None,
            }

            def _distinct_value_for(column, baseline):
                if column == "scale_kind":
                    return "zpe"
                if column == "value":
                    return 0.978
                if column == "level_of_theory_id":
                    other_lot_id = connection.scalar(
                        text("""
                            INSERT INTO level_of_theory (method, lot_hash)
                            VALUES ('fsf-preflight-legit-b', :h)
                            RETURNING id
                        """),
                        {"h": f"fsflegitb{suffix}"},
                    )
                    created.setdefault("level_of_theory", []).append(other_lot_id)
                    return other_lot_id
                if column == "source_literature_id":
                    lit_id = connection.scalar(
                        text("""
                            INSERT INTO literature (kind, title)
                            VALUES ('article', :t) RETURNING id
                        """),
                        {"t": f"FSF Preflight Legit Difference {suffix}"},
                    )
                    created.setdefault("literature", []).append(lit_id)
                    return lit_id
                if column == "workflow_tool_release_id":
                    tool_id = connection.scalar(
                        text(
                            "INSERT INTO workflow_tool (name) VALUES (:n) "
                            "RETURNING id"
                        ),
                        {"n": f"FSFPreflightLegitTool{suffix}"},
                    )
                    created.setdefault("workflow_tool", []).append(tool_id)
                    release_id = connection.scalar(
                        text("""
                            INSERT INTO workflow_tool_release
                                (workflow_tool_id, version)
                            VALUES (:t, '1.0') RETURNING id
                        """),
                        {"t": tool_id},
                    )
                    created.setdefault("workflow_tool_release", []).append(
                        release_id
                    )
                    return release_id
                raise AssertionError(f"no distinct value for {column!r}")

            row_a = dict(base)
            row_b = dict(base)
            row_b[differing_column] = _distinct_value_for(
                differing_column, base[differing_column]
            )

            columns = ", ".join(row_a)
            binds_a = ", ".join(f":a_{c}" for c in row_a)
            binds_b = ", ".join(f":b_{c}" for c in row_b)
            params = {f"a_{c}": v for c, v in row_a.items()}
            params.update({f"b_{c}": v for c, v in row_b.items()})
            inserted_fsf_ids = list(
                connection.scalars(
                    text(
                        f"INSERT INTO frequency_scale_factor ({columns}) "
                        f"VALUES ({binds_a}), ({binds_b}) RETURNING id"
                    ),
                    params,
                )
            )

        # Must not raise.
        command.upgrade(config, _MIGRATION.revision)

        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT id, software_release_id FROM frequency_scale_factor "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": inserted_fsf_ids},
            ).all()
            assert len(rows) == 2
            assert all(row.software_release_id is None for row in rows)
    finally:
        with engine.begin() as connection:
            if inserted_fsf_ids:
                connection.execute(
                    text(
                        "DELETE FROM frequency_scale_factor WHERE id = ANY(:ids)"
                    ),
                    {"ids": inserted_fsf_ids},
                )
            for table in (
                "workflow_tool_release",
                "workflow_tool",
                "literature",
                "level_of_theory",
            ):
                ids = created.get(table)
                if ids:
                    connection.execute(
                        text(f"DELETE FROM {table} WHERE id = ANY(:ids)"),
                        {"ids": ids},
                    )
        engine.dispose()
        command.upgrade(config, "head")


# ---------------------------------------------------------------------------
# No backfill
# ---------------------------------------------------------------------------


def test_upgrade_preserves_refs_and_lands_software_release_id_null(
    db_engine, monkeypatch
):
    """A fixture with a level of theory and a calculation at it resolving
    to exactly one software release (the shape ``b6d80e36dcec``'s
    LOT-unanimous derivation would have reached, had the same pattern
    been applied here) keeps its ``public_ref`` across the upgrade, and
    ``software_release_id`` lands ``NULL`` -- never backfilled, never
    guessed.

    *Mutation*: add any backfill at all to ``upgrade()`` -- the
    ``software_release_id IS NULL`` assertion below must then fail. This
    is the criterion that pins ruling 9 (no backfill) into the suite for
    this table too.

    Must downgrade first: ``db_engine`` migrates to head, and this
    revision *is* head, so an ``upgrade()`` without a preceding
    ``downgrade()`` is a no-op and every assertion below would be
    checked against a table the migration never touched.
    """
    _set_db_env(monkeypatch, db_engine.url.database)
    engine = _engine_for(db_engine)
    config = Config(str(REPO_ROOT / "alembic.ini"))

    fsf_ids: list[int] = []
    species_ids: list[int] = []
    lot_ids: list[int] = []
    calculation_ids: list[int] = []
    software_ids: list[int] = []
    software_release_ids: list[int] = []
    try:
        command.downgrade(config, _MIGRATION.parent)

        with engine.begin() as connection:
            suffix = uuid4().hex[:8]
            lot_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO level_of_theory (method, lot_hash)
                        VALUES ('fsf-release-grain-method', :h)
                        RETURNING id
                    """),
                    {"h": f"fsfrelgrain{suffix}"},
                )
            )
            software_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO software (name) VALUES (:name)
                        RETURNING id
                    """),
                    {"name": f"FSF-ReleaseGrain-Backfillable-{suffix}"},
                )
            )
            software_release_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO software_release (software_id, version)
                        VALUES (:sid, '16') RETURNING id
                    """),
                    {"sid": software_ids[0]},
                )
            )
            fsf_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO frequency_scale_factor
                            (level_of_theory_id, scale_kind, value, note)
                        VALUES (:lot_id, 'fundamental', 0.988,
                                'no backfill survives this row')
                        RETURNING id
                    """),
                    {"lot_id": lot_ids[0]},
                )
            )
            fsf_id = fsf_ids[0]
            ref_before = connection.scalar(
                text("SELECT public_ref FROM frequency_scale_factor WHERE id = :id"),
                {"id": fsf_id},
            )

            species_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO species
                            (kind, smiles, inchi_key, charge, multiplicity, stereo_kind)
                        VALUES ('molecule', :smiles, :inchi_key, 0, 1, 'achiral')
                        RETURNING id
                    """),
                    {
                        "smiles": f"C{suffix}",
                        "inchi_key": f"FSFRELGRAIN{suffix}AAAAAAAA"[:27],
                    },
                )
            )
            species_entry_id = connection.scalar(
                text(
                    "INSERT INTO species_entry (species_id) VALUES (:sid) "
                    "RETURNING id"
                ),
                {"sid": species_ids[0]},
            )
            calculation_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO calculation
                            (type, lot_id, software_release_id, species_entry_id)
                        VALUES ('freq', :lot_id, :srel_id, :entry_id)
                        RETURNING id
                    """),
                    {
                        "lot_id": lot_ids[0],
                        "srel_id": software_release_ids[0],
                        "entry_id": species_entry_id,
                    },
                )
            )

        command.upgrade(config, _MIGRATION.revision)

        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT public_ref, software_release_id FROM "
                    "frequency_scale_factor WHERE id = :id"
                ),
                {"id": fsf_id},
            ).one()
            assert row.public_ref == ref_before
            assert row.software_release_id is None
    finally:
        with engine.begin() as connection:
            if fsf_ids:
                connection.execute(
                    text(
                        "DELETE FROM frequency_scale_factor WHERE id = ANY(:ids)"
                    ),
                    {"ids": fsf_ids},
                )
            if calculation_ids:
                connection.execute(
                    text("DELETE FROM calculation WHERE id = ANY(:ids)"),
                    {"ids": calculation_ids},
                )
            if species_ids:
                connection.execute(
                    text("DELETE FROM species_entry WHERE species_id = ANY(:ids)"),
                    {"ids": species_ids},
                )
                connection.execute(
                    text("DELETE FROM species WHERE id = ANY(:ids)"),
                    {"ids": species_ids},
                )
            if software_release_ids:
                connection.execute(
                    text("DELETE FROM software_release WHERE id = ANY(:ids)"),
                    {"ids": software_release_ids},
                )
            if software_ids:
                connection.execute(
                    text("DELETE FROM software WHERE id = ANY(:ids)"),
                    {"ids": software_ids},
                )
            if lot_ids:
                connection.execute(
                    text("DELETE FROM level_of_theory WHERE id = ANY(:ids)"),
                    {"ids": lot_ids},
                )
        engine.dispose()
        command.upgrade(config, "head")


# ---------------------------------------------------------------------------
# The live archive's own shape: 10 rows distinguished only by
# workflow_tool_release_id must survive as 10 distinct rows.
# ---------------------------------------------------------------------------


def test_ten_rows_differing_only_by_workflow_tool_release_stay_distinct(
    db_engine, monkeypatch
):
    """Plan §6/§9.5's measured shape: 10 of the 12 live rows share
    ``(level_of_theory_id, scale_kind, value, source_literature_id)`` and
    are distinguished only by ``workflow_tool_release_id`` -- a column
    that was in the identity before this revision and stays in it. They
    must remain 10 distinct rows across the upgrade, not merge, since
    nothing about this migration touches that column.
    """
    _set_db_env(monkeypatch, db_engine.url.database)
    engine = _engine_for(db_engine)
    config = Config(str(REPO_ROOT / "alembic.ini"))

    fsf_ids: list[int] = []
    lot_ids: list[int] = []
    tool_ids: list[int] = []
    release_ids: list[int] = []
    try:
        command.downgrade(config, _MIGRATION.parent)

        with engine.begin() as connection:
            suffix = uuid4().hex[:8]
            lot_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO level_of_theory (method, lot_hash)
                        VALUES ('fsf-ten-rows-method', :h) RETURNING id
                    """),
                    {"h": f"fsftenrows{suffix}"},
                )
            )
            tool_id = connection.scalar(
                text("INSERT INTO workflow_tool (name) VALUES (:n) RETURNING id"),
                {"n": f"FSFTenRowsARC{suffix}"},
            )
            tool_ids.append(tool_id)

            for i in range(10):
                release_id = connection.scalar(
                    text("""
                        INSERT INTO workflow_tool_release
                            (workflow_tool_id, version)
                        VALUES (:t, :v) RETURNING id
                    """),
                    {"t": tool_id, "v": f"1.{i}.0"},
                )
                release_ids.append(release_id)
                fsf_id = connection.scalar(
                    text("""
                        INSERT INTO frequency_scale_factor
                            (level_of_theory_id, scale_kind, value,
                             workflow_tool_release_id, note)
                        VALUES (:lot_id, 'fundamental', 0.999, :rel_id,
                                'http://cccbdb.nist.gov/vibscalejust.asp')
                        RETURNING id
                    """),
                    {"lot_id": lot_ids[0], "rel_id": release_id},
                )
                fsf_ids.append(fsf_id)

            refs_before = {
                row.id: row.public_ref
                for row in connection.execute(
                    text(
                        "SELECT id, public_ref FROM frequency_scale_factor "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": fsf_ids},
                )
            }
            assert len(refs_before) == 10, "fixture must seed 10 distinct rows"

        command.upgrade(config, _MIGRATION.revision)

        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT id, public_ref, software_release_id FROM "
                    "frequency_scale_factor WHERE id = ANY(:ids)"
                ),
                {"ids": fsf_ids},
            ).all()
            assert len(rows) == 10, "all 10 rows must survive, not merge"
            for row in rows:
                assert row.public_ref == refs_before[row.id]
                assert row.software_release_id is None
    finally:
        with engine.begin() as connection:
            if fsf_ids:
                connection.execute(
                    text(
                        "DELETE FROM frequency_scale_factor WHERE id = ANY(:ids)"
                    ),
                    {"ids": fsf_ids},
                )
            if release_ids:
                connection.execute(
                    text(
                        "DELETE FROM workflow_tool_release WHERE id = ANY(:ids)"
                    ),
                    {"ids": release_ids},
                )
            if tool_ids:
                connection.execute(
                    text("DELETE FROM workflow_tool WHERE id = ANY(:ids)"),
                    {"ids": tool_ids},
                )
            if lot_ids:
                connection.execute(
                    text("DELETE FROM level_of_theory WHERE id = ANY(:ids)"),
                    {"ids": lot_ids},
                )
        engine.dispose()
        command.upgrade(config, "head")


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_two_row_fixture_survives_downgrade_and_reupgrade(db_engine, monkeypatch):
    """Upgrade -> downgrade -> upgrade: a fixture that does not depend on
    the widened index for its distinctness (two rows differing by
    ``scale_kind``) survives byte-for-byte throughout, and
    ``software_id`` (reintroduced by the downgrade) then
    ``software_release_id`` (reintroduced by the re-upgrade) both land
    ``NULL`` -- restored as columns, never as guessed values.
    """
    _set_db_env(monkeypatch, db_engine.url.database)
    engine = _engine_for(db_engine)
    config = Config(str(REPO_ROOT / "alembic.ini"))

    inserted_ids: list[int] = []
    lot_ids: list[int] = []
    try:
        with engine.begin() as connection:
            suffix = uuid4().hex[:8]
            lot_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO level_of_theory (method, lot_hash)
                        VALUES ('fsf-round-trip-method', :h) RETURNING id
                    """),
                    {"h": f"fsfroundtrip{suffix}"},
                )
            )
            inserted_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO frequency_scale_factor
                            (level_of_theory_id, scale_kind, value, note)
                        VALUES
                            (:lot_id, 'fundamental', 0.991, 'round trip row a'),
                            (:lot_id, 'zpe', 0.991, 'round trip row b')
                        RETURNING id
                    """),
                    {"lot_id": lot_ids[0]},
                )
            )
            refs_before = {
                row.id: row.public_ref
                for row in connection.execute(
                    text(
                        "SELECT id, public_ref FROM frequency_scale_factor "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": inserted_ids},
                )
            }

        command.downgrade(config, _MIGRATION.parent)
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT id, public_ref, scale_kind, software_id FROM "
                    "frequency_scale_factor WHERE id = ANY(:ids) ORDER BY id"
                ),
                {"ids": inserted_ids},
            ).all()
            assert len(rows) == 2
            for row in rows:
                assert row.public_ref == refs_before[row.id]
                assert row.software_id is None
            assert {r.scale_kind for r in rows} == {"fundamental", "zpe"}
            has_release_column = connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'frequency_scale_factor' "
                    "AND column_name = 'software_release_id'"
                )
            )
            assert has_release_column == 0

        command.upgrade(config, _MIGRATION.revision)
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT id, public_ref, scale_kind, software_release_id FROM "
                    "frequency_scale_factor WHERE id = ANY(:ids) ORDER BY id"
                ),
                {"ids": inserted_ids},
            ).all()
            assert len(rows) == 2
            for row in rows:
                assert row.public_ref == refs_before[row.id]
                assert row.software_release_id is None
            assert {r.scale_kind for r in rows} == {"fundamental", "zpe"}
    finally:
        with engine.begin() as connection:
            if inserted_ids:
                connection.execute(
                    text(
                        "DELETE FROM frequency_scale_factor WHERE id = ANY(:ids)"
                    ),
                    {"ids": inserted_ids},
                )
            if lot_ids:
                connection.execute(
                    text("DELETE FROM level_of_theory WHERE id = ANY(:ids)"),
                    {"ids": lot_ids},
                )
        engine.dispose()
        command.upgrade(config, "head")


def test_downgrade_after_release_only_distinction_fails_loudly(db_engine, monkeypatch):
    """Once two factors have been distinguished only by
    ``software_release_id`` -- possible only under this revision's
    widened index -- a downgrade past it cannot represent them and must
    fail on a genuine unique violation rather than silently re-merging
    two distinct factors.
    """
    _set_db_env(monkeypatch, db_engine.url.database)
    engine = _engine_for(db_engine)
    config = Config(str(REPO_ROOT / "alembic.ini"))

    inserted_ids: list[int] = []
    lot_ids: list[int] = []
    software_ids: list[int] = []
    software_release_ids: list[int] = []
    try:
        with engine.begin() as connection:
            suffix = uuid4().hex[:8]
            lot_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO level_of_theory (method, lot_hash)
                        VALUES ('fsf-release-only-method', :h) RETURNING id
                    """),
                    {"h": f"fsfrelonly{suffix}"},
                )
            )
            software_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO software (name) VALUES (:n) RETURNING id
                    """),
                    {"n": f"FSF-Release-Only-{suffix}"},
                )
            )
            software_release_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO software_release (software_id, version)
                        VALUES (:sid, '16'), (:sid, '09')
                        RETURNING id
                    """),
                    {"sid": software_ids[0]},
                )
            )
            inserted_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO frequency_scale_factor
                            (level_of_theory_id, scale_kind, value,
                             software_release_id, note)
                        VALUES
                            (:lot_id, 'fundamental', 0.965, :rel_a, 'release 16'),
                            (:lot_id, 'fundamental', 0.965, :rel_b, 'release 09')
                        RETURNING id
                    """),
                    {
                        "lot_id": lot_ids[0],
                        "rel_a": software_release_ids[0],
                        "rel_b": software_release_ids[1],
                    },
                )
            )

        with pytest.raises(
            Exception, match="uq_frequency_scale_factor_identity"
        ):
            command.downgrade(config, _MIGRATION.parent)

        with engine.connect() as connection:
            count = connection.scalar(
                text(
                    "SELECT count(*) FROM frequency_scale_factor "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": inserted_ids},
            )
            assert count == 2

            columns = set(
                connection.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'frequency_scale_factor'"
                    )
                )
            )
            assert "software_release_id" in columns
            assert "software_id" not in columns
    finally:
        with engine.begin() as connection:
            if inserted_ids:
                connection.execute(
                    text(
                        "DELETE FROM frequency_scale_factor WHERE id = ANY(:ids)"
                    ),
                    {"ids": inserted_ids},
                )
            if software_release_ids:
                connection.execute(
                    text("DELETE FROM software_release WHERE id = ANY(:ids)"),
                    {"ids": software_release_ids},
                )
            if software_ids:
                connection.execute(
                    text("DELETE FROM software WHERE id = ANY(:ids)"),
                    {"ids": software_ids},
                )
            if lot_ids:
                connection.execute(
                    text("DELETE FROM level_of_theory WHERE id = ANY(:ids)"),
                    {"ids": lot_ids},
                )
        engine.dispose()
        command.upgrade(config, "head")
