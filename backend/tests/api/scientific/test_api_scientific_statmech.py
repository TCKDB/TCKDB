"""API tests for the scientific statmech detail + search endpoints.

Covers:

- GET  /api/v1/scientific/statmech/{statmech_ref_or_id}
- GET  /api/v1/scientific/statmech/search
- POST /api/v1/scientific/statmech/search
"""

from __future__ import annotations

from app.db.models.common import (
    CalculationType,
    RecordReviewStatus,
    StatmechCalculationRole,
    StatmechTreatmentKind,
    SubmissionRecordType,
    TorsionTreatmentKind,
)
from app.db.models.software import Software, SoftwareRelease
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from tests.services.scientific_read._factories import (
    attach_statmech_source_calculation,
    attach_statmech_torsion,
    make_calculation,
    make_conformer_group,
    make_lot,
    make_species,
    make_species_entry,
    make_statmech,
    next_inchi_key,
    set_review,
)


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


def _make_species_entry(db_session):
    species = make_species(
        db_session, smiles="CCO", inchi_key=next_inchi_key("STAT")
    )
    return species, make_species_entry(db_session, species)


def _make_statmech(db_session, **kw):
    species, entry = _make_species_entry(db_session)
    sm = make_statmech(db_session, species_entry=entry, **kw)
    return species, entry, sm


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


def _attach_freq_source(db_session, sm, *, species_entry, lot=None):
    calc = make_calculation(
        db_session,
        type=CalculationType.freq,
        species_entry_id=species_entry.id,
        lot_id=lot.id if lot is not None else None,
    )
    attach_statmech_source_calculation(
        db_session,
        statmech=sm,
        calculation=calc,
        role=StatmechCalculationRole.freq,
    )
    return calc


def _detail_url(handle: str, **params) -> str:
    base = f"/api/v1/scientific/statmech/{handle}"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


def _search_url(**params) -> str:
    base = "/api/v1/scientific/statmech/search"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


# ===========================================================================
# Detail endpoint
# ===========================================================================


def test_detail_by_ref_returns_record(client, db_session):
    _, _, sm = _make_statmech(db_session)
    resp = client.get(_detail_url(sm.public_ref))
    assert resp.status_code == 200, resp.text
    assert resp.json()["record"]["statmech"]["statmech_ref"] == sm.public_ref


def test_detail_by_integer_id_works(client, db_session):
    _, _, sm = _make_statmech(db_session)
    resp = client.get(_detail_url(str(sm.id)))
    assert resp.status_code == 200, resp.text
    assert resp.json()["record"]["statmech"]["statmech_ref"] == sm.public_ref


def test_detail_unknown_handle_returns_404(client, db_session):
    resp = client.get(_detail_url("sm_doesnotexist00000"))
    assert resp.status_code == 404
    assert "statmech not found" in resp.text


def test_detail_wrong_prefix_returns_422(client, db_session):
    resp = client.get(_detail_url("cg_abcdef0123456789"))
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_detail_malformed_handle_returns_422(client, db_session):
    resp = client.get(_detail_url("not-a-handle"))
    assert resp.status_code == 422
    assert "invalid_handle" in resp.text


def test_detail_default_response_shape(client, db_session):
    _, _, sm = _make_statmech(db_session)
    body = client.get(_detail_url(sm.public_ref)).json()
    record = body["record"]
    for key in (
        "statmech",
        "species",
        "evidence_summary",
        "available_sections",
    ):
        assert key in record
    # Heavy include blocks omitted by default.
    assert record["source_calculations"] is None
    assert record["torsions"] is None
    assert record["frequencies"] is None
    assert record["conformers"] is None
    assert record["review_history"] is None


def test_detail_review_badge_present(client, db_session):
    _, _, sm = _make_statmech(db_session)
    body = client.get(_detail_url(sm.public_ref)).json()
    assert body["record"]["statmech"]["review"]["status"] == "not_reviewed"
    assert body["review_summary"]["not_reviewed"] == 1
    assert body["review_summary"]["total"] == 1


