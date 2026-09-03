"""API tests for GET /api/v1/scientific/transition-states/search's
``family`` / ``participant_smiles`` findability filters.

The service layer (``transition_states_search.py``) has applied both
filters since the browse-findability change landed -- they were already in
``TransitionStatesSearchRequest`` and ``_MEANINGFUL_FILTER_FIELDS``. What
was missing, and is what this file pins, is the GET route itself: it never
declared the two as ``Query(...)`` parameters, so FastAPI silently dropped
them from every request. Measured before this fix:

- ``GET /transition-states/search?family=H_Abstraction`` -> 422
  ``missing_filter`` (the message LISTS ``family``/``participant_smiles``
  as acceptable filter names, because they are already in the Pydantic
  request model -- but the route never reads the query param into that
  model, so the request arrives with every field unset).
- ``GET /transition-states/search?family=zzz_no_such&status=optimized`` ->
  200, with ``family`` silently absent from the echoed
  ``request.filter`` block and every record returned regardless of
  family.

Reuses fixture helpers from the sibling test modules rather than
duplicating them -- ``_make_reaction_with_ts`` (opaque fake InChIKeys, fine
for filters that don't touch structure) from
``test_api_scientific_transition_states.py``, ``_make_reaction_with_species``
(real RDKit-computed InChIKeys, needed for ``participant_smiles``) from
``test_api_transition_states_browse.py``.
"""

from __future__ import annotations

from urllib.parse import quote

from app.db.models.reaction import ReactionFamily
from tests.api.scientific.test_api_scientific_transition_states import (
    _make_reaction_with_ts,
)
from tests.api.scientific.test_api_transition_states_browse import (
    _make_reaction_with_species,
)


def _search_url(**params) -> str:
    base = "/api/v1/scientific/transition-states/search"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


# ===========================================================================
# family
# ===========================================================================


def test_family_alone_satisfies_the_meaningful_filter_check(client, db_session):
    """The exact 422 this bug produced: ``family`` alone used to still
    422 with ``missing_filter`` because the route dropped the query
    param before it ever reached ``TransitionStatesSearchRequest``.
    """
    _make_reaction_with_species(
        db_session,
        reactant_smiles="CCO",
        product_smiles="CC=O",
        family_name="H_Abstraction",
    )
    resp = client.get(_search_url(family="H_Abstraction"))
    assert resp.status_code == 200, resp.text


def test_family_filter_is_honoured(client, db_session):
    _, _, _, entry_a = _make_reaction_with_species(
        db_session,
        reactant_smiles="CCO",
        product_smiles="CC=O",
        family_name="R_Addition_MultipleBond",
    )
    _, _, _, entry_b = _make_reaction_with_species(
        db_session,
        reactant_smiles="CCN",
        product_smiles="CC=N",
        family_name="H_Abstraction",
    )

    body = client.get(_search_url(family="R_Addition_MultipleBond")).json()
    refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in body["records"]
    }
    assert refs == {entry_a.public_ref}
    assert entry_b.public_ref not in refs


def test_family_filter_is_echoed_in_request_filter(client, db_session):
    _make_reaction_with_species(
        db_session,
        reactant_smiles="CCO",
        product_smiles="CC=O",
        family_name="R_Addition_MultipleBond",
    )
    body = client.get(_search_url(family="R_Addition_MultipleBond")).json()
    assert body["request"]["filter"].get("family") == "R_Addition_MultipleBond"


def test_unknown_family_returns_empty_not_422(client, db_session):
    _make_reaction_with_ts(db_session)
    resp = client.get(_search_url(family="zzz_no_such", status="optimized"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["records"] == []


# ===========================================================================
# participant_smiles
# ===========================================================================


def test_participant_smiles_alone_satisfies_the_meaningful_filter_check(
    client, db_session
):
    _make_reaction_with_species(
        db_session, reactant_smiles="CCO", product_smiles="CC=O"
    )
    resp = client.get(_search_url(participant_smiles="CCO"))
    assert resp.status_code == 200, resp.text


def test_participant_smiles_filter_is_honoured(client, db_session):
    _, _, _, entry_a = _make_reaction_with_species(
        db_session, reactant_smiles="CCO", product_smiles="CC=O"
    )
    _, _, _, entry_b = _make_reaction_with_species(
        db_session, reactant_smiles="CCN", product_smiles="CC=N"
    )

    body = client.get(_search_url(participant_smiles="CCO")).json()
    refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in body["records"]
    }
    assert refs == {entry_a.public_ref}
    assert entry_b.public_ref not in refs


def test_participant_smiles_filter_is_echoed_in_request_filter(client, db_session):
    _make_reaction_with_species(
        db_session, reactant_smiles="CCO", product_smiles="CC=O"
    )
    body = client.get(_search_url(participant_smiles="CCO")).json()
    assert body["request"]["filter"].get("participant_smiles") == "CCO"


def test_participant_smiles_invalid_smiles_returns_422(client, db_session):
    resp = client.get(
        _search_url(participant_smiles=quote("not(a valid smiles", safe=""))
    )
    assert resp.status_code == 422, resp.text
    assert "invalid_structure_query" in resp.text


def test_family_and_reaction_family_row_created_get_or_reused(client, db_session):
    """Sanity check on the fixture itself, not the route: two calls with
    the same ``family_name`` must not violate ``ReactionFamily.name``'s
    unique constraint (the get-or-create branch in
    ``_make_reaction_with_species``).
    """
    _make_reaction_with_species(
        db_session,
        reactant_smiles="CCO",
        product_smiles="CC=O",
        family_name="R_Addition_MultipleBond",
    )
    _make_reaction_with_species(
        db_session,
        reactant_smiles="CCCl",
        product_smiles="CC=S",
        family_name="R_Addition_MultipleBond",
    )
    count = (
        db_session.query(ReactionFamily)
        .filter(ReactionFamily.name == "R_Addition_MultipleBond")
        .count()
    )
    assert count == 1


def test_blank_participant_smiles_alone_does_not_satisfy_the_meaningful_filter_check(client, db_session):
    """``?participant_smiles=`` is no filter: the route must 422 with
    ``missing_filter`` exactly as a bare request does, not serve the whole
    corpus while echoing an empty-string filter (review of #356)."""
    resp = client.get(_search_url(participant_smiles=""))
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "missing_filter"


def test_blank_family_is_normalised_to_no_filter_in_the_echo(client, db_session):
    """With a real filter alongside, a blank ``family`` must neither
    narrow the result nor be echoed back as an applied filter."""
    resp = client.get(_search_url(family="   ", status="optimized"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["request"]["filter"].get("family") is None
