"""``kinetics.energy_level_of_theory_id`` is a deferrable, indexed foreign key to ``level_of_theory``."""

from __future__ import annotations

from sqlalchemy import text

_COLUMN = "energy_level_of_theory_id"
_FK = "fk_kinetics_energy_level_of_theory_id_level_of_theory"
_INDEX = "ix_kinetics_energy_level_of_theory_id"


def _count(db_session, query: str, **params) -> int:
    return db_session.scalar(text(query), params)


def test_the_column_is_a_deferrable_indexed_foreign_key_to_level_of_theory(db_session):
    assert _count(db_session, "SELECT count(*) FROM pg_constraint WHERE conname = :n", n=_FK) == 1
    deferrable = db_session.scalar(
        text("SELECT condeferrable AND NOT condeferred FROM pg_constraint WHERE conname = :n"), {"n": _FK}
    )
    assert deferrable is True
    assert _count(db_session, "SELECT count(*) FROM pg_indexes WHERE indexname = :n", n=_INDEX) == 1
    assert db_session.scalar(
        text("SELECT is_nullable FROM information_schema.columns WHERE table_name = 'kinetics' AND column_name = :c"),
        {"c": _COLUMN},
    ) == "YES"
