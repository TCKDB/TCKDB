"""API tests for GET /api/v1/scientific/reaction-entries/{id}/full."""

from __future__ import annotations

from app.db.models.common import (
    RecordReviewStatus,
    ScientificOriginKind,
    SubmissionRecordType,
)
from tests.services.scientific_read._factories import (
    make_chem_reaction,
    make_kinetics,
    make_reaction_entry,
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)


def _entry(db_session):
    rs = make_species(db_session, smiles="A", inchi_key=next_inchi_key("FAPI1"))
    ps = make_species(db_session, smiles="B", inchi_key=next_inchi_key("FAPI2"))
    chem = make_chem_reaction(db_session, reactants=[rs], products=[ps])
    return make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[make_species_entry(db_session, rs)],
        product_entries=[make_species_entry(db_session, ps)],
    )


def test_returns_200_for_valid_reaction_entry_id(client, db_session):
    entry = _entry(db_session)
    resp = client.get(f"/api/v1/scientific/reaction-entries/{entry.id}/full")
    assert resp.status_code == 200
    body = resp.json()
    # Phase D: reaction_entry.id is hidden; identity surfaces via the ref.
    assert body["reaction_entry"]["reaction_entry_ref"] == entry.public_ref
    # Default: species + kinetics + transition_states present.
    assert body["species"] is not None
    assert body["kinetics"] == []
    assert body["transition_states"] == []


def test_returns_404_for_missing_reaction_entry_id(client, db_session):
    resp = client.get("/api/v1/scientific/reaction-entries/999999/full")
    assert resp.status_code == 404


def test_include_all_populates_every_top_level_section(client, db_session):
    entry = _entry(db_session)
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full?include=all"
    )
    body = resp.json()
    # Empty arrays for empty included sections (per Phase 2.1 policy).
    for key in (
        "species",
        "kinetics",
        "transition_states",
        "calculations",
        "path_search",
        "irc",
        "scans",
        "conformers",
        "artifacts",
    ):
        assert key in body
        assert body[key] is not None  # included sections are present


def test_default_omits_non_default_sections(client, db_session):
    entry = _entry(db_session)
    resp = client.get(f"/api/v1/scientific/reaction-entries/{entry.id}/full")
    body = resp.json()
    # Default include set: species, kinetics, transition_states only.
    assert body["calculations"] is None
    assert body["path_search"] is None
    assert body["artifacts"] is None


def test_non_ts_backed_kinetics_no_fabricated_ts_links(client, db_session):
    entry = _entry(db_session)
    make_kinetics(
        db_session,
        reaction_entry=entry,
        scientific_origin=ScientificOriginKind.experimental,
    )

    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full?include=kinetics,transition_states"
    )
    body = resp.json()
    assert len(body["kinetics"]) == 1
    p = body["kinetics"][0]["provenance"]
    # Phase D: ref siblings are null for non-TS-backed records.
    assert p["transition_state_entry_ref"] is None
    assert body["transition_states"] == []  # not fabricated


def test_include_review_full_adds_audit_array(client, db_session):
    entry = _entry(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.reaction_entry,
        record_id=entry.id,
        status=RecordReviewStatus.approved,
    )
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full?include_review=full"
    )
    body = resp.json()
    assert body["review_records"] is not None
    # Phase D: ReviewRecordEntry.record_id is an internal PK with no
    # ref sibling, so it's hidden in the default response. Verify the
    # audit array shows the reaction_entry record_type entry.
    assert any(
        r["record_type"] == "reaction_entry" for r in body["review_records"]
    )


def test_default_review_summary_omits_audit_array(client, db_session):
    entry = _entry(db_session)
    resp = client.get(f"/api/v1/scientific/reaction-entries/{entry.id}/full")
    assert resp.json()["review_records"] is None


def test_rejects_client_sort(client, db_session):
    entry = _entry(db_session)
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full?sort=anything"
    )
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_unknown_include_token_rejected(client, db_session):
    entry = _entry(db_session)
    resp = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full?include=banana"
    )
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


# ---------------------------------------------------------------------------
# TS section linkage to the scientific transition-state read surface
# ---------------------------------------------------------------------------


