"""Round-trip coverage for the energy-correction-scheme provenance
migrations: the v1 software+citation widening (``b6d80e36dcec``) and the
v2 release-grain migration built on top of it (``c24ce2d9c198``).

``energy_correction_scheme`` is an already-deployed table, so the
migration rules require both ``upgrade()`` and ``downgrade()`` to be
implemented and correct.

``b6d80e36dcec`` coverage (below) asserts the concrete claim that
revision's docstring makes: a two-row fixture in the shape of the two
live rows (same kind/lot/version-adjacent identity, both distinct only
by ``kind``, both uncited and software-less) survives an
upgrade -> downgrade -> upgrade round trip byte-for-byte on every
column the narrower schema still has, and the new columns come back
``NULL`` (never guessed) after the second upgrade.

``c24ce2d9c198`` coverage (further below) covers plan §10's PR 1
red-first criteria: the mandatory pre-flight collision check, the
no-backfill guarantee on ``software_release_id``, and an
upgrade -> downgrade -> upgrade round trip for the release-grain shape
-- on a fixture that does not itself depend on the widened index, since
one that does (two rows distinguished only by ``units``) is exactly the
case a *downgrade* must refuse: that direction is covered by its own
test asserting the genuine unique violation, not a round trip.
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

_MIGRATION = revision_under_test("b6d80e36dcec")
_MIGRATION_RELEASE_GRAIN = revision_under_test("c24ce2d9c198")


def _set_db_env(monkeypatch, db_name: str) -> None:
    monkeypatch.setenv("DB_NAME", db_name)
    monkeypatch.setenv("DB_USER", "tckdb")
    monkeypatch.setenv("DB_PASSWORD", "tckdb")
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "5432")


def test_two_row_fixture_survives_downgrade_and_reupgrade(db_engine, monkeypatch):
    """Upgrade -> downgrade -> upgrade: the two-row fixture is intact
    throughout, refs never change, and the reintroduced columns land
    NULL, never a guess."""
    db_name = db_engine.url.database
    db_engine.dispose()
    monkeypatch.setenv("DB_NAME", db_name)
    monkeypatch.setenv("DB_USER", "tckdb")
    monkeypatch.setenv("DB_PASSWORD", "tckdb")
    monkeypatch.setenv("DB_HOST", "127.0.0.1")
    monkeypatch.setenv("DB_PORT", "5432")
    config = Config(str(REPO_ROOT / "alembic.ini"))

    engine = create_engine(db_engine.url.render_as_string(hide_password=False))
    inserted_ids: list[int] = []
    try:
        # Seed the fixture at head (columns present, both NULL -- exactly
        # the two live rows' shape).
        with engine.begin() as connection:
            inserted_ids = list(
                connection.scalars(
                    text("""
                INSERT INTO energy_correction_scheme (kind, name, version, units, note)
                VALUES ('atom_energy', 'atom_energy', NULL, 'hartree', 'migration test row 1'),
                       ('bac_petersson', 'bac_petersson', NULL, 'hartree', 'migration test row 2')
                RETURNING id
            """)
                )
            )
            refs_before = {
                row.id: row.public_ref
                for row in connection.execute(
                    text(
                        "SELECT id, public_ref FROM energy_correction_scheme "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": inserted_ids},
                )
            }

        # Downgrade past this revision: the new columns/index disappear,
        # the fixture's other columns and refs must not.
        command.downgrade(config, _MIGRATION.parent)
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT id, kind, name, public_ref, note FROM "
                    "energy_correction_scheme WHERE id = ANY(:ids) ORDER BY id"
                ),
                {"ids": inserted_ids},
            ).all()
            assert len(rows) == 2
            for row in rows:
                assert row.public_ref == refs_before[row.id]
            assert {r.note for r in rows} == {
                "migration test row 1",
                "migration test row 2",
            }
            has_software_column = connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'energy_correction_scheme' "
                    "AND column_name = 'software_id'"
                )
            )
            assert has_software_column == 0

        # Re-upgrade: the fixture is still there, with the same refs, and
        # the reintroduced columns are NULL -- not backfilled, not guessed.
        command.upgrade(config, _MIGRATION.revision)
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT id, public_ref, software_id, source_literature_id, "
                    "workflow_tool_release_id, note FROM energy_correction_scheme "
                    "WHERE id = ANY(:ids) ORDER BY id"
                ),
                {"ids": inserted_ids},
            ).all()
            assert len(rows) == 2
            for row in rows:
                assert row.public_ref == refs_before[row.id]
                assert row.software_id is None
                assert row.source_literature_id is None
                assert row.workflow_tool_release_id is None
    finally:
        if inserted_ids:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM energy_correction_scheme WHERE id = ANY(:ids)"
                    ),
                    {"ids": inserted_ids},
                )
        engine.dispose()
        # Put the schema back where this test found it -- see
        # test_upload_job_lease_migration.py's identical finally block for
        # why this must be "head", not ``_MIGRATION.revision``.
        command.upgrade(config, "head")


# ---------------------------------------------------------------------------
# c24ce2d9c198 -- release grain (correction-scheme-provenance plan v2, PR 1)
# ---------------------------------------------------------------------------


def test_release_grain_preflight_check_aborts_on_software_only_collision(
    db_engine, monkeypatch
):
    """Plan §10's first red-first criterion: two schemes distinguished
    today only by ``software_id`` must abort the upgrade, naming both
    offending public refs, rather than let the new unique index merge or
    pick between them.

    *Mutation*: delete ``_refuse_software_only_collisions`` from the
    revision's ``upgrade()`` -- this fixture must then fail with a raw
    ``IntegrityError`` from ``CREATE UNIQUE INDEX`` instead of the
    legible ``RuntimeError`` this check exists to raise instead.
    """
    db_name = db_engine.url.database
    db_engine.dispose()
    _set_db_env(monkeypatch, db_name)
    config = Config(str(REPO_ROOT / "alembic.ini"))

    engine = create_engine(db_engine.url.render_as_string(hide_password=False))
    inserted_scheme_ids: list[int] = []
    inserted_software_ids: list[int] = []
    try:
        # Back the DB down to the schema this revision inherits:
        # software_id still a column, software_release_id/the widened
        # index not yet present.
        command.downgrade(config, _MIGRATION_RELEASE_GRAIN.parent)

        with engine.begin() as connection:
            inserted_software_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO software (name)
                        VALUES ('Preflight-Collision-Gaussian'),
                               ('Preflight-Collision-ORCA')
                        RETURNING id
                    """)
                )
            )
            gaussian_id, orca_id = inserted_software_ids
            inserted_scheme_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO energy_correction_scheme
                            (kind, name, version, units, note, software_id)
                        VALUES
                            ('atom_energy', 'preflight_collision_scheme', NULL,
                             'hartree', 'row a', :gaussian_id),
                            ('atom_energy', 'preflight_collision_scheme', NULL,
                             'hartree', 'row b', :orca_id)
                        RETURNING id
                    """),
                    {"gaussian_id": gaussian_id, "orca_id": orca_id},
                )
            )
            refs = {
                row.id: row.public_ref
                for row in connection.execute(
                    text(
                        "SELECT id, public_ref FROM energy_correction_scheme "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": inserted_scheme_ids},
                )
            }

        with pytest.raises(RuntimeError) as excinfo:
            command.upgrade(config, _MIGRATION_RELEASE_GRAIN.revision)
        message = str(excinfo.value)
        for ref in refs.values():
            assert ref in message, f"{ref!r} not named in abort message: {message}"

        # The abort must be loud, not merely silent-and-stalled: the DB is
        # still at the parent revision, not partway through the new one.
        with engine.connect() as connection:
            has_release_column = connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'energy_correction_scheme' "
                    "AND column_name = 'software_release_id'"
                )
            )
            assert has_release_column == 0
    finally:
        with engine.begin() as connection:
            if inserted_scheme_ids:
                connection.execute(
                    text(
                        "DELETE FROM energy_correction_scheme WHERE id = ANY(:ids)"
                    ),
                    {"ids": inserted_scheme_ids},
                )
            if inserted_software_ids:
                connection.execute(
                    text("DELETE FROM software WHERE id = ANY(:ids)"),
                    {"ids": inserted_software_ids},
                )
        engine.dispose()
        command.upgrade(config, "head")


def _track(created: dict[str, list[int]], table: str, row_id: int) -> int:
    """Record ``row_id`` under ``table`` so the caller can delete it."""
    created.setdefault(table, []).append(row_id)
    return row_id


def _distinct_value_for(connection, column: str, baseline, created):
    """A value for ``column`` that differs from ``baseline``, creating the
    parent row first for the three foreign-key columns.

    Every parent row created here is recorded in ``created`` (a
    ``{table: [ids]}`` mapping) so the caller can delete it. This suite
    asserts that a test commits nothing into the shared database, so a
    lookup row left behind fails the run rather than merely littering.

    Kept narrow on purpose -- this exists to vary one identity column at
    a time, not to build a general fixture factory.
    """
    suffix = uuid4().hex[:8]
    if column == "kind":
        return "atom_hf"
    if column == "name":
        return f"legit_difference_scheme_{suffix}"
    if column == "version":
        return "v2"
    if column == "units":
        return "kcal_mol"
    if column == "level_of_theory_id":
        return _track(created, "level_of_theory", connection.scalar(
            text(
                "INSERT INTO level_of_theory (method, lot_hash) "
                "VALUES ('preflight-legit', :h) RETURNING id"
            ),
            {"h": f"preflightlegit{suffix}"},
        ))
    if column == "source_literature_id":
        return _track(created, "literature", connection.scalar(
            text(
                "INSERT INTO literature (kind, title) "
                "VALUES ('article', :t) RETURNING id"
            ),
            {"t": f"Preflight Legit Difference {suffix}"},
        ))
    if column == "workflow_tool_release_id":
        tool_id = _track(
            created,
            "workflow_tool",
            connection.scalar(
                text("INSERT INTO workflow_tool (name) VALUES (:n) RETURNING id"),
                {"n": f"PreflightLegitTool{suffix}"},
            ),
        )
        return _track(created, "workflow_tool_release", connection.scalar(
            text(
                "INSERT INTO workflow_tool_release (workflow_tool_id, version) "
                "VALUES (:t, '1.0') RETURNING id"
            ),
            {"t": tool_id},
        ))
    raise AssertionError(f"no distinct value defined for column {column!r}")


@pytest.mark.parametrize(
    "differing_column",
    [
        "kind",
        "name",
        "version",
        # "units" is deliberately absent, and its absence is a result
        # rather than an omission: at the parent revision ``units`` is
        # NOT in the identity index, so two rows differing only by units
        # cannot be inserted there at all -- the seed fails with a
        # UniqueViolation on the narrow index. That is precisely the
        # defect this revision fixes (plan §3.3). The units axis is
        # covered instead by
        # tests/services/test_energy_correction_resolution_provenance.py
        # (``test_two_schemes_same_identity_different_units_are_distinct_rows``
        # and ``test_same_correction_in_a_second_unit_creates_a_second_row
        # _not_a_conflict``) and, for the down path, by
        # ``test_release_grain_downgrade_after_units_only_distinction
        # _fails_loudly`` below.
        "level_of_theory_id",
        "source_literature_id",
        "workflow_tool_release_id",
    ],
)
def test_release_grain_preflight_check_does_not_fire_for_a_legitimate_difference(
    db_engine, monkeypatch, differing_column
):
    """The same shape, differing by one of the seven columns the widened
    identity still checks instead of by ``software_id``, must upgrade
    cleanly -- the check is scoped to the one column this revision
    actually drops, not to every pair of rows sharing a name.

    Parametrized over **all seven** columns in ``_COLLISION_QUERY``'s
    ``GROUP BY``. The single-column (``kind``-only) version of this test
    could not see a ``GROUP BY`` that had lost any of the other six:
    dropping one makes the check *over*-refuse, which is the safe
    direction but produces a legible-sounding error about a collision
    that is not one, and blocks an upgrade that should proceed. Raised in
    review of #458.

    *Mutation*: delete any column from ``_COLLISION_QUERY``'s ``GROUP
    BY`` -- the case named after that column must then fail with the
    revision's ``RuntimeError``.
    """
    db_name = db_engine.url.database
    db_engine.dispose()
    _set_db_env(monkeypatch, db_name)
    config = Config(str(REPO_ROOT / "alembic.ini"))

    engine = create_engine(db_engine.url.render_as_string(hide_password=False))
    inserted_scheme_ids: list[int] = []
    created: dict[str, list[int]] = {}
    try:
        command.downgrade(config, _MIGRATION_RELEASE_GRAIN.parent)

        with engine.begin() as connection:
            # Baseline the two rows agree on; ``differing_column`` is
            # then overridden to a distinct value on the second row.
            base: dict[str, object] = {
                "kind": "atom_energy",
                "name": "legit_difference_scheme",
                "version": None,
                "units": "hartree",
                "level_of_theory_id": None,
                "source_literature_id": None,
                "workflow_tool_release_id": None,
            }
            row_a = dict(base)
            row_b = dict(base)
            row_b[differing_column] = _distinct_value_for(
                connection, differing_column, base[differing_column], created
            )

            columns = ", ".join(base)
            binds_a = ", ".join(f":a_{c}" for c in base)
            binds_b = ", ".join(f":b_{c}" for c in base)
            params = {f"a_{c}": v for c, v in row_a.items()}
            params.update({f"b_{c}": v for c, v in row_b.items()})
            inserted_scheme_ids = list(
                connection.scalars(
                    text(
                        f"INSERT INTO energy_correction_scheme ({columns}) "
                        f"VALUES ({binds_a}), ({binds_b}) RETURNING id"
                    ),
                    params,
                )
            )

        # Must not raise.
        command.upgrade(config, _MIGRATION_RELEASE_GRAIN.revision)

        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT id, software_release_id FROM energy_correction_scheme "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": inserted_scheme_ids},
            ).all()
            assert len(rows) == 2
            assert all(row.software_release_id is None for row in rows)
    finally:
        with engine.begin() as connection:
            if inserted_scheme_ids:
                connection.execute(
                    text(
                        "DELETE FROM energy_correction_scheme WHERE id = ANY(:ids)"
                    ),
                    {"ids": inserted_scheme_ids},
                )
            # Parents last, and releases before their tools. The schemes
            # referencing them are already gone above.
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


def test_release_grain_upgrade_preserves_refs_params_and_applied_rows(
    db_engine, monkeypatch
):
    """Plan §10's second red-first criterion: a fixture in the live rows'
    shape keeps its ``public_ref``, its parameters, and every dependent
    ``applied_energy_correction`` row across the upgrade, and
    ``software_release_id`` lands ``NULL`` -- never backfilled, never
    guessed.

    *Mutation*: add any backfill at all to ``upgrade()`` -- the
    ``software_release_id IS NULL`` assertion below must then fail. This
    is the criterion that pins ruling 9 (no backfill) into the suite.

    Two things this test needs in order to mean that, both of which it
    lacked when first written (found in review of #458):

    1. It must **downgrade first**. ``db_engine`` migrates the test DB to
       head, and this revision *is* head, so an ``upgrade()`` to it
       without a preceding ``downgrade()`` is a no-op and every assertion
       below is checked against a table the migration never touched. The
       blanket-backfill mutation above was applied and this test stayed
       green; only the round-trip test (which does downgrade) went red.

    2. The fixture must be **backfillable**. The realistic regression is
       not a blanket ``UPDATE`` -- it is someone restoring
       ``b6d80e36dcec``'s ``_backfill_software_and_workflow_tool_release``,
       which derives from ``calculation`` rows at the scheme's own level
       of theory (``GROUP BY c.lot_id HAVING count(DISTINCT
       sr.software_id) = 1``). A scheme with no ``level_of_theory_id``
       and no calculations is invisible to that derivation, so it would
       stay ``NULL`` and this test would pass while ruling 9 was being
       violated. The fixture below therefore carries a level of theory
       and one ``calculation`` at it with exactly one distinct software
       release -- precisely the shape the v1 derivation *would* fill in.
    """
    db_name = db_engine.url.database
    db_engine.dispose()
    _set_db_env(monkeypatch, db_name)
    config = Config(str(REPO_ROOT / "alembic.ini"))

    engine = create_engine(db_engine.url.render_as_string(hide_password=False))
    scheme_ids: list[int] = []
    species_ids: list[int] = []
    lot_ids: list[int] = []
    calculation_ids: list[int] = []
    software_ids: list[int] = []
    software_release_ids: list[int] = []
    try:
        # Back the DB down to the schema this revision inherits, so the
        # upgrade below actually runs. Without this the DB is already at
        # this revision and Alembic no-ops -- see the docstring.
        command.downgrade(config, _MIGRATION_RELEASE_GRAIN.parent)

        with engine.begin() as connection:
            # The shape b6d80e36dcec's derivation would have filled in:
            # a level of theory with calculations at it resolving to
            # exactly one software. If any backfill returns, this row is
            # what it reaches, and the NULL assertion below goes red.
            lot_suffix = uuid4().hex[:8]
            lot_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO level_of_theory (method, lot_hash)
                        VALUES ('ecs-release-grain-method', :lot_hash)
                        RETURNING id
                    """),
                    {"lot_hash": f"ecsrelgrain{lot_suffix}"},
                )
            )
            software_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO software (name)
                        VALUES (:name)
                        RETURNING id
                    """),
                    {"name": f"ReleaseGrain-Backfillable-{lot_suffix}"},
                )
            )
            software_release_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO software_release (software_id, version)
                        VALUES (:sid, '16')
                        RETURNING id
                    """),
                    {"sid": software_ids[0]},
                )
            )
            scheme_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO energy_correction_scheme
                            (kind, name, version, units, note,
                             level_of_theory_id)
                        VALUES ('atom_energy', 'applied_row_survives_scheme',
                                NULL, 'hartree', 'has params and a dependent row',
                                :lot_id)
                        RETURNING id
                    """),
                    {"lot_id": lot_ids[0]},
                )
            )
            scheme_id = scheme_ids[0]
            ref_before = connection.scalar(
                text(
                    "SELECT public_ref FROM energy_correction_scheme WHERE id = :id"
                ),
                {"id": scheme_id},
            )

            connection.execute(
                text(
                    "INSERT INTO energy_correction_scheme_atom_param "
                    "(scheme_id, element, value) VALUES (:id, 'H', -0.501092978611)"
                ),
                {"id": scheme_id},
            )

            # Species identity is (smiles, charge, multiplicity), and this
            # database outlives each test in the module, so the SMILES has
            # to be distinct per call rather than a literal (see
            # test_accepted_science_repair_lifetime.py's identical fixture).
            suffix = uuid4().hex[:8]
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
                        "inchi_key": f"ECSRELGRAIN{suffix}AAAAAAAA"[:27],
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
            # ``calculation`` requires exactly one owner
            # (``ck_calculation_one_owner``), so it is created here,
            # after the species entry exists, rather than beside the
            # level of theory above.
            calculation_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO calculation
                            (type, lot_id, software_release_id,
                             species_entry_id)
                        VALUES ('sp', :lot_id, :srel_id, :entry_id)
                        RETURNING id
                    """),
                    {
                        "lot_id": lot_ids[0],
                        "srel_id": software_release_ids[0],
                        "entry_id": species_entry_id,
                    },
                )
            )

            applied_id = connection.scalar(
                text("""
                    INSERT INTO applied_energy_correction
                        (target_species_entry_id, scheme_id, application_role,
                         value, value_unit)
                    VALUES (:entry_id, :scheme_id, 'aec_total', -0.001, 'hartree')
                    RETURNING id
                """),
                {"entry_id": species_entry_id, "scheme_id": scheme_id},
            )

        command.upgrade(config, _MIGRATION_RELEASE_GRAIN.revision)

        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT public_ref, software_release_id FROM "
                    "energy_correction_scheme WHERE id = :id"
                ),
                {"id": scheme_id},
            ).one()
            assert row.public_ref == ref_before
            assert row.software_release_id is None

            atom_param = connection.execute(
                text(
                    "SELECT element, value FROM energy_correction_scheme_atom_param "
                    "WHERE scheme_id = :id"
                ),
                {"id": scheme_id},
            ).one()
            assert atom_param.element == "H"

            applied = connection.execute(
                text(
                    "SELECT scheme_id FROM applied_energy_correction WHERE id = :id"
                ),
                {"id": applied_id},
            ).one()
            assert applied.scheme_id == scheme_id
    finally:
        with engine.begin() as connection:
            if scheme_ids:
                connection.execute(
                    text(
                        "DELETE FROM applied_energy_correction "
                        "WHERE scheme_id = ANY(:ids)"
                    ),
                    {"ids": scheme_ids},
                )
                connection.execute(
                    text(
                        "DELETE FROM energy_correction_scheme_atom_param "
                        "WHERE scheme_id = ANY(:ids)"
                    ),
                    {"ids": scheme_ids},
                )
                connection.execute(
                    text(
                        "DELETE FROM energy_correction_scheme WHERE id = ANY(:ids)"
                    ),
                    {"ids": scheme_ids},
                )
            # FK order: calculation owns a species_entry and points at
            # the level of theory and software release, so it goes first;
            # level_of_theory last, once both the scheme and the
            # calculation that reference it are gone.
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


