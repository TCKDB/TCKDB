"""What the release gate actually sweeps, and what reclaiming an orphan risks.

The sweep is the answer ADR 0014 gives to "an artifact nobody downloads
is checked by nothing", and its designated trigger is cutting a citable
release. That makes ``--release`` the load-bearing scope: if it walks the
wrong set, the release runbook records a green gate over evidence nobody
looked at.
"""

from __future__ import annotations

import hashlib
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.db.models.common import (
    ArtifactKind,
    CalculationDependencyRole,
    CalculationType,
    RecordReviewStatus,
    SubmissionRecordType,
    ThermoCalculationRole,
)
from app.db.models.thermo import ThermoSourceCalculation
from app.services.record_review import set_record_review_status
from app.services.release.curation import (
    add_selection,
    create_release,
    resolve_curation_policy,
    withdraw_selection,
)
from tests.services.scientific_read._factories import (
    attach_artifact,
    attach_dependency,
    make_calculation,
    make_lot,
    make_species,
    make_species_entry,
    make_thermo_scalar,
)

_SWEEP = Path(__file__).parents[2] / "scripts" / "ops" / "verify_artifact_integrity.py"


def _load_sweep():
    spec = importlib.util.spec_from_file_location("verify_artifact_integrity", _SWEEP)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def sweep():
    return _load_sweep()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _owned_calculation(session):
    """A calculation with an owner, as ``ck_calculation_one_owner`` requires."""
    species = make_species(session)
    entry = make_species_entry(session, species=species)
    return make_calculation(session, species_entry_id=entry.id)


@pytest.fixture
def curator(db_session):
    from app.db.models.app_user import AppUser
    from app.db.models.common import AppUserRole

    user = AppUser(username="sweep-curator", role=AppUserRole.curator)
    db_session.add(user)
    db_session.flush()
    return user


