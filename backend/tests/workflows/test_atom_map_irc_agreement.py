"""Two surfaces, one saddle point: the atom map and the IRC partition must agree.

``transition_state_validation_evidence``'s participant mappings *partition* the
saddle-point atoms among the declared participants — "TS atoms 1,2,3 belong to
reactant 1". A ``reaction_atom_map`` says which reactant atom each of those
saddle-point atoms **is**. The schema's own words are that the map is "the
refinement of that partition into a bijection", so the two are one claim at two
resolutions and cannot disagree about which atoms a participant is made of.

They could, though. A deposit was able to carry both, stating two incompatible
partitions of the same saddle point, and each passed on its own because nothing
compared them. The reaction here is ``CH3 + H -> CH4``, the same bundle the rest
of the computed-reaction workflow tests use, and the contradiction it is built
around is deliberately invisible to every check that came before: the atom map
says the methyl is saddle-point atoms 1-4 and the incoming hydrogen is atom 5,
while the IRC mapping says the methyl is 1,2,3,5 and the incoming hydrogen is
atom 4. Both are well-formed. Both partition the five atoms exactly once. Both
give the methyl one carbon and three hydrogens, so the element check added for
the IRC side sees nothing wrong. They still cannot both be true, and which
hydrogen is the transferring one is the entire content of the reaction.

The false-positive tests are as load-bearing as the refusal ones. ADR 0008 lets
a check block only where no correct calculation could produce the record it
refuses, and this is the rule most likely to get that wrong: a partial atom map,
an absent IRC mapping, failed evidence, a barrierless channel and a reaction
releasing a free electron are all *absences*, and absence is not disagreement.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.fragments.reaction_atom_map import (
    W_ATOM_MAP_ATOMS_UNACCOUNTED_FOR,
    W_ATOM_MAP_INDICES_NOT_GEOMETRY_RELATIVE,
    W_ATOM_MAP_PARTICIPANT_NOT_DECLARED,
    ReactionAtomMapParticipantIn,
)

from app.db.models.app_user import AppUser
from app.db.models.reaction_atom_map import ReactionAtomMap
from app.db.models.transition_state import TransitionStateValidationEvidence
from app.schemas.workflows.computed_reaction_upload import (
    ComputedReactionUploadRequest,
)
from app.services.reaction_atom_map import (
    W_ATOM_MAP_ATOMS_INCOMPLETE,
    W_ATOM_MAP_CONTRADICTS_IRC_MAPPING,
    W_ATOM_MAP_PARTICIPANTS_INCOMPLETE,
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
#: Saddle point for ``CH3 + H -> CH4``: carbon, three methyl hydrogens, then
#: the incoming hydrogen as atom 5. Every atom after the carbon is a hydrogen,
#: which is exactly why a partition can be wrong without being wrong about
#: elements.
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

_USER_ID = 50_711


@contextmanager
def _isolated_session(db_engine) -> Iterator[Session]:
    """Roll back everything: the workflow writes a large graph per call."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        session.add(AppUser(id=_USER_ID, username="atom_map_irc_agreement_tests"))
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


def _payload() -> dict:
    """``CH3 + H -> CH4`` with its saddle point and an IRC calculation."""
    return {
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
                },
                {
                    "key": "ts-irc",
                    "type": "irc",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                },
            ],
            "label": "ch3+h->ch4 TS",
        },
    }


#: The map and the partition that agree: methyl is saddle-point atoms 1-4, the
#: incoming hydrogen is atom 5, methane is all five.
_MAP_METHYL_IS_1234 = [
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
]

_IRC_METHYL_IS_1234 = {
    "reactant:1": [1, 2, 3, 4],
    "reactant:2": [5],
}
#: The same reaction, described so that saddle-point atoms 4 and 5 swap
#: molecules. Still a partition, still C+3H for the methyl, still passes the
#: element check — and it says the other hydrogen is the one being transferred.
_IRC_METHYL_IS_1235 = {
    "reactant:1": [1, 2, 3, 5],
    "reactant:2": [4],
}
_IRC_PRODUCTS = {"product:1": [1, 2, 3, 4, 5]}


def _with_map(payload: dict, participants: list[dict]) -> dict:
    payload["atom_map"] = {
        "source": "declared",
        "ts_geometry_key": "ts-geom",
        "participants": participants,
    }
    return payload