def _entry_with_ts(
    db_session, *, ts_status=None, with_opt=False, with_freq=False
):
    """Build a reaction entry with one TS + one TS entry attached.

    Returns ``(reaction_entry, transition_state, ts_entry, calc_or_none)``.
    """
    from app.db.models.common import (
        CalculationType,
        TransitionStateEntryStatus,
    )
    from tests.services.scientific_read._factories import (
        make_calculation,
        make_transition_state,
        make_transition_state_entry,
    )

    entry = _entry(db_session)
    ts = make_transition_state(
        db_session, reaction_entry=entry, label="ts1"
    )
    tse = make_transition_state_entry(
        db_session,
        transition_state=ts,
        charge=0,
        multiplicity=2,
        status=ts_status or TransitionStateEntryStatus.optimized,
    )
    calc = None
    if with_opt:
        calc = make_calculation(
            db_session,
            type=CalculationType.opt,
            transition_state_entry_id=tse.id,
        )
    if with_freq:
        make_calculation(
            db_session,
            type=CalculationType.freq,
            transition_state_entry_id=tse.id,
        )
    return entry, ts, tse, calc


def test_full_ts_block_includes_transition_state_ref(client, db_session):
    entry, ts, tse, _ = _entry_with_ts(db_session)
    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full"
        "?include=transition_states"
    ).json()
    assert body["transition_states"]
    ts_block = body["transition_states"][0]
    assert ts_block["transition_state_ref"] == ts.public_ref
    assert ts_block["transition_state_entry_ref"] == tse.public_ref


def test_full_ts_block_includes_status_field(client, db_session):
    from app.db.models.common import TransitionStateEntryStatus

    entry, _, _, _ = _entry_with_ts(
        db_session, ts_status=TransitionStateEntryStatus.validated
    )
    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full"
        "?include=transition_states"
    ).json()
    assert body["transition_states"][0]["status"] == "validated"


def test_full_ts_block_includes_evidence_summary(client, db_session):
    entry, _, _, _ = _entry_with_ts(
        db_session, with_opt=True, with_freq=True
    )
    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full"
        "?include=transition_states"
    ).json()
    ev = body["transition_states"][0]["evidence_summary"]
    assert ev["calculation_count"] == 2
    assert ev["has_opt"] is True
    assert ev["has_freq"] is True
    assert ev["has_sp"] is False


def test_full_ts_refs_resolve_to_ts_detail_endpoints(client, db_session):
    """The refs surfaced under /full must navigate to the new TS surface."""
    entry, ts, tse, _ = _entry_with_ts(db_session, with_opt=True)
    full_body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full"
        "?include=transition_states"
    ).json()
    ts_block = full_body["transition_states"][0]

    # Follow transition_state_ref to the TS concept detail.
    ts_ref = ts_block["transition_state_ref"]
    detail = client.get(
        f"/api/v1/scientific/transition-states/{ts_ref}"
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["record"]["transition_state"][
        "transition_state_ref"
    ] == ts.public_ref

    # Follow transition_state_entry_ref to the TS-entry detail.
    tse_ref = ts_block["transition_state_entry_ref"]
    entry_detail = client.get(
        f"/api/v1/scientific/transition-state-entries/{tse_ref}"
    )
    assert entry_detail.status_code == 200, entry_detail.text
    assert entry_detail.json()["record"]["transition_state_entry"][
        "transition_state_entry_ref"
    ] == tse.public_ref


def test_full_ts_evidence_summary_matches_tse_detail(client, db_session):
    """The evidence_summary embedded in /full must equal the one on the
    standalone TS-entry detail endpoint for the same entry."""
    entry, _, tse, _ = _entry_with_ts(
        db_session, with_opt=True, with_freq=True
    )
    full = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full"
        "?include=transition_states"
    ).json()
    full_evidence = full["transition_states"][0]["evidence_summary"]

    detail = client.get(
        f"/api/v1/scientific/transition-state-entries/{tse.public_ref}"
    ).json()
    detail_evidence = detail["record"]["evidence_summary"]
    assert full_evidence == detail_evidence


def test_full_ts_block_hides_internal_ids_by_default(client, db_session):
    entry, _, _, _ = _entry_with_ts(db_session)
    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full"
        "?include=transition_states"
    ).json()
    ts_block = body["transition_states"][0]
    # Refs always present.
    assert "transition_state_ref" in ts_block
    assert "transition_state_entry_ref" in ts_block
    # Phase D default: integer ids stripped.
    assert "transition_state_id" not in ts_block
    assert "transition_state_entry_id" not in ts_block


def test_full_ts_block_restores_internal_ids_under_policy(
    client, db_session, allow_internal_ids
):
    entry, ts, tse, _ = _entry_with_ts(db_session)
    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full"
        "?include=transition_states,internal_ids"
    ).json()
    ts_block = body["transition_states"][0]
    assert ts_block["transition_state_id"] == ts.id
    assert ts_block["transition_state_entry_id"] == tse.id


