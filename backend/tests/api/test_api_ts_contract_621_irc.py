"""An IRC result may leave its direction and branch flags unstated (#621).

``IRCResultPayload`` required ``direction``, ``has_forward`` and ``has_reverse``
and offered no way to say "the log does not record which way this ran". A
producer in that position had two choices, both bad: drop the whole IRC result
(and its points), or write ``false`` for a branch it simply did not know about.
The first loses data; the second turns "not stated" into the claim "there is no
such branch".

All three are now optional and stored as NULL. What these tests hold:

* an unstated value reads back as **null** and never as ``false``, on every
  surface that serves it;
* a **stated** ``false`` is still ``false``, so the two cannot be confused;
* the points survive, which is the whole reason to accept the result;
* a flag that is stated false against points that contradict it is still
  refused, because the contradiction is in what was stated.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.calculation import Calculation, CalculationIRCResult
from tests.api.test_api_ts_contract_621 import (
    _BUNDLE,
    _LOT,
    _SOFTWARE,
    _entry_ref,
    _ok,
    _standalone,
    _standalone_ok,
    _standalone_refused,
)
from tests.workflows.test_computed_reaction_upload import (
    _irc_result_block,
    _payload_with_ts_irc,
)


def _bundle_with_irc(*, forward_only: bool = False, **irc_fields) -> dict:
    """The reaction bundle with an IRC result carrying the given fields.

    ``forward_only`` drops the reverse-branch point, for a test that states
    ``has_reverse=False`` and must not contradict its own points.
    """
    block = _irc_result_block(with_points=True)
    for name in ("direction", "has_forward", "has_reverse"):
        block.pop(name, None)
    if forward_only:
        block["points"] = [p for p in block["points"] if p["direction"] != "reverse"]
    block.update(irc_fields)
    payload = _payload_with_ts_irc()
    irc_calc = next(c for c in payload["transition_state"]["calculations"] if c["key"] == "ts-irc")
    irc_calc["irc_result"] = block
    return payload


def _calc_read(client, ref: str, *includes: str) -> dict:
    response = client.get(f"/api/v1/scientific/calculations/{ref}?include={','.join(includes)}")
    assert response.status_code == 200, response.text[:800]
    return response.json()["record"]


class TestUnstatedIrcOnTheReactionBundle:
    def test_nothing_stated_reads_back_as_null_with_its_points(self, client):
        result = _ok(client.post(_BUNDLE, json=_bundle_with_irc()))
        ref = result["calculation_key_refs"]["ts-irc"]

        record = _calc_read(client, ref, "irc", "results")
        assert record["irc"]["direction"] is None
        assert record["irc"]["has_forward"] is None
        assert record["irc"]["has_reverse"] is None
        assert record["irc"]["forward_point_count"] == 1
        assert record["irc"]["reverse_point_count"] == 1
        assert record["results"]["kind"] == "irc"
        assert record["results"]["irc"]["has_forward"] is None

        points = client.get(f"/api/v1/scientific/calculations/{ref}/irc")
        assert points.status_code == 200, points.text[:800]
        body = points.json()
        assert body["irc"]["direction"] is None
        assert body["irc"]["has_forward"] is None
        assert body["irc"]["has_reverse"] is None
        assert len(body["points"]) == 3

    def test_the_reaction_entry_full_view_carries_the_null_too(self, client):
        result = _ok(client.post(_BUNDLE, json=_bundle_with_irc()))
        response = client.get(
            f"/api/v1/scientific/reaction-entries/{result['reaction_entry_id']}/full?include=irc"
        )
        assert response.status_code == 200, response.text[:800]
        (item,) = response.json()["irc"]
        assert item["summary"]["direction"] is None
        assert item["summary"]["has_forward"] is None
        assert item["summary"]["has_reverse"] is None

    def test_a_stated_false_is_kept_distinct_from_not_stated(self, client):
        result = _ok(
            client.post(
                _BUNDLE,
                json=_bundle_with_irc(
                    forward_only=True, direction="forward", has_forward=True, has_reverse=False
                ),
            )
        )
        ref = result["calculation_key_refs"]["ts-irc"]
        summary = _calc_read(client, ref, "irc")["irc"]
        assert summary["direction"] == "forward"
        assert summary["has_forward"] is True
        assert summary["has_reverse"] is False

    def test_each_value_is_independently_optional(self, client):
        result = _ok(client.post(_BUNDLE, json=_bundle_with_irc(has_forward=True)))
        summary = _calc_read(client, result["calculation_key_refs"]["ts-irc"], "irc")["irc"]
        assert summary["direction"] is None
        assert summary["has_forward"] is True
        assert summary["has_reverse"] is None

    def test_a_result_with_no_points_and_nothing_stated_is_still_a_result_row(
        self, client, db_session
    ):
        block = {"zero_energy_reference_hartree": -40.5}
        payload = _payload_with_ts_irc()
        next(c for c in payload["transition_state"]["calculations"] if c["key"] == "ts-irc")[
            "irc_result"
        ] = block
        result = _ok(client.post(_BUNDLE, json=payload))
        calc = db_session.scalars(
            select(Calculation).where(
                Calculation.transition_state_entry_id == result["transition_state_entry_id"],
                Calculation.public_ref == result["calculation_key_refs"]["ts-irc"],
            )
        ).one()
        row = db_session.get(CalculationIRCResult, calc.id)
        assert row is not None
        assert (row.direction, row.has_forward, row.has_reverse) == (None, None, None)

    def test_forward_points_against_a_stated_false_flag_are_still_refused(self, client):
        payload = _bundle_with_irc(has_forward=False)
        response = client.post(_BUNDLE, json=payload)
        assert response.status_code == 422, response.text[:800]
        assert "has_forward must be true" in str(response.json()["detail"])

    def test_forward_points_with_the_flag_left_out_are_accepted_and_not_inferred(
        self, client
    ):
        """The points say a forward branch exists; the producer did not say so.

        TCKDB records what was stated. Filling ``has_forward`` from the points
        would put a value in the record that nobody deposited.
        """
        result = _ok(client.post(_BUNDLE, json=_bundle_with_irc()))
        summary = _calc_read(client, result["calculation_key_refs"]["ts-irc"], "irc")["irc"]
        assert summary["forward_point_count"] == 1
        assert summary["has_forward"] is None


class TestUnstatedIrcOnTheStandaloneRoute:
    def _irc_calc(self, **irc_fields) -> dict:
        return {
            "type": "irc",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
            "irc_result": {
                "points": [
                    {"point_index": 0, "is_ts": True},
                    {"point_index": 1, "direction": "forward", "reaction_coordinate": 0.5},
                    {"point_index": 2, "direction": "reverse", "reaction_coordinate": -0.5},
                ],
                **irc_fields,
            },
        }

    def _stored(self, client, db_session, result: dict) -> dict:
        irc_calc = db_session.scalars(
            select(Calculation).where(
                Calculation.transition_state_entry_id == result["id"],
                Calculation.type == "irc",
            )
        ).one()
        return _calc_read(client, irc_calc.public_ref, "irc")["irc"]

    def test_an_unstated_direction_and_flags_are_accepted_and_read_as_null(
        self, client, db_session
    ):
        payload = _standalone()
        payload["additional_calculations"].append(self._irc_calc())
        summary = self._stored(client, db_session, _standalone_ok(client, payload))
        assert summary["direction"] is None
        assert summary["has_forward"] is None
        assert summary["has_reverse"] is None
        assert summary["forward_point_count"] == 1
        assert summary["reverse_point_count"] == 1

    def test_stated_values_are_kept(self, client, db_session):
        payload = _standalone()
        payload["additional_calculations"].append(
            self._irc_calc(direction="both", has_forward=True, has_reverse=True)
        )
        summary = self._stored(client, db_session, _standalone_ok(client, payload))
        assert summary["direction"] == "both"
        assert summary["has_forward"] is True
        assert summary["has_reverse"] is True

    def test_a_stated_false_against_contradicting_points_is_refused(self, client):
        payload = _standalone()
        payload["additional_calculations"].append(self._irc_calc(has_reverse=False))
        body = _standalone_refused(client, payload)
        assert "has_reverse must be true" in str(body["detail"]), body

    def test_an_unknown_direction_token_is_still_refused(self, client):
        payload = _standalone()
        payload["additional_calculations"].append(self._irc_calc(direction="sideways"))
        _standalone_refused(client, payload)

    def test_entry_read_still_serves_the_calculation_list(self, client, db_session):
        payload = _standalone()
        payload["additional_calculations"].append(self._irc_calc())
        result = _standalone_ok(client, payload)
        response = client.get(
            "/api/v1/scientific/transition-state-entries/"
            f"{_entry_ref(db_session, result['id'])}?include=calculations"
        )
        assert response.status_code == 200, response.text[:800]
        assert "irc" in {c["type"] for c in response.json()["record"]["calculations"]}
