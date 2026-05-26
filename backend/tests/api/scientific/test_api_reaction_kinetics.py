"""API tests for GET /api/v1/scientific/reaction-entries/{id}/kinetics."""

from __future__ import annotations

from app.db.models.common import (
    CalculationQuality,
    CalculationType,
    KineticsCalculationRole,
    RecordReviewStatus,
    ScientificOriginKind,
    SubmissionRecordType,
    ValidationStatus,
)
from app.db.models.kinetics import KineticsSourceCalculation
from app.db.models.software import SoftwareRelease
from tests.services.scientific_read._factories import (
    attach_artifact,
    attach_geometry_validation,
    attach_opt_result,
    make_chem_reaction,
    make_calculation,
    make_kinetics,
    make_lot,
    make_reaction_entry,
    make_software,
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)


def _entry(db_session):
    rs = make_species(db_session, smiles="A", inchi_key=next_inchi_key("KAPI1"))
    ps = make_species(db_session, smiles="B", inchi_key=next_inchi_key("KAPI2"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    return make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )


def test_returns_200_for_valid_reaction_entry_id(client, db_session):
    entry = _entry(db_session)
    make_kinetics(db_session, reaction_entry=entry)

    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/kinetics"
    )
    assert resp.status_code == 200
    body = resp.json()
    # Phase D: default response identifies the reaction entry by ref.
    assert body["reaction_entry_ref"] == entry.public_ref
    assert len(body["records"]) == 1


def test_returns_404_for_missing_reaction_entry_id(client, db_session):
    resp = client.get("/api/v1/scientific/reaction-entries/999999/kinetics")
    assert resp.status_code == 404
    assert "reaction_entry not found" in resp.text


def test_rejects_temperature_min_greater_than_max(client, db_session):
    entry = _entry(db_session)
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/kinetics"
        "?temperature_min=2000&temperature_max=300"
    )
    assert resp.status_code == 422
    assert "invalid_temperature_range" in resp.text


def test_rejects_client_sort(client, db_session):
    entry = _entry(db_session)
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/kinetics?sort=anything"
    )
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_non_ts_backed_provenance_returns_nulls(client, db_session):
    entry = _entry(db_session)
    make_kinetics(
        db_session,
        reaction_entry=entry,
        scientific_origin=ScientificOriginKind.experimental,
    )

    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/kinetics"
    )
    assert resp.status_code == 200
    record = resp.json()["records"][0]
    p = record["provenance"]
    # Phase D: integer TS-chain ids are hidden in the default response.
    # The corresponding ref siblings remain visible and are null for
    # non-TS-backed kinetics.
    assert p["transition_state_entry_ref"] is None
    assert p["ts_opt_calculation_ref"] is None
    assert p["ts_freq_calculation_ref"] is None
    assert p["ts_sp_calculation_ref"] is None
    assert p["path_search"] is None
    assert p["irc"] is None
    # Non-TS provenance keys are still present in the JSON shape.
    assert "literature" in p
    assert "software_release" in p
    assert "workflow_tool_release" in p


def test_temperature_coverage_metadata_present(client, db_session):
    entry = _entry(db_session)
    make_kinetics(
        db_session, reaction_entry=entry, tmin_k=300.0, tmax_k=1500.0
    )

    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/kinetics"
        "?temperature_min=300&temperature_max=2000"
    )
    cov = resp.json()["records"][0]["temperature_coverage"]
    assert cov["covers_requested_range"] is False
    assert cov["extrapolation_distance_k"] == 500.0


def test_unknown_include_token_rejected(client, db_session):
    entry = _entry(db_session)
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/kinetics?include=banana"
    )
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


# ---------------------------------------------------------------------------
# include=trust
# ---------------------------------------------------------------------------


def _link_source(db_session, *, kinetics, calculation, role):
    db_session.add(
        KineticsSourceCalculation(
            kinetics_id=kinetics.id,
            calculation_id=calculation.id,
            role=role,
        )
    )
    db_session.flush()
    db_session.refresh(kinetics)


def _source_calculation(db_session, *, quality=CalculationQuality.curated):
    species = make_species(
        db_session,
        smiles="C",
        inchi_key=next_inchi_key("KTRUST"),
    )
    species_entry = make_species_entry(db_session, species)
    software = make_software(db_session, name=f"kin-trust-sw-{species.id}")
    release = SoftwareRelease(software_id=software.id, version="1.0")
    db_session.add(release)
    db_session.flush()
    calc = make_calculation(
        db_session,
        type=CalculationType.opt,
        species_entry_id=species_entry.id,
        lot_id=make_lot(db_session).id,
    )
    calc.quality = quality
    calc.software_release_id = release.id
    attach_opt_result(db_session, calculation=calc, final_energy_hartree=-10.0)
    attach_geometry_validation(
        db_session, calculation=calc, status=ValidationStatus.passed
    )
    attach_artifact(db_session, calculation=calc)
    db_session.flush()
    db_session.refresh(calc)
    return calc


def test_trust_omitted_when_not_requested(client, db_session):
    entry = _entry(db_session)
    make_kinetics(db_session, reaction_entry=entry)

    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/kinetics"
    ).json()

    assert "trust" not in body["records"][0]


