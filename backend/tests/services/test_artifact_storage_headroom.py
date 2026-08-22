"""How much room is left — measured before a write is refused.

WHY THIS FILE EXISTS
    TCKDB learned its object store was full by *being refused*. Nothing
    warned while it was getting full, so the first notice was a depositor's
    failed upload. This is the before-the-fact half, and it is the kind of
    code that fails silently in the safe-looking direction: a headroom
    number that is too large produces a green ``/status`` and no warning,
    which is indistinguishable from a store with plenty of room. So the
    tests here are mostly about the ways the number can be wrong while
    still looking plausible.

THE ONE THAT MATTERS MOST
    ``test_the_same_content_deposited_twice_is_counted_once``. The object
    store is content-addressed — one object per distinct sha256 — while
    ``calculation_artifact`` is append-only and records one row per upload
    *event*. Two uploads of the same file are therefore two rows and one
    object, and a plain ``SUM(bytes)`` counts the bytes twice. That is not
    a rounding error: a producer re-uploading a run inflates the total by
    100 %, TCKDB reports a store as nearly full that is half empty, and
    the warning it raises is false. The test deposits the same content
    twice through the real persistence path and fails against the naive
    sum.

WHAT IS AND IS NOT FAKED
    The ledger half is real: a real database, real ``calculation_artifact``
    rows written by ``persist_artifact_batch``, and a fake object store
    that is content-addressed like the real one (a dict keyed by digest),
    so "two rows, one object" is *observed* rather than asserted.

    The two admin arms are faked, because they are HTTP against MinIO and
    are measured in ``test_artifact_storage_admin.py``. What is tested here
    is the arithmetic between them: which arm wins, what happens when one
    or both say nothing, and that "said nothing" never becomes "said zero".
"""

from __future__ import annotations

import base64
import hashlib

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.schemas.fragments.artifact import ArtifactIn
from app.services import artifact_persistence, artifact_storage
from app.services import artifact_storage_admin as admin
from app.services import artifact_storage_headroom as headroom
from tests.services.scientific_read._factories import (
    make_calculation,
    make_species,
    make_species_entry,
)

ENDPOINT = "http://minio.test:9000"
BUCKET = "tckdb-artifacts-test"
CEILING = artifact_storage.MAX_ARTIFACT_BYTES


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------


@pytest.fixture
def content_addressed_store(monkeypatch) -> dict[str, bytes]:
    """A fake object store that deduplicates the way the real one does.

    Keyed by digest, so depositing the same bytes twice leaves one entry.
    A list-of-writes fake would have let the dedup claim go unverified —
    the whole point is that the store holds fewer objects than the ledger
    holds rows.
    """
    objects: dict[str, bytes] = {}

    def _fake_store(content: bytes, sha256: str) -> str:
        objects[sha256] = content
        return f"s3://{BUCKET}/{sha256[:2]}/{sha256}"

    monkeypatch.setattr(
        "app.services.artifact_persistence.store_artifact", _fake_store
    )
    return objects


def _artifact(content: bytes, filename: str = "note.txt") -> ArtifactIn:
    return ArtifactIn(
        kind="ancillary",
        filename=filename,
        content_base64=base64.b64encode(content).decode("ascii"),
        sha256=hashlib.sha256(content).hexdigest(),
        bytes=len(content),
    )


def _deposit(session, calculation_id: int, content: bytes, filename: str) -> None:
    artifact_persistence.persist_artifact_batch(
        session,
        calculation_id=calculation_id,
        artifacts=[_artifact(content, filename)],
    )


def _a_calculation(db_session):
    """A ``calculation`` row an artifact can hang off.

    ``ck_calculation_one_owner`` requires an owning entry, so the species
    pair is created rather than skipped -- an artifact with no calculation
    is not a shape the ledger ever has to sum.
    """
    entry = make_species_entry(db_session, make_species(db_session))
    return make_calculation(db_session, species_entry_id=entry.id)


def _ledger(db_session) -> int | None:
    """Sum the ledger the way production does: through a *factory*.

    ``deposited_bytes`` opens and closes its own session, which is right in
    production and fatal if handed the test's own session -- ``with
    session:`` closes it, and everything the test does afterwards fails on
    a closed session. A savepoint-joining sessionmaker on the same
    connection gives it a session of its own that still sees this test's
    uncommitted rows.
    """
    factory = sessionmaker(
        bind=db_session.connection(),
        join_transaction_mode="create_savepoint",
    )
    return headroom.deposited_bytes(factory)


