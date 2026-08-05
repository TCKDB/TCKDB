"""Atom mapping across a reaction (ADR 0011).

The reaction under test throughout is ``CH3 + H -> CH4`` with its saddle point,
the same bundle the rest of the computed-reaction workflow tests use. Its atom
counts are small enough to state a whole map inline and asymmetric enough that
a wrong map is visibly wrong: the methyl carries the carbon and three
hydrogens, the lone hydrogen becomes the fifth saddle-point atom, and methane
carries all five.

The tiering under test is ADR 0008's: **absence warns, contradiction blocks**.
An unmapped reaction is an incomplete record, not a false one, so it is
accepted and annotated. A map that cannot be what it says it is — an element
changing on the way across, an atom claimed twice, an atom of the reactants
unaccounted for in the products where no species is missing, an index naming an
atom that does not exist — is refused.
"""

from __future__ import annotations

import copy
from contextlib import contextmanager
from typing import Iterator

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.common import AtomMapSource, ReactionRole
from app.db.models.reaction_atom_map import ReactionAtomMap, ReactionAtomMapPair
from app.schemas.workflows.computed_reaction_upload import (
    ComputedReactionUploadRequest,
)
from app.services.reaction_atom_map import (
    W_ATOM_MAP_ATOMS_INCOMPLETE,
    W_ATOM_MAP_PARTICIPANTS_INCOMPLETE,
    W_MISSING_REACTION_ATOM_MAP,
)
from app.workflows.computed_reaction import persist_computed_reaction_upload

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
#: Same connectivity as methyl, but the carbon replaced by nitrogen. Used to
#: build a map whose two ends are different elements without having to write an
#: index that does not exist.
_XYZ_NH3 = (
    "4\nammonia\n"
    "N  0.000  0.000  0.000\n"
    "H  1.010  0.000  0.000\n"
    "H -0.505  0.875  0.000\n"
    "H -0.505 -0.875  0.000"
)

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}

_USER_ID = 50_411


