"""API tests for GET /api/v1/scientific/reactions/browse.

Companion to ``test_api_reaction_search.py``. Reuses that module's
reasoning: the browse surface shares its query builder and record shape
with ``/reactions/search`` (see
:func:`app.services.scientific_read.reactions.browse_reactions`); only
"no filter required" is new.
"""

from __future__ import annotations

from urllib.parse import urlencode

from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.schemas.reads._field_bounds import MAX_PARTICIPANTS_PER_REACTION
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    next_inchi_key,
    set_review,
)


def _browse_url(**params) -> str:
    base = "/api/v1/scientific/reactions/browse"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


def _browse_url_multi(**params) -> str:
    """Build a browse URL where a list-valued param repeats the key.

    ``urlencode(..., doseq=True)`` turns ``{"reactant_smiles": ["A", "B"]}``
    into ``reactant_smiles=A&reactant_smiles=B`` -- the wire shape a real
    client uses to supply more than one SMILES per side.
    """
    base = "/api/v1/scientific/reactions/browse"
    if not params:
        return base
    return f"{base}?{urlencode(params, doseq=True)}"


def _entry(db_session, *, reactant_smiles="A", product_smiles="B", family=None):
    rs = make_species(db_session, smiles=reactant_smiles, inchi_key=next_inchi_key("RBRW1"))
    ps = make_species(db_session, smiles=product_smiles, inchi_key=next_inchi_key("RBRW2"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    if family is not None:
        from app.db.models.reaction import ReactionFamily

        fam = db_session.query(ReactionFamily).filter_by(name=family).first()
        if fam is None:
            fam = ReactionFamily(name=family)
            db_session.add(fam)
            db_session.flush()
        chem.reaction_family_id = fam.id
        db_session.flush()
    return make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )


def _entry_multi(db_session, *, reactant_smiles: list[str], product_smiles: list[str]):
    """Like ``_entry`` but with an arbitrary number of reactants/products.

    Needed for the multi-SMILES ``contains`` (set-containment/AND) tests,
    which require a stored reaction with more than one participant on a
    side to distinguish "any one of these" from "all of these".
    """
    reactant_species = [
        make_species(db_session, smiles=s, inchi_key=next_inchi_key("RBRWM1"))
        for s in reactant_smiles
    ]
    product_species = [
        make_species(db_session, smiles=s, inchi_key=next_inchi_key("RBRWM2"))
        for s in product_smiles
    ]
    chem = make_chem_reaction(
        db_session, reactants=reactant_species, products=product_species
    )
    return make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[
            make_species_entry(db_session, sp) for sp in reactant_species
        ],
        product_entries=[
            make_species_entry(db_session, sp) for sp in product_species
        ],
    )


# ---------------------------------------------------------------------------
# The headline defect this route exists to fix
# ---------------------------------------------------------------------------


def test_get_with_no_query_params_returns_200_not_422(client, db_session):
    """The exact call that 422s on /search today must 200 here."""
    _entry(db_session)

    search_resp = client.get("/api/v1/scientific/reactions/search")
    assert search_resp.status_code == 422
    assert "missing_reaction_search_filter" in search_resp.text

    browse_resp = client.get(_browse_url())
    assert browse_resp.status_code == 200


def test_browse_returns_the_same_record_shape_as_search(client, db_session):
    entry = _entry(db_session)

    search_body = client.get(
        f"/api/v1/scientific/reactions/search?reaction_entry_ref={entry.public_ref}"
    ).json()
    browse_body = client.get(_browse_url()).json()

    search_record = search_body["records"][0]
    browse_record = next(
        r for r in browse_body["records"] if r["reaction_entry_ref"] == entry.public_ref
    )
    assert set(search_record.keys()) == set(browse_record.keys())
    for key in ("reaction_ref", "reaction_entry_ref", "equation", "reversible", "family"):
        assert search_record[key] == browse_record[key]
    assert search_record["reactants"] == browse_record["reactants"]
    assert search_record["products"] == browse_record["products"]


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_family_filter_narrows_results(client, db_session):
    matching = _entry(db_session, reactant_smiles="C", product_smiles="N", family="h_abstraction")
    _entry(db_session, reactant_smiles="O", product_smiles="F")

    body = client.get(_browse_url(family="h_abstraction")).json()
    refs = {r["reaction_entry_ref"] for r in body["records"]}
    assert refs == {matching.public_ref}


