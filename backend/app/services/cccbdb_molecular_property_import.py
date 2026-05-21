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
  constraint (postgresql_nulls_not_distinct=True) via
  ``INSERT ... ON CONFLICT DO NOTHING``. A second import of the same
  payloads yields ``duplicate`` dispositions instead of new rows.
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

from app.db.models.common import (
    SpeciesEntryStateKind,
    StationaryPointKind,
)
from app.db.models.molecular_property_observation import (
    MolecularPropertyObservation,
)
from app.db.models.species import Species, SpeciesEntry
from app.schemas.entities.molecular_property_observation import (
    MolecularPropertyObservationCreate,
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


_IDENTITY_RESOLVED = "resolved"
_IDENTITY_UNRESOLVED = "unresolved"
_IDENTITY_AMBIGUOUS = "ambiguous"
_IDENTITY_NOT_FOUND = "not_found"
_IDENTITY_SKIPPED = "skipped"

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
            "warnings": list(self.warnings),
            "dispositions": [d.to_json() for d in self.dispositions],
        }


# ---------------------------------------------------------------------------
# Identity resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _IdentityResolution:
    species_entry_id: int | None
    status: str
    warnings: tuple[str, ...] = ()


def _identity_hint(payload: MolecularPropertyObservationCreate) -> dict:
    hint = (
        payload.raw_payload_json.get("identity_hint")
        if payload.raw_payload_json else None
    )
    return hint if isinstance(hint, dict) else {}


