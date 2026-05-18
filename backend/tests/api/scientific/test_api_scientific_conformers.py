"""API tests for the scientific conformer detail endpoints.

Covers:

- GET /api/v1/scientific/conformer-groups/{conformer_group_ref_or_id}
- GET /api/v1/scientific/conformer-observations/{conformer_observation_ref_or_id}
"""

from __future__ import annotations

from app.db.models.calculation import CalculationOutputGeometry
from app.db.models.common import (
    CalculationGeometryRole,
    CalculationType,
    ConformerSelectionKind,
    RecordReviewStatus,
    ScientificOriginKind,
    SubmissionRecordType,
)
from tests.services.scientific_read._factories import (
    attach_conformer_selection,
    attach_geometry_validation,
    attach_scf_stability,
    make_calculation,
    make_calculation_with_conformer,
    make_conformer_group,
    make_conformer_observation,
    make_geometry,
    make_lot,
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)


# ---------------------------------------------------------------------------
# Local fixtures
# ---------------------------------------------------------------------------


def _make_species_entry(db_session):
    species = make_species(
        db_session, smiles="CC", inchi_key=next_inchi_key("CONF")
    )
    return species, make_species_entry(db_session, species)


def _make_group(db_session, *, label="basin_a"):
    _, entry = _make_species_entry(db_session)
    cg = make_conformer_group(db_session, entry, label=label)
    return entry, cg


def _make_group_with_obs(
    db_session,
    *,
    label="basin_a",
    n_observations=1,
    origin=ScientificOriginKind.computed,
):
    entry, cg = _make_group(db_session, label=label)
    obs = [
        make_conformer_observation(
            db_session,
            conformer_group=cg,
            torsion_fingerprint_json={"hash": f"fp-{i}"},
        )
        for i in range(n_observations)
    ]
    # Force scientific_origin where requested.
    for o in obs:
        if o.scientific_origin != origin:
            o.scientific_origin = origin
            db_session.flush()
    return entry, cg, obs


def _attach_calc(
    db_session,
    *,
    species_entry,
    conformer_observation,
    calc_type=CalculationType.opt,
    with_geom=False,
):
    calc = make_calculation_with_conformer(
        db_session,
        species_entry=species_entry,
        conformer_observation=conformer_observation,
        type=calc_type,
    )
    if with_geom:
        geom = make_geometry(db_session, natoms=4)
        db_session.add(
            CalculationOutputGeometry(
                calculation_id=calc.id,
                output_order=1,
                geometry_id=geom.id,
                role=CalculationGeometryRole.final,
            )
        )
        db_session.flush()
        return calc, geom
    return calc, None


def _cg_url(handle: str, **params) -> str:
    base = f"/api/v1/scientific/conformer-groups/{handle}"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


def _co_url(handle: str, **params) -> str:
    base = f"/api/v1/scientific/conformer-observations/{handle}"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


# ===========================================================================
# Conformer-group detail
# ===========================================================================


def test_cg_detail_by_ref_returns_record(client, db_session):
    _, cg, _ = _make_group_with_obs(db_session)
    resp = client.get(_cg_url(cg.public_ref))
    assert resp.status_code == 200, resp.text
    assert resp.json()["record"]["conformer_group"]["conformer_group_ref"] == cg.public_ref


def test_cg_detail_by_integer_id_works(client, db_session):
    _, cg, _ = _make_group_with_obs(db_session)
    resp = client.get(_cg_url(str(cg.id)))
    assert resp.status_code == 200, resp.text
    assert resp.json()["record"]["conformer_group"]["conformer_group_ref"] == cg.public_ref


def test_cg_detail_unknown_handle_returns_404(client, db_session):
    resp = client.get(_cg_url("cg_doesnotexist00000"))
    assert resp.status_code == 404
    assert "conformer_group not found" in resp.text


def test_cg_detail_wrong_prefix_returns_422(client, db_session):
    resp = client.get(_cg_url("co_abcdef0123456789"))
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_cg_detail_malformed_handle_returns_422(client, db_session):
    resp = client.get(_cg_url("not-a-handle"))
    assert resp.status_code == 422
    assert "invalid_handle" in resp.text