def test_unknown_family_returns_empty_not_422(client, db_session):
    _entry(db_session)
    body = client.get(_browse_url(family="not_a_real_family")).json()
    assert body["records"] == []


def test_reactant_smiles_filter_narrows_results(client, db_session):
    matching = _entry(db_session, reactant_smiles="[NH2]", product_smiles="N")
    _entry(db_session, reactant_smiles="O", product_smiles="F")

    body = client.get(_browse_url(reactant_smiles="%5BNH2%5D")).json()
    refs = {r["reaction_entry_ref"] for r in body["records"]}
    assert refs == {matching.public_ref}


def test_product_smiles_filter_narrows_results(client, db_session):
    matching = _entry(db_session, reactant_smiles="C", product_smiles="[OH]")
    _entry(db_session, reactant_smiles="O", product_smiles="F")

    body = client.get(_browse_url(product_smiles="%5BOH%5D")).json()
    refs = {r["reaction_entry_ref"] for r in body["records"]}
    assert refs == {matching.public_ref}


def test_has_kinetics_filter(client, db_session):
    with_kinetics = _entry(db_session, reactant_smiles="C", product_smiles="N")
    make_kinetics(db_session, reaction_entry=with_kinetics)
    without_kinetics = _entry(db_session, reactant_smiles="O", product_smiles="F")

    body = client.get(_browse_url(has_kinetics="true")).json()
    refs = {r["reaction_entry_ref"] for r in body["records"]}
    assert with_kinetics.public_ref in refs
    assert without_kinetics.public_ref not in refs

    body_false = client.get(_browse_url(has_kinetics="false")).json()
    refs_false = {r["reaction_entry_ref"] for r in body_false["records"]}
    assert without_kinetics.public_ref in refs_false
    assert with_kinetics.public_ref not in refs_false


def test_has_transition_state_filter(client, db_session):
    with_ts = _entry(db_session, reactant_smiles="C", product_smiles="N")
    make_transition_state(db_session, reaction_entry=with_ts)
    without_ts = _entry(db_session, reactant_smiles="O", product_smiles="F")

    body = client.get(_browse_url(has_transition_state="true")).json()
    refs = {r["reaction_entry_ref"] for r in body["records"]}
    assert with_ts.public_ref in refs
    assert without_ts.public_ref not in refs


def test_min_review_status_filters_by_entry_review(client, db_session):
    approved = _entry(db_session, reactant_smiles="C", product_smiles="N")
    set_review(
        db_session,
        record_type=SubmissionRecordType.reaction_entry,
        record_id=approved.id,
        status=RecordReviewStatus.approved,
    )
    unreviewed = _entry(db_session, reactant_smiles="O", product_smiles="F")

    body = client.get(
        _browse_url(min_review_status=RecordReviewStatus.approved.value)
    ).json()
    refs = {r["reaction_entry_ref"] for r in body["records"]}
    assert refs == {approved.public_ref}
    assert unreviewed.public_ref not in refs


def test_include_rejected_returns_rejected_entries(client, db_session):
    visible = _entry(db_session, reactant_smiles="C", product_smiles="N")
    rejected = _entry(db_session, reactant_smiles="O", product_smiles="F")
    set_review(
        db_session,
        record_type=SubmissionRecordType.reaction_entry,
        record_id=rejected.id,
        status=RecordReviewStatus.rejected,
    )

    default_refs = {
        r["reaction_entry_ref"] for r in client.get(_browse_url()).json()["records"]
    }
    assert visible.public_ref in default_refs
    assert rejected.public_ref not in default_refs

    included_refs = {
        r["reaction_entry_ref"]
        for r in client.get(_browse_url(include_rejected="true")).json()["records"]
    }
    assert rejected.public_ref in included_refs


