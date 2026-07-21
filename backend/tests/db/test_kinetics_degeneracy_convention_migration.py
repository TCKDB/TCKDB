from __future__ import annotations

import math
import runpy
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.models.common import ScientificOriginKind
from app.db.models.kinetics import Kinetics
from app.db.models.reaction import ChemReaction, ReactionEntry

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "f2a4c6e8b0d1_add_kinetics_degeneracy_convention.py"
)

CONSTRAINT_NAME = "ck_kinetics_degeneracy_finite_positive"


def _make_kinetics(db_session, *, degeneracy: float | None = None) -> Kinetics:
    reaction = ChemReaction(
        reversible=True,
        stoichiometry_hash=uuid4().hex * 2,
    )
    db_session.add(reaction)
    db_session.flush()
    reaction_entry = ReactionEntry(reaction_id=reaction.id)
    db_session.add(reaction_entry)
    db_session.flush()
    kinetics = Kinetics(
        reaction_entry_id=reaction_entry.id,
        scientific_origin=ScientificOriginKind.computed,
        degeneracy=degeneracy,
    )
    db_session.add(kinetics)
    db_session.flush()
    return kinetics


def test_degeneracy_convention_migration_follows_current_head() -> None:
    source = MIGRATION.read_text()
    assert 'revision: str = "f2a4c6e8b0d1"' in source
    assert 'down_revision: Union[str, Sequence[str], None] = "e9a3c5f7b1d2"' in source
    assert 'server_default="unknown"' in source
    assert "nullable=False" in source


def test_degeneracy_convention_database_contract(db_session) -> None:
    column = db_session.execute(
        text(
            """
            SELECT is_nullable, column_default, udt_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'kinetics'
              AND column_name = 'degeneracy_convention'
            """
        )
    ).one()
    assert column.is_nullable == "NO"
    assert column.column_default == "'unknown'::kinetics_degeneracy_convention"
    assert column.udt_name == "kinetics_degeneracy_convention"

    labels = db_session.scalars(
        text(
            """
            SELECT enumlabel
            FROM pg_enum
            JOIN pg_type ON pg_type.oid = pg_enum.enumtypid
            WHERE pg_type.typname = 'kinetics_degeneracy_convention'
            ORDER BY enumsortorder
            """
        )
    ).all()
    assert labels == ["already_applied", "not_applied", "unknown"]


def test_degeneracy_constraint_is_present_in_database_and_metadata(db_session) -> None:
    database_name = db_session.scalar(
        text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'kinetics'::regclass AND conname = :name"
        ),
        {"name": CONSTRAINT_NAME},
    )
    metadata_names = {constraint.name for constraint in Kinetics.__table__.constraints}

    assert database_name == CONSTRAINT_NAME
    assert CONSTRAINT_NAME in metadata_names


@pytest.mark.parametrize(
    "value",
    [0.0, -1.0, math.nan, math.inf, -math.inf],
    ids=["zero", "negative", "nan", "positive-infinity", "negative-infinity"],
)
def test_database_rejects_invalid_raw_degeneracy(db_session, value: float) -> None:
    kinetics = _make_kinetics(db_session)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.execute(
                text("UPDATE kinetics SET degeneracy = :value WHERE id = :id"),
                {"value": value, "id": kinetics.id},
            )


@pytest.mark.parametrize("value", [None, 1.0e-12, 1.0, 2.5])
def test_database_accepts_null_or_finite_positive_raw_degeneracy(
    db_session,
    value: float | None,
) -> None:
    kinetics = _make_kinetics(db_session)

    db_session.execute(
        text("UPDATE kinetics SET degeneracy = :value WHERE id = :id"),
        {"value": value, "id": kinetics.id},
    )

    stored = db_session.scalar(
        text("SELECT degeneracy FROM kinetics WHERE id = :id"),
        {"id": kinetics.id},
    )
    assert stored == value


def test_migration_preflight_rejects_invalid_legacy_row(db_session) -> None:
    kinetics = _make_kinetics(db_session)
    db_session.execute(
        text(f"ALTER TABLE kinetics DROP CONSTRAINT {CONSTRAINT_NAME}")
    )
    db_session.execute(
        text(
            "UPDATE kinetics SET degeneracy = 'Infinity'::double precision "
            "WHERE id = :id"
        ),
        {"id": kinetics.id},
    )

    migration = runpy.run_path(str(MIGRATION))
    with pytest.raises(RuntimeError) as exc_info:
        migration["_preflight_legacy_degeneracy"](db_session.connection())

    message = str(exc_info.value)
    assert f"Cannot add {CONSTRAINT_NAME}: found 1 kinetics row(s)" in message
    assert "Set each invalid degeneracy to NULL or a finite positive value" in message
    assert "alembic upgrade head" in message