def test_cg_detail_default_response_shape(client, db_session):
    _, cg, _ = _make_group_with_obs(db_session)
    body = client.get(_cg_url(cg.public_ref)).json()
    record = body["record"]
    assert "conformer_group" in record
    assert "species" in record
    assert "observations_summary" in record
    assert "selection_summary" in record
    assert "evidence_summary" in record
    assert "available_sections" in record
    # Heavy include blocks omitted by default — Pydantic serializes as null
    # (the schema fields are ``... | None = None``).
    assert record["observations"] is None
    assert record["selections"] is None
    assert record["calculations"] is None
    assert record["geometries"] is None
    assert record["review_history"] is None


def test_cg_detail_review_badge_present(client, db_session):
    _, cg, _ = _make_group_with_obs(db_session)
    body = client.get(_cg_url(cg.public_ref)).json()
    assert body["record"]["conformer_group"]["review"]["status"] == "not_reviewed"
    assert body["review_summary"]["not_reviewed"] == 1
    assert body["review_summary"]["total"] == 1


def test_cg_detail_species_context_present(client, db_session):
    species, entry = _make_species_entry(db_session)
    cg = make_conformer_group(db_session, entry, label="basin_a")
    body = client.get(_cg_url(cg.public_ref)).json()
    sp = body["record"]["species"]
    assert sp["species_ref"] == species.public_ref
    assert sp["species_entry_ref"] == entry.public_ref
    assert sp["canonical_smiles"] == "CC"
    # CHAR(27) column right-pads with spaces in the DB; compare trimmed.
    assert sp["inchi_key"].rstrip() == species.inchi_key.rstrip()


def test_cg_detail_observations_summary_counts(client, db_session):
    _, cg, obs = _make_group_with_obs(db_session, n_observations=3)
    body = client.get(_cg_url(cg.public_ref)).json()
    summary = body["record"]["observations_summary"]
    assert summary["total"] == 3
    assert summary["by_scientific_origin"]["computed"] == 3


def test_cg_detail_evidence_summary_with_calcs(client, db_session):
    entry, cg, obs = _make_group_with_obs(db_session)
    _attach_calc(
        db_session,
        species_entry=entry,
        conformer_observation=obs[0],
        calc_type=CalculationType.opt,
    )
    _attach_calc(
        db_session,
        species_entry=entry,
        conformer_observation=obs[0],
        calc_type=CalculationType.freq,
    )
    body = client.get(_cg_url(cg.public_ref)).json()
    ev = body["record"]["evidence_summary"]
    assert ev["observation_count"] == 1
    assert ev["calculation_count"] == 2
    assert ev["has_opt"] is True
    assert ev["has_freq"] is True
    assert ev["has_sp"] is False
    assert ev["geometry_count"] == 0


def test_cg_detail_available_sections_present(client, db_session):
    _, cg, _ = _make_group_with_obs(db_session)
    body = client.get(_cg_url(cg.public_ref)).json()
    sections = body["record"]["available_sections"]
    assert sections["has_observations"] is True
    assert sections["has_selections"] is False
    assert sections["has_calculations"] is False


def test_cg_detail_include_observations(client, db_session):
    _, cg, obs = _make_group_with_obs(db_session, n_observations=2)
    body = client.get(_cg_url(cg.public_ref, include="observations")).json()
    assert body["record"]["observations"] is not None
    assert len(body["record"]["observations"]) == 2
    refs = {
        o["conformer_observation"]["conformer_observation_ref"]
        for o in body["record"]["observations"]
    }
    assert refs == {obs[0].public_ref, obs[1].public_ref}


def test_cg_detail_include_selections(client, db_session):
    _, cg, _ = _make_group_with_obs(db_session)
    attach_conformer_selection(
        db_session,
        conformer_group=cg,
        selection_kind=ConformerSelectionKind.lowest_energy,
    )
    body = client.get(_cg_url(cg.public_ref, include="selections")).json()
    sel = body["record"]["selections"]
    assert sel is not None
    assert len(sel) == 1
    assert sel[0]["selection_kind"] == "lowest_energy"
    # selection_summary is also in the default block — same content.
    assert body["record"]["selection_summary"][0]["selection_kind"] == "lowest_energy"


