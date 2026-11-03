"""An electronic-state label and a term symbol must match the strict grammar, on every upload route.

Owner decision 2026-11-03: free text there let a typed ``R`` on a ground-state entry read as
"R enantiomer" on the website, and let anything at all onto a public page. The grammar lives in
``tckdb_schemas.electronic_state``; this file proves the routes enforce it, with a coded 422 that
names the field and never echoes what was sent, and that nothing is stored on a refusal.

Every refusal test has a positive control on the same payload shape (a valid label is accepted,
201), so an empty-handed pass cannot hide a route that refuses everything.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select

from app.db.models.common import SpeciesEntryStateKind
from app.db.models.species import Species, SpeciesEntry
from app.services.scientific_read.species_identity import species_entry_label
from tests.api.test_api_created_by_attribution import _computed_species_payload_with_thermo_and_statmech
from tests.api.test_api_network_reads import _pdep_payload
from tests.api.test_api_statmech_upload import _statmech_payload
from tests.api.test_api_transport_upload import _transport_payload
from tests.api.test_api_uploads import (
    _hydrogen_conformer_payload,
    _reaction_payload,
    _transition_state_payload,
)

ARC = Path(__file__).resolve().parents[1] / "fixtures" / "arc_runs"

CODES = {
    "electronic_state_label": "electronic_state_label_invalid",
    "term_symbol": "term_symbol_invalid",
}
#: One valid and one invalid value per field. ``R`` is the reviewer's case: a stereo-looking state label.
VALID = {"electronic_state_label": "T1", "term_symbol": "X 2S"}
INVALID = {"electronic_state_label": "R", "term_symbol": "ground state"}


def _thermo() -> dict:
    return {
        "enthalpy_reference_kind": "formation_298k",
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "scientific_origin": "computed",
        "h298_kj_mol": 217.998,
    }


def _computed_reaction() -> dict:
    root = ARC / "rotor_scan_10" / "tckdb_payloads" / "computed_reaction"
    (path,) = sorted(root.glob("*.payload.json"))
    return json.loads(path.read_text())


#: route -> (payload builder, how to reach the species_entry dict inside that payload)
ROUTES = {
    "conformers": (_hydrogen_conformer_payload, lambda p: p["species_entry"]),
    "reactions": (_reaction_payload, lambda p: p["reactants"][0]["species_entry"]),
    "thermo": (_thermo, lambda p: p["species_entry"]),
    "statmech": (_statmech_payload, lambda p: p["species_entry"]),
    "transport": (_transport_payload, lambda p: p["species_entry"]),
    "transition-states": (_transition_state_payload, lambda p: p["reaction"]["reactants"][0]["species_entry"]),
    "networks/pdep": (_pdep_payload, lambda p: p["species"][0]["species_entry"]),
    "computed-species": (
        _computed_species_payload_with_thermo_and_statmech,
        lambda p: p["species_entry"],
    ),
    "computed-reaction": (_computed_reaction, lambda p: p["species"][0]["species_entry"]),
}


def _post(client, route: str, field: str, value: str | None):
    build, reach = ROUTES[route]
    payload = copy.deepcopy(build())
    if value is not None:
        reach(payload)[field] = value
    return client.post(f"/api/v1/uploads/{route}", json=payload)


def _stored(db_session) -> tuple[int, int]:
    return (
        db_session.scalar(select(func.count()).select_from(Species)),
        db_session.scalar(select(func.count()).select_from(SpeciesEntry)),
    )


@pytest.mark.parametrize("field", list(CODES))
@pytest.mark.parametrize("route", list(ROUTES))
def test_a_valid_value_is_accepted_on_the_route(client, route, field) -> None:
    """The positive control: the same payload with an accepted value is stored."""
    response = _post(client, route, field, VALID[field])
    assert response.status_code == 201, response.text[:800]


@pytest.mark.parametrize("field", list(CODES))
@pytest.mark.parametrize("route", list(ROUTES))
def test_an_invalid_value_is_refused_with_the_field_code_and_nothing_is_stored(
    client, db_session, route, field
) -> None:
    before = _stored(db_session)
    response = _post(client, route, field, INVALID[field])
    assert response.status_code == 422, response.text[:800]
    body = response.json()
    assert body["code"] == CODES[field], body
    assert body["context"]["field"] == field
    assert body["context"]["accepted_shapes"]
    # never echoes more than needed: neither the value nor an id appears in the response
    assert INVALID[field] not in json.dumps(body["context"])
    assert INVALID[field] not in body["detail"]
    assert _stored(db_session) == before


@pytest.mark.parametrize("field", list(CODES))
def test_an_over_long_value_gets_the_same_coded_refusal(client, field) -> None:
    response = _post(client, "thermo", field, "X" * 200)
    assert response.status_code == 422
    assert response.json()["code"] == CODES[field]


@pytest.mark.parametrize("token", ["R", "S", "E", "Z", "r", "s", "M", "P", "Ra", "Sa", "?", "-"])
def test_a_stereo_looking_state_label_is_refused_end_to_end(client, token) -> None:
    """The reviewer's case: a typed state label ``R`` on a ground-state entry."""
    response = _post(client, "thermo", "electronic_state_label", token)
    assert response.status_code == 422
    assert response.json()["code"] == "electronic_state_label_invalid"