def _with_evidence(
    payload: dict,
    *,
    reactants: dict | None,
    products: dict | None,
    passed: bool = True,
) -> dict:
    payload["transition_state"]["validation_evidence"] = [
        {
            "kind": "irc",
            "passed": passed,
            "rationale": "IRC descends to CH3 + H one way and CH4 the other.",
            "source_calculation_key": "ts-irc",
            "reactant_participant_mapping": reactants,
            "product_participant_mapping": products,
        }
    ]
    return payload


def _upload(session: Session, payload: dict) -> dict:
    return persist_computed_reaction_upload(
        session,
        ComputedReactionUploadRequest(**payload),
        created_by=_USER_ID,
    )


def _codes(result: dict) -> set[str]:
    return {warning.code for warning in result["warnings"]}


# ---------------------------------------------------------------------------
# The proof that matters: contradiction is refused, agreement is not
# ---------------------------------------------------------------------------


def test_two_contradictory_partitions_of_one_saddle_point_are_refused(
    db_engine,
) -> None:
    """The gap, closed. Neither surface is wrong on its own; together they are."""

    payload = _with_evidence(
        _with_map(_payload(), _MAP_METHYL_IS_1234),
        reactants=_IRC_METHYL_IS_1235,
        products=_IRC_PRODUCTS,
    )

    with _isolated_session(db_engine) as session:
        with pytest.raises(ValueError) as excinfo:
            _upload(session, payload)

    message = str(excinfo.value)
    assert W_ATOM_MAP_CONTRADICTS_IRC_MAPPING in message
    # The message has to name the atom the two surfaces fight over and both
    # participants that claim it, or a depositor cannot act on it.
    assert "[4]" in message
    assert "reactant 1" in message
    assert "reactant 2" in message


def test_two_agreeing_partitions_of_one_saddle_point_still_deposit(
    db_engine,
) -> None:
    """The other half of the proof: the check refuses only the contradiction."""

    payload = _with_evidence(
        _with_map(_payload(), _MAP_METHYL_IS_1234),
        reactants=_IRC_METHYL_IS_1234,
        products=_IRC_PRODUCTS,
    )

    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)

        assert result["atom_map_id"] is not None
        atom_map = session.get(ReactionAtomMap, result["atom_map_id"])
        assert atom_map is not None
        assert (
            atom_map.transition_state_entry_id
            == result["transition_state_entry_id"]
        )
    assert W_ATOM_MAP_CONTRADICTS_IRC_MAPPING not in _codes(result)


def test_the_product_leg_is_compared_too(db_engine) -> None:
    """Both legs run toward the saddle point, so both are checked.

    Written as the dissociation ``CH4 -> CH3 + H`` rather than the recombination
    above, because a side carrying one participant has only one possible
    partition and cannot disagree with anything. Here the *products* are the two
    molecules, and the same swap of saddle-point atoms 4 and 5 is a
    contradiction on the leg the previous tests exercised from the other end.
    """

    payload = _payload()
    payload["reactant_keys"] = ["ch4"]
    payload["product_keys"] = ["ch3", "h"]
    _with_map(
        payload,
        [
            {
                "side": "product",
                "species_key": "ch3",
                "participant_index": 1,
                "geometry_key": "ch3-geom",
                "atom_to_ts": {1: 1, 2: 2, 3: 3, 4: 4},
            },
            {
                "side": "product",
                "species_key": "h",
                "participant_index": 2,
                "geometry_key": "h-geom",
                "atom_to_ts": {1: 5},
            },
            {
                "side": "reactant",
                "species_key": "ch4",
                "participant_index": 1,
                "geometry_key": "ch4-geom",
                "atom_to_ts": {1: 1, 2: 2, 3: 3, 4: 4, 5: 5},
            },
        ],
    )
    _with_evidence(
        payload,
        reactants={"reactant:1": [1, 2, 3, 4, 5]},
        products={"product:1": [1, 2, 3, 5], "product:2": [4]},
    )

    with _isolated_session(db_engine) as session:
        with pytest.raises(ValueError) as excinfo:
            _upload(session, payload)

    message = str(excinfo.value)
    assert W_ATOM_MAP_CONTRADICTS_IRC_MAPPING in message
    assert "product 1" in message
    assert "product 2" in message


# ---------------------------------------------------------------------------
# Absence is not disagreement
# ---------------------------------------------------------------------------


