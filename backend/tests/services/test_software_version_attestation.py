"""Owner attestation of a software version (issue #305, decision (a)).

Seeded like the playground instance: ORCA and Molpro releases with a NULL
version, cited by calculations that have **no stored artifacts**, so no
banner can fill them. The owner attested "ORCA 6" and "Molpro 26" about
their own runs (2026-09-12). The attestation mode records that statement and
re-points exactly the calculations it covers, one link row per calculation.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.db.models.app_user import AppUser
from app.db.models.calculation import Calculation
from app.db.models.common import (
    AppUserRole,
    ArtifactKind,
    SoftwareVersionEvidenceKind,
    StatmechCalculationRole,
    SubmissionRecordType,
    ThermoCalculationRole,
)
from app.db.models.software import SoftwareRelease
from app.db.models.software_version_attestation import (
    SoftwareVersionAttestation,
    SoftwareVersionAttestationCalculation,
)
from app.db.models.statmech import StatmechSourceCalculation
from app.db.models.thermo import ThermoSourceCalculation
from app.services import software_release_version_fill as fill
from app.services.reproducibility_rubric import evaluate_and_append_reproducibility
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
from tests.services.test_software_release_version_fill import (
    FIXTURES,
    _approve,
    _bind_to_manifest,
    _run,
    _Store,
    script,  # noqa: F401  (module-scoped fixture)
)

ATTESTED_ON = datetime(2026, 9, 12)
ORCA_STATEMENT = "ORCA 6 was used for all ORCA runs deposited by owner, attested 2026-09-12"
MOLPRO_STATEMENT = "Molpro 26 was used for all Molpro runs deposited by owner, attested 2026-09-12"


def _user(db_session, username: str) -> AppUser:
    user = AppUser(username=username, role=AppUserRole.user)
    db_session.add(user)
    db_session.flush()
    return user


def _calc(db_session, release, *, created_by, store=None, log=None) -> Calculation:
    entry = make_species_entry(
        db_session,
        make_species(db_session, smiles=unique_smiles(), inchi_key=next_inchi_key("ATTEST")),
    )
    calc = make_calculation(db_session, species_entry_id=entry.id, software_release_id=release.id)
    calc.created_by = created_by.id
    if log is not None:
        attach_artifact(
            db_session, calculation=calc, kind=ArtifactKind.output_log,
            sha256=store.put(log), filename="job.out",
        )
    db_session.flush()
    return calc


@pytest.fixture
def playground(db_session):
    """ORCA and Molpro NULL-version releases; the owner's calculations carry no artifacts."""
    owner = _user(db_session, "attest-owner")
    other = _user(db_session, "attest-someone-else")
    orca_null = make_software_release(db_session, name="ORCA", version=None)
    molpro_null = make_software_release(db_session, name="Molpro", version=None)
    calcs = {
        "orca_1": _calc(db_session, orca_null, created_by=owner),
        "orca_2": _calc(db_session, orca_null, created_by=owner),
        "molpro_1": _calc(db_session, molpro_null, created_by=owner),
        "orca_other": _calc(db_session, orca_null, created_by=other),
    }
    return owner, other, orca_null, molpro_null, calcs


def _attestation(owner, version="6", statement=ORCA_STATEMENT, *, depositor=None) -> fill.Attestation:
    return fill.Attestation(
        attested_version=version,
        statement=statement,
        attested_by=owner,
        attested_at=ATTESTED_ON,
        covers_depositor=depositor or owner,
    )


def _release_tuple(db_session, calc) -> tuple:
    db_session.refresh(calc)
    release = db_session.get(SoftwareRelease, calc.software_release_id)
    return (release.software.name, release.version, release.revision, release.build)


def _counts(db_session) -> tuple[int, int]:
    return (
        db_session.scalar(select(func.count(SoftwareVersionAttestation.id))),
        db_session.scalar(select(func.count(SoftwareVersionAttestationCalculation.id))),
    )


