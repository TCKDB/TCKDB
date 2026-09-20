"""Workflow service: persist ThermoML Cp(T) observation payloads.

Phase C-E3. Closes the gap left by the E2 importer package
(``app.importers.thermoml``): that package turns one archive article into
:class:`~app.schemas.entities.molecular_property_observation.
MolecularPropertyObservationCreate` payloads but never touches the
database. This module is the first ThermoML-side module allowed to write.

Mirrors ``app.services.cccbdb_molecular_property_import`` wherever the two
importers share a shape (dispositions, dry-run/commit, per-row SAVEPOINT,
identity resolution via ``app.services.external_observation_identity``) and
adds what the CCCBDB path does not need: a source-custody row
(``external_source`` / ``external_source_record``), a literature citation,
and a submission wrapper with a ``source_terms`` rights attestation quoting
NIST/TRC's own terms verbatim.

Two public entry points, one shared core (Phase C-E6)
-------------------------------------------------------

:func:`import_thermoml_cp_article` (the original, C-E3) persists one
article selected from the pinned NIST bulk archive. :func:`import_
thermoml_cp_upload` (C-E6) persists one ThermoML document *anyone*
uploaded directly -- a CLI ``--file`` or the ``POST /uploads/thermoml``
route -- never fetched from NIST. Both validate -> parse -> map -> persist
through the same private ``_run_pipeline`` core; they differ only in
**custody** (who/where the bytes came from) and **rights** (whose
agreement the deposit stands on):

* Archive: ``external_source`` names NIST/TRC; the custody row's
  ``container_digest`` is the pinned archive's own SHA-256; the standing
  rights attestation is an explicit ``source_terms`` row quoting NIST's
  published terms verbatim (see ``_open_submission_with_source_terms_
  attestation``) -- nobody at deposit time "agreed" to license these rows,
  they were taken under a source's own terms.
* Upload: ``external_source`` names the depositor-upload *channel*
  (``UPLOAD_SOURCE_NAME`` -- one shared row; *who* uploaded is recorded on
  the submission itself, not the source), there is no bulk-container
  digest to cite (``container_digest=None`` -- honest: there is no
  container), and the standing rights attestation is the caller-supplied
  ``DepositRights`` recorded as an ordinary ``depositor_agreement`` (see
  ``_open_upload_submission_for_depositor``) -- never NIST's
  ``source_terms``, because this deposit did not come from NIST.

``doi`` is also handled differently: the archive path always receives an
operator-known DOI (the operator picked that specific article to fetch).
The upload path's ``doi`` is optional -- taken from the file's own
``sDOI`` citation element when the caller does not supply one, refused
with :class:`ThermoMLDoiConflictError` when both are present and disagree,
and falls back to a content-digest-derived synthetic key
(``"upload:<sha256[:16]>"``) when neither is available, so every custody
row and dedupe key still has *something* stable to key on. Literature
resolution is unaffected by this: it always reads the file's own
``sDOI``/title/etc. (``mapping_result.literature``), never the caller's
``doi`` argument -- an uploaded file with no citation of its own resolves
no literature row, exactly as ``map_document`` already produces.

Design contract
----------------

* **Layering.** This module imports ``app.importers.thermoml``'s parser,
  mapper and validator (pure functions, no I/O) -- never its archive
  fetch/select functions, and never a per-family upload workflow module (this service
  composes lower-level primitives -- rights, submission, literature,
  identity -- directly). The importer package must never import this
  module back -- enforced by
  ``backend/tests/importers/thermoml/test_layering.py`` and mirrored here
  by ``test_service_does_not_import_upload_workflows`` in this service's
  own test module.
* **Custody is get-or-create.** ``external_source`` is keyed on
  ``(source_name, source_release)``; ``external_source_record`` is keyed on
  ``(external_source_id, source_record_key, content_sha256, parser_version,
  mapping_version)`` (the DB's own ``uq_external_source_record_identity``).
  A second run with the *same* parser/mapping version reuses both rows. A
  changed parser or mapping version appends a new custody row rather than
  overwriting the old one.
* **The submission is opened only when there is something to link.** A
  second run of the same article, once every observation row is already
  present, resolves every row to ``duplicate`` and opens **no** new
  ``Submission`` -- an audit wrapper with nothing newly deposited to wrap
  would be a submission whose only content is "nothing happened twice",
  which is not a contribution event worth auditing. This is decided by a
  cheap dedupe-key pre-check *before* any row is written, so it never races
  the per-row ``INSERT ... ON CONFLICT DO NOTHING`` that does the actual,
  authoritative dedupe.
* **Two attestations, one standing -- archive path only.**
  ``open_upload_submission`` always records the deposit's ``rights``
  fragment as a ``depositor_agreement`` attestation
  (``app.services.rights.attest_from_deposit`` -- it has no other basis to
  record). On the archive path that is not the basis the deposit actually
  stands on: these rows were not licensed by a depositor's say-so, they
  were taken from NIST/TRC's own published terms. So
  :func:`_open_submission_with_source_terms_attestation` records a second,
  explicit ``source_terms`` attestation quoting ``TERMS_TEXT`` verbatim
  right after -- it supersedes the automatic ``depositor_agreement`` row,
  so the *standing* attestation for the submission is ``source_terms``.
  Exactly one ``source_terms`` attestation is recorded per submission;
  never called twice. **The upload path never does this** -- an uploaded
  file's ``depositor_agreement`` (the caller's own ``DepositRights``) *is*
  the basis it stands on, so it is the one and only attestation
  recorded (:func:`_open_upload_submission_for_depositor`); a
  ``source_terms`` row there would misattribute the deposit to a source
  that never produced it.
* **Reject, don't guess.** A schema-invalid document is never parsed
  (E2's ``validate_bytes`` already refuses it); this service does not
  create a custody row, a submission, or any observation for it.
* **Dry-run by default.** ``commit=False`` runs the full pipeline inside
  the caller's transaction without committing. ``commit=True`` commits on
  success and rolls back on unexpected error. A dry run never writes to
  the object store either: ``_raw_uri_for`` computes the would-be
  content-addressed URI locally and reports it via a
  ``ThermoMLCpImportResult.warnings`` entry instead of calling
  ``app.services.artifact_storage.store_artifact``; only ``commit=True``
  actually uploads.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from tckdb_schemas.literature import LiteratureUploadRequest
from tckdb_schemas.rights import DepositRights

from app.db.models.app_user import AppUser
from app.db.models.common import (
    ExternalSourceRecordKind,
    RightsBasisKind,
    SubmissionKind,
    SubmissionRecordType,
    SubmissionSourceKind,
)
from app.db.models.external_source import ExternalSource, ExternalSourceRecord
from app.db.models.molecular_property_observation import (
    MolecularPropertyObservation,
)
from app.importers.thermoml import (
    ARCHIVE_SHA256,
    ARCHIVE_URL,
    MAPPING_VERSION,
    PARSER_VERSION,
    SOURCE_DATABASE_DOI,
    SOURCE_NAME,
    SOURCE_RELEASE,
    TERMS_TEXT,
    TERMS_URL,
    XSD_SHA256,
)
from app.importers.thermoml.archive import ArticleBytes
from app.importers.thermoml.mapping import map_document
from app.importers.thermoml.parser import parse_thermoml_document
from app.importers.thermoml.validate import validate_bytes
from app.services.external_observation_identity import (
    IDENTITY_AMBIGUOUS,
    IDENTITY_NOT_FOUND,
    IDENTITY_RESOLVED,
    resolve_identity,
)
from app.services.literature_resolution import resolve_or_create_literature
from app.services.rights import record_attestation
from app.services.submission import link_record
from app.services.upload_submission import (
    UploadSubmissionContext,
    mark_upload_ingested,
    open_upload_submission,
)

_logger = logging.getLogger(__name__)

#: Mirrors the ``mpo_dedupe_key`` UNIQUE constraint on
#: ``molecular_property_observation`` -- shared with
#: ``app.services.cccbdb_molecular_property_import`` by construction (one
#: table, one dedupe key), not by import: each importer keeps its own copy
#: so neither depends on the other's internals.
_DEDUPE_CONSTRAINT_NAME = "mpo_dedupe_key"
_DEDUPE_COLUMNS = (
    "species_entry_id",
    "property_kind",
    "scientific_origin",
    "external_source_name",
    "external_source_release",
    "external_source_url",
    "external_source_record_key",
    "reference_label",
    "scalar_value",
    "temperature_k",
)

#: This importer's fixed identity for ``external_source_record.parser_name``.
_PARSER_NAME = "thermoml"

#: Schema id recorded on the custody row: the XSD this article validated
#: against, named and pinned by digest, not by ThermoML's own ``<Version>``
#: element (which is per-document, not per-schema-file).
_SCHEMA_ID = f"ThermoML.xsd v4.0 sha256:{XSD_SHA256}"

#: Custody identity for the depositor-upload channel (Phase C-E6) -- one
#: shared ``external_source`` row for every direct upload, as opposed to
#: the NIST bulk-archive row above (``SOURCE_NAME``/``SOURCE_RELEASE``).
#: *Who* uploaded a given document is already recorded on the submission
#: itself (``created_by``, and the ``depositor_agreement`` attestation
#: carrying their ``rights``) -- this row names the *channel* a document
#: arrived through, not the individual depositor, mirroring how
#: ``SOURCE_NAME``/``SOURCE_RELEASE`` name the NIST *database*, not the
#: NIST staff member who curated a given article into it.
UPLOAD_SOURCE_NAME = "TCKDB depositor ThermoML upload"
UPLOAD_SOURCE_RELEASE = "v1"

#: Recorded as ``external_source_record.source_uri`` for an uploaded
#: document. Not a fetched URL -- there is nothing to fetch, the bytes
#: arrived in the request/CLI argument -- but the column is ``NOT NULL``,
#: and a constant here (rather than, say, the object-store key duplicated
#: from ``raw_uri``) makes the two custody channels distinguishable by
#: ``source_uri`` alone at a glance.
UPLOAD_SOURCE_URI = "tckdb:depositor-upload"

#: Prefix for the synthetic record-key basis used when an uploaded
#: document carries no DOI at all (neither caller-supplied nor the file's
#: own ``sDOI``) -- see :func:`_resolve_upload_doi`.
_UPLOAD_DOI_FALLBACK_PREFIX = "upload"


class ThermoMLDoiConflictError(ValueError):
    """A caller-supplied ``doi`` disagrees with the file's own ``sDOI``.

    Raised by :func:`_resolve_upload_doi` (via :func:`import_thermoml_cp_
    upload`'s ``resolve_doi`` closure) *after* the document has been
    schema-validated and parsed (the file's own ``sDOI`` cannot be read
    before that), but before anything is written -- reject, don't guess
    which one is right.

    :ivar declared_doi: The caller-supplied ``doi``, verbatim.
    :ivar file_doi: The uploaded document's own ``Citation/sDOI``, verbatim.
        Both are non-``None`` whenever this is raised (see
        :func:`_resolve_upload_doi` -- it only raises when both are
        present and disagree).
    """

    def __init__(self, message: str, *, declared_doi: str, file_doi: str) -> None:
        super().__init__(message)
        self.declared_doi = declared_doi
        self.file_doi = file_doi


class ThermoMLNoSupportedContentError(ValueError):
    """A schema-valid document mapped zero Cp(T) payloads.

    Raised only on the **upload** path (:func:`import_thermoml_cp_upload`,
    via ``_run_pipeline``'s ``refuse_when_unsupported=True``), and only
    right after :func:`~app.importers.thermoml.mapping.map_document` --
    before any custody row, literature row or object-store write. The
    archive path (:func:`import_thermoml_cp_article`,
    ``refuse_when_unsupported=False``) never raises this: an archive scan
    across many DOIs has always completed normally with
    ``inserted_count == 0`` for an article with no usable content (see
    ``TestSMethodNameAllowlist.test_unknown_smethodname_string_is_rejected``),
    and nothing in this change disturbs that -- only the upload route,
    where a 422 response and a silently-populated custody trail would
    disagree about whether anything happened, needed the harder refusal.

    :ivar mapping_report: The full ``MappingReport`` dict (``transformed``,
        ``retained_only``, ``unsupported``, ``rejected``, ``counts``) --
        carried on the exception itself (not read off ``ThermoMLCpImport
        Result``) because a raised exception never hands the caller that
        result object.
    :ivar reasons: Sorted, deduplicated ``reason`` strings pulled from
        ``mapping_report["unsupported"]`` and ``["rejected"]`` -- the
        list a caller actually wants to show, without re-deriving it.
    """

    def __init__(self, mapping_report: dict[str, Any]) -> None:
        self.mapping_report = mapping_report
        self.reasons = sorted(
            {
                str(entry.get("reason"))
                for entry in (
                    *mapping_report.get("unsupported", []),
                    *mapping_report.get("rejected", []),
                )
                if entry.get("reason")
            }
        )
        super().__init__(
            "thermoml_no_supported_content: no mappable ideal-gas or "
            "real-gas Cp(T) content was found in this document"
        )


_ACTION_WOULD_INSERT = "would_insert"
_ACTION_INSERTED = "inserted"
_ACTION_DUPLICATE = "duplicate"
_ACTION_SKIPPED = "skipped"

#: No ``_ACTION_INVALID`` here, unlike ``app.services.
#: cccbdb_molecular_property_import``: that importer validates raw
#: untyped dicts at the service layer and can meet a row that fails
#: pydantic validation. ``mapping_result.payloads`` here is already a
#: ``list[MolecularPropertyObservationCreate]`` built by ``app.importers.
#: thermoml.mapping.map_document`` -- a row that fails mapping/validation
#: never becomes a payload in the first place (it is dropped and reported
#: in ``mapping_result.report`` instead), so this service's per-payload
#: loop has no "invalid" outcome to represent.


def _now_naive_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class ObservationDisposition:
    """Per-observation outcome from one import run."""

    property_kind: str | None
    external_source_record_key: str | None
    identity_status: str
    species_entry_id: int | None
    action: str
    warnings: list[str] = field(default_factory=list)
    #: The inserted row's public ref (``mpo_...``), set only when
    #: ``action == "inserted"`` (a real commit). ``None`` for a dry-run
    #: ``would_insert`` -- no row exists yet to have a ref -- and for
    #: ``duplicate``/``skipped``. Added for Phase C-E6 so the upload route
    #: can report per-row outcomes without ever naming a database id.
    observation_ref: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "property_kind": self.property_kind,
            "external_source_record_key": self.external_source_record_key,
            "identity_status": self.identity_status,
            "species_entry_id": self.species_entry_id,
            "action": self.action,
            "warnings": list(self.warnings),
            "observation_ref": self.observation_ref,
        }


@dataclass
class ThermoMLCpImportResult:
    """Aggregate report from one :func:`import_thermoml_cp_article` (the
    archive path) or :func:`import_thermoml_cp_upload` (the depositor
    upload path) run -- both entry points return this same shape."""

    doi: str = ""
    schema_valid: bool = True
    payload_count: int = 0
    would_insert_count: int = 0
    inserted_count: int = 0
    duplicate_count: int = 0
    skipped_count: int = 0
    resolved_identity_count: int = 0
    unresolved_identity_count: int = 0
    ambiguous_identity_count: int = 0
    not_found_identity_count: int = 0
    submission_id: int | None = None
    submission_ref: str | None = None
    external_source_id: int | None = None
    external_source_record_id: int | None = None
    external_source_record_created: bool = False
    literature_id: int | None = None
    warnings: list[str] = field(default_factory=list)
    dispositions: list[ObservationDisposition] = field(default_factory=list)
    #: {transformed, retained_only, unsupported, rejected, counts} from
    #: ``app.importers.thermoml.mapping.MappingReport``, added for Phase
    #: C-E6 so a caller (route or CLI) can report *why* zero rows mapped
    #: without re-running the mapper. ``None`` only when the document was
    #: schema-invalid (never reached the mapper at all).
    mapping_report: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "doi": self.doi,
            "schema_valid": self.schema_valid,
            "payload_count": self.payload_count,
            "would_insert_count": self.would_insert_count,
            "inserted_count": self.inserted_count,
            "duplicate_count": self.duplicate_count,
            "skipped_count": self.skipped_count,
            "resolved_identity_count": self.resolved_identity_count,
            "unresolved_identity_count": self.unresolved_identity_count,
            "ambiguous_identity_count": self.ambiguous_identity_count,
            "not_found_identity_count": self.not_found_identity_count,
            "submission_id": self.submission_id,
            "submission_ref": self.submission_ref,
            "external_source_id": self.external_source_id,
            "external_source_record_id": self.external_source_record_id,
            "external_source_record_created": self.external_source_record_created,
            "literature_id": self.literature_id,
            "warnings": list(self.warnings),
            "dispositions": [d.to_json() for d in self.dispositions],
            "mapping_report": self.mapping_report,
        }


# ---------------------------------------------------------------------------
# Custody: get-or-create ExternalSource / ExternalSourceRecord
# ---------------------------------------------------------------------------


def _get_or_create_external_source(
    session: Session,
    *,
    name: str,
    release: str,
    database_doi: str | None = None,
    terms_url: str | None = None,
    terms_text: str | None = None,
) -> ExternalSource:
    """Get-or-create keyed on ``(name, release)`` -- shared by both the
    NIST-archive channel (Phase C-E3) and the depositor-upload channel
    (Phase C-E6); which constants a caller passes is what distinguishes
    them (see module docstring)."""
    existing = session.scalar(
        select(ExternalSource).where(
            ExternalSource.source_name == name,
            ExternalSource.source_release == release,
        )
    )
    if existing is not None:
        return existing
    row = ExternalSource(
        source_name=name,
        source_release=release,
        source_database_doi=database_doi,
        terms_url=terms_url,
        terms_text=terms_text,
    )
    session.add(row)
    session.flush()
    return row


def _get_or_create_custody(
    session: Session,
    *,
    external_source: ExternalSource,
    article: ArticleBytes,
    doi: str,
    schema_valid: bool,
    mapping_report_json: dict[str, Any],
    retrieved_at: datetime,
    commit: bool,
    source_uri: str,
    container_digest: str | None,
    allow_member_path_fallback: bool,
) -> tuple[ExternalSourceRecord, bool, bool]:
    """Reuse the custody row for this ``(doi, content, parser, mapping)``
    identity if one already exists; otherwise insert one.

    Matches ``uq_external_source_record_identity`` exactly, so a second run
    with the same parser/mapping version finds and reuses this row instead
    of appending a duplicate; a changed parser or mapping version misses and
    appends a new one, as the model's docstring promises.

    Returns ``(record, created, would_store)``. ``would_store`` is only
    ever ``True`` when a new row was created during a dry run (see
    :func:`_raw_uri_for`) -- an existing row needed no object-store call at
    all.

    :param allow_member_path_fallback: Forwarded to :func:`_raw_uri_for`
        -- ``True`` on the archive path, ``False`` on the upload path.
    """

    existing = session.scalar(
        select(ExternalSourceRecord).where(
            ExternalSourceRecord.external_source_id == external_source.id,
            ExternalSourceRecord.source_record_key == doi,
            ExternalSourceRecord.content_sha256 == article.xml_sha256,
            ExternalSourceRecord.parser_version == PARSER_VERSION,
            ExternalSourceRecord.mapping_version == MAPPING_VERSION,
        )
    )
    if existing is not None:
        return existing, False, False

    raw_uri, would_store = _raw_uri_for(
        article, commit=commit, allow_member_path_fallback=allow_member_path_fallback
    )
    row = ExternalSourceRecord(
        external_source_id=external_source.id,
        # Both channels snapshot the same document shape (a ThermoML
        # DataReport for one article), whether it came from the NIST bulk
        # archive or was uploaded directly -- see the C-E6 PR description
        # for why this is not a new ``ExternalSourceRecordKind`` member.
        record_kind=ExternalSourceRecordKind.thermoml_article,
        source_uri=source_uri,
        source_record_key=doi,
        retrieved_at=retrieved_at,
        content_sha256=article.xml_sha256,
        content_length=len(article.xml),
        raw_uri=raw_uri,
        container_digest=container_digest,
        schema_id=_SCHEMA_ID,
        schema_valid=schema_valid,
        parser_name=_PARSER_NAME,
        parser_version=PARSER_VERSION,
        mapping_version=MAPPING_VERSION,
        mapping_report_json=mapping_report_json,
    )
    session.add(row)
    session.flush()
    return row, True, would_store


def _raw_uri_for(
    article: ArticleBytes, *, commit: bool, allow_member_path_fallback: bool
) -> tuple[str, bool]:
    """The custody row's ``raw_uri``: a content-addressed object-store key
    when the store is reachable, else the local snapshot member path --
    on the archive path only.

    Returns ``(raw_uri, would_store)``. In a dry run (``commit=False``)
    this **never** calls the object store: it computes the same
    content-addressed key :func:`~app.services.artifact_storage.
    store_artifact` would use, entirely locally, and returns it with
    ``would_store=True``. Only ``commit=True`` actually uploads -- storing
    the bytes is best-effort provenance, not a precondition for recording
    that the article was seen and parsed, and a preview run must leave no
    trace in the object store (or in the capacity-refusal log a failed
    upload could append to; see ``_raise_write_refusal``/``record_refusal``
    in :mod:`app.services.artifact_storage`, which this function no longer
    reaches at all when ``commit`` is ``False``).

    :param allow_member_path_fallback: ``True`` on the archive path only.
        There, ``article.member_paths[0]`` is a real, meaningful location
        -- the tar member path inside the pinned, digest-verified bulk
        archive -- so falling back to it when the object store is down is
        honest degraded provenance. On the upload path
        (``allow_member_path_fallback=False``) there is no such fallback:
        an uploaded document's ``member_paths`` is a synthetic label
        (``f"upload:{filename}"``), not a retrievable location, and
        writing it as ``raw_uri`` would point a reader at nothing (Phase
        C-E6 review round 2, F5). So this re-raises
        ``ArtifactStorageUnavailable`` instead of swallowing it -- the
        exception has its own registered FastAPI handler
        (``app.api.errors``, ``503 artifact_storage_unavailable``), so
        letting it propagate is the correct refusal, not a gap.
    """
    from app.services.artifact_storage import (
        S3_BUCKET,
        ArtifactStorageUnavailable,
        content_addressed_key,
        store_artifact,
    )

    if not commit:
        key = content_addressed_key(article.xml_sha256)
        return f"s3://{S3_BUCKET}/{key}", True

    try:
        return store_artifact(article.xml, article.xml_sha256), False
    except ArtifactStorageUnavailable:
        if not allow_member_path_fallback:
            raise
        _logger.warning(
            "object store unavailable while snapshotting ThermoML article "
            "xml_sha256=%s; falling back to the archive member path",
            article.xml_sha256,
        )
        return article.member_paths[0], False


# ---------------------------------------------------------------------------
# Dedupe pre-check (read-only; decides whether to open a submission at all)
# ---------------------------------------------------------------------------


def _existing_dedupe_id(
    session: Session, row_kwargs: dict[str, Any]
) -> int | None:
    model = MolecularPropertyObservation
    conditions = [
        getattr(model, col).is_not_distinct_from(row_kwargs.get(col))
        for col in _DEDUPE_COLUMNS
    ]
    return session.execute(
        select(model.id).where(and_(*conditions))
    ).scalar_one_or_none()


def _payload_to_row_kwargs(
    payload,
    *,
    species_entry_id: int | None,
    created_by: int,
    literature_id: int | None,
    external_source_record_id: int,
) -> dict[str, Any]:
    data = payload.model_dump(mode="json")
    data["species_entry_id"] = species_entry_id
    data["created_by"] = created_by
    data["literature_id"] = literature_id
    data["external_source_record_id"] = external_source_record_id
    return data


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _run_pipeline(
    session: Session,
    *,
    article: ArticleBytes,
    result: ThermoMLCpImportResult,
    actor: AppUser,
    commit: bool,
    retrieved_at: datetime,
    external_source_kwargs: dict[str, Any],
    custody_source_uri: str,
    custody_container_digest: str | None,
    open_submission: Callable[[Session], UploadSubmissionContext],
    resolve_doi: Callable[[Any], str],
    refuse_when_unsupported: bool,
    allow_member_path_fallback: bool,
    literature_doi_fallback: str | None,
    document_noun: str,
) -> None:
    """Shared validate -> parse -> map -> persist core for both public
    entry points (Phase C-E6). Mutates ``result`` in place.

    Returns (after a single ``session.rollback()``) without persisting
    anything when the document is schema-invalid -- callers check
    ``result.schema_valid`` immediately after calling this and return
    early themselves rather than falling through to their own
    commit/rollback trailer, so that rollback stays the *only* rollback
    call on this path (matching the pre-refactor behaviour exactly).
    ``refuse_when_unsupported=True`` adds a second such early exit, this
    time by raising rather than returning -- see that parameter.

    :param external_source_kwargs: Forwarded to
        :func:`_get_or_create_external_source` -- the NIST constants for
        the archive path, the upload-channel constants for the upload
        path.
    :param custody_source_uri: ``external_source_record.source_uri`` for
        the new custody row, if one is created.
    :param custody_container_digest: ``external_source_record.
        container_digest`` -- the pinned archive's SHA-256 for the archive
        path, ``None`` for the upload path (there is no container).
    :param open_submission: Called (at most once, only when at least one
        row would newly insert) to open the submission wrapper and record
        its rights attestation. Differs by path -- see module docstring.
    :param resolve_doi: Called with the parsed document to produce the
        DOI/record-key basis for :func:`~app.importers.thermoml.mapping.
        map_document` and the custody row. The archive path ignores its
        argument and returns the operator-supplied DOI unchanged; the
        upload path reconciles the caller's optional ``doi`` against the
        document's own ``sDOI`` (see :func:`_resolve_upload_doi`). May
        raise :class:`ThermoMLDoiConflictError`.
    :param refuse_when_unsupported: ``True`` for the upload path only.
        When the document is schema-valid but maps zero Cp(T) payloads,
        raises :class:`ThermoMLNoSupportedContentError` immediately after
        mapping and *before* any custody row, literature row or
        object-store write -- a request the caller will see refused must
        not leave a custody trail behind it (Phase C-E6 review round 2,
        F1). ``False`` on the archive path preserves its original,
        already-tested behaviour: an article with no usable content
        completes normally (``inserted_count == 0``, custody still
        recorded as "this article was checked"), because an archive scan
        across many DOIs is not a single request a client can see
        refused -- there is nothing for the depositor-facing inconsistency
        this guards against to apply to.
    :param allow_member_path_fallback: Forwarded to
        :func:`_get_or_create_custody` / :func:`_raw_uri_for`. ``True`` on
        the archive path (its ``member_paths`` names a real tar-member
        location inside the pinned, digest-verified archive, so falling
        back to it when the object store is down is honest degraded
        provenance); ``False`` on the upload path (its ``member_paths``
        is a synthetic label pointing at nothing, so an object-store
        outage is refused instead -- see :func:`_raw_uri_for`, Phase C-E6
        review round 2 F5).
    :param literature_doi_fallback: The DOI used for literature
        resolution when the document's own ``Citation/sDOI`` is absent.
        The archive path passes its required, operator-supplied ``doi``;
        the upload path passes its raw, optional ``doi`` argument
        (*before* the synthetic content-digest fallback ``resolve_doi``
        may substitute for record-keying -- that synthetic key is never a
        real DOI and must never reach literature resolution). ``None``
        propagates through to skip literature resolution entirely when
        no DOI is available from either source (Phase C-E6 review round
        2, F4).
    :param document_noun: The word naming the thing that was ingested in
        the ``ingestion_succeeded`` audit summary -- ``"article"`` on the
        archive path (restoring C-E3's exact original wording, Phase C-E6
        review round 2, F6) and ``"document"`` on the upload path (a
        depositor's file is not "an article" in the bibliographic sense
        the archive path's wording assumes).
    """

    schema_report = validate_bytes(article.xml)
    result.schema_valid = schema_report.valid
    if not schema_report.valid:
        result.warnings.append(
            "schema-invalid document, never parsed: "
            + "; ".join(schema_report.errors)
        )
        session.rollback()
        return

    document = parse_thermoml_document(article.xml)
    doi = resolve_doi(document)
    result.doi = doi

    mapping_result = map_document(document, doi=doi)
    result.payload_count = len(mapping_result.payloads)
    result.mapping_report = mapping_result.report.model_dump(by_alias=True)
    # The file's own citation (doi/title/year/journal/authors) is recorded
    # here unconditionally, in the custody row's existing raw
    # ``mapping_report_json`` field -- regardless of whether a
    # ``literature`` row ends up created below. A depositor's file with a
    # title but no DOI (or no citation at all) still has its citation text
    # preserved as provenance, without inventing a manual literature
    # record on their behalf (Phase C-E6 review round 2, F4).
    result.mapping_report["citation"] = mapping_result.literature.model_dump(
        mode="json"
    )
    # ``article.member_paths[0]`` -- the archive tar member path on the
    # archive path, or the depositor's declared filename
    # (``f"upload:{filename}"``) on the upload path -- is likewise
    # recorded here unconditionally, so a claim that a filename is
    # "recorded on the custody row" is actually true rather than aspirational
    # (Phase C-E6 review round 2, F8; see ``ThermoMLUploadRequest.filename``).
    result.mapping_report["source_label"] = article.member_paths[0]

    if refuse_when_unsupported and result.payload_count == 0:
        # Nothing has been written yet at this point -- no custody row,
        # no literature row, no object-store call -- so this rollback is
        # purely defensive (mirrors the schema-invalid early exit above)
        # rather than undoing anything.
        session.rollback()
        raise ThermoMLNoSupportedContentError(result.mapping_report)

    external_source = _get_or_create_external_source(session, **external_source_kwargs)
    result.external_source_id = external_source.id

    custody, custody_created, custody_would_store = _get_or_create_custody(
        session,
        external_source=external_source,
        article=article,
        doi=doi,
        schema_valid=schema_report.valid,
        mapping_report_json=result.mapping_report,
        retrieved_at=retrieved_at,
        commit=commit,
        source_uri=custody_source_uri,
        container_digest=custody_container_digest,
        allow_member_path_fallback=allow_member_path_fallback,
    )
    result.external_source_record_id = custody.id
    result.external_source_record_created = custody_created
    if custody_would_store:
        result.warnings.append(
            "dry run: object store not written; would_store "
            f"raw_uri={custody.raw_uri}"
        )

    # Literature is resolved by DOI only -- never by title alone, which
    # crashes ``LiteratureUploadRequest``'s own validator (it requires
    # either an identifier or *both* ``kind`` and ``title``, and this
    # importer never determines a ``kind``). The file's own ``sDOI`` wins;
    # the caller-supplied ``doi`` is the fallback when the file has none
    # (Phase C-E6 review round 2, F4) -- this is the same "caller doi
    # fills in when the file's citation has none" rule ``resolve_doi``
    # already applies for record-keying, applied here to literature too.
    # When neither is present, no literature row is created at all; the
    # citation text is still preserved above, in the custody row.
    literature_doi = mapping_result.literature.doi or literature_doi_fallback
    literature = None
    if literature_doi:
        literature = resolve_or_create_literature(
            session,
            LiteratureUploadRequest(
                doi=literature_doi,
                title=mapping_result.literature.title,
                year=mapping_result.literature.year,
                journal=mapping_result.literature.journal,
            ),
        )
        result.literature_id = literature.id

    # Resolve identity + build row kwargs for every payload, and
    # pre-check the dedupe key (read-only) so we know *before* opening
    # a submission whether this run will actually deposit anything new.
    prepared: list[tuple[Any, Any, dict[str, Any], int | None]] = []
    for payload in mapping_result.payloads:
        identity = resolve_identity(payload, session)
        row_kwargs = _payload_to_row_kwargs(
            payload,
            species_entry_id=identity.species_entry_id,
            created_by=actor.id,
            literature_id=(literature.id if literature is not None else None),
            external_source_record_id=custody.id,
        )
        existing_id = _existing_dedupe_id(session, row_kwargs)
        prepared.append((payload, identity, row_kwargs, existing_id))

    would_insert_count = sum(
        1 for *_rest, existing_id in prepared if existing_id is None
    )

    submission_ctx: UploadSubmissionContext | None = None
    if would_insert_count > 0:
        submission_ctx = open_submission(session)
        result.submission_id = submission_ctx.submission_id
        result.submission_ref = submission_ctx.submission.public_ref

    for payload, identity, row_kwargs, _existing_id in prepared:
        disposition = _insert_one(
            session,
            row_kwargs=row_kwargs,
            submission_ctx=submission_ctx,
            commit=commit,
            identity_status=identity.status,
            warnings=list(identity.warnings),
            payload=payload,
        )
        result.dispositions.append(disposition)
        _bump_counters(result, disposition)

    if submission_ctx is not None and commit:
        mark_upload_ingested(
            session,
            submission_ctx,
            summary=(
                f"Ingested ThermoML Cp(T) {document_noun} DOI={doi} "
                f"({result.inserted_count} row(s))."
            ),
        )


def import_thermoml_cp_article(
    session: Session,
    *,
    article: ArticleBytes,
    doi: str,
    actor: AppUser,
    license_id: str,
    commit: bool = False,
    retrieved_at: datetime | None = None,
) -> ThermoMLCpImportResult:
    """Validate, parse, map and persist one ThermoML article's Cp(T) rows,
    selected from the pinned NIST bulk archive (Phase C-E3).

    :param session: An open SQLAlchemy session. Per-row inserts use a
        SAVEPOINT so one bad row never breaks the outer transaction.
    :param article: The article's XML/JSON bytes, already selected from the
        archive (:func:`app.importers.thermoml.archive.select_article`) or
        built directly from a fixture in tests. This service never fetches
        or selects an archive member itself.
    :param doi: The article's own DOI (record key on the custody row and on
        every ``external_source_record_key``).
    :param actor: The operator running this import. Must hold the curator
        or admin role: recording a ``source_terms`` attestation is a
        curator's judgement call (``app.services.rights.record_attestation``
        refuses any actor without that role for a non-``depositor_agreement``
        basis), which is what this deposit's rights are actually recorded
        under (see module docstring).
    :param license_id: SPDX identifier the operator is licensing this
        deposit's rows under. Must equal the release's own ``data_license``
        exactly for a citable release to include them
        (``app.services.rights.licenses_match``); this service does not
        check that here, only the release layer does.
    :param commit: ``False`` (default) runs the full pipeline and rolls
        back; ``True`` commits on success, rolls back on error.
    :param retrieved_at: When the article bytes were fetched. Defaults to
        now (naive UTC, matching the column type).

    See :func:`import_thermoml_cp_upload` for the Phase C-E6 sibling that
    persists a directly-uploaded document instead (custody names the
    depositor-upload channel, not NIST; rights stand on the caller's own
    ``DepositRights``, not a ``source_terms`` attestation).
    """

    result = ThermoMLCpImportResult(doi=doi)
    retrieved_at = retrieved_at or _now_naive_utc()

    try:
        _run_pipeline(
            session,
            article=article,
            result=result,
            actor=actor,
            commit=commit,
            retrieved_at=retrieved_at,
            external_source_kwargs={
                "name": SOURCE_NAME,
                "release": SOURCE_RELEASE,
                "database_doi": SOURCE_DATABASE_DOI,
                "terms_url": TERMS_URL,
                "terms_text": TERMS_TEXT,
            },
            custody_source_uri=ARCHIVE_URL,
            custody_container_digest=ARCHIVE_SHA256,
            open_submission=lambda s: _open_submission_with_source_terms_attestation(
                s, actor=actor, license_id=license_id, doi=doi
            ),
            resolve_doi=lambda _document: doi,
            refuse_when_unsupported=False,
            allow_member_path_fallback=True,
            literature_doi_fallback=doi,
            document_noun="article",
        )
    except Exception:
        session.rollback()
        raise

    if not result.schema_valid:
        return result

    if commit:
        session.commit()
    else:
        session.rollback()

    return result


def _resolve_upload_doi(
    explicit_doi: str | None, document_doi: str | None
) -> str | None:
    """Reconcile a caller-supplied ``doi`` with the uploaded file's own
    ``sDOI`` citation field.

    :returns: The DOI to use for record-keying, or ``None`` when neither
        is present (the caller falls back to a content-digest-derived
        key -- see :func:`import_thermoml_cp_upload`).
    :raises ThermoMLDoiConflictError: Both are present and disagree
        (compared case-insensitively, whitespace-trimmed).
    """
    explicit = explicit_doi.strip() if explicit_doi else None
    document = document_doi.strip() if document_doi else None
    if explicit and document and explicit.casefold() != document.casefold():
        raise ThermoMLDoiConflictError(
            f"thermoml_doi_conflict: the supplied doi {explicit!r} does not "
            f"match the uploaded file's own sDOI {document!r}",
            declared_doi=explicit,
            file_doi=document,
        )
    return explicit or document


def _open_upload_submission_for_depositor(
    session: Session,
    *,
    actor: AppUser,
    rights: DepositRights,
    doi_for_keying: str,
) -> UploadSubmissionContext:
    """Open the submission wrapper for a depositor-uploaded ThermoML file
    (Phase C-E6).

    Unlike :func:`_open_submission_with_source_terms_attestation`, no
    second attestation is recorded here: ``open_upload_submission``
    already records ``rights`` as a ``depositor_agreement`` attestation,
    and that *is* the basis this deposit stands on -- there is no
    third-party source (NIST or otherwise) whose terms it was taken
    under. Recording a ``source_terms`` row here would misattribute a
    depositor's own upload to a source that never produced it.
    """
    return open_upload_submission(
        session,
        created_by=actor.id,
        kind=SubmissionKind.other,
        rights=rights,
        title=f"ThermoML Cp(T) file upload (doi={doi_for_keying})",
    )


def import_thermoml_cp_upload(
    session: Session,
    *,
    article: ArticleBytes,
    doi: str | None,
    actor: AppUser,
    rights: DepositRights,
    commit: bool = False,
    retrieved_at: datetime | None = None,
) -> ThermoMLCpImportResult:
    """Validate, parse, map and persist one depositor-uploaded ThermoML
    document's Cp(T) rows (Phase C-E6) -- the source-neutral sibling of
    :func:`import_thermoml_cp_article` that lets anyone ingest a ThermoML
    file, not only the pinned NIST archive.

    Shares the validate -> parse -> map -> persist core with the archive
    path (:func:`_run_pipeline`); differs in custody and rights -- see the
    module docstring for the full comparison.

    :param session: An open SQLAlchemy session. Per-row inserts use a
        SAVEPOINT so one bad row never breaks the outer transaction.
    :param article: The uploaded document's bytes, wrapped as
        :class:`~app.importers.thermoml.archive.ArticleBytes`. There is no
        JSON twin to cross-check for an upload (unlike the archive path);
        only ``xml``/``xml_sha256`` are read from it. Callers pass
        ``json_bytes=b"{}"`` (or any placeholder) and a synthetic
        ``member_paths``.
    :param doi: Optional. When given, must agree with the file's own
        ``sDOI`` citation element (case/whitespace-insensitive) or the
        import is refused with :class:`ThermoMLDoiConflictError` *before*
        anything is written. When omitted, the file's own ``sDOI`` is used
        if present; when neither is present, a synthetic record-key basis
        derived from the content digest is used
        (``f"upload:{article.xml_sha256[:16]}"``) -- never used for
        literature resolution. Literature resolution reads the file's own
        ``sDOI`` first and falls back to this argument when the file has
        none (Phase C-E6 review round 2, F4); when neither is present, no
        literature row is created, and no manual ``kind``+``title`` record
        is fabricated on the depositor's behalf -- the file's citation
        text (title/year/journal/authors), if any, is still preserved in
        the custody row's ``mapping_report_json["citation"]``.
    :param actor: The depositor. Recorded as the submission's creator and
        the ``depositor_agreement`` attestor. No elevated role is
        required -- this is a standard upload, unlike the archive path's
        curator-only ``source_terms`` attestation.
    :param rights: The depositor's own rights agreement. Required --
        unlike the archive path, there is no third-party source terms to
        fall back on if this is absent.
    :param commit: ``False`` (default) runs the full pipeline and rolls
        back; ``True`` commits on success, rolls back on error.
    :param retrieved_at: When the bytes were received. Defaults to now
        (naive UTC, matching the column type).
    :raises ThermoMLDoiConflictError: See ``doi`` above. Raised before any
        row is validated as schema-valid content, so nothing is written.
    """

    result = ThermoMLCpImportResult(doi=doi or "")
    retrieved_at = retrieved_at or _now_naive_utc()

    def _resolve(document: Any) -> str:
        resolved = _resolve_upload_doi(doi, document.citation.doi)
        return resolved or f"{_UPLOAD_DOI_FALLBACK_PREFIX}:{article.xml_sha256[:16]}"

    try:
        _run_pipeline(
            session,
            article=article,
            result=result,
            actor=actor,
            commit=commit,
            retrieved_at=retrieved_at,
            external_source_kwargs={
                "name": UPLOAD_SOURCE_NAME,
                "release": UPLOAD_SOURCE_RELEASE,
            },
            custody_source_uri=UPLOAD_SOURCE_URI,
            custody_container_digest=None,
            open_submission=lambda s: _open_upload_submission_for_depositor(
                s, actor=actor, rights=rights, doi_for_keying=result.doi
            ),
            resolve_doi=_resolve,
            refuse_when_unsupported=True,
            allow_member_path_fallback=False,
            literature_doi_fallback=doi,
            document_noun="document",
        )
    except Exception:
        session.rollback()
        raise

    if not result.schema_valid:
        return result

    if commit:
        session.commit()
    else:
        session.rollback()

    return result


def _open_submission_with_source_terms_attestation(
    session: Session,
    *,
    actor: AppUser,
    license_id: str,
    doi: str,
) -> UploadSubmissionContext:
    """Open the submission wrapper and make ``source_terms`` the standing
    attestation.

    ``open_upload_submission`` always records ``rights`` as a
    ``depositor_agreement`` attestation (``app.services.rights.
    attest_from_deposit`` has no other basis to record) -- passing the
    NIST/TRC terms through it anyway keeps the Phase B choke point in the
    loop and ``TERMS_TEXT`` on that row too. It is not, however, the basis
    this deposit actually stands on: nobody at deposit time "agreed" to
    license these rows, they were taken under a source's own published
    terms. So one explicit ``source_terms`` attestation is recorded right
    after, which supersedes the automatic row and becomes standing. Called
    exactly once per opened submission -- never call this twice for the
    same submission, or the submission ends up with two ``source_terms``
    rows instead of one.
    """

    rights = DepositRights(
        license=license_id,
        depositor_attests_right_to_license=True,
        source_terms=TERMS_TEXT,
    )
    submission_ctx = open_upload_submission(
        session,
        created_by=actor.id,
        kind=SubmissionKind.other,
        rights=rights,
        title=f"ThermoML Cp(T) bulk import: DOI {doi}",
    )
    submission_ctx.submission.source_kind = SubmissionSourceKind.bulk_import
    record_attestation(
        session,
        submission=submission_ctx.submission,
        license_id=license_id,
        basis=RightsBasisKind.source_terms,
        actor=actor,
        source_terms=TERMS_TEXT,
    )
    return submission_ctx


def _insert_one(
    session: Session,
    *,
    row_kwargs: dict[str, Any],
    submission_ctx: UploadSubmissionContext | None,
    commit: bool,
    identity_status: str,
    warnings: list[str],
    payload,
) -> ObservationDisposition:
    savepoint = session.begin_nested()
    observation_ref: str | None = None
    try:
        stmt = (
            pg_insert(MolecularPropertyObservation)
            .values(**row_kwargs)
            .on_conflict_do_nothing(constraint=_DEDUPE_CONSTRAINT_NAME)
            .returning(
                MolecularPropertyObservation.id,
                MolecularPropertyObservation.public_ref,
            )
        )
        inserted_row = session.execute(stmt).one_or_none()
        if inserted_row is None:
            action = _ACTION_DUPLICATE
            savepoint.rollback()
        else:
            inserted_id, inserted_ref = inserted_row
            if commit:
                assert submission_ctx is not None, (
                    "a row was inserted but no submission was opened to "
                    "link it to -- the would-insert pre-check and the "
                    "authoritative ON CONFLICT insert disagreed"
                )
                link_record(
                    session,
                    submission=submission_ctx.submission,
                    record_type=SubmissionRecordType.molecular_property_observation,
                    record_id=inserted_id,
                )
                action = _ACTION_INSERTED
                # Only a real, committed insert gets to report its ref --
                # a dry-run's row is rolled back below and a later real
                # insert would mint a *different* random public_ref, so
                # showing this one here would promise a stability the
                # rolled-back row never had.
                observation_ref = inserted_ref
                savepoint.commit()
            else:
                action = _ACTION_WOULD_INSERT
                savepoint.rollback()
    except Exception as exc:  # pragma: no cover - genuine constraint failure
        savepoint.rollback()
        warnings = [*warnings, f"row insert failed: {type(exc).__name__}: {exc}"]
        action = _ACTION_SKIPPED

    return ObservationDisposition(
        property_kind=(
            payload.property_kind.value
            if hasattr(payload.property_kind, "value")
            else str(payload.property_kind)
        ),
        external_source_record_key=payload.external_source_record_key,
        identity_status=identity_status,
        species_entry_id=row_kwargs.get("species_entry_id"),
        action=action,
        warnings=warnings,
        observation_ref=observation_ref,
    )


def _bump_counters(
    result: ThermoMLCpImportResult, disposition: ObservationDisposition
) -> None:
    if disposition.identity_status == IDENTITY_RESOLVED:
        result.resolved_identity_count += 1
    elif disposition.identity_status == IDENTITY_AMBIGUOUS:
        result.ambiguous_identity_count += 1
    elif disposition.identity_status == IDENTITY_NOT_FOUND:
        result.not_found_identity_count += 1
    else:
        result.unresolved_identity_count += 1

    if disposition.action == _ACTION_WOULD_INSERT:
        result.would_insert_count += 1
    elif disposition.action == _ACTION_INSERTED:
        result.inserted_count += 1
    elif disposition.action == _ACTION_DUPLICATE:
        result.duplicate_count += 1
    elif disposition.action == _ACTION_SKIPPED:
        result.skipped_count += 1


__all__ = [
    "UPLOAD_SOURCE_NAME",
    "UPLOAD_SOURCE_RELEASE",
    "UPLOAD_SOURCE_URI",
    "ObservationDisposition",
    "ThermoMLCpImportResult",
    "ThermoMLDoiConflictError",
    "ThermoMLNoSupportedContentError",
    "import_thermoml_cp_article",
    "import_thermoml_cp_upload",
]
