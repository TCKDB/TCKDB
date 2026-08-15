"""Four refusals over a cited row a request did not create, on the wire.

What was wrong
--------------
Three uploads let a depositor name a record by a handle resolved in a
database-wide namespace and then attach it to a subject:

* ``/uploads/thermo`` takes ``existing_statmech_id`` -- a literal row id;
* ``/uploads/kinetics`` takes ``statmech_ref``, a public ref, on every
  interpretation assignment;
* the same assignment takes a ``conformer_selection`` *content* locator,
  resolved against whichever species entry it names.

Each is guarded, and each guard raised a bare ``ValueError``, so all four
refusals arrived as ``code="validation_error"``. These are the plainest
instances of the reachability rule's first clause: the enclosing block
scopes nothing, and a depositor supplies the handle themselves.

Why these tests are on the wire
-------------------------------
The workflow tests establish that the guards fire; only a request can
establish that the envelope carries the code out to a client. Assertions
are on ``code`` and ``context``, never on substrings of ``detail``.

Every refusal here is paired with the request that must still be
**accepted**, because a guard that refuses everything satisfies a 422
assertion perfectly -- and that pairing is the whole reason to run these
against a live database rather than mocking the lookups.
"""

from __future__ import annotations

from app.db.models.statmech import Statmech
from tests.services.scientific_read._factories import (
    attach_conformer_selection,
    make_chem_reaction,
    make_conformer_group,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
)

_THERMO = "/api/v1/uploads/thermo"
_KINETICS = "/api/v1/uploads/kinetics"

_THERMO_STATMECH_MISMATCH = "thermo_statmech_owner_mismatch"
_INTERPRETATION_STATMECH_MISMATCH = "kinetics_interpretation_statmech_owner_mismatch"
_CONFORMER_SELECTION_MISMATCH = (
    "kinetics_interpretation_conformer_selection_owner_mismatch"
)

_CONVENTIONS = {
    "ensemble_policy": "single_structure",
    "standard_state_convention": "ideal_gas_1_bar",
    "degeneracy_interpretation": "reaction_path_degeneracy",
}

#: ``CH3 + OH -> CH3OH``. Three distinct species, so "the statmech of the
#: wrong participant" is a payload a depositor could plausibly send.
_REACTION = {
    "reversible": False,
    "reactants": [
        {"species_entry": {"smiles": "[CH3]", "charge": 0, "multiplicity": 2}},
        {"species_entry": {"smiles": "[OH]", "charge": 0, "multiplicity": 2}},
    ],
    "products": [{"species_entry": {"smiles": "CO", "charge": 0, "multiplicity": 1}}],
}


def _seed_participants(session):
    """One species entry and one statmech row per participant.

    The statmech rows are ``experimental`` in origin on purpose. A
    *computed* statmech backing a computed rate must carry source
    calculations, and that separate refusal would fire first and refuse
    these payloads for a reason that has nothing to do with ownership --
    which is exactly the shape of test that passes while proving nothing.
    """
    entries = {
        "ch3": make_species_entry(
            session,
            make_species(
                session, smiles="[CH3]", multiplicity=2,
                inchi_key=next_inchi_key("OWNA"),
            ),
        ),
        "oh": make_species_entry(
            session,
            make_species(
                session, smiles="[OH]", multiplicity=2,
                inchi_key=next_inchi_key("OWNB"),
            ),
        ),
        "ch3oh": make_species_entry(
            session,
            make_species(
                session, smiles="CO", multiplicity=1,
                inchi_key=next_inchi_key("OWNC"),
            ),
        ),
    }
    statmechs = {}
    for key, entry in entries.items():
        row = Statmech(species_entry_id=entry.id, scientific_origin="experimental")
        session.add(row)
        statmechs[key] = row
    session.flush()
    return entries, statmechs


def _assignments(statmechs, **overrides):
    """The complete, correct interpretation set, before any corruption.

    Complete because the schema refuses a partial one: naming any
    participant is the claim that all of them are named.
    """
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
    for key, patch in overrides.items():
        rows[key] = {**rows[key], **patch}
    return list(rows.values())


def _rate(assignments) -> dict:
    return {
        "reaction": _REACTION,
        "scientific_origin": "computed",
        "a": 1.0e13,
        "a_units": "cm3_mol_s",
        "interpretation_assignments": assignments,
    }