def test_a_map_with_no_irc_mapping_at_all_is_not_compared(db_engine) -> None:
    """Evidence without participant mappings says nothing to contradict.

    The mappings are optional on every path, and this is the shape a reaction
    releasing a free electron is *forced* into — see the electron test below.
    """

    payload = _with_evidence(
        _with_map(_payload(), _MAP_METHYL_IS_1234),
        reactants=None,
        products=None,
    )

    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
        assert result["atom_map_id"] is not None
    assert W_ATOM_MAP_CONTRADICTS_IRC_MAPPING not in _codes(result)


def test_an_irc_mapping_with_no_map_is_not_compared(db_engine) -> None:
    """The reverse absence. Most deposits with IRC evidence carry no map."""

    payload = _with_evidence(
        _payload(),
        reactants=_IRC_METHYL_IS_1235,
        products=_IRC_PRODUCTS,
    )

    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
        assert result["atom_map_id"] is None
    assert W_ATOM_MAP_CONTRADICTS_IRC_MAPPING not in _codes(result)


def test_failed_irc_evidence_is_not_compared(db_engine) -> None:
    """``passed=False`` is not a claim, so nothing contradicts it.

    Matching ``validate_ts_evidence_participant_composition``: a failed record
    is stored as the negative result it is, and ``validate_ts_evidence_set``
    never held it to covering every atom, so comparing it would refuse deposits
    whose mappings were never asserted as evidence in the first place.
    """

    payload = _with_evidence(
        _with_map(_payload(), _MAP_METHYL_IS_1234),
        reactants=_IRC_METHYL_IS_1235,
        products=_IRC_PRODUCTS,
        passed=False,
    )

    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
        assert result["atom_map_id"] is not None
    assert W_ATOM_MAP_CONTRADICTS_IRC_MAPPING not in _codes(result)


def test_a_barrierless_channel_has_neither_surface(db_engine) -> None:
    """No saddle point, so no map to compare and no partition to compare it to."""

    payload = _payload()
    payload.pop("transition_state")

    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
        assert result["atom_map_id"] is None
    assert W_ATOM_MAP_CONTRADICTS_IRC_MAPPING not in _codes(result)


# ---------------------------------------------------------------------------
# A partial map is compared only over what it claims
# ---------------------------------------------------------------------------


def test_a_map_omitting_a_participant_is_not_a_contradiction(db_engine) -> None:
    """The coverage guard's ``participants_incomplete`` case, under this rule.

    The map covers the methyl and methane and says nothing about the incoming
    hydrogen. A complete IRC partition names all three. Absence is not
    disagreement, so this deposits — with the incompleteness warning it already
    earned.
    """

    payload = _with_evidence(
        _with_map(
            _payload(),
            [p for p in _MAP_METHYL_IS_1234 if p["species_key"] != "h"],
        ),
        reactants=_IRC_METHYL_IS_1234,
        products=_IRC_PRODUCTS,
    )

    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
        assert result["atom_map_id"] is not None
    assert W_ATOM_MAP_CONTRADICTS_IRC_MAPPING not in _codes(result)
    assert W_ATOM_MAP_PARTICIPANTS_INCOMPLETE in _codes(result)


def test_a_participant_mapping_only_some_of_its_atoms_is_not_a_contradiction(
    db_engine,
) -> None:
    """The ``atoms_incomplete`` case. Three of the methyl's four atoms, agreeing."""

    partial = [dict(p) for p in _MAP_METHYL_IS_1234]
    partial[0]["atom_to_ts"] = {1: 1, 2: 2, 3: 3}

    payload = _with_evidence(
        _with_map(_payload(), partial),
        reactants=_IRC_METHYL_IS_1234,
        products=_IRC_PRODUCTS,
    )

    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
        assert result["atom_map_id"] is not None
    assert W_ATOM_MAP_CONTRADICTS_IRC_MAPPING not in _codes(result)


