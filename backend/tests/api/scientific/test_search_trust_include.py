"""``include=trust`` on the five search surfaces that declared the field.

Five surfaces published a ``trust`` field that no request could fill,
because ``?include=trust`` answered ``422 unknown_include_token`` on all
five. **They failed in two shapes, and the fix for one is a no-op on the
other** — which is the whole reason this file tests them as two families
rather than as one repair repeated five times.

*Nested and unstripped* — ``/thermo/search``, ``/kinetics/search``,
``/transition-states/search``. The route never called the strip at all, so
the field serialised as a permanent ``null``. On the first two it sits one
level down, inside the ``thermo`` / ``kinetics`` wrapper, so the obvious fix
— a ``scope="search"`` strip, which pops from the record's *top* level —
would have iterated the records, popped nothing, and left every "no error
occurred" assertion passing. The measured proof that this is not
hypothetical: on ``/thermo/search``, at the *same depth inside the same
record*, ``assessments`` is correctly absent while ``trust`` was present and
null, and the only difference between them was the scope their helpers
passed.

*Stripped and unrequestable* — ``/statmech/search``, ``/transport/search``.
The strip was already there, already at the right scope, already correct.
Touching it would have been the mistake. The only edit those two needed was
to the vocabulary, at which point the existing unconditional strip becomes
conditional by itself.

So the assertions below are deliberately phrased over the **populated
path**, never over the absence of an error. "``trust`` is not at the record
root" passes on ``/thermo/search`` today, passed before anything was fixed,
and proves nothing.

Three further properties are pinned here because each is a way this change
could ship looking correct:

- ``include=all`` must not produce ``trust`` on any of the five. The
  evaluator's eager-load chain runs from 9 entries on transport to 23 on
  transition-states; ``all`` is the token a client sends when it does not
  want to think, and buying that chain on it would re-enter the cost the old
  vocabulary existed to avoid.
- ``trust`` occurs **exactly once per record**. That is what licenses the
  payload-wide scope: a walker that pops every ``trust`` at any depth is
  safe only while no other field in the payload is called ``trust`` and
  means something else. It is a property of today's response shape and
  nothing else enforces it.
- ``trust`` and ``assessments.deterministic_trust`` agree. They are two
  readouts of one evaluation by construction (see
  ``services/scientific_read/public_assessments.py``), and this is what
  would catch them drifting into two answers to one question.

The POST twins are exercised by their own fixtures rather than inherited
from the GET halves. A fix applied to one method and skipped on the other
leaves the defect live and the suite green, and these five POST operations
had never been exercised at all.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.db.models.common import (
    CalculationType,
    StatmechCalculationRole,
    TransportCalculationRole,
)
from tests.services.scientific_read._factories import (
    attach_statmech_source_calculation,
    attach_transport_source_calculation,
    make_calculation,
    make_chem_reaction,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    make_transition_state,
    make_transition_state_entry,
    make_transport,
    next_inchi_key,
)

# ---------------------------------------------------------------------------
# Seeds — one per surface, each returning the GET query string and the POST
# body that select the seeded record.
# ---------------------------------------------------------------------------


def _seed_thermo(db_session) -> tuple[str, dict[str, Any]]:
    species = make_species(db_session, inchi_key=next_inchi_key("TRT"))
    entry = make_species_entry(db_session, species)
    make_thermo_scalar(db_session, species_entry=entry)
    return (
        f"species_entry_ref={entry.public_ref}",
        {"species_entry_ref": entry.public_ref},
    )


def _seed_kinetics(db_session) -> tuple[str, dict[str, Any]]:
    reactant = make_species(db_session, smiles="[CH3]", inchi_key=next_inchi_key("TRKR"))
    product = make_species(db_session, smiles="[OH]", inchi_key=next_inchi_key("TRKP"))
    chem = make_chem_reaction(db_session, reactants=[reactant], products=[product])
    entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, reactant)],
        product_entries=[make_species_entry(db_session, product)],
    )
    make_kinetics(db_session, reaction_entry=entry)
    return (
        f"reaction_entry_ref={entry.public_ref}",
        {"reaction_entry_ref": entry.public_ref},
    )


def _seed_transition_states(db_session) -> tuple[str, dict[str, Any]]:
    sp_a = make_species(db_session, inchi_key=next_inchi_key("TRTA"))
    sp_b = make_species(db_session, inchi_key=next_inchi_key("TRTB"))
    chem = make_chem_reaction(db_session, reactants=[sp_a], products=[sp_b])
    rxe = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, sp_a)],
        product_entries=[make_species_entry(db_session, sp_b)],
    )
    ts = make_transition_state(db_session, reaction_entry=rxe)
    make_transition_state_entry(db_session, transition_state=ts)
    return (
        f"transition_state_ref={ts.public_ref}",
        {"transition_state_ref": ts.public_ref},
    )


def _seed_statmech(db_session) -> tuple[str, dict[str, Any]]:
    species = make_species(db_session, inchi_key=next_inchi_key("TRSM"))
    entry = make_species_entry(db_session, species)
    sm = make_statmech(db_session, species_entry=entry)
    calc = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=entry.id
    )
    attach_statmech_source_calculation(
        db_session,
        statmech=sm,
        calculation=calc,
        role=StatmechCalculationRole.freq,
    )
    return (
        f"species_entry_ref={entry.public_ref}",
        {"species_entry_ref": entry.public_ref},
    )


def _seed_transport(db_session) -> tuple[str, dict[str, Any]]:
    """A transport row, which is the only reason this surface is observable.

    ``/transport/search`` returns zero rows on the hosted instance, so no
    response from it has ever been seen anywhere. Every claim about its
    shape before this fixture was inference from source.
    """
    species = make_species(db_session, inchi_key=next_inchi_key("TRTR"))
    entry = make_species_entry(db_session, species)
    tr = make_transport(db_session, species_entry=entry)
    calc = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    attach_transport_source_calculation(
        db_session,
        transport=tr,
        calculation=calc,
        role=TransportCalculationRole.full_transport,
    )
    return (
        f"species_entry_ref={entry.public_ref}",
        {"species_entry_ref": entry.public_ref},
    )


# ---------------------------------------------------------------------------
# The surface table
# ---------------------------------------------------------------------------

#: ``(id, path, seed, wrapper, family)``. ``wrapper`` is the record key the
#: ``trust`` field sits under, or ``None`` when it is at the record root —
#: the difference that decides which repair a surface needed and which scope
#: its route has to pass.
SURFACES: list[tuple[str, str, Any, str | None, str]] = [
    (
        "thermo",
        "/api/v1/scientific/thermo/search",
        _seed_thermo,
        "thermo",
        "nested-and-unstripped",
    ),
    (
        "kinetics",
        "/api/v1/scientific/kinetics/search",
        _seed_kinetics,
        "kinetics",
        "nested-and-unstripped",
    ),
    (
        "transition-states",
        "/api/v1/scientific/transition-states/search",
        _seed_transition_states,
        None,
        "nested-and-unstripped",
    ),
    (
        "statmech",
        "/api/v1/scientific/statmech/search",
        _seed_statmech,
        None,
        "stripped-and-unrequestable",
    ),
    (
        "transport",
        "/api/v1/scientific/transport/search",
        _seed_transport,
        None,
        "stripped-and-unrequestable",
    ),
]

_IDS = [case[0] for case in SURFACES]


def _trust_container(record: dict[str, Any], wrapper: str | None) -> dict[str, Any]:
    """The dict ``trust`` lives in on this surface's record."""
    return record if wrapper is None else record[wrapper]


