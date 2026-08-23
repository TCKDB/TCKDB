"""API tests for the scientific transition-state read/search surface.

Covers:

- GET  /api/v1/scientific/transition-states/{transition_state_ref_or_id}
- GET  /api/v1/scientific/transition-state-entries/{transition_state_entry_ref_or_id}
- GET  /api/v1/scientific/transition-states/search
- POST /api/v1/scientific/transition-states/search
"""

from __future__ import annotations

from app.db.models.calculation import (
    CalculationDependency,
    CalculationFreqResult,
    CalculationOutputGeometry,
)
from app.db.models.common import (
    CalculationDependencyRole,
    CalculationGeometryRole,
    CalculationQuality,
    CalculationType,
    RecordReviewStatus,
    SubmissionRecordType,
    TransitionStateEntryStatus,
)
from app.db.models.software import Software, SoftwareRelease
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from tests.services.scientific_read._factories import (
    attach_artifact,
    attach_geometry_validation,
    attach_scf_stability,
    make_calculation,
    make_chem_reaction,
    make_geometry,
    make_lot,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    next_inchi_key,
    set_review,
)

# ---------------------------------------------------------------------------
# Local fixture helpers
# ---------------------------------------------------------------------------


def _make_reaction_with_ts(
    db_session,
    *,
    label: str | None = "ts1",
    note: str | None = None,
    n_entries: int = 1,
    statuses: list[TransitionStateEntryStatus] | None = None,
):
    """Build a Species×2 → ChemReaction → ReactionEntry → TS → TS-entry chain.

    Returns ``(reaction, reaction_entry, ts, list_of_entries)``.
    """
    if statuses is None:
        statuses = [TransitionStateEntryStatus.optimized] * n_entries
    assert len(statuses) == n_entries

    sp_a = make_species(db_session, inchi_key=next_inchi_key("TSA"))
    sp_b = make_species(db_session, inchi_key=next_inchi_key("TSB"))
    sp_c = make_species(db_session, inchi_key=next_inchi_key("TSC"))
    sp_d = make_species(db_session, inchi_key=next_inchi_key("TSD"))
    se_a = make_species_entry(db_session, sp_a)
    se_b = make_species_entry(db_session, sp_b)
    se_c = make_species_entry(db_session, sp_c)
    se_d = make_species_entry(db_session, sp_d)
    chem = make_chem_reaction(
        db_session, reactants=[sp_a, sp_b], products=[sp_c, sp_d]
    )
    rxe = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[se_a, se_b],
        product_entries=[se_c, se_d],
    )
    ts = make_transition_state(
        db_session, reaction_entry=rxe, label=label, note=note
    )
    entries = [
        make_transition_state_entry(
            db_session,
            transition_state=ts,
            charge=0,
            multiplicity=2,
            status=status,
            unmapped_smiles="[CH3].[OH2]",
        )
        for status in statuses
    ]
    return chem, rxe, ts, entries


def _attach_calc(
    db_session,
    *,
    tse,
    calc_type=CalculationType.opt,
    quality=CalculationQuality.raw,
    lot=None,
    software_release=None,
    workflow_tool_release=None,
):
    calc = make_calculation(
        db_session,
        type=calc_type,
        transition_state_entry_id=tse.id,
        lot_id=lot.id if lot is not None else None,
    )
    if quality != CalculationQuality.raw:
        calc.quality = quality
    if software_release is not None:
        calc.software_release_id = software_release.id
    if workflow_tool_release is not None:
        calc.workflow_tool_release_id = workflow_tool_release.id
    db_session.flush()
    return calc


def _make_software_release(db_session, *, name="gaussian", version="g16.a03"):
    # Get-or-create so a single test may attach several calcs that share a
    # software/release without tripping ``uq_software_name`` or
    # ``uq_software_release_software_id`` (the latter is NULLS NOT DISTINCT
    # over ``(software_id, version, revision, build)``).
    sw = (
        db_session.query(Software).filter(Software.name == name).one_or_none()
    )
    if sw is None:
        sw = Software(name=name)
        db_session.add(sw)
        db_session.flush()
    sr = (
        db_session.query(SoftwareRelease)
        .filter(
            SoftwareRelease.software_id == sw.id,
            SoftwareRelease.version == version,
        )
        .one_or_none()
    )
    if sr is None:
        sr = SoftwareRelease(software_id=sw.id, version=version)
        db_session.add(sr)
        db_session.flush()
    return sw, sr


def _make_workflow_tool_release(db_session, *, name="arc", version="1.2.3"):
    wt = (
        db_session.query(WorkflowTool)
        .filter(WorkflowTool.name == name)
        .one_or_none()
    )
    if wt is None:
        wt = WorkflowTool(name=name)
        db_session.add(wt)
        db_session.flush()
    wtr = (
        db_session.query(WorkflowToolRelease)
        .filter(
            WorkflowToolRelease.workflow_tool_id == wt.id,
            WorkflowToolRelease.version == version,
        )
        .one_or_none()
    )
    if wtr is None:
        wtr = WorkflowToolRelease(workflow_tool_id=wt.id, version=version)
        db_session.add(wtr)
        db_session.flush()
    return wt, wtr


def _ts_detail_url(handle: str, **params) -> str:
    base = f"/api/v1/scientific/transition-states/{handle}"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


def _tse_detail_url(handle: str, **params) -> str:
    base = f"/api/v1/scientific/transition-state-entries/{handle}"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


def _search_url(**params) -> str:
    base = "/api/v1/scientific/transition-states/search"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


# ===========================================================================
# TS detail endpoint
# ===========================================================================


def test_ts_detail_by_ref_returns_record(client, db_session):
    _, rxe, ts, entries = _make_reaction_with_ts(db_session)
    resp = client.get(_ts_detail_url(ts.public_ref))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["record"]["transition_state"]["transition_state_ref"] == ts.public_ref


def test_ts_detail_by_integer_id_works(client, db_session):
    _, _, ts, _ = _make_reaction_with_ts(db_session)
    resp = client.get(_ts_detail_url(str(ts.id)))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["record"]["transition_state"]["transition_state_ref"] == ts.public_ref


def test_ts_detail_unknown_handle_returns_404(client, db_session):
    resp = client.get(_ts_detail_url("ts_doesnotexist00000"))
    assert resp.status_code == 404
    assert "transition_state not found" in resp.text


def test_ts_detail_wrong_prefix_returns_422(client, db_session):
    resp = client.get(_ts_detail_url("spe_abcdef0123456789"))
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_ts_detail_malformed_handle_returns_422(client, db_session):
    resp = client.get(_ts_detail_url("not-a-handle"))
    assert resp.status_code == 422
    assert "invalid_handle" in resp.text


def test_ts_detail_default_response_shape(client, db_session):
    _, rxe, ts, entries = _make_reaction_with_ts(db_session)
    body = client.get(_ts_detail_url(ts.public_ref)).json()
    record = body["record"]
    assert "transition_state" in record
    assert "reaction" in record
    assert "entries_summary" in record
    assert "evidence_summary" in record
    assert "available_sections" in record
    # Heavy includes omitted by default — Pydantic emits these as null
    # rather than absent (the schema uses ``... | None = None``).
    assert record["entries"] is None
    assert record["calculations"] is None
    assert record["geometries"] is None
    assert record["review_history"] is None


