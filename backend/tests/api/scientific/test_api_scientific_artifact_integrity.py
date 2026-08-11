"""The custody record, readable without a psql prompt (ADR 0014).

``hard_fail_reason`` already reaches clients, so a consumer learns a
calculation is untrustworthy. These tests are about the other half: a
curator being able to find out *what happened* to it, and being able to
tell one incident from the same incident observed forty times.
"""

from __future__ import annotations

import hashlib

from app.db.models.common import (
    ArtifactIntegrityDetectionContext,
    ArtifactIntegrityFinding,
)
from app.services.artifact_integrity import (
    record_integrity_observation,
    record_integrity_verified,
)
from tests.api.scientific.test_api_scientific_artifacts import (
    _make_species_owned_calc,
)
from tests.services.scientific_read._factories import attach_artifact
from tests.services.test_artifact_integrity import _SessionProxy

LIST_URL = "/api/v1/scientific/artifacts/integrity"


class _MuteStoreProbe:
    def head_object(self, **_kwargs):
        raise RuntimeError("no object store in this test")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _record(db_session, sha256, finding, context, **kwargs):
    return record_integrity_observation(
        sha256=sha256,
        finding=finding,
        detected_during=context,
        observed_sha256=(
            None if finding is ArtifactIntegrityFinding.object_missing else "0" * 64
        ),
        session_factory=_SessionProxy(db_session),
        storage_client=_MuteStoreProbe(),
        **kwargs,
    )


def _broken_artifact(db_session, token: str):
    _, _, calculation = _make_species_owned_calc(db_session)
    sha = _digest(token)
    artifact = attach_artifact(
        db_session, calculation=calculation, sha256=sha, filename=f"{token}.log"
    )
    db_session.flush()
    return calculation, artifact, sha


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


def test_a_curator_can_ask_whether_this_has_ever_happened(
    client, db_session, login_as, _api_curator_user
):
    calculation, artifact, sha = _broken_artifact(db_session, "seen-once")
    _record(
        db_session,
        sha,
        ArtifactIntegrityFinding.digest_mismatch,
        ArtifactIntegrityDetectionContext.download,
        artifact_id=artifact.id,
        expected_bytes=artifact.bytes,
    )
    login_as(_api_curator_user)

    response = client.get(LIST_URL, params={"sha256": sha})

    assert response.status_code == 200
    (record,) = response.json()["records"]
    assert record["sha256"] == sha
    assert record["currently_broken"] is True
    assert record["latest"]["finding"] == "digest_mismatch"
    assert record["latest"]["expected_sha256"] == sha
    assert record["latest"]["observed_sha256"] == "0" * 64
    assert record["latest"]["detected_during"] == "download"
    assert record["calculation_refs"] == [calculation.public_ref]
    assert record["filenames"] == ["seen-once.log"]


def test_repeat_detections_are_one_record_with_a_count(
    client, db_session, login_as, _api_curator_user
):
    """Persistence is evidence, but a list of retries is not a list of incidents.

    Every read of a corrupt object appends another row, deliberately. A
    surface that paged raw observations would report one broken artifact
    read forty times as forty findings.
    """
    _calculation, artifact, sha = _broken_artifact(db_session, "read-repeatedly")
    for _ in range(5):
        _record(
            db_session,
            sha,
            ArtifactIntegrityFinding.digest_mismatch,
            ArtifactIntegrityDetectionContext.download,
            artifact_id=artifact.id,
        )
    login_as(_api_curator_user)

    body = client.get(LIST_URL, params={"sha256": sha}).json()

    assert body["pagination"]["total"] == 1
    (record,) = body["records"]
    assert record["observation_count"] == 5
    assert record["break_count"] == 5
    assert record["first_observed_at"] <= record["last_observed_at"]


def test_a_repaired_object_reads_as_clear_but_keeps_its_history(
    client, db_session, login_as, _api_curator_user
):
    """The verdict is the latest observation; the break is still on the record."""
    _calculation, artifact, sha = _broken_artifact(db_session, "broken-then-fixed")
    _record(
        db_session,
        sha,
        ArtifactIntegrityFinding.digest_mismatch,
        ArtifactIntegrityDetectionContext.verification_sweep,
        artifact_id=artifact.id,
    )
    record_integrity_verified(
        sha256=sha,
        detected_during=ArtifactIntegrityDetectionContext.verification_sweep,
        artifact_id=artifact.id,
        session_factory=_SessionProxy(db_session),
        storage_client=_MuteStoreProbe(),
    )
    login_as(_api_curator_user)

    (record,) = client.get(LIST_URL, params={"sha256": sha}).json()["records"]

    assert record["currently_broken"] is False
    assert record["latest"]["finding"] == "verified"
    # The break is not erased by the repair; it is superseded by it.
    assert record["break_count"] == 1
    assert record["observation_count"] == 2