def test_include_deprecated_returns_deprecated_entries(client, db_session):
    visible = _entry(db_session, reactant_smiles="C", product_smiles="N")
    deprecated = _entry(db_session, reactant_smiles="O", product_smiles="F")
    set_review(
        db_session,
        record_type=SubmissionRecordType.reaction_entry,
        record_id=deprecated.id,
        status=RecordReviewStatus.deprecated,
    )

    default_refs = {
        r["reaction_entry_ref"] for r in client.get(_browse_url()).json()["records"]
    }
    assert visible.public_ref in default_refs
    assert deprecated.public_ref not in default_refs

    included_refs = {
        r["reaction_entry_ref"]
        for r in client.get(_browse_url(include_deprecated="true")).json()["records"]
    }
    assert deprecated.public_ref in included_refs


def test_offset_and_limit_page_results(client, db_session):
    for i in range(2, 5):
        _entry(db_session, reactant_smiles=f"[{i}H]", product_smiles="N")
    body_full = client.get(_browse_url(limit=200)).json()
    assert len(body_full["records"]) >= 3

    body_page = client.get(_browse_url(offset=0, limit=1)).json()
    assert len(body_page["records"]) == 1
    assert body_page["pagination"]["total"] == body_full["pagination"]["total"]


# ---------------------------------------------------------------------------
# Sort / echo
# ---------------------------------------------------------------------------


def test_default_sort_orders_records_review_rank_then_has_kinetics(client, db_session):
    """Asserts both the echoed sort label and the actual row order it names.

    Three entries, distinguished on the sort key's first two axes:
    ``review_rank`` (approved beats not_reviewed) beats ``has_kinetics``
    (a not_reviewed entry with kinetics still sorts after an approved
    entry with none), and among equal review rank, ``has_kinetics``
    breaks the tie. The echoed string alone (asserted first) is a label a
    caller can display; it is not proof of the order actually served, so
    every assertion below checks real row positions.
    """
    best = _entry(db_session, reactant_smiles="[2H]", product_smiles="N")
    set_review(
        db_session,
        record_type=SubmissionRecordType.reaction_entry,
        record_id=best.id,
        status=RecordReviewStatus.approved,
    )
    mid = _entry(db_session, reactant_smiles="[3H]", product_smiles="N")
    make_kinetics(db_session, reaction_entry=mid)
    worst = _entry(db_session, reactant_smiles="[4H]", product_smiles="N")

    body = client.get(_browse_url(limit=200)).json()
    assert body["request"]["sort"] == (
        "review_rank,has_kinetics,has_transition_state,created_at,id"
    )

    refs = [r["reaction_entry_ref"] for r in body["records"]]
    positions = {ref: i for i, ref in enumerate(refs)}

    assert positions[best.public_ref] < positions[mid.public_ref]
    assert positions[mid.public_ref] < positions[worst.public_ref]


# ---------------------------------------------------------------------------
# Multiple SMILES per side (repeated query parameter)
# ---------------------------------------------------------------------------


def test_single_reactant_smiles_still_behaves_as_before(client, db_session):
    """A single repeated-param value matches the same records as the old
    scalar filter did (the echoed filter *shape* changed -- string to a
    one-item list -- but the matched records and ``matched_direction``
    did not)."""
    matching = _entry(db_session, reactant_smiles="[NH2]", product_smiles="N")
    _entry(db_session, reactant_smiles="O", product_smiles="F")

    body = client.get(_browse_url_multi(reactant_smiles="[NH2]")).json()
    refs = {r["reaction_entry_ref"] for r in body["records"]}
    assert refs == {matching.public_ref}


