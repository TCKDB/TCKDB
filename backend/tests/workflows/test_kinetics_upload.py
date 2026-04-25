from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.kinetics import Kinetics
from app.db.models.literature import Literature
from app.db.models.reaction import ReactionEntryStructureParticipant
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.workflows.kinetics import persist_kinetics_upload


def _kinetics_request() -> KineticsUploadRequest:
    return KineticsUploadRequest(
        reaction={
            "reversible": False,
            "reactants": [
                {
                    "species_entry": {
                        "smiles": "[H]",
                        "charge": 0,
                        "multiplicity": 2,
                    }
                },
                {
                    "species_entry": {
                        "smiles": "[H]",
                        "charge": 0,
                        "multiplicity": 2,
                    }
                },
            ],
            "products": [
                {
                    "species_entry": {
                        "smiles": "[H][H]",
                        "charge": 0,
                        "multiplicity": 1,
                    }
                }
            ],
        },
        scientific_origin="computed",
        model_kind="modified_arrhenius",
        software_release={"name": "gaussian", "version": "09", "revision": "D.01"},
        workflow_tool_release={"name": "ARC", "version": "1.0.0"},
        literature={
            "doi": "10.1000/example.doi",
            "title": "Fallback title if DOI lookup is unavailable",
        },
        a=1.23e12,
        a_units="cm3_mol_s",
        n=0.5,
        reported_ea=12.3,
        reported_ea_units="kj_mol",
        tmin_k=300.0,
        tmax_k=2000.0,
        degeneracy=2.0,
        tunneling_model="eckart",
        note="upload note",
    )


def test_persist_kinetics_upload_resolves_reaction_and_provenance(
    db_engine,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {
            "title": "Hydrogen recombination kinetics",
            "container-title": ["J. Chem. Phys."],
            "issued": 2024,
            "volume": "123",
            "issue": "4",
            "page": "100-110",
            "publisher": "AIP",
            "URL": f"https://doi.org/{doi}",
        },
    )

    with Session(db_engine) as session, session.begin():
        session.add(AppUser(id=7, username="kinetics_tester"))
        session.flush()
        kinetics = persist_kinetics_upload(session, _kinetics_request(), created_by=7)

        assert kinetics.id is not None
        assert kinetics.reaction_entry_id is not None
        assert kinetics.created_by == 7
        assert kinetics.software_release is not None
        assert kinetics.software_release.software.name == "Gaussian"
        assert kinetics.workflow_tool_release is not None
        assert kinetics.workflow_tool_release.workflow_tool.name == "ARC"
        assert kinetics.literature is not None
        assert kinetics.literature.title == "Hydrogen recombination kinetics"

        participants = session.scalars(
            select(ReactionEntryStructureParticipant).where(
                ReactionEntryStructureParticipant.reaction_entry_id
                == kinetics.reaction_entry_id
            )
        ).all()
        assert len(participants) == 3


def test_persist_kinetics_upload_reuses_existing_literature_by_doi(
    db_engine,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {"title": "Shared DOI title", "URL": f"https://doi.org/{doi}"},
    )

    request = _kinetics_request()

    with Session(db_engine) as session, session.begin():
        before_kinetics = len(session.scalars(select(Kinetics)).all())
        first = persist_kinetics_upload(session, request)
        after_first_literature = len(session.scalars(select(Literature)).all())
        second = persist_kinetics_upload(session, request)
        after_second_literature = len(session.scalars(select(Literature)).all())

        assert first.literature_id == second.literature_id
        # Second call must not create a duplicate Literature row
        assert after_second_literature == after_first_literature

        kinetics_rows = session.scalars(select(Kinetics)).all()
        assert len(kinetics_rows) == before_kinetics + 2
