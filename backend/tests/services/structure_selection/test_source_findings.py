"""Confirmed findings applied to the sources a product rests on: scope, role, settling and disclosure.

Each test names one fact about a finding and asserts whether a product's source in a given role is invalidated by it. The
paired cases are the point: a finding about an unrelated target must change nothing, a curvature finding must not ban a
recorded energy on the same calculation, and a settled finding is history.
"""

from __future__ import annotations

import pytest

from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationType,
    StructureFindingAuthority,
    StructureFindingKind,
    StructureFindingVerdict,
    StructureSourceRole,
)
from app.services.structure_selection.source_findings import (
    KINETICS_ROLES,
    THERMO_ROLES,
    SourceUse,
    assess_source_findings,
)
from tests.services.scientific_read._factories import (
    attach_input_geometry,
    attach_opt_result,
    attach_output_geometry,
    attach_sp_result,
    make_calculation,
    make_geometry,
)
from tests.services.structure_selection._support import make_world

K = StructureFindingKind
V = StructureFindingVerdict
A = StructureFindingAuthority
R = StructureSourceRole


def use(calc: Calculation, legacy_role: str, roles=THERMO_ROLES) -> SourceUse:
    return SourceUse(calc.id, calc.type.value, legacy_role, roles[legacy_role])


def verdict(session, *uses: SourceUse):
    session.flush()
    return assess_source_findings(session, {"p": list(uses)})["p"]


@pytest.fixture
def world(db_session):
    return make_world(db_session)


@pytest.fixture
def sp(world, db_session):
    """A single point on its own geometry: its energy describes its INPUT geometry."""
    geometry = make_geometry(db_session)
    calc = make_calculation(db_session, type=CalculationType.sp, species_entry_id=world.entry.id, lot_id=world.lot.id)
    attach_sp_result(db_session, calculation=calc, electronic_energy_hartree=-76.4)
    attach_input_geometry(db_session, calculation=calc, geometry=geometry)
    return calc, geometry


def test_nothing_in_the_store_changes_nothing(db_session, sp):
    calc, _ = sp
    result = verdict(db_session, use(calc, "sp"))
    assert (result.blocking, result.unresolved, result.advisory) == ((), (), ())


def test_a_confirmed_identity_failure_on_the_calculation_blocks_every_role_it_fills(db_session, world, sp):
    calc, _ = sp
    world.finding(calculation=calc, kind=K.identity_incompatibility)
    result = verdict(db_session, use(calc, "sp"))
    assert result.blocking == ("source_finding:sp:finding_invalidates:identity_incompatibility:authorized_adjudication",)


def test_a_finding_on_the_geometry_the_energy_describes_blocks_and_on_the_other_side_does_not(db_session, world, sp):
    calc, geometry = sp
    produced = make_geometry(db_session)  # an sp has no output geometry of interest: attach one the energy does NOT describe
    attach_output_geometry(db_session, calculation=calc, geometry=produced)
    world.finding(geometry=produced, kind=K.identity_incompatibility)
    assert verdict(db_session, use(calc, "sp")).blocking == ()  # the energy reads the input side
    world.finding(geometry=geometry, kind=K.identity_incompatibility)
    assert verdict(db_session, use(calc, "sp")).blocking  # now it is the geometry the energy describes


def test_a_finding_about_another_calculation_geometry_or_determination_is_not_this_products_business(db_session, world, sp):
    calc, _ = sp
    other = make_calculation(db_session, type=CalculationType.sp, species_entry_id=world.entry.id, lot_id=world.lot.id)
    other_geometry = make_geometry(db_session)
    attach_input_geometry(db_session, calculation=other, geometry=other_geometry)
    world.finding(calculation=other, kind=K.identity_incompatibility)
    world.finding(geometry=other_geometry, kind=K.state_incompatibility)
    result = verdict(db_session, use(calc, "sp"))
    assert (result.blocking, result.unresolved, result.advisory) == ((), (), ())


def test_a_curvature_finding_does_not_ban_a_separately_valid_recorded_energy_on_the_same_calculation(db_session, world):
    geometry = make_geometry(db_session)
    opt = make_calculation(db_session, type=CalculationType.opt, species_entry_id=world.entry.id, lot_id=world.lot.id)
    attach_opt_result(db_session, calculation=opt, final_energy_hartree=-76.4, converged=True)
    attach_output_geometry(db_session, calculation=opt, geometry=geometry)
    world.finding(calculation=opt, kind=K.contradictory_characterization)
    world.finding(calculation=opt, kind=K.role_invalidation, role=R.curvature)
    energy_only = SourceUse(opt.id, "opt", "sp", frozenset({"energy"}))
    curvature = SourceUse(opt.id, "opt", "freq", THERMO_ROLES["freq"])
    assert verdict(db_session, energy_only).blocking == ()
    assert any("contradictory_characterization" in c for c in verdict(db_session, curvature).blocking)
    assert any("role_invalidated:curvature" in c for c in verdict(db_session, curvature).blocking)


