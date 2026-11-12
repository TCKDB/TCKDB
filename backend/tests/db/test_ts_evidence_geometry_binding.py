"""IRC evidence must name the geometry its atom indices count into.

``transition_state_validation_evidence`` carries two JSONB participant mappings whose values are saddle-point atom
indices. An atom index is a property of a geometry -- a transition-state entry can accumulate several as it is
re-optimised, with no guarantee any two order their atoms alike -- so an index with no geometry beside it identifies a
position in an ordering the reader has to infer. The record names the geometry rather than leaving one to be derived,
and the database enforces that on every write.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db.models.transition_state import TransitionStateValidationEvidence
from tests.services.scientific_read._factories import (
    make_calculation,
    make_chem_reaction,
    make_geometry,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
)

_CONSTRAINT = "ck_transition_state_validation_evidence_mapping_names_geometry"


_MAPPING = {"reactant:1": [1], "reactant:2": [2, 3]}
_PRODUCT_MAPPING = {"product:1": [3], "product:2": [1, 2]}


def _evidence_fixture(db_session, tag: str):
    reactant = make_species(db_session, inchi_key=next_inchi_key(f"{tag}R"))
    product = make_species(db_session, inchi_key=next_inchi_key(f"{tag}P"))
    reaction_entry = make_reaction_entry(
        db_session,
        reaction=make_chem_reaction(db_session, reactants=[reactant], products=[product]),
        reactant_entries=[make_species_entry(db_session, reactant)],
        product_entries=[make_species_entry(db_session, product)],
    )
    ts_entry = make_transition_state_entry(
        db_session,
        transition_state=make_transition_state(db_session, reaction_entry=reaction_entry),
    )
    calculation = make_calculation(db_session, transition_state_entry_id=ts_entry.id)
    geometry = make_geometry(db_session, natoms=3)
    return ts_entry, calculation, geometry


def test_a_mapping_without_a_geometry_is_refused(db_session) -> None:
    """The rule is the database's, not one deposit path's.

    Three workflows write this table, and a fourth would inherit the rule for
    free. The row below is otherwise complete and its mappings are internally
    well formed; what it cannot say is what its indices were counted in.
    """

    ts_entry, calculation, _ = _evidence_fixture(db_session, "TSGEOM")

    with pytest.raises(DBAPIError) as excinfo, db_session.begin_nested():
        db_session.add(
            TransitionStateValidationEvidence(
                transition_state_entry_id=ts_entry.id,
                kind="irc",
                passed=True,
                rationale="IRC reaches both endpoints",
                reconstruction_calculation_id=calculation.id,
                reactant_participant_mapping=_MAPPING,
                product_participant_mapping=_PRODUCT_MAPPING,
            )
        )
        db_session.flush()

    assert _CONSTRAINT in str(excinfo.value)


def test_one_sided_mapping_also_needs_a_geometry(db_session) -> None:
    """A half-populated row still has indices, so it still needs a geometry.

    The wire schema pairs the two sides, but the constraint does not lean on
    that: it tests each side independently, so a row reaching the table by any
    other route cannot carry indices anonymously.
    """

    ts_entry, calculation, _ = _evidence_fixture(db_session, "TSGEOM1S")

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(
            TransitionStateValidationEvidence(
                transition_state_entry_id=ts_entry.id,
                kind="irc",
                passed=False,
                rationale="only one side was resolved",
                reconstruction_calculation_id=calculation.id,
                reactant_participant_mapping=_MAPPING,
            )
        )
        db_session.flush()


def test_evidence_without_a_mapping_needs_no_geometry(db_session) -> None:
    """Evidence may be a rationale and a verdict, and then partitions no atoms.

    Both spellings of absence are accepted, which is not a formality: the
    JSONB type persists an explicitly-assigned ``None`` as JSON ``null`` rather
    than as SQL NULL, so the service's own rows take the second branch of the
    constraint and a rule written as ``IS NULL`` would refuse them all.
    """

    ts_entry, calculation, _ = _evidence_fixture(db_session, "TSGEOMNONE")

    unset = TransitionStateValidationEvidence(
        transition_state_entry_id=ts_entry.id,
        kind="irc",
        passed=True,
        rationale="the path was followed but the partition was not resolved",
        reconstruction_calculation_id=calculation.id,
    )
    db_session.add(unset)
    db_session.flush()
    assert unset.transition_state_geometry_id is None

    stored = db_session.execute(
        text(
            "SELECT jsonb_typeof(reactant_participant_mapping) AS assigned_null "
            "FROM transition_state_validation_evidence WHERE id = :id"
        ),
        {"id": unset.id},
    ).one()
    # SQL NULL here; the service's explicit ``None`` produces JSON 'null'.
    assert stored.assigned_null is None

    db_session.execute(
        text(
            "UPDATE transition_state_validation_evidence "
            "SET reactant_participant_mapping = 'null'::jsonb, "
            "product_participant_mapping = 'null'::jsonb WHERE id = :id"
        ),
        {"id": unset.id},
    )
    db_session.flush()


def test_a_mapping_that_names_its_geometry_is_accepted(db_session) -> None:
    ts_entry, calculation, geometry = _evidence_fixture(db_session, "TSGEOMOK")

    row = TransitionStateValidationEvidence(
        transition_state_entry_id=ts_entry.id,
        kind="irc",
        passed=True,
        rationale="IRC reaches both endpoints",
        reconstruction_calculation_id=calculation.id,
        reactant_participant_mapping=_MAPPING,
        product_participant_mapping=_PRODUCT_MAPPING,
        transition_state_geometry_id=geometry.id,
    )
    db_session.add(row)
    db_session.flush()

    stored = db_session.scalars(
        select(TransitionStateValidationEvidence).where(TransitionStateValidationEvidence.id == row.id)
    ).one()
    assert stored.transition_state_geometry_id == geometry.id


def test_the_constraint_is_validated_on_a_normally_migrated_database(db_session) -> None:
    """``NOT VALID`` is the fallback for unresolvable legacy rows, not the norm.

    The revision validates the constraint whenever its backfill leaves nothing
    unresolved, which is every database that has no legacy evidence at all --
    including this one. Asserting it here keeps the escape hatch from silently
    becoming the permanent state: a constraint left ``NOT VALID`` forever would
    still govern new writes, but would stop being evidence about what the table
    contains.
    """

    validated = db_session.execute(
        text("SELECT convalidated FROM pg_constraint WHERE conname = :name"),
        {"name": _CONSTRAINT},
    ).scalar_one()
    assert validated is True
