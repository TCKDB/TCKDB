"""Three reaction-bundle ownership refusals, measured on the wire.

What was wrong
--------------
``app.workflows.computed_reaction`` held three inline copies of the
owner-consistency comparison the rest of the backend routes through
``app.services.calculation_ownership``. Each raised a bare ``ValueError``,
so each arrived at a depositor as ``code="validation_error"`` -- the same
answer a missing required field gets. A client could not branch on them,
and a depositor could not tell "you cited the transition state's job for a
species correction" from "your enthalpy is the wrong type".

All three resolve ``calculation_key`` through ``calculation_key_to_id``,
the bundle-global namespace spanning every species *and* the transition
state, and then attach the result to one specific entry. That is the
second clause of the reachability rule (#173): a key resolved in a
namespace wider than the target's owner makes the guard reachable even
though no payload here carries an ``existing_*_id``. Two of the three
produce ``applied_energy_correction_source_calculation_owner_mismatch``,
which was catalogued ``Reach.guard`` on exactly that misreading until
these tests measured it.

Why these tests are on the wire
-------------------------------
The guards live in the workflow and are covered there
(``tests/workflows/test_computed_reaction_upload.py``), but what a
depositor can act on is the ``(status, code)`` pair their client receives,
and the workflow tests cannot see whether the envelope preserved it.

Assertions are on ``code`` and ``context``, never on substrings of
``detail``: Pydantic echoes rejected input back into its error string, so
a substring check can pass even when the field was wrongly accepted. Each
refusal is paired with the payload that should be *accepted*, because a
guard that refuses everything satisfies a 422 assertion perfectly.
"""

from __future__ import annotations

from tests.workflows.test_computed_reaction_upload import (
    _aec_scheme_ref_rxn,
    _minimal_payload,
    _payload_with_aec_carriers,
)

_URL = "/api/v1/uploads/computed-reaction"
_AEC_OWNER_MISMATCH = "applied_energy_correction_source_calculation_owner_mismatch"
_STATMECH_OWNER_MISMATCH = "statmech_source_calculation_owner_mismatch"


def _correction(source_calculation_key: str) -> dict:
    return {
        "scheme": _aec_scheme_ref_rxn(),
        "application_role": "aec_total",
        "value": -0.1,
        "value_unit": "hartree",
        "source_calculation_key": source_calculation_key,
    }


def test_a_species_correction_citing_the_ts_job_is_coded(client) -> None:
    """``ts-sp`` is a real key in the bundle and a real ``sp`` calculation.

    Deliberately so: a payload that also named an undeclared key would be
    refused for that instead, and the test would pass while proving
    nothing about ownership.
    """
    payload = _payload_with_aec_carriers()
    payload["species"][0]["applied_energy_corrections"] = [_correction("ts-sp")]

    resp = client.post(_URL, json=payload)
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == _AEC_OWNER_MISMATCH, body
    assert body["context"]["owner_kind"] == "species_entry", body
    assert "ts-sp" in body["context"]["field"], body
    assert body["context"]["target"] == "applied energy correction", body


def test_a_ts_correction_citing_a_species_job_is_coded(client) -> None:
    """The transition-state half: same code, different ``owner_kind``."""
    payload = _payload_with_aec_carriers()
    payload["transition_state"]["applied_energy_corrections"] = [
        _correction("ch3-sp")
    ]

    resp = client.post(_URL, json=payload)
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == _AEC_OWNER_MISMATCH, body
    assert body["context"]["owner_kind"] == "transition_state_entry", body
    assert "ch3-sp" in body["context"]["field"], body


def test_a_correction_citing_its_own_subjects_job_is_accepted(client) -> None:
    """The negative half of both tests above, in one request.

    Each correction cites a calculation its own target owns. Without
    this, the two 422 assertions would still pass if the AEC fixture had
    drifted into being invalid for an unrelated reason.
    """
    payload = _payload_with_aec_carriers()
    payload["species"][0]["applied_energy_corrections"] = [_correction("ch3-sp")]
    payload["transition_state"]["applied_energy_corrections"] = [
        _correction("ts-sp")
    ]

    resp = client.post(_URL, json=payload)
    assert resp.status_code == 201, resp.text[:800]