def test_ts_detail_review_badge_present(client, db_session):
    _, _, ts, _ = _make_reaction_with_ts(db_session)
    body = client.get(_ts_detail_url(ts.public_ref)).json()
    ts_block = body["record"]["transition_state"]
    assert ts_block["review"]["status"] == "not_reviewed"
    assert body["review_summary"]["not_reviewed"] == 1
    assert body["review_summary"]["total"] == 1


def test_ts_detail_reaction_context_present(client, db_session):
    chem, rxe, ts, _ = _make_reaction_with_ts(db_session)
    body = client.get(_ts_detail_url(ts.public_ref)).json()
    reaction = body["record"]["reaction"]
    assert reaction["reaction_ref"] == chem.public_ref
    assert reaction["reaction_entry_ref"] == rxe.public_ref
    assert reaction["reversible"] is True
    assert reaction["equation"]
    assert "<=>" in reaction["equation"]


def test_ts_detail_entries_summary_counts(client, db_session):
    _, _, ts, _ = _make_reaction_with_ts(
        db_session,
        n_entries=3,
        statuses=[
            TransitionStateEntryStatus.optimized,
            TransitionStateEntryStatus.validated,
            TransitionStateEntryStatus.optimized,
        ],
    )
    body = client.get(_ts_detail_url(ts.public_ref)).json()
    summary = body["record"]["entries_summary"]
    assert summary["total"] == 3
    assert summary["by_status"]["optimized"] == 2
    assert summary["by_status"]["validated"] == 1


def test_ts_detail_evidence_summary_counts(client, db_session):
    """Concept scope: coverage counts, not booleans (single-entry shape).

    Rewritten from the ``has_*`` form when the TS-concept evidence block
    moved to counts. This is the shape every TS in the live corpus has
    today — exactly one entry — so it pins the no-regression case:
    ``count == 1`` where the retired boolean said ``true``, ``0`` where
    it said ``false``.
    """
    _, _, ts, entries = _make_reaction_with_ts(db_session)
    lot = make_lot(db_session)
    _attach_calc(db_session, tse=entries[0], calc_type=CalculationType.opt, lot=lot)
    _attach_calc(db_session, tse=entries[0], calc_type=CalculationType.freq, lot=lot)
    body = client.get(_ts_detail_url(ts.public_ref)).json()
    ev = body["record"]["evidence_summary"]
    assert ev["entry_count"] == 1
    assert ev["calculation_count"] == 2
    coverage = ev["evidence_coverage"]
    assert coverage["opt"] == 1
    assert coverage["freq"] == 1
    assert coverage["sp"] == 0
    assert coverage["irc"] == 0
    # The retired booleans are gone from this surface entirely — a client
    # reading ``has_opt`` here must be updated, not silently served a
    # stale key.
    assert "has_opt" not in ev
    assert "has_freq" not in ev


def test_ts_detail_evidence_coverage_partial_across_entries(client, db_session):
    """The case the boolean could not express, and the reason for the PR.

    Two entries under one TS; one has an ``sp`` calculation and one does
    not. The retired ``has_sp`` OR-ed across entries and reported
    ``true``, which a reader takes as "this transition state has a
    single point". Coverage reports ``1`` of ``2``.
    """
    _, _, ts, entries = _make_reaction_with_ts(
        db_session,
        n_entries=2,
        statuses=[
            TransitionStateEntryStatus.optimized,
            TransitionStateEntryStatus.optimized,
        ],
    )
    lot = make_lot(db_session)
    _attach_calc(db_session, tse=entries[0], calc_type=CalculationType.opt, lot=lot)
    _attach_calc(db_session, tse=entries[0], calc_type=CalculationType.sp, lot=lot)
    _attach_calc(db_session, tse=entries[1], calc_type=CalculationType.opt, lot=lot)

    body = client.get(_ts_detail_url(ts.public_ref)).json()
    ev = body["record"]["evidence_summary"]
    assert ev["entry_count"] == 2
    assert ev["calculation_count"] == 3
    coverage = ev["evidence_coverage"]
    # Both entries were optimised; only one has a single point.
    assert coverage["opt"] == 2
    assert coverage["sp"] == 1
    # ``count > 0`` is what the old boolean said. It is still true here,
    # and still says almost nothing on its own — which is the point.
    assert coverage["sp"] > 0
    assert coverage["sp"] < ev["entry_count"]


def test_ts_detail_evidence_coverage_all_entries_covered(client, db_session):
    """Complete coverage reads as ``count == entry_count``."""
    _, _, ts, entries = _make_reaction_with_ts(
        db_session,
        n_entries=3,
        statuses=[TransitionStateEntryStatus.optimized] * 3,
    )
    lot = make_lot(db_session)
    for entry in entries:
        _attach_calc(db_session, tse=entry, calc_type=CalculationType.freq, lot=lot)

    ev = client.get(_ts_detail_url(ts.public_ref)).json()["record"][
        "evidence_summary"
    ]
    assert ev["entry_count"] == 3
    assert ev["evidence_coverage"]["freq"] == 3
    assert ev["evidence_coverage"]["opt"] == 0


def test_ts_detail_evidence_coverage_no_entries_covered(client, db_session):
    """No evidence anywhere reads as ``0`` — as strong as the old ``false``."""
    _, _, ts, _entries = _make_reaction_with_ts(
        db_session,
        n_entries=2,
        statuses=[TransitionStateEntryStatus.optimized] * 2,
    )
    ev = client.get(_ts_detail_url(ts.public_ref)).json()["record"][
        "evidence_summary"
    ]
    assert ev["entry_count"] == 2
    assert ev["calculation_count"] == 0
    assert ev["evidence_coverage"] == {
        "opt": 0,
        "freq": 0,
        "sp": 0,
        "irc": 0,
        "path_search": 0,
        "geometry_validation": 0,
        "scf_stability": 0,
    }


def test_ts_detail_evidence_coverage_counts_entries_not_calculations(
    client, db_session
):
    """An entry with several calculations of one kind counts **once**.

    Getting this backwards lets a coverage value exceed ``entry_count``,
    which is nonsense on its face: "four of two entries have freq".
    """
    _, _, ts, entries = _make_reaction_with_ts(
        db_session,
        n_entries=2,
        statuses=[TransitionStateEntryStatus.optimized] * 2,
    )
    lot = make_lot(db_session)
    for _ in range(3):
        _attach_calc(
            db_session, tse=entries[0], calc_type=CalculationType.freq, lot=lot
        )
    _attach_calc(db_session, tse=entries[1], calc_type=CalculationType.freq, lot=lot)

    ev = client.get(_ts_detail_url(ts.public_ref)).json()["record"][
        "evidence_summary"
    ]
    assert ev["entry_count"] == 2
    # Four freq calculations across two entries.
    assert ev["calculation_count"] == 4
    assert ev["evidence_coverage"]["freq"] == 2
    assert ev["evidence_coverage"]["freq"] <= ev["entry_count"]


def test_ts_detail_evidence_coverage_joined_kinds_count_entries(
    client, db_session
):
    """``geometry_validation`` / ``scf_stability`` count entries too.

    These two are reached through a join rather than a calculation type,
    so they are the ones most likely to drift back to counting rows.
    """
    _, _, ts, entries = _make_reaction_with_ts(
        db_session,
        n_entries=2,
        statuses=[TransitionStateEntryStatus.optimized] * 2,
    )
    lot = make_lot(db_session)
    first = _attach_calc(
        db_session, tse=entries[0], calc_type=CalculationType.opt, lot=lot
    )
    second = _attach_calc(
        db_session, tse=entries[0], calc_type=CalculationType.freq, lot=lot
    )
    # Both validated rows sit under the SAME entry — coverage is 1 of 2.
    attach_geometry_validation(db_session, calculation=first)
    attach_geometry_validation(db_session, calculation=second)
    attach_scf_stability(db_session, calculation=first)
    # The second entry carries a calculation with neither joined row.
    _attach_calc(db_session, tse=entries[1], calc_type=CalculationType.opt, lot=lot)

    ev = client.get(_ts_detail_url(ts.public_ref)).json()["record"][
        "evidence_summary"
    ]
    assert ev["entry_count"] == 2
    assert ev["evidence_coverage"]["geometry_validation"] == 1
    assert ev["evidence_coverage"]["scf_stability"] == 1


