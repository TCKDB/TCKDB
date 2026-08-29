"""Every declared include-gated section is absent until it is asked for.

One parametrised case per operation that owns a declared token -> section
table. Each case asserts the three states the contract distinguishes:

    not requested            -> the key is **absent**
    requested, nothing there -> the key is present and ``null`` / ``[]``
    requested, something there -> the key is present and populated

and asserts that ``request.include`` echoes the resolved set, which is what
makes the first two interpretable: a reader who can see what was asked can
read an absent key as "I did not ask" rather than "there is none".

Three things about the shape of this file are deliberate.

**It enumerates the runtime's own table objects, not a copy of them.** The
cases below name the constants from ``_response``; a table that is declared
and never wired, or wired and never declared, cannot pass
:func:`test_the_parametrisation_covers_every_declared_table`.

**It asserts its own case count.** A parametrisation that silently
enumerates nothing passes green, which is the failure mode this repository
has been bitten by most often. The count is checked against the declared
tables rather than written down twice.

**It has no expected-no-field exemption list, and must not grow one.** The
cases are generated from the *tables*, and a table only ever names a token
that produces a field. Several legal include tokens on these surfaces
produce nothing at all — ``review`` on ``/species/search`` and on
``/reaction-entries/{id}/full``, ``literature`` on the two provenance
surfaces, ``review`` on ``/artifacts/search``, and every public token on
``/literature/{ref}`` — and none of them is in a table, so none of them
needs excusing here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pytest

from app.api.routes.scientific._response import (
    ALL_INCLUDE_GATED_TABLES,
    ANYWHERE_SCOPE,
    ARTIFACT_RECORD_SECTIONS,
    ASSESSMENTS_SECTION,
    CALCULATION_RECORD_SECTIONS,
    CONFORMER_RECORD_SECTIONS,
    DETAIL_SCOPE,
    DOCUMENT_SCOPE,
    ENERGY_CORRECTION_SCHEME_RECORD_SECTIONS,
    FREQUENCY_SCALE_FACTOR_RECORD_SECTIONS,
    KINETICS_RECORD_SECTIONS,
    LITERATURE_RECORDS_SECTIONS,
    NETWORK_KINETICS_RECORD_SECTIONS,
    NETWORK_RECORD_SECTIONS,
    NETWORK_SOLVE_RECORD_SECTIONS,
    REACTION_FULL_SECTIONS,
    SEARCH_SCOPE,
    SPECIES_BROWSE_SECTIONS,
    SPECIES_CALCULATIONS_SEARCH_SECTIONS,
    SPECIES_SEARCH_SECTIONS,
    STATMECH_RECORD_SECTIONS,
    TRANSITION_STATE_RECORD_SECTIONS,
    TRANSPORT_RECORD_SECTIONS,
    TRUST_SECTION,
    IncludeGatedSections,
    _record_nodes,
)
from app.db.models.common import (
    CalculationType,
    NetworkChannelKind,
    NetworkKineticsModelKind,
    NetworkSolveCalculationRole,
    NetworkSolveKind,
    NetworkSpeciesRole,
    NetworkStateKind,
    RecordReviewStatus,
    SubmissionRecordType,
)
from tests.services.scientific_read._factories import (
    attach_artifact,
    attach_conformer_selection,
    attach_network_kinetics_chebyshev,
    attach_network_kinetics_plog,
    attach_network_kinetics_point,
    attach_network_solve_bath_gas,
    attach_network_solve_source_calculation,
    attach_network_species,
    attach_network_state_participant,
    attach_statmech_source_calculation,
    attach_transport_source_calculation,
    make_calculation,
    make_chem_reaction,
    make_conformer_group,
    make_conformer_observation,
    make_energy_correction_scheme,
    make_frequency_scale_factor,
    make_kinetics,
    make_literature,
    make_lot,
    make_network,
    make_network_channel,
    make_network_kinetics,
    make_network_solve,
    make_network_state,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    make_transition_state,
    make_transition_state_entry,
    make_transport,
    set_review,
)

# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------


@pytest.fixture
def corpus(client, db_session) -> dict[str, Any]:
    """One small record of every shape the flipped operations return.

    Deliberately built once per test rather than shared: the API session is
    a transaction that is rolled back, so there is nothing to share.
    """
    session = db_session
    lot = make_lot(session)
    literature = make_literature(session)

    reactant_species = make_species(session, smiles="CC")
    reactant = make_species_entry(session, reactant_species)
    product_species = make_species(session, smiles="C[CH2]", multiplicity=2)
    product = make_species_entry(session, product_species)
    set_review(
        session,
        record_type=SubmissionRecordType.species_entry,
        record_id=reactant.id,
        status=RecordReviewStatus.approved,
    )

    calculation = make_calculation(
        session,
        type=CalculationType.freq,
        species_entry_id=reactant.id,
        lot_id=lot.id,
    )
    artifact = attach_artifact(session, calculation=calculation)

    thermo = make_thermo_scalar(session, species_entry=reactant)
    statmech = make_statmech(session, species_entry=reactant)
    attach_statmech_source_calculation(
        session, statmech=statmech, calculation=calculation
    )
    make_statmech(session, species_entry=product, literature_id=literature.id)
    transport = make_transport(session, species_entry=reactant)
    attach_transport_source_calculation(
        session, transport=transport, calculation=calculation
    )

    group = make_conformer_group(session, reactant, label="cg-1")
    observation = make_conformer_observation(session, conformer_group=group)
    attach_conformer_selection(session, conformer_group=group)

    reaction = make_chem_reaction(
        session, reactants=[reactant_species], products=[product_species]
    )
    reaction_entry = make_reaction_entry(
        session,
        reaction=reaction,
        reactant_entries=[reactant],
        product_entries=[product],
    )
    kinetics = make_kinetics(session, reaction_entry=reaction_entry)
    transition_state = make_transition_state(
        session, reaction_entry=reaction_entry, label="ts-1"
    )
    ts_entry = make_transition_state_entry(
        session, transition_state=transition_state
    )

    network = make_network(session)
    attach_network_species(
        session,
        network=network,
        species_entry=reactant,
        role=NetworkSpeciesRole.well,
    )
    source_state = make_network_state(
        session,
        network=network,
        kind=NetworkStateKind.well,
        composition_hash="well-1",
    )
    sink_state = make_network_state(
        session,
        network=network,
        kind=NetworkStateKind.bimolecular,
        composition_hash="bimol-1",
    )
    attach_network_state_participant(
        session, state=source_state, species_entry=reactant
    )
    channel = make_network_channel(
        session,
        network=network,
        source_state=source_state,
        sink_state=sink_state,
        kind=NetworkChannelKind.isomerization,
    )
    solve = make_network_solve(
        session, network=network, kind=NetworkSolveKind.computed
    )
    attach_network_solve_bath_gas(
        session, solve=solve, species_entry=reactant
    )
    attach_network_solve_source_calculation(
        session,
        solve=solve,
        calculation=calculation,
        role=NetworkSolveCalculationRole.well_energy,
    )
    network_kinetics = make_network_kinetics(
        session,
        solve=solve,
        channel=channel,
        model_kind=NetworkKineticsModelKind.chebyshev,
    )
    attach_network_kinetics_chebyshev(session, kinetics=network_kinetics)
    attach_network_kinetics_plog(session, kinetics=network_kinetics)
    attach_network_kinetics_point(
        session,
        kinetics=network_kinetics,
        temperature_k=1000.0,
        pressure_bar=1.0,
        rate_value=1.0e10,
    )

    # No ``source_literature`` on either: their ``literature`` field is an
    # ungated scientific fact and this corpus needs it to be ``null`` so the
    # boundary assertions have something to stand on.
    fsf = make_frequency_scale_factor(session, lot=lot)
    ecs = make_energy_correction_scheme(session, lot=lot)

    session.flush()
    return {
        "species_smiles": reactant_species.smiles,
        "species_entry_ref": reactant.public_ref,
        "reaction_entry_ref": reaction_entry.public_ref,
        "transition_state_ref": transition_state.public_ref,
        "transition_state_entry_ref": ts_entry.public_ref,
        "conformer_group_ref": group.public_ref,
        "conformer_observation_ref": observation.public_ref,
        "statmech_ref": statmech.public_ref,
        "transport_ref": transport.public_ref,
        "network_ref": network.public_ref,
        "network_solve_ref": solve.public_ref,
        "network_kinetics_ref": network_kinetics.public_ref,
        "frequency_scale_factor_ref": fsf.public_ref,
        "energy_correction_scheme_ref": ecs.public_ref,
        "literature_ref": literature.public_ref,
        "calculation_ref": calculation.public_ref,
        "artifact_sha256": artifact.sha256,
        "thermo_ref": thermo.public_ref,
        "kinetics_ref": kinetics.public_ref,
    }


# ---------------------------------------------------------------------------
# The cases
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """One operation, the table it declares, and how to reach it."""

    label: str
    table: IncludeGatedSections
    scope: str
    path: Callable[[dict[str, Any]], str]
    method: str = "GET"
    body: Callable[[dict[str, Any]], dict[str, Any]] = lambda c: {}
    #: Tokens this operation's table declares that this operation's own
    #: vocabulary does not accept, because the twin surface does. This is
    #: the residue class the OpenAPI marker rule exists for, and it is one
    #: entry on one table rather than a general escape hatch: the
    #: ``/calculations`` table is shared by the search and detail
    #: operations, and only the detail one accepts
    #: ``imaginary_mode_projections``. (``trust`` is the same split on the
    #: same pair, but it lives in ``TRUST_SECTION`` rather than here.)
    #: Naming it turns a would-be skip into two positive assertions — the
    #: token really is refused, and the field really is absent.
    detail_only_tokens: tuple[str, ...] = ()

    def raw(self, client, corpus, include: list[str]):
        url = self.path(corpus)
        if self.method == "POST":
            payload = {**self.body(corpus), "include": include}
            return client.post(url, json=payload)
        query = "&".join(f"include={token}" for token in include)
        joiner = "&" if "?" in url else "?"
        return client.get(f"{url}{query and joiner + query}")

    def request(self, client, corpus, include: list[str]) -> dict[str, Any]:
        response = self.raw(client, corpus, include)
        assert response.status_code == 200, (
            f"{self.label} include={include}: {response.status_code} "
            f"{response.text[:400]}"
        )
        return response.json()


_SCI = "/api/v1/scientific"


CASES: tuple[Case, ...] = (
    Case(
        "GET /species/search",
        SPECIES_SEARCH_SECTIONS,
        ANYWHERE_SCOPE,
        lambda c: f"{_SCI}/species/search?smiles={c['species_smiles']}",
    ),
    Case(
        "GET /reaction-entries/{ref}/kinetics",
        KINETICS_RECORD_SECTIONS,
        SEARCH_SCOPE,
        lambda c: f"{_SCI}/reaction-entries/{c['reaction_entry_ref']}/kinetics",
    ),
    Case(
        "GET /reaction-entries/{ref}/full",
        REACTION_FULL_SECTIONS,
        DOCUMENT_SCOPE,
        lambda c: f"{_SCI}/reaction-entries/{c['reaction_entry_ref']}/full",
    ),
    Case(
        "GET /transition-states/search",
        TRANSITION_STATE_RECORD_SECTIONS,
        ANYWHERE_SCOPE,
        lambda c: (
            f"{_SCI}/transition-states/search"
            f"?transition_state_ref={c['transition_state_ref']}"
        ),
    ),
    Case(
        "POST /transition-states/search",
        TRANSITION_STATE_RECORD_SECTIONS,
        ANYWHERE_SCOPE,
        lambda c: f"{_SCI}/transition-states/search",
        method="POST",
        body=lambda c: {"transition_state_ref": c["transition_state_ref"]},
    ),
    Case(
        "GET /transition-states/{ref}",
        TRANSITION_STATE_RECORD_SECTIONS,
        ANYWHERE_SCOPE,
        lambda c: f"{_SCI}/transition-states/{c['transition_state_ref']}",
    ),
    Case(
        "GET /transition-state-entries/{ref}",
        TRANSITION_STATE_RECORD_SECTIONS,
        ANYWHERE_SCOPE,
        lambda c: (
            f"{_SCI}/transition-state-entries/{c['transition_state_entry_ref']}"
        ),
    ),
    Case(
        "GET /species-calculations/search",
        SPECIES_CALCULATIONS_SEARCH_SECTIONS,
        SEARCH_SCOPE,
        lambda c: (
            f"{_SCI}/species-calculations/search"
            f"?species_entry_ref={c['species_entry_ref']}"
        ),
    ),
    Case(
        "POST /species-calculations/search",
        SPECIES_CALCULATIONS_SEARCH_SECTIONS,
        SEARCH_SCOPE,
        lambda c: f"{_SCI}/species-calculations/search",
        method="POST",
        body=lambda c: {"species_entry_ref": c["species_entry_ref"]},
    ),
    Case(
        "GET /conformers/search",
        CONFORMER_RECORD_SECTIONS,
        ANYWHERE_SCOPE,
        lambda c: (
            f"{_SCI}/conformers/search"
            f"?species_entry_ref={c['species_entry_ref']}"
        ),
    ),
    Case(
        "POST /conformers/search",
        CONFORMER_RECORD_SECTIONS,
        ANYWHERE_SCOPE,
        lambda c: f"{_SCI}/conformers/search",
        method="POST",
        body=lambda c: {"species_entry_ref": c["species_entry_ref"]},
    ),
    Case(
        "GET /conformer-groups/{ref}",
        CONFORMER_RECORD_SECTIONS,
        ANYWHERE_SCOPE,
        lambda c: f"{_SCI}/conformer-groups/{c['conformer_group_ref']}",
    ),
    Case(
        "GET /conformer-observations/{ref}",
        CONFORMER_RECORD_SECTIONS,
        ANYWHERE_SCOPE,
        lambda c: (
            f"{_SCI}/conformer-observations/{c['conformer_observation_ref']}"
        ),
    ),
    Case(
        "GET /statmech/search",
        STATMECH_RECORD_SECTIONS,
        SEARCH_SCOPE,
        lambda c: (
            f"{_SCI}/statmech/search"
            f"?species_entry_ref={c['species_entry_ref']}"
        ),
    ),
    Case(
        "POST /statmech/search",
        STATMECH_RECORD_SECTIONS,
        SEARCH_SCOPE,
        lambda c: f"{_SCI}/statmech/search",
        method="POST",
        body=lambda c: {"species_entry_ref": c["species_entry_ref"]},
    ),
    Case(
        "GET /statmech/{ref}",
        STATMECH_RECORD_SECTIONS,
        DETAIL_SCOPE,
        lambda c: f"{_SCI}/statmech/{c['statmech_ref']}",
    ),
    Case(
        "GET /species-entries/{ref}/statmech",
        STATMECH_RECORD_SECTIONS,
        SEARCH_SCOPE,
        lambda c: f"{_SCI}/species-entries/{c['species_entry_ref']}/statmech",
    ),
    Case(
        "GET /transport/search",
        TRANSPORT_RECORD_SECTIONS,
        SEARCH_SCOPE,
        lambda c: (
            f"{_SCI}/transport/search"
            f"?species_entry_ref={c['species_entry_ref']}"
        ),
    ),
    Case(
        "POST /transport/search",
        TRANSPORT_RECORD_SECTIONS,
        SEARCH_SCOPE,
        lambda c: f"{_SCI}/transport/search",
        method="POST",
        body=lambda c: {"species_entry_ref": c["species_entry_ref"]},
    ),
    Case(
        "GET /transport/{ref}",
        TRANSPORT_RECORD_SECTIONS,
        DETAIL_SCOPE,
        lambda c: f"{_SCI}/transport/{c['transport_ref']}",
    ),
    Case(
        "GET /species-entries/{ref}/transport",
        TRANSPORT_RECORD_SECTIONS,
        SEARCH_SCOPE,
        lambda c: f"{_SCI}/species-entries/{c['species_entry_ref']}/transport",
    ),
    Case(
        "GET /networks/search",
        NETWORK_RECORD_SECTIONS,
        SEARCH_SCOPE,
        lambda c: f"{_SCI}/networks/search?network_ref={c['network_ref']}",
    ),
    Case(
        "POST /networks/search",
        NETWORK_RECORD_SECTIONS,
        SEARCH_SCOPE,
        lambda c: f"{_SCI}/networks/search",
        method="POST",
        body=lambda c: {"network_ref": c["network_ref"]},
    ),
    Case(
        "GET /networks/{ref}",
        NETWORK_RECORD_SECTIONS,
        DETAIL_SCOPE,
        lambda c: f"{_SCI}/networks/{c['network_ref']}",
    ),
    Case(
        "GET /network-solves/search",
        NETWORK_SOLVE_RECORD_SECTIONS,
        SEARCH_SCOPE,
        lambda c: (
            f"{_SCI}/network-solves/search?network_ref={c['network_ref']}"
        ),
    ),
    Case(
        "POST /network-solves/search",
        NETWORK_SOLVE_RECORD_SECTIONS,
        SEARCH_SCOPE,
        lambda c: f"{_SCI}/network-solves/search",
        method="POST",
        body=lambda c: {"network_ref": c["network_ref"]},
    ),
    Case(
        "GET /network-solves/{ref}",
        NETWORK_SOLVE_RECORD_SECTIONS,
        DETAIL_SCOPE,
        lambda c: f"{_SCI}/network-solves/{c['network_solve_ref']}",
    ),
    Case(
        "GET /network-kinetics/search",
        NETWORK_KINETICS_RECORD_SECTIONS,
        SEARCH_SCOPE,
        lambda c: (
            f"{_SCI}/network-kinetics/search?network_ref={c['network_ref']}"
        ),
    ),
    Case(
        "POST /network-kinetics/search",
        NETWORK_KINETICS_RECORD_SECTIONS,
        SEARCH_SCOPE,
        lambda c: f"{_SCI}/network-kinetics/search",
        method="POST",
        body=lambda c: {"network_ref": c["network_ref"]},
    ),
    Case(
        "GET /network-kinetics/{ref}",
        NETWORK_KINETICS_RECORD_SECTIONS,
        DETAIL_SCOPE,
        lambda c: f"{_SCI}/network-kinetics/{c['network_kinetics_ref']}",
    ),
    Case(
        "GET /frequency-scale-factors/search",
        FREQUENCY_SCALE_FACTOR_RECORD_SECTIONS,
        SEARCH_SCOPE,
        lambda c: (
            f"{_SCI}/frequency-scale-factors/search"
            f"?frequency_scale_factor_ref={c['frequency_scale_factor_ref']}"
        ),
    ),
    Case(
        "POST /frequency-scale-factors/search",
        FREQUENCY_SCALE_FACTOR_RECORD_SECTIONS,
        SEARCH_SCOPE,
        lambda c: f"{_SCI}/frequency-scale-factors/search",
        method="POST",
        body=lambda c: {
            "frequency_scale_factor_ref": c["frequency_scale_factor_ref"]
        },
    ),
    Case(
        "GET /frequency-scale-factors/{ref}",
        FREQUENCY_SCALE_FACTOR_RECORD_SECTIONS,
        DETAIL_SCOPE,
        lambda c: (
            f"{_SCI}/frequency-scale-factors/{c['frequency_scale_factor_ref']}"
        ),
    ),
    Case(
        "GET /energy-correction-schemes/search",
        ENERGY_CORRECTION_SCHEME_RECORD_SECTIONS,
        SEARCH_SCOPE,
        lambda c: (
            f"{_SCI}/energy-correction-schemes/search"
            f"?energy_correction_scheme_ref={c['energy_correction_scheme_ref']}"
        ),
    ),
    Case(
        "POST /energy-correction-schemes/search",
        ENERGY_CORRECTION_SCHEME_RECORD_SECTIONS,
        SEARCH_SCOPE,
        lambda c: f"{_SCI}/energy-correction-schemes/search",
        method="POST",
        body=lambda c: {
            "energy_correction_scheme_ref": c["energy_correction_scheme_ref"]
        },
    ),
    Case(
        "GET /energy-correction-schemes/{ref}",
        ENERGY_CORRECTION_SCHEME_RECORD_SECTIONS,
        DETAIL_SCOPE,
        lambda c: (
            f"{_SCI}/energy-correction-schemes/"
            f"{c['energy_correction_scheme_ref']}"
        ),
    ),
    Case(
        "GET /artifacts/search",
        ARTIFACT_RECORD_SECTIONS,
        SEARCH_SCOPE,
        lambda c: f"{_SCI}/artifacts/search?sha256={c['artifact_sha256']}",
    ),
    Case(
        "POST /artifacts/search",
        ARTIFACT_RECORD_SECTIONS,
        SEARCH_SCOPE,
        lambda c: f"{_SCI}/artifacts/search",
        method="POST",
        body=lambda c: {"sha256": c["artifact_sha256"]},
    ),
    Case(
        "GET /literature/{ref}/records",
        LITERATURE_RECORDS_SECTIONS,
        SEARCH_SCOPE,
        lambda c: f"{_SCI}/literature/{c['literature_ref']}/records",
    ),
    Case(
        "GET /calculations/search",
        CALCULATION_RECORD_SECTIONS,
        SEARCH_SCOPE,
        lambda c: (
            f"{_SCI}/calculations/search"
            f"?species_entry_ref={c['species_entry_ref']}"
        ),
        detail_only_tokens=("imaginary_mode_projections",),
    ),
    Case(
        "POST /calculations/search",
        CALCULATION_RECORD_SECTIONS,
        SEARCH_SCOPE,
        lambda c: f"{_SCI}/calculations/search",
        method="POST",
        body=lambda c: {"species_entry_ref": c["species_entry_ref"]},
        detail_only_tokens=("imaginary_mode_projections",),
    ),
    Case(
        "GET /calculations/{ref}",
        CALCULATION_RECORD_SECTIONS,
        DETAIL_SCOPE,
        lambda c: f"{_SCI}/calculations/{c['calculation_ref']}",
    ),
)


#: Tables applied by :func:`omit_trust_unless_requested` and
#: :func:`omit_assessments_unless_requested` on many surfaces at once rather
#: than by a per-operation case. Their behaviour is pinned by
#: ``test_search_trust_include.py`` and ``test_include_gated_sections.py``.
_CROSS_CUTTING_TABLES = (TRUST_SECTION, ASSESSMENTS_SECTION)

#: Tables every one of whose declared tokens is refused by the single
#: operation that applies them -- ``SPECIES_BROWSE_SECTIONS`` names
#: ``thermo``/``statmech``/``transport``/``conformers``, and none of the
#: four is in ``/species/browse``'s own vocabulary
#: (``species.py::_BROWSE_LEGAL_INCLUDE_TOKENS`` omits all of them: their
#: payload is a bare integer-id array, and on an identifier-free,
#: unauthenticated, whole-corpus listing that is a primary-key-harvest
#: route). A ``Case`` here exercises two behaviours -- "absent by
#: default" and "present when requested" -- and the second would have
#: nothing left to request: every token would need ``detail_only_tokens``,
#: which makes that half of the parametrisation run a loop that iterates
#: zero times and reports a pass. That is the same vacuity this file's
#: own pagination-completeness sibling was called out for, so it is not
#: reproduced here. The one behaviour that still applies --
#: ``thermo_summary`` et al. are absent from *every* browse response, not
#: just the default one -- is pinned directly, unconditionally, in
#: ``tests/services/scientific_read/test_browse_species.py`` and
#: ``tests/api/scientific/test_api_species_browse.py``.
_UNREQUESTABLE_TABLES = (SPECIES_BROWSE_SECTIONS,)

_EXEMPT_TABLES = _CROSS_CUTTING_TABLES + _UNREQUESTABLE_TABLES


# ---------------------------------------------------------------------------
# The parametrisation cannot be empty, and cannot drift from the tables
# ---------------------------------------------------------------------------


def test_the_parametrisation_covers_every_declared_table():
    """No table is declared without a case, and no case invents a table.

    This is the guard against the parametrisation quietly enumerating
    nothing — the failure mode that passes green. It compares *objects*,
    so a table copied instead of imported fails here too.
    """
    exercised = {id(case.table) for case in CASES}
    declared = {
        id(table)
        for table in ALL_INCLUDE_GATED_TABLES.values()
        if table not in _EXEMPT_TABLES
    }

    assert exercised == declared, (
        "the cases and the declared tables disagree: "
        f"{len(exercised)} exercised vs {len(declared)} declared"
    )


def test_the_parametrisation_asserts_its_own_size():
    """The case count and the section count are both pinned.

    Written as a comparison against the tables rather than as two magic
    numbers, so adding a section to a table without adding a case fails
    here instead of passing silently.
    """
    assert len(CASES) == 42

    sections_under_test = {
        (case.table.surface, token, field_name)
        for case in CASES
        for token, field_names in case.table.sections.items()
        for field_name in field_names
    }
    declared_sections = {
        (table.surface, token, field_name)
        for table in ALL_INCLUDE_GATED_TABLES.values()
        if table not in _EXEMPT_TABLES
        for token, field_names in table.sections.items()
        for field_name in field_names
    }

    assert sections_under_test == declared_sections
    # 81 before ``energy_corrections`` joined CALCULATION_RECORD_SECTIONS;
    # 82 before ``freq_modes`` joined the species-calculations search.
    # SPECIES_BROWSE_SECTIONS does not add a fifth data point here even
    # though it declares four sections: it is in ``_UNREQUESTABLE_TABLES``
    # (none of its tokens is legal on ``/species/browse``, so there is no
    # Case, and ``_EXEMPT_TABLES`` excludes it from this count the same
    # way TRUST_SECTION/ASSESSMENTS_SECTION are excluded).
    assert len(sections_under_test) == 83


# ---------------------------------------------------------------------------
# The behaviour
# ---------------------------------------------------------------------------


def _nodes(case: Case, body: dict[str, Any]) -> list[dict[str, Any]]:
    """The dicts the runtime's own strip would have visited for this case."""
    return list(_record_nodes(body, case.scope))


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.label)
def test_an_unrequested_section_is_absent(case: Case, client, corpus):
    body = case.request(client, corpus, include=[])
    nodes = _nodes(case, body)

    assert nodes, f"{case.label}: no records to assert on — vacuous case"

    default_echo = body["request"]["include"]
    for token, field_names in case.table.sections.items():
        if token in default_echo:
            # ``/full`` answers a bare request with its three default
            # sections; those are requested, not unrequested.
            continue
        for field_name in field_names:
            for node in nodes:
                assert field_name not in node, (
                    f"{case.label}: {field_name!r} is gated by {token!r} and "
                    "the caller did not ask for it, but the key is on the wire"
                )


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.label)
def test_a_requested_section_is_present_and_the_echo_says_so(
    case: Case, client, corpus
):
    for token, field_names in case.table.sections.items():
        if token in case.detail_only_tokens:
            continue
        body = case.request(client, corpus, include=[token])
        nodes = _nodes(case, body)
        assert nodes, f"{case.label}: no records to assert on — vacuous case"

        echo = body["request"]["include"]
        assert token in echo, (
            f"{case.label}: include={token!r} was accepted but the echo "
            f"reports {echo!r} — a reader cannot tell what was resolved"
        )

        for field_name in field_names:
            assert any(field_name in node for node in nodes), (
                f"{case.label}: include={token!r} was requested and echoed, "
                f"but {field_name!r} is on no record"
            )


