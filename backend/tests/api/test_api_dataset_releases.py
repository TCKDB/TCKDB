"""The Stage 3 exit criterion, end to end over HTTP.

    "A user can cite and reproduce the exact selected dataset while still
     retrieving all underlying candidates and review history."

`test_cite_and_reproduce_a_release_end_to_end` is that sentence, executed. It
walks the whole path a reader with nothing but a release tag would walk:
resolve the tag, read the manifest, verify the manifest digest by hand,
download each artifact, check its SHA-256 against the manifest, and confirm the
unselected candidates and the review history are in there too.

The rest of the file pins the curation lifecycle around it.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.api.config import settings
from app.db.models.app_user import AppUser
from app.db.models.common import RecordReviewStatus, SubmissionRecordType
from app.services.record_review import set_record_review_status
from tests.services.scientific_read._factories import (
    make_species,
    make_species_entry,
    make_thermo_scalar,
)

POLICY = {
    "name": "tckdb-benchmark",
    "version": "1.0",
    "description": "Prefer the highest-level composite single point with a converged frequency calculation at the same level.",
    "criteria": {"requires_review_status": "approved"},
}

RELEASE = {
    "tag": "2026.07.0",
    "title": "TCKDB curated thermochemistry, July 2026",
    "curation_policy_name": POLICY["name"],
    "curation_policy_version": POLICY["version"],
    "data_license": "CC-BY-4.0",
    "code_license": "MIT",
    "citation_text": "TCKDB curated dataset release 2026.07.0.",
    "contact": "tckdb-maintainers@example.org",
    "changelog_entry": "First curated release.",
}


@pytest.fixture
def corpus(db_session, _api_curator_user):
    """One species entry, two approved thermo candidates, a real choice."""
    curator = db_session.get(AppUser, _api_curator_user)
    entry = make_species_entry(
        db_session, species=make_species(db_session, smiles="CCO")
    )
    chosen = make_thermo_scalar(
        db_session, species_entry=entry, h298_kj_mol=-234.5, s298_j_mol_k=281.6
    )
    other = make_thermo_scalar(
        db_session, species_entry=entry, h298_kj_mol=-228.1, s298_j_mol_k=277.4
    )
    for row in (chosen, other):
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=row.id,
            status=RecordReviewStatus.approved,
            actor=curator,
        )
    db_session.flush()
    return entry, chosen, other


def _as_curator(client, login_as, curator_id):
    login_as(curator_id)
    return client


def _publish(client, corpus, *, rationale="CCSD(T)-F12 composite; frequencies all real."):
    _entry, chosen, _other = corpus
    assert client.post("/api/v1/releases/policies", json=POLICY).status_code == 201
    assert client.post("/api/v1/releases", json=RELEASE).status_code == 201
    created = client.post(
        f"/api/v1/releases/{RELEASE['tag']}/selections",
        json={"record_ref": chosen.public_ref, "rationale": rationale},
    )
    assert created.status_code == 201, created.text
    published = client.post(f"/api/v1/releases/{RELEASE['tag']}/publish")
    assert published.status_code == 200, published.text
    return created.json()


# ---------------------------------------------------------------------------
# The exit criterion
# ---------------------------------------------------------------------------


def test_cite_and_reproduce_a_release_end_to_end(
    client, login_as, _api_curator_user, corpus
):
    entry, chosen, other = corpus
    _as_curator(client, login_as, _api_curator_user)
    _publish(client, corpus)

    # --- 1. A reader has only the citable tag. -----------------------------
    detail = client.get(f"/api/v1/scientific/releases/{RELEASE['tag']}")
    assert detail.status_code == 200, detail.text
    record = detail.json()["record"]
    assert record["status"] == "published"
    assert record["citation_text"] == RELEASE["citation_text"]
    assert record["data_license"] == "CC-BY-4.0"
    assert record["code_license"] == "MIT"
    assert record["contact"] == RELEASE["contact"]
    assert record["doi"] is None, "no DOI is minted by the implementation"

    # --- 2. The manifest, and the server's own verification. ---------------
    manifest_response = client.get(
        f"/api/v1/scientific/releases/{RELEASE['tag']}/manifest"
    )
    assert manifest_response.status_code == 200, manifest_response.text
    body = manifest_response.json()
    assert body["verification"]["verified"] is True, body["verification"]["problems"]

    manifest = body["manifest"]
    document = manifest["document"]
    assert manifest["manifest_schema"] == "tckdb.dataset_release.v1"
    assert manifest["profile"] == "curated"

    # Version binding — what a reproducer needs to know.
    versions = manifest["versions"]
    assert versions["alembic_revision"]
    assert versions["review_policy_version"] == "record_review.v1"
    assert versions["recovery_archive_schema"] == "tckdb.archive.v1"
    assert set(versions) == {
        "alembic_revision",
        "backend_version",
        "schemas_package_version",
        "review_policy_version",
        "recovery_archive_schema",
    }

    # --- 3. Recompute the manifest digest independently. -------------------
    canonical = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert (
        hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        == manifest["content_sha256"]
    )

    # --- 4. Download every artifact and check its checksum. ----------------
    downloaded: dict[str, bytes] = {}
    for artifact in manifest["artifacts"]:
        response = client.get(artifact["download_url"])
        assert response.status_code == 200, (artifact["path"], response.text)
        assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
        assert len(response.content) == artifact["byte_count"]
        assert response.headers["X-TCKDB-Content-SHA256"] == artifact["sha256"]
        downloaded[artifact["path"]] = response.content

    assert set(downloaded) == {
        "selected_records.ndjson",
        "candidate_records.ndjson",
        "review_history.ndjson",
        "selection_ledger.ndjson",
    }

    def lines(path):
        return [
            json.loads(line) for line in downloaded[path].decode().splitlines() if line
        ]

    # --- 5. The selected dataset is exactly reproducible. ------------------
    selected = lines("selected_records.ndjson")
    assert len(selected) == 1
    assert selected[0]["record_ref"] == chosen.public_ref
    assert selected[0]["subject_ref"] == entry.public_ref
    assert selected[0]["record"]["h298_kj_mol"] == pytest.approx(-234.5)
    assert selected[0]["selection"]["curation_policy"]["version"] == "1.0"
    assert selected[0]["selection"]["curator"]["username"] == "testcurator"
    assert "CCSD(T)-F12" in selected[0]["selection"]["rationale"]

    # --- 6. …while all underlying candidates are still there. --------------
    candidates = {row["record_ref"]: row for row in lines("candidate_records.ndjson")}
    assert {chosen.public_ref, other.public_ref} <= set(candidates)
    assert candidates[chosen.public_ref]["selected_in_release"] is True
    assert candidates[other.public_ref]["selected_in_release"] is False
    assert candidates[other.public_ref]["record"]["h298_kj_mol"] == pytest.approx(-228.1)

    # --- 7. …and so is the review history behind them. ---------------------
    history = {row["record_ref"]: row for row in lines("review_history.ndjson")}
    for ref in (chosen.public_ref, other.public_ref):
        assert history[ref]["review"]["status"] == "approved"
        assert history[ref]["events"], "review events must ship with the release"

    # --- 8. The ledger explains how the decision was reached. --------------
    ledger = lines("selection_ledger.ndjson")
    assert len(ledger) == 1
    assert ledger[0]["action"] == "select"
    assert ledger[0]["stands"] is True


def test_release_states_it_is_not_a_backup(client, login_as, _api_curator_user, corpus):
    _as_curator(client, login_as, _api_curator_user)
    _publish(client, corpus)
    document = client.get(
        f"/api/v1/scientific/releases/{RELEASE['tag']}/manifest"
    ).json()["manifest"]["document"]
    contract = document["contract"]
    assert contract["kind"] == "curated_dataset_release"
    assert contract["lossless_database_backup"] is False
    assert contract["distinct_from"]["recovery_archive"] == "tckdb.archive.v1"
    assert contract["distinct_from"]["convenience_projection"] == "tckdb.export.v0"


def test_release_endpoints_echo_the_read_profile(
    client, login_as, _api_curator_user, corpus
):
    _as_curator(client, login_as, _api_curator_user)
    _publish(client, corpus)
    for path in (
        "/api/v1/scientific/releases",
        f"/api/v1/scientific/releases/{RELEASE['tag']}",
        f"/api/v1/scientific/releases/{RELEASE['tag']}/manifest",
        f"/api/v1/scientific/releases/{RELEASE['tag']}/selections",
    ):
        body = client.get(f"{path}?profile=curated").json()
        assert body["request"]["profile"] == "curated", path


def test_a_published_release_is_discoverable_by_ref_and_by_tag(
    client, login_as, _api_curator_user, corpus
):
    _as_curator(client, login_as, _api_curator_user)
    _publish(client, corpus)
    by_tag = client.get(f"/api/v1/scientific/releases/{RELEASE['tag']}").json()
    ref = by_tag["record"]["release_ref"]
    by_ref = client.get(f"/api/v1/scientific/releases/{ref}").json()
    assert by_ref["record"]["tag"] == RELEASE["tag"]


def test_curated_product_reads_never_claim_release_backing(
    client, login_as, _api_curator_user, corpus
):
    """Publishing a release must not make every curated response claim backing.

    The endorsement was resolved per *database* — newest published release —
    not per record, so once any release existed every curated read asserted
    ``tckdb_curated_release``, including for the candidate this very release
    declined to select.
    """
    entry, _chosen, other = corpus
    _as_curator(client, login_as, _api_curator_user)
    _publish(client, corpus)

    body = client.get(
        f"/api/v1/scientific/species-entries/{entry.public_ref}/thermo?profile=curated"
    ).json()
    echo = body["request"]
    assert echo["profile"] == "curated"
    assert echo["profile_recommendation"] == "approved_floor_only"
    assert echo["profile_release_ref"] is None

    # The unselected candidate is still returned (it is approved) — which is
    # exactly why claiming release backing here would have been false.
    assert other.public_ref in {r["thermo_ref"] for r in body["records"]}

    # Exploratory still disclaims everything.
    plain = client.get(
        f"/api/v1/scientific/species-entries/{entry.public_ref}/thermo"
    ).json()["request"]
    assert plain["profile_recommendation"] == "none"


def test_only_release_endpoints_claim_release_backing(
    client, login_as, _api_curator_user, corpus
):
    """The endorsement lives where records are resolved *through* a selection."""
    _as_curator(client, login_as, _api_curator_user)
    _publish(client, corpus)

    for path in (
        f"/api/v1/scientific/releases/{RELEASE['tag']}",
        f"/api/v1/scientific/releases/{RELEASE['tag']}/manifest",
        f"/api/v1/scientific/releases/{RELEASE['tag']}/selections",
    ):
        echo = client.get(path).json()["request"]
        assert echo["profile_recommendation"] == "tckdb_curated_release", path
        assert echo["profile_release_ref"].startswith("rel_"), path


def test_release_scoping_on_general_reads_is_refused_not_ignored(
    client, login_as, _api_curator_user, corpus
):
    entry, _chosen, _other = corpus
    _as_curator(client, login_as, _api_curator_user)
    _publish(client, corpus)
    response = client.get(
        f"/api/v1/scientific/species-entries/{entry.public_ref}/thermo"
        f"?profile=curated&release={RELEASE['tag']}"
    )
    assert response.status_code == 422
    assert "release_scoping_not_implemented" in response.text


# ---------------------------------------------------------------------------
# Supersession over HTTP
# ---------------------------------------------------------------------------


def test_supersede_appends_and_the_ledger_keeps_both(
    client, login_as, _api_curator_user, _api_admin_user, corpus
):
    _entry, chosen, other = corpus
    _as_curator(client, login_as, _api_curator_user)
    assert client.post("/api/v1/releases/policies", json=POLICY).status_code == 201
    assert client.post("/api/v1/releases", json=RELEASE).status_code == 201
    first = client.post(
        f"/api/v1/releases/{RELEASE['tag']}/selections",
        json={"record_ref": chosen.public_ref, "rationale": "Initial pick."},
    ).json()

    # A different curator changes the recommendation.
    login_as(_api_admin_user)
    replacement = client.post(
        f"/api/v1/releases/{RELEASE['tag']}/selections/{first['selection_ref']}/supersede",
        json={
            "record_ref": other.public_ref,
            "rationale": "Superseded: the earlier candidate used unscaled frequencies.",
        },
    )
    assert replacement.status_code == 201, replacement.text

    ledger = client.get(
        f"/api/v1/scientific/releases/{RELEASE['tag']}/selections"
    ).json()["records"]
    assert len(ledger) == 2, "superseding must append a row"

    original = next(r for r in ledger if r["selection_ref"] == first["selection_ref"])
    assert original["rationale"] == "Initial pick.", "the original wording is preserved"
    assert original["stands"] is False
    assert original["record_ref"] == chosen.public_ref

    new = next(r for r in ledger if r["selection_ref"] != first["selection_ref"])
    assert new["action"] == "supersede"
    assert new["stands"] is True
    assert new["supersedes_selection_ref"] == first["selection_ref"]
    assert new["record_ref"] == other.public_ref
    assert new["curator"]["username"] == "testadmin"


def test_there_is_no_way_to_edit_or_delete_a_selection(client):
    """Not "we chose not to expose it" — the routes do not exist."""
    from app.api.app import create_app

    app = create_app()
    mutating = [
        (method, route.path)
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/v1/releases")
        for method in getattr(route, "methods", set())
        if method in {"PATCH", "PUT", "DELETE"}
    ]
    assert mutating == []


def test_superseded_selections_are_returned_by_default(
    client, login_as, _api_curator_user, corpus
):
    _entry, chosen, other = corpus
    _as_curator(client, login_as, _api_curator_user)
    client.post("/api/v1/releases/policies", json=POLICY)
    client.post("/api/v1/releases", json=RELEASE)
    first = client.post(
        f"/api/v1/releases/{RELEASE['tag']}/selections",
        json={"record_ref": chosen.public_ref, "rationale": "Initial."},
    ).json()
    client.post(
        f"/api/v1/releases/{RELEASE['tag']}/selections/{first['selection_ref']}/supersede",
        json={"record_ref": other.public_ref, "rationale": "Revised."},
    )

    default = client.get(
        f"/api/v1/scientific/releases/{RELEASE['tag']}/selections"
    ).json()
    assert default["pagination"]["total"] == 2

    standing_only = client.get(
        f"/api/v1/scientific/releases/{RELEASE['tag']}/selections"
        "?include_superseded=false"
    ).json()
    assert standing_only["pagination"]["total"] == 1


# ---------------------------------------------------------------------------
# Lifecycle and authorization
# ---------------------------------------------------------------------------


def test_release_writes_require_a_curator(client, corpus):
    """The default test client is a plain user."""
    assert client.post("/api/v1/releases/policies", json=POLICY).status_code == 403


def test_public_release_reads_need_no_authentication(
    client, login_as, _api_curator_user, corpus
):
    """A citation must resolve for anyone, not just a logged-in curator."""
    _as_curator(client, login_as, _api_curator_user)
    _publish(client, corpus)

    from fastapi import HTTPException

    from app.api.deps import get_current_user

    def _anonymous():
        raise HTTPException(status_code=401, detail="Authentication required.")

    client.app.dependency_overrides[get_current_user] = _anonymous
    for path in (
        "/api/v1/scientific/releases",
        f"/api/v1/scientific/releases/{RELEASE['tag']}",
        f"/api/v1/scientific/releases/{RELEASE['tag']}/manifest",
        f"/api/v1/scientific/releases/{RELEASE['tag']}/selections",
        f"/api/v1/scientific/releases/{RELEASE['tag']}/artifacts/selected_records.ndjson",
    ):
        assert client.get(path).status_code == 200, path


def test_a_draft_release_has_no_citable_manifest(
    client, login_as, _api_curator_user, corpus
):
    _entry, chosen, _other = corpus
    _as_curator(client, login_as, _api_curator_user)
    client.post("/api/v1/releases/policies", json=POLICY)
    client.post("/api/v1/releases", json=RELEASE)
    client.post(
        f"/api/v1/releases/{RELEASE['tag']}/selections",
        json={"record_ref": chosen.public_ref, "rationale": "Draft pick."},
    )
    response = client.get(f"/api/v1/scientific/releases/{RELEASE['tag']}/manifest")
    assert response.status_code == 404
    assert "manifest_not_frozen" in response.text


def test_withdrawn_release_stays_resolvable(
    client, login_as, _api_curator_user, corpus
):
    """A citation must never dangle."""
    _as_curator(client, login_as, _api_curator_user)
    _publish(client, corpus)
    withdrawn = client.post(
        f"/api/v1/releases/{RELEASE['tag']}/withdraw",
        json={"reason": "Systematic error in the applied AEC scheme."},
    )
    assert withdrawn.status_code == 200, withdrawn.text

    detail = client.get(f"/api/v1/scientific/releases/{RELEASE['tag']}").json()["record"]
    assert detail["status"] == "withdrawn"
    assert detail["withdrawn_reason"] == "Systematic error in the applied AEC scheme."
    # Still listed, so a reader holding the old citation can find out.
    listed = client.get("/api/v1/scientific/releases").json()["records"]
    assert RELEASE["tag"] in {r["tag"] for r in listed}
    # And the manifest still resolves.
    assert (
        client.get(f"/api/v1/scientific/releases/{RELEASE['tag']}/manifest").status_code
        == 200
    )


def test_doi_is_recorded_not_minted(client, login_as, _api_curator_user, corpus):
    _as_curator(client, login_as, _api_curator_user)
    _publish(client, corpus)
    assert (
        client.get(f"/api/v1/scientific/releases/{RELEASE['tag']}").json()["record"][
            "doi"
        ]
        is None
    )
    attached = client.post(
        f"/api/v1/releases/{RELEASE['tag']}/doi", json={"doi": "10.5281/zenodo.1"}
    )
    assert attached.status_code == 200, attached.text
    assert attached.json()["doi"] == "10.5281/zenodo.1"

    # 409 since #211, not 422: the body is well formed and the release
    # already cites a DOI. No corrected payload repoints a citation, so a
    # client must not be told to fix and resend.
    repointed = client.post(
        f"/api/v1/releases/{RELEASE['tag']}/doi", json={"doi": "10.5281/zenodo.2"}
    )
    assert repointed.status_code == 409, repointed.text
    assert repointed.json()["code"] == "doi_already_recorded", repointed.text


def test_unknown_release_is_404_not_422(client):
    assert client.get("/api/v1/scientific/releases/9999.99.9").status_code == 404


def test_unknown_artifact_path_is_404(client, login_as, _api_curator_user, corpus):
    _as_curator(client, login_as, _api_curator_user)
    _publish(client, corpus)
    response = client.get(
        f"/api/v1/scientific/releases/{RELEASE['tag']}/artifacts/nope.ndjson"
    )
    assert response.status_code == 404
    assert "unknown_release_artifact" in response.text


def test_a_non_selectable_record_ref_is_refused(
    client, login_as, _api_curator_user, corpus, db_session
):
    _as_curator(client, login_as, _api_curator_user)
    client.post("/api/v1/releases/policies", json=POLICY)
    client.post("/api/v1/releases", json=RELEASE)
    response = client.post(
        f"/api/v1/releases/{RELEASE['tag']}/selections",
        json={
            "record_ref": "spe_0123456789abcdefghijklmnop",
            "rationale": "A species entry is not a scientific product value.",
        },
    )
    assert response.status_code == 422
    assert "record_ref_not_selectable" in response.text


# ---------------------------------------------------------------------------
# The two refusals that arrive at 409, and why the status is the assertion
# ---------------------------------------------------------------------------
#
# Both used to raise a bare ``ReleaseCurationError``, which subclasses
# ``ValueError`` -- so reading the raise site said 422, and the catalogue
# recorded 422 for both, while the routes that reach them wrapped the error
# in ``HTTPException(409)``. Since #211 they raise ``ReleaseStateConflict``
# and the route reads the status off the class, so the raise site and the
# wire can no longer disagree: in both cases the tag or the policy version
# *already exists*, and the caller is colliding with state rather than
# sending a malformed payload. Nothing in the suite emitted either code, so
# the runtime observer had never seen the disagreement, and it was comparing
# codes rather than ``(status, code)`` pairs and could not have reported it
# even if it had. These two tests are what make that guard load-bearing:
# after them, a status regression on either code fails here *and* in the
# observer's teardown hook.


def test_a_repeated_release_tag_is_a_409(client, login_as, _api_curator_user):
    """409, not 422: the write collides with a release that already exists.

    The distinction is the client's retry advice. 422 invites resending a
    corrected payload; 409 says the tag is spoken for and a different one
    is needed.
    """
    _as_curator(client, login_as, _api_curator_user)
    assert client.post("/api/v1/releases/policies", json=POLICY).status_code == 201
    assert client.post("/api/v1/releases", json=RELEASE).status_code == 201

    again = client.post("/api/v1/releases", json=RELEASE)
    assert again.status_code == 409, again.text
    assert again.json()["code"] == "release_tag_taken"


def test_re_registering_a_policy_version_with_new_content_is_a_409(
    client, login_as, _api_curator_user
):
    """Registering ``(name, version)`` twice is idempotent only if it agrees.

    A released dataset cites a policy *version*, so the same version may not
    change content underneath it. Re-posting the identical body is accepted
    and returns the existing row; changing the description is refused.
    """
    _as_curator(client, login_as, _api_curator_user)
    assert client.post("/api/v1/releases/policies", json=POLICY).status_code == 201
    assert client.post("/api/v1/releases/policies", json=POLICY).status_code == 201

    edited = dict(POLICY, description="Quietly different criteria prose.")
    response = client.post("/api/v1/releases/policies", json=edited)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "curation_policy_version_conflict"


# ---------------------------------------------------------------------------
# A published release survives everything that happens next
# ---------------------------------------------------------------------------


def _download_all(client, manifest) -> dict[str, bytes]:
    out = {}
    for artifact in manifest["artifacts"]:
        response = client.get(artifact["download_url"])
        assert response.status_code == 200, (artifact["path"], response.text)
        out[artifact["path"]] = response.content
    return out


def test_ordinary_upload_after_publication_does_not_break_the_citation(
    client, login_as, _api_curator_user, corpus, db_session
):
    """The blocking finding, reproduced over HTTP and then fixed.

    Publish, verify clean, then perform ONE ordinary write — a third thermo for
    a released species — and nothing else. Previously:
    ``candidate_records.ndjson`` → 409 ``release_artifact_drift``,
    ``review_history.ndjson`` → 409, manifest → ``verified: false``. The citable
    window was the interval between publishing and the next write anywhere near
    the release, which on a live corpus is approximately zero.
    """
    entry, _chosen, _other = corpus
    _as_curator(client, login_as, _api_curator_user)
    _publish(client, corpus)

    before = client.get(
        f"/api/v1/scientific/releases/{RELEASE['tag']}/manifest"
    ).json()
    assert before["verification"]["verified"] is True
    published_bytes = _download_all(client, before["manifest"])

    # --- one ordinary upload ----------------------------------------------
    make_thermo_scalar(
        db_session, species_entry=entry, h298_kj_mol=-231.7, s298_j_mol_k=279.9
    )
    db_session.flush()

    after = client.get(f"/api/v1/scientific/releases/{RELEASE['tag']}/manifest").json()

    # Integrity holds and the digest is unchanged.
    assert after["verification"]["verified"] is True, after["verification"]["problems"]
    assert (
        after["manifest"]["content_sha256"] == before["manifest"]["content_sha256"]
    )

    # Every artifact still downloads, byte-identically.
    assert _download_all(client, after["manifest"]) == published_bytes

    # And the growth is reported as information, not as breakage.
    divergence = after["live_divergence"]
    assert divergence["diverged"] is True
    assert any("candidate_records" in d for d in divergence["differences"])
    assert "snapshot" in divergence["note"]


def test_review_progressing_after_publication_does_not_break_the_citation(
    client, login_as, _api_curator_user, corpus, db_session
):
    """The other everyday write that used to un-cite a release."""
    entry, _chosen, _other = corpus
    _as_curator(client, login_as, _api_curator_user)
    _publish(client, corpus)
    before = client.get(f"/api/v1/scientific/releases/{RELEASE['tag']}/manifest").json()
    published_bytes = _download_all(client, before["manifest"])

    extra = make_thermo_scalar(
        db_session, species_entry=entry, h298_kj_mol=-230.0, s298_j_mol_k=278.0
    )
    set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=extra.id,
        status=RecordReviewStatus.approved,
        actor=db_session.get(AppUser, _api_curator_user),
    )
    db_session.flush()

    after = client.get(f"/api/v1/scientific/releases/{RELEASE['tag']}/manifest").json()
    assert after["verification"]["verified"] is True
    assert _download_all(client, after["manifest"]) == published_bytes


def test_attaching_the_doi_does_not_break_the_digest(
    client, login_as, _api_curator_user, corpus
):
    """The runbook's own final step used to break verification permanently.

    Every genuinely cited release has a DOI, so the steady state of every real
    release was ``verified: false``.
    """
    _as_curator(client, login_as, _api_curator_user)
    _publish(client, corpus)
    before = client.get(f"/api/v1/scientific/releases/{RELEASE['tag']}/manifest").json()

    attached = client.post(
        f"/api/v1/releases/{RELEASE['tag']}/doi", json={"doi": "10.5281/zenodo.1234567"}
    )
    assert attached.status_code == 200, attached.text

    after = client.get(f"/api/v1/scientific/releases/{RELEASE['tag']}/manifest").json()
    assert after["verification"]["verified"] is True, after["verification"]["problems"]
    assert after["manifest"]["content_sha256"] == before["manifest"]["content_sha256"]
    assert after["manifest"]["document"] == before["manifest"]["document"]
    # The DOI is visible on the release; the frozen document reports the
    # publication-time value, which is none.
    assert (
        client.get(f"/api/v1/scientific/releases/{RELEASE['tag']}").json()["record"][
            "doi"
        ]
        == "10.5281/zenodo.1234567"
    )
    assert after["manifest"]["document"]["release"]["doi_at_publication"] is None


def test_withdrawing_does_not_break_the_digest(
    client, login_as, _api_curator_user, corpus
):
    _as_curator(client, login_as, _api_curator_user)
    _publish(client, corpus)
    before = client.get(f"/api/v1/scientific/releases/{RELEASE['tag']}/manifest").json()

    client.post(
        f"/api/v1/releases/{RELEASE['tag']}/withdraw",
        json={"reason": "Systematic AEC error."},
    )
    after = client.get(f"/api/v1/scientific/releases/{RELEASE['tag']}/manifest").json()
    assert after["verification"]["verified"] is True
    assert after["manifest"]["content_sha256"] == before["manifest"]["content_sha256"]


def test_a_release_cannot_select_an_unapproved_record(
    client, login_as, _api_curator_user, corpus, db_session
):
    """A published release must not recommend a record that can still be edited."""
    entry, _chosen, _other = corpus
    _as_curator(client, login_as, _api_curator_user)
    unreviewed = make_thermo_scalar(
        db_session, species_entry=entry, h298_kj_mol=-1.0, s298_j_mol_k=1.0
    )
    db_session.flush()

    client.post("/api/v1/releases/policies", json=POLICY)
    client.post("/api/v1/releases", json=RELEASE)
    response = client.post(
        f"/api/v1/releases/{RELEASE['tag']}/selections",
        json={"record_ref": unreviewed.public_ref, "rationale": "Looks fine."},
    )
    assert response.status_code == 422
    assert "record_not_approved" in response.text


def test_an_empty_release_cannot_be_published(client, login_as, _api_curator_user):
    """A citable, DOI-able release containing nothing is not a release."""
    _as_curator(client, login_as, _api_curator_user)
    client.post("/api/v1/releases/policies", json=POLICY)
    client.post("/api/v1/releases", json=RELEASE)
    response = client.post(f"/api/v1/releases/{RELEASE['tag']}/publish")
    assert response.status_code == 422
    assert "release_selects_nothing" in response.text


def test_selected_record_line_is_interpretable_offline(
    client, login_as, _api_curator_user, corpus
):
    """A deposited file must name the molecule, not just an opaque handle."""
    entry, chosen, _other = corpus
    _as_curator(client, login_as, _api_curator_user)
    _publish(client, corpus)

    body = client.get(f"/api/v1/scientific/releases/{RELEASE['tag']}/manifest").json()
    artifact = next(
        a for a in body["manifest"]["artifacts"] if a["path"] == "selected_records.ndjson"
    )
    line = json.loads(client.get(artifact["download_url"]).text.splitlines()[0])

    assert line["record_ref"] == chosen.public_ref
    assert line["subject"]["species_entry_ref"] == entry.public_ref
    assert line["subject"]["smiles"], "the release must say which molecule this is"
    assert "inchi_key" in line["subject"]
    assert line["record"]["species_entry_ref"] == entry.public_ref
    assert "provenance" in line


# ---------------------------------------------------------------------------
# Curator ergonomics
# ---------------------------------------------------------------------------


def test_a_release_is_not_capped_at_one_page_of_selections(
    client, login_as, _api_curator_user, db_session
):
    """The 201st selection used to 500 and roll itself back.

    The write path read the new row back by paging the ledger at ``limit=200``
    and scanning, so a curator simply could not build a larger release — while
    the read service's own comments assume thousands of selections. This uses a
    small page-size-crossing count rather than 201 real records to stay fast,
    but exercises the same lookup: the read-back is now a direct query.
    """
    from app.services.scientific_read.releases import get_release_selection

    curator = db_session.get(AppUser, _api_curator_user)
    _as_curator(client, login_as, _api_curator_user)
    client.post("/api/v1/releases/policies", json=POLICY)
    client.post("/api/v1/releases", json=RELEASE)

    refs = []
    for index in range(12):
        entry = make_species_entry(
            db_session, species=make_species(db_session, smiles="C" * (index + 1))
        )
        thermo = make_thermo_scalar(
            db_session, species_entry=entry, h298_kj_mol=-10.0 * index, s298_j_mol_k=200.0
        )
        set_record_review_status(
            db_session,
            record_type=SubmissionRecordType.thermo,
            record_id=thermo.id,
            status=RecordReviewStatus.approved,
            actor=curator,
        )
        db_session.flush()
        created = client.post(
            f"/api/v1/releases/{RELEASE['tag']}/selections",
            json={"record_ref": thermo.public_ref, "rationale": f"Pick {index}."},
        )
        assert created.status_code == 201, created.text
        refs.append(created.json()["selection_ref"])

    # Every one is readable by direct lookup, including those past any page
    # boundary a paged read-back would have imposed.
    for ref in refs:
        assert get_release_selection(db_session, RELEASE["tag"], ref) is not None

    ledger = client.get(
        f"/api/v1/scientific/releases/{RELEASE['tag']}/selections?limit=200"
    ).json()
    assert ledger["pagination"]["total"] == len(refs)


def test_superseding_through_the_wrong_release_is_a_404(
    client, login_as, _api_curator_user, corpus, db_session
):
    """Selection sub-routes used to ignore the release in the URL entirely.

    Superseding via the wrong tag resolved the selection globally and appended
    the replacement to the *other* release — silently editing a release the
    caller never named — then 500ed.
    """
    _entry, _chosen, other = corpus
    _as_curator(client, login_as, _api_curator_user)
    _publish(client, corpus)

    selection_ref = client.get(
        f"/api/v1/scientific/releases/{RELEASE['tag']}/selections"
    ).json()["records"][0]["selection_ref"]

    # A second, unrelated draft release.
    second = dict(RELEASE, tag="2026.08.0", title="Second release")
    assert client.post("/api/v1/releases", json=second).status_code == 201

    response = client.post(
        f"/api/v1/releases/2026.08.0/selections/{selection_ref}/supersede",
        json={"record_ref": other.public_ref, "rationale": "Wrong release."},
    )
    assert response.status_code == 404
    assert "unknown_selection" in response.text

    # The other release is untouched.
    assert (
        client.get("/api/v1/scientific/releases/2026.08.0/selections").json()[
            "pagination"
        ]["total"]
        == 0
    )


# ---------------------------------------------------------------------------
# Abuse controls: the release reads page like every other read surface
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "route",
    [
        "/api/v1/scientific/releases",
        f"/api/v1/scientific/releases/{RELEASE['tag']}/selections",
    ],
)
def test_release_reads_refuse_deep_offsets(
    client, login_as, _api_curator_user, corpus, route
):
    """Deep paging is refused here with the same code as everywhere else.

    These two reads used to materialise every row into Python and slice it,
    so ``offset=10001`` returned a cheerful empty 200 while every other read
    surface refused it. Nothing was ever incorrect -- ``limit`` is bounded by
    the route, so no unbounded body was ever returned -- but one family of
    reads answered a question the hosted abuse-control policy says is not
    answerable, and the difference was invisible until somebody paged deeply.

    Both sides of the boundary are asserted. An expected value derived from
    the same setting the guard reads follows that setting wherever it moves;
    pinning the cap itself as *accepted* is what stops this passing if the
    comparison is widened. ``offset`` carries no ``le`` on either route, so
    this is reachable against the configuration TCKDB actually runs.
    """
    _as_curator(client, login_as, _api_curator_user)
    _publish(client, corpus)

    allowed = client.get(f"{route}?offset={settings.public_max_offset}")
    assert allowed.status_code == 200, allowed.text

    refused = client.get(f"{route}?offset={settings.public_max_offset + 1}")
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "offset_too_large"


def test_ledger_page_resolves_a_supersession_outside_the_page(
    client, login_as, _api_curator_user, _api_admin_user, corpus
):
    """``supersedes_selection_ref`` still resolves when the parent is off-page.

    A page is not self-contained: the row a selection supersedes is by
    construction *older*, so on any page but the first it lives behind the
    offset. The ledger used to have the whole release in memory and could not
    get this wrong; paging in SQL is exactly what makes it possible to.
    """
    _entry, chosen, other = corpus
    _as_curator(client, login_as, _api_curator_user)
    assert client.post("/api/v1/releases/policies", json=POLICY).status_code == 201
    assert client.post("/api/v1/releases", json=RELEASE).status_code == 201
    first = client.post(
        f"/api/v1/releases/{RELEASE['tag']}/selections",
        json={"record_ref": chosen.public_ref, "rationale": "Initial pick."},
    ).json()

    login_as(_api_admin_user)
    assert (
        client.post(
            f"/api/v1/releases/{RELEASE['tag']}/selections/{first['selection_ref']}/supersede",
            json={"record_ref": other.public_ref, "rationale": "Rescaled."},
        ).status_code
        == 201
    )

    second_page = client.get(
        f"/api/v1/scientific/releases/{RELEASE['tag']}/selections?offset=1&limit=1"
    ).json()

    assert second_page["pagination"]["total"] == 2
    assert second_page["pagination"]["returned"] == 1
    (row,) = second_page["records"]
    assert row["action"] == "supersede"
    assert row["stands"] is True
    assert row["supersedes_selection_ref"] == first["selection_ref"]
