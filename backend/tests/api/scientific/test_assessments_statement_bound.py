"""``include=assessments`` costs a bounded number of statements, whatever the number of records on the page.

Re-evaluating every stored reproducibility assessment's inputs is tens of statements per record, and every public species
page asks for the assessments of all of its thermo records. A response holding more than ``MAX_INPUT_CHECKS`` records
compares the rubric name and version only and says so (``made_under.inputs_checked`` false).
"""

from __future__ import annotations

from sqlalchemy import event

from app.db.models.common import SubmissionRecordType
from app.services.reproducibility_rubric import evaluate_and_append_reproducibility
from tests.services.scientific_read._factories import make_species, make_species_entry, make_thermo_scalar


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
    for i in range(n):
        thermo = make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-10.0 - i)
        evaluate_and_append_reproducibility(db_session, record_type=SubmissionRecordType.thermo, record_id=thermo.id)
    return entry


def _overhead(client, db_session, n: int) -> int:
    entry = _entry_with_assessed_thermos(db_session, n)
    url = f"/api/v1/scientific/species-entries/{entry.public_ref}/thermo"
    client.get(url)  # warm caches that are not about assessments
    _, without = _statements(db_session, lambda: client.get(url))
    response, with_assessments = _statements(db_session, lambda: client.get(f"{url}?include=assessments"))
    assert response.status_code == 200, response.text
    assert len(response.json()["records"]) == n
    return with_assessments - without


def test_assessment_statements_do_not_grow_with_the_number_of_records(client, db_session):
    small = _overhead(client, db_session, 2)
    large = _overhead(client, db_session, 10)
    # A fixed number of grouped statements, however many records: the 8 extra records add at most a couple.
    assert large - small <= 2, f"assessments cost {small} statements for 2 records and {large} for 10"


def test_a_list_says_its_inputs_were_not_checked_and_a_detail_read_checks_them(client, db_session):
    entry = _entry_with_assessed_thermos(db_session, 3)
    records = client.get(
        f"/api/v1/scientific/species-entries/{entry.public_ref}/thermo?include=assessments"
    ).json()["records"]
    for record in records:
        made_under = record["assessments"]["reproducibility"]["made_under"]
        assert made_under["state"] == "current" and made_under["inputs_checked"] is False
    single = _entry_with_assessed_thermos(db_session, 1)
    only = client.get(
        f"/api/v1/scientific/species-entries/{single.public_ref}/thermo?include=assessments"
    ).json()["records"][0]
    assert only["assessments"]["reproducibility"]["made_under"]["inputs_checked"] is True
