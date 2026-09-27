"""Filling a NULL ``software_release.version`` from artifact banners (issue #305, item 3).

Real output logs from ``tests/fixtures`` stand in for the stored artifacts;
the store itself is replaced by a dict keyed by SHA-256, so this exercises
the evidence logic, not S3.

The first test is the reason the design re-points calculations instead of
filling the version-less row in place: it reproduces what an in-place
UPDATE does to the next version-less deposit.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import pathlib
import sys
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation
from app.db.models.common import (
    AppUserRole,
    ArtifactKind,
    RecordReviewStatus,
    SoftwareReconciliationStatus,
    StatmechCalculationRole,
    SubmissionRecordType,
    ThermoCalculationRole,
)
from app.db.models.execution_environment import ExecutionEnvironmentManifest
from app.db.models.record_review import RecordReview
from app.db.models.software import SoftwareRelease
from app.db.models.statmech import StatmechSourceCalculation
from app.db.models.thermo import ThermoSourceCalculation
from app.services import software_release_version_fill as fill
from app.services.reproducibility_rubric import evaluate_and_append_reproducibility
from app.services.software_resolution import resolve_software_release
from tests.services.scientific_read._factories import (
    attach_artifact,
    make_calculation,
    make_software_release,
    make_species,
    make_species_entry,
    make_statmech,
    make_thermo_scalar,
    next_inchi_key,
    unique_smiles,
)

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "fixtures"
_SCRIPT = (
    pathlib.Path(__file__).parents[2]
    / "scripts"
    / "ops"
    / "fill_software_release_version.py"
)


def test_an_in_place_fill_breaks_the_next_version_less_deposit(db_session):
    """Why the tool never UPDATEs the release row.

    ``public_ref`` is content-derived from the identity tuple and assigned
    once, at INSERT. Fill the version in place and the row keeps the ref of
    "ORCA, no version"; the next depositor who sends "ORCA, no version"
    computes that same ref for their new row, the insert hits the unique
    index, and the dedupe resolver comes back empty-handed.
    """
    orca_null = resolve_software_release(db_session, name="ORCA", version=None)
    stale_ref = orca_null.public_ref
    orca_null.version = "6"  # the tempting in-place fill
    db_session.flush()

    assert orca_null.public_ref == stale_ref  # the ref did not follow the content
    assert resolve_software_release(db_session, name="ORCA", version=None) is None


def _entry(db_session):
    return make_species_entry(
        db_session,
        make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("VFILL")),
    )


class _Store:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, content: bytes) -> str:
        sha = hashlib.sha256(content).hexdigest()
        self.objects[sha] = content
        return sha

    def __call__(self, sha256: str, **_kwargs) -> bytes:
        if sha256 not in self.objects:
            raise FileNotFoundError(sha256)
        return self.objects[sha256]


def _calc_with(db_session, store, release, *, artifacts=(), accepted=False):
    calc = make_calculation(
        db_session, species_entry_id=_entry(db_session).id, software_release_id=release.id
    )
    for kind, content in artifacts:
        sha = store.put(content) if content is not None else "f" * 64
        attach_artifact(db_session, calculation=calc, kind=kind, sha256=sha, filename=f"{kind.value}.txt")
    if accepted:
        when = datetime(2026, 9, 1)
        curator = AppUser(username=f"vfill-curator-{calc.id}", role=AppUserRole.curator)
        db_session.add(curator)
        db_session.flush()
        db_session.add(
            RecordReview(
                record_type=SubmissionRecordType.calculation,
                record_id=calc.id,
                status=RecordReviewStatus.approved,
                reviewed_by=curator.id,
                reviewed_at=when,
                first_approved_at=when,
            )
        )
    db_session.flush()
    return calc


@pytest.fixture
def seeded(db_session):
    store = _Store()
    orca_log = (FIXTURES / "orca/opt_orca.out").read_bytes()
    gaussian_log = (FIXTURES / "gaussian/sp_ub3lyp_g16.log").read_bytes()
    orca_input = b"! B3LYP def2-SVP Opt\n* xyz 0 1\nH 0 0 0\nH 0 0 0.74\n*\n"

    orca_null = make_software_release(db_session, name="ORCA", version=None)
    calcs = {
        "fillable": _calc_with(
            db_session,
            store,
            orca_null,
            artifacts=[(ArtifactKind.input, orca_input), (ArtifactKind.output_log, orca_log)],
        ),
        "no_banner": _calc_with(
            db_session, store, orca_null, artifacts=[(ArtifactKind.input, orca_input)]
        ),
        "no_artifact": _calc_with(db_session, store, orca_null),
        "other_program": _calc_with(
            db_session, store, orca_null, artifacts=[(ArtifactKind.output_log, gaussian_log)]
        ),
        "accepted": _calc_with(
            db_session,
            store,
            orca_null,
            artifacts=[(ArtifactKind.output_log, orca_log)],
            accepted=True,
        ),
        "unreadable": _calc_with(
            db_session, store, orca_null, artifacts=[(ArtifactKind.output_log, None)]
        ),
    }
    return store, orca_null, calcs


def test_plan_reports_every_calculation_exactly(db_session, seeded):
    store, orca_null, calcs = seeded

    plan = fill.plan_release_fill(db_session, orca_null, load_bytes=store)

    got = {(o.calculation_ref, o.status, o.target) for o in plan.outcomes}
    orca_610 = ("6.1.0", None, None)
    assert got == {
        (calcs["fillable"].public_ref, "fillable", orca_610),
        (calcs["accepted"].public_ref, "accepted", orca_610),
        (calcs["no_banner"].public_ref, "no_banner", None),
        (calcs["no_artifact"].public_ref, "no_artifact", None),
        (calcs["other_program"].public_ref, "other_program", None),
        (calcs["unreadable"].public_ref, "unreadable", None),
    }
    assert plan.release_ref == orca_null.public_ref
    # A plan writes nothing.
    assert all(c.software_release_id == orca_null.id for c in calcs.values())
    assert all(c.observed_software_banner is None for c in calcs.values())


def test_apply_repoints_only_the_fillable_calculation(db_session, seeded):
    store, orca_null, calcs = seeded
    null_ref = orca_null.public_ref

    _plan, moved = fill.apply_release_fill(db_session, orca_null, load_bytes=store)

    target = db_session.scalar(
        select(SoftwareRelease).where(SoftwareRelease.public_ref == moved[calcs["fillable"].public_ref])
    )
    assert list(moved) == [calcs["fillable"].public_ref]
    assert (target.software.name, target.version, target.revision, target.build) == (
        "ORCA",
        "6.1.0",
        None,
        None,
    )
    filled = db_session.get(Calculation, calcs["fillable"].id)
    assert filled.software_release_id == target.id
    assert filled.observed_software_banner == "orca 6.1.0"
    assert filled.software_reconciliation_status is SoftwareReconciliationStatus.enriched

    # The version-less release is untouched: same ref, still NULL.
    db_session.refresh(orca_null)
    assert (orca_null.public_ref, orca_null.version) == (null_ref, None)
    for key in ("accepted", "no_banner", "no_artifact", "other_program", "unreadable"):
        calc = db_session.get(Calculation, calcs[key].id)
        assert calc.software_release_id == orca_null.id, key
        assert calc.observed_software_banner is None, key


def _bind_to_manifest(db_session, calc, release) -> None:
    manifest = ExecutionEnvironmentManifest(
        schema_version="1",
        content_digest="sha256:" + hashlib.sha256(calc.public_ref.encode()).hexdigest(),
        runtime_kind="container",
        runtime_locator="docker://example/orca@sha256:" + "0" * 64,
        executable_locator="/opt/orca/orca",
        software_release_id=release.id,
        workflow_tool_release_id=None,
        closure_json=[],
        canonical_json={},
    )
    db_session.add(manifest)
    db_session.flush()
    calc.execution_environment_manifest_id = manifest.id
    db_session.flush()


def test_environment_bound_calculation_is_reported_and_skipped(db_session):
    """Review finding F1. ``trg_calculation_execution_environment_binding``
    requires a calculation's release to equal its execution-environment
    manifest's, and the manifest is immutable. Re-pointing such a
    calculation raised P0001 and aborted the whole ``--commit``; it must be
    reported as ``environment_bound`` and left alone instead."""
    store = _Store()
    orca_null = make_software_release(db_session, name="ORCA", version=None)
    orca_log = (FIXTURES / "orca/opt_orca.out").read_bytes()
    bound = _calc_with(db_session, store, orca_null, artifacts=[(ArtifactKind.output_log, orca_log)])
    _bind_to_manifest(db_session, bound, orca_null)
    free = _calc_with(db_session, store, orca_null, artifacts=[(ArtifactKind.output_log, orca_log)])

    plan = fill.plan_release_fill(db_session, orca_null, load_bytes=store)
    assert {(o.calculation_ref, o.status) for o in plan.outcomes} == {
        (bound.public_ref, "environment_bound"),
        (free.public_ref, "fillable"),
    }

    _plan, moved = fill.apply_release_fill(db_session, orca_null, load_bytes=store)

    assert list(moved) == [free.public_ref]
    assert db_session.get(Calculation, bound.id).software_release_id == orca_null.id


def _approve(db_session, record_type, record_id) -> None:
    when = datetime(2026, 9, 1)
    curator = AppUser(username=f"vfill-approver-{record_type.value}-{record_id}", role=AppUserRole.curator)
    db_session.add(curator)
    db_session.flush()
    db_session.add(
        RecordReview(
            record_type=record_type,
            record_id=record_id,
            status=RecordReviewStatus.approved,
            reviewed_by=curator.id,
            reviewed_at=when,
            first_approved_at=when,
        )
    )
    db_session.flush()


def test_plan_reports_stale_assessments_and_approved_products(db_session, script, monkeypatch, capsys):
    """Review finding F4, reporting only. Re-pointing a calculation changes
    the release columns every stored reproducibility assessment covering it
    hashed, and an approved thermo/statmech citing it as a source is not
    guarded by the accepted-science trigger. The dry run must say both."""
    store = _Store()
    orca_null = make_software_release(db_session, name="ORCA", version=None)
    log = (FIXTURES / "orca/opt_orca.out").read_bytes()
    calc = _calc_with(db_session, store, orca_null, artifacts=[(ArtifactKind.output_log, log)])
    entry = db_session.get(Calculation, calc.id).species_entry

    thermo = make_thermo_scalar(db_session, species_entry=entry)
    db_session.add(ThermoSourceCalculation(thermo_id=thermo.id, calculation_id=calc.id, role=ThermoCalculationRole.sp))
    _approve(db_session, SubmissionRecordType.thermo, thermo.id)
    statmech = make_statmech(db_session, species_entry=entry)  # cites it, not approved
    db_session.add(
        StatmechSourceCalculation(statmech_id=statmech.id, calculation_id=calc.id, role=StatmechCalculationRole.opt)
    )
    db_session.flush()
    own = evaluate_and_append_reproducibility(
        db_session, record_type=SubmissionRecordType.calculation, record_id=calc.id, artifact_loader=store
    )
    via_statmech = evaluate_and_append_reproducibility(
        db_session, record_type=SubmissionRecordType.statmech, record_id=statmech.id, artifact_loader=store
    )

    plan = fill.plan_release_fill(db_session, orca_null, load_bytes=store)

    [outcome] = plan.outcomes
    assert outcome.status == "fillable"
    assert outcome.stale_assessment_refs == tuple(sorted([own.public_ref, via_statmech.public_ref]))
    assert outcome.approved_product_refs == (thermo.public_ref,)
    assert outcome.fills_build is False

    assert _run(script, monkeypatch, db_session, store, ["--software", "ORCA"]) == 0
    out = capsys.readouterr().out
    assert "2 reproducibility assessment(s) go stale" in out
    assert f"source for 1 APPROVED thermo/statmech record(s): {thermo.public_ref}" in out
    assert (
        "--commit would re-point 1 calculation(s); 2 reproducibility assessment(s) "
        "go stale; 1 approved thermo/statmech record(s) cite them."
    ) in out


def test_gaussian_banner_fills_build_and_says_so(db_session, script, monkeypatch, capsys):
    """Review finding F3, behaviour unchanged (DR-0008 ``enriched``): a
    Gaussian banner also fills ``build``, so the target is a sibling of an
    existing build-less ``16``/``C.02`` release. The dry run says so."""
    store = _Store()
    g_null = make_software_release(db_session, name="Gaussian", version=None)
    clean = make_software_release(db_session, name="Gaussian", version="16", revision="C.02")
    calc = _calc_with(
        db_session,
        store,
        g_null,
        artifacts=[(ArtifactKind.output_log, (FIXTURES / "gaussian/sp_ub3lyp_g16.log").read_bytes())],
    )

    [outcome] = fill.plan_release_fill(db_session, g_null, load_bytes=store).outcomes
    assert (outcome.calculation_ref, outcome.status, outcome.target, outcome.fills_build) == (
        calc.public_ref,
        "fillable",
        ("16", "C.02", "ES64L-G16RevC.02"),
        True,
    )
    assert _run(script, monkeypatch, db_session, store, ["--software", "Gaussian"]) == 0
    assert "also fills build from the banner (DR-0008 enriched)" in capsys.readouterr().out
    assert clean.build is None


def test_accepted_is_the_database_s_answer_not_a_courtesy(db_session, seeded):
    """The ``accepted`` skip mirrors the trigger: re-pointing an approved
    calculation is refused by ``trg_as_root_calculation`` (SQLSTATE 55000)."""
    _store, _orca_null, calcs = seeded
    other = make_software_release(db_session, name="ORCA", version="6.1.0")
    accepted = db_session.get(Calculation, calcs["accepted"].id)

    with pytest.raises(DBAPIError) as excinfo:
        with db_session.begin_nested():
            accepted.software_release_id = other.id
            db_session.flush()
    assert getattr(excinfo.value.orig, "sqlstate", None) == "55000"


def test_molpro_banner_is_matched_case_insensitively(db_session):
    """``Molpro`` in the registry, ``molpro`` from the parser: the same program."""
    store = _Store()
    molpro_null = make_software_release(db_session, name="Molpro", version=None)
    calc = _calc_with(
        db_session,
        store,
        molpro_null,
        artifacts=[(ArtifactKind.output_log, (FIXTURES / "molpro/ch4_closed_shell/input.out").read_bytes())],
    )

    plan = fill.plan_release_fill(db_session, molpro_null, load_bytes=store)

    assert [(o.calculation_ref, o.status, o.target) for o in plan.outcomes] == [
        (calc.public_ref, "fillable", ("2026.1", None, None))
    ]


def test_a_recorded_version_is_never_the_target(db_session):
    release = make_software_release(db_session, name="ORCA", version="5.0.4")

    with pytest.raises(fill.VersionFillRefused):
        fill.plan_release_fill(db_session, release, load_bytes=_Store())
    assert fill.version_less_releases(db_session, "orca") == []


class _SessionProxy:
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


@pytest.fixture(scope="module")
def script():
    spec = importlib.util.spec_from_file_location("fill_software_release_version", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(script, monkeypatch, db_session, store, argv, *, db_name="tckdb_test_vfill"):
    import app.api.deps as deps
    from app.api.config import settings

    monkeypatch.setattr(deps, "SessionLocal", _SessionProxy(db_session))
    monkeypatch.setattr(settings, "db_name", db_name)
    monkeypatch.setattr(fill, "load_artifact_bytes", store)
    return script.main(argv)


def test_script_dry_run_writes_nothing(script, monkeypatch, db_session, seeded, capsys):
    store, orca_null, calcs = seeded

    assert _run(script, monkeypatch, db_session, store, ["--software", "ORCA"]) == 0

    out = capsys.readouterr().out
    assert (
        "6 calculation(s) -- accepted=1, fillable=1, no_artifact=1, no_banner=1, "
        "other_program=1, unreadable=1"
    ) in out
    assert "Dry run -- nothing was written." in out
    assert db_session.get(Calculation, calcs["fillable"].id).software_release_id == orca_null.id


def test_script_commit_on_a_deployed_name_needs_the_flag(
    script, monkeypatch, db_session, seeded, capsys
):
    store, orca_null, calcs = seeded

    rc = _run(
        script, monkeypatch, db_session, store, ["--software", "ORCA", "--commit"], db_name="tckdb"
    )

    assert rc == 2
    assert "Refusing --commit" in capsys.readouterr().err
    assert db_session.get(Calculation, calcs["fillable"].id).software_release_id == orca_null.id


def test_script_commit_repoints(script, monkeypatch, db_session, seeded, capsys):
    store, orca_null, calcs = seeded

    assert _run(script, monkeypatch, db_session, store, ["--software", "ORCA", "--commit"]) == 0

    out = capsys.readouterr().out
    assert "re-pointed 1 calculation(s):" in out
    moved = db_session.get(Calculation, calcs["fillable"].id)
    assert moved.software_release.version == "6.1.0"