def test_tse_detail_evidence_summary_keeps_booleans(client, db_session):
    """Entry scope keeps ``has_*``.

    One entry means one set of calculations, so the boolean cannot pool
    a covered entry with an uncovered one — there is nothing for a
    ``true`` to hide behind. The coverage block belongs to the concept
    surface and is deliberately absent here.
    """
    _, _, _ts, entries = _make_reaction_with_ts(
        db_session,
        n_entries=2,
        statuses=[TransitionStateEntryStatus.optimized] * 2,
    )
    lot = make_lot(db_session)
    _attach_calc(db_session, tse=entries[0], calc_type=CalculationType.opt, lot=lot)
    _attach_calc(db_session, tse=entries[0], calc_type=CalculationType.sp, lot=lot)
    _attach_calc(db_session, tse=entries[1], calc_type=CalculationType.opt, lot=lot)

    covered = client.get(_tse_detail_url(entries[0].public_ref)).json()["record"][
        "evidence_summary"
    ]
    assert covered["calculation_count"] == 2
    assert covered["has_opt"] is True
    assert covered["has_sp"] is True
    assert covered["has_freq"] is False
    assert "evidence_coverage" not in covered

    # The sibling entry has no single point, and says so — where the
    # concept-scope OR reported ``has_sp: true`` for the whole TS.
    uncovered = client.get(_tse_detail_url(entries[1].public_ref)).json()["record"][
        "evidence_summary"
    ]
    assert uncovered["calculation_count"] == 1
    assert uncovered["has_opt"] is True
    assert uncovered["has_sp"] is False


def test_ts_detail_available_sections_present(client, db_session):
    _, _, ts, entries = _make_reaction_with_ts(db_session)
    body = client.get(_ts_detail_url(ts.public_ref)).json()
    sections = body["record"]["available_sections"]
    assert sections["has_entries"] is True
    assert sections["has_calculations"] is False  # no calcs attached
    assert sections["has_geometries"] is False


def test_ts_detail_include_entries(client, db_session):
    _, _, ts, entries = _make_reaction_with_ts(db_session, n_entries=2)
    body = client.get(_ts_detail_url(ts.public_ref, include="entries")).json()
    assert body["record"]["entries"] is not None
    assert len(body["record"]["entries"]) == 2
    refs = {e["transition_state_entry"]["transition_state_entry_ref"] for e in body["record"]["entries"]}
    assert refs == {entries[0].public_ref, entries[1].public_ref}


def test_ts_detail_include_calculations(client, db_session):
    _, _, ts, entries = _make_reaction_with_ts(db_session)
    lot = make_lot(db_session)
    calc = _attach_calc(db_session, tse=entries[0], calc_type=CalculationType.opt, lot=lot)
    body = client.get(
        _ts_detail_url(ts.public_ref, include="calculations")
    ).json()
    calcs = body["record"]["calculations"]
    assert calcs is not None
    assert len(calcs) == 1
    assert calcs[0]["calculation_ref"] == calc.public_ref
    assert calcs[0]["type"] == "opt"
    assert calcs[0]["level_of_theory"]["method"] == "wb97xd"


def test_ts_detail_include_geometries(client, db_session):
    _, _, ts, entries = _make_reaction_with_ts(db_session)
    calc = _attach_calc(db_session, tse=entries[0], calc_type=CalculationType.opt)
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
    body = client.get(
        _ts_detail_url(ts.public_ref, include="geometries")
    ).json()
    geoms = body["record"]["geometries"]
    assert geoms is not None
    assert len(geoms) == 1
    assert geoms[0]["geometry_ref"] == geom.public_ref
    assert geoms[0]["natoms"] == 4
    # Defense-in-depth: no XYZ inlining.
    assert "xyz_text" not in geoms[0]
    assert "atoms" not in geoms[0]