def test_cg_detail_include_calculations(client, db_session):
    entry, cg, obs = _make_group_with_obs(db_session)
    lot = make_lot(db_session)
    calc = make_calculation_with_conformer(
        db_session,
        species_entry=entry,
        conformer_observation=obs[0],
        type=CalculationType.opt,
        lot_id=lot.id,
    )
    body = client.get(_cg_url(cg.public_ref, include="calculations")).json()
    calcs = body["record"]["calculations"]
    assert calcs is not None
    assert len(calcs) == 1
    assert calcs[0]["calculation_ref"] == calc.public_ref
    assert calcs[0]["type"] == "opt"
    assert calcs[0]["level_of_theory"]["method"] == "wb97xd"


def test_cg_detail_include_geometries(client, db_session):
    entry, cg, obs = _make_group_with_obs(db_session)
    calc, geom = _attach_calc(
        db_session,
        species_entry=entry,
        conformer_observation=obs[0],
        calc_type=CalculationType.opt,
        with_geom=True,
    )
    body = client.get(_cg_url(cg.public_ref, include="geometries")).json()
    geoms = body["record"]["geometries"]
    assert geoms is not None
    assert len(geoms) == 1
    assert geoms[0]["geometry"]["geometry_ref"] == geom.public_ref
    assert geoms[0]["geometry"]["natoms"] == 4
    assert geoms[0]["calculation_ref"] == calc.public_ref
    # Forbidden inlining.
    assert "xyz_text" not in geoms[0]["geometry"]


