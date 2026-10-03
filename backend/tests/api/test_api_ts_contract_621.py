"""Transition-state contract additions (#621), through the real upload endpoints.

Four additions, each deposited over HTTP and read back over HTTP, because the
property under test is the round trip: what a producer writes is what a reader
sees, and a refusal is a refusal the producer's client receives.

1. **TS statmech on the reaction bundle.** ``transition_state.statmech`` goes
   through the same persistence seam a species' statmech does, so the rules that
   govern one govern the other.
2. **Two more evidence kinds**, ``energy_ordering`` and ``imaginary_mode``, at
   most one record per kind. Neither stands in for the IRC: the
   ``transition_state_missing_irc_evidence`` warning is still raised until a
   passing ``irc`` record is deposited.
3. **Standalone TS route gaps**: scans, ``applied_energy_corrections`` and an
   ``atom_map``.
4. **IRC direction and branch flags may be unstated.**

Assertions are on ``code`` and ``context`` where the refusal has one, and on
the status code otherwise; never on a substring of ``detail`` alone, because
Pydantic echoes rejected input back into its error string and a substring check
can pass while the field was wrongly accepted.
"""

from __future__ import annotations

from app.db.models.transition_state import TransitionStateEntry
from tests.workflows.test_computed_reaction_upload import (
    _ch4_scan_result_payload,
    _payload_with_ts_irc,
)

_BUNDLE = "/api/v1/uploads/computed-reaction"
_STANDALONE = "/api/v1/uploads/transition-states"
_MISSING_IRC = "transition_state_missing_irc_evidence"

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "wb97xd", "basis": "def2tzvp"}

_XYZ_H = "1\nH\nH 0.0 0.0 0.0"
_XYZ_CH3 = (
    "4\nmethyl\n"
    "C  0.000  0.000  0.000\n"
    "H  1.080  0.000  0.000\n"
    "H -0.540  0.935  0.000\n"
    "H -0.540 -0.935  0.000"
)
_XYZ_CH4 = (
    "5\nmethane\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.629 -0.629 -0.629"
)
_XYZ_TS = (
    "5\nTS for CH3 + H -> CH4\n"
    "C  0.000  0.000  0.000\n"
    "H  0.629  0.629  0.629\n"
    "H -0.629 -0.629  0.629\n"
    "H -0.629  0.629 -0.629\n"
    "H  0.000  0.000  1.400"
)


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def _energy(participant: str, kind: str, value: float, key: str) -> dict:
    return {
        "participant": participant,
        "energy_kind": kind,
        "energy_hartree": value,
        "source_calculation_key": key,
    }


def _energy_ordering(**overrides) -> dict:
    """A passing ordering: the saddle point above the reactant sum and the product.

    Electronic energies come from the single points, E0s from the frequency
    calculations, and each group satisfies ``ts > sum(reactants)`` and
    ``ts > product`` so a refusal below is attributable to what the test
    changed and not to the fixture.
    """
    record: dict = {
        "kind": "energy_ordering",
        "passed": True,
        "rationale": "TS above both wells at electronic and E0 level",
        "energies": [
            _energy("ts", "electronic", -40.20, "ts-sp"),
            _energy("reactant:1", "electronic", -39.75, "ch3-sp"),
            _energy("reactant:2", "electronic", -0.50, "h-sp"),
            _energy("product:1", "electronic", -40.50, "ch4-sp"),
            _energy("ts", "e0", -40.18, "ts-freq"),
            _energy("reactant:1", "e0", -39.70, "ch3-freq"),
            _energy("reactant:2", "e0", -0.49, "h-freq"),
            _energy("product:1", "e0", -40.45, "ch4-freq"),
        ],
    }
    record.update(overrides)
    return record


def _imaginary_mode(**overrides) -> dict:
    record: dict = {
        "kind": "imaginary_mode",
        "passed": True,
        "rationale": "One imaginary mode along the reaction coordinate",
        "source_calculation_key": "ts-freq",
        "imaginary_frequency_count": 1,
        "imaginary_frequency_cm1": -1500.0,
        "mode_displacement_agrees": True,
    }
    record.update(overrides)
    return record