def test_full_ts_block_does_not_leak_forbidden_payload_keys(
    client, db_session
):
    """Defense-in-depth: never inline mol blobs, XYZ, atom rows, or
    artifact bodies under the TS section of /full."""
    entry, _, _, _ = _entry_with_ts(
        db_session, with_opt=True, with_freq=True
    )
    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full"
        "?include=transition_states"
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
                    f"reaction-full TS block leaked forbidden key {k!r}"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body["transition_states"])


# ---------------------------------------------------------------------------
# Path-section alignment with scientific calculation path endpoints
# ---------------------------------------------------------------------------


def _entry_with_ts_calc(db_session, *, calc_type):
    """Build a reaction entry with a TS entry + one calc of *calc_type*."""
    from app.db.models.common import TransitionStateEntryStatus
    from tests.services.scientific_read._factories import (
        make_calculation,
        make_transition_state,
        make_transition_state_entry,
    )

    entry = _entry(db_session)
    ts = make_transition_state(
        db_session, reaction_entry=entry, label="ts1"
    )
    tse = make_transition_state_entry(
        db_session,
        transition_state=ts,
        charge=0,
        multiplicity=2,
        status=TransitionStateEntryStatus.optimized,
    )
    calc = make_calculation(
        db_session, type=calc_type, transition_state_entry_id=tse.id
    )
    return entry, ts, tse, calc


def _entry_with_ts_and_calcs(db_session, *, calc_types):
    """Build one reaction entry + TS entry + one calc per ``calc_type``.

    Returned as ``(entry, tse, {calc_type: calc, ...})``. Used by tests
    that need scan + irc + path-search side-by-side on the same parent
    reaction — keeping them under one reaction avoids hitting the
    ChemReaction public-ref fallback (which leans on ``id(obj)`` when
    ``stoichiometry_hash`` is unset and can collide across rapid
    successive inserts).
    """
    from app.db.models.common import TransitionStateEntryStatus
    from tests.services.scientific_read._factories import (
        make_calculation,
        make_transition_state,
        make_transition_state_entry,
    )

    entry = _entry(db_session)
    ts = make_transition_state(
        db_session, reaction_entry=entry, label="ts1"
    )
    tse = make_transition_state_entry(
        db_session,
        transition_state=ts,
        charge=0,
        multiplicity=2,
        status=TransitionStateEntryStatus.optimized,
    )
    calcs = {
        ct: make_calculation(
            db_session, type=ct, transition_state_entry_id=tse.id
        )
        for ct in calc_types
    }
    return entry, tse, calcs


def _attach_scan_result(db_session, calc, *, dimension=1, n_points=3):
    from app.db.models.calculation import (
        CalculationScanCoordinate,
        CalculationScanPoint,
        CalculationScanPointCoordinateValue,
        CalculationScanResult,
    )
    from app.db.models.common import CoordinateUnit, ScanCoordinateKind

    db_session.add(
        CalculationScanResult(
            calculation_id=calc.id,
            dimension=dimension,
            is_relaxed=True,
            zero_energy_reference_hartree=-100.0,
            note="full-test scan",
        )
    )
    db_session.add(
        CalculationScanCoordinate(
            calculation_id=calc.id,
            coordinate_index=1,
            coordinate_kind=ScanCoordinateKind.bond,
            atom1_index=1,
            atom2_index=2,
            step_count=n_points,
            step_size=0.1,
            start_value=0.8,
            end_value=0.8 + 0.1 * (n_points - 1),
            value_unit=CoordinateUnit.angstrom,
        )
    )
    for i in range(1, n_points + 1):
        db_session.add(
            CalculationScanPoint(
                calculation_id=calc.id,
                point_index=i,
                electronic_energy_hartree=-99.0 - i * 0.1,
                relative_energy_kj_mol=float(i) * 5.0,
            )
        )
        db_session.add(
            CalculationScanPointCoordinateValue(
                calculation_id=calc.id,
                point_index=i,
                coordinate_index=1,
                coordinate_value=0.8 + 0.1 * (i - 1),
                value_unit=CoordinateUnit.angstrom,
            )
        )
    db_session.flush()


def _attach_irc_result(db_session, calc, *, n_points=5):
    from app.db.models.calculation import (
        CalculationIRCPoint,
        CalculationIRCResult,
    )
    from app.db.models.common import IRCDirection

    db_session.add(
        CalculationIRCResult(
            calculation_id=calc.id,
            direction=IRCDirection.both,
            has_forward=True,
            has_reverse=True,
            ts_point_index=0,
            point_count=n_points,
            zero_energy_reference_hartree=-100.0,
        )
    )
    for i in range(n_points):
        if i == 0:
            direction = None
            is_ts = True
        elif i <= n_points // 2:
            direction = IRCDirection.forward
            is_ts = False
        else:
            direction = IRCDirection.reverse
            is_ts = False
        db_session.add(
            CalculationIRCPoint(
                calculation_id=calc.id,
                point_index=i,
                direction=direction,
                is_ts=is_ts,
                reaction_coordinate=float(i) * 0.25,
                electronic_energy_hartree=-99.5 - i * 0.1,
            )
        )
    db_session.flush()


