"""Three legal include tokens that were accepted and produced nothing.

Measured against the hosted instance, each was a `200` that changed no key
in the response:

- ``entries`` on ``GET /transition-states/search`` — accepted, and then
  **dropped from the echo**: ``request.include`` came back ``[]``, so the
  response reported understanding a request the caller had not made.
- ``validation_evidence`` on ``GET /transition-states/{ref}`` — echoed
  correctly, but the concept record had no such field, while its own
  ``available_sections`` advertised ``has_validation_evidence`` on the same
  payload.
- ``observations`` on ``GET /conformer-observations/{ref}`` — echoed, no
  field, in either state.

They are fixed rather than retired. Retiring a token breaks any client
already sending it, and looks free only because nothing currently depends on
the behaviour — which is equally an argument for making it work. Each also
names a real question the surface could not otherwise answer.

**One rule settles all three:** on a member-grained record, a collection
token populates the collection the record belongs to. A TS-entry's
collection is its parent transition state's entries; an observation's is its
conformer group's observations. The concept surface's
``validation_evidence`` is the one that is not member-grained, and it takes
the shape that follows from that: a list keyed by entry ref, never a union
across entries.

The reason this is load-bearing rather than cosmetic is the *next* change.
The flip to omitted-when-unrequested is tested as "an unrequested section
key is absent, a requested one is present". For a token with no field
neither half holds, so a parametrisation generated from the legal-token sets
would need an expected-no-field exemption list — and an exemption list is
exactly where a vacuous green hides. After this, there is no token left to
exempt.
"""

from __future__ import annotations

from app.db.models.common import (
    CalculationType,
    TransitionStateEntryStatus,
)
from app.db.models.transition_state import TransitionStateValidationEvidence
from tests.services.scientific_read._factories import (
    make_calculation,
    make_chem_reaction,
    make_conformer_group,
    make_conformer_observation,
    make_geometry,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_ts_with_entries(db_session, *, n_entries: int = 3):
    """One transition state carrying *n_entries* entries."""
    sp_a = make_species(db_session, inchi_key=next_inchi_key("NOA"))
    sp_b = make_species(db_session, inchi_key=next_inchi_key("NOB"))
    chem = make_chem_reaction(db_session, reactants=[sp_a], products=[sp_b])
    rxe = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, sp_a)],
        product_entries=[make_species_entry(db_session, sp_b)],
    )
    ts = make_transition_state(db_session, reaction_entry=rxe)
    entries = [
        make_transition_state_entry(
            db_session,
            transition_state=ts,
            status=TransitionStateEntryStatus.optimized,
        )
        for _ in range(n_entries)
    ]
    return ts, entries


# ---------------------------------------------------------------------------
# entries on /transition-states/search
# ---------------------------------------------------------------------------


def test_entries_on_ts_search_is_echoed_rather_than_discarded(
    client, db_session
):
    """The token used to vanish from the resolved set before anything read it.

    That is worse than producing no field: the echo is what makes an absent
    key interpretable under the omission contract, and an echo that silently
    disagrees with the request tells the caller their token was understood as
    something they did not send.
    """
    ts, _entries = _make_ts_with_entries(db_session, n_entries=2)

    body = client.get(
        "/api/v1/scientific/transition-states/search"
        f"?transition_state_ref={ts.public_ref}&include=entries"
    ).json()

    assert body["request"]["include"] == ["entries"]


def test_entries_on_ts_search_returns_the_parents_entry_list(
    client, db_session
):
    """Every record gets its parent's entries — including the record itself.

    The search is entry-grained: each record is one entry plus its parent's
    core block. "What else is under this transition state" is the one piece
    of context it cannot otherwise give, and it is the same list, in the same
    shape, that the concept surface returns under the same token.
    """
    ts, entries = _make_ts_with_entries(db_session, n_entries=3)
    expected_refs = sorted(e.public_ref for e in entries)

    body = client.get(
        "/api/v1/scientific/transition-states/search"
        f"?transition_state_ref={ts.public_ref}&include=entries"
    ).json()

    assert len(body["records"]) == 3
    for record in body["records"]:
        block = record["entries"]
        assert block is not None, (
            "include=entries was accepted and produced no field: the no-op "
            "this change exists to remove."
        )
        refs = sorted(
            e["transition_state_entry"]["transition_state_entry_ref"]
            for e in block
        )
        assert refs == expected_refs
        # The nested records must not each carry their own sibling list —
        # that recursion has no base case, and the list would be identical
        # at every level anyway.
        assert all(e["entries"] is None for e in block)