def test_plan_classifies_every_calculation_and_writes_nothing(db_session, playground):
    owner, _other, orca_null, _molpro_null, calcs = playground
    store = _Store()
    accepted = _calc(db_session, orca_null, created_by=owner)
    _approve(db_session, SubmissionRecordType.calculation, accepted.id)
    bound = _calc(db_session, orca_null, created_by=owner)
    _bind_to_manifest(db_session, bound, orca_null)
    bannered = _calc(
        db_session, orca_null, created_by=owner, store=store,
        log=(FIXTURES / "orca/opt_orca.out").read_bytes(),
    )
    unreadable = _calc(db_session, orca_null, created_by=owner)
    attach_artifact(
        db_session, calculation=unreadable, kind=ArtifactKind.output_log,
        sha256="f" * 64, filename="gone.out",
    )

    plan = fill.plan_attestation(db_session, orca_null, _attestation(owner), load_bytes=store)

    assert {(o.calculation_ref, o.status) for o in plan.outcomes} == {
        (calcs["orca_1"].public_ref, "fillable"),
        (calcs["orca_2"].public_ref, "fillable"),
        (calcs["orca_other"].public_ref, "other_depositor"),
        (accepted.public_ref, "accepted"),
        (bound.public_ref, "environment_bound"),
        (bannered.public_ref, "banner_available"),
        (unreadable.public_ref, "unreadable"),
    }
    assert plan.target == ("6", None, None)
    assert _counts(db_session) == (0, 0)
    assert _release_tuple(db_session, calcs["orca_1"]) == ("ORCA", None, None, None)


def test_apply_records_the_statement_and_one_link_per_calculation(db_session, playground):
    owner, _other, orca_null, _molpro_null, calcs = playground
    null_ref = orca_null.public_ref

    _plan, moved = fill.apply_attestation(db_session, orca_null, _attestation(owner), load_bytes=_Store())

    assert set(moved) == {calcs["orca_1"].public_ref, calcs["orca_2"].public_ref}
    for key in ("orca_1", "orca_2"):
        # Exactly the attested string; revision and build are not invented.
        assert _release_tuple(db_session, calcs[key]) == ("ORCA", "6", None, None)
    assert _release_tuple(db_session, calcs["orca_other"]) == ("ORCA", None, None, None)
    assert _release_tuple(db_session, calcs["molpro_1"]) == ("Molpro", None, None, None)

    [record] = db_session.scalars(select(SoftwareVersionAttestation)).all()
    assert (
        record.software_release_id,
        record.attested_version,
        record.statement,
        record.evidence_kind,
        record.attested_by,
        record.attested_at,
    ) == (orca_null.id, "6", ORCA_STATEMENT, SoftwareVersionEvidenceKind.owner_attestation, owner.id, ATTESTED_ON)
    target_id = db_session.get(Calculation, calcs["orca_1"].id).software_release_id
    links = db_session.scalars(select(SoftwareVersionAttestationCalculation)).all()
    assert sorted(
        (link.calculation_id, link.before_software_release_id, link.after_software_release_id, link.attestation_id)
        for link in links
    ) == sorted(
        (calcs[key].id, orca_null.id, target_id, record.id) for key in ("orca_1", "orca_2")
    )
    # The version-less release is never filled in place.
    db_session.refresh(orca_null)
    assert (orca_null.version, orca_null.public_ref) == (None, null_ref)


def test_a_second_run_is_a_no_op(db_session, playground):
    owner, _other, orca_null, _molpro_null, _calcs = playground
    fill.apply_attestation(db_session, orca_null, _attestation(owner), load_bytes=_Store())
    before = _counts(db_session)

    _plan, moved = fill.apply_attestation(db_session, orca_null, _attestation(owner), load_bytes=_Store())

    assert moved == {}
    assert _counts(db_session) == before == (1, 2)


def test_an_attestation_that_moves_nothing_is_not_recorded(db_session, playground):
    """Someone who deposited none of these calculations attests: every one is
    ``other_depositor``, and no attestation row is written for a statement
    that re-pointed nothing."""
    _owner, _other, orca_null, *_ = playground
    nobody = _user(db_session, "attest-nobody")

    plan, moved = fill.apply_attestation(
        db_session, orca_null, _attestation(nobody), load_bytes=_Store()
    )

    assert moved == {}
    assert {o.status for o in plan.outcomes} == {"other_depositor"}
    assert _counts(db_session) == (0, 0)


def test_an_identical_statement_is_reused_for_calculations_that_arrive_later(db_session, playground):
    owner, _other, orca_null, _molpro_null, _calcs = playground
    fill.apply_attestation(db_session, orca_null, _attestation(owner), load_bytes=_Store())
    late = _calc(db_session, orca_null, created_by=owner)

    _plan, moved = fill.apply_attestation(db_session, orca_null, _attestation(owner), load_bytes=_Store())

    assert list(moved) == [late.public_ref]
    assert _counts(db_session) == (1, 3)


