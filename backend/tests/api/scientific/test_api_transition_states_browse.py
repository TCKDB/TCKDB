"""API tests for GET /api/v1/scientific/transition-states/browse.

Companion to ``test_api_scientific_transition_states.py``'s search-surface
tests. Reuses that module's fixture helpers directly (build-a-TS,
attach-a-calc, get-or-create software/workflow-tool release) rather than
duplicating them, since the two surfaces share the exact same underlying
data model and filter semantics -- only the "no filter required, no
ref/identifier fields" part is new.
"""

from __future__ import annotations

from app.api.config import settings
from app.db.models.common import (
    RecordReviewStatus,
    SubmissionRecordType,
    TransitionStateEntryStatus,
)
from app.db.models.reaction import ReactionFamily
from tests.api.scientific.test_api_scientific_transition_states import (
    _attach_calc,
    _make_reaction_with_ts,
    _make_software_release,
    _make_workflow_tool_release,
)
from tests.services.scientific_read._factories import (
    attach_geometry_validation,
    attach_scf_stability,
    make_chem_reaction,
    make_lot,
    make_reaction_entry,
    make_species,
    make_species_entry,
    make_transition_state,
    make_transition_state_entry,
    set_review,
)


def _browse_url(**params) -> str:
    base = "/api/v1/scientific/transition-states/browse"
    if not params:
        return base
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}?{qs}"


# ===========================================================================
# The headline defect this route exists to fix
# ===========================================================================


def test_get_with_no_query_params_returns_200_not_422(client, db_session):
    """The exact call that 422s on /search today must 200 here.

    ``/transition-states/search`` with an empty query string 422s
    (``missing_filter``); this sibling route must not.
    """
    _, _, _, entries = _make_reaction_with_ts(db_session)

    resp = client.get(_browse_url())

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "request" in body and "review_summary" in body and "records" in body
    assert "pagination" in body
    refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in body["records"]
    }
    assert entries[0].public_ref in refs


def test_search_still_422s_missing_filter(client, db_session):
    """The guard this route deliberately does not relax, pinned so a
    future edit cannot silently widen /search instead of adding a sibling.
    """
    resp = client.get("/api/v1/scientific/transition-states/search")
    assert resp.status_code == 422
    assert "missing_filter" in resp.text


def test_get_ignores_ref_query_params_it_does_not_declare(client, db_session):
    """Owner/parent ref filters are not declared parameters here -- FastAPI
    drops them. A TS entry that would never match a bogus ref on /search
    still shows up here, because browse never reads that parameter at all.
    """
    _, rxe, ts, entries = _make_reaction_with_ts(db_session)

    resp = client.get(
        _browse_url(reaction_entry_ref="rxe_this_matches_nothing_at_all")
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in body["records"]
    }
    assert entries[0].public_ref in refs
    # And the ignored parameter must not even echo back as a filter --
    # there is no field on TransitionStatesBrowseRequest to have carried it.
    assert "reaction_entry_ref" not in body["request"]["filter"]


def test_get_rejects_client_supplied_sort(client, db_session):
    resp = client.get(_browse_url(sort="anything"))
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


def test_get_rejects_unknown_include_token(client, db_session):
    resp = client.get(_browse_url(include="banana"))
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


def test_get_limit_above_the_framework_bound_is_rejected_by_the_framework(
    client, db_session
):
    resp = client.get(_browse_url(limit=999))
    assert resp.status_code == 422
    assert resp.json()["code"] == "request_validation_error"


def test_get_limit_above_the_service_cap_is_rejected_by_the_service(
    client, db_session, monkeypatch
):
    monkeypatch.setattr(settings, "public_max_limit", 10)
    resp = client.get(_browse_url(limit=50))
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "limit_too_large"


