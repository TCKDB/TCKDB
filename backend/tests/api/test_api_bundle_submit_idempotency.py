"""API tests for Idempotency-Key on POST /api/v1/bundles/submit.

Verifies that an exact retry of a bundle submission replays the stored
response without duplicating the submission, audit events, link rows,
or the imported scientific records (thermo / kinetics).
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select

from app.db.models.idempotency import IdempotencyRecord
from app.db.models.kinetics import Kinetics
from app.db.models.submission import (
    Submission,
    SubmissionAuditEvent,
    SubmissionRecordLink,
)
from app.db.models.thermo import Thermo


REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES_DIR = REPO_ROOT / "examples" / "bundles"
ENDPOINT = "/api/v1/bundles/submit"
KEY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotency-Replayed"


def _load_bundle(filename: str) -> dict:
    return json.loads((EXAMPLES_DIR / filename).read_text())


def _counts(session) -> dict[str, int]:
    return {
        "thermo": session.scalar(select(func.count()).select_from(Thermo)) or 0,
        "kinetics": session.scalar(select(func.count()).select_from(Kinetics)) or 0,
        "submission": session.scalar(select(func.count()).select_from(Submission)) or 0,
        "audit": session.scalar(
            select(func.count()).select_from(SubmissionAuditEvent)
        )
        or 0,
        "links": session.scalar(
            select(func.count()).select_from(SubmissionRecordLink)
        )
        or 0,
        "idempotency": session.scalar(
            select(func.count()).select_from(IdempotencyRecord)
        )
        or 0,
    }


class TestBundleSubmitReplay:
    def test_thermo_bundle_replay_does_not_duplicate(self, client, db_session) -> None:
        bundle = _load_bundle("thermo-bundle-v0.json")

        first = client.post(
            ENDPOINT,
            json=bundle,
            headers={KEY_HEADER: "bundle-thermo-key-aaaaaa"},
        )
        assert first.status_code == 201, first.text
        assert REPLAYED_HEADER not in first.headers

        snapshot = _counts(db_session)

        second = client.post(
            ENDPOINT,
            json=bundle,
            headers={KEY_HEADER: "bundle-thermo-key-aaaaaa"},
        )
        assert second.status_code == 201
        assert second.headers.get(REPLAYED_HEADER) == "true"
        assert second.json() == first.json()

        after = _counts(db_session)
        # No new thermo, no new submission, no new audit/link, no new
        # idempotency record.
        assert after == snapshot

    def test_kinetics_bundle_replay_does_not_duplicate(
        self, client, db_session
    ) -> None:
        bundle = _load_bundle("kinetics-bundle-v0.json")

        first = client.post(
            ENDPOINT,
            json=bundle,
            headers={KEY_HEADER: "bundle-kin-key-aaaaaaaaa"},
        )
        assert first.status_code == 201, first.text

        snapshot = _counts(db_session)

        second = client.post(
            ENDPOINT,
            json=bundle,
            headers={KEY_HEADER: "bundle-kin-key-aaaaaaaaa"},
        )
        assert second.status_code == 201
        assert second.headers.get(REPLAYED_HEADER) == "true"
        assert second.json() == first.json()
        assert _counts(db_session) == snapshot

    def test_bundle_submit_conflict_when_payload_differs(
        self, client, db_session
    ) -> None:
        bundle_a = _load_bundle("thermo-bundle-v0.json")
        # Mutate the payload (an unrelated metadata field would do).
        bundle_b = json.loads(json.dumps(bundle_a))
        bundle_b.setdefault("notes", "different submission")
        if bundle_b.get("notes") == bundle_a.get("notes"):
            bundle_b["notes"] = "different-" + (bundle_a.get("notes") or "x")

        first = client.post(
            ENDPOINT,
            json=bundle_a,
            headers={KEY_HEADER: "bundle-conflict-aaaaaaaa"},
        )
        assert first.status_code == 201, first.text

        before = _counts(db_session)
        conflict = client.post(
            ENDPOINT,
            json=bundle_b,
            headers={KEY_HEADER: "bundle-conflict-aaaaaaaa"},
        )
        assert conflict.status_code == 409
        body = conflict.json()
        assert body["code"] == "idempotency_conflict"
        # No second import.
        assert _counts(db_session) == before
