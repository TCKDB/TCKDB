"""The reaction bundle takes kinetics evidence exactly as ``/uploads/kinetics`` does (#620).

``BundleKineticsIn`` used to lack ``interpretation_assignments``,
``tunneling_application`` and ``network_kinetics_ref``, so the majority route
recorded less than the standalone one. They are now the same models, checked by
the same validators and written by the same persistence helpers. This file
drives one evidence fragment through both routes and asserts the *same refusal*
(status, code, the ref that failed) and the *same stored rows*.

Two limits are part of the contract and are asserted, not hidden:

* Every reference is a public ref of a record deposited **earlier**. A bundle
  cannot cite a statmech, transition state or calculation created by the same
  request, because the depositor cannot know its ref before it exists.
* A bundle always mints a new reaction entry, so a cited transition state must
  belong to the same *reaction*, not the same reaction *entry* (the standalone
  route derives the entry from the transition state, so it cannot disagree).

Every refusal is paired with an accepted neighbour built from the same
fixtures, so a route that refuses everything cannot pass.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.common import NetworkChannelKind, NetworkKineticsModelKind, NetworkStateKind
from app.db.models.kinetics import (
    Kinetics,
    KineticsInterpretationAssignment,
    KineticsTunnelingApplication,
)
from app.db.models.statmech import Statmech
from tests.services.scientific_read._factories import (
    attach_network_state_participant,
    make_chem_reaction,
    make_network,
    make_network_channel,
    make_network_kinetics,
    make_network_solve,
    make_network_state,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
)

_KINETICS = "/api/v1/uploads/kinetics"
_BUNDLE = "/api/v1/uploads/computed-reaction"

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}
_XYZ_H = "1\nH atom\nH 0.0 0.0 0.0"
_XYZ_H2 = "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74"

_CONVENTIONS = {
    "ensemble_policy": "single_structure",
    "standard_state_convention": "ideal_gas_1_bar",
    "degeneracy_interpretation": "reaction_path_degeneracy",
}

_REACTION = {
    "reversible": False,
    "reactants": [
        {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
        {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
    ],
    "products": [{"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}}],
}

_WIGNER = {"model": "wigner", "imaginary_frequency_cm1": -1500.0}


# ---------------------------------------------------------------------------
# The two routes, taking one evidence fragment
# ---------------------------------------------------------------------------


def _standalone(client, evidence: dict):
    return client.post(
        _KINETICS,
        json={
            "reaction": _REACTION,
            "scientific_origin": "computed",
            "model_kind": "modified_arrhenius",
            "a": 1.0e13,
            "a_units": "cm3_mol_s",
            **evidence,
        },
    )


def _species(key: str, smiles: str, multiplicity: int, xyz: str) -> dict:
    return {
        "key": key,
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": multiplicity},
        "conformers": [
            {
                "key": f"{key}-conf",
                "geometry": {"key": f"{key}-geom", "xyz_text": xyz},
                "calculation": {
                    "key": f"{key}-opt",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "opt_converged": True,
                },
            }
        ],
        "calculations": [],
    }


def _bundle(client, evidence: dict):
    return client.post(
        _BUNDLE,
        json={
            "species": [
                _species("h", "[H]", 2, _XYZ_H),
                _species("h2", "[H][H]", 1, _XYZ_H2),
            ],
            "reversible": False,
            "reactant_keys": ["h", "h"],
            "product_keys": ["h2"],
            "kinetics": [
                {
                    "scientific_origin": "computed",
                    "model_kind": "modified_arrhenius",
                    "a": 1.0e13,
                    "a_units": "cm3_mol_s",
                    "reactant_keys": ["h", "h"],
                    "product_keys": ["h2"],
                    **evidence,
                }
            ],
        },
    )


_ROUTES = pytest.mark.parametrize("send", [_standalone, _bundle], ids=["standalone", "bundle"])


# ---------------------------------------------------------------------------
# Fixtures: a stored H + H -> H2 with a statmech per subject and a TS
# ---------------------------------------------------------------------------


@pytest.fixture
def corpus(db_session):
    """``H + H -> H2`` with statmech rows for every subject and a transition state.

    The statmech rows are ``experimental`` in origin on purpose: a *computed*
    statmech backing a computed rate must carry source calculations, and that
    separate refusal would fire first and refuse these payloads for a reason
    unrelated to the ref under test.
    """
    from types import SimpleNamespace

    h = make_species_entry(
        db_session,
        make_species(db_session, smiles="[H]", multiplicity=2, inchi_key=next_inchi_key("PAH")),
    )
    h2 = make_species_entry(
        db_session,
        make_species(db_session, smiles="[H][H]", multiplicity=1, inchi_key=next_inchi_key("PAI")),
    )
    reaction = make_chem_reaction(
        db_session, reactants=[h.species, h.species], products=[h2.species], reversible=False
    )
    entry = make_reaction_entry(
        db_session,
        reaction=reaction,
        reactant_entries=[h, h],
        product_entries=[h2],
    )
    ts_entry = make_transition_state_entry(
        db_session,
        transition_state=make_transition_state(db_session, reaction_entry=entry),
        multiplicity=2,
    )
    statmechs = {
        "h": Statmech(species_entry_id=h.id, scientific_origin="experimental"),
        "h2": Statmech(species_entry_id=h2.id, scientific_origin="experimental"),
        "ts": Statmech(transition_state_entry_id=ts_entry.id, scientific_origin="experimental"),
    }
    db_session.add_all(statmechs.values())
    db_session.flush()
    return SimpleNamespace(h=h, h2=h2, entry=entry, ts_entry=ts_entry, statmechs=statmechs)


def _interpretations(corpus, *, with_ts: bool = False, **patches) -> list[dict]:
    """The complete interpretation set; the schema refuses a partial one."""
    rows = {
        "reactant:1": {
            "role": "reactant",
            "participant_index": 1,
            "statmech_ref": corpus.statmechs["h"].public_ref,
            **_CONVENTIONS,
        },
        "reactant:2": {
            "role": "reactant",
            "participant_index": 2,
            "statmech_ref": corpus.statmechs["h"].public_ref,
            **_CONVENTIONS,
        },
        "product:1": {
            "role": "product",
            "participant_index": 1,
            "statmech_ref": corpus.statmechs["h2"].public_ref,
            **_CONVENTIONS,
        },
    }
    if with_ts:
        rows["transition_state"] = {
            "role": "transition_state",
            "statmech_ref": corpus.statmechs["ts"].public_ref,
            "transition_state_entry_ref": corpus.ts_entry.public_ref,
            **_CONVENTIONS,
        }
    for key, patch in patches.items():
        rows[key] = {**rows[key], **patch}
    return list(rows.values())


def _refusal(response) -> dict:
    """The parts of an error body a client branches on, never ``detail``."""
    body = response.json()
    context = body.get("context") or {}
    return {
        "status": response.status_code,
        "code": body["code"],
        "kind": context.get("kind"),
        "ref": context.get("ref"),
        "field": context.get("field"),
    }


def _new_kinetics(db_session) -> Kinetics:
    return db_session.scalars(select(Kinetics).order_by(Kinetics.id.desc()).limit(1)).one()


# ---------------------------------------------------------------------------
# Accepted: the same rows, from both routes
# ---------------------------------------------------------------------------


@_ROUTES
def test_a_complete_interpretation_set_is_stored_identically(client, db_session, corpus, send):
    response = send(client, {"interpretation_assignments": _interpretations(corpus)})
    assert response.status_code == 201, response.text[:800]

    rows = db_session.scalars(
        select(KineticsInterpretationAssignment)
        .where(KineticsInterpretationAssignment.kinetics_id == _new_kinetics(db_session).id)
        .order_by(KineticsInterpretationAssignment.subject_key)
    ).all()
    assert [(r.subject_key, r.role, r.statmech_id) for r in rows] == [
        ("product:1", "product", corpus.statmechs["h2"].id),
        ("reactant:1", "reactant", corpus.statmechs["h"].id),
        ("reactant:2", "reactant", corpus.statmechs["h"].id),
    ]
    assert {r.ensemble_policy.value for r in rows} == {"single_structure"}
    assert {r.standard_state_convention.value for r in rows} == {"ideal_gas_1_bar"}


@_ROUTES
def test_a_tunneling_application_on_the_reactions_own_ts_is_stored_identically(
    client, db_session, corpus, send
):
    """The TS is cited by public ref and belongs to this reaction.

    Through the bundle the TS is one of a *different reaction entry* of the
    same reaction (a bundle always mints its own), which is why ownership is
    judged on the reaction there.
    """
    response = send(
        client,
        {
            "interpretation_assignments": _interpretations(corpus, with_ts=True),
            "tunneling_application": {
                **_WIGNER,
                "transition_state_entry_ref": corpus.ts_entry.public_ref,
            },
        },
    )
    assert response.status_code == 201, response.text[:800]

    kinetics = _new_kinetics(db_session)
    assert kinetics.tunneling_model.value == "wigner", "the label is filled from the evidence"
    application = db_session.scalars(
        select(KineticsTunnelingApplication).where(
            KineticsTunnelingApplication.kinetics_id == kinetics.id
        )
    ).one()
    assert application.model == "wigner"
    assert application.transition_state_entry_id == corpus.ts_entry.id
    assert application.imaginary_frequency_cm1 == -1500.0
    subjects = db_session.scalars(
        select(KineticsInterpretationAssignment.subject_key).where(
            KineticsInterpretationAssignment.kinetics_id == kinetics.id
        )
    ).all()
    assert "transition_state" in subjects


@_ROUTES
def test_a_network_kinetics_ref_is_stored_identically(client, db_session, corpus, send):
    network = make_network(db_session, name=f"net-{next_inchi_key('NET')}")
    species_entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("NA")))
    states = []
    for label in ("a", "b"):
        state = make_network_state(
            db_session,
            network=network,
            kind=NetworkStateKind.well,
            composition_hash=f"{label}{'0' * 63}",
        )
        attach_network_state_participant(db_session, state=state, species_entry=species_entry)
        states.append(state)
    channel = make_network_channel(
        db_session,
        network=network,
        source_state=states[0],
        sink_state=states[1],
        kind=NetworkChannelKind.isomerization,
    )
    network_kinetics = make_network_kinetics(
        db_session,
        channel=channel,
        solve=make_network_solve(db_session, network=network),
        model_kind=NetworkKineticsModelKind.plog,
    )

    response = send(client, {"network_kinetics_ref": network_kinetics.public_ref})
    assert response.status_code == 201, response.text[:800]
    assert _new_kinetics(db_session).network_kinetics_id == network_kinetics.id


@_ROUTES
def test_without_evidence_nothing_is_written(client, db_session, corpus, send):
    """The accepted neighbour of every refusal below: no evidence, no rows."""
    before = db_session.scalars(select(KineticsInterpretationAssignment.kinetics_id)).all()
    response = send(client, {})
    assert response.status_code == 201, response.text[:800]
    kinetics = _new_kinetics(db_session)
    assert kinetics.network_kinetics_id is None
    assert db_session.scalars(select(KineticsInterpretationAssignment.kinetics_id)).all() == before
    assert (
        db_session.scalars(
            select(KineticsTunnelingApplication).where(KineticsTunnelingApplication.kinetics_id == kinetics.id)
        ).first()
        is None
    )


# ---------------------------------------------------------------------------
# Refused: the same refusal from both routes
# ---------------------------------------------------------------------------


def _refused_alike(client, evidence: dict) -> tuple[dict, dict]:
    return _refusal(_standalone(client, evidence)), _refusal(_bundle(client, evidence))


def test_an_unknown_statmech_ref_is_the_same_404_on_both_routes(client, corpus):
    evidence = {
        "interpretation_assignments": _interpretations(
            corpus, **{"reactant:1": {"statmech_ref": "sm_notdeposited"}}
        )
    }
    standalone, bundle = _refused_alike(client, evidence)
    for seen in (standalone, bundle):
        assert (seen["status"], seen["code"], seen["kind"], seen["ref"]) == (
            404,
            "unknown_statmech_ref",
            "statmech",
            "sm_notdeposited",
        )
    assert standalone["field"] == "interpretation_assignments[0].statmech_ref"
    assert bundle["field"] == "kinetics[0].interpretation_assignments[0].statmech_ref"


def test_a_statmech_owned_by_another_species_is_the_same_refusal_on_both_routes(client, corpus):
    """reactant:1 is H, and the cited statmech is H2's."""
    evidence = {
        "interpretation_assignments": _interpretations(
            corpus, **{"reactant:1": {"statmech_ref": corpus.statmechs["h2"].public_ref}}
        )
    }
    standalone, bundle = _refused_alike(client, evidence)
    assert standalone["code"] == bundle["code"] == "kinetics_interpretation_statmech_owner_mismatch"
    assert standalone["status"] == bundle["status"] == 422