def test_a_partial_map_that_contradicts_what_it_does_claim_still_blocks(
    db_engine,
) -> None:
    """Partiality is not immunity.

    The map says nothing about three of the methyl's atoms, but it does say
    that methyl atom 4 is saddle-point atom 5 — and the partition says
    saddle-point atom 5 is the *other* reactant. Comparing only what is claimed
    is what makes the earlier tests pass; it is not a way to smuggle a
    contradiction through.
    """

    partial = [dict(p) for p in _MAP_METHYL_IS_1234]
    partial[0]["atom_to_ts"] = {4: 5}
    partial[1]["atom_to_ts"] = {1: 4}

    payload = _with_evidence(
        _with_map(_payload(), partial),
        reactants=_IRC_METHYL_IS_1234,
        products=_IRC_PRODUCTS,
    )

    with _isolated_session(db_engine) as session:
        with pytest.raises(ValueError) as excinfo:
            _upload(session, payload)

    assert W_ATOM_MAP_CONTRADICTS_IRC_MAPPING in str(excinfo.value)


# ---------------------------------------------------------------------------
# Order independence
# ---------------------------------------------------------------------------


def test_the_verdict_does_not_depend_on_which_surface_is_written_first(
    db_engine,
) -> None:
    """The check reads both surfaces from the database, so either order works.

    ``persist_computed_reaction_upload`` writes the evidence first and the map
    second, so today only the atom-map seam can see both. That is an incidental
    ordering — nothing in the schema requires it — and the check is called from
    both seams so that reversing it cannot silently switch the rule off. Here
    the seams run in the reverse order against the same rows, and the
    contradiction is still refused.
    """

    from app.services.reaction_atom_map import (
        validate_atom_map_agrees_with_irc_evidence,
    )

    payload = _with_map(_payload(), _MAP_METHYL_IS_1234)

    with _isolated_session(db_engine) as session:
        # Deposit the map with no evidence at all, which cannot block.
        result = _upload(session, payload)
        assert result["atom_map_id"] is not None

        # Now write the contradicting evidence against the saddle point that
        # already carries the map — the order the workflow never produces.
        from app.db.models.transition_state import TransitionStateValidationEvidence

        session.add(
            TransitionStateValidationEvidence(
                transition_state_entry_id=result["transition_state_entry_id"],
                kind="irc",
                passed=True,
                rationale="IRC, deposited after the map.",
                reconstruction_calculation_id=_irc_calculation_id(session, result),
                reactant_participant_mapping=_IRC_METHYL_IS_1235,
                product_participant_mapping=_IRC_PRODUCTS,
                # Both surfaces index the same saddle point, so the evidence
                # names the geometry the map already named. Comparing two sets
                # of indices only means something once both say what they
                # counted in, which is the whole reason this column exists.
                transition_state_geometry_id=session.get(
                    ReactionAtomMap, result["atom_map_id"]
                ).transition_state_geometry_id,
            )
        )
        session.flush()

        with pytest.raises(ValueError) as excinfo:
            validate_atom_map_agrees_with_irc_evidence(
                session,
                reaction_entry_id=result["reaction_entry_id"],
                transition_state_entry_id=result["transition_state_entry_id"],
            )

    assert W_ATOM_MAP_CONTRADICTS_IRC_MAPPING in str(excinfo.value)


# ---------------------------------------------------------------------------
# Interchangeable participants: which identical molecule is "reactant 1"
# ---------------------------------------------------------------------------

_XYZ_C2H6 = (
    "8\nethane\n"
    "C  0.000  0.000  0.765\n"
    "H  1.019  0.000  1.163\n"
    "H -0.510  0.883  1.163\n"
    "H -0.510 -0.883  1.163\n"
    "C  0.000  0.000 -0.765\n"
    "H  1.019  0.000 -1.163\n"
    "H -0.510  0.883 -1.163\n"
    "H -0.510 -0.883 -1.163"
)
_XYZ_TS_C2H6 = (
    "8\nTS for CH3 + CH3 -> C2H6\n"
    "C  0.000  0.000  1.100\n"
    "H  1.030  0.000  1.450\n"
    "H -0.515  0.892  1.450\n"
    "H -0.515 -0.892  1.450\n"
    "C  0.000  0.000 -1.100\n"
    "H  1.030  0.000 -1.450\n"
    "H -0.515  0.892 -1.450\n"
    "H -0.515 -0.892 -1.450"
)


