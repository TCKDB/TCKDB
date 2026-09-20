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

Design contract
----------------

* **Layering.** This module imports ``app.importers.thermoml`` (the pure
  parse/validate/map package). The importer package must never import this
  module back -- enforced by
  ``backend/tests/importers/thermoml/test_layering.py`` and mirrored here by
  ``test_service_does_not_import_parsers_or_fetchers`` in this service's own
  test module.
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
* **Two attestations, one standing.** ``open_upload_submission`` always
  records the deposit's ``rights`` fragment as a ``depositor_agreement``
  attestation (``app.services.rights.attest_from_deposit`` -- it has no
  other basis to record). That is not the basis this deposit actually
  stands on: these rows were not licensed by a depositor's say-so, they
  were taken from a source whose own published terms permit it. So this
  service records a second, explicit ``source_terms`` attestation quoting
  ``TERMS_TEXT`` verbatim right after -- it supersedes the automatic
  ``depositor_agreement`` row, so the *standing* attestation for the
  submission is ``source_terms``, honestly naming what license basis the
  deposit is actually released under. Exactly one ``source_terms``
  attestation is recorded per submission; never called twice.
* **Reject, don't guess.** A schema-invalid document is never parsed
  (E2's ``validate_bytes`` already refuses it); this service does not
  create a custody row, a submission, or any observation for it.
* **Dry-run by default.** ``commit=False`` runs the full pipeline inside
  the caller's transaction without committing. ``commit=True`` commits on
  success and rolls back on unexpected error.
"""

from __future__ import annotations

import logging
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

_ACTION_WOULD_INSERT = "would_insert"
_ACTION_INSERTED = "inserted"
_ACTION_DUPLICATE = "duplicate"
_ACTION_INVALID = "invalid"
_ACTION_SKIPPED = "skipped"


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

    def to_json(self) -> dict[str, Any]:
        return {
            "property_kind": self.property_kind,
            "external_source_record_key": self.external_source_record_key,
            "identity_status": self.identity_status,
            "species_entry_id": self.species_entry_id,
            "action": self.action,
            "warnings": list(self.warnings),
        }


@dataclass
class ThermoMLCpImportResult:
    """Aggregate report from one :func:`import_thermoml_cp_article` run."""

    doi: str = ""
    schema_valid: bool = True
    payload_count: int = 0
    would_insert_count: int = 0
    inserted_count: int = 0
    duplicate_count: int = 0
    invalid_count: int = 0
    skipped_count: int = 0
    resolved_identity_count: int = 0
    unresolved_identity_count: int = 0
    ambiguous_identity_count: int = 0
    not_found_identity_count: int = 0
    submission_id: int | None = None
    external_source_id: int | None = None
    external_source_record_id: int | None = None
    external_source_record_created: bool = False
    literature_id: int | None = None
    warnings: list[str] = field(default_factory=list)
    dispositions: list[ObservationDisposition] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "doi": self.doi,
            "schema_valid": self.schema_valid,
            "payload_count": self.payload_count,
            "would_insert_count": self.would_insert_count,
            "inserted_count": self.inserted_count,
            "duplicate_count": self.duplicate_count,
            "invalid_count": self.invalid_count,
            "skipped_count": self.skipped_count,
            "resolved_identity_count": self.resolved_identity_count,
            "unresolved_identity_count": self.unresolved_identity_count,
            "ambiguous_identity_count": self.ambiguous_identity_count,
            "not_found_identity_count": self.not_found_identity_count,
            "submission_id": self.submission_id,
            "external_source_id": self.external_source_id,
            "external_source_record_id": self.external_source_record_id,
            "external_source_record_created": self.external_source_record_created,
            "literature_id": self.literature_id,
            "warnings": list(self.warnings),
            "dispositions": [d.to_json() for d in self.dispositions],
        }


# ---------------------------------------------------------------------------
# Custody: get-or-create ExternalSource / ExternalSourceRecord
# ---------------------------------------------------------------------------


def _get_or_create_external_source(session: Session) -> ExternalSource:
    existing = session.scalar(
        select(ExternalSource).where(
            ExternalSource.source_name == SOURCE_NAME,
            ExternalSource.source_release == SOURCE_RELEASE,
        )
    )
    if existing is not None:
        return existing
    row = ExternalSource(
        source_name=SOURCE_NAME,
        source_release=SOURCE_RELEASE,
        source_database_doi=SOURCE_DATABASE_DOI,
        terms_url=TERMS_URL,
        terms_text=TERMS_TEXT,
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
) -> tuple[ExternalSourceRecord, bool]:
    """Reuse the custody row for this ``(doi, content, parser, mapping)``
    identity if one already exists; otherwise insert one.

    Matches ``uq_external_source_record_identity`` exactly, so a second run
    with the same parser/mapping version finds and reuses this row instead
    of appending a duplicate; a changed parser or mapping version misses and
    appends a new one, as the model's docstring promises.
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
        return existing, False

    row = ExternalSourceRecord(
        external_source_id=external_source.id,
        record_kind=ExternalSourceRecordKind.thermoml_article,
        source_uri=ARCHIVE_URL,
        source_record_key=doi,
        retrieved_at=retrieved_at,
        content_sha256=article.xml_sha256,
        content_length=len(article.xml),
        raw_uri=_raw_uri_for(article),
        container_digest=ARCHIVE_SHA256,
        schema_id=_SCHEMA_ID,
        schema_valid=schema_valid,
        parser_name=_PARSER_NAME,
        parser_version=PARSER_VERSION,
        mapping_version=MAPPING_VERSION,
        mapping_report_json=mapping_report_json,
    )
    session.add(row)
    session.flush()
    return row, True


def _raw_uri_for(article: ArticleBytes) -> str:
    """The custody row's ``raw_uri``: a content-addressed object-store key
    when the store is reachable, else the local snapshot member path.

    Never blocks the import on the object store being down -- storing the
    bytes is best-effort provenance, not a precondition for recording that
    the article was seen and parsed.
    """
    from app.services.artifact_storage import ArtifactStorageUnavailable, store_artifact

    try:
        return store_artifact(article.xml, article.xml_sha256)
    except ArtifactStorageUnavailable as exc:
        _logger.warning(
            "object store unavailable while snapshotting ThermoML article "
            "xml_sha256=%s; falling back to the archive member path: %s",
            article.xml_sha256,
            exc,
        )
        return article.member_paths[0]


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
    """Validate, parse, map and persist one ThermoML article's Cp(T) rows.

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
    """

    result = ThermoMLCpImportResult(doi=doi)
    retrieved_at = retrieved_at or _now_naive_utc()

    try:
        schema_report = validate_bytes(article.xml)
        result.schema_valid = schema_report.valid
        if not schema_report.valid:
            result.warnings.append(
                "schema-invalid document, never parsed: "
                + "; ".join(schema_report.errors)
            )
            session.rollback()
            return result

        document = parse_thermoml_document(article.xml)
        mapping_result = map_document(document, doi=doi)
        result.payload_count = len(mapping_result.payloads)

        external_source = _get_or_create_external_source(session)
        result.external_source_id = external_source.id

        custody, custody_created = _get_or_create_custody(
            session,
            external_source=external_source,
            article=article,
            doi=doi,
            schema_valid=schema_report.valid,
            mapping_report_json=mapping_result.report.model_dump(by_alias=True),
            retrieved_at=retrieved_at,
        )
        result.external_source_record_id = custody.id
        result.external_source_record_created = custody_created

        literature = None
        if mapping_result.literature.doi or mapping_result.literature.title:
            literature = resolve_or_create_literature(
                session,
                LiteratureUploadRequest(
                    doi=mapping_result.literature.doi,
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
            submission_ctx = _open_submission_with_source_terms_attestation(
                session, actor=actor, license_id=license_id, doi=doi
            )
            result.submission_id = submission_ctx.submission_id

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
                    f"Ingested ThermoML Cp(T) article DOI={doi} "
                    f"({result.inserted_count} row(s))."
                ),
            )
    except Exception:
        session.rollback()
        raise

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
    try:
        stmt = (
            pg_insert(MolecularPropertyObservation)
            .values(**row_kwargs)
            .on_conflict_do_nothing(constraint=_DEDUPE_CONSTRAINT_NAME)
            .returning(MolecularPropertyObservation.id)
        )
        inserted_id = session.execute(stmt).scalar_one_or_none()
        if inserted_id is None:
            action = _ACTION_DUPLICATE
            savepoint.rollback()
        else:
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
    )


def _bump_counters(
    result: ThermoMLCpImportResult, disposition: ObservationDisposition
) -> None:
    if disposition.identity_status == IDENTITY_RESOLVED:
        result.resolved_identity_count += 1
    elif disposition.identity_status == "ambiguous":
        result.ambiguous_identity_count += 1
    elif disposition.identity_status == "not_found":
        result.not_found_identity_count += 1
    else:
        result.unresolved_identity_count += 1

    if disposition.action == _ACTION_WOULD_INSERT:
        result.would_insert_count += 1
    elif disposition.action == _ACTION_INSERTED:
        result.inserted_count += 1
    elif disposition.action == _ACTION_DUPLICATE:
        result.duplicate_count += 1
    elif disposition.action == _ACTION_INVALID:
        result.invalid_count += 1
    elif disposition.action == _ACTION_SKIPPED:
        result.skipped_count += 1


__all__ = [
    "ObservationDisposition",
    "ThermoMLCpImportResult",
    "import_thermoml_cp_article",
]