# ---------------------------------------------------------------------------
# The middle state, and the fields that are not sections at all
# ---------------------------------------------------------------------------


#: (case label, token, field) triples the corpus deliberately leaves empty.
#: Requested-and-empty is the state that makes the whole change work, so it
#: is asserted on named surfaces rather than wherever it happens to occur.
REQUESTED_BUT_EMPTY = (
    ("GET /statmech/{ref}", "torsions", "torsions"),
    ("GET /statmech/{ref}", "electronic_levels", "electronic_levels"),
    ("GET /conformer-groups/{ref}", "calculations", "calculations"),
    ("GET /networks/{ref}", "reactions", "reactions"),
    ("GET /network-solves/{ref}", "channel_barriers", "channel_barriers"),
    ("GET /frequency-scale-factors/{ref}", "used_by", "used_by"),
    ("GET /energy-correction-schemes/{ref}", "corrections", "corrections"),
    ("GET /reaction-entries/{ref}/full", "scans", "scans"),
)

_BY_LABEL = {case.label: case for case in CASES}


@pytest.mark.parametrize(
    ("label", "token", "field_name"),
    REQUESTED_BUT_EMPTY,
    ids=lambda v: v if isinstance(v, str) else str(v),
)
def test_a_requested_but_empty_section_keeps_its_null(
    label, token, field_name, client, corpus
):
    """Asked-for-and-empty is present, not absent.

    Collapsing this state back into absence restores the original
    ambiguity from the other direction: an absent key would again mean
    either "you did not ask" or "there is none".
    """
    case = _BY_LABEL[label]
    body = case.request(client, corpus, include=[token])
    nodes = _nodes(case, body)
    assert nodes, f"{label}: no records to assert on — vacuous case"

    carriers = [node for node in nodes if field_name in node]
    assert carriers, f"{label}: include={token!r} produced no {field_name!r} key"
    for node in carriers:
        assert node[field_name] in (None, [], {}), (
            f"{label}: {field_name!r} was expected to be requested-but-empty; "
            f"the corpus now fills it with {node[field_name]!r}, so this case "
            "no longer tests the middle state and needs a different section"
        )