def test_a_recorded_version_is_never_overwritten(db_session, playground):
    owner, *_ = playground
    versioned = make_software_release(db_session, name="ORCA", version="5.0.4")

    with pytest.raises(fill.VersionFillRefused):
        fill.plan_attestation(db_session, versioned, _attestation(owner), load_bytes=_Store())


@pytest.mark.parametrize("version", ["", "  ", " 6", "6 "])
def test_a_blank_or_padded_version_is_refused(db_session, playground, version):
    owner, _other, orca_null, *_ = playground
    with pytest.raises(fill.VersionFillRefused):
        fill.plan_attestation(db_session, orca_null, _attestation(owner, version=version))


def test_consequences_are_listed_before_anything_moves(db_session, playground):
    owner, _other, orca_null, _molpro_null, calcs = playground
    calc = calcs["orca_1"]
    entry = calc.species_entry
    thermo = make_thermo_scalar(db_session, species_entry=entry)
    db_session.add(ThermoSourceCalculation(thermo_id=thermo.id, calculation_id=calc.id, role=ThermoCalculationRole.sp))
    _approve(db_session, SubmissionRecordType.thermo, thermo.id)
    statmech = make_statmech(db_session, species_entry=entry)
    db_session.add(
        StatmechSourceCalculation(statmech_id=statmech.id, calculation_id=calc.id, role=StatmechCalculationRole.opt)
    )
    db_session.flush()
    own = evaluate_and_append_reproducibility(
        db_session, record_type=SubmissionRecordType.calculation, record_id=calc.id, artifact_loader=_Store()
    )

    plan = fill.plan_attestation(db_session, orca_null, _attestation(owner), load_bytes=_Store())

    [outcome] = [o for o in plan.outcomes if o.calculation_ref == calc.public_ref]
    assert own.public_ref in outcome.stale_assessment_refs
    assert outcome.approved_product_refs == (thermo.public_ref,)


class TestAppendOnly:
    def _record(self, db_session, playground):
        owner, _other, orca_null, *_ = playground
        fill.apply_attestation(db_session, orca_null, _attestation(owner), load_bytes=_Store())
        return db_session.scalars(select(SoftwareVersionAttestation)).one()

    @pytest.mark.parametrize(
        "statement",
        [
            "UPDATE software_version_attestation SET statement = 'edited'",
            "DELETE FROM software_version_attestation",
            "UPDATE software_version_attestation_calculation SET after_software_release_id = before_software_release_id",
            "DELETE FROM software_version_attestation_calculation",
            "TRUNCATE software_version_attestation_calculation",
        ],
    )
    def test_rows_cannot_be_changed(self, db_session, playground, statement):
        self._record(db_session, playground)
        with pytest.raises(DBAPIError) as excinfo:
            with db_session.begin_nested():
                db_session.execute(text(statement))
        assert excinfo.value.orig.sqlstate == "55000"

    def _link(self, db_session, record, calculation_id, before, after):
        with pytest.raises(DBAPIError) as excinfo:
            with db_session.begin_nested():
                db_session.add(
                    SoftwareVersionAttestationCalculation(
                        attestation_id=record.id,
                        calculation_id=calculation_id,
                        before_software_release_id=before,
                        after_software_release_id=after,
                    )
                )
                db_session.flush()
        return excinfo.value.orig

    @pytest.mark.parametrize(
        "case",
        [
            "before_is_not_the_attested_release",
            "after_is_another_program",
            "after_is_not_the_attested_version",
            "after_invents_a_revision",
            "after_invents_a_build",
            "never_re_pointed",
            "not_the_covered_depositors_calculation",
        ],
    )
    def test_a_forged_link_row_is_refused(self, db_session, playground, case):
        """Finding 7. Each forged row breaks exactly one invariant, and every
        invariant is a fact that never changes afterwards (finding 5)."""
        owner, other, orca_null, molpro_null, calcs = playground
        record = self._record(db_session, playground)
        orca_6 = db_session.get(Calculation, calcs["orca_1"].id).software_release_id
        # A fresh owner calculation already on ORCA 6, so only the named
        # invariant can be what refuses it.
        moved = _calc(db_session, orca_null, created_by=owner)
        moved.software_release_id = orca_6
        db_session.flush()
        before, after, calculation_id = orca_null.id, orca_6, moved.id
        if case == "before_is_not_the_attested_release":
            before = molpro_null.id
        elif case == "after_is_another_program":
            after = make_software_release(db_session, name="Molpro", version="6").id
        elif case == "after_is_not_the_attested_version":
            after = make_software_release(db_session, name="ORCA", version="7").id
        elif case == "after_invents_a_revision":
            after = make_software_release(db_session, name="ORCA", version="6", revision="r9").id
        elif case == "after_invents_a_build":
            after = make_software_release(db_session, name="ORCA", version="6", build="b1").id
        elif case == "never_re_pointed":
            calculation_id = _calc(db_session, orca_null, created_by=owner).id
        elif case == "not_the_covered_depositors_calculation":
            foreign = _calc(db_session, orca_null, created_by=other)
            foreign.software_release_id = orca_6
            db_session.flush()
            calculation_id = foreign.id

        error = self._link(db_session, record, calculation_id, before, after)

        assert error.sqlstate == "23514", case

    def test_the_valid_control_row_is_accepted(self, db_session, playground):
        """The control for the forged cases: the same setup, unforged, inserts."""
        owner, _other, orca_null, _molpro_null, calcs = playground
        record = self._record(db_session, playground)
        orca_6 = db_session.get(Calculation, calcs["orca_1"].id).software_release_id
        moved = _calc(db_session, orca_null, created_by=owner)
        moved.software_release_id = orca_6
        db_session.flush()
        db_session.add(
            SoftwareVersionAttestationCalculation(
                attestation_id=record.id,
                calculation_id=moved.id,
                before_software_release_id=orca_null.id,
                after_software_release_id=orca_6,
            )
        )
        db_session.flush()

    def test_an_attestation_about_a_versioned_release_is_refused(self, db_session, playground):
        owner, *_ = playground
        versioned = make_software_release(db_session, name="ORCA", version="5.0.4")
        with pytest.raises(DBAPIError) as excinfo:
            with db_session.begin_nested():
                db_session.add(
                    SoftwareVersionAttestation(
                        software_release_id=versioned.id,
                        attested_version="6",
                        statement="forged",
                        evidence_kind=SoftwareVersionEvidenceKind.owner_attestation,
                        attested_by=owner.id,
                        covers_depositor=owner.id,
                        attested_at=ATTESTED_ON,
                    )
                )
                db_session.flush()
        assert excinfo.value.orig.sqlstate == "23514"


