"""A superseded record cannot be approved again (``superseded_record_not_approvable``).

Supersession is the correction path: the edge write deprecates the predecessor.
``deprecated -> approved`` is otherwise an allowed transition, so without this
guard a curator could re-approve the predecessor and make it curated-visible
with a notice that names a successor which may itself be hidden.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, text

from app.api.errors import DomainError, RecordReviewConflict
from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.network import Network
from app.db.models.record_review import RecordReview, RecordReviewEvent
from app.db.models.scientific_record_supersession import ScientificRecordSupersession
from app.services.accepted_science import is_accepted_science_type
from app.services.record_review import (
    RecordRef,
    bulk_set_record_review_status,
    ensure_record_review,
    set_record_review_status,
)
from app.services.scientific_record_supersession import supersede_scientific_record
from tests.services.scientific_read._factories import (
    make_applied_energy_correction,
    make_calculation,
    make_chem_reaction,
    make_conformer_group,
    make_conformer_observation,
    make_energy_correction_scheme,
    make_kinetics,
    make_network,
    make_network_solve,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    make_transition_state,
    make_transition_state_entry,
    make_transport,
)

NETWORK = SubmissionRecordType.network
CODE = "superseded_record_not_approvable"


def _curator(session, name: str = "guard-curator") -> AppUser:
    actor = AppUser(username=name, role=AppUserRole.curator)
    session.add(actor)
    session.flush()
    return actor


def _network(session, name: str) -> Network:
    row = Network(name=name)
    session.add(row)
    session.flush()
    return row


def _set(session, row_id: int, status: RecordReviewStatus, actor: AppUser, record_type=NETWORK):
    return set_record_review_status(
        session, record_type=record_type, record_id=row_id, status=status, actor=actor
    )


def _approved_pair(session, actor: AppUser) -> tuple[Network, Network]:
    old, new = _network(session, "old"), _network(session, "new")
    for row in (old, new):
        ensure_record_review(session, record_type=NETWORK, record_id=row.id)
        _set(session, row.id, RecordReviewStatus.approved, actor)
    return old, new


def _supersede(session, old: Network, new: Network, actor: AppUser):
    return supersede_scientific_record(
        session,
        record_type=NETWORK,
        superseded_record_id=old.id,
        superseding_record_id=new.id,
        actor=actor,
        reason="corrected topology",
    )


def _history(session, record_type, record_id) -> tuple[RecordReviewStatus, int]:
    review = session.scalar(
        select(RecordReview).where(
            RecordReview.record_type == record_type, RecordReview.record_id == record_id
        )
    )
    events = session.scalar(
        select(func.count()).select_from(RecordReviewEvent).where(RecordReviewEvent.record_review_id == review.id)
    )
    return review.status, events


def test_reapproving_a_superseded_record_is_refused_with_the_code(db_session) -> None:
    actor = _curator(db_session)
    old, new = _approved_pair(db_session, actor)
    _supersede(db_session, old, new, actor)
    before = _history(db_session, NETWORK, old.id)
    assert before[0] is RecordReviewStatus.deprecated

    with pytest.raises(RecordReviewConflict) as caught:
        _set(db_session, old.id, RecordReviewStatus.approved, actor)

    assert str(caught.value).startswith(f"{CODE}:")
    # Public refs only: no row id appears in the context.
    assert caught.value.context == {
        "record_type": "network",
        "record_ref": old.public_ref,
        "superseded_by_ref": new.public_ref,
    }
    assert _history(db_session, NETWORK, old.id) == before
    # Subclass of DomainError: a caller that does not know the class still refuses.
    assert isinstance(caught.value, DomainError)


def test_a_legacy_approved_and_superseded_row_cannot_be_restamped(db_session) -> None:
    """``approved -> approved`` on a legacy approved-and-superseded row is refused too."""
    actor = _curator(db_session)
    other = _curator(db_session, "guard-curator-2")
    old, new = _approved_pair(db_session, actor)
    _supersede(db_session, old, new, actor)
    # Legacy state: before the guard, a curator could move the deprecated
    # predecessor back to approved. Reproduce that row directly.
    db_session.execute(
        text("UPDATE record_review SET status = 'approved' WHERE record_type = 'network' AND record_id = :id"),
        {"id": old.id},
    )
    db_session.expire_all()
    assert _history(db_session, NETWORK, old.id)[0] is RecordReviewStatus.approved
    with pytest.raises(RecordReviewConflict, match=CODE):
        _set(db_session, old.id, RecordReviewStatus.approved, other)


def test_approving_a_non_superseded_record_still_works(db_session) -> None:
    actor = _curator(db_session)
    row = _network(db_session, "plain")
    ensure_record_review(db_session, record_type=NETWORK, record_id=row.id)
    assert _set(db_session, row.id, RecordReviewStatus.approved, actor).status is RecordReviewStatus.approved
    # deprecated -> approved with no edge stays an allowed transition.
    _set(db_session, row.id, RecordReviewStatus.deprecated, actor)
    assert _set(db_session, row.id, RecordReviewStatus.approved, actor).status is RecordReviewStatus.approved


def test_the_superseding_record_stays_approved_and_reapprovable(db_session) -> None:
    actor = _curator(db_session)
    other = _curator(db_session, "guard-curator-2")
    old, new = _approved_pair(db_session, actor)
    _supersede(db_session, old, new, actor)
    # The guard looks at superseded_record_id, not superseding_record_id.
    assert _set(db_session, new.id, RecordReviewStatus.approved, other).status is RecordReviewStatus.approved
    _set(db_session, new.id, RecordReviewStatus.under_review, actor)
    assert _set(db_session, new.id, RecordReviewStatus.approved, actor).status is RecordReviewStatus.approved


def test_only_the_approved_target_is_refused(db_session) -> None:
    """under_review and rejected stay open on a superseded record; approval does not."""
    actor = _curator(db_session)
    old, new = _approved_pair(db_session, actor)
    _supersede(db_session, old, new, actor)

    assert _set(db_session, old.id, RecordReviewStatus.under_review, actor).status is RecordReviewStatus.under_review
    with pytest.raises(RecordReviewConflict, match=CODE):
        _set(db_session, old.id, RecordReviewStatus.approved, actor)
    assert _set(db_session, old.id, RecordReviewStatus.rejected, actor).status is RecordReviewStatus.rejected
    # rejected -> deprecated is allowed by the transition table.
    assert _set(db_session, old.id, RecordReviewStatus.deprecated, actor).status is RecordReviewStatus.deprecated
    with pytest.raises(RecordReviewConflict, match=CODE):
        _set(db_session, old.id, RecordReviewStatus.approved, actor)


def test_a_disallowed_transition_keeps_its_own_message_and_is_not_the_conflict(db_session) -> None:
    """The guard runs after ``_check_transition_allowed`` (design section 8).

    ``rejected -> approved`` is disallowed by the transition table. On a
    superseded record it must still answer with that 400-class message, not
    with the 409 conflict.
    """
    actor = _curator(db_session)
    old, new = _approved_pair(db_session, actor)
    _supersede(db_session, old, new, actor)
    _set(db_session, old.id, RecordReviewStatus.under_review, actor)
    _set(db_session, old.id, RecordReviewStatus.rejected, actor)

    with pytest.raises(DomainError, match="Disallowed record-review transition") as caught:
        _set(db_session, old.id, RecordReviewStatus.approved, actor)
    assert not isinstance(caught.value, RecordReviewConflict)


def test_the_guard_is_scoped_to_record_type(db_session) -> None:
    """An edge on a network does not block a thermo record that has the same id.

    Both id sequences are only ever moved forward, so that a thermo row can be
    given exactly the id of the superseded network; only the ``record_type``
    predicate can then tell the two apart.
    """
    actor = _curator(db_session)
    thermo_seq = db_session.scalar(text("SELECT pg_get_serial_sequence('thermo', 'id')"))
    network_seq = db_session.scalar(text("SELECT pg_get_serial_sequence('network', 'id')"))
    high = db_session.scalar(
        text(
            "SELECT GREATEST((SELECT last_value FROM " + thermo_seq + "), "
            "(SELECT last_value FROM " + network_seq + ")) + 10"
        )
    )
    db_session.execute(text("SELECT setval(:s, :v)"), {"s": network_seq, "v": high})
    old, new = _approved_pair(db_session, actor)
    _supersede(db_session, old, new, actor)

    db_session.execute(text("SELECT setval(:s, :v)"), {"s": thermo_seq, "v": old.id - 1})
    entry = make_species_entry(db_session, make_species(db_session))
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    assert thermo.id == old.id
    ensure_record_review(db_session, record_type=SubmissionRecordType.thermo, record_id=thermo.id)
    review = _set(
        db_session, thermo.id, RecordReviewStatus.approved, actor, record_type=SubmissionRecordType.thermo
    )
    assert review.status is RecordReviewStatus.approved


def test_bulk_approval_with_one_superseded_record_refuses_the_batch(db_session) -> None:
    actor = _curator(db_session)
    old, new = _approved_pair(db_session, actor)
    _supersede(db_session, old, new, actor)
    fresh = _network(db_session, "fresh")
    ensure_record_review(db_session, record_type=NETWORK, record_id=fresh.id)

    # The superseded record is last, so the batch has already touched ``fresh``
    # when it hits the refusal: the whole call raises rather than skipping it.
    with pytest.raises(RecordReviewConflict, match=CODE):
        bulk_set_record_review_status(
            db_session,
            targets=[RecordRef(NETWORK, fresh.id), RecordRef(NETWORK, old.id)],
            status=RecordReviewStatus.approved,
            actor=actor,
        )


def test_supersession_flow_itself_is_unchanged(db_session) -> None:
    actor = _curator(db_session)
    old, new = _approved_pair(db_session, actor)
    edge = _supersede(db_session, old, new, actor)
    assert (edge.superseded_record_id, edge.superseding_record_id) == (old.id, new.id)
    assert _history(db_session, NETWORK, old.id)[0] is RecordReviewStatus.deprecated
    assert _history(db_session, NETWORK, new.id)[0] is RecordReviewStatus.approved
    # The exact retry is still idempotent and does not trip the guard.
    assert _supersede(db_session, old, new, actor).id == edge.id


ACCEPTED_SCIENCE_TYPES = sorted(
    (t for t in SubmissionRecordType if is_accepted_science_type(t)), key=lambda t: t.value
)


def _species_entry(session):
    return make_species_entry(session, make_species(session))


def _reaction_entry(session):
    reactant, product = make_species(session), make_species(session)
    return make_reaction_entry(
        session,
        reaction=make_chem_reaction(session, reactants=[reactant], products=[product]),
        reactant_entries=[make_species_entry(session, reactant)],
        product_entries=[make_species_entry(session, product)],
    )


def _pair_calculation(s):
    entry = _species_entry(s)
    return (make_calculation(s, species_entry_id=entry.id), make_calculation(s, species_entry_id=entry.id))


def _pair_thermo(s):
    entry = _species_entry(s)
    return (
        make_thermo_scalar(s, species_entry=entry, h298_kj_mol=-1.0),
        make_thermo_scalar(s, species_entry=entry, h298_kj_mol=-2.0),
    )


def _pair_statmech(s):
    entry = _species_entry(s)
    return make_statmech(s, species_entry=entry), make_statmech(s, species_entry=entry, point_group="Cs")


def _pair_transport(s):
    entry = _species_entry(s)
    return make_transport(s, species_entry=entry), make_transport(s, species_entry=entry, sigma_angstrom=3.6)


def _pair_kinetics(s):
    entry = _reaction_entry(s)
    return make_kinetics(s, reaction_entry=entry), make_kinetics(s, reaction_entry=entry, a=2.0e-12)


def _pair_network(s):
    return make_network(s, name="pair-a"), make_network(s, name="pair-b")


def _pair_network_solve(s):
    network = make_network(s, name="pair-solve")
    return make_network_solve(s, network=network), make_network_solve(s, network=network)


def _pair_applied_energy_correction(s):
    entry = _species_entry(s)
    scheme = make_energy_correction_scheme(s)
    return (
        make_applied_energy_correction(s, target_species_entry=entry, scheme=scheme, value=0.001, temperature_k=300.0),
        make_applied_energy_correction(s, target_species_entry=entry, scheme=scheme, value=0.002, temperature_k=400.0),
    )


def _pair_transition_state_entry(s):
    ts = make_transition_state(s, reaction_entry=_reaction_entry(s))
    return (
        make_transition_state_entry(s, transition_state=ts, multiplicity=2),
        make_transition_state_entry(s, transition_state=ts, multiplicity=4),
    )


def _pair_conformer_observation(s):
    group = make_conformer_group(s, _species_entry(s))
    return make_conformer_observation(s, conformer_group=group), make_conformer_observation(s, conformer_group=group)


PAIR_BUILDERS = {
    SubmissionRecordType.calculation: _pair_calculation,
    SubmissionRecordType.thermo: _pair_thermo,
    SubmissionRecordType.statmech: _pair_statmech,
    SubmissionRecordType.kinetics: _pair_kinetics,
    SubmissionRecordType.transport: _pair_transport,
    SubmissionRecordType.network: _pair_network,
    SubmissionRecordType.network_solve: _pair_network_solve,
    SubmissionRecordType.applied_energy_correction: _pair_applied_energy_correction,
    SubmissionRecordType.transition_state_entry: _pair_transition_state_entry,
    SubmissionRecordType.conformer_observation: _pair_conformer_observation,
}


def test_every_supersession_type_has_a_builder() -> None:
    """Non-vacuity: the parametrisation below covers the table's own allow-list, all ten types."""
    check = next(
        c for c in ScientificRecordSupersession.__table_args__ if str(getattr(c, "name", "")).endswith("supported_type")
    )
    sql = str(check.sqltext)
    allowed = {t for t in SubmissionRecordType if f"'{t.value}'" in sql}
    assert len(ACCEPTED_SCIENCE_TYPES) == 10
    assert set(ACCEPTED_SCIENCE_TYPES) == allowed == set(PAIR_BUILDERS)


