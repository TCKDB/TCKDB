"""Tests for ``POST /api/v1/uploads/thermoml`` (Phase C-E6).

Lets anyone deposit a ThermoML XML document directly -- not only the NIST
bulk archive (``backend/scripts/thermoml_cp_import.py --archive``). Exercises
the route end to end: happy path (refs only, never a database id), coded
refusals, idempotency (required here, unlike every sibling ``/uploads/*``
route -- see the route's own docstring), and authorization -- mirroring
``test_api_calculation_artifacts.py``'s structure for the nearest sibling
(the only other route that carries inline base64 file bytes).

This route always commits (no preview flag -- see
``ThermoMLUploadRequest``'s docstring for why: no sibling ``/uploads/*``
route previews either). A preview is available only through the backend
CLI's default (no ``--commit``) dry-run mode.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api.app import create_app
from app.api.deps import get_db, get_write_db
from app.db.models.common import (
    MoleculeKind,
    SpeciesEntryStateKind,
    StationaryPointKind,
    StereoKind,
)
from app.db.models.external_source import ExternalSource, ExternalSourceRecord
from app.db.models.literature import Literature
from app.db.models.molecular_property_observation import (
    MolecularPropertyObservation,
)
from app.db.models.species import Species, SpeciesEntry
from app.db.models.submission import Submission, SubmissionRecordLink
from app.services.artifact_storage import head_artifact_object

FIXTURES = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "importers"
    / "thermoml"
    / "fixtures"
)
BENZENE_XML = FIXTURES / "cp_ideal_gas_statistical_thermodynamics.xml"
LIQUID_XML = FIXTURES / "cp_liquid_unsupported.xml"
SCHEMA_INVALID_XML = FIXTURES / "schema_invalid.xml"

KEY_HEADER = "Idempotency-Key"

_RIGHTS = {"license": "CC0-1.0", "depositor_attests_right_to_license": True}


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _payload(
    path: Path,
    *,
    filename: str = "article.xml",
    rights: dict | None = _RIGHTS,
    doi: str | None = None,
) -> dict:
    body: dict = {
        "filename": filename,
        "content_base64": _b64(path),
    }
    if rights is not None:
        body["rights"] = rights
    if doi is not None:
        body["doi"] = doi
    return body


def _payload_bytes(
    content: bytes,
    *,
    filename: str = "article.xml",
    rights: dict | None = _RIGHTS,
    doi: str | None = None,
) -> dict:
    body: dict = {
        "filename": filename,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }
    if rights is not None:
        body["rights"] = rights
    if doi is not None:
        body["doi"] = doi
    return body


#: F4 fixture (Phase C-E6 review round 2): schema-valid, ``sTitle``
#: present, ``sDOI`` **absent entirely** (both elements are
#: ``minOccurs="0"`` in the XSD -- verified against
#: ``backend/app/importers/thermoml/schema/ThermoML.xsd:121,149``).
#: Structurally modelled on ``cp_ideal_gas_statistical_thermodynamics.xml``
#: (Component/Compound keyed by RegNum/nOrgNum, matching the real archive).
#: No number here was read from any real article; every value is invented
#: for the test suite.
TITLED_NO_DOI_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<DataReport xmlns="http://www.iupac.org/namespaces/ThermoML"
            xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
            xsi:schemaLocation="http://www.iupac.org/namespaces/ThermoML ../schema/ThermoML.xsd">
  <Version>
    <nVersionMajor>4</nVersionMajor>
    <nVersionMinor>0</nVersionMinor>
  </Version>
  <Citation>
    <sAuthor>Doe, J.</sAuthor>
    <sPubName>J. Chem. Thermodyn.</sPubName>
    <yrPubYr>2013</yrPubYr>
    <sTitle>Fixture: titled citation with no DOI (invented values)</sTitle>
  </Citation>
  <Compound>
    <RegNum>
      <nOrgNum>1</nOrgNum>
    </RegNum>
    <sStandardInChI>InChI=1S/C6H6/c1-2-4-6-5-3-1/h1-6H</sStandardInChI>
    <sStandardInChIKey>UHOVQNZJYSORNB-UHFFFAOYSA-N</sStandardInChIKey>
    <sCommonName>benzene</sCommonName>
    <sFormulaMolec>C6H6</sFormulaMolec>
  </Compound>
  <PureOrMixtureData>
    <nPureOrMixtureDataNumber>1</nPureOrMixtureDataNumber>
    <Component>
      <RegNum>
        <nOrgNum>1</nOrgNum>
      </RegNum>
    </Component>
    <Property>
      <nPropNumber>1</nPropNumber>
      <Property-MethodID>
        <PropertyGroup>
          <HeatCapacityAndDerivedProp>
            <ePropName>Molar heat capacity at constant pressure, J/K/mol</ePropName>
            <sMethodName>statistical thermodynamics</sMethodName>
          </HeatCapacityAndDerivedProp>
        </PropertyGroup>
      </Property-MethodID>
      <PropPhaseID>
        <ePropPhase>Ideal gas</ePropPhase>
      </PropPhaseID>
      <ePresentation>Direct value, X</ePresentation>
    </Property>
    <PhaseID>
      <ePhase>Ideal gas</ePhase>
    </PhaseID>
    <Constraint>
      <nConstraintNumber>1</nConstraintNumber>
      <ConstraintID>
        <ConstraintType>
          <ePressure>Pressure, kPa</ePressure>
        </ConstraintType>
      </ConstraintID>
      <ConstraintPhaseID>
        <eConstraintPhase>Ideal gas</eConstraintPhase>
      </ConstraintPhaseID>
      <nConstraintValue>100</nConstraintValue>
      <nConstrDigits>1</nConstrDigits>
    </Constraint>
    <Variable>
      <nVarNumber>1</nVarNumber>
      <VariableID>
        <VariableType>
          <eTemperature>Temperature, K</eTemperature>
        </VariableType>
      </VariableID>
    </Variable>
    <NumValues>
      <VariableValue>
        <nVarNumber>1</nVarNumber>
        <nVarValue>298.15</nVarValue>
        <nVarDigits>5</nVarDigits>
      </VariableValue>
      <PropertyValue>
        <nPropNumber>1</nPropNumber>
        <nPropValue>82.44</nPropValue>
        <nPropDigits>4</nPropDigits>
      </PropertyValue>
    </NumValues>
  </PureOrMixtureData>
</DataReport>
"""