def _attest_argv(software, version, statement, *extra):
    return [
        "--software", software,
        "--attest-version", version,
        "--statement", statement,
        "--attested-by", "attest-owner",
        "--attested-on", "2026-09-12",
        *extra,
    ]


def test_script_dry_run_then_commit_for_orca_and_molpro(script, monkeypatch, db_session, playground, capsys):  # noqa: F811
    _owner, _other, orca_null, molpro_null, calcs = playground
    store = _Store()

    assert _run(script, monkeypatch, db_session, store, _attest_argv("ORCA", "6", ORCA_STATEMENT)) == 0
    out = capsys.readouterr().out
    assert f"statement (verbatim): {ORCA_STATEMENT!r}" in out
    assert "3 calculation(s) -- fillable=2, other_depositor=1" in out
    assert "--commit would re-point 2 calculation(s)" in out
    assert "Dry run -- nothing was written." in out
    assert _counts(db_session) == (0, 0)

    for software, version, statement in (("ORCA", "6", ORCA_STATEMENT), ("Molpro", "26", MOLPRO_STATEMENT)):
        assert _run(
            script, monkeypatch, db_session, store, _attest_argv(software, version, statement, "--commit")
        ) == 0
    out = capsys.readouterr().out
    assert "re-pointed 2 calculation(s) under the attestation:" in out
    assert "re-pointed 1 calculation(s) under the attestation:" in out
    assert _release_tuple(db_session, calcs["orca_1"]) == ("ORCA", "6", None, None)
    assert _release_tuple(db_session, calcs["molpro_1"]) == ("Molpro", "26", None, None)
    assert _counts(db_session) == (2, 3)

    # Run twice: nothing more is written.
    assert _run(
        script, monkeypatch, db_session, store, _attest_argv("ORCA", "6", ORCA_STATEMENT, "--commit")
    ) == 0
    assert "no attestation was recorded" in capsys.readouterr().out
    assert _counts(db_session) == (2, 3)