def test_detail_species_context_present(client, db_session):
    species, entry, sm = _make_statmech(db_session)
    body = client.get(_detail_url(sm.public_ref)).json()
    sp = body["record"]["species"]
    assert sp["species_ref"] == species.public_ref
    assert sp["species_entry_ref"] == entry.public_ref
    assert sp["canonical_smiles"] == "CCO"
    assert sp["inchi_key"].rstrip() == species.inchi_key.rstrip()


def test_detail_evidence_summary_default_zero(client, db_session):
    _, _, sm = _make_statmech(db_session)
    body = client.get(_detail_url(sm.public_ref)).json()
    ev = body["record"]["evidence_summary"]
    assert ev["source_calculation_count"] == 0
    assert ev["has_freq_calculation"] is False
    assert ev["has_rotor_scans"] is False
    assert ev["torsion_count"] == 0


def test_detail_evidence_summary_with_freq_and_torsion(client, db_session):
    species, entry, sm = _make_statmech(db_session)
    _attach_freq_source(db_session, sm, species_entry=entry)
    scan = make_calculation(
        db_session, type=CalculationType.scan, species_entry_id=entry.id
    )
    attach_statmech_torsion(
        db_session, statmech=sm, source_scan_calculation=scan
    )
    body = client.get(_detail_url(sm.public_ref)).json()
    ev = body["record"]["evidence_summary"]
    assert ev["has_freq_calculation"] is True
    assert ev["has_rotor_scans"] is True
    assert ev["torsion_count"] == 1
    assert ev["source_calculation_count"] == 1


def test_detail_available_sections_present(client, db_session):
    species, entry, sm = _make_statmech(db_session)
    body = client.get(_detail_url(sm.public_ref)).json()
    sections = body["record"]["available_sections"]
    for key in (
        "has_source_calculations",
        "has_torsions",
        "has_frequencies",
        "has_conformers",
        "has_review",
    ):
        assert key in sections


def test_detail_include_source_calculations(client, db_session):
    species, entry, sm = _make_statmech(db_session)
    lot = make_lot(db_session)
    calc = _attach_freq_source(db_session, sm, species_entry=entry, lot=lot)
    body = client.get(
        _detail_url(sm.public_ref, include="source_calculations")
    ).json()
    src = body["record"]["source_calculations"]
    assert src is not None
    assert len(src) == 1
    assert src[0]["calculation_ref"] == calc.public_ref
    assert src[0]["role"] == "freq"
    assert src[0]["level_of_theory"]["method"] == "wb97xd"


def test_detail_include_torsions(client, db_session):
    species, entry, sm = _make_statmech(db_session)
    scan = make_calculation(
        db_session, type=CalculationType.scan, species_entry_id=entry.id
    )
    attach_statmech_torsion(
        db_session,
        statmech=sm,
        torsion_index=1,
        treatment_kind=TorsionTreatmentKind.hindered_rotor,
        source_scan_calculation=scan,
        atoms=(1, 2, 3, 4),
    )
    body = client.get(_detail_url(sm.public_ref, include="torsions")).json()
    tor = body["record"]["torsions"]
    assert tor is not None
    assert len(tor) == 1
    assert tor[0]["torsion_index"] == 1
    assert tor[0]["treatment_kind"] == "hindered_rotor"
    assert tor[0]["source_scan_calculation_ref"] == scan.public_ref
    assert len(tor[0]["coordinates"]) == 1
    assert tor[0]["coordinates"][0]["atom1_index"] == 1


def test_detail_include_frequencies_points_at_source_freq_calcs(
    client, db_session
):
    species, entry, sm = _make_statmech(db_session)
    calc = _attach_freq_source(db_session, sm, species_entry=entry)
    body = client.get(_detail_url(sm.public_ref, include="frequencies")).json()
    freq = body["record"]["frequencies"]
    assert freq is not None
    assert calc.public_ref in freq["source_freq_calculation_refs"]
    # Frequencies block must never inline per-mode arrays.
    assert "frequencies_cm1" not in freq
    assert "modes" not in freq


def test_detail_include_conformers_surfaces_species_entry_groups(
    client, db_session
):
    species, entry, sm = _make_statmech(db_session)
    cg = make_conformer_group(db_session, entry, label="basin_a")
    body = client.get(_detail_url(sm.public_ref, include="conformers")).json()
    confs = body["record"]["conformers"]
    assert confs is not None
    assert len(confs) == 1
    assert confs[0]["conformer_group_ref"] == cg.public_ref


