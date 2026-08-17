"""A superseded scientific product read must announce its own replacement.

TCKDB never rewrites an accepted record. A correction appends a new record
plus one immutable ``scientific_record_supersession`` edge, and the old row is
left byte-identical so an existing citation keeps resolving. Resolving is only
half a contract: a citation that 404s announces its own problem, but a
citation that resolves cleanly to a *superseded* number looks healthy, so
nobody investigates.

Every test here is built on a **three**-link chain (A → B → C), never two. On
a two-link chain "immediate successor" and "head of chain" are the same row,
so a two-link test passes against an implementation that confuses the two and
proves nothing. Three links is the shortest chain that can tell them apart.
"""

from __future__ import annotations

import re
from itertools import pairwise

import pytest
from sqlalchemy import event, select

from app.db.models.app_user import AppUser
from app.db.models.common import (
    AppUserRole,
    RecordReviewStatus,
    ScientificOriginKind,
    SubmissionRecordType,
)
from app.db.models.kinetics import Kinetics
from app.db.models.statmech import Statmech
from app.db.models.thermo import Thermo
from app.db.models.transport import Transport
from app.services.record_review import ensure_record_review, set_record_review_status
from app.services.scientific_read.supersession import fetch_supersession_notices
from app.services.scientific_record_supersession import supersede_scientific_record
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    make_transport,
    next_inchi_key,
)

#: The public-ref shape every pointer in a notice must have: a registered
#: prefix plus a 26-char base32 body. A bare integer must never appear
#: (DR-0028 Req 2).
_PUBLIC_REF = re.compile(r"^[a-z]+_[a-z2-7]{26}$")


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _curator(db_session) -> AppUser:
    actor = AppUser(username="supersession-notice-curator", role=AppUserRole.curator)
    db_session.add(actor)
    db_session.flush()
    return actor


def _approve(db_session, *, record_type: SubmissionRecordType, record_id: int, actor):
    ensure_record_review(db_session, record_type=record_type, record_id=record_id)
    return set_record_review_status(
        db_session,
        record_type=record_type,
        record_id=record_id,
        status=RecordReviewStatus.approved,
        actor=actor,
    )


def _snapshot(db_session, model, row_id: int) -> dict:
    """Every stored column of one row, as raw values.

    Mirrors the helper ADR 0007's selection test uses
    (``tests/services/release/test_release_selection.py``) so "the superseded
    record was not touched" is a whole-row claim, not a spot check on the two
    columns the author happened to think of.
    """
    row = (
        db_session.execute(
            select(model.__table__).where(model.__table__.c.id == row_id)
        )
        .mappings()
        .one()
    )
    return dict(row)


def _chain(db_session, *, record_type: SubmissionRecordType, rows, actor):
    """Supersede ``rows`` pairwise, left to right, and return the edges."""
    edges = []
    for older, newer in pairwise(rows):
        edges.append(
            supersede_scientific_record(
                db_session,
                record_type=record_type,
                superseded_record_id=older.id,
                superseding_record_id=newer.id,
                actor=actor,
                reason=f"corrected {older.id} -> {newer.id}",
            )
        )
    return edges


def _thermo_chain(db_session):
    """A → B → C on one species entry, plus the curator that recorded it."""
    actor = _curator(db_session)
    species = make_species(db_session, inchi_key=next_inchi_key("SUPSD"))
    entry = make_species_entry(db_session, species)
    rows = [
        make_thermo_scalar(
            db_session,
            species_entry=entry,
            h298_kj_mol=value,
            scientific_origin=ScientificOriginKind.computed,
        )
        for value in (-10.0, -11.0, -12.0)
    ]
    for row in rows:
        _approve(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=row.id,
            actor=actor,
        )
    _chain(db_session, record_type=SubmissionRecordType.thermo, rows=rows, actor=actor)
    return entry, rows, actor


def _records_by_ref(body) -> dict[str, dict]:
    return {rec["thermo_ref"]: rec for rec in body["records"]}


# ---------------------------------------------------------------------------
# The three-link chain, through the HTTP thermo read
# ---------------------------------------------------------------------------