def _headers(key: str = "default") -> dict:
    # Idempotency-Key must be 16-200 chars (app.services.idempotency._KEY_PATTERN);
    # pad every test-supplied suffix out to that floor rather than hand-picking
    # a long-enough literal for each call site.
    full_key = f"e6-thermoml-test-{key}"
    return {KEY_HEADER: full_key.ljust(16, "0")}


def _ids_in(obj) -> set[str]:
    """Every dict key ending in ``_id`` (or bare ``id``), anywhere in the
    response tree -- the C-E6 brief's own scan-for-``_id``-keys check."""
    found: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "id" or key.endswith("_id"):
                found.add(key)
            found |= _ids_in(value)
    elif isinstance(obj, list):
        for item in obj:
            found |= _ids_in(item)
    return found


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_commit_persists_and_response_names_only_public_refs(
        self, client
    ) -> None:
        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML),
            headers=_headers("e6-happy-path"),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["schema_valid"] is True
        assert body["payload_count"] == 4
        assert body["inserted_count"] == 4
        assert body["submission_ref"] is not None
        assert body["submission_ref"].startswith("sub_")
        for disposition in body["dispositions"]:
            assert disposition["action"] == "inserted"
            assert disposition["observation_ref"].startswith("mpo_")
        assert not _ids_in(body), f"response leaked database ids: {_ids_in(body)}"

    def test_every_inserted_row_is_linked_to_the_submission(
        self, client, db_session
    ) -> None:
        """Surviving mutation from the first review round: skipping
        ``link_record`` on the upload path went unnoticed because nothing
        checked ``submission_record_link`` directly (only the response
        shape and row counts were asserted).

        Mutation: comment out the ``link_record(...)`` call inside
        ``_insert_one`` (or gate it to the archive path only) -- this goes
        red because inserted rows would have no matching
        ``submission_record_link`` row.
        """
        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML),
            headers=_headers("e6-ledger-check"),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["inserted_count"] == 4

        submission = db_session.execute(
            select(Submission).where(Submission.public_ref == body["submission_ref"])
        ).scalar_one()

        refs = [d["observation_ref"] for d in body["dispositions"]]
        assert len(refs) == 4
        observation_ids = db_session.execute(
            select(MolecularPropertyObservation.id).where(
                MolecularPropertyObservation.public_ref.in_(refs)
            )
        ).scalars().all()
        assert len(observation_ids) == 4

        links = db_session.execute(
            select(SubmissionRecordLink).where(
                SubmissionRecordLink.submission_id == submission.id,
                SubmissionRecordLink.record_id.in_(observation_ids),
            )
        ).scalars().all()
        assert len(links) == 4, (
            f"expected one submission_record_link per inserted row, got "
            f"{len(links)} for {len(observation_ids)} rows"
        )

    def test_doi_is_taken_from_the_files_own_sdoi(self, client) -> None:
        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML),
            headers=_headers("e6-doi-from-file"),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["doi"] == "10.1016/j.jct.2013.08.022"

    def test_species_entry_ref_populated_when_identity_resolves(
        self, client, db_session
    ) -> None:
        """A surviving mutation from the first review round: with no
        species seeded, every disposition's ``species_entry_ref`` is
        ``None`` regardless of whether the resolver is even wired up
        correctly, so nothing in the suite proved it worked. Seeding a
        species with the fixture's own InChIKey closes that gap.

        Mutation: make ``_thermoml_species_entry_ref`` always return
        ``None`` (e.g. drop the ``session.scalar(select(...))`` lookup) --
        this goes red because the resolved ref would then be missing.
        """
        species = Species(
            smiles="c1ccccc1",
            inchi_key="UHOVQNZJYSORNB-UHFFFAOYSA-N",
            charge=0,
            multiplicity=1,
            kind=MoleculeKind.molecule,
            stereo_kind=StereoKind.achiral,
        )
        db_session.add(species)
        db_session.flush()
        entry = SpeciesEntry(
            species_id=species.id,
            unmapped_smiles="c1ccccc1",
            kind=StationaryPointKind.minimum,
            electronic_state_kind=SpeciesEntryStateKind.ground,
        )
        db_session.add(entry)
        db_session.flush()

        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML),
            headers=_headers("e6-identity-resolves"),
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["inserted_count"] == 4
        refs = {d["species_entry_ref"] for d in body["dispositions"]}
        assert refs == {entry.public_ref}, refs
        for disposition in body["dispositions"]:
            assert disposition["identity_status"] == "resolved"


