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
    # No fixed smiles: species identity is (smiles, charge, multiplicity)
    # (DR-0031), and these helpers are called repeatedly to build distinct
    # species. make_species defaults to a unique smiles per call.
    species = make_species(db_session, inchi_key=next_inchi_key("CONF"))
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
    assert sp["canonical_smiles"] == species.smiles
    # CHAR(27) column right-pads with spaces in the DB; compare trimmed.
    assert sp["inchi_key"].rstrip() == species.inchi_key.rstrip()


def test_cg_detail_observations_summary_counts(client, db_session):
    _, cg, obs = _make_group_with_obs(db_session, n_observations=3)
    body = client.get(_cg_url(cg.public_ref)).json()
    summary = body["record"]["observations_summary"]
    assert summary["total"] == 3
    assert summary["by_scientific_origin"]["computed"] == 3


def test_cg_detail_evidence_summary_with_calcs(client, db_session):
    """Rewritten from ``has_opt``/``has_freq``/``has_sp`` booleans.

    Same scenario as before; the group surface now reports observation
    coverage instead. ``coverage > 0`` is the exact replacement for the
    retired ``has_x is True``.
    """
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
    assert ev["evidence_coverage"] == {
        "opt": 1,
        "freq": 1,
        "sp": 0,
        "geometry_validation": 0,
        "scf_stability": 0,
    }
    assert ev["geometry_count"] == 0


def test_cg_detail_evidence_coverage_reports_partial_observation_cover(
    client, db_session
):
    """The case the boolean could not express.

    Two observations under one group; only one has a ``freq``
    calculation. The retired ``has_freq`` reported ``true`` here, which
    a reader takes as "this basin has frequency evidence" when half of
    it does not. Coverage says ``1`` of ``2``.
    """
    entry, cg, obs = _make_group_with_obs(db_session, n_observations=2)
    _attach_calc(
        db_session,
        species_entry=entry,
        conformer_observation=obs[0],
        calc_type=CalculationType.freq,
    )
    _attach_calc(
        db_session,
        species_entry=entry,
        conformer_observation=obs[1],
        calc_type=CalculationType.opt,
    )
    body = client.get(_cg_url(cg.public_ref)).json()
    ev = body["record"]["evidence_summary"]
    assert ev["observation_count"] == 2
    assert ev["calculation_count"] == 2
    assert ev["evidence_coverage"]["freq"] == 1
    assert ev["evidence_coverage"]["opt"] == 1
    # Both kinds would have read ``true`` under the old shape; only the
    # denominator distinguishes "half covered" from "fully covered".
    assert ev["evidence_coverage"]["freq"] < ev["observation_count"]


def test_cg_detail_evidence_coverage_complete_across_observations(
    client, db_session
):
    """Five of five — the coverage-is-complete reading."""
    entry, cg, obs = _make_group_with_obs(db_session, n_observations=5)
    for o in obs:
        _attach_calc(
            db_session,
            species_entry=entry,
            conformer_observation=o,
            calc_type=CalculationType.freq,
        )
    body = client.get(_cg_url(cg.public_ref)).json()
    ev = body["record"]["evidence_summary"]
    assert ev["observation_count"] == 5
    assert ev["evidence_coverage"]["freq"] == 5
    assert ev["calculation_count"] == 5


def test_cg_detail_evidence_coverage_zero_when_no_observation_has_it(
    client, db_session
):
    """Zero of five — as strong as the old ``has_x is False``."""
    entry, cg, obs = _make_group_with_obs(db_session, n_observations=5)
    for o in obs:
        _attach_calc(
            db_session,
            species_entry=entry,
            conformer_observation=o,
            calc_type=CalculationType.opt,
        )
    body = client.get(_cg_url(cg.public_ref)).json()
    ev = body["record"]["evidence_summary"]
    assert ev["observation_count"] == 5
    assert ev["evidence_coverage"]["opt"] == 5
    assert ev["evidence_coverage"]["freq"] == 0
    assert ev["evidence_coverage"]["sp"] == 0


def test_cg_detail_evidence_coverage_counts_observations_not_calculations(
    client, db_session
):
    """Three ``freq`` calculations on one observation cover **one**.

    This is the property the whole field rests on: the denominator is
    observations, so the numerator must be too. A coverage value that
    counted calculations could exceed ``observation_count`` and would be
    unreadable against it.
    """
    entry, cg, obs = _make_group_with_obs(db_session, n_observations=2)
    for _ in range(3):
        _attach_calc(
            db_session,
            species_entry=entry,
            conformer_observation=obs[0],
            calc_type=CalculationType.freq,
        )
    body = client.get(_cg_url(cg.public_ref)).json()
    ev = body["record"]["evidence_summary"]
    assert ev["observation_count"] == 2
    assert ev["calculation_count"] == 3
    assert ev["evidence_coverage"]["freq"] == 1
    assert ev["evidence_coverage"]["freq"] <= ev["observation_count"]