def _irc_evidence(**overrides) -> dict:
    record: dict = {
        "kind": "irc",
        "passed": True,
        "rationale": "IRC reaches both wells",
        "source_calculation_key": "ts-irc",
    }
    record.update(overrides)
    return record


#: The energies ``_energy_ordering`` states, as the cited calculations store them
#: (issue #638): an ordering is held against the stored values, so the fixture's
#: calculations must store what its record says. Each E0 is that participant's
#: electronic energy plus the ZPE below, which every freq and sp of one
#: participant share a geometry key for.
STORED_SP_HARTREE = {
    "ts-sp": -40.20,
    "ch3-sp": -39.75,
    "h-sp": -0.50,
    "ch4-sp": -40.50,
}
STORED_ZPE_HARTREE = {
    "ts-freq": 0.02,
    "ch3-freq": 0.05,
    "h-freq": 0.01,
    "ch4-freq": 0.05,
}


def state_stored_energies(
    payload: dict,
    sp: dict[str, float] | None = None,
    zpe: dict[str, float] | None = None,
) -> dict:
    """Overwrite the stored sp energies and freq ZPEs of the calculations named by key."""

    sp = STORED_SP_HARTREE if sp is None else sp
    zpe = STORED_ZPE_HARTREE if zpe is None else zpe

    def walk(node: object) -> None:
        if isinstance(node, dict):
            key = node.get("key")
            if key in sp and node.get("type") == "sp":
                node["sp_electronic_energy_hartree"] = sp[key]
            if key in zpe and node.get("type") == "freq":
                node["freq_zpe_hartree"] = zpe[key]
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return payload


def _bundle(
    evidence: list[dict] | None = None,
    *,
    sp: dict[str, float] | None = None,
    zpe: dict[str, float] | None = None,
) -> dict:
    """``sp`` / ``zpe`` override what the named calculations store, on top of the defaults."""
    payload = state_stored_energies(
        _payload_with_ts_irc(),
        sp={**STORED_SP_HARTREE, **(sp or {})},
        zpe={**STORED_ZPE_HARTREE, **(zpe or {})},
    )
    if evidence is not None:
        payload["transition_state"]["validation_evidence"] = evidence
    return payload


def _ts_statmech(**overrides) -> dict:
    block: dict = {
        "scientific_origin": "computed",
        "external_symmetry": 3,
        "optical_isomers": 1,
        "point_group": "C3v",
        "is_linear": False,
        "rigid_rotor_kind": "asymmetric_top",
        "statmech_treatment": "rrho",
        "rotational_constant_a_cm1": 5.2,
        "rotational_constant_b_cm1": 4.1,
        "rotational_constant_c_cm1": 3.9,
        "uses_projected_frequencies": True,
        "source_calculations": [{"calculation_key": "ts-freq", "role": "freq"}],
    }
    block.update(overrides)
    return block


def _post_bundle(client, payload: dict):
    return client.post(_BUNDLE, json=payload)


def _ok(response) -> dict:
    assert response.status_code == 201, response.text[:1500]
    return response.json()


def _codes(result: dict) -> set[str]:
    return {warning["code"] for warning in result["warnings"]}


def _entry_ref(db_session, entry_id: int) -> str:
    entry = db_session.get(TransitionStateEntry, entry_id)
    assert entry is not None
    return entry.public_ref


def _entry_read(client, ref: str, *includes: str) -> dict:
    query = f"?include={','.join(includes)}" if includes else ""
    response = client.get(f"/api/v1/scientific/transition-state-entries/{ref}{query}")
    assert response.status_code == 200, response.text[:1500]
    return response.json()["record"]


def _evidence_by_kind(record: dict) -> dict[str, dict]:
    return {item["kind"]: item for item in record["validation_evidence"]}


# ---------------------------------------------------------------------------
# 1. Transition-state statmech on the reaction bundle
# ---------------------------------------------------------------------------


