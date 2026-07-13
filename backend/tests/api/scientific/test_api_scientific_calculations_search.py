"""API tests for ``GET|POST /api/v1/scientific/calculations/search`` (MVP)."""

from __future__ import annotations

from app.db.models.calculation import (
    CalculationConstraint,
    CalculationInputGeometry,
    CalculationIRCPoint,
    CalculationIRCResult,
    CalculationParameter,
    CalculationParameterVocab,
    CalculationPathSearchPoint,
    CalculationPathSearchResult,
    CalculationScanCoordinate,
    CalculationScanPoint,
    CalculationScanResult,
)
from app.db.models.common import (
    ArtifactKind,
    CalculationDependencyRole,
    CalculationGeometryRole,
    CalculationQuality,
    CalculationType,
    ConstraintKind,
    IRCDirection,
    ParameterSource,
    PathSearchMethod,
    RecordReviewStatus,
    ScanCoordinateKind,
    SCFStabilityStatus,
    SubmissionRecordType,
    TransitionStateEntryStatus,
    ValidationStatus,
)
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from tests.services.scientific_read._factories import (
    attach_artifact,
    attach_dependency,
    attach_geometry_validation,
    attach_opt_result,
    attach_output_geometry,
    attach_scf_stability,
    attach_sp_result,
    make_calculation,
    make_geometry,
    make_lot,
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)

SEARCH_URL = "/api/v1/scientific/calculations/search"

# All heavy include tokens have shipped a summary loader. The only
# include token still rejected is ``all``, gated by the
# policy-deferred guard in the calculations service.


# ---------------------------------------------------------------------------
# Local fixture helpers
# ---------------------------------------------------------------------------


def _attach_input_geometry(db_session, *, calculation, geometry, input_order):
    row = CalculationInputGeometry(
        calculation_id=calculation.id,
        geometry_id=geometry.id,
        input_order=input_order,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _make_species_owned_calc(db_session, **kw):
    species = make_species(
        db_session,
        smiles=kw.pop("smiles", None),
        inchi_key=next_inchi_key("SRCH"),
    )
    entry = make_species_entry(db_session, species)
    calc = make_calculation(
        db_session,
        type=kw.pop("calc_type", CalculationType.opt),
        species_entry_id=entry.id,
        lot_id=kw.pop("lot_id", None),
    )
    return species, entry, calc


def _make_ts_owned_calc(db_session, **kw):
    rxn = ChemReaction(reversible=True)
    db_session.add(rxn)
    db_session.flush()
    rxe = ReactionEntry(reaction_id=rxn.id)
    db_session.add(rxe)
    db_session.flush()
    ts = TransitionState(reaction_entry_id=rxe.id, label=kw.pop("label", "ts1"))
    db_session.add(ts)
    db_session.flush()
    tse = TransitionStateEntry(
        transition_state_id=ts.id,
        charge=0,
        multiplicity=2,
        unmapped_smiles="[CH2]",
        status=TransitionStateEntryStatus.optimized,
    )
    db_session.add(tse)
    db_session.flush()
    calc = make_calculation(
        db_session,
        type=kw.pop("calc_type", CalculationType.sp),
        transition_state_entry_id=tse.id,
        lot_id=kw.pop("lot_id", None),
    )
    return tse, calc


def _refs(records):
    """Pull calculation_ref out of every record. Useful for set comparisons."""
    return {r["calculation"]["calculation_ref"] for r in records}


# ---------------------------------------------------------------------------
# Missing-filter rule + sort/include validation
# ---------------------------------------------------------------------------


def test_get_search_missing_filter_returns_422(client, db_session):
    resp = client.get(SEARCH_URL)
    assert resp.status_code == 422
    assert "missing_filter" in resp.text


def test_post_search_missing_filter_returns_422(client, db_session):
    resp = client.post(SEARCH_URL, json={})
    assert resp.status_code == 422
    assert "missing_filter" in resp.text


def test_get_search_pure_pagination_does_not_count_as_filter(
    client, db_session
):
    """Pure pagination/include/review knobs without a filter are still
    rejected — the missing-filter rule prevents accidental scans."""
    resp = client.get(
        SEARCH_URL + "?offset=0&limit=10&include_rejected=true"
    )
    assert resp.status_code == 422
    assert "missing_filter" in resp.text


def test_get_search_rejects_client_sort(client, db_session):
    resp = client.get(
        SEARCH_URL + "?calculation_type=opt&sort=created_at"
    )
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_get_search_unknown_include_returns_422(client, db_session):
    resp = client.get(
        SEARCH_URL + "?calculation_type=opt&include=banana"
    )
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


# ---------------------------------------------------------------------------
# Calculation / owner filters
# ---------------------------------------------------------------------------


def test_get_search_by_calculation_type_narrows(client, db_session):
    species, entry, opt_calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    sp_calc = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    body = client.get(SEARCH_URL + "?calculation_type=sp").json()
    refs = _refs(body["records"])
    assert sp_calc.public_ref in refs
    assert opt_calc.public_ref not in refs


def test_get_search_by_owner_kind_species_entry(client, db_session):
    _, _, sp_calc = _make_species_owned_calc(db_session)
    _, ts_calc = _make_ts_owned_calc(db_session)
    body = client.get(SEARCH_URL + "?owner_kind=species_entry").json()
    refs = _refs(body["records"])
    assert sp_calc.public_ref in refs
    assert ts_calc.public_ref not in refs


def test_get_search_by_owner_kind_transition_state_entry(client, db_session):
    _, _, sp_calc = _make_species_owned_calc(db_session)
    _, ts_calc = _make_ts_owned_calc(db_session)
    body = client.get(
        SEARCH_URL + "?owner_kind=transition_state_entry"
    ).json()
    refs = _refs(body["records"])
    assert ts_calc.public_ref in refs
    assert sp_calc.public_ref not in refs


def test_get_search_by_species_entry_ref(client, db_session):
    _, entry_a, calc_a = _make_species_owned_calc(db_session)
    _, _, calc_b = _make_species_owned_calc(db_session)
    body = client.get(
        SEARCH_URL + f"?species_entry_ref={entry_a.public_ref}"
    ).json()
    refs = _refs(body["records"])
    assert refs == {calc_a.public_ref}
    assert calc_b.public_ref not in refs


def test_get_search_by_transition_state_entry_ref(client, db_session):
    tse, ts_calc = _make_ts_owned_calc(db_session)
    _, _, sp_calc = _make_species_owned_calc(db_session)
    body = client.get(
        SEARCH_URL + f"?transition_state_entry_ref={tse.public_ref}"
    ).json()
    refs = _refs(body["records"])
    assert refs == {ts_calc.public_ref}
    assert sp_calc.public_ref not in refs


def test_get_search_unknown_species_entry_ref_returns_empty(
    client, db_session
):
    """Phase C semantics: unknown filter ref short-circuits to empty."""
    body = client.get(
        SEARCH_URL + "?species_entry_ref=spe_doesnotexist"
    ).json()
    assert body["records"] == []
    assert body["pagination"]["total"] == 0


# ---------------------------------------------------------------------------
# LoT / software / workflow filters
# ---------------------------------------------------------------------------


def test_get_search_by_method_and_basis(client, db_session):
    lot_a = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    lot_b = make_lot(db_session, method="b3lyp", basis="6-31g*")
    species, entry, _ = _make_species_owned_calc(db_session)
    calc_a = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
        lot_id=lot_a.id,
    )
    calc_b = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
        lot_id=lot_b.id,
    )
    body = client.get(
        SEARCH_URL + "?method=wb97xd&basis=def2tzvp"
    ).json()
    refs = _refs(body["records"])
    assert calc_a.public_ref in refs
    assert calc_b.public_ref not in refs


def test_get_search_by_software_and_version(client, db_session):
    from app.db.models.software import Software, SoftwareRelease

    sw = Software(name="orca")
    db_session.add(sw)
    db_session.flush()
    rel = SoftwareRelease(software_id=sw.id, version="6.0.1")
    db_session.add(rel)
    db_session.flush()

    species, entry, calc_match = _make_species_owned_calc(db_session)
    calc_match.software_release_id = rel.id
    _, _, calc_other = _make_species_owned_calc(db_session)
    db_session.flush()

    body = client.get(
        SEARCH_URL + "?software=orca&software_version=6.0.1"
    ).json()
    refs = _refs(body["records"])
    assert calc_match.public_ref in refs
    assert calc_other.public_ref not in refs


def test_get_search_by_workflow_tool_and_version(client, db_session):
    from app.db.models.workflow import WorkflowTool, WorkflowToolRelease

    wt = WorkflowTool(name="ARC")
    db_session.add(wt)
    db_session.flush()
    rel = WorkflowToolRelease(workflow_tool_id=wt.id, version="1.2.3")
    db_session.add(rel)
    db_session.flush()

    _, _, calc_match = _make_species_owned_calc(db_session)
    calc_match.workflow_tool_release_id = rel.id
    _, _, calc_other = _make_species_owned_calc(db_session)
    db_session.flush()

    body = client.get(
        SEARCH_URL + "?workflow_tool=ARC&workflow_tool_version=1.2.3"
    ).json()
    refs = _refs(body["records"])
    assert calc_match.public_ref in refs
    assert calc_other.public_ref not in refs


