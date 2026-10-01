"""Source-calculation rules for the #622 bundle blocks, through the real API.

Companion to ``test_api_bundle_extras_622.py``. That file proves the blocks
are stored; this one proves what may be cited as their source: which job may
measure an SCF stability verdict (same conformer, no cycle, a level-of-theory
warning), that every carrier kind is linked, that the PDep route refuses the
key it cannot honour, and that the transport record enters review and is
named by ref. Each test names the mutation that falsifies it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from tckdb_schemas.fragments.calculation import SCFStabilityContent

from app.api.error_contract import CodedValueError
from app.db.models.calculation import CalculationSCFStability
from app.db.models.common import (
    CalculationType,
    RecordReviewStatus,
    SCFStabilityStatus,
    SubmissionRecordType,
)
from app.db.models.record_review import RecordReview
from app.db.models.submission import SubmissionRecordLink
from app.db.models.transport import Transport
from app.services.scf_stability_sources import link_scf_stability_sources
from tests.api.test_api_bundle_extras_622 import (
    _LJ,
    _LOT,
    _SOFTWARE,
    _STABILITY,
    _XYZ_ETHANE,
    _calc_ids,
    _ethane_bundle,
    _reaction_bundle,
    _with_stability,
)
from tests.services.scientific_read._factories import (
    make_calculation,
    make_conformer_group,
    make_conformer_observation,
    make_species,
    make_species_entry,
    next_inchi_key,
)

_XYZ_ETHANE_B = _XYZ_ETHANE.replace("-0.765000", "-0.780000").replace(
    "0.765000", "0.780000"
)


def _two_conformer_ethane() -> dict:
    """The ethane bundle plus a second conformer ``c1`` with its own opt and sp."""
    payload = _ethane_bundle()
    payload["conformers"].append(
        {
            "key": "c1",
            "geometry": {"xyz_text": _XYZ_ETHANE_B},
            "primary_calculation": {
                "key": "opt1",
                "type": "opt",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "opt_result": {"converged": True},
            },
            "additional_calculations": [
                {
                    "key": "sp1",
                    "type": "sp",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "sp_result": {"electronic_energy_hartree": -79.81},
                }
            ],
        }
    )
    return payload


def _carrier(payload: dict, key: str) -> dict:
    for conf in payload["conformers"]:
        for calc in (conf["primary_calculation"], *conf["additional_calculations"]):
            if calc["key"] == key:
                return calc
    raise KeyError(key)


# ---------------------------------------------------------------------------
# Which job may measure a stability verdict
# ---------------------------------------------------------------------------


def test_stability_source_on_another_conformer_is_refused_with_its_code(client):
    """opt0 (conformer c0) cannot cite sp1 (conformer c1).

    A stability analysis describes one wavefunction at one geometry, the same
    reason ``thermo_sp_geometry_mismatch`` exists. Asserted on ``code`` and
    ``context``. Paired: opt0 citing sp0 (same conformer) is accepted in
    ``test_stability_verdict_names_the_job_that_measured_it``.

    Mutation: delete the conformer comparison from both the species schema and
    the service; this returns 201 (the next test pins the service alone).
    """
    payload = _two_conformer_ethane()
    _carrier(payload, "opt0")["scf_stability"] = {
        **_STABILITY,
        "source_calculation_key": "sp1",
    }
    resp = client.post("/api/v1/uploads/computed-species", json=payload)
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == "scf_stability_source_geometry_mismatch", body
    assert body["context"] == {
        "field": "calculations['opt0'].scf_stability.source_calculation_key",
        "key": "sp1",
        "carrier_key": "opt0",
    }, body


def test_workflow_refuses_a_stability_source_on_another_conformer(db_session):
    """The workflow half, provoked with no schema in front of it.

    Mutation: delete the ``conformer_observation_id`` comparison in
    ``link_scf_stability_sources``; the row is linked across conformers.
    """
    entry = make_species_entry(
        db_session, make_species(db_session, inchi_key=next_inchi_key("SCFG"))
    )
    group = make_conformer_group(db_session, entry)
    first = make_conformer_observation(db_session, conformer_group=group)
    second = make_conformer_observation(db_session, conformer_group=group)
    opt = make_calculation(
        db_session,
        type=CalculationType.opt,
        species_entry_id=entry.id,
        conformer_observation_id=first.id,
    )
    sp = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
        conformer_observation_id=second.id,
    )
    db_session.add(
        CalculationSCFStability(calculation_id=opt.id, status=SCFStabilityStatus.stable)
    )
    db_session.flush()

    with pytest.raises(CodedValueError) as err:
        link_scf_stability_sources(
            db_session,
            [("opt", SCFStabilityContent(status="stable", source_calculation_key="sp"))],
            {"opt": opt, "sp": sp},
        )
    assert err.value.code == "scf_stability_source_geometry_mismatch"
    assert err.value.context == {
        "field": "calculations['opt'].scf_stability.source_calculation_key",
        "key": "sp",
        "carrier_key": "opt",
    }


def test_workflow_refuses_a_stability_source_cycle(db_session):
    """The service refuses a cycle on its own.

    Mutation: delete the ``find_scf_source_cycle`` call in the service.
    """
    entry = make_species_entry(
        db_session, make_species(db_session, inchi_key=next_inchi_key("SCFH"))
    )
    a = make_calculation(db_session, type=CalculationType.opt, species_entry_id=entry.id)
    b = make_calculation(db_session, type=CalculationType.sp, species_entry_id=entry.id)
    for calc in (a, b):
        db_session.add(
            CalculationSCFStability(calculation_id=calc.id, status=SCFStabilityStatus.stable)
        )
    db_session.flush()
    with pytest.raises(ValueError, match="forms a cycle"):
        link_scf_stability_sources(
            db_session,
            [
                ("a", SCFStabilityContent(status="stable", source_calculation_key="b")),
                ("b", SCFStabilityContent(status="stable", source_calculation_key="a")),
            ],
            {"a": a, "b": b},
        )


def test_stability_source_cycle_is_refused(client):
    """opt0 cites sp0 and sp0 cites opt0.

    Mutation: delete the ``find_scf_source_cycle`` call from the species
    schema; the service still refuses, so delete it from both for a 201.
    """
    payload = _ethane_bundle()
    _carrier(payload, "opt0")["scf_stability"] = {
        **_STABILITY,
        "source_calculation_key": "sp0",
    }
    _carrier(payload, "sp0")["scf_stability"] = {
        **_STABILITY,
        "source_calculation_key": "opt0",
    }
    resp = client.post("/api/v1/uploads/computed-species", json=payload)
    assert resp.status_code == 422, resp.text[:800]
    assert "forms a cycle" in resp.text


def test_stability_source_at_another_level_of_theory_is_kept_with_a_warning(
    client, db_session
):
    """A different level is worth saying, not worth losing the record over.

    ADR 0008: definitions block, expectations warn. The verdict is stored and
    the response carries ``scf_stability_source_level_mismatch``; the same
    bundle at one level carries none.

    Mutation: delete the level comparison in ``link_scf_stability_sources``;
    the warning disappears.
    """
    code = "scf_stability_source_level_mismatch"
    same = _with_stability(_ethane_bundle(), carrier="opt0", source_calculation_key="sp0")
    resp = client.post("/api/v1/uploads/computed-species", json=same)
    assert resp.status_code == 201, resp.text[:800]
    assert [w for w in resp.json()["warnings"] if w["code"] == code] == []

    other = _with_stability(_ethane_bundle(), carrier="opt0", source_calculation_key="sp0")
    _carrier(other, "sp0")["level_of_theory"] = {"method": "b3lyp", "basis": "6-31g*"}
    resp = client.post("/api/v1/uploads/computed-species", json=other)
    assert resp.status_code == 201, resp.text[:800]
    warned = [w for w in resp.json()["warnings"] if w["code"] == code]
    assert [w["field"] for w in warned] == [
        "calculations['opt0'].scf_stability.source_calculation_key"
    ]
    ids = _calc_ids(resp)
    assert db_session.get(CalculationSCFStability, ids["opt0"]).source_calculation_id == ids["sp0"]


# ---------------------------------------------------------------------------
# Every kind of carrier is linked
# ---------------------------------------------------------------------------


def test_an_additional_calculation_can_carry_the_stability_verdict(client, db_session):
    """The carrier need not be the primary: freq0 cites sp0.

    Mutation: drop ``additional_calculations`` from the carriers the species
    workflow hands to ``link_scf_stability_sources``; the row keeps no source.
    """
    payload = _with_stability(
        _ethane_bundle(), carrier="freq0", source_calculation_key="sp0"
    )
    resp = client.post("/api/v1/uploads/computed-species", json=payload)
    assert resp.status_code == 201, resp.text[:800]
    ids = _calc_ids(resp)
    row = db_session.get(CalculationSCFStability, ids["freq0"])
    assert row.source_calculation_id == ids["sp0"]


def test_reaction_species_calculation_entry_can_carry_the_stability_verdict(
    client, db_session
):
    """A block on a species ``calculations[]`` entry (not the conformer's) is linked.

    Mutation: drop ``sp.calculations`` from ``_stability_carriers`` in the
    reaction workflow; ``source_calculation_id`` stays ``NULL``.
    """
    bundle = _reaction_bundle()
    bundle["species"][1]["calculations"][0]["scf_stability"] = {
        **_STABILITY,
        "source_calculation_key": "h2-sp",
    }
    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]
    keys = resp.json()["calculation_keys"]
    row = db_session.get(CalculationSCFStability, keys["h2-freq"])
    assert row.source_calculation_id == keys["h2-sp"]


def test_reaction_transition_state_calculation_can_carry_the_stability_verdict(
    client, db_session
):
    """A block on a TS calculation is linked, within the TS's own calculations.

    Mutation: drop the transition state's calculations from
    ``_stability_carriers``; ``source_calculation_id`` stays ``NULL``.
    """
    from tests.workflows.test_computed_reaction_upload import (
        _payload_with_aec_carriers,
    )

    payload = _payload_with_aec_carriers()
    next(c for c in payload["transition_state"]["calculations"] if c["key"] == "ts-freq")[
        "scf_stability"
    ] = {**_STABILITY, "source_calculation_key": "ts-sp"}
    resp = client.post("/api/v1/uploads/computed-reaction", json=payload)
    assert resp.status_code == 201, resp.text[:800]
    keys = resp.json()["calculation_keys"]
    row = db_session.get(CalculationSCFStability, keys["ts-freq"])
    assert row.source_calculation_id == keys["ts-sp"]


def test_reaction_transition_state_stability_may_not_cite_a_species_job(client):
    """The transition-state half of the owner rule: ``owner_kind`` differs.

    Mutation: hard-code ``"species entry"`` as the owner noun in the reaction
    schema's ``owner_mismatch_error`` call.
    """
    from tests.workflows.test_computed_reaction_upload import (
        _payload_with_aec_carriers,
    )

    payload = _payload_with_aec_carriers()
    next(c for c in payload["transition_state"]["calculations"] if c["key"] == "ts-freq")[
        "scf_stability"
    ] = {**_STABILITY, "source_calculation_key": "ch3-sp"}
    resp = client.post("/api/v1/uploads/computed-reaction", json=payload)
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == "scf_stability_source_calculation_owner_mismatch", body
    assert body["context"]["owner_kind"] == "transition_state_entry", body


def _two_conformer_h2_bundle() -> dict:
    """``_reaction_bundle`` with a second H2 conformer and its own opt and sp."""
    bundle = _reaction_bundle()
    h2 = bundle["species"][1]
    h2["conformers"].append(
        {
            "key": "h2-conf-b",
            "geometry": {
                "key": "h2-geom-b",
                "xyz_text": "2\nH2\nH 0.0 0.0 0.0\nH 0.0 0.0 0.75",
            },
            "calculation": {
                "key": "h2-opt-b",
                "type": "opt",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "opt_converged": True,
            },
        }
    )
    h2["calculations"].append(
        {
            "key": "h2-sp-b",
            "type": "sp",
            "conformer_key": "h2-conf-b",
            "geometry_key": "h2-geom-b",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
            "sp_electronic_energy_hartree": -1.49,
        }
    )
    return bundle


def test_reaction_stability_source_on_another_conformer_is_refused(client):
    """h2-opt (conformer h2-conf) cannot cite h2-sp-b (conformer h2-conf-b).

    Paired: citing ``h2-sp``, on the same conformer, is accepted.

    Mutation: delete the anchor comparison in the reaction schema and the
    ``conformer_observation_id`` comparison in the service.
    """
    ok = _two_conformer_h2_bundle()
    ok["species"][1]["conformers"][0]["calculation"]["scf_stability"] = {
        **_STABILITY,
        "source_calculation_key": "h2-sp",
    }
    resp = client.post("/api/v1/uploads/computed-reaction", json=ok)
    assert resp.status_code == 201, resp.text[:800]

    bad = _two_conformer_h2_bundle()
    bad["species"][1]["conformers"][0]["calculation"]["scf_stability"] = {
        **_STABILITY,
        "source_calculation_key": "h2-sp-b",
    }
    resp = client.post("/api/v1/uploads/computed-reaction", json=bad)
    assert resp.status_code == 422, resp.text[:800]
    body = resp.json()
    assert body["code"] == "scf_stability_source_geometry_mismatch", body
    assert body["context"]["key"] == "h2-sp-b", body
    assert body["context"]["carrier_key"] == "h2-opt", body


def test_reaction_stability_source_cycle_is_refused(client):
    """h2-freq cites h2-sp and h2-sp cites h2-freq.

    Mutation: delete the ``find_scf_source_cycle`` call from the reaction
    schema and the service.
    """
    bundle = _reaction_bundle()
    calcs = bundle["species"][1]["calculations"]
    calcs[0]["scf_stability"] = {**_STABILITY, "source_calculation_key": "h2-sp"}
    calcs[1]["scf_stability"] = {**_STABILITY, "source_calculation_key": "h2-freq"}
    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 422, resp.text[:800]
    assert "forms a cycle" in resp.text


def test_pdep_route_refuses_the_stability_source_key(client):
    """The PDep route persists the block but has no pass to link the key.

    Without the refusal the key would be accepted and dropped with a 201.
    Paired: the same stability block without the key is accepted.

    Mutation: delete the loop in ``NetworkPDepUploadRequest`` that refuses it.
    """
    from tests.workflows.test_network_pdep_upload import _full_payload

    url = "/api/v1/uploads/networks/pdep"
    ok = _full_payload(include_solve=False)
    ok["species"][0]["calculations"][0]["scf_stability"] = dict(_STABILITY)
    resp = client.post(url, json=ok)
    assert resp.status_code == 201, resp.text[:800]

    bad = _full_payload(include_solve=False)
    bad["species"][0]["calculations"][0]["scf_stability"] = {
        **_STABILITY,
        "source_calculation_key": bad["species"][0]["calculations"][1]["key"],
    }
    resp = client.post(url, json=bad)
    assert resp.status_code == 422, resp.text[:800]
    assert "not supported on the pressure-dependent network route" in resp.text


# ---------------------------------------------------------------------------
# Review rows and refs for the new record
# ---------------------------------------------------------------------------


def _assert_not_reviewed_and_linked(db_session, transport_id: int, submission_id: int):
    review = db_session.scalar(
        select(RecordReview).where(
            RecordReview.record_type == SubmissionRecordType.transport,
            RecordReview.record_id == transport_id,
        )
    )
    assert review is not None, "no review row for the transport"
    assert review.status is RecordReviewStatus.not_reviewed
    assert review.submission_id == submission_id
    link = db_session.scalar(
        select(SubmissionRecordLink).where(
            SubmissionRecordLink.submission_id == submission_id,
            SubmissionRecordLink.record_type == SubmissionRecordType.transport,
            SubmissionRecordLink.record_id == transport_id,
        )
    )
    assert link is not None, "transport not linked to its submission"


def test_species_bundle_transport_enters_review_and_is_named_by_ref(client, db_session):
    """Transport is reviewable like every other record the bundle writes.

    Mutation: drop the transport ``RecordRef`` from the species workflow's
    ``review_targets``; there is no review row or link.
    """
    resp = client.post(
        "/api/v1/uploads/computed-species", json=_ethane_bundle(transport={**_LJ})
    )
    assert resp.status_code == 201, resp.text[:800]
    body = resp.json()
    tid = body["transport"]["transport_id"]
    _assert_not_reviewed_and_linked(db_session, tid, body["submission_id"])
    row = db_session.get(Transport, tid)
    assert body["transport"]["transport_ref"] == row.public_ref
    assert row.public_ref.startswith("trn_")


def test_reaction_bundle_transport_enters_review_and_is_named_by_ref(client, db_session):
    """Same, per species, on the reaction bundle.

    Mutation: drop the transport ``RecordRef`` extension from the reaction
    workflow's ``review_targets``.
    """
    bundle = _reaction_bundle()
    bundle["species"][0]["transport"] = {**_LJ}
    bundle["species"][1]["transport"] = {"sigma_angstrom": 2.83, "epsilon_over_k_k": 59.7}
    resp = client.post("/api/v1/uploads/computed-reaction", json=bundle)
    assert resp.status_code == 201, resp.text[:800]
    body = resp.json()
    assert len(body["transport_ids"]) == len(body["transport_refs"]) == 2
    for tid, ref in zip(body["transport_ids"], body["transport_refs"], strict=True):
        _assert_not_reviewed_and_linked(db_session, tid, body["submission_id"])
        assert db_session.get(Transport, tid).public_ref == ref