class TestTransitionStateStatmech:
    def test_statmech_on_the_bundle_ts_is_stored_and_read_back(self, client, db_session):
        payload = _bundle()
        payload["transition_state"]["statmech"] = _ts_statmech()
        result = _ok(_post_bundle(client, payload))

        statmech_id = result["transition_state_statmech_id"]
        assert statmech_id is not None
        # The species list is one id per species statmech and is not padded.
        assert statmech_id not in result["statmech_ids"]
        assert result["transition_state_statmech_ref"].startswith("sm_")

        ref = _entry_ref(db_session, result["transition_state_entry_id"])
        record = _entry_read(client, ref, "statmech")
        (summary,) = record["statmech"]
        assert summary["scientific_origin"] == "computed"
        assert summary["external_symmetry"] == 3
        assert summary["optical_isomers"] == 1
        assert summary["point_group"] == "C3v"
        assert summary["rigid_rotor_kind"] == "asymmetric_top"
        assert summary["statmech_treatment"] == "rrho"
        assert summary["rotational_constant_b_cm1"] == 4.1
        assert summary["uses_projected_frequencies"] is True
        assert summary["torsion_count"] == 0
        assert summary["source_calculations"] == [
            {"role": "freq", "calculation_ref": result["calculation_key_refs"]["ts-freq"]}
        ]

        # The summary is a pointer to the full record, which names its subject.
        detail = client.get(f"/api/v1/scientific/statmech/{summary['statmech_ref']}")
        assert detail.status_code == 200, detail.text[:800]
        context = detail.json()["record"]["transition_state"]
        assert context["transition_state_entry_ref"] == ref
        assert detail.json()["record"]["statmech"]["statmech_ref"] == result["transition_state_statmech_ref"]

    def test_statmech_is_absent_unless_asked_for_and_empty_when_there_is_none(
        self, client, db_session
    ):
        result = _ok(_post_bundle(client, _bundle()))
        ref = _entry_ref(db_session, result["transition_state_entry_id"])
        assert result["transition_state_statmech_id"] is None
        assert "statmech" not in _entry_read(client, ref)
        assert _entry_read(client, ref, "statmech")["statmech"] == []

    def test_concept_read_keys_statmech_by_entry(self, client, db_session):
        payload = _bundle()
        payload["transition_state"]["statmech"] = _ts_statmech()
        result = _ok(_post_bundle(client, payload))
        entry = db_session.get(TransitionStateEntry, result["transition_state_entry_id"])

        from app.db.models.transition_state import TransitionState

        concept = db_session.get(TransitionState, entry.transition_state_id)
        response = client.get(
            f"/api/v1/scientific/transition-states/{concept.public_ref}?include=statmech"
        )
        assert response.status_code == 200, response.text[:800]
        (per_entry,) = response.json()["record"]["statmech"]
        assert per_entry["transition_state_entry_ref"] == entry.public_ref
        assert len(per_entry["statmech"]) == 1

    def test_torsions_are_counted_and_the_scan_must_be_a_ts_scan(self, client, db_session):
        payload = _bundle()
        payload["transition_state"]["calculations"].append(
            {
                "key": "ts-scan",
                "type": "scan",
                "geometry_key": "ts-geom",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "scan_result": _ch4_scan_result_payload(points=3),
            }
        )
        payload["transition_state"]["statmech"] = _ts_statmech(
            torsions=[
                {
                    "torsion_index": 1,
                    "symmetry_number": 3,
                    "treatment_kind": "hindered_rotor",
                    "source_scan_calculation_key": "ts-scan",
                }
            ],
        )
        result = _ok(_post_bundle(client, payload))
        ref = _entry_ref(db_session, result["transition_state_entry_id"])
        (summary,) = _entry_read(client, ref, "statmech")["statmech"]
        assert summary["torsion_count"] == 1

    def test_a_ts_statmech_cannot_cite_a_species_calculation(self, client):
        payload = _bundle()
        payload["transition_state"]["statmech"] = _ts_statmech(
            source_calculations=[{"calculation_key": "ch3-freq", "role": "freq"}]
        )
        response = _post_bundle(client, payload)
        assert response.status_code == 422, response.text[:800]
        body = response.json()
        assert body["code"] == "calculation_key_undeclared", body
        assert body["context"]["key"] == "ch3-freq", body
        # The repair it offers is the saddle point's own keys.
        assert "ts-freq" in body["context"]["declared_keys"], body
        assert "ch3-freq" not in body["context"]["declared_keys"], body

    def test_a_ts_statmech_with_an_unknown_calculation_is_refused(self, client):
        payload = _bundle()
        payload["transition_state"]["statmech"] = _ts_statmech(
            source_calculations=[{"calculation_key": "nope", "role": "freq"}]
        )
        body = _post_bundle(client, payload).json()
        assert body["code"] == "calculation_key_undeclared", body

    def test_a_ts_torsion_cannot_cite_a_species_scan(self, client):
        """The scan must belong to the saddle point, as a species' must to its species."""
        payload = _bundle()
        payload["species"][2]["calculations"].append(
            {
                "key": "ch4-scan",
                "type": "scan",
                "geometry_key": "ch4-geom",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
            }
        )
        payload["transition_state"]["statmech"] = _ts_statmech(
            torsions=[
                {
                    "torsion_index": 1,
                    "treatment_kind": "hindered_rotor",
                    "source_scan_calculation_key": "ch4-scan",
                }
            ],
        )
        body = _post_bundle(client, payload).json()
        assert body["code"] == "calculation_key_undeclared", body
        assert body["context"]["key"] == "ch4-scan", body
        assert "ch4-scan" not in body["context"]["declared_keys"], body

    def test_a_torsion_scan_must_be_a_scan_calculation(self, client):
        payload = _bundle()
        payload["transition_state"]["statmech"] = _ts_statmech(
            torsions=[
                {
                    "torsion_index": 1,
                    "treatment_kind": "hindered_rotor",
                    "source_scan_calculation_key": "ts-freq",
                }
            ],
        )
        response = _post_bundle(client, payload)
        assert response.status_code == 422, response.text[:800]

    def test_the_shared_seam_still_refuses_a_role_the_calculation_cannot_play(
        self, client
    ):
        """Role/type compatibility is the seam's, not re-implemented for the TS.

        ``ts-opt`` is an ``opt`` calculation; citing it as the ``freq`` source
        is the mistake the species path already refuses.
        """
        payload = _bundle()
        payload["transition_state"]["statmech"] = _ts_statmech(
            source_calculations=[{"calculation_key": "ts-opt", "role": "freq"}]
        )
        response = _post_bundle(client, payload)
        assert response.status_code == 422, response.text[:800]
        assert response.json()["code"] == "statmech_source_role_type_mismatch", response.json()

    def test_the_shared_seam_still_refuses_an_energy_level_that_contradicts_its_source(
        self, client
    ):
        """R-rules on the energy level apply to a saddle point as to a species.

        The declared energy level is the DFT one while the ``sp`` source ran at
        CCSD(T); the seam refuses the contradiction for a species, so it must
        for a saddle point.
        """
        payload = _bundle()
        payload["transition_state"]["statmech"] = _ts_statmech(
            source_calculations=[
                {"calculation_key": "ts-freq", "role": "freq"},
                {"calculation_key": "ts-sp", "role": "sp"},
            ],
            energy_level_of_theory=_LOT,
        )
        response = _post_bundle(client, payload)
        assert response.status_code == 422, response.text[:800]
        assert response.json()["code"] == "statmech_energy_level_contradiction", response.json()

    def test_a_ts_statmech_citing_its_own_sp_at_the_declared_level_is_accepted(
        self, client
    ):
        """The negative half: the refusals above are about the level, not the block."""
        payload = _bundle()
        payload["transition_state"]["statmech"] = _ts_statmech(
            source_calculations=[
                {"calculation_key": "ts-freq", "role": "freq"},
                {"calculation_key": "ts-sp", "role": "sp"},
            ],
            energy_level_of_theory={"method": "CCSD(T)", "basis": "cc-pVTZ"},
        )
        _ok(_post_bundle(client, payload))

    def test_a_computed_ts_statmech_with_no_sources_warns_under_the_ts_path(self, client):
        payload = _bundle()
        payload["transition_state"]["statmech"] = _ts_statmech(source_calculations=[])
        result = _ok(_post_bundle(client, payload))
        content = [
            w for w in result["warnings"] if w["field"].endswith("statmech.source_calculations")
        ]
        assert [w["field"] for w in content] == ["transition_state.statmech.source_calculations"]

    def test_provenance_gaps_are_reported_under_the_ts_path(self, client):
        payload = _bundle()
        payload["transition_state"]["statmech"] = _ts_statmech()
        result = _ok(_post_bundle(client, payload))
        fields = {w["field"] for w in result["warnings"]}
        assert any(f.startswith("transition_state.statmech.") for f in fields), fields