def test_the_term_symbol_raw_is_not_checked(client) -> None:
    """Kept verbatim for provenance (the CCCBDB importer writes prose into it), never displayed."""
    payload = _thermo()
    payload["species_entry"]["term_symbol_raw"] = "X 1A1g (planar, D6h)"
    assert client.post("/api/v1/uploads/thermo", json=payload).status_code == 201


def _models_with(field: str) -> set[type[BaseModel]]:
    found: set[type[BaseModel]] = set()
    stack = list(BaseModel.__subclasses__())
    while stack:
        cls = stack.pop()
        stack.extend(cls.__subclasses__())
        if field in getattr(cls, "model_fields", {}) and cls.__module__.split(".")[0] in {"app", "tckdb_schemas"}:
            found.add(cls)
    return found


#: Models that only describe what is stored. They must keep rendering data stored before the rule.
_READ_ONLY = ("Read", "Summary", "Chemistry")


@pytest.mark.parametrize("field", list(CODES))
def test_every_schema_that_accepts_the_field_validates_it(field) -> None:
    """No entry path is left over: a write model gaining the field without the check fails here."""
    models = {
        m
        for m in _models_with(field)
        if ".reads" not in m.__module__ and not m.__name__.endswith(_READ_ONLY)
    }
    assert {m.__name__ for m in models} >= {"SpeciesEntryIdentityPayload", "SpeciesEntryCreate", "SpeciesEntryUpdate"}
    for model in models:
        with pytest.raises(ValidationError) as caught:
            model.model_validate({field: "ground state"})
        codes = {getattr(e.get("ctx", {}).get("error"), "code", None) for e in caught.value.errors()}
        assert CODES[field] in codes, model


@pytest.mark.parametrize(
    ("stereo", "kind", "label", "expected"),
    [
        (None, SpeciesEntryStateKind.ground, "R", "state R"),
        ("R", SpeciesEntryStateKind.ground, "S", "R state S"),
        (None, SpeciesEntryStateKind.excited, "A", "excited state A"),
        ("Z", SpeciesEntryStateKind.ground, None, "Z"),
    ],
)
def test_a_state_label_in_a_joined_label_is_marked_so_it_cannot_read_as_stereo(stereo, kind, label, expected) -> None:
    """Rows stored before the grammar can hold ``R``; the joined label must not read as "R enantiomer"."""
    joined = species_entry_label(
        stereo_label=stereo,
        electronic_state_kind=kind,
        electronic_state_label=label,
        term_symbol=None,
        isotope_key=None,
    )
    assert joined == expected