def test_empty_reactant_smiles_value_means_unfiltered(client, db_session):
    """``?reactant_smiles=`` (present, empty) must still mean "no filter".

    FastAPI parses a present-but-empty repeated query value as ``[""]``
    for a ``list[str]`` parameter -- a non-empty, truthy list containing
    one blank string. Without stripping that blank before it reaches
    :class:`~app.schemas.reads.scientific_reactions.ReactionsBrowseRequest`,
    it would hit the unresolvable-SMILES guard (no species has an
    empty-string SMILES) and hard-empty the response -- a regression
    from the old ``str | None`` filter, where an empty string was
    itself falsy and therefore already meant "not supplied".
    """
    entry = _entry(db_session, reactant_smiles="EMPTYVAL_A", product_smiles="EMPTYVAL_B")

    unfiltered = client.get(_browse_url()).json()
    empty_value = client.get(_browse_url(reactant_smiles="")).json()

    assert empty_value["pagination"]["total"] == unfiltered["pagination"]["total"]
    refs = {r["reaction_entry_ref"] for r in empty_value["records"]}
    assert entry.public_ref in refs


def test_empty_product_smiles_value_means_unfiltered(client, db_session):
    """Same regression check, product side."""
    entry = _entry(db_session, reactant_smiles="EMPTYVALP_A", product_smiles="EMPTYVALP_B")

    unfiltered = client.get(_browse_url()).json()
    empty_value = client.get(_browse_url(product_smiles="")).json()

    assert empty_value["pagination"]["total"] == unfiltered["pagination"]["total"]
    refs = {r["reaction_entry_ref"] for r in empty_value["records"]}
    assert entry.public_ref in refs


def test_mixed_reactant_and_product_smiles_in_one_group_does_not_match(
    client, db_session
):
    """Documents the corrected claim in the field descriptions: a
    ``reactant_smiles`` group mixing a genuine reactant with a genuine
    product of the SAME reaction matches in neither orientation, because
    every member of the group must land on the same stored side at once
    -- there is no orientation where one stored side holds both a
    reaction's reactant and its product.
    """
    entry = _entry_multi(db_session, reactant_smiles=["MIX_A"], product_smiles=["MIX_C"])

    body = client.get(_browse_url_multi(reactant_smiles=["MIX_A", "MIX_C"])).json()
    refs = {r["reaction_entry_ref"] for r in body["records"]}
    assert entry.public_ref not in refs


def test_two_reactant_smiles_requires_both_present(client, db_session):
    """Multiple values on one side are an AND (set containment), not an OR.

    A reaction with both A and B among its reactants matches
    ``?reactant_smiles=A&reactant_smiles=B``; a reaction with only A does
    not, even though A alone would match a single-value query.
    """
    both = _entry_multi(
        db_session, reactant_smiles=["MSA", "MSB"], product_smiles=["MSC"]
    )
    only_one = _entry_multi(
        db_session, reactant_smiles=["MSA"], product_smiles=["MSD"]
    )

    body = client.get(_browse_url_multi(reactant_smiles=["MSA", "MSB"])).json()
    refs = {r["reaction_entry_ref"] for r in body["records"]}
    assert refs == {both.public_ref}
    assert only_one.public_ref not in refs


def test_unresolvable_reactant_smiles_among_valid_ones_returns_empty(client, db_session):
    """The correctness trap this task exists to fix.

    One SMILES resolves to a real species that IS among this reaction's
    reactants; the other does not resolve to any species TCKDB has at
    all. The old scalar-filter guard (``if reactant_smiles and not ids``)
    would have passed here because *some* id resolved, silently
    degrading to a one-species search and returning the reaction below as
    if the caller had only asked for the resolvable SMILES. The correct
    behaviour is an empty response: no stored reaction can contain a
    species the archive does not have.
    """
    entry = _entry(db_session, reactant_smiles="UNRES_A", product_smiles="UNRES_C")

    body = client.get(
        _browse_url_multi(
            reactant_smiles=["UNRES_A", "totally-unresolvable-smiles-xyz"]
        )
    ).json()
    assert body["records"] == []

    # Sanity: the resolvable SMILES alone WOULD have matched, so the empty
    # result above is caused by the unresolved second SMILES, not by
    # "UNRES_A" failing to be a real reactant.
    solo = client.get(_browse_url_multi(reactant_smiles=["UNRES_A"])).json()
    assert {r["reaction_entry_ref"] for r in solo["records"]} == {entry.public_ref}