# ---------------------------------------------------------------------------
# Coded refusals
# ---------------------------------------------------------------------------


class TestCodedRefusals:
    def test_schema_invalid_document(self, client) -> None:
        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(SCHEMA_INVALID_XML),
            headers=_headers("e6-schema-invalid"),
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "thermoml_schema_invalid"

    def test_liquid_only_document_has_no_supported_content(
        self, client, db_session
    ) -> None:
        """A schema-valid, zero-payload upload must be refused *before*
        anything is written -- not just before the response claims
        success. F1 (Phase C-E6 review round 2, BLOCKER): a probe against
        the pre-fix code found ``external_source``/``external_source_
        record``/``literature`` rows and a MinIO object committed for a
        liquid-only upload despite the 422, with no submission wrapping
        them (and ``audit_sync_upload_failure`` then recording a *second*,
        failed submission on top).

        Mutation: move the ``payload_count == 0`` check in
        ``_run_pipeline`` back to after ``_get_or_create_custody`` (i.e.
        restore the old post-hoc check) -- the ``head_artifact_object``
        assertion goes red because the object-store write happens again.
        The row-count assertions stay green under that mutation, since the
        refusal path rolls the flushed rows back; they pin the committed
        state, not the ordering.
        """

        def _count(model) -> int:
            return len(db_session.execute(select(model)).all())

        # A unique nonce spliced into the fixture's own leading comment
        # makes the content -- and its SHA-256 -- unique to this test run,
        # so "no object exists yet" is a real precondition rather than one
        # that could spuriously hold or fail depending on what an earlier
        # run (including a reviewer's own manual repro of this exact
        # finding) already wrote to the shared local object store.
        import uuid

        xml_text = LIQUID_XML.read_text(encoding="utf-8")
        header, _, rest = xml_text.partition("\n")
        xml_bytes = f"{header}\n<!-- test-nonce: {uuid.uuid4().hex} -->\n{rest}".encode(
            "utf-8"
        )
        sha256 = hashlib.sha256(xml_bytes).hexdigest()

        before = {
            "molecular_property_observation": _count(MolecularPropertyObservation),
            "external_source": _count(ExternalSource),
            "external_source_record": _count(ExternalSourceRecord),
            "literature": _count(Literature),
            "submission": _count(Submission),
        }
        assert head_artifact_object(sha256) is None, (
            "unique nonce content already had an object in the local "
            "store -- this should be statistically impossible; "
            "investigate before trusting this test"
        )

        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload_bytes(xml_bytes, filename="liquid.xml"),
            headers=_headers("e6-liquid-only"),
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["code"] == "thermoml_no_supported_content"
        assert body["context"]["reasons"], "expected unsupported/rejected reasons"

        after = {
            "molecular_property_observation": _count(MolecularPropertyObservation),
            "external_source": _count(ExternalSource),
            "external_source_record": _count(ExternalSourceRecord),
            "literature": _count(Literature),
            "submission": _count(Submission),
        }
        assert after == before, f"refused upload wrote rows: {before} -> {after}"
        assert head_artifact_object(sha256) is None, (
            "refused upload wrote to the object store"
        )

    def test_oversized_file_is_refused(self, client, monkeypatch) -> None:
        monkeypatch.setattr(
            "app.api.routes.uploads.MAX_ENCODED_ARTIFACT_LEN", 16
        )
        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML),
            headers=_headers("e6-oversized"),
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["code"] == "thermoml_file_too_large"
        assert body["context"]["max_bytes"]

    def test_decoded_length_check_fires_independently_of_the_encoded_one(
        self, client, monkeypatch
    ) -> None:
        """Surviving mutation from the first review round: an encoded
        body under ``MAX_ENCODED_ARTIFACT_LEN`` but decoding to more than
        ``MAX_THERMOML_UPLOAD_BYTES`` bytes used to slip through, because
        no test exercised the decoded-length check on its own -- every
        existing oversize test tripped the *encoded* pre-check first,
        never reaching the decoded one. ``MAX_ENCODED_ARTIFACT_LEN`` is
        deliberately generous relative to the decoded cap (base64
        expansion plus padding slack), so the decoded check is not
        redundant -- this proves it does real, distinct work.

        Mutation: delete the ``if len(xml_bytes) > MAX_THERMOML_UPLOAD_
        BYTES:`` block in ``upload_thermoml`` -- this goes red because the
        request then succeeds instead of being refused.
        """
        monkeypatch.setattr(
            "app.api.routes.uploads.MAX_THERMOML_UPLOAD_BYTES", 10
        )
        # The fixture's base64 content is nowhere near
        # MAX_ENCODED_ARTIFACT_LEN's real (large) value, so only the
        # decoded-length check (against the patched-down 10-byte cap) can
        # fire here.
        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML),
            headers=_headers("e6-decoded-oversize"),
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["code"] == "thermoml_file_too_large"
        assert body["context"]["max_bytes"] == 10
        assert body["context"]["given_bytes"] > 10

    def test_doi_conflicting_with_the_files_own_sdoi_is_refused(
        self, client
    ) -> None:
        """F3 (Phase C-E6 review round 2, HIGH): the catalogue entry for
        ``thermoml_doi_conflict`` promises ``context`` carrying
        ``declared_doi``/``file_doi`` and ``Shape.relationship`` -- the
        route used to raise with no context at all.

        Mutation: drop the ``context={...}`` kwarg from the
        ``CodedValidationError("thermoml_doi_conflict", ...)`` raise site
        -- this goes red on the context-key assertions below.
        """
        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML, doi="10.1000/not-the-real-doi"),
            headers=_headers("e6-doi-conflict"),
        )
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert body["code"] == "thermoml_doi_conflict"
        assert body["context"]["declared_doi"] == "10.1000/not-the-real-doi"
        assert body["context"]["file_doi"] == "10.1016/j.jct.2013.08.022"

    def test_invalid_base64_is_refused(self, client) -> None:
        body = _payload(BENZENE_XML)
        body["content_base64"] = "not valid base64!!"
        resp = client.post(
            "/api/v1/uploads/thermoml", json=body, headers=_headers("e6-bad-b64")
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "thermoml_invalid_base64"

    def test_non_ascii_base64_is_refused(self, client) -> None:
        """``base64.b64decode(s, validate=True)`` raises a plain
        ``ValueError`` -- NOT ``binascii.Error`` -- for non-ASCII input
        (Phase C-E6 review round 2, F2: measured, ``"YWJjé"`` ->
        ``ValueError('string argument should contain only ASCII
        characters')``). Without the ``isascii()`` pre-check this fell
        through to the generic ``ValueError`` handler as
        ``validation_error`` instead of ``thermoml_invalid_base64``.

        Mutation: drop the ``if not request.content_base64.isascii():``
        pre-check in ``upload_thermoml`` -- this goes red because the
        code becomes ``validation_error``.
        """
        body = _payload(BENZENE_XML)
        body["content_base64"] = "YWJjé"
        resp = client.post(
            "/api/v1/uploads/thermoml", json=body, headers=_headers("e6-non-ascii")
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["code"] == "thermoml_invalid_base64"

    def test_object_store_unavailable_is_refused_not_silently_degraded(
        self, client, monkeypatch
    ) -> None:
        """F5 (Phase C-E6 review round 2, MEDIUM): when the object store is
        down, the upload path must refuse the request -- not fall back to
        writing a custody row whose ``raw_uri`` is ``upload:<filename>``,
        a label that points at nothing (unlike the archive path's member
        path, which names a real location inside the pinned archive).

        ``ArtifactStorageUnavailable`` has its own registered FastAPI
        handler (503 ``artifact_storage_unavailable``); this route lets it
        propagate rather than catching and reinterpreting it.

        Mutation: set ``allow_member_path_fallback=True`` on the upload
        path's ``_run_pipeline`` call -- this goes red because the request
        would then succeed (201) with a bogus ``raw_uri`` instead of 503.
        """
        from app.services.artifact_storage import ArtifactStorageUnavailable

        def _down(content: bytes, sha256: str) -> str:
            raise ArtifactStorageUnavailable("simulated object store outage")

        monkeypatch.setattr(
            "app.services.artifact_storage.store_artifact", _down
        )

        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML),
            headers=_headers("e6-store-down"),
        )
        assert resp.status_code == 503, resp.text
        assert resp.json()["code"] == "artifact_storage_unavailable"

    def test_missing_rights_is_refused_by_ordinary_field_requiredness(
        self, client
    ) -> None:
        """``rights`` is a required field on ``ThermoMLUploadRequest`` --
        omitting it hits the same generic Pydantic 422 every other
        required field on every other schema in this codebase already
        produces. There is no bespoke "rights missing" refusal to
        invent: DepositRights is optional everywhere else in TCKDB
        (see its own docstring -- absence bites at release time), but
        this route's whole content is a third-party document with no
        source_terms fallback, so rights is required here specifically.
        """
        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML, rights=None),
            headers=_headers("e6-no-rights"),
        )
        assert resp.status_code == 422, resp.text
        # Not just the status: the framework's own validation-error body
        # names the missing field, so a client can tell "rights" apart
        # from any other 422 on this route.
        locs = [tuple(err["loc"]) for err in resp.json()["detail"]]
        assert any("rights" in loc for loc in locs), locs

    def test_missing_idempotency_key_is_refused(self, client) -> None:
        """Unlike every sibling ``/uploads/*`` route, this one requires
        ``Idempotency-Key``, by the C-E6 decision (2026-09-20) -- not by
        DR-0024, which leaves the header optional in general. The route
        declares a second, required binding of the same header, so
        FastAPI's ordinary missing-required-header 422 fires -- the
        existing validation mechanism, not a new bespoke refusal.

        Mutation: drop the required ``_idempotency_key_required: str =
        Header(...)`` parameter from ``upload_thermoml`` -- this goes red
        because a keyless request would then succeed (201).
        """
        resp = client.post(
            "/api/v1/uploads/thermoml", json=_payload(BENZENE_XML)
        )
        assert resp.status_code == 422, resp.text
        # Not just the status: the framework's own validation-error body
        # names the missing header.
        locs = [tuple(err["loc"]) for err in resp.json()["detail"]]
        assert any(KEY_HEADER in loc for loc in locs), locs