def test_only_currently_broken_reads_the_latest_not_any(
    client, db_session, login_as, _api_curator_user
):
    """"Any break ever" would leave a restored object condemned forever."""
    _calc, repaired_artifact, repaired = _broken_artifact(db_session, "repaired-one")
    _calc2, broken_artifact, broken = _broken_artifact(db_session, "still-broken-one")
    for sha, artifact in ((repaired, repaired_artifact), (broken, broken_artifact)):
        _record(
            db_session,
            sha,
            ArtifactIntegrityFinding.digest_mismatch,
            ArtifactIntegrityDetectionContext.verification_sweep,
            artifact_id=artifact.id,
        )
    record_integrity_verified(
        sha256=repaired,
        detected_during=ArtifactIntegrityDetectionContext.verification_sweep,
        artifact_id=repaired_artifact.id,
        session_factory=_SessionProxy(db_session),
        storage_client=_MuteStoreProbe(),
    )
    login_as(_api_curator_user)

    body = client.get(LIST_URL, params={"only_currently_broken": True}).json()

    digests = {record["sha256"] for record in body["records"]}
    assert broken in digests
    assert repaired not in digests


def test_the_contexts_that_have_looked_are_reported(
    client, db_session, login_as, _api_curator_user
):
    """Coverage is uneven by construction, so which reads ran is the finding."""
    _calculation, artifact, sha = _broken_artifact(db_session, "seen-by-two")
    _record(
        db_session,
        sha,
        ArtifactIntegrityFinding.digest_mismatch,
        ArtifactIntegrityDetectionContext.download,
        artifact_id=artifact.id,
    )
    _record(
        db_session,
        sha,
        ArtifactIntegrityFinding.digest_mismatch,
        ArtifactIntegrityDetectionContext.verification_sweep,
        artifact_id=artifact.id,
    )
    login_as(_api_curator_user)

    (record,) = client.get(LIST_URL, params={"sha256": sha}).json()["records"]

    assert set(record["detected_during"]) == {"download", "verification_sweep"}


def test_a_digest_with_no_artifact_row_is_still_findable(
    client, db_session, login_as, _api_curator_user
):
    """The dedup-refusal case has observations and no row to reach them by.

    ``store_artifact`` verifies an object already at the content-addressed
    key before attaching another row to it, so when that verification
    fails the upload that would have created the row is being refused --
    and if nothing else happens to share the object, the break exists with
    ``artifact_id`` null. Resolving a ``sha256`` filter through
    ``calculation_artifact`` would have hidden exactly that case.
    """
    sha = _digest("no-row-points-here")
    _record(
        db_session,
        sha,
        ArtifactIntegrityFinding.digest_mismatch,
        ArtifactIntegrityDetectionContext.store_dedup_verification,
    )
    login_as(_api_curator_user)

    (record,) = client.get(LIST_URL, params={"sha256": sha}).json()["records"]

    assert record["sha256"] == sha
    assert record["calculation_refs"] == []
    assert record["currently_broken"] is True


def test_filtering_by_calculation_ref_reaches_its_digests(
    client, db_session, login_as, _api_curator_user
):
    calculation, artifact, sha = _broken_artifact(db_session, "by-calculation")
    _record(
        db_session,
        sha,
        ArtifactIntegrityFinding.object_missing,
        ArtifactIntegrityDetectionContext.verification_sweep,
        artifact_id=artifact.id,
    )
    login_as(_api_curator_user)

    body = client.get(
        LIST_URL, params={"calculation_ref": calculation.public_ref}
    ).json()

    assert [record["sha256"] for record in body["records"]] == [sha]
    assert body["records"][0]["latest"]["finding"] == "object_missing"
    # ``object_missing`` retrieved nothing, so there is no observed digest.
    assert body["records"][0]["latest"]["observed_sha256"] is None


# ---------------------------------------------------------------------------
# The history
# ---------------------------------------------------------------------------


def test_the_history_is_a_sequence_not_a_current_state(
    client, db_session, login_as, _api_curator_user
):
    """Broken, verified, broken again is a claim no summary can express."""
    _calculation, artifact, sha = _broken_artifact(db_session, "flapping")
    _record(
        db_session,
        sha,
        ArtifactIntegrityFinding.digest_mismatch,
        ArtifactIntegrityDetectionContext.download,
        artifact_id=artifact.id,
    )
    record_integrity_verified(
        sha256=sha,
        detected_during=ArtifactIntegrityDetectionContext.verification_sweep,
        artifact_id=artifact.id,
        session_factory=_SessionProxy(db_session),
        storage_client=_MuteStoreProbe(),
    )
    _record(
        db_session,
        sha,
        ArtifactIntegrityFinding.size_mismatch,
        ArtifactIntegrityDetectionContext.parameter_extraction,
        artifact_id=artifact.id,
    )
    login_as(_api_curator_user)

    body = client.get(
        f"/api/v1/scientific/artifacts/{sha}/integrity"
    ).json()

    assert [row["finding"] for row in body["observations"]] == [
        "digest_mismatch",
        "verified",
        "size_mismatch",
    ]
    assert [row["is_break"] for row in body["observations"]] == [True, False, True]
    assert body["summary"]["currently_broken"] is True
    assert body["pagination"]["total"] == 3


