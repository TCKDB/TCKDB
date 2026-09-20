"""Workflow service: import CCCBDB ``MolecularPropertyObservationCreate``
payloads into the database with conservative identity resolution and
idempotency.

This is the first CCCBDB-side module allowed to touch the database.
Everything upstream (parsers, builders, dry-runs) is pure transformation.

Design contract
---------------

* **Conservative identity resolution.** Auto-resolve ``species_entry_id``
  only when the payload's ``raw_payload_json["identity_hint"]["inchikey"]``
  matches exactly one :class:`Species` AND that species has exactly
  one *compatible* :class:`SpeciesEntry` (``kind=minimum`` +
  ``electronic_state_kind=ground``). Anything else — formula-only,
  name-only, formula+name, multiple-entry ambiguity — leaves
  ``species_entry_id=None`` and records the proposal in the
  disposition's ``warnings``. CAS is left as proposal-only unless a
  normalized CAS-identity table appears in a future phase.
* **Unresolved is fine.** The DB schema declares
  ``species_entry_id`` nullable for exactly this case. Insertion with
  ``species_entry_id=None`` preserves the observation's CCCBDB
  provenance for later manual curation.
* **Idempotency** rides on the existing
  ``molecular_property_observation.mpo_dedupe_key`` UNIQUE
  constraint (postgresql_nulls_not_distinct=True) via a read-before-write
  dedupe pre-check. A second import of the same payloads yields
  ``duplicate`` dispositions instead of new rows, and opens no new
  submission (see below).
* **Every inserted row is linked to a submission (Phase C-E5 review round
  3).** Mirrors ``app.services.thermoml_cp_import``: one
  ``Submission(source_kind=bulk_import)`` is opened per run whenever the
  run's dedupe pre-check finds at least one row that would newly insert,
  every row this run actually inserts is linked to it via
  ``app.services.submission.link_record``, and the open happens
  regardless of ``commit`` (a dry run's submission is rolled back with
  everything else at the end, so nothing persists — see
  ``_open_bulk_import_submission`` for why the rights basis is
  ``depositor_agreement`` rather than ``source_terms``). Without this, a
  curator's later ``observation_identity_attach`` call on any row this
  importer wrote could never succeed:
  ``observation_identity_attach_requires_submission`` refuses an
  observation with no submission link, and until this change every row
  this importer ever wrote had none. Rows written by this importer
  *before* this change are not retroactively linked — see the "Legacy
  CCCBDB rows" section of PR #513 for the operator remediation.
* **Dry-run by default.** ``commit=False`` runs the full pipeline
  inside the caller's transaction without committing. ``commit=True``
  commits on success and rolls back on unexpected error.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

from pydantic import ValidationError
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tckdb_schemas.rights import DepositRights

from app.db.models.app_user import AppUser
from app.db.models.common import SubmissionKind, SubmissionRecordType, SubmissionSourceKind
from app.db.models.molecular_property_observation import (
    MolecularPropertyObservation,
)
from app.schemas.entities.molecular_property_observation import (
    MolecularPropertyObservationCreate,
)
from app.services.external_observation_identity import (
    IDENTITY_AMBIGUOUS,
    IDENTITY_NOT_FOUND,
    IDENTITY_RESOLVED,
    IDENTITY_SKIPPED,
    IDENTITY_UNRESOLVED,
)
from app.services.external_observation_identity import (
    IdentityResolution as _IdentityResolution,
)
from app.services.external_observation_identity import (
    identity_hint as _identity_hint,
)
from app.services.external_observation_identity import (
    resolve_identity as _resolve_identity,
)
from app.services.submission import link_record
from app.services.upload_submission import (
    UploadSubmissionContext,
    mark_upload_ingested,
    open_upload_submission,
)

_logger = logging.getLogger(__name__)


# Dedupe-key columns (mirrors mpo_dedupe_key in
# app/db/models/molecular_property_observation.py).
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


# ---------------------------------------------------------------------------
# Result + per-row disposition dataclasses
# ---------------------------------------------------------------------------


_IDENTITY_RESOLVED = IDENTITY_RESOLVED
_IDENTITY_UNRESOLVED = IDENTITY_UNRESOLVED
_IDENTITY_AMBIGUOUS = IDENTITY_AMBIGUOUS
_IDENTITY_NOT_FOUND = IDENTITY_NOT_FOUND
_IDENTITY_SKIPPED = IDENTITY_SKIPPED

_ACTION_WOULD_INSERT = "would_insert"
_ACTION_INSERTED = "inserted"
_ACTION_DUPLICATE = "duplicate"
_ACTION_INVALID = "invalid"
_ACTION_SKIPPED = "skipped"


@dataclass
class PayloadDisposition:
    """Per-payload outcome from the import service."""

    property_kind: str | None
    property_label: str | None
    external_source_record_key: str | None
    identity_status: str
    species_entry_id: int | None
    action: str
    warnings: list[str] = field(default_factory=list)
    inchikey: str | None = None
    source_path: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "property_kind": self.property_kind,
            "property_label": self.property_label,
            "external_source_record_key": self.external_source_record_key,
            "identity_status": self.identity_status,
            "species_entry_id": self.species_entry_id,
            "action": self.action,
            "warnings": list(self.warnings),
            "inchikey": self.inchikey,
            "source_path": self.source_path,
        }


@dataclass
class CCCBDBMolecularPropertyImportResult:
    """Aggregate report from one ``import_cccbdb_molecular_property_payloads``
    invocation."""

    payload_files_read: int = 0
    payload_count: int = 0
    valid_payload_count: int = 0
    invalid_payload_count: int = 0
    would_insert_count: int = 0
    inserted_count: int = 0
    duplicate_count: int = 0
    resolved_identity_count: int = 0
    unresolved_identity_count: int = 0
    ambiguous_identity_count: int = 0
    not_found_identity_count: int = 0
    submission_id: int | None = None
    warnings: list[str] = field(default_factory=list)
    dispositions: list[PayloadDisposition] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "payload_files_read": self.payload_files_read,
            "payload_count": self.payload_count,
            "valid_payload_count": self.valid_payload_count,
            "invalid_payload_count": self.invalid_payload_count,
            "would_insert_count": self.would_insert_count,
            "inserted_count": self.inserted_count,
            "duplicate_count": self.duplicate_count,
            "resolved_identity_count": self.resolved_identity_count,
            "unresolved_identity_count": self.unresolved_identity_count,
            "ambiguous_identity_count": self.ambiguous_identity_count,
            "not_found_identity_count": self.not_found_identity_count,
            "submission_id": self.submission_id,
            "warnings": list(self.warnings),
            "dispositions": [d.to_json() for d in self.dispositions],
        }


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------


# _IdentityResolution / _identity_hint / _resolve_identity now live in
# app.services.external_observation_identity (Phase C-E3 extraction);
# imported above and aliased so the rest of this module (and any external
# caller importing these private names) sees identical behavior.


# ---------------------------------------------------------------------------
# Submission wrapper (Phase C-E5 review round 3)
# ---------------------------------------------------------------------------


def _open_bulk_import_submission(
    session: Session,
    *,
    actor: AppUser,
    license_id: str,
) -> UploadSubmissionContext:
    """Open the submission wrapper for one CCCBDB import run.

    Unlike ``app.services.thermoml_cp_import``'s ThermoML importer, which
    records an explicit ``source_terms`` attestation quoting NIST/TRC's own
    published terms verbatim, CCCBDB has no terms/citation text captured
    anywhere in this repo: ``app/importers/cccbdb/__init__.py`` defines
    ``SOURCE_NAME`` / ``SOURCE_RELEASE`` / ``SOURCE_DATABASE_DOI`` but no
    ``TERMS_TEXT``, and ``backend/docs/specs/cccbdb_importer.md`` still
    lists "Q1. Legal / terms of use: confirm CCCBDB's published use policy"
    as an open question. Rather than fabricate terms text that was never
    verified, this import stands on ``depositor_agreement`` — the same
    basis ``open_upload_submission`` always records automatically from the
    deposit's own ``rights`` fragment, and the same basis ThermoML's own
    first (superseded) attestation carries before its explicit
    ``source_terms`` row supersedes it. No second attestation is recorded
    here, so ``depositor_agreement`` is left standing for this submission.
    """

    rights = DepositRights(
        license=license_id,
        depositor_attests_right_to_license=True,
    )
    submission_ctx = open_upload_submission(
        session,
        created_by=actor.id,
        kind=SubmissionKind.other,
        rights=rights,
        title="CCCBDB molecular-property bulk import",
    )
    submission_ctx.submission.source_kind = SubmissionSourceKind.bulk_import
    return submission_ctx


# ---------------------------------------------------------------------------
# Insert helpers
# ---------------------------------------------------------------------------


def _payload_to_row_kwargs(
    payload: MolecularPropertyObservationCreate,
    *,
    species_entry_id: int | None,
    created_by: int | None,
) -> dict[str, Any]:
    """Project a validated ``MolecularPropertyObservationCreate`` onto
    keyword arguments for :class:`MolecularPropertyObservation`. The
    schema is already field-aligned; we just override
    ``species_entry_id`` with the resolution result and add
    ``created_by`` when supplied. ``created_at`` is left to the
    ORM/server default (``func.now()``)."""

    data = payload.model_dump(mode="json")
    data["species_entry_id"] = species_entry_id
    if created_by is not None:
        data["created_by"] = created_by
    return data


def _existing_dedupe_id(
    session: Session,
    row_kwargs: dict[str, Any],
) -> int | None:
    """Return the id of an existing row that matches the
    ``mpo_dedupe_key`` unique constraint, or ``None`` when no match
    exists. The lookup uses ``NULLS NOT DISTINCT`` semantics manually
    via ``IS NOT DISTINCT FROM`` so the result matches the DB-level
    unique constraint's behavior."""

    model = MolecularPropertyObservation
    conditions = []
    for col_name in _DEDUPE_COLUMNS:
        col = getattr(model, col_name)
        value = row_kwargs.get(col_name)
        conditions.append(col.is_not_distinct_from(value))
    return session.execute(
        select(model.id).where(and_(*conditions))
    ).scalar_one_or_none()