def _recombination_payload() -> dict:
    """``CH3 + CH3 -> C2H6``: the same species twice on one side.

    The shape ``reaction_atom_map_pair`` exists to keep straight — "the same
    species may appear twice on one side and the two copies map to different
    transition-state atoms".
    """
    payload = {
        "species": [
            _species("ch3", "[CH3]", 2, _XYZ_CH3),
            _species("c2h6", "CC", 1, _XYZ_C2H6),
        ],
        "reversible": True,
        "reactant_keys": ["ch3", "ch3"],
        "product_keys": ["c2h6"],
        "transition_state": {
            "charge": 0,
            "multiplicity": 1,
            "geometry": {"key": "ts-geom", "xyz_text": _XYZ_TS_C2H6},
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
                    "freq_imag_freq_cm1": -420.0,
                },
                {
                    "key": "ts-irc",
                    "type": "irc",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                },
            ],
            "label": "ch3+ch3->c2h6 TS",
        },
    }
    _with_map(
        payload,
        [
            {
                "side": "reactant",
                "species_key": "ch3",
                "participant_index": 1,
                "geometry_key": "ch3-geom",
                "atom_to_ts": {1: 1, 2: 2, 3: 3, 4: 4},
            },
            {
                "side": "reactant",
                "species_key": "ch3",
                "participant_index": 2,
                "geometry_key": "ch3-geom",
                "atom_to_ts": {1: 5, 2: 6, 3: 7, 4: 8},
            },
            {
                "side": "product",
                "species_key": "c2h6",
                "participant_index": 1,
                "geometry_key": "c2h6-geom",
                "atom_to_ts": {1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8},
            },
        ],
    )
    return payload


def test_relabelling_two_identical_participants_is_not_a_contradiction(
    db_engine,
) -> None:
    """Which methyl a depositor called "reactant 1" is arbitrary in each surface.

    The atom map says the first methyl is saddle-point atoms 1-4; the IRC
    partition says it is 5-8, and the second methyl the other four. The two
    molecules are the same species entry, so the surfaces describe the identical
    physical partition under a different arbitrary labelling. Refusing that
    would refuse correct science over bookkeeping, which is what ADR 0008 puts
    out of bounds for a blocking check.
    """

    payload = _with_evidence(
        _recombination_payload(),
        reactants={"reactant:1": [5, 6, 7, 8], "reactant:2": [1, 2, 3, 4]},
        products={"product:1": [1, 2, 3, 4, 5, 6, 7, 8]},
    )

    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
        assert result["atom_map_id"] is not None
    assert W_ATOM_MAP_CONTRADICTS_IRC_MAPPING not in _codes(result)


def test_identical_participants_do_not_buy_a_blanket_exemption(db_engine) -> None:
    """The partition is still physical even when the labelling is not.

    Here one hydrogen is swapped between the two methyls, so the IRC says atoms
    1,2,3,6 form one molecule while the map says 1,2,3,4 do. Both surfaces still
    give each methyl a carbon and three hydrogens, so the element check sees
    nothing; and no relabelling of the two participants repairs it, because the
    two descriptions genuinely disagree about which atoms are bonded together.
    """

    payload = _with_evidence(
        _recombination_payload(),
        reactants={"reactant:1": [1, 2, 3, 6], "reactant:2": [4, 5, 7, 8]},
        products={"product:1": [1, 2, 3, 4, 5, 6, 7, 8]},
    )

    with _isolated_session(db_engine) as session:
        with pytest.raises(ValueError) as excinfo:
            _upload(session, payload)

    assert W_ATOM_MAP_CONTRADICTS_IRC_MAPPING in str(excinfo.value)


# ---------------------------------------------------------------------------
# A reaction releasing a free electron
# ---------------------------------------------------------------------------

_XYZ_OH = "2\nhydroxide\nO  0.000  0.000  0.000\nH  0.964  0.000  0.000"
_XYZ_H2O = (
    "3\nwater\n"
    "O  0.000  0.000  0.000\n"
    "H  0.958  0.000  0.000\n"
    "H -0.240  0.927  0.000"
)
_XYZ_TS_AD = (
    "3\nTS for OH- + H -> H2O + e-\n"
    "O  0.000  0.000  0.000\n"
    "H  0.960  0.000  0.000\n"
    "H -0.400  1.400  0.000"
)


def _charged_species(
    key: str, smiles: str, charge: int, multiplicity: int, xyz: str
) -> dict:
    species = _species(key, smiles, multiplicity, xyz)
    species["species_entry"]["charge"] = charge
    return species