def _attach_path_search_result(db_session, calc, *, n_points=3):
    from app.db.models.calculation import (
        CalculationPathSearchPoint,
        CalculationPathSearchResult,
    )
    from app.db.models.common import PathSearchMethod

    db_session.add(
        CalculationPathSearchResult(
            calculation_id=calc.id,
            method=PathSearchMethod.neb,
            is_double_ended=True,
            converged=True,
            n_points=n_points,
            selected_ts_point_index=n_points // 2,
            climbing_image_index=n_points // 2,
            source_endpoint_count=2,
            zero_energy_reference_hartree=-100.0,
        )
    )
    for i in range(n_points):
        db_session.add(
            CalculationPathSearchPoint(
                calculation_id=calc.id,
                point_index=i,
                path_coordinate=float(i) / max(n_points - 1, 1),
                electronic_energy_hartree=-100.0 + i * 0.05,
                is_ts_guess=(i == n_points // 2),
                is_climbing_image=(i == n_points // 2),
            )
        )
    db_session.flush()


def _full_url(entry_id, *includes):
    inc = ",".join(includes) if includes else ""
    return (
        f"/api/v1/scientific/reaction-entries/{entry_id}/full"
        f"?include={inc}"
        if inc
        else f"/api/v1/scientific/reaction-entries/{entry_id}/full"
    )


# --- Scan -------------------------------------------------------------------


def test_full_scan_section_returns_summary_per_calc(client, db_session):
    from app.db.models.common import CalculationType

    entry, _, _, calc = _entry_with_ts_calc(
        db_session, calc_type=CalculationType.scan
    )
    _attach_scan_result(db_session, calc, dimension=1, n_points=3)
    body = client.get(_full_url(entry.id, "scans")).json()
    assert body["scans"] is not None
    assert len(body["scans"]) == 1
    item = body["scans"][0]
    assert item["calculation_ref"] == calc.public_ref
    assert item["endpoint"] == (
        f"/api/v1/scientific/calculations/{calc.public_ref}/scan"
    )
    summary = item["summary"]
    assert summary is not None
    assert summary["dimension"] == 1
    assert summary["point_count"] == 3
    assert summary["coordinate_count"] == 1
    assert len(summary["coordinates"]) == 1


def test_full_scan_summary_matches_calc_detail_include_scan(
    client, db_session
):
    from app.db.models.common import CalculationType

    entry, _, _, calc = _entry_with_ts_calc(
        db_session, calc_type=CalculationType.scan
    )
    _attach_scan_result(db_session, calc, dimension=1, n_points=4)
    full = client.get(_full_url(entry.id, "scans")).json()
    detail = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=scan"
    ).json()
    assert full["scans"][0]["summary"] == detail["record"]["scan"]


# --- IRC --------------------------------------------------------------------


def test_full_irc_section_returns_summary_per_calc(client, db_session):
    from app.db.models.common import CalculationType

    entry, _, _, calc = _entry_with_ts_calc(
        db_session, calc_type=CalculationType.irc
    )
    _attach_irc_result(db_session, calc, n_points=5)
    body = client.get(_full_url(entry.id, "irc")).json()
    assert body["irc"] is not None
    assert len(body["irc"]) == 1
    item = body["irc"][0]
    assert item["calculation_ref"] == calc.public_ref
    assert item["endpoint"] == (
        f"/api/v1/scientific/calculations/{calc.public_ref}/irc"
    )
    summary = item["summary"]
    assert summary is not None
    assert summary["direction"] == "both"
    assert summary["forward_point_count"] == 2
    assert summary["reverse_point_count"] == 2


def test_full_irc_summary_matches_calc_detail_include_irc(client, db_session):
    from app.db.models.common import CalculationType

    entry, _, _, calc = _entry_with_ts_calc(
        db_session, calc_type=CalculationType.irc
    )
    _attach_irc_result(db_session, calc, n_points=5)
    full = client.get(_full_url(entry.id, "irc")).json()
    detail = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=irc"
    ).json()
    assert full["irc"][0]["summary"] == detail["record"]["irc"]


# --- Path search ------------------------------------------------------------