def test_the_same_content_deposited_twice_is_counted_once(
    db_session, content_addressed_store
) -> None:
    """Two rows, one object, one lot of bytes.

    The store is content-addressed and ``calculation_artifact`` is
    append-only, so this is the *ordinary* shape of a re-upload rather than
    a corner case: ARC resubmitting a job, a bundle replayed, a producer
    retrying after a network failure.

    Mutation-checked: replacing the distinct-digest sum with
    ``SELECT COALESCE(SUM(bytes), 0) FROM calculation_artifact`` makes this
    fail with ``assert 210 == 105`` and nothing else in the file move.
    """
    calc = _a_calculation(db_session)
    content = b"the very same bytes, uploaded twice" * 3
    before = _ledger(db_session)

    _deposit(db_session, calc.id, content, "first-upload.txt")
    _deposit(db_session, calc.id, content, "second-upload.txt")

    rows = db_session.execute(
        text(
            "SELECT count(*) FROM calculation_artifact WHERE calculation_id = :cid"
        ),
        {"cid": calc.id},
    ).scalar_one()
    assert rows == 2, "the ledger is append-only; both uploads must be recorded"
    assert len(content_addressed_store) == 1, (
        "the store is content-addressed; the same digest is one object"
    )

    assert _ledger(db_session) == before + len(content), (
        "the second row's bytes were counted again, so the ledger claims the "
        "store holds twice what it holds"
    )


def test_two_different_artifacts_are_both_counted(
    db_session, content_addressed_store
) -> None:
    """The other half of the pair, and it is not redundant.

    A query that collapsed *everything* to one row — ``SELECT MIN(bytes)``
    without the ``GROUP BY`` — passes the dedup test above and is
    catastrophically wrong here. Distinct content is distinct objects.

    Mutation-checked: that exact substitution fails here with
    ``assert 100 == 220`` while the dedup test above stays green, which is
    why the pair is kept rather than either one alone.
    """
    calc = _a_calculation(db_session)
    first = b"alpha" * 20
    second = b"beta" * 30
    before = _ledger(db_session)

    _deposit(db_session, calc.id, first, "alpha.txt")
    _deposit(db_session, calc.id, second, "beta.txt")

    assert len(content_addressed_store) == 2
    assert _ledger(db_session) == before + len(first) + len(second)


def test_an_empty_ledger_is_zero_and_not_no_opinion(db_session) -> None:
    """Zero deposited bytes is a fact; ``None`` is the absence of one.

    They must not be confused, because they take opposite paths: ``0``
    means the whole quota is headroom, ``None`` removes the quota arm from
    the comparison entirely.
    """
    db_session.execute(text("DELETE FROM calculation_artifact"))
    assert _ledger(db_session) == 0


def test_a_database_failure_is_no_opinion_and_never_raises() -> None:
    """Not ``0``, which would be the dangerous direction.

    A database TCKDB cannot read says nothing about how full the object
    store is. Reporting ``0`` deposited would claim the entire quota is
    free on a store that may be at its limit — a confidently wrong green,
    which is worse than no signal.
    """

    def _broken_factory():
        raise RuntimeError("the database is not there")

    assert headroom.deposited_bytes(_broken_factory) is None


# ---------------------------------------------------------------------------
# The two arms
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_admin_calls_by_default(monkeypatch):
    """Neither arm has an opinion unless a test gives it one.

    Also clears the quota cache around every test: a module-level cache
    that leaked between tests would make them order-dependent, and an
    order-dependent test about a monitor is a monitor nobody can trust.
    """
    headroom.clear_quota_cache()
    monkeypatch.setattr(admin, "report_free_bytes", lambda **_kw: None)
    monkeypatch.setattr(admin, "report_bucket_quota_bytes", lambda **_kw: None)
    yield
    headroom.clear_quota_cache()


def _arms(monkeypatch, *, quota=None, free=None, ledger=None):
    monkeypatch.setattr(admin, "report_bucket_quota_bytes", lambda **_kw: quota)
    monkeypatch.setattr(admin, "report_free_bytes", lambda **_kw: free)
    monkeypatch.setattr(headroom, "deposited_bytes", lambda _factory=None: ledger)


