"""A declared atom map is frozen by its transition-state entry's acceptance.

``c6f2a9d4e7b1`` made accepted scientific records immutable in the database.
``d3a7f1c9b284`` added ``reaction_atom_map`` / ``reaction_atom_map_pair``
afterwards and did not join that regime, so a depositor's mechanism claim was
the one recently added scientific record that could still be rewritten in place
under an accepted saddle point. ``b6c1f4a8e703`` closes that.

These tests pin both halves of the rule, because a guard that is present but
never fires is the failure mode:

* an atom map under an **approved** ``transition_state_entry`` cannot be
  updated, deleted, extended with a new pair, or created at all;
* an atom map under an **unapproved** one is fully editable, so the freeze is
  acceptance-triggered rather than a blanket write ban on the table.

The last test proves the correction path exists: a wrong map is replaced by
depositing a new transition-state entry that carries the right one and
recording a ``transition_state_entry`` supersession, end to end through the
curator route. A freeze with no correction path would be worse than no freeze.

Why the transition-state entry and not something else owns this is argued in
``b6c1f4a8e703``'s module docstring; the short version is that it is the only
accepted-science root the map names, it is ``NOT NULL`` on every map, and one
TS entry determines exactly one reaction entry, so nothing unrelated can freeze
a map by being approved.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    AtomMapSource,
    ReactionRole,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.reaction import ReactionEntryStructureParticipant
from app.db.models.reaction_atom_map import ReactionAtomMap, ReactionAtomMapPair
from app.db.models.scientific_record_supersession import ScientificRecordSupersession
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from app.services.record_review import ensure_record_review, set_record_review_status
from tests.services.scientific_read._factories import (
    attach_geometry_atoms,
    make_chem_reaction,
    make_geometry,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
)

#: Two atoms on each side, so a pair can be re-pointed from atom 1 to atom 2
#: without violating the ``geometry_atom`` foreign keys. The mutation the guard
#: has to refuse is a *valid* remap -- a guard that only stops nonsense the
#: foreign keys already stop would prove nothing.
_SYMBOLS = ["H", "H"]
_COORDS = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.9]]


class _Bundle:
    """One reaction entry, its saddle point, and one declared atom map."""

    def __init__(self, *, transition_state, transition_state_entry, atom_map, pair, participant, geometries):
        self.transition_state = transition_state
        self.transition_state_entry = transition_state_entry
        self.atom_map = atom_map
        self.pair = pair
        self.participant = participant
        self.reactant_geometry, self.ts_geometry = geometries


def _curator(session, username: str = "atom-map-curator") -> AppUser:
    actor = AppUser(username=username, role=AppUserRole.curator)
    session.add(actor)
    session.flush()
    return actor


def _approve(session, record_id: int, actor: AppUser) -> None:
    ensure_record_review(
        session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_id=record_id,
    )
    review = set_record_review_status(
        session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_id=record_id,
        status=RecordReviewStatus.approved,
        actor=actor,
    )
    assert review.first_approved_at is not None


def _make_bundle(session, tag: str) -> _Bundle:
    reactant_species = make_species(session, inchi_key=next_inchi_key(f"{tag}R"))
    product_species = make_species(session, inchi_key=next_inchi_key(f"{tag}P"))
    reactant_entry = make_species_entry(session, reactant_species)
    product_entry = make_species_entry(session, product_species)
    reaction = make_chem_reaction(
        session,
        reactants=[reactant_species],
        products=[product_species],
    )
    reaction_entry = make_reaction_entry(
        session,
        reaction=reaction,
        reactant_entries=[reactant_entry],
        product_entries=[product_entry],
    )
    transition_state = make_transition_state(session, reaction_entry=reaction_entry)
    transition_state_entry = make_transition_state_entry(
        session,
        transition_state=transition_state,
    )
    participant = session.scalars(
        select(ReactionEntryStructureParticipant).where(
            ReactionEntryStructureParticipant.reaction_entry_id == reaction_entry.id,
            ReactionEntryStructureParticipant.role == ReactionRole.reactant,
        )
    ).one()

    reactant_geometry = make_geometry(session, natoms=len(_SYMBOLS))
    attach_geometry_atoms(
        session,
        geometry=reactant_geometry,
        symbols=list(_SYMBOLS),
        coords=[list(row) for row in _COORDS],
    )
    ts_geometry = make_geometry(session, natoms=len(_SYMBOLS))
    attach_geometry_atoms(
        session,
        geometry=ts_geometry,
        symbols=list(_SYMBOLS),
        coords=[list(row) for row in _COORDS],
    )

    atom_map = ReactionAtomMap(
        reaction_entry_id=reaction_entry.id,
        transition_state_entry_id=transition_state_entry.id,
        transition_state_geometry_id=ts_geometry.id,
        source=AtomMapSource.declared,
    )
    session.add(atom_map)
    session.flush()
    pair = ReactionAtomMapPair(
        atom_map_id=atom_map.id,
        side=ReactionRole.reactant,
        structure_participant_id=participant.id,
        geometry_id=reactant_geometry.id,
        atom_index=1,
        transition_state_geometry_id=ts_geometry.id,
        ts_atom_index=1,
        element="H",
        ts_element="H",
    )
    session.add(pair)
    session.flush()
    return _Bundle(
        transition_state=transition_state,
        transition_state_entry=transition_state_entry,
        atom_map=atom_map,
        pair=pair,
        participant=participant,
        geometries=(reactant_geometry, ts_geometry),
    )


def _second_pair(bundle: _Bundle) -> ReactionAtomMapPair:
    """A pair the unique constraints admit, so only the guard can refuse it."""

    return ReactionAtomMapPair(
        atom_map_id=bundle.atom_map.id,
        side=ReactionRole.reactant,
        structure_participant_id=bundle.participant.id,
        geometry_id=bundle.reactant_geometry.id,
        atom_index=2,
        transition_state_geometry_id=bundle.ts_geometry.id,
        ts_atom_index=2,
        element="H",
        ts_element="H",
    )


def test_map_of_approved_transition_state_entry_cannot_be_rewritten(db_session) -> None:
    actor = _curator(db_session)
    bundle = _make_bundle(db_session, "AMFRZ")
    # The pair goes before approval so every statement below reaches the map's
    # own guard and nothing else. Deleting a map that still had pairs would
    # cascade into them first and be refused by the *pair* guard, which would
    # leave this test green with the map's trigger missing entirely.
    db_session.delete(bundle.pair)
    db_session.flush()
    _approve(db_session, bundle.transition_state_entry.id, actor)

    with pytest.raises(DBAPIError), db_session.begin_nested():
        bundle.atom_map.note = "second thoughts"
        db_session.flush()

    with pytest.raises(DBAPIError), db_session.begin_nested():
        bundle.atom_map.equivalent_map_count = 4
        db_session.flush()

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.delete(bundle.atom_map)
        db_session.flush()


def test_pairs_of_approved_transition_state_entry_cannot_be_rewritten(db_session) -> None:
    actor = _curator(db_session)
    bundle = _make_bundle(db_session, "AMPAIR")
    _approve(db_session, bundle.transition_state_entry.id, actor)

    # A valid remap: atom 1 of the reactant becomes atom 2 of the saddle point.
    # Every foreign key and unique constraint still holds; only the guard
    # stands between this statement and a rewritten mechanism claim.
    with pytest.raises(DBAPIError), db_session.begin_nested():
        bundle.pair.ts_atom_index = 2
        db_session.flush()

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(_second_pair(bundle))
        db_session.flush()

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.delete(bundle.pair)
        db_session.flush()


def test_new_map_cannot_be_attached_to_an_approved_transition_state_entry(db_session) -> None:
    """Adding a claim to an accepted record changes what was accepted."""

    actor = _curator(db_session)
    bundle = _make_bundle(db_session, "AMLATE")
    db_session.delete(bundle.pair)
    db_session.delete(bundle.atom_map)
    db_session.flush()
    _approve(db_session, bundle.transition_state_entry.id, actor)

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(
            ReactionAtomMap(
                reaction_entry_id=bundle.transition_state.reaction_entry_id,
                transition_state_entry_id=bundle.transition_state_entry.id,
                transition_state_geometry_id=bundle.ts_geometry.id,
                source=AtomMapSource.declared,
            )
        )
        db_session.flush()


def test_map_of_unapproved_transition_state_entry_stays_editable(db_session) -> None:
    """The freeze is acceptance-triggered, not a write ban on the table."""

    bundle = _make_bundle(db_session, "AMOPEN")

    bundle.atom_map.note = "corrected before review"
    bundle.atom_map.equivalent_map_count = 2
    bundle.pair.ts_atom_index = 2
    db_session.flush()

    assert bundle.atom_map.note == "corrected before review"
    assert bundle.pair.ts_atom_index == 2

    # The saddle-point index the original pair vacated is free again, so the
    # second leg can be completed in place.
    extra = ReactionAtomMapPair(
        atom_map_id=bundle.atom_map.id,
        side=ReactionRole.reactant,
        structure_participant_id=bundle.participant.id,
        geometry_id=bundle.reactant_geometry.id,
        atom_index=2,
        transition_state_geometry_id=bundle.ts_geometry.id,
        ts_atom_index=1,
        element="H",
        ts_element="H",
    )
    db_session.add(extra)
    db_session.flush()

    db_session.delete(extra)
    db_session.flush()

    stored = db_session.scalars(
        select(ReactionAtomMapPair).where(ReactionAtomMapPair.atom_map_id == bundle.atom_map.id)
    ).all()
    assert [(row.atom_index, row.ts_atom_index) for row in stored] == [(1, 2)]


def test_truncate_cannot_bypass_the_atom_map_guard(db_session) -> None:
    for table in ("reaction_atom_map_pair", "reaction_atom_map"):
        with pytest.raises(DBAPIError), db_session.begin_nested():
            db_session.execute(text(f"TRUNCATE {table}"))


def test_a_wrong_map_is_corrected_by_superseding_its_transition_state_entry(
    client,
    db_session,
    login_as,
    _api_curator_user,
) -> None:
    """The correction path, end to end through the curator route.

    The wrong map stays exactly as deposited -- that is the point of the
    freeze -- and the record that carries the right one is a new
    ``transition_state_entry`` under the same ``transition_state``, joined to
    the old one by a supersession edge.
    """

    curator = db_session.get(AppUser, _api_curator_user)
    wrong = _make_bundle(db_session, "AMSUP")
    _approve(db_session, wrong.transition_state_entry.id, curator)

    replacement_entry = make_transition_state_entry(
        db_session,
        transition_state=wrong.transition_state,
    )
    corrected_map = ReactionAtomMap(
        reaction_entry_id=wrong.transition_state.reaction_entry_id,
        transition_state_entry_id=replacement_entry.id,
        transition_state_geometry_id=wrong.ts_geometry.id,
        source=AtomMapSource.declared,
        note="reactant atom 1 becomes saddle-point atom 2, not atom 1",
    )
    db_session.add(corrected_map)
    db_session.flush()
    db_session.add(
        ReactionAtomMapPair(
            atom_map_id=corrected_map.id,
            side=ReactionRole.reactant,
            structure_participant_id=wrong.participant.id,
            geometry_id=wrong.reactant_geometry.id,
            atom_index=1,
            transition_state_geometry_id=wrong.ts_geometry.id,
            ts_atom_index=2,
            element="H",
            ts_element="H",
        )
    )
    db_session.flush()
    _approve(db_session, replacement_entry.id, curator)

    login_as(_api_curator_user)
    response = client.post(
        "/api/v1/curation/scientific-record-supersessions",
        json={
            "record_type": SubmissionRecordType.transition_state_entry.value,
            "superseded_record_id": wrong.transition_state_entry.id,
            "superseding_record_id": replacement_entry.id,
            "reason": "the declared atom map identified the wrong saddle-point atom",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["record_type"] == SubmissionRecordType.transition_state_entry.value
    assert body["superseded_record_id"] == wrong.transition_state_entry.id
    assert body["superseding_record_id"] == replacement_entry.id

    edge = db_session.scalars(
        select(ScientificRecordSupersession).where(ScientificRecordSupersession.id == body["id"])
    ).one()
    assert edge.superseding_record_id == replacement_entry.id

    superseding_entry = db_session.scalars(
        select(TransitionStateEntry).where(TransitionStateEntry.id == edge.superseding_record_id)
    ).one()
    assert [pair.ts_atom_index for pair in superseding_entry.atom_maps[0].pairs] == [2]

    # The superseded claim is preserved verbatim rather than repaired.
    db_session.expire(wrong.atom_map)
    assert wrong.atom_map.note is None
    assert [pair.ts_atom_index for pair in wrong.atom_map.pairs] == [1]
    with pytest.raises(DBAPIError), db_session.begin_nested():
        wrong.atom_map.note = "still frozen after supersession"
        db_session.flush()

    assert (
        db_session.scalars(select(TransitionState).where(TransitionState.id == wrong.transition_state.id)).one().id
        == superseding_entry.transition_state_id
    )
