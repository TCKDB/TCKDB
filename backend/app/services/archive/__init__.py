"""Portable scientific-state archive interface.

Callers and tests should use only :func:`write_archive`,
:func:`restore_archive` and the offline :func:`verify_archive`; table
traversal, codecs, packaging, and integrity verification remain inside this
module.
"""

from app.services.archive.core import (
    ARCHIVE_SCHEMA,
    ArchiveCompatibilityError,
    ArchiveError,
    ArchiveIntegrityError,
    ArchiveNotEmptyError,
    ArchiveRestoreReport,
    ArchiveVerificationReport,
    restore_archive,
    verify_archive,
    write_archive,
)

__all__ = [
    "ARCHIVE_SCHEMA",
    "ArchiveCompatibilityError",
    "ArchiveError",
    "ArchiveIntegrityError",
    "ArchiveNotEmptyError",
    "ArchiveRestoreReport",
    "ArchiveVerificationReport",
    "restore_archive",
    "verify_archive",
    "write_archive",
]