# ---------------------------------------------------------------------------
# 2. Validation evidence kinds
# ---------------------------------------------------------------------------


class TestEvidenceKindsOnTheReactionBundle:
    def test_energy_ordering_round_trips_with_its_energies_and_sources(
        self, client, db_session
    ):
        result = _ok(_post_bundle(client, _bundle([_energy_ordering()])))
        ref = _entry_ref(db_session, result["transition_state_entry_id"])
        record = _entry_read(client, ref, "validation_evidence")
        ordering = _evidence_by_kind(record)["energy_ordering"]

        assert ordering["passed"] is True
        # No record-level source: each energy names its own.
        assert ordering["reconstruction_calculation_ref"] is None
        refs = result["calculation_key_refs"]
        energies = {
            (e["participant"], e["energy_kind"]): e for e in ordering["compared_energies"]
        }
        assert len(energies) == 8
        assert energies[("ts", "e0")]["energy_hartree"] == -40.18
        assert energies[("ts", "e0")]["source_calculation_ref"] == refs["ts-freq"]
        assert energies[("ts", "electronic")]["source_calculation_ref"] == refs["ts-sp"]
        assert energies[("reactant:2", "electronic")]["source_calculation_ref"] == refs["h-sp"]
        assert energies[("product:1", "e0")]["source_calculation_ref"] == refs["ch4-freq"]

    def test_imaginary_mode_round_trips(self, client, db_session):
        result = _ok(_post_bundle(client, _bundle([_imaginary_mode()])))
        ref = _entry_ref(db_session, result["transition_state_entry_id"])
        mode = _evidence_by_kind(_entry_read(client, ref, "validation_evidence"))[
            "imaginary_mode"
        ]
        assert mode["passed"] is True
        assert mode["imaginary_frequency_count"] == 1
        assert mode["imaginary_frequency_cm1"] == -1500.0
        assert mode["mode_displacement_agrees"] is True
        assert mode["reconstruction_calculation_ref"] == result["calculation_key_refs"]["ts-freq"]
        assert mode["compared_energies"] is None

    def test_an_unassessed_displacement_reads_back_as_null_and_not_false(
        self, client, db_session
    ):
        record = _imaginary_mode()
        del record["mode_displacement_agrees"]
        result = _ok(_post_bundle(client, _bundle([record])))
        ref = _entry_ref(db_session, result["transition_state_entry_id"])
        mode = _evidence_by_kind(_entry_read(client, ref, "validation_evidence"))[
            "imaginary_mode"
        ]
        assert mode["mode_displacement_agrees"] is None

    def test_a_failed_displacement_verdict_is_kept_as_false(self, client, db_session):
        result = _ok(
            _post_bundle(
                client,
                _bundle([_imaginary_mode(passed=False, mode_displacement_agrees=False)]),
            )
        )
        ref = _entry_ref(db_session, result["transition_state_entry_id"])
        mode = _evidence_by_kind(_entry_read(client, ref, "validation_evidence"))[
            "imaginary_mode"
        ]
        assert mode["passed"] is False
        assert mode["mode_displacement_agrees"] is False

    def test_the_descriptor_reports_each_kind_independently(self, client, db_session):
        result = _ok(
            _post_bundle(
                client,
                _bundle(
                    [
                        _energy_ordering(),
                        _imaginary_mode(passed=False, mode_displacement_agrees=False),
                    ]
                ),
            )
        )
        ref = _entry_ref(db_session, result["transition_state_entry_id"])
        assert _entry_read(client, ref)["validation"] == {
            "irc": "absent",
            "energy_ordering": "present",
            "imaginary_mode": "failed",
        }

    def test_neither_new_kind_silences_the_missing_irc_warning(self, client):
        result = _ok(
            _post_bundle(client, _bundle([_energy_ordering(), _imaginary_mode()]))
        )
        assert _MISSING_IRC in _codes(result)

    def test_a_passing_irc_does_silence_it_beside_the_other_kinds(self, client):
        result = _ok(
            _post_bundle(
                client,
                _bundle([_irc_evidence(), _energy_ordering(), _imaginary_mode()]),
            )
        )
        assert _MISSING_IRC not in _codes(result)

    def test_a_failed_irc_still_warns_beside_passing_new_kinds(self, client):
        result = _ok(
            _post_bundle(
                client,
                _bundle(
                    [_irc_evidence(passed=False), _energy_ordering(), _imaginary_mode()]
                ),
            )
        )
        assert _MISSING_IRC in _codes(result)

    # -- refusals: energy_ordering ----------------------------------------

    def _refused(self, client, evidence: list[dict]) -> dict:
        response = _post_bundle(client, _bundle(evidence))
        assert response.status_code == 422, response.text[:1200]
        return response.json()

    def test_a_pass_the_energies_contradict_is_refused(self, client):
        record = _energy_ordering()
        # Put the saddle point below the reactant sum at the electronic level.
        record["energies"][0] = _energy("ts", "electronic", -40.30, "ts-sp")
        body = self._refused(client, [record])
        assert "at or below the reactant side" in str(body["detail"]), body

    def test_the_same_energies_marked_failed_are_accepted(self, client):
        """A producer that says it failed is not contradicted by numbers."""
        record = _energy_ordering(passed=False)
        record["energies"][0] = _energy("ts", "electronic", -40.30, "ts-sp")
        # The cited calculations store these numbers (and the ZPE that keeps the
        # saddle point's E0 at -40.18): being marked failed excuses an ordering,
        # not a stated energy the stored one contradicts.
        _ok(_post_bundle(client, _bundle([record], sp={"ts-sp": -40.30}, zpe={"ts-freq": 0.12})))

    def test_an_ordering_needs_a_well_on_each_side(self, client):
        record = _energy_ordering()
        record["energies"] = [e for e in record["energies"] if not e["participant"].startswith("product")]
        body = self._refused(client, [record])
        assert "at least one product" in str(body["detail"]), body

    def test_a_passing_ordering_must_cover_every_participant_with_atoms(self, client):
        record = _energy_ordering()
        record["energies"] = [e for e in record["energies"] if e["participant"] != "reactant:2"]
        body = self._refused(client, [record])
        assert "reactant:2" in str(body["detail"]), body

    def test_an_energy_for_an_undeclared_participant_is_refused(self, client):
        record = _energy_ordering()
        record["energies"].append(_energy("reactant:3", "electronic", -1.0, "h-sp"))
        body = self._refused(client, [record])
        assert "does not declare" in str(body["detail"]), body

    def test_an_energy_cannot_be_taken_from_another_species_calculation(self, client):
        record = _energy_ordering()
        record["energies"][1] = _energy("reactant:1", "electronic", -39.75, "h-sp")
        body = self._refused(client, [record])
        assert body["code"] == "calculation_key_undeclared", body
        assert "h-sp" == body["context"]["key"], body
        assert "ch3-sp" in body["context"]["declared_keys"], body

    def test_the_saddle_points_energy_cannot_come_from_a_species_calculation(self, client):
        record = _energy_ordering()
        record["energies"][0] = _energy("ts", "electronic", -40.20, "ch4-sp")
        body = self._refused(client, [record])
        assert body["code"] == "calculation_key_undeclared", body

    def test_an_energy_with_an_unknown_calculation_key_is_refused(self, client):
        record = _energy_ordering()
        record["energies"][0] = _energy("ts", "electronic", -40.20, "nope")
        body = self._refused(client, [record])
        assert body["code"] == "calculation_key_undeclared", body

    def test_an_energy_must_say_which_kind_it_is(self, client):
        record = _energy_ordering()
        record["energies"][0]["energy_kind"] = "total"
        self._refused(client, [record])

    def test_an_energy_must_name_a_source_calculation(self, client):
        record = _energy_ordering()
        del record["energies"][0]["source_calculation_key"]
        self._refused(client, [record])

    def test_a_record_level_source_is_not_accepted_on_an_ordering(self, client):
        self._refused(client, [_energy_ordering(source_calculation_key="ts-freq")])

    def test_an_ordering_with_no_energies_is_refused(self, client):
        self._refused(client, [_energy_ordering(energies=[])])

    def test_the_same_slot_twice_is_refused(self, client):
        record = _energy_ordering()
        record["energies"].append(_energy("ts", "electronic", -40.1, "ts-sp"))
        self._refused(client, [record])

    # -- refusals: imaginary_mode -----------------------------------------

    def test_an_imaginary_mode_must_name_a_freq_calculation(self, client):
        body = self._refused(client, [_imaginary_mode(source_calculation_key="ts-opt")])
        assert "requires a freq calculation" in str(body["detail"]), body

    def test_an_imaginary_mode_must_name_a_source_calculation(self, client):
        record = _imaginary_mode()
        del record["source_calculation_key"]
        self._refused(client, [record])

    def test_an_imaginary_mode_cannot_cite_a_species_freq(self, client):
        body = self._refused(client, [_imaginary_mode(source_calculation_key="ch3-freq")])
        assert body["code"] == "calculation_key_undeclared", body

    def test_a_pass_with_no_imaginary_mode_is_refused(self, client):
        record = _imaginary_mode(imaginary_frequency_count=0)
        del record["imaginary_frequency_cm1"]
        self._refused(client, [record])

    def test_a_frequency_with_a_zero_count_is_refused(self, client):
        self._refused(
            client,
            [_imaginary_mode(passed=False, imaginary_frequency_count=0)],
        )

    def test_an_imaginary_frequency_must_be_negative(self, client):
        self._refused(client, [_imaginary_mode(imaginary_frequency_cm1=1500.0)])

    def test_a_negative_count_is_refused(self, client):
        self._refused(client, [_imaginary_mode(imaginary_frequency_count=-1)])

    # -- refusals: shape --------------------------------------------------

    def test_a_field_is_refused_on_a_kind_it_does_not_describe(self, client):
        energies = _energy_ordering()["energies"]
        for record in (
            _irc_evidence(energies=energies),
            _imaginary_mode(energies=energies),
            _energy_ordering(imaginary_frequency_cm1=-1.0),
            _irc_evidence(mode_displacement_agrees=True),
            _imaginary_mode(
                reactant_participant_mapping={"reactant:1": [1], "reactant:2": [2]},
                product_participant_mapping={"product:1": [1, 2]},
            ),
        ):
            self._refused(client, [record])

    def test_at_most_one_record_per_kind(self, client):
        self._refused(client, [_imaginary_mode(), _imaginary_mode()])
        self._refused(client, [_energy_ordering(), _energy_ordering()])

    def test_an_unknown_kind_is_refused(self, client):
        self._refused(client, [_imaginary_mode(kind="nmd")])

    def test_a_unique_row_per_kind_is_enforced_in_storage_too(self, client, db_session):
        """One of each kind is stored, and reads back as one of each."""
        result = _ok(
            _post_bundle(
                client,
                _bundle([_irc_evidence(), _energy_ordering(), _imaginary_mode()]),
            )
        )
        ref = _entry_ref(db_session, result["transition_state_entry_id"])
        record = _entry_read(client, ref, "validation_evidence")
        assert sorted(_evidence_by_kind(record)) == ["energy_ordering", "imaginary_mode", "irc"]
        assert _entry_read(client, ref)["validation"]["irc"] == "present"


