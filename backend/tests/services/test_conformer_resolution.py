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


def test_resolve_conformer_group_takes_advisory_xact_lock(db_conn) -> None:
    """resolve_conformer_group must acquire a transaction-scoped advisory lock.

    The lock serializes basin resolution per species_entry to close the
    read-then-create race that could otherwise fork one basin into duplicate
    conformer_group rows. Here we assert the guard is present by capturing the
    SQL emitted during a resolve call and checking for pg_advisory_xact_lock.
    """
    with Session(db_conn) as session:
        with session.begin():
            # Set up a real species_entry (and its first conformer group) so the
            # FK targets exist and the resolve below runs against rows that are
            # visible to it — flushed within this transaction, not committed.
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


# ---------------------------------------------------------------------------
# The live upload path and the recompute decide "same basin" the same way
# ---------------------------------------------------------------------------


def _molecule_request(smiles: str, atoms) -> ConformerUploadRequest:
    lines = [str(len(atoms)), "conformer"] + [f"{s} {x:.6f} {y:.6f} {z:.6f}" for s, x, y, z in atoms]
    return ConformerUploadRequest(
        species_entry={"smiles": smiles, "charge": 0, "multiplicity": 1},
        geometry={"xyz_text": "\n".join(lines)},
        calculation={
            "type": "opt",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
    )


def _upload_groups(session, smiles: str, geometries) -> list[int]:
    """Upload each geometry through the real workflow; return the group id each observation landed in."""
    from app.db.models.species import ConformerObservation

    for atoms in geometries:
        persist_conformer_upload(session, _molecule_request(smiles, atoms))
    session.flush()
    return [
        o.conformer_group_id
        for o in session.scalars(select(ConformerObservation).order_by(ConformerObservation.id))
    ]


def test_upload_keeps_a_planar_aryl_wobble_in_one_basin_and_separates_ethanol_rotamers(db_conn) -> None:
    from tests.services.test_basin_matching import _ARYL, with_dihedral

    nitro = "c1ccccc1[N+](=O)[O-]"
    ethanol = "CCO"
    with Session(db_conn) as session, session.begin():
        wobble = _upload_groups(
            session, nitro, [with_dihedral(nitro, _ARYL, 0.5), with_dihedral(nitro, _ARYL, -0.5)]
        )
        assert wobble[0] == wobble[1]

    with Session(db_conn) as session, session.begin():
        rotamers = _upload_groups(
            session,
            ethanol,
            [
                with_dihedral(ethanol, "[#1][OX2][CH2][CH3]", 180.0, seed=5),
                with_dihedral(ethanol, "[#1][OX2][CH2][CH3]", 60.0, seed=5),
                with_dihedral(ethanol, "[#1][OX2][CH2][CH3]", 170.0, seed=5),
            ],
        )[-3:]
        assert rotamers[0] != rotamers[1]
        assert rotamers[2] == rotamers[0]


def test_recomputing_what_the_upload_path_just_wrote_changes_nothing(db_conn) -> None:
    """The geometry reassignment recovers from an observation is the one the upload used, so the two agree."""
    from app.services.conformer_reassignment import reassign_conformer_groups
    from tests.services.test_basin_matching import with_dihedral

    ethanol = "CCO"
    with Session(db_conn) as session, session.begin():
        _upload_groups(
            session,
            ethanol,
            [with_dihedral(ethanol, "[#1][OX2][CH2][CH3]", d, seed=5) for d in (180.0, 60.0, 175.0)],
        )
        report = reassign_conformer_groups(session, repair_revision="test-recompute")
        assert report.observations_unresolved == 0
        assert report.observations_moved == 0 and report.groups_created == 0
        assert report.groups_emptied_kept == 0 and report.groups_review_reset == 0
