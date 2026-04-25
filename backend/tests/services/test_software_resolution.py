from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.software import Software, SoftwareRelease
from app.schemas.fragments.refs import SoftwareReleaseRef
from app.services.software_resolution import resolve_software_release_ref


def test_resolve_software_release_ref_consolidates_same_software_and_release(
    db_engine,
) -> None:
    with Session(db_engine) as session:
        with session.begin():
            first = resolve_software_release_ref(
                session,
                SoftwareReleaseRef(
                    name="orca",
                    version="5.0.4",
                    revision="4e2c7d8",
                ),
            )
            second = resolve_software_release_ref(
                session,
                SoftwareReleaseRef(
                    name="ORCA",
                    version="5.0.4",
                    revision="4e2c7d8",
                ),
            )

            assert first.id == second.id

            software = session.scalar(
                select(Software).where(Software.id == first.software_id)
            )
            assert software is not None
            assert software.name == "ORCA"

            release = session.scalar(
                select(SoftwareRelease).where(SoftwareRelease.id == first.id)
            )
            assert release is not None
            assert release.version == "5.0.4"
            assert release.revision == "4e2c7d8"

            software_count = session.scalar(
                select(func.count())
                .select_from(Software)
                .where(Software.id == first.software_id)
            )
            release_count = session.scalar(
                select(func.count())
                .select_from(SoftwareRelease)
                .where(
                    SoftwareRelease.software_id == first.software_id,
                    SoftwareRelease.version == "5.0.4",
                    SoftwareRelease.revision == "4e2c7d8",
                    SoftwareRelease.build.is_(None),
                )
            )
            assert software_count == 1
            assert release_count == 1


def test_resolve_software_release_ref_reuses_software_but_splits_versions(
    db_engine,
) -> None:
    first_version = "test-9.9.1"
    second_version = "test-9.9.2"

    with Session(db_engine) as session:
        with session.begin():
            existing_software = session.scalar(
                select(Software).where(Software.name == "ORCA")
            )
            existing_release_count = 0
            if existing_software is not None:
                existing_release_count = session.scalar(
                    select(func.count())
                    .select_from(SoftwareRelease)
                    .where(SoftwareRelease.software_id == existing_software.id)
                )

            first = resolve_software_release_ref(
                session,
                SoftwareReleaseRef(
                    name="ORCA",
                    version=first_version,
                ),
            )
            second = resolve_software_release_ref(
                session,
                SoftwareReleaseRef(
                    name="orca",
                    version=second_version,
                ),
            )

            assert first.id != second.id
            assert first.software_id == second.software_id

            software = session.scalar(
                select(Software).where(Software.id == first.software_id)
            )
            assert software is not None
            assert software.name == "ORCA"

            releases = session.scalars(
                select(SoftwareRelease).where(
                    SoftwareRelease.software_id == first.software_id
                )
            ).all()
            versions = {release.version for release in releases}
            assert first_version in versions
            assert second_version in versions

            software_count = session.scalar(
                select(func.count())
                .select_from(Software)
                .where(Software.id == first.software_id)
            )
            release_count = session.scalar(
                select(func.count())
                .select_from(SoftwareRelease)
                .where(SoftwareRelease.software_id == first.software_id)
            )
            assert software_count == 1
            assert release_count == existing_release_count + 2
