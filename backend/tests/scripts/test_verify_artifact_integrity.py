"""What the release gate actually sweeps, and what reclaiming an orphan risks.

The sweep is the answer ADR 0014 gives to "an artifact nobody downloads
is checked by nothing", and its designated trigger is cutting a citable
release. That makes ``--release`` the load-bearing scope: if it walks the
wrong set, the release runbook records a green gate over evidence nobody
looked at.

The second half of this file is about the *other* way that gate goes
green over nothing. A sweep can be wrong by reporting a clean result it
did not earn, and it had three ways to do that after the ``--release``
scope query was fixed: a scope that resolves to zero digests, a store
that will not answer, and a digest recorded on the ``store_artifact``
dedup path, which has no ``calculation_artifact`` row to be found
through. Each exited 0. The invariant those tests hold is one sentence:
**a verification job must distinguish "nothing was wrong" from "nothing
was checked."**
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from sqlalchemy import func, select

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
    """Import the sweep from its path, as a module that knows its own name.

    Registered in ``sys.modules`` before execution, which is not
    ceremony: the script's ``from __future__ import annotations`` leaves
    every annotation a string, and ``@dataclass`` resolves those by
    looking its own module up by name. Without the registration the
    lookup returns ``None`` and the module fails to import at all.
    """
    spec = importlib.util.spec_from_file_location("verify_artifact_integrity", _SWEEP)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
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
    artifacts = sweep._scope_targets(
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
    db_session, sweep, released_thermo, capsys
):
    """Evidence a release no longer stands behind is not its evidence."""
    withdraw_selection(
        db_session,
        superseded=released_thermo["selection"],
        rationale="Superseded by a better composite; withdrawn from the release.",
        selected_by=released_thermo["curator"].id,
    )
    db_session.flush()

    with pytest.raises(SystemExit) as exit_info:
        sweep._scope_targets(
            db_session,
            sha256=None,
            calculation_ref=None,
            release_ref=released_thermo["release"].public_ref,
            limit=None,
        )

    assert "cites no calculations" in capsys.readouterr().err
    # Not exit 1: that code means a break was recorded, and nothing was
    # read here at all.
    assert exit_info.value.code == sweep.EXIT_NOT_VERIFIED


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

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise ClientError(
                {"Error": {"Code": "404", "Message": "not there"}}, "HeadObject"
            )
        return {"LastModified": self.objects[Key], "ETag": '"etag"'}

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


# ---------------------------------------------------------------------------
# The purge guard
# ---------------------------------------------------------------------------


def test_purging_refuses_a_held_object_a_row_references(db_session, sweep):
    """The last line of defense on the only irreversible operation here.

    Being held is most of the safety argument -- an object away from its
    content-addressed key cannot be deduplicated against -- but not all
    of it. An upload that deduplicated against the object moments before
    it was moved commits afterwards, and then a committed
    ``calculation_artifact`` row points at a digest sitting in the hold.
    This check is the only thing between that row and the permanent loss
    of its bytes, so it gets a test of its own.
    """
    calculation = _owned_calculation(db_session)
    digest = _digest("held-but-referenced")
    attach_artifact(db_session, calculation=calculation, sha256=digest)
    db_session.flush()
    client = _FakeBucket(
        {f"reclaimed/{digest}": datetime.now(timezone.utc) - timedelta(days=365)}
    )

    purged = sweep._purge_hold(db_session, client=client, bucket="b", min_age_days=90)

    assert purged == []
    assert client.deleted == []
    assert f"reclaimed/{digest}" in client.objects


def test_purging_refuses_an_undated_held_object(db_session, sweep):
    """Unknown age is not "old enough".

    The age gate is the whole guard, and a store that will not date an
    object has said its age is unknown -- which is a reason to leave it
    alone, not to treat it as ancient.
    """
    digest = _digest("held-without-a-date")
    client = _FakeBucket({f"reclaimed/{digest}": None})

    purged = sweep._purge_hold(db_session, client=client, bucket="b", min_age_days=90)

    assert purged == []
    assert f"reclaimed/{digest}" in client.objects


def test_an_undated_object_is_not_an_orphan(db_session, sweep):
    digest = _digest("unreferenced-without-a-date")
    client = _FakeBucket({_cas_key(digest): None})

    orphans = sweep._find_orphans(
        db_session, client=client, bucket="b", min_age_days=30
    )

    assert orphans == []


# ---------------------------------------------------------------------------
# Age floors
# ---------------------------------------------------------------------------


class _Args:
    def __init__(self, orphan_age_days=30, purge_hold_days=None):
        self.orphan_age_days = orphan_age_days
        self.purge_hold_days = purge_hold_days


def test_a_zero_orphan_age_is_rejected(sweep, capsys):
    """``--orphan-age-days 0`` reclaims uploads that are still in flight."""
    with pytest.raises(SystemExit) as exit_info:
        sweep._validate_age_floors(_Args(orphan_age_days=0))

    assert "orphan-age-days" in capsys.readouterr().err
    assert exit_info.value.code == sweep.EXIT_NOT_VERIFIED


def test_a_zero_hold_period_is_rejected(sweep, capsys):
    """A hold emptied immediately is not a hold."""
    with pytest.raises(SystemExit) as exit_info:
        sweep._validate_age_floors(_Args(purge_hold_days=0))

    assert "purge-hold-days" in capsys.readouterr().err
    assert exit_info.value.code == sweep.EXIT_NOT_VERIFIED


def test_the_documented_defaults_are_accepted(sweep):
    sweep._validate_age_floors(_Args(orphan_age_days=30, purge_hold_days=90))


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------


def test_restoring_puts_a_held_object_back_at_its_key(db_session, sweep):
    """The documented recovery for the race the reclaim cannot close."""
    digest = _digest("restore-me")
    client = _FakeBucket(
        {f"reclaimed/{digest}": datetime.now(timezone.utc) - timedelta(days=1)}
    )

    assert sweep._restore_held(db_session, digest, client=client, bucket="b") is True
    assert _cas_key(digest) in client.objects
    assert f"reclaimed/{digest}" not in client.objects


def test_restoring_refuses_when_the_key_is_already_occupied(db_session, sweep):
    """A re-upload of the same bytes repopulates the key; that copy wins.

    Content-addressing makes the two byte-identical, so overwriting would
    at best be a no-op -- and at worst would erase a genuine corruption
    that the custody record exists to preserve.
    """
    digest = _digest("restore-contested")
    now = datetime.now(timezone.utc)
    client = _FakeBucket({_cas_key(digest): now, f"reclaimed/{digest}": now})

    assert sweep._restore_held(db_session, digest, client=client, bucket="b") is False
    assert client.copied == []
    assert client.deleted == []
    assert f"reclaimed/{digest}" in client.objects


# ---------------------------------------------------------------------------
# A sweep that checked nothing is not a sweep that found nothing
# ---------------------------------------------------------------------------


class _SessionProxy:
    """The test's own session, wearing the shape ``main`` expects.

    ``main`` uses ``SessionLocal`` two ways: as a context manager for its
    own reads, and as the ``session_factory`` the recorder opens a
    separate transaction on. That separate transaction is deliberate and
    has its own test elsewhere; here it would commit rows outside the
    fixture's rollback, so ``begin`` is neutered.
    """

    def __init__(self, session) -> None:
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def __call__(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def begin(self):
        return contextlib.nullcontext()


class _Body:
    def __init__(self, content: bytes) -> None:
        self._content = content

    def read(self) -> bytes:
        return self._content


class _ReadableBucket:
    """A store that serves bytes, so a sweep over it genuinely verifies."""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = dict(objects)

    def get_object(self, *, Bucket, Key):
        digest = Key.rsplit("/", 1)[-1]
        if digest not in self.objects:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "gone"}}, "GetObject"
            )
        return {"Body": _Body(self.objects[digest])}

    def head_object(self, *, Bucket, Key):
        digest = Key.rsplit("/", 1)[-1]
        if digest not in self.objects:
            raise ClientError(
                {"Error": {"Code": "404", "Message": "gone"}}, "HeadObject"
            )
        return {"ETag": '"etag"', "ContentLength": len(self.objects[digest])}


class _UnreachableBucket:
    """A store that does not answer at all.

    ``EndpointConnectionError`` descends from ``BotoCoreError``, not
    ``ClientError``, which is the arm ``load_artifact_bytes`` turns into
    an ``ArtifactStorageUnavailable`` carrying no ``missing`` flag -- the
    outcome that says nothing whatever about the object.
    """

    def get_object(self, *, Bucket, Key):
        raise EndpointConnectionError(endpoint_url="http://store.invalid")

    def head_object(self, *, Bucket, Key):
        raise EndpointConnectionError(endpoint_url="http://store.invalid")


def _run_main(sweep, monkeypatch, db_session, argv, client):
    """Drive ``main`` end to end against a fake store and the test session."""
    import app.api.deps as deps

    monkeypatch.setattr(
        "sys.argv", ["verify_artifact_integrity.py", *argv], raising=False
    )
    monkeypatch.setattr(deps, "SessionLocal", _SessionProxy(db_session))
    monkeypatch.setattr(sweep, "_get_s3_client", lambda: client)
    return sweep.main()


@pytest.fixture
def released_without_artifacts(db_session, curator):
    """A release citing a calculation that retained no artifact rows.

    The residue of the ``--release`` fix: the scope query now finds the
    calculations, and those calculations have nothing stored. Before,
    the sweep printed a clean summary over zero digests and exited 0 --
    the same green gate the ``record_type`` bug produced, reached a
    different way.
    """
    species = make_species(db_session, smiles="CC")
    entry = make_species_entry(db_session, species=species)
    lot = make_lot(db_session)
    freq = make_calculation(
        db_session,
        type=CalculationType.freq,
        species_entry_id=entry.id,
        lot_id=lot.id,
    )
    thermo = make_thermo_scalar(db_session, species_entry=entry, h298_kj_mol=-1.5)
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
        note="approved for the empty-evidence fixture",
    )
    policy = resolve_curation_policy(
        db_session,
        name="empty-evidence-policy",
        version="1.0",
        description="Whatever survives review.",
        criteria={"requires_review_status": "approved"},
        created_by=curator.id,
    )
    release = create_release(
        db_session,
        tag="2026.08.1",
        title="Cites a calculation that kept nothing",
        curation_policy=policy,
        data_license="CC-BY-4.0",
        code_license="MIT",
        citation_text="TCKDB empty-evidence fixture release.",
        contact="tckdb-maintainers@example.org",
        changelog_entry="Fixture.",
        created_by=curator.id,
    )
    add_selection(
        db_session,
        release=release,
        record_type=SubmissionRecordType.thermo,
        record_id=thermo.id,
        subject_type=SubmissionRecordType.species_entry,
        subject_id=entry.id,
        rationale="Selected before anyone noticed the logs were never uploaded.",
        selected_by=curator.id,
    )
    db_session.flush()
    return release


def test_a_release_whose_calculations_kept_no_artifacts_is_not_a_pass(
    db_session, sweep, monkeypatch, capsys, released_without_artifacts
):
    """The scope resolves, finds calculations, and they have no evidence.

    ADR 0014 names this sweep as the gate standing between a citable
    release and undetected corruption. Exiting 0 here would let the
    runbook record a verification of a release whose evidence does not
    exist -- a stronger claim than the one it made while the scope query
    was broken, and just as false.
    """
    code = _run_main(
        sweep,
        monkeypatch,
        db_session,
        ["--release", released_without_artifacts.public_ref],
        _ReadableBucket({}),
    )

    assert code == sweep.EXIT_NOT_VERIFIED
    assert "NOT VERIFIED" in capsys.readouterr().out


def test_a_scope_naming_nothing_is_not_a_pass(db_session, sweep, monkeypatch, capsys):
    """A mistyped ref must not read as a clean bill of health."""
    code = _run_main(
        sweep,
        monkeypatch,
        db_session,
        ["--calculation-ref", "calc_thisdoesnotexistanywhere"],
        _ReadableBucket({}),
    )

    out = capsys.readouterr().out
    assert code == sweep.EXIT_NOT_VERIFIED
    assert "NOT VERIFIED" in out
    # The refusal has to be actionable, which means echoing what was
    # asked for rather than only saying that nothing was found.
    assert "calc_thisdoesnotexistanywhere" in out


def test_an_empty_scope_can_be_accepted_but_only_on_purpose(
    db_session, sweep, monkeypatch, capsys
):
    """``--allow-empty`` is the opt-out, on the age floors' reasoning.

    A fresh deployment legitimately has nothing to sweep. What must not
    happen is that state being indistinguishable from a wrong ref, so
    the operator says so in the invocation -- and the run still reports
    that nothing was verified.
    """
    code = _run_main(
        sweep,
        monkeypatch,
        db_session,
        ["--calculation-ref", "calc_thisdoesnotexistanywhere", "--allow-empty"],
        _ReadableBucket({}),
    )

    out = capsys.readouterr().out
    assert code == sweep.EXIT_OK
    assert "Nothing was verified" in out


def test_a_storage_outage_is_not_a_pass(
    db_session, sweep, monkeypatch, capsys, released_thermo
):
    """The store did not answer, so the objects are as unchecked as before.

    The second silence: ``_verify_one`` returned ``unavailable`` and the
    exit code counted only breaks, so a sweep run while the object store
    was down verified nothing and exited 0. Recording a custody break
    instead would be a lie in the opposite direction -- an unreachable
    store says nothing about the bytes -- so the outcome is counted as
    unchecked and the run refuses to call itself a pass.
    """
    code = _run_main(
        sweep,
        monkeypatch,
        db_session,
        ["--release", released_thermo["release"].public_ref],
        _UnreachableBucket(),
    )

    out = capsys.readouterr().out
    assert code == sweep.EXIT_NOT_VERIFIED
    assert "unchecked=2" in out
    assert "verified=0" in out


def test_a_storage_outage_records_no_custody_break(
    db_session, sweep, monkeypatch, released_thermo
):
    """Refusing to pass must not become manufacturing a finding.

    The guard against overcorrecting: an unreachable store is not
    evidence that an object is corrupt, and a row saying it is would
    hard-fail a calculation on the strength of a network failure.
    """
    from app.db.models.calculation import ArtifactIntegrityEvent

    before = db_session.scalar(
        select(func.count()).select_from(ArtifactIntegrityEvent)
    )

    _run_main(
        sweep,
        monkeypatch,
        db_session,
        ["--release", released_thermo["release"].public_ref],
        _UnreachableBucket(),
    )

    assert (
        db_session.scalar(select(func.count()).select_from(ArtifactIntegrityEvent))
        == before
    )


def _attach_real_bytes(session, calculation, content: bytes, filename: str) -> bytes:
    """Attach an artifact row whose digest and size describe real bytes.

    The verification is a hash, so a fixture that stores an arbitrary
    digest beside arbitrary bytes can only ever produce a break. These
    rows are consistent, which is what makes a *clean* sweep expressible
    at all.
    """
    attach_artifact(
        session,
        calculation=calculation,
        sha256=hashlib.sha256(content).hexdigest(),
        bytes_=len(content),
        filename=filename,
    )
    return content


def test_a_clean_sweep_reports_how_many_it_actually_verified(
    db_session, sweep, monkeypatch, capsys
):
    """The count is the whole difference between two clean-looking runs.

    ``breaks=0`` is true of a sweep that read two objects and of a sweep
    that read none. Only ``verified=N`` tells them apart, so it is
    printed unconditionally and is what a runbook should record.
    """
    calculation = _owned_calculation(db_session)
    first = _attach_real_bytes(db_session, calculation, b"an output log", "out.log")
    second = _attach_real_bytes(db_session, calculation, b"an input deck", "in.inp")
    db_session.flush()
    store = {
        hashlib.sha256(content).hexdigest(): content for content in (first, second)
    }

    code = _run_main(
        sweep,
        monkeypatch,
        db_session,
        ["--calculation-ref", calculation.public_ref],
        _ReadableBucket(store),
    )

    out = capsys.readouterr().out
    assert code == sweep.EXIT_OK
    assert "verified=2" in out
    assert "unchecked=0" in out
    assert "breaks=0" in out


# ---------------------------------------------------------------------------
# The digest that no artifact row points at
# ---------------------------------------------------------------------------


def _dedup_recorded_event(session, digest: str):
    """A break recorded with no ``calculation_artifact`` row behind it.

    Exactly the ``store_dedup_verification`` shape: ``store_artifact``
    HEADs an object already at its content-addressed key, verification
    fails, and the upload that would have created the referencing row is
    refused -- so the row never commits and the digest is known only to
    the custody record.
    """
    from app.db.models.calculation import ArtifactIntegrityEvent
    from app.db.models.common import (
        ArtifactIntegrityDetectionContext,
        ArtifactIntegrityFinding,
    )

    event = ArtifactIntegrityEvent(
        sha256=digest,
        artifact_id=None,
        finding=ArtifactIntegrityFinding.digest_mismatch,
        detected_during=ArtifactIntegrityDetectionContext.store_dedup_verification,
        observed_sha256=_digest("what-was-actually-there"),
        expected_bytes=11,
        observed_bytes=13,
        detail="dedup HEAD verification failed; the upload was refused",
    )
    session.add(event)
    session.flush()
    return event


def test_a_dedup_recorded_digest_can_be_swept_by_digest(db_session, sweep):
    """``--sha256`` must reach the one digest that has no artifact row.

    The third silence. The scope resolved through ``calculation_artifact``
    and a digest recorded on the dedup path has no row there by
    construction -- so the single case that most needs a manual re-read,
    the one an operator was just handed, produced an empty scope and an
    exit 0.
    """
    digest = _digest("dedup-only-digest")
    _dedup_recorded_event(db_session, digest)

    targets = sweep._scope_targets(
        db_session,
        sha256=digest,
        calculation_ref=None,
        release_ref=None,
        limit=None,
    )

    assert [target.sha256 for target in targets] == [digest]
    assert targets[0].artifact_id is None
    assert targets[0].expected_bytes == 11


def test_an_artifact_row_still_wins_over_the_recorded_fallback(db_session, sweep):
    """The fallback is a fallback; a real row carries better facts.

    An artifact row has the byte count the database committed to and the
    ``created_at`` that ``artifact_recorded_at`` is compared against to
    separate "modified after write" from "never stored correctly". The
    event's copies are used only when there is no row to prefer.
    """
    calculation = _owned_calculation(db_session)
    digest = _digest("recorded-and-referenced")
    attach_artifact(db_session, calculation=calculation, sha256=digest)
    _dedup_recorded_event(db_session, digest)
    db_session.flush()

    targets = sweep._scope_targets(
        db_session,
        sha256=digest,
        calculation_ref=None,
        release_ref=None,
        limit=None,
    )

    assert len(targets) == 1
    assert targets[0].artifact_id is not None


def test_a_digest_nothing_has_ever_recorded_is_still_an_empty_scope(db_session, sweep):
    """The fallback must not invent a target out of an arbitrary string.

    Otherwise every typo would produce a scope of one, an
    ``object_missing`` row, and a hard fail on nothing.
    """
    targets = sweep._scope_targets(
        db_session,
        sha256=_digest("never-heard-of-it"),
        calculation_ref=None,
        release_ref=None,
        limit=None,
    )

    assert targets == []


# ---------------------------------------------------------------------------
# Losing the reclaim's race is repaired, and the repair is recorded
# ---------------------------------------------------------------------------
#
# The reclaim cannot see an upload that deduplicated against an object
# seconds before it was held, so a committed ``calculation_artifact`` row
# can end up pointing at a digest sitting under ``reclaimed/``. The bytes
# always survive that. What did not survive it was the record's meaning:
# every read of the artifact recorded ``object_missing``, the trust layer
# hard-failed a perfectly good calculation, and the only thing that
# noticed was a purge refusal months later that changed nothing.
#
# The invariant these tests hold: **a live wrong state gets repaired, and
# the repair is a recorded change of custody, not a log line.**


class _HoldBucket(_FakeBucket):
    """A fake store that can also serve bytes, so a restore can be re-read.

    ``_FakeBucket`` models keys and dates, which is all the reclaim needs.
    The restore path ends in a real ``load_artifact_bytes`` call, because
    a restore that recorded ``verified`` without hashing anything would be
    the operator-asserts-a-repair move ADR 0014 forbids.
    """

    def __init__(self, contents: dict[str, bytes], modified=None) -> None:
        when = modified or (datetime.now(timezone.utc) - timedelta(days=365))
        super().__init__(dict.fromkeys(contents, when))
        self.contents = dict(contents)

    def copy_object(self, *, Bucket, Key, CopySource):
        super().copy_object(Bucket=Bucket, Key=Key, CopySource=CopySource)
        self.contents[Key] = self.contents[CopySource["Key"]]

    def delete_object(self, *, Bucket, Key):
        super().delete_object(Bucket=Bucket, Key=Key)
        self.contents.pop(Key, None)

    def get_object(self, *, Bucket, Key):
        if Key not in self.contents:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "gone"}}, "GetObject"
            )
        return {"Body": _Body(self.contents[Key])}

    def head_object(self, *, Bucket, Key):
        head = super().head_object(Bucket=Bucket, Key=Key)
        head["ContentLength"] = len(self.contents.get(Key, b""))
        return head


def _held_but_referenced(session, content: bytes = b"a log that was mislaid"):
    """The exact aftermath of the race: committed row, object in the hold."""
    calculation = _owned_calculation(session)
    digest = hashlib.sha256(content).hexdigest()
    attach_artifact(
        session,
        calculation=calculation,
        sha256=digest,
        bytes_=len(content),
        filename="mislaid.log",
    )
    session.flush()
    return digest, _HoldBucket({f"reclaimed/{digest}": content})


def _events_for(session, digest: str):
    from app.db.models.calculation import ArtifactIntegrityEvent

    return (
        session.scalars(
            select(ArtifactIntegrityEvent)
            .where(ArtifactIntegrityEvent.sha256 == digest)
            .order_by(ArtifactIntegrityEvent.id)
        )
        .unique()
        .all()
    )


def test_a_held_object_a_row_references_is_put_back(db_session, sweep):
    """The repair itself. Refusing left the row reading as corrupt."""
    digest, client = _held_but_referenced(db_session)

    outcome = sweep._restore_referenced_holds(
        db_session,
        client=client,
        bucket="b",
        session_factory=_SessionProxy(db_session),
    )

    assert outcome.restored == [digest]
    assert _cas_key(digest) in client.objects
    assert f"reclaimed/{digest}" not in client.objects


def test_the_restore_is_recorded_as_a_custody_event(db_session, sweep):
    """ADR 0014's discipline: custody changes are recorded, not logged.

    A repair that only prints is two of the three things the state needed
    -- repaired and visible-if-you-were-watching -- and not the third,
    auditable. The row is what makes "the reclaim's race fired against
    real data on this date" answerable months later.
    """
    from app.db.models.common import ArtifactIntegrityDetectionContext

    digest, client = _held_but_referenced(db_session)

    sweep._restore_referenced_holds(
        db_session,
        client=client,
        bucket="b",
        session_factory=_SessionProxy(db_session),
    )

    events = _events_for(db_session, digest)
    assert len(events) == 1
    assert (
        events[0].detected_during
        is ArtifactIntegrityDetectionContext.reclaim_restore
    )
    assert "reclaim hold" in events[0].detail


def test_the_recorded_restore_is_a_real_re_read_not_an_assertion(db_session, sweep):
    """``verified`` is earned by bytes that hash, never by a claim.

    The check constraint requires ``observed_sha256 == sha256``, and the
    only honest way to satisfy it is to read the object back after
    putting it there. A restore that recorded a clean verdict without
    reading would be exactly the operator-asserts-a-repair move this
    table's constraint exists to forbid.
    """
    from app.db.models.common import ArtifactIntegrityFinding

    content = b"bytes that must be hashed to be believed"
    digest, client = _held_but_referenced(db_session, content)

    sweep._restore_referenced_holds(
        db_session,
        client=client,
        bucket="b",
        session_factory=_SessionProxy(db_session),
    )

    event = _events_for(db_session, digest)[0]
    assert event.finding is ArtifactIntegrityFinding.verified
    assert event.observed_sha256 == digest
    assert event.observed_bytes == len(content)


def test_a_restore_that_puts_back_wrong_bytes_records_the_break(db_session, sweep):
    """The repair must be able to fail, and say so.

    If the held copy does not hash to the key it was held under, putting
    it back has not repaired anything -- and a ``verified`` row would
    clear a hard fail on evidence that is genuinely bad.
    """
    from app.db.models.common import ArtifactIntegrityFinding

    digest, client = _held_but_referenced(db_session)
    client.contents[f"reclaimed/{digest}"] = b"not the bytes that were held"

    outcome = sweep._restore_referenced_holds(
        db_session,
        client=client,
        bucket="b",
        session_factory=_SessionProxy(db_session),
    )

    assert outcome.restored == []
    assert outcome.broken == [digest]
    event = _events_for(db_session, digest)[0]
    assert event.finding is ArtifactIntegrityFinding.digest_mismatch


def test_an_unreferenced_held_object_is_left_in_the_hold(db_session, sweep):
    """The hold is where reclaimed garbage is supposed to sit.

    Restoring everything would undo the reclaim on every run and make the
    two-step design a no-op with extra copies.
    """
    content = b"genuinely unreferenced"
    digest = hashlib.sha256(content).hexdigest()
    client = _HoldBucket({f"reclaimed/{digest}": content})

    outcome = sweep._restore_referenced_holds(
        db_session,
        client=client,
        bucket="b",
        session_factory=_SessionProxy(db_session),
    )

    assert outcome.restored == []
    assert f"reclaimed/{digest}" in client.objects
    assert _events_for(db_session, digest) == []


def test_a_restore_never_overwrites_an_occupied_key(db_session, sweep):
    """Somebody re-uploaded the same bytes; that copy is what rows read.

    There is no wrong state here, so there is nothing to repair and
    nothing to record -- and overwriting would at best be a no-op and at
    worst erase a genuine corruption the custody record exists to keep.
    """
    content = b"uploaded again while the first copy was held"
    digest, client = _held_but_referenced(db_session, content)
    client.objects[_cas_key(digest)] = datetime.now(timezone.utc)
    client.contents[_cas_key(digest)] = content

    outcome = sweep._restore_referenced_holds(
        db_session,
        client=client,
        bucket="b",
        session_factory=_SessionProxy(db_session),
    )

    assert outcome.restored == []
    assert outcome.blocked == [digest]
    assert client.copied == []
    assert client.deleted == []
    assert _events_for(db_session, digest) == []


def test_the_purge_guard_is_still_the_purge_guard(db_session, sweep):
    """The repair runs first; the irreversible operation keeps its own check.

    A guard that trusts an earlier pass to have run is not a guard, and
    this one sits in front of the only thing here that destroys bytes.
    """
    digest, client = _held_but_referenced(db_session)

    purged = sweep._purge_hold(db_session, client=client, bucket="b", min_age_days=7)

    assert purged == []
    assert f"reclaimed/{digest}" in client.objects


def test_a_run_that_restores_says_so_and_does_not_exit_clean(
    db_session, sweep, monkeypatch, capsys
):
    """Repaired, visible and auditable -- the exit code is the visible part.

    Exiting 0 would make a repair something only a log reader could ever
    learn about, which is the shape of failure this script has been
    corrected for twice already.
    """
    digest, client = _held_but_referenced(db_session)

    code = _run_main(
        sweep, monkeypatch, db_session, ["--all", "--purge-hold-days", "90"], client
    )

    out = capsys.readouterr().out
    assert code == sweep.EXIT_REPAIRED
    assert "REPAIRED" in out
    assert digest in out


def test_a_reclaim_run_repairs_without_waiting_for_the_next_purge(
    db_session, sweep, monkeypatch, capsys
):
    """The run that creates this state is the cheapest place to catch it.

    Leaving it to ``--purge-hold-days`` would mean the hard fail stands
    for however long the operator's purge interval is -- a quarter, in
    the documented invocation.
    """
    digest, client = _held_but_referenced(db_session)

    code = _run_main(
        sweep, monkeypatch, db_session, ["--all", "--reclaim-orphans"], client
    )

    assert code == sweep.EXIT_REPAIRED
    assert _cas_key(digest) in client.objects
    assert len(_events_for(db_session, digest)) == 1


def test_a_dry_run_repairs_nothing(db_session, sweep, monkeypatch, capsys):
    """``--dry-run`` is a plan. A plan that mutates the store is not one."""
    digest, client = _held_but_referenced(db_session)

    _run_main(
        sweep,
        monkeypatch,
        db_session,
        ["--all", "--reclaim-orphans", "--dry-run"],
        client,
    )

    assert f"reclaimed/{digest}" in client.objects
    assert _events_for(db_session, digest) == []


def test_the_reclaim_itself_records_nothing(db_session, sweep):
    """The asymmetry, held by a test because it is a decision and not an
    oversight.

    A reclaimed object is referenced by nothing, so a row about it would
    name no evidence -- and the only honest finding for "not at its key"
    is ``object_missing``, a break, whose latest-observation rule would
    then hard-fail the next legitimate upload of those bytes on the
    strength of a maintenance action. That is this same defect in a new
    place, so the reclaim stays a report.
    """
    content = b"an orphan on its way to the hold"
    digest = hashlib.sha256(content).hexdigest()
    client = _HoldBucket({_cas_key(digest): content})

    held = sweep._reclaim_orphans(
        lambda: _NullSession(), [digest], client=client, bucket="b"
    )

    assert held == [digest]
    assert _events_for(db_session, digest) == []
