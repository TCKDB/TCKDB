"""Regression tests for the canonical StereoKind enum.

These tests lock the Python enum, the ORM column binding, the database
enum type, and the schema layer together so any future drift (e.g. an
inline literal enum list on Species.stereo_kind, or a missing value in
the PostgreSQL type) fails immediately.

See docs/stereo-kind-drift-spec.md for the rationale.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import Enum as SAEnum
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.models.common import StereoKind
from app.db.models.species import Species
from app.schemas.entities.species import SpeciesCreate

CANONICAL_VALUES = ("unspecified", "achiral", "enantiomer", "diastereomer", "ez_isomer")


def test_stereo_kind_python_enum_parity() -> None:
    """The canonical five-value set is the single source of truth."""
    assert {m.value for m in StereoKind} == set(CANONICAL_VALUES)
    assert {m.name for m in StereoKind} == set(CANONICAL_VALUES)


def test_species_stereo_kind_column_uses_shared_enum() -> None:
    """Species.stereo_kind must bind to the shared StereoKind class,
    not an inline list of strings. If this regresses, someone replaced
    SAEnum(StereoKind, ...) with SAEnum('achiral', 'enantiomer', ...)."""
    column_type = Species.__table__.c.stereo_kind.type
    assert isinstance(column_type, SAEnum)
    assert column_type.enum_class is StereoKind
    assert set(column_type.enums) == set(CANONICAL_VALUES)


def test_species_schema_accepts_all_canonical_stereo_kinds() -> None:
    for value in CANONICAL_VALUES:
        schema = SpeciesCreate(
            kind="molecule",
            smiles="C",
            inchi_key="A" * 27,
            charge=0,
            multiplicity=1,
            stereo_kind=value,
        )
        assert schema.stereo_kind == StereoKind(value)


def test_species_schema_rejects_unknown_stereo_kind() -> None:
    with pytest.raises(ValidationError):
        SpeciesCreate(
            kind="molecule",
            smiles="C",
            inchi_key="A" * 27,
            charge=0,
            multiplicity=1,
            stereo_kind="racemic",
        )


@pytest.mark.parametrize("value", CANONICAL_VALUES)
def test_species_stereo_kind_round_trip(db_conn, value: str) -> None:
    """Every canonical StereoKind must round-trip through the Postgres enum."""
    inchi_key = f"STEREO{value:<21}"[:27]
    with Session(bind=db_conn, expire_on_commit=False) as session:
        species = Species(
            kind="molecule",
            smiles="C",
            inchi_key=inchi_key,
            charge=0,
            multiplicity=1,
            stereo_kind=StereoKind(value),
        )
        session.add(species)
        session.flush()
        species_id = species.id

        session.expire_all()
        reloaded = session.get(Species, species_id)
        assert reloaded is not None
        assert reloaded.stereo_kind == StereoKind(value)
        assert reloaded.stereo_kind.value == value


def test_species_stereo_kind_db_rejects_unknown_value(db_conn) -> None:
    """Postgres enum must reject values outside the canonical set."""
    savepoint = db_conn.begin_nested()
    try:
        with pytest.raises(Exception):
            db_conn.execute(
                text(
                    """
                    INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind)
                    VALUES ('molecule', 'C', :inchi_key, 0, 1, 'racemic')
                    """
                ),
                {"inchi_key": "BADSTEREO" + "X" * 18},
            )
    finally:
        savepoint.rollback()