def test_full_path_search_section_returns_summary_per_calc(
    client, db_session
):
    from app.db.models.common import CalculationType

    entry, _, _, calc = _entry_with_ts_calc(
        db_session, calc_type=CalculationType.path_search
    )
    _attach_path_search_result(db_session, calc, n_points=5)
    body = client.get(_full_url(entry.id, "path_search")).json()
    assert body["path_search"] is not None
    assert len(body["path_search"]) == 1
    item = body["path_search"][0]
    assert item["calculation_ref"] == calc.public_ref
    assert item["endpoint"] == (
        f"/api/v1/scientific/calculations/{calc.public_ref}/path-search"
    )
    summary = item["summary"]
    assert summary is not None
    assert summary["method"] == "neb"
    assert summary["stored_point_count"] == 5
    assert summary["ts_guess_count"] == 1
    assert summary["climbing_image_count"] == 1


def test_full_path_search_summary_matches_calc_detail_include(
    client, db_session
):
    from app.db.models.common import CalculationType

    entry, _, _, calc = _entry_with_ts_calc(
        db_session, calc_type=CalculationType.path_search
    )
    _attach_path_search_result(db_session, calc, n_points=5)
    full = client.get(_full_url(entry.id, "path_search")).json()
    detail = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=path_search"
    ).json()
    assert full["path_search"][0]["summary"] == detail["record"]["path_search"]


# --- Internal-ID policy + abuse-control --------------------------------------


def test_full_path_sections_hide_calculation_id_by_default(client, db_session):
    from app.db.models.common import CalculationType

    entry, _, calcs = _entry_with_ts_and_calcs(
        db_session,
        calc_types=[
            CalculationType.scan,
            CalculationType.irc,
            CalculationType.path_search,
        ],
    )
    _attach_scan_result(db_session, calcs[CalculationType.scan])
    _attach_irc_result(db_session, calcs[CalculationType.irc])
    _attach_path_search_result(db_session, calcs[CalculationType.path_search])

    body = client.get(_full_url(entry.id, "scans", "irc", "path_search")).json()
    for section in ("scans", "irc", "path_search"):
        items = body[section]
        assert items
        for item in items:
            assert "calculation_id" not in item
            assert "calculation_ref" in item


def test_full_path_sections_restore_calculation_id_under_policy(
    client, db_session, allow_internal_ids
):
    from app.db.models.common import CalculationType

    entry, _, _, calc = _entry_with_ts_calc(
        db_session, calc_type=CalculationType.scan
    )
    _attach_scan_result(db_session, calc)
    body = client.get(
        _full_url(entry.id, "scans", "internal_ids")
    ).json()
    item = body["scans"][0]
    assert item["calculation_id"] == calc.id


# --- Defense-in-depth: no point arrays / no geometry coordinates ------------