def test_get_offset_beyond_the_shipped_deep_paging_cap_is_rejected(
    client, db_session
):
    base = "/api/v1/scientific/transition-states/browse?offset="

    allowed = client.get(f"{base}{settings.public_max_offset}")
    assert allowed.status_code == 200, allowed.text

    resp = client.get(f"{base}{settings.public_max_offset + 1}")
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "offset_too_large"


# ===========================================================================
# Pagination is real
# ===========================================================================


def test_pagination_offset_moves_the_window_and_pages_do_not_overlap(
    client, db_session
):
    refs = set()
    for _ in range(5):
        _, _, _, entries = _make_reaction_with_ts(db_session)
        refs.add(entries[0].public_ref)

    page1 = client.get(_browse_url(limit=2, offset=0)).json()
    page2 = client.get(_browse_url(limit=2, offset=2)).json()

    refs1 = [
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in page1["records"]
    ]
    refs2 = [
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in page2["records"]
    ]
    assert len(refs1) == 2
    assert len(refs2) == 2
    # Real movement, not the same page twice.
    assert set(refs1).isdisjoint(set(refs2))
    assert page1["pagination"]["total"] == page2["pagination"]["total"]
    assert page1["pagination"]["total"] >= 5


def test_pagination_total_reflects_full_corpus_not_the_page(client, db_session):
    for _ in range(4):
        _make_reaction_with_ts(db_session)

    resp = client.get(_browse_url(limit=2))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["records"]) == 2
    assert body["pagination"]["returned"] == 2
    assert body["pagination"]["total"] >= 4
    assert body["pagination"]["total"] != body["pagination"]["returned"]


# ===========================================================================
# Every filter actually filters (strict subset + excluded rows fail
# the predicate)
# ===========================================================================


def test_filter_by_status_is_a_strict_subset(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(
        db_session,
        n_entries=2,
        statuses=[
            TransitionStateEntryStatus.optimized,
            TransitionStateEntryStatus.validated,
        ],
    )
    unfiltered = client.get(_browse_url()).json()
    filtered = client.get(_browse_url(status="validated")).json()

    unfiltered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in unfiltered["records"]
    }
    filtered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in filtered["records"]
    }
    assert filtered_refs < unfiltered_refs
    assert filtered_refs == {entries[1].public_ref}
    excluded = unfiltered_refs - filtered_refs
    assert entries[0].public_ref in excluded
    for r in unfiltered["records"]:
        if r["transition_state_entry"]["transition_state_entry_ref"] in excluded:
            assert r["transition_state_entry"]["status"] != "validated"
    assert all(
        r["transition_state_entry"]["status"] == "validated"
        for r in filtered["records"]
    )


def test_filter_by_charge_and_multiplicity_is_a_strict_subset(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    entries_b[0].charge = 1
    db_session.flush()

    unfiltered = client.get(_browse_url()).json()
    filtered = client.get(_browse_url(charge=0, multiplicity=2)).json()

    unfiltered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in unfiltered["records"]
    }
    filtered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in filtered["records"]
    }
    assert filtered_refs < unfiltered_refs
    assert entries_a[0].public_ref in filtered_refs
    assert entries_b[0].public_ref in unfiltered_refs - filtered_refs
    assert all(
        r["transition_state_entry"]["charge"] == 0
        and r["transition_state_entry"]["multiplicity"] == 2
        for r in filtered["records"]
    )


def test_filter_by_has_calculations_is_a_strict_subset(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    _attach_calc(db_session, tse=entries_a[0])

    unfiltered = client.get(_browse_url()).json()
    filtered = client.get(_browse_url(has_calculations="true")).json()

    unfiltered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in unfiltered["records"]
    }
    filtered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in filtered["records"]
    }
    assert filtered_refs < unfiltered_refs
    assert filtered_refs == {entries_a[0].public_ref}
    assert entries_b[0].public_ref in unfiltered_refs - filtered_refs