#: Fields that are ``| None`` for reasons that have nothing to do with
#: ``include``. Each says something about the chemistry — this record has no
#: NASA-9 polynomial, no Chebyshev fit, no PLOG entries — and losing the key
#: loses the ability to say it. No token moves any of them, and the strip
#: must be structurally unable to.
UNGATED_NULLABLE_FIELDS = (
    ("GET /species-entries/{ref}/thermo", "records", "nasa9"),
    ("GET /species-entries/{ref}/thermo", "records", "wilhoit"),
    ("GET /species-entries/{ref}/thermo", "records", "group_additivity"),
    ("GET /species-entries/{ref}/thermo", "records", "points"),
    ("GET /species-entries/{ref}/thermo", "records", "supersession"),
    ("GET /reaction-entries/{ref}/kinetics", "records", "chebyshev"),
    ("GET /reaction-entries/{ref}/kinetics", "records", "plog_entries"),
    ("GET /reaction-entries/{ref}/kinetics", "records", "falloff"),
    ("GET /reaction-entries/{ref}/kinetics", "records", "multi_arrhenius"),
    ("GET /reaction-entries/{ref}/kinetics", "records", "third_body_efficiencies"),
    ("GET /statmech/{ref}", "record", "literature"),
    ("GET /statmech/{ref}", "record", "software_release"),
    ("GET /statmech/{ref}", "record", "workflow_tool_release"),
    ("GET /statmech/{ref}", "record", "transition_state"),
    ("GET /networks/{ref}", "record", "literature"),
    ("GET /networks/{ref}", "record", "software_release"),
    ("GET /conformer-observations/{ref}", "record", "assignment_scheme"),
    ("GET /frequency-scale-factors/{ref}", "record", "literature"),
    ("GET /frequency-scale-factors/{ref}", "record", "workflow_tool_release"),
    ("GET /energy-correction-schemes/{ref}", "record", "literature"),
)


