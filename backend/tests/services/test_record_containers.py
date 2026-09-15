"""Tests for ``(record_type, record_id) -> the record it is part of``.

:mod:`app.services.record_containers` exists because 30% of the record review
queue points at nothing: ``thermo``, ``statmech``, ``kinetics``,
``transition_state``, ``network_solve`` and ``applied_energy_correction`` have
no page of their own, so a curator asked to judge one cannot open it. Each of
those tables already carries the key naming its owner, and the owner *does*
have a page.

Four things can go wrong at that crossing, and there is a test below for each:

* reporting no container for a type that has one (the whole defect, restored);
* reporting the *wrong* table's container, which is silent -- a ref still
  comes back, just the wrong record's;
* issuing a query per row;
* emitting half a pair: a ref with no type, which no client can address.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.db.models.common import ScientificOriginKind, SubmissionRecordType
from app.db.models.reaction import ReactionEntry
from app.db.models.species import SpeciesEntry
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.db.models.transition_state import TransitionStateEntry
from app.services.record_containers import (
    CONTAINED_RECORD_TYPES,
    CONTAINER_RECORD_TYPES,
    RecordContainer,
    resolve_record_container,
    resolve_record_containers,
)
from app.services.record_refs import REF_BEARING_RECORD_TYPES
from tests.services.scientific_read._factories import (
    make_applied_energy_correction,
    make_chem_reaction,
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

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _species_entry(session: Session) -> SpeciesEntry:
    return make_species_entry(session, make_species(session))


def _reaction_entry(session: Session) -> ReactionEntry:
    reactant = make_species(session)
    product = make_species(session)
    return make_reaction_entry(
        session,
        reaction=make_chem_reaction(
            session, reactants=[reactant], products=[product]
        ),
        reactant_entries=[make_species_entry(session, reactant)],
        product_entries=[make_species_entry(session, product)],
    )


def _transition_state_entry(session: Session) -> TransitionStateEntry:
    return make_transition_state_entry(
        session,
        transition_state=make_transition_state(
            session, reaction_entry=_reaction_entry(session)
        ),
    )


class _Counter:
    def __init__(self) -> None:
        self.statements = 0


class _counted:
    """Count SQL statements issued on the session's engine inside the block.

    The same harness ``test_record_refs.py`` uses, as a context manager so the
    listener is removed even when the assertion inside fails -- a leaked
    listener counts every later test's statements too, which is how a
    query-count guard turns into a source of unrelated failures.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._counter = _Counter()

    def __enter__(self) -> _Counter:
        self._engine = self._session.connection().engine

        def _before(conn, cursor, statement, parameters, context, executemany):
            self._counter.statements += 1

        self._listener = _before
        event.listen(self._engine, "before_cursor_execute", _before)
        return self._counter

    def __exit__(self, *exc) -> None:
        event.remove(self._engine, "before_cursor_execute", self._listener)


# --------------------------------------------------------------------------- #
# The six types the review queue could not open. One test each, because the
# defect was type-by-type and a loop over a derived list would pass on the
# strength of whichever types happened to be in it.
# --------------------------------------------------------------------------- #


def test_thermo_is_contained_by_its_species_entry(db_session):
    entry = _species_entry(db_session)
    thermo = make_thermo_scalar(db_session, species_entry=entry)

    # The whole value, not just "something came back": a resolver reading the
    # wrong column would still return a populated RecordContainer, and one
    # reading the right column against the wrong table would still return a
    # ref. Equality with the parent's own ref is what rules both out.
    assert resolve_record_container(
        db_session, record_type=SubmissionRecordType.thermo, record_id=thermo.id
    ) == RecordContainer(SubmissionRecordType.species_entry, entry.public_ref)


def test_statmech_on_a_species_entry_is_contained_by_it(db_session):
    entry = _species_entry(db_session)
    statmech = make_statmech(db_session, species_entry=entry)

    assert resolve_record_container(
        db_session,
        record_type=SubmissionRecordType.statmech,
        record_id=statmech.id,
    ) == RecordContainer(SubmissionRecordType.species_entry, entry.public_ref)


def test_statmech_on_a_transition_state_entry_is_contained_by_it(db_session):
    """The second of statmech's two owner columns.

    ``statmech`` is the only queue type whose owner is genuinely either of two
    tables, and a resolver that read only ``species_entry_id`` would pass the
    test above and leave every transition-state statmech unlinked. The table's
    XOR ``CHECK`` means this row has ``species_entry_id IS NULL``, so the
    candidate walk has to fall through to the second column to answer at all.
    """
    ts_entry = _transition_state_entry(db_session)
    statmech = Statmech(
        transition_state_entry_id=ts_entry.id,
        scientific_origin=ScientificOriginKind.computed,
        external_symmetry=1,
    )
    db_session.add(statmech)
    db_session.flush()

    assert resolve_record_container(
        db_session,
        record_type=SubmissionRecordType.statmech,
        record_id=statmech.id,
    ) == RecordContainer(
        SubmissionRecordType.transition_state_entry, ts_entry.public_ref
    )


