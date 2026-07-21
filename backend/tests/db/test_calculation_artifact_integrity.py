"""Database invariants for content-addressed calculation artifacts."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.calculation import CalculationArtifact
from app.db.models.common import ArtifactKind
from tests.services.scientific_read._factories import (
    make_calculation,
    make_species,
    make_species_entry,
    next_inchi_key,
)


@pytest.mark.parametrize(
    ("sha256", "byte_count"),
    [
        (None, 1),
        ("A" * 64, 1),
        ("a" * 63, 1),
        ("a" * 64, None),
        ("a" * 64, 0),
        ("a" * 64, -1),
    ],
)
def test_artifact_integrity_metadata_is_database_enforced(
    db_session, sha256, byte_count
) -> None:
    species = make_species(db_session, inchi_key=next_inchi_key("ARTDB"))
    entry = make_species_entry(db_session, species)
    calculation = make_calculation(db_session, species_entry_id=entry.id)

    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(
            CalculationArtifact(
                calculation_id=calculation.id,
                kind=ArtifactKind.input,
                uri="s3://test/invalid",
                sha256=sha256,
                bytes=byte_count,
                filename="invalid.gjf",
            )
        )
        db_session.flush()


def test_artifact_integrity_metadata_accepts_valid_content_address(db_session) -> None:
    species = make_species(db_session, inchi_key=next_inchi_key("ARTOK"))
    entry = make_species_entry(db_session, species)
    calculation = make_calculation(db_session, species_entry_id=entry.id)
    artifact = CalculationArtifact(
        calculation_id=calculation.id,
        kind=ArtifactKind.input,
        uri="s3://test/valid",
        sha256="a" * 64,
        bytes=1,
        filename="valid.gjf",
    )
    db_session.add(artifact)
    db_session.flush()

    assert artifact.id is not None