@contextmanager
def _isolated_session(db_engine) -> Iterator[Session]:
    """Roll back everything: the workflow writes a large graph per call."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        session.add(AppUser(id=_USER_ID, username="reaction_atom_map_tests"))
        session.flush()
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


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
        "calculations": [
            {
                "key": f"{key}-freq",
                "type": "freq",
                "geometry_key": f"{key}-geom",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "freq_n_imag": 0,
            }
        ],
    }


def _payload(*, with_ts: bool = True) -> dict:
    """``CH3 + H -> CH4``, with its saddle point and no atom map."""
    payload: dict = {
        "species": [
            _species("ch3", "[CH3]", 2, _XYZ_CH3),
            _species("h", "[H]", 2, _XYZ_H),
            _species("ch4", "C", 1, _XYZ_CH4),
        ],
        "reversible": True,
        "reactant_keys": ["ch3", "h"],
        "product_keys": ["ch4"],
    }
    if with_ts:
        payload["transition_state"] = {
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
            "label": "ch3+h->ch4 TS",
        }
    return payload


def _complete_map(source: str = "declared", **overrides) -> dict:
    """The one correct map for this reaction.

    Methyl's carbon and three hydrogens are saddle-point atoms 1-4; the lone
    hydrogen becomes saddle-point atom 5; methane carries all five.
    """
    atom_map: dict = {
        "source": source,
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
    atom_map.update(overrides)
    return atom_map


def _upload(session: Session, payload: dict) -> dict:
    return persist_computed_reaction_upload(
        session,
        ComputedReactionUploadRequest(**payload),
        created_by=_USER_ID,
    )


def _codes(result: dict) -> set[str]:
    return {warning.code for warning in result["warnings"]}


# ---------------------------------------------------------------------------
# A declared map is accepted and round-trips
# ---------------------------------------------------------------------------


def test_declared_map_persists_both_legs_toward_the_transition_state(
    db_engine,
) -> None:
    payload = _payload()
    payload["atom_map"] = _complete_map(equivalent_map_count=3)

    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)

        atom_map = session.get(ReactionAtomMap, result["atom_map_id"])
        assert atom_map is not None
        assert atom_map.reaction_entry_id == result["reaction_entry_id"]
        assert (
            atom_map.transition_state_entry_id
            == result["transition_state_entry_id"]
        )
        assert atom_map.source is AtomMapSource.declared
        assert atom_map.equivalent_map_count == 3

        pairs = session.scalars(
            select(ReactionAtomMapPair).where(
                ReactionAtomMapPair.atom_map_id == atom_map.id
            )
        ).all()
        # Four methyl atoms + one hydrogen on the way in, five methane atoms
        # on the way out. Both legs run toward the saddle point; the
        # reactants -> products composition is not a stored row.
        assert len(pairs) == 10

        reactant_leg = {
            (pair.atom_index, pair.ts_atom_index)
            for pair in pairs
            if pair.side is ReactionRole.reactant
        }
        product_leg = {
            (pair.atom_index, pair.ts_atom_index)
            for pair in pairs
            if pair.side is ReactionRole.product
        }
        assert reactant_leg == {(1, 1), (2, 2), (3, 3), (4, 4), (1, 5)}
        assert product_leg == {(1, 1), (2, 2), (3, 3), (4, 4), (5, 5)}

        # Every pair names the geometry its index counts into, and the saddle
        # point geometry both legs land on.
        assert all(
            pair.transition_state_geometry_id
            == atom_map.transition_state_geometry_id
            for pair in pairs
        )
        assert {pair.element.strip() for pair in pairs} == {"C", "H"}
        # The two methyl hydrogens and the lone hydrogen are told apart by the
        # participant they belong to, not by their index.
        lone_h = next(
            pair for pair in pairs if pair.ts_atom_index == 5 and pair.side is ReactionRole.reactant
        )
        methyl_h = next(
            pair for pair in pairs if pair.ts_atom_index == 2 and pair.side is ReactionRole.reactant
        )
        assert lone_h.structure_participant_id != methyl_h.structure_participant_id


def test_declared_map_emits_no_absence_or_incompleteness_warning(
    db_engine,
) -> None:
    payload = _payload()
    payload["atom_map"] = _complete_map()
    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
    assert W_MISSING_REACTION_ATOM_MAP not in _codes(result)
    assert W_ATOM_MAP_PARTICIPANTS_INCOMPLETE not in _codes(result)
    assert W_ATOM_MAP_ATOMS_INCOMPLETE not in _codes(result)


def test_equivalent_map_count_absent_is_no_claim_not_one(db_engine) -> None:
    """Symmetry makes a valid map non-unique; silence is not a count of 1."""
    payload = _payload()
    payload["atom_map"] = _complete_map()
    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
        atom_map = session.get(ReactionAtomMap, result["atom_map_id"])
        assert atom_map.equivalent_map_count is None


# ---------------------------------------------------------------------------
# Provenance: inferred never reads back as declared
# ---------------------------------------------------------------------------


def test_inferred_map_is_stored_and_read_back_as_inferred(db_engine) -> None:
    payload = _payload()
    payload["atom_map"] = _complete_map(
        source="inferred", note="mapped by a maximum-common-substructure search"
    )
    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
        atom_map = session.get(ReactionAtomMap, result["atom_map_id"])
        assert atom_map.source is AtomMapSource.inferred
        assert atom_map.source is not AtomMapSource.declared
        assert "maximum-common-substructure" in atom_map.note


def test_inferred_map_without_a_note_is_refused(db_engine) -> None:
    """An inferred map whose origin is anonymous is one step from a lie."""
    payload = _payload()
    payload["atom_map"] = _complete_map(source="inferred")
    with pytest.raises(ValidationError, match="requires atom_map.note"):
        ComputedReactionUploadRequest(**payload)


def test_a_blank_note_does_not_name_an_algorithm(db_engine) -> None:
    """Whitespace is not the name of an inference engine.

    Without normalisation ``note='   '`` satisfies "an inferred map says what
    inferred it" while saying nothing, which is the anonymous inferred map
    ADR 0011 refuses, admitted through a loophole.
    """
    payload = _payload()
    payload["atom_map"] = _complete_map(source="inferred", note="   ")
    with pytest.raises(ValidationError, match="requires atom_map.note"):
        ComputedReactionUploadRequest(**payload)


def test_a_declared_map_note_is_trimmed(db_engine) -> None:
    payload = _payload()
    payload["atom_map"] = _complete_map(note="  followed the IRC by hand  ")
    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
        atom_map = session.get(ReactionAtomMap, result["atom_map_id"])
        assert atom_map.note == "followed the IRC by hand"


def test_a_blank_declared_note_becomes_no_note(db_engine) -> None:
    payload = _payload()
    payload["atom_map"] = _complete_map(note="  ")
    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
        atom_map = session.get(ReactionAtomMap, result["atom_map_id"])
        assert atom_map.note is None


def test_database_refuses_relabelling_an_inferred_map_as_declared(
    db_engine,
) -> None:
    """The laundering path, closed where a second write path cannot reopen it.

    ``ck_reaction_atom_map_inferred_requires_note`` constrains only the
    ``inferred`` member, so ``SET source='declared', note=NULL`` satisfies the
    check as written and commits: the row then reads back as a depositor's own
    statement about a mechanism an algorithm produced. There is no API route
    that does this — which is exactly why it is a trigger, per ADR 0010's
    argument about rules that hold only because today's code happens not to
    exercise them.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    payload = _payload()
    payload["atom_map"] = _complete_map(
        source="inferred", note="mapped by RXNMapper"
    )

    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
        atom_map_id = result["atom_map_id"]

        with pytest.raises(DBAPIError) as excinfo:
            session.execute(
                text(
                    "UPDATE reaction_atom_map "
                    "SET source = 'declared', note = NULL WHERE id = :id"
                ),
                {"id": atom_map_id},
            )
        assert "atom_map_source_is_immutable" in str(excinfo.value)


