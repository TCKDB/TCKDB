"""API tests for the scientific transport detail + search endpoints."""

from __future__ import annotations

from app.db.models.common import (
    CalculationType,
    RecordReviewStatus,
    ScientificOriginKind,
    SubmissionRecordType,
    TransportCalculationRole,
    ValidationStatus,
)
from app.db.models.software import Software, SoftwareRelease
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from tests.services.scientific_read._factories import (
    attach_artifact,
    attach_geometry_validation,
    attach_sp_result,
    attach_transport_source_calculation,
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
    make_transport,
    next_inchi_key,
    set_review,
)

# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


def _make_species_entry(db_session):
    species = make_species(
        db_session, smiles="CCO", inchi_key=next_inchi_key("TRAN")
    )
    return species, make_species_entry(db_session, species)


def _make_transport(db_session, **kw):
    species, entry = _make_species_entry(db_session)
    tr = make_transport(db_session, species_entry=entry, **kw)
    return species, entry, tr


def _make_software_release(db_session, *, name="gaussian", version="g16.a03"):
    sw = Software(name=name)
    db_session.add(sw)
    db_session.flush()
    sr = SoftwareRelease(software_id=sw.id, version=version)
    db_session.add(sr)
    db_session.flush()
    return sw, sr


def _make_workflow_tool_release(db_session, *, name="arc", version="1.2.3"):
    wt = WorkflowTool(name=name)
    db_session.add(wt)
    db_session.flush()
    wtr = WorkflowToolRelease(workflow_tool_id=wt.id, version=version)
    db_session.add(wtr)
    db_session.flush()
    return wt, wtr


def _attach_source(db_session, tr, *, species_entry, lot=None, role=None):
    calc = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=species_entry.id,
        lot_id=lot.id if lot is not None else None,
    )
    attach_transport_source_calculation(
        db_session,
        transport=tr,
        calculation=calc,
        role=role or TransportCalculationRole.full_transport,
    )
    return calc


def _attach_supported_source(db_session, tr, *, species_entry, role=None):
    lot = make_lot(db_session)
    calc = _attach_source(
        db_session, tr, species_entry=species_entry, lot=lot, role=role
    )
    attach_sp_result(db_session, calculation=calc, electronic_energy_hartree=-76.4)
    attach_artifact(db_session, calculation=calc)
    attach_geometry_validation(db_session, calculation=calc)
    return calc


def _detail_url(handle: str, **params) -> str:
    base = f"/api/v1/scientific/transport/{handle}"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


def _search_url(**params) -> str:
    base = "/api/v1/scientific/transport/search"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


# ===========================================================================
# Detail endpoint
# ===========================================================================


def test_detail_by_ref_returns_record(client, db_session):
    _, _, tr = _make_transport(db_session)
    resp = client.get(_detail_url(tr.public_ref))
    assert resp.status_code == 200, resp.text
    assert resp.json()["record"]["transport"]["transport_ref"] == tr.public_ref


def test_detail_by_integer_id_works(client, db_session):
    _, _, tr = _make_transport(db_session)
    resp = client.get(_detail_url(str(tr.id)))
    assert resp.status_code == 200, resp.text


def test_detail_unknown_handle_returns_404(client, db_session):
    resp = client.get(_detail_url("trn_doesnotexist00000"))
    assert resp.status_code == 404
    assert "transport not found" in resp.text


def test_detail_wrong_prefix_returns_422(client, db_session):
    resp = client.get(_detail_url("sm_abcdef0123456789"))
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_detail_malformed_handle_returns_422(client, db_session):
    resp = client.get(_detail_url("not-a-handle"))
    assert resp.status_code == 422
    assert "invalid_handle" in resp.text


def test_detail_default_response_shape(client, db_session):
    _, _, tr = _make_transport(db_session)
    body = client.get(_detail_url(tr.public_ref)).json()
    record = body["record"]
    for key in (
        "transport",
        "species",
        "evidence_summary",
        "available_sections",
    ):
        assert key in record
    assert record["source_calculations"] is None
    assert record["review_history"] is None


def test_detail_review_badge_present(client, db_session):
    _, _, tr = _make_transport(db_session)
    body = client.get(_detail_url(tr.public_ref)).json()
    assert body["record"]["transport"]["review"]["status"] == "not_reviewed"
    assert body["review_summary"]["not_reviewed"] == 1
    assert body["review_summary"]["total"] == 1