def test_cg_detail_evidence_coverage_for_validation_and_stability(
    client, db_session
):
    """Coverage for the two joined-evidence kinds, partially covered.

    The covered observation carries **two** validated calculations, so
    this also pins the counts-observations-not-calculations property on
    the joined-evidence path — a coverage of ``2`` here would exceed the
    number of observations that actually have the evidence.
    """
    entry, cg, obs = _make_group_with_obs(db_session, n_observations=2)
    calc_a, _ = _attach_calc(
        db_session, species_entry=entry, conformer_observation=obs[0]
    )
    calc_b, _ = _attach_calc(
        db_session, species_entry=entry, conformer_observation=obs[0]
    )
    _attach_calc(db_session, species_entry=entry, conformer_observation=obs[1])
    for calc in (calc_a, calc_b):
        attach_geometry_validation(db_session, calculation=calc)
        attach_scf_stability(db_session, calculation=calc)
    body = client.get(_cg_url(cg.public_ref)).json()
    ev = body["record"]["evidence_summary"]
    coverage = ev["evidence_coverage"]
    assert ev["observation_count"] == 2
    assert ev["calculation_count"] == 3
    assert coverage["geometry_validation"] == 1
    assert coverage["scf_stability"] == 1


def test_cg_detail_evidence_coverage_empty_group(client, db_session):
    """A basin with no observations is 0 of 0 — never vacuously covered."""
    _, cg = _make_group(db_session)
    body = client.get(_cg_url(cg.public_ref)).json()
    ev = body["record"]["evidence_summary"]
    assert ev["observation_count"] == 0
    assert ev["calculation_count"] == 0
    assert ev["geometry_count"] == 0
    assert ev["evidence_coverage"] == {
        "opt": 0,
        "freq": 0,
        "sp": 0,
        "geometry_validation": 0,
        "scf_stability": 0,
    }


def test_cg_detail_evidence_summary_carries_no_boolean_flags(
    client, db_session
):
    """The misleading group-scope booleans are gone, not merely joined."""
    entry, cg, obs = _make_group_with_obs(db_session, n_observations=2)
    _attach_calc(
        db_session,
        species_entry=entry,
        conformer_observation=obs[0],
        calc_type=CalculationType.freq,
    )
    body = client.get(_cg_url(cg.public_ref)).json()
    ev = body["record"]["evidence_summary"]
    for retired in (
        "has_opt",
        "has_freq",
        "has_sp",
        "has_geometry_validation",
        "has_scf_stability",
    ):
        assert retired not in ev


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


def test_co_detail_include_observations_returns_the_basin(client, db_session):
    """``include=observations`` returns the sibling observations, this one included.

    This test asserted the opposite — that the token was legal and produced
    no field — on the reading that the record already *is* an observation
    so there was nothing for the token to say. There was: which other
    observations share the basin, which is the one question an
    observation-grained record cannot answer from itself. A token that is
    accepted, echoed, and produces nothing is indistinguishable to a client
    from one that failed silently.

    The nested records carry no ``observations`` of their own. That is not
    a size optimisation but the thing that terminates the recursion.
    """
    _, _, obs = _make_group_with_obs(db_session)
    expected = sorted(o.public_ref for o in obs)

    body = client.get(_co_url(obs[0].public_ref, include="observations")).json()

    assert body["request"]["include"] == ["observations"]
    block = body["record"]["observations"]
    assert block is not None
    assert sorted(
        o["conformer_observation"]["conformer_observation_ref"] for o in block
    ) == expected
    assert all(o["observations"] is None for o in block)

    default = client.get(_co_url(obs[0].public_ref)).json()
    assert default["record"]["observations"] is None


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


def test_co_detail_evidence_summary_keeps_booleans(client, db_session):
    """The observation surface is deliberately shaped differently.

    One observation is one provenance row, so a boolean there cannot
    pool a covered observation with an uncovered one — the failure that
    forced the group surface onto counts. The booleans stay, and the
    group's ``evidence_coverage`` block must not appear here.
    """
    entry, _, obs = _make_group_with_obs(db_session, n_observations=2)
    _attach_calc(
        db_session,
        species_entry=entry,
        conformer_observation=obs[0],
        calc_type=CalculationType.freq,
    )
    body = client.get(_co_url(obs[0].public_ref)).json()
    ev = body["record"]["evidence_summary"]
    assert ev["observation_count"] == 1
    assert ev["has_freq"] is True
    assert ev["has_opt"] is False
    assert "evidence_coverage" not in ev

    sibling = client.get(_co_url(obs[1].public_ref)).json()
    assert sibling["record"]["evidence_summary"]["has_freq"] is False


