from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.common import (
    MoleculeKind,
    ReactionRole,
    SpeciesEntryStateKind,
    StationaryPointKind,
    StereoKind,
)
from app.db.models.reaction import (
    ChemReaction,
    ReactionEntry,
    ReactionEntryStructureParticipant,
    ReactionParticipant,
)
from app.db.models.species import ConformerGroup, Species, SpeciesEntry
from app.schemas.workflows.conformer_upload import ConformerUploadRequest
from app.schemas.workflows.reaction_upload import ReactionUploadRequest
from app.workflows.conformer import persist_conformer_upload
from app.workflows.reaction import persist_reaction_upload


def _make_species_entry(
    session: Session,
    *,
    smiles: str,
    inchi_key: str,
    charge: int,
    multiplicity: int,
) -> SpeciesEntry:
    species = Species(
        kind=MoleculeKind.molecule,
        smiles=smiles,
        inchi_key=inchi_key,
        charge=charge,
        multiplicity=multiplicity,
        stereo_kind=StereoKind.achiral,
    )
    session.add(species)
    session.flush()

    species_entry = SpeciesEntry(
        species_id=species.id,
        kind=StationaryPointKind.minimum,
        electronic_state_kind=SpeciesEntryStateKind.ground,
    )
    session.add(species_entry)
    session.flush()
    return species_entry


