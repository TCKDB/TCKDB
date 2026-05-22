"""API tests for ``GET|POST /api/v1/scientific/artifacts/search``."""

from __future__ import annotations

from app.db.models.calculation import CalculationArtifact
from app.db.models.common import (
    ArtifactKind,
    CalculationQuality,
    CalculationType,
    RecordReviewStatus,
    SubmissionRecordType,
    TransitionStateEntryStatus,
)
from app.db.models.reaction import ChemReaction, ReactionEntry
from app.db.models.transition_state import TransitionState, TransitionStateEntry
from tests.services.scientific_read._factories import (
    attach_artifact,
    make_calculation,
    make_calculation_with_conformer,
    make_conformer_group,
    make_conformer_observation,
    make_lot,
    make_species,
    make_species_entry,
    next_inchi_key,
    set_review,
)


SEARCH_URL = "/api/v1/scientific/artifacts/search"


# ---------------------------------------------------------------------------
# Local fixture helpers
# ---------------------------------------------------------------------------


def _make_species_owned_calc(db_session, **kw):
    species = make_species(
        db_session,
        smiles=kw.pop("smiles", "CCO"),
        inchi_key=next_inchi_key("ART"),
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


def _artifact_refs(body):
    return [
        (r["artifact"].get("kind"), r["calculation"]["calculation_ref"])
        for r in body["records"]
    ]


# ---------------------------------------------------------------------------
# Missing-filter rule + sort validation
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
    resp = client.get(
        SEARCH_URL + "?include_rejected=true&offset=0&limit=10"
    )
    assert resp.status_code == 422
    assert "missing_filter" in resp.text


def test_get_search_rejects_client_sort(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc)
    resp = client.get(SEARCH_URL + "?artifact_kind=output_log&sort=created_at")
    assert resp.status_code == 422
    assert "client_sort_not_supported" in resp.text


# ---------------------------------------------------------------------------
# Filter semantics
# ---------------------------------------------------------------------------


def test_search_by_artifact_kind(client, db_session):
    _, _, calc1 = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc1, kind=ArtifactKind.output_log)
    _, _, calc2 = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc2, kind=ArtifactKind.input)
    body = client.get(SEARCH_URL + "?artifact_kind=output_log").json()
    kinds = {r["artifact"]["kind"] for r in body["records"]}
    assert kinds == {"output_log"}


def test_search_by_filename_exact(client, db_session):
    _, _, calc1 = _make_species_owned_calc(db_session)
    attach_artifact(
        db_session, calculation=calc1, filename="output.log"
    )
    _, _, calc2 = _make_species_owned_calc(db_session)
    attach_artifact(
        db_session, calculation=calc2, filename="other.log"
    )
    body = client.get(SEARCH_URL + "?filename=output.log").json()
    filenames = {r["artifact"]["filename"] for r in body["records"]}
    assert filenames == {"output.log"}


def test_search_by_filename_contains(client, db_session):
    _, _, calc1 = _make_species_owned_calc(db_session)
    attach_artifact(
        db_session, calculation=calc1, filename="Frog-OUTPUT.log"
    )
    _, _, calc2 = _make_species_owned_calc(db_session)
    attach_artifact(
        db_session, calculation=calc2, filename="input.gjf"
    )
    body = client.get(SEARCH_URL + "?filename_contains=output").json()
    filenames = {r["artifact"]["filename"] for r in body["records"]}
    assert filenames == {"Frog-OUTPUT.log"}


def test_search_by_sha256(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    a = attach_artifact(db_session, calculation=calc)
    a.sha256 = "a" * 64
    db_session.flush()

    body = client.get(SEARCH_URL + "?sha256=" + ("a" * 64)).json()
    assert len(body["records"]) == 1
    assert body["records"][0]["artifact"]["sha256"] == "a" * 64


def test_search_by_has_sha256_true(client, db_session):
    _, _, calc1 = _make_species_owned_calc(db_session)
    a1 = attach_artifact(db_session, calculation=calc1)
    a1.sha256 = "b" * 64
    _, _, calc2 = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc2)  # sha256=None
    db_session.flush()
    body = client.get(SEARCH_URL + "?has_sha256=true").json()
    for rec in body["records"]:
        assert rec["artifact"]["sha256"] is not None