def test_thermo_read_names_both_the_immediate_successor_and_the_head(
    client, db_session
):
    """A → B → C. A read of A must report *both* pointers, and differently.

    This is the load-bearing assertion of the whole feature. ``superseded_by``
    is the one edge that was recorded (B); ``current`` is where the chain ends
    (C). An implementation that reports the immediate successor as the head
    would satisfy a two-link chain and fails here.
    """
    entry, (first, second, third), _ = _thermo_chain(db_session)

    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo",
        params={"include_deprecated": "true"},
    )
    assert resp.status_code == 200
    records = _records_by_ref(resp.json())
    assert set(records) == {first.public_ref, second.public_ref, third.public_ref}

    head = records[first.public_ref]["supersession"]
    assert head is not None, "a superseded record must carry a correction notice"
    assert head["superseded_by"] == second.public_ref
    assert head["current"] == third.public_ref
    assert head["superseded_by"] != head["current"], (
        "on a three-link chain the immediate successor and the head are "
        "different rows; a test that cannot tell them apart proves nothing"
    )
    assert head["chain_length"] == 2

    middle = records[second.public_ref]["supersession"]
    assert middle is not None
    assert middle["superseded_by"] == third.public_ref
    assert middle["current"] == third.public_ref
    assert middle["chain_length"] == 1


def test_the_current_thermo_record_reports_nothing(client, db_session):
    """The head of the chain is not superseded and must say so by silence.

    ``null`` rather than an empty block: a client that has to inspect the
    inside of a notice to learn there is no notice will get it wrong.
    """
    entry, (_, _, third), _ = _thermo_chain(db_session)

    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo",
        params={"include_deprecated": "true"},
    )
    assert resp.status_code == 200
    assert _records_by_ref(resp.json())[third.public_ref]["supersession"] is None


def test_an_unsuperseded_record_reports_nothing(client, db_session):
    """A record in no chain at all: the notice key exists and is null."""
    species = make_species(db_session, inchi_key=next_inchi_key("SUPNO"))
    entry = make_species_entry(db_session, species)
    lone = make_thermo_scalar(db_session, species_entry=entry)

    resp = client.get(f"/api/v1/scientific/species-entries/{entry.id}/thermo")
    assert resp.status_code == 200
    record = _records_by_ref(resp.json())[lone.public_ref]
    assert "supersession" in record, "the key must be present on every record"
    assert record["supersession"] is None


def test_the_notice_is_not_behind_an_include_token(client, db_session):
    """Default-on. A correction notice a client must ask for is one most
    clients will not ask for, which defeats its purpose."""
    entry, (first, second, third), _ = _thermo_chain(db_session)

    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo",
        params={"include_deprecated": "true"},
    )
    body = resp.json()
    assert "supersession" not in body["request"]["include"]
    assert _records_by_ref(body)[first.public_ref]["supersession"] is not None


def test_the_notice_carries_the_immediate_edges_reason_not_the_heads(
    client, db_session
):
    """``reason`` describes the edge ``superseded_by`` names, not the last one.

    Pairing A's notice with C's reason would tell a reader the wrong story
    about why their specific citation stopped being current.
    """
    entry, (first, second, third), _ = _thermo_chain(db_session)

    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo",
        params={"include_deprecated": "true"},
    )
    records = _records_by_ref(resp.json())
    assert records[first.public_ref]["supersession"]["reason"] == (
        f"corrected {first.id} -> {second.id}"
    )
    assert records[second.public_ref]["supersession"]["reason"] == (
        f"corrected {second.id} -> {third.id}"
    )
    assert records[first.public_ref]["supersession"]["superseded_at"] is not None


# ---------------------------------------------------------------------------
# The record itself is never touched
# ---------------------------------------------------------------------------


def test_reading_a_superseded_record_leaves_it_byte_identical(client, db_session):
    """Nothing about the notice writes to the record it is attached to.

    Whole-row snapshots before and after both the supersession *and* the read,
    the way ADR 0007's selection test asserts it — the notice is computed at
    read time precisely so no earlier row has to be updated when a correction
    lands.
    """
    actor = _curator(db_session)
    species = make_species(db_session, inchi_key=next_inchi_key("SUPBI"))
    entry = make_species_entry(db_session, species)
    rows = [
        make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=value)
        for value in (-10.0, -11.0, -12.0)
    ]
    for row in rows:
        _approve(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=row.id,
            actor=actor,
        )
    db_session.flush()
    before = {row.id: _snapshot(db_session, Thermo, row.id) for row in rows}

    _chain(db_session, record_type=SubmissionRecordType.thermo, rows=rows, actor=actor)
    db_session.flush()
    for row_id, snapshot in before.items():
        assert _snapshot(db_session, Thermo, row_id) == snapshot, (
            "appending a supersession edge must not edit any record in the chain"
        )

    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo",
        params={"include_deprecated": "true"},
    )
    assert resp.status_code == 200
    for row_id, snapshot in before.items():
        assert _snapshot(db_session, Thermo, row_id) == snapshot, (
            "resolving the head of the chain at read time must not write anything"
        )


