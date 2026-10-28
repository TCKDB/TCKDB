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
    """Z->E and E->Z are one graph reaction because both sides hold the same species (the hash keys the two
    sides separately, so in general A->B and B->A are two rows); the entries tell the directions apart."""
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


# ---------------------------------------------------------------------------
# Identical entries are accepted when the deposit says something happens
# ---------------------------------------------------------------------------

_H = {"smiles": "[H]", "charge": 0, "multiplicity": 2}
_H2 = {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}


def _h_exchange_request() -> dict:
    """``H + H2 -> H2 + H``: the same entries on both sides."""
    return {
        "reversible": False,
        "reactants": [{"species_entry": _H}, {"species_entry": _H2}],
        "products": [{"species_entry": _H2}, {"species_entry": _H}],
    }


def _exchange_kinetics():
    from app.schemas.workflows.kinetics_upload import KineticsUploadRequest

    return KineticsUploadRequest(
        reaction=_h_exchange_request(),
        scientific_origin="experimental",
        a=1.0e13,
        a_units="cm3_mol_s",
        n=0.0,
        reported_ea=40.0,
        reported_ea_units="kj_mol",
    )


def test_a_bare_degenerate_reaction_is_refused_and_the_error_names_the_missing_evidence(db_conn) -> None:
    with Session(db_conn) as session, session.begin():
        with pytest.raises(CodedValueError) as excinfo:
            persist_reaction_upload(session, ReactionUploadRequest(**_h_exchange_request()))
    error = excinfo.value
    assert error.code == W_REACTION_ENTRY_NULL_REACTION
    text = str(error)
    assert "transition state" in text and "kinetics" in text and "atom mapping" in text
    assert error.context["missing_evidence"] == ["transition_state", "kinetics"]


def test_a_degenerate_reaction_with_a_transition_state_is_accepted(db_conn) -> None:
    from tests.services.test_calculation_geometry_composition import _isolated_session, _ts_payload, _upload_ts

    with _isolated_session(db_conn) as session:
        ts_entry = _upload_ts(session, _ts_payload())
        session.flush()
        assert ts_entry.id is not None


def test_a_degenerate_reaction_with_kinetics_but_no_transition_state_is_accepted(db_conn) -> None:
    from app.workflows.kinetics import persist_kinetics_upload

    with Session(db_conn) as session, session.begin():
        kinetics = persist_kinetics_upload(session, _exchange_kinetics(), created_by=None)
        assert kinetics.reaction_entry_id is not None


def _degenerate_bundle(*, transition_state: bool, kinetics: bool) -> dict:
    """The CH3 + H -> CH4 bundle with its sides made identical (CH3 + H on both), plus the chosen evidence."""
    import copy

    from tests.workflows.test_computed_reaction_upload import _minimal_payload

    payload = copy.deepcopy(_minimal_payload())
    payload["product_keys"] = list(payload["reactant_keys"])
    if not transition_state:
        del payload["transition_state"]
    if kinetics:
        payload["kinetics"][0]["product_keys"] = list(payload["reactant_keys"])
    else:
        payload["kinetics"] = []
    return payload


def _persist_bundle(session, payload: dict):
    from app.schemas.workflows.computed_reaction_upload import ComputedReactionUploadRequest
    from app.workflows.computed_reaction import persist_computed_reaction_upload

    return persist_computed_reaction_upload(session, ComputedReactionUploadRequest(**payload))


def test_a_bundle_with_a_transition_state_accepts_a_same_entries_reaction(db_conn) -> None:
    from tests.workflows.test_computed_reaction_upload import _isolated_session

    with _isolated_session(db_conn) as session:
        assert _persist_bundle(session, _degenerate_bundle(transition_state=True, kinetics=False))


def test_a_bundle_with_kinetics_but_no_transition_state_accepts_a_same_entries_reaction(db_conn) -> None:
    from tests.workflows.test_computed_reaction_upload import _isolated_session

    with _isolated_session(db_conn) as session:
        assert _persist_bundle(session, _degenerate_bundle(transition_state=False, kinetics=True))


def test_a_bundle_with_neither_refuses_a_same_entries_reaction(db_conn) -> None:
    from tests.workflows.test_computed_reaction_upload import _isolated_session

    with _isolated_session(db_conn) as session:
        with pytest.raises(CodedValueError) as excinfo:
            _persist_bundle(session, _degenerate_bundle(transition_state=False, kinetics=False))
    assert excinfo.value.code == W_REACTION_ENTRY_NULL_REACTION


def test_a_fit_for_another_reaction_is_not_evidence_for_a_bare_same_entries_reaction(db_conn) -> None:
    """``CH4 -> CH4`` with no TS, whose only kinetics fit is ``CH3 + H -> CH4``, is still bare."""
    import copy

    from tests.workflows.test_computed_reaction_upload import _isolated_session, _minimal_payload

    payload = copy.deepcopy(_minimal_payload())
    payload["reactant_keys"] = ["ch4"]
    payload["product_keys"] = ["ch4"]
    del payload["transition_state"]
    assert payload["kinetics"][0]["reactant_keys"] == ["ch3", "h"]
    with _isolated_session(db_conn) as session:
        with pytest.raises(CodedValueError) as excinfo:
            _persist_bundle(session, payload)
    assert excinfo.value.code == W_REACTION_ENTRY_NULL_REACTION


def test_a_reversed_fit_in_another_order_is_evidence(db_conn) -> None:
    from tests.workflows.test_computed_reaction_upload import _isolated_session

    payload = _degenerate_bundle(transition_state=False, kinetics=True)
    payload["kinetics"][0]["reactant_keys"] = ["h", "ch3"]
    payload["kinetics"][0]["product_keys"] = ["h", "ch3"]
    with _isolated_session(db_conn) as session:
        assert _persist_bundle(session, payload)


def test_a_pdep_micro_reaction_named_by_a_transition_state_accepts_a_same_entries_reaction(db_conn) -> None:
    """The elimination micro reaction of the C2H5OO fixture, made degenerate: reactants equal products."""
    import copy

    from app.schemas.workflows.network_pdep_upload import NetworkPDepUploadRequest
    from app.workflows.network_pdep import persist_network_pdep_upload
    from tests.workflows.test_network_pdep_upload import _full_payload

    payload = copy.deepcopy(_full_payload(include_solve=False))
    micro = next(r for r in payload["micro_reactions"] if r["key"] == "rxn_ho2_elim")
    micro["products"] = copy.deepcopy(micro["reactants"])
    for ts in payload["transition_states"]:
        ts.pop("validation_evidence", None)  # its IRC partition was written for the original products
    with Session(db_conn) as session, session.begin():
        network = persist_network_pdep_upload(session, NetworkPDepUploadRequest(**payload))
        assert network.id is not None


def test_the_transition_state_may_come_after_the_kinetics(db_conn) -> None:
    """Kinetics first (it creates the reaction entry), the saddle point in a later request."""
    from app.workflows.kinetics import persist_kinetics_upload
    from tests.services.test_calculation_geometry_composition import _isolated_session, _ts_payload, _upload_ts

    with _isolated_session(db_conn) as session:
        persist_kinetics_upload(session, _exchange_kinetics(), created_by=None)
        ts_entry = _upload_ts(session, _ts_payload())
        session.flush()
        assert ts_entry.id is not None