def _headroom(**kwargs):
    return headroom.current_headroom(
        endpoint_url=ENDPOINT,
        bucket=BUCKET,
        access_key="ak",
        secret_key="sk",
        session_factory=lambda: None,
        **kwargs,
    )


def test_the_quota_arm_is_the_quota_minus_what_tckdb_deposited(monkeypatch) -> None:
    _arms(monkeypatch, quota=100 * 1024**2, ledger=40 * 1024**2)
    report = _headroom()
    assert report is not None
    assert report.bytes == 60 * 1024**2
    assert report.source == "bucket_quota"
    assert report.quota_bytes == 100 * 1024**2
    assert report.ledger_bytes == 40 * 1024**2
    assert report.free_bytes is None


def test_the_free_space_arm_stands_alone_when_no_quota_is_set(monkeypatch) -> None:
    """The default deployment, and the Pi.

    MinIO answers ``quota: 0`` when none is configured, which this module
    reads as no opinion — so free space is the only arm, and it had better
    still produce an answer or the feature does nothing where it is
    actually deployed.
    """
    _arms(monkeypatch, quota=None, free=7 * 1024**2)
    report = _headroom()
    assert report is not None
    assert report.bytes == 7 * 1024**2
    assert report.source == "free_space"
    assert report.quota_bytes is None
    assert report.quota_age_seconds is None


@pytest.mark.parametrize(
    "quota,ledger,free,expected_bytes,expected_source",
    [
        # Free disk is plentiful; the quota is what will refuse the write.
        (20 * 1024**2, 19 * 1024**2, 400 * 1024**2, 1 * 1024**2, "bucket_quota"),
        # A generous quota on a drive that is nearly gone.
        (10 * 1024**3, 1 * 1024**3, 3 * 1024**2, 3 * 1024**2, "free_space"),
    ],
)
def test_the_tighter_arm_wins_and_says_so(
    monkeypatch, quota, ledger, free, expected_bytes, expected_source
) -> None:
    """``min()``, and the *name* of the winner, because the remedies differ.

    "Free disk" and "raise the quota" send an operator to different places.
    A single number without its source would have them guessing, and the
    measured case that motivates this is stark: 418 MiB free while a 2 MiB
    write was refused for quota.
    """
    _arms(monkeypatch, quota=quota, ledger=ledger, free=free)
    report = _headroom()
    assert report is not None
    assert report.bytes == expected_bytes
    assert report.source == expected_source


def test_neither_arm_speaking_is_no_opinion(monkeypatch) -> None:
    """AWS S3, a non-MinIO store, a 403, a timeout.

    ``None`` and not a ``Headroom`` of zero. Zero would read as "no room"
    and would warn forever on every deployment that is not MinIO.
    """
    _arms(monkeypatch, quota=None, free=None)
    assert _headroom() is None


def test_a_quota_with_an_unreadable_ledger_drops_that_arm(monkeypatch) -> None:
    """Half an arm is not an arm.

    ``quota - None`` has no value, and substituting ``0`` for the ledger
    would report the full quota as headroom on a store that could be at
    its limit. The arm is dropped; the other one still answers.
    """
    _arms(monkeypatch, quota=100 * 1024**2, ledger=None, free=9 * 1024**2)
    report = _headroom()
    assert report is not None
    assert report.source == "free_space"
    assert report.bytes == 9 * 1024**2
    assert report.ledger_bytes is None


def test_a_bucket_already_over_its_quota_reports_a_negative(monkeypatch) -> None:
    """Not clamped to zero.

    Zero reads as "exactly no room left". Minus 40 MB says how far past
    the line the bucket already is, which is the number that tells an
    operator how much to reclaim.
    """
    _arms(monkeypatch, quota=100 * 1024**2, ledger=140 * 1024**2)
    report = _headroom()
    assert report is not None
    assert report.bytes == -40 * 1024**2
    assert report.is_low is True