def _thermo_case(corpus):
    return f"{_SCI}/species-entries/{corpus['species_entry_ref']}/thermo"


@pytest.mark.parametrize(
    ("label", "envelope", "field_name"),
    UNGATED_NULLABLE_FIELDS,
    ids=lambda v: v,
)
def test_an_ungated_nullable_field_keeps_its_null(
    label, envelope, field_name, client, corpus
):
    """Absence describes the request; null describes the data.

    This is the assertion an over-broad strip fails and the positive cases
    do not: every one of these keys is ``null`` on the corpus, none is
    named by any table, and all of them must still be on the wire — under
    the default request *and* under ``include=all``, which is where a
    scope-widening mistake would show.
    """
    if label == "GET /species-entries/{ref}/thermo":
        url = _thermo_case(corpus)
    else:
        url = _BY_LABEL[label].path(corpus)

    for query in ("", "?include=all"):
        response = client.get(f"{url}{query}")
        assert response.status_code == 200, response.text
        body = response.json()
        records = (
            body["records"] if envelope == "records" else [body["record"]]
        )
        assert records, f"{label}: no records to assert on — vacuous case"
        for record in records:
            assert field_name in record, (
                f"{label}{query}: {field_name!r} is nullable for a reason that "
                "has nothing to do with include= and must not be omitted; "
                "dropping it makes 'no such fit' and 'you did not ask' the "
                "same wire value again, from the other side"
            )
            assert record[field_name] is None


@pytest.mark.parametrize(
    "case",
    [c for c in CASES if c.detail_only_tokens],
    ids=lambda c: c.label,
)
def test_a_detail_only_token_is_refused_and_its_field_never_appears(
    case: Case, client, corpus
):
    """The residue class, asserted rather than excused.

    A token its twin accepts and this operation does not leaves the field
    unconditionally absent here. That is the harmless direction — absent
    more often than the OpenAPI marker promises — but it has to be true,
    not assumed, or the marker becomes a claim about a key some operation
    always sends.
    """
    for token in case.detail_only_tokens:
        response = case.raw(client, corpus, include=[token])
        assert response.status_code == 422, (
            f"{case.label}: {token!r} is recorded as detail-only but this "
            "operation accepted it — the table and the vocabulary have "
            "converged and this exemption should be deleted"
        )
        assert response.json()["code"] == "unknown_include_token"

        for field_name in case.table.sections[token]:
            for query in ([], ["all"]):
                body = case.request(client, corpus, include=query)
                for node in _nodes(case, body):
                    assert field_name not in node