# ---------------------------------------------------------------------------
# Literature resolution (Phase C-E6 review round 2, F4, MEDIUM):
# a titled file with no sDOI used to crash the literature step with a raw
# pydantic ValidationError, because LiteratureUploadRequest requires either
# an identifier or both kind+title, and this importer never sets kind.
# Fixed rule: literature resolves by DOI only (file's own sDOI, else the
# caller's doi); when neither exists, no literature row is created, and no
# manual kind+title record is fabricated on the depositor's behalf.
# ---------------------------------------------------------------------------


class TestLiteratureResolution:
    def test_titled_file_without_sdoi_and_no_caller_doi_succeeds_no_literature(
        self, client, db_session
    ) -> None:
        before = len(db_session.execute(select(Literature)).all())

        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload_bytes(TITLED_NO_DOI_XML),
            headers=_headers("e6-titled-no-doi-no-caller-doi"),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["inserted_count"] == 1
        # No real DOI from either source -- the record-key basis falls
        # back to the content-digest-derived synthetic key, which is
        # never a real DOI and must never reach literature resolution
        # (see ``literature_doi_fallback`` on ``_run_pipeline``).
        assert resp.json()["doi"].startswith("upload:")

        after = len(db_session.execute(select(Literature)).all())
        assert after == before, "no DOI from either source -- no literature row"

    def test_titled_file_without_sdoi_but_with_caller_doi_creates_literature(
        self, client, db_session
    ) -> None:
        """The caller-supplied ``doi`` IS the literature DOI when the file
        has none.

        Mutation: change ``literature_doi_fallback`` back to always being
        ``None`` on the upload path (i.e. never fall back to the caller's
        ``doi``) -- this goes red because no literature row appears.
        """
        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload_bytes(
                TITLED_NO_DOI_XML, doi="10.1016/caller-supplied-doi"
            ),
            headers=_headers("e6-titled-no-doi-with-caller-doi"),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["inserted_count"] == 1
        assert resp.json()["doi"] == "10.1016/caller-supplied-doi"

        literature = db_session.execute(
            select(Literature).where(
                Literature.doi == "10.1016/caller-supplied-doi"
            )
        ).scalar_one()
        assert literature is not None


