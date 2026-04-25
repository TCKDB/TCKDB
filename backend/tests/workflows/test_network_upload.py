from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.network import NetworkReaction, NetworkSpecies
from app.db.models.reaction import ReactionEntry
from app.schemas.workflows.network_upload import NetworkUploadRequest
from app.workflows.network import persist_network_upload


def _network_request() -> NetworkUploadRequest:
    return NetworkUploadRequest(
        name="Propyl + O2 network",
        description="Imported from Arkane",
        literature={
            "doi": "10.1000/network.example",
            "title": "Fallback network title",
        },
        software_release={"name": "gaussian", "version": "09", "revision": "D.01"},
        workflow_tool_release={"name": "Arkane", "version": "1.0.0"},
        species_links=[
            {
                "species_entry": {
                    "smiles": "CC[CH2]",
                    "charge": 0,
                    "multiplicity": 2,
                },
                "role": "well",
            },
            {
                "species_entry": {
                    "smiles": "[He]",
                    "charge": 0,
                    "multiplicity": 1,
                },
                "role": "bath_gas",
            },
        ],
        reactions=[
            {
                "reaction": {
                    "reversible": False,
                    "reactants": [
                        {
                            "species_entry": {
                                "smiles": "CC[CH2]",
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
                                "smiles": "CCC",
                                "charge": 0,
                                "multiplicity": 1,
                            }
                        }
                    ],
                }
            }
        ],
    )


def test_persist_network_upload_resolves_links_and_provenance(
    db_engine,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {
            "title": "Arkane network import",
            "container-title": ["Combust. Flame"],
            "issued": 2024,
            "publisher": "Elsevier",
            "URL": f"https://doi.org/{doi}",
        },
    )

    with Session(db_engine) as session, session.begin():
        session.add(AppUser(id=9, username="network_tester"))
        session.flush()

        network = persist_network_upload(session, _network_request(), created_by=9)

        assert network.id is not None
        assert network.created_by == 9
        assert network.literature is not None
        assert network.literature.title == "Arkane network import"
        assert network.software_release is not None
        assert network.software_release.software.name == "Gaussian"
        assert network.workflow_tool_release is not None
        assert network.workflow_tool_release.workflow_tool.name == "Arkane"

        species_links = session.scalars(
            select(NetworkSpecies).where(NetworkSpecies.network_id == network.id)
        ).all()
        assert len(species_links) == 2

        reaction_links = session.scalars(
            select(NetworkReaction).where(NetworkReaction.network_id == network.id)
        ).all()
        assert len(reaction_links) == 1

        reaction_entry = session.get(ReactionEntry, reaction_links[0].reaction_entry_id)
        assert reaction_entry is not None


def test_persist_network_upload_creates_species_and_reaction_entries_without_user_ids(
    db_engine,
) -> None:
    with Session(db_engine) as session, session.begin():
        reactions_before = len(session.scalars(select(ReactionEntry)).all())

        network = persist_network_upload(session, _network_request())

        assert network.id is not None
        assert len(session.scalars(select(ReactionEntry)).all()) == reactions_before + 1
        assert (
            len(
                session.scalars(
                    select(NetworkSpecies).where(
                        NetworkSpecies.network_id == network.id
                    )
                ).all()
            )
            == 2
        )
        assert (
            len(
                session.scalars(
                    select(NetworkReaction).where(
                        NetworkReaction.network_id == network.id
                    )
                ).all()
            )
            == 1
        )