def _resolve_identity(
    payload: MolecularPropertyObservationCreate,
    session: Session,
) -> _IdentityResolution:
    """Conservative identity resolution.

    Returns ``_IdentityResolution`` reflecting the outcome:

    * ``resolved`` — exactly one ``Species`` matched by InChIKey AND
      exactly one compatible :class:`SpeciesEntry`.
    * ``ambiguous`` — multiple species rows or multiple compatible
      entries matched.
    * ``not_found`` — no Species/SpeciesEntry matched the InChIKey.
    * ``unresolved`` — no InChIKey available; identity hints are
      preserved on the row but no FK is set.
    """

    if payload.species_entry_id is not None:
        return _IdentityResolution(
            species_entry_id=payload.species_entry_id,
            status=_IDENTITY_RESOLVED,
        )

    hint = _identity_hint(payload)
    inchikey = (hint.get("inchikey") or "").strip().upper() or None
    cas_number = (hint.get("cas_number") or "").strip() or None
    formula = (hint.get("formula") or "").strip() or None
    name = (hint.get("name") or "").strip() or None

    warnings: list[str] = []
    if inchikey is None:
        # Propose-only signals; we do NOT auto-resolve from these.
        if cas_number:
            warnings.append(
                "CAS present but no CAS identity table available for "
                "automatic resolution"
            )
        if formula and name:
            warnings.append(
                "formula+name available but not used for automatic "
                "resolution (proposal-only)"
            )
        elif formula:
            warnings.append(
                "formula available but not used for automatic resolution"
            )
        return _IdentityResolution(
            species_entry_id=None,
            status=_IDENTITY_UNRESOLVED,
            warnings=tuple(warnings),
        )

    species_rows = session.scalars(
        select(Species).where(Species.inchi_key == inchikey)
    ).all()
    if not species_rows:
        return _IdentityResolution(
            species_entry_id=None,
            status=_IDENTITY_NOT_FOUND,
            warnings=(f"no Species row matched inchi_key={inchikey!r}",),
        )
    if len(species_rows) > 1:
        return _IdentityResolution(
            species_entry_id=None,
            status=_IDENTITY_AMBIGUOUS,
            warnings=(
                f"{len(species_rows)} Species rows share inchi_key="
                f"{inchikey!r}; refusing to pick",
            ),
        )

    species = species_rows[0]
    compatible_entries = session.scalars(
        select(SpeciesEntry).where(
            SpeciesEntry.species_id == species.id,
            SpeciesEntry.kind == StationaryPointKind.minimum,
            SpeciesEntry.electronic_state_kind
                == SpeciesEntryStateKind.ground,
        )
    ).all()
    if not compatible_entries:
        return _IdentityResolution(
            species_entry_id=None,
            status=_IDENTITY_NOT_FOUND,
            warnings=(
                f"species_id={species.id} (inchi_key={inchikey!r}) has no "
                "minimum / ground SpeciesEntry; refusing to pick",
            ),
        )
    if len(compatible_entries) > 1:
        return _IdentityResolution(
            species_entry_id=None,
            status=_IDENTITY_AMBIGUOUS,
            warnings=(
                f"species_id={species.id} has {len(compatible_entries)} "
                "compatible minimum/ground entries; refusing to pick",
            ),
        )

    return _IdentityResolution(
        species_entry_id=compatible_entries[0].id,
        status=_IDENTITY_RESOLVED,
    )


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
) -> tuple[bool, str | None]:
    """Insert one row via the ORM after a dedupe-key pre-check.

    Returns ``(inserted, warning)``. ``inserted`` is False iff the
    pre-check found a match (the dedupe is taken as "this exact row
    already exists"). The pre-check uses ``IS NOT DISTINCT FROM`` so
    NULL columns match exactly like the DB-level UNIQUE constraint
    with ``postgresql_nulls_not_distinct=True``.

    A SAVEPOINT around the insert (managed by the caller) protects
    the outer transaction from race-condition IntegrityError if two
    concurrent imports race to insert the same dedupe key — the
    IntegrityError is caught and treated as "duplicate".
    """

    existing_id = _existing_dedupe_id(session, row_kwargs)
    if existing_id is not None:
        return False, None

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
            return False, "race-condition duplicate (unique constraint)"
        raise
    return True, None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def import_cccbdb_molecular_property_payloads(
    session: Session,
    payloads: Sequence[dict | MolecularPropertyObservationCreate],
    *,
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
    :param commit: When ``True``, commit on success. When ``False``
        (default), every change is rolled back at the end so the
        caller can preview what would have happened.
    :param resolve_identity: When ``False``, skip auto-resolution
        entirely and insert every row with ``species_entry_id=None``.
        Useful for staging large CCCBDB imports before identity work
        catches up.
    :param created_by: Optional user id to record on each inserted row.
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
        for raw, source_path in zip(payloads, source_paths_list):
            disposition = _process_one(
                session,
                raw,
                source_path=source_path,
                resolve_identity=resolve_identity,
                created_by=created_by,
                commit_mode=commit,
                fail_on_invalid=fail_on_invalid,
            )
            result.dispositions.append(disposition)
            _bump_counters(result, disposition)
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


def _process_one(
    session: Session,
    raw: dict | MolecularPropertyObservationCreate,
    *,
    source_path: str | None,
    resolve_identity: bool,
    created_by: int | None,
    commit_mode: bool,
    fail_on_invalid: bool,
) -> PayloadDisposition:
    """Validate, resolve, and (try to) insert one payload. Runs in a
    SAVEPOINT so a recoverable failure (e.g. an invalid row's pydantic
    error after an earlier good row) never poisons the outer transaction.
    """

    # 1) Validation
    try:
        if isinstance(raw, MolecularPropertyObservationCreate):
            payload = raw
        else:
            payload = MolecularPropertyObservationCreate.model_validate(raw)
    except ValidationError as exc:
        if fail_on_invalid:
            raise
        return PayloadDisposition(
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
        )

    hint = _identity_hint(payload)
    inchikey = (hint.get("inchikey") or None) if hint else None

    # 2) Identity resolution
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

    # 3) Insertion (or dry-run preview)
    row_kwargs = _payload_to_row_kwargs(
        payload,
        species_entry_id=resolution.species_entry_id,
        created_by=created_by,
    )

    warnings = list(resolution.warnings)
    nested = session.begin_nested()
    try:
        inserted, insert_warning = _attempt_insert(
            session, row_kwargs, commit_mode=commit_mode
        )
        if insert_warning:
            warnings.append(insert_warning)
        if not inserted:
            action = _ACTION_DUPLICATE
        elif commit_mode:
            action = _ACTION_INSERTED
        else:
            action = _ACTION_WOULD_INSERT
        # In dry-run mode the SAVEPOINT will roll back when we drop
        # it below; in commit mode it stays and is later committed
        # by the outer session.commit().
        if commit_mode:
            nested.commit()
        else:
            nested.rollback()
    except Exception as exc:  # noqa: BLE001
        nested.rollback()
        warnings.append(
            f"row insert failed: {type(exc).__name__}: {exc}"
        )
        action = _ACTION_SKIPPED

    return PayloadDisposition(
        property_kind=payload.property_kind.value
        if hasattr(payload.property_kind, "value")
        else str(payload.property_kind),
        property_label=payload.property_label,
        external_source_record_key=payload.external_source_record_key,
        identity_status=resolution.status,
        species_entry_id=resolution.species_entry_id,
        action=action,
        warnings=warnings,
        inchikey=inchikey,
        source_path=source_path,
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