def test_search_by_has_sha256_false(client, db_session):
    _, _, calc1 = _make_species_owned_calc(db_session)
    a1 = attach_artifact(db_session, calculation=calc1)
    a1.sha256 = "c" * 64
    _, _, calc2 = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc2)
    db_session.flush()
    body = client.get(SEARCH_URL + "?has_sha256=false").json()
    for rec in body["records"]:
        assert rec["artifact"]["sha256"] is None


def test_search_by_has_bytes(client, db_session):
    _, _, calc1 = _make_species_owned_calc(db_session)
    a1 = attach_artifact(db_session, calculation=calc1)
    a1.bytes = 1234
    _, _, calc2 = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc2)
    db_session.flush()
    body = client.get(SEARCH_URL + "?has_bytes=true").json()
    for rec in body["records"]:
        assert rec["artifact"]["bytes"] is not None


def test_search_by_bytes_min_max(client, db_session):
    _, _, calc1 = _make_species_owned_calc(db_session)
    a1 = attach_artifact(db_session, calculation=calc1)
    a1.bytes = 100
    _, _, calc2 = _make_species_owned_calc(db_session)
    a2 = attach_artifact(db_session, calculation=calc2)
    a2.bytes = 1_000_000
    db_session.flush()
    body = client.get(
        SEARCH_URL + "?bytes_min=500&bytes_max=2000000"
    ).json()
    sizes = {r["artifact"]["bytes"] for r in body["records"]}
    assert sizes == {1_000_000}


def test_search_by_calculation_ref(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc)
    _, _, other = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=other)
    body = client.get(
        SEARCH_URL + f"?calculation_ref={calc.public_ref}"
    ).json()
    refs = {r["calculation"]["calculation_ref"] for r in body["records"]}
    assert refs == {calc.public_ref}


def test_search_by_calculation_type(client, db_session):
    _, _, opt_calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.opt
    )
    attach_artifact(db_session, calculation=opt_calc)
    _, _, sp_calc = _make_species_owned_calc(
        db_session, calc_type=CalculationType.sp
    )
    attach_artifact(db_session, calculation=sp_calc)
    body = client.get(SEARCH_URL + "?calculation_type=opt").json()
    refs = {r["calculation"]["calculation_ref"] for r in body["records"]}
    assert opt_calc.public_ref in refs
    assert sp_calc.public_ref not in refs


def test_search_by_method_and_basis(client, db_session):
    lot_match = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    lot_other = make_lot(db_session, method="wb97xd", basis="6-31g")
    species_a = make_species(
        db_session, smiles="CCO", inchi_key=next_inchi_key("LOTA")
    )
    entry_a = make_species_entry(db_session, species_a)
    species_b = make_species(
        db_session, smiles="CCN", inchi_key=next_inchi_key("LOTB")
    )
    entry_b = make_species_entry(db_session, species_b)
    calc_match = make_calculation(
        db_session, species_entry_id=entry_a.id, lot_id=lot_match.id
    )
    attach_artifact(db_session, calculation=calc_match)
    calc_other = make_calculation(
        db_session, species_entry_id=entry_b.id, lot_id=lot_other.id
    )
    attach_artifact(db_session, calculation=calc_other)
    body = client.get(
        SEARCH_URL + "?method=b3lyp&basis=def2tzvp"
    ).json()
    refs = {r["calculation"]["calculation_ref"] for r in body["records"]}
    assert calc_match.public_ref in refs
    assert calc_other.public_ref not in refs


def test_search_by_software_and_version(client, db_session):
    from app.db.models.software import Software, SoftwareRelease

    sw = Software(name="orca")
    db_session.add(sw)
    db_session.flush()
    rel = SoftwareRelease(software_id=sw.id, version="6.0.1")
    db_session.add(rel)
    db_session.flush()

    _, _, calc_match = _make_species_owned_calc(db_session)
    calc_match.software_release_id = rel.id
    _, _, calc_other = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc_match)
    attach_artifact(db_session, calculation=calc_other)
    db_session.flush()

    body = client.get(
        SEARCH_URL + "?software=orca&software_version=6.0.1"
    ).json()
    refs = {r["calculation"]["calculation_ref"] for r in body["records"]}
    assert calc_match.public_ref in refs
    assert calc_other.public_ref not in refs