def _assert_has_type_filter_is_strict_subset(client, db_session, param, calc_type):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    _attach_calc(db_session, tse=entries_a[0], calc_type=calc_type)

    unfiltered = client.get(_browse_url()).json()
    filtered = client.get(_browse_url(**{param: "true"})).json()

    unfiltered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in unfiltered["records"]
    }
    filtered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in filtered["records"]
    }
    assert filtered_refs < unfiltered_refs
    assert filtered_refs == {entries_a[0].public_ref}
    assert entries_b[0].public_ref in unfiltered_refs - filtered_refs


def test_filter_by_has_opt_is_a_strict_subset(client, db_session):
    from app.db.models.common import CalculationType

    _assert_has_type_filter_is_strict_subset(
        client, db_session, "has_opt", CalculationType.opt
    )


def test_filter_by_has_freq_is_a_strict_subset(client, db_session):
    from app.db.models.common import CalculationType

    _assert_has_type_filter_is_strict_subset(
        client, db_session, "has_freq", CalculationType.freq
    )


def test_filter_by_has_sp_is_a_strict_subset(client, db_session):
    from app.db.models.common import CalculationType

    _assert_has_type_filter_is_strict_subset(
        client, db_session, "has_sp", CalculationType.sp
    )


def test_filter_by_has_irc_is_a_strict_subset(client, db_session):
    from app.db.models.common import CalculationType

    _assert_has_type_filter_is_strict_subset(
        client, db_session, "has_irc", CalculationType.irc
    )


def test_filter_by_has_path_search_is_a_strict_subset(client, db_session):
    from app.db.models.common import CalculationType

    _assert_has_type_filter_is_strict_subset(
        client, db_session, "has_path_search", CalculationType.path_search
    )


def test_filter_by_has_geometry_validation_is_a_strict_subset(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    calc = _attach_calc(db_session, tse=entries_a[0])
    attach_geometry_validation(db_session, calculation=calc)
    _attach_calc(db_session, tse=entries_b[0])

    unfiltered = client.get(_browse_url()).json()
    filtered = client.get(_browse_url(has_geometry_validation="true")).json()

    unfiltered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in unfiltered["records"]
    }
    filtered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in filtered["records"]
    }
    assert filtered_refs < unfiltered_refs
    assert filtered_refs == {entries_a[0].public_ref}
    assert entries_b[0].public_ref in unfiltered_refs - filtered_refs


def test_filter_by_has_scf_stability_is_a_strict_subset(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    calc = _attach_calc(db_session, tse=entries_a[0])
    attach_scf_stability(db_session, calculation=calc)
    _attach_calc(db_session, tse=entries_b[0])

    unfiltered = client.get(_browse_url()).json()
    filtered = client.get(_browse_url(has_scf_stability="true")).json()

    unfiltered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in unfiltered["records"]
    }
    filtered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in filtered["records"]
    }
    assert filtered_refs < unfiltered_refs
    assert filtered_refs == {entries_a[0].public_ref}
    assert entries_b[0].public_ref in unfiltered_refs - filtered_refs


def test_filter_by_method_and_basis_is_a_strict_subset(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    lot_a = make_lot(db_session, method="wb97xd", basis="def2tzvp")
    lot_b = make_lot(db_session, method="b3lyp", basis="6-31g")
    _attach_calc(db_session, tse=entries_a[0], lot=lot_a)
    _attach_calc(db_session, tse=entries_b[0], lot=lot_b)

    unfiltered = client.get(_browse_url()).json()
    filtered = client.get(
        _browse_url(method="wb97xd", basis="def2tzvp")
    ).json()

    unfiltered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in unfiltered["records"]
    }
    filtered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in filtered["records"]
    }
    assert filtered_refs < unfiltered_refs
    assert filtered_refs == {entries_a[0].public_ref}
    assert entries_b[0].public_ref in unfiltered_refs - filtered_refs


