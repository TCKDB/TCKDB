"""API tests for the record_review feature.

Covers:

* direct ``/uploads/*`` paths create ``submission`` +
  ``submission_record_link`` + ``record_review(not_reviewed)`` rows,
* ``/bundles/submit`` creates ``submission`` + ``submission_record_link``
  + ``record_review(not_reviewed)`` rows for linked records,
* approving a submission flips linked records to ``approved``,
* rejecting a submission flips linked records to ``rejected``,
* uploader cannot approve their own submission,
* ``PATCH /record-reviews`` is curator/admin-gated,
* ``PATCH`` enforces the disallowed-transition policy,
* the unique constraint stops duplicate current-state rows.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import event, func, select

from app.db.models.common import (
    RecordReviewStatus,
    SubmissionAuditEventKind,
    SubmissionKind,
    SubmissionRecordType,
    SubmissionSourceKind,
    SubmissionStatus,
)
from app.db.models.record_review import RecordReview
from app.db.models.species import SpeciesEntry
from app.db.models.submission import (
    Submission,
    SubmissionAuditEvent,
    SubmissionRecordLink,
)
from app.db.models.thermo import Thermo
from tests.services.scientific_read._factories import (
    make_applied_energy_correction,
    make_calculation,
    make_energy_correction_scheme,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES_DIR = REPO_ROOT / "examples" / "bundles"


def _hydrogen_conformer_payload(label: str = "conf-record-review") -> dict:
    return {
        "species_entry": {
            "smiles": "[H]",
            "charge": 0,
            "multiplicity": 2,
        },
        "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
        "calculation": {
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        "label": label,
        "note": "review row test",
    }


def _load_bundle(filename: str) -> dict:
    return json.loads((EXAMPLES_DIR / filename).read_text())


# ---------------------------------------------------------------------------
# Direct uploads → reviewable submission, records not_reviewed
# ---------------------------------------------------------------------------


class TestDirectUploadsCreateNotReviewedSubmissions:
    """Every accepted ``/uploads/*`` call creates a submission wrapper, links
    the produced records to it, and initialises their review rows at
    ``not_reviewed`` — the same reviewable semantics as the hosted bundle
    path, differing only by payload shape.

    ``not_reviewed``, because nobody has looked. These assertions used to read
    ``under_review``, which claimed a reviewer on a row whose ``reviewed_by``
    the very next line asserts is ``None``.
    """

    def test_conformer_upload_creates_submission_and_not_reviewed_rows(
        self, client, db_session
    ):
        before_subs = (
            db_session.scalar(select(func.count()).select_from(Submission)) or 0
        )

        resp = client.post(
            "/api/v1/uploads/conformers",
            json=_hydrogen_conformer_payload(label="conf-direct-upload"),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()

        # A submission wrapper is created for the direct upload.
        after_subs = (
            db_session.scalar(select(func.count()).select_from(Submission)) or 0
        )
        assert after_subs == before_subs + 1

        submission_id = body["submission_id"]
        assert submission_id is not None
        submission = db_session.get(Submission, submission_id)
        assert submission is not None
        assert submission.submission_kind is SubmissionKind.conformer
        assert submission.source_kind is SubmissionSourceKind.api
        # Entered review, not approved: success != scientific approval.
        assert submission.status is SubmissionStatus.pending

        # Primary record gets a not_reviewed review row linked to the submission.
        observation_id = body["id"]
        review = db_session.scalar(
            select(RecordReview).where(
                RecordReview.record_type
                == SubmissionRecordType.conformer_observation,
                RecordReview.record_id == observation_id,
            )
        )
        assert review is not None
        assert review.status is RecordReviewStatus.not_reviewed
        assert review.submission_id == submission_id
        assert review.reviewed_by is None

        # The observation is linked to the submission.
        link = db_session.scalar(
            select(SubmissionRecordLink).where(
                SubmissionRecordLink.submission_id == submission_id,
                SubmissionRecordLink.record_type
                == SubmissionRecordType.conformer_observation,
                SubmissionRecordLink.record_id == observation_id,
            )
        )
        assert link is not None

        # Calculation gets one too — included by Decision 2 in the design.
        calc_id = body["primary_calculation"]["calculation_id"]
        calc_review = db_session.scalar(
            select(RecordReview).where(
                RecordReview.record_type == SubmissionRecordType.calculation,
                RecordReview.record_id == calc_id,
            )
        )
        assert calc_review is not None
        assert calc_review.status is RecordReviewStatus.not_reviewed
        assert calc_review.submission_id == submission_id

        # Audit trail: submission_created + ingestion_succeeded.
        kinds = {
            e.event_kind
            for e in db_session.scalars(
                select(SubmissionAuditEvent).where(
                    SubmissionAuditEvent.submission_id == submission_id
                )
            ).all()
        }
        assert SubmissionAuditEventKind.submission_created in kinds
        assert SubmissionAuditEventKind.ingestion_succeeded in kinds

    def test_thermo_upload_creates_submission_and_not_reviewed_row(
        self, client, db_session
    ):
        resp = client.post(
            "/api/v1/uploads/thermo",
            json={"enthalpy_reference_kind": "formation_from_elements_298k",
                "species_entry": {
                    "smiles": "[H]",
                    "charge": 0,
                    "multiplicity": 2,
                },
                "scientific_origin": "computed",
                "h298_kj_mol": 217.998,
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        thermo_id = body["id"]
        submission_id = body["submission_id"]
        assert submission_id is not None

        review = db_session.scalar(
            select(RecordReview).where(
                RecordReview.record_type == SubmissionRecordType.thermo,
                RecordReview.record_id == thermo_id,
            )
        )
        assert review is not None
        assert review.status is RecordReviewStatus.not_reviewed
        assert review.submission_id == submission_id

        submission = db_session.get(Submission, submission_id)
        assert submission is not None
        assert submission.submission_kind is SubmissionKind.thermo

        # The thermo product is linked to the submission.
        link = db_session.scalar(
            select(SubmissionRecordLink).where(
                SubmissionRecordLink.submission_id == submission_id,
                SubmissionRecordLink.record_type == SubmissionRecordType.thermo,
                SubmissionRecordLink.record_id == thermo_id,
            )
        )
        assert link is not None

    def test_computed_species_creates_submission_and_not_reviewed_rows(
        self, client, db_session
    ):
        # Minimal valid computed-species bundle with one conformer + opt
        # primary calc.
        bundle = {
            "species_entry": {
                "smiles": "[H]",
                "charge": 0,
                "multiplicity": 2,
            },
            "conformers": [
                {
                    "key": "conf-a",
                    "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
                    "label": "conf-a",
                    "primary_calculation": {
                        "key": "primary-opt",
                        "type": "opt",
                        "software_release": {
                            "name": "Gaussian",
                            "version": "16",
                        },
                        "level_of_theory": {
                            "method": "B3LYP",
                            "basis": "6-31G(d)",
                        },
                    },
                    "additional_calculations": [],
                }
            ],
        }

        resp = client.post("/api/v1/uploads/computed-species", json=bundle)
        assert resp.status_code == 201, resp.text
        body = resp.json()

        submission_id = body["submission_id"]
        assert submission_id is not None
        submission = db_session.get(Submission, submission_id)
        assert submission is not None
        assert submission.submission_kind is SubmissionKind.computed_species

        species_review = db_session.scalar(
            select(RecordReview).where(
                RecordReview.record_type == SubmissionRecordType.species_entry,
                RecordReview.record_id == body["species_entry_id"],
            )
        )
        assert species_review is not None
        assert species_review.status is RecordReviewStatus.not_reviewed
        assert species_review.submission_id == submission_id

        # The species_entry is linked to the submission.
        link = db_session.scalar(
            select(SubmissionRecordLink).where(
                SubmissionRecordLink.submission_id == submission_id,
                SubmissionRecordLink.record_type
                == SubmissionRecordType.species_entry,
                SubmissionRecordLink.record_id == body["species_entry_id"],
            )
        )
        assert link is not None


# ---------------------------------------------------------------------------
# Bundle submit → not_reviewed
# ---------------------------------------------------------------------------


class TestBundleSubmitCreatesNotReviewedRows:
    def test_thermo_bundle_review_rows(self, client, db_session):
        bundle = _load_bundle("thermo-bundle-v0.json")
        resp = client.post("/api/v1/bundles/submit", json=bundle)
        assert resp.status_code == 201, resp.text
        body = resp.json()

        submission_id = body["submission_id"]

        # Every submission_record_link target has a not_reviewed review row.
        link_pairs = db_session.scalars(
            select(SubmissionRecordLink).where(
                SubmissionRecordLink.submission_id == submission_id
            )
        ).all()
        assert link_pairs, "bundle submit should create record links"
        for link in link_pairs:
            review = db_session.scalar(
                select(RecordReview).where(
                    RecordReview.record_type == link.record_type,
                    RecordReview.record_id == link.record_id,
                )
            )
            assert review is not None
            assert review.status is RecordReviewStatus.not_reviewed
            assert review.submission_id == submission_id


# ---------------------------------------------------------------------------
# Approve / reject flip linked review rows
# ---------------------------------------------------------------------------


class TestSubmissionApprovalFlipsReviewState:
    def test_approve_flips_to_approved(
        self, client, db_session, login_as, _api_curator_user
    ):
        bundle = _load_bundle("thermo-bundle-v0.json")
        resp = client.post("/api/v1/bundles/submit", json=bundle)
        assert resp.status_code == 201
        submission_id = resp.json()["submission_id"]

        login_as(_api_curator_user)
        approve_resp = client.post(
            f"/api/v1/submissions/{submission_id}/approve",
            json={"summary": "looks good"},
        )
        assert approve_resp.status_code == 200, approve_resp.text

        rows = db_session.scalars(
            select(RecordReview).where(
                RecordReview.submission_id == submission_id
            )
        ).all()
        assert rows
        for r in rows:
            assert r.status is RecordReviewStatus.approved
            assert r.reviewed_by == _api_curator_user
            assert r.reviewed_at is not None

    def test_reject_flips_to_rejected(
        self, client, db_session, login_as, _api_curator_user
    ):
        bundle = _load_bundle("thermo-bundle-v0.json")
        resp = client.post("/api/v1/bundles/submit", json=bundle)
        assert resp.status_code == 201
        submission_id = resp.json()["submission_id"]

        login_as(_api_curator_user)
        reject_resp = client.post(
            f"/api/v1/submissions/{submission_id}/reject",
            json={"reason": "scientific issue"},
        )
        assert reject_resp.status_code == 200, reject_resp.text

        rows = db_session.scalars(
            select(RecordReview).where(
                RecordReview.submission_id == submission_id
            )
        ).all()
        assert rows
        for r in rows:
            assert r.status is RecordReviewStatus.rejected
            assert r.reviewed_by == _api_curator_user

    def test_uploader_cannot_approve_own_submission(self, client, db_session):
        bundle = _load_bundle("thermo-bundle-v0.json")
        resp = client.post("/api/v1/bundles/submit", json=bundle)
        assert resp.status_code == 201
        submission_id = resp.json()["submission_id"]

        # The default test user is the uploader; they're a normal-role
        # user and so are blocked twice over (role and self-approval).
        approve_resp = client.post(
            f"/api/v1/submissions/{submission_id}/approve",
        )
        assert approve_resp.status_code == 403, approve_resp.text


# ---------------------------------------------------------------------------
# /record-reviews API endpoints
# ---------------------------------------------------------------------------


class TestRecordReviewApi:
    def _seed_thermo(self, client) -> int:
        resp = client.post(
            "/api/v1/uploads/thermo",
            json={"enthalpy_reference_kind": "formation_from_elements_298k",
                "species_entry": {
                    "smiles": "[H]",
                    "charge": 0,
                    "multiplicity": 2,
                },
                "scientific_origin": "computed",
                "h298_kj_mol": 217.998,
            },
        )
        assert resp.status_code == 201
        return resp.json()["id"]

    def test_get_one(self, client):
        thermo_id = self._seed_thermo(client)
        resp = client.get(f"/api/v1/record-reviews/thermo/{thermo_id}")
        assert resp.status_code == 200
        body = resp.json()
        # Direct uploads are queued for review, not being reviewed.
        assert body["status"] == "not_reviewed"
        assert body["record_type"] == "thermo"
        assert body["record_id"] == thermo_id

    def test_get_one_404(self, client):
        resp = client.get("/api/v1/record-reviews/thermo/9999999")
        assert resp.status_code == 404

    def test_list_filters_by_status(self, client):
        self._seed_thermo(client)
        resp = client.get(
            "/api/v1/record-reviews",
            params={"status": "not_reviewed", "limit": 50},
        )
        assert resp.status_code == 200
        rows = resp.json()
        assert rows
        assert all(r["status"] == "not_reviewed" for r in rows)

    def test_a_review_row_names_its_record_by_public_ref(self, client, db_session):
        """The queue's whole job is "go and look at this record".

        ``record_id`` alone cannot be pasted into a URL or looked up
        through any public route, so a reviewer holding only that has
        nothing to act on. This is the same gap #479 closed on the
        machine-review inspection surface.
        """
        thermo_id = self._seed_thermo(client)
        resp = client.get(f"/api/v1/record-reviews/thermo/{thermo_id}")
        assert resp.status_code == 200
        body = resp.json()

        ref = body["record_public_ref"]
        assert ref, "thermo carries PublicRefMixin, so a ref must resolve"
        # Not the row id wearing a different name -- the two are
        # deliberately compared, because falling back to the id is exactly
        # the defect this field exists to prevent.
        assert ref != str(thermo_id)
        assert body["record_id"] == thermo_id
        # And it is THIS record's ref, read back from the record itself.
        # "a plausible-looking ref came back" is a weaker claim than "the
        # right ref came back", and only the second is worth anything to a
        # reviewer about to click it.
        assert ref == db_session.get(Thermo, thermo_id).public_ref

    def test_the_list_route_names_every_row_it_returns(self, client, db_session):
        """Resolved for a whole page, not just for the single-row read.

        A list that omitted the ref would send a reviewer to the detail
        route for every row just to learn what each row is about.
        """
        first = self._seed_thermo(client)
        second = self._seed_thermo(client)
        resp = client.get(
            "/api/v1/record-reviews",
            params={"record_type": "thermo", "status": "not_reviewed", "limit": 50},
        )
        assert resp.status_code == 200
        rows = {r["record_id"]: r for r in resp.json()}

        for record_id in (first, second):
            assert record_id in rows, "seeded row missing from the listing"
            ref = rows[record_id]["record_public_ref"]
            assert ref, f"row {record_id} came back unnamed"
            assert ref != str(record_id)
            # Each row must carry ITS OWN record's ref. Checking only that
            # the refs are non-null and distinct lets a mapper that pairs
            # refs to rows positionally through: the listing is ordered
            # newest-first and the ref lookup comes back id-ascending, so
            # the two orders really do differ and every row would name its
            # neighbour. A reviewer would click the row they were asked to
            # review and land on a different record -- worse than a null.
            assert ref == db_session.get(Thermo, record_id).public_ref
        # Two records, two distinct refs -- a mapper that resolved one ref
        # and reused it for the page would pass every check above.
        assert rows[first]["record_public_ref"] != rows[second]["record_public_ref"]

    def test_a_row_whose_record_cannot_be_named_is_still_listed(
        self, client, db_session
    ):
        """Unnameable is not a reason to hide the work.

        Two records cannot produce a ref: ``applied_energy_correction``
        has no ``public_ref`` column at all (task #253), and a row may
        point at a record that no longer exists. Either way the review is
        outstanding and a queue that quietly drops it understates the
        backlog -- the one thing a backlog must not do.
        """
        db_session.add(
            RecordReview(
                record_type=SubmissionRecordType.thermo,
                record_id=9_999_999,
                status=RecordReviewStatus.not_reviewed,
            )
        )
        db_session.flush()

        resp = client.get(
            "/api/v1/record-reviews",
            params={"record_type": "thermo", "status": "not_reviewed", "limit": 200},
        )
        assert resp.status_code == 200
        rows = {r["record_id"]: r for r in resp.json()}

        assert 9_999_999 in rows, "an unnameable row was dropped from the queue"
        assert rows[9_999_999]["record_public_ref"] is None

    def test_the_ref_survives_a_curator_transition(
        self, client, login_as, _api_curator_user
    ):
        """PATCH answers with the same shape the reads do.

        A client that re-renders a row from the PATCH reply would lose the
        record's name on every approval if this route were the one that
        forgot to resolve it.
        """
        thermo_id = self._seed_thermo(client)
        before = client.get(f"/api/v1/record-reviews/thermo/{thermo_id}").json()

        login_as(_api_curator_user)
        resp = client.patch(
            f"/api/v1/record-reviews/thermo/{thermo_id}",
            json={"status": "approved"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "approved"
        assert body["record_public_ref"] == before["record_public_ref"]
        assert body["record_public_ref"]

    def test_patch_requires_curator(self, client):
        thermo_id = self._seed_thermo(client)
        # Default test user is role=user → 403.
        resp = client.patch(
            f"/api/v1/record-reviews/thermo/{thermo_id}",
            json={"status": "approved"},
        )
        assert resp.status_code == 403

    def test_curator_can_approve(self, client, login_as, _api_curator_user):
        thermo_id = self._seed_thermo(client)
        login_as(_api_curator_user)
        resp = client.patch(
            f"/api/v1/record-reviews/thermo/{thermo_id}",
            json={"status": "approved"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "approved"
        assert body["reviewed_by"] == _api_curator_user

    def test_disallowed_transition(self, client, login_as, _api_curator_user):
        thermo_id = self._seed_thermo(client)
        login_as(_api_curator_user)
        # First go approved.
        approve = client.patch(
            f"/api/v1/record-reviews/thermo/{thermo_id}",
            json={"status": "approved"},
        )
        assert approve.status_code == 200
        # approved → rejected is disallowed.
        bad = client.patch(
            f"/api/v1/record-reviews/thermo/{thermo_id}",
            json={"status": "rejected"},
        )
        assert bad.status_code == 400
        assert "Disallowed" in bad.json()["detail"]


class TestReviewRowSaysWhereItsRecordCanBeSeen:
    """#262: 385 of 1,299 queue rows could not be opened, and it was the wrong
    385 -- the thermochemistry, kinetics and energy corrections the archive
    exists to publish.

    ``record_public_ref`` names a record; it does not locate one. Six record
    types are rendered only inside their parent, so a ``thm_...`` addresses no
    route. ``container_type``/``container_ref`` are what a client turns into a
    link, and they are resolved the same way the ref is: at read time, in bulk.
    """

    def _seed_thermo(self, client) -> int:
        resp = client.post(
            "/api/v1/uploads/thermo",
            json={"enthalpy_reference_kind": "formation_from_elements_298k",
                "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
                "scientific_origin": "computed",
                "h298_kj_mol": 217.998,
            },
        )
        assert resp.status_code == 201
        return resp.json()["id"]

    def test_a_thermo_row_names_the_species_entry_it_is_shown_on(
        self, client, db_session
    ):
        """The measured defect, at the wire.

        Thermo is 65 of the 385. It renders as a tab on the species entry
        page, and this is the field that says which one.
        """
        thermo_id = self._seed_thermo(client)

        body = client.get(f"/api/v1/record-reviews/thermo/{thermo_id}").json()

        thermo = db_session.get(Thermo, thermo_id)
        entry = db_session.get(SpeciesEntry, thermo.species_entry_id)
        assert body["container_type"] == "species_entry"
        # THIS thermo's species entry, read back from the record itself. "a
        # ref came back" would pass against any other entry in the database.
        assert body["container_ref"] == entry.public_ref

    def test_the_container_is_a_ref_and_never_a_row_id(self, client, db_session):
        """DR-0028 Req 2: no internal row id in what a reader is shown.

        The shortcut this rules out is real and tempting -- the parent's id is
        already in hand after the first query, and returning it would save the
        second. It would also put a number in front of a curator that resolves
        through no public route, while looking like an identifier.
        """
        thermo_id = self._seed_thermo(client)

        body = client.get(f"/api/v1/record-reviews/thermo/{thermo_id}").json()

        thermo = db_session.get(Thermo, thermo_id)
        assert body["container_ref"] != str(thermo.species_entry_id)
        assert body["container_ref"] != thermo.species_entry_id
        # No id-shaped sibling crept in beside the ref, under any spelling.
        assert "container_id" not in body
        assert not any(
            key.startswith("container") and key.endswith("_id") for key in body
        )

    def test_the_two_halves_are_null_together(self, client, db_session):
        """A ref with no type cannot be linked; a type with no ref names
        nothing. The schema promises they move together, so a row that has
        neither must have neither -- not one of them.
        """
        db_session.add(
            RecordReview(
                record_type=SubmissionRecordType.species,
                record_id=make_species(db_session).id,
                status=RecordReviewStatus.not_reviewed,
            )
        )
        db_session.flush()

        rows = client.get(
            "/api/v1/record-reviews",
            params={"record_type": "species", "status": "not_reviewed", "limit": 200},
        ).json()
        assert rows, "seeded species review row missing from the listing"

        for row in rows:
            assert (row["container_type"] is None) == (row["container_ref"] is None)
        # species is a root: it has no owner, and that is a normal answer.
        assert all(row["container_type"] is None for row in rows)

    def test_a_correction_that_cannot_be_named_still_says_where_it_applies(
        self, client, db_session
    ):
        """164 of the 385, and the case the whole field is justified by.

        ``applied_energy_correction`` has no ``public_ref`` column, so the
        queue renders it as "applied_energy_correction cannot be named".
        Giving that table a ref is task #253 and is NOT needed here: "a
        correction on species entry spc_..." is the more useful sentence
        anyway, because it says what the correction is attached to.
        """
        entry = make_species_entry(db_session, make_species(db_session))
        correction = make_applied_energy_correction(
            db_session,
            target_species_entry=entry,
            scheme=make_energy_correction_scheme(db_session, name="api_container"),
        )
        db_session.add(
            RecordReview(
                record_type=SubmissionRecordType.applied_energy_correction,
                record_id=correction.id,
                status=RecordReviewStatus.not_reviewed,
            )
        )
        db_session.flush()

        body = client.get(
            f"/api/v1/record-reviews/applied_energy_correction/{correction.id}"
        ).json()

        # Still unnameable -- this change does not invent a ref for it.
        assert body["record_public_ref"] is None
        # But no longer unreachable.
        assert body["container_type"] == "species_entry"
        assert body["container_ref"] == entry.public_ref

    def test_the_list_route_gives_every_row_its_own_container(
        self, client, db_session
    ):
        """Resolved for a whole page, and per row rather than once per page.

        Two thermos on two different species entries. A mapper that resolved
        one container and reused it for the page, or that paired containers to
        rows positionally, would pass a check that only asserted non-null --
        and would send a curator to the wrong record, which is worse than
        sending them nowhere.
        """
        first = self._seed_thermo(client)
        second_entry = make_species_entry(db_session, make_species(db_session))
        second = make_thermo_scalar(
            db_session, species_entry=second_entry, h298_kj_mol=-99.5
        )
        db_session.add(
            RecordReview(
                record_type=SubmissionRecordType.thermo,
                record_id=second.id,
                status=RecordReviewStatus.not_reviewed,
            )
        )
        db_session.flush()

        rows = {
            r["record_id"]: r
            for r in client.get(
                "/api/v1/record-reviews",
                params={
                    "record_type": "thermo",
                    "status": "not_reviewed",
                    "limit": 200,
                },
            ).json()
        }

        first_entry = db_session.get(
            SpeciesEntry, db_session.get(Thermo, first).species_entry_id
        )
        assert rows[first]["container_ref"] == first_entry.public_ref
        assert rows[second.id]["container_ref"] == second_entry.public_ref
        assert rows[first]["container_ref"] != rows[second.id]["container_ref"]

    def test_a_longer_page_does_not_cost_more_queries(self, client, db_session):
        """The bulk resolve, measured at the route rather than the service.

        Counted as "the same number of statements for 2 rows as for 10",
        which is the property that matters and is insensitive to however many
        fixed queries authentication and the listing itself take. A resolver
        moved inside the row loop -- the single most likely regression here,
        since the pure-mapper split is the only thing preventing it -- makes
        this grow by eight and fails nothing else in this file.
        """
        def _seed(n: int) -> None:
            for i in range(n):
                # A DIFFERENT species entry per row, which is how a real page
                # looks and is what makes this count meaningful. Seeded under
                # one shared parent -- as this test originally was -- the
                # whole page has a single distinct container, and a resolver
                # that queried once per container would cost the same for 2
                # rows as for 10 and pass unchanged.
                thermo = make_thermo_scalar(
                    db_session,
                    species_entry=make_species_entry(
                        db_session, make_species(db_session)
                    ),
                    h298_kj_mol=-500.0 - i,
                )
                db_session.add(
                    RecordReview(
                        record_type=SubmissionRecordType.thermo,
                        record_id=thermo.id,
                        status=RecordReviewStatus.under_review,
                    )
                )
            db_session.flush()

        def _count() -> int:
            statements = 0
            engine = db_session.connection().engine

            def _before(conn, cursor, statement, parameters, context, executemany):
                nonlocal statements
                statements += 1

            event.listen(engine, "before_cursor_execute", _before)
            try:
                resp = client.get(
                    "/api/v1/record-reviews",
                    params={
                        "record_type": "thermo",
                        "status": "under_review",
                        "limit": 200,
                    },
                )
                assert resp.status_code == 200
            finally:
                event.remove(engine, "before_cursor_execute", _before)
            return statements

        _seed(2)
        two_rows = _count()
        _seed(8)
        ten_rows = _count()

        assert ten_rows == two_rows, (
            f"the page cost {two_rows} queries for 2 rows and {ten_rows} for "
            "10 -- the ref or container resolve is running per row"
        )


class TestReviewQueueSubjectsApi:
    """``GET /api/v1/record-reviews/queue`` (task #269).

    The flat ``GET /api/v1/record-reviews`` list is unchanged and still
    tested above; this is the new subject-grouped read the redesigned
    queue page consumes. See ``app/services/review_queue.py`` for the
    grouping rule these assertions pin at the wire.
    """

    def test_two_corrections_on_one_species_render_as_one_subject_with_a_count(
        self, client, db_session
    ):
        entry = make_species_entry(db_session, make_species(db_session))
        atom = make_applied_energy_correction(
            db_session,
            target_species_entry=entry,
            scheme=make_energy_correction_scheme(db_session, name="wire_atom"),
        )
        bond = make_applied_energy_correction(
            db_session,
            target_species_entry=entry,
            scheme=make_energy_correction_scheme(db_session, name="wire_bond"),
        )
        db_session.add_all(
            [
                RecordReview(
                    record_type=SubmissionRecordType.applied_energy_correction,
                    record_id=atom.id,
                    status=RecordReviewStatus.not_reviewed,
                ),
                RecordReview(
                    record_type=SubmissionRecordType.applied_energy_correction,
                    record_id=bond.id,
                    status=RecordReviewStatus.not_reviewed,
                ),
            ]
        )
        db_session.flush()

        resp = client.get(
            "/api/v1/record-reviews/queue",
            params={"status": "not_reviewed", "limit": 200},
        )
        assert resp.status_code == 200
        body = resp.json()

        matching = [
            s for s in body["subjects"] if s["subject_ref"] == entry.public_ref
        ]
        assert len(matching) == 1, "one species rendered as more than one subject"
        assert len(matching[0]["records"]) == 2
        record_types = {r["record_type"] for r in matching[0]["records"]}
        assert record_types == {"applied_energy_correction"}
        # Each nested record still carries everything the per-record Review
        # action needs -- the redesign is presentation-only.
        for record in matching[0]["records"]:
            assert record["record_id"] in {atom.id, bond.id}
            assert record["status"] == "not_reviewed"

    def test_species_entry_subject_carries_formula_and_never_says_cannot_be_named(
        self, client, db_session
    ):
        species = make_species(db_session, smiles="O", multiplicity=1)
        entry = make_species_entry(db_session, species)
        correction = make_applied_energy_correction(
            db_session,
            target_species_entry=entry,
            scheme=make_energy_correction_scheme(db_session, name="wire_formula"),
        )
        db_session.add(
            RecordReview(
                record_type=SubmissionRecordType.applied_energy_correction,
                record_id=correction.id,
                status=RecordReviewStatus.not_reviewed,
            )
        )
        db_session.flush()

        resp = client.get(
            "/api/v1/record-reviews/queue",
            params={"status": "not_reviewed", "limit": 200},
        )
        assert resp.status_code == 200
        body = resp.json()
        subject = next(
            s for s in body["subjects"] if s["subject_ref"] == entry.public_ref
        )
        assert subject["chemistry"]["formula"] == "H2O"
        # NOT `assert "cannot be named" not in json.dumps(body)` -- review
        # of #492 caught that assertion as vacuous: that phrase was never
        # emitted by any backend response (it is frontend copy, deleted
        # from `ReviewQueuePage.tsx` in the same PR), so the assertion
        # passed against main, unmodified, before this endpoint existed.
        # An assertion that cannot fail asserts nothing; the formula check
        # above is the real content of this test.

    def test_the_header_totals_are_honest_counts_not_estimates(
        self, client, db_session
    ):
        """`subject_total`/`record_total` are the server's OWN count over the
        whole filtered backlog -- not derived from what is shown on this page.

        Review of #492 caught the previous version of this test as unable to
        fail: it only asserted ``total >= len(page)``, which
        ``total = len(page)`` (a route that quietly forgot the whole-backlog
        count and fell back to counting what it happened to return) also
        satisfies. This version seeds MORE subjects and records than the
        page's `limit` returns and asserts the totals EQUAL the true seeded
        counts -- a route computing `len(subjects_on_this_page)` instead
        would report 1, not 3.
        """
        first_entry = make_species_entry(db_session, make_species(db_session))
        first_thermo = make_thermo_scalar(
            db_session, species_entry=first_entry, h298_kj_mol=1.0
        )
        second_entry = make_species_entry(db_session, make_species(db_session))
        second_thermo = make_thermo_scalar(
            db_session, species_entry=second_entry, h298_kj_mol=2.0
        )
        second_statmech = make_statmech(db_session, species_entry=second_entry)
        third_entry = make_species_entry(db_session, make_species(db_session))
        third_thermo = make_thermo_scalar(
            db_session, species_entry=third_entry, h298_kj_mol=3.0
        )
        db_session.add_all(
            [
                RecordReview(
                    record_type=SubmissionRecordType.thermo,
                    record_id=first_thermo.id,
                    status=RecordReviewStatus.not_reviewed,
                ),
                RecordReview(
                    record_type=SubmissionRecordType.thermo,
                    record_id=second_thermo.id,
                    status=RecordReviewStatus.not_reviewed,
                ),
                RecordReview(
                    record_type=SubmissionRecordType.statmech,
                    record_id=second_statmech.id,
                    status=RecordReviewStatus.not_reviewed,
                ),
                RecordReview(
                    record_type=SubmissionRecordType.thermo,
                    record_id=third_thermo.id,
                    status=RecordReviewStatus.not_reviewed,
                ),
            ]
        )
        db_session.flush()

        # limit=1: the page shows exactly ONE subject and its records, but
        # the totals must still describe the whole 3-subject, 4-record set.
        resp = client.get(
            "/api/v1/record-reviews/queue",
            params={"status": "not_reviewed", "limit": 1},
        )
        body = resp.json()
        assert len(body["subjects"]) == 1
        assert body["subject_total"] == 3
        assert body["record_total"] == 4

    def test_paging_counts_subjects_never_splits_one(self, client, db_session):
        entry = make_species_entry(db_session, make_species(db_session))
        thermo = make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=5.0)
        statmech = make_statmech(db_session, species_entry=entry)
        db_session.add_all(
            [
                RecordReview(
                    record_type=SubmissionRecordType.thermo,
                    record_id=thermo.id,
                    status=RecordReviewStatus.not_reviewed,
                ),
                RecordReview(
                    record_type=SubmissionRecordType.statmech,
                    record_id=statmech.id,
                    status=RecordReviewStatus.not_reviewed,
                ),
            ]
        )
        db_session.flush()

        resp = client.get(
            "/api/v1/record-reviews/queue",
            params={"status": "not_reviewed", "limit": 1},
        )
        body = resp.json()
        assert len(body["subjects"]) == 1
        matching = next(
            s for s in body["subjects"] if s["subject_ref"] == entry.public_ref
        )
        assert len(matching["records"]) == 2, (
            "a one-subject page dropped one of that subject's own records"
        )

    def test_a_nested_records_own_ref_and_container_reach_the_wire(
        self, client, db_session
    ):
        """Review of #492, mutation 2: a build that served ``refs={}``/
        ``containers={}`` for the page's own rows -- so every nested record's
        own ``record_public_ref``/``container_ref`` silently went null --
        passed all 15 previously-shipped tests, because none of them read
        those two fields off a ``/queue`` record. A calculation nested under
        a species_entry subject is exactly the case that build broke: it has
        its own page and its own ref (``calc_...``), and losing it falls
        back to the "(unnamed)" wording #488 exists to prevent.
        """
        entry = make_species_entry(db_session, make_species(db_session))
        calc = make_calculation(db_session, species_entry_id=entry.id)
        db_session.add(
            RecordReview(
                record_type=SubmissionRecordType.calculation,
                record_id=calc.id,
                status=RecordReviewStatus.not_reviewed,
            )
        )
        db_session.flush()

        resp = client.get(
            "/api/v1/record-reviews/queue",
            params={"status": "not_reviewed", "limit": 200},
        )
        body = resp.json()
        subject = next(
            s for s in body["subjects"] if s["subject_ref"] == entry.public_ref
        )
        record = next(
            r for r in subject["records"] if r["record_type"] == "calculation"
        )
        assert record["record_public_ref"] == calc.public_ref
        assert record["container_type"] == "species_entry"
        assert record["container_ref"] == entry.public_ref

    def test_offset_actually_advances_the_page(self, client, db_session):
        """Review of #492, mutation 1: ``page_keys = list(groups.keys())
        [:limit]`` (offset silently ignored) passed all 15 previously-shipped
        tests, because every one of them requested offset 0. Page 2 would
        have silently repeated page 1 while the header claimed "subjects
        51-100". Three subjects, limit=1: offset 0, 1 and 2 must each show a
        DIFFERENT subject, in the server's own newest-first order.
        """
        refs_in_order = []
        for i in range(3):
            entry = make_species_entry(db_session, make_species(db_session))
            thermo = make_thermo_scalar(
                db_session, species_entry=entry, h298_kj_mol=float(i)
            )
            db_session.add(
                RecordReview(
                    record_type=SubmissionRecordType.thermo,
                    record_id=thermo.id,
                    status=RecordReviewStatus.not_reviewed,
                )
            )
            db_session.flush()
            refs_in_order.append(entry.public_ref)
        # Newest-first: the last one seeded is first on the page.
        expected = list(reversed(refs_in_order))

        seen = []
        for offset in range(3):
            resp = client.get(
                "/api/v1/record-reviews/queue",
                params={"status": "not_reviewed", "limit": 1, "skip": offset},
            )
            body = resp.json()
            assert len(body["subjects"]) == 1
            seen.append(body["subjects"][0]["subject_ref"])

        assert seen == expected, (
            f"offset did not advance the page: got {seen}, expected {expected}"
        )
        assert len(set(seen)) == 3, "two different offsets returned the same subject"