def test_detail_species_context_present(client, db_session):
    species, entry, tr = _make_transport(db_session)
    body = client.get(_detail_url(tr.public_ref)).json()
    sp = body["record"]["species"]
    assert sp["species_ref"] == species.public_ref
    assert sp["species_entry_ref"] == entry.public_ref
    assert sp["canonical_smiles"] == "CCO"
    assert sp["inchi_key"].rstrip() == species.inchi_key.rstrip()


def test_detail_scalar_transport_parameters_present(client, db_session):
    _, _, tr = _make_transport(
        db_session,
        sigma_angstrom=3.7,
        epsilon_over_k_k=200.5,
        dipole_debye=1.85,
        polarizability_angstrom3=1.45,
        rotational_relaxation=2.0,
    )
    core = client.get(_detail_url(tr.public_ref)).json()["record"]["transport"]
    assert core["sigma_angstrom"] == 3.7
    assert core["epsilon_over_k_k"] == 200.5
    assert core["dipole_debye"] == 1.85
    assert core["polarizability_angstrom3"] == 1.45
    assert core["rotational_relaxation"] == 2.0


def test_detail_evidence_summary_default(client, db_session):
    _, _, tr = _make_transport(db_session)
    ev = client.get(_detail_url(tr.public_ref)).json()["record"]["evidence_summary"]
    assert ev["source_calculation_count"] == 0
    assert ev["has_source_calculations"] is False
    assert ev["has_lj_parameters"] is True  # factory default sets sigma+epsilon
    assert ev["has_dipole_moment"] is False
    assert ev["has_polarizability"] is False
    assert ev["has_rotational_relaxation"] is False
    assert ev["has_literature_source"] is False


def test_detail_evidence_summary_with_source(client, db_session):
    species, entry, tr = _make_transport(db_session)
    _attach_source(db_session, tr, species_entry=entry)
    ev = client.get(_detail_url(tr.public_ref)).json()["record"]["evidence_summary"]
    assert ev["has_source_calculations"] is True
    assert ev["source_calculation_count"] == 1


def test_detail_available_sections_present(client, db_session):
    _, _, tr = _make_transport(db_session)
    sections = client.get(_detail_url(tr.public_ref)).json()["record"][
        "available_sections"
    ]
    assert "has_source_calculations" in sections
    assert "has_review" in sections


def test_detail_include_source_calculations(client, db_session):
    species, entry, tr = _make_transport(db_session)
    lot = make_lot(db_session)
    calc = _attach_source(db_session, tr, species_entry=entry, lot=lot)
    body = client.get(
        _detail_url(tr.public_ref, include="source_calculations")
    ).json()
    src = body["record"]["source_calculations"]
    assert src is not None
    assert len(src) == 1
    assert src[0]["calculation_ref"] == calc.public_ref
    assert src[0]["role"] == "full_transport"
    assert src[0]["level_of_theory"]["method"] == "wb97xd"


def test_detail_include_review(client, db_session):
    _, _, tr = _make_transport(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transport,
        record_id=tr.id,
        status=RecordReviewStatus.approved,
    )
    rh = client.get(_detail_url(tr.public_ref, include="review")).json()[
        "record"
    ]["review_history"]
    assert rh is not None
    assert rh[0]["status"] == "approved"


def test_detail_include_all_expands_all_public_tokens(client, db_session):
    _, _, tr = _make_transport(db_session)
    body = client.get(_detail_url(tr.public_ref, include="all")).json()
    inc = body["request"]["include"]
    assert "source_calculations" in inc
    assert "review" in inc
    assert "internal_ids" not in inc
    assert "trust" not in inc
    assert "trust" not in body["record"]


def test_detail_default_response_omits_trust(client, db_session):
    _, _, tr = _make_transport(db_session)
    body = client.get(_detail_url(tr.public_ref)).json()
    assert "trust" not in body["record"]


def test_detail_include_trust_returns_transport_fragment(client, db_session):
    _, _, tr = _make_transport(db_session)
    body = client.get(_detail_url(tr.public_ref, include="trust")).json()
    trust = body["record"]["trust"]
    assert trust["review_status"] == "not_reviewed"
    assert trust["evidence"]["record_type"] == "transport"
    assert trust["evidence"]["rubric"] == "computed_transport_v1"
    assert trust["evidence"]["rubric_version"] == 1
    assert trust["llm_precheck"] == {
        "enabled": False,
        "label": "not_run",
        "summary": None,
    }
    assert trust["is_certified"] is False