def _count_key(node: Any, key: str) -> int:
    """How many times *key* appears anywhere under *node*."""
    if isinstance(node, dict):
        return (1 if key in node else 0) + sum(
            _count_key(value, key) for value in node.values()
        )
    if isinstance(node, list):
        return sum(_count_key(value, key) for value in node)
    return 0


def _get(client, path: str, query: str, include: list[str]) -> dict[str, Any]:
    tokens = "".join(f"&include={token}" for token in include)
    resp = client.get(f"{path}?{query}{tokens}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _post(client, path: str, body: dict[str, Any], include: list[str]) -> dict[str, Any]:
    payload = dict(body)
    if include:
        payload["include"] = include
    resp = client.post(path, json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# The table itself
# ---------------------------------------------------------------------------


def test_the_table_names_five_surfaces_in_two_families():
    """A parametrisation that quietly enumerates fewer cases passes green.

    So the count and the family split are asserted rather than assumed: two
    surfaces needed the vocabulary edit alone and three needed the route to
    start calling the strip, and a table that lost one of either would still
    look like a full run.
    """
    assert len(SURFACES) == 5
    families = {case[4] for case in SURFACES}
    assert families == {"nested-and-unstripped", "stripped-and-unrequestable"}
    assert sum(1 for case in SURFACES if case[3] is not None) == 2


# ---------------------------------------------------------------------------
# The token is legal, and it produces a value
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,path,seed,wrapper,family", SURFACES, ids=_IDS)
def test_get_trust_is_absent_unrequested_and_populated_when_asked(
    client, db_session, name, path, seed, wrapper, family
):
    query, _ = seed(db_session)

    default = _get(client, path, query, [])
    assert default["records"], f"{name}: fixture produced no record to test"
    for record in default["records"]:
        container = _trust_container(record, wrapper)
        assert "trust" not in container, (
            f"{name}: 'trust' is present on a request that did not ask for "
            f"it. Unrequested means absent — a null here is the defect this "
            f"change removed, wearing the same key."
        )
    assert "trust" not in default["request"]["include"]

    asked = _get(client, path, query, ["trust"])
    assert asked["request"]["include"] == ["trust"]
    assert len(asked["records"]) == len(default["records"])
    for record in asked["records"]:
        container = _trust_container(record, wrapper)
        assert "trust" in container, (
            f"{name}: '?include=trust' was accepted and produced no field. "
            f"That is the no-op token defect in a new place."
        )
        fragment = container["trust"]
        assert fragment is not None
        # Assert the populated path, not merely a present key: a fragment
        # that came back as an empty object would satisfy 'in' and say
        # nothing about whether the evaluator ever ran.
        assert isinstance(fragment["trust_status"], str) and fragment["trust_status"]
        assert isinstance(fragment["evidence"], dict)
        assert fragment["evidence"].get("rubric")


@pytest.mark.parametrize("name,path,seed,wrapper,family", SURFACES, ids=_IDS)
def test_post_trust_is_absent_unrequested_and_populated_when_asked(
    client, db_session, name, path, seed, wrapper, family
):
    """The POST twin, by its own fixture rather than by inheritance.

    All five of these POST operations are declared in the hosted document
    and none had ever been exercised. A fix applied to the GET half and
    skipped on the POST half leaves the defect live and the suite green,
    which is exactly why they are not asserted here as "same as GET".
    """
    _, body = seed(db_session)

    default = _post(client, path, body, [])
    assert default["records"], f"{name}: fixture produced no record to test"
    for record in default["records"]:
        assert "trust" not in _trust_container(record, wrapper)

    asked = _post(client, path, body, ["trust"])
    assert asked["request"]["include"] == ["trust"]
    for record in asked["records"]:
        fragment = _trust_container(record, wrapper)["trust"]
        assert fragment is not None
        assert fragment["trust_status"]


# ---------------------------------------------------------------------------
# include=all must not buy the graph
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,path,seed,wrapper,family", SURFACES, ids=_IDS)
def test_include_all_does_not_produce_trust(
    client, db_session, name, path, seed, wrapper, family
):
    """Legal is not free.

    ``trust`` is internal-tokenized on every one of the five, exactly as it
    already was on the detail surfaces. Without that, one ``include=all``
    on a search page silently buys the whole evidence graph — the cost the
    old vocabulary existed to prevent, re-entered through the door marked
    convenience, and the line whose omission would be discovered last.
    """
    query, body = seed(db_session)

    for kind, response in (
        ("GET", _get(client, path, query, ["all"])),
        ("POST", _post(client, path, body, ["all"])),
    ):
        assert "trust" not in response["request"]["include"], (
            f"{name} {kind}: include=all resolved to "
            f"{response['request']['include']!r}, which contains 'trust'."
        )
        for record in response["records"]:
            assert "trust" not in _trust_container(record, wrapper), (
                f"{name} {kind}: include=all produced a trust fragment."
            )


# ---------------------------------------------------------------------------
# What licenses the payload-wide scope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,path,seed,wrapper,family", SURFACES, ids=_IDS)
def test_trust_occurs_exactly_once_per_record(
    client, db_session, name, path, seed, wrapper, family
):
    """The safety condition for a payload-wide pop, asserted not assumed.

    ``ANYWHERE_SCOPE`` is scope-blind: it pops the named key from every dict
    at any depth. That is safe for ``trust`` only while ``trust`` names one
    thing everywhere it occurs in these payloads. It is unsafe for names
    like ``points``, ``literature`` and ``parameters``, which are
    include-gated in one place and ungated scientific facts in another — so
    the licence has to be checked for this key, on these responses, rather
    than inherited from the fact that it worked for ``assessments``.

    Checked with the token requested, because that is the state in which a
    ``trust`` key exists at all: counting zero occurrences in a default
    response would be an assertion over nothing.
    """
    query, _ = seed(db_session)
    body = _get(client, path, query, ["trust"])

    assert body["records"]
    for record in body["records"]:
        assert _count_key(record, "trust") == 1, (
            f"{name}: 'trust' occurs {_count_key(record, 'trust')} times in "
            f"one record. A payload-wide strip pops all of them, so a second "
            f"occurrence meaning something else would be silently deleted."
        )
    # And nowhere outside the records — the envelope must not carry one.
    envelope = {k: v for k, v in body.items() if k != "records"}
    assert _count_key(envelope, "trust") == 0


# ---------------------------------------------------------------------------
# One rubric source, not two
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,path,seed,wrapper,family",
    [case for case in SURFACES if case[0] in {"thermo", "kinetics", "statmech", "transport"}],
    ids=["thermo", "kinetics", "statmech", "transport"],
)
def test_trust_and_assessments_report_the_same_verdict(
    client, db_session, name, path, seed, wrapper, family
):
    """Two readouts of one evaluation, not two opinions.

    ``assessments.deterministic_trust`` already answered "how much do you
    trust this" on these surfaces, under a different token and in a
    different shape. Making ``trust`` requestable created a second path to
    the same question, and the two must be derived from one source or a
    consumer gets two answers and no rule for choosing. They are: both come
    from a single ``_evaluate`` call in ``public_assessments``. This is what
    would notice if that stopped being true.

    ``/transition-states/search`` is excluded because it has no
    ``assessments`` token — there is no second readout there to disagree
    with.
    """
    query, _ = seed(db_session)
    body = _get(client, path, query, ["trust", "assessments"])

    assert body["records"]
    for record in body["records"]:
        container = _trust_container(record, wrapper)
        fragment = container["trust"]
        deterministic = container["assessments"]["deterministic_trust"]
        assert fragment["trust_status"] == deterministic["grade"], (
            f"{name}: trust.trust_status={fragment['trust_status']!r} but "
            f"assessments.deterministic_trust.grade={deterministic['grade']!r}. "
            f"Two answers to one question."
        )
        # The fragment's rubric carries an explicit ``_vN`` suffix; the
        # compact readout reports name and version separately. Same rubric.
        assert fragment["evidence"]["rubric"] == (
            f"{deterministic['rubric']}_v{deterministic['rubric_version']}"
        )


# ---------------------------------------------------------------------------
# The two families, named
# ---------------------------------------------------------------------------


def test_the_two_nested_surfaces_carry_trust_below_the_record_root(
    client, db_session
):
    """Where the field actually sits, which is what sank the obvious fix.

    A ``scope="search"`` strip pops from ``records[*]`` — the record's top
    level. On these two that top level is exactly ``['species', 'thermo']``
    and ``['reaction', 'kinetics']``, so such a strip would pop nothing and
    every "no error" assertion would still pass. Recording the depth here
    means a future reader does not have to rediscover it from a 422.
    """
    thermo_query, _ = _seed_thermo(db_session)
    kinetics_query, _ = _seed_kinetics(db_session)

    thermo = _get(
        client, "/api/v1/scientific/thermo/search", thermo_query, ["trust"]
    )
    kinetics = _get(
        client, "/api/v1/scientific/kinetics/search", kinetics_query, ["trust"]
    )

    thermo_record = thermo["records"][0]
    assert set(thermo_record) == {"species", "thermo"}
    assert "trust" not in thermo_record
    assert thermo_record["thermo"]["trust"] is not None

    kinetics_record = kinetics["records"][0]
    assert set(kinetics_record) == {"reaction", "kinetics"}
    assert "trust" not in kinetics_record
    assert kinetics_record["kinetics"]["trust"] is not None


def test_the_two_stripped_surfaces_needed_no_change_to_their_strip(
    client, db_session
):
    """statmech and transport were already stripping correctly.

    Their defect was the vocabulary alone: a strip that ran unconditionally
    because the token that would have made it conditional did not exist. The
    observable consequence of the fix is that the same unconditional call is
    now conditional — absent by default, present when asked — with the call
    itself unchanged.
    """
    statmech_query, _ = _seed_statmech(db_session)
    transport_query, _ = _seed_transport(db_session)

    for path, query in (
        ("/api/v1/scientific/statmech/search", statmech_query),
        ("/api/v1/scientific/transport/search", transport_query),
    ):
        default = _get(client, path, query, [])
        asked = _get(client, path, query, ["trust"])
        assert default["records"] and asked["records"]
        assert all("trust" not in r for r in default["records"])
        assert all(r["trust"] is not None for r in asked["records"])


def test_nested_entries_carry_trust_on_the_same_terms_as_their_parent(
    client, db_session
):
    """``trust`` means one thing at every depth of the TS search payload.

    This is the case where the count in the licence test above goes above
    one per record: ``?include=entries&include=trust`` nests entry records
    that carry the same field. They are populated on the same terms as the
    record they hang off — the token governs the whole response, not one
    depth of it — and they are stripped on the same terms too, so a
    payload-wide pop still deletes exactly the fields the token governs and
    nothing else.

    The alternative, leaving the nested ones unpopulated, would have put a
    permanently-null ``trust`` back into the payload at depth: the very
    shape this change removed from the record root.
    """
    sp_a = make_species(db_session, inchi_key=next_inchi_key("TRNA"))
    sp_b = make_species(db_session, inchi_key=next_inchi_key("TRNB"))
    chem = make_chem_reaction(db_session, reactants=[sp_a], products=[sp_b])
    rxe = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, sp_a)],
        product_entries=[make_species_entry(db_session, sp_b)],
    )
    ts = make_transition_state(db_session, reaction_entry=rxe)
    for _ in range(2):
        make_transition_state_entry(db_session, transition_state=ts)

    path = "/api/v1/scientific/transition-states/search"
    query = f"transition_state_ref={ts.public_ref}"

    both = _get(client, path, query, ["entries", "trust"])
    assert len(both["records"]) == 2
    for record in both["records"]:
        assert record["trust"] is not None
        assert len(record["entries"]) == 2
        for nested in record["entries"]:
            assert nested["trust"] is not None
        # One per record, counting the nested ones: 1 root + 2 entries.
        assert _count_key(record, "trust") == 3

    # And with the token withheld, the payload-wide strip reaches all of
    # them — nothing is left nulled at depth.
    entries_only = _get(client, path, query, ["entries"])
    for record in entries_only["records"]:
        assert _count_key(record, "trust") == 0


def test_an_unknown_token_is_still_rejected_on_all_five(client, db_session):
    """Widening a vocabulary must not widen it to everything.

    The five surfaces each gained exactly one token. A caller's typo still
    has to come back as a ``422`` naming the legal set, because under the
    omission contract the echo is the only thing that makes an absent key
    interpretable — and a typo that returns ``200`` is indistinguishable
    from a correct request.
    """
    for _, path, seed, _, _ in SURFACES:
        query, _body = seed(db_session)
        resp = client.get(f"{path}?{query}&include=trsut")
        assert resp.status_code == 422, f"{path}: {resp.status_code}"
        detail = json.dumps(resp.json())
        assert "unknown_include_token" in detail
        assert "trust" in detail
