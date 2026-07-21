"""Deterministic ``tckdb.archive.v1`` writer and empty-target restorer.

The archive is a portable scientific-state archive, not an operational
PostgreSQL backup.  Its declared registry excludes credentials and ephemeral
worker/request state while preserving all classified scientific, provenance,
submission, and curation rows plus byte-exact calculation artifacts.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tarfile
import tempfile
from collections import defaultdict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum as PyEnum
from pathlib import Path
from typing import Any, BinaryIO

from sqlalchemy import cast, func, select, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Session
from sqlalchemy.sql.sqltypes import (
    CHAR,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Double,
    Enum,
    Float,
    Integer,
    SmallInteger,
    String,
    Text,
)

import app.db.models  # noqa: F401 -- register the complete mapper graph
from app.db.base import Base
from app.db.types import RDKitMol
from app.services.archive.registry import (
    EXCLUDED_COLUMNS,
    EXCLUDED_TABLES,
    PRESEEDED_TABLES,
    included_column_names,
    included_tables_in_fk_order,
)
from app.services.artifact_storage import load_artifact_bytes, store_artifact

ARCHIVE_SCHEMA = "tckdb.archive.v1"
_MANIFEST_PATH = "manifest.json"
_ROWS_PATH = "rows.ndjson"
_BLOB_PREFIX = "blobs/"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SPOOL_LIMIT = 8 * 1024 * 1024

PathOrBinaryIO = str | os.PathLike[str] | BinaryIO


class ArchiveError(RuntimeError):
    """Base archive failure."""


class ArchiveIntegrityError(ArchiveError):
    """Archive bytes or declared checksums are inconsistent."""


class ArchiveCompatibilityError(ArchiveError):
    """Archive contract or database schema is incompatible with this code."""


class ArchiveNotEmptyError(ArchiveError):
    """A v1 restore target contains non-seed data or seed identity drift."""


@dataclass(frozen=True)
class ArchiveRestoreReport:
    """Verified row/blob counts produced by a successful restore."""

    rows_restored: int
    blobs_restored: int
    table_row_counts: Mapping[str, int]


@dataclass
class _BlobSpool:
    sha256: str
    size: int
    file: BinaryIO


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _encode_value(value: Any, sql_type) -> Any:
    if value is None:
        return None
    if isinstance(sql_type, JSONB):
        return value
    if isinstance(sql_type, ARRAY):
        return [_encode_value(item, sql_type.item_type) for item in value]
    if isinstance(sql_type, Enum):
        return value.value if isinstance(value, PyEnum) else str(value)
    if isinstance(sql_type, DateTime):
        if not isinstance(value, datetime):
            raise ArchiveError(f"Expected datetime, got {type(value).__name__}")
        return value.isoformat(timespec="microseconds")
    if isinstance(sql_type, Date):
        if not isinstance(value, date):
            raise ArchiveError(f"Expected date, got {type(value).__name__}")
        return value.isoformat()
    if isinstance(sql_type, (Double, Float)):
        return {"$float": float(value).hex()}
    if isinstance(sql_type, Boolean):
        return bool(value)
    if isinstance(sql_type, (BigInteger, Integer, SmallInteger)):
        return int(value)
    if isinstance(sql_type, UUID):
        return str(value)
    if isinstance(sql_type, (CHAR, String, Text, RDKitMol)):
        return str(value)
    raise ArchiveCompatibilityError(f"No tckdb.archive.v1 codec for SQL type {type(sql_type)!r}")


def _decode_value(value: Any, sql_type) -> Any:
    if value is None:
        return None
    if isinstance(sql_type, JSONB):
        return value
    if isinstance(sql_type, ARRAY):
        if not isinstance(value, list):
            raise ArchiveIntegrityError("ARRAY archive value is not a list")
        return [_decode_value(item, sql_type.item_type) for item in value]
    if isinstance(sql_type, Enum):
        if not isinstance(value, str):
            raise ArchiveIntegrityError("Enum archive value is not a string")
        return value
    if isinstance(sql_type, DateTime):
        if not isinstance(value, str):
            raise ArchiveIntegrityError("Datetime archive value is not a string")
        return datetime.fromisoformat(value)
    if isinstance(sql_type, Date):
        if not isinstance(value, str):
            raise ArchiveIntegrityError("Date archive value is not a string")
        return date.fromisoformat(value)
    if isinstance(sql_type, (Double, Float)):
        if not isinstance(value, dict) or set(value) != {"$float"}:
            raise ArchiveIntegrityError("Floating-point archive value is malformed")
        return float.fromhex(value["$float"])
    if isinstance(sql_type, Boolean):
        if not isinstance(value, bool):
            raise ArchiveIntegrityError("Boolean archive value is malformed")
        return value
    if isinstance(sql_type, (BigInteger, Integer, SmallInteger)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ArchiveIntegrityError("Integer archive value is malformed")
        return value
    if isinstance(sql_type, (UUID, CHAR, String, Text, RDKitMol)):
        if not isinstance(value, str):
            raise ArchiveIntegrityError("Text archive value is malformed")
        return value
    raise ArchiveCompatibilityError(f"No tckdb.archive.v1 codec for SQL type {type(sql_type)!r}")


def _supports_type(sql_type) -> bool:
    if isinstance(sql_type, ARRAY):
        return _supports_type(sql_type.item_type)
    return isinstance(
        sql_type,
        (
            JSONB,
            Enum,
            DateTime,
            Date,
            Double,
            Float,
            Boolean,
            BigInteger,
            Integer,
            SmallInteger,
            UUID,
            CHAR,
            String,
            Text,
            RDKitMol,
        ),
    )


def _validate_column_codecs(tables) -> None:
    unsupported = [
        f"{table.name}.{column.name}:{type(column.type).__name__}"
        for table in tables
        for column in table.columns
        if column.name in included_column_names(table) and not _supports_type(column.type)
    ]
    if unsupported:
        raise ArchiveCompatibilityError(f"Archive registry has unsupported SQL columns: {unsupported}")


def _select_expression(column):
    # psycopg/RDKit's binary ``mol`` representation is deployment-specific;
    # its canonical text cast round-trips through the cartridge input codec.
    if isinstance(column.type, RDKitMol):
        return cast(column, Text).label(column.name)
    return column


def _current_revisions(session: Session) -> list[str]:
    try:
        revisions = session.scalars(text("SELECT version_num FROM alembic_version ORDER BY version_num")).all()
    except Exception as exc:  # pragma: no cover - defensive for unmanaged DBs
        raise ArchiveCompatibilityError("Database has no readable Alembic revision state") from exc
    if not revisions:
        raise ArchiveCompatibilityError("Database has no Alembic head revision")
    return list(revisions)


def _lock_snapshot_tables(session: Session, tables) -> None:
    """Freeze included table contents for the caller's transaction.

    PostgreSQL ``SHARE`` locks allow concurrent ``SELECT`` queries but conflict
    with the ``ROW EXCLUSIVE`` lock taken by INSERT/UPDATE/DELETE. Acquiring all
    relations in one stable lexical order prevents two archive writers from
    choosing different lock orders. Locks live until the surrounding session
    transaction commits or rolls back; this function intentionally never owns
    that transaction.
    """

    preparer = session.get_bind().dialect.identifier_preparer
    names = ["alembic_version", *(table.name for table in tables)]
    quoted = ", ".join(preparer.quote(name) for name in sorted(names))
    session.execute(text(f"LOCK TABLE {quoted} IN SHARE MODE"))


def _write_rows(session: Session, tables) -> tuple[BinaryIO, dict[str, int], str, int]:
    spool = tempfile.SpooledTemporaryFile(max_size=_SPOOL_LIMIT, mode="w+b")
    digest = hashlib.sha256()
    counts: dict[str, int] = {}
    total = 0

    try:
        for table in tables:
            names = included_column_names(table)
            columns = [table.c[name] for name in names]
            expressions = [_select_expression(column) for column in columns]
            order_by = [table.c[column.name] for column in table.primary_key.columns]
            rows = session.execute(select(*expressions).select_from(table).order_by(*order_by)).mappings()
            count = 0
            for row in rows:
                values = {column.name: _encode_value(row[column.name], column.type) for column in columns}
                line = _canonical_json({"record_type": "row", "table": table.name, "values": values})
                spool.write(line)
                digest.update(line)
                count += 1
                total += 1
            counts[table.name] = count
        spool.seek(0)
        return spool, counts, digest.hexdigest(), total
    except Exception:
        spool.close()
        raise


def _load_blob_spools(session: Session) -> list[_BlobSpool]:
    artifact = Base.metadata.tables["calculation_artifact"]
    persisted: dict[str, int] = {}
    for sha256, size in session.execute(
        select(artifact.c.sha256, artifact.c.bytes).distinct().order_by(artifact.c.sha256, artifact.c.bytes)
    ):
        prior = persisted.setdefault(sha256, size)
        if prior != size:
            raise ArchiveIntegrityError(f"Artifact rows disagree on byte count for sha256={sha256}")

    spools: list[_BlobSpool] = []
    try:
        for sha256, expected_size in sorted(persisted.items()):
            if not _SHA256_RE.fullmatch(sha256):
                raise ArchiveIntegrityError(f"Malformed artifact sha256: {sha256!r}")
            content = load_artifact_bytes(sha256, expected_bytes=expected_size)
            actual_sha = hashlib.sha256(content).hexdigest()
            if actual_sha != sha256 or len(content) != expected_size:
                raise ArchiveIntegrityError(f"Artifact storage returned invalid bytes for sha256={sha256}")
            spool = tempfile.SpooledTemporaryFile(max_size=_SPOOL_LIMIT, mode="w+b")
            spool.write(content)
            spool.seek(0)
            spools.append(_BlobSpool(sha256=sha256, size=len(content), file=spool))
        return spools
    except Exception:
        for blob in spools:
            blob.file.close()
        raise


def _manifest(
    *,
    revisions: list[str],
    tables,
    counts: Mapping[str, int],
    rows_sha256: str,
    rows_size: int,
    total_rows: int,
    blobs: list[_BlobSpool],
) -> dict[str, Any]:
    return {
        "schema": ARCHIVE_SCHEMA,
        "database_revisions": revisions,
        "tables": [
            {
                "name": table.name,
                "columns": included_column_names(table),
                "rows": counts[table.name],
            }
            for table in tables
        ],
        "excluded_tables": dict(EXCLUDED_TABLES),
        "excluded_columns": {table: dict(columns) for table, columns in EXCLUDED_COLUMNS.items()},
        "rows": {
            "path": _ROWS_PATH,
            "sha256": rows_sha256,
            "bytes": rows_size,
            "count": total_rows,
        },
        "blobs": [
            {
                "path": f"{_BLOB_PREFIX}{blob.sha256}",
                "sha256": blob.sha256,
                "bytes": blob.size,
            }
            for blob in blobs
        ],
        "restore_transformations": {
            "calculation_artifact.uri": ("rewritten to the destination content-addressed artifact store"),
            "excluded_columns": "restored as NULL/default",
        },
    }


def _tar_info(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mtime = 0
    info.mode = 0o600
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


@contextmanager
def _open_tar(value: PathOrBinaryIO, mode: str) -> Iterator[tarfile.TarFile]:
    if isinstance(value, (str, os.PathLike, Path)):
        with tarfile.open(name=os.fspath(value), mode=mode) as archive:
            yield archive
    else:
        with tarfile.open(fileobj=value, mode=mode) as archive:
            yield archive


def _add_bytes(archive: tarfile.TarFile, name: str, content: bytes) -> None:
    archive.addfile(_tar_info(name, len(content)), io.BytesIO(content))


def write_archive(session: Session, destination: PathOrBinaryIO) -> dict[str, Any]:
    """Write a deterministic ``tckdb.archive.v1`` tar archive.

    ``destination`` may be a filesystem path or a seekable binary file object.
    Before reading any revision, row, or artifact metadata, the writer takes
    PostgreSQL ``SHARE`` locks on every included table in lexical order. Reads
    remain available, while concurrent INSERT/UPDATE/DELETE waits, preventing a
    cross-statement archive under PostgreSQL's default READ COMMITTED isolation.
    The caller controls the surrounding transaction and therefore the lock
    lifetime: commit or roll back promptly after this function returns.
    """

    tables = included_tables_in_fk_order(Base.metadata)
    _validate_column_codecs(tables)
    _lock_snapshot_tables(session, tables)
    revisions = _current_revisions(session)
    row_file, counts, rows_sha256, total_rows = _write_rows(session, tables)
    blob_spools: list[_BlobSpool] = []
    try:
        row_file.seek(0, io.SEEK_END)
        rows_size = row_file.tell()
        row_file.seek(0)
        blob_spools = _load_blob_spools(session)
        manifest = _manifest(
            revisions=revisions,
            tables=tables,
            counts=counts,
            rows_sha256=rows_sha256,
            rows_size=rows_size,
            total_rows=total_rows,
            blobs=blob_spools,
        )
        manifest_bytes = _canonical_json(manifest)

        with _open_tar(destination, "w") as archive:
            _add_bytes(archive, _MANIFEST_PATH, manifest_bytes)
            archive.addfile(_tar_info(_ROWS_PATH, rows_size), row_file)
            for blob in blob_spools:
                archive.addfile(
                    _tar_info(f"{_BLOB_PREFIX}{blob.sha256}", blob.size),
                    blob.file,
                )
        return manifest
    finally:
        row_file.close()
        for blob in blob_spools:
            blob.file.close()


def _read_member_bytes(archive: tarfile.TarFile, member: tarfile.TarInfo, *, max_bytes: int | None = None) -> bytes:
    if max_bytes is not None and member.size > max_bytes:
        raise ArchiveIntegrityError(f"Archive member {member.name!r} is too large")
    handle = archive.extractfile(member)
    if handle is None:
        raise ArchiveIntegrityError(f"Archive member {member.name!r} is unreadable")
    content = handle.read()
    if len(content) != member.size:
        raise ArchiveIntegrityError(f"Archive member {member.name!r} is truncated")
    return content


def _members_by_name(archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    members: dict[str, tarfile.TarInfo] = {}
    for member in archive.getmembers():
        if not member.isfile():
            raise ArchiveIntegrityError(f"Archive contains non-file member {member.name!r}")
        if member.name in members:
            raise ArchiveIntegrityError(f"Duplicate archive member {member.name!r}")
        members[member.name] = member
    return members


def _expected_table_manifest(tables) -> list[dict[str, Any]]:
    return [{"name": table.name, "columns": included_column_names(table)} for table in tables]


def _validate_manifest(
    manifest: Any,
    *,
    tables,
    revisions: list[str],
) -> None:
    if not isinstance(manifest, dict) or manifest.get("schema") != ARCHIVE_SCHEMA:
        raise ArchiveCompatibilityError("Not a tckdb.archive.v1 manifest")
    if manifest.get("database_revisions") != revisions:
        raise ArchiveCompatibilityError("Archive Alembic revision does not match the target database")
    if manifest.get("excluded_tables") != dict(EXCLUDED_TABLES):
        raise ArchiveCompatibilityError("Archive excluded-table policy differs")
    expected_excluded_columns = {table: dict(columns) for table, columns in EXCLUDED_COLUMNS.items()}
    if manifest.get("excluded_columns") != expected_excluded_columns:
        raise ArchiveCompatibilityError("Archive excluded-column policy differs")

    declared_tables = manifest.get("tables")
    if not isinstance(declared_tables, list):
        raise ArchiveIntegrityError("Manifest tables block is malformed")
    expected = _expected_table_manifest(tables)
    stripped: list[dict[str, Any]] = []
    for item in declared_tables:
        if not isinstance(item, dict) or not isinstance(item.get("rows"), int):
            raise ArchiveIntegrityError("Manifest table entry is malformed")
        stripped.append({"name": item.get("name"), "columns": item.get("columns")})
    if stripped != expected:
        raise ArchiveCompatibilityError("Archive table/column registry differs")


def _parse_rows(
    content: bytes,
    *,
    manifest: Mapping[str, Any],
    tables,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    rows_block = manifest.get("rows")
    if not isinstance(rows_block, dict):
        raise ArchiveIntegrityError("Manifest rows block is malformed")
    if rows_block.get("path") != _ROWS_PATH:
        raise ArchiveIntegrityError("Manifest rows path is invalid")
    if rows_block.get("bytes") != len(content):
        raise ArchiveIntegrityError("rows.ndjson byte count differs from manifest")
    if rows_block.get("sha256") != hashlib.sha256(content).hexdigest():
        raise ArchiveIntegrityError("rows.ndjson checksum mismatch")

    table_by_name = {table.name: table for table in tables}
    table_index = {table.name: index for index, table in enumerate(tables)}
    decoded: dict[str, list[dict[str, Any]]] = defaultdict(list)
    counts = {table.name: 0 for table in tables}
    prior_table_index = -1
    prior_pk: dict[str, tuple[Any, ...]] = {}

    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        if not raw_line:
            raise ArchiveIntegrityError(f"Blank rows.ndjson line {line_number}")
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ArchiveIntegrityError(f"Invalid rows.ndjson JSON at line {line_number}") from exc
        if not isinstance(record, dict) or record.get("record_type") != "row":
            raise ArchiveIntegrityError(f"Malformed row record at line {line_number}")
        table_name = record.get("table")
        table = table_by_name.get(table_name)
        if table is None:
            raise ArchiveIntegrityError(f"Unknown archived table {table_name!r}")
        current_index = table_index[table_name]
        if current_index < prior_table_index:
            raise ArchiveIntegrityError("rows.ndjson table order is not deterministic")
        prior_table_index = current_index

        values = record.get("values")
        expected_names = included_column_names(table)
        if not isinstance(values, dict) or set(values) != set(expected_names):
            raise ArchiveIntegrityError(f"Archived columns differ for table {table_name!r}")
        row = {name: _decode_value(values[name], table.c[name].type) for name in expected_names}
        pk = tuple(row[column.name] for column in table.primary_key.columns)
        previous = prior_pk.get(table_name)
        if previous is not None and pk <= previous:
            raise ArchiveIntegrityError(f"Primary keys are duplicated or unsorted for table {table_name!r}")
        prior_pk[table_name] = pk
        decoded[table_name].append(row)
        counts[table_name] += 1

    if rows_block.get("count") != sum(counts.values()):
        raise ArchiveIntegrityError("rows.ndjson row count differs from manifest")
    declared_counts = {item["name"]: item["rows"] for item in manifest["tables"]}
    if counts != declared_counts:
        raise ArchiveIntegrityError("Per-table row counts differ from manifest")
    return decoded, counts


def _read_blobs(
    archive: tarfile.TarFile,
    members: Mapping[str, tarfile.TarInfo],
    manifest: Mapping[str, Any],
) -> dict[str, bytes]:
    declarations = manifest.get("blobs")
    if not isinstance(declarations, list):
        raise ArchiveIntegrityError("Manifest blobs block is malformed")
    blobs: dict[str, bytes] = {}
    expected_paths: set[str] = set()
    for declaration in declarations:
        if not isinstance(declaration, dict):
            raise ArchiveIntegrityError("Manifest blob entry is malformed")
        sha256 = declaration.get("sha256")
        path = declaration.get("path")
        size = declaration.get("bytes")
        if (
            not isinstance(sha256, str)
            or not _SHA256_RE.fullmatch(sha256)
            or path != f"{_BLOB_PREFIX}{sha256}"
            or not isinstance(size, int)
            or size < 0
        ):
            raise ArchiveIntegrityError("Manifest blob entry is malformed")
        if path in expected_paths:
            raise ArchiveIntegrityError(f"Duplicate manifest blob {path!r}")
        expected_paths.add(path)
        member = members.get(path)
        if member is None:
            raise ArchiveIntegrityError(f"Missing blob member {path!r}")
        content = _read_member_bytes(archive, member)
        if len(content) != size or hashlib.sha256(content).hexdigest() != sha256:
            raise ArchiveIntegrityError(f"Blob checksum/size mismatch for {path!r}")
        blobs[sha256] = content

    actual_blob_paths = {name for name in members if name.startswith(_BLOB_PREFIX)}
    if actual_blob_paths != expected_paths:
        raise ArchiveIntegrityError("Archive has undeclared or missing blob members")
    return blobs


def _ensure_restore_target(session: Session, tables) -> None:
    """Require only the exact identities seeded by the migrations."""

    for table in tables:
        preseed = PRESEEDED_TABLES.get(table.name)
        if preseed is not None:
            identity_names, expected = preseed
            columns = [table.c[name] for name in identity_names]
            actual = frozenset(tuple(row) for row in session.execute(select(*columns)))
            if actual != expected:
                raise ArchiveNotEmptyError(f"Target migration seeds differ in table {table.name!r}")
            continue
        if session.scalar(select(func.count()).select_from(table)):
            raise ArchiveNotEmptyError(f"Target contains rows in table {table.name!r}")


def _remove_target_preseeds(session: Session, tables) -> None:
    """Remove verified migration seeds before inserting archive-owned rows."""

    by_name = {table.name: table for table in tables}
    for table_name in reversed(tuple(PRESEEDED_TABLES)):
        session.execute(by_name[table_name].delete())


def _repair_sequences(session: Session, tables) -> None:
    for table in tables:
        for column in table.primary_key.columns:
            if not isinstance(column.type, (BigInteger, Integer, SmallInteger)):
                continue
            sequence = session.scalar(
                text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
                {"table_name": table.fullname, "column_name": column.name},
            )
            if sequence is None:
                continue
            maximum = session.scalar(select(func.max(column)))
            if maximum is None:
                session.execute(
                    text("SELECT setval(CAST(:sequence AS regclass), 1, false)"),
                    {"sequence": sequence},
                )
            else:
                session.execute(
                    text("SELECT setval(CAST(:sequence AS regclass), :value, true)"),
                    {"sequence": sequence, "value": maximum},
                )


def restore_archive(
    session: Session,
    source: PathOrBinaryIO,
) -> ArchiveRestoreReport:
    """Verify and restore a ``tckdb.archive.v1`` archive.

    Validation completes before blob or database writes begin. V1 requires an
    otherwise-empty migrated target with its exact canonical seed identities
    and has no merge/upsert mode. Artifact-store writes precede the database
    commit, so a later database failure can leave harmless unreferenced
    content-addressed blobs.
    """

    tables = included_tables_in_fk_order(Base.metadata)
    target_tables = list(Base.metadata.sorted_tables)
    _validate_column_codecs(tables)
    revisions = _current_revisions(session)

    with _open_tar(source, "r:*") as archive:
        members = _members_by_name(archive)
        allowed_fixed = {_MANIFEST_PATH, _ROWS_PATH}
        unexpected = {name for name in members if name not in allowed_fixed and not name.startswith(_BLOB_PREFIX)}
        if unexpected:
            raise ArchiveIntegrityError(f"Archive contains unexpected members: {sorted(unexpected)}")
        manifest_member = members.get(_MANIFEST_PATH)
        rows_member = members.get(_ROWS_PATH)
        if manifest_member is None or rows_member is None:
            raise ArchiveIntegrityError("Archive lacks manifest.json or rows.ndjson")
        try:
            manifest = json.loads(_read_member_bytes(archive, manifest_member, max_bytes=10 * 1024 * 1024))
        except json.JSONDecodeError as exc:
            raise ArchiveIntegrityError("manifest.json is not valid JSON") from exc
        _validate_manifest(manifest, tables=tables, revisions=revisions)
        rows_content = _read_member_bytes(archive, rows_member)
        rows_by_table, counts = _parse_rows(rows_content, manifest=manifest, tables=tables)
        blobs = _read_blobs(archive, members, manifest)
        referenced_blobs = {row["sha256"] for row in rows_by_table.get("calculation_artifact", [])}
        if referenced_blobs != set(blobs):
            raise ArchiveIntegrityError("Artifact rows and packaged blob declarations differ")

    # Reject before the first external blob-store write. The check is repeated
    # inside the write transaction to close the local race.
    _ensure_restore_target(session, target_tables)

    artifact_uris: dict[str, str] = {}
    for sha256, content in sorted(blobs.items()):
        artifact_uris[sha256] = store_artifact(content, sha256)

    transaction = session.begin_nested() if session.in_transaction() else session.begin()
    with transaction:
        _lock_snapshot_tables(session, target_tables)
        _ensure_restore_target(session, target_tables)
        session.execute(text("SET CONSTRAINTS ALL DEFERRED"))
        _remove_target_preseeds(session, target_tables)
        for table in tables:
            rows = rows_by_table.get(table.name, [])
            if not rows:
                continue
            if table.name == "calculation_artifact":
                rows = [{**row, "uri": artifact_uris[row["sha256"]]} for row in rows]
            session.execute(table.insert(), rows)
        _repair_sequences(session, tables)

    return ArchiveRestoreReport(
        rows_restored=sum(counts.values()),
        blobs_restored=len(blobs),
        table_row_counts=dict(counts),
    )


__all__ = [
    "ARCHIVE_SCHEMA",
    "ArchiveCompatibilityError",
    "ArchiveError",
    "ArchiveIntegrityError",
    "ArchiveNotEmptyError",
    "ArchiveRestoreReport",
    "restore_archive",
    "write_archive",
]
