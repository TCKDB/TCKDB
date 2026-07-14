"""Tests for conformer group resolution service (app.services.conformer_resolution).

Focus: proving the per-species_entry advisory lock that serializes basin
resolution is actually wired into resolve_conformer_group. See the lock comment
in the service for the read-then-create race it guards against.
"""

from __future__ import annotations

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.db.models.species import SpeciesEntry
from app.schemas.workflows.conformer_upload import ConformerUploadRequest
from app.services.conformer_resolution import resolve_conformer_group
from app.workflows.conformer import persist_conformer_upload


def _hydrogen_request(*, label: str | None = None) -> ConformerUploadRequest:
    return ConformerUploadRequest(
        species_entry={
            "smiles": "[H]",
            "charge": 0,
            "multiplicity": 2,
        },
        geometry={
            "xyz_text": "1\nH atom\nH 0.0 0.0 0.0",
        },
        calculation={
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        label=label,
        note="uploaded conformer",
    )


def test_resolve_conformer_group_takes_advisory_xact_lock(db_engine) -> None:
    """resolve_conformer_group must acquire a transaction-scoped advisory lock.

    The lock serializes basin resolution per species_entry to close the
    read-then-create race that could otherwise fork one basin into duplicate
    conformer_group rows. Here we assert the guard is present by capturing the
    SQL emitted during a resolve call and checking for pg_advisory_xact_lock.
    """
    with Session(db_engine) as session:
        with session.begin():
            # Set up a real species_entry (and its first conformer group) so the
            # FK targets exist and the resolve below runs against committed rows.
            persist_conformer_upload(session, _hydrogen_request(label="conf-a"))
            session.flush()

            species_entry = session.scalar(select(SpeciesEntry))
            assert species_entry is not None

            captured: list[str] = []

            def _capture(conn, cursor, statement, parameters, context, executemany):
                captured.append(statement)

            event.listen(session.bind, "before_cursor_execute", _capture)
            try:
                resolve_conformer_group(
                    session,
                    species_entry,
                    label="conf-b",
                    smiles="[H]",
                    xyz_atoms=(("H", 0.0, 0.0, 0.0),),
                )
            finally:
                event.remove(session.bind, "before_cursor_execute", _capture)

    assert any("pg_advisory_xact_lock" in stmt for stmt in captured), (
        "resolve_conformer_group did not issue a pg_advisory_xact_lock; "
        f"captured statements: {captured}"
    )
