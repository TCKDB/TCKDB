"""Every ``/uploads/kinetics`` ref that names no row, asserted on the wire.

What changed, and why the status is the assertion
--------------------------------------------------
A kinetics deposit cites already-stored records by public ref: the
statmech an interpretation was built from, the transition-state entry the
rate is anchored to, the calculation a tunneling correction was read out
of, the artifact holding its output, the network solve it came from. Every
one of those lookups used to raise a bare ``ValueError``, so a depositor
received **422 ``validation_error`` with an empty ``context``** and had to
read English prose to find out which of five refs was wrong.

Both halves of that were wrong:

* **422 says "your message is malformed, fix it and resend".** Nothing a
  depositor can retype makes a missing statmech exist. The honest answer
  is 404 — the thing you named is not there — and it is what a client
  needs in order to retry differently (deposit the dependency, *then*
  resend) instead of resending forever.
* **A generic code with no context** left the client string-matching a
  sentence. Each refusal now carries its own code and a ``context``
  naming the field, the kind of row and the ref that failed.

It was also inconsistent *across upload roots*, which is the part no
single route could have shown: ``/uploads/thermo`` and ``/uploads/statmech``
already answered 404 when ``existing_statmech_id`` or
``existing_calculation_id`` named no row. The status therefore depended on
whether the caller spelled the name as a row id or as a public ref, which
is not a distinction a depositor can act on. That neighbouring behaviour
is asserted here too, so "consistent across roots" is a checked claim
rather than a sentence in a commit message.

The shape of every test here
----------------------------
* the **exact status and code**, plus the structured ``context`` — never a
  substring of ``detail``, which quotes the caller's own input back and so
  can match a request that was wrongly accepted;
* a **neighbouring request that must still be accepted**, because a test
  asserting only "a 4xx arrived" passes against a handler that refuses
  everything;
* no database primary key anywhere in the body (DR-0028 Requirement 2).

One refusal here is deliberately *not* a separate test
-------------------------------------------------------
``unknown_transition_state_entry_ref`` is written at three raise sites and
only one of them is reachable through a route. The upload schema confines
an interpretation's ``transition_state_entry_ref`` to
``role='transition_state'``, and the TS-anchored reaction lookup collects
every such ref *plus* the tunneling block's and resolves them before
either of the other two sites runs. Both routes into the condition are
asserted below and both arrive at the anchor lookup — which is the fact,
and the reason this file does not claim three tests' worth of coverage for
one reachable branch.
"""

from __future__ import annotations

import pytest

from app.db.models.statmech import Statmech
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
)

_KINETICS = "/api/v1/uploads/kinetics"
_THERMO = "/api/v1/uploads/thermo"

_CONVENTIONS = {
    "ensemble_policy": "single_structure",
    "standard_state_convention": "ideal_gas_1_bar",
    "degeneracy_interpretation": "reaction_path_degeneracy",
}

#: ``CH3 + OH -> CH3OH``. Element-balanced, so a refusal here is about the
#: ref under test and not about the reaction.
_REACTION = {
    "reversible": False,
    "reactants": [
        {"species_entry": {"smiles": "[CH3]", "charge": 0, "multiplicity": 2}},
        {"species_entry": {"smiles": "[OH]", "charge": 0, "multiplicity": 2}},
    ],
    "products": [{"species_entry": {"smiles": "CO", "charge": 0, "multiplicity": 1}}],
}


@pytest.fixture
def corpus(db_session):
    """The reaction entry, its TS entry, and a statmech for every subject.

    The statmech rows are ``experimental`` in origin on purpose: a
    *computed* statmech backing a computed rate must carry source
    calculations, and that separate refusal would fire first and refuse
    these payloads for a reason that has nothing to do with the ref under
    test — the shape of test that passes while proving nothing.
    """
    ch3 = make_species_entry(
        db_session,
        make_species(
            db_session, smiles="[CH3]", multiplicity=2,
            inchi_key=next_inchi_key("UREF"),
        ),
    )
    oh = make_species_entry(
        db_session,
        make_species(
            db_session, smiles="[OH]", multiplicity=2,
            inchi_key=next_inchi_key("UREG"),
        ),
    )
    ch3oh = make_species_entry(
        db_session,
        make_species(
            db_session, smiles="CO", multiplicity=1,
            inchi_key=next_inchi_key("UREH"),
        ),
    )
    reaction = make_chem_reaction(
        db_session,
        reactants=[ch3.species, oh.species],
        products=[ch3oh.species],
        reversible=False,
    )
    entry = make_reaction_entry(
        db_session,
        reaction=reaction,
        reactant_entries=[ch3, oh],
        product_entries=[ch3oh],
    )
    ts_entry = make_transition_state_entry(
        db_session,
        transition_state=make_transition_state(db_session, reaction_entry=entry),
        multiplicity=2,
    )
    statmechs = {
        "ch3": Statmech(species_entry_id=ch3.id, scientific_origin="experimental"),
        "oh": Statmech(species_entry_id=oh.id, scientific_origin="experimental"),
        "ch3oh": Statmech(species_entry_id=ch3oh.id, scientific_origin="experimental"),
        "ts": Statmech(
            transition_state_entry_id=ts_entry.id, scientific_origin="experimental"
        ),
    }
    db_session.add_all(statmechs.values())
    db_session.flush()
    return ts_entry, statmechs