# ===========================================================================
# Conformer search (group grain)
# ===========================================================================


def _search_url(**params) -> str:
    base = "/api/v1/scientific/conformers/search"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


# --- empty filter -----------------------------------------------------------


def test_search_missing_filter_returns_422_get(client, db_session):
    resp = client.get(_search_url())
    assert resp.status_code == 422
    assert "missing_filter" in resp.text


def test_search_missing_filter_returns_422_post(client, db_session):
    resp = client.post(_search_url(), json={"limit": 50})
    assert resp.status_code == 422
    assert "missing_filter" in resp.text


# --- identity filters -------------------------------------------------------


def test_search_by_species_entry_ref(client, db_session):
    species, entry = _make_species_entry(db_session)
    cg = make_conformer_group(db_session, entry, label="basin_a")
    make_conformer_observation(db_session, conformer_group=cg)
    body = client.get(
        _search_url(species_entry_ref=entry.public_ref)
    ).json()
    assert body["pagination"]["total"] == 1
    assert body["records"][0]["conformer_group"]["conformer_group_ref"] == cg.public_ref


def test_search_by_species_ref(client, db_session):
    species, entry = _make_species_entry(db_session)
    cg = make_conformer_group(db_session, entry)
    body = client.get(_search_url(species_ref=species.public_ref)).json()
    assert body["pagination"]["total"] == 1
    assert body["records"][0]["conformer_group"]["conformer_group_ref"] == cg.public_ref


def test_search_by_conformer_group_ref(client, db_session):
    _, _, _ = _make_group_with_obs(db_session, label="a")
    _, cg_b, _ = _make_group_with_obs(db_session, label="b")
    body = client.get(
        _search_url(conformer_group_ref=cg_b.public_ref)
    ).json()
    assert body["pagination"]["total"] == 1
    assert body["records"][0]["conformer_group"]["conformer_group_ref"] == cg_b.public_ref


def test_search_by_conformer_observation_ref(client, db_session):
    _, cg_a, obs_a = _make_group_with_obs(db_session, label="a")
    _, _, _ = _make_group_with_obs(db_session, label="b")
    body = client.get(
        _search_url(conformer_observation_ref=obs_a[0].public_ref)
    ).json()
    assert body["pagination"]["total"] == 1
    assert body["records"][0]["conformer_group"]["conformer_group_ref"] == cg_a.public_ref


# --- curation filters -------------------------------------------------------


def test_search_by_selection_kind(client, db_session):
    _, cg_a, _ = _make_group_with_obs(db_session, label="a")
    _, _, _ = _make_group_with_obs(db_session, label="b")
    attach_conformer_selection(
        db_session,
        conformer_group=cg_a,
        selection_kind=ConformerSelectionKind.lowest_energy,
    )
    body = client.get(_search_url(selection_kind="lowest_energy")).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert refs == {cg_a.public_ref}


def test_search_by_has_selection_true(client, db_session):
    _, cg_a, _ = _make_group_with_obs(db_session, label="a")
    _, _, _ = _make_group_with_obs(db_session, label="b")
    attach_conformer_selection(
        db_session,
        conformer_group=cg_a,
        selection_kind=ConformerSelectionKind.curator_pick,
    )
    body = client.get(_search_url(has_selection="true")).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert refs == {cg_a.public_ref}


def test_search_by_has_selection_false(client, db_session):
    _, cg_a, _ = _make_group_with_obs(db_session, label="a")
    _, cg_b, _ = _make_group_with_obs(db_session, label="b")
    attach_conformer_selection(
        db_session,
        conformer_group=cg_a,
        selection_kind=ConformerSelectionKind.curator_pick,
    )
    body = client.get(_search_url(has_selection="false")).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert cg_b.public_ref in refs
    assert cg_a.public_ref not in refs


def test_search_by_assignment_scheme_ref(client, db_session):
    from app.db.models.species import ConformerAssignmentScheme

    _, cg_a, _ = _make_group_with_obs(db_session, label="a")
    _, _, _ = _make_group_with_obs(db_session, label="b")
    scheme = ConformerAssignmentScheme(name="canon", version="v1")
    db_session.add(scheme)
    db_session.flush()
    sel = attach_conformer_selection(
        db_session,
        conformer_group=cg_a,
        selection_kind=ConformerSelectionKind.lowest_energy,
    )
    sel.assignment_scheme_id = scheme.id
    db_session.flush()
    body = client.get(
        _search_url(assignment_scheme_ref=scheme.public_ref)
    ).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert refs == {cg_a.public_ref}


# --- evidence filters -------------------------------------------------------


