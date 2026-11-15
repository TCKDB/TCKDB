"""Artifact ids behind a calculation come back in id order, whatever the table's physical order."""

from __future__ import annotations

from app.db.models.calculation import CalculationArtifact
from app.db.models.common import ArtifactKind
from app.services.record_review import _artifact_ids_for_calculations
from tests.services.scientific_read._factories import make_calculation, make_species, make_species_entry


def test_artifact_ids_are_in_id_order_after_a_row_is_rewritten(db_session):
    entry = make_species_entry(db_session, species=make_species(db_session, smiles="CCO"))
    calc = make_calculation(db_session, species_entry_id=entry.id)
    rows = [
        CalculationArtifact(
            calculation_id=calc.id,
            kind=ArtifactKind.output_log,
            uri=f"s3://bucket/{i}.log",
            sha256=str(i) * 64,
            bytes=1,
            filename=f"{i}.log",
        )
        for i in range(1, 4)
    ]
    db_session.add_all(rows)
    db_session.flush()
    ids = [row.id for row in rows]

    # Rewriting an indexed column moves the row in the table and its index; the ids must not follow.
    rows[0].sha256 = "f" * 64
    db_session.flush()

    assert _artifact_ids_for_calculations(db_session, [calc.id]) == ids