def test_filter_by_software_and_version_is_a_strict_subset(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    _, sr_a = _make_software_release(db_session, name="gaussian", version="g16.a03")
    _, sr_b = _make_software_release(db_session, name="orca", version="5.0.4")
    _attach_calc(db_session, tse=entries_a[0], software_release=sr_a)
    _attach_calc(db_session, tse=entries_b[0], software_release=sr_b)

    unfiltered = client.get(_browse_url()).json()
    filtered = client.get(
        _browse_url(software="gaussian", software_version="g16.a03")
    ).json()

    unfiltered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in unfiltered["records"]
    }
    filtered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in filtered["records"]
    }
    assert filtered_refs < unfiltered_refs
    assert filtered_refs == {entries_a[0].public_ref}
    assert entries_b[0].public_ref in unfiltered_refs - filtered_refs


def test_filter_by_workflow_tool_and_version_is_a_strict_subset(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    _, wtr_a = _make_workflow_tool_release(db_session, name="arc", version="1.2.3")
    _, wtr_b = _make_workflow_tool_release(
        db_session, name="qcelemental", version="0.27.0"
    )
    _attach_calc(db_session, tse=entries_a[0], workflow_tool_release=wtr_a)
    _attach_calc(db_session, tse=entries_b[0], workflow_tool_release=wtr_b)

    unfiltered = client.get(_browse_url()).json()
    filtered = client.get(
        _browse_url(workflow_tool="arc", workflow_tool_version="1.2.3")
    ).json()

    unfiltered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in unfiltered["records"]
    }
    filtered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in filtered["records"]
    }
    assert filtered_refs < unfiltered_refs
    assert filtered_refs == {entries_a[0].public_ref}
    assert entries_b[0].public_ref in unfiltered_refs - filtered_refs


# ===========================================================================
# Review-status handling, same contract as /species/browse
# ===========================================================================


def test_default_hides_rejected(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_id=entries_b[0].id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(_browse_url()).json()
    refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in body["records"]
    }
    assert entries_a[0].public_ref in refs
    assert entries_b[0].public_ref not in refs


def test_include_rejected_surfaces_them(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_id=entries_b[0].id,
        status=RecordReviewStatus.rejected,
    )
    default_body = client.get(_browse_url()).json()
    included_body = client.get(_browse_url(include_rejected="true")).json()

    default_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in default_body["records"]
    }
    included_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in included_body["records"]
    }
    assert entries_b[0].public_ref not in default_refs
    assert entries_b[0].public_ref in included_refs
    assert default_refs < included_refs


def test_default_hides_deprecated(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_id=entries_b[0].id,
        status=RecordReviewStatus.deprecated,
    )
    default_body = client.get(_browse_url()).json()
    included_body = client.get(_browse_url(include_deprecated="true")).json()

    default_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in default_body["records"]
    }
    included_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in included_body["records"]
    }
    assert entries_b[0].public_ref not in default_refs
    assert entries_b[0].public_ref in included_refs


def test_min_review_status_narrows(client, db_session):
    _, _, _, entries_a = _make_reaction_with_ts(db_session)
    _, _, _, entries_b = _make_reaction_with_ts(db_session)
    set_review(
        db_session,
        record_type=SubmissionRecordType.transition_state_entry,
        record_id=entries_a[0].id,
        status=RecordReviewStatus.approved,
    )
    unfiltered = client.get(_browse_url()).json()
    filtered = client.get(_browse_url(min_review_status="approved")).json()

    unfiltered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in unfiltered["records"]
    }
    filtered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in filtered["records"]
    }
    assert filtered_refs < unfiltered_refs
    assert filtered_refs == {entries_a[0].public_ref}
    assert entries_b[0].public_ref in unfiltered_refs - filtered_refs


# ===========================================================================
# Record shape parity with /search
# ===========================================================================