def test_script_commit_on_a_deployed_name_needs_the_flag(script, monkeypatch, db_session, playground, capsys):  # noqa: F811
    rc = _run(
        script, monkeypatch, db_session, _Store(),
        _attest_argv("ORCA", "6", ORCA_STATEMENT, "--commit"), db_name="tckdb",
    )
    assert rc == 2
    assert "Refusing --commit" in capsys.readouterr().err
    assert _counts(db_session) == (0, 0)


def test_script_attestation_flags_go_together(script, monkeypatch, db_session, playground):  # noqa: F811
    with pytest.raises(SystemExit):
        _run(script, monkeypatch, db_session, _Store(), ["--software", "ORCA", "--attest-version", "6"])


# Review findings on #566 ----------------------------------------------------


def test_archive_round_trip_survives_a_later_re_point(db_session, playground):
    """Finding 5. Restore replays the link rows' INSERTs after the
    calculations are back at their *final* release. A calculation re-pointed
    again after the attestation (here by DR-0008's name correction, the
    Gaussian log proving ORCA never ran it) must still restore."""
    import io

    from app.services.archive import restore_archive, write_archive
    from app.services.calculation_resolution import record_software_reconciliation
    from app.services.gaussian_parameter_parser import parse_software_version
    from tests.services.archive.test_archive import _empty_archive_tables

    owner, _other, orca_null, _molpro_null, calcs = playground
    fill.apply_attestation(db_session, orca_null, _attestation(owner), load_bytes=_Store())
    calc = db_session.get(Calculation, calcs["orca_1"].id)
    record_software_reconciliation(
        db_session,
        calc,
        parsed_software=parse_software_version((FIXTURES / "gaussian/sp_ub3lyp_g16.log").read_text()),
    )
    db_session.flush()
    assert calc.software_release.software.name == "Gaussian"
    before = sorted(
        (link.calculation_id, link.before_software_release_id, link.after_software_release_id)
        for link in db_session.scalars(select(SoftwareVersionAttestationCalculation))
    )

    archive = io.BytesIO()
    write_archive(db_session, archive)
    _empty_archive_tables(db_session)
    restore_archive(db_session, io.BytesIO(archive.getvalue()))

    after = sorted(
        (link.calculation_id, link.before_software_release_id, link.after_software_release_id)
        for link in db_session.scalars(select(SoftwareVersionAttestationCalculation))
    )
    assert after == before and len(after) == 2
    assert db_session.scalar(select(func.count(SoftwareVersionAttestation.id))) == 1


def test_one_unreadable_artifact_makes_the_calculation_unreadable(db_session, playground):
    """Finding 6. One readable artifact without a banner plus one unreadable
    artifact is not evidence of absence: the unreadable one may hold the
    banner. An owner statement must not override it."""
    owner, _other, orca_null, *_ = playground
    store = _Store()
    calc = _calc(
        db_session, orca_null, created_by=owner, store=store,
        log=b"! B3LYP def2-SVP\n* xyz 0 2\nH 0 0 0\n*\n",
    )
    attach_artifact(
        db_session, calculation=calc, kind=ArtifactKind.output_log, sha256="e" * 64, filename="lost.out"
    )

    plan = fill.plan_attestation(db_session, orca_null, _attestation(owner), load_bytes=store)

    assert {o.status for o in plan.outcomes if o.calculation_ref == calc.public_ref} == {"unreadable"}


def test_a_release_carrying_a_revision_or_build_is_refused(db_session, playground):
    """Finding 8. Attesting "09" on a version-less row whose build says
    ``ES64L-G16RevC.02`` would mint ``("09", NULL, "ES64L-G16RevC.02")``."""
    owner, *_ = playground
    g_null_build = make_software_release(db_session, name="Gaussian", version=None, build="ES64L-G16RevC.02")
    _calc(db_session, g_null_build, created_by=owner)

    with pytest.raises(fill.VersionFillRefused, match="release_has_revision_or_build"):
        fill.plan_attestation(db_session, g_null_build, _attestation(owner, version="09"))


def test_script_requires_release_when_a_program_has_several_version_less_releases(
    script, monkeypatch, db_session, playground, capsys  # noqa: F811
):
    """Finding 8. One statement is never applied to every version-less
    release of a program silently."""
    owner, *_ = playground
    second = make_software_release(db_session, name="ORCA", version=None, revision="r1")
    _calc(db_session, second, created_by=owner)

    rc = _run(script, monkeypatch, db_session, _Store(), _attest_argv("ORCA", "6", ORCA_STATEMENT))

    assert rc == 2
    err = capsys.readouterr().err
    assert "--release" in err and second.public_ref in err
    assert _counts(db_session) == (0, 0)