# ---------------------------------------------------------------------------
# has_* filters
# ---------------------------------------------------------------------------


def test_get_search_by_has_result_true(client, db_session):
    _, entry, with_result = _make_species_owned_calc(
        db_session, calc_type=CalculationType.sp
    )
    attach_sp_result(
        db_session, calculation=with_result, electronic_energy_hartree=-1.0
    )
    no_result = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    body = client.get(
        SEARCH_URL + "?calculation_type=sp&has_result=true"
    ).json()
    refs = _refs(body["records"])
    assert with_result.public_ref in refs
    assert no_result.public_ref not in refs


def test_get_search_by_has_artifacts(client, db_session):
    _, entry, with_art = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=with_art)
    no_art = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    body = client.get(
        SEARCH_URL + "?calculation_type=opt&has_artifacts=true"
    ).json()
    refs = _refs(body["records"])
    assert with_art.public_ref in refs
    assert no_art.public_ref not in refs


def test_get_search_by_has_input_geometry(client, db_session):
    _, entry, with_in = _make_species_owned_calc(db_session)
    geom = make_geometry(db_session, natoms=3)
    _attach_input_geometry(
        db_session, calculation=with_in, geometry=geom, input_order=1
    )
    no_in = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    body = client.get(
        SEARCH_URL + "?calculation_type=opt&has_input_geometry=true"
    ).json()
    refs = _refs(body["records"])
    assert with_in.public_ref in refs
    assert no_in.public_ref not in refs


def test_get_search_by_has_output_geometry(client, db_session):
    _, entry, with_out = _make_species_owned_calc(db_session)
    geom = make_geometry(db_session, natoms=3)
    attach_output_geometry(
        db_session,
        calculation=with_out,
        geometry=geom,
        role=CalculationGeometryRole.final,
    )
    no_out = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    body = client.get(
        SEARCH_URL + "?calculation_type=opt&has_output_geometry=true"
    ).json()
    refs = _refs(body["records"])
    assert with_out.public_ref in refs
    assert no_out.public_ref not in refs


# ---------------------------------------------------------------------------
# Validation filters
# ---------------------------------------------------------------------------


def test_get_search_by_geometry_validation_status_passed(client, db_session):
    _, entry, calc_passed = _make_species_owned_calc(db_session)
    attach_geometry_validation(
        db_session,
        calculation=calc_passed,
        status=ValidationStatus.passed,
    )
    calc_no = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    body = client.get(
        SEARCH_URL
        + "?calculation_type=opt&geometry_validation_status=passed"
    ).json()
    refs = _refs(body["records"])
    assert calc_passed.public_ref in refs
    assert calc_no.public_ref not in refs


def test_get_search_by_geometry_validation_status_not_present(
    client, db_session
):
    _, entry, calc_with = _make_species_owned_calc(db_session)
    attach_geometry_validation(
        db_session, calculation=calc_with, status=ValidationStatus.passed
    )
    calc_no = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    body = client.get(
        SEARCH_URL
        + "?calculation_type=opt&geometry_validation_status=not_present"
    ).json()
    refs = _refs(body["records"])
    assert calc_no.public_ref in refs
    assert calc_with.public_ref not in refs


def test_get_search_by_scf_stability_status_stable(client, db_session):
    _, entry, calc_stable = _make_species_owned_calc(db_session)
    attach_scf_stability(
        db_session, calculation=calc_stable, status=SCFStabilityStatus.stable
    )
    calc_no = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    body = client.get(
        SEARCH_URL + "?calculation_type=opt&scf_stability_status=stable"
    ).json()
    refs = _refs(body["records"])
    assert calc_stable.public_ref in refs
    assert calc_no.public_ref not in refs


def test_get_search_by_scf_stability_status_not_present(client, db_session):
    _, entry, calc_with = _make_species_owned_calc(db_session)
    attach_scf_stability(db_session, calculation=calc_with)
    calc_no = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    body = client.get(
        SEARCH_URL
        + "?calculation_type=opt&scf_stability_status=not_present"
    ).json()
    refs = _refs(body["records"])
    assert calc_no.public_ref in refs
    assert calc_with.public_ref not in refs


# ---------------------------------------------------------------------------
# Review / quality trust posture
# ---------------------------------------------------------------------------


def test_get_search_default_excludes_rejected_and_deprecated(
    client, db_session
):
    _, entry, ok_calc = _make_species_owned_calc(db_session)
    rej_calc = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    dep_calc = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=rej_calc.id,
        status=RecordReviewStatus.rejected,
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=dep_calc.id,
        status=RecordReviewStatus.deprecated,
    )
    body = client.get(SEARCH_URL + "?calculation_type=opt").json()
    refs = _refs(body["records"])
    assert ok_calc.public_ref in refs
    assert rej_calc.public_ref not in refs
    assert dep_calc.public_ref not in refs


def test_get_search_include_rejected_returns_them_sorted_last(
    client, db_session
):
    _, entry, ok_calc = _make_species_owned_calc(db_session)
    rej_calc = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=rej_calc.id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(
        SEARCH_URL + "?calculation_type=opt&include_rejected=true"
    ).json()
    statuses = [r["calculation"]["review"]["status"] for r in body["records"]]
    assert "rejected" in statuses
    # Rejected has the worst review_rank (4), so it sorts last.
    assert statuses[-1] == "rejected"


def test_get_search_quality_rejected_requires_opt_in(client, db_session):
    """Default trust posture also excludes ``CalculationQuality.rejected``
    even when review status is fine."""
    _, entry, raw_calc = _make_species_owned_calc(db_session)
    rejq_calc = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    rejq_calc.quality = CalculationQuality.rejected
    db_session.flush()

    body_default = client.get(SEARCH_URL + "?calculation_type=opt").json()
    refs_default = _refs(body_default["records"])
    assert raw_calc.public_ref in refs_default
    assert rejq_calc.public_ref not in refs_default

    body_opt = client.get(
        SEARCH_URL
        + "?calculation_type=opt&include_rejected_quality=true"
    ).json()
    refs_opt = _refs(body_opt["records"])
    assert rejq_calc.public_ref in refs_opt


# ---------------------------------------------------------------------------
# Sorting / pagination
# ---------------------------------------------------------------------------


def test_get_search_ordering_is_deterministic(client, db_session):
    _, entry, _ = _make_species_owned_calc(db_session)
    for _ in range(3):
        make_calculation(
            db_session, type=CalculationType.opt, species_entry_id=entry.id
        )
    body_first = client.get(SEARCH_URL + "?calculation_type=opt").json()
    body_second = client.get(SEARCH_URL + "?calculation_type=opt").json()
    assert body_first["records"] == body_second["records"]


def test_get_search_pagination_envelope(client, db_session):
    _, entry, _ = _make_species_owned_calc(db_session)
    for _ in range(5):
        make_calculation(
            db_session, type=CalculationType.opt, species_entry_id=entry.id
        )

    body = client.get(SEARCH_URL + "?calculation_type=opt&limit=2").json()
    page = body["pagination"]
    assert page["limit"] == 2
    assert page["offset"] == 0
    assert page["returned"] == len(body["records"]) == 2
    assert page["total"] >= 6  # 5 new + 1 from helper

    body2 = client.get(
        SEARCH_URL + "?calculation_type=opt&limit=2&offset=2"
    ).json()
    assert body2["pagination"]["offset"] == 2
    # Disjoint page contents.
    assert _refs(body["records"]).isdisjoint(_refs(body2["records"]))


# ---------------------------------------------------------------------------
# Includes
# ---------------------------------------------------------------------------


