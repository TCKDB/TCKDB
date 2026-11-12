"""Database coverage for the molecular-property observation contracts.

An experimental ideal-gas Cp(T) point is expressed with ``property_kind`` ``heat_capacity_cp``, a fixed-unit
``scalar_unit``, a pressure, a state basis and a stated uncertainty kind; the source-custody table keeps its own
uniqueness. This module proves the CHECK constraints, tables and enum values the wire contract relies on exist and are
enforced by PostgreSQL, not only by Pydantic.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

import app.db.models  # noqa: F401 -- register the mapper graph
from app.db.base import Base

REPO_ROOT = Path(__file__).resolve().parents[2]


#: The final DB constraint names on ``molecular_property_observation``, as
#: declared via ``conv()`` on the model / ``op.f()`` in the migration.
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
        "  raw_uri, parser_name, parser_version, mapping_name, mapping_version)"
        " VALUES"
        " (:external_source_id, 'thermoml_article', 'https://example.org/x',"
        "  :source_record_key, now(), :http_status, :content_sha256,"
        "  :content_length, 'raw://x', :parser_name, :parser_version,"
        "  :mapping_name, :mapping_version)"
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
        "parser_name": "thermoml_cp",
        "parser_version": "1",
        "mapping_name": "thermoml_cp_mapping",
        "mapping_version": "1",
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