def test_entries_is_absent_on_ts_search_when_not_requested(client, db_session):
    """A field that appears only under its token, like every other section."""
    ts, _entries = _make_ts_with_entries(db_session, n_entries=2)

    body = client.get(
        "/api/v1/scientific/transition-states/search"
        f"?transition_state_ref={ts.public_ref}"
    ).json()

    assert body["request"]["include"] == []
    assert all("entries" not in record for record in body["records"])


def test_include_all_on_ts_search_does_not_expand_to_entries(
    client, db_session
):
    """``entries`` has to be asked for by name on the search surface.

    Not because it is private, but because its cost follows the fan-out
    under the page's parents: a record's block is every entry of its
    transition state, each a full record build. It was *discarded* on this
    surface before, so ``include=all`` never paid for it — keeping it out of
    the expansion means ``include=all`` here returns what it always did, and
    the one genuinely new cost on this surface is one a caller opted into.

    It stays inside ``include=all`` on the two detail surfaces, where the
    block is bounded by a single record's parent.
    """
    ts, _entries = _make_ts_with_entries(db_session, n_entries=2)

    body = client.get(
        "/api/v1/scientific/transition-states/search"
        f"?transition_state_ref={ts.public_ref}&include=all"
    ).json()

    assert "entries" not in body["request"]["include"]
    assert all("entries" not in record for record in body["records"])


def test_entries_on_ts_search_shares_one_list_per_parent(client, db_session):
    """Three records under one transition state resolve one sibling list.

    Search pages cluster: several entries of the same transition state
    routinely match the same filter. Resolving the same list once per record
    would be a fan-out proportional to the page, so it is grouped by parent
    and the records share the result — which is observable as the blocks
    being equal.
    """
    ts, _entries = _make_ts_with_entries(db_session, n_entries=3)

    body = client.get(
        "/api/v1/scientific/transition-states/search"
        f"?transition_state_ref={ts.public_ref}&include=entries"
    ).json()

    blocks = [record["entries"] for record in body["records"]]
    assert len(blocks) == 3
    assert all(block == blocks[0] for block in blocks)


# ---------------------------------------------------------------------------
# validation_evidence on /transition-states/{ref}
# ---------------------------------------------------------------------------


def test_validation_evidence_on_ts_concept_is_keyed_by_entry(
    client, db_session
):
    """One list per entry, never a union across them.

    A concept is a collection of entries computed at different levels of
    theory. An OR across their evidence would report "validated" for a
    concept whose entries disagree and would not say which entry carried the
    evidence — the aggregation error ``0a6271c8`` removed from
    conformer-group evidence summaries. So the block names the entry.
    """
    ts, entries = _make_ts_with_entries(db_session, n_entries=2)
    validated, unvalidated = entries
    calc = make_calculation(
        db_session,
        type=CalculationType.irc,
        transition_state_entry_id=validated.id,
    )
    geometry = make_geometry(db_session, natoms=2)
    db_session.add(
        TransitionStateValidationEvidence(
            transition_state_entry_id=validated.id,
            kind="irc",
            passed=True,
            rationale="forward and reverse endpoints match participants",
            reconstruction_calculation_id=calc.id,
            reactant_participant_mapping={"reactant:1": [1, 2]},
            product_participant_mapping={"product:1": [1, 2]},
            transition_state_geometry_id=geometry.id,
        )
    )
    db_session.flush()

    body = client.get(
        f"/api/v1/scientific/transition-states/{ts.public_ref}"
        "?include=validation_evidence"
    ).json()

    record = body["record"]
    assert body["request"]["include"] == ["validation_evidence"]
    assert record["available_sections"]["has_validation_evidence"] is True
    block = record["validation_evidence"]
    assert block is not None, (
        "the concept surface advertised has_validation_evidence and accepted "
        "the token while having no field to put the answer in."
    )
    by_ref = {
        item["transition_state_entry_ref"]: item["validation_evidence"]
        for item in block
    }
    assert set(by_ref) == {validated.public_ref, unvalidated.public_ref}
    assert len(by_ref[validated.public_ref]) == 1
    assert by_ref[validated.public_ref][0]["kind"] == "irc"
    assert by_ref[validated.public_ref][0]["passed"] is True
    assert (
        by_ref[validated.public_ref][0]["reconstruction_calculation_ref"]
        == calc.public_ref
    )
    # The entry that deposited nothing gets an empty list, not an omission:
    # "this entry has no IRC evidence" is a fact about the entry, and the
    # caller asked.
    assert by_ref[unvalidated.public_ref] == []