@pytest.mark.parametrize(
    "free,low",
    [
        (CEILING - 1, True),
        (CEILING, False),
        (CEILING + 1, False),
    ],
)
def test_the_predicate_is_the_largest_artifact_tckdb_accepts(
    monkeypatch, free, low
) -> None:
    """``headroom < MAX_ARTIFACT_BYTES``, on the boundary.

    Not a percentage. "80 % full" invites an operator to read a store as
    safe when the next 50 MB write will fail; this says exactly that the
    store can or cannot take one more artifact of the maximum size the
    upload path will accept.
    """
    _arms(monkeypatch, free=free)
    report = _headroom()
    assert report is not None
    assert report.is_low is low


def test_a_probe_that_raises_does_not_take_status_with_it(monkeypatch) -> None:
    """Both callees promise never to raise. This is what happens if one lies.

    ``/status`` exists to answer while things are broken, so the capacity
    warning is the last thing that may turn it into a 500.
    """

    def _explode(**_kw):
        raise RuntimeError("the admin client grew a new failure mode")

    monkeypatch.setattr(admin, "report_bucket_quota_bytes", _explode)
    assert _headroom() is None


# ---------------------------------------------------------------------------
# The quota cache
# ---------------------------------------------------------------------------


def test_the_quota_is_fetched_once_and_then_believed(monkeypatch) -> None:
    """A quota changes about once a year; ``/status`` is polled every 5 min.

    Caching it is what keeps the added cost of this feature to one cheap
    database aggregate on the common path. The staleness is reported rather
    than hidden — ``quota_age_seconds`` is what tells an operator whether
    the quota they just raised has been picked up yet.
    """
    calls: list[dict] = []

    def _count(**kw):
        calls.append(kw)
        return 100 * 1024**2

    monkeypatch.setattr(admin, "report_bucket_quota_bytes", _count)
    monkeypatch.setattr(headroom, "deposited_bytes", lambda _f=None: 0)

    clock = [1000.0]
    first = _headroom(monotonic=lambda: clock[0])
    assert first is not None and first.quota_age_seconds == 0.0

    clock[0] = 1000.0 + headroom._QUOTA_TTL_SECONDS - 1
    second = _headroom(monotonic=lambda: clock[0])
    assert second is not None
    assert len(calls) == 1, "the quota was re-fetched inside its TTL"
    assert second.quota_age_seconds == pytest.approx(
        headroom._QUOTA_TTL_SECONDS - 1
    ), "a cached quota must report its age, not pretend to be fresh"

    clock[0] = 1000.0 + headroom._QUOTA_TTL_SECONDS + 1
    third = _headroom(monotonic=lambda: clock[0])
    assert third is not None
    assert len(calls) == 2, "the quota was never re-fetched after its TTL expired"
    assert third.quota_age_seconds == 0.0


def test_a_store_with_no_quota_is_not_re_asked_every_poll(monkeypatch) -> None:
    """``None`` is cached too.

    The default deployment has no bucket quota, and it is the one polled
    every five minutes forever. Re-asking a question already answered
    "there is no quota" would make the no-quota case the *expensive* one.
    """
    calls: list[dict] = []

    def _count(**kw):
        calls.append(kw)
        return None

    monkeypatch.setattr(admin, "report_bucket_quota_bytes", _count)
    monkeypatch.setattr(admin, "report_free_bytes", lambda **_kw: 1024)

    clock = [500.0]
    _headroom(monotonic=lambda: clock[0])
    clock[0] += 10
    _headroom(monotonic=lambda: clock[0])
    assert len(calls) == 1


def test_the_ttl_is_configurable_and_a_bad_value_is_ignored(monkeypatch) -> None:
    """``TCKDB_STORAGE_QUOTA_TTL_SECONDS``, with a safe default.

    A typo in an environment variable must not silently disable the cache
    or, worse, make the TTL negative and re-fetch on every poll.
    """
    monkeypatch.setenv("TCKDB_STORAGE_QUOTA_TTL_SECONDS", "30")
    assert headroom._ttl_seconds() == 30.0

    monkeypatch.setenv("TCKDB_STORAGE_QUOTA_TTL_SECONDS", "0")
    assert headroom._ttl_seconds() == 0.0

    for nonsense in ("soon", "-5", ""):
        monkeypatch.setenv("TCKDB_STORAGE_QUOTA_TTL_SECONDS", nonsense)
        assert headroom._ttl_seconds() == headroom._QUOTA_TTL_SECONDS