def test_a_never_observed_digest_is_an_empty_history_not_a_404(
    client, db_session, login_as, _api_curator_user
):
    """Absence of findings is a true answer, and never a verification claim."""
    login_as(_api_curator_user)

    response = client.get(f"/api/v1/scientific/artifacts/{_digest('untouched')}/integrity")

    assert response.status_code == 200
    body = response.json()
    assert body["observations"] == []
    assert body["summary"] is None
    assert body["pagination"]["total"] == 0


# ---------------------------------------------------------------------------
# Who may read it
# ---------------------------------------------------------------------------


def test_a_plain_user_may_not_read_the_custody_record(client, db_session):
    """Verifier prose and object-store ``ETag``s are deployment detail.

    The ``client`` fixture authenticates as an ordinary user, which is
    exactly the principal that may download approved bytes and may not
    read TCKDB's operational notes about its own bucket.
    """
    assert client.get(LIST_URL).status_code == 403
    assert (
        client.get(f"/api/v1/scientific/artifacts/{_digest('x')}/integrity").status_code
        == 403
    )


def test_an_admin_may_read_the_custody_record(
    client, db_session, login_as, _api_admin_user
):
    login_as(_api_admin_user)

    assert client.get(LIST_URL).status_code == 200


def test_client_sort_is_rejected_like_every_other_scientific_read(
    client, db_session, login_as, _api_curator_user
):
    login_as(_api_curator_user)

    response = client.get(LIST_URL, params={"sort": "sha256:asc"})

    assert response.status_code == 422
    assert "client_sort_not_supported" in response.text


# ---------------------------------------------------------------------------
# The citation resolves
# ---------------------------------------------------------------------------


def test_a_cited_observation_is_identifiable_in_the_history(
    client, db_session, login_as, _api_curator_user
):
    """The whole point of the ref: an exact match, not a timestamp guess.

    The reproducibility rubric copies this record's verdict and names the
    observation it copied. That citation was the row's primary key, which
    never appeared on this surface -- ``apply_internal_ids_visibility``
    strips every ``*_id`` key and the hosted startup check refuses to
    boot with the opt-in enabled -- so a curator holding one had no way
    to find the row except by matching timestamps against a page of
    near-identical observations.
    """
    _calculation, artifact, sha = _broken_artifact(db_session, "cited-observation")
    first = _record(
        db_session,
        sha,
        ArtifactIntegrityFinding.digest_mismatch,
        ArtifactIntegrityDetectionContext.download,
        artifact_id=artifact.id,
    )
    second = _record(
        db_session,
        sha,
        ArtifactIntegrityFinding.digest_mismatch,
        ArtifactIntegrityDetectionContext.verification_sweep,
        artifact_id=artifact.id,
    )
    login_as(_api_curator_user)

    body = client.get(f"/api/v1/scientific/artifacts/{sha}/integrity").json()

    refs = [row["integrity_event_ref"] for row in body["observations"]]
    assert refs == [first, second]
    assert all(ref.startswith("aie_") for ref in refs)
    # Two observations of the same digest, same finding, recorded back to
    # back: the refs are what tells them apart, and nothing else on the
    # row does.
    assert first != second
    assert body["summary"]["latest"]["integrity_event_ref"] == second


def test_the_custody_surface_still_hands_out_no_row_ids(
    client, db_session, login_as, _api_curator_user
):
    """A ref, and not the id under a new name.

    The refusal this surface inherits is not about the *word* ``id`` but
    about handing a client an implementation detail of one database
    instance. A test that only checked for a ref would pass if the ref
    were minted and the id kept beside it.
    """
    _calculation, artifact, sha = _broken_artifact(db_session, "no-ids-here")
    _record(
        db_session,
        sha,
        ArtifactIntegrityFinding.digest_mismatch,
        ArtifactIntegrityDetectionContext.download,
        artifact_id=artifact.id,
    )
    login_as(_api_curator_user)

    observation = client.get(
        f"/api/v1/scientific/artifacts/{sha}/integrity"
    ).json()["observations"][0]

    leaked = [key for key in observation if key == "id" or key.endswith("_id")]
    assert leaked == []