@pytest.mark.parametrize("blanked", ["'   '", "''", "NULL"])
def test_database_refuses_blanking_an_inferred_maps_note(
    db_engine, blanked: str
) -> None:
    """Freezing ``source`` is only half of "an inferred map names its origin".

    ``source`` can no longer leave ``inferred``, but ``note`` stays mutable so
    a typo can be corrected -- which means the same anonymity the wire schema
    refuses on the way in can still be reached by emptying the note afterwards.
    The check has to hold the trimmed value, not merely a non-NULL one.

    ``NULL`` is parametrised alongside the blank strings because the obvious
    tightening -- ``btrim(note) <> ''`` on its own -- silently *loses* that
    case: a CHECK rejects only on FALSE, and ``btrim(NULL) <> ''`` is NULL, so
    the anonymous map would start committing again. This asserts both halves
    of the constraint at once.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    payload = _payload()
    payload["atom_map"] = _complete_map(
        source="inferred", note="mapped by RXNMapper"
    )

    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)

        with pytest.raises(DBAPIError) as excinfo:
            session.execute(
                text(
                    f"UPDATE reaction_atom_map SET note = {blanked} "
                    "WHERE id = :id"
                ),
                {"id": result["atom_map_id"]},
            )
        assert "inferred_requires_note" in str(excinfo.value)


def test_an_inferred_map_may_still_have_its_note_corrected(db_engine) -> None:
    """Freezing the token must not freeze the record it labels.

    A depositor correcting the name of the algorithm is completing the record,
    not changing what it claims. Removing the note entirely is still refused,
    by the check constraint that was there already — with ``source`` frozen,
    the two together close the hole without making the row read-only.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    payload = _payload()
    payload["atom_map"] = _complete_map(source="inferred", note="RXNMapper")

    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
        atom_map_id = result["atom_map_id"]

        session.execute(
            text("UPDATE reaction_atom_map SET note = :note WHERE id = :id"),
            {"note": "RXNMapper v1.1.0", "id": atom_map_id},
        )
        session.expire_all()
        assert session.get(ReactionAtomMap, atom_map_id).note == "RXNMapper v1.1.0"

        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "UPDATE reaction_atom_map SET note = NULL WHERE id = :id"
                ),
                {"id": atom_map_id},
            )


# ---------------------------------------------------------------------------
# Absence warns
# ---------------------------------------------------------------------------


def test_reaction_without_a_map_is_accepted_and_warned(db_engine) -> None:
    with _isolated_session(db_engine) as session:
        result = _upload(session, _payload())

    assert result["reaction_entry_id"] is not None
    assert result["atom_map_id"] is None
    warning = next(
        w for w in result["warnings"] if w.code == W_MISSING_REACTION_ATOM_MAP
    )
    assert warning.field == "atom_map"
    # Loud enough that a depositor who *has* the mapping notices they are being
    # asked for it, and explicit that TCKDB will not invent one.
    assert "will not infer" in warning.message
    assert "intrinsic reaction coordinate" in warning.message


