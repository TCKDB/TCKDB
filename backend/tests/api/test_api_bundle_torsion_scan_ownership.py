"""A reaction bundle's torsion may only cite its own species' rotor scan.

What was wrong
--------------
``app.workflows.computed_reaction`` resolved a torsion's
``source_scan_calculation_key`` out of ``calculation_key_to_id`` -- the
bundle-global namespace, spanning every species and the transition state
-- and wrote the result to ``statmech_torsion.source_scan_calculation_id``
without asking who owned it. The key resolving proves only that some
subject in the bundle declared that calculation, not that the citing
species did.

So a bundle could give species A's hindered rotor species B's rotor
scan. Both the schema checks passed: the key existed, and the target was
a genuine ``scan``-type calculation. The row that came out looked
entirely well-formed, and a partition function computed from it is wrong
with nothing in the record saying so.

This is the failure the four-role split exists to catch. A torsion is a
*result*; its scan is that result's *provenance*. A rotor potential taken
from a different molecule is not weaker evidence for this one -- it is
evidence about something else, and ADR 0008 puts that in the ``block``
tier because no correct deposit can produce it.

Why these tests are on the wire
-------------------------------
The guard lives in the workflow, but what a depositor can act on is the
``(status, code)`` pair their client receives. Asserting it here measures
that pair instead of declaring it; ``statmech_torsion_scan_calculation_owner_mismatch``
was catalogued long before anything on this route could produce it.

Scope, stated rather than left to inference
-------------------------------------------
A **transition state** cannot reach this rule on this route:
``BundleTransitionStateIn`` has no ``statmech`` field, so it carries no
torsions. The TS case is reachable only through
``/uploads/networks/pdep``, where it is enforced at the same seam and
provoked by ``tests/api/test_api_network_pdep_ownership.py``. The rule in
both places is the same one, and it is the strict one: a torsion's scan
must belong to the torsion's own subject. Decided 2026-08-15 -- a
depositor who genuinely approximated one rotor with another molecule's
scan has no way to record that today, and gaining one is a schema change
that says so explicitly, not a silently accepted link.

Assertions are on ``code`` and ``context``, never on substrings of
``detail``: Pydantic echoes rejected input back into its error string, so
a substring check can pass even when the field was wrongly accepted.
"""

from __future__ import annotations

from tests.workflows.test_computed_reaction_upload import _payload_with_ch4_scan

_URL = "/api/v1/uploads/computed-reaction"
_TORSION_OWNER_MISMATCH = "statmech_torsion_scan_calculation_owner_mismatch"


def _payload_with_ch3_torsion_citing_ch4s_scan() -> dict:
    """ch3's rotor is parameterised by ch4's scan.

    ``ch4-scan`` is a real key in the bundle's global namespace and a real
    ``scan`` calculation, so the schema's two existing checks both pass
    and ownership is the only thing left wrong. That is deliberate: a
    payload that also had a bad key or a non-scan target could be refused
    for the other reason, and the test would pass while proving nothing.

    ch3's own ``source_calculations`` link stays correct so the statmech
    itself is not what is being refused.
    """
    payload = _payload_with_ch4_scan()  # scan belongs to species[2], ch4
    payload["species"][0]["statmech"] = {  # ch3
        "is_linear": False,
        "statmech_treatment": "rrho",
        "source_calculations": [{"calculation_key": "ch3-freq", "role": "freq"}],
        "torsions": [
            {
                "torsion_index": 1,
                "symmetry_number": 3,
                "treatment_kind": "hindered_rotor",
                "dimension": 1,
                "source_scan_calculation_key": "ch4-scan",
            }
        ],
    }
    return payload


def test_a_torsion_citing_another_species_scan_is_refused(client) -> None:
    resp = client.post(_URL, json=_payload_with_ch3_torsion_citing_ch4s_scan())
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == _TORSION_OWNER_MISMATCH, body
    # The context names the field the depositor wrote and the kind of
    # owner that disagreed -- never a row id (DR-0028 Requirement 2).
    assert body["context"]["target"] == "statmech torsion", body
    assert "ch4-scan" in body["context"]["field"], body
    assert body["context"]["owner_kind"] == "species_entry", body


def test_the_refusal_discloses_no_row_ids(client) -> None:
    """The offending ids go to the log; the 422 body names only keys.

    Separated from the test above because "refused with the right code"
    and "refused without leaking primary keys" are different properties,
    and a regression in the second is silent.

    The status assertion is load-bearing, not decoration. Without it a
    mutation that stops the guard firing altogether leaves a 201 whose
    body contains no ids either, and this test passes while checking
    nothing -- caught by running exactly that mutation (#192's class).
    """
    resp = client.post(_URL, json=_payload_with_ch3_torsion_citing_ch4s_scan())
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == _TORSION_OWNER_MISMATCH, body
    for value in (body.get("detail", ""), str(body.get("context", {}))):
        assert "species_entry_id=" not in value, body
        assert "calculation id" not in value, body


def test_the_same_torsion_on_its_own_species_is_accepted(client) -> None:
    """The negative half: the payload is refused for ownership, nothing else.

    Without this, the tests above would still pass if the fixture had
    drifted into being invalid for some unrelated reason and every
    reaction bundle were answering 422.
    """
    payload = _payload_with_ch3_torsion_citing_ch4s_scan()
    # Move the identical torsion onto ch4, which owns the scan.
    torsions = payload["species"][0]["statmech"].pop("torsions")
    payload["species"][2]["statmech"] = {
        "is_linear": False,
        "statmech_treatment": "rrho",
        "source_calculations": [{"calculation_key": "ch4-freq", "role": "freq"}],
        "torsions": torsions,
    }
    resp = client.post(_URL, json=payload)
    assert resp.status_code == 201, resp.text[:800]