def test_include_trust_returns_fragment(client, db_session):
    entry = _entry(db_session)
    kinetics = make_kinetics(db_session, reaction_entry=entry)
    set_review(
        db_session,
        record_type=SubmissionRecordType.kinetics,
        record_id=kinetics.id,
        status=RecordReviewStatus.approved,
    )

    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/kinetics"
        "?include=trust"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["request"]["include"] == ["trust"]
    trust = body["records"][0]["trust"]
    assert trust["review_status"] == "approved"
    assert trust["trust_status"] in {
        "well_supported",
        "mostly_supported",
        "partial",
        "sparse",
        "unsupported",
        "hard_failed",
    }
    assert trust["llm_precheck"] == {
        "enabled": False,
        "label": "not_run",
        "summary": None,
    }
    evidence = trust["evidence"]
    assert evidence["record_type"] == "kinetics"
    assert evidence["rubric"] == "computed_kinetics_v1"
    assert evidence["rubric_version"] == 1
    assert "record_id" not in evidence
    assert "passed_checks" in evidence
    assert "missing_checks" in evidence
    assert "warning_checks" in evidence
    assert "not_applicable_checks" in evidence


def test_include_trust_sparse_kinetics_reports_missing_checks(client, db_session):
    entry = _entry(db_session)
    make_kinetics(
        db_session,
        reaction_entry=entry,
        a=None,
        a_units=None,
        n=None,
        ea_kj_mol=None,
    )

    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/kinetics"
        "?include=trust"
    ).json()
    evidence = body["records"][0]["trust"]["evidence"]

    assert evidence["label"] in {"sparse", "unsupported", "partial"}
    assert "arrhenius_parameters_complete" in evidence["missing_checks"]
    assert "source_calculations_present" in evidence["missing_checks"]


def test_include_trust_source_calculations_score_higher(client, db_session):
    entry = _entry(db_session)
    sparse = make_kinetics(db_session, reaction_entry=entry)
    rich = make_kinetics(db_session, reaction_entry=entry, ea_kj_mol=25.0)
    calc = _source_calculation(db_session)
    _link_source(
        db_session,
        kinetics=rich,
        calculation=calc,
        role=KineticsCalculationRole.ts_energy,
    )

    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/kinetics"
        "?include=trust"
    ).json()
    by_ref = {record["kinetics_ref"]: record for record in body["records"]}

    sparse_score = by_ref[sparse.public_ref]["trust"]["evidence"][
        "evidence_completeness"
    ]
    rich_score = by_ref[rich.public_ref]["trust"]["evidence"][
        "evidence_completeness"
    ]
    assert rich_score > sparse_score


def test_include_trust_invalid_temperature_range_hard_failed(client, db_session):
    entry = _entry(db_session)
    make_kinetics(
        db_session,
        reaction_entry=entry,
        tmin_k=500.0,
        tmax_k=500.0,
    )

    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/kinetics"
        "?include=trust"
    ).json()
    evidence = body["records"][0]["trust"]["evidence"]

    assert evidence["label"] == "hard_failed"
    assert evidence["hard_fail_reason"] == "invalid_temperature_range"


def test_include_all_does_not_include_trust(client, db_session):
    entry = _entry(db_session)
    make_kinetics(db_session, reaction_entry=entry)

    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/kinetics"
        "?include=all"
    ).json()

    assert "trust" not in body["request"]["include"]
    assert "trust" not in body["records"][0]


def test_include_trust_exposes_record_id_when_internal_ids_allowed(
    client, db_session, allow_internal_ids
):
    entry = _entry(db_session)
    kinetics = make_kinetics(db_session, reaction_entry=entry)

    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/kinetics"
        "?include=trust,internal_ids"
    ).json()

    evidence = body["records"][0]["trust"]["evidence"]
    assert evidence["record_id"] == kinetics.id


def test_include_trust_does_not_mutate_kinetics(client, db_session):
    entry = _entry(db_session)
    kinetics = make_kinetics(db_session, reaction_entry=entry)
    before = (
        kinetics.a,
        kinetics.a_units,
        kinetics.n,
        kinetics.ea_kj_mol,
        kinetics.tmin_k,
        kinetics.tmax_k,
        kinetics.scientific_origin,
        kinetics.model_kind,
    )

    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/kinetics"
        "?include=trust"
    )
    assert resp.status_code == 200, resp.text
    db_session.refresh(kinetics)
    after = (
        kinetics.a,
        kinetics.a_units,
        kinetics.n,
        kinetics.ea_kj_mol,
        kinetics.tmin_k,
        kinetics.tmax_k,
        kinetics.scientific_origin,
        kinetics.model_kind,
    )
    assert after == before


def test_include_trust_uses_loaded_kinetics_path(client, db_session, monkeypatch):
    entry = _entry(db_session)
    make_kinetics(db_session, reaction_entry=entry)

    def fail_session_id_entrypoint(*args, **kwargs):
        raise AssertionError("read trust path must use loaded kinetics")

    monkeypatch.setattr(
        "app.services.trust.evaluator.evaluate_computed_kinetics",
        fail_session_id_entrypoint,
    )

    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.public_ref}/kinetics"
        "?include=trust"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["records"][0]["trust"]["evidence"]["record_type"] == "kinetics"
