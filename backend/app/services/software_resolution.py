from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from app.db.models.software import Software, SoftwareRelease
from app.schemas.fragments.refs import SoftwareReleaseRef
from app.schemas.utils import normalize_required_text

_SOFTWARE_NAME_ALIASES = {
    "arc": "ARC",
    "gaussian": "Gaussian",
    "orca": "ORCA",
    "rmg": "RMG",
}


def _null_safe_equals(column: ColumnElement, value: str | None) -> ColumnElement[bool]:
    """Build a nullable equality predicate for dedupe lookups.

    :param column: SQLAlchemy column expression to compare.
    :param value: Candidate value, possibly ``None``.
    :returns: ``column IS NULL`` when ``value`` is ``None``, otherwise ``column = value``.
    """

    return column.is_(None) if value is None else column == value


def normalize_software_name(name: str) -> str:
    """Normalize uploaded software names to canonical stored identities.

    :param name: Raw uploaded software name.
    :returns: Canonical normalized software name used for dedupe and storage.
    """

    normalized = normalize_required_text(name)
    alias_key = " ".join(normalized.split()).lower()
    return _SOFTWARE_NAME_ALIASES.get(alias_key, normalized)


def resolve_software(
    session: Session,
    name: str,
) -> Software:
    """Resolve or create the stable software identity row.

    :param session: Active SQLAlchemy session.
    :param name: Uploaded software name.
    :returns: Existing or newly created ``Software`` row.
    """

    normalized_name = normalize_software_name(name)
    software = session.scalar(select(Software).where(Software.name == normalized_name))
    if software is None:
        try:
            with session.begin_nested():
                software = Software(name=normalized_name)
                session.add(software)
                session.flush()
        except IntegrityError:
            software = session.scalar(select(Software).where(Software.name == normalized_name))

    return software


def resolve_software_release(
    session: Session,
    *,
    name: str,
    version: str | None = None,
    revision: str | None = None,
    build: str | None = None,
    release_date=None,
    notes: str | None = None,
) -> SoftwareRelease:
    """Resolve or create an exact software release row.

    :param session: Active SQLAlchemy session.
    :param name: Uploaded software name.
    :param version: Optional release version string.
    :param revision: Optional revision string or git commit hash.
    :param build: Optional build variant string.
    :param release_date: Optional release date metadata.
    :param notes: Optional free-text notes.
    :returns: Existing or newly created ``SoftwareRelease`` row.
    """

    software = resolve_software(session, name)

    release = session.scalar(
        select(SoftwareRelease).where(
            SoftwareRelease.software_id == software.id,
            _null_safe_equals(SoftwareRelease.version, version),
            _null_safe_equals(SoftwareRelease.revision, revision),
            _null_safe_equals(SoftwareRelease.build, build),
        )
    )
    if release is None:
        try:
            with session.begin_nested():
                release = SoftwareRelease(
                    software_id=software.id,
                    version=version,
                    revision=revision,
                    build=build,
                    release_date=release_date,
                    notes=notes,
                )
                session.add(release)
                session.flush()
        except IntegrityError:
            release = session.scalar(
                select(SoftwareRelease).where(
                    SoftwareRelease.software_id == software.id,
                    _null_safe_equals(SoftwareRelease.version, version),
                    _null_safe_equals(SoftwareRelease.revision, revision),
                    _null_safe_equals(SoftwareRelease.build, build),
                )
            )

    return release


def resolve_software_release_ref(
    session: Session,
    ref: SoftwareReleaseRef,
) -> SoftwareRelease:
    """Resolve or create a software release row from an upload reference.

    :param session: Active SQLAlchemy session.
    :param ref: Upload-facing software release reference.
    :returns: Existing or newly created ``SoftwareRelease`` row.
    """

    return resolve_software_release(
        session,
        name=ref.name,
        version=ref.version,
        revision=ref.revision,
        build=ref.build,
        release_date=ref.release_date,
        notes=ref.notes,
    )
