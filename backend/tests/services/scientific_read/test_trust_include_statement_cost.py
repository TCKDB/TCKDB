"""What ``include=trust`` costs per additional record, on each of the five surfaces.

``trust`` was kept out of every search vocabulary for a reason, and the
reason does not go away because the decision did: only the detail paths
eager-loaded the graph the evidence evaluator walks, so a search page that
legalised the token could demand that graph once per record.

Counted from source at the point of the change, the chains are **9** tuple
entries on transport, 14 on kinetics, 19 on statmech, 20 on thermo and **23
on transition-states** — the last being the only four-hop chain, the only one
rooted on a *collection* (``TransitionStateEntry.calculations``) rather than
a scalar relationship, and the surface observed returning the largest page
(34 records). So this is not a uniform risk, and a single aggregate case
would hide a 2.5× spread between the cheapest and the most expensive. Each
surface gets its own test and can fail while the others pass.

**What is measured is the marginal cost of the token, and its slope.** For
each surface: statements for a page without ``trust``, statements for the
same page with it, at two page sizes. The number that must not grow is
``with - without``. A batched implementation pays a fixed number of
``selectinload`` round trips for the whole page, so that delta is the same
at 4 records as at 20. A per-record implementation pays the chain once per
row, so the delta grows with the page — and *that* is the regression, not
the absolute count.

Pinning the delta rather than the total is deliberate twice over. The total
moves whenever a fixture gains provenance, which is not a regression; and
the ideal fix for a slope that *has* regressed is to batch, which takes the
total down and would fail an equality assertion on the total.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db.models.common import (
    CalculationType,
    StatmechCalculationRole,
    TransitionStateEntryStatus,
    TransportCalculationRole,
)
from app.schemas.reads.scientific_kinetics_search import KineticsSearchRequest
from app.schemas.reads.scientific_statmech_search import StatmechSearchRequest
from app.schemas.reads.scientific_thermo_search import ThermoSearchRequest
from app.schemas.reads.scientific_transition_state_search import (
    TransitionStatesSearchRequest,
)
from app.schemas.reads.scientific_transport_search import TransportSearchRequest
from app.services.scientific_read.kinetics_search import search_kinetics
from app.services.scientific_read.statmech_search import search_statmech
from app.services.scientific_read.thermo_search import search_thermo
from app.services.scientific_read.transition_states_search import (
    search_transition_states,
)
from app.services.scientific_read.transport_search import search_transport
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

_SMALL_PAGE = 4
_LARGE_PAGE = 20

#: How much the *marginal* cost of ``include=trust`` may grow between a
#: 4-record page and a 20-record page. Zero is the honest target — a
#: ``selectinload`` chain applied to the page query issues the same number of
#: round trips whatever ``limit`` is — and a small allowance absorbs
#: statements that legitimately follow the data rather than the page (an
#: ``IN`` list that happens to split, a batch that empties). It is nowhere
#: near the 16 additional records' worth of chain a per-record
#: implementation would spend.
_ALLOWED_GROWTH = 2


def _count_statements(session: Session, run: Callable[[], Any]) -> int:
    """Statements issued while *run* executes."""
    count = 0
    engine = session.connection().engine

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        nonlocal count
        count += 1

    try:
        run()
    finally:
        event.remove(engine, "before_cursor_execute", _before)
    return count


def _marginal_cost(
    session: Session,
    *,
    search: Callable[..., Any],
    request_for: Callable[[int, list[str]], Any],
    limit: int,
    token: str = "trust",
) -> int:
    """Statements the *token* adds to a page of *limit* records."""
    without_response: list[Any] = []
    without = _count_statements(
        session,
        lambda: without_response.append(search(session, request_for(limit, []))),
    )
    with_response: list[Any] = []
    with_trust = _count_statements(
        session,
        lambda: with_response.append(search(session, request_for(limit, [token]))),
    )
    # A page that is not full is not comparable with one that is, and an
    # empty page would make every assertion below vacuous.
    assert len(without_response[0].records) == limit, (
        f"page of {limit} returned {len(without_response[0].records)} records"
    )
    assert len(with_response[0].records) == limit
    return with_trust - without


def _assert_slope_is_flat(
    name: str, small: int, large: int, *, token: str = "trust"
) -> None:
    assert small > 0, (
        f"{name}: include={token} cost zero extra statements, which means "
        f"the token did nothing. An assertion over no work proves nothing."
    )
    assert large <= small + _ALLOWED_GROWTH, (
        f"{name}: include={token} costs {small} extra statements on a "
        f"{_SMALL_PAGE}-record page and {large} on a {_LARGE_PAGE}-record "
        f"page — {(large - small) / (_LARGE_PAGE - _SMALL_PAGE):.2f} per "
        f"additional record. The evidence graph is being loaded per record "
        f"rather than per page. Batch it, or cap and paginate; do not "
        f"un-legalise the token."
    )


# ---------------------------------------------------------------------------
# Fixtures — one page-worth of records per surface, all under one parent so
# the page is full and deterministic.
# ---------------------------------------------------------------------------


@pytest.fixture
def thermo_page(db_session):
    species = make_species(db_session, inchi_key=next_inchi_key("CST"))
    entry = make_species_entry(db_session, species)
    for _ in range(_LARGE_PAGE):
        make_thermo_scalar(db_session, species_entry=entry)
    return entry


@pytest.fixture
def kinetics_page(db_session):
    reactant = make_species(db_session, smiles="[CH3]", inchi_key=next_inchi_key("CSKR"))
    product = make_species(db_session, smiles="[OH]", inchi_key=next_inchi_key("CSKP"))
    chem = make_chem_reaction(db_session, reactants=[reactant], products=[product])
    entry = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, reactant)],
        product_entries=[make_species_entry(db_session, product)],
    )
    for _ in range(_LARGE_PAGE):
        make_kinetics(db_session, reaction_entry=entry)
    return entry


@pytest.fixture
def transition_states_page(db_session):
    sp_a = make_species(db_session, inchi_key=next_inchi_key("CSTA"))
    sp_b = make_species(db_session, inchi_key=next_inchi_key("CSTB"))
    chem = make_chem_reaction(db_session, reactants=[sp_a], products=[sp_b])
    rxe = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, sp_a)],
        product_entries=[make_species_entry(db_session, sp_b)],
    )
    ts = make_transition_state(db_session, reaction_entry=rxe)
    for _ in range(_LARGE_PAGE):
        entry = make_transition_state_entry(
            db_session,
            transition_state=ts,
            status=TransitionStateEntryStatus.optimized,
        )
        # Evidence for the evaluator to walk. Without a calculation under
        # the entry the 23-entry chain has nothing to expand and the test
        # would measure an empty graph.
        make_calculation(
            db_session,
            type=CalculationType.freq,
            transition_state_entry_id=entry.id,
        )
    return ts


@pytest.fixture
def statmech_page(db_session):
    species = make_species(db_session, inchi_key=next_inchi_key("CSSM"))
    entry = make_species_entry(db_session, species)
    for _ in range(_LARGE_PAGE):
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
    return entry


@pytest.fixture
def transport_page(db_session):
    species = make_species(db_session, inchi_key=next_inchi_key("CSTR"))
    entry = make_species_entry(db_session, species)
    for _ in range(_LARGE_PAGE):
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
    return entry


# ---------------------------------------------------------------------------
# One test per surface
# ---------------------------------------------------------------------------


def test_thermo_search_trust_does_not_scale_with_page_size(
    db_session, thermo_page
):
    """20-entry chain, six root relationships.

    The tempting implementation here was to forward ``trust`` into
    ``get_species_thermo``, which the search already calls once per matched
    species entry. That would have loaded the graph for every candidate the
    search walked rather than for the page it returns — a cost proportional
    to the corpus, which this test could not even see, since it holds the
    corpus fixed and varies ``limit``. The page-scoped attach is what makes
    the number below stay put.
    """
    def request_for(limit: int, include: list[str]) -> ThermoSearchRequest:
        return ThermoSearchRequest(
            species_entry_ref=thermo_page.public_ref,
            include=include,
            limit=limit,
        )

    small = _marginal_cost(
        db_session, search=search_thermo, request_for=request_for, limit=_SMALL_PAGE
    )
    large = _marginal_cost(
        db_session, search=search_thermo, request_for=request_for, limit=_LARGE_PAGE
    )
    _assert_slope_is_flat("/thermo/search", small, large)


def test_kinetics_search_trust_does_not_scale_with_page_size(
    db_session, kinetics_page
):
    """14-entry chain, two root relationships. Same shape as thermo."""
    def request_for(limit: int, include: list[str]) -> KineticsSearchRequest:
        return KineticsSearchRequest(
            reaction_entry_ref=kinetics_page.public_ref,
            include=include,
            limit=limit,
        )

    small = _marginal_cost(
        db_session, search=search_kinetics, request_for=request_for, limit=_SMALL_PAGE
    )
    large = _marginal_cost(
        db_session, search=search_kinetics, request_for=request_for, limit=_LARGE_PAGE
    )
    _assert_slope_is_flat("/kinetics/search", small, large)


def test_transition_states_search_trust_does_not_scale_with_page_size(
    db_session, transition_states_page
):
    """The expensive one, and the one a staged rollout would have deferred.

    23 tuple entries, four hops, rooted on a collection, on the surface
    observed returning 34 records. If any of the five was going to force
    ``trust`` to stay off search, it was this — so its number is the one
    worth reading in a review, and this test exists to make it readable
    rather than inferred from an aggregate.
    """
    def request_for(
        limit: int, include: list[str]
    ) -> TransitionStatesSearchRequest:
        return TransitionStatesSearchRequest(
            transition_state_ref=transition_states_page.public_ref,
            include=include,
            limit=limit,
        )

    small = _marginal_cost(
        db_session,
        search=search_transition_states,
        request_for=request_for,
        limit=_SMALL_PAGE,
    )
    large = _marginal_cost(
        db_session,
        search=search_transition_states,
        request_for=request_for,
        limit=_LARGE_PAGE,
    )
    _assert_slope_is_flat("/transition-states/search", small, large)


def test_statmech_search_trust_does_not_scale_with_page_size(
    db_session, statmech_page
):
    """19-entry chain — the one the original policy comment was written about.

    It is not the most expensive of the five; measurement moved that title
    to transition states. It is still the only chain with a ``torsions``
    subtree, which fans out per record and then loads each torsion's source
    scan calculation three further ways, so it is the one where a per-record
    load would be worst *per row*.
    """
    def request_for(limit: int, include: list[str]) -> StatmechSearchRequest:
        return StatmechSearchRequest(
            species_entry_ref=statmech_page.public_ref,
            include=include,
            limit=limit,
        )

    small = _marginal_cost(
        db_session, search=search_statmech, request_for=request_for, limit=_SMALL_PAGE
    )
    large = _marginal_cost(
        db_session, search=search_statmech, request_for=request_for, limit=_LARGE_PAGE
    )
    _assert_slope_is_flat("/statmech/search", small, large)


def test_transport_search_trust_does_not_scale_with_page_size(
    db_session, transport_page
):
    """The cheapest of the five, and the one with no prior observation at all.

    Nine tuple entries: no ``torsions`` subtree, and none of ``lot``,
    ``software_release``, ``workflow_tool_release``, ``scf_stability`` or
    ``child_dependencies``. The transport table is empty on the hosted
    instance, so before this fixture no response from that surface had ever
    been seen. The cost objection was weakest exactly where the evidence was
    thinnest.
    """
    def request_for(limit: int, include: list[str]) -> TransportSearchRequest:
        return TransportSearchRequest(
            species_entry_ref=transport_page.public_ref,
            include=include,
            limit=limit,
        )

    small = _marginal_cost(
        db_session, search=search_transport, request_for=request_for, limit=_SMALL_PAGE
    )
    large = _marginal_cost(
        db_session, search=search_transport, request_for=request_for, limit=_LARGE_PAGE
    )
    _assert_slope_is_flat("/transport/search", small, large)


# ---------------------------------------------------------------------------
# The other token this PR gave a field to
# ---------------------------------------------------------------------------


def test_ts_search_entries_resolves_one_sibling_list_per_parent(
    db_session, transition_states_page
):
    """``include=entries`` groups by parent instead of resolving per record.

    Every record on this fixture's page shares one transition state, which
    is the realistic clustered case: several entries of the same saddle
    point routinely match the same filter. Grouped, the sibling list is
    resolved once for the page; per record, it would be resolved once per
    row, each time returning the same list.
    """
    def request_for(
        limit: int, include: list[str]
    ) -> TransitionStatesSearchRequest:
        return TransitionStatesSearchRequest(
            transition_state_ref=transition_states_page.public_ref,
            include=include,
            limit=limit,
        )

    small = _marginal_cost(
        db_session,
        search=search_transition_states,
        request_for=request_for,
        limit=_SMALL_PAGE,
        token="entries",
    )
    large = _marginal_cost(
        db_session,
        search=search_transition_states,
        request_for=request_for,
        limit=_LARGE_PAGE,
        token="entries",
    )
    _assert_slope_is_flat(
        "/transition-states/search", small, large, token="entries"
    )