def _attempt_insert(
    session: Session,
    row_kwargs: dict[str, Any],
    *,
    commit_mode: bool,
) -> tuple[bool, str | None, int | None]:
    """Insert one row via the ORM after a dedupe-key pre-check.

    Returns ``(inserted, warning, row_id)``. ``inserted`` is False iff the
    pre-check found a match (the dedupe is taken as "this exact row
    already exists"). The pre-check uses ``IS NOT DISTINCT FROM`` so
    NULL columns match exactly like the DB-level UNIQUE constraint
    with ``postgresql_nulls_not_distinct=True``. ``row_id`` is the
    inserted (or matched) row's id, so a caller in commit mode can link
    it to the run's submission.

    A SAVEPOINT around the insert (managed by the caller) protects
    the outer transaction from race-condition IntegrityError if two
    concurrent imports race to insert the same dedupe key — the
    IntegrityError is caught and treated as "duplicate".
    """

    existing_id = _existing_dedupe_id(session, row_kwargs)
    if existing_id is not None:
        return False, None, existing_id

    row = MolecularPropertyObservation(**row_kwargs)
    session.add(row)
    try:
        session.flush()
    except IntegrityError as exc:
        # Race-condition duplicate (two imports inserting the same
        # dedupe key) → treat as ``duplicate`` instead of raising.
        # Anything else (FK violation, NOT NULL, check constraint)
        # bubbles out so the outer try block can roll back.
        message = str(exc.orig) if exc.orig is not None else str(exc)
        if _DEDUPE_CONSTRAINT_NAME in message:
            return False, "race-condition duplicate (unique constraint)", None
        raise
    return True, None, row.id


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def import_cccbdb_molecular_property_payloads(
    session: Session,
    payloads: Sequence[dict | MolecularPropertyObservationCreate],
    *,
    actor: AppUser,
    license_id: str,
    commit: bool = False,
    resolve_identity: bool = True,
    created_by: int | None = None,
    fail_on_invalid: bool = False,
    source_paths: Sequence[str] | None = None,
) -> CCCBDBMolecularPropertyImportResult:
    """Import CCCBDB molecular-property payloads with conservative
    identity resolution and idempotency.

    :param session: An open SQLAlchemy session. The service uses
        a SAVEPOINT per row so a single bad row never breaks the
        outer transaction.
    :param payloads: Validated ``MolecularPropertyObservationCreate``
        instances or raw dicts. Dicts are validated lazily; invalid
        ones are recorded as ``action="invalid"`` (or raise when
        ``fail_on_invalid=True``).
    :param actor: The operator running this import. Recorded as the
        creator of this run's ``Submission`` (opened only when at
        least one row would newly insert — see module docstring) and
        as the ``depositor_agreement`` rights attestor.
    :param license_id: SPDX identifier the operator is licensing this
        deposit's rows under. Must equal the release's own
        ``data_license`` exactly for a citable release to include them
        (``app.services.rights.licenses_match``); this service does
        not check that here, only the release layer does.
    :param commit: When ``True``, commit on success. When ``False``
        (default), every change is rolled back at the end so the
        caller can preview what would have happened. A submission may
        still be opened during a dry run (mirroring
        ``app.services.thermoml_cp_import``) — it is rolled back with
        everything else, so nothing persists.
    :param resolve_identity: When ``False``, skip auto-resolution
        entirely and insert every row with ``species_entry_id=None``.
        Useful for staging large CCCBDB imports before identity work
        catches up.
    :param created_by: Optional user id to record on each inserted row.
        Independent of ``actor``: ``actor`` owns the submission/rights
        wrapper, ``created_by`` is who/what is recorded on the row
        itself (defaults to unset, matching the pre-existing behavior).
    :param fail_on_invalid: When ``True``, raise the first pydantic
        :class:`ValidationError` instead of recording the disposition.
    :param source_paths: Optional parallel list of source-path strings
        for each payload; surfaced on every disposition so disk-based
        runs can point a maintainer at the exact file.
    """

    result = CCCBDBMolecularPropertyImportResult()
    result.payload_count = len(payloads)
    if source_paths is None:
        source_paths_list: list[str | None] = [None] * len(payloads)
    else:
        source_paths_list = [str(p) if p is not None else None for p in source_paths]
        if len(source_paths_list) != len(payloads):
            raise ValueError(
                "len(source_paths) must equal len(payloads) when provided"
            )

    try:
        # Phase 1: validate + resolve identity + build row kwargs + a
        # read-only dedupe pre-check for every payload, WITHOUT inserting
        # anything yet. This decides, before any row is written, whether
        # this run has anything new to deposit at all -- so a fully
        # duplicate second run opens no submission (mirrors
        # app.services.thermoml_cp_import's "the submission is opened only
        # when there is something to link").
        prepared: list[tuple[Any, dict[str, Any] | None, int | None, str | None]] = []
        for raw, source_path in zip(payloads, source_paths_list, strict=False):
            prepared.append(
                _prepare_one(
                    session,
                    raw,
                    source_path=source_path,
                    resolve_identity=resolve_identity,
                    created_by=created_by,
                    fail_on_invalid=fail_on_invalid,
                )
            )

        would_insert_count = sum(
            1
            for _invalid, row_kwargs, existing_id, _ctx in prepared
            if row_kwargs is not None and existing_id is None
        )

        submission_ctx: UploadSubmissionContext | None = None
        if would_insert_count > 0:
            submission_ctx = _open_bulk_import_submission(
                session, actor=actor, license_id=license_id
            )
            result.submission_id = submission_ctx.submission_id

        for invalid_disposition, row_kwargs, existing_id, ctx in prepared:
            if row_kwargs is None:
                # invalid_disposition is already a complete PayloadDisposition
                disposition = invalid_disposition
            else:
                disposition = _insert_one(
                    session,
                    row_kwargs=row_kwargs,
                    existing_id=existing_id,
                    submission_ctx=submission_ctx,
                    commit_mode=commit,
                    ctx=ctx,
                )
            result.dispositions.append(disposition)
            _bump_counters(result, disposition)

        if submission_ctx is not None and commit:
            mark_upload_ingested(
                session,
                submission_ctx,
                summary=(
                    "Ingested CCCBDB molecular-property bulk import "
                    f"({result.inserted_count} row(s))."
                ),
            )
    except Exception:
        # Any unexpected exception → roll back the outer transaction
        # and re-raise so the caller sees the original traceback.
        session.rollback()
        raise

    if commit:
        session.commit()
    else:
        session.rollback()

    return result


