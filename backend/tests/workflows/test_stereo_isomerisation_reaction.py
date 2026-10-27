"""A cis-trans isomerisation is a reaction between two entries of one species (DR-0038).

Since the species key is the constitution, ``Z-2-butene -> E-2-butene`` has the same species on
both sides. The graph reaction (``chem_reaction``) must accept that, whether the two entries are
written flat with geometries or with stereo marks; the *entry-level* reaction must still refuse
the null reaction, the same entry on both sides.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.species import Species
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.workflows.reaction_upload import ReactionUploadRequest
from app.services.species_resolution import resolve_species_entry
from app.workflows.reaction import persist_reaction_upload
from tests.services.test_stereo_descriptor import embed_xyz

W_REACTION_ENTRY_NULL_REACTION = "reaction_entry_null_reaction"

_TRANS_XYZ = embed_xyz("C/C=C/C")
_CIS_XYZ = embed_xyz("C/C=C\\C")


def _flat_entry(session: Session, xyz: str):
    """``CC=CC`` written without marks; only the geometry says which isomer."""
    entry = resolve_species_entry(
        session,
        SpeciesEntryIdentityPayload(smiles="CC=CC", charge=0, multiplicity=1),
        geometry=GeometryPayload(xyz_text=xyz),
    )
    session.flush()
    return entry


def _marked(smiles: str) -> dict:
    return {"species_entry": {"smiles": smiles, "charge": 0, "multiplicity": 1}}


def _by_id(entry) -> dict:
    return {"species_entry_id": entry.id}


def _request(reactants: list[dict], products: list[dict], *, reversible: bool = False):
    return ReactionUploadRequest(reversible=reversible, reactants=reactants, products=products)


def _count(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def test_z_to_e_written_flat_with_geometries_is_accepted(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        cis = _flat_entry(session, _CIS_XYZ)
        trans = _flat_entry(session, _TRANS_XYZ)
        assert cis.species_id == trans.species_id
        assert cis.id != trans.id

        entry = persist_reaction_upload(session, _request([_by_id(cis)], [_by_id(trans)]))

        assert entry.reaction_id is not None
        assert [p.species_entry_id for p in entry.structure_participants] == [cis.id, trans.id]


def test_z_to_e_written_flat_and_with_marks_is_the_same_chem_reaction(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        cis = _flat_entry(session, _CIS_XYZ)
        trans = _flat_entry(session, _TRANS_XYZ)
        flat = persist_reaction_upload(session, _request([_by_id(cis)], [_by_id(trans)]))
        marked = persist_reaction_upload(session, _request([_marked("C/C=C\\C")], [_marked("C/C=C/C")]))

        assert flat.reaction_id == marked.reaction_id
        # The marked deposit resolved to the very entries the flat one named.
        assert [p.species_entry_id for p in marked.structure_participants] == [cis.id, trans.id]
        assert _count(session, ChemReaction) == 1


def test_the_same_entry_on_both_sides_is_refused(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        trans = _flat_entry(session, _TRANS_XYZ)
        before = _count(session, ReactionEntry)
        with pytest.raises(CodedValueError) as excinfo:
            persist_reaction_upload(session, _request([_by_id(trans)], [_by_id(trans)]))
        assert excinfo.value.code == W_REACTION_ENTRY_NULL_REACTION
        assert "id=" not in str(excinfo.value)
        assert _count(session, ReactionEntry) == before


def test_the_same_entry_written_with_marks_on_both_sides_is_refused(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        with pytest.raises(CodedValueError) as excinfo:
            persist_reaction_upload(session, _request([_marked("C/C=C/C")], [_marked("C/C=C/C")]))
        assert excinfo.value.code == W_REACTION_ENTRY_NULL_REACTION


def test_the_same_entry_set_in_another_order_is_still_a_null_reaction(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        cis = _flat_entry(session, _CIS_XYZ)
        trans = _flat_entry(session, _TRANS_XYZ)
        with pytest.raises(CodedValueError) as excinfo:
            persist_reaction_upload(
                session, _request([_by_id(cis), _by_id(trans)], [_by_id(trans), _by_id(cis)])
            )
        assert excinfo.value.code == W_REACTION_ENTRY_NULL_REACTION


def test_forward_and_reverse_irreversible_deposits_share_one_chem_reaction(db_conn) -> None:
    """Both directions are one graph reaction (the sides are sorted); the entries tell them apart."""
    with Session(db_conn) as session, session.begin():
        cis = _flat_entry(session, _CIS_XYZ)
        trans = _flat_entry(session, _TRANS_XYZ)
        forward = persist_reaction_upload(session, _request([_by_id(cis)], [_by_id(trans)]))
        reverse = persist_reaction_upload(session, _request([_by_id(trans)], [_by_id(cis)]))

        assert forward.reaction_id == reverse.reaction_id
        assert forward.id != reverse.id
        assert [(p.role.value, p.species_entry_id) for p in forward.structure_participants] == [
            ("reactant", cis.id),
            ("product", trans.id),
        ]
        assert [(p.role.value, p.species_entry_id) for p in reverse.structure_participants] == [
            ("reactant", trans.id),
            ("product", cis.id),
        ]
        assert _count(session, Species) == 1
        assert _count(session, ChemReaction) == 1