def test_a_role_invalidation_bites_only_the_role_it_names(db_session, world, sp):
    calc, _ = sp
    world.finding(calculation=calc, kind=K.role_invalidation, role=R.curvature)
    assert verdict(db_session, use(calc, "sp")).blocking == ()
    world.finding(calculation=calc, kind=K.role_invalidation, role=R.energy)
    assert verdict(db_session, use(calc, "sp")).blocking == ("source_finding:sp:role_invalidated:energy:authorized_adjudication",)


def test_an_authorized_same_subject_adjudication_settles_a_finding_and_history_is_kept(db_session, world, sp):
    calc, _ = sp
    disproof = world.finding(calculation=calc, kind=K.identity_incompatibility)
    assert verdict(db_session, use(calc, "sp")).blocking
    world.finding(
        calculation=calc, kind=K.adjudication, verdict=V.does_not_invalidate, authority=A.authorized_adjudication,
        supersedes=disproof,
    )
    assert verdict(db_session, use(calc, "sp")).blocking == ()  # a fresh decision no longer sees it
    from sqlalchemy import func, select

    from app.db.models.structure_determination import StructureEvidenceFinding

    # ...and nothing was rewritten: both rows are still stored, the disproof unchanged.
    assert db_session.scalar(select(func.count()).select_from(StructureEvidenceFinding)) >= 2
    db_session.refresh(disproof)
    assert disproof.verdict is V.invalidates


def test_a_producers_own_does_not_invalidate_does_not_erase_an_authorized_disproof(db_session, world, sp):
    """The database refuses a producer assertion that supersedes; standing beside the disproof, it settles nothing."""
    calc, _ = sp
    world.finding(calculation=calc, kind=K.identity_incompatibility)
    world.finding(
        calculation=calc, kind=K.identity_incompatibility, verdict=V.does_not_invalidate, authority=A.producer_assertion,
    )
    assert verdict(db_session, use(calc, "sp")).blocking


def test_an_adjudication_about_a_different_subject_settles_nothing_here(db_session, world, sp):
    calc, _ = sp
    other = make_calculation(db_session, type=CalculationType.sp, species_entry_id=world.entry.id, lot_id=world.lot.id)
    disproof = world.finding(calculation=calc, kind=K.identity_incompatibility)
    world.finding(calculation=other, kind=K.adjudication, verdict=V.does_not_invalidate, supersedes=disproof)
    assert verdict(db_session, use(calc, "sp")).blocking


def test_an_unresolved_verdict_is_unresolved_not_confirmed(db_session, world, sp):
    calc, _ = sp
    world.finding(calculation=calc, kind=K.identity_incompatibility, verdict=V.unresolved)
    result = verdict(db_session, use(calc, "sp"))
    assert result.blocking == () and result.unresolved == ("source_finding:sp:finding_unresolved:identity_incompatibility",)


def test_a_finding_version_this_release_cannot_read_is_disclosed_and_excludes_nothing(db_session, world, sp):
    calc, _ = sp
    world.finding(calculation=calc, kind=K.identity_incompatibility, version=2)
    result = verdict(db_session, use(calc, "sp"))
    assert result.blocking == () and result.advisory == ("source_finding:sp:finding_version_unsupported",)


def test_a_determination_finding_binds_only_through_a_source_role_the_product_uses_on_the_geometry_it_evaluates(
    db_session, world, sp
):
    calc, geometry = sp
    other_geometry = make_geometry(db_session)
    on_this = world.determination([("energy", calc)], evaluated=geometry)
    elsewhere = world.determination([("energy", calc)], evaluated=other_geometry)
    world.finding(determination=elsewhere, kind=K.identity_incompatibility)
    assert verdict(db_session, use(calc, "sp")).blocking == ()  # another geometry's claim
    world.finding(determination=on_this, kind=K.identity_incompatibility)
    assert verdict(db_session, use(calc, "sp")).blocking
    # the same determination read through a role the product does not use the calculation for does not bind either
    curvature_only = SourceUse(calc.id, "sp", "freq", THERMO_ROLES["freq"])
    assert verdict(db_session, curvature_only).blocking == ()


def test_the_kinetics_roles_are_the_same_vocabulary(db_session, world, sp):
    calc, _ = sp
    world.finding(calculation=calc, kind=K.identity_incompatibility)
    assert verdict(db_session, use(calc, "ts_energy", KINETICS_ROLES)).blocking
    assert "irc" not in KINETICS_ROLES and "fit_source" not in KINETICS_ROLES  # no structural role is read from them


def test_one_batch_answers_each_product_by_its_own_sources(db_session, world, sp):
    calc, _ = sp
    clean = make_calculation(db_session, type=CalculationType.sp, species_entry_id=world.entry.id, lot_id=world.lot.id)
    attach_input_geometry(db_session, calculation=clean, geometry=make_geometry(db_session))
    world.finding(calculation=calc, kind=K.identity_incompatibility)
    db_session.flush()
    out = assess_source_findings(db_session, {"bad": [use(calc, "sp")], "good": [use(clean, "sp")], "none": []})
    assert out["bad"].blocking and not out["good"].blocking and not out["none"].blocking
