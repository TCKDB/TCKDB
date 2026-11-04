"""What ``include=parameters`` costs on the calculation read, with ``effective_settings`` riding on it.

``effective_settings`` is computed from the parameter rows, the output-log presence and the warnings of the calculation
the read already holds. Before the settings were batched it cost five statements over a plain read; reloading the
calculation, its level of theory, release and software, and always asking for warnings, took it to eight.
"""

from __future__ import annotations

from sqlalchemy import event

from app.db.models.common import CalculationType
from tests.services.scientific_read._factories import (
    make_calculation,
    make_lot,
    make_software_release,
    make_species,
    make_species_entry,
    next_inchi_key,
)

#: The figure measured before the batching refactor (15e5a1bd): five statements over a read without the include.
_PREVIOUS_MARGINAL_COST = 5


def _statements(db_session, run) -> int:
    count = 0
    engine = db_session.connection().engine

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        nonlocal count
        count += 1

    try:
        run()
    finally:
        event.remove(engine, "before_cursor_execute", _before)
    return count


def test_include_parameters_costs_no_more_than_it_did_before_the_settings_were_batched(client, db_session):
    entry = make_species_entry(db_session, make_species(db_session, inchi_key=next_inchi_key("CPC")))
    release = make_software_release(db_session, name="orca", version="6.0.0")
    calc = make_calculation(
        db_session,
        type=CalculationType.opt,
        species_entry_id=entry.id,
        lot_id=make_lot(db_session, method="b3lyp", basis="def2tzvp").id,
        software_release_id=release.id,
    )
    url = f"/api/v1/scientific/calculations/{calc.public_ref}"
    # Warm anything cached per process so the comparison is of the include, not of first use.
    assert client.get(url, params={"include": "parameters"}).status_code == 200

    without = _statements(db_session, lambda: client.get(url))
    with_parameters = _statements(db_session, lambda: client.get(url, params={"include": "parameters"}))
    assert with_parameters - without > 0, "the include did nothing; the comparison would prove nothing"
    assert with_parameters - without <= _PREVIOUS_MARGINAL_COST, (
        f"include=parameters costs {with_parameters - without} extra statements; it cost {_PREVIOUS_MARGINAL_COST} "
        "before the effective settings were batched"
    )