def _electron_payload() -> dict:
    """``OH- + H -> H2O + e-`` (associative detachment) with its saddle point.

    Three saddle-point atoms, four declared participants, one of which has no
    atoms at all. The electron is what makes this reaction worth a fixture: it
    is the only participant either surface can describe as being made of
    nothing.
    """

    return {
        "species": [
            _charged_species("oh", "[OH-]", -1, 1, _XYZ_OH),
            _species("h", "[H]", 2, _XYZ_H),
            _species("h2o", "O", 1, _XYZ_H2O),
            {
                "key": "electron",
                "species_entry": {
                    "molecule_kind": "electron",
                    "smiles": "[e-]",
                    "charge": -1,
                    "multiplicity": 2,
                },
            },
        ],
        "reversible": False,
        "reactant_keys": ["oh", "h"],
        "product_keys": ["h2o", "electron"],
        "transition_state": {
            "charge": -1,
            "multiplicity": 2,
            "geometry": {"key": "ts-geom", "xyz_text": _XYZ_TS_AD},
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
                    "freq_imag_freq_cm1": -900.0,
                },
                {
                    "key": "ts-irc",
                    "type": "irc",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                },
            ],
            "label": "associative detachment TS",
        },
    }


#: The map of the atom-bearing participants. Complete over every atom of every
#: molecule, and silent about the electron.
_MAP_ELECTRON_OMITTED = [
    {
        "side": "reactant",
        "species_key": "oh",
        "participant_index": 1,
        "geometry_key": "oh-geom",
        "atom_to_ts": {1: 1, 2: 2},
    },
    {
        "side": "reactant",
        "species_key": "h",
        "participant_index": 2,
        "geometry_key": "h-geom",
        "atom_to_ts": {1: 3},
    },
    {
        "side": "product",
        "species_key": "h2o",
        "participant_index": 1,
        "geometry_key": "h2o-geom",
        "atom_to_ts": {1: 1, 2: 2, 3: 3},
    },
]

#: The electron, said rather than skipped: no geometry to count into and no
#: atoms to count. Appending it is what makes the map complete over every
#: declared participant.
_MAPPED_ELECTRON = {
    "side": "product",
    "species_key": "electron",
    "participant_index": 2,
    "atom_to_ts": {},
}

_IRC_AD_REACTANTS = {"reactant:1": [1, 2], "reactant:2": [3]}
#: The partition an associative detachment actually has: water is the whole
#: saddle point and the electron is none of it.
_IRC_AD_PRODUCTS = {"product:1": [1, 2, 3], "product:2": []}


def test_a_reaction_releasing_an_electron_deposits_its_map_unopposed(
    db_engine,
) -> None:
    """``OH- + H -> H2O + e-``. An absence that must not read as disagreement.

    The map skips the electron and the IRC mappings are omitted altogether,
    which a depositor is entitled to do on any reaction: the mappings are
    optional on every path and a partial map is always accepted. The deposit
    therefore carries a map and no partition, and must still be accepted —
    with the incompleteness warning the skipped participant earns it, and
    nothing from this rule.
    """

    payload = _electron_payload()
    _with_map(
        payload,
        [dict(participant) for participant in _MAP_ELECTRON_OMITTED],
    )
    _with_evidence(payload, reactants=None, products=None)

    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
        assert result["atom_map_id"] is not None
    assert W_ATOM_MAP_CONTRADICTS_IRC_MAPPING not in _codes(result)
    # The electron is the participant the map does not cover.
    assert W_ATOM_MAP_PARTICIPANTS_INCOMPLETE in _codes(result)


def _irc_calculation_id(session: Session, result: dict) -> int:
    from app.db.models.calculation import Calculation
    from app.db.models.common import CalculationType

    return session.scalar(
        select(Calculation.id).where(
            Calculation.transition_state_entry_id
            == result["transition_state_entry_id"],
            Calculation.type == CalculationType.irc,
        )
    )


# ---------------------------------------------------------------------------
# A participant with no atoms, said rather than skipped
# ---------------------------------------------------------------------------
#
# A free electron is the one participant that is made of nothing, and until
# both wire surfaces could say so it had to be left out: the IRC mappings were
# omitted altogether and the atom map skipped the participant. That was
# accepted, and it was quietly weaker than for every other reaction -- the
# completeness rules on both surfaces are gated on every declared participant
# being covered, so a reaction with an electron in it could never reach the
# state where they engage.
#
# What must not follow from letting an empty list mean "no atoms" is that an
# empty list can mean "I did not work this one out". That is the wrong-mapping
# failure in reverse: the coverage rule still demands every saddle-point atom
# be claimed by somebody, so a molecule declared empty has its atoms attributed
# to some other participant. Both directions are therefore refused, on both
# surfaces, from the participant's declared kind.