@pytest.fixture
def released_thermo(db_session, curator):
    """A published-shape release selecting one thermo backed by two calcs.

    The thermo cites a freq calculation through
    ``thermo_source_calculation``; that freq depends on an opt. Both logs
    are evidence a reader following the release's provenance reaches, and
    neither is selected by the release directly -- a release cannot select
    a calculation at all.
    """
    species = make_species(db_session, smiles="CCO")
    entry = make_species_entry(db_session, species=species)
    lot = make_lot(db_session)
    opt = make_calculation(
        db_session,
        type=CalculationType.opt,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    freq = make_calculation(
        db_session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    attach_dependency(
        db_session,
        parent=opt,
        child=freq,
        role=CalculationDependencyRole.freq_on,
    )
    attach_artifact(
        db_session,
        calculation=opt,
        kind=ArtifactKind.output_log,
        sha256=_digest("sweep-opt"),
        filename="opt.log",
    )
    attach_artifact(
        db_session,
        calculation=freq,
        kind=ArtifactKind.output_log,
        sha256=_digest("sweep-freq"),
        filename="freq.log",
    )
    thermo = make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-234.5)
    db_session.add(
        ThermoSourceCalculation(
            thermo_id=thermo.id,
            calculation_id=freq.id,
            role=ThermoCalculationRole.freq,
        )
    )
    set_record_review_status(
        db_session,
        record_type=SubmissionRecordType.thermo,
        record_id=thermo.id,
        status=RecordReviewStatus.approved,
        actor=curator,
        note="approved for the sweep fixture",
    )
    policy = resolve_curation_policy(
        db_session,
        name="sweep-policy",
        version="1.0",
        description="Prefer the composite single point.",
        criteria={"requires_review_status": "approved"},
        created_by=curator.id,
    )
    release = create_release(
        db_session,
        tag="2026.08.0",
        title="Sweep coverage fixture",
        curation_policy=policy,
        data_license="CC-BY-4.0",
        code_license="MIT",
        citation_text="TCKDB sweep fixture release.",
        contact="tckdb-maintainers@example.org",
        changelog_entry="Fixture.",
        created_by=curator.id,
    )
    selection = add_selection(
        db_session,
        release=release,
        record_type=SubmissionRecordType.thermo,
        record_id=thermo.id,
        subject_type=SubmissionRecordType.species_entry,
        subject_id=entry.id,
        rationale="Frequencies all real; composite single point.",
        selected_by=curator.id,
    )
    db_session.flush()
    return {
        "release": release,
        "selection": selection,
        "thermo": thermo,
        "opt": opt,
        "freq": freq,
        "curator": curator,
    }


# ---------------------------------------------------------------------------
# --release scope
# ---------------------------------------------------------------------------


def test_release_scope_reaches_the_calculations_a_product_cites(
    db_session, sweep, released_thermo
):
    """A release selects products, so a product's sources are the scope.

    Regression: the scope query filtered ``record_type == calculation``,
    which ``SELECTABLE_RECORD_TYPES`` forbids and a check constraint
    rejects. ``--release`` therefore matched nothing at all and exited 0,
    reporting a clean gate over an empty sweep.
    """
    ids = sweep._release_calculation_ids(
        db_session, released_thermo["release"].public_ref
    )

    assert released_thermo["freq"].id in ids


def test_release_scope_follows_the_dependency_chain_upward(
    db_session, sweep, released_thermo
):
    """The opt whose geometry the cited freq used is evidence too."""
    ids = sweep._release_calculation_ids(
        db_session, released_thermo["release"].public_ref
    )

    assert released_thermo["opt"].id in ids


def test_release_scope_covers_the_artifacts_of_those_calculations(
    db_session, sweep, released_thermo
):
    artifacts = sweep._distinct_artifacts(
        db_session,
        sha256=None,
        calculation_ref=None,
        release_ref=released_thermo["release"].public_ref,
        limit=None,
    )

    assert {row.sha256 for row in artifacts} == {
        _digest("sweep-opt"),
        _digest("sweep-freq"),
    }


def test_a_withdrawn_selection_is_not_part_of_the_release(
    db_session, sweep, released_thermo
):
    """Evidence a release no longer stands behind is not its evidence."""
    withdraw_selection(
        db_session,
        superseded=released_thermo["selection"],
        rationale="Superseded by a better composite; withdrawn from the release.",
        selected_by=released_thermo["curator"].id,
    )
    db_session.flush()

    with pytest.raises(SystemExit, match="cites no calculations"):
        sweep._distinct_artifacts(
            db_session,
            sha256=None,
            calculation_ref=None,
            release_ref=released_thermo["release"].public_ref,
            limit=None,
        )


# ---------------------------------------------------------------------------
# Orphan reclaim
# ---------------------------------------------------------------------------


class _FakeBucket:
    """Enough of an S3 client to enumerate keys and move one."""

    def __init__(self, objects: dict[str, datetime]) -> None:
        self.objects = dict(objects)
        self.copied: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    def get_paginator(self, _name):
        bucket = self

        class _Paginator:
            def paginate(self, *, Bucket, Prefix=""):
                yield {
                    "Contents": [
                        {"Key": key, "LastModified": modified}
                        for key, modified in sorted(bucket.objects.items())
                        if key.startswith(Prefix)
                    ]
                }

        return _Paginator()

    def copy_object(self, *, Bucket, Key, CopySource):
        source = CopySource["Key"]
        if source not in self.objects:
            raise AssertionError(f"copy of a key that is not there: {source}")
        self.copied.append((source, Key))
        self.objects[Key] = self.objects[source]

    def delete_object(self, *, Bucket, Key):
        self.deleted.append(Key)
        self.objects.pop(Key, None)


def _cas_key(digest: str) -> str:
    return f"{digest[:2]}/{digest}"


def test_a_recently_written_unreferenced_object_is_not_an_orphan(db_session, sweep):
    """An upload writes its bytes before its row commits.

    Without an age floor the sweep would call an in-flight upload garbage,
    which is the exact failure the retain-on-rollback behaviour exists to
    avoid.
    """
    digest = _digest("fresh-unreferenced")
    client = _FakeBucket({_cas_key(digest): datetime.now(timezone.utc)})

    orphans = sweep._find_orphans(
        db_session, client=client, bucket="b", min_age_days=30
    )

    assert digest not in orphans


def test_an_old_unreferenced_object_is_an_orphan(db_session, sweep):
    digest = _digest("stale-unreferenced")
    client = _FakeBucket(
        {_cas_key(digest): datetime.now(timezone.utc) - timedelta(days=90)}
    )

    orphans = sweep._find_orphans(
        db_session, client=client, bucket="b", min_age_days=30
    )

    assert digest in orphans


def test_a_referenced_object_is_never_an_orphan(db_session, sweep):
    calculation = _owned_calculation(db_session)
    digest = _digest("referenced-and-old")
    attach_artifact(db_session, calculation=calculation, sha256=digest)
    client = _FakeBucket(
        {_cas_key(digest): datetime.now(timezone.utc) - timedelta(days=365)}
    )

    orphans = sweep._find_orphans(
        db_session, client=client, bucket="b", min_age_days=30
    )

    assert digest not in orphans


def test_reclaiming_moves_bytes_and_never_deletes_them(db_session, sweep):
    """Reclaim is a rename. Being wrong must not cost the bytes."""
    digest = _digest("reclaimable")
    client = _FakeBucket(
        {_cas_key(digest): datetime.now(timezone.utc) - timedelta(days=90)}
    )

    held = sweep._reclaim_orphans(
        lambda: _NullSession(), [digest], client=client, bucket="b"
    )

    assert held == [digest]
    assert client.copied == [(_cas_key(digest), f"reclaimed/{digest}")]
    assert client.deleted == [_cas_key(digest)]
    assert f"reclaimed/{digest}" in client.objects


def test_reclaim_rechecks_references_after_the_scan(db_session, sweep):
    """A row committed while the bucket was being enumerated wins.

    The first reference check is stale by the length of the enumeration,
    so the object is re-checked immediately before it is moved.
    """
    calculation = _owned_calculation(db_session)
    digest = _digest("referenced-mid-scan")
    attach_artifact(db_session, calculation=calculation, sha256=digest)
    client = _FakeBucket(
        {_cas_key(digest): datetime.now(timezone.utc) - timedelta(days=90)}
    )

    held = sweep._reclaim_orphans(
        _SessionFactory(db_session), [digest], client=client, bucket="b"
    )

    assert held == []
    assert client.copied == []
    assert client.deleted == []


def test_held_objects_are_not_re_reported_as_orphans(db_session, sweep):
    """The hold is outside the content-addressed namespace, so it is skipped."""
    digest = _digest("already-held")
    client = _FakeBucket(
        {f"reclaimed/{digest}": datetime.now(timezone.utc) - timedelta(days=90)}
    )

    orphans = sweep._find_orphans(
        db_session, client=client, bucket="b", min_age_days=30
    )

    assert orphans == []


def test_purging_the_hold_respects_its_own_age_floor(db_session, sweep):
    fresh = _digest("held-yesterday")
    stale = _digest("held-last-year")
    client = _FakeBucket(
        {
            f"reclaimed/{fresh}": datetime.now(timezone.utc) - timedelta(days=1),
            f"reclaimed/{stale}": datetime.now(timezone.utc) - timedelta(days=365),
        }
    )

    purged = sweep._purge_hold(db_session, client=client, bucket="b", min_age_days=90)

    assert purged == [stale]
    assert f"reclaimed/{fresh}" in client.objects
    assert f"reclaimed/{stale}" not in client.objects


class _NullSession:
    """A session whose reference query returns nothing."""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def scalars(self, _statement):
        class _Empty:
            def all(self_inner):
                return []

        return _Empty()


class _SessionFactory:
    """Hand the reclaim re-check the test's own session."""

    def __init__(self, session) -> None:
        self._session = session

    def __call__(self):
        return self

    def __enter__(self):
        return self._session

    def __exit__(self, *_exc):
        return False