def test_search_by_workflow_tool_and_version(client, db_session):
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
    attach_artifact(db_session, calculation=calc_match)
    attach_artifact(db_session, calculation=calc_other)
    db_session.flush()

    body = client.get(
        SEARCH_URL + "?workflow_tool=ARC&workflow_tool_version=1.2.3"
    ).json()
    refs = {r["calculation"]["calculation_ref"] for r in body["records"]}
    assert calc_match.public_ref in refs
    assert calc_other.public_ref not in refs


def test_search_by_species_entry_ref(client, db_session):
    _, entry, calc = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc)
    _, _, other = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=other)
    body = client.get(
        SEARCH_URL + f"?species_entry_ref={entry.public_ref}"
    ).json()
    refs = {r["calculation"]["calculation_ref"] for r in body["records"]}
    assert refs == {calc.public_ref}


def test_search_by_transition_state_entry_ref(client, db_session):
    tse, calc = _make_ts_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc)
    _, _, other = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=other)
    body = client.get(
        SEARCH_URL + f"?transition_state_entry_ref={tse.public_ref}"
    ).json()
    refs = {r["calculation"]["calculation_ref"] for r in body["records"]}
    assert refs == {calc.public_ref}


def test_search_by_conformer_observation_ref(client, db_session):
    _, entry, _ = _make_species_owned_calc(db_session)
    cg = make_conformer_group(db_session, entry)
    obs = make_conformer_observation(db_session, conformer_group=cg)
    calc = make_calculation_with_conformer(
        db_session, species_entry=entry, conformer_observation=obs
    )
    attach_artifact(db_session, calculation=calc)
    _, _, other = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=other)
    body = client.get(
        SEARCH_URL + f"?conformer_observation_ref={obs.public_ref}"
    ).json()
    refs = {r["calculation"]["calculation_ref"] for r in body["records"]}
    assert refs == {calc.public_ref}


def test_search_by_created_after_before(client, db_session):
    from datetime import datetime, timezone, timedelta

    _, _, calc = _make_species_owned_calc(db_session)
    a = attach_artifact(db_session, calculation=calc)
    cutoff = (a.created_at - timedelta(seconds=1)).isoformat()
    after_cutoff = (a.created_at + timedelta(seconds=1)).isoformat()
    db_session.flush()
    body_after = client.get(
        SEARCH_URL + f"?artifact_kind=output_log&created_after={cutoff}"
    ).json()
    refs = {r["calculation"]["calculation_ref"] for r in body_after["records"]}
    assert calc.public_ref in refs
    body_before = client.get(
        SEARCH_URL
        + f"?artifact_kind=output_log&created_before={after_cutoff}"
    ).json()
    refs = {
        r["calculation"]["calculation_ref"] for r in body_before["records"]
    }
    assert calc.public_ref in refs


# ---------------------------------------------------------------------------
# Review/trust gating (owning calc)
# ---------------------------------------------------------------------------


def test_default_hides_rejected_owner(client, db_session):
    _, _, ok_calc = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=ok_calc)
    _, _, rej_calc = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=rej_calc)
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=rej_calc.id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(SEARCH_URL + "?artifact_kind=output_log").json()
    refs = {r["calculation"]["calculation_ref"] for r in body["records"]}
    assert ok_calc.public_ref in refs
    assert rej_calc.public_ref not in refs


def test_include_rejected_restores_them(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc)
    set_review(
        db_session,
        record_type=SubmissionRecordType.calculation,
        record_id=calc.id,
        status=RecordReviewStatus.rejected,
    )
    body = client.get(
        SEARCH_URL + "?artifact_kind=output_log&include_rejected=true"
    ).json()
    refs = {r["calculation"]["calculation_ref"] for r in body["records"]}
    assert calc.public_ref in refs


# ---------------------------------------------------------------------------
# Ordering, pagination, GET/POST parity
# ---------------------------------------------------------------------------


