"""What happens to a reaction deposited before its atom map existed.

ADR 0011 says an absent map warns rather than blocks, so a depositor who has
no map is told to deposit anyway. This file pins what that depositor can and
cannot do afterwards, because the answer is not obvious and every part of it
is a deliberate rule working as designed:

* re-depositing with a map yields a **second, unlinked** ``reaction_entry``
  rather than upgrading the first;
* replaying the original ``Idempotency-Key`` with the corrected payload is
  refused and writes nothing;
* the supersession route -- which ``b6c1f4a8e703`` names as the correction
  path for a *wrong* map -- **cannot join two separately deposited
  transition-state entries**, because each deposit mints its own
  ``transition_state`` and the subject identity for a
  ``transition_state_entry`` is ``(transition_state_id,)``.

That last one is the load-bearing fact behind
``backend/docs/specs/late_atom_maps.md``. It is asserted here rather than
described there, because the neighbouring proof --
``tests/db/test_atom_map_immutability.py::
test_a_wrong_map_is_corrected_by_superseding_its_transition_state_entry`` --
builds its replacement entry *directly in the session* under the same
``transition_state``, which no API caller can do. That test proves the
curation endpoint works; it does not prove the route is reachable, and these
tests are what tell the two apart.

These are characterisation tests. If a future change makes the second deposit
linkable to the first, the assertions here should be updated to describe the
new behaviour -- they are not asserting that the current shape is desirable.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.common import SubmissionRecordType
from app.db.models.reaction import ReactionEntry
from app.db.models.transition_state import TransitionState, TransitionStateEntry

_XYZ_H = "1\nH\nH 0.0 0.0 0.0"
_XYZ_CH3 = (
    "4\nmethyl\n"
    "C  0.000  0.000  0.000\n"
    "H  1.080  0.000  0.000\n"
    "H -0.540  0.935  0.000\n"
    "H -0.540 -0.935  0.000"
)
_XYZ_CH4 = (
    "5\nmethane\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.629 -0.629 -0.629"
)
_XYZ_TS = (
    "5\nTS for CH3 + H -> CH4\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.000  0.000  1.400"
)

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}

#: Long enough for the idempotency key shape rule (16-200 of [A-Za-z0-9._:-]).
_KEY = "late-atom-map-redeposit-0001"


def _species(key: str, smiles: str, multiplicity: int, xyz: str) -> dict:
    return {
        "key": key,
        "species_entry": {
            "smiles": smiles,
            "charge": 0,
            "multiplicity": multiplicity,
        },
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


def _bundle(atom_map: dict | None) -> dict:
    """The same reaction either way -- only the map differs."""
    bundle: dict = {
        "species": [
            _species("ch3", "[CH3]", 2, _XYZ_CH3),
            _species("h", "[H]", 2, _XYZ_H),
            _species("ch4", "C", 1, _XYZ_CH4),
        ],
        "reversible": True,
        "reactant_keys": ["ch3", "h"],
        "product_keys": ["ch4"],
        "transition_state": {
            "charge": 0,
            "multiplicity": 2,
            "geometry": {"key": "ts-geom", "xyz_text": _XYZ_TS},
            "calculation": {
                "key": "ts-opt",
                "type": "opt",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "opt_converged": True,
            },
            "calculations": [
                {
                    "key": "ts-freq",
                    "type": "freq",
                    "geometry_key": "ts-geom",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "freq_n_imag": 1,
                    "freq_imag_freq_cm1": -1500.0,
                }
            ],
        },
    }
    if atom_map is not None:
        bundle["atom_map"] = atom_map
    return bundle


def _map() -> dict:
    return {
        "source": "declared",
        "ts_geometry_key": "ts-geom",
        "participants": [
            {
                "side": "reactant",
                "species_key": "ch3",
                "participant_index": 1,
                "geometry_key": "ch3-geom",
                "atom_to_ts": {1: 1, 2: 2, 3: 3, 4: 4},
            },
            {
                "side": "reactant",
                "species_key": "h",
                "participant_index": 2,
                "geometry_key": "h-geom",
                "atom_to_ts": {1: 5},
            },
            {
                "side": "product",
                "species_key": "ch4",
                "participant_index": 1,
                "geometry_key": "ch4-geom",
                "atom_to_ts": {1: 1, 2: 2, 3: 3, 4: 4, 5: 5},
            },
        ],
    }


def _upload(client, atom_map: dict | None, *, key: str | None = None) -> dict:
    headers = {"Idempotency-Key": key} if key else None
    resp = client.post(
        "/api/v1/uploads/computed-reaction",
        json=_bundle(atom_map),
        headers=headers,
    )
    assert resp.status_code == 201, resp.text[:800]
    return resp.json()


def _ts_entry_id(db_session, reaction_entry_id: int) -> int:
    return db_session.scalars(
        select(TransitionStateEntry.id)
        .join(TransitionState, TransitionState.id == TransitionStateEntry.transition_state_id)
        .where(TransitionState.reaction_entry_id == reaction_entry_id)
    ).one()


def test_depositing_without_a_map_warns_rather_than_blocks(client):
    """ADR 0011's premise: the honest partial deposit is accepted."""
    result = _upload(client, None)
    codes = {warning["code"] for warning in result["warnings"]}
    assert "reaction_atom_map_absent" in codes


