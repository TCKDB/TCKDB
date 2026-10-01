"""Review fixes for #621: finite values, comparable sources, mixed levels.

Over the real endpoints, on the reaction bundle (which has the species
calculations an ordering needs). The imaginary-mode-against-stored-result rules
are in ``tests/services/test_ts_imaginary_mode_against_freq.py``, where the
stored result can be shaped exactly.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from tests.api.test_api_ts_contract_621 import (
    _BUNDLE,
    _bundle,
    _codes,
    _energy,
    _energy_ordering,
    _imaginary_mode,
    _ok,
    _post_bundle,
)

_MIXED = "transition_state_energy_ordering_mixed_levels"


def _raw_post(client, payload: dict):
    """POST with ``Infinity``/``NaN`` literals, which ``httpx`` will not serialise."""
    return client.post(
        _BUNDLE,
        content=json.dumps(payload),
        headers={"content-type": "application/json"},
    )


def _with_energy(index: int, value) -> dict:
    record = _energy_ordering()
    record["energies"][index] = dict(record["energies"][index], energy_hartree=value)
    return _bundle([record])


class TestNonFiniteAndImplausibleValues:
    @pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
    def test_a_non_finite_energy_is_refused(self, client, value):
        # Index 0 is the saddle point, 1 a reactant.
        for index in (0, 1):
            response = _raw_post(client, _with_energy(index, value))
            assert response.status_code == 422, (index, value, response.text[:500])

    @pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
    def test_a_non_finite_imaginary_frequency_is_refused(self, client, value):
        payload = _bundle([_imaginary_mode(imaginary_frequency_cm1=value)])
        response = _raw_post(client, payload)
        assert response.status_code == 422, (value, response.text[:500])

    @pytest.mark.parametrize("value", [0.5, 40.2])
    def test_a_positive_energy_is_refused(self, client, value):
        """Absolute energies are not positive; a positive one is a slip.

        Sent on a record marked failed, so the ordering rule cannot be the
        thing that refuses it: a positive reactant energy would also make a
        *passing* record contradict itself, and that would satisfy this test
        with the rule under test deleted.
        """
        record = _energy_ordering(passed=False)
        record["energies"][1] = dict(record["energies"][1], energy_hartree=value)
        response = _post_bundle(client, _bundle([record]))
        assert response.status_code == 422, (value, response.text[:500])
        assert "energy_hartree" in str(response.json()["detail"]), response.json()

    def test_the_database_refuses_what_the_schema_would_have(self, db_session, client):
        """The CHECKs stand on their own, for a writer that bypasses the schema."""
        result = _ok(_post_bundle(client, _bundle([_energy_ordering(), _imaginary_mode()])))
        evidence_id = db_session.execute(
            text(
                "SELECT id FROM transition_state_validation_evidence "
                "WHERE kind = 'energy_ordering' AND transition_state_entry_id = :e"
            ),
            {"e": result["transition_state_entry_id"]},
        ).scalar_one()
        calc_id = db_session.execute(
            text("SELECT source_calculation_id FROM transition_state_validation_energy LIMIT 1")
        ).scalar_one()
        for bad in ("'NaN'::float8", "'Infinity'::float8", "'-Infinity'::float8", "1.5"):
            with pytest.raises(Exception), db_session.begin_nested():
                db_session.execute(
                    text(
                        "INSERT INTO transition_state_validation_energy "
                        "(evidence_id, participant, energy_kind, energy_hartree, source_calculation_id) "
                        f"VALUES (:i, 'reactant:9', 'e0', {bad}, :c)"
                    ),
                    {"i": evidence_id, "c": calc_id},
                )
        for bad in ("'NaN'::float8", "'-Infinity'::float8", "1.0"):
            with pytest.raises(Exception), db_session.begin_nested():
                db_session.execute(
                    text(
                        "UPDATE transition_state_validation_evidence "
                        f"SET imaginary_frequency_cm1 = {bad} WHERE kind = 'imaginary_mode'"
                    )
                )


class TestSourcesAreTheKindOfEnergyTheyClaim:
    def _refused(self, client, record) -> dict:
        response = _post_bundle(client, _bundle([record]))
        assert response.status_code == 422, response.text[:800]
        return response.json()

    def test_a_saddle_point_energy_from_an_irc_calculation_is_refused(self, client):
        record = _energy_ordering()
        record["energies"][0] = _energy("ts", "electronic", -40.2, "ts-irc")
        body = self._refused(client, record)
        assert "from a 'irc' calculation" in str(body["detail"]), body

    def test_an_e0_from_a_single_point_is_refused(self, client):
        record = _energy_ordering()
        record["energies"][4] = _energy("ts", "e0", -40.18, "ts-sp")
        body = self._refused(client, record)
        assert "'e0' energy from a 'sp' calculation" in str(body["detail"]), body

    def test_an_electronic_energy_from_a_freq_calculation_is_refused(self, client):
        record = _energy_ordering()
        record["energies"][0] = _energy("ts", "electronic", -40.2, "ts-freq")
        self._refused(client, record)

    def test_an_electronic_energy_from_an_opt_is_accepted(self, client):
        record = _energy_ordering()
        record["energies"][1] = _energy("reactant:1", "electronic", -39.75, "ch3-opt")
        _ok(_post_bundle(client, _bundle([record])))


class TestMixedLevelsWarn:
    def test_one_level_per_kind_raises_nothing(self, client):
        result = _ok(_post_bundle(client, _bundle([_energy_ordering()])))
        assert _MIXED not in _codes(result)

    def test_a_reactant_at_another_level_warns_but_deposits(self, client):
        record = _energy_ordering()
        # ch3-opt is wB97XD/def2-TZVP; the other electronic energies are CCSD(T).
        record["energies"][1] = _energy("reactant:1", "electronic", -39.75, "ch3-opt")
        result = _ok(_post_bundle(client, _bundle([record])))
        (warning,) = [w for w in result["warnings"] if w["code"] == _MIXED]
        assert warning["field"] == "transition_state.validation_evidence[0].energies"
        assert "'electronic'" in warning["message"]

    def test_the_two_kinds_are_judged_separately(self, client):
        """An E0 group at DFT and an electronic group at CCSD(T) is not mixing."""
        result = _ok(_post_bundle(client, _bundle([_energy_ordering()])))
        assert _MIXED not in _codes(result)


class TestTheBareProton:
    def test_a_zero_energy_participant_can_be_part_of_a_passing_ordering(self, client):
        """``[H+]`` has exactly zero energy, so zero must be depositable.

        The saddle point is placed above the (zero-containing) reactant sum so
        the ordering rule has nothing to object to.
        """
        record = _energy_ordering()
        record["energies"][0] = _energy("ts", "electronic", -39.5, "ts-sp")
        record["energies"][2] = _energy("reactant:2", "electronic", 0.0, "h-sp")
        _ok(_post_bundle(client, _bundle([record])))

    def test_the_database_stores_it(self, db_session, client):
        record = _energy_ordering()
        record["energies"][0] = _energy("ts", "electronic", -39.5, "ts-sp")
        record["energies"][2] = _energy("reactant:2", "electronic", 0.0, "h-sp")
        _ok(_post_bundle(client, _bundle([record])))
        stored = db_session.execute(
            text(
                "SELECT energy_hartree FROM transition_state_validation_energy "
                "WHERE participant = 'reactant:2' AND energy_kind = 'electronic'"
            )
        ).scalar_one()
        assert stored == 0.0