@dataclass
class _PreparedContext:
    payload: MolecularPropertyObservationCreate
    source_path: str | None
    identity_status: str
    inchikey: str | None
    warnings: list[str]


def _prepare_one(
    session: Session,
    raw: dict | MolecularPropertyObservationCreate,
    *,
    source_path: str | None,
    resolve_identity: bool,
    created_by: int | None,
    fail_on_invalid: bool,
) -> tuple[
    PayloadDisposition | None,
    dict[str, Any] | None,
    int | None,
    "_PreparedContext | None",
]:
    """Validate and resolve identity for one payload, and pre-check the
    dedupe key -- read-only, no insert.

    Returns ``(invalid_disposition, row_kwargs, existing_id, ctx)``:

    * On a validation failure, ``invalid_disposition`` is a complete
      :class:`PayloadDisposition` (``action="invalid"``) and the other
      three fields are ``None`` -- the caller appends it directly.
    * On success, ``invalid_disposition`` is ``None`` and ``row_kwargs``/
      ``existing_id`` carry the prepared insert (or the id of the row it
      already matches). ``ctx`` is a :class:`_PreparedContext` carrying
      everything :func:`_insert_one` needs to build the final
      disposition without re-deriving it from ``row_kwargs``.
    """

    try:
        if isinstance(raw, MolecularPropertyObservationCreate):
            payload = raw
        else:
            payload = MolecularPropertyObservationCreate.model_validate(raw)
    except ValidationError as exc:
        if fail_on_invalid:
            raise
        return (
            PayloadDisposition(
                property_kind=(raw or {}).get("property_kind")
                if isinstance(raw, dict) else None,
                property_label=(raw or {}).get("property_label")
                if isinstance(raw, dict) else None,
                external_source_record_key=(raw or {}).get(
                    "external_source_record_key"
                ) if isinstance(raw, dict) else None,
                identity_status=_IDENTITY_SKIPPED,
                species_entry_id=None,
                action=_ACTION_INVALID,
                warnings=[
                    f"pydantic validation failed: "
                    f"{exc.errors()[0].get('msg', '?')!r}"
                ],
                source_path=source_path,
            ),
            None,
            None,
            None,
        )

    hint = _identity_hint(payload)
    inchikey = (hint.get("inchikey") or None) if hint else None

    if resolve_identity:
        resolution = _resolve_identity(payload, session)
    else:
        resolution = _IdentityResolution(
            species_entry_id=payload.species_entry_id,
            status=(
                _IDENTITY_RESOLVED if payload.species_entry_id is not None
                else _IDENTITY_UNRESOLVED
            ),
            warnings=(
                ("identity resolution skipped (--no-resolve-identity)",)
                if payload.species_entry_id is None else ()
            ),
        )

    row_kwargs = _payload_to_row_kwargs(
        payload,
        species_entry_id=resolution.species_entry_id,
        created_by=created_by,
    )
    existing_id = _existing_dedupe_id(session, row_kwargs)

    context = _PreparedContext(
        payload=payload,
        source_path=source_path,
        identity_status=resolution.status,
        inchikey=inchikey,
        warnings=list(resolution.warnings),
    )
    return None, row_kwargs, existing_id, context