def _rate(**overrides) -> dict:
    body = {
        "reaction": _REACTION,
        "scientific_origin": "computed",
        "a": 1.0e13,
        "a_units": "cm3_mol_s",
        "interpretation_assignments": [],
    }
    body.update(overrides)
    return body


def _assignments(statmechs, ts_entry=None, **overrides) -> list[dict]:
    """The complete interpretation set — the schema refuses a partial one."""
    rows = {
        "reactant:1": {
            "role": "reactant", "participant_index": 1,
            "statmech_ref": statmechs["ch3"].public_ref, **_CONVENTIONS,
        },
        "reactant:2": {
            "role": "reactant", "participant_index": 2,
            "statmech_ref": statmechs["oh"].public_ref, **_CONVENTIONS,
        },
        "product:1": {
            "role": "product", "participant_index": 1,
            "statmech_ref": statmechs["ch3oh"].public_ref, **_CONVENTIONS,
        },
    }
    if ts_entry is not None:
        rows["transition_state"] = {
            "role": "transition_state",
            "statmech_ref": statmechs["ts"].public_ref,
            "transition_state_entry_ref": ts_entry.public_ref,
            **_CONVENTIONS,
        }
    for key, patch in overrides.items():
        rows[key] = {**rows[key], **patch}
    return list(rows.values())


def _missing(response, *, code: str, field: str, kind: str, ref: str) -> dict:
    """Assert the 404 a client branches on, and return the body.

    ``detail`` is deliberately not matched: it quotes the caller's own ref
    back, so a substring check would pass against a request that had been
    accepted and echoed elsewhere.
    """
    assert response.status_code == 404, response.text[:800]
    body = response.json()
    assert body["code"] == code, body
    context = body["context"]
    assert context["field"] == field, body
    assert context["kind"] == kind, body
    assert context["ref"] == ref, body
    leaked = [key for key in context if key == "id" or key.endswith("_id")]
    assert not leaked, f"{code} leaked database ids into context: {leaked}"
    return body


# ---------------------------------------------------------------------------
# The five refusals
# ---------------------------------------------------------------------------


def test_an_interpretation_naming_no_statmech_is_a_coded_404(client, corpus):
    """A rate cannot be built from a partition function that is not there.

    The repair is to deposit the statmech, which is a different request —
    so the depositor is told the row is missing rather than that their
    payload is malformed.
    """
    _ts_entry, statmechs = corpus
    refused = client.post(
        _KINETICS,
        json=_rate(
            interpretation_assignments=_assignments(
                statmechs, **{"reactant:1": {"statmech_ref": "sm_notdeposited"}}
            )
        ),
    )
    _missing(
        refused,
        code="unknown_statmech_ref",
        field="interpretation_assignments[0].statmech_ref",
        kind="statmech",
        ref="sm_notdeposited",
    )

    # The same request with every ref resolving is accepted.
    accepted = client.post(
        _KINETICS, json=_rate(interpretation_assignments=_assignments(statmechs))
    )
    assert accepted.status_code == 201, accepted.text[:800]


def test_a_ts_anchor_naming_no_transition_state_entry_is_a_coded_404(client, corpus):
    """Both ways of naming a TS reach the same anchor lookup.

    Asserted from the interpretation side *and* the tunneling side, because
    the two are the only routes into the condition and a test of one would
    read as a test of both.
    """
    ts_entry, statmechs = corpus

    from_interpretation = client.post(
        _KINETICS,
        json=_rate(
            interpretation_assignments=_assignments(
                statmechs, ts_entry=ts_entry,
                **{"transition_state": {"transition_state_entry_ref": "tse_gone"}},
            ),
            tunneling_application={
                "model": "wigner",
                "transition_state_entry_ref": "tse_gone",
                "imaginary_frequency_cm1": -1500.0,
            },
        ),
    )
    _missing(
        from_interpretation,
        code="unknown_transition_state_entry_ref",
        field="transition_state_entry_ref",
        kind="transition_state_entry",
        ref="tse_gone",
    )

    from_tunneling = client.post(
        _KINETICS,
        json=_rate(
            tunneling_application={
                "model": "wigner",
                "transition_state_entry_ref": "tse_gone",
                "imaginary_frequency_cm1": -1500.0,
            }
        ),
    )
    _missing(
        from_tunneling,
        code="unknown_transition_state_entry_ref",
        field="transition_state_entry_ref",
        kind="transition_state_entry",
        ref="tse_gone",
    )

    accepted = client.post(
        _KINETICS,
        json=_rate(
            interpretation_assignments=_assignments(statmechs, ts_entry=ts_entry),
            tunneling_application={
                "model": "wigner",
                "transition_state_entry_ref": ts_entry.public_ref,
                "imaginary_frequency_cm1": -1500.0,
            },
        ),
    )
    assert accepted.status_code == 201, accepted.text[:800]