def test_reaction_without_a_transition_state_is_not_warned(db_engine) -> None:
    """A barrierless channel has nothing for the two legs to point at."""
    with _isolated_session(db_engine) as session:
        result = _upload(session, _payload(with_ts=False))
    assert W_MISSING_REACTION_ATOM_MAP not in _codes(result)


def test_map_without_a_transition_state_is_refused(db_engine) -> None:
    payload = _payload(with_ts=False)
    payload["atom_map"] = _complete_map()
    with pytest.raises(ValidationError, match="no transition state"):
        ComputedReactionUploadRequest(**payload)


# ---------------------------------------------------------------------------
# Incompleteness warns, it does not block
# ---------------------------------------------------------------------------


def test_map_omitting_a_participant_is_accepted_and_warned(db_engine) -> None:
    payload = _payload()
    atom_map = _complete_map()
    atom_map["participants"] = [
        p for p in atom_map["participants"] if p["species_key"] != "h"
    ]
    payload["atom_map"] = atom_map

    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
        assert result["atom_map_id"] is not None

    warning = next(
        w
        for w in result["warnings"]
        if w.code == W_ATOM_MAP_PARTICIPANTS_INCOMPLETE
    )
    assert "reactant 2" in warning.message


def test_map_omitting_atoms_of_a_mapped_participant_is_accepted_and_warned(
    db_engine,
) -> None:
    payload = _payload()
    atom_map = _complete_map()
    for participant in atom_map["participants"]:
        if participant["species_key"] == "ch4":
            del participant["atom_to_ts"][5]
    # Drop the matching reactant claim too, so the two legs still agree and the
    # only thing wrong with the map is that it is partial.
    for participant in atom_map["participants"]:
        if participant["species_key"] == "h":
            atom_map["participants"].remove(participant)
            break
    payload["atom_map"] = atom_map

    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
        assert result["atom_map_id"] is not None

    warning = next(
        w for w in result["warnings"] if w.code == W_ATOM_MAP_ATOMS_INCOMPLETE
    )
    assert "partial" in warning.message


# ---------------------------------------------------------------------------
# Contradiction blocks: one test per class named in ADR 0011
# ---------------------------------------------------------------------------


def test_element_changing_across_the_map_is_refused(db_engine) -> None:
    """Carbon does not become nitrogen."""
    payload = _payload()
    payload["species"][0] = _species("ch3", "[NH3]", 1, _XYZ_NH3)
    payload["species"][0]["species_entry"]["multiplicity"] = 1
    atom_map = _complete_map()
    payload["atom_map"] = atom_map

    with pytest.raises(ValidationError) as excinfo:
        ComputedReactionUploadRequest(**payload)
    message = str(excinfo.value)
    assert "which is N" in message
    assert "which is C" in message
    assert "An element does not change across a reaction" in message


def test_transition_state_atom_claimed_twice_is_refused(db_engine) -> None:
    """One saddle-point atom is one atom."""
    payload = _payload()
    atom_map = _complete_map()
    for participant in atom_map["participants"]:
        if participant["species_key"] == "h":
            # The lone hydrogen claims a saddle-point atom methyl already has.
            participant["atom_to_ts"] = {1: 2}
    payload["atom_map"] = atom_map

    with pytest.raises(ValidationError) as excinfo:
        ComputedReactionUploadRequest(**payload)
    message = str(excinfo.value)
    assert "claims transition-state atom 2 twice" in message
    assert "reactant leg" in message


def test_saddle_point_atom_claimed_by_neither_leg_is_refused(db_engine) -> None:
    """A balanced, fully mapped reaction cannot leave a saddle-point atom over.

    Both legs are complete over every declared participant and the reaction
    conserves every element, so there is no missing species to explain an atom
    of the saddle point that comes from nothing and becomes nothing.
    """
    payload = _payload()
    payload["transition_state"]["geometry"]["xyz_text"] = (
        "6\nTS with a spare atom\n"
        "C  0.000  0.000  0.000\n"
        "H  0.629  0.629  0.629\n"
        "H -0.629 -0.629  0.629\n"
        "H -0.629  0.629 -0.629\n"
        "H  0.000  0.000  1.400\n"
        "H  0.000  0.000  3.000"
    )
    # Every participant fully mapped, both legs agreeing, atom 6 left over.
    payload["atom_map"] = _complete_map()

    with pytest.raises(ValidationError) as excinfo:
        ComputedReactionUploadRequest(**payload)
    message = str(excinfo.value)
    assert "atom(s) [6] are claimed by neither leg" in message
    assert "No species is missing" in message