def test_kinetics_is_contained_by_its_reaction_entry(db_session):
    reaction_entry = _reaction_entry(db_session)
    kinetics = make_kinetics(db_session, reaction_entry=reaction_entry)

    assert resolve_record_container(
        db_session,
        record_type=SubmissionRecordType.kinetics,
        record_id=kinetics.id,
    ) == RecordContainer(
        SubmissionRecordType.reaction_entry, reaction_entry.public_ref
    )


def test_transition_state_is_contained_by_its_reaction_entry(db_session):
    """``transition_state`` is in the broken 30% and the #262 brief lists no
    parent for it. It has one: ``transition_state.reaction_entry_id``, NOT
    NULL, and the reaction entry page is where transition states are shown.
    """
    reaction_entry = _reaction_entry(db_session)
    ts = make_transition_state(db_session, reaction_entry=reaction_entry)

    assert resolve_record_container(
        db_session,
        record_type=SubmissionRecordType.transition_state,
        record_id=ts.id,
    ) == RecordContainer(
        SubmissionRecordType.reaction_entry, reaction_entry.public_ref
    )


def test_network_solve_is_contained_by_its_network(db_session):
    network = make_network(db_session, name="record-containers-network")
    solve = make_network_solve(db_session, network=network)

    assert resolve_record_container(
        db_session,
        record_type=SubmissionRecordType.network_solve,
        record_id=solve.id,
    ) == RecordContainer(SubmissionRecordType.network, network.public_ref)


# --------------------------------------------------------------------------- #
# applied_energy_correction: three targets, and it is the type that most needs
# a container because it is the only one that cannot be named at all.
# --------------------------------------------------------------------------- #


def test_correction_on_a_species_entry_names_that_entry(db_session):
    entry = _species_entry(db_session)
    correction = make_applied_energy_correction(
        db_session,
        target_species_entry=entry,
        scheme=make_energy_correction_scheme(db_session, name="rc_species"),
    )

    assert resolve_record_container(
        db_session,
        record_type=SubmissionRecordType.applied_energy_correction,
        record_id=correction.id,
    ) == RecordContainer(SubmissionRecordType.species_entry, entry.public_ref)


def test_correction_on_a_reaction_entry_names_that_entry(db_session):
    reaction_entry = _reaction_entry(db_session)
    correction = make_applied_energy_correction(
        db_session,
        target_reaction_entry=reaction_entry,
        scheme=make_energy_correction_scheme(db_session, name="rc_reaction"),
    )

    assert resolve_record_container(
        db_session,
        record_type=SubmissionRecordType.applied_energy_correction,
        record_id=correction.id,
    ) == RecordContainer(
        SubmissionRecordType.reaction_entry, reaction_entry.public_ref
    )


def test_correction_on_a_transition_state_entry_names_that_entry(db_session):
    """The third target column, which the #262 brief does not mention.

    A registry naming only the species and reaction columns passes both tests
    above and leaves every transition-state correction unlinked.
    """
    ts_entry = _transition_state_entry(db_session)
    correction = make_applied_energy_correction(
        db_session,
        target_transition_state_entry=ts_entry,
        scheme=make_energy_correction_scheme(db_session, name="rc_ts"),
    )

    assert resolve_record_container(
        db_session,
        record_type=SubmissionRecordType.applied_energy_correction,
        record_id=correction.id,
    ) == RecordContainer(
        SubmissionRecordType.transition_state_entry, ts_entry.public_ref
    )


def test_a_correction_that_cannot_be_named_can_still_be_located(db_session):
    """The point of the whole exercise, stated as one assertion.

    ``applied_energy_correction`` has no ``public_ref`` column, so
    ``record_refs`` correctly refuses to name it -- 164 rows of the queue
    render as "cannot be named". Giving the table a ref is task #253. This
    module does not need it: knowing the correction sits on ``spc_...`` is
    what a reviewer actually has to know, and it is available today.
    """
    entry = _species_entry(db_session)
    correction = make_applied_energy_correction(
        db_session,
        target_species_entry=entry,
        scheme=make_energy_correction_scheme(db_session, name="rc_unnamed"),
    )

    assert SubmissionRecordType.applied_energy_correction not in REF_BEARING_RECORD_TYPES
    container = resolve_record_container(
        db_session,
        record_type=SubmissionRecordType.applied_energy_correction,
        record_id=correction.id,
    )
    assert container is not None
    assert container.container_ref == entry.public_ref