# ---------------------------------------------------------------------------
# Idempotency -- required on this route by the C-E6 decision, unlike its
# siblings (DR-0024 itself leaves the header optional in general).
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_replay_with_same_key_returns_identical_response_no_new_rows(
        self, client, db_session
    ) -> None:
        from sqlalchemy import select

        from app.db.models.molecular_property_observation import (
            MolecularPropertyObservation,
        )

        headers = _headers("e6-replay-test-key-001")
        payload = _payload(BENZENE_XML)

        first = client.post(
            "/api/v1/uploads/thermoml", json=payload, headers=headers
        )
        assert first.status_code == 201, first.text
        rows_after_first = db_session.execute(
            select(MolecularPropertyObservation)
        ).scalars().all()

        second = client.post(
            "/api/v1/uploads/thermoml", json=payload, headers=headers
        )
        assert second.status_code == 201, second.text
        assert second.headers.get("Idempotency-Replayed") == "true"
        assert second.json() == first.json()

        rows_after_second = db_session.execute(
            select(MolecularPropertyObservation)
        ).scalars().all()
        assert len(rows_after_second) == len(rows_after_first)

    def test_same_key_different_payload_conflicts(self, client) -> None:
        headers = _headers("e6-conflict-test-key-001")
        resp1 = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML),
            headers=headers,
        )
        assert resp1.status_code == 201, resp1.text

        resp2 = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML, filename="different.xml"),
            headers=headers,
        )
        assert resp2.status_code == 409, resp2.text
        assert resp2.json()["code"] == "idempotency_conflict"


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestAuthorization:
    def test_unauthenticated_returns_401(self, db_session) -> None:
        app = create_app()
        app.dependency_overrides[get_db] = lambda: db_session
        app.dependency_overrides[get_write_db] = lambda: db_session
        anon_client = TestClient(app)
        resp = anon_client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML),
            headers=_headers("e6-anon"),
        )
        assert resp.status_code == 401

    def test_ordinary_user_role_suffices(self, client) -> None:
        # ``client`` is already a plain user-role account (see
        # backend/tests/conftest.py::_api_test_user) -- this is a
        # standard upload, not a curator-only action.
        resp = client.post(
            "/api/v1/uploads/thermoml",
            json=_payload(BENZENE_XML),
            headers=_headers("e6-user-role"),
        )
        assert resp.status_code == 201, resp.text