def test_cg_detail_include_review(client, db_session):
    _, cg, _ = _make_group_with_obs(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.conformer_group,
        record_id=cg.id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(_cg_url(cg.public_ref, include="review")).json()
    rh = body["record"]["review_history"]
    assert rh is not None
    assert len(rh) == 1
    assert rh[0]["status"] == "approved"


def test_cg_detail_include_all_expands_all_public_tokens(client, db_session):
    _, cg, _ = _make_group_with_obs(db_session)
    body = client.get(_cg_url(cg.public_ref, include="all")).json()
    inc = body["request"]["include"]
    assert "observations" in inc
    assert "selections" in inc
    assert "calculations" in inc
    assert "geometries" in inc
    assert "review" in inc
    assert "internal_ids" not in inc


def test_cg_detail_include_all_does_not_restore_internal_ids(
    client, db_session
):
    _, cg, _ = _make_group_with_obs(db_session)
    body = client.get(_cg_url(cg.public_ref, include="all")).json()
    assert "conformer_group_id" not in body["record"]["conformer_group"]


def test_cg_detail_internal_ids_restored_when_policy_allows(
    client, db_session, allow_internal_ids
):
    _, cg, _ = _make_group_with_obs(db_session)
    body = client.get(
        _cg_url(cg.public_ref, include="internal_ids")
    ).json()
    assert body["record"]["conformer_group"]["conformer_group_id"] == cg.id


def test_cg_detail_internal_ids_silently_dropped_when_disallowed(
    client, db_session
):
    _, cg, _ = _make_group_with_obs(db_session)
    body = client.get(
        _cg_url(cg.public_ref, include="internal_ids")
    ).json()
    assert body["request"]["include"] == []
    assert "conformer_group_id" not in body["record"]["conformer_group"]


def test_cg_detail_unknown_include_token_returns_422(client, db_session):
    _, cg, _ = _make_group_with_obs(db_session)
    resp = client.get(_cg_url(cg.public_ref, include="banana"))
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


def test_cg_detail_rejected_record_still_returned_with_badge(
    client, db_session
):
    _, cg, _ = _make_group_with_obs(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.conformer_group,
        record_id=cg.id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(_cg_url(cg.public_ref)).json()
    assert body["record"]["conformer_group"]["review"]["status"] == "rejected"


def test_cg_detail_no_large_json_payload_leak(client, db_session):
    """Recursive walk: never inline fingerprint / coords JSON or
    geometry coordinate payloads under the conformer-group surface."""
    entry, cg, obs = _make_group_with_obs(db_session)
    _attach_calc(
        db_session,
        species_entry=entry,
        conformer_observation=obs[0],
        with_geom=True,
    )
    body = client.get(_cg_url(cg.public_ref, include="all")).json()
    forbidden = {
        "representative_fingerprint_json",
        "representative_coords_json",
        "torsion_fingerprint_json",
        "mol",
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
                    f"conformer-group detail leaked forbidden key {k!r}"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)


# ===========================================================================
# Conformer-observation detail
# ===========================================================================


def test_co_detail_by_ref_returns_record(client, db_session):
    _, cg, obs = _make_group_with_obs(db_session)
    resp = client.get(_co_url(obs[0].public_ref))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["record"]["conformer_observation"]["conformer_observation_ref"] == obs[0].public_ref


def test_co_detail_by_integer_id_works(client, db_session):
    _, _, obs = _make_group_with_obs(db_session)
    resp = client.get(_co_url(str(obs[0].id)))
    assert resp.status_code == 200, resp.text


def test_co_detail_unknown_handle_returns_404(client, db_session):
    resp = client.get(_co_url("co_doesnotexist00000"))
    assert resp.status_code == 404
    assert "conformer_observation not found" in resp.text


def test_co_detail_wrong_prefix_returns_422(client, db_session):
    resp = client.get(_co_url("cg_abcdef0123456789"))
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_co_detail_malformed_handle_returns_422(client, db_session):
    resp = client.get(_co_url("not-a-handle"))
    assert resp.status_code == 422


def test_co_detail_default_response_shape(client, db_session):
    _, cg, obs = _make_group_with_obs(db_session)
    body = client.get(_co_url(obs[0].public_ref)).json()
    record = body["record"]
    assert "conformer_observation" in record
    assert "conformer_group" in record
    assert "species" in record
    assert "evidence_summary" in record
    assert "available_sections" in record
    # Parent group ref reachable from the observation record.
    assert record["conformer_group"]["conformer_group_ref"] == cg.public_ref


def test_co_detail_review_badge_present(client, db_session):
    _, _, obs = _make_group_with_obs(db_session)
    body = client.get(_co_url(obs[0].public_ref)).json()
    assert body["record"]["conformer_observation"]["review"]["status"] == "not_reviewed"


def test_co_detail_species_context_present(client, db_session):
    species, entry = _make_species_entry(db_session)
    cg = make_conformer_group(db_session, entry)
    obs = make_conformer_observation(db_session, conformer_group=cg)
    body = client.get(_co_url(obs.public_ref)).json()
    sp = body["record"]["species"]
    assert sp["species_ref"] == species.public_ref
    assert sp["species_entry_ref"] == entry.public_ref


def test_co_detail_evidence_summary_scoped_to_observation(client, db_session):
    """Evidence on the observation surface counts only the observation's
    own calcs — not its siblings under the parent group."""
    entry, cg, obs = _make_group_with_obs(db_session, n_observations=2)
    _attach_calc(
        db_session,
        species_entry=entry,
        conformer_observation=obs[0],
        calc_type=CalculationType.opt,
    )
    _attach_calc(
        db_session,
        species_entry=entry,
        conformer_observation=obs[1],
        calc_type=CalculationType.freq,
    )
    body = client.get(_co_url(obs[0].public_ref)).json()
    ev = body["record"]["evidence_summary"]
    assert ev["observation_count"] == 1
    assert ev["calculation_count"] == 1
    assert ev["has_opt"] is True
    assert ev["has_freq"] is False


def test_co_detail_include_calculations(client, db_session):
    entry, _, obs = _make_group_with_obs(db_session)
    calc = make_calculation_with_conformer(
        db_session,
        species_entry=entry,
        conformer_observation=obs[0],
        type=CalculationType.sp,
    )
    body = client.get(
        _co_url(obs[0].public_ref, include="calculations")
    ).json()
    calcs = body["record"]["calculations"]
    assert calcs is not None
    assert len(calcs) == 1
    assert calcs[0]["calculation_ref"] == calc.public_ref


def test_co_detail_include_geometries(client, db_session):
    entry, _, obs = _make_group_with_obs(db_session)
    calc, geom = _attach_calc(
        db_session,
        species_entry=entry,
        conformer_observation=obs[0],
        with_geom=True,
    )
    body = client.get(_co_url(obs[0].public_ref, include="geometries")).json()
    geoms = body["record"]["geometries"]
    assert geoms is not None
    assert len(geoms) == 1
    assert geoms[0]["geometry"]["geometry_ref"] == geom.public_ref


def test_co_detail_include_review(client, db_session):
    _, _, obs = _make_group_with_obs(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.conformer_observation,
        record_id=obs[0].id,
        status=RecordReviewStatus.under_review,
    )
    body = client.get(_co_url(obs[0].public_ref, include="review")).json()
    rh = body["record"]["review_history"]
    assert rh is not None
    assert rh[0]["status"] == "under_review"


def test_co_detail_include_observations_is_no_op_legal(client, db_session):
    """``include=observations`` on the observation surface is silently
    dropped from the response data (the record IS an observation); the
    token still flows through validation as legal and appears in
    request.include if the resolver kept it."""
    _, _, obs = _make_group_with_obs(db_session)
    body = client.get(_co_url(obs[0].public_ref, include="observations")).json()
    # Heavy ``observations`` block is not on the observation record schema.
    assert "observations" not in body["record"]


def test_co_detail_include_selections_surfaces_parent_group_selections(
    client, db_session
):
    """The observation surface surfaces selections via the parent
    group — convenient for clients that landed on an observation
    detail page and want to know how the basin is curated."""
    _, cg, obs = _make_group_with_obs(db_session)
    attach_conformer_selection(
        db_session,
        conformer_group=cg,
        selection_kind=ConformerSelectionKind.curator_pick,
    )
    body = client.get(_co_url(obs[0].public_ref, include="selections")).json()
    sel = body["record"]["selections"]
    assert sel is not None
    assert len(sel) == 1
    assert sel[0]["selection_kind"] == "curator_pick"


def test_co_detail_include_all_does_not_restore_internal_ids(
    client, db_session
):
    _, _, obs = _make_group_with_obs(db_session)
    body = client.get(_co_url(obs[0].public_ref, include="all")).json()
    inc = body["request"]["include"]
    assert "calculations" in inc
    assert "geometries" in inc
    assert "review" in inc
    assert "internal_ids" not in inc
    assert "conformer_observation_id" not in body["record"]["conformer_observation"]


def test_co_detail_internal_ids_restored_when_policy_allows(
    client, db_session, allow_internal_ids
):
    _, cg, obs = _make_group_with_obs(db_session)
    body = client.get(
        _co_url(obs[0].public_ref, include="internal_ids")
    ).json()
    obs_block = body["record"]["conformer_observation"]
    assert obs_block["conformer_observation_id"] == obs[0].id
    assert body["record"]["conformer_group"]["conformer_group_id"] == cg.id


def test_co_detail_no_torsion_fingerprint_leak(client, db_session):
    entry, _, obs = _make_group_with_obs(db_session)
    _attach_calc(
        db_session,
        species_entry=entry,
        conformer_observation=obs[0],
        with_geom=True,
    )
    body = client.get(_co_url(obs[0].public_ref, include="all")).json()
    forbidden = {
        "representative_fingerprint_json",
        "representative_coords_json",
        "torsion_fingerprint_json",
        "mol",
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
                    f"conformer-observation detail leaked forbidden key {k!r}"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)


def test_co_detail_evidence_summary_with_validation_evidence(
    client, db_session
):
    entry, _, obs = _make_group_with_obs(db_session)
    calc = make_calculation_with_conformer(
        db_session,
        species_entry=entry,
        conformer_observation=obs[0],
        type=CalculationType.opt,
    )
    attach_geometry_validation(db_session, calculation=calc)
    attach_scf_stability(db_session, calculation=calc)
    body = client.get(_co_url(obs[0].public_ref)).json()
    ev = body["record"]["evidence_summary"]
    assert ev["has_geometry_validation"] is True
    assert ev["has_scf_stability"] is True
