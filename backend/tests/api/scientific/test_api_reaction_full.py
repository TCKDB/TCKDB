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