def test_detail_include_review(client, db_session):
    _, _, sm = _make_statmech(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.statmech,
        record_id=sm.id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(_detail_url(sm.public_ref, include="review")).json()
    rh = body["record"]["review_history"]
    assert rh is not None
    assert rh[0]["status"] == "approved"


def test_detail_include_all_expands_all_public_tokens(client, db_session):
    _, _, sm = _make_statmech(db_session)
    body = client.get(_detail_url(sm.public_ref, include="all")).json()
    inc = body["request"]["include"]
    for token in (
        "source_calculations",
        "torsions",
        "frequencies",
        "conformers",
        "review",
    ):
        assert token in inc
    assert "internal_ids" not in inc


def test_detail_include_all_does_not_restore_internal_ids(client, db_session):
    _, _, sm = _make_statmech(db_session)
    body = client.get(_detail_url(sm.public_ref, include="all")).json()
    assert "statmech_id" not in body["record"]["statmech"]


def test_detail_internal_ids_restored_when_policy_allows(
    client, db_session, allow_internal_ids
):
    _, _, sm = _make_statmech(db_session)
    body = client.get(
        _detail_url(sm.public_ref, include="internal_ids")
    ).json()
    assert body["record"]["statmech"]["statmech_id"] == sm.id


def test_detail_internal_ids_silently_dropped_when_disallowed(
    client, db_session
):
    _, _, sm = _make_statmech(db_session)
    body = client.get(
        _detail_url(sm.public_ref, include="internal_ids")
    ).json()
    assert body["request"]["include"] == []
    assert "statmech_id" not in body["record"]["statmech"]


def test_detail_unknown_include_token_returns_422(client, db_session):
    _, _, sm = _make_statmech(db_session)
    resp = client.get(_detail_url(sm.public_ref, include="banana"))
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


def test_detail_rejected_record_still_returned_with_badge(client, db_session):
    _, _, sm = _make_statmech(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.statmech,
        record_id=sm.id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(_detail_url(sm.public_ref)).json()
    assert body["record"]["statmech"]["review"]["status"] == "rejected"


def test_detail_no_forbidden_payload_keys(client, db_session):
    species, entry, sm = _make_statmech(db_session)
    _attach_freq_source(db_session, sm, species_entry=entry)
    attach_statmech_torsion(db_session, statmech=sm)
    body = client.get(_detail_url(sm.public_ref, include="all")).json()
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
        # Statmech doesn't store frequency arrays inline — make sure
        # nothing surfaces them under the read surface either.
        "frequencies_cm1",
        "modes",
        # Conformer-style JSON blobs must never leak via the conformer
        # context.
        "representative_fingerprint_json",
        "representative_coords_json",
        "torsion_fingerprint_json",
    }

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden, (
                    f"statmech detail leaked forbidden key {k!r}"
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
    species, entry, sm = _make_statmech(db_session)
    body = client.get(
        _search_url(species_entry_ref=entry.public_ref)
    ).json()
    assert body["pagination"]["total"] == 1
    assert body["records"][0]["statmech"]["statmech_ref"] == sm.public_ref


def test_search_by_species_ref(client, db_session):
    species, entry, sm = _make_statmech(db_session)
    body = client.get(_search_url(species_ref=species.public_ref)).json()
    assert body["pagination"]["total"] == 1
    assert body["records"][0]["statmech"]["statmech_ref"] == sm.public_ref


def test_search_by_statmech_ref(client, db_session):
    _, _, sm_a = _make_statmech(db_session)
    _, _, sm_b = _make_statmech(db_session)
    body = client.get(_search_url(statmech_ref=sm_b.public_ref)).json()
    refs = {r["statmech"]["statmech_ref"] for r in body["records"]}
    assert refs == {sm_b.public_ref}


def test_search_by_conformer_group_ref(client, db_session):
    species, entry, sm = _make_statmech(db_session)
    cg = make_conformer_group(db_session, entry, label="basin_a")
    # Another unrelated statmech under a different species_entry.
    _make_statmech(db_session)
    body = client.get(
        _search_url(conformer_group_ref=cg.public_ref)
    ).json()
    refs = {r["statmech"]["statmech_ref"] for r in body["records"]}
    assert refs == {sm.public_ref}


def test_search_by_model_kind(client, db_session):
    _, _, sm_a = _make_statmech(
        db_session, statmech_treatment=StatmechTreatmentKind.rrho
    )
    _, _, sm_b = _make_statmech(
        db_session, statmech_treatment=StatmechTreatmentKind.rrho_1d
    )
    body = client.get(_search_url(model_kind="rrho_1d")).json()
    refs = {r["statmech"]["statmech_ref"] for r in body["records"]}
    assert refs == {sm_b.public_ref}


def test_search_by_has_source_calculations_true(client, db_session):
    species_a, entry_a, sm_a = _make_statmech(db_session)
    _attach_freq_source(db_session, sm_a, species_entry=entry_a)
    _make_statmech(db_session)  # no source calcs
    body = client.get(_search_url(has_source_calculations="true")).json()
    refs = {r["statmech"]["statmech_ref"] for r in body["records"]}
    assert refs == {sm_a.public_ref}


def test_search_by_has_source_calculations_false(client, db_session):
    """``has_source_calculations=false`` is meaningful — selects statmech
    rows without any source-calc evidence."""
    species_a, entry_a, sm_a = _make_statmech(db_session)
    _attach_freq_source(db_session, sm_a, species_entry=entry_a)
    _, _, sm_b = _make_statmech(db_session)
    body = client.get(_search_url(has_source_calculations="false")).json()
    refs = {r["statmech"]["statmech_ref"] for r in body["records"]}
    assert refs == {sm_b.public_ref}


def test_search_by_has_freq_calculation(client, db_session):
    species_a, entry_a, sm_a = _make_statmech(db_session)
    _attach_freq_source(db_session, sm_a, species_entry=entry_a)
    _, _, sm_b = _make_statmech(db_session)
    body = client.get(_search_url(has_freq_calculation="true")).json()
    refs = {r["statmech"]["statmech_ref"] for r in body["records"]}
    assert refs == {sm_a.public_ref}


def test_search_by_has_torsions(client, db_session):
    _, _, sm_a = _make_statmech(db_session)
    attach_statmech_torsion(db_session, statmech=sm_a)
    _, _, sm_b = _make_statmech(db_session)
    body = client.get(_search_url(has_torsions="true")).json()
    refs = {r["statmech"]["statmech_ref"] for r in body["records"]}
    assert refs == {sm_a.public_ref}


def test_search_by_has_rotor_scans(client, db_session):
    species_a, entry_a, sm_a = _make_statmech(db_session)
    scan = make_calculation(
        db_session, type=CalculationType.scan, species_entry_id=entry_a.id
    )
    attach_statmech_torsion(
        db_session, statmech=sm_a, source_scan_calculation=scan
    )
    _, _, sm_b = _make_statmech(db_session)
    attach_statmech_torsion(db_session, statmech=sm_b)
    body = client.get(_search_url(has_rotor_scans="true")).json()
    refs = {r["statmech"]["statmech_ref"] for r in body["records"]}
    assert refs == {sm_a.public_ref}


def test_search_by_method_and_basis(client, db_session):
    species_a, entry_a, sm_a = _make_statmech(db_session)
    species_b, entry_b, sm_b = _make_statmech(db_session)
    lot_a = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    lot_b = make_lot(db_session, method="b3lyp", basis="6-31g")
    _attach_freq_source(db_session, sm_a, species_entry=entry_a, lot=lot_a)
    _attach_freq_source(db_session, sm_b, species_entry=entry_b, lot=lot_b)
    body = client.get(
        _search_url(method="wb97xd", basis="def2tzvp")
    ).json()
    refs = {r["statmech"]["statmech_ref"] for r in body["records"]}
    assert refs == {sm_a.public_ref}


def test_search_by_software_and_version(client, db_session):
    species_a, entry_a, sm_a = _make_statmech(db_session)
    species_b, entry_b, sm_b = _make_statmech(db_session)
    _, sr_a = _make_software_release(
        db_session, name="gaussian", version="g16.a03"
    )
    _, sr_b = _make_software_release(db_session, name="orca", version="5.0.4")
    calc_a = _attach_freq_source(db_session, sm_a, species_entry=entry_a)
    calc_a.software_release_id = sr_a.id
    calc_b = _attach_freq_source(db_session, sm_b, species_entry=entry_b)
    calc_b.software_release_id = sr_b.id
    db_session.flush()
    body = client.get(
        _search_url(software="gaussian", software_version="g16.a03")
    ).json()
    refs = {r["statmech"]["statmech_ref"] for r in body["records"]}
    assert refs == {sm_a.public_ref}


def test_search_by_workflow_tool_and_version(client, db_session):
    species_a, entry_a, sm_a = _make_statmech(db_session)
    species_b, entry_b, sm_b = _make_statmech(db_session)
    _, wtr_a = _make_workflow_tool_release(
        db_session, name="arc", version="1.2.3"
    )
    _, wtr_b = _make_workflow_tool_release(
        db_session, name="qcelemental", version="0.27.0"
    )
    calc_a = _attach_freq_source(db_session, sm_a, species_entry=entry_a)
    calc_a.workflow_tool_release_id = wtr_a.id
    calc_b = _attach_freq_source(db_session, sm_b, species_entry=entry_b)
    calc_b.workflow_tool_release_id = wtr_b.id
    db_session.flush()
    body = client.get(
        _search_url(workflow_tool="arc", workflow_tool_version="1.2.3")
    ).json()
    refs = {r["statmech"]["statmech_ref"] for r in body["records"]}
    assert refs == {sm_a.public_ref}


def test_search_default_hides_rejected(client, db_session):
    _, _, sm_a = _make_statmech(db_session)
    _, _, sm_b = _make_statmech(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.statmech,
        record_id=sm_b.id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(_search_url(has_source_calculations="false")).json()
    refs = {r["statmech"]["statmech_ref"] for r in body["records"]}
    assert sm_a.public_ref in refs
    assert sm_b.public_ref not in refs


def test_search_include_rejected_sorts_them_last(client, db_session):
    _, _, sm_a = _make_statmech(db_session)
    _, _, sm_b = _make_statmech(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.statmech,
        record_id=sm_b.id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(
        _search_url(
            has_source_calculations="false", include_rejected="true"
        )
    ).json()
    refs = [r["statmech"]["statmech_ref"] for r in body["records"]]
    assert sm_a.public_ref in refs
    assert sm_b.public_ref in refs
    assert refs[-1] == sm_b.public_ref


def test_search_pagination_envelope(client, db_session):
    species, entry = _make_species_entry(db_session)
    for _ in range(4):
        make_statmech(db_session, species_entry=entry)
    body = client.get(
        _search_url(species_entry_ref=entry.public_ref, limit=2, offset=0)
    ).json()
    p = body["pagination"]
    assert p["limit"] == 2
    assert p["offset"] == 0
    assert p["returned"] == 2
    assert p["total"] == 4


def test_search_deterministic_ordering_review_then_created(client, db_session):
    _, _, sm_a = _make_statmech(db_session)
    _, _, sm_b = _make_statmech(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.statmech,
        record_id=sm_a.id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(_search_url(has_source_calculations="false")).json()
    refs = [r["statmech"]["statmech_ref"] for r in body["records"]]
    assert refs[0] == sm_a.public_ref


def test_search_client_sort_rejected(client, db_session):
    _, _, sm = _make_statmech(db_session)
    resp = client.get(
        _search_url(has_source_calculations="false", sort="created_at")
    )
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_search_get_post_parity(client, db_session):
    species, entry, sm = _make_statmech(db_session)
    get_body = client.get(
        _search_url(species_entry_ref=entry.public_ref)
    ).json()
    post_body = client.post(
        _search_url(), json={"species_entry_ref": entry.public_ref}
    ).json()
    assert get_body["pagination"] == post_body["pagination"]
    assert get_body["records"] == post_body["records"]


def test_search_post_rejects_query_string_search_fields(client, db_session):
    _, _, sm = _make_statmech(db_session)
    resp = client.post(
        "/api/v1/scientific/statmech/search?limit=5",
        json={"has_source_calculations": True},
    )
    assert resp.status_code == 422
    assert "post_search_fields_must_be_in_body" in resp.text


def test_search_include_source_calculations_on_records(client, db_session):
    species, entry, sm = _make_statmech(db_session)
    calc = _attach_freq_source(db_session, sm, species_entry=entry)
    body = client.get(
        _search_url(
            statmech_ref=sm.public_ref, include="source_calculations"
        )
    ).json()
    rec = body["records"][0]
    assert rec["source_calculations"] is not None
    assert rec["source_calculations"][0]["calculation_ref"] == calc.public_ref


def test_search_include_torsions_on_records(client, db_session):
    _, _, sm = _make_statmech(db_session)
    attach_statmech_torsion(db_session, statmech=sm)
    body = client.get(
        _search_url(statmech_ref=sm.public_ref, include="torsions")
    ).json()
    rec = body["records"][0]
    assert rec["torsions"] is not None
    assert len(rec["torsions"]) == 1


def test_search_include_conformers_on_records(client, db_session):
    species, entry, sm = _make_statmech(db_session)
    cg = make_conformer_group(db_session, entry, label="basin_a")
    body = client.get(
        _search_url(statmech_ref=sm.public_ref, include="conformers")
    ).json()
    rec = body["records"][0]
    assert rec["conformers"] is not None
    assert rec["conformers"][0]["conformer_group_ref"] == cg.public_ref


def test_search_include_review_on_records(client, db_session):
    _, _, sm = _make_statmech(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.statmech,
        record_id=sm.id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(
        _search_url(statmech_ref=sm.public_ref, include="review")
    ).json()
    rec = body["records"][0]
    assert rec["review_history"] is not None
    assert rec["review_history"][0]["status"] == "approved"


def test_search_include_all_on_records(client, db_session):
    _, _, sm = _make_statmech(db_session)
    body = client.get(
        _search_url(statmech_ref=sm.public_ref, include="all")
    ).json()
    inc = body["request"]["include"]
    for token in (
        "source_calculations",
        "torsions",
        "frequencies",
        "conformers",
        "review",
    ):
        assert token in inc
    assert "internal_ids" not in inc


def test_search_include_all_does_not_restore_internal_ids(client, db_session):
    _, _, sm = _make_statmech(db_session)
    body = client.get(
        _search_url(statmech_ref=sm.public_ref, include="all")
    ).json()
    assert "statmech_id" not in body["records"][0]["statmech"]


def test_search_internal_ids_restored_when_policy_allows(
    client, db_session, allow_internal_ids
):
    _, _, sm = _make_statmech(db_session)
    body = client.get(
        _search_url(statmech_ref=sm.public_ref, include="internal_ids")
    ).json()
    assert body["records"][0]["statmech"]["statmech_id"] == sm.id


def test_search_record_shape_matches_detail(client, db_session):
    """Anti-drift: per-record search payload equals detail record for
    the same statmech and include set."""
    species, entry, sm = _make_statmech(db_session)
    _attach_freq_source(db_session, sm, species_entry=entry)
    search_body = client.get(
        _search_url(
            statmech_ref=sm.public_ref, include="source_calculations"
        )
    ).json()
    detail_body = client.get(
        _detail_url(sm.public_ref, include="source_calculations")
    ).json()
    assert search_body["records"][0] == detail_body["record"]


def test_search_unknown_ref_short_circuits_empty(client, db_session):
    body = client.get(
        _search_url(species_entry_ref="spe_doesnotexist00")
    ).json()
    assert body["pagination"]["total"] == 0
    assert body["records"] == []


def test_search_wrong_prefix_ref_returns_422(client, db_session):
    resp = client.get(_search_url(species_entry_ref="sm_abcdef0123456789"))
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_search_no_forbidden_payload_keys(client, db_session):
    species, entry, sm = _make_statmech(db_session)
    _attach_freq_source(db_session, sm, species_entry=entry)
    attach_statmech_torsion(db_session, statmech=sm)
    body = client.get(
        _search_url(statmech_ref=sm.public_ref, include="all")
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
        "frequencies_cm1",
        "modes",
        "representative_fingerprint_json",
        "representative_coords_json",
        "torsion_fingerprint_json",
    }

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden, (
                    f"statmech search leaked forbidden key {k!r}"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)