def test_an_unknown_network_kinetics_ref_is_the_same_404_on_both_routes(client, corpus):
    standalone, bundle = _refused_alike(client, {"network_kinetics_ref": "nkin_gone"})
    for seen in (standalone, bundle):
        assert (seen["status"], seen["code"], seen["kind"], seen["ref"]) == (
            404,
            "unknown_network_kinetics_ref",
            "network_kinetics",
            "nkin_gone",
        )
    assert bundle["field"] == "kinetics[0].network_kinetics_ref"


def test_an_unknown_tunneling_transition_state_is_the_same_404_on_both_routes(client, corpus):
    evidence = {"tunneling_application": {**_WIGNER, "transition_state_entry_ref": "tse_gone"}}
    standalone, bundle = _refused_alike(client, evidence)
    for seen in (standalone, bundle):
        assert (seen["status"], seen["code"], seen["kind"], seen["ref"]) == (
            404,
            "unknown_transition_state_entry_ref",
            "transition_state_entry",
            "tse_gone",
        )


def test_an_unknown_tunneling_source_calculation_is_the_same_404_on_both_routes(client, corpus):
    evidence = {
        "tunneling_application": {
            **_WIGNER,
            "transition_state_entry_ref": corpus.ts_entry.public_ref,
            "source_calculation_ref": "calc_gone",
        }
    }
    standalone, bundle = _refused_alike(client, evidence)
    for seen in (standalone, bundle):
        assert (seen["status"], seen["code"], seen["kind"], seen["ref"]) == (
            404,
            "unknown_calculation_ref",
            "calculation",
            "calc_gone",
        )


