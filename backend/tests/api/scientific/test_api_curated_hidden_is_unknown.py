"""The shared curated-floor 404 on a detail read: a hidden record answers exactly as a ref that names nothing.

``_enforce_curated_floor`` (``scientific_read/handles.py``) is the one function every detail-by-ref read passes through.
It used to drop the ref from its 404 while an unknown ref's 404 echoed it, so the detail text told the two apart and
"hidden" was an existence oracle. This pins the echo on a route that is not statmech's, by comparing whole bodies.
"""

from __future__ import annotations

import json

from app.db.models.common import CalculationType
from tests.services.scientific_read._factories import make_calculation, make_species, make_species_entry

API = "/api/v1/scientific"
GHOST = "a" * 26


def _body(response, ref: str) -> tuple[int, object]:
    return response.status_code, json.loads(response.text.replace(ref, "<ref>"))


def test_a_hidden_calculation_answers_exactly_as_an_unknown_one_ref_included(client, db_session):
    entry = make_species_entry(db_session, species=make_species(db_session, smiles="CCO"))
    hidden = make_calculation(db_session, type=CalculationType.opt, species_entry_id=entry.id)  # never reviewed
    assert client.get(f"{API}/calculations/{hidden.public_ref}").status_code == 200  # exploratory: addressable
    got = client.get(f"{API}/calculations/{hidden.public_ref}", params={"profile": "curated"})
    unknown = client.get(f"{API}/calculations/calc_{GHOST}", params={"profile": "curated"})
    assert got.status_code == 404
    assert _body(got, hidden.public_ref) == _body(unknown, f"calc_{GHOST}")
    assert hidden.public_ref in got.text  # the caller's own ref is echoed, as an unknown ref's 404 echoes it