#: The saddle point of ``_electron_payload`` with one hydrogen too many. The
#: reaction it belongs to still balances -- three atoms on each side -- so a
#: complete map of it has a saddle-point atom that comes from nothing and
#: becomes nothing.
_XYZ_TS_AD_SPARE = (
    "4\nTS for OH- + H -> H2O + e-, with a spare atom\n"
    "O  0.000  0.000  0.000\n"
    "H  0.960  0.000  0.000\n"
    "H -0.400  1.400  0.000\n"
    "H  0.000  0.000  3.000"
)


def _refusal_codes(error: ValidationError) -> set[str | None]:
    """The codes a wire refusal carries, read where the contract puts them.

    Not out of the message: ``CodedValidationError`` deliberately keeps the
    code off the sentence so that improving the prose cannot change the
    contract. Pydantic preserves the exception object itself, which is where
    the backend's ``validation_detail_code`` reads it from too.
    """

    return {
        getattr(detail.get("ctx", {}).get("error"), "code", None)
        for detail in error.errors()
    }


def _electron_map(*, include_electron: bool) -> list[dict]:
    participants = [dict(participant) for participant in _MAP_ELECTRON_OMITTED]
    if include_electron:
        participants.append(dict(_MAPPED_ELECTRON))
    return participants


def test_a_reaction_releasing_an_electron_can_partition_its_saddle_point(
    db_engine,
) -> None:
    """The hole, closed: ``product:2: []`` on one surface, no atoms on the other.

    Both surfaces now describe all four declared participants of
    ``OH- + H -> H2O + e-``, and describing the electron costs nothing but
    saying it: no geometry, no indices. The deposit is accepted, the two
    partitions agree, and neither incompleteness warning fires, because
    nothing is in fact incomplete.
    """

    payload = _with_evidence(
        _with_map(_electron_payload(), _electron_map(include_electron=True)),
        reactants=_IRC_AD_REACTANTS,
        products=_IRC_AD_PRODUCTS,
    )

    with _isolated_session(db_engine) as session:
        result = _upload(session, payload)
        assert result["atom_map_id"] is not None
        stored = session.scalar(
            select(TransitionStateValidationEvidence).where(
                TransitionStateValidationEvidence.transition_state_entry_id
                == result["transition_state_entry_id"]
            )
        )
        # The claim survives the round trip as a claim: an empty list, not a
        # missing key and not a null mapping.
        assert stored.product_participant_mapping == {
            "product:1": [1, 2, 3],
            "product:2": [],
        }

    codes = _codes(result)
    assert W_ATOM_MAP_CONTRADICTS_IRC_MAPPING not in codes
    assert W_ATOM_MAP_PARTICIPANTS_INCOMPLETE not in codes
    assert W_ATOM_MAP_ATOMS_INCOMPLETE not in codes


def test_the_irc_partition_may_not_hand_the_electron_a_real_atom() -> None:
    """The original failure, on the surface that could once have deposited it.

    Water gets two of the three saddle-point atoms and the electron gets the
    third. It is a well-formed partition -- every atom claimed exactly once --
    so the coverage rule sees nothing wrong, and only the participant's
    declared kind says that an electron cannot be made of a hydrogen.
    """

    payload = _with_evidence(
        _electron_payload(),
        reactants=_IRC_AD_REACTANTS,
        products={"product:1": [1, 2], "product:2": [3]},
    )

    with pytest.raises(ValidationError) as excinfo:
        ComputedReactionUploadRequest(**payload)

    message = str(excinfo.value)
    assert "product:2" in message
    assert "has no atoms" in message


def test_a_molecule_may_not_be_declared_atomless_on_the_irc_surface() -> None:
    """An empty list is a claim, not a shrug.

    The methyl is handed all five saddle-point atoms and the incoming hydrogen
    is declared to have none. Every atom is still claimed exactly once, so this
    is precisely the shape the completeness rule cannot see; what refuses it is
    that ``reactant:2`` is a hydrogen atom, and a hydrogen atom has an atom.
    """

    payload = _with_evidence(
        _payload(),
        reactants={"reactant:1": [1, 2, 3, 4, 5], "reactant:2": []},
        products=_IRC_PRODUCTS,
    )

    with pytest.raises(ValidationError) as excinfo:
        ComputedReactionUploadRequest(**payload)

    message = str(excinfo.value)
    assert "reactant:2" in message
    assert "omit the mappings instead" in message