def test_ts_detail_include_review(client, db_session):
    _, _, ts, _ = _make_reaction_with_ts(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transition_state,
        record_id=ts.id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(
        _ts_detail_url(ts.public_ref, include="review")
    ).json()
    rh = body["record"]["review_history"]
    assert rh is not None
    assert len(rh) == 1
    assert rh[0]["status"] == "approved"


def test_ts_detail_include_all_expands_all_public_tokens(client, db_session):
    _, _, ts, entries = _make_reaction_with_ts(db_session)
    body = client.get(_ts_detail_url(ts.public_ref, include="all")).json()
    inc = body["request"]["include"]
    assert "entries" in inc
    assert "calculations" in inc
    assert "geometries" in inc
    assert "review" in inc
    assert "internal_ids" not in inc


def test_ts_detail_include_all_does_not_restore_internal_ids(
    client, db_session
):
    _, _, ts, _ = _make_reaction_with_ts(db_session)
    body = client.get(_ts_detail_url(ts.public_ref, include="all")).json()
    ts_block = body["record"]["transition_state"]
    assert "transition_state_id" not in ts_block
    assert ts_block["transition_state_ref"] == ts.public_ref


def test_ts_detail_internal_ids_restored_when_policy_allows(
    client, db_session, allow_internal_ids
):
    _, _, ts, _ = _make_reaction_with_ts(db_session)
    body = client.get(
        _ts_detail_url(ts.public_ref, include="internal_ids")
    ).json()
    assert body["record"]["transition_state"]["transition_state_id"] == ts.id


def test_ts_detail_internal_ids_silently_dropped_when_disallowed(
    client, db_session
):
    _, _, ts, _ = _make_reaction_with_ts(db_session)
    body = client.get(
        _ts_detail_url(ts.public_ref, include="internal_ids")
    ).json()
    assert body["request"]["include"] == []
    assert "transition_state_id" not in body["record"]["transition_state"]


def test_ts_detail_unknown_include_token_returns_422(client, db_session):
    _, _, ts, _ = _make_reaction_with_ts(db_session)
    resp = client.get(_ts_detail_url(ts.public_ref, include="banana"))
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


def test_ts_detail_rejected_record_still_returned_with_badge(
    client, db_session
):
    _, _, ts, _ = _make_reaction_with_ts(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transition_state,
        record_id=ts.id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(_ts_detail_url(ts.public_ref)).json()
    assert body["record"]["transition_state"]["review"]["status"] == "rejected"


def test_ts_detail_no_mol_blob_in_default_or_include_all(client, db_session):
    _, _, ts, _ = _make_reaction_with_ts(
        db_session, n_entries=1,
    )
    body = client.get(
        _ts_detail_url(ts.public_ref, include="all")
    ).json()
    forbidden_keys = {
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
                assert k not in forbidden_keys, (
                    f"TS detail leaked forbidden key {k!r}"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)


# ===========================================================================
# TS-entry detail endpoint
# ===========================================================================


def test_tse_detail_by_ref_returns_record(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    tse = entries[0]
    resp = client.get(_tse_detail_url(tse.public_ref))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["record"]["transition_state_entry"]["transition_state_entry_ref"] == tse.public_ref


def test_tse_detail_by_integer_id_works(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    tse = entries[0]
    resp = client.get(_tse_detail_url(str(tse.id)))
    assert resp.status_code == 200, resp.text


def test_tse_detail_unknown_handle_returns_404(client, db_session):
    resp = client.get(_tse_detail_url("tse_doesnotexist00000"))
    assert resp.status_code == 404


def test_tse_detail_wrong_prefix_returns_422(client, db_session):
    resp = client.get(_tse_detail_url("ts_abcdef0123456789"))
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_tse_detail_malformed_handle_returns_422(client, db_session):
    resp = client.get(_tse_detail_url("not-a-handle"))
    assert resp.status_code == 422


def test_tse_detail_default_response_shape(client, db_session):
    _, _, ts, entries = _make_reaction_with_ts(db_session)
    body = client.get(_tse_detail_url(entries[0].public_ref)).json()
    record = body["record"]
    assert "transition_state_entry" in record
    assert "transition_state" in record
    assert "reaction" in record
    assert "evidence_summary" in record
    assert "available_sections" in record
    assert record["transition_state"]["transition_state_ref"] == ts.public_ref


def test_tse_detail_does_not_expose_mol_blob(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    body = client.get(_tse_detail_url(entries[0].public_ref)).json()
    tse_block = body["record"]["transition_state_entry"]
    assert "mol" not in tse_block
    # unmapped_smiles is the public-readable representation and IS allowed.
    assert tse_block["unmapped_smiles"] == "[CH3].[OH2]"


def test_tse_detail_include_calculations(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    lot = make_lot(db_session)
    _attach_calc(db_session, tse=entries[0], calc_type=CalculationType.opt, lot=lot)
    _attach_calc(db_session, tse=entries[0], calc_type=CalculationType.freq, lot=lot)
    body = client.get(
        _tse_detail_url(entries[0].public_ref, include="calculations")
    ).json()
    calcs = body["record"]["calculations"]
    assert calcs is not None
    assert len(calcs) == 2
    assert {c["type"] for c in calcs} == {"opt", "freq"}


def test_tse_detail_includes_structured_irc_validation_evidence(
    client, db_session
):
    from app.db.models.transition_state import TransitionStateValidationEvidence

    _, _, _, entries = _make_reaction_with_ts(db_session)
    tse = entries[0]
    calc = _attach_calc(db_session, tse=tse, calc_type=CalculationType.irc)
    # The saddle-point geometry those participant mappings index into. A
    # mapping that does not name one is refused by the database, because an
    # atom index counted into an unnamed ordering does not identify an atom.
    ts_geometry = make_geometry(db_session, natoms=2)
    db_session.add(
        TransitionStateValidationEvidence(
            transition_state_entry_id=tse.id,
            kind="irc",
            passed=True,
            rationale="forward and reverse endpoints match participants",
            reconstruction_calculation_id=calc.id,
            reactant_participant_mapping={"reactant:1": [1, 2]},
            product_participant_mapping={"product:1": [1, 2]},
            transition_state_geometry_id=ts_geometry.id,
        )
    )
    db_session.flush()

    body = client.get(
        _tse_detail_url(tse.public_ref, include="validation_evidence")
    ).json()
    record = body["record"]
    assert record["available_sections"]["has_validation_evidence"] is True
    evidence = record["validation_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["kind"] == "irc"
    assert evidence[0]["reconstruction_calculation_ref"] == calc.public_ref
    assert evidence[0]["reactant_participant_mapping"] == {"reactant:1": [1, 2]}
    # The mappings' indices count into a named geometry, and the reader is
    # told which one rather than having to pick among the entry's geometries.
    assert evidence[0]["transition_state_geometry_ref"] == ts_geometry.public_ref
    # NMD evidence is not a TCKDB concept; no such field survives.
    assert "mode_index" not in evidence[0]
    assert "displacement_artifact_sha256" not in evidence[0]
    # One typed descriptor states validation status without any include token.
    assert record["validation"] == {"irc": "present"}


def test_tse_detail_validation_descriptor_reports_absent_and_failed(
    client, db_session
):
    """The descriptor is always present, so 'no IRC' is stated, not inferred."""
    from app.db.models.transition_state import TransitionStateValidationEvidence

    _, _, _, entries = _make_reaction_with_ts(db_session)
    tse = entries[0]
    body = client.get(_tse_detail_url(tse.public_ref)).json()
    assert body["record"]["validation"] == {"irc": "absent"}

    calc = _attach_calc(db_session, tse=tse, calc_type=CalculationType.irc)
    db_session.add(
        TransitionStateValidationEvidence(
            transition_state_entry_id=tse.id,
            kind="irc",
            passed=False,
            rationale="IRC ran downhill to an unintended fragment pair",
            reconstruction_calculation_id=calc.id,
        )
    )
    db_session.flush()
    body = client.get(_tse_detail_url(tse.public_ref)).json()
    assert body["record"]["validation"] == {"irc": "failed"}


def test_tse_detail_include_geometries(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    calc = _attach_calc(db_session, tse=entries[0])
    geom = make_geometry(db_session, natoms=5)
    db_session.add(
        CalculationOutputGeometry(
            calculation_id=calc.id,
            output_order=1,
            geometry_id=geom.id,
            role=CalculationGeometryRole.final,
        )
    )
    db_session.flush()
    body = client.get(
        _tse_detail_url(entries[0].public_ref, include="geometries")
    ).json()
    geoms = body["record"]["geometries"]
    assert geoms is not None
    assert geoms[0]["geometry_ref"] == geom.public_ref
    assert geoms[0]["natoms"] == 5


def test_tse_detail_include_review(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_id=entries[0].id,
        status=RecordReviewStatus.under_review,
    )
    body = client.get(
        _tse_detail_url(entries[0].public_ref, include="review")
    ).json()
    rh = body["record"]["review_history"]
    assert rh is not None
    assert rh[0]["status"] == "under_review"


def test_tse_detail_include_all_does_not_restore_internal_ids(
    client, db_session
):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    body = client.get(
        _tse_detail_url(entries[0].public_ref, include="all")
    ).json()
    inc = body["request"]["include"]
    assert "calculations" in inc
    assert "geometries" in inc
    assert "review" in inc
    assert "internal_ids" not in inc
    assert "transition_state_entry_id" not in body["record"]["transition_state_entry"]


def test_tse_detail_internal_ids_restored_when_policy_allows(
    client, db_session, allow_internal_ids
):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    body = client.get(
        _tse_detail_url(entries[0].public_ref, include="internal_ids")
    ).json()
    assert body["record"]["transition_state_entry"]["transition_state_entry_id"] == entries[0].id


def test_tse_detail_no_forbidden_payload_leak(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    calc = _attach_calc(db_session, tse=entries[0])
    geom = make_geometry(db_session, natoms=3)
    db_session.add(
        CalculationOutputGeometry(
            calculation_id=calc.id,
            output_order=1,
            geometry_id=geom.id,
            role=CalculationGeometryRole.final,
        )
    )
    db_session.flush()
    body = client.get(
        _tse_detail_url(entries[0].public_ref, include="all")
    ).json()
    forbidden = {
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
                    f"TS-entry detail leaked forbidden key {k!r}"
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


def test_search_by_reaction_entry_ref(client, db_session):
    _, rxe, _, entries = _make_reaction_with_ts(db_session)
    body = client.get(
        _search_url(reaction_entry_ref=rxe.public_ref)
    ).json()
    assert body["pagination"]["total"] == 1
    rec = body["records"][0]
    assert rec["transition_state_entry"]["transition_state_entry_ref"] == entries[0].public_ref
    assert rec["reaction"]["reaction_entry_ref"] == rxe.public_ref


def test_search_by_reaction_ref(client, db_session):
    chem, _, _, entries = _make_reaction_with_ts(db_session)
    body = client.get(_search_url(reaction_ref=chem.public_ref)).json()
    assert body["pagination"]["total"] == 1
    assert body["records"][0]["transition_state_entry"]["transition_state_entry_ref"] == entries[0].public_ref


def test_search_by_transition_state_ref(client, db_session):
    _, _, ts, entries = _make_reaction_with_ts(db_session, n_entries=2)
    body = client.get(
        _search_url(transition_state_ref=ts.public_ref)
    ).json()
    assert body["pagination"]["total"] == 2


def test_search_by_transition_state_entry_ref(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(db_session, n_entries=2)
    body = client.get(
        _search_url(transition_state_entry_ref=entries[0].public_ref)
    ).json()
    assert body["pagination"]["total"] == 1
    assert body["records"][0]["transition_state_entry"]["transition_state_entry_ref"] == entries[0].public_ref


def test_search_by_status(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(
        db_session,
        n_entries=2,
        statuses=[
            TransitionStateEntryStatus.optimized,
            TransitionStateEntryStatus.validated,
        ],
    )
    body = client.get(_search_url(status="validated")).json()
    assert body["pagination"]["total"] == 1
    assert body["records"][0]["transition_state_entry"]["status"] == "validated"


def test_search_by_charge_and_multiplicity(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    # Match: charge=0 multiplicity=2 (factory defaults).
    body = client.get(_search_url(charge=0, multiplicity=2)).json()
    assert body["pagination"]["total"] == 1
    # Miss.
    body = client.get(_search_url(charge=1, multiplicity=2)).json()
    assert body["pagination"]["total"] == 0


def test_search_by_has_calculations(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    _attach_calc(db_session, tse=entries_a[0], calc_type=CalculationType.opt)
    body = client.get(_search_url(has_calculations="true")).json()
    refs = {r["transition_state_entry"]["transition_state_entry_ref"] for r in body["records"]}
    assert entries_a[0].public_ref in refs
    assert entries_b[0].public_ref not in refs


def test_search_by_has_opt(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    _attach_calc(db_session, tse=entries_a[0], calc_type=CalculationType.opt)
    _attach_calc(db_session, tse=entries_b[0], calc_type=CalculationType.sp)
    body = client.get(_search_url(has_opt="true")).json()
    refs = {r["transition_state_entry"]["transition_state_entry_ref"] for r in body["records"]}
    assert entries_a[0].public_ref in refs
    assert entries_b[0].public_ref not in refs


def test_search_by_has_freq(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    _attach_calc(db_session, tse=entries_a[0], calc_type=CalculationType.freq)
    body = client.get(_search_url(has_freq="true")).json()
    refs = {r["transition_state_entry"]["transition_state_entry_ref"] for r in body["records"]}
    assert refs == {entries_a[0].public_ref}


def test_search_by_has_sp(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    _attach_calc(db_session, tse=entries_a[0], calc_type=CalculationType.sp)
    body = client.get(_search_url(has_sp="true")).json()
    refs = {r["transition_state_entry"]["transition_state_entry_ref"] for r in body["records"]}
    assert refs == {entries_a[0].public_ref}


def test_search_by_has_irc(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    _attach_calc(db_session, tse=entries_a[0], calc_type=CalculationType.irc)
    body = client.get(_search_url(has_irc="true")).json()
    refs = {r["transition_state_entry"]["transition_state_entry_ref"] for r in body["records"]}
    assert refs == {entries_a[0].public_ref}


def test_search_by_has_path_search(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    _attach_calc(
        db_session, tse=entries_a[0], calc_type=CalculationType.path_search
    )
    body = client.get(_search_url(has_path_search="true")).json()
    refs = {r["transition_state_entry"]["transition_state_entry_ref"] for r in body["records"]}
    assert refs == {entries_a[0].public_ref}


def test_search_by_has_geometry_validation(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    calc = _attach_calc(db_session, tse=entries_a[0])
    attach_geometry_validation(db_session, calculation=calc)
    body = client.get(_search_url(has_geometry_validation="true")).json()
    refs = {r["transition_state_entry"]["transition_state_entry_ref"] for r in body["records"]}
    assert refs == {entries_a[0].public_ref}


def test_search_by_has_scf_stability(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    calc = _attach_calc(db_session, tse=entries_a[0])
    attach_scf_stability(db_session, calculation=calc)
    body = client.get(_search_url(has_scf_stability="true")).json()
    refs = {r["transition_state_entry"]["transition_state_entry_ref"] for r in body["records"]}
    assert refs == {entries_a[0].public_ref}


def test_search_by_method_and_basis(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    lot_a = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    lot_b = make_lot(db_session, method="b3lyp", basis="6-31g")
    _attach_calc(db_session, tse=entries_a[0], lot=lot_a)
    _attach_calc(db_session, tse=entries_b[0], lot=lot_b)
    body = client.get(_search_url(method="wb97xd", basis="def2tzvp")).json()
    refs = {r["transition_state_entry"]["transition_state_entry_ref"] for r in body["records"]}
    assert refs == {entries_a[0].public_ref}


def test_search_by_software_and_version(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    _, sr_a = _make_software_release(
        db_session, name="gaussian", version="g16.a03"
    )
    _, sr_b = _make_software_release(
        db_session, name="orca", version="5.0.4"
    )
    _attach_calc(db_session, tse=entries_a[0], software_release=sr_a)
    _attach_calc(db_session, tse=entries_b[0], software_release=sr_b)
    body = client.get(
        _search_url(software="gaussian", software_version="g16.a03")
    ).json()
    refs = {r["transition_state_entry"]["transition_state_entry_ref"] for r in body["records"]}
    assert refs == {entries_a[0].public_ref}


def test_search_by_workflow_tool_and_version(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    _, wtr_a = _make_workflow_tool_release(
        db_session, name="arc", version="1.2.3"
    )
    _, wtr_b = _make_workflow_tool_release(
        db_session, name="qcelemental", version="0.27.0"
    )
    _attach_calc(db_session, tse=entries_a[0], workflow_tool_release=wtr_a)
    _attach_calc(db_session, tse=entries_b[0], workflow_tool_release=wtr_b)
    body = client.get(
        _search_url(workflow_tool="arc", workflow_tool_version="1.2.3")
    ).json()
    refs = {r["transition_state_entry"]["transition_state_entry_ref"] for r in body["records"]}
    assert refs == {entries_a[0].public_ref}


def test_search_default_hides_rejected(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, ts_b, entries_b = _make_reaction_with_ts(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_id=entries_b[0].id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(_search_url(status="optimized")).json()
    refs = {r["transition_state_entry"]["transition_state_entry_ref"] for r in body["records"]}
    assert entries_a[0].public_ref in refs
    assert entries_b[0].public_ref not in refs


def test_search_include_rejected_surfaces_them(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_id=entries_b[0].id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(
        _search_url(status="optimized", include_rejected="true")
    ).json()
    refs = [r["transition_state_entry"]["transition_state_entry_ref"] for r in body["records"]]
    assert entries_a[0].public_ref in refs
    assert entries_b[0].public_ref in refs
    # Rejected sorts last (review_rank desc).
    assert refs[-1] == entries_b[0].public_ref


def test_search_pagination_envelope(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(
        db_session,
        n_entries=4,
        statuses=[TransitionStateEntryStatus.optimized] * 4,
    )
    body = client.get(
        _search_url(status="optimized", limit=2, offset=0)
    ).json()
    p = body["pagination"]
    assert p["limit"] == 2
    assert p["offset"] == 0
    assert p["returned"] == 2
    assert p["total"] == 4


def test_search_deterministic_ordering_review_then_created(
    client, db_session
):
    """Approved records win over not_reviewed regardless of creation order."""
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    # entries_a older; entries_b newer. Approve the older one — it
    # should still come first because review_rank dominates.
    set_review(
        db_session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_id=entries_a[0].id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(_search_url(status="optimized")).json()
    refs = [r["transition_state_entry"]["transition_state_entry_ref"] for r in body["records"]]
    assert refs[0] == entries_a[0].public_ref


def test_search_client_sort_rejected(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    resp = client.get(_search_url(status="optimized", sort="created_at"))
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_search_get_post_parity(client, db_session):
    chem, rxe, _, entries = _make_reaction_with_ts(db_session)
    get_body = client.get(
        _search_url(reaction_entry_ref=rxe.public_ref)
    ).json()
    post_body = client.post(
        _search_url(), json={"reaction_entry_ref": rxe.public_ref}
    ).json()
    assert get_body["pagination"] == post_body["pagination"]
    assert get_body["records"] == post_body["records"]


def test_search_include_calculations_on_records(client, db_session):
    _, rxe, _, entries = _make_reaction_with_ts(db_session)
    lot = make_lot(db_session)
    _attach_calc(db_session, tse=entries[0], calc_type=CalculationType.opt, lot=lot)
    body = client.get(
        _search_url(reaction_entry_ref=rxe.public_ref, include="calculations")
    ).json()
    rec = body["records"][0]
    assert rec["calculations"] is not None
    assert len(rec["calculations"]) == 1
    assert rec["calculations"][0]["type"] == "opt"


def test_search_include_geometries_on_records(client, db_session):
    _, rxe, _, entries = _make_reaction_with_ts(db_session)
    calc = _attach_calc(db_session, tse=entries[0])
    geom = make_geometry(db_session, natoms=3)
    db_session.add(
        CalculationOutputGeometry(
            calculation_id=calc.id,
            output_order=1,
            geometry_id=geom.id,
            role=CalculationGeometryRole.final,
        )
    )
    db_session.flush()
    body = client.get(
        _search_url(reaction_entry_ref=rxe.public_ref, include="geometries")
    ).json()
    rec = body["records"][0]
    assert rec["geometries"] is not None
    assert rec["geometries"][0]["geometry_ref"] == geom.public_ref


def test_search_include_review_on_records(client, db_session):
    _, rxe, _, entries = _make_reaction_with_ts(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_id=entries[0].id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(
        _search_url(reaction_entry_ref=rxe.public_ref, include="review")
    ).json()
    rec = body["records"][0]
    assert rec["review_history"] is not None
    assert rec["review_history"][0]["status"] == "approved"


def test_search_include_all_on_records(client, db_session):
    _, rxe, _, entries = _make_reaction_with_ts(db_session)
    _attach_calc(db_session, tse=entries[0])
    body = client.get(
        _search_url(reaction_entry_ref=rxe.public_ref, include="all")
    ).json()
    inc = body["request"]["include"]
    assert "calculations" in inc
    assert "geometries" in inc
    assert "review" in inc
    assert "internal_ids" not in inc
    # ``entries`` is silently dropped on the search surface (each record
    # IS an entry); kept legal so a generic client passes the same
    # include set everywhere.
    assert "entries" not in inc


def test_search_internal_ids_restored_when_policy_allows(
    client, db_session, allow_internal_ids
):
    _, rxe, _, entries = _make_reaction_with_ts(db_session)
    body = client.get(
        _search_url(
            reaction_entry_ref=rxe.public_ref, include="internal_ids"
        )
    ).json()
    assert body["records"][0]["transition_state_entry"]["transition_state_entry_id"] == entries[0].id


def test_search_internal_ids_silently_dropped_when_disallowed(
    client, db_session
):
    _, rxe, _, entries = _make_reaction_with_ts(db_session)
    body = client.get(
        _search_url(
            reaction_entry_ref=rxe.public_ref, include="internal_ids"
        )
    ).json()
    assert body["request"]["include"] == []
    assert "transition_state_entry_id" not in body["records"][0]["transition_state_entry"]


def test_search_unknown_ref_short_circuits_empty(client, db_session):
    # Well-formed but unknown ref → empty result set, not 404.
    body = client.get(_search_url(reaction_entry_ref="rxe_doesnotexist00")).json()
    assert body["pagination"]["total"] == 0
    assert body["records"] == []


def test_search_wrong_prefix_ref_returns_422(client, db_session):
    resp = client.get(_search_url(reaction_entry_ref="ts_abcdef0123456789"))
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_search_post_body_only_no_query_params(client, db_session):
    _, rxe, _, _ = _make_reaction_with_ts(db_session)
    resp = client.post(
        "/api/v1/scientific/transition-states/search?limit=5",
        json={"reaction_entry_ref": rxe.public_ref},
    )
    assert resp.status_code == 422
    assert "post_search_fields_must_be_in_body" in resp.text


def test_search_no_forbidden_payload_keys(client, db_session):
    _, rxe, _, entries = _make_reaction_with_ts(db_session)
    calc = _attach_calc(db_session, tse=entries[0])
    geom = make_geometry(db_session, natoms=3)
    db_session.add(
        CalculationOutputGeometry(
            calculation_id=calc.id,
            output_order=1,
            geometry_id=geom.id,
            role=CalculationGeometryRole.final,
        )
    )
    db_session.flush()
    body = client.get(
        _search_url(reaction_entry_ref=rxe.public_ref, include="all")
    ).json()
    forbidden = {
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
                    f"TS search leaked forbidden key {k!r}"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body)


# ---------------------------------------------------------------------------
# Regression: explicit False boolean filters must count as meaningful
# (was: 422 missing_filter — fixed by treating None vs False distinctly)
# ---------------------------------------------------------------------------


def test_search_has_calculations_false_does_not_return_missing_filter(
    client, db_session
):
    """``has_calculations=false`` is a meaningful filter (TS entries
    without any calculation evidence) and must not 422."""
    resp = client.get(_search_url(has_calculations="false"))
    assert resp.status_code == 200, resp.text


def test_search_has_opt_false_does_not_return_missing_filter(client, db_session):
    resp = client.get(_search_url(has_opt="false"))
    assert resp.status_code == 200, resp.text


def test_search_has_freq_false_does_not_return_missing_filter(
    client, db_session
):
    resp = client.get(_search_url(has_freq="false"))
    assert resp.status_code == 200, resp.text


def test_search_has_sp_false_does_not_return_missing_filter(client, db_session):
    resp = client.get(_search_url(has_sp="false"))
    assert resp.status_code == 200, resp.text


def test_search_has_irc_false_does_not_return_missing_filter(client, db_session):
    resp = client.get(_search_url(has_irc="false"))
    assert resp.status_code == 200, resp.text


def test_search_has_path_search_false_does_not_return_missing_filter(
    client, db_session
):
    resp = client.get(_search_url(has_path_search="false"))
    assert resp.status_code == 200, resp.text


def test_search_has_geometry_validation_false_does_not_return_missing_filter(
    client, db_session
):
    resp = client.get(_search_url(has_geometry_validation="false"))
    assert resp.status_code == 200, resp.text


def test_search_has_scf_stability_false_does_not_return_missing_filter(
    client, db_session
):
    resp = client.get(_search_url(has_scf_stability="false"))
    assert resp.status_code == 200, resp.text


def test_search_has_opt_false_narrows_to_entries_without_opt(client, db_session):
    """End-to-end: ``has_opt=false`` returns TS entries that have no
    opt-typed calculation, and excludes entries that do."""
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    _attach_calc(db_session, tse=entries_a[0], calc_type=CalculationType.opt)
    # entries_b[0] gets no calculations.

    true_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in client.get(_search_url(has_opt="true")).json()["records"]
    }
    false_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in client.get(_search_url(has_opt="false")).json()["records"]
    }
    assert entries_a[0].public_ref in true_refs
    assert entries_a[0].public_ref not in false_refs
    assert entries_b[0].public_ref not in true_refs
    assert entries_b[0].public_ref in false_refs


# ===========================================================================
# TS-entry detail trust fragment (include=trust)
# ===========================================================================
#
# Trust is wired to the *standalone* TS-entry detail surface only. The
# parent-TS detail (embedded entries) and the search surface do not expose
# it, and the default TS-entry response is byte-identical to its pre-trust
# shape. See ``backend/docs/specs/trust_read_api_current.md``.


def _attach_ts_opt(
    db_session,
    *,
    tse,
    with_geom_validation=True,
    with_artifact=True,
    with_workflow_tool=True,
):
    """Attach a fully-provenanced TS-optimization calc to a TS entry."""
    lot = make_lot(db_session)
    _, sr = _make_software_release(db_session)
    wtr = None
    if with_workflow_tool:
        _, wtr = _make_workflow_tool_release(db_session)
    calc = _attach_calc(
        db_session,
        tse=tse,
        calc_type=CalculationType.opt,
        lot=lot,
        software_release=sr,
        workflow_tool_release=wtr,
    )
    if with_geom_validation:
        attach_geometry_validation(db_session, calculation=calc)
    if with_artifact:
        attach_artifact(db_session, calculation=calc)
    return calc


def _attach_ts_freq(
    db_session,
    *,
    tse,
    opt_calc=None,
    n_imag=1,
    imag_freq_cm1=-550.0,
):
    """Attach a frequency calc (with an n_imag-bearing result) to a TS entry."""
    lot = make_lot(db_session)
    _, sr = _make_software_release(db_session)
    calc = _attach_calc(
        db_session,
        tse=tse,
        calc_type=CalculationType.freq,
        lot=lot,
        software_release=sr,
    )
    db_session.add(
        CalculationFreqResult(
            calculation_id=calc.id,
            n_imag=n_imag,
            imag_freq_cm1=imag_freq_cm1,
            zpe_hartree=0.05,
        )
    )
    if opt_calc is not None:
        db_session.add(
            CalculationDependency(
                parent_calculation_id=opt_calc.id,
                child_calculation_id=calc.id,
                dependency_role=CalculationDependencyRole.freq_on,
            )
        )
    db_session.flush()
    return calc


def test_tse_detail_default_response_omits_trust(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    body = client.get(_tse_detail_url(entries[0].public_ref)).json()
    assert "trust" not in body["record"]


def test_tse_detail_include_trust_returns_fragment(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    body = client.get(
        _tse_detail_url(entries[0].public_ref, include="trust")
    ).json()
    trust = body["record"]["trust"]
    assert trust["review_status"] == "not_reviewed"
    assert trust["is_certified"] is False
    evidence = trust["evidence"]
    assert evidence["record_type"] == "transition_state_entry"
    assert evidence["rubric"] == "computed_transition_state_v2"
    assert evidence["rubric_version"] == 2
    # Evidence object carries the deterministic check breakdown.
    for key in (
        "passed_checks",
        "missing_checks",
        "warning_checks",
        "not_applicable_checks",
        "passed_count",
        "possible_count",
        "evidence_completeness",
    ):
        assert key in evidence


def test_tse_detail_include_trust_disabled_llm_precheck(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    body = client.get(
        _tse_detail_url(entries[0].public_ref, include="trust")
    ).json()
    assert body["record"]["trust"]["llm_precheck"] == {
        "enabled": False,
        "label": "not_run",
        "summary": None,
    }


def test_tse_detail_include_trust_uses_review_badge(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_id=entries[0].id,
        status=RecordReviewStatus.approved,
    )
    body = client.get(
        _tse_detail_url(entries[0].public_ref, include="trust")
    ).json()
    assert body["record"]["trust"]["review_status"] == "approved"


def test_tse_detail_include_all_excludes_trust(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    body = client.get(
        _tse_detail_url(entries[0].public_ref, include="all")
    ).json()
    assert "trust" not in body["request"]["include"]
    assert "trust" not in body["record"]


def test_tse_detail_unknown_include_still_422(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    resp = client.get(_tse_detail_url(entries[0].public_ref, include="bogus"))
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


def test_tse_detail_trust_preserves_internal_id_policy(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    body = client.get(
        _tse_detail_url(entries[0].public_ref, include="trust")
    ).json()
    assert "record_id" not in body["record"]["trust"]["evidence"]


def test_tse_detail_trust_internal_id_policy_allows_record_id(
    client, db_session, allow_internal_ids
):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    body = client.get(
        _tse_detail_url(entries[0].public_ref, include="trust,internal_ids")
    ).json()
    assert body["record"]["trust"]["evidence"]["record_id"] == entries[0].id


def test_tse_detail_trust_sparse_entry_reports_missing_checks(
    client, db_session
):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    # No calculations attached → supporting-evidence checks are missing.
    body = client.get(
        _tse_detail_url(entries[0].public_ref, include="trust")
    ).json()
    evidence = body["record"]["trust"]["evidence"]
    assert "supporting_calculations_present" in evidence["missing_checks"]
    assert evidence["passed_count"] < evidence["possible_count"]


def test_tse_detail_trust_optimized_n_imag_one_passes_freq_check(
    client, db_session
):
    _, _, _, entries = _make_reaction_with_ts(
        db_session, statuses=[TransitionStateEntryStatus.optimized]
    )
    opt = _attach_ts_opt(db_session, tse=entries[0])
    _attach_ts_freq(db_session, tse=entries[0], opt_calc=opt, n_imag=1)
    body = client.get(
        _tse_detail_url(entries[0].public_ref, include="trust")
    ).json()
    trust = body["record"]["trust"]
    assert trust["trust_status"] != "hard_failed"
    passed = set(trust["evidence"]["passed_checks"])
    assert "reaction_coordinate_designated_for_ts" in passed
    assert "imaginary_frequency_count_recorded" in passed


def test_tse_detail_trust_validated_n_imag_one_passes_freq_check(
    client, db_session
):
    _, _, _, entries = _make_reaction_with_ts(
        db_session, statuses=[TransitionStateEntryStatus.validated]
    )
    opt = _attach_ts_opt(db_session, tse=entries[0])
    _attach_ts_freq(db_session, tse=entries[0], opt_calc=opt, n_imag=1)
    trust = client.get(
        _tse_detail_url(entries[0].public_ref, include="trust")
    ).json()["record"]["trust"]
    assert trust["trust_status"] != "hard_failed"
    assert (
        "reaction_coordinate_designated_for_ts"
        in trust["evidence"]["passed_checks"]
    )


def test_tse_detail_trust_optimized_n_imag_zero_hard_fails(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(
        db_session, statuses=[TransitionStateEntryStatus.optimized]
    )
    opt = _attach_ts_opt(db_session, tse=entries[0])
    _attach_ts_freq(
        db_session, tse=entries[0], opt_calc=opt, n_imag=0, imag_freq_cm1=None
    )
    trust = client.get(
        _tse_detail_url(entries[0].public_ref, include="trust")
    ).json()["record"]["trust"]
    assert trust["trust_status"] == "hard_failed"


def test_tse_detail_trust_optimized_n_imag_multiple_hard_fails(
    client, db_session
):
    _, _, _, entries = _make_reaction_with_ts(
        db_session, statuses=[TransitionStateEntryStatus.optimized]
    )
    opt = _attach_ts_opt(db_session, tse=entries[0])
    _attach_ts_freq(
        db_session,
        tse=entries[0],
        opt_calc=opt,
        n_imag=3,
        imag_freq_cm1=-100.0,
    )
    trust = client.get(
        _tse_detail_url(entries[0].public_ref, include="trust")
    ).json()["record"]["trust"]
    assert trust["trust_status"] == "hard_failed"


def test_tse_detail_trust_guess_n_imag_not_one_warns_not_hard_fail(
    client, db_session
):
    _, _, _, entries = _make_reaction_with_ts(
        db_session, statuses=[TransitionStateEntryStatus.guess]
    )
    opt = _attach_ts_opt(db_session, tse=entries[0])
    _attach_ts_freq(
        db_session, tse=entries[0], opt_calc=opt, n_imag=0, imag_freq_cm1=None
    )
    trust = client.get(
        _tse_detail_url(entries[0].public_ref, include="trust")
    ).json()["record"]["trust"]
    # A guess-stage TS with the wrong imaginary-mode count is missing the
    # required check but is NOT collapsed to hard_failed.
    assert trust["trust_status"] != "hard_failed"
    assert (
        "reaction_coordinate_designated_for_ts"
        in trust["evidence"]["missing_checks"]
    )


def test_tse_detail_trust_irc_and_path_search_increase_completeness(
    client, db_session
):
    # Baseline: opt + freq only.
    _, _, _, base_entries = _make_reaction_with_ts(
        db_session, statuses=[TransitionStateEntryStatus.optimized]
    )
    opt_b = _attach_ts_opt(db_session, tse=base_entries[0])
    _attach_ts_freq(db_session, tse=base_entries[0], opt_calc=opt_b, n_imag=1)
    base = client.get(
        _tse_detail_url(base_entries[0].public_ref, include="trust")
    ).json()["record"]["trust"]["evidence"]

    # Richer: same + an IRC calc + a path-search calc.
    _, _, _, rich_entries = _make_reaction_with_ts(
        db_session, statuses=[TransitionStateEntryStatus.optimized]
    )
    opt_r = _attach_ts_opt(db_session, tse=rich_entries[0])
    _attach_ts_freq(db_session, tse=rich_entries[0], opt_calc=opt_r, n_imag=1)
    _attach_calc(
        db_session,
        tse=rich_entries[0],
        calc_type=CalculationType.irc,
        lot=make_lot(db_session),
    )
    _attach_calc(
        db_session,
        tse=rich_entries[0],
        calc_type=CalculationType.path_search,
        lot=make_lot(db_session),
    )
    rich = client.get(
        _tse_detail_url(rich_entries[0].public_ref, include="trust")
    ).json()["record"]["trust"]["evidence"]

    rich_passed = set(rich["passed_checks"])
    assert "irc_evidence_present" in rich_passed
    assert "path_search_evidence_present" in rich_passed
    assert rich["evidence_completeness"] >= base["evidence_completeness"]


def test_tse_detail_trust_does_not_mutate_record(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    tse = entries[0]
    before = (tse.charge, tse.multiplicity, tse.status, tse.unmapped_smiles)
    resp = client.get(_tse_detail_url(tse.public_ref, include="trust"))
    assert resp.status_code == 200, resp.text
    db_session.refresh(tse)
    after = (tse.charge, tse.multiplicity, tse.status, tse.unmapped_smiles)
    assert after == before


def test_tse_detail_trust_path_uses_loaded_evaluator(
    client, db_session, monkeypatch
):
    """The read path must call the loaded evaluator, not the session/id
    wrapper (which would issue its own query / hard-fail on missing rows).
    """
    from app.services.scientific_read import (
        transition_states as ts_service,
    )
    from app.services.trust import evaluate_loaded_transition_state_entry

    _, _, _, entries = _make_reaction_with_ts(db_session)
    calls = 0

    def counted_loaded_evaluator(ts_entry):
        nonlocal calls
        calls += 1
        return evaluate_loaded_transition_state_entry(ts_entry)

    monkeypatch.setattr(
        ts_service,
        "evaluate_loaded_transition_state_entry",
        counted_loaded_evaluator,
    )
    resp = client.get(_tse_detail_url(entries[0].public_ref, include="trust"))
    assert resp.status_code == 200, resp.text
    assert calls == 1


def test_ts_concept_detail_embedded_entries_omit_trust(client, db_session):
    """Trust is not exposed through the parent-TS detail surface, even
    with ``include=entries`` (``trust`` is not a legal token there)."""
    _, _, ts, _ = _make_reaction_with_ts(db_session)
    body = client.get(_ts_detail_url(ts.public_ref, include="entries")).json()
    for entry in body["record"]["entries"]:
        assert entry.get("trust") is None


def test_ts_concept_detail_rejects_trust_token(client, db_session):
    _, _, ts, _ = _make_reaction_with_ts(db_session)
    resp = client.get(_ts_detail_url(ts.public_ref, include="trust"))
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


def test_ts_search_accepts_trust_token_and_keeps_it_out_of_all(
    client, db_session
):
    """Search accepts ``trust``; ``include=all`` still does not reach it.

    This test used to assert the opposite — a 422 — and the reversal is the
    decision rather than a relaxation of it. The search response carried a
    ``trust`` field at the record root on every record while refusing the
    only token that could fill it, so the field was permanently ``null``:
    a slot advertised and never fillable.

    What survives unchanged is the half that costs. The evaluator walks a
    23-entry eager-load chain here — the largest of the five search
    surfaces, and the only four-hop one — so ``trust`` stays
    internal-tokenized and a caller has to name it. An ``include=all`` that
    bought that chain per page would break nothing until it met a real page
    size on the hosted instance.
    """
    _, _, _, entries = _make_reaction_with_ts(db_session)
    _attach_calc(db_session, tse=entries[0], calc_type=CalculationType.opt)

    asked = client.get(_search_url(has_opt="true", include="trust"))
    assert asked.status_code == 200, asked.text
    body = asked.json()
    assert body["request"]["include"] == ["trust"]
    assert body["records"]
    for record in body["records"]:
        assert record["trust"] is not None
        assert record["trust"]["trust_status"]

    everything = client.get(_search_url(has_opt="true", include="all")).json()
    assert "trust" not in everything["request"]["include"]
    for record in everything["records"]:
        assert "trust" not in record

    default = client.get(_search_url(has_opt="true")).json()
    for record in default["records"]:
        assert "trust" not in record, (
            "an unrequested trust key is the defect this reversal removed"
        )