def test_unbalanced_reaction_is_not_refused_for_an_unaccounted_atom() -> None:
    """ADR 0011's hedge: "where no species is missing".

    Here a co-product genuinely is absent — the declared participants do not
    conserve hydrogen — so the same leftover saddle-point atom that blocks a
    balanced reaction is incompleteness rather than contradiction, and the map
    is accepted.

    Constructed rather than uploaded because the computed-reaction workflow
    refuses an unbalanced reaction outright, upstream of the atom map, via
    ``validate_reaction_elemental_balance``. The hedge therefore never fires on
    *this* deposit path; it is checked here because the rule lives in the
    path-agnostic schema fragment, and a future path that does not enforce
    balance must not have a balance-shaped refusal appear from nowhere.
    """
    payload = _payload()
    payload["product_keys"] = ["ch3"]
    atom_map = _complete_map()
    atom_map["participants"] = [
        p for p in atom_map["participants"] if p["species_key"] != "ch4"
    ] + [
        {
            "side": "product",
            "species_key": "ch3",
            "participant_index": 1,
            "geometry_key": "ch3-geom",
            "atom_to_ts": {1: 1, 2: 2, 3: 3, 4: 4},
        }
    ]
    payload["atom_map"] = atom_map

    # Saddle-point atom 5 is claimed by the reactant leg and by no product.
    request = ComputedReactionUploadRequest(**payload)
    assert request.atom_map is not None


def test_legs_covering_different_atoms_of_a_balanced_reaction_is_refused(
    db_engine,
) -> None:
    """The named case from ADR 0011, with nothing missing to explain it.

    Both legs are complete over every declared participant and the reaction
    conserves every element, so a saddle-point atom claimed by one leg and not
    the other is unaccounted for rather than merely unstated.
    """
    payload = _payload()
    # Six-atom saddle point: the reaction's five atoms plus a spare hydrogen
    # the product leg will claim and the reactant leg cannot.
    payload["transition_state"]["geometry"]["xyz_text"] = (
        "6\nTS with a spare atom\n"
        "C  0.000  0.000  0.000\n"
        "H  0.629  0.629  0.629\n"
        "H -0.629 -0.629  0.629\n"
        "H -0.629  0.629 -0.629\n"
        "H  0.000  0.000  1.400\n"
        "H  0.000  0.000  3.000"
    )
    atom_map = _complete_map()
    for participant in atom_map["participants"]:
        if participant["species_key"] == "ch4":
            # Methane's fifth hydrogen lands on the spare saddle-point atom.
            participant["atom_to_ts"] = {1: 1, 2: 2, 3: 3, 4: 4, 5: 6}
    payload["atom_map"] = atom_map

    with pytest.raises(ValidationError) as excinfo:
        ComputedReactionUploadRequest(**payload)
    message = str(excinfo.value)
    assert "do not cover the same" in message
    assert "No species is missing" in message


def test_index_outside_the_named_geometry_is_refused(db_engine) -> None:
    payload = _payload()
    atom_map = _complete_map()
    for participant in atom_map["participants"]:
        if participant["species_key"] == "ch3":
            # Methyl has four atoms.
            participant["atom_to_ts"][9] = 5
    payload["atom_map"] = atom_map

    with pytest.raises(ValidationError) as excinfo:
        ComputedReactionUploadRequest(**payload)
    assert "geometry 'ch3-geom' has 4 atoms" in str(excinfo.value)


def test_transition_state_index_outside_the_saddle_point_geometry_is_refused(
    db_engine,
) -> None:
    payload = _payload()
    atom_map = _complete_map()
    for participant in atom_map["participants"]:
        if participant["species_key"] == "h":
            participant["atom_to_ts"] = {1: 99}
    payload["atom_map"] = atom_map

    with pytest.raises(ValidationError) as excinfo:
        ComputedReactionUploadRequest(**payload)
    assert "geometry 'ts-geom' has 5 atoms" in str(excinfo.value)