@pytest.mark.parametrize(
    ("evidence_for", "message"),
    [
        pytest.param(
            lambda c: {"interpretation_assignments": _interpretations(c)[:-1]},
            "computed kinetics requires one interpretation_assignment per reaction subject",
            id="partial-interpretation-set",
        ),
        pytest.param(
            lambda c: {"interpretation_assignments": _interpretations(c) + _interpretations(c)[:1]},
            "interpretation_assignments must be unique by role and participant_index",
            id="duplicate-subject",
        ),
        pytest.param(
            lambda c: {
                "interpretation_assignments": [
                    {**a, "participant_index": 9} if a["role"] == "product" else a
                    for a in _interpretations(c)
                ]
            },
            "is outside the declared product list",
            id="participant-index-out-of-range",
        ),
        pytest.param(
            lambda c: {
                "tunneling_model": "eckart",
                "tunneling_application": {
                    **_WIGNER,
                    "transition_state_entry_ref": c.ts_entry.public_ref,
                },
            },
            "tunneling_application.model must match tunneling_model",
            id="label-disagrees-with-evidence",
        ),
        pytest.param(
            lambda c: {
                "tunneling_application": {
                    "model": "eckart",
                    "imaginary_frequency_cm1": -1500.0,
                    "transition_state_entry_ref": c.ts_entry.public_ref,
                }
            },
            "Eckart tunneling requires reactant/product energies",
            id="eckart-without-barriers",
        ),
        pytest.param(
            lambda c: {
                "tunneling_application": {
                    "model": "wigner",
                    "imaginary_frequency_cm1": 1500.0,
                    "transition_state_entry_ref": c.ts_entry.public_ref,
                }
            },
            "imaginary_frequency_cm1 must be negative",
            id="positive-imaginary-frequency",
        ),
        pytest.param(
            lambda c: {
                "interpretation_assignments": [
                    *_interpretations(c),
                    {
                        "role": "transition_state",
                        "statmech_ref": c.statmechs["ts"].public_ref,
                        **_CONVENTIONS,
                    },
                ]
            },
            "transition_state interpretation requires transition_state_entry_ref",
            id="ts-interpretation-without-ts-ref",
        ),
    ],
)
def test_the_schema_refusals_are_the_same_422_on_both_routes(client, corpus, evidence_for, message):
    """One shared model and one set of validators, so one refusal.

    Asserted on the message, which here is the contract: these are validation
    errors of the shared models, not coded reference lookups. The accepted
    neighbours above use the same fixtures.
    """
    evidence = evidence_for(corpus)
    for send in (_standalone, _bundle):
        response = send(client, evidence)
        assert response.status_code == 422, response.text[:800]
        assert message in response.text, response.text[:1200]
        assert "Extra inputs are not permitted" not in response.text