def test_detail_include_trust_uses_review_badge(client, db_session):
    _, _, tr = _make_transport(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transport,
        record_id=tr.id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(_detail_url(tr.public_ref, include="trust")).json()
    assert body["record"]["trust"]["review_status"] == "approved"


def test_detail_include_trust_sparse_transport_reports_missing_checks(
    client, db_session
):
    _, _, tr = _make_transport(db_session)
    body = client.get(_detail_url(tr.public_ref, include="trust")).json()
    evidence = body["record"]["trust"]["evidence"]
    assert evidence["label"] in {"sparse", "partial"}
    assert "source_calculations_present" in evidence["missing_checks"]


def test_detail_include_trust_source_calculation_scores_higher(
    client, db_session
):
    _, _, sparse = _make_transport(db_session)
    _, entry, supported = _make_transport(db_session)
    _attach_supported_source(db_session, supported, species_entry=entry)

    sparse_evidence = client.get(
        _detail_url(sparse.public_ref, include="trust")
    ).json()["record"]["trust"]["evidence"]
    supported_evidence = client.get(
        _detail_url(supported.public_ref, include="trust")
    ).json()["record"]["trust"]["evidence"]

    assert (
        supported_evidence["evidence_completeness"]
        > sparse_evidence["evidence_completeness"]
    )


def test_detail_include_trust_lj_pair_checks_pass(client, db_session):
    _, _, tr = _make_transport(db_session)
    body = client.get(_detail_url(tr.public_ref, include="trust")).json()
    passed = set(body["record"]["trust"]["evidence"]["passed_checks"])
    assert {
        "lj_pair_present_if_applicable",
        "sigma_present",
        "epsilon_present",
    } <= passed


def test_detail_include_trust_property_source_checks_pass(client, db_session):
    _, entry, tr = _make_transport(
        db_session,
        dipole_debye=1.85,
        polarizability_angstrom3=1.45,
    )
    _attach_supported_source(
        db_session,
        tr,
        species_entry=entry,
        role=TransportCalculationRole.dipole,
    )
    _attach_supported_source(
        db_session,
        tr,
        species_entry=entry,
        role=TransportCalculationRole.polarizability,
    )

    body = client.get(_detail_url(tr.public_ref, include="trust")).json()
    passed = set(body["record"]["trust"]["evidence"]["passed_checks"])
    assert "dipole_present" in passed
    assert "polarizability_present" in passed
    assert "dipole_source_present_if_dipole_present" in passed
    assert "polarizability_source_present_if_polarizability_present" in passed


def test_detail_include_trust_no_property_hard_fail(client, db_session):
    _, _, tr = _make_transport(
        db_session,
        sigma_angstrom=None,
        epsilon_over_k_k=None,
    )
    body = client.get(_detail_url(tr.public_ref, include="trust")).json()
    evidence = body["record"]["trust"]["evidence"]
    assert evidence["label"] == "hard_failed"
    assert evidence["hard_fail_reason"] == "no_transport_property_present"


def test_detail_include_trust_source_calc_hard_fail(client, db_session):
    _, entry, tr = _make_transport(db_session)
    calc = _attach_supported_source(db_session, tr, species_entry=entry)
    calc.geometry_validation.validation_status = ValidationStatus.fail
    calc.geometry_validation.is_isomorphic = False

    body = client.get(_detail_url(tr.public_ref, include="trust")).json()
    evidence = body["record"]["trust"]["evidence"]
    assert evidence["label"] == "hard_failed"
    assert (
        evidence["hard_fail_reason"]
        == "source_calculation_hard_failed_for_required_role"
    )


def test_detail_include_trust_preserves_internal_id_policy(client, db_session):
    _, _, tr = _make_transport(db_session)
    body = client.get(_detail_url(tr.public_ref, include="trust")).json()
    assert "record_id" not in body["record"]["trust"]["evidence"]


def test_detail_include_trust_internal_id_policy_allows_record_id(
    client, db_session, allow_internal_ids
):
    _, _, tr = _make_transport(db_session)
    body = client.get(_detail_url(tr.public_ref, include="trust,internal_ids")).json()
    assert body["record"]["trust"]["evidence"]["record_id"] == tr.id


def test_detail_include_trust_does_not_mutate_transport(client, db_session):
    _, _, tr = _make_transport(db_session)
    before = (
        tr.sigma_angstrom,
        tr.epsilon_over_k_k,
        tr.dipole_debye,
        tr.polarizability_angstrom3,
        tr.rotational_relaxation,
    )
    resp = client.get(_detail_url(tr.public_ref, include="trust"))
    assert resp.status_code == 200, resp.text
    db_session.refresh(tr)
    after = (
        tr.sigma_angstrom,
        tr.epsilon_over_k_k,
        tr.dipole_debye,
        tr.polarizability_angstrom3,
        tr.rotational_relaxation,
    )
    assert after == before


def test_detail_trust_path_uses_loaded_evaluator(
    client, db_session, monkeypatch
):
    from app.services.scientific_read import transport as transport_service
    from app.services.trust import evaluate_loaded_transport

    _, _, tr = _make_transport(db_session)
    calls = 0

    def counted_loaded_evaluator(transport):
        nonlocal calls
        calls += 1
        return evaluate_loaded_transport(transport)

    monkeypatch.setattr(
        transport_service,
        "evaluate_loaded_transport",
        counted_loaded_evaluator,
    )

    resp = client.get(_detail_url(tr.public_ref, include="trust"))
    assert resp.status_code == 200, resp.text
    assert calls == 1


def test_detail_include_all_does_not_restore_internal_ids(client, db_session):
    _, _, tr = _make_transport(db_session)
    body = client.get(_detail_url(tr.public_ref, include="all")).json()
    assert "transport_id" not in body["record"]["transport"]


def test_detail_internal_ids_restored_when_policy_allows(
    client, db_session, allow_internal_ids
):
    _, _, tr = _make_transport(db_session)
    body = client.get(
        _detail_url(tr.public_ref, include="internal_ids")
    ).json()
    assert body["record"]["transport"]["transport_id"] == tr.id


def test_detail_internal_ids_silently_dropped_when_disallowed(
    client, db_session
):
    _, _, tr = _make_transport(db_session)
    body = client.get(
        _detail_url(tr.public_ref, include="internal_ids")
    ).json()
    assert body["request"]["include"] == []
    assert "transport_id" not in body["record"]["transport"]


def test_detail_unknown_include_token_returns_422(client, db_session):
    _, _, tr = _make_transport(db_session)
    resp = client.get(_detail_url(tr.public_ref, include="banana"))
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


def test_detail_rejected_record_still_returned_with_badge(client, db_session):
    _, _, tr = _make_transport(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transport,
        record_id=tr.id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(_detail_url(tr.public_ref)).json()
    assert body["record"]["transport"]["review"]["status"] == "rejected"


def test_detail_no_forbidden_payload_keys(client, db_session):
    species, entry, tr = _make_transport(db_session)
    _attach_source(db_session, tr, species_entry=entry)
    body = client.get(_detail_url(tr.public_ref, include="all")).json()
    forbidden = {
        "xyz_text",
        "atoms",
        "coords",
        "symbols",
        "body",
        "content",
        "data",
        "presigned_url",
        "download_url",
    }

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden, (
                    f"transport detail leaked forbidden key {k!r}"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)


# ===========================================================================
# Search endpoint
# ===========================================================================


def test_search_missing_filter_returns_422_get(client, db_session):
    resp = client.get(_search_url())
    assert resp.status_code == 422
    assert "missing_filter" in resp.text


def test_search_missing_filter_returns_422_post(client, db_session):
    resp = client.post(_search_url(), json={"limit": 50})
    assert resp.status_code == 422
    assert "missing_filter" in resp.text


def test_search_by_species_entry_ref(client, db_session):
    species, entry, tr = _make_transport(db_session)
    body = client.get(
        _search_url(species_entry_ref=entry.public_ref)
    ).json()
    assert body["pagination"]["total"] == 1
    assert body["records"][0]["transport"]["transport_ref"] == tr.public_ref


def test_search_by_species_ref(client, db_session):
    species, _, tr = _make_transport(db_session)
    body = client.get(_search_url(species_ref=species.public_ref)).json()
    assert body["pagination"]["total"] == 1
    assert body["records"][0]["transport"]["transport_ref"] == tr.public_ref


def test_search_by_transport_ref(client, db_session):
    _, _, tr_a = _make_transport(db_session)
    _, _, tr_b = _make_transport(db_session)
    body = client.get(_search_url(transport_ref=tr_b.public_ref)).json()
    refs = {r["transport"]["transport_ref"] for r in body["records"]}
    assert refs == {tr_b.public_ref}


def test_search_by_model_kind(client, db_session):
    _, _, tr_a = _make_transport(
        db_session, scientific_origin=ScientificOriginKind.computed
    )
    _, _, tr_b = _make_transport(
        db_session, scientific_origin=ScientificOriginKind.experimental
    )
    body = client.get(_search_url(model_kind="experimental")).json()
    refs = {r["transport"]["transport_ref"] for r in body["records"]}
    assert refs == {tr_b.public_ref}


def test_search_by_has_source_calculations_true(client, db_session):
    species_a, entry_a, tr_a = _make_transport(db_session)
    _attach_source(db_session, tr_a, species_entry=entry_a)
    _make_transport(db_session)
    body = client.get(_search_url(has_source_calculations="true")).json()
    refs = {r["transport"]["transport_ref"] for r in body["records"]}
    assert refs == {tr_a.public_ref}


def test_search_by_has_source_calculations_false(client, db_session):
    """``has_source_calculations=false`` is meaningful — selects
    transport rows without supporting calculations."""
    species_a, entry_a, tr_a = _make_transport(db_session)
    _attach_source(db_session, tr_a, species_entry=entry_a)
    _, _, tr_b = _make_transport(db_session)
    body = client.get(_search_url(has_source_calculations="false")).json()
    refs = {r["transport"]["transport_ref"] for r in body["records"]}
    assert refs == {tr_b.public_ref}


def test_search_by_has_lj_parameters_true_and_false(client, db_session):
    _, _, tr_with_lj = _make_transport(db_session)
    _, _, tr_without_lj = _make_transport(
        db_session, sigma_angstrom=None, epsilon_over_k_k=None
    )
    body_true = client.get(_search_url(has_lj_parameters="true")).json()
    body_false = client.get(_search_url(has_lj_parameters="false")).json()
    true_refs = {r["transport"]["transport_ref"] for r in body_true["records"]}
    false_refs = {r["transport"]["transport_ref"] for r in body_false["records"]}
    assert tr_with_lj.public_ref in true_refs
    assert tr_with_lj.public_ref not in false_refs
    assert tr_without_lj.public_ref in false_refs
    assert tr_without_lj.public_ref not in true_refs


def test_search_by_has_dipole_moment_true_and_false(client, db_session):
    _, _, tr_a = _make_transport(db_session, dipole_debye=1.85)
    _, _, tr_b = _make_transport(db_session)
    body_true = client.get(_search_url(has_dipole_moment="true")).json()
    body_false = client.get(_search_url(has_dipole_moment="false")).json()
    true_refs = {r["transport"]["transport_ref"] for r in body_true["records"]}
    false_refs = {r["transport"]["transport_ref"] for r in body_false["records"]}
    assert tr_a.public_ref in true_refs
    assert tr_b.public_ref in false_refs


def test_search_by_has_polarizability_true_and_false(client, db_session):
    _, _, tr_a = _make_transport(db_session, polarizability_angstrom3=1.45)
    _, _, tr_b = _make_transport(db_session)
    true_refs = {
        r["transport"]["transport_ref"]
        for r in client.get(_search_url(has_polarizability="true")).json()[
            "records"
        ]
    }
    false_refs = {
        r["transport"]["transport_ref"]
        for r in client.get(_search_url(has_polarizability="false")).json()[
            "records"
        ]
    }
    assert tr_a.public_ref in true_refs
    assert tr_b.public_ref in false_refs


def test_search_by_has_rotational_relaxation_true_and_false(
    client, db_session
):
    _, _, tr_a = _make_transport(db_session, rotational_relaxation=2.0)
    _, _, tr_b = _make_transport(db_session)
    true_refs = {
        r["transport"]["transport_ref"]
        for r in client.get(
            _search_url(has_rotational_relaxation="true")
        ).json()["records"]
    }
    false_refs = {
        r["transport"]["transport_ref"]
        for r in client.get(
            _search_url(has_rotational_relaxation="false")
        ).json()["records"]
    }
    assert tr_a.public_ref in true_refs
    assert tr_b.public_ref in false_refs


def test_search_by_method_and_basis(client, db_session):
    species_a, entry_a, tr_a = _make_transport(db_session)
    species_b, entry_b, tr_b = _make_transport(db_session)
    lot_a = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    lot_b = make_lot(db_session, method="b3lyp", basis="6-31g")
    _attach_source(db_session, tr_a, species_entry=entry_a, lot=lot_a)
    _attach_source(db_session, tr_b, species_entry=entry_b, lot=lot_b)
    body = client.get(
        _search_url(method="wb97xd", basis="def2tzvp")
    ).json()
    refs = {r["transport"]["transport_ref"] for r in body["records"]}
    assert refs == {tr_a.public_ref}


def test_search_by_software_and_version(client, db_session):
    species_a, entry_a, tr_a = _make_transport(db_session)
    species_b, entry_b, tr_b = _make_transport(db_session)
    _, sr_a = _make_software_release(
        db_session, name="gaussian", version="g16.a03"
    )
    _, sr_b = _make_software_release(db_session, name="orca", version="5.0.4")
    calc_a = _attach_source(db_session, tr_a, species_entry=entry_a)
    calc_a.software_release_id = sr_a.id
    calc_b = _attach_source(db_session, tr_b, species_entry=entry_b)
    calc_b.software_release_id = sr_b.id
    db_session.flush()
    body = client.get(
        _search_url(software="gaussian", software_version="g16.a03")
    ).json()
    refs = {r["transport"]["transport_ref"] for r in body["records"]}
    assert refs == {tr_a.public_ref}


def test_search_by_workflow_tool_and_version(client, db_session):
    species_a, entry_a, tr_a = _make_transport(db_session)
    species_b, entry_b, tr_b = _make_transport(db_session)
    _, wtr_a = _make_workflow_tool_release(
        db_session, name="arc", version="1.2.3"
    )
    _, wtr_b = _make_workflow_tool_release(
        db_session, name="qcelemental", version="0.27.0"
    )
    calc_a = _attach_source(db_session, tr_a, species_entry=entry_a)
    calc_a.workflow_tool_release_id = wtr_a.id
    calc_b = _attach_source(db_session, tr_b, species_entry=entry_b)
    calc_b.workflow_tool_release_id = wtr_b.id
    db_session.flush()
    body = client.get(
        _search_url(workflow_tool="arc", workflow_tool_version="1.2.3")
    ).json()
    refs = {r["transport"]["transport_ref"] for r in body["records"]}
    assert refs == {tr_a.public_ref}


def test_search_default_hides_rejected(client, db_session):
    _, _, tr_a = _make_transport(db_session)
    _, _, tr_b = _make_transport(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transport,
        record_id=tr_b.id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(_search_url(has_source_calculations="false")).json()
    refs = {r["transport"]["transport_ref"] for r in body["records"]}
    assert tr_a.public_ref in refs
    assert tr_b.public_ref not in refs


def test_search_include_rejected_sorts_them_last(client, db_session):
    _, _, tr_a = _make_transport(db_session)
    _, _, tr_b = _make_transport(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transport,
        record_id=tr_b.id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(
        _search_url(
            has_source_calculations="false", include_rejected="true"
        )
    ).json()
    refs = [r["transport"]["transport_ref"] for r in body["records"]]
    assert tr_a.public_ref in refs
    assert tr_b.public_ref in refs
    assert refs[-1] == tr_b.public_ref


def test_search_pagination_envelope(client, db_session):
    species, entry = _make_species_entry(db_session)
    for _ in range(4):
        make_transport(db_session, species_entry=entry)
    body = client.get(
        _search_url(species_entry_ref=entry.public_ref, limit=2, offset=0)
    ).json()
    p = body["pagination"]
    assert p["limit"] == 2
    assert p["offset"] == 0
    assert p["returned"] == 2
    assert p["total"] == 4


def test_search_deterministic_ordering_review_then_created(client, db_session):
    _, _, tr_a = _make_transport(db_session)
    _, _, tr_b = _make_transport(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transport,
        record_id=tr_a.id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(_search_url(has_source_calculations="false")).json()
    refs = [r["transport"]["transport_ref"] for r in body["records"]]
    assert refs[0] == tr_a.public_ref


def test_search_client_sort_rejected(client, db_session):
    _, _, tr = _make_transport(db_session)
    resp = client.get(
        _search_url(has_source_calculations="false", sort="created_at")
    )
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_search_get_post_parity(client, db_session):
    species, entry, tr = _make_transport(db_session)
    get_body = client.get(
        _search_url(species_entry_ref=entry.public_ref)
    ).json()
    post_body = client.post(
        _search_url(), json={"species_entry_ref": entry.public_ref}
    ).json()
    assert get_body["pagination"] == post_body["pagination"]
    assert get_body["records"] == post_body["records"]


def test_search_post_rejects_query_string_search_fields(client, db_session):
    _, _, tr = _make_transport(db_session)
    resp = client.post(
        "/api/v1/scientific/transport/search?limit=5",
        json={"has_source_calculations": True},
    )
    assert resp.status_code == 422
    assert "post_search_fields_must_be_in_body" in resp.text


def test_search_include_source_calculations_on_records(client, db_session):
    species, entry, tr = _make_transport(db_session)
    calc = _attach_source(db_session, tr, species_entry=entry)
    body = client.get(
        _search_url(
            transport_ref=tr.public_ref, include="source_calculations"
        )
    ).json()
    rec = body["records"][0]
    assert rec["source_calculations"] is not None
    assert rec["source_calculations"][0]["calculation_ref"] == calc.public_ref


def test_search_include_review_on_records(client, db_session):
    _, _, tr = _make_transport(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transport,
        record_id=tr.id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(
        _search_url(transport_ref=tr.public_ref, include="review")
    ).json()
    rec = body["records"][0]
    assert rec["review_history"] is not None
    assert rec["review_history"][0]["status"] == "approved"


def test_search_include_all_on_records(client, db_session):
    _, _, tr = _make_transport(db_session)
    body = client.get(
        _search_url(transport_ref=tr.public_ref, include="all")
    ).json()
    inc = body["request"]["include"]
    assert "source_calculations" in inc
    assert "review" in inc
    assert "internal_ids" not in inc
    # Broad search never exposes trust (detail-only token).
    assert "trust" not in inc
    assert "trust" not in body["records"][0]


def test_search_include_all_does_not_restore_internal_ids(client, db_session):
    _, _, tr = _make_transport(db_session)
    body = client.get(
        _search_url(transport_ref=tr.public_ref, include="all")
    ).json()
    assert "transport_id" not in body["records"][0]["transport"]


def test_search_include_trust_returns_422(client, db_session):
    """Broad transport search rejects ``include=trust`` — trust is a
    detail/subresource-only token, never exposed on list/search."""
    _, _, tr = _make_transport(db_session)
    resp = client.get(_search_url(transport_ref=tr.public_ref, include="trust"))
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


def test_search_internal_ids_restored_when_policy_allows(
    client, db_session, allow_internal_ids
):
    _, _, tr = _make_transport(db_session)
    body = client.get(
        _search_url(transport_ref=tr.public_ref, include="internal_ids")
    ).json()
    assert body["records"][0]["transport"]["transport_id"] == tr.id


def test_search_record_shape_matches_detail(client, db_session):
    """Cross-endpoint anti-drift."""
    species, entry, tr = _make_transport(db_session)
    _attach_source(db_session, tr, species_entry=entry)
    search_body = client.get(
        _search_url(
            transport_ref=tr.public_ref, include="source_calculations"
        )
    ).json()
    detail_body = client.get(
        _detail_url(tr.public_ref, include="source_calculations")
    ).json()
    assert search_body["records"][0] == detail_body["record"]


def test_search_unknown_ref_short_circuits_empty(client, db_session):
    body = client.get(
        _search_url(species_entry_ref="spe_doesnotexist00")
    ).json()
    assert body["pagination"]["total"] == 0
    assert body["records"] == []


def test_search_wrong_prefix_ref_returns_422(client, db_session):
    resp = client.get(_search_url(species_entry_ref="trn_abcdef0123456789"))
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_search_no_forbidden_payload_keys(client, db_session):
    species, entry, tr = _make_transport(db_session)
    _attach_source(db_session, tr, species_entry=entry)
    body = client.get(
        _search_url(transport_ref=tr.public_ref, include="all")
    ).json()
    forbidden = {
        "xyz_text",
        "atoms",
        "coords",
        "symbols",
        "body",
        "content",
        "data",
        "presigned_url",
        "download_url",
    }

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden, (
                    f"transport search leaked forbidden key {k!r}"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)