# --------------------------------------------------------------------------- #
# Absences, each for its own reason
# --------------------------------------------------------------------------- #


def test_a_root_record_type_has_no_container(db_session):
    """``species`` is the root of its own tree. Absent, not an error."""
    species = make_species(db_session)

    assert SubmissionRecordType.species not in CONTAINED_RECORD_TYPES
    assert (
        resolve_record_container(
            db_session,
            record_type=SubmissionRecordType.species,
            record_id=species.id,
        )
        is None
    )
    # And it never reaches the database looking for a column that is not there.
    assert resolve_record_containers(
        db_session, [(SubmissionRecordType.species, species.id)]
    ) == {}


def test_an_id_naming_no_row_resolves_to_nothing(db_session):
    missing = 9_000_000_001

    assert (
        resolve_record_container(
            db_session, record_type=SubmissionRecordType.thermo, record_id=missing
        )
        is None
    )


def test_a_container_that_cannot_be_named_yields_no_half_pair(db_session):
    """A parent gone since the review row was written.

    The tempting shortcut is to return the container *type* anyway, since it
    is known without the second query. That is a type with no ref: it names
    nothing, cannot be linked, and would render as "shown on species entry"
    followed by no species entry. Both halves or neither.

    The dangling key is made by deferring the foreign key for this
    transaction -- the column is ``DEFERRABLE INITIALLY IMMEDIATE``, so an
    ordinary UPDATE would be refused at statement time. The read under test
    happens before any COMMIT, which is exactly when a review surface reads.
    """
    entry = _species_entry(db_session)
    thermo = make_thermo_scalar(db_session, species_entry=entry)

    db_session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
    db_session.execute(
        Thermo.__table__.update()
        .where(Thermo.__table__.c.id == thermo.id)
        .values(species_entry_id=9_000_000_002)
    )

    assert (
        resolve_record_container(
            db_session, record_type=SubmissionRecordType.thermo, record_id=thermo.id
        )
        is None
    )

    # Undo the dangling key before the fixture's own teardown tries to commit
    # or flush around it.
    db_session.execute(
        Thermo.__table__.update()
        .where(Thermo.__table__.c.id == thermo.id)
        .values(species_entry_id=entry.id)
    )
    db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


# --------------------------------------------------------------------------- #
# Registry-wide properties
# --------------------------------------------------------------------------- #


def test_every_record_type_is_decided():
    """A new ``SubmissionRecordType`` must be placed or deliberately rooted.

    Without this, adding a member gives it no container forever and no test
    fails. The roots are written out here rather than derived, so promoting a
    root to a child (or the reverse) has to be stated twice.
    """
    roots = {
        SubmissionRecordType.species,
        SubmissionRecordType.reaction,
        SubmissionRecordType.network,
    }

    assert CONTAINED_RECORD_TYPES | roots == set(SubmissionRecordType)
    assert CONTAINED_RECORD_TYPES & roots == set()


def test_every_container_type_can_actually_be_named():
    """A container whose own table has no ``public_ref`` resolves to nothing.

    That failure is silent and total: every child of such a type would report
    no container while the registry looked correct. ``applied_energy_correction``
    is the one type in the archive with no ref, so this is the assertion that
    stops it -- or any future refless table -- from being used as a container.
    """
    assert CONTAINER_RECORD_TYPES <= REF_BEARING_RECORD_TYPES


def test_the_six_unopenable_queue_types_are_all_covered():
    """The measured defect, pinned as a list.

    These are the record types the #262 survey found unopenable, with their
    row counts on the live archive: applied_energy_correction 164, statmech
    101, thermo 65, transition_state 34, kinetics 17, network_solve 4 -- 385
    of 1,299 rows. Every one must have a container, or that fraction of the
    queue stays dead.
    """
    unopenable = {
        SubmissionRecordType.applied_energy_correction,
        SubmissionRecordType.statmech,
        SubmissionRecordType.thermo,
        SubmissionRecordType.transition_state,
        SubmissionRecordType.kinetics,
        SubmissionRecordType.network_solve,
    }

    assert unopenable <= CONTAINED_RECORD_TYPES


# --------------------------------------------------------------------------- #
# Cost
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("record_count", [1, 12])
def test_a_page_of_one_type_costs_two_queries(db_session, record_count):
    """The claim in the docstring, measured.

    A resolver that looped per row would pass every other test in this file
    and turn a 50-row page into 100 round trips. Two queries: one reading the
    owner columns, one naming the owners found.
    """
    entry = _species_entry(db_session)
    thermos = [
        make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-10.0 - i)
        for i in range(record_count)
    ]
    refs = [(SubmissionRecordType.thermo, t.id) for t in thermos]

    with _counted(db_session) as count:
        resolved = resolve_record_containers(db_session, refs)

    assert len(resolved) == record_count
    assert count.statements == 2, (
        f"expected two grouped SELECTs, saw {count.statements}"
    )


