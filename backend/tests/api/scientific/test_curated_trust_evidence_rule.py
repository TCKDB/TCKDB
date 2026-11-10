"""ADR 0023, step 5: the trust checklists read a returned record's calculations by the evidence rule.

The checklist of a kinetics record ("is a source calculation linked?") and of a transition-state entry ("is there a
frequency result that designates the reaction coordinate?") is a presence check over the calculations the record rests on.
Under ``curated`` those calculations are linked evidence: an unreviewed one is read (the exploratory answer), a rejected or
deprecated one is withheld. Before this step the checklists applied the record floor, so a record whose only source
calculation was unreviewed read as having none.

Every test states the review rows it builds and names the mutation it kills.
"""

from __future__ import annotations

import pytest

from app.db.models.common import (
    CalculationType,
    KineticsCalculationRole,
    RecordReviewStatus,
    SubmissionRecordType,
)
from app.db.models.kinetics import KineticsSourceCalculation
from tests.services.scientific_read._factories import (
    make_calculation,
    make_chem_reaction,
    make_kinetics,
    make_lot,
    make_reaction_entry,
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)

BASE = "/api/v1/scientific"
APPROVED = RecordReviewStatus.approved
WITHHELD = pytest.mark.parametrize("status", [RecordReviewStatus.rejected, RecordReviewStatus.deprecated])
CHECK = "source_calculations_present"


def _kinetics_with_one_source(db_session, *, source_status):
    """An approved kinetics record whose single source calculation has ``source_status`` (``None``: no review row)."""
    a, b = make_species(db_session, inchi_key=next_inchi_key("KTA")), make_species(db_session, inchi_key=next_inchi_key("KTB"))
    ea, eb = make_species_entry(db_session, a), make_species_entry(db_session, b)
    rxe = make_reaction_entry(
        db_session, reaction=make_chem_reaction(db_session, reactants=[a], products=[b]), reactant_entries=[ea], product_entries=[eb]
    )
    kin = make_kinetics(db_session, reaction_entry=rxe)
    calc = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=ea.id, lot_id=make_lot(db_session, method="ktlot").id
    )
    db_session.add(KineticsSourceCalculation(kinetics_id=kin.id, calculation_id=calc.id, role=KineticsCalculationRole.ts_energy))
    db_session.flush()
    if source_status is not None:
        set_review(db_session, record_type=SubmissionRecordType.calculation, record_id=calc.id, status=source_status)
    for kind, row in (
        (SubmissionRecordType.reaction_entry, rxe),
        (SubmissionRecordType.species_entry, ea),
        (SubmissionRecordType.species_entry, eb),
        (SubmissionRecordType.kinetics, kin),
    ):
        set_review(db_session, record_type=kind, record_id=row.id, status=APPROVED)
    return rxe, kin, calc


def _checks(client, rxe, kin, profile):
    r = client.get(f"{BASE}/reaction-entries/{rxe.public_ref}/kinetics", params={"profile": profile, "include": "trust"})
    assert r.status_code == 200, r.text
    [record] = [x for x in r.json()["records"] if x["kinetics_ref"] == kin.public_ref]
    return record["trust"]["evidence"]["checks"]


@pytest.mark.parametrize("status", [None, RecordReviewStatus.not_reviewed, RecordReviewStatus.under_review])
def test_a_kinetics_checklist_reads_an_unreviewed_source_calculation_under_curated(client, db_session, status):
    """Kills: ``kinetics_without_hidden_calculations`` keeping the record floor (the check reading ``missing`` for a
    record whose only source calculation is unreviewed, where exploratory reads ``passed``)."""
    rxe, kin, calc = _kinetics_with_one_source(db_session, source_status=status)
    exploratory = _checks(client, rxe, kin, "exploratory")
    assert exploratory[CHECK] == "passed"  # the control: the source is linked
    assert _checks(client, rxe, kin, "curated")[CHECK] == "passed"


@WITHHELD
def test_a_kinetics_checklist_leaves_out_a_withheld_source_calculation_under_curated(client, db_session, status):
    """Kills: the checklist reading a rejected or deprecated source calculation (the evidence rule not applied)."""
    rxe, kin, calc = _kinetics_with_one_source(db_session, source_status=status)
    assert _checks(client, rxe, kin, "exploratory")[CHECK] == "passed"  # non-vacuous: exploratory withholds nothing
    assert _checks(client, rxe, kin, "curated")[CHECK] == "missing"