def test_map_written_against_a_geometry_that_is_not_the_saddle_point_is_refused(
    db_engine,
) -> None:
    """Atom indices are geometry-relative; the wrong geometry is wrong atoms."""
    payload = _payload()
    payload["atom_map"] = _complete_map(ts_geometry_key="ch4-geom")
    with pytest.raises(ValidationError) as excinfo:
        ComputedReactionUploadRequest(**payload)
    assert "is not the transition state's geometry" in str(excinfo.value)


def test_participant_mapped_against_another_species_geometry_is_refused(
    db_engine,
) -> None:
    payload = _payload()
    atom_map = _complete_map()
    for participant in atom_map["participants"]:
        if participant["species_key"] == "ch3":
            participant["geometry_key"] = "ch4-geom"
    payload["atom_map"] = atom_map

    with pytest.raises(ValidationError) as excinfo:
        ComputedReactionUploadRequest(**payload)
    assert "does not belong to species 'ch3'" in str(excinfo.value)


def test_map_naming_a_participant_the_reaction_does_not_declare_is_refused(
    db_engine,
) -> None:
    payload = _payload()
    atom_map = _complete_map()
    atom_map["participants"].append(
        {
            "side": "product",
            "species_key": "ch4",
            "participant_index": 2,
            "geometry_key": "ch4-geom",
            "atom_to_ts": {1: 1},
        }
    )
    payload["atom_map"] = atom_map

    with pytest.raises(ValidationError) as excinfo:
        ComputedReactionUploadRequest(**payload)
    assert "which this reaction does not declare" in str(excinfo.value)


def test_same_participant_mapped_twice_is_refused(db_engine) -> None:
    payload = _payload()
    atom_map = _complete_map()
    atom_map["participants"].append(
        copy.deepcopy(
            next(p for p in atom_map["participants"] if p["species_key"] == "ch4")
        )
    )
    payload["atom_map"] = atom_map

    with pytest.raises(ValidationError) as excinfo:
        ComputedReactionUploadRequest(**payload)
    assert "more than once" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Capitalisation is not chemistry
# ---------------------------------------------------------------------------

#: Methyl and the saddle point, with the same atoms written in different case.
#: ``geometry_atom.element`` stores the depositor's symbol verbatim, so this is
#: what a reactant optimised in one program and a saddle point located in
#: another actually looks like once both are in the database.
_XYZ_CH3_LOWER = (
    "4\nmethyl, elements whispered\n"
    "c  0.000  0.000  0.000\n"
    "h  1.080  0.000  0.000\n"
    "h -0.540  0.935  0.000\n"
    "h -0.540 -0.935  0.000"
)


def test_map_across_geometries_that_disagree_only_about_case_persists(
    db_engine,
) -> None:
    """``c`` and ``C`` are one element, and a map across them is one map.

    Both ends of a pair quote a *different* geometry, and each geometry stores
    the element symbol its own XYZ wrote. If the two ends are compared raw, or
    forced through one shared column, then a depositor whose reactant came out
    of a program that writes ``c`` and whose saddle point came out of one that
    writes ``C`` cannot record a correct map at all — refused for a capital
    letter, which is the failure ADR 0008 puts out of bounds.

    The map here is the same map as everywhere else in this module; only the
    methyl geometry's capitalisation differs.
    """
    payload = _payload()
    payload["species"][0] = _species("ch3", "[CH3]", 2, _XYZ_CH3_LOWER)
    payload["atom_map"] = _complete_map()

    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)

        atom_map = session.get(ReactionAtomMap, result["atom_map_id"])
        assert atom_map is not None

        pairs = session.scalars(
            select(ReactionAtomMapPair).where(
                ReactionAtomMapPair.atom_map_id == atom_map.id
            )
        ).all()
        assert len(pairs) == 10

        # Each end kept the spelling of the geometry it points at, which is
        # what lets both composite foreign keys into ``geometry_atom`` resolve.
        methyl_carbon = next(
            pair
            for pair in pairs
            if pair.side is ReactionRole.reactant
            and pair.geometry_id != atom_map.transition_state_geometry_id
            and pair.atom_index == 1
            and pair.ts_atom_index == 1
        )
        assert methyl_carbon.element.strip() == "c"
        assert methyl_carbon.ts_element.strip() == "C"


