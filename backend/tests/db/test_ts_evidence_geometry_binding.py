"""IRC evidence must name the geometry its atom indices count into.

``c1d2e3f4a5b6`` gave ``transition_state_validation_evidence`` two JSONB
participant mappings whose values are saddle-point atom indices, and recorded
nothing about which geometry those indices count into. An atom index is a
property of a geometry -- a transition-state entry can accumulate several as it
is re-optimised, with no guarantee any two order their atoms alike -- so an
index with no geometry beside it identifies a position in an ordering the
reader has to infer, and an inference landing on the wrong geometry silently
means a different atom. ``f3b7d2c8a419`` closes that the way ADR 0011 closed it
for ``reaction_atom_map``: the record names the geometry rather than leaving
one to be derived.

Two things are tested here, because the column is only worth having if both
hold: the rule is enforced by the database on every write, and the revision
that introduced it resolved the rows it could while declining to guess at the
rows it could not.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, text
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

#: The revision immediately before the one under test, so the fixture rows
#: below can be written in the shape that predates the column.
_BASE_REVISION = "b6c1f4a8e703"
_GEOMETRY_REVISION = "f3b7d2c8a419"

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


def test_backfill_resolves_what_it_can_and_declines_to_guess() -> None:
    """The revision's backfill contract, on a real disposable database.

    Three rows written in the pre-column shape:

    * one whose transition-state entry has exactly one output geometry, of the
      right size -- derivable, and derived;
    * one whose entry has two distinct output geometries -- ambiguous, and left
      NULL rather than filled with whichever the query happened to see first,
      because a wrong geometry here is precisely the silent atom substitution
      the column exists to prevent;
    * one with no mapping at all -- nothing to bind, nothing written.

    Because a row is left unresolved the constraint stays ``NOT VALID``, and
    the last assertion pins the property that makes that acceptable: it still
    refuses new violations.
    """

    from conftest import _database_url, _db_env, scratch_database_name

    db_name = scratch_database_name("ts_evidence_geometry")
    admin = create_engine(_database_url("postgres"), isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    admin_conn = None
    engine = None
    root = Path(__file__).resolve().parents[2]

    def run_revision(revision: str, current_engine):
        if current_engine is not None:
            current_engine.dispose()
        subprocess.run(
            ["conda", "run", "-n", "tckdb_env", "alembic", "upgrade", revision],
            cwd=root,
            env=env,
            check=True,
        )
        return create_engine(_database_url(db_name), pool_pre_ping=True)

    try:
        admin_conn = admin.connect()
        admin_conn.execute(text(f'CREATE DATABASE "{db_name}"'))

        env = _db_env(db_name)
        engine = run_revision(_BASE_REVISION, engine)

        with engine.begin() as conn:
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = 'transition_state_validation_evidence' "
                        "AND column_name = 'transition_state_geometry_id'"
                    )
                )
                == 0
            ), "fixture rows must be written in the shape that predates the column"

            reaction_id = conn.scalar(
                text("INSERT INTO chem_reaction (reversible) VALUES (true) RETURNING id")
            )
            reaction_entry_id = conn.scalar(
                text("INSERT INTO reaction_entry (reaction_id) VALUES (:r) RETURNING id"),
                {"r": reaction_id},
            )

            evidence_ids: dict[str, int] = {}
            geometry_ids: dict[str, int] = {}
            for tag, geometry_count, natoms, mapped in (
                ("derivable", 1, 3, True),
                ("ambiguous", 2, 3, True),
                ("unmapped", 1, 3, False),
            ):
                ts_id = conn.scalar(
                    text("INSERT INTO transition_state (reaction_entry_id) VALUES (:r) RETURNING id"),
                    {"r": reaction_entry_id},
                )
                ts_entry_id = conn.scalar(
                    text(
                        "INSERT INTO transition_state_entry "
                        "(transition_state_id, charge, multiplicity) "
                        "VALUES (:ts, 0, 2) RETURNING id"
                    ),
                    {"ts": ts_id},
                )
                calculation_id = conn.scalar(
                    text(
                        "INSERT INTO calculation (type, transition_state_entry_id) "
                        "VALUES ('opt', :e) RETURNING id"
                    ),
                    {"e": ts_entry_id},
                )
                for order in range(1, geometry_count + 1):
                    geometry_id = conn.scalar(
                        text(
                            "INSERT INTO geometry (natoms, geom_hash) "
                            "VALUES (:n, :h) RETURNING id"
                        ),
                        {"n": natoms, "h": f"{tag}-geom-{order}"},
                    )
                    conn.execute(
                        text(
                            "INSERT INTO calculation_output_geometry "
                            "(calculation_id, geometry_id, output_order) "
                            "VALUES (:c, :g, :o)"
                        ),
                        {"c": calculation_id, "g": geometry_id, "o": order},
                    )
                    if order == 1:
                        geometry_ids[tag] = geometry_id
                evidence_ids[tag] = conn.scalar(
                    text(
                        "INSERT INTO transition_state_validation_evidence "
                        "(transition_state_entry_id, kind, passed, rationale, "
                        " reconstruction_calculation_id, reactant_participant_mapping, "
                        " product_participant_mapping) "
                        "VALUES (:e, 'irc', true, :why, :c, "
                        "        CAST(:reactants AS jsonb), CAST(:products AS jsonb)) "
                        "RETURNING id"
                    ),
                    {
                        "e": ts_entry_id,
                        "why": f"{tag} legacy evidence",
                        "c": calculation_id,
                        "reactants": (
                            '{"reactant:1": [1], "reactant:2": [2, 3]}' if mapped else None
                        ),
                        "products": (
                            '{"product:1": [3], "product:2": [1, 2]}' if mapped else None
                        ),
                    },
                )

        engine = run_revision(_GEOMETRY_REVISION, engine)

        with engine.begin() as conn:
            bound = dict(
                conn.execute(
                    text(
                        "SELECT id, transition_state_geometry_id "
                        "FROM transition_state_validation_evidence"
                    )
                ).all()
            )
            # Derived exactly, from the single output geometry of the entry's
            # calculations -- not from whichever row sorted first.
            assert bound[evidence_ids["derivable"]] == geometry_ids["derivable"]
            # Two candidates, so the revision writes neither.
            assert bound[evidence_ids["ambiguous"]] is None
            # No indices, so nothing to bind.
            assert bound[evidence_ids["unmapped"]] is None

            assert (
                conn.scalar(
                    text("SELECT convalidated FROM pg_constraint WHERE conname = :name"),
                    {"name": _CONSTRAINT},
                )
                is False
            ), "an unresolved legacy row must leave the constraint NOT VALID"

            # NOT VALID skips the existing rows; it does not stop governing
            # new ones. Without this the fallback would be a silent opt-out.
            with pytest.raises(DBAPIError):
                conn.execute(
                    text(
                        "INSERT INTO transition_state_validation_evidence "
                        "(transition_state_entry_id, kind, passed, rationale, "
                        " reconstruction_calculation_id, reactant_participant_mapping, "
                        " product_participant_mapping) "
                        "SELECT transition_state_entry_id, 'irc', true, 'new row', "
                        "       reconstruction_calculation_id, "
                        "       CAST('{\"reactant:1\": [1]}' AS jsonb), "
                        "       CAST('{\"product:1\": [1]}' AS jsonb) "
                        "FROM transition_state_validation_evidence WHERE id = :id"
                    ),
                    {"id": evidence_ids["ambiguous"]},
                )
    finally:
        if engine is not None:
            engine.dispose()
        if admin_conn is not None:
            admin_conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
            admin_conn.close()
        admin.dispose()