def test_a_tunneling_source_calculation_that_is_not_there_is_a_coded_404(
    client, corpus
):
    """Without the job the energies were read from, the block is untraceable."""
    ts_entry, _statmechs = corpus
    tunneling = {
        "model": "wigner",
        "transition_state_entry_ref": ts_entry.public_ref,
        "imaginary_frequency_cm1": -1500.0,
    }
    refused = client.post(
        _KINETICS,
        json=_rate(
            tunneling_application={**tunneling, "source_calculation_ref": "calc_gone"}
        ),
    )
    _missing(
        refused,
        code="unknown_calculation_ref",
        field="tunneling_application.source_calculation_ref",
        kind="calculation",
        ref="calc_gone",
    )

    # Omitting the optional ref entirely is still accepted: the guard
    # refuses a ref that names nothing, not the act of citing one.
    accepted = client.post(_KINETICS, json=_rate(tunneling_application=tunneling))
    assert accepted.status_code == 201, accepted.text[:800]


def test_a_tunneling_artifact_locator_that_matches_nothing_is_a_coded_404(
    client, corpus
):
    """The locator is a pair, and the refusal says so.

    ``context`` carries the digest as well as the calculation ref, because
    the pair can fail two ways — no such calculation, or that calculation
    holds no file with that SHA-256 — and a depositor repairs them
    differently.
    """
    ts_entry, _statmechs = corpus
    tunneling = {
        "model": "wigner",
        "transition_state_entry_ref": ts_entry.public_ref,
        "imaginary_frequency_cm1": -1500.0,
    }
    refused = client.post(
        _KINETICS,
        json=_rate(
            tunneling_application={
                **tunneling,
                "result_artifact_calculation_ref": "calc_gone",
                "result_artifact_sha256": "a" * 64,
            }
        ),
    )
    body = _missing(
        refused,
        code="unknown_calculation_artifact_ref",
        field="tunneling_application.result_artifact_calculation_ref",
        kind="calculation_artifact",
        ref="calc_gone",
    )
    assert body["context"]["sha256"] == "a" * 64, body

    accepted = client.post(_KINETICS, json=_rate(tunneling_application=tunneling))
    assert accepted.status_code == 201, accepted.text[:800]


def test_a_network_kinetics_ref_that_is_not_there_is_a_coded_404(client, corpus):
    """The bridge to a pressure-dependent solve names a row or it names nothing."""
    _ts_entry, statmechs = corpus
    refused = client.post(
        _KINETICS, json=_rate(network_kinetics_ref="nkin_gone")
    )
    _missing(
        refused,
        code="unknown_network_kinetics_ref",
        field="network_kinetics_ref",
        kind="network_kinetics",
        ref="nkin_gone",
    )

    accepted = client.post(
        _KINETICS, json=_rate(interpretation_assignments=_assignments(statmechs))
    )
    assert accepted.status_code == 201, accepted.text[:800]


# ---------------------------------------------------------------------------
# The neighbouring root, which is why these are 404 and not 409 or 422
# ---------------------------------------------------------------------------


def test_the_thermo_root_already_answered_404_for_the_same_condition(
    client, db_session
):
    """``/uploads/thermo`` was the precedent, and it is pinned here.

    Before #204 the status for "you named a record that is not there"
    depended on whether the caller spelled the name as a row id (404, on
    thermo and statmech) or as a public ref (422, on kinetics). This test
    holds the half that did *not* change, so a later edit cannot restore
    the split by moving thermo instead of kinetics.

    It also records what is still owed: this 404 carries the generic
    ``resource_not_found`` rather than a code of its own, so a client can
    read the status but not branch on the field. Giving the ``existing_*_id``
    404s real codes is a separate pass — asserting the current answer here
    is what keeps it from being forgotten.
    """
    refused = client.post(
        _THERMO,
        json={
            "species_entry": {"smiles": "[CH3]", "charge": 0, "multiplicity": 2},
            "scientific_origin": "computed",
            "h298_kj_mol": 146.7,
            "existing_statmech_id": 2_000_000_000,
        },
    )
    assert refused.status_code == 404, refused.text[:800]
    assert refused.json()["code"] == "resource_not_found", refused.json()

    accepted = client.post(
        _THERMO,
        json={
            "species_entry": {"smiles": "[CH3]", "charge": 0, "multiplicity": 2},
            "scientific_origin": "computed",
            "h298_kj_mol": 146.7,
        },
    )
    assert accepted.status_code == 201, accepted.text[:800]