def test_an_element_really_changing_across_the_map_is_still_refused(
    db_engine,
) -> None:
    """Normalising case must not cost the rule it is normalising for.

    Carbon becoming nitrogen is a record that cannot be what it says it is, and
    it blocks — the map is refused at the schema boundary before anything is
    written.
    """
    payload = _payload()
    payload["species"][0] = _species("nh3", "N", 1, _XYZ_NH3)
    payload["reactant_keys"] = ["nh3", "h"]
    atom_map = _complete_map()
    for participant in atom_map["participants"]:
        if participant["species_key"] == "ch3":
            participant["species_key"] = "nh3"
            participant["geometry_key"] = "nh3-geom"
    payload["atom_map"] = atom_map

    with pytest.raises(ValidationError) as excinfo:
        ComputedReactionUploadRequest(**payload)
    assert "An element does not change across a reaction" in str(excinfo.value)


# ---------------------------------------------------------------------------
# The database refuses these too, not only the API
# ---------------------------------------------------------------------------


def test_database_refuses_a_pair_whose_two_ends_are_really_different_elements(
    db_engine,
) -> None:
    """The check constraint owns the element rule; prove it is not dead.

    Both foreign keys can resolve and the pair still be a lie: point the
    saddle-point end at a real hydrogen atom of the saddle-point geometry and
    give it that hydrogen's own element, and only
    ``ck_reaction_atom_map_pair_element_matches`` stands between the write and
    a carbon that became a hydrogen. Going around the service layer entirely,
    which is the write path this constraint exists for.
    """
    from sqlalchemy.exc import IntegrityError

    payload = _payload()
    payload["atom_map"] = _complete_map()

    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
        pair = session.scalars(
            select(ReactionAtomMapPair)
            .where(ReactionAtomMapPair.atom_map_id == result["atom_map_id"])
            .where(ReactionAtomMapPair.element == "C ")
        ).first()
        assert pair is not None
        pair.ts_atom_index = 5
        pair.ts_element = "H "
        with pytest.raises(IntegrityError) as excinfo:
            session.flush()
    assert "element_matches" in str(excinfo.value)


def test_database_refuses_a_pair_whose_two_ends_are_different_elements(
    db_engine,
) -> None:
    """The element rule is a constraint, not just a validator on one path.

    ADR 0010's lesson: a rule that lives only in one Pydantic validator on one
    wired write path is a rule a second write path does not have.
    """
    from sqlalchemy.exc import IntegrityError

    payload = _payload()
    payload["atom_map"] = _complete_map()

    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
        pair = session.scalars(
            select(ReactionAtomMapPair)
            .where(ReactionAtomMapPair.atom_map_id == result["atom_map_id"])
            .where(ReactionAtomMapPair.element == "C ")
        ).first()
        assert pair is not None
        # Point the carbon's saddle-point end at a hydrogen, going around the
        # service layer entirely.
        pair.ts_atom_index = 5
        with pytest.raises(IntegrityError):
            session.flush()


def test_database_refuses_two_reactant_atoms_claiming_one_saddle_point_atom(
    db_engine,
) -> None:
    from sqlalchemy.exc import IntegrityError

    payload = _payload()
    payload["atom_map"] = _complete_map()

    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
        session.add(
            ReactionAtomMapPair(
                atom_map_id=result["atom_map_id"],
                side=ReactionRole.reactant,
                structure_participant_id=session.scalars(
                    select(ReactionAtomMapPair.structure_participant_id).where(
                        ReactionAtomMapPair.atom_map_id == result["atom_map_id"]
                    )
                ).first(),
                geometry_id=session.scalars(
                    select(ReactionAtomMapPair.geometry_id).where(
                        ReactionAtomMapPair.atom_map_id == result["atom_map_id"]
                    )
                ).first(),
                atom_index=2,
                transition_state_geometry_id=session.get(
                    ReactionAtomMap, result["atom_map_id"]
                ).transition_state_geometry_id,
                ts_atom_index=5,
                element="H ",
            )
        )
        with pytest.raises(IntegrityError):
            session.flush()