def test_record_envelope_matches_search_shape(client, db_session):
    """Same shape as ``ScientificTransitionStateEntryRecord`` on /search, so
    a client can reuse one parser across both surfaces.
    """
    _, rxe, ts, entries = _make_reaction_with_ts(db_session)

    resp = client.get(_browse_url())
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) >= {"request", "review_summary", "records", "pagination"}
    record = next(
        r
        for r in body["records"]
        if r["transition_state_entry"]["transition_state_entry_ref"]
        == entries[0].public_ref
    )
    assert set(record.keys()) >= {
        "transition_state_entry",
        "transition_state",
        "reaction",
        "evidence_summary",
        "validation",
        "available_sections",
    }
    assert record["reaction"]["reaction_entry_ref"] == rxe.public_ref
    assert record["transition_state"]["transition_state_ref"] == ts.public_ref
    # Internal ids stripped by default (Phase D policy), same boundary
    # helper as /search.
    assert "transition_state_entry_id" not in record["transition_state_entry"]
    assert "transition_state_id" not in record["transition_state"]


def test_include_calculations_on_records(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    lot = make_lot(db_session)
    _attach_calc(db_session, tse=entries[0], lot=lot)

    resp = client.get(_browse_url(include="calculations"))
    assert resp.status_code == 200, resp.text
    record = next(
        r
        for r in resp.json()["records"]
        if r["transition_state_entry"]["transition_state_entry_ref"]
        == entries[0].public_ref
    )
    assert record["calculations"] is not None
    assert len(record["calculations"]) == 1


def test_default_response_omits_calculations_key(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    resp = client.get(_browse_url())
    record = next(
        r
        for r in resp.json()["records"]
        if r["transition_state_entry"]["transition_state_entry_ref"]
        == entries[0].public_ref
    )
    assert "calculations" not in record


# ===========================================================================
# Software on evidence_summary (item 1: rows must state software)
# ===========================================================================


def test_evidence_summary_reports_software_per_calculation_type(client, db_session):
    """A calc's ``software_release`` must surface on the browse row.

    Mirrors ``levels_of_theory``'s shape and absence contract: keyed by
    calculation type, a list even at length one. This is the wire field
    that lets a reader tell apart two rows with an identical level of
    theory but different originating code (e.g. CCSD(T)-F12 on Molpro vs
    ORCA) -- see the PR description for the live-archive case this closes.
    """
    _, _, _, entries = _make_reaction_with_ts(db_session)
    _, sr = _make_software_release(db_session, name="orca", version="5.0.4")
    _attach_calc(db_session, tse=entries[0], software_release=sr)

    resp = client.get(_browse_url())
    assert resp.status_code == 200, resp.text
    record = next(
        r
        for r in resp.json()["records"]
        if r["transition_state_entry"]["transition_state_entry_ref"]
        == entries[0].public_ref
    )
    software = record["evidence_summary"]["software"]
    assert "opt" in software
    assert len(software["opt"]) == 1
    assert software["opt"][0]["software"] == "orca"
    assert software["opt"][0]["version"] == "5.0.4"
    assert software["opt"][0]["software_release_ref"] == sr.public_ref
    # Internal ids stripped by default, same boundary as every other block.
    assert "software_release_id" not in software["opt"][0]


def test_evidence_summary_software_key_present_empty_when_unattributed(
    client, db_session
):
    """A calc of a type exists but names no software -- key present, empty
    list, never an absent key (that would claim the type has no calc at
    all, which is false). Same absence-vs-emptiness contract as
    ``levels_of_theory``.
    """
    _, _, _, entries = _make_reaction_with_ts(db_session)
    _attach_calc(db_session, tse=entries[0])  # no software_release

    resp = client.get(_browse_url())
    record = next(
        r
        for r in resp.json()["records"]
        if r["transition_state_entry"]["transition_state_entry_ref"]
        == entries[0].public_ref
    )
    software = record["evidence_summary"]["software"]
    assert software.get("opt") == []


def test_evidence_summary_software_absent_key_when_no_calculation(
    client, db_session
):
    _, _, _, entries = _make_reaction_with_ts(db_session)
    resp = client.get(_browse_url())
    record = next(
        r
        for r in resp.json()["records"]
        if r["transition_state_entry"]["transition_state_entry_ref"]
        == entries[0].public_ref
    )
    assert record["evidence_summary"]["software"] == {}


# ===========================================================================
# Findability filters (item 4: family + participant_smiles)
# ===========================================================================


def _make_reaction_with_species(
    db_session, *, reactant_smiles: str, product_smiles: str, family_name=None
):
    """Build a Species x2 -> ChemReaction -> ReactionEntry -> TS -> TS-entry
    chain with caller-controlled reactant/product SMILES and an optional
    reaction family, for filter tests that need real structural content
    (unlike ``_make_reaction_with_ts``, which uses opaque fake InChIKeys).
    """
    # ``make_species`` defaults ``inchi_key`` to an opaque fake value
    # (deliberately, per its own docstring) -- the participant filter
    # matches on the *real* RDKit-computed InChIKey, so these tests must
    # supply it explicitly, same as production's species-resolution
    # listener would.
    from app.schemas.reads.scientific_structure_search import StructureQueryKind
    from app.services.scientific_read.structure_query import (
        inchi_key_from_query,
    )

    sp_a = make_species(
        db_session,
        smiles=reactant_smiles,
        inchi_key=inchi_key_from_query(StructureQueryKind.smiles, reactant_smiles),
    )
    sp_b = make_species(
        db_session,
        smiles=product_smiles,
        inchi_key=inchi_key_from_query(StructureQueryKind.smiles, product_smiles),
    )
    se_a = make_species_entry(db_session, sp_a)
    se_b = make_species_entry(db_session, sp_b)
    chem = make_chem_reaction(db_session, reactants=[sp_a], products=[sp_b])
    if family_name is not None:
        family = (
            db_session.query(ReactionFamily)
            .filter(ReactionFamily.name == family_name)
            .one_or_none()
        )
        if family is None:
            family = ReactionFamily(name=family_name)
            db_session.add(family)
            db_session.flush()
        chem.reaction_family_id = family.id
        db_session.flush()
    rxe = make_reaction_entry(
        db_session,
        reaction=chem,
        reactant_entries=[se_a],
        product_entries=[se_b],
    )
    ts = make_transition_state(db_session, reaction_entry=rxe, label="TS0")
    entry = make_transition_state_entry(db_session, transition_state=ts)
    return chem, rxe, ts, entry


def test_filter_by_family_is_a_strict_subset(client, db_session):
    _, _, _, entry_a = _make_reaction_with_species(
        db_session,
        reactant_smiles="CCO",
        product_smiles="CC=O",
        family_name="R_Addition_MultipleBond",
    )
    _, _, _, entry_b = _make_reaction_with_species(
        db_session,
        reactant_smiles="CCN",
        product_smiles="CC=N",
        family_name="H_Abstraction",
    )

    unfiltered = client.get(_browse_url()).json()
    filtered = client.get(
        _browse_url(family="R_Addition_MultipleBond")
    ).json()

    unfiltered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in unfiltered["records"]
    }
    filtered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in filtered["records"]
    }
    assert filtered_refs < unfiltered_refs
    assert filtered_refs == {entry_a.public_ref}
    assert entry_b.public_ref in unfiltered_refs - filtered_refs
    assert all(
        r["reaction"]["family"] == "R_Addition_MultipleBond"
        for r in filtered["records"]
    )