def test_the_head_is_computed_not_stored(db_session):
    """No column anywhere on ``thermo`` caches "is this the current one".

    ADR 0007 rejected exactly this as a stored flag: a second source of truth
    able to disagree with the ledger it summarises. If someone later adds one,
    this fails and they have to argue with the ADR rather than with a diff.
    """
    columns = set(Thermo.__table__.c.keys())
    forbidden = {
        "is_current",
        "current",
        "superseded",
        "superseded_by",
        "superseded_by_id",
        "superseding_record_id",
        "replaced_by",
        "replaced_by_id",
    }
    assert not columns & forbidden


# ---------------------------------------------------------------------------
# No database primary keys in a user-facing body (DR-0028 Req 2)
# ---------------------------------------------------------------------------


def test_the_notice_names_records_by_public_ref_only(client, db_session):
    """Both pointers are refs of the superseding *records*, never row ids.

    They are also not refs of the supersession *edge* — a reader wants to
    fetch the corrected number, not the paperwork about the correction.
    """
    entry, (first, second, third), _ = _thermo_chain(db_session)

    resp = client.get(
        f"/api/v1/scientific/species-entries/{entry.id}/thermo",
        params={"include_deprecated": "true"},
    )
    notice = _records_by_ref(resp.json())[first.public_ref]["supersession"]

    assert _PUBLIC_REF.match(notice["superseded_by"]), notice["superseded_by"]
    assert _PUBLIC_REF.match(notice["current"]), notice["current"]
    assert notice["superseded_by"].startswith("thm_")
    assert notice["current"].startswith("thm_")
    # No id-shaped key anywhere in the block...
    assert not [key for key in notice if key.endswith("_id") or key == "id"]
    # ...and exactly one integer-valued field, which is a *count* rather than a
    # pointer. Naming it explicitly matters: "no integer equal to any of our
    # row ids" looked like the right assertion and passed only by luck, since a
    # chain length of 2 collides with row id 2 on a freshly migrated database.
    # A whitelist cannot pass by luck.
    integer_fields = {key for key, value in notice.items() if isinstance(value, int)}
    assert integer_fields == {"chain_length"}
    assert notice["chain_length"] == 2
    assert set(notice) == {
        "superseded_by",
        "current",
        "reason",
        "superseded_at",
        "chain_length",
    }


# ---------------------------------------------------------------------------
# Query cost does not scale with page size
# ---------------------------------------------------------------------------


def _statmech_page(db_session, *, count: int, actor):
    """``count`` statmech rows, each the head of its own three-link chain."""
    heads = []
    for index in range(count):
        species = make_species(db_session, inchi_key=next_inchi_key(f"SQ{index:03d}"))
        entry = make_species_entry(db_session, species)
        rows = [make_statmech(db_session, species_entry=entry) for _ in range(3)]
        for row in rows:
            _approve(
                db_session,
                record_type=SubmissionRecordType.statmech,
                record_id=row.id,
                actor=actor,
            )
        _chain(
            db_session,
            record_type=SubmissionRecordType.statmech,
            rows=rows,
            actor=actor,
        )
        heads.append(rows[0])
    return heads


@pytest.mark.parametrize("page_size", [1, 3, 12])
def test_resolving_a_page_of_chains_costs_a_fixed_number_of_queries(
    db_session, page_size
):
    """One recursive walk plus one ref lookup — whatever the page size.

    Resolving a chain per row is the obvious N+1 trap in a list endpoint, and
    the trap gets *worse* exactly where the notice matters most: a page of ten
    records of which one is superseded. Counting statements rather than timing
    them is deliberate — a timing assertion on a 55-row dev database would
    pass against an N+1 implementation.
    """
    actor = _curator(db_session)
    superseded = _statmech_page(db_session, count=page_size, actor=actor)
    db_session.flush()

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db_session.get_bind(), "before_cursor_execute", record)
    try:
        notices = fetch_supersession_notices(
            db_session,
            record_type=SubmissionRecordType.statmech,
            record_ids=[row.id for row in superseded],
        )
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", record)

    assert len(notices) == page_size
    assert len(statements) == 2, (
        f"{page_size} chains cost {len(statements)} statements; the resolver "
        "must batch, not walk one chain per row:\n"
        + "\n".join(statements)
    )
    assert sum("RECURSIVE" in s.upper() for s in statements) == 1


