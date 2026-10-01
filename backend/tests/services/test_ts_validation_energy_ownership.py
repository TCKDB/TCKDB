"""An ``energy_ordering`` energy must come from a calculation of what it is the energy of.

The wire schemas refuse a mistaken key earlier, with a list of the keys that
would have worked. This is the seam's own backstop for a caller that reaches
``persist_transition_state_validation_evidence`` another way, and it is tested
directly because nothing over HTTP can reach it: a test through the routes
would be satisfied by the schema layer and go on passing with the backstop
deleted.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from tckdb_schemas.fragments.ts_validation_evidence import (
    TransitionStateValidationEvidenceIn,
)

from app.api.error_contract import CodedValueError
from app.db.models.common import ReactionRole
from app.db.models.reaction import ReactionEntryStructureParticipant
from app.db.models.transition_state import TransitionStateValidationEnergy
from app.services.transition_state_validation import (
    persist_transition_state_validation_evidence,
)
from tests.services.scientific_read._factories import (
    make_calculation,
    make_chem_reaction,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
)

_OWNER_MISMATCH = "ts_validation_source_calculation_owner_mismatch"


def _fixture(db_session, tag: str):
    """``A -> B`` with a saddle point, and one calculation for each of the three."""
    reactant = make_species(db_session, inchi_key=next_inchi_key(f"{tag}R"))
    product = make_species(db_session, inchi_key=next_inchi_key(f"{tag}P"))
    reactant_entry = make_species_entry(db_session, reactant)
    product_entry = make_species_entry(db_session, product)
    reaction_entry = make_reaction_entry(
        db_session,
        reaction=make_chem_reaction(db_session, reactants=[reactant], products=[product]),
        reactant_entries=[reactant_entry],
        product_entries=[product_entry],
    )
    ts_entry = make_transition_state_entry(
        db_session,
        transition_state=make_transition_state(db_session, reaction_entry=reaction_entry),
    )
    return {
        "reaction_entry": reaction_entry,
        "ts_entry": ts_entry,
        "ts_calc": make_calculation(db_session, transition_state_entry_id=ts_entry.id),
        "reactant_calc": make_calculation(db_session, species_entry_id=reactant_entry.id),
        "product_calc": make_calculation(db_session, species_entry_id=product_entry.id),
    }


def _ordering(ts_key="ts", reactant_key="r", product_key="p") -> TransitionStateValidationEvidenceIn:
    return TransitionStateValidationEvidenceIn(
        kind="energy_ordering",
        passed=True,
        rationale="TS above both wells",
        energies=[
            {"participant": "ts", "energy_kind": "electronic", "energy_hartree": -1.0,
             "source_calculation_key": ts_key},
            {"participant": "reactant:1", "energy_kind": "electronic", "energy_hartree": -2.0,
             "source_calculation_key": reactant_key},
            {"participant": "product:1", "energy_kind": "electronic", "energy_hartree": -3.0,
             "source_calculation_key": product_key},
        ],
    )


def _persist(db_session, fx, evidence, keys):
    return persist_transition_state_validation_evidence(
        db_session,
        [evidence],
        transition_state_entry_id=fx["ts_entry"].id,
        reconstruction_calculation_ids=[None],
        calculation_ids_by_key=keys,
        subject_label="ts",
        field_path="validation_evidence",
        reaction_entry_id=fx["reaction_entry"].id,
        transition_state_geometry_id=None,
    )


def _keys(fx) -> dict[str, int]:
    return {"ts": fx["ts_calc"].id, "r": fx["reactant_calc"].id, "p": fx["product_calc"].id}


def test_each_energy_is_stored_against_its_own_calculation(db_session) -> None:
    fx = _fixture(db_session, "OWNOK")
    (row,) = _persist(db_session, fx, _ordering(), _keys(fx))
    db_session.flush()

    stored = {
        (e.participant, e.energy_kind): (e.energy_hartree, e.source_calculation_id)
        for e in db_session.scalars(
            select(TransitionStateValidationEnergy).where(
                TransitionStateValidationEnergy.evidence_id == row.id
            )
        )
    }
    assert stored == {
        ("ts", "electronic"): (-1.0, fx["ts_calc"].id),
        ("reactant:1", "electronic"): (-2.0, fx["reactant_calc"].id),
        ("product:1", "electronic"): (-3.0, fx["product_calc"].id),
    }
    assert row.reconstruction_calculation_id is None


@pytest.mark.parametrize(
    ("ts_key", "reactant_key", "product_key"),
    [
        ("r", "r", "p"),  # the saddle point's energy from a reactant calculation
        ("ts", "p", "p"),  # the reactant's energy from the product's calculation
        ("ts", "r", "ts"),  # the product's energy from the saddle point's
    ],
)
def test_an_energy_from_a_calculation_of_something_else_is_refused(
    db_session, ts_key, reactant_key, product_key
) -> None:
    fx = _fixture(db_session, f"OWNBAD{ts_key}{reactant_key}{product_key}")
    with pytest.raises(CodedValueError) as caught:
        _persist(db_session, fx, _ordering(ts_key, reactant_key, product_key), _keys(fx))
    assert caught.value.code == _OWNER_MISMATCH
    # Refused before anything was stored.
    assert db_session.scalars(select(TransitionStateValidationEnergy)).all() == []


def test_the_refusal_names_the_field_and_discloses_no_row_id(db_session) -> None:
    fx = _fixture(db_session, "OWNMSG")
    with pytest.raises(CodedValueError) as caught:
        _persist(db_session, fx, _ordering("r"), _keys(fx))
    assert caught.value.code == _OWNER_MISMATCH
    message = str(caught.value)
    assert "validation_evidence[0].energies[0]" in message
    for row_id in (fx["ts_calc"].id, fx["reactant_calc"].id, fx["ts_entry"].id):
        assert f"id={row_id}" not in message


def test_a_path_with_no_key_namespace_cannot_carry_an_ordering(db_session) -> None:
    fx = _fixture(db_session, "OWNNS")
    with pytest.raises(ValueError, match="no calculation-key namespace"):
        _persist(db_session, fx, _ordering(), None)


def test_a_participant_the_reaction_does_not_declare_is_refused(db_session) -> None:
    fx = _fixture(db_session, "OWNUND")
    evidence = TransitionStateValidationEvidenceIn(
        kind="energy_ordering",
        passed=False,
        rationale="r",
        energies=[
            {"participant": "ts", "energy_kind": "electronic", "energy_hartree": -1.0,
             "source_calculation_key": "ts"},
            {"participant": "reactant:2", "energy_kind": "electronic", "energy_hartree": -2.0,
             "source_calculation_key": "r"},
            {"participant": "product:1", "energy_kind": "electronic", "energy_hartree": -3.0,
             "source_calculation_key": "p"},
        ],
    )
    with pytest.raises(ValueError, match="does not declare"):
        _persist(db_session, fx, evidence, _keys(fx))


def test_an_imaginary_mode_must_name_a_freq_calculation_of_this_saddle_point(db_session) -> None:
    fx = _fixture(db_session, "OWNFREQ")
    from app.db.models.common import CalculationType

    freq = make_calculation(
        db_session, transition_state_entry_id=fx["ts_entry"].id, type=CalculationType.freq
    )
    other_freq = make_calculation(
        db_session, species_entry_id=fx["reactant_calc"].species_entry_id, type=CalculationType.freq
    )
    record = TransitionStateValidationEvidenceIn(kind="imaginary_mode", passed=True, rationale="r")

    def persist(calculation_id: int):
        return persist_transition_state_validation_evidence(
            db_session,
            [record],
            transition_state_entry_id=fx["ts_entry"].id,
            reconstruction_calculation_ids=[calculation_id],
            subject_label="ts",
            field_path="validation_evidence",
            reaction_entry_id=fx["reaction_entry"].id,
            transition_state_geometry_id=None,
        )

    # The saddle point's own opt-like calculation is not a freq calculation.
    with pytest.raises(ValueError, match="must name a freq calculation of this transition state"):
        persist(fx["ts_calc"].id)
    # Nor is a freq calculation of some other subject: a different refusal,
    # with its own code, because the repair is a different calculation.
    with pytest.raises(CodedValueError) as caught:
        persist(other_freq.id)
    assert caught.value.code == _OWNER_MISMATCH
    (row,) = persist(freq.id)
    assert row.reconstruction_calculation_id == freq.id


def test_the_participant_rows_the_check_reads_are_the_declared_ones(db_session) -> None:
    """Pins the lookup the ownership check depends on: role and 1-based index."""
    fx = _fixture(db_session, "OWNROW")
    rows = db_session.scalars(
        select(ReactionEntryStructureParticipant).where(
            ReactionEntryStructureParticipant.reaction_entry_id == fx["reaction_entry"].id
        )
    ).all()
    assert {(r.role, r.participant_index) for r in rows} == {
        (ReactionRole.reactant, 1),
        (ReactionRole.product, 1),
    }