def test_full_path_sections_do_not_expose_point_arrays_or_xyz(
    client, db_session
):
    """Recursive walk: never inline scan/IRC/path-search point arrays,
    coordinate-value rows, or geometry XYZ under the /full path
    sections — those live only behind the specialized endpoints."""
    from app.db.models.common import CalculationType

    entry, _, calcs = _entry_with_ts_and_calcs(
        db_session,
        calc_types=[
            CalculationType.scan,
            CalculationType.irc,
            CalculationType.path_search,
        ],
    )
    _attach_scan_result(db_session, calcs[CalculationType.scan], n_points=3)
    _attach_irc_result(db_session, calcs[CalculationType.irc])
    _attach_path_search_result(
        db_session, calcs[CalculationType.path_search]
    )

    body = client.get(
        _full_url(entry.id, "scans", "irc", "path_search")
    ).json()
    forbidden = {
        "scan_points",
        "irc_points",
        "path_search_points",
        "points",
        "point_coordinate_values",
        "coordinate_values",
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
                    f"/full path section leaked forbidden key {k!r}"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    for section in ("scans", "irc", "path_search"):
        _walk(body[section])


def test_full_scan_section_empty_when_no_scan_calc(client, db_session):
    """A reaction with TS but no scan calc returns scans=[] under
    include=scans (collection present, empty)."""
    from app.db.models.common import CalculationType

    entry, _, _, _ = _entry_with_ts_calc(
        db_session, calc_type=CalculationType.opt
    )
    body = client.get(_full_url(entry.id, "scans", "irc", "path_search")).json()
    assert body["scans"] == []
    assert body["irc"] == []
    assert body["path_search"] == []


# ---------------------------------------------------------------------------
# Artifacts section alignment with calculation artifact summaries
# ---------------------------------------------------------------------------


def _attach_artifact(db_session, calc, **kw):
    from tests.services.scientific_read._factories import attach_artifact

    return attach_artifact(db_session, calculation=calc, **kw)


def test_full_artifacts_section_empty_when_no_artifacts(client, db_session):
    from app.db.models.common import CalculationType

    entry, _, _, _ = _entry_with_ts_calc(
        db_session, calc_type=CalculationType.opt
    )
    body = client.get(_full_url(entry.id, "artifacts")).json()
    assert body["artifacts"] == []


def test_full_artifacts_section_groups_by_calculation(client, db_session):
    from app.db.models.common import ArtifactKind, CalculationType

    entry, _, _, calc = _entry_with_ts_calc(
        db_session, calc_type=CalculationType.opt
    )
    _attach_artifact(
        db_session,
        calc,
        kind=ArtifactKind.output_log,
        filename="opt.log",
        uri="s3://bucket/opt.log",
    )
    _attach_artifact(
        db_session,
        calc,
        kind=ArtifactKind.input,
        filename="opt.in",
        uri="s3://bucket/opt.in",
    )
    body = client.get(_full_url(entry.id, "artifacts")).json()
    assert body["artifacts"] is not None
    assert len(body["artifacts"]) == 1
    group = body["artifacts"][0]
    assert group["calculation_ref"] == calc.public_ref
    assert group["calculation_type"] == "opt"
    assert len(group["artifacts"]) == 2
    kinds = {a["kind"] for a in group["artifacts"]}
    assert kinds == {"output_log", "input"}


def test_full_artifacts_item_carries_metadata_fields(client, db_session):
    from app.db.models.common import ArtifactKind, CalculationType

    entry, _, _, calc = _entry_with_ts_calc(
        db_session, calc_type=CalculationType.freq
    )
    _attach_artifact(
        db_session,
        calc,
        kind=ArtifactKind.output_log,
        filename="freq.log",
        uri="s3://bucket/freq.log",
    )
    body = client.get(_full_url(entry.id, "artifacts")).json()
    art = body["artifacts"][0]["artifacts"][0]
    # Always-present projection keys.
    for key in ("kind", "uri", "filename", "sha256", "bytes", "created_at"):
        assert key in art
    assert art["kind"] == "output_log"
    assert art["uri"] == "s3://bucket/freq.log"
    assert art["filename"] == "freq.log"


def test_full_artifacts_summary_matches_calc_detail_include_artifacts(
    client, db_session
):
    """Cross-endpoint anti-drift: the artifact list under /full is the
    same shape and values as ``record.artifacts`` from the calculation
    detail endpoint with ``include=artifacts``."""
    from app.db.models.common import ArtifactKind, CalculationType

    entry, _, _, calc = _entry_with_ts_calc(
        db_session, calc_type=CalculationType.sp
    )
    _attach_artifact(
        db_session,
        calc,
        kind=ArtifactKind.output_log,
        filename="sp.log",
        uri="s3://bucket/sp.log",
    )
    _attach_artifact(
        db_session,
        calc,
        kind=ArtifactKind.input,
        filename="sp.in",
        uri="s3://bucket/sp.in",
    )
    full = client.get(_full_url(entry.id, "artifacts")).json()
    detail = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}"
        "?include=artifacts"
    ).json()
    group = next(
        g
        for g in full["artifacts"]
        if g["calculation_ref"] == calc.public_ref
    )
    assert group["artifacts"] == detail["record"]["artifacts"]


def test_full_artifacts_section_hides_internal_ids_by_default(
    client, db_session
):
    from app.db.models.common import CalculationType

    entry, _, _, calc = _entry_with_ts_calc(
        db_session, calc_type=CalculationType.opt
    )
    _attach_artifact(db_session, calc)
    body = client.get(_full_url(entry.id, "artifacts")).json()
    group = body["artifacts"][0]
    assert "calculation_id" not in group
    assert "calculation_ref" in group
    art = group["artifacts"][0]
    assert "artifact_id" not in art
    assert "artifact_ref" in art  # the field exists; None today


def test_full_artifacts_section_restores_ids_under_policy(
    client, db_session, allow_internal_ids
):
    from app.db.models.common import CalculationType

    entry, _, _, calc = _entry_with_ts_calc(
        db_session, calc_type=CalculationType.opt
    )
    artifact = _attach_artifact(db_session, calc)
    body = client.get(_full_url(entry.id, "artifacts", "internal_ids")).json()
    group = body["artifacts"][0]
    assert group["calculation_id"] == calc.id
    assert group["artifacts"][0]["artifact_id"] == artifact.id