# ---------------------------------------------------------------------------
# /uploads/thermo -- existing_statmech_id
# ---------------------------------------------------------------------------


def test_a_thermo_citing_another_species_statmech_is_coded(client, db_session) -> None:
    other = make_species_entry(
        db_session,
        make_species(
            db_session, smiles="[NH2]", multiplicity=2,
            inchi_key=next_inchi_key("OWND"),
        ),
    )
    statmech = Statmech(species_entry_id=other.id, scientific_origin="computed")
    db_session.add(statmech)
    db_session.flush()

    resp = client.post(
        _THERMO,
        json={
            "species_entry": {"smiles": "[CH3]", "charge": 0, "multiplicity": 2},
            "scientific_origin": "computed",
            "h298_kj_mol": 146.7,
            "existing_statmech_id": statmech.id,
        },
    )
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == _THERMO_STATMECH_MISMATCH, body
    assert body["context"]["field"] == "thermo existing_statmech_id", body
    assert body["context"]["owner_kind"] == "species_entry", body
    # The id the depositor supplied is theirs; the id of the entry that
    # owns it is not (DR-0028 Requirement 2).
    assert str(other.id) not in str(body.get("detail", "")), body


def test_a_thermo_citing_its_own_statmech_is_accepted(client, db_session) -> None:
    """The negative half. Same route, same field, correct owner."""
    entry = make_species_entry(
        db_session,
        make_species(
            db_session, smiles="[CH3]", multiplicity=2,
            inchi_key=next_inchi_key("OWNE"),
        ),
    )
    statmech = Statmech(species_entry_id=entry.id, scientific_origin="computed")
    db_session.add(statmech)
    db_session.flush()

    resp = client.post(
        _THERMO,
        json={
            "species_entry": {"smiles": "[CH3]", "charge": 0, "multiplicity": 2},
            "scientific_origin": "computed",
            "h298_kj_mol": 146.7,
            "existing_statmech_id": statmech.id,
        },
    )
    assert resp.status_code == 201, resp.text[:800]


# ---------------------------------------------------------------------------
# /uploads/kinetics -- interpretation statmech_ref
# ---------------------------------------------------------------------------


def test_a_participant_assigned_another_species_statmech_is_coded(
    client, db_session
) -> None:
    """CH3's slot is handed OH's partition function.

    Both refs resolve and both rows exist, so ownership is the only thing
    left wrong -- the property that makes this a test of the guard rather
    than of the lookup above it.
    """
    _entries, statmechs = _seed_participants(db_session)
    resp = client.post(
        _KINETICS,
        json=_rate(
            _assignments(
                statmechs,
                **{"reactant:1": {"statmech_ref": statmechs["oh"].public_ref}},
            )
        ),
    )
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == _INTERPRETATION_STATMECH_MISMATCH, body
    assert body["context"]["field"] == "interpretation_assignments[0].statmech_ref", body
    assert body["context"]["owner_kind"] == "species_entry", body
    assert body["context"]["target"] == "reactant 1", body


def test_the_transition_state_slot_reports_the_same_code(client, db_session) -> None:
    """A species statmech handed to the TS slot, distinguished by owner kind.

    The TS entry is seeded on the same reaction the payload declares --
    ``_resolve_ts_anchored_reaction_entry`` refuses a TS whose reaction
    entry does not match the submitted content, and that refusal would
    otherwise fire first and hide this one.
    """
    entries, statmechs = _seed_participants(db_session)
    reaction_entry = make_reaction_entry(
        db_session,
        reaction=make_chem_reaction(
            db_session,
            reactants=[entries["ch3"].species, entries["oh"].species],
            products=[entries["ch3oh"].species],
            reversible=False,
        ),
        reactant_entries=[entries["ch3"], entries["oh"]],
        product_entries=[entries["ch3oh"]],
    )
    ts_entry = make_transition_state_entry(
        db_session,
        transition_state=make_transition_state(
            db_session, reaction_entry=reaction_entry
        ),
    )

    resp = client.post(
        _KINETICS,
        json=_rate(
            [
                *_assignments(statmechs),
                {
                    "role": "transition_state",
                    "statmech_ref": statmechs["ch3"].public_ref,
                    "transition_state_entry_ref": ts_entry.public_ref,
                    **_CONVENTIONS,
                },
            ]
        ),
    )
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == _INTERPRETATION_STATMECH_MISMATCH, body
    assert body["context"]["owner_kind"] == "transition_state_entry", body
    assert body["context"]["target"] == "transition state", body