def test_filter_by_unknown_family_returns_empty_not_422(client, db_session):
    _make_reaction_with_ts(db_session)
    resp = client.get(_browse_url(family="not_a_real_family"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["records"] == []


def test_filter_by_participant_smiles_matches_reactant_side(client, db_session):
    _, _, _, entry_a = _make_reaction_with_species(
        db_session, reactant_smiles="CCO", product_smiles="CC=O"
    )
    _, _, _, entry_b = _make_reaction_with_species(
        db_session, reactant_smiles="CCN", product_smiles="CC=N"
    )

    unfiltered = client.get(_browse_url()).json()
    filtered = client.get(_browse_url(participant_smiles="CCO")).json()

    unfiltered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in unfiltered["records"]
    }
    filtered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in filtered["records"]
    }
    assert filtered_refs < unfiltered_refs
    assert filtered_refs == {entry_a.public_ref}
    assert entry_b.public_ref in unfiltered_refs - filtered_refs


def test_filter_by_participant_smiles_matches_product_side_too(client, db_session):
    """The filter is OR-across-role -- a query matching only the product
    still finds the reaction, since the request has no separate
    reactant/product fields (one filter, either side). Asserted as a
    strict subset against a second, non-matching reaction (not just
    ``in``) -- an ``in`` check alone would pass vacuously if the filter
    were silently ignored and every record came back unfiltered.
    """
    _, _, _, entry_a = _make_reaction_with_species(
        db_session, reactant_smiles="CCO", product_smiles="CC=O"
    )
    _, _, _, entry_b = _make_reaction_with_species(
        db_session, reactant_smiles="CCN", product_smiles="CC=N"
    )

    unfiltered = client.get(_browse_url()).json()
    filtered = client.get(_browse_url(participant_smiles="CC=O")).json()

    unfiltered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in unfiltered["records"]
    }
    filtered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in filtered["records"]
    }
    assert filtered_refs < unfiltered_refs
    assert filtered_refs == {entry_a.public_ref}
    assert entry_b.public_ref in unfiltered_refs - filtered_refs