def test_validation_evidence_is_absent_on_ts_concept_when_not_requested(
    client, db_session
):
    ts, _entries = _make_ts_with_entries(db_session, n_entries=1)

    body = client.get(
        f"/api/v1/scientific/transition-states/{ts.public_ref}"
    ).json()

    assert "validation_evidence" not in body["record"]


# ---------------------------------------------------------------------------
# observations on /conformer-observations/{ref}
# ---------------------------------------------------------------------------


def _make_group_with_observations(db_session, *, n: int = 3):
    species = make_species(db_session, inchi_key=next_inchi_key("NOC"))
    entry = make_species_entry(db_session, species)
    group = make_conformer_group(db_session, species_entry=entry)
    observations = [
        make_conformer_observation(db_session, conformer_group=group)
        for _ in range(n)
    ]
    return group, observations


def test_observations_on_an_observation_returns_the_basins_observations(
    client, db_session
):
    """The sibling observations in the same conformer group, this one included.

    The observation record already embeds its ``conformer_group``, so the
    parent is resolved before the token is read; what the token adds is the
    rest of the basin, which an observation-grained record has no other way
    to report.
    """
    _group, observations = _make_group_with_observations(db_session, n=3)
    subject = observations[0]
    expected_refs = sorted(o.public_ref for o in observations)

    body = client.get(
        f"/api/v1/scientific/conformer-observations/{subject.public_ref}"
        "?include=observations"
    ).json()

    assert body["request"]["include"] == ["observations"]
    block = body["record"]["observations"]
    assert block is not None, (
        "include=observations was accepted and produced no field."
    )
    refs = sorted(
        o["conformer_observation"]["conformer_observation_ref"] for o in block
    )
    assert refs == expected_refs
    # Same recursion guard as the TS entries block.
    assert all(o["observations"] is None for o in block)


def test_observations_is_absent_on_an_observation_when_not_requested(
    client, db_session
):
    _group, observations = _make_group_with_observations(db_session, n=2)

    body = client.get(
        f"/api/v1/scientific/conformer-observations/{observations[0].public_ref}"
    ).json()

    assert "observations" not in body["record"]


def test_the_group_surface_still_returns_its_observations_without_nesting(
    client, db_session
):
    """The group's own ``include=observations`` is unchanged in shape.

    It is the surface the new block borrowed its shape from, so a regression
    here would mean the two have quietly diverged — and the embedded records
    must not have grown sibling lists of their own, which on a group of *n*
    observations would be *n* copies of the same list.
    """
    group, observations = _make_group_with_observations(db_session, n=3)

    body = client.get(
        f"/api/v1/scientific/conformer-groups/{group.public_ref}"
        "?include=observations"
    ).json()

    block = body["record"]["observations"]
    assert len(block) == len(observations)
    assert all(o["observations"] is None for o in block)
