"""A PDep bundle's transition state may only cite its own calculations.

Why this file exists, and why it is about a *code* rather than a rule
---------------------------------------------------------------------
``app.services.calculation_ownership.assert_calculation_owned_by`` is one
implementation with five codes, and two of them were read as unreachable
by construction: every call site was said to read a calculation out of a
key map the same block had just built for the target's own owner, so the
comparison was against a value it had itself assigned. The rule that
verdict rested on was "an ownership guard is reachable exactly when the
field it guards accepts a foreign row id", and for
``statmech_torsion_scan_calculation_owner_mismatch`` that rule gives the
wrong answer.

``_persist_statmech_block`` is shared. Under ``/uploads/computed-species``
its calc-key map is one species entry's own, and the reading holds. Under
``/uploads/networks/pdep`` the same seam is handed a map spanning every
species *and* every transition state in the bundle
(``app.workflows.network_pdep``), which is a namespace wider than the
target's owner — the second way a guard becomes reachable, and the one
the rule did not name. The PDep payload schema narrows a **species**
statmech's source and torsion keys to that species's own calculations
before the seam ever sees them
(``NetworkPDepUploadRequest.validate_key_references``); it does not do
the same for a **transition state**'s. So a TS statmech that cites a
species-owned calculation, or a TS torsion that names a species-owned
rotor scan, reaches the guard and is refused with a code.

That is a real refusal a depositor can provoke and a client can branch
on, and until this file existed nothing in the suite produced either one
on the wire — which is why the runtime observer, whose whole job is to
hold the catalogue to what a consumer receives, had never recorded them.
Both are now provoked through the route, so the ``(status, code)`` pair
is measured rather than declared.

What these tests are *not*
--------------------------
They are not an endorsement of the asymmetry above. A TS-side key being
narrowed at the schema layer the way a species-side key already is would
be a better refusal — the same 422, one layer earlier, naming the keys
that *are* in scope. If that lands, these tests should be rewritten to
assert the new refusal deliberately, not deleted: the property being
protected is "a transition state cannot borrow another subject's
calculation", and it must keep costing a red test to break.

The assertions are on ``code``, never on substrings of ``detail``:
Pydantic echoes rejected input back into its error string, so a substring
check passes even when the field was wrongly accepted.
"""

from __future__ import annotations

from tests.workflows.test_network_pdep_upload import (
    _LOT_DFT,
    _SOFTWARE,
    _full_payload,
)

_PDEP_URL = "/api/v1/uploads/networks/pdep"

_SOURCE_OWNER_MISMATCH = "statmech_source_calculation_owner_mismatch"
_TORSION_OWNER_MISMATCH = "statmech_torsion_scan_calculation_owner_mismatch"


def _assert_code(response, expected: str) -> dict:
    """The response is a 422 whose ``code`` field is *expected*."""
    assert response.status_code == 422, response.text
    body = response.json()
    assert body.get("code") == expected, (
        f"expected code={expected!r}, got {body.get('code')!r}. "
        f"detail={body.get('detail')!r}"
    )
    return body


def _payload_with_ts_statmech_citing_a_species_calculation() -> dict:
    """A TS statmech whose ``source_calculations`` names ethyl's freq job.

    ``ethyl_freq`` is a real key in the bundle's global calc namespace and
    a real ``freq`` calculation, so nothing but ownership is wrong with
    it. That is deliberate: a payload that also had a bad role or a
    missing key could be refused for the other reason and the test would
    pass while proving nothing.
    """
    payload = _full_payload(include_solve=False)
    payload["transition_states"][0]["statmech"] = {
        "statmech_treatment": "rrho",
        "source_calculations": [
            {"calculation_key": "ethyl_freq", "role": "freq"}
        ],
    }
    return payload


def _payload_with_ts_torsion_citing_a_species_scan() -> dict:
    """A TS torsion whose rotor scan belongs to ethyl.

    The TS statmech's own source link stays correct (``ts_elim_freq``), so
    the only thing left to refuse is the torsion's scan key. The scan is
    added to ethyl rather than reused from somewhere, because the key must
    resolve to a genuine ``scan`` calculation for the ownership check to
    be what fires.
    """
    payload = _full_payload(include_solve=False)
    ethyl = next(sp for sp in payload["species"] if sp["key"] == "ethyl")
    ethyl["calculations"].append(
        {
            "key": "ethyl_scan",
            "type": "scan",
            "geometry_key": "ethyl_geom",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT_DFT,
        }
    )
    payload["transition_states"][0]["statmech"] = {
        "statmech_treatment": "rrho_1d",
        "source_calculations": [
            {"calculation_key": "ts_elim_freq", "role": "freq"}
        ],
        "torsions": [
            {"torsion_index": 1, "source_scan_calculation_key": "ethyl_scan"}
        ],
    }
    return payload


def test_ts_statmech_citing_a_species_calculation_is_refused(client) -> None:
    body = _assert_code(
        client.post(_PDEP_URL, json=_payload_with_ts_statmech_citing_a_species_calculation()),
        _SOURCE_OWNER_MISMATCH,
    )
    # The context names the field the way the depositor wrote it and the
    # kind of owner that disagreed -- never a row id (DR-0028 Req. 2).
    assert body["context"]["owner_kind"] == "transition_state_entry", body
    assert "ethyl_freq" in body["context"]["field"], body
    assert "id" not in body["context"], body


def test_ts_torsion_citing_a_species_scan_is_refused(client) -> None:
    """The code the triage recorded as unreachable, measured on the wire.

    It carries its own code rather than the statmech source-link one
    because the repair differs: the depositor picked the wrong rotor
    scan, not the wrong supporting job (#158/#162).
    """
    body = _assert_code(
        client.post(_PDEP_URL, json=_payload_with_ts_torsion_citing_a_species_scan()),
        _TORSION_OWNER_MISMATCH,
    )
    assert body["context"]["target"] == "statmech torsion", body
    assert "ethyl_scan" in body["context"]["field"], body


def test_the_same_bundle_without_the_borrowed_keys_is_accepted(client) -> None:
    """The negative half: the payload is refused for ownership and nothing else.

    Without this, both tests above would still pass if the fixture had
    drifted into being invalid for some unrelated reason and every PDep
    upload were failing at 422.
    """
    payload = _payload_with_ts_torsion_citing_a_species_scan()
    statmech = payload["transition_states"][0]["statmech"]
    statmech["torsions"] = [
        {"torsion_index": 1, "source_scan_calculation_key": None}
    ]
    resp = client.post(_PDEP_URL, json=payload)
    assert resp.status_code == 201, resp.text