def _conformer_request(
    *,
    smiles: str,
    charge: int,
    multiplicity: int,
    xyz_text: str,
    label: str,
) -> ConformerUploadRequest:
    return ConformerUploadRequest(
        species_entry={
            "smiles": smiles,
            "charge": charge,
            "multiplicity": multiplicity,
        },
        geometry={"xyz_text": xyz_text},
        calculation={
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        label=label,
    )


def test_persist_reaction_upload_creates_graph_and_entry_layers(db_engine) -> None:
    request = ReactionUploadRequest(
        reversible=False,
        reactants=[
            {
                "species_entry": {
                    "smiles": "[H]",
                    "charge": 0,
                    "multiplicity": 2,
                },
                "note": "first H",
            },
            {
                "species_entry": {
                    "smiles": "[H]",
                    "charge": 0,
                    "multiplicity": 2,
                },
                "note": "second H",
            },
        ],
        products=[
            {
                "species_entry": {
                    "smiles": "[H][H]",
                    "charge": 0,
                    "multiplicity": 1,
                },
                "note": "hydrogen product",
            }
        ],
    )

    with Session(db_engine) as session:
        with session.begin():
            reaction_entry = persist_reaction_upload(session, request)

            stored_entry = session.get(ReactionEntry, reaction_entry.id)
            assert stored_entry is not None

            chem_reaction = session.get(ChemReaction, stored_entry.reaction_id)
            assert chem_reaction is not None
            assert chem_reaction.stoichiometry_hash is not None

            graph_participants = session.scalars(
                select(ReactionParticipant).where(
                    ReactionParticipant.reaction_id == chem_reaction.id
                )
            ).all()
            assert len(graph_participants) == 2

            reactant_summary = next(
                participant
                for participant in graph_participants
                if participant.role == ReactionRole.reactant
            )
            assert reactant_summary.stoichiometry == 2

            structured_participants = session.scalars(
                select(ReactionEntryStructureParticipant).where(
                    ReactionEntryStructureParticipant.reaction_entry_id
                    == stored_entry.id
                )
            ).all()
            assert len(structured_participants) == 3

            reactant_slots = [
                participant
                for participant in structured_participants
                if participant.role == ReactionRole.reactant
            ]
            product_slots = [
                participant
                for participant in structured_participants
                if participant.role == ReactionRole.product
            ]
            assert [slot.participant_index for slot in reactant_slots] == [1, 2]
            assert [slot.participant_index for slot in product_slots] == [1]
            assert reactant_slots[0].note == "first H"
            assert reactant_slots[1].note == "second H"


def test_persist_reaction_upload_reuses_graph_layer_for_matching_submission(
    db_engine,
) -> None:
    with Session(db_engine) as session:
        with session.begin():
            reactant_entry = _make_species_entry(
                session,
                smiles="[He]",
                inchi_key="REACTIONUPLD000000000000001",
                charge=0,
                multiplicity=1,
            )
            product_entry = _make_species_entry(
                session,
                smiles="[He]",
                inchi_key="REACTIONUPLD000000000000002",
                charge=0,
                multiplicity=1,
            )

            request = ReactionUploadRequest(
                reversible=True,
                reactants=[{"species_entry_id": reactant_entry.id}],
                products=[{"species_entry_id": product_entry.id}],
            )

            first_entry = persist_reaction_upload(session, request)
            second_entry = persist_reaction_upload(session, request)

            assert first_entry.reaction_id == second_entry.reaction_id
            assert first_entry.id != second_entry.id

            graph_participants = session.scalars(
                select(ReactionParticipant).where(
                    ReactionParticipant.reaction_id == first_entry.reaction_id
                )
            ).all()
            assert len(graph_participants) == 2


def test_persist_reaction_upload_reuses_species_entries_from_conformer_upload(
    db_engine,
) -> None:
    with Session(db_engine) as session:
        with session.begin():
            species_entry_count_before = session.scalar(
                select(func.count()).select_from(SpeciesEntry)
            )

            reactant_observation = persist_conformer_upload(
                session,
                _conformer_request(
                    smiles="[H]",
                    charge=0,
                    multiplicity=2,
                    xyz_text="1\nH atom\nH 0.0 0.0 0.0\n",
                    label="h-conf-a",
                ),
            ).observation
            product_observation = persist_conformer_upload(
                session,
                _conformer_request(
                    smiles="[H]",
                    charge=0,
                    multiplicity=2,
                    xyz_text="1\nH atom\nH 0.0 0.0 0.0\n",
                    label="h-conf-b",
                ),
            ).observation

            reactant_group = session.get(
                ConformerGroup, reactant_observation.conformer_group_id
            )
            product_group = session.get(
                ConformerGroup, product_observation.conformer_group_id
            )
            assert reactant_group is not None
            assert product_group is not None

            reaction_entry = persist_reaction_upload(
                session,
                ReactionUploadRequest(
                    reversible=False,
                    reactants=[{"species_entry_id": reactant_group.species_entry_id}],
                    products=[{"species_entry_id": product_group.species_entry_id}],
                ),
            )

            species_entry_count_after_conformer_upload = session.scalar(
                select(func.count()).select_from(SpeciesEntry)
            )
            structured_participants = session.scalars(
                select(ReactionEntryStructureParticipant).where(
                    ReactionEntryStructureParticipant.reaction_entry_id
                    == reaction_entry.id
                )
            ).all()
            species_entry_count_after_reaction_upload = session.scalar(
                select(func.count()).select_from(SpeciesEntry)
            )
            assert (
                species_entry_count_after_reaction_upload
                == species_entry_count_after_conformer_upload
            )
            assert (
                species_entry_count_after_conformer_upload >= species_entry_count_before
            )
            assert {
                participant.species_entry_id for participant in structured_participants
            } == {
                reactant_group.species_entry_id,
                product_group.species_entry_id,
            }


# ---------------------------------------------------------------------------
# Strict elemental-balance policy
# ---------------------------------------------------------------------------
#
# Ordinary reactions must be element-balanced at the shared reaction-resolution
# seam (``resolve_chem_reaction``). Pseudo-species are the only first-pass
# exception. See ``docs/strict-reaction-balance-policy-spec.md``.


def test_balanced_ordinary_reaction_persists(db_engine) -> None:
    """Balanced ordinary reactions must continue to upload successfully."""
    request = ReactionUploadRequest(
        reversible=False,
        reactants=[
            {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
            {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
        ],
        products=[
            {"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}},
        ],
    )

    with Session(db_engine) as session:
        with session.begin():
            reaction_entry = persist_reaction_upload(session, request)
            assert reaction_entry.id is not None
            assert session.get(ChemReaction, reaction_entry.reaction_id) is not None


def test_imbalanced_ordinary_reaction_is_rejected(db_engine) -> None:
    """Imbalanced ordinary reactions must fail with a stable error and
    leave no reaction rows persisted."""
    request = ReactionUploadRequest(
        reversible=False,
        reactants=[{"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}}],
        products=[{"species_entry": {"smiles": "[He]", "charge": 0, "multiplicity": 1}}],
    )

    with Session(db_engine) as session:
        with session.begin():
            chem_reaction_count_before = session.scalar(
                select(func.count()).select_from(ChemReaction)
            )
            with pytest.raises(ValueError, match="not element-balanced"):
                persist_reaction_upload(session, request)

            chem_reaction_count_after = session.scalar(
                select(func.count()).select_from(ChemReaction)
            )
            assert chem_reaction_count_after == chem_reaction_count_before


def test_pseudo_species_participant_skips_elemental_balance(db_engine) -> None:
    """A reaction participant with ``species.kind == pseudo`` exempts the
    reaction from strict elemental-balance rejection in this first pass."""
    with Session(db_engine) as session:
        with session.begin():
            ordinary_entry = _make_species_entry(
                session,
                smiles="[H]",
                inchi_key="PSEUDOBALANCE0000000000001",
                charge=0,
                multiplicity=2,
            )
            pseudo_species = Species(
                kind=MoleculeKind.pseudo,
                smiles="lumped_sink",
                inchi_key="PSEUDOBALANCE0000000000002",
                charge=0,
                multiplicity=1,
                stereo_kind=StereoKind.achiral,
            )
            session.add(pseudo_species)
            session.flush()
            pseudo_entry = SpeciesEntry(
                species_id=pseudo_species.id,
                kind=StationaryPointKind.minimum,
                electronic_state_kind=SpeciesEntryStateKind.ground,
            )
            session.add(pseudo_entry)
            session.flush()

            request = ReactionUploadRequest(
                reversible=False,
                reactants=[{"species_entry_id": ordinary_entry.id}],
                products=[{"species_entry_id": pseudo_entry.id}],
            )
            reaction_entry = persist_reaction_upload(session, request)
            assert reaction_entry.id is not None
