from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.software import Software, SoftwareRelease
from app.schemas.fragments.refs import SoftwareReleaseRef
from app.services.software_resolution import resolve_software_release_ref


def test_resolve_software_release_ref_consolidates_same_software_and_release(
    db_conn,
) -> None:
    with Session(db_conn) as session:
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
    db_conn,
) -> None:
    first_version = "test-9.9.1"
    second_version = "test-9.9.2"

    with Session(db_conn) as session:
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


# --- trailing version token: linear, and the same answer as the regex it replaced -------------------------


def _old_strip_trailing_version(folded: str) -> str:
    import re

    return re.sub(r"[\s._-]+v?\d[\w.+-]*$", "", folded, flags=re.IGNORECASE)


@pytest.mark.parametrize(
    "name",
    [
        "arc 1.1.0", "arc-1.1.0", "arc_v3", "arc v3.0", "rmg-py 3.2.0", "orca 6.1.0", "archem", "arc v",
        "arc 1.1.0 beta", "arc 1.1.0-rc1+build", "a--1", "a.1", "a v1 v2", "arc  v3", "arc-1é",
        "arc ٣", "arc 1 x", "1", " ", "", "v1", "-1", "a -1",
    ],
    ids=repr,
)
def test_trailing_version_strip_agrees_with_the_regex_it_replaced(name: str) -> None:
    from app.services.software_resolution import _strip_trailing_version

    folded = " ".join(name.split()).lower()
    assert _strip_trailing_version(folded) == _old_strip_trailing_version(folded)


def test_trailing_version_strip_agrees_with_the_regex_on_random_names() -> None:
    import random

    from app.services.software_resolution import _strip_trailing_version

    rng = random.Random(7)
    alphabet = ["a", "b", "1", "v", " ", "_", ".", "-", "+", "é", "3", "x", "(", "²", "٣"]
    for _ in range(20_000):
        folded = " ".join("".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12))).split()).lower()
        assert _strip_trailing_version(folded) == _old_strip_trailing_version(folded), folded


@pytest.mark.parametrize(
    "name",
    ["a" + "_" * 200_000 + "b", "a-" + "1-" * 100_000 + " x", "a" + "-1" * 100_000],
    ids=["underscores", "digits", "long-token"],
)
def test_trailing_version_strip_is_fast_on_long_runs(name: str) -> None:
    import time

    from app.services.software_resolution import _strip_trailing_version

    started = time.perf_counter()
    _strip_trailing_version(name)
    assert time.perf_counter() - started < 0.5