@pytest.mark.parametrize("record_type", ACCEPTED_SCIENCE_TYPES, ids=lambda t: t.value)
def test_every_supersession_type_refuses_reapproval(db_session, record_type) -> None:
    actor = _curator(db_session)
    old, new = PAIR_BUILDERS[record_type](db_session)
    for row in (old, new):
        ensure_record_review(db_session, record_type=record_type, record_id=row.id)
        _set(db_session, row.id, RecordReviewStatus.approved, actor, record_type=record_type)

    # The supersession flow itself still deprecates the predecessor, per type.
    supersede_scientific_record(
        db_session,
        record_type=record_type,
        superseded_record_id=old.id,
        superseding_record_id=new.id,
        actor=actor,
        reason="corrected",
    )
    assert _history(db_session, record_type, old.id)[0] is RecordReviewStatus.deprecated

    with pytest.raises(RecordReviewConflict, match=CODE) as caught:
        _set(db_session, old.id, RecordReviewStatus.approved, actor, record_type=record_type)
    assert caught.value.context["record_type"] == record_type.value

    # The successor is not blocked.
    other = _curator(db_session, "guard-curator-2")
    restamped = _set(db_session, new.id, RecordReviewStatus.approved, other, record_type=record_type)
    assert restamped.status is RecordReviewStatus.approved
