"""A composite with an output geometry is a valid parent of freq / sp / scan (ADR 0021, P3a).

A program-run named composite optimises the geometry as the first step of its
recipe, so a frequency, single point or scan that ran on that geometry hangs off
the composite as it would off an ``opt``. One with no output geometry produced
nothing for anything to run on, and an IRC still starts from an ``opt``.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from tckdb_schemas.fragments.geometry import GeometryPayload

from app.db.models.calculation import Calculation, CalculationOutputGeometry
from app.db.models.common import (
    CalculationDependencyRole,
    CalculationGeometryRole,
    CalculationType,
)
from app.services.calculation_resolution import (
    assert_dependency_role_type_compatible,
    dependency_role_type_compatible,
)
from app.services.geometry_resolution import resolve_geometry_payload

_WIDENED = [
    CalculationDependencyRole.freq_on,
    CalculationDependencyRole.single_point_on,
    CalculationDependencyRole.scan_parent,
]
_NOT_WIDENED = [CalculationDependencyRole.irc_start, CalculationDependencyRole.irc_followup]
_COUNTER = 0


def _calc(session: Session, calc_type: CalculationType) -> Calculation:
    global _COUNTER
    _COUNTER += 1
    tag = f"CMPDEP{_COUNTER:0>21}"[:27]
    species_id = session.connection().execute(
        text(
            "INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind) "
            "VALUES ('molecule', :s, :k, 0, 1, 'achiral') RETURNING id"
        ),
        {"s": tag, "k": tag},
    ).scalar_one()
    entry_id = session.connection().execute(
        text("INSERT INTO species_entry (species_id) VALUES (:s) RETURNING id"), {"s": species_id}
    ).scalar_one()
    calc = Calculation(type=calc_type, species_entry_id=entry_id)
    session.add(calc)
    session.flush()
    return calc


def _give_output_geometry(session: Session, calc: Calculation, *, flush: bool) -> None:
    geometry = resolve_geometry_payload(session, GeometryPayload(xyz_text="1\nH\nH 0.0 0.0 0.0"))
    session.add(
        CalculationOutputGeometry(
            calculation_id=calc.id,
            geometry_id=geometry.id,
            output_order=1,
            role=CalculationGeometryRole.final,
        )
    )
    if flush:
        session.flush()


@pytest.mark.parametrize("role", _WIDENED)
def test_an_opt_is_still_a_parent_of_every_widened_role(db_conn, role) -> None:
    with Session(db_conn) as session, session.begin():
        assert dependency_role_type_compatible(_calc(session, CalculationType.opt), role)
        session.rollback()


@pytest.mark.parametrize("flush", [True, False], ids=["linked", "pending"])
@pytest.mark.parametrize("role", _WIDENED)
def test_a_composite_with_an_output_geometry_is_a_parent(db_conn, role, flush) -> None:
    """Pending counts: the bundle workflow adds the link and asks before flushing."""
    with Session(db_conn) as session, session.begin():
        composite = _calc(session, CalculationType.composite)
        _give_output_geometry(session, composite, flush=flush)
        assert dependency_role_type_compatible(composite, role)
        assert_dependency_role_type_compatible(composite, role, context="test")
        session.rollback()


@pytest.mark.parametrize("role", _WIDENED)
def test_a_composite_without_an_output_geometry_is_not_a_parent(db_conn, role) -> None:
    with Session(db_conn) as session, session.begin():
        composite = _calc(session, CalculationType.composite)
        assert not dependency_role_type_compatible(composite, role)
        with pytest.raises(ValueError, match="declares none"):
            assert_dependency_role_type_compatible(composite, role, context="test")
        session.rollback()


@pytest.mark.parametrize("role", _NOT_WIDENED)
def test_irc_roles_do_not_accept_a_composite_even_with_a_geometry(db_conn, role) -> None:
    with Session(db_conn) as session, session.begin():
        composite = _calc(session, CalculationType.composite)
        _give_output_geometry(session, composite, flush=True)
        assert not dependency_role_type_compatible(composite, role)
        session.rollback()


@pytest.mark.parametrize("parent_type", [CalculationType.sp, CalculationType.freq, CalculationType.scan])
@pytest.mark.parametrize("role", _WIDENED)
def test_no_other_type_became_a_parent(db_conn, role, parent_type) -> None:
    with Session(db_conn) as session, session.begin():
        parent = _calc(session, parent_type)
        _give_output_geometry(session, parent, flush=True)
        assert not dependency_role_type_compatible(parent, role)
        session.rollback()
