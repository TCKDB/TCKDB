"""A PDep transition state may not name a calculation nobody declared.

The 500 this file removes
-------------------------
``/uploads/networks/pdep`` hands ``_persist_statmech_block`` a calc-key
map spanning every species and every transition state in the bundle, and
``NetworkPDepUploadRequest.validate_key_references`` narrows a *species*
statmech's source and torsion keys to that species's own calculations
without doing the same for a *transition state*'s. The seam then looked
the key up with a raw subscript, so a TS statmech naming a key nothing
declared left the route as an unhandled ``KeyError``:

    E   KeyError: 'no_such_calc'
    app/workflows/computed_species.py:998: KeyError

That is a 500 whose body says nothing about which key was wrong, for a
mistake whose whole repair is spelling. It is now a 422 carrying
``calculation_key_undeclared``, the offending field, the key, and the
keys that *are* declared.

Why it is worth a file when the schema "already checks"
-------------------------------------------------------
It did not. The claim that a request schema refuses every undeclared key
before a workflow runs was true of seventeen of the nineteen lookups and
false of these two, and it was false in the way that arrangement always
fails: the schema lives in ``tckdb_schemas``, a different distributable
package from the workflow it is supposed to protect, so the two drift
with nothing going red. These two are the drift, caught.

The assertions are on ``code`` and ``context``, never on a substring of
``detail``: Pydantic echoes rejected input back into its error string, so
a substring check passes even where the field was wrongly accepted. The
last test is the one that keeps the rest honest — a resolver that refused
*everything* would satisfy every negative assertion here.
"""

from __future__ import annotations

from tests.workflows.test_network_pdep_upload import (
    _LOT_DFT,
    _SOFTWARE,
    _full_payload,
)

_PDEP_URL = "/api/v1/uploads/networks/pdep"

_KEY_UNDECLARED = "calculation_key_undeclared"


def _assert_code(response, expected: str) -> dict:
    assert response.status_code == 422, response.text
    body = response.json()
    assert body.get("code") == expected, (
        f"expected code={expected!r}, got {body.get('code')!r}. "
        f"detail={body.get('detail')!r}"
    )
    return body


def _payload_with_ts_statmech_naming_nothing() -> dict:
    payload = _full_payload(include_solve=False)
    payload["transition_states"][0]["statmech"] = {
        "statmech_treatment": "rrho",
        "source_calculations": [
            {"calculation_key": "no_such_calc", "role": "freq"}
        ],
    }
    return payload


def _payload_with_ts_torsion_naming_nothing() -> dict:
    payload = _full_payload(include_solve=False)
    payload["transition_states"][0]["statmech"] = {
        "statmech_treatment": "rrho_1d",
        "source_calculations": [
            {"calculation_key": "ts_elim_freq", "role": "freq"}
        ],
        "torsions": [
            {"torsion_index": 1, "source_scan_calculation_key": "no_such_scan"}
        ],
    }
    return payload


def test_ts_statmech_source_key_naming_nothing_is_a_coded_422(client) -> None:
    body = _assert_code(
        client.post(_PDEP_URL, json=_payload_with_ts_statmech_naming_nothing()),
        _KEY_UNDECLARED,
    )
    assert body["context"]["field"] == (
        "statmech.source_calculations[0].calculation_key"
    ), body
    assert body["context"]["key"] == "no_such_calc", body
    # The alternatives are the point: a refusal that names them turns a
    # typo into a mechanical fix rather than a guess.
    assert "ts_elim_freq" in body["context"]["declared_keys"], body
    # DR-0028 Requirement 2 -- the map's *values* are row ids and they
    # stay there.
    assert set(body["context"]) == {"field", "key", "declared_keys"}, body


def test_ts_torsion_scan_key_naming_nothing_is_a_coded_422(client) -> None:
    """Same code, different field.

    An undeclared source link and an undeclared rotor scan are one repair
    -- declare the calculation, or fix the spelling -- against one
    namespace, so they share a code and ``context['field']`` carries the
    difference. That is the opposite call from the ownership family, and
    deliberately: there, the two fields need two *different* right
    answers, so they get two codes.
    """
    body = _assert_code(
        client.post(_PDEP_URL, json=_payload_with_ts_torsion_naming_nothing()),
        _KEY_UNDECLARED,
    )
    assert body["context"]["field"] == (
        "statmech.torsions[0].source_scan_calculation_key"
    ), body
    assert body["context"]["key"] == "no_such_scan", body


def test_a_declared_ts_statmech_key_is_still_accepted(client) -> None:
    """The half that makes the two above mean anything.

    A resolver that refused every key would pass both negative tests. It
    fails this one.
    """
    payload = _payload_with_ts_statmech_naming_nothing()
    payload["transition_states"][0]["statmech"]["source_calculations"] = [
        {"calculation_key": "ts_elim_freq", "role": "freq"}
    ]
    resp = client.post(_PDEP_URL, json=payload)
    assert resp.status_code == 201, resp.text


def test_a_declared_ts_torsion_scan_key_is_still_accepted(client) -> None:
    """A real scan, owned by the transition state, resolves and persists."""
    payload = _full_payload(include_solve=False)
    ts = payload["transition_states"][0]
    ts["calculations"].append(
        {
            "key": "ts_elim_scan",
            "type": "scan",
            "geometry_key": "ts_elim_geom",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT_DFT,
        }
    )
    ts["statmech"] = {
        "statmech_treatment": "rrho_1d",
        "source_calculations": [
            {"calculation_key": "ts_elim_freq", "role": "freq"}
        ],
        "torsions": [
            {"torsion_index": 1, "source_scan_calculation_key": "ts_elim_scan"}
        ],
    }
    resp = client.post(_PDEP_URL, json=payload)
    assert resp.status_code == 201, resp.text
