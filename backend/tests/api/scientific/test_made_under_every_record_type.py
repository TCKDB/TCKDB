"""A curated read compares a stamp's inputs exactly as an exploratory one does, for every record type that offers it (ADR 0023, step 6).

Each record is stamped over five inputs whose review rows are stated here: approved, no row at all, ``not_reviewed``,
``rejected`` and ``deprecated``. Five cases per type: nothing changed, then only the level of theory of one of the four
non-approved inputs changed.
"""

from __future__ import annotations

import pytest

from app.db.models.common import (
    CalculationType,
    KineticsCalculationRole,
    RecordReviewStatus,
    StatmechCalculationRole,
    SubmissionRecordType,
    ThermoCalculationRole,
    TransportCalculationRole,
)
from app.db.models.kinetics import KineticsSourceCalculation
from app.services.reproducibility_rubric import evaluate_and_append_reproducibility
from tests.services.scientific_read._factories import (
    attach_statmech_source_calculation,
    attach_thermo_source_calculation,
    attach_transport_source_calculation,
    make_calculation,
    make_chem_reaction,
    make_kinetics,
    make_lot,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    make_transport,
    next_inchi_key,
    set_review,
)

API = "/api/v1/scientific"
A = RecordReviewStatus.approved
INPUTS = {
    "approved": A,
    "no_row": None,
    "not_reviewed": RecordReviewStatus.not_reviewed,
    "rejected": RecordReviewStatus.rejected,
    "deprecated": RecordReviewStatus.deprecated,
}
CASES = [None, "rejected", "deprecated", "no_row", "not_reviewed"]


def _inputs(db_session, *, entry_kw):
    lot = make_lot(db_session)
    calcs = {}
    for name, status in INPUTS.items():
        calc = make_calculation(db_session, type=CalculationType.sp, lot_id=lot.id, **entry_kw)
        if status is not None:
            set_review(db_session, record_type=SubmissionRecordType.calculation, record_id=calc.id, status=status)
        calcs[name] = calc
    return calcs


def _species_entry(db_session):
    entry = make_species_entry(db_session, make_species(db_session))
    set_review(db_session, record_type=SubmissionRecordType.species_entry, record_id=entry.id, status=A)
    return entry


def _finish(db_session, record_type, record):
    evaluate_and_append_reproducibility(db_session, record_type=record_type, record_id=record.id)
    # Approved last: an accepted record's source links are immutable.
    set_review(db_session, record_type=record_type, record_id=record.id, status=A)
    db_session.expire_all()


def _thermo(db_session):
    entry = _species_entry(db_session)
    record = make_thermo_scalar(db_session, species_entry=entry)
    calcs = _inputs(db_session, entry_kw={"species_entry_id": entry.id})
    for calc in calcs.values():
        attach_thermo_source_calculation(db_session, thermo=record, calculation=calc, role=ThermoCalculationRole.sp)
    _finish(db_session, SubmissionRecordType.thermo, record)
    url = f"{API}/species-entries/{entry.public_ref}/thermo?include=assessments&check_inputs=true&limit=10"
    return calcs, url, lambda body: body["records"][0]["assessments"]["reproducibility"]


def _kinetics(db_session):
    rs = make_species(db_session, smiles="A", inchi_key=next_inchi_key("KMU1"))
    ps = make_species(db_session, smiles="B", inchi_key=next_inchi_key("KMU2"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    rse, pse = make_species_entry(db_session, rs), make_species_entry(db_session, ps)
    rxe = make_reaction_entry(db_session, reaction=chem, reactant_entries=[rse], product_entries=[pse])
    set_review(db_session, record_type=SubmissionRecordType.reaction_entry, record_id=rxe.id, status=A)
    record = make_kinetics(db_session, reaction_entry=rxe)
    calcs = _inputs(db_session, entry_kw={"species_entry_id": rse.id})
    for calc in calcs.values():
        db_session.add(
            KineticsSourceCalculation(kinetics_id=record.id, calculation_id=calc.id, role=KineticsCalculationRole.freq)
        )
    db_session.flush()
    _finish(db_session, SubmissionRecordType.kinetics, record)
    url = f"{API}/reaction-entries/{rxe.public_ref}/kinetics?include=assessments&check_inputs=true&limit=10"
    return calcs, url, lambda body: body["records"][0]["assessments"]["reproducibility"]


def _statmech(db_session):
    entry = _species_entry(db_session)
    record = make_statmech(db_session, species_entry=entry)
    calcs = _inputs(db_session, entry_kw={"species_entry_id": entry.id})
    for calc in calcs.values():
        attach_statmech_source_calculation(
            db_session, statmech=record, calculation=calc, role=StatmechCalculationRole.freq
        )
    _finish(db_session, SubmissionRecordType.statmech, record)
    url = f"{API}/statmech/{record.public_ref}?include=assessments,source_calculations"
    return calcs, url, lambda body: body["record"]["assessments"]["reproducibility"]


def _transport(db_session):
    entry = _species_entry(db_session)
    record = make_transport(db_session, species_entry=entry)
    calcs = _inputs(db_session, entry_kw={"species_entry_id": entry.id})
    for calc in calcs.values():
        attach_transport_source_calculation(
            db_session, transport=record, calculation=calc, role=TransportCalculationRole.full_transport
        )
    _finish(db_session, SubmissionRecordType.transport, record)
    url = f"{API}/transport/{record.public_ref}?include=assessments,source_calculations"
    return calcs, url, lambda body: body["record"]["assessments"]["reproducibility"]


def _get(client, url, profile):
    response = client.get(f"{url}&profile={profile}")
    assert response.status_code == 200, response.text
    return response


@pytest.mark.parametrize("changed", CASES, ids=lambda c: c or "unchanged")
@pytest.mark.parametrize(
    "build, refs_in_exploratory",
    [(_thermo, False), (_kinetics, False), (_statmech, True), (_transport, True)],
    ids=["thermo", "kinetics", "statmech", "transport"],
)
def test_curated_and_exploratory_compare_inputs_alike(client, db_session, build, refs_in_exploratory, changed):
    calcs, url, block = build(db_session)
    assert len(calcs) == 5 and all(c.public_ref for c in calcs.values())
    if changed is not None:
        calcs[changed].lot_id = make_lot(db_session, method="a-different-method").id
        db_session.flush()
        db_session.expire_all()
    curated, exploratory = _get(client, url, "curated"), _get(client, url, "exploratory")
    got_c, got_e = block(curated.json()), block(exploratory.json())
    assert got_c == got_e
    made = got_c["made_under"]
    assert made["inputs_checked"] is True
    if changed is None:
        assert (got_c["state"], made["state"], made["stale_reason"]) == ("current", "current", None)
    else:
        assert (got_c["state"], made["state"], made["stale_reason"]) == ("stale", "stale", "inputs_changed")
    for name in ("rejected", "deprecated"):
        assert calcs[name].public_ref not in curated.text
        if refs_in_exploratory:
            assert calcs[name].public_ref in exploratory.text, "the control: exploratory does name the input"
