"""Tests for ``(record_type, record_id) -> public_ref`` resolution.

:mod:`app.services.record_refs` is the crossing between the two ways this
archive names a record: the internal database id the private review surfaces
store, and the ``public_ref`` every scientific read route answers to. The
tests below are about the three things that can go wrong at a crossing --
returning the wrong table's ref, inventing one for a table that has none, and
issuing a query per row.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db.models.common import SubmissionRecordType
from app.db.models.network import Network
from app.services.record_refs import (
    REF_BEARING_RECORD_TYPES,
    resolve_record_public_ref,
    resolve_record_public_refs,
    resolve_record_public_refs_by_name,
)
from tests.services.scientific_read._factories import make_species


def _make_network(session: Session, name: str) -> Network:
    network = Network(name=name, description="record_refs fixture")
    session.add(network)
    session.flush()
    return network


def test_resolves_a_record_to_its_own_public_ref(db_session):
    species = make_species(db_session)

    resolved = resolve_record_public_ref(
        db_session,
        record_type=SubmissionRecordType.species,
        record_id=species.id,
    )

    # Equality with the row's own column, not merely "a truthy string": a
    # resolver that returned any other species' ref would still be truthy.
    assert resolved == species.public_ref
    assert resolved.startswith("spc_")


def test_two_record_types_resolve_to_their_own_tables(db_session):
    """The bulk path groups by record type; each group must stay in its table.

    The failure this guards against is a resolver that pools every id into one
    ``IN`` clause and queries a single table: ids are per-table sequences, so a
    species id and a network id are routinely the same integer and the pooled
    form would silently answer with the wrong table's ref. Asserting the
    ``spc_`` / ``net_`` prefixes catches that even when the ids differ.
    """
    species = make_species(db_session)
    network = _make_network(db_session, "record-refs-two-types")

    resolved = resolve_record_public_refs(
        db_session,
        [
            (SubmissionRecordType.species, species.id),
            (SubmissionRecordType.network, network.id),
        ],
    )

    assert resolved[(SubmissionRecordType.species, species.id)] == species.public_ref
    assert resolved[(SubmissionRecordType.network, network.id)] == network.public_ref
    assert resolved[(SubmissionRecordType.species, species.id)].startswith("spc_")
    assert resolved[(SubmissionRecordType.network, network.id)].startswith("net_")


def test_an_id_naming_no_row_is_absent_rather_than_guessed(db_session):
    # Far beyond any sequence this test database has reached.
    missing = 9_000_000_001

    assert resolve_record_public_refs(
        db_session, [(SubmissionRecordType.species, missing)]
    ) == {}
    assert (
        resolve_record_public_ref(
            db_session,
            record_type=SubmissionRecordType.species,
            record_id=missing,
        )
        is None
    )


def test_applied_energy_correction_has_no_ref_and_none_is_invented(db_session):
    """``AppliedEnergyCorrection`` carries no ``public_ref`` column.

    The tempting workaround is to fall back to the stringified row id, which is
    what the private machine-review matching key does. That would put an
    internal id in front of a reader (DR-0028 Req 2) while looking like a
    public handle. ``None`` is the honest answer until the table gains a ref.
    """
    assert SubmissionRecordType.applied_energy_correction not in REF_BEARING_RECORD_TYPES
    assert (
        resolve_record_public_ref(
            db_session,
            record_type=SubmissionRecordType.applied_energy_correction,
            record_id=1,
        )
        is None
    )
    # And it never reaches the database looking for a column that is not there.
    assert (
        resolve_record_public_refs(
            db_session,
            [(SubmissionRecordType.applied_energy_correction, 1)],
        )
        == {}
    )


def test_every_record_type_is_accounted_for():
    """A new ``SubmissionRecordType`` must be decided, not silently unnamed.

    Without this, adding a member gives it ``None`` forever and no test fails.

    Note what this does **not** check: the key set says nothing about which
    model each key points at, so it passes unchanged if two entries are
    swapped. That check lives in ``test_record_models.py``, against an
    independently written table of ``__tablename__`` values -- and it has to,
    because a test that derives its expectation from the mapping agrees with
    any typo in it.
    """
    no_public_ref_column = {
        SubmissionRecordType.applied_energy_correction,
        SubmissionRecordType.molecular_property_observation,
    }

    assert REF_BEARING_RECORD_TYPES | no_public_ref_column == set(SubmissionRecordType)
    assert REF_BEARING_RECORD_TYPES & no_public_ref_column == set()


def test_molecular_property_observation_has_no_ref_and_none_is_invented(db_session):
    """``MolecularPropertyObservation`` carries no ``public_ref`` column
    either (Phase C-E1) -- same refusal, same reason, as
    ``applied_energy_correction`` above.
    """
    assert (
        SubmissionRecordType.molecular_property_observation
        not in REF_BEARING_RECORD_TYPES
    )
    assert (
        resolve_record_public_ref(
            db_session,
            record_type=SubmissionRecordType.molecular_property_observation,
            record_id=1,
        )
        is None
    )
    assert (
        resolve_record_public_refs(
            db_session,
            [(SubmissionRecordType.molecular_property_observation, 1)],
        )
        == {}
    )


@pytest.mark.parametrize("record_count", [1, 8])
def test_a_page_of_one_type_costs_one_query(db_session, record_count):
    """The claim in the docstring, measured.

    A resolver that looped per row would pass every other test in this file
    and turn the curator queue's 200-row page into 200 round trips.
    """
    species = [make_species(db_session) for _ in range(record_count)]
    refs = [(SubmissionRecordType.species, s.id) for s in species]

    statements = 0
    engine = db_session.connection().engine

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        nonlocal statements
        statements += 1

    try:
        resolved = resolve_record_public_refs(db_session, refs)
    finally:
        event.remove(engine, "before_cursor_execute", _before)

    assert len(resolved) == record_count
    assert statements == 1, f"expected one grouped SELECT, saw {statements}"


# --------------------------------------------------------------------------- #
# The name-keyed form, for callers holding a raw string
# --------------------------------------------------------------------------- #


def test_by_name_resolves_a_known_type(db_session):
    species = make_species(db_session)

    resolved = resolve_record_public_refs_by_name(
        db_session, [("species", species.id)]
    )

    assert resolved == {("species", species.id): species.public_ref}


def test_by_name_drops_an_unknown_type_without_raising(db_session):
    """The projection tolerates a record type it cannot parse, so this must too.

    Note *how* it must not be implemented: ``SubmissionRecordType(name)`` inside
    ``try/except ValueError`` reads as the obvious version and is a defect --
    ``CodedValidationError`` subclasses ``ValueError``, so such a handler
    silently eats a coded refusal raised beneath it, costing a client the
    ``code`` and ``context`` it is told to branch on.
    ``tests/api/test_coded_exception_reraise_gate`` fails the build for that
    shape and caught this exact one on 2026-09-14.
    """
    species = make_species(db_session)

    resolved = resolve_record_public_refs_by_name(
        db_session,
        [("species", species.id), ("not_a_record_type", 1), ("", 2)],
    )

    # The known one still resolves -- an unknown sibling must not poison the
    # batch, which is what an exception escaping the loop would do.
    assert resolved == {("species", species.id): species.public_ref}


def test_by_name_keys_results_by_the_string_it_was_given(db_session):
    """Callers hold strings; handing back enum-keyed pairs would not match.

    A resolver that returned ``{(SubmissionRecordType.species, id): ref}`` would
    look correct and find nothing at the call site, because the caller looks up
    by the raw ``record_type`` string it already has.
    """
    species = make_species(db_session)

    resolved = resolve_record_public_refs_by_name(
        db_session, [("species", species.id)]
    )

    assert list(resolved) == [("species", species.id)]
    assert all(isinstance(name, str) for name, _ in resolved)