def test_pagination_envelope(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    for i in range(3):
        attach_artifact(
            db_session, calculation=calc, filename=f"out_{i}.log"
        )
    body = client.get(
        SEARCH_URL + "?artifact_kind=output_log&offset=0&limit=2"
    ).json()
    p = body["pagination"]
    assert p["offset"] == 0
    assert p["limit"] == 2
    assert p["returned"] <= 2
    assert p["total"] >= 3


def test_deterministic_ordering(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    a1 = attach_artifact(db_session, calculation=calc, filename="a.log")
    a2 = attach_artifact(db_session, calculation=calc, filename="b.log")
    a3 = attach_artifact(db_session, calculation=calc, filename="c.log")
    body1 = client.get(SEARCH_URL + "?calculation_ref=" + calc.public_ref).json()
    body2 = client.get(SEARCH_URL + "?calculation_ref=" + calc.public_ref).json()
    fn1 = [r["artifact"]["filename"] for r in body1["records"]]
    fn2 = [r["artifact"]["filename"] for r in body2["records"]]
    assert fn1 == fn2
    assert set(fn1) == {"a.log", "b.log", "c.log"}


def test_post_search_returns_same_records_as_get(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc)
    body_get = client.get(SEARCH_URL + "?artifact_kind=output_log").json()
    body_post = client.post(
        SEARCH_URL, json={"artifact_kind": "output_log"}
    ).json()
    assert body_get["records"] == body_post["records"]
    assert body_get["pagination"] == body_post["pagination"]


def test_post_search_rejects_query_string_search_fields(client, db_session):
    resp = client.post(
        SEARCH_URL + "?artifact_kind=output_log",
        json={"artifact_kind": "output_log"},
    )
    assert resp.status_code == 422
    assert "post_search_fields_must_be_in_body" in resp.text


# ---------------------------------------------------------------------------
# Include behavior
# ---------------------------------------------------------------------------


def test_include_calculation_populates_lot_software_workflow(
    client, db_session
):
    lot = make_lot(db_session, method="b3lyp", basis="def2tzvp")
    species = make_species(
        db_session, smiles="CCO", inchi_key=next_inchi_key("INC")
    )
    entry = make_species_entry(db_session, species)
    calc = make_calculation(
        db_session, species_entry_id=entry.id, lot_id=lot.id
    )
    attach_artifact(db_session, calculation=calc)
    body = client.get(
        SEARCH_URL
        + f"?calculation_ref={calc.public_ref}&include=calculation"
    ).json()
    assert body["records"], body
    rec = body["records"][0]
    assert rec["calculation"]["level_of_theory"]["method"] == "b3lyp"


def test_default_include_set_calculation_context_present(client, db_session):
    lot = make_lot(db_session, method="hf", basis="sto-3g")
    species = make_species(
        db_session, smiles="O", inchi_key=next_inchi_key("DFL")
    )
    entry = make_species_entry(db_session, species)
    calc = make_calculation(
        db_session, species_entry_id=entry.id, lot_id=lot.id
    )
    attach_artifact(db_session, calculation=calc)
    body = client.get(
        SEARCH_URL + f"?calculation_ref={calc.public_ref}"
    ).json()
    rec = body["records"][0]
    assert rec["calculation"]["calculation_ref"] == calc.public_ref


def test_include_owner_populates_owner_block(client, db_session):
    _, entry, calc = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc)
    body = client.get(
        SEARCH_URL + f"?calculation_ref={calc.public_ref}&include=owner"
    ).json()
    rec = body["records"][0]
    assert rec["owner"] is not None
    assert rec["owner"]["kind"] == "species_entry"
    assert rec["owner"]["species_entry"]["species_entry_ref"] == entry.public_ref


def test_default_omits_owner(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc)
    body = client.get(
        SEARCH_URL + f"?calculation_ref={calc.public_ref}"
    ).json()
    rec = body["records"][0]
    assert rec.get("owner") is None


def test_include_all_expands_public_tokens(client, db_session):
    _, entry, calc = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc)
    body = client.get(
        SEARCH_URL + f"?calculation_ref={calc.public_ref}&include=all"
    ).json()
    rec = body["records"][0]
    # ``all`` should expand to owner + calculation + review.
    assert rec["owner"] is not None
    assert set(body["request"]["include"]) >= {
        "calculation",
        "owner",
        "review",
    }
    assert "internal_ids" not in body["request"]["include"]


def test_include_all_does_not_restore_internal_ids(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc)
    body = client.get(
        SEARCH_URL + f"?calculation_ref={calc.public_ref}&include=all"
    ).json()
    rec = body["records"][0]
    assert "artifact_id" not in rec["artifact"]
    assert "calculation_id" not in rec["calculation"]


