"""``include=assessments`` on a list route costs a bounded number of statements, whatever the number of records.

Re-evaluating a stored assessment's inputs is tens of statements per record, and every public species page asks for the
assessments of all of its thermo records. The ROUTE decides whether to do it (``check_inputs``, an explicit and bounded
opt-in on the thermo and kinetics lists, always on a by-ref statmech or transport read); the response size never does.

The seeded records carry opt, freq and sp source calculations. Without them ``evaluate_reproducibility`` costs nothing
and a per-record check would be invisible: ``test_the_seed_makes_a_per_record_check_visible`` pins that.
"""

from __future__ import annotations

from sqlalchemy import event

from app.db.models.common import CalculationType, SubmissionRecordType, ThermoCalculationRole
from app.services.reproducibility_rubric import evaluate_and_append_reproducibility, evaluate_reproducibility
from tests.services.scientific_read._factories import (
    attach_thermo_source_calculation,
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
    make_thermo_scalar,
)


def _statements(db_session, call) -> tuple[object, int]:
    seen: list[str] = []

    def hook(conn, cursor, statement, *args):
        seen.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", hook)
    try:
        out = call()
    finally:
        event.remove(bind, "before_cursor_execute", hook)
    return out, len(seen)


def _entry_with_assessed_thermos(db_session, n: int):
    entry = make_species_entry(db_session, make_species(db_session))
    lot = make_lot(db_session)
    thermos = []
    for i in range(n):
        thermo = make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-10.0 - i)
        for calc_type, role in (
            (CalculationType.opt, ThermoCalculationRole.opt),
            (CalculationType.freq, ThermoCalculationRole.freq),
            (CalculationType.sp, ThermoCalculationRole.sp),
        ):
            calc = make_calculation(db_session, type=calc_type, species_entry_id=entry.id, lot_id=lot.id)
            attach_thermo_source_calculation(db_session, thermo=thermo, calculation=calc, role=role)
        evaluate_and_append_reproducibility(db_session, record_type=SubmissionRecordType.thermo, record_id=thermo.id)
        thermos.append(thermo)
    db_session.expire_all()
    return entry, thermos


def _url(entry) -> str:
    return f"/api/v1/scientific/species-entries/{entry.public_ref}/thermo"


def test_the_seed_makes_a_per_record_check_visible(db_session):
    _, thermos = _entry_with_assessed_thermos(db_session, 1)
    _, cost = _statements(
        db_session,
        lambda: evaluate_reproducibility(db_session, record_type=SubmissionRecordType.thermo, record_id=thermos[0].id),
    )
    assert cost >= 10, f"a per-record evaluation costs {cost} statements: the seed is too thin to catch one"


def _overhead(client, db_session, n: int, *, check_inputs: bool = False) -> int:
    entry, _ = _entry_with_assessed_thermos(db_session, n)
    url = _url(entry)
    suffix = "&check_inputs=true&limit=10" if check_inputs else ""
    client.get(url)  # warm caches that are not about assessments
    _, without = _statements(db_session, lambda: client.get(url))
    response, with_assessments = _statements(db_session, lambda: client.get(f"{url}?include=assessments{suffix}"))
    assert response.status_code == 200, response.text
    assert len(response.json()["records"]) == n
    return with_assessments - without


def test_assessment_statements_do_not_grow_with_the_number_of_records(client, db_session):
    small = _overhead(client, db_session, 2)
    large = _overhead(client, db_session, 8)
    # A fixed number of grouped statements, however many records: the 6 extra records add at most a couple.
    assert large - small <= 2, f"assessments cost {small} statements for 2 records and {large} for 8"


def test_the_opt_in_check_does_cost_per_record(client, db_session):
    """The positive control: if this stopped growing, the bound above would be measuring nothing."""
    small = _overhead(client, db_session, 2, check_inputs=True)
    large = _overhead(client, db_session, 8, check_inputs=True)
    assert large - small >= 6 * 10, f"checked assessments cost {small} for 2 records and {large} for 8"


def test_a_list_never_says_current_and_the_same_record_reads_the_same_on_a_page_of_one_or_two(client, db_session):
    entry, thermos = _entry_with_assessed_thermos(db_session, 2)
    url = _url(entry)
    both = {
        r["thermo_ref"]: r["assessments"]["reproducibility"]
        for r in client.get(f"{url}?include=assessments").json()["records"]
    }
    one = client.get(f"{url}?include=assessments&limit=1").json()["records"]
    assert len(one) == 1
    ref = one[0]["thermo_ref"]
    assert one[0]["assessments"]["reproducibility"] == both[ref]
    for reproducibility in both.values():
        assert reproducibility["state"] == reproducibility["made_under"]["state"] == "inputs_unchecked"
        assert reproducibility["made_under"]["inputs_checked"] is False


def test_the_input_check_is_an_explicit_bounded_opt_in(client, db_session):
    entry, _ = _entry_with_assessed_thermos(db_session, 2)
    url = _url(entry)
    ok = client.get(f"{url}?include=assessments&check_inputs=true&limit=10")
    assert ok.status_code == 200 and ok.json()["request"]["check_inputs"] is True
    too_many = client.get(f"{url}?include=assessments&check_inputs=true&limit=11")
    assert too_many.status_code == 422 and "assessment_input_check_limit" in too_many.text
    needs_include = client.get(f"{url}?check_inputs=true")
    assert needs_include.status_code == 422 and "check_inputs_needs_assessments" in needs_include.text
    assert client.get(f"{url}?include=assessments").json()["request"]["check_inputs"] is False
