"""The record's own declared electronic level is the method evidence a kinetics rule reads.

``kinetics.energy_level_of_theory_id`` is the depositor's claim about the whole record's energies. The H298 E1 rule
reads thermo's declared level the same way (``thermo_selection.loader``: the column first, the record's own source
calculations beside it); here the calculations can only verify or contradict the claim, never supply it. Nothing is
read from a sibling record or inferred from links alone.
"""

from __future__ import annotations

from app.db.models.common import CalculationType, KineticsCalculationRole
from app.db.models.kinetics import KineticsSourceCalculation
from app.db.models.level_of_theory import LevelOfTheoryMerge
from app.services.kinetics_selection.loader import load_population, scan_population
from app.services.kinetics_selection.rules import XYG3B3LYPBarrierRule
from app.services.selection_kernel import Tri
from tests.services.kinetics_selection.test_rules import FULL_PROTOCOL
from tests.services.kinetics_selection.test_service import REQUEST, declared, determination
from tests.services.scientific_read._factories import make_calculation, make_lot

RULE = XYG3B3LYPBarrierRule()
XYG3 = ("XYG3", "6-311+G(3df,2p)")
STORED_PROTOCOL = dict(FULL_PROTOCOL)


def _candidates(session, world):
    scan = scan_population(session, reaction_entry_id=world.entry.id, request=REQUEST)
    return {c.kinetics_ref: c for c in load_population(session, scan).candidates.values()}


def _record(session, world, key, *, lot=None, protocol=None, links=(), supporting=()):
    det = determination(session, world, key)

    def attach(k):
        if lot is not None:
            k.energy_level_of_theory_id = lot.id
        if protocol is not None:
            k.protocol_declaration = {
                **protocol,
                "supporting_calculations": [
                    {"calculation_ref": c.public_ref, "purpose": "electronic_energy"} for c in supporting
                ],
            }
        for calc, role in links:
            session.add(KineticsSourceCalculation(kinetics_id=k.id, calculation_id=calc.id, role=role))
        session.flush()

    return declared(session, world, det, children=attach)


def _sp(session, world, lot):
    return make_calculation(session, type=CalculationType.sp, species_entry_id=world.h.id, lot_id=lot.id)


def test_the_declared_level_is_read_first_and_only_for_the_record_that_declared_it(db_session, world):
    xyg3 = make_lot(db_session, method=XYG3[0], basis=XYG3[1])
    mine = _record(db_session, world, "mine", lot=xyg3)
    plain = _record(db_session, world, "plain")

    candidates = _candidates(db_session, world)

    (level,) = candidates[mine.public_ref].energy_levels
    assert (level["source"], level["role"], level["method"], level["basis"]) == (
        "record_declared", "electronic_energy", XYG3[0], XYG3[1]
    )
    assert level["level_of_theory_ref"] == xyg3.public_ref
    # No borrowing: the record that declared nothing has no declared level, and nothing was read from its sibling.
    assert candidates[plain.public_ref].energy_levels == ()


def test_a_declared_level_later_merged_is_read_as_the_row_it_merged_into(db_session, world):
    duplicate = make_lot(db_session, method="xyg3", basis="6-311+g(3df,2p)")
    holder = make_lot(db_session, method="XYG3", basis="6-311+G(3df,2p)")
    mine = _record(db_session, world, "mine", lot=duplicate)
    db_session.add(LevelOfTheoryMerge(merged_lot_id=duplicate.id, into_lot_id=holder.id))
    db_session.flush()

    (level,) = _candidates(db_session, world)[mine.public_ref].energy_levels

    assert level["level_of_theory_ref"] == holder.public_ref and level["method"] == "XYG3"


def test_declared_alone_is_declared_and_a_matching_calculation_makes_it_verified(db_session, world):
    xyg3 = make_lot(db_session, method=XYG3[0], basis=XYG3[1])
    alone = _record(db_session, world, "alone", lot=xyg3, protocol=STORED_PROTOCOL)
    calc = _sp(db_session, world, xyg3)
    verified = _record(
        db_session, world, "verified", lot=xyg3, protocol=STORED_PROTOCOL,
        links=[(calc, KineticsCalculationRole.ts_energy)], supporting=[calc],
    )

    candidates = _candidates(db_session, world)

    declared_side = RULE.preferred_side(candidates[alone.public_ref])
    assert declared_side.state is Tri.true and "energy_level_declared" in declared_side.reasons
    verified_side = RULE.preferred_side(candidates[verified.public_ref])
    assert verified_side.state is Tri.true and "energy_level_verified" in verified_side.reasons


def test_a_declaration_a_linked_calculation_contradicts_is_matched_by_no_side(db_session, world):
    xyg3 = make_lot(db_session, method=XYG3[0], basis=XYG3[1])
    b3lyp = make_lot(db_session, method="B3LYP", basis="6-311+G(3df,2p)")
    contradicted = _record(
        db_session, world, "contradicted", lot=xyg3, protocol=STORED_PROTOCOL,
        links=[(_sp(db_session, world, b3lyp), KineticsCalculationRole.ts_energy)],
    )

    candidate = _candidates(db_session, world)[contradicted.public_ref]

    assert RULE.preferred_side(candidate).state is Tri.false
    assert RULE.yielding_side(candidate).state is Tri.false


def test_calculations_without_a_declaration_are_not_method_evidence(db_session, world):
    """Links and supporting calculations alone never supply the method (they carry no side-of-the-barrier role)."""
    xyg3 = make_lot(db_session, method=XYG3[0], basis=XYG3[1])
    calc = _sp(db_session, world, xyg3)
    only_calcs = _record(
        db_session, world, "only-calcs", protocol=STORED_PROTOCOL,
        links=[(calc, KineticsCalculationRole.ts_energy)], supporting=[calc],
    )

    candidate = _candidates(db_session, world)[only_calcs.public_ref]

    assert RULE.preferred_side(candidate).state is Tri.unknown
    assert "electronic_energy_level_not_declared_on_the_record" in RULE.preferred_side(candidate).reasons
