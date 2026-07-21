"""Database enforcement tests for accepted-science immutability."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.models.app_user import AppUser
from app.db.models.calculation import CalculationDependency
from app.db.models.common import (
    AppUserRole,
    CalculationDependencyRole,
    NetworkStateKind,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.geometry import GeometryAtom
from app.db.models.network import Network
from app.db.models.network_pdep import NetworkState
from app.services.record_review import ensure_record_review, set_record_review_status
from tests.services.scientific_read._factories import (
    attach_geometry_atoms,
    attach_input_geometry,
    make_calculation,
    make_geometry,
    make_species,
    make_species_entry,
    next_inchi_key,
)


def _actor(session) -> AppUser:
    actor = AppUser(username="immutability-curator", role=AppUserRole.curator)
    session.add(actor)
    session.flush()
    return actor


def _approve(session, record_type, record_id, actor) -> None:
    ensure_record_review(session, record_type=record_type, record_id=record_id)
    review = set_record_review_status(
        session,
        record_type=record_type,
        record_id=record_id,
        status=RecordReviewStatus.approved,
        actor=actor,
    )
    assert review.first_approved_at is not None


def test_ever_approved_root_and_new_child_are_immutable(db_session) -> None:
    actor = _actor(db_session)
    network = Network(name="frozen")
    db_session.add(network)
    db_session.flush()
    _approve(db_session, SubmissionRecordType.network, network.id, actor)

    with pytest.raises(DBAPIError), db_session.begin_nested():
        network.name = "mutated"
        db_session.flush()

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(
            NetworkState(
                network_id=network.id,
                kind=NetworkStateKind.well,
                composition_hash="a" * 64,
            )
        )
        db_session.flush()


def test_geometry_atoms_of_accepted_calculation_are_immutable(db_session) -> None:
    actor = _actor(db_session)
    species = make_species(db_session, inchi_key=next_inchi_key("IMMGEOM"))
    entry = make_species_entry(db_session, species)
    calculation = make_calculation(db_session, species_entry_id=entry.id)
    geometry = make_geometry(db_session, natoms=1)
    atom = attach_geometry_atoms(
        db_session,
        geometry=geometry,
        symbols=["H"],
        coords=[[0.0, 0.0, 0.0]],
    )[0]
    attach_input_geometry(db_session, calculation=calculation, geometry=geometry)
    _approve(
        db_session,
        SubmissionRecordType.calculation,
        calculation.id,
        actor,
    )

    with pytest.raises(DBAPIError), db_session.begin_nested():
        atom.x = 1.0
        db_session.flush()

    other_geometry = make_geometry(db_session, natoms=1)
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(
            GeometryAtom(
                geometry_id=geometry.id,
                atom_index=2,
                element="H",
                x=0.0,
                y=0.0,
                z=0.0,
            )
        )
        db_session.flush()
    assert other_geometry.id != geometry.id


def test_two_root_child_guard_handles_both_calculations(db_session) -> None:
    actor = _actor(db_session)
    species = make_species(db_session, inchi_key=next_inchi_key("IMMDEP"))
    entry = make_species_entry(db_session, species)
    parent = make_calculation(db_session, species_entry_id=entry.id)
    child = make_calculation(db_session, species_entry_id=entry.id)
    for calculation in (parent, child):
        _approve(
            db_session,
            SubmissionRecordType.calculation,
            calculation.id,
            actor,
        )

    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.add(
            CalculationDependency(
                parent_calculation_id=parent.id,
                child_calculation_id=child.id,
                dependency_role=CalculationDependencyRole.arkane_source,
            )
        )
        db_session.flush()


def test_truncate_cannot_bypass_append_only_assessment(db_session) -> None:
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(text("TRUNCATE record_reproducibility_assessment"))


def test_temp_record_review_cannot_shadow_immutability_lookup(db_session) -> None:
    actor = _actor(db_session)
    network = Network(name="temp-shadow-target")
    db_session.add(network)
    db_session.flush()
    _approve(db_session, SubmissionRecordType.network, network.id, actor)

    db_session.execute(text("CREATE TEMP TABLE record_review (LIKE public.record_review INCLUDING ALL) ON COMMIT DROP"))
    with pytest.raises(DBAPIError), db_session.begin_nested():
        db_session.execute(
            text("UPDATE public.network SET name = 'bypassed' WHERE id = :id"),
            {"id": network.id},
        )