def test_a_page_with_one_superseded_row_among_current_ones(db_session):
    """The mixed page is the case the notice exists for, and it must not
    attribute a notice to a record that has none."""
    actor = _curator(db_session)
    species = make_species(db_session, inchi_key=next_inchi_key("SUPMX"))
    entry = make_species_entry(db_session, species)
    chained = [make_statmech(db_session, species_entry=entry) for _ in range(3)]
    for row in chained:
        _approve(
            db_session,
            record_type=SubmissionRecordType.statmech,
            record_id=row.id,
            actor=actor,
        )
    _chain(
        db_session,
        record_type=SubmissionRecordType.statmech,
        rows=chained,
        actor=actor,
    )
    untouched = [make_statmech(db_session, species_entry=entry) for _ in range(4)]
    db_session.flush()

    notices = fetch_supersession_notices(
        db_session,
        record_type=SubmissionRecordType.statmech,
        record_ids=[row.id for row in chained + untouched],
    )

    assert set(notices) == {chained[0].id, chained[1].id}
    assert notices[chained[0].id].current == chained[2].public_ref
    assert notices[chained[0].id].chain_length == 2
    assert notices[chained[1].id].chain_length == 1


def test_an_empty_id_list_touches_the_database_not_at_all(db_session):
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db_session.get_bind(), "before_cursor_execute", record)
    try:
        assert (
            fetch_supersession_notices(
                db_session,
                record_type=SubmissionRecordType.statmech,
                record_ids=[],
            )
            == {}
        )
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", record)
    assert statements == []


# ---------------------------------------------------------------------------
# The other product reads carry the same block at the same address
# ---------------------------------------------------------------------------


def test_statmech_detail_read_names_both_pointers(client, db_session):
    actor = _curator(db_session)
    species = make_species(db_session, inchi_key=next_inchi_key("SUPSM"))
    entry = make_species_entry(db_session, species)
    rows = [make_statmech(db_session, species_entry=entry) for _ in range(3)]
    for row in rows:
        _approve(
            db_session,
            record_type=SubmissionRecordType.statmech,
            record_id=row.id,
            actor=actor,
        )
    _chain(
        db_session, record_type=SubmissionRecordType.statmech, rows=rows, actor=actor
    )
    before = _snapshot(db_session, Statmech, rows[0].id)

    resp = client.get(f"/api/v1/scientific/statmech/{rows[0].public_ref}")
    assert resp.status_code == 200
    notice = resp.json()["record"]["supersession"]
    assert notice["superseded_by"] == rows[1].public_ref
    assert notice["current"] == rows[2].public_ref
    assert notice["chain_length"] == 2
    assert _snapshot(db_session, Statmech, rows[0].id) == before

    head = client.get(f"/api/v1/scientific/statmech/{rows[2].public_ref}")
    assert head.status_code == 200
    assert head.json()["record"]["supersession"] is None


def test_transport_detail_read_names_both_pointers(client, db_session):
    actor = _curator(db_session)
    species = make_species(db_session, inchi_key=next_inchi_key("SUPTR"))
    entry = make_species_entry(db_session, species)
    rows = [make_transport(db_session, species_entry=entry) for _ in range(3)]
    for row in rows:
        _approve(
            db_session,
            record_type=SubmissionRecordType.transport,
            record_id=row.id,
            actor=actor,
        )
    _chain(
        db_session, record_type=SubmissionRecordType.transport, rows=rows, actor=actor
    )
    before = _snapshot(db_session, Transport, rows[0].id)

    resp = client.get(f"/api/v1/scientific/transport/{rows[0].public_ref}")
    assert resp.status_code == 200
    notice = resp.json()["record"]["supersession"]
    assert notice["superseded_by"] == rows[1].public_ref
    assert notice["current"] == rows[2].public_ref
    assert notice["chain_length"] == 2
    assert _snapshot(db_session, Transport, rows[0].id) == before

    head = client.get(f"/api/v1/scientific/transport/{rows[2].public_ref}")
    assert head.status_code == 200
    assert head.json()["record"]["supersession"] is None