def test_search_by_has_observations(client, db_session):
    _, cg_a, _ = _make_group_with_obs(db_session, label="a")
    _, entry_b = _make_species_entry(db_session)
    cg_b = make_conformer_group(db_session, entry_b, label="b")  # no obs
    body = client.get(_search_url(has_observations="true")).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert cg_a.public_ref in refs
    assert cg_b.public_ref not in refs


def test_search_by_has_calculations(client, db_session):
    entry_a, cg_a, obs_a = _make_group_with_obs(db_session, label="a")
    _, _, _ = _make_group_with_obs(db_session, label="b")
    _attach_calc(
        db_session,
        species_entry=entry_a,
        conformer_observation=obs_a[0],
        calc_type=CalculationType.opt,
    )
    body = client.get(_search_url(has_calculations="true")).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert refs == {cg_a.public_ref}


def test_search_by_has_geometries(client, db_session):
    entry_a, cg_a, obs_a = _make_group_with_obs(db_session, label="a")
    _, _, _ = _make_group_with_obs(db_session, label="b")
    _attach_calc(
        db_session,
        species_entry=entry_a,
        conformer_observation=obs_a[0],
        with_geom=True,
    )
    body = client.get(_search_url(has_geometries="true")).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert refs == {cg_a.public_ref}


def test_search_by_has_opt(client, db_session):
    entry_a, cg_a, obs_a = _make_group_with_obs(db_session, label="a")
    entry_b, cg_b, obs_b = _make_group_with_obs(db_session, label="b")
    _attach_calc(
        db_session,
        species_entry=entry_a,
        conformer_observation=obs_a[0],
        calc_type=CalculationType.opt,
    )
    _attach_calc(
        db_session,
        species_entry=entry_b,
        conformer_observation=obs_b[0],
        calc_type=CalculationType.sp,
    )
    body = client.get(_search_url(has_opt="true")).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert refs == {cg_a.public_ref}


def test_search_by_has_freq(client, db_session):
    entry_a, cg_a, obs_a = _make_group_with_obs(db_session, label="a")
    _, _, _ = _make_group_with_obs(db_session, label="b")
    _attach_calc(
        db_session,
        species_entry=entry_a,
        conformer_observation=obs_a[0],
        calc_type=CalculationType.freq,
    )
    body = client.get(_search_url(has_freq="true")).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert refs == {cg_a.public_ref}


def test_search_by_has_sp(client, db_session):
    entry_a, cg_a, obs_a = _make_group_with_obs(db_session, label="a")
    _, _, _ = _make_group_with_obs(db_session, label="b")
    _attach_calc(
        db_session,
        species_entry=entry_a,
        conformer_observation=obs_a[0],
        calc_type=CalculationType.sp,
    )
    body = client.get(_search_url(has_sp="true")).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert refs == {cg_a.public_ref}


def test_search_by_has_geometry_validation(client, db_session):
    entry_a, cg_a, obs_a = _make_group_with_obs(db_session, label="a")
    _, _, _ = _make_group_with_obs(db_session, label="b")
    calc, _ = _attach_calc(
        db_session,
        species_entry=entry_a,
        conformer_observation=obs_a[0],
    )
    attach_geometry_validation(db_session, calculation=calc)
    body = client.get(_search_url(has_geometry_validation="true")).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert refs == {cg_a.public_ref}


def test_search_by_has_scf_stability(client, db_session):
    entry_a, cg_a, obs_a = _make_group_with_obs(db_session, label="a")
    _, _, _ = _make_group_with_obs(db_session, label="b")
    calc, _ = _attach_calc(
        db_session,
        species_entry=entry_a,
        conformer_observation=obs_a[0],
    )
    attach_scf_stability(db_session, calculation=calc)
    body = client.get(_search_url(has_scf_stability="true")).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert refs == {cg_a.public_ref}


# --- evidence_match (any vs all observations) -------------------------------


def _group_with_freq_coverage(db_session, *, label, covered, total):
    """Build a group of *total* observations where *covered* have freq."""
    entry, cg, obs = _make_group_with_obs(
        db_session, label=label, n_observations=total
    )
    for index, o in enumerate(obs):
        _attach_calc(
            db_session,
            species_entry=entry,
            conformer_observation=o,
            calc_type=(
                CalculationType.freq if index < covered else CalculationType.opt
            ),
        )
    return entry, cg, obs


def test_search_has_freq_defaults_to_any_observation(client, db_session):
    """Unchanged default: one covered observation is enough to match.

    Existing clients pass no ``evidence_match``; they must keep seeing
    the partially covered group.
    """
    _, cg_partial, _ = _group_with_freq_coverage(
        db_session, label="partial", covered=1, total=2
    )
    _, cg_none, _ = _group_with_freq_coverage(
        db_session, label="none", covered=0, total=2
    )
    body = client.get(_search_url(has_freq="true")).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert refs == {cg_partial.public_ref}
    assert cg_none.public_ref not in refs