def test_full_artifacts_section_does_not_expose_body_or_download(
    client, db_session
):
    """Recursive walk: never inline artifact bytes, contents, or
    download/presigned URLs under the /full artifacts section."""
    from app.db.models.common import ArtifactKind, CalculationType

    entry, _, _, calc = _entry_with_ts_calc(
        db_session, calc_type=CalculationType.opt
    )
    _attach_artifact(
        db_session,
        calc,
        kind=ArtifactKind.output_log,
        filename="opt.log",
        uri="s3://bucket/opt.log",
    )
    body = client.get(_full_url(entry.id, "artifacts")).json()
    forbidden = {
        "body",
        "content",
        "data",
        "presigned_url",
        "download_url",
        "xyz_text",
        "atoms",
        "coords",
        "symbols",
    }

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden, (
                    f"/full artifacts section leaked forbidden key {k!r}"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body["artifacts"])


def test_full_artifacts_section_omits_calcs_without_artifacts(
    client, db_session
):
    """A reachable calc with no artifact rows must not produce an empty
    grouping entry — only calcs with at least one artifact appear."""
    from app.db.models.common import CalculationType

    entry, _, tse, calc_with = _entry_with_ts_calc(
        db_session, calc_type=CalculationType.opt
    )
    # Attach a second TS-owned calc without artifacts.
    from tests.services.scientific_read._factories import make_calculation

    calc_without = make_calculation(
        db_session,
        type=CalculationType.freq,
        transition_state_entry_id=tse.id,
    )
    _attach_artifact(db_session, calc_with)
    body = client.get(_full_url(entry.id, "artifacts")).json()
    refs = {g["calculation_ref"] for g in body["artifacts"]}
    assert calc_with.public_ref in refs
    assert calc_without.public_ref not in refs


# ---------------------------------------------------------------------------
# Conformers section alignment with scientific conformer reads
# ---------------------------------------------------------------------------


def _entry_with_reactant_conformer(
    db_session, *, with_observation=True, with_calc=False
):
    """Build a reaction entry where the reactant species has a
    conformer group + (optional) observation + (optional) calculation."""
    from app.db.models.common import CalculationType
    from tests.services.scientific_read._factories import (
        make_calculation_with_conformer,
        make_conformer_group,
        make_conformer_observation,
    )

    entry = _entry(db_session)
    # The reactant species_entry was created inline by _entry; look it
    # up via the reaction-entry's structure participants so we can
    # attach a conformer group to it.
    from sqlalchemy import select as _select

    from app.db.models.reaction import ReactionEntryStructureParticipant
    from app.db.models.species import SpeciesEntry

    reactant_entry_id = db_session.scalar(
        _select(ReactionEntryStructureParticipant.species_entry_id)
        .where(
            ReactionEntryStructureParticipant.reaction_entry_id == entry.id,
            ReactionEntryStructureParticipant.role
            == _ReactionRole.reactant,
        )
        .limit(1)
    )
    reactant_entry = db_session.get(SpeciesEntry, reactant_entry_id)
    cg = make_conformer_group(db_session, reactant_entry, label="basin_a")
    obs = None
    if with_observation:
        obs = make_conformer_observation(db_session, conformer_group=cg)
    calc = None
    if with_calc and obs is not None:
        calc = make_calculation_with_conformer(
            db_session,
            species_entry=reactant_entry,
            conformer_observation=obs,
            type=CalculationType.opt,
        )
    return entry, reactant_entry, cg, obs, calc


# ReactionRole is needed but the file imports a `ReactionRole` from common;
# keep the helper import close so the test file stays self-contained.
from app.db.models.common import ReactionRole as _ReactionRole


def test_full_conformers_section_groups_by_species_participant(
    client, db_session
):
    entry, reactant_entry, cg, _, _ = _entry_with_reactant_conformer(db_session)
    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full"
        "?include=conformers"
    ).json()
    section = body["conformers"]
    assert section is not None
    # One participant for each side of the reaction (1 reactant + 1 product).
    assert len(section) == 2
    # The reactant participant carries the conformer group.
    by_entry = {
        p["species_entry_ref"]: p for p in section
    }
    reactant_block = by_entry[reactant_entry.public_ref]
    assert reactant_block["role"] == "reactant"
    assert len(reactant_block["conformer_groups"]) == 1
    group_item = reactant_block["conformer_groups"][0]
    assert group_item["conformer_group_ref"] == cg.public_ref