def test_unresolvable_product_smiles_among_valid_ones_returns_empty(client, db_session):
    """Same correctness trap, product side."""
    _entry(db_session, reactant_smiles="UNRESP_A", product_smiles="UNRESP_B")

    body = client.get(
        _browse_url_multi(product_smiles=["UNRESP_B", "another-nonexistent-smiles"])
    ).json()
    assert body["records"] == []


def test_duplicate_smiles_in_query_returns_same_records_as_single_smiles(
    client, db_session
):
    """Repeating the SAME resolvable SMILES must return exactly the
    single-value result -- record for record, not just "non-empty".

    ``_resolve_smiles_to_species_ids`` (``reactions.py``) does NOT
    de-duplicate its input; for ``["DUP_A", "DUP_A"]`` it resolves to
    ``[id, id]`` (index/count-aligned with the request list, duplicates
    preserved), which is exactly what makes the guard's raw
    ``len(resolved) != len(request.reactant_smiles)`` check correct
    without any pre-dedup step: a duplicated-but-resolvable SMILES
    contributes the same count on both sides of that comparison. This
    test asserts the *observable* consequence -- the duplicated query
    and the single-value query serve identical records -- so it stays
    meaningful regardless of how resolution is implemented underneath,
    unlike a bare "records is non-empty" check.
    """
    entry = _entry(db_session, reactant_smiles="DUP_A", product_smiles="DUP_B")

    single = client.get(_browse_url_multi(reactant_smiles=["DUP_A"])).json()
    duplicated = client.get(
        _browse_url_multi(reactant_smiles=["DUP_A", "DUP_A"])
    ).json()

    assert single["records"] != []
    assert duplicated["records"] == single["records"]
    assert {r["reaction_entry_ref"] for r in single["records"]} == {entry.public_ref}


def test_default_direction_excludes_reverse_matches(client, db_session):
    """The headline defect this task exists to fix -- reproduced live.

    ``entry`` is a REVERSIBLE reaction with ``EIR_B`` stored only on the
    PRODUCT side. Under the OLD default (``direction=either``,
    unconditional), a plain ``?reactant_smiles=EIR_B`` query returned
    this entry via the reverse orientation -- so a parameter named
    ``reactant_smiles`` came back with a reaction where ``EIR_B`` is
    never a stored reactant at all. Under the NEW default
    (``direction=forward``), the same query must NOT return it: the
    field means what it says now.
    """
    entry = _entry(db_session, reactant_smiles="EIR_A", product_smiles="EIR_B")
    assert entry.reaction.reversible is True

    body = client.get(_browse_url_multi(reactant_smiles=["EIR_B"])).json()
    refs = {r["reaction_entry_ref"] for r in body["records"]}
    assert entry.public_ref not in refs


def test_explicit_direction_either_still_matches_reverse_orientation(
    client, db_session
):
    """The old either-direction behaviour stays reachable, opt-in.

    Same fixture as the default-direction test above: ``EIR_B`` is
    stored only as a product of a reversible reaction. Passing
    ``direction=either`` explicitly must still surface it, carrying
    ``matched_direction: "reverse"`` so a caller can tell how it
    matched.
    """
    entry = _entry(db_session, reactant_smiles="EIR_A", product_smiles="EIR_B")

    body = client.get(
        _browse_url_multi(reactant_smiles=["EIR_B"], direction="either")
    ).json()
    matches = [
        r for r in body["records"] if r["reaction_entry_ref"] == entry.public_ref
    ]
    assert len(matches) == 1
    assert matches[0]["matched_direction"] == "reverse"


