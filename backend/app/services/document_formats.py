"""The document formats TCKDB writes and reads, held in one registry keyed by (name, version).

Every document TCKDB serves or stores -- the dataset-release manifest, the recovery archive, the convenience export,
the ML export, the publication deposit -- opens with a ``format`` header: the format's ``name`` beside its integer
``version``, never one string with the version glued on. A reader asks the registry for the pair the document recorded;
a pair the registry does not hold is refused (:class:`~app.versioning.UnknownVersion`), and is never read as the current
format.

A format that a published release cites must stay readable for as long as that release stands, so it is registered as a
further version of its name with ``current=False`` (the release renderer registers one renderer per version). A format no
release cites is replaced, not kept. Today every name has exactly one version.
"""

from __future__ import annotations

from typing import Any

from tckdb_schemas.fragments.version import VersionRef

from app.versioning import UnknownVersion, VersionRegistry

DATASET_RELEASE = "tckdb.dataset_release"
ARCHIVE = "tckdb.archive"
EXPORT = "tckdb.export"
ML_EXPORT = "tckdb.ml"
DEPOSIT = "tckdb.deposit"
REVIEW_POLICY = "record_review"

#: ``(name, version)`` -> the :class:`VersionRef` itself, for every format this release can read.
FORMATS: VersionRegistry[VersionRef] = VersionRegistry("document_formats", startup_checked=True)
for _name in (DATASET_RELEASE, ARCHIVE, DEPOSIT, REVIEW_POLICY):
    FORMATS.register(_name, 1, VersionRef(name=_name, version=1), current=True)
# Version 2 of the export and the ML export: no export picks a record any more. The native export lists every
# qualifying record (its manifest says ``records``, not ``collapse`` / ``selection_policy``), and the ML species export
# carries ``thermos``, a list, where version 1 carried the one thermo it had picked. Pre-release, so version 1 is not kept.
for _name in (EXPORT, ML_EXPORT):
    FORMATS.register(_name, 2, VersionRef(name=_name, version=2), current=True)
del _name
FORMATS.validate()


def current_header(name: str) -> dict[str, Any]:
    """The ``format`` header a new document of *name* carries: its name and the current integer version."""
    ref = FORMATS.current(name)
    return {"name": ref.name, "version": ref.version}


def header_of(ref: VersionRef) -> dict[str, Any]:
    """A ``format`` header for an explicit ref (a stored one, for a renderer that reproduces an older document)."""
    return {"name": ref.name, "version": ref.version}


def reads_as(header: Any, name: str) -> VersionRef | None:
    """The registered ref *header* names, or ``None`` when it is not a well-formed header of *name* this release reads.

    A header that is not an object, names another format, carries a version that is not exactly an integer, or names a
    version the registry does not hold, is not read.
    """
    if not isinstance(header, dict) or header.get("name") != name or type(header.get("version")) is not int:
        return None
    try:
        FORMATS.get(name, header["version"])
    except UnknownVersion:
        return None
    return VersionRef(name=name, version=header["version"])