def test_full_conformer_group_item_carries_summary_blocks(client, db_session):
    entry, _, cg, _, _ = _entry_with_reactant_conformer(db_session)
    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full"
        "?include=conformers"
    ).json()
    group_item = next(
        g
        for p in body["conformers"]
        for g in p["conformer_groups"]
        if g["conformer_group_ref"] == cg.public_ref
    )
    assert "conformer_group" in group_item
    assert "observations_summary" in group_item
    assert "evidence_summary" in group_item
    assert "selection_summary" in group_item
    assert "available_sections" in group_item
    # Endpoint hint is ref-based.
    assert group_item["endpoint"] == (
        f"/api/v1/scientific/conformer-groups/{cg.public_ref}"
    )


def test_full_conformer_group_endpoint_resolves(client, db_session):
    """The ref-based endpoint hint must navigate to the live detail
    surface and return the same conformer_group_ref."""
    entry, _, cg, _, _ = _entry_with_reactant_conformer(db_session)
    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full"
        "?include=conformers"
    ).json()
    group_item = body["conformers"][0]["conformer_groups"][0] if (
        body["conformers"][0]["conformer_groups"]
    ) else body["conformers"][1]["conformer_groups"][0]
    detail = client.get(group_item["endpoint"])
    assert detail.status_code == 200, detail.text
    assert detail.json()["record"]["conformer_group"]["conformer_group_ref"] == cg.public_ref


def test_full_conformer_evidence_matches_conformer_detail(
    client, db_session
):
    """Anti-drift: the /full per-group evidence_summary +
    selection_summary + observations_summary blocks are byte-identical
    to the corresponding default-include blocks on the conformer
    detail endpoint."""
    entry, _, cg, _, _ = _entry_with_reactant_conformer(
        db_session, with_calc=True
    )
    full_body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full"
        "?include=conformers"
    ).json()
    detail = client.get(
        f"/api/v1/scientific/conformer-groups/{cg.public_ref}"
    ).json()
    group_item = next(
        g
        for p in full_body["conformers"]
        for g in p["conformer_groups"]
        if g["conformer_group_ref"] == cg.public_ref
    )
    detail_record = detail["record"]
    assert group_item["evidence_summary"] == detail_record["evidence_summary"]
    assert group_item["selection_summary"] == detail_record["selection_summary"]
    assert group_item["observations_summary"] == detail_record["observations_summary"]
    assert group_item["conformer_group"] == detail_record["conformer_group"]


def test_full_conformers_section_empty_participants_listed(client, db_session):
    """Reactant + product participants always appear; empty
    conformer_groups list is the signal for 'no basins on this side'."""
    entry = _entry(db_session)
    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full"
        "?include=conformers"
    ).json()
    assert body["conformers"] is not None
    assert len(body["conformers"]) == 2
    assert all(p["conformer_groups"] == [] for p in body["conformers"])


def test_full_conformers_section_hides_internal_ids_by_default(
    client, db_session
):
    entry, _, _, _, _ = _entry_with_reactant_conformer(db_session)
    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full"
        "?include=conformers"
    ).json()
    for participant in body["conformers"]:
        assert "species_id" not in participant
        assert "species_entry_id" not in participant
        for group in participant["conformer_groups"]:
            assert "conformer_group_id" not in group
            assert "conformer_group_id" not in group["conformer_group"]


def test_full_conformers_section_restores_ids_under_policy(
    client, db_session, allow_internal_ids
):
    entry, reactant_entry, cg, _, _ = _entry_with_reactant_conformer(db_session)
    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full"
        "?include=conformers,internal_ids"
    ).json()
    reactant_block = next(
        p
        for p in body["conformers"]
        if p["species_entry_ref"] == reactant_entry.public_ref
    )
    assert reactant_block["species_entry_id"] == reactant_entry.id
    assert reactant_block["conformer_groups"][0]["conformer_group_id"] == cg.id


def test_full_conformers_section_no_forbidden_payload_keys(
    client, db_session
):
    """Recursive walk: never inline fingerprint / coords JSON or
    geometry coordinate payloads under the conformers section."""
    entry, _, _, _, _ = _entry_with_reactant_conformer(
        db_session, with_calc=True
    )
    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full"
        "?include=conformers"
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
        # /full conformer items must NOT carry heavy include blocks.
        "observations",
        "calculations",
        "geometries",
        "review_history",
    }

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden, (
                    f"/full conformers section leaked forbidden key {k!r}"
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(body["conformers"])


def test_full_conformers_section_omitted_by_default(client, db_session):
    """Without ``include=conformers``, the section is null/absent."""
    entry, _, _, _, _ = _entry_with_reactant_conformer(db_session)
    body = client.get(
        f"/api/v1/scientific/reaction-entries/{entry.id}/full"
    ).json()
    assert body["conformers"] is None
