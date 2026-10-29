"""Reproduction (phase 1): a deposit-time warning reaches the depositor and no scientific read.

A transition state is deposited with no IRC evidence. The upload response carries
``transition_state_missing_irc_evidence``; the scientific reads of the transition
state entry, the transition state concept, its frequency calculation and the
kinetics citing it carry no warning of any kind. This pins *today's* behaviour
(no read schema carries deposit-time warnings); the feature flips it.
"""

from __future__ import annotations

import json

from app.db.models.kinetics import Kinetics
from app.db.models.reaction import ReactionEntry
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from tests.api.test_api_ts_contract_621 import _bundle, _codes, _ok, _post_bundle

_MISSING_IRC = "transition_state_missing_irc_evidence"
_ALL = "trust,results,statmech,validation_evidence,evidence,findings,assessments"


def _get(client, url: str) -> dict:
    response = client.get(url)
    assert response.status_code == 200, (url, response.status_code, response.text[:600])
    return response.json()


def _mentions_warning(node: object) -> list[str]:
    """Every key or string value anywhere in ``node`` that names a warning or the code."""
    hits: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if "warning" in key.lower():
                hits.append(f"key:{key}")
            hits.extend(_mentions_warning(value))
    elif isinstance(node, list):
        for value in node:
            hits.extend(_mentions_warning(value))
    elif isinstance(node, str) and (_MISSING_IRC in node or "warning" in node.lower()):
        hits.append(f"value:{node[:80]}")
    return hits


def test_repro_irc_missing_warning_is_in_the_response_but_on_no_read(client, db_session):
    result = _ok(_post_bundle(client, _bundle([])))
    print("\nUPLOAD RESPONSE warnings:")
    print(json.dumps([w for w in result["warnings"] if w["code"] == _MISSING_IRC], indent=2))
    assert _MISSING_IRC in _codes(result)

    tse = db_session.get(TransitionStateEntry, result["transition_state_entry_id"])
    ts = db_session.get(TransitionState, tse.transition_state_id)
    rxe = db_session.get(ReactionEntry, result["reaction_entry_id"])
    kin = db_session.get(Kinetics, result["kinetics_ids"][0])
    calc_ref = result["calculation_key_refs"]["ts-freq"]
    print("refs:", tse.public_ref, ts.public_ref, rxe.public_ref, kin.public_ref, calc_ref)

    reads = {
        "transition-state-entry default": f"/api/v1/scientific/transition-state-entries/{tse.public_ref}",
        "transition-state-entry include=trust,validation_evidence": (
            f"/api/v1/scientific/transition-state-entries/{tse.public_ref}"
            "?include=trust,validation_evidence"
        ),
        "transition-state concept default": f"/api/v1/scientific/transition-states/{ts.public_ref}",
        "calculation default": f"/api/v1/scientific/calculations/{calc_ref}",
        "calculation include=results,trust": f"/api/v1/scientific/calculations/{calc_ref}?include=results,trust",
        "kinetics of reaction entry default": f"/api/v1/scientific/reaction-entries/{rxe.public_ref}/kinetics",
        "kinetics of reaction entry include=trust": (
            f"/api/v1/scientific/reaction-entries/{rxe.public_ref}/kinetics?include=trust"
        ),
    }
    for label, url in reads.items():
        response = client.get(url)
        if response.status_code != 200:
            print(f"READ {label}: HTTP {response.status_code} {response.text[:200]}")
            continue
        body = response.json()
        hits = _mentions_warning(body)
        rec = body.get("record", {})
        for k in ("validation", "evidence_summary", "trust"):
            if k in rec:
                print(f"     {k} = {json.dumps(rec[k], default=str)[:700]}")
        print(f"READ {label}: HTTP 200, top-level record keys={sorted(body.get('record', body))[:40]}")
        print(f"     warning-ish keys/values found: {hits}")
        # The only mention allowed today is the evidence *row* kind, never a warning.
        assert not [h for h in hits if _MISSING_IRC in h or h.startswith("key:warning")], (label, hits)