@pytest.mark.parametrize("record_count", [2, 6])
def test_a_page_whose_rows_have_DIFFERENT_parents_still_costs_two(
    db_session, record_count
):
    """The cost pin that the two above cannot make.

    Every other cost test on this page puts all its rows under ONE shared
    parent, and that is a hole rather than a simplification: with a single
    distinct container, a resolver that queried once per *container* costs
    exactly what the grouped one does, so all of them pass it. MEASURED
    2026-09-16 -- replacing pass 2 with

        for c in set(container_ids.values()):
            container_refs.update(resolve_record_public_refs(session, [c]))

    passed all 44 tests in this file and ``test_api_record_reviews.py``.

    On a real page the rows do NOT share a parent: thermo rows sit on the
    species entries they were deposited against, so that sabotage is one
    round trip per row, which is the very regression the grouping exists to
    prevent -- reintroduced one layer below where the earlier pins look.

    So: N rows, N distinct species entries, still two queries. The count must
    not move with ``record_count``, which is what makes the parametrisation
    load-bearing rather than decorative.
    """
    thermos = [
        make_thermo_scalar(db_session, species_entry=_species_entry(db_session))
        for _ in range(record_count)
    ]
    refs = [(SubmissionRecordType.thermo, t.id) for t in thermos]

    # The parents really are distinct -- otherwise this test degrades into a
    # copy of the shared-parent one and silently stops testing anything.
    parents = {
        db_session.get(Thermo, t.id).species_entry_id for t in thermos
    }
    assert len(parents) == record_count, "fixture failed to build distinct parents"

    with _counted(db_session) as count:
        resolved = resolve_record_containers(db_session, refs)

    assert len(resolved) == record_count
    assert count.statements == 2, (
        f"expected two grouped SELECTs for {record_count} rows on "
        f"{record_count} different parents, saw {count.statements} -- the "
        "container refs are being resolved one parent at a time"
    )


def test_a_mixed_page_stays_bounded_by_the_types_present(db_session):
    """Twelve rows of two record types still cost four queries.

    Bounded by the number of *types*, not by the number of rows: two record
    types (one query each) whose owners are two container types (one query
    each). Growing the rows must not grow the count.
    """
    entry = _species_entry(db_session)
    reaction_entry = _reaction_entry(db_session)
    refs: list[tuple[SubmissionRecordType, int]] = []
    for i in range(6):
        thermo = make_thermo_scalar(
            db_session, species_entry=entry, h298_kj_mol=-20.0 - i
        )
        kinetics = make_kinetics(db_session, reaction_entry=reaction_entry)
        refs.append((SubmissionRecordType.thermo, thermo.id))
        refs.append((SubmissionRecordType.kinetics, kinetics.id))

    with _counted(db_session) as count:
        resolved = resolve_record_containers(db_session, refs)

    assert len(resolved) == 12
    # 2 record types + 2 container types. Per-row would be 24.
    assert count.statements == 4, (
        f"expected four grouped SELECTs, saw {count.statements}"
    )


def test_two_record_types_resolve_against_their_own_tables(db_session):
    """Ids are per-table sequences, so a thermo id and a kinetics id are
    routinely the same integer. A resolver that pooled every id into one
    ``IN`` clause against one table would answer with the wrong record's
    container and nothing else in this file would notice.
    """
    entry = _species_entry(db_session)
    reaction_entry = _reaction_entry(db_session)
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    kinetics = make_kinetics(db_session, reaction_entry=reaction_entry)

    resolved = resolve_record_containers(
        db_session,
        [
            (SubmissionRecordType.thermo, thermo.id),
            (SubmissionRecordType.kinetics, kinetics.id),
        ],
    )

    assert resolved[(SubmissionRecordType.thermo, thermo.id)] == RecordContainer(
        SubmissionRecordType.species_entry, entry.public_ref
    )
    assert resolved[(SubmissionRecordType.kinetics, kinetics.id)] == RecordContainer(
        SubmissionRecordType.reaction_entry, reaction_entry.public_ref
    )


def test_transport_is_contained_by_its_species_entry(db_session):
    """Not in the broken 30% only because no transport row is in the queue
    yet. It is the same mechanism, and it must not be the one entry nobody
    checked.
    """
    entry = _species_entry(db_session)
    transport = make_transport(db_session, species_entry=entry)

    assert resolve_record_container(
        db_session,
        record_type=SubmissionRecordType.transport,
        record_id=transport.id,
    ) == RecordContainer(SubmissionRecordType.species_entry, entry.public_ref)