def test_the_map_may_not_hand_the_electron_a_real_atom() -> None:
    """The same refusal on the other surface, from the same declared kind."""

    participants = _electron_map(include_electron=False)
    participants.append(
        {
            **_MAPPED_ELECTRON,
            "geometry_key": "h2o-geom",
            "atom_to_ts": {1: 3},
        }
    )
    payload = _with_map(_electron_payload(), participants)

    with pytest.raises(ValidationError) as excinfo:
        ComputedReactionUploadRequest(**payload)

    assert W_ATOM_MAP_PARTICIPANT_NOT_DECLARED in _refusal_codes(excinfo.value)
    assert "has no atoms" in str(excinfo.value)


def test_a_molecule_may_not_be_declared_atomless_on_the_map() -> None:
    """To say nothing about a participant, leave it out; do not say "none"."""

    participants = [dict(participant) for participant in _MAP_METHYL_IS_1234]
    for participant in participants:
        if participant["species_key"] == "h":
            participant.pop("geometry_key")
            participant["atom_to_ts"] = {}
    payload = _with_map(_payload(), participants)

    with pytest.raises(ValidationError) as excinfo:
        ComputedReactionUploadRequest(**payload)

    assert W_ATOM_MAP_PARTICIPANT_NOT_DECLARED in _refusal_codes(excinfo.value)
    assert "leave it out of the map" in str(excinfo.value)


def test_atoms_need_a_geometry_and_a_geometry_needs_atoms() -> None:
    """The two halves of geometry-relative indexing, both refused.

    An index with no geometry beside it identifies a position in an ordering
    the reader has to guess at. A geometry beside no indices is the reverse:
    the participant has just said it has no atoms, and a participant with no
    atoms has no geometry.
    """

    with pytest.raises(ValidationError) as excinfo:
        ReactionAtomMapParticipantIn(
            side="reactant",
            species_key="ch3",
            participant_index=1,
            atom_to_ts={1: 1},
        )
    assert W_ATOM_MAP_INDICES_NOT_GEOMETRY_RELATIVE in _refusal_codes(
        excinfo.value
    )

    with pytest.raises(ValidationError) as excinfo:
        ReactionAtomMapParticipantIn(
            side="reactant",
            species_key="ch3",
            participant_index=1,
            geometry_key="ch3-geom",
            atom_to_ts={},
        )
    assert W_ATOM_MAP_INDICES_NOT_GEOMETRY_RELATIVE in _refusal_codes(
        excinfo.value
    )


def test_mapping_the_electron_lets_the_unaccounted_atom_rule_engage() -> None:
    """What being able to say "no atoms" actually buys.

    The saddle point here carries a fourth atom the reaction does not have.
    The rule that refuses it engages only once the map is complete over every
    declared participant, so it turns entirely on whether the electron can be
    named -- and that is a state a reaction releasing one could not reach at
    all while a zero-atom participant was inexpressible.
    """

    payload = _electron_payload()
    payload["transition_state"]["geometry"]["xyz_text"] = _XYZ_TS_AD_SPARE
    _with_map(payload, _electron_map(include_electron=True))

    with pytest.raises(ValidationError) as excinfo:
        ComputedReactionUploadRequest(**payload)

    assert W_ATOM_MAP_ATOMS_UNACCOUNTED_FOR in _refusal_codes(excinfo.value)
    assert "atom(s) [4] are claimed by neither leg" in str(excinfo.value)


def test_skipping_the_electron_still_gates_that_rule_off() -> None:
    """The control, and the measure of what this closed.

    The identical saddle point with the identical spare atom, mapped the only
    way the schema used to allow. The map is incomplete over its participants,
    so a missing species could explain the leftover atom, so the rule stays
    silent and the payload is accepted. That is correct behaviour for an
    incomplete map -- and it was, until the electron could be named, the *only*
    behaviour available to this reaction.
    """

    payload = _electron_payload()
    payload["transition_state"]["geometry"]["xyz_text"] = _XYZ_TS_AD_SPARE
    _with_map(payload, _electron_map(include_electron=False))

    request = ComputedReactionUploadRequest(**payload)
    assert request.atom_map is not None