def test_explicit_direction_forward_matches_named_side_only(client, db_session):
    """``direction=forward`` (also the default) pins the orientation."""
    matching = _entry(db_session, reactant_smiles="DFW_A", product_smiles="DFW_B")
    non_matching = _entry(db_session, reactant_smiles="DFW_C", product_smiles="DFW_A")

    body = client.get(
        _browse_url_multi(reactant_smiles=["DFW_A"], direction="forward")
    ).json()
    refs = {r["reaction_entry_ref"] for r in body["records"]}
    assert matching.public_ref in refs
    assert non_matching.public_ref not in refs


def test_direction_reverse_swaps_the_single_orientation(client, db_session):
    """``direction=reverse`` matches ``reactant_smiles`` against stored products."""
    entry = _entry(db_session, reactant_smiles="DRV_A", product_smiles="DRV_B")

    forward_hit = client.get(
        _browse_url_multi(reactant_smiles=["DRV_B"], direction="forward")
    ).json()
    assert entry.public_ref not in {
        r["reaction_entry_ref"] for r in forward_hit["records"]
    }

    reverse_hit = client.get(
        _browse_url_multi(reactant_smiles=["DRV_B"], direction="reverse")
    ).json()
    reverse_refs = {r["reaction_entry_ref"] for r in reverse_hit["records"]}
    assert entry.public_ref in reverse_refs
    matched = next(
        r
        for r in reverse_hit["records"]
        if r["reaction_entry_ref"] == entry.public_ref
    )
    assert matched["matched_direction"] == "reverse"


def test_direction_exact_is_rejected(client, db_session):
    """``direction=exact`` is not a legal enum value on browse, matching search."""
    resp = client.get(_browse_url(reactant_smiles="X", direction="exact"))
    assert resp.status_code == 422


def test_filters_echo_includes_direction(client, db_session):
    """The echoed filter always states which direction was matched under,
    mirroring ``/reactions/search``'s ``request.filter.direction``."""
    _entry(db_session, reactant_smiles="ECHODIR_A", product_smiles="ECHODIR_B")

    default_body = client.get(_browse_url()).json()
    assert default_body["request"]["filter"]["direction"] == "forward"

    either_body = client.get(_browse_url(direction="either")).json()
    assert either_body["request"]["filter"]["direction"] == "either"


def test_filters_echo_round_trips_multiple_smiles(client, db_session):
    """The echoed filter must let a caller reconstruct the query they sent."""
    _entry_multi(
        db_session, reactant_smiles=["ECHO_A", "ECHO_B"], product_smiles=["ECHO_C"]
    )

    body = client.get(
        _browse_url_multi(
            reactant_smiles=["ECHO_A", "ECHO_B"], product_smiles=["ECHO_C"]
        )
    ).json()
    assert body["request"]["filter"]["reactant_smiles"] == ["ECHO_A", "ECHO_B"]
    assert body["request"]["filter"]["product_smiles"] == ["ECHO_C"]


def test_too_many_reactant_smiles_returns_422(client, db_session):
    too_many = [f"CAP{i}" for i in range(MAX_PARTICIPANTS_PER_REACTION + 1)]
    resp = client.get(_browse_url_multi(reactant_smiles=too_many))
    assert resp.status_code == 422


def test_too_many_product_smiles_returns_422(client, db_session):
    too_many = [f"CAPP{i}" for i in range(MAX_PARTICIPANTS_PER_REACTION + 1)]
    resp = client.get(_browse_url_multi(product_smiles=too_many))
    assert resp.status_code == 422


def test_max_allowed_reactant_smiles_count_is_accepted(client, db_session):
    """The bound is exclusive: exactly the max is fine, one more is not."""
    at_cap = [f"ATCAP{i}" for i in range(MAX_PARTICIPANTS_PER_REACTION)]
    resp = client.get(_browse_url_multi(reactant_smiles=at_cap))
    assert resp.status_code == 200