def test_get_search_include_results_populates_summary(client, db_session):
    _, entry, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.sp
    )
    attach_sp_result(
        db_session, calculation=calc, electronic_energy_hartree=-2.0
    )
    body = client.get(
        SEARCH_URL + "?calculation_type=sp&include=results"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert record["results"]["kind"] == "sp"
    assert record["results"]["sp"]["electronic_energy_hartree"] == -2.0


def test_get_search_include_results_dependencies_artifacts(client, db_session):
    _, entry, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    attach_opt_result(
        db_session, calculation=calc, final_energy_hartree=-10.0
    )
    child = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=entry.id
    )
    attach_dependency(
        db_session,
        parent=calc,
        child=child,
        role=CalculationDependencyRole.freq_on,
    )
    attach_artifact(db_session, calculation=calc)
    body = client.get(
        SEARCH_URL
        + "?calculation_type=opt&include=results,dependencies,artifacts"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert record["results"]["kind"] == "opt"
    assert len(record["dependencies"]) == 1
    assert len(record["artifacts"]) == 1


def test_get_search_default_omits_heavy_sections(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    attach_sp_result(
        db_session, calculation=calc, electronic_energy_hartree=-1.0
    )
    body = client.get(SEARCH_URL + "?calculation_type=opt").json()
    for record in body["records"]:
        for heavy in (
            "results",
            "dependencies",
            "artifacts",
            "input_geometries",
            "output_geometries",
            "geometry_validation",
            "scf_stability",
        ):
            assert heavy not in record


# ---------------------------------------------------------------------------
# Internal-IDs visibility
# ---------------------------------------------------------------------------


def test_get_search_default_hides_internal_ids(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(SEARCH_URL + "?calculation_type=opt").json()
    for record in body["records"]:
        assert "calculation_id" not in record["calculation"]


def test_get_search_internal_ids_when_allowed(
    client, db_session, allow_internal_ids
):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        SEARCH_URL + "?calculation_type=opt&include=internal_ids"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert record["calculation"]["calculation_id"] == calc.id


# ---------------------------------------------------------------------------
# POST parity
# ---------------------------------------------------------------------------


def test_post_search_returns_same_records_as_get(client, db_session):
    _, entry, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.sp
    )
    attach_sp_result(
        db_session, calculation=calc, electronic_energy_hartree=-3.0
    )
    body_get = client.get(
        SEARCH_URL + "?calculation_type=sp&include=results"
    ).json()
    body_post = client.post(
        SEARCH_URL,
        json={
            "calculation_type": "sp",
            "include": ["results"],
        },
    ).json()
    assert body_get["records"] == body_post["records"]
    assert body_get["pagination"] == body_post["pagination"]


def test_post_search_rejects_query_string_search_fields(client, db_session):
    resp = client.post(
        SEARCH_URL + "?calculation_type=opt",
        json={"calculation_type": "opt"},
    )
    assert resp.status_code == 422
    assert "post_search_fields_must_be_in_body" in resp.text


def test_post_search_owner_kind_filter(client, db_session):
    _, _, sp_calc = _make_species_owned_calc(db_session)
    _, ts_calc = _make_ts_owned_calc(db_session)
    body = client.post(
        SEARCH_URL,
        json={"owner_kind": "transition_state_entry"},
    ).json()
    refs = _refs(body["records"])
    assert ts_calc.public_ref in refs
    assert sp_calc.public_ref not in refs


# ---------------------------------------------------------------------------
# Dependency-graph filters
# ---------------------------------------------------------------------------


def _make_freq_on_pair(db_session):
    """Create an opt parent → freq child pair connected by ``freq_on``."""
    _, entry, opt_parent = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    freq_child = make_calculation(
        db_session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
    )
    attach_dependency(
        db_session,
        parent=opt_parent,
        child=freq_child,
        role=CalculationDependencyRole.freq_on,
    )
    return entry, opt_parent, freq_child


def test_search_dependency_role_alone_counts_as_filter(client, db_session):
    """``dependency_role`` is meaningful — ``missing_filter`` must NOT fire."""
    _, parent, child = _make_freq_on_pair(db_session)
    resp = client.get(SEARCH_URL + "?dependency_role=freq_on")
    assert resp.status_code == 200
    refs = _refs(resp.json()["records"])
    # Both endpoints participate in a freq_on edge.
    assert parent.public_ref in refs
    assert child.public_ref in refs


def test_search_by_dependency_role_returns_both_endpoints(client, db_session):
    """``dependency_role=X`` returns both parent and child participants
    in any role-X edge."""
    _, opt_a, freq_a = _make_freq_on_pair(db_session)
    _, opt_b, freq_b = _make_freq_on_pair(db_session)
    # Add an unrelated calc with no dependency to confirm it's filtered out.
    _, _, unrelated = _make_species_owned_calc(db_session)

    body = client.get(SEARCH_URL + "?dependency_role=freq_on").json()
    refs = _refs(body["records"])
    assert {opt_a.public_ref, freq_a.public_ref}.issubset(refs)
    assert {opt_b.public_ref, freq_b.public_ref}.issubset(refs)
    assert unrelated.public_ref not in refs


def test_search_by_parent_calculation_ref_returns_children(client, db_session):
    _, opt_parent, freq_child = _make_freq_on_pair(db_session)
    # An unrelated calc without an edge to the parent.
    _, _, unrelated = _make_species_owned_calc(db_session)

    body = client.get(
        SEARCH_URL + f"?parent_calculation_ref={opt_parent.public_ref}"
    ).json()
    refs = _refs(body["records"])
    assert refs == {freq_child.public_ref}
    assert opt_parent.public_ref not in refs
    assert unrelated.public_ref not in refs


def test_search_by_child_calculation_ref_returns_parents(client, db_session):
    _, opt_parent, freq_child = _make_freq_on_pair(db_session)
    _, _, unrelated = _make_species_owned_calc(db_session)

    body = client.get(
        SEARCH_URL + f"?child_calculation_ref={freq_child.public_ref}"
    ).json()
    refs = _refs(body["records"])
    assert refs == {opt_parent.public_ref}
    assert freq_child.public_ref not in refs
    assert unrelated.public_ref not in refs


def test_search_parent_ref_with_dependency_role_narrows(client, db_session):
    """Same parent with two distinct child roles; role filter narrows."""
    _, entry, parent = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    freq_child = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=entry.id
    )
    sp_child = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    attach_dependency(
        db_session,
        parent=parent,
        child=freq_child,
        role=CalculationDependencyRole.freq_on,
    )
    attach_dependency(
        db_session,
        parent=parent,
        child=sp_child,
        role=CalculationDependencyRole.single_point_on,
    )

    body = client.get(
        SEARCH_URL
        + f"?parent_calculation_ref={parent.public_ref}"
        + "&dependency_role=freq_on"
    ).json()
    refs = _refs(body["records"])
    assert refs == {freq_child.public_ref}


def test_search_child_ref_with_dependency_role_narrows(client, db_session):
    """Same child with two distinct parent roles (different roles allowed
    by the schema's partial uniqueness); role filter narrows."""
    _, entry, child = _make_species_owned_calc(
        db_session, calc_type=CalculationType.freq
    )
    parent_freq = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    parent_sp = make_calculation(
        db_session, type=CalculationType.sp, species_entry_id=entry.id
    )
    attach_dependency(
        db_session,
        parent=parent_freq,
        child=child,
        role=CalculationDependencyRole.freq_on,
    )
    attach_dependency(
        db_session,
        parent=parent_sp,
        child=child,
        role=CalculationDependencyRole.single_point_on,
    )

    body = client.get(
        SEARCH_URL
        + f"?child_calculation_ref={child.public_ref}"
        + "&dependency_role=single_point_on"
    ).json()
    refs = _refs(body["records"])
    assert refs == {parent_sp.public_ref}


def test_search_both_endpoint_refs_returns_both_when_edge_exists(
    client, db_session
):
    _, parent, child = _make_freq_on_pair(db_session)
    body = client.get(
        SEARCH_URL
        + f"?parent_calculation_ref={parent.public_ref}"
        + f"&child_calculation_ref={child.public_ref}"
    ).json()
    refs = _refs(body["records"])
    # Chosen behavior: return both endpoints when the exact edge exists.
    assert refs == {parent.public_ref, child.public_ref}


def test_search_both_endpoint_refs_returns_empty_when_edge_missing(
    client, db_session
):
    _, entry, parent = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    detached = make_calculation(
        db_session, type=CalculationType.freq, species_entry_id=entry.id
    )
    body = client.get(
        SEARCH_URL
        + f"?parent_calculation_ref={parent.public_ref}"
        + f"&child_calculation_ref={detached.public_ref}"
    ).json()
    assert body["records"] == []
    assert body["pagination"]["total"] == 0


def test_search_both_endpoint_refs_with_role_narrows(client, db_session):
    """Edge exists with role A; a role=B query returns empty."""
    _, parent, child = _make_freq_on_pair(db_session)
    body = client.get(
        SEARCH_URL
        + f"?parent_calculation_ref={parent.public_ref}"
        + f"&child_calculation_ref={child.public_ref}"
        + "&dependency_role=single_point_on"
    ).json()
    assert body["records"] == []


def test_search_unknown_parent_calculation_ref_returns_empty(
    client, db_session
):
    body = client.get(
        SEARCH_URL + "?parent_calculation_ref=calc_doesnotexist"
    ).json()
    assert body["records"] == []
    assert body["pagination"]["total"] == 0


def test_search_wrong_prefix_parent_calculation_ref_returns_422(
    client, db_session
):
    resp = client.get(
        SEARCH_URL + "?parent_calculation_ref=spe_abcdef0123456789"
    )
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_search_malformed_parent_calculation_ref_returns_422(
    client, db_session
):
    resp = client.get(
        SEARCH_URL + "?parent_calculation_ref=not-a-handle"
    )
    assert resp.status_code == 422
    assert "invalid_handle" in resp.text


def test_search_dependency_filter_and_calculation_type(client, db_session):
    """``dependency_role + calculation_type`` AND-combine."""
    _, opt_a, freq_a = _make_freq_on_pair(db_session)
    body = client.get(
        SEARCH_URL + "?dependency_role=freq_on&calculation_type=freq"
    ).json()
    refs = _refs(body["records"])
    assert refs == {freq_a.public_ref}
    assert opt_a.public_ref not in refs


def test_search_dependency_filter_and_owner_kind(client, db_session):
    """``dependency_role + owner_kind`` AND-combine."""
    # Build a TS-owned opt parent → species-owned freq child via freq_on
    # would violate the one_owner constraint; simpler: construct one TS-owned
    # pair and one species-owned pair, both with freq_on, then narrow by owner_kind.
    tse, ts_parent = _make_ts_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    ts_child = make_calculation(
        db_session,
        type=CalculationType.freq,
        transition_state_entry_id=tse.id,
    )
    attach_dependency(
        db_session,
        parent=ts_parent,
        child=ts_child,
        role=CalculationDependencyRole.freq_on,
    )
    _, sp_opt, sp_freq = _make_freq_on_pair(db_session)

    body = client.get(
        SEARCH_URL
        + "?dependency_role=freq_on&owner_kind=transition_state_entry"
    ).json()
    refs = _refs(body["records"])
    assert {ts_parent.public_ref, ts_child.public_ref}.issubset(refs)
    assert sp_opt.public_ref not in refs
    assert sp_freq.public_ref not in refs


def test_search_dependency_filters_with_include_dependencies(
    client, db_session
):
    _, parent, child = _make_freq_on_pair(db_session)
    body = client.get(
        SEARCH_URL
        + f"?parent_calculation_ref={parent.public_ref}"
        + "&include=dependencies"
    ).json()
    rec = body["records"][0]
    assert rec["calculation"]["calculation_ref"] == child.public_ref
    assert len(rec["dependencies"]) >= 1
    edge = rec["dependencies"][0]
    # From the child's POV, this edge has direction='child'.
    assert edge["direction"] == "child"
    assert edge["role"] == "freq_on"
    assert edge["parent_calculation_ref"] == parent.public_ref
    assert edge["child_calculation_ref"] == child.public_ref


def test_search_dependency_filters_with_include_results_and_dependencies(
    client, db_session
):
    _, parent, child = _make_freq_on_pair(db_session)
    attach_opt_result(
        db_session, calculation=parent, final_energy_hartree=-12.0
    )
    body = client.get(
        SEARCH_URL
        + f"?child_calculation_ref={child.public_ref}"
        + "&include=results,dependencies"
    ).json()
    rec = body["records"][0]
    assert rec["calculation"]["calculation_ref"] == parent.public_ref
    assert rec["results"]["kind"] == "opt"
    assert rec["results"]["opt"]["final_energy_hartree"] == -12.0
    assert len(rec["dependencies"]) >= 1


def test_search_post_supports_dependency_filters(client, db_session):
    _, parent, child = _make_freq_on_pair(db_session)
    body = client.post(
        SEARCH_URL,
        json={
            "parent_calculation_ref": parent.public_ref,
            "dependency_role": "freq_on",
        },
    ).json()
    refs = _refs(body["records"])
    assert refs == {child.public_ref}


def test_search_dependency_get_post_parity(client, db_session):
    _, parent, child = _make_freq_on_pair(db_session)
    body_get = client.get(
        SEARCH_URL
        + f"?parent_calculation_ref={parent.public_ref}"
        + "&dependency_role=freq_on"
    ).json()
    body_post = client.post(
        SEARCH_URL,
        json={
            "parent_calculation_ref": parent.public_ref,
            "dependency_role": "freq_on",
        },
    ).json()
    assert body_get["records"] == body_post["records"]
    assert body_get["pagination"] == body_post["pagination"]


def test_search_dependency_ordering_is_deterministic(client, db_session):
    """A role-only filter with multiple matches returns the same record
    order across two requests."""
    for _ in range(3):
        _make_freq_on_pair(db_session)
    body_first = client.get(SEARCH_URL + "?dependency_role=freq_on").json()
    body_second = client.get(SEARCH_URL + "?dependency_role=freq_on").json()
    assert body_first["records"] == body_second["records"]


# ---------------------------------------------------------------------------
# artifact_kind filter
# ---------------------------------------------------------------------------


def test_search_artifact_kind_alone_counts_as_filter(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    attach_artifact(
        db_session, calculation=calc, kind=ArtifactKind.output_log
    )
    resp = client.get(SEARCH_URL + "?artifact_kind=output_log")
    assert resp.status_code == 200
    refs = _refs(resp.json()["records"])
    assert calc.public_ref in refs


def test_search_by_artifact_kind_input(client, db_session):
    _, entry, with_input = _make_species_owned_calc(db_session)
    attach_artifact(
        db_session,
        calculation=with_input,
        kind=ArtifactKind.input,
        filename="job.in",
    )
    with_log = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    attach_artifact(
        db_session, calculation=with_log, kind=ArtifactKind.output_log
    )
    body = client.get(SEARCH_URL + "?artifact_kind=input").json()
    refs = _refs(body["records"])
    assert with_input.public_ref in refs
    assert with_log.public_ref not in refs


def test_search_by_artifact_kind_output_log(client, db_session):
    _, entry, with_log = _make_species_owned_calc(db_session)
    attach_artifact(
        db_session, calculation=with_log, kind=ArtifactKind.output_log
    )
    no_artifacts = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    body = client.get(SEARCH_URL + "?artifact_kind=output_log").json()
    refs = _refs(body["records"])
    assert with_log.public_ref in refs
    assert no_artifacts.public_ref not in refs


def test_search_artifact_kind_is_stricter_than_has_artifacts(
    client, db_session
):
    """A calc with only ``input`` artifacts must NOT match
    ``artifact_kind=output_log``, even though ``has_artifacts=true``
    would match it."""
    _, _, only_input = _make_species_owned_calc(db_session)
    attach_artifact(
        db_session, calculation=only_input, kind=ArtifactKind.input
    )
    body = client.get(
        SEARCH_URL + "?artifact_kind=output_log"
    ).json()
    refs = _refs(body["records"])
    assert only_input.public_ref not in refs


def test_search_artifact_kind_and_calculation_type(client, db_session):
    _, entry, sp_calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.sp
    )
    attach_artifact(
        db_session, calculation=sp_calc, kind=ArtifactKind.output_log
    )
    opt_calc = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    attach_artifact(
        db_session, calculation=opt_calc, kind=ArtifactKind.output_log
    )
    body = client.get(
        SEARCH_URL + "?calculation_type=sp&artifact_kind=output_log"
    ).json()
    refs = _refs(body["records"])
    assert sp_calc.public_ref in refs
    assert opt_calc.public_ref not in refs


def test_search_artifact_kind_and_owner_kind(client, db_session):
    _, _, sp_calc = _make_species_owned_calc(db_session)
    attach_artifact(
        db_session, calculation=sp_calc, kind=ArtifactKind.checkpoint
    )
    tse, ts_calc = _make_ts_owned_calc(db_session)
    attach_artifact(
        db_session, calculation=ts_calc, kind=ArtifactKind.checkpoint
    )
    body = client.get(
        SEARCH_URL
        + "?artifact_kind=checkpoint&owner_kind=transition_state_entry"
    ).json()
    refs = _refs(body["records"])
    assert ts_calc.public_ref in refs
    assert sp_calc.public_ref not in refs


def test_search_artifact_kind_and_has_artifacts_true(client, db_session):
    """Combining ``artifact_kind=X`` with ``has_artifacts=true`` is
    equivalent to ``artifact_kind=X``."""
    _, _, calc = _make_species_owned_calc(db_session)
    attach_artifact(
        db_session, calculation=calc, kind=ArtifactKind.output_log
    )
    body_combo = client.get(
        SEARCH_URL + "?artifact_kind=output_log&has_artifacts=true"
    ).json()
    body_solo = client.get(
        SEARCH_URL + "?artifact_kind=output_log"
    ).json()
    assert _refs(body_combo["records"]) == _refs(body_solo["records"])
    assert calc.public_ref in _refs(body_combo["records"])


def test_search_artifact_kind_and_has_artifacts_false_returns_empty(
    client, db_session
):
    """Contradictory combo: returns empty result without 422."""
    _, _, calc = _make_species_owned_calc(db_session)
    attach_artifact(
        db_session, calculation=calc, kind=ArtifactKind.output_log
    )
    resp = client.get(
        SEARCH_URL + "?artifact_kind=output_log&has_artifacts=false"
    )
    assert resp.status_code == 200
    assert resp.json()["records"] == []
    assert resp.json()["pagination"]["total"] == 0


def test_search_artifact_kind_with_include_artifacts_returns_all(
    client, db_session
):
    """``include=artifacts`` returns *all* artifacts on matching calcs,
    not only artifacts of the filtered kind. Filter narrows calcs;
    include selects child rows."""
    _, _, calc = _make_species_owned_calc(db_session)
    attach_artifact(
        db_session,
        calculation=calc,
        kind=ArtifactKind.input,
        filename="job.in",
    )
    attach_artifact(
        db_session,
        calculation=calc,
        kind=ArtifactKind.output_log,
        filename="job.log",
    )
    body = client.get(
        SEARCH_URL + "?artifact_kind=output_log&include=artifacts"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    kinds = sorted(a["kind"] for a in record["artifacts"])
    # Both artifacts present even though the filter was kind=output_log.
    assert kinds == ["input", "output_log"]


def test_search_artifact_kind_unknown_value_returns_422(client, db_session):
    resp = client.get(SEARCH_URL + "?artifact_kind=not_a_kind")
    assert resp.status_code == 422


def test_search_post_supports_artifact_kind(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    attach_artifact(
        db_session, calculation=calc, kind=ArtifactKind.output_log
    )
    body = client.post(
        SEARCH_URL,
        json={"artifact_kind": "output_log"},
    ).json()
    refs = _refs(body["records"])
    assert calc.public_ref in refs


def test_search_artifact_kind_get_post_parity(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    attach_artifact(
        db_session, calculation=calc, kind=ArtifactKind.input
    )
    body_get = client.get(SEARCH_URL + "?artifact_kind=input").json()
    body_post = client.post(
        SEARCH_URL, json={"artifact_kind": "input"}
    ).json()
    assert body_get["records"] == body_post["records"]
    assert body_get["pagination"] == body_post["pagination"]


def test_search_artifact_kind_ordering_is_deterministic(client, db_session):
    """Multiple matching calcs return in the same order across two calls."""
    _, entry, _ = _make_species_owned_calc(db_session)
    for _ in range(3):
        c = make_calculation(
            db_session, type=CalculationType.opt, species_entry_id=entry.id
        )
        attach_artifact(
            db_session, calculation=c, kind=ArtifactKind.output_log
        )
    body_first = client.get(SEARCH_URL + "?artifact_kind=output_log").json()
    body_second = client.get(SEARCH_URL + "?artifact_kind=output_log").json()
    assert body_first["records"] == body_second["records"]


# ---------------------------------------------------------------------------
# calculation-parameter filters
# ---------------------------------------------------------------------------


def _ensure_param_vocab(db_session, canonical_key: str) -> None:
    """Idempotent: insert a vocab row for *canonical_key* if missing.

    ``calculation_parameter.canonical_key`` has an FK to
    ``calculation_parameter_vocab.canonical_key``, so any test that
    sets a canonical_key must first ensure the vocab row exists.
    """
    existing = db_session.get(CalculationParameterVocab, canonical_key)
    if existing is None:
        db_session.add(
            CalculationParameterVocab(canonical_key=canonical_key)
        )
        db_session.flush()


def _attach_parameter(
    db_session,
    *,
    calculation,
    raw_key: str,
    raw_value: str,
    canonical_key: str | None = None,
    canonical_value: str | None = None,
):
    """Attach a single ``calculation_parameter`` row.

    No factory helper exists yet for parameters; this is a narrow local
    fixture for the search tests. When *canonical_key* is supplied, the
    matching ``calculation_parameter_vocab`` row is created on-demand
    so the FK constraint is satisfied.
    """
    if canonical_key is not None:
        _ensure_param_vocab(db_session, canonical_key)
    row = CalculationParameter(
        calculation_id=calculation.id,
        raw_key=raw_key,
        raw_value=raw_value,
        canonical_key=canonical_key,
        canonical_value=canonical_value,
        source=ParameterSource.upload,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_search_parameter_key_alone_counts_as_filter(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_parameter(
        db_session, calculation=calc, raw_key="Grid", raw_value="ultrafine"
    )
    resp = client.get(SEARCH_URL + "?parameter_key=Grid")
    assert resp.status_code == 200
    refs = _refs(resp.json()["records"])
    assert calc.public_ref in refs


def test_search_canonical_parameter_key_alone_counts_as_filter(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_parameter(
        db_session,
        calculation=calc,
        raw_key="ScfConv",
        raw_value="1e-8",
        canonical_key="scf.convergence",
        canonical_value="1e-8",
    )
    resp = client.get(
        SEARCH_URL + "?canonical_parameter_key=scf.convergence"
    )
    assert resp.status_code == 200
    refs = _refs(resp.json()["records"])
    assert calc.public_ref in refs


def test_search_by_parameter_key_only(client, db_session):
    _, entry, with_grid = _make_species_owned_calc(db_session)
    _attach_parameter(
        db_session,
        calculation=with_grid,
        raw_key="Grid",
        raw_value="ultrafine",
    )
    no_grid = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    body = client.get(SEARCH_URL + "?parameter_key=Grid").json()
    refs = _refs(body["records"])
    assert with_grid.public_ref in refs
    assert no_grid.public_ref not in refs


def test_search_by_parameter_key_and_value_same_row(client, db_session):
    """Key and value must match on the same parameter row."""
    _, entry, match = _make_species_owned_calc(db_session)
    _attach_parameter(
        db_session, calculation=match, raw_key="Grid", raw_value="ultrafine"
    )
    # Same calc, but key=Grid value lives on a *different* row from the
    # one where value=fine.
    mismatch = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    _attach_parameter(
        db_session, calculation=mismatch, raw_key="Grid", raw_value="fine"
    )
    _attach_parameter(
        db_session,
        calculation=mismatch,
        raw_key="OtherKey",
        raw_value="ultrafine",
    )

    body = client.get(
        SEARCH_URL + "?parameter_key=Grid&parameter_value=ultrafine"
    ).json()
    refs = _refs(body["records"])
    assert match.public_ref in refs
    assert mismatch.public_ref not in refs


def test_search_parameter_value_without_key_returns_422(client, db_session):
    resp = client.get(
        SEARCH_URL
        + "?calculation_type=opt&parameter_value=ultrafine"
    )
    assert resp.status_code == 422
    assert "parameter_value_requires_key" in resp.text


def test_search_canonical_parameter_value_without_key_returns_422(
    client, db_session
):
    resp = client.get(
        SEARCH_URL
        + "?calculation_type=opt&canonical_parameter_value=1e-8"
    )
    assert resp.status_code == 422
    assert "canonical_parameter_value_requires_key" in resp.text


def test_search_by_canonical_key_and_value_same_row(client, db_session):
    """Canonical key + canonical value must match on the same row."""
    _, entry, match = _make_species_owned_calc(db_session)
    _attach_parameter(
        db_session,
        calculation=match,
        raw_key="ScfConv",
        raw_value="1e-8",
        canonical_key="scf.convergence",
        canonical_value="1e-8",
    )
    other_value = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    _attach_parameter(
        db_session,
        calculation=other_value,
        raw_key="ScfConv",
        raw_value="1e-6",
        canonical_key="scf.convergence",
        canonical_value="1e-6",
    )
    body = client.get(
        SEARCH_URL
        + "?canonical_parameter_key=scf.convergence"
        + "&canonical_parameter_value=1e-8"
    ).json()
    refs = _refs(body["records"])
    assert match.public_ref in refs
    assert other_value.public_ref not in refs


def test_search_unknown_parameter_key_returns_empty(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_parameter(
        db_session, calculation=calc, raw_key="Grid", raw_value="ultrafine"
    )
    resp = client.get(SEARCH_URL + "?parameter_key=NotAKnownKey")
    assert resp.status_code == 200
    body = resp.json()
    assert body["records"] == []
    assert body["pagination"]["total"] == 0


def test_search_unknown_canonical_parameter_key_returns_empty(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_parameter(
        db_session,
        calculation=calc,
        raw_key="ScfConv",
        raw_value="1e-8",
        canonical_key="scf.convergence",
        canonical_value="1e-8",
    )
    resp = client.get(
        SEARCH_URL + "?canonical_parameter_key=does.not.exist"
    )
    assert resp.status_code == 200
    assert resp.json()["records"] == []


def test_search_raw_and_canonical_parameter_filters_combine(client, db_session):
    """Both raw key and canonical key supplied: calc must have a row
    matching the raw key AND a row matching the canonical key (not
    necessarily the same row)."""
    _, entry, calc = _make_species_owned_calc(db_session)
    # Two parameter rows, one carrying the raw key and one carrying
    # the canonical key. AND-combine should match this calc.
    _attach_parameter(
        db_session,
        calculation=calc,
        raw_key="Grid",
        raw_value="ultrafine",
    )
    _attach_parameter(
        db_session,
        calculation=calc,
        raw_key="ScfConv",
        raw_value="1e-8",
        canonical_key="scf.convergence",
        canonical_value="1e-8",
    )
    # Another calc with only the raw key — must NOT match.
    only_raw = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    _attach_parameter(
        db_session,
        calculation=only_raw,
        raw_key="Grid",
        raw_value="ultrafine",
    )

    body = client.get(
        SEARCH_URL
        + "?parameter_key=Grid"
        + "&canonical_parameter_key=scf.convergence"
    ).json()
    refs = _refs(body["records"])
    assert calc.public_ref in refs
    assert only_raw.public_ref not in refs


def test_search_parameter_filter_and_calculation_type(client, db_session):
    _, entry, sp_calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.sp
    )
    _attach_parameter(
        db_session,
        calculation=sp_calc,
        raw_key="ScfConv",
        raw_value="1e-8",
    )
    opt_calc = make_calculation(
        db_session, type=CalculationType.opt, species_entry_id=entry.id
    )
    _attach_parameter(
        db_session,
        calculation=opt_calc,
        raw_key="ScfConv",
        raw_value="1e-8",
    )
    body = client.get(
        SEARCH_URL + "?calculation_type=sp&parameter_key=ScfConv"
    ).json()
    refs = _refs(body["records"])
    assert sp_calc.public_ref in refs
    assert opt_calc.public_ref not in refs


def test_search_parameter_filter_and_method_basis(client, db_session):
    lot_match = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    lot_other = make_lot(db_session, method="b3lyp", basis="6-31g*")
    _, entry, _ = _make_species_owned_calc(db_session)

    match = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
        lot_id=lot_match.id,
    )
    _attach_parameter(
        db_session, calculation=match, raw_key="Grid", raw_value="ultrafine"
    )
    other = make_calculation(
        db_session,
        type=CalculationType.sp,
        species_entry_id=entry.id,
        lot_id=lot_other.id,
    )
    _attach_parameter(
        db_session, calculation=other, raw_key="Grid", raw_value="ultrafine"
    )
    body = client.get(
        SEARCH_URL
        + "?method=wb97xd&basis=def2tzvp&parameter_key=Grid"
    ).json()
    refs = _refs(body["records"])
    assert match.public_ref in refs
    assert other.public_ref not in refs


def test_search_parameter_filter_and_owner_kind(client, db_session):
    tse, ts_calc = _make_ts_owned_calc(db_session)
    _attach_parameter(
        db_session,
        calculation=ts_calc,
        raw_key="ScfConv",
        raw_value="1e-8",
        canonical_key="scf.convergence",
        canonical_value="1e-8",
    )
    _, _, sp_calc = _make_species_owned_calc(db_session)
    _attach_parameter(
        db_session,
        calculation=sp_calc,
        raw_key="ScfConv",
        raw_value="1e-8",
        canonical_key="scf.convergence",
        canonical_value="1e-8",
    )
    body = client.get(
        SEARCH_URL
        + "?canonical_parameter_key=scf.convergence"
        + "&owner_kind=transition_state_entry"
    ).json()
    refs = _refs(body["records"])
    assert ts_calc.public_ref in refs
    assert sp_calc.public_ref not in refs


def test_search_post_supports_parameter_filters(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_parameter(
        db_session,
        calculation=calc,
        raw_key="Grid",
        raw_value="ultrafine",
        canonical_key="grid",
        canonical_value="ultrafine",
    )
    body = client.post(
        SEARCH_URL,
        json={
            "parameter_key": "Grid",
            "canonical_parameter_key": "grid",
        },
    ).json()
    refs = _refs(body["records"])
    assert calc.public_ref in refs


def test_search_parameter_get_post_parity(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_parameter(
        db_session,
        calculation=calc,
        raw_key="Grid",
        raw_value="ultrafine",
    )
    body_get = client.get(
        SEARCH_URL + "?parameter_key=Grid&parameter_value=ultrafine"
    ).json()
    body_post = client.post(
        SEARCH_URL,
        json={"parameter_key": "Grid", "parameter_value": "ultrafine"},
    ).json()
    assert body_get["records"] == body_post["records"]
    assert body_get["pagination"] == body_post["pagination"]


def test_search_parameter_filter_ordering_is_deterministic(client, db_session):
    _, entry, _ = _make_species_owned_calc(db_session)
    for _ in range(3):
        c = make_calculation(
            db_session, type=CalculationType.opt, species_entry_id=entry.id
        )
        _attach_parameter(
            db_session, calculation=c, raw_key="Grid", raw_value="ultrafine"
        )
    body_first = client.get(SEARCH_URL + "?parameter_key=Grid").json()
    body_second = client.get(SEARCH_URL + "?parameter_key=Grid").json()
    assert body_first["records"] == body_second["records"]


# ---------------------------------------------------------------------------
# Search × include=parameters
# ---------------------------------------------------------------------------


def test_search_include_parameters_returns_summaries(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_parameter(
        db_session,
        calculation=calc,
        raw_key="ScfConv",
        raw_value="1e-8",
        canonical_key="scf.convergence",
        canonical_value="1e-8",
    )
    body = client.get(
        SEARCH_URL + "?calculation_type=opt&include=parameters"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    rows = record["parameters"]
    assert isinstance(rows, list) and len(rows) == 1
    assert rows[0]["raw_key"] == "ScfConv"
    assert rows[0]["canonical_key"] == "scf.convergence"
    # Default Phase D: parameter_id stripped.
    assert "parameter_id" not in rows[0]


def test_search_parameter_filter_with_include_parameters_returns_all_rows(
    client, db_session
):
    """``parameter_key=X`` narrows the *parent calculation set*, but
    ``include=parameters`` returns *every* parameter row attached to
    the matching calc, not just the rows matching the filter."""
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_parameter(
        db_session,
        calculation=calc,
        raw_key="Grid",
        raw_value="ultrafine",
    )
    _attach_parameter(
        db_session,
        calculation=calc,
        raw_key="ScfConv",
        raw_value="1e-8",
    )
    body = client.get(
        SEARCH_URL
        + "?parameter_key=Grid&include=parameters"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    raw_keys = sorted(r["raw_key"] for r in record["parameters"])
    # Both rows present even though the filter was raw_key=Grid only.
    assert raw_keys == ["Grid", "ScfConv"]


def test_search_include_parameters_returns_empty_list_when_none(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        SEARCH_URL + "?calculation_type=opt&include=parameters"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert record["parameters"] == []
    assert record["available_sections"]["has_parameters"] is False


def test_search_post_supports_include_parameters(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_parameter(
        db_session, calculation=calc, raw_key="Grid", raw_value="ultrafine"
    )
    body = client.post(
        SEARCH_URL,
        json={
            "calculation_type": "opt",
            "include": ["parameters"],
        },
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert len(record["parameters"]) == 1


def test_search_include_parameters_get_post_parity(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_parameter(
        db_session,
        calculation=calc,
        raw_key="ScfConv",
        raw_value="1e-8",
    )
    body_get = client.get(
        SEARCH_URL + "?calculation_type=opt&include=parameters"
    ).json()
    body_post = client.post(
        SEARCH_URL,
        json={"calculation_type": "opt", "include": ["parameters"]},
    ).json()
    assert body_get["records"] == body_post["records"]
    assert body_get["pagination"] == body_post["pagination"]


def test_search_include_parameters_internal_ids_when_allowed(
    client, db_session, allow_internal_ids
):
    _, _, calc = _make_species_owned_calc(db_session)
    row = _attach_parameter(
        db_session, calculation=calc, raw_key="Grid", raw_value="ultrafine"
    )
    body = client.get(
        SEARCH_URL
        + "?calculation_type=opt&include=parameters,internal_ids"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert record["parameters"][0]["parameter_id"] == row.id


def test_search_include_parameters_default_hides_parameter_ids(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_parameter(
        db_session, calculation=calc, raw_key="Grid", raw_value="ultrafine"
    )
    body = client.get(
        SEARCH_URL + "?calculation_type=opt&include=parameters"
    ).json()
    for record in body["records"]:
        if record.get("parameters"):
            for row in record["parameters"]:
                assert "parameter_id" not in row


# ---------------------------------------------------------------------------
# Search × include=constraints
# ---------------------------------------------------------------------------


def _attach_constraint(
    db_session,
    *,
    calculation,
    constraint_index: int,
    constraint_kind: ConstraintKind = ConstraintKind.bond,
    atom1_index: int = 1,
    atom2_index: int | None = 2,
    atom3_index: int | None = None,
    atom4_index: int | None = None,
    target_value: float | None = None,
):
    """Insert one ``calculation_constraint`` row for the search tests."""
    row = CalculationConstraint(
        calculation_id=calculation.id,
        constraint_index=constraint_index,
        constraint_kind=constraint_kind,
        atom1_index=atom1_index,
        atom2_index=atom2_index,
        atom3_index=atom3_index,
        atom4_index=atom4_index,
        target_value=target_value,
    )
    db_session.add(row)
    db_session.flush()
    return row


def test_search_include_constraints_returns_summaries(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_constraint(
        db_session,
        calculation=calc,
        constraint_index=1,
        constraint_kind=ConstraintKind.bond,
        atom1_index=1,
        atom2_index=2,
        target_value=1.42,
    )
    body = client.get(
        SEARCH_URL + "?calculation_type=opt&include=constraints"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    rows = record["constraints"]
    assert isinstance(rows, list) and len(rows) == 1
    assert rows[0]["constraint_kind"] == "bond"
    assert rows[0]["atom_indices"] == [1, 2]
    assert rows[0]["target_value"] == 1.42
    # Default Phase D: calculation_id stripped on the constraint summary too.
    assert "calculation_id" not in rows[0]


def test_search_include_constraints_returns_empty_list_when_none(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        SEARCH_URL + "?calculation_type=opt&include=constraints"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert record["constraints"] == []
    assert record["available_sections"]["has_constraints"] is False


def test_search_post_supports_include_constraints(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_constraint(
        db_session,
        calculation=calc,
        constraint_index=1,
        constraint_kind=ConstraintKind.bond,
        atom1_index=1,
        atom2_index=2,
    )
    body = client.post(
        SEARCH_URL,
        json={"calculation_type": "opt", "include": ["constraints"]},
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert len(record["constraints"]) == 1


def test_search_include_constraints_get_post_parity(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_constraint(
        db_session,
        calculation=calc,
        constraint_index=1,
        constraint_kind=ConstraintKind.bond,
        atom1_index=1,
        atom2_index=2,
    )
    body_get = client.get(
        SEARCH_URL + "?calculation_type=opt&include=constraints"
    ).json()
    body_post = client.post(
        SEARCH_URL,
        json={"calculation_type": "opt", "include": ["constraints"]},
    ).json()
    assert body_get["records"] == body_post["records"]
    assert body_get["pagination"] == body_post["pagination"]


def test_search_include_constraints_internal_ids_when_allowed(
    client, db_session, allow_internal_ids
):
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_constraint(
        db_session,
        calculation=calc,
        constraint_index=1,
        constraint_kind=ConstraintKind.bond,
        atom1_index=1,
        atom2_index=2,
    )
    body = client.get(
        SEARCH_URL
        + "?calculation_type=opt&include=constraints,internal_ids"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert record["constraints"][0]["calculation_id"] == calc.id


def test_search_include_parameters_and_constraints_combined(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    _attach_parameter(
        db_session, calculation=calc, raw_key="Grid", raw_value="ultrafine"
    )
    _attach_constraint(
        db_session,
        calculation=calc,
        constraint_index=1,
        constraint_kind=ConstraintKind.bond,
        atom1_index=1,
        atom2_index=2,
    )
    body = client.get(
        SEARCH_URL
        + "?calculation_type=opt&include=parameters,constraints"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert len(record["parameters"]) == 1
    assert len(record["constraints"]) == 1


# ---------------------------------------------------------------------------
# Search × include=review
# ---------------------------------------------------------------------------


def test_search_include_review_returns_entries(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(
        SEARCH_URL + "?calculation_type=opt&include=review"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert len(record["review_history"]) == 1
    entry = record["review_history"][0]
    assert entry["status"] == "approved"
    # Default Phase D: review_id stripped.
    assert "review_id" not in entry


def test_search_include_review_returns_empty_list_when_no_row(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        SEARCH_URL + "?calculation_type=opt&include=review"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert record["review_history"] == []


def test_search_post_supports_include_review(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.approved,
    )
    body = client.post(
        SEARCH_URL,
        json={"calculation_type": "opt", "include": ["review"]},
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert len(record["review_history"]) == 1


def test_search_include_review_get_post_parity(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.approved,
    )
    body_get = client.get(
        SEARCH_URL + "?calculation_type=opt&include=review"
    ).json()
    body_post = client.post(
        SEARCH_URL,
        json={"calculation_type": "opt", "include": ["review"]},
    ).json()
    assert body_get["records"] == body_post["records"]
    assert body_get["pagination"] == body_post["pagination"]


def test_search_include_review_internal_ids_when_allowed(
    client, db_session, allow_internal_ids
):
    _, _, calc = _make_species_owned_calc(db_session)
    review = set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(
        SEARCH_URL
        + "?calculation_type=opt&include=review,internal_ids"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    entry = record["review_history"][0]
    assert entry["review_id"] == review.id


def test_search_include_results_and_review_combined(client, db_session):
    _, _, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.sp
    )
    attach_sp_result(
        db_session, calculation=calc, electronic_energy_hartree=-2.0
    )
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(
        SEARCH_URL + "?calculation_type=sp&include=results,review"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert record["results"]["kind"] == "sp"
    assert len(record["review_history"]) == 1


# ---------------------------------------------------------------------------
# Search × include=scan
# ---------------------------------------------------------------------------


def _make_scan_calc_with_data(db_session):
    _, entry, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.scan
    )
    db_session.add(
        CalculationScanResult(
            calculation_id=calc.id,
            dimension=1,
            is_relaxed=True,
            zero_energy_reference_hartree=-100.0,
        )
    )
    db_session.flush()
    db_session.add(
        CalculationScanCoordinate(
            calculation_id=calc.id,
            coordinate_index=1,
            coordinate_kind=ScanCoordinateKind.bond,
            atom1_index=1,
            atom2_index=2,
            step_count=2,
            step_size=0.1,
            start_value=0.8,
            end_value=1.0,
        )
    )
    db_session.add(
        CalculationScanPoint(
            calculation_id=calc.id,
            point_index=1,
            electronic_energy_hartree=-99.5,
        )
    )
    db_session.flush()
    return calc


def test_search_include_scan_returns_summary(client, db_session):
    calc = _make_scan_calc_with_data(db_session)
    body = client.get(
        SEARCH_URL + "?calculation_type=scan&include=scan"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    scan = record["scan"]
    assert scan is not None
    assert scan["dimension"] == 1
    assert scan["coordinate_count"] == 1
    assert scan["point_count"] == 1
    assert scan["min_electronic_energy_hartree"] == -99.5
    assert scan["max_electronic_energy_hartree"] == -99.5


def test_search_include_scan_returns_null_when_no_result_row(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.scan
    )
    body = client.get(
        SEARCH_URL + "?calculation_type=scan&include=scan"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert record["scan"] is None


def test_search_post_supports_include_scan(client, db_session):
    calc = _make_scan_calc_with_data(db_session)
    body = client.post(
        SEARCH_URL,
        json={"calculation_type": "scan", "include": ["scan"]},
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert record["scan"]["dimension"] == 1


def test_search_include_scan_get_post_parity(client, db_session):
    _calc = _make_scan_calc_with_data(db_session)
    body_get = client.get(
        SEARCH_URL + "?calculation_type=scan&include=scan"
    ).json()
    body_post = client.post(
        SEARCH_URL,
        json={"calculation_type": "scan", "include": ["scan"]},
    ).json()
    assert body_get["records"] == body_post["records"]
    assert body_get["pagination"] == body_post["pagination"]


def test_search_include_results_and_scan_combined(client, db_session):
    """``include=results,scan`` populates both blocks for a scan calc."""
    calc = _make_scan_calc_with_data(db_session)
    resp = client.get(
        SEARCH_URL + "?calculation_type=scan&include=results,scan"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert record["results"]["kind"] == "scan"
    assert record["scan"] is not None
    # The CalculationScanResultSummary block (under results) carries
    # only result-row fields; the include=scan block carries
    # coordinates + counts. Both shapes coexist without overlap.
    assert "coordinates" not in record["results"]["scan"]
    assert "coordinates" in record["scan"]


def test_search_include_scan_does_not_expose_point_arrays(client, db_session):
    """Defense-in-depth: search-record scan summary must NOT carry
    per-point arrays."""
    calc = _make_scan_calc_with_data(db_session)
    body = client.get(
        SEARCH_URL + "?calculation_type=scan&include=scan"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    scan = record["scan"]
    for forbidden in (
        "points",
        "scan_points",
        "point_coordinate_values",
        "atoms",
        "coords",
        "xyz_text",
    ):
        assert forbidden not in scan


# ---------------------------------------------------------------------------
# Search × include=irc
# ---------------------------------------------------------------------------


def _make_irc_calc_with_data(db_session):
    _, entry, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.irc
    )
    db_session.add(
        CalculationIRCResult(
            calculation_id=calc.id,
            direction=IRCDirection.both,
            has_forward=True,
            has_reverse=True,
            ts_point_index=0,
            point_count=3,
            zero_energy_reference_hartree=-100.0,
        )
    )
    db_session.flush()
    db_session.add(
        CalculationIRCPoint(
            calculation_id=calc.id, point_index=0, direction=None,
            is_ts=True, electronic_energy_hartree=-99.5,
            reaction_coordinate=0.0,
        )
    )
    db_session.add(
        CalculationIRCPoint(
            calculation_id=calc.id, point_index=1,
            direction=IRCDirection.forward,
            electronic_energy_hartree=-99.9,
            reaction_coordinate=1.0,
        )
    )
    db_session.add(
        CalculationIRCPoint(
            calculation_id=calc.id, point_index=2,
            direction=IRCDirection.reverse,
            electronic_energy_hartree=-100.1,
            reaction_coordinate=-1.0,
        )
    )
    db_session.flush()
    return calc


def test_search_include_irc_returns_summary(client, db_session):
    calc = _make_irc_calc_with_data(db_session)
    body = client.get(
        SEARCH_URL + "?calculation_type=irc&include=irc"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    irc = record["irc"]
    assert irc is not None
    assert irc["direction"] == "both"
    assert irc["forward_point_count"] == 1
    assert irc["reverse_point_count"] == 1
    assert irc["ts_point_count"] == 1
    assert irc["min_electronic_energy_hartree"] == -100.1
    assert irc["max_electronic_energy_hartree"] == -99.5
    assert irc["min_reaction_coordinate"] == -1.0
    assert irc["max_reaction_coordinate"] == 1.0


def test_search_include_irc_returns_null_when_no_result_row(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.irc
    )
    body = client.get(
        SEARCH_URL + "?calculation_type=irc&include=irc"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert record["irc"] is None


def test_search_post_supports_include_irc(client, db_session):
    calc = _make_irc_calc_with_data(db_session)
    body = client.post(
        SEARCH_URL,
        json={"calculation_type": "irc", "include": ["irc"]},
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert record["irc"]["direction"] == "both"


def test_search_include_irc_get_post_parity(client, db_session):
    _calc = _make_irc_calc_with_data(db_session)
    body_get = client.get(
        SEARCH_URL + "?calculation_type=irc&include=irc"
    ).json()
    body_post = client.post(
        SEARCH_URL,
        json={"calculation_type": "irc", "include": ["irc"]},
    ).json()
    assert body_get["records"] == body_post["records"]
    assert body_get["pagination"] == body_post["pagination"]


def test_search_include_results_and_irc_combined(client, db_session):
    calc = _make_irc_calc_with_data(db_session)
    resp = client.get(
        SEARCH_URL + "?calculation_type=irc&include=results,irc"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    # results = the per-type result-row summary; irc = the include
    # block with directional aggregates. Both shapes coexist.
    assert record["results"]["kind"] == "irc"
    assert record["irc"] is not None
    # Directional counts live only on the include block, not on
    # the results-side summary.
    assert "forward_point_count" not in record["results"]["irc"]
    assert "forward_point_count" in record["irc"]


def test_search_include_irc_does_not_expose_point_arrays(client, db_session):
    calc = _make_irc_calc_with_data(db_session)
    body = client.get(
        SEARCH_URL + "?calculation_type=irc&include=irc"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    irc = record["irc"]
    for forbidden in (
        "points",
        "irc_points",
        "atoms",
        "coords",
        "xyz_text",
        "reaction_coordinates",
    ):
        assert forbidden not in irc


# ---------------------------------------------------------------------------
# Search × include=path_search
# ---------------------------------------------------------------------------


def _make_path_search_calc_with_data(db_session):
    _, entry, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.path_search
    )
    db_session.add(
        CalculationPathSearchResult(
            calculation_id=calc.id,
            method=PathSearchMethod.neb,
            is_double_ended=True,
            converged=True,
            n_points=3,
            selected_ts_point_index=1,
            climbing_image_index=1,
            source_endpoint_count=2,
            zero_energy_reference_hartree=-100.0,
        )
    )
    db_session.flush()
    db_session.add(
        CalculationPathSearchPoint(
            calculation_id=calc.id, point_index=0,
            electronic_energy_hartree=-100.0, path_coordinate=0.0,
        )
    )
    db_session.add(
        CalculationPathSearchPoint(
            calculation_id=calc.id, point_index=1,
            electronic_energy_hartree=-99.2, path_coordinate=0.5,
            is_ts_guess=True, is_climbing_image=True,
        )
    )
    db_session.add(
        CalculationPathSearchPoint(
            calculation_id=calc.id, point_index=2,
            electronic_energy_hartree=-100.2, path_coordinate=1.0,
        )
    )
    db_session.flush()
    return calc


def test_search_include_path_search_returns_summary(client, db_session):
    calc = _make_path_search_calc_with_data(db_session)
    body = client.get(
        SEARCH_URL + "?calculation_type=path_search&include=path_search"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    ps = record["path_search"]
    assert ps is not None
    assert ps["method"] == "neb"
    assert ps["stored_point_count"] == 3
    assert ps["ts_guess_count"] == 1
    assert ps["climbing_image_count"] == 1
    assert ps["min_electronic_energy_hartree"] == -100.2
    assert ps["max_electronic_energy_hartree"] == -99.2
    assert ps["min_path_coordinate"] == 0.0
    assert ps["max_path_coordinate"] == 1.0


def test_search_include_path_search_returns_null_when_no_result_row(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.path_search
    )
    body = client.get(
        SEARCH_URL + "?calculation_type=path_search&include=path_search"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert record["path_search"] is None


def test_search_post_supports_include_path_search(client, db_session):
    calc = _make_path_search_calc_with_data(db_session)
    body = client.post(
        SEARCH_URL,
        json={
            "calculation_type": "path_search",
            "include": ["path_search"],
        },
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert record["path_search"]["method"] == "neb"


def test_search_include_path_search_get_post_parity(client, db_session):
    _calc = _make_path_search_calc_with_data(db_session)
    body_get = client.get(
        SEARCH_URL + "?calculation_type=path_search&include=path_search"
    ).json()
    body_post = client.post(
        SEARCH_URL,
        json={
            "calculation_type": "path_search",
            "include": ["path_search"],
        },
    ).json()
    assert body_get["records"] == body_post["records"]
    assert body_get["pagination"] == body_post["pagination"]


def test_search_include_results_and_path_search_combined(client, db_session):
    calc = _make_path_search_calc_with_data(db_session)
    resp = client.get(
        SEARCH_URL
        + "?calculation_type=path_search&include=results,path_search"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    # results = the per-type result-row summary; path_search = the
    # include block with point aggregates. Both shapes coexist.
    assert record["results"]["kind"] == "path_search"
    assert record["path_search"] is not None
    # Aggregate counts live only on the include block.
    assert "stored_point_count" not in record["results"]["path_search"]
    assert "stored_point_count" in record["path_search"]


def test_search_include_path_search_does_not_expose_point_arrays(
    client, db_session
):
    calc = _make_path_search_calc_with_data(db_session)
    body = client.get(
        SEARCH_URL + "?calculation_type=path_search&include=path_search"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    ps = record["path_search"]
    for forbidden in (
        "points",
        "path_search_points",
        "atoms",
        "coords",
        "xyz_text",
        "path_coordinates",
    ):
        assert forbidden not in ps


# ---------------------------------------------------------------------------
# Search × include=all
# ---------------------------------------------------------------------------


_ALL_EXPANSION_TOKENS = {
    "results",
    "dependencies",
    "artifacts",
    "input_geometries",
    "output_geometries",
    "geometry_validation",
    "scf_stability",
    "wavefunction_diagnostic",
    "spin_diagnostic",
    "parameters",
    "constraints",
    "review",
    "scan",
    "irc",
    "path_search",
}


def test_search_include_all_returns_200(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    resp = client.get(
        SEARCH_URL + "?calculation_type=opt&include=all"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body["request"]["include"]) == _ALL_EXPANSION_TOKENS
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    for record_key in (
        "results",
        "dependencies",
        "artifacts",
        "input_geometries",
        "output_geometries",
        "geometry_validation",
        "scf_stability",
        "wavefunction_diagnostic",
        "spin_diagnostic",
        "parameters",
        "constraints",
        "review_history",
        "scan",
        "irc",
        "path_search",
    ):
        assert record_key in record


def test_search_post_supports_include_all(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.post(
        SEARCH_URL,
        json={"calculation_type": "opt", "include": ["all"]},
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert "scan" in record
    assert set(body["request"]["include"]) == _ALL_EXPANSION_TOKENS


def test_search_include_all_get_post_parity(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    body_get = client.get(
        SEARCH_URL + "?calculation_type=opt&include=all"
    ).json()
    body_post = client.post(
        SEARCH_URL,
        json={"calculation_type": "opt", "include": ["all"]},
    ).json()
    assert body_get["records"] == body_post["records"]
    assert body_get["pagination"] == body_post["pagination"]


def test_search_include_all_default_hides_internal_ids(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        SEARCH_URL + "?calculation_type=opt&include=all"
    ).json()
    assert "internal_ids" not in body["request"]["include"]
    for record in body["records"]:
        assert "calculation_id" not in record["calculation"]


def test_search_include_all_with_internal_ids_when_allowed(
    client, db_session, allow_internal_ids
):
    _, _, calc = _make_species_owned_calc(db_session)
    body = client.get(
        SEARCH_URL + "?calculation_type=opt&include=all,internal_ids"
    ).json()
    assert "internal_ids" in body["request"]["include"]
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    assert record["calculation"]["calculation_id"] == calc.id


def test_search_include_all_does_not_expose_full_point_or_xyz_payloads(
    client, db_session
):
    """Defense-in-depth on the search path: ``include=all`` must not
    inline per-point arrays, artifact bodies, or XYZ coordinates."""
    # Use a scan calc with point + coordinate data so the loaders
    # have something concrete to project from.
    calc = _make_scan_calc_with_data(db_session)
    body = client.get(
        SEARCH_URL + "?calculation_type=scan&include=all"
    ).json()
    record = next(
        r for r in body["records"]
        if r["calculation"]["calculation_ref"] == calc.public_ref
    )
    forbidden_keys = {
        "points",
        "scan_points",
        "irc_points",
        "path_search_points",
        "point_coordinate_values",
        "atoms",
        "coords",
        "xyz_text",
        "body",
        "content",
        "data",
        "presigned_url",
        "download_url",
    }

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden_keys, (
                    f"include=all leaked forbidden key {k!r} in search record"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(record)