def test_two_stored_logs_naming_different_releases_are_banners_disagree(db_session, playground):
    """The ingest hook's batch rule, applied to stored artifacts too: two
    logs naming ORCA 5.0.4 and ORCA 6.1.0 fill nothing in the banner mode."""
    owner, _other, orca_null, *_ = playground
    store = _Store()
    calc = _calc(
        db_session, orca_null, created_by=owner, store=store,
        log=(FIXTURES / "orca/sp_dlpno_ccsdt_orca.out").read_bytes(),
    )
    attach_artifact(
        db_session, calculation=calc, kind=ArtifactKind.output_log,
        sha256=store.put((FIXTURES / "orca/opt_orca.out").read_bytes()), filename="second.out",
    )

    banner = fill.plan_release_fill(db_session, orca_null, load_bytes=store)

    assert {o.status for o in banner.outcomes if o.calculation_ref == calc.public_ref} == {"banners_disagree"}


def test_a_banner_naming_another_program_has_one_status_in_both_modes(db_session, playground):
    """Finding 10. ``banner_available`` in one mode and ``other_program`` in
    the other left the operator with no route; both now say why."""
    owner, _other, orca_null, *_ = playground
    store = _Store()
    calc = _calc(
        db_session, orca_null, created_by=owner, store=store,
        log=(FIXTURES / "gaussian/sp_ub3lyp_g16.log").read_bytes(),
    )

    attest = fill.plan_attestation(db_session, orca_null, _attestation(owner), load_bytes=store)
    banner = fill.plan_release_fill(db_session, orca_null, load_bytes=store)

    for plan in (attest, banner):
        [status] = {o.status for o in plan.outcomes if o.calculation_ref == calc.public_ref}
        assert status == "banner_names_other_program"



class TestCoveredDepositor:
    """Design item 9: the depositor a statement covers is named explicitly."""

    def test_an_admin_attests_for_another_account(self, db_session, playground):
        _owner, other, orca_null, _molpro_null, calcs = playground
        admin = AppUser(username="attest-admin", role=AppUserRole.admin)
        db_session.add(admin)
        db_session.flush()

        _plan, moved = fill.apply_attestation(
            db_session, orca_null, _attestation(admin, depositor=other), load_bytes=_Store()
        )

        assert list(moved) == [calcs["orca_other"].public_ref]
        record = db_session.scalars(select(SoftwareVersionAttestation)).one()
        assert (record.attested_by, record.covers_depositor) == (admin.id, other.id)

    def test_a_non_admin_cannot_attest_for_another_account(self, db_session, playground):
        owner, other, orca_null, *_ = playground
        with pytest.raises(fill.VersionFillRefused, match="only for their own"):
            fill.plan_attestation(db_session, orca_null, _attestation(owner, depositor=other))
        assert _counts(db_session) == (0, 0)

    def test_script_for_depositor_flag(self, script, monkeypatch, db_session, playground, capsys):  # noqa: F811
        _owner, _other, _orca_null, _molpro_null, calcs = playground
        admin = AppUser(username="attest-admin", role=AppUserRole.admin)
        db_session.add(admin)
        db_session.flush()
        argv = [
            "--software", "ORCA", "--attest-version", "6", "--statement", ORCA_STATEMENT,
            "--attested-by", "attest-admin", "--for-depositor", "attest-someone-else",
            "--attested-on", "2026-09-12", "--commit",
        ]

        assert _run(script, monkeypatch, db_session, _Store(), argv) == 0

        assert "covering deposits by 'attest-someone-else'" in capsys.readouterr().out
        assert _release_tuple(db_session, calcs["orca_other"]) == ("ORCA", "6", None, None)
        assert _release_tuple(db_session, calcs["orca_1"]) == ("ORCA", None, None, None)

    def test_script_refuses_a_non_admin_for_another_account(self, script, monkeypatch, db_session, playground, capsys):  # noqa: F811
        argv = [
            "--software", "ORCA", "--attest-version", "6", "--statement", ORCA_STATEMENT,
            "--attested-by", "attest-owner", "--for-depositor", "attest-someone-else",
        ]

        assert _run(script, monkeypatch, db_session, _Store(), argv) == 2
        assert "only for their own" in capsys.readouterr().err