def test_a_statmech_citing_a_sibling_species_job_is_coded(client) -> None:
    """The third inline copy, reporting the code its three siblings do.

    ``statmech_source_calculation_owner_mismatch`` was already emitted by
    the statmech seam, the species bundle and the standalone statmech
    upload; the reaction bundle -- the path ARC deposits through -- was
    the one that answered ``validation_error``.
    """
    payload = _minimal_payload()
    payload["species"][0]["statmech"] = {
        "is_linear": False,
        "statmech_treatment": "rrho",
        "source_calculations": [
            {"calculation_key": "ch3-freq", "role": "freq"},
            {"calculation_key": "h-sp", "role": "sp"},  # owned by species 'h'
        ],
    }

    resp = client.post(_URL, json=payload)
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == _STATMECH_OWNER_MISMATCH, body
    assert "h-sp" in body["context"]["field"], body
    assert body["context"]["target"] == "statmech", body


def test_a_statmech_citing_the_ts_job_reports_the_same_code(client) -> None:
    """The TS-owned variant, which used to get its own sentence.

    Before #195 this site chose between "owned by a transition state" and
    "owned by a different species entry" in the message. Neither was a
    code, and the shared guard states the one thing that matters -- the
    calculation is not this species entry's -- so both now report the same
    contract and ``field`` is what tells them apart.
    """
    payload = _minimal_payload()
    payload["species"][0]["statmech"] = {
        "is_linear": False,
        "statmech_treatment": "rrho",
        "source_calculations": [{"calculation_key": "ts-freq", "role": "freq"}],
    }

    resp = client.post(_URL, json=payload)
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == _STATMECH_OWNER_MISMATCH, body
    assert "ts-freq" in body["context"]["field"], body


def test_a_statmech_citing_its_own_species_job_is_accepted(client) -> None:
    """The negative half of the two statmech tests."""
    payload = _minimal_payload()
    payload["species"][0]["statmech"] = {
        "is_linear": False,
        "statmech_treatment": "rrho",
        "source_calculations": [{"calculation_key": "ch3-freq", "role": "freq"}],
    }

    resp = client.post(_URL, json=payload)
    assert resp.status_code == 201, resp.text[:800]


def test_no_ownership_refusal_discloses_a_row_id(client) -> None:
    """The offending ids go to the log; the 422 body names only keys.

    Separate from the tests above because "refused with the right code"
    and "refused without handing out primary keys" are different
    properties and a regression in the second is silent. The status and
    code assertions inside the loop are load-bearing: without them a
    mutation that stops a guard firing leaves a 201 whose body contains no
    ids either, and this test passes while checking nothing.
    """
    species_aec = _payload_with_aec_carriers()
    species_aec["species"][0]["applied_energy_corrections"] = [
        _correction("ts-sp")
    ]
    ts_aec = _payload_with_aec_carriers()
    ts_aec["transition_state"]["applied_energy_corrections"] = [
        _correction("ch3-sp")
    ]
    statmech = _minimal_payload()
    statmech["species"][0]["statmech"] = {
        "is_linear": False,
        "statmech_treatment": "rrho",
        "source_calculations": [{"calculation_key": "ts-freq", "role": "freq"}],
    }

    cases = [
        (species_aec, _AEC_OWNER_MISMATCH),
        (ts_aec, _AEC_OWNER_MISMATCH),
        (statmech, _STATMECH_OWNER_MISMATCH),
    ]
    assert len(cases) == 3, "the three reaction-bundle sites #195 coded"
    for payload, code in cases:
        resp = client.post(_URL, json=payload)
        assert resp.status_code == 422, resp.text[:800]
        body = resp.json()
        assert body["code"] == code, body
        for value in (str(body.get("detail", "")), str(body.get("context", {}))):
            assert "species_entry_id=" not in value, body
            assert "transition_state_entry_id=" not in value, body
            assert "calculation id" not in value, body