def test_the_bundle_refuses_a_ts_of_a_different_reaction(client, db_session, corpus):
    """A bundle for H + H -> H2 cannot cite the TS of another reaction.

    Ownership is judged on the reaction because a bundle mints its own
    reaction entry; it must still refuse a TS that is not this reaction's.
    """
    a = make_species_entry(
        db_session, make_species(db_session, smiles="[O]", multiplicity=3, inchi_key=next_inchi_key("PAO"))
    )
    b = make_species_entry(
        db_session, make_species(db_session, smiles="[O][O]", multiplicity=3, inchi_key=next_inchi_key("PAP"))
    )
    other_entry = make_reaction_entry(
        db_session,
        reaction=make_chem_reaction(db_session, reactants=[a.species, a.species], products=[b.species], reversible=False),
        reactant_entries=[a, a],
        product_entries=[b],
    )
    other_ts = make_transition_state_entry(
        db_session,
        transition_state=make_transition_state(db_session, reaction_entry=other_entry),
        multiplicity=3,
    )
    evidence = {"tunneling_application": {**_WIGNER, "transition_state_entry_ref": other_ts.public_ref}}

    response = _bundle(client, evidence)
    assert response.status_code == 422, response.text[:800]
    assert "is not a transition state of this rate's reaction" in response.text

    # The neighbour: the same call citing this reaction's own TS is accepted.
    evidence["tunneling_application"]["transition_state_entry_ref"] = corpus.ts_entry.public_ref
    accepted = _bundle(client, evidence)
    assert accepted.status_code == 201, accepted.text[:800]


