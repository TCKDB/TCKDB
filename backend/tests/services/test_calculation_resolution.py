from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.software import Software, SoftwareRelease
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.fragments.calculation import CalculationCreateRequest
from app.services.calculation_resolution import (
    persist_calculation,
    resolve_calculation_create_request,
)


def _create_species(connection, *, inchi_key: str, smiles: str = "[H]") -> int:
    return connection.execute(
        text("""
            INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind)
            VALUES ('molecule', :smiles, :inchi_key, 0, 1, 'achiral')
            RETURNING id
            """),
        {"smiles": smiles, "inchi_key": inchi_key},
    ).scalar_one()


def _create_species_entry(connection, species_id: int) -> int:
    return connection.execute(
        text("""
            INSERT INTO species_entry (species_id)
            VALUES (:species_id)
            RETURNING id
            """),
        {"species_id": species_id},
    ).scalar_one()


_INCHI_COUNTER = 0


def _next_inchi_key(prefix: str) -> str:
    global _INCHI_COUNTER
    _INCHI_COUNTER += 1
    stem = f"{prefix}{_INCHI_COUNTER:0>21}"
    return stem[:27]


def test_resolve_calculation_create_request_creates_and_reuses_refs(db_engine) -> None:
    with Session(db_engine) as session:
        with session.begin():
            species_id = _create_species(
                session.connection(), inchi_key=_next_inchi_key("CALCRESOLVE")
            )
            species_entry_id = _create_species_entry(session.connection(), species_id)

            request = CalculationCreateRequest(
                type="sp",
                species_entry_id=species_entry_id,
                software_release={
                    "name": "gaussian",
                    "version": "16",
                    "revision": "C.01",
                },
                workflow_tool_release={
                    "name": "ARC",
                    "version": "1.0",
                    "git_commit": "abc123",
                },
                level_of_theory={
                    "method": "wB97X-D",
                    "basis": "def2-TZVP",
                },
            )

            first = resolve_calculation_create_request(session, request)
            second = resolve_calculation_create_request(session, request)

            assert first.software_release_id == second.software_release_id
            assert first.workflow_tool_release_id == second.workflow_tool_release_id
            assert first.lot_id == second.lot_id

            assert session.scalar(select(Software).where(Software.name == "Gaussian"))
            assert session.scalar(
                select(SoftwareRelease).where(
                    SoftwareRelease.id == first.software_release_id
                )
            )
            assert session.scalar(
                select(WorkflowTool).where(WorkflowTool.name == "ARC")
            )
            assert session.scalar(
                select(WorkflowToolRelease).where(
                    WorkflowToolRelease.id == first.workflow_tool_release_id
                )
            )
            assert session.scalar(
                select(LevelOfTheory).where(LevelOfTheory.id == first.lot_id)
            )


def test_persist_calculation_persists_calculation(db_engine) -> None:
    with Session(db_engine) as session:
        with session.begin():
            species_id = _create_species(
                session.connection(), inchi_key=_next_inchi_key("CALCCREATE")
            )
            species_entry_id = _create_species_entry(session.connection(), species_id)

            request = CalculationCreateRequest(
                type="freq",
                species_entry_id=species_entry_id,
                software_release={"name": "ORCA", "version": "5.0.4"},
                level_of_theory={"method": "B3LYP", "basis": "6-31G(d)"},
            )
            resolved = resolve_calculation_create_request(session, request)
            calculation = persist_calculation(session, resolved)

            stored = session.scalar(
                select(Calculation).where(Calculation.id == calculation.id)
            )

            assert stored is not None
            assert stored.type.value == "freq"
            assert stored.species_entry_id == species_entry_id
            assert stored.software_release_id == resolved.software_release_id