def test_a_correct_interpretation_set_is_accepted(client, db_session) -> None:
    """The negative half of both interpretation tests."""
    _entries, statmechs = _seed_participants(db_session)
    resp = client.post(_KINETICS, json=_rate(_assignments(statmechs)))
    assert resp.status_code == 201, resp.text[:800]


# ---------------------------------------------------------------------------
# /uploads/kinetics -- interpretation conformer_selection
# ---------------------------------------------------------------------------


def test_a_conformer_selection_for_another_species_is_coded(
    client, db_session
) -> None:
    """The ``statmech_ref`` is right and the selection is the wrong species.

    A distinct code from the one above precisely because of that: the
    depositor's repair is in ``conformer_selection``, not in
    ``statmech_ref``, and telling them the statmech was wrong would send
    them to the wrong field.
    """
    entries, statmechs = _seed_participants(db_session)
    attach_conformer_selection(
        db_session, conformer_group=make_conformer_group(db_session, entries["oh"])
    )

    resp = client.post(
        _KINETICS,
        json=_rate(
            _assignments(
                statmechs,
                **{
                    "reactant:1": {
                        "conformer_selection": {
                            "species_entry": {
                                "smiles": "[OH]", "charge": 0, "multiplicity": 2,
                            },
                            "selection_kind": "lowest_energy",
                        }
                    }
                },
            )
        ),
    )
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == _CONFORMER_SELECTION_MISMATCH, body
    assert body["context"]["field"] == (
        "interpretation_assignments[0].conformer_selection"
    ), body
    assert body["context"]["target"] == "reactant 1", body


def test_a_conformer_selection_for_its_own_species_is_accepted(
    client, db_session
) -> None:
    """The negative half: the identical locator, on the species that owns it."""
    entries, statmechs = _seed_participants(db_session)
    attach_conformer_selection(
        db_session, conformer_group=make_conformer_group(db_session, entries["ch3"])
    )

    resp = client.post(
        _KINETICS,
        json=_rate(
            _assignments(
                statmechs,
                **{
                    "reactant:1": {
                        "conformer_selection": {
                            "species_entry": {
                                "smiles": "[CH3]", "charge": 0, "multiplicity": 2,
                            },
                            "selection_kind": "lowest_energy",
                        }
                    }
                },
            )
        ),
    )
    assert resp.status_code == 201, resp.text[:800]


def test_no_statmech_citation_refusal_discloses_a_row_id(client, db_session) -> None:
    """The ids go to the log. Status and code are asserted first.

    Without them, a mutation that stops a guard firing leaves a 201 whose
    body contains no ids either and this passes while checking nothing --
    the failure shape #192 was opened for.
    """
    entries, statmechs = _seed_participants(db_session)
    attach_conformer_selection(
        db_session, conformer_group=make_conformer_group(db_session, entries["oh"])
    )

    cases = [
        (
            _rate(
                _assignments(
                    statmechs,
                    **{"reactant:1": {"statmech_ref": statmechs["oh"].public_ref}},
                )
            ),
            _INTERPRETATION_STATMECH_MISMATCH,
        ),
        (
            _rate(
                _assignments(
                    statmechs,
                    **{
                        "reactant:1": {
                            "conformer_selection": {
                                "species_entry": {
                                    "smiles": "[OH]", "charge": 0,
                                    "multiplicity": 2,
                                },
                                "selection_kind": "lowest_energy",
                            }
                        }
                    },
                )
            ),
            _CONFORMER_SELECTION_MISMATCH,
        ),
    ]
    assert len(cases) == 2, "both kinetics-side citations #195 coded"

    forbidden = (
        str(entries["oh"].id),
        str(statmechs["oh"].id),
        "species_entry_id=",
        "transition_state_entry_id=",
    )
    for payload, code in cases:
        resp = client.post(_KINETICS, json=payload)
        assert resp.status_code == 422, resp.text[:800]
        body = resp.json()
        assert body["code"] == code, body
        rendered = str(body.get("detail", "")) + str(body.get("context", {}))
        for token in forbidden:
            assert token not in rendered, (body, token)
