"""Round-trip and invariant coverage for ``0b4a3afabfd3``.

Phase C-E1 (docs/research/tckdb-phase-c-implementation-plan.md, "C2 --
observation, state, uncertainty and source-custody contracts").
``molecular_property_observation`` is an already-deployed table, so the
migration rules require both ``upgrade()`` and ``downgrade()`` to be
implemented and correct -- this module pins both directions.

Known problem, reproduced first. Before this revision, an experimental
ideal-gas Cp(T) point could only be expressed as
``property_kind=other``, a free-text ``scalar_unit`` (no fixed-unit
guarantee), no pressure, no state basis, and a bare ``scalar_uncertainty``
with no stated meaning -- see ``tests/schemas/
test_molecular_property_observation_schema.py::
TestExperimentalCpPointContract`` for the schema-level version of that
same story (the wire contract). This module is the DB-level version: it
proves the new CHECK constraints, tables and enum values the wire
contract relies on actually exist and are enforced by PostgreSQL, not
only by Pydantic.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

import app.db.models  # noqa: F401 -- register the mapper graph
from alembic import command
from app.db.base import Base
from tests.db._migration_chain import revision_under_test

REPO_ROOT = Path(__file__).resolve().parents[2]

_MIGRATION = revision_under_test("0b4a3afabfd3")

_NEW_COLUMNS = (
    "pressure_bar",
    "state_basis",
    "uncertainty_kind",
    "uncertainty_coverage_factor",
    "uncertainty_level_of_confidence_pct",
    "uncertainty_assessor",
    "external_source_record_id",
)

_NEW_ENUM_TYPES = (
    "observed_uncertainty_kind",
    "observed_uncertainty_assessor",
    "observed_state_basis",
    "external_source_record_kind",
)

#: The final DB constraint names this revision creates on
#: ``molecular_property_observation``, as declared via ``conv()`` on the
#: model / ``op.f()`` in the migration (see both files' comments on why
#: that wrapping is required for this specific table).
_MPO_CHECK_NAMES = (
    "ck_mpo_pressure_bar_gt_0",
    "ck_mpo_uncertainty_coverage_factor_ge_1",
    "ck_mpo_uncertainty_confidence_pct_range",
    "ck_mpo_heat_capacity_cp_unit_j_mol_k",
    "ck_mpo_heat_capacity_cp_requires_temperature",
    "ck_mpo_uncertainty_kind_iff_value",
    "ck_mpo_coverage_factor_only_expanded",
)

_EXTERNAL_SOURCE_RECORD_CHECK_NAMES = (
    "ck_external_source_record_content_sha256_hex",
    "ck_external_source_record_content_length_ge_0",
    "ck_external_source_record_http_status_range",
)


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


def _table_exists(connection, table_name: str) -> bool:
    return bool(
        connection.scalar(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name = :t"
            ),
            {"t": table_name},
        )
    )


def _column_exists(connection, table_name: str, column_name: str) -> bool:
    return bool(
        connection.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table_name, "c": column_name},
        )
    )


def _enum_type_exists(connection, type_name: str) -> bool:
    return bool(
        connection.scalar(
            text("SELECT count(*) FROM pg_type WHERE typname = :n"),
            {"n": type_name},
        )
    )


# ---------------------------------------------------------------------------
# Upgrade / downgrade round trip
# ---------------------------------------------------------------------------


def test_upgrade_creates_columns_tables_and_enums(db_engine, monkeypatch):
    """*Mutation*: gut ``upgrade()`` (e.g. remove the column adds) -- the
    presence assertions below then fail.
    """
    _set_db_env(monkeypatch, db_engine.url.database)
    engine = _engine_for(db_engine)
    config = Config(str(REPO_ROOT / "alembic.ini"))
    try:
        command.downgrade(config, _MIGRATION.parent)
        with engine.connect() as connection:
            assert not _table_exists(connection, "external_source")
            assert not _table_exists(connection, "external_source_record")
            for column in _NEW_COLUMNS:
                assert not _column_exists(
                    connection, "molecular_property_observation", column
                ), f"{column} should not exist below {_MIGRATION.revision}"

        command.upgrade(config, _MIGRATION.revision)
        with engine.connect() as connection:
            assert _table_exists(connection, "external_source")
            assert _table_exists(connection, "external_source_record")
            for column in _NEW_COLUMNS:
                assert _column_exists(
                    connection, "molecular_property_observation", column
                ), f"{column} missing after upgrade"
            for enum_type in _NEW_ENUM_TYPES:
                assert _enum_type_exists(connection, enum_type)
    finally:
        engine.dispose()
        command.upgrade(config, "head")


def test_downgrade_removes_columns_tables_and_enums(db_engine, monkeypatch):
    """*Mutation*: gut ``downgrade()`` (e.g. ``pass`` the body) -- the
    absence assertions below then fail because everything is still there.
    """
    _set_db_env(monkeypatch, db_engine.url.database)
    engine = _engine_for(db_engine)
    config = Config(str(REPO_ROOT / "alembic.ini"))
    try:
        command.downgrade(config, _MIGRATION.parent)
        with engine.connect() as connection:
            assert not _table_exists(connection, "external_source")
            assert not _table_exists(connection, "external_source_record")
            for column in _NEW_COLUMNS:
                assert not _column_exists(
                    connection, "molecular_property_observation", column
                )
            for enum_type in _NEW_ENUM_TYPES:
                assert not _enum_type_exists(connection, enum_type)
            # The enum VALUES added to existing types are NOT removed --
            # see the migration's "Downgrade asymmetry" docstring section.
            # Both parent types must still exist.
            assert _enum_type_exists(connection, "molecular_property_kind")
            assert _enum_type_exists(connection, "submission_record_type")
    finally:
        engine.dispose()
        command.upgrade(config, "head")


# ---------------------------------------------------------------------------
# Model-level inventory: catches a CHECK removed from the model but left in
# the migration (a DB-insert test alone cannot see this, since the test
# database is built purely from migration files).
# ---------------------------------------------------------------------------


def test_all_new_check_constraint_names_present_on_the_model():
    """*Mutation*: delete one ``CheckConstraint`` entry from
    ``MolecularPropertyObservation.__table_args__`` or
    ``ExternalSourceRecord.__table_args__`` -- the corresponding name then
    goes missing from this set.
    """
    mpo_table = Base.metadata.tables["molecular_property_observation"]
    mpo_names = {c.name for c in mpo_table.constraints if c.__class__.__name__ == "CheckConstraint"}
    for name in _MPO_CHECK_NAMES:
        assert name in mpo_names, f"{name} missing from the ORM model"

    esr_table = Base.metadata.tables["external_source_record"]
    esr_names = {c.name for c in esr_table.constraints if c.__class__.__name__ == "CheckConstraint"}
    for name in _EXTERNAL_SOURCE_RECORD_CHECK_NAMES:
        assert name in esr_names, f"{name} missing from the ORM model"


# ---------------------------------------------------------------------------
# CHECK constraints on molecular_property_observation
# ---------------------------------------------------------------------------


def _valid_cp_row_sql() -> str:
    return (
        "INSERT INTO molecular_property_observation"
        " (scientific_origin, property_kind, scalar_value, scalar_unit,"
        "  temperature_k, pressure_bar, state_basis, scalar_uncertainty,"
        "  uncertainty_kind, uncertainty_coverage_factor,"
        "  uncertainty_level_of_confidence_pct)"
        " VALUES"
        " ('experimental', 'heat_capacity_cp', 29.1, 'J/mol/K',"
        "  298.15, :pressure_bar, :state_basis, :scalar_uncertainty,"
        "  :uncertainty_kind, :uncertainty_coverage_factor,"
        "  :uncertainty_level_of_confidence_pct)"
        " RETURNING id"
    )


def _valid_cp_row_params(**overrides) -> dict:
    params = {
        "pressure_bar": 1.01325,
        "state_basis": "ideal_gas",
        "scalar_uncertainty": 0.5,
        "uncertainty_kind": "expanded",
        "uncertainty_coverage_factor": 2.0,
        "uncertainty_level_of_confidence_pct": 95.0,
    }
    params.update(overrides)
    return params


def test_valid_cp_row_is_accepted(db_session):
    """The happy path every violation test below is one field away from."""
    savepoint = db_session.begin_nested()
    try:
        db_session.execute(text(_valid_cp_row_sql()), _valid_cp_row_params())
        db_session.flush()
    finally:
        savepoint.rollback()


@pytest.mark.parametrize(
    "overrides,constraint",
    [
        pytest.param({"pressure_bar": -1.0}, "ck_mpo_pressure_bar_gt_0", id="pressure_bar"),
        pytest.param({"pressure_bar": 0.0}, "ck_mpo_pressure_bar_gt_0", id="pressure_bar_zero"),
        pytest.param(
            {"uncertainty_coverage_factor": 0.5},
            "ck_mpo_uncertainty_coverage_factor_ge_1",
            id="coverage_factor_below_one",
        ),
        pytest.param(
            {"uncertainty_level_of_confidence_pct": 0.0},
            "ck_mpo_uncertainty_confidence_pct_range",
            id="confidence_pct_zero",
        ),
        pytest.param(
            {"uncertainty_level_of_confidence_pct": 101.0},
            "ck_mpo_uncertainty_confidence_pct_range",
            id="confidence_pct_over_100",
        ),
        pytest.param(
            {
                "uncertainty_coverage_factor": 2.0,
                "uncertainty_kind": "standard",
            },
            "ck_mpo_coverage_factor_only_expanded",
            id="coverage_factor_with_standard_kind",
        ),
    ],
)
def test_a_cp_row_violating_one_check_is_refused(db_session, overrides, constraint):
    """*Mutation*: remove the named CHECK from the model AND the migration
    -- this insert then succeeds instead of raising.
    """
    savepoint = db_session.begin_nested()
    try:
        with pytest.raises(IntegrityError) as excinfo:
            db_session.execute(
                text(_valid_cp_row_sql()), _valid_cp_row_params(**overrides)
            )
            db_session.flush()
        assert constraint in str(excinfo.value), (
            f"refused by something other than {constraint}: {excinfo.value}"
        )
    finally:
        savepoint.rollback()


def test_cp_kind_requires_j_mol_k_unit(db_session):
    savepoint = db_session.begin_nested()
    try:
        with pytest.raises(IntegrityError) as excinfo:
            db_session.execute(
                text(
                    "INSERT INTO molecular_property_observation"
                    " (scientific_origin, property_kind, scalar_value,"
                    "  scalar_unit, temperature_k)"
                    " VALUES ('experimental', 'heat_capacity_cp', 29.1,"
                    "  'J/K/mol', 298.15)"
                )
            )
            db_session.flush()
        assert "ck_mpo_heat_capacity_cp_unit_j_mol_k" in str(excinfo.value)
    finally:
        savepoint.rollback()


def test_cp_kind_requires_temperature(db_session):
    savepoint = db_session.begin_nested()
    try:
        with pytest.raises(IntegrityError) as excinfo:
            db_session.execute(
                text(
                    "INSERT INTO molecular_property_observation"
                    " (scientific_origin, property_kind, scalar_value,"
                    "  scalar_unit)"
                    " VALUES ('experimental', 'heat_capacity_cp', 29.1,"
                    "  'J/mol/K')"
                )
            )
            db_session.flush()
        assert "ck_mpo_heat_capacity_cp_requires_temperature" in str(excinfo.value)
    finally:
        savepoint.rollback()


def test_cp_uncertainty_value_without_kind_is_refused(db_session):
    savepoint = db_session.begin_nested()
    try:
        with pytest.raises(IntegrityError) as excinfo:
            db_session.execute(
                text(_valid_cp_row_sql()),
                _valid_cp_row_params(
                    uncertainty_kind=None, uncertainty_coverage_factor=None,
                    uncertainty_level_of_confidence_pct=None,
                ),
            )
            db_session.flush()
        assert "ck_mpo_uncertainty_kind_iff_value" in str(excinfo.value)
    finally:
        savepoint.rollback()


def test_cp_uncertainty_kind_without_value_is_refused(db_session):
    savepoint = db_session.begin_nested()
    try:
        with pytest.raises(IntegrityError) as excinfo:
            db_session.execute(
                text(_valid_cp_row_sql()),
                _valid_cp_row_params(
                    scalar_uncertainty=None, uncertainty_coverage_factor=None,
                    uncertainty_level_of_confidence_pct=None,
                ),
            )
            db_session.flush()
        assert "ck_mpo_uncertainty_kind_iff_value" in str(excinfo.value)
    finally:
        savepoint.rollback()


def test_cccbdb_style_row_with_uncertainty_and_no_kind_survives(db_session):
    """The decision this revision takes on the iff-CHECK versus existing
    data: ``ck_mpo_uncertainty_kind_iff_value`` is scoped to
    ``property_kind = 'heat_capacity_cp'`` only. A CCCBDB-shaped row --
    any other ``property_kind``, a bare ``scalar_uncertainty``, no
    ``uncertainty_kind`` -- is exactly the shape the live CCCBDB importer
    (``app/importers/cccbdb/form_payload_builder.py``) writes today, and
    must keep inserting cleanly.

    *Mutation*: widen the CHECK to apply to every row (drop the
    ``property_kind <> 'heat_capacity_cp' OR`` clause) -- this insert
    then raises ``IntegrityError`` instead of succeeding.
    """
    savepoint = db_session.begin_nested()
    try:
        row_id = db_session.execute(
            text(
                "INSERT INTO molecular_property_observation"
                " (scientific_origin, property_kind, scalar_value,"
                "  scalar_unit, scalar_uncertainty)"
                " VALUES ('experimental', 'ionization_energy', 9.0, 'eV', 0.1)"
                " RETURNING id"
            )
        ).scalar_one()
        db_session.flush()
        uncertainty_kind = db_session.execute(
            text(
                "SELECT uncertainty_kind FROM molecular_property_observation"
                " WHERE id = :id"
            ),
            {"id": row_id},
        ).scalar_one()
        assert uncertainty_kind is None
    finally:
        savepoint.rollback()


# ---------------------------------------------------------------------------
# external_source / external_source_record
# ---------------------------------------------------------------------------


@pytest.fixture
def external_source_id(db_session) -> int:
    suffix = uuid4().hex[:8]
    return db_session.execute(
        text(
            "INSERT INTO external_source (source_name, source_release)"
            " VALUES (:name, 'test-release') RETURNING id"
        ),
        {"name": f"Test Source {suffix}"},
    ).scalar_one()


def _valid_record_sql() -> str:
    return (
        "INSERT INTO external_source_record"
        " (external_source_id, record_kind, source_uri, source_record_key,"
        "  retrieved_at, http_status, content_sha256, content_length,"
        "  raw_uri, parser_name, parser_version, mapping_version)"
        " VALUES"
        " (:external_source_id, 'thermoml_article', 'https://example.org/x',"
        "  :source_record_key, now(), :http_status, :content_sha256,"
        "  :content_length, 'raw://x', 'thermoml', :parser_version,"
        "  :mapping_version)"
        " RETURNING id"
    )


def _valid_record_params(external_source_id: int, **overrides) -> dict:
    suffix = uuid4().hex[:8]
    params = {
        "external_source_id": external_source_id,
        "source_record_key": f"10.1000/x#Compound[{suffix}]",
        "http_status": 200,
        "content_sha256": "a" * 64,
        "content_length": 1024,
        "parser_version": "1.0.0",
        "mapping_version": "mapping-v1",
    }
    params.update(overrides)
    return params


def test_valid_custody_record_is_accepted(db_session, external_source_id):
    savepoint = db_session.begin_nested()
    try:
        db_session.execute(
            text(_valid_record_sql()), _valid_record_params(external_source_id)
        )
        db_session.flush()
    finally:
        savepoint.rollback()


@pytest.mark.parametrize(
    "overrides,constraint",
    [
        pytest.param(
            {"content_sha256": "not-hex"},
            "ck_external_source_record_content_sha256_hex",
            id="sha256_not_hex",
        ),
        pytest.param(
            {"content_sha256": "A" * 64},
            "ck_external_source_record_content_sha256_hex",
            id="sha256_uppercase",
        ),
        pytest.param(
            {"content_length": -1},
            "ck_external_source_record_content_length_ge_0",
            id="content_length_negative",
        ),
        pytest.param(
            {"http_status": 999},
            "ck_external_source_record_http_status_range",
            id="http_status_out_of_range",
        ),
    ],
)
def test_a_custody_record_violating_one_check_is_refused(
    db_session, external_source_id, overrides, constraint
):
    """*Mutation*: remove the named CHECK from the model AND the migration
    -- this insert then succeeds instead of raising.
    """
    savepoint = db_session.begin_nested()
    try:
        with pytest.raises(IntegrityError) as excinfo:
            db_session.execute(
                text(_valid_record_sql()),
                _valid_record_params(external_source_id, **overrides),
            )
            db_session.flush()
        assert constraint in str(excinfo.value)
    finally:
        savepoint.rollback()


def test_custody_unique_constraint_rejects_an_exact_duplicate(
    db_session, external_source_id
):
    savepoint = db_session.begin_nested()
    try:
        params = _valid_record_params(external_source_id)
        db_session.execute(text(_valid_record_sql()), params)
        db_session.flush()
        with pytest.raises(IntegrityError) as excinfo:
            db_session.execute(text(_valid_record_sql()), dict(params))
            db_session.flush()
        assert "uq_external_source_record_identity" in str(excinfo.value)
    finally:
        savepoint.rollback()


@pytest.mark.parametrize("column", ["parser_version", "mapping_version", "content_sha256", "source_record_key"])
def test_custody_unique_constraint_still_distinguishes_rows_by_each_column(
    db_session, external_source_id, column
):
    """Every column named in ``uq_external_source_record_identity`` still
    participates: two rows differing ONLY by ``column`` must both insert.

    *Mutation*: drop ``column`` from the UniqueConstraint's column list (in
    the model AND the migration) -- the two rows then collide as an exact
    duplicate and the second insert raises instead of succeeding.
    """
    savepoint = db_session.begin_nested()
    try:
        base = _valid_record_params(external_source_id)
        db_session.execute(text(_valid_record_sql()), base)
        db_session.flush()

        distinct = dict(base)
        if column == "content_sha256":
            distinct[column] = "b" * 64
        elif column == "source_record_key":
            distinct[column] = base[column] + "-2"
        else:
            distinct[column] = base[column] + "-2"

        db_session.execute(text(_valid_record_sql()), distinct)
        db_session.flush()

        count = db_session.execute(
            text(
                "SELECT count(*) FROM external_source_record"
                " WHERE external_source_id = :eid"
            ),
            {"eid": external_source_id},
        ).scalar_one()
        assert count == 2
    finally:
        savepoint.rollback()


def test_custody_unique_constraint_distinguishes_rows_by_external_source_id(
    db_session, external_source_id
):
    """Same as the parametrized test above, for the FK column specifically
    (it can't share that helper: it needs a second ``external_source``
    row, not just a different string).

    *Mutation*: drop ``external_source_id`` from the UniqueConstraint's
    column list -- two otherwise-identical rows citing different sources
    then collide and the second insert raises instead of succeeding.
    """
    savepoint = db_session.begin_nested()
    try:
        other_source_id = db_session.execute(
            text(
                "INSERT INTO external_source (source_name, source_release)"
                " VALUES ('Other Test Source', 'test-release')"
                " RETURNING id"
            )
        ).scalar_one()

        base = _valid_record_params(external_source_id)
        db_session.execute(text(_valid_record_sql()), base)
        db_session.flush()

        other = dict(base)
        other["external_source_id"] = other_source_id
        db_session.execute(text(_valid_record_sql()), other)
        db_session.flush()

        count = db_session.execute(
            text(
                "SELECT count(*) FROM external_source_record"
                " WHERE source_record_key = :key"
            ),
            {"key": base["source_record_key"]},
        ).scalar_one()
        assert count == 2
    finally:
        savepoint.rollback()