def test_search_has_freq_all_observations_requires_every_observation(
    client, db_session
):
    """``all_observations`` is the question the boolean could not ask."""
    _, cg_partial, _ = _group_with_freq_coverage(
        db_session, label="partial", covered=1, total=2
    )
    _, cg_full, _ = _group_with_freq_coverage(
        db_session, label="full", covered=2, total=2
    )
    body = client.get(
        _search_url(has_freq="true", evidence_match="all_observations")
    ).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert refs == {cg_full.public_ref}
    assert cg_partial.public_ref not in refs

    # ...and the same two groups under the default quantifier: both match.
    any_body = client.get(_search_url(has_freq="true")).json()
    any_refs = {
        r["conformer_group"]["conformer_group_ref"] for r in any_body["records"]
    }
    assert any_refs == {cg_partial.public_ref, cg_full.public_ref}


def test_search_has_freq_all_observations_false_finds_incomplete_cover(
    client, db_session
):
    """Under ``all_observations``, ``false`` means coverage is incomplete."""
    _, cg_partial, _ = _group_with_freq_coverage(
        db_session, label="partial", covered=1, total=2
    )
    _, cg_full, _ = _group_with_freq_coverage(
        db_session, label="full", covered=2, total=2
    )
    _, cg_none, _ = _group_with_freq_coverage(
        db_session, label="none", covered=0, total=2
    )
    body = client.get(
        _search_url(has_freq="false", evidence_match="all_observations")
    ).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert cg_partial.public_ref in refs
    assert cg_none.public_ref in refs
    assert cg_full.public_ref not in refs


def test_search_has_freq_any_observations_false_needs_zero_coverage(
    client, db_session
):
    """Default ``false`` stays strict: no observation may have freq."""
    _, cg_partial, _ = _group_with_freq_coverage(
        db_session, label="partial", covered=1, total=2
    )
    _, cg_none, _ = _group_with_freq_coverage(
        db_session, label="none", covered=0, total=2
    )
    body = client.get(_search_url(has_freq="false")).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert cg_none.public_ref in refs
    assert cg_partial.public_ref not in refs


def test_search_all_observations_never_matches_an_empty_group(
    client, db_session
):
    """"Every observation has freq" must not be vacuously true of nothing."""
    _, cg_empty = _make_group(db_session, label="empty")
    _, cg_full, _ = _group_with_freq_coverage(
        db_session, label="full", covered=1, total=1
    )
    true_body = client.get(
        _search_url(has_freq="true", evidence_match="all_observations")
    ).json()
    true_refs = {
        r["conformer_group"]["conformer_group_ref"] for r in true_body["records"]
    }
    assert cg_empty.public_ref not in true_refs
    assert cg_full.public_ref in true_refs

    false_body = client.get(
        _search_url(has_freq="false", evidence_match="all_observations")
    ).json()
    false_refs = {
        r["conformer_group"]["conformer_group_ref"]
        for r in false_body["records"]
    }
    assert cg_empty.public_ref not in false_refs


def test_search_all_observations_applies_to_geometry_validation(
    client, db_session
):
    """The quantifier covers the whole evidence family, not just types."""
    entry, cg_partial, obs_partial = _make_group_with_obs(
        db_session, label="partial", n_observations=2
    )
    calc_a, _ = _attach_calc(
        db_session, species_entry=entry, conformer_observation=obs_partial[0]
    )
    _attach_calc(
        db_session, species_entry=entry, conformer_observation=obs_partial[1]
    )
    attach_geometry_validation(db_session, calculation=calc_a)

    entry_b, cg_full, obs_full = _make_group_with_obs(
        db_session, label="full", n_observations=2
    )
    for o in obs_full:
        calc, _ = _attach_calc(
            db_session, species_entry=entry_b, conformer_observation=o
        )
        attach_geometry_validation(db_session, calculation=calc)

    body = client.get(
        _search_url(
            has_geometry_validation="true",
            evidence_match="all_observations",
        )
    ).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert refs == {cg_full.public_ref}


def test_search_evidence_match_alone_is_not_a_filter(client, db_session):
    """A modifier must not satisfy the at-least-one-filter rule."""
    resp = client.get(_search_url(evidence_match="all_observations"))
    assert resp.status_code == 422
    assert "missing_filter" in resp.text


def test_search_echoes_evidence_match(client, db_session):
    """The echo must say which quantifier produced the result set."""
    _, cg, _ = _group_with_freq_coverage(
        db_session, label="full", covered=1, total=1
    )
    default_body = client.get(_search_url(has_freq="true")).json()
    assert default_body["request"]["filter"]["evidence_match"] == (
        "any_observation"
    )
    explicit_body = client.get(
        _search_url(has_freq="true", evidence_match="all_observations")
    ).json()
    assert explicit_body["request"]["filter"]["evidence_match"] == (
        "all_observations"
    )
    assert cg.public_ref  # the fixture is real, not a no-op