def _insert_one(
    session: Session,
    *,
    row_kwargs: dict[str, Any],
    existing_id: int | None,
    submission_ctx: UploadSubmissionContext | None,
    commit_mode: bool,
    ctx: "_PreparedContext",
) -> PayloadDisposition:
    """Actually attempt the insert (or record the pre-checked duplicate)
    for one prepared payload, in a SAVEPOINT, and link a newly inserted
    row to ``submission_ctx`` when committing."""

    payload = ctx.payload
    row_warnings = list(ctx.warnings)

    nested = session.begin_nested()
    try:
        if existing_id is not None:
            inserted, insert_warning, _row_id = False, None, existing_id
        else:
            inserted, insert_warning, row_id = _attempt_insert(
                session, row_kwargs, commit_mode=commit_mode
            )
        if insert_warning:
            row_warnings.append(insert_warning)
        if not inserted:
            action = _ACTION_DUPLICATE
        elif commit_mode:
            action = _ACTION_INSERTED
            assert submission_ctx is not None, (
                "a row was inserted but no submission was opened to link "
                "it to -- the would-insert pre-check and the authoritative "
                "insert disagreed"
            )
            link_record(
                session,
                submission=submission_ctx.submission,
                record_type=SubmissionRecordType.molecular_property_observation,
                record_id=row_id,
            )
        else:
            action = _ACTION_WOULD_INSERT
        # In dry-run mode the SAVEPOINT will roll back when we drop
        # it below; in commit mode it stays and is later committed
        # by the outer session.commit().
        if commit_mode:
            nested.commit()
        else:
            nested.rollback()
    except Exception as exc:
        nested.rollback()
        row_warnings.append(
            f"row insert failed: {type(exc).__name__}: {exc}"
        )
        action = _ACTION_SKIPPED

    return PayloadDisposition(
        property_kind=payload.property_kind.value
        if hasattr(payload.property_kind, "value")
        else str(payload.property_kind),
        property_label=payload.property_label,
        external_source_record_key=payload.external_source_record_key,
        identity_status=ctx.identity_status,
        species_entry_id=row_kwargs.get("species_entry_id"),
        action=action,
        warnings=row_warnings,
        inchikey=ctx.inchikey,
        source_path=ctx.source_path,
    )


def _bump_counters(
    result: CCCBDBMolecularPropertyImportResult,
    disposition: PayloadDisposition,
) -> None:
    if disposition.action == _ACTION_INVALID:
        result.invalid_payload_count += 1
        return

    result.valid_payload_count += 1
    if disposition.identity_status == _IDENTITY_RESOLVED:
        result.resolved_identity_count += 1
    elif disposition.identity_status == _IDENTITY_AMBIGUOUS:
        result.ambiguous_identity_count += 1
    elif disposition.identity_status == _IDENTITY_NOT_FOUND:
        result.not_found_identity_count += 1
    else:
        result.unresolved_identity_count += 1

    if disposition.action == _ACTION_WOULD_INSERT:
        result.would_insert_count += 1
    elif disposition.action == _ACTION_INSERTED:
        result.inserted_count += 1
    elif disposition.action == _ACTION_DUPLICATE:
        result.duplicate_count += 1


__all__ = [
    "CCCBDBMolecularPropertyImportResult",
    "PayloadDisposition",
    "import_cccbdb_molecular_property_payloads",
]