def test_filter_by_participant_smiles_invalid_smiles_422s(client, db_session):
    from urllib.parse import quote

    resp = client.get(
        _browse_url(participant_smiles=quote("not(a valid smiles", safe=""))
    )
    assert resp.status_code == 422, resp.text
    assert "invalid_structure_query" in resp.text
    # The 422 must name the query parameter the caller actually supplied
    # (`participant_smiles`), not `query_smiles` -- the two share the same
    # RDKit parsing helper (`inchi_key_from_query`), which used to hardcode
    # the species-browse structure filter's own field name into every
    # message regardless of caller.
    assert "participant_smiles" in resp.text
    assert "query_smiles" not in resp.text


def test_filter_by_empty_participant_smiles_is_a_no_op_not_a_zero_match(
    client, db_session
):
    """``?participant_smiles=`` (present, empty) must behave exactly like
    omitting the filter, not like a filter that matches nothing.

    RDKit parses the empty string as a valid *empty* molecule rather than
    returning ``None``, so before the ``if request.participant_smiles:``
    guard this silently computed a real (if useless) InChIKey and narrowed
    the result set to zero rows -- a caller clearing a form field would see
    "no results" rather than "no filter applied".
    """
    _, _, _, entries = _make_reaction_with_ts(db_session)

    unfiltered = client.get(_browse_url()).json()
    empty_filtered = client.get(_browse_url(participant_smiles="")).json()

    unfiltered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in unfiltered["records"]
    }
    empty_filtered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in empty_filtered["records"]
    }
    assert empty_filtered_refs == unfiltered_refs
    assert entries[0].public_ref in empty_filtered_refs


def test_filter_by_empty_family_is_a_no_op_not_a_zero_match(client, db_session):
    _, _, _, entries = _make_reaction_with_ts(db_session)

    unfiltered = client.get(_browse_url()).json()
    empty_filtered = client.get(_browse_url(family="")).json()

    unfiltered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in unfiltered["records"]
    }
    empty_filtered_refs = {
        r["transition_state_entry"]["transition_state_entry_ref"]
        for r in empty_filtered["records"]
    }
    assert empty_filtered_refs == unfiltered_refs
    assert entries[0].public_ref in empty_filtered_refs