def test_redepositing_with_a_map_creates_a_second_unlinked_reaction_entry(
    client, db_session
):
    """The second deposit does not upgrade the first; it sits beside it.

    The two share a ``chem_reaction`` -- identity is deduped on the
    stoichiometry hash -- and share nothing else. In particular the original
    entry still reads as unmapped, which is the outcome a depositor is most
    likely to be surprised by.
    """
    first = _upload(client, None)
    second = _upload(client, _map())

    assert first["reaction_entry_id"] != second["reaction_entry_id"]

    entries = {
        row.id: row.reaction_id
        for row in db_session.scalars(
            select(ReactionEntry).where(
                ReactionEntry.id.in_(
                    (first["reaction_entry_id"], second["reaction_entry_id"])
                )
            )
        )
    }
    # Same reaction concept, two entries under it.
    assert len(set(entries.values())) == 1

    # The first entry is still unmapped when read back, and the second
    # carries the map. Nothing points either at the other.
    first_full = client.get(
        f"/api/v1/scientific/reaction-entries/{first['reaction_entry_id']}/full"
    ).json()
    second_full = client.get(
        f"/api/v1/scientific/reaction-entries/{second['reaction_entry_id']}/full"
    ).json()
    assert first_full["reaction_entry"]["atom_maps"] == []
    assert len(second_full["reaction_entry"]["atom_maps"]) == 1


def test_each_deposit_mints_its_own_transition_state(client, db_session):
    """Why the supersession route cannot join them -- the mechanism."""
    first = _upload(client, None)
    second = _upload(client, _map())

    first_ts = db_session.scalars(
        select(TransitionState.id).where(
            TransitionState.reaction_entry_id == first["reaction_entry_id"]
        )
    ).one()
    second_ts = db_session.scalars(
        select(TransitionState.id).where(
            TransitionState.reaction_entry_id == second["reaction_entry_id"]
        )
    ).one()
    assert first_ts != second_ts


def test_replaying_the_original_key_with_the_corrected_payload_writes_nothing(
    client, db_session
):
    """The 409 is the idempotency key doing its job, not an obstacle.

    A key promises that one key means one request. The corrected payload is a
    different request, so honouring the key would mean either silently
    ignoring the correction or silently applying a body the caller never
    associated with that key. Refusing is the only answer that keeps the
    promise -- the depositor's move is a new key, which is the test above.
    """
    _upload(client, None, key=_KEY)
    before = db_session.scalars(select(ReactionEntry.id)).all()

    conflict = client.post(
        "/api/v1/uploads/computed-reaction",
        json=_bundle(_map()),
        headers={"Idempotency-Key": _KEY},
    )

    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"
    # Rejected before the route body ran, so no partial deposit landed.
    assert db_session.scalars(select(ReactionEntry.id)).all() == before


def test_supersession_cannot_join_two_separately_deposited_entries(
    client, db_session, login_as, _api_curator_user
):
    """The gap, stated as an assertion.

    ``b6c1f4a8e703`` prescribes "deposit a new transition-state entry
    carrying the map, approve it, and record a ``transition_state_entry``
    supersession". Through the API that prescription cannot be followed: the
    new entry arrives under a *new* ``transition_state``, and
    ``supersession_subject`` for a ``transition_state_entry`` is
    ``(transition_state_id,)``, so the two entries are not the same subject.

    This is a refusal, not a corruption -- the curation route declines rather
    than recording a misleading edge, which is the right failure. But it means
    the documented correction path has no API-only route today.
    """
    from app.db.models.app_user import AppUser
    from app.db.models.common import RecordReviewStatus
    from app.services.record_review import (
        ensure_record_review,
        set_record_review_status,
    )

    first = _upload(client, None)
    second = _upload(client, _map())
    old_entry = _ts_entry_id(db_session, first["reaction_entry_id"])
    new_entry = _ts_entry_id(db_session, second["reaction_entry_id"])

    curator = db_session.get(AppUser, _api_curator_user)
    for entry_id in (old_entry, new_entry):
        ensure_record_review(
            db_session,
            record_type=SubmissionRecordType.transition_state_entry,
            record_id=entry_id,
        )
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.transition_state_entry,
            record_id=entry_id,
            status=RecordReviewStatus.approved,
            actor=curator,
        )
    db_session.flush()

    login_as(_api_curator_user)
    response = client.post(
        "/api/v1/curation/scientific-record-supersessions",
        json={
            "record_type": SubmissionRecordType.transition_state_entry.value,
            "superseded_record_id": old_entry,
            "superseding_record_id": new_entry,
            "reason": "the first deposit predated the atom map",
        },
    )

    assert response.status_code == 400, response.text[:800]
    assert "same subject" in response.json()["detail"]
