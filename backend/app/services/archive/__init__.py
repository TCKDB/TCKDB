"""Portable scientific-state archive interface.

Callers and tests should use only :func:`write_archive` and
:func:`restore_archive`; table traversal, codecs, packaging, and integrity
verification remain inside this module.
"""

from app.services.archive.core import (
    ARCHIVE_SCHEMA,
    ArchiveCompatibilityError,
    ArchiveError,
    ArchiveIntegrityError,
    ArchiveNotEmptyError,
    ArchiveRestoreReport,
    restore_archive,
    write_archive,
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