def _sibling_entry_ts(db_session, corpus, *, reactants, product):
    """A TS of a *different reaction entry of the same reaction*."""
    entry = make_reaction_entry(
        db_session,
        reaction=corpus.entry.reaction,
        reactant_entries=reactants,
        product_entries=[product],
    )
    return make_transition_state_entry(
        db_session,
        transition_state=make_transition_state(db_session, reaction_entry=entry),
        multiplicity=2,
    )


@pytest.mark.parametrize("variant", ["excited_state", "isotopologue"])
def test_the_bundle_refuses_a_ts_of_a_sibling_entry_of_the_same_reaction(
    client, db_session, corpus, variant
):
    """Same graph reaction is not the same rate.

    An excited-state H2 entry, or a D-for-H isotopologue entry, is another
    reaction entry of the same reaction with its own transition state. Citing
    it from a ground-state H + H -> H2 rate would attach its tunneling evidence
    to the wrong rate, through the tunneling block or a TS interpretation.
    """
    if variant == "excited_state":
        excited = make_species_entry(
            db_session,
            corpus.h2.species,
            electronic_state_kind="excited",
            electronic_state_label="B1Su",
        )
        sibling_ts = _sibling_entry_ts(
            db_session, corpus, reactants=[corpus.h, corpus.h], product=excited
        )
    else:
        deuterium = make_species_entry(db_session, corpus.h.species, isotope_key="D")
        sibling_ts = _sibling_entry_ts(
            db_session, corpus, reactants=[deuterium, corpus.h], product=corpus.h2
        )
    assert sibling_ts.id != corpus.ts_entry.id

    tunneling = {
        "tunneling_application": {**_WIGNER, "transition_state_entry_ref": sibling_ts.public_ref}
    }
    response = _bundle(client, tunneling)
    assert response.status_code == 422, response.text[:800]
    assert "is not a transition state of this rate's reaction" in response.text

    interpretation = {
        "interpretation_assignments": [
            *_interpretations(corpus),
            {
                "role": "transition_state",
                "statmech_ref": corpus.statmechs["ts"].public_ref,
                "transition_state_entry_ref": sibling_ts.public_ref,
                **_CONVENTIONS,
            },
        ]
    }
    response = _bundle(client, interpretation)
    assert response.status_code == 422, response.text[:800]
    assert "is not a transition state of this rate's reaction" in response.text

    # The neighbour: this rate's own TS is still accepted.
    own = {
        "tunneling_application": {**_WIGNER, "transition_state_entry_ref": corpus.ts_entry.public_ref}
    }
    assert _bundle(client, own).status_code == 201


def test_a_refused_bundle_leaves_no_kinetics_behind(client, db_session, corpus):
    """Evidence is resolved before the row is written: a refusal is atomic."""
    before = db_session.scalars(select(Kinetics.id)).all()
    response = _bundle(client, {"network_kinetics_ref": "nkin_gone"})
    assert response.status_code == 404
    assert db_session.scalars(select(Kinetics.id)).all() == before