def test_release_grain_two_row_fixture_survives_downgrade_and_reupgrade(
    db_engine, monkeypatch
):
    """Upgrade -> downgrade -> upgrade at the release-grain revision: a
    fixture that does *not* depend on the widened index for its
    distinctness (two rows differing by ``kind``, both otherwise NULL --
    the two live rows' own shape) survives byte-for-byte throughout, and
    ``software_id`` (reintroduced by the downgrade) then
    ``software_release_id`` (reintroduced by the re-upgrade) both land
    ``NULL`` -- restored as columns, never as guessed values.

    A fixture distinguished *only* by ``units`` is deliberately not used
    here -- see
    ``test_release_grain_downgrade_after_units_only_distinction_fails_loudly``
    below for that case, which the module docstring says must fail, not
    round-trip.
    """
    db_name = db_engine.url.database
    db_engine.dispose()
    _set_db_env(monkeypatch, db_name)
    config = Config(str(REPO_ROOT / "alembic.ini"))

    engine = create_engine(db_engine.url.render_as_string(hide_password=False))
    inserted_ids: list[int] = []
    try:
        # Seed at head: two rows identical but for kind -- distinct under
        # both the narrower and the widened index, so the round trip
        # exercises the column changes without also depending on them.
        with engine.begin() as connection:
            inserted_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO energy_correction_scheme
                            (kind, name, version, units, note)
                        VALUES
                            ('atom_energy', 'round_trip_kind_scheme', NULL,
                             'hartree', 'round trip row atom_energy'),
                            ('atom_hf', 'round_trip_kind_scheme', NULL,
                             'hartree', 'round trip row atom_hf')
                        RETURNING id
                    """)
                )
            )
            refs_before = {
                row.id: row.public_ref
                for row in connection.execute(
                    text(
                        "SELECT id, public_ref FROM energy_correction_scheme "
                        "WHERE id = ANY(:ids)"
                    ),
                    {"ids": inserted_ids},
                )
            }

        command.downgrade(config, _MIGRATION_RELEASE_GRAIN.parent)
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT id, public_ref, kind, software_id FROM "
                    "energy_correction_scheme WHERE id = ANY(:ids) ORDER BY id"
                ),
                {"ids": inserted_ids},
            ).all()
            assert len(rows) == 2
            for row in rows:
                assert row.public_ref == refs_before[row.id]
                assert row.software_id is None
            assert {r.kind for r in rows} == {"atom_energy", "atom_hf"}
            has_release_column = connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = 'energy_correction_scheme' "
                    "AND column_name = 'software_release_id'"
                )
            )
            assert has_release_column == 0

        command.upgrade(config, _MIGRATION_RELEASE_GRAIN.revision)
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT id, public_ref, kind, software_release_id FROM "
                    "energy_correction_scheme WHERE id = ANY(:ids) ORDER BY id"
                ),
                {"ids": inserted_ids},
            ).all()
            assert len(rows) == 2
            for row in rows:
                assert row.public_ref == refs_before[row.id]
                assert row.software_release_id is None
            assert {r.kind for r in rows} == {"atom_energy", "atom_hf"}
    finally:
        if inserted_ids:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM energy_correction_scheme WHERE id = ANY(:ids)"
                    ),
                    {"ids": inserted_ids},
                )
        engine.dispose()
        command.upgrade(config, "head")


def test_release_grain_downgrade_after_units_only_distinction_fails_loudly(
    db_engine, monkeypatch
):
    """The module docstring's claim, made concrete: once two schemes have
    been distinguished only by ``units`` -- possible only under this
    revision's widened index -- a downgrade past it cannot represent
    them and must fail on a genuine unique violation rather than
    silently re-merging two scientifically distinct libraries.

    This is the reverse-direction counterpart to the pre-flight check
    above: that check guards the *upgrade* against merging rows the old
    schema distinguished; here, nothing needs to guard the *downgrade*,
    because ``CREATE UNIQUE INDEX`` on the narrower key already refuses
    the merge on its own -- correctly and loudly.
    """
    db_name = db_engine.url.database
    db_engine.dispose()
    _set_db_env(monkeypatch, db_name)
    config = Config(str(REPO_ROOT / "alembic.ini"))

    engine = create_engine(db_engine.url.render_as_string(hide_password=False))
    inserted_ids: list[int] = []
    try:
        # Two rows identical on every column but units -- only expressible
        # under uq_energy_correction_scheme_identity as widened by
        # c24ce2d9c198.
        with engine.begin() as connection:
            inserted_ids = list(
                connection.scalars(
                    text("""
                        INSERT INTO energy_correction_scheme
                            (kind, name, version, units, note)
                        VALUES
                            ('bac_petersson', 'units_only_distinction_scheme',
                             NULL, 'hartree', 'row hartree'),
                            ('bac_petersson', 'units_only_distinction_scheme',
                             NULL, 'kcal_mol', 'row kcal_mol')
                        RETURNING id
                    """)
                )
            )

        with pytest.raises(Exception, match="uq_energy_correction_scheme_identity"):
            command.downgrade(config, _MIGRATION_RELEASE_GRAIN.parent)

        # The failed downgrade's transaction rolled back: still at head,
        # both rows and the widened index untouched.
        with engine.connect() as connection:
            count = connection.scalar(
                text(
                    "SELECT count(*) FROM energy_correction_scheme "
                    "WHERE id = ANY(:ids)"
                ),
                {"ids": inserted_ids},
            )
            assert count == 2

            # `count == 2` alone does not show the downgrade rolled back:
            # a failed downgrade never deletes rows, so that assertion
            # holds whether the schema reverted or was left half-changed.
            # Assert the schema itself is still the post-upgrade one
            # (raised in review of #458).
            columns = set(
                connection.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'energy_correction_scheme'"
                    )
                )
            )
            assert "software_release_id" in columns
            assert "software_id" not in columns

            indexed = list(
                connection.scalars(
                    text("""
                        SELECT a.attname
                        FROM pg_index i
                        JOIN pg_class c ON c.oid = i.indexrelid
                        JOIN pg_attribute a
                          ON a.attrelid = i.indrelid
                         AND a.attnum = ANY(i.indkey)
                        WHERE c.relname = 'uq_energy_correction_scheme_identity'
                    """)
                )
            )
            assert "units" in indexed
            assert "software_release_id" in indexed
            assert "software_id" not in indexed
    finally:
        if inserted_ids:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "DELETE FROM energy_correction_scheme WHERE id = ANY(:ids)"
                    ),
                    {"ids": inserted_ids},
                )
        engine.dispose()
        command.upgrade(config, "head")