def test_unknown_include_token_rejected(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc)
    resp = client.get(
        SEARCH_URL
        + f"?calculation_ref={calc.public_ref}&include=bogus_token"
    )
    assert resp.status_code == 422
    assert "unknown_include_token" in resp.text


# ---------------------------------------------------------------------------
# Internal-IDs visibility
# ---------------------------------------------------------------------------


def test_default_hides_internal_ids(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc)
    body = client.get(
        SEARCH_URL + f"?calculation_ref={calc.public_ref}"
    ).json()
    rec = body["records"][0]
    assert "artifact_id" not in rec["artifact"]
    assert "calculation_id" not in rec["calculation"]


def test_internal_ids_when_allowed(client, db_session, allow_internal_ids):
    _, _, calc = _make_species_owned_calc(db_session)
    art = attach_artifact(db_session, calculation=calc)
    body = client.get(
        SEARCH_URL
        + f"?calculation_ref={calc.public_ref}&include=internal_ids"
    ).json()
    rec = body["records"][0]
    assert rec["artifact"]["artifact_id"] == art.id
    assert rec["calculation"]["calculation_id"] == calc.id


# ---------------------------------------------------------------------------
# Parity with calculation include=artifacts
# ---------------------------------------------------------------------------


def test_artifact_summary_matches_calculation_detail_include_artifacts(
    client, db_session
):
    _, _, calc = _make_species_owned_calc(db_session)
    art = attach_artifact(db_session, calculation=calc, filename="parity.log")
    art.sha256 = "d" * 64
    art.bytes = 4242
    db_session.flush()

    # Standalone artifact search
    body_search = client.get(
        SEARCH_URL + f"?calculation_ref={calc.public_ref}"
    ).json()
    summary_search = body_search["records"][0]["artifact"]

    # Calculation detail include=artifacts
    body_detail = client.get(
        f"/api/v1/scientific/calculations/{calc.public_ref}?include=artifacts"
    ).json()
    summary_detail = body_detail["record"]["artifacts"][0]

    # Both surfaces drop artifact_id by default; everything else should
    # match exactly.
    assert summary_search == summary_detail


# ---------------------------------------------------------------------------
# Payload safety: forbidden keys must never appear, recursively.
# ---------------------------------------------------------------------------


_FORBIDDEN_KEYS = frozenset(
    {
        "body",
        "content",
        "data",
        "presigned_url",
        "download_url",
        "signed_url",
        "url_for_download",
        "xyz_text",
        "atoms",
        "coords",
    }
)


def _walk_forbidden(value, path="$"):
    """Recursively assert no forbidden key exists in *value*."""
    if isinstance(value, dict):
        for k, v in value.items():
            assert k not in _FORBIDDEN_KEYS, (
                f"forbidden key {k!r} appeared at {path}"
            )
            _walk_forbidden(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _walk_forbidden(item, f"{path}[{i}]")


def test_payload_has_no_forbidden_keys_default(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc)
    body = client.get(
        SEARCH_URL + f"?calculation_ref={calc.public_ref}"
    ).json()
    _walk_forbidden(body)


def test_payload_has_no_forbidden_keys_with_include_all(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc)
    body = client.get(
        SEARCH_URL + f"?calculation_ref={calc.public_ref}&include=all"
    ).json()
    _walk_forbidden(body)


# ---------------------------------------------------------------------------
# Ref handle errors
# ---------------------------------------------------------------------------


def test_calculation_ref_wrong_prefix_returns_422(client, db_session):
    resp = client.get(SEARCH_URL + "?calculation_ref=spe_abc123")
    assert resp.status_code == 422
    assert "handle_type_mismatch" in resp.text


def test_calculation_ref_malformed_returns_422(client, db_session):
    resp = client.get(SEARCH_URL + "?calculation_ref=not-a-ref")
    assert resp.status_code == 422
    assert "invalid_handle" in resp.text


def test_unknown_calculation_ref_returns_empty(client, db_session):
    _, _, calc = _make_species_owned_calc(db_session)
    attach_artifact(db_session, calculation=calc)
    # Well-formed but non-existent (correct prefix). Use a body that
    # cannot collide with a real ref.
    body = client.get(
        SEARCH_URL + "?calculation_ref=calc_zzzzzzzzzzzzzzzzzzzzzzzzzz"
    ).json()
    assert body["records"] == []
    assert body["pagination"]["total"] == 0