def test_kinetics_read_names_both_pointers(client, db_session):
    """The same three-link assertion on the other product the brief named.

    A rate constant is the value most likely to be cited in a mechanism file
    and never looked at again, so the notice mattering here is the whole
    point.
    """
    actor = _curator(db_session)
    reactant = make_species(db_session, smiles="A", inchi_key=next_inchi_key("SUPK1"))
    product = make_species(db_session, smiles="B", inchi_key=next_inchi_key("SUPK2"))
    chem = make_chem_reaction(db_session, reactants=[reactant], products=[product])
    entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, reactant)],
        product_entries=[make_species_entry(db_session, product)],
    )
    rows = [
        make_kinetics(db_session, reaction_entry=entry, ea_kj_mol=value)
        for value in (15.0, 16.0, 17.0)
    ]
    for row in rows:
        _approve(
            db_session,
            record_type=SubmissionRecordType.kinetics,
            record_id=row.id,
            actor=actor,
        )
    _chain(
        db_session, record_type=SubmissionRecordType.kinetics, rows=rows, actor=actor
    )
    before = _snapshot(db_session, Kinetics, rows[0].id)

    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/kinetics",
        params={"include_deprecated": "true"},
    )
    assert resp.status_code == 200
    records = {rec["kinetics_ref"]: rec for rec in resp.json()["records"]}

    oldest = records[rows[0].public_ref]["supersession"]
    assert oldest["superseded_by"] == rows[1].public_ref
    assert oldest["current"] == rows[2].public_ref
    assert oldest["superseded_by"] != oldest["current"]
    assert oldest["chain_length"] == 2
    assert oldest["superseded_by"].startswith("kin_")
    assert records[rows[2].public_ref]["supersession"] is None
    assert _snapshot(db_session, Kinetics, rows[0].id) == before


# ---------------------------------------------------------------------------
# The search endpoint: the notice appears in a list, at fixed query cost
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("limit", [1, 2, 6])
def test_statmech_search_carries_the_notice_at_one_walk_per_page(
    client, db_session, limit
):
    """A list of records where one is superseded is where the notice matters
    most, and where payload weight and N+1 both push back.

    Asserted through the real HTTP endpoint, counting only the statements the
    correction notice is responsible for: exactly one recursive walk per page
    however many rows the page holds. The rest of the endpoint's query cost is
    not this feature's to claim.
    """
    actor = _curator(db_session)
    species = make_species(db_session, inchi_key=next_inchi_key("SUPSR"))
    entry = make_species_entry(db_session, species)
    chained = [make_statmech(db_session, species_entry=entry) for _ in range(3)]
    for row in chained:
        _approve(
            db_session,
            record_type=SubmissionRecordType.statmech,
            record_id=row.id,
            actor=actor,
        )
    _chain(
        db_session,
        record_type=SubmissionRecordType.statmech,
        rows=chained,
        actor=actor,
    )
    for _ in range(6):
        make_statmech(db_session, species_entry=entry)
    db_session.flush()

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", record)
    try:
        resp = client.get(
            "/api/v1/scientific/statmech/search",
            params={
                "species_entry_ref": entry.public_ref,
                "include_deprecated": "true",
                "limit": limit,
            },
        )
    finally:
        event.remove(bind, "before_cursor_execute", record)

    assert resp.status_code == 200
    walks = [s for s in statements if "supersession_walk" in s]
    assert len(walks) == 1, (
        f"limit={limit} issued {len(walks)} chain walks; the notice must cost "
        "one batched walk per page, not one per row"
    )

    records = {
        rec["statmech"]["statmech_ref"]: rec for rec in resp.json()["records"]
    }
    assert records, "the search must return the page it was asked for"
    for ref, rec in records.items():
        if ref == chained[0].public_ref:
            assert rec["supersession"]["current"] == chained[2].public_ref
            assert rec["supersession"]["chain_length"] == 2
        elif ref == chained[1].public_ref:
            assert rec["supersession"]["current"] == chained[2].public_ref
            assert rec["supersession"]["chain_length"] == 1
        else:
            assert rec["supersession"] is None


def test_thermo_search_records_carry_the_notice(client, db_session):
    """The composed search row nests the same block under ``thermo``.

    Search delegates to the detail service, so this is inherited rather than
    reimplemented -- which is exactly why it is worth pinning: nothing else
    would notice if the composed shape started dropping the field.
    """
    actor = _curator(db_session)
    species = make_species(
        db_session, smiles="C#CC", inchi_key=next_inchi_key("SUPTS")
    )
    entry = make_species_entry(db_session, species)
    rows = [
        make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=value)
        for value in (-1.0, -2.0, -3.0)
    ]
    for row in rows:
        _approve(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=row.id,
            actor=actor,
        )
    _chain(db_session, record_type=SubmissionRecordType.thermo, rows=rows, actor=actor)

    resp = client.get(
        "/api/v1/scientific/thermo/search",
        params={"smiles": "C#CC", "include_deprecated": "true"},
    )
    assert resp.status_code == 200
    found = {
        rec["thermo"]["thermo_ref"]: rec["thermo"]["supersession"]
        for rec in resp.json()["records"]
    }
    assert found[rows[0].public_ref]["superseded_by"] == rows[1].public_ref
    assert found[rows[0].public_ref]["current"] == rows[2].public_ref
    assert found[rows[2].public_ref] is None