def test_search_post_accepts_evidence_match(client, db_session):
    """GET/POST parity for the new parameter."""
    _, cg_partial, _ = _group_with_freq_coverage(
        db_session, label="partial", covered=1, total=2
    )
    _, cg_full, _ = _group_with_freq_coverage(
        db_session, label="full", covered=2, total=2
    )
    body = client.post(
        _search_url(),
        json={"has_freq": True, "evidence_match": "all_observations"},
    ).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert refs == {cg_full.public_ref}
    assert cg_partial.public_ref not in refs


def test_search_rejects_unknown_evidence_match_value(client, db_session):
    resp = client.get(
        _search_url(has_freq="true", evidence_match="most_observations")
    )
    assert resp.status_code == 422


# --- provenance filters -----------------------------------------------------


def test_search_by_scientific_origin(client, db_session):
    _, cg_a, _ = _make_group_with_obs(
        db_session, label="a", origin=ScientificOriginKind.experimental
    )
    _, _, _ = _make_group_with_obs(
        db_session, label="b", origin=ScientificOriginKind.computed
    )
    body = client.get(_search_url(scientific_origin="experimental")).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert refs == {cg_a.public_ref}


def test_search_by_method_and_basis(client, db_session):
    entry_a, cg_a, obs_a = _make_group_with_obs(db_session, label="a")
    entry_b, _, obs_b = _make_group_with_obs(db_session, label="b")
    lot_a = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    lot_b = make_lot(db_session, method="b3lyp", basis="6-31g")
    make_calculation_with_conformer(
        db_session,
        species_entry=entry_a,
        conformer_observation=obs_a[0],
        type=CalculationType.opt,
        lot_id=lot_a.id,
    )
    make_calculation_with_conformer(
        db_session,
        species_entry=entry_b,
        conformer_observation=obs_b[0],
        type=CalculationType.opt,
        lot_id=lot_b.id,
    )
    body = client.get(
        _search_url(method="wb97xd", basis="def2tzvp")
    ).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert refs == {cg_a.public_ref}


def test_search_by_software_and_version(client, db_session):
    from app.db.models.software import Software, SoftwareRelease

    entry_a, cg_a, obs_a = _make_group_with_obs(db_session, label="a")
    entry_b, _, obs_b = _make_group_with_obs(db_session, label="b")
    sw_a = Software(name="gaussian")
    sw_b = Software(name="orca")
    db_session.add_all([sw_a, sw_b])
    db_session.flush()
    sr_a = SoftwareRelease(software_id=sw_a.id, version="g16.a03")
    sr_b = SoftwareRelease(software_id=sw_b.id, version="5.0.4")
    db_session.add_all([sr_a, sr_b])
    db_session.flush()
    calc_a = make_calculation_with_conformer(
        db_session,
        species_entry=entry_a,
        conformer_observation=obs_a[0],
        type=CalculationType.opt,
    )
    calc_a.software_release_id = sr_a.id
    calc_b = make_calculation_with_conformer(
        db_session,
        species_entry=entry_b,
        conformer_observation=obs_b[0],
        type=CalculationType.opt,
    )
    calc_b.software_release_id = sr_b.id
    db_session.flush()
    body = client.get(
        _search_url(software="gaussian", software_version="g16.a03")
    ).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert refs == {cg_a.public_ref}


def test_search_by_workflow_tool_and_version(client, db_session):
    from app.db.models.workflow import WorkflowTool, WorkflowToolRelease

    entry_a, cg_a, obs_a = _make_group_with_obs(db_session, label="a")
    entry_b, _, obs_b = _make_group_with_obs(db_session, label="b")
    wt_a = WorkflowTool(name="arc")
    wt_b = WorkflowTool(name="qcelemental")
    db_session.add_all([wt_a, wt_b])
    db_session.flush()
    wtr_a = WorkflowToolRelease(workflow_tool_id=wt_a.id, version="1.2.3")
    wtr_b = WorkflowToolRelease(workflow_tool_id=wt_b.id, version="0.27.0")
    db_session.add_all([wtr_a, wtr_b])
    db_session.flush()
    calc_a = make_calculation_with_conformer(
        db_session,
        species_entry=entry_a,
        conformer_observation=obs_a[0],
        type=CalculationType.opt,
    )
    calc_a.workflow_tool_release_id = wtr_a.id
    calc_b = make_calculation_with_conformer(
        db_session,
        species_entry=entry_b,
        conformer_observation=obs_b[0],
        type=CalculationType.opt,
    )
    calc_b.workflow_tool_release_id = wtr_b.id
    db_session.flush()
    body = client.get(
        _search_url(workflow_tool="arc", workflow_tool_version="1.2.3")
    ).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert refs == {cg_a.public_ref}


