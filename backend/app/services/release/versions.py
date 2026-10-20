"""Version binding for a dataset release manifest.

A citation is only reproducible if the reader can tell *which* code and
*which* schema produced the numbers. This module collects those facts in one
place so the manifest builder never has to guess.

Every lookup degrades to an explicit ``"unknown"`` rather than raising: a
release cut from a source checkout that was never ``pip install``-ed should
still produce a manifest, and a manifest that honestly says ``unknown`` is far
more useful than a failed release or a silently fabricated version string.
"""

from __future__ import annotations

from importlib import metadata

from sqlalchemy import text
from sqlalchemy.orm import Session
from tckdb_schemas.fragments.version import VersionRef

from app.services.document_formats import ARCHIVE, DATASET_RELEASE, FORMATS
from app.services.document_formats import REVIEW_POLICY as REVIEW_POLICY_NAME

#: The dataset-release manifest format, recorded on the manifest row as a name beside an integer version. Bumped
#: only when the rendered manifest document changes shape, since ``content_sha256`` values recorded under an older
#: version would no longer reproduce. The renderer dispatches on the format *stored on the manifest row*
#: (:data:`~app.services.document_formats.FORMATS`), never on this constant, so a row frozen under an older version
#: keeps reproducing its digest.
#:
#: The document carries a ``rights`` block: the data license, the number of rights attestations standing behind the
#: shipped records, and a count per basis kind.
MANIFEST_FORMAT: VersionRef = FORMATS.current(DATASET_RELEASE)

#: The *recovery* archive contract, recorded in the manifest purely so a reader can see that it is a different thing
#: (see ``backend/docs/specs/dataset_release_and_profiles.md``). The same ref as the archive writer's.
RECOVERY_ARCHIVE_FORMAT: VersionRef = FORMATS.current(ARCHIVE)

#: The review/trust policy the corpus was curated under: the ``RecordReviewStatus`` vocabulary plus the guarded
#: transition machine in ``app/services/record_review.py``. Its version is raised when either changes meaning, so a
#: release states what "approved" meant when it was cut.
REVIEW_POLICY: VersionRef = FORMATS.current(REVIEW_POLICY_NAME)

UNKNOWN = "unknown"


def alembic_revision(session: Session) -> str:
    """The migration revision the database is currently at."""
    try:
        rows = session.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    except Exception:  # pragma: no cover - only on a schema-less database
        return UNKNOWN
    if not rows:
        return UNKNOWN
    # A branched head would be a deployment error, not something to hide.
    return ",".join(sorted(str(r) for r in rows))


def _distribution_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return UNKNOWN


def backend_version() -> str:
    """Version of the ``tckdb-backend`` distribution serving the release."""
    return _distribution_version("tckdb-backend")


def schemas_package_version() -> str:
    """Version of the ``tckdb-schemas`` wire-contract package."""
    return _distribution_version("tckdb-schemas")


__all__ = [
    "MANIFEST_FORMAT",
    "RECOVERY_ARCHIVE_FORMAT",
    "REVIEW_POLICY",
    "UNKNOWN",
    "alembic_revision",
    "backend_version",
    "schemas_package_version",
]