# ---------------------------------------------------------------------------
# 2b. The same kinds on the standalone route
# ---------------------------------------------------------------------------


def _standalone(**changes) -> dict:
    """CH3 + H -> CH4 as a standalone transition-state upload."""
    payload: dict = {
        "reaction": {
            "reversible": True,
            "reactants": [
                {"species_entry": {"smiles": "[CH3]", "charge": 0, "multiplicity": 2}},
                {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
            ],
            "products": [
                {"species_entry": {"smiles": "C", "charge": 0, "multiplicity": 1}},
            ],
        },
        "charge": 0,
        "multiplicity": 2,
        "geometry": {"xyz_text": _XYZ_TS},
        "primary_opt": {
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
            "opt_result": {"converged": True, "n_steps": 10, "final_energy_hartree": -40.2},
        },
        "additional_calculations": [
            {
                "type": "freq",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "freq_result": {"n_imag": 1, "imag_freq_cm1": -1500.0, "zpe_hartree": 0.03},
            },
        ],
    }
    payload.update(changes)
    return payload


def _standalone_ok(client, payload: dict) -> dict:
    response = client.post(_STANDALONE, json=payload)
    assert response.status_code == 201, response.text[:1500]
    return response.json()


def _standalone_refused(client, payload: dict) -> dict:
    response = client.post(_STANDALONE, json=payload)
    assert response.status_code == 422, response.text[:1500]
    return response.json()


class TestEvidenceKindsOnTheStandaloneRoute:
    def test_imaginary_mode_binds_to_the_single_freq_calculation(self, client, db_session):
        record = _imaginary_mode()
        del record["source_calculation_key"]
        result = _standalone_ok(client, _standalone(validation_evidence=[record]))
        ref = _entry_ref(db_session, result["id"])
        mode = _evidence_by_kind(_entry_read(client, ref, "validation_evidence"))[
            "imaginary_mode"
        ]
        assert mode["imaginary_frequency_count"] == 1
        assert mode["imaginary_frequency_cm1"] == -1500.0
        assert mode["mode_displacement_agrees"] is True
        assert mode["reconstruction_calculation_ref"] is not None
        assert _MISSING_IRC in _codes(result)

    def test_imaginary_mode_needs_exactly_one_freq_calculation_to_bind_to(self, client):
        record = _imaginary_mode()
        del record["source_calculation_key"]
        payload = _standalone(validation_evidence=[record])
        payload["additional_calculations"] = []
        body = _standalone_refused(client, payload)
        assert "exactly one additional calculation of type 'freq'" in str(body["detail"]), body

    def test_a_source_key_is_not_accepted_where_there_is_no_namespace(self, client):
        body = _standalone_refused(
            client, _standalone(validation_evidence=[_imaginary_mode()])
        )
        assert "source_calculation_key is not accepted" in str(body["detail"]), body

    def test_energy_ordering_is_refused_where_the_wells_have_no_calculations(self, client):
        body = _standalone_refused(
            client, _standalone(validation_evidence=[_energy_ordering()])
        )
        assert "computed-reaction upload" in str(body["detail"]), body

    def test_the_imaginary_mode_contradiction_is_refused_here_too(self, client):
        record = _imaginary_mode(imaginary_frequency_count=0)
        del record["source_calculation_key"]
        del record["imaginary_frequency_cm1"]
        _standalone_refused(client, _standalone(validation_evidence=[record]))

    def test_an_irc_and_an_imaginary_mode_bind_to_their_own_calculations(
        self, client, db_session
    ):
        payload = _standalone()
        payload["additional_calculations"].append(
            {"type": "irc", "software_release": _SOFTWARE, "level_of_theory": _LOT}
        )
        irc = _irc_evidence()
        mode = _imaginary_mode()
        del irc["source_calculation_key"]
        del mode["source_calculation_key"]
        payload["validation_evidence"] = [irc, mode]
        result = _standalone_ok(client, payload)
        assert _MISSING_IRC not in _codes(result)
        ref = _entry_ref(db_session, result["id"])
        by_kind = _evidence_by_kind(_entry_read(client, ref, "validation_evidence"))
        assert by_kind["irc"]["reconstruction_calculation_ref"] is not None
        assert by_kind["imaginary_mode"]["reconstruction_calculation_ref"] is not None
        assert (
            by_kind["irc"]["reconstruction_calculation_ref"]
            != by_kind["imaginary_mode"]["reconstruction_calculation_ref"]
        )