# --- review trust posture ---------------------------------------------------


def test_search_default_hides_rejected(client, db_session):
    _, cg_a, _ = _make_group_with_obs(db_session, label="a")
    _, cg_b, _ = _make_group_with_obs(db_session, label="b")
    set_review(
        db_session,
        record_type=SubmissionRecordType.conformer_group,
        record_id=cg_b.id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(_search_url(has_observations="true")).json()
    refs = {r["conformer_group"]["conformer_group_ref"] for r in body["records"]}
    assert cg_a.public_ref in refs
    assert cg_b.public_ref not in refs


def test_search_include_rejected_surfaces_them_last(client, db_session):
    _, cg_a, _ = _make_group_with_obs(db_session, label="a")
    _, cg_b, _ = _make_group_with_obs(db_session, label="b")
    set_review(
        db_session,
        record_type=SubmissionRecordType.conformer_group,
        record_id=cg_b.id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(
        _search_url(has_observations="true", include_rejected="true")
    ).json()
    refs = [
        r["conformer_group"]["conformer_group_ref"] for r in body["records"]
    ]
    assert cg_a.public_ref in refs
    assert cg_b.public_ref in refs
    # Rejected sorts last (review_rank ASC).
    assert refs[-1] == cg_b.public_ref


# --- pagination + ordering --------------------------------------------------


def test_search_pagination_envelope(client, db_session):
    _, entry = _make_species_entry(db_session)
    for i in range(4):
        cg = make_conformer_group(db_session, entry, label=f"b{i}")
        make_conformer_observation(db_session, conformer_group=cg)
    body = client.get(
        _search_url(species_entry_ref=entry.public_ref, limit=2, offset=0)
    ).json()
    p = body["pagination"]
    assert p["limit"] == 2
    assert p["offset"] == 0
    assert p["returned"] == 2
    assert p["total"] == 4


def test_search_deterministic_ordering_review_then_created(client, db_session):
    """Approved record wins over not_reviewed regardless of creation order."""
    _, cg_a, _ = _make_group_with_obs(db_session, label="a")
    _, cg_b, _ = _make_group_with_obs(db_session, label="b")
    set_review(
        db_session,
        record_type=SubmissionRecordType.conformer_group,
        record_id=cg_a.id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(_search_url(has_observations="true")).json()
    refs = [
        r["conformer_group"]["conformer_group_ref"] for r in body["records"]
    ]
    assert refs[0] == cg_a.public_ref


def test_search_client_sort_rejected(client, db_session):
    _, cg, _ = _make_group_with_obs(db_session)
    resp = client.get(
        _search_url(has_observations="true", sort="created_at")
    )
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


# --- GET / POST parity ------------------------------------------------------


def test_search_get_post_parity(client, db_session):
    _, entry = _make_species_entry(db_session)
    cg = make_conformer_group(db_session, entry, label="a")
    make_conformer_observation(db_session, conformer_group=cg)
    get_body = client.get(
        _search_url(species_entry_ref=entry.public_ref)
    ).json()
    post_body = client.post(
        _search_url(), json={"species_entry_ref": entry.public_ref}
    ).json()
    assert get_body["pagination"] == post_body["pagination"]
    assert get_body["records"] == post_body["records"]


def test_search_post_rejects_query_string_search_fields(client, db_session):
    _, _, _ = _make_group_with_obs(db_session)
    resp = client.post(
        "/api/v1/scientific/conformers/search?limit=5",
        json={"has_observations": True},
    )
    assert resp.status_code == 422
    assert "post_search_fields_must_be_in_body" in resp.text


# --- include behavior -------------------------------------------------------


def test_search_include_observations_on_records(client, db_session):
    _, cg, obs = _make_group_with_obs(db_session, n_observations=2)
    body = client.get(
        _search_url(
            conformer_group_ref=cg.public_ref, include="observations"
        )
    ).json()
    rec = body["records"][0]
    assert rec["observations"] is not None
    assert len(rec["observations"]) == 2


def test_search_include_selections_on_records(client, db_session):
    _, cg, _ = _make_group_with_obs(db_session)
    attach_conformer_selection(
        db_session,
        conformer_group=cg,
        selection_kind=ConformerSelectionKind.lowest_energy,
    )
    body = client.get(
        _search_url(
            conformer_group_ref=cg.public_ref, include="selections"
        )
    ).json()
    rec = body["records"][0]
    assert rec["selections"] is not None
    assert rec["selections"][0]["selection_kind"] == "lowest_energy"


def test_search_include_calculations_on_records(client, db_session):
    entry, cg, obs = _make_group_with_obs(db_session)
    lot = make_lot(db_session)
    make_calculation_with_conformer(
        db_session,
        species_entry=entry,
        conformer_observation=obs[0],
        type=CalculationType.opt,
        lot_id=lot.id,
    )
    body = client.get(
        _search_url(
            conformer_group_ref=cg.public_ref, include="calculations"
        )
    ).json()
    rec = body["records"][0]
    assert rec["calculations"] is not None
    assert len(rec["calculations"]) == 1
    assert rec["calculations"][0]["type"] == "opt"


def test_search_include_geometries_on_records(client, db_session):
    entry, cg, obs = _make_group_with_obs(db_session)
    _, geom = _attach_calc(
        db_session,
        species_entry=entry,
        conformer_observation=obs[0],
        with_geom=True,
    )
    body = client.get(
        _search_url(
            conformer_group_ref=cg.public_ref, include="geometries"
        )
    ).json()
    rec = body["records"][0]
    assert rec["geometries"] is not None
    assert rec["geometries"][0]["geometry"]["geometry_ref"] == geom.public_ref


def test_search_include_review_on_records(client, db_session):
    _, cg, _ = _make_group_with_obs(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.conformer_group,
        record_id=cg.id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(
        _search_url(
            conformer_group_ref=cg.public_ref, include="review"
        )
    ).json()
    rec = body["records"][0]
    assert rec["review_history"] is not None
    assert rec["review_history"][0]["status"] == "approved"


def test_search_include_all_on_records(client, db_session):
    _, cg, _ = _make_group_with_obs(db_session)
    body = client.get(
        _search_url(conformer_group_ref=cg.public_ref, include="all")
    ).json()
    inc = body["request"]["include"]
    assert "observations" in inc
    assert "selections" in inc
    assert "calculations" in inc
    assert "geometries" in inc
    assert "review" in inc
    assert "internal_ids" not in inc


def test_search_include_all_does_not_restore_internal_ids(client, db_session):
    _, cg, _ = _make_group_with_obs(db_session)
    body = client.get(
        _search_url(conformer_group_ref=cg.public_ref, include="all")
    ).json()
    assert "conformer_group_id" not in body["records"][0]["conformer_group"]


def test_search_internal_ids_restored_when_policy_allows(
    client, db_session, allow_internal_ids
):
    _, cg, _ = _make_group_with_obs(db_session)
    body = client.get(
        _search_url(
            conformer_group_ref=cg.public_ref, include="internal_ids"
        )
    ).json()
    assert body["records"][0]["conformer_group"]["conformer_group_id"] == cg.id


def test_search_internal_ids_silently_dropped_when_disallowed(
    client, db_session
):
    _, cg, _ = _make_group_with_obs(db_session)
    body = client.get(
        _search_url(
            conformer_group_ref=cg.public_ref, include="internal_ids"
        )
    ).json()
    assert body["request"]["include"] == []
    assert "conformer_group_id" not in body["records"][0]["conformer_group"]


# --- cross-endpoint record-shape parity -------------------------------------


def test_search_record_shape_matches_group_detail(client, db_session):
    """Per-record shape from /conformers/search must match
    record-shape from /conformer-groups/{ref} for the same group and
    include set."""
    entry, cg, obs = _make_group_with_obs(db_session)
    lot = make_lot(db_session)
    make_calculation_with_conformer(
        db_session,
        species_entry=entry,
        conformer_observation=obs[0],
        type=CalculationType.opt,
        lot_id=lot.id,
    )
    search_body = client.get(
        _search_url(
            conformer_group_ref=cg.public_ref,
            include="calculations",
        )
    ).json()
    detail_body = client.get(
        _cg_url(cg.public_ref, include="calculations")
    ).json()
    assert search_body["records"][0] == detail_body["record"]


# --- ref resolution edge cases ---------------------------------------------


def test_search_unknown_ref_short_circuits_empty(client, db_session):
    body = client.get(
        _search_url(species_entry_ref="spe_doesnotexist00")
    ).json()
    assert body["pagination"]["total"] == 0
    assert body["records"] == []


def test_search_wrong_prefix_ref_returns_422(client, db_session):
    resp = client.get(_search_url(species_entry_ref="cg_abcdef0123456789"))
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


# --- forbidden payload walk -------------------------------------------------


def test_search_no_forbidden_payload_keys(client, db_session):
    entry, cg, obs = _make_group_with_obs(db_session)
    _attach_calc(
        db_session,
        species_entry=entry,
        conformer_observation=obs[0],
        with_geom=True,
    )
    body = client.get(
        _search_url(
            conformer_group_ref=cg.public_ref, include="all"
        )
    ).json()
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
                    f"conformer search leaked forbidden key {k!r}"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)
