"""Every refusal of the deposit builder, each with a landed cause.

A refusal test that passes because *something* raised is worthless, so every
one here asserts the specific error class, that the message names the cause,
and -- for the privacy refusals -- that the message carries a username and
never an email address or a database id.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.base import Base
from app.db.models.app_user import AppUser
from app.db.models.common import AppUserRole, SubmissionRecordType
from app.services.deposit import build
from app.services.deposit.build import (
    AccountNotAllowlistedError,
    ActorOutsideAllowlistError,
    DirtyTreeError,
    NoExactTagError,
    OutputNotEmptyError,
    ReleaseNotPublishableError,
    RevisionMismatchError,
    SourceBinding,
    SourcePinMissingError,
    UnknownVersionError,
    alembic_script_head,
    assert_publishable,
    checksum_lines,
    collect_release_members,
    source_binding,
    verify_deposit,
    write_deposit,
)
from app.services.release import versions
from app.services.release.curation import add_selection, publish_release
from app.services.release.manifest import VerificationReport, freeze_manifest
from scripts.paper.registry import GENERATORS

BACKEND_ROOT = Path(__file__).resolve().parents[3]
REPO_ROOT = BACKEND_ROOT.parent
GENERATOR_DIR = BACKEND_ROOT / "scripts" / "paper"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def tagged_repo(tmp_path: Path) -> Path:
    """A throwaway git repository with one tagged, committed file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "deposit-test@example.invalid")
    _git(repo, "config", "user.name", "deposit test")
    (repo / "tracked.txt").write_text("one\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-q", "-m", "first")
    _git(repo, "tag", "v1.0.0")
    return repo


@pytest.fixture
def fake_alembic_head(monkeypatch):
    """The throwaway repo has no ``backend/alembic.ini``; pin the head lookup."""
    monkeypatch.setattr(build, "alembic_script_head", lambda root: "feedfacefeed")
    return "feedfacefeed"


def _publish(session, release, curator, thermo, species_entry):
    add_selection(
        session,
        release=release,
        record_type=SubmissionRecordType.thermo,
        record_id=thermo.id,
        subject_type=SubmissionRecordType.species_entry,
        subject_id=species_entry.id,
        rationale="Lower-energy composite single point.",
        selected_by=curator.id,
    )
    publish_release(session, release)
    return freeze_manifest(session, release, created_by=curator.id)


def _real_source(session) -> SourceBinding:
    """A binding whose revision matches the test database, everything else honest."""
    return SourceBinding(
        git_commit="0" * 40,
        git_tag="test",
        backend_version="0.0.0-test",
        schemas_package_version="0.0.0-test",
        alembic_head=versions.alembic_revision(session),
        tree_clean=True,
    )


def _allowlist(session) -> list[str]:
    return sorted(session.scalars(select(AppUser.username)))


# ---------------------------------------------------------------------------
# source binding
# ---------------------------------------------------------------------------


def test_source_binding_binds_a_clean_tagged_head(tagged_repo, fake_alembic_head):
    binding = source_binding(tagged_repo)
    assert binding.git_commit == _git(tagged_repo, "rev-parse", "HEAD")
    assert binding.git_tag == "v1.0.0"
    assert binding.tree_clean is True
    assert binding.alembic_head == fake_alembic_head
    assert binding.backend_version != versions.UNKNOWN


def test_source_binding_refuses_a_dirty_tree(tagged_repo, fake_alembic_head):
    (tagged_repo / "tracked.txt").write_text("two\n")
    with pytest.raises(DirtyTreeError, match="dirty_tree.*tracked.txt"):
        source_binding(tagged_repo)


def test_an_untracked_file_does_not_make_the_tree_dirty(tagged_repo, fake_alembic_head):
    """An untracked file cannot change a tracked member; it is reported, not refused."""
    (tagged_repo / "stray.txt").write_text("x\n")
    (tagged_repo / "notes").mkdir()
    (tagged_repo / "notes" / "draft.md").write_text("y\n")
    binding = source_binding(tagged_repo)
    assert binding.tree_clean is True
    assert binding.untracked_paths == ("notes/draft.md", "stray.txt")
    assert "untracked_paths" not in binding.as_document()


def test_a_staged_and_a_deleted_tracked_path_still_refuse(tagged_repo, fake_alembic_head):
    (tagged_repo / "new.txt").write_text("z\n")
    _git(tagged_repo, "add", "new.txt")
    with pytest.raises(DirtyTreeError, match="new.txt"):
        source_binding(tagged_repo)
    _git(tagged_repo, "reset", "-q", "new.txt")
    (tagged_repo / "new.txt").unlink()
    (tagged_repo / "tracked.txt").unlink()
    with pytest.raises(DirtyTreeError, match="tracked.txt"):
        source_binding(tagged_repo)


def test_the_clean_tree_switch_is_real_and_reports_the_dirt(tagged_repo, fake_alembic_head):
    (tagged_repo / "tracked.txt").write_text("two\n")
    binding = source_binding(tagged_repo, require_clean_tree=False)
    assert binding.tree_clean is False


def test_source_binding_refuses_an_untagged_head(tagged_repo, fake_alembic_head):
    (tagged_repo / "tracked.txt").write_text("two\n")
    _git(tagged_repo, "commit", "-q", "-am", "second")
    with pytest.raises(NoExactTagError, match="no_exact_tag"):
        source_binding(tagged_repo)


def test_the_exact_tag_switch_is_real(tagged_repo, fake_alembic_head):
    (tagged_repo / "tracked.txt").write_text("two\n")
    _git(tagged_repo, "commit", "-q", "-am", "second")
    binding = source_binding(tagged_repo, require_exact_tag=False)
    assert binding.git_tag is None


def test_source_binding_refuses_an_unknown_package_version(tagged_repo, fake_alembic_head, monkeypatch):
    monkeypatch.setattr(versions, "backend_version", lambda: versions.UNKNOWN)
    with pytest.raises(UnknownVersionError, match="backend_version"):
        source_binding(tagged_repo)


def test_alembic_script_head_is_the_revision_the_test_database_is_at(db_session):
    assert alembic_script_head(REPO_ROOT) == versions.alembic_revision(db_session)


# ---------------------------------------------------------------------------
# publishability
# ---------------------------------------------------------------------------


def test_refuses_a_database_not_at_the_script_head(db_session, draft_release, curator, thermo_candidates, species_entry):
    _publish(db_session, draft_release, curator, thermo_candidates[0], species_entry)
    source = SourceBinding(
        git_commit="0" * 40,
        git_tag="t",
        backend_version="1",
        schemas_package_version="1",
        alembic_head="deadbeefcafe",
        tree_clean=True,
    )
    actual = versions.alembic_revision(db_session)
    with pytest.raises(RevisionMismatchError) as excinfo:
        assert_publishable(db_session, draft_release, author_accounts=_allowlist(db_session), source=source)
    assert "deadbeefcafe" in str(excinfo.value)
    assert actual in str(excinfo.value)


def test_refuses_an_unknown_version_even_when_injected(db_session, draft_release, curator, thermo_candidates, species_entry):
    _publish(db_session, draft_release, curator, thermo_candidates[0], species_entry)
    source = SourceBinding(
        git_commit="0" * 40,
        git_tag="t",
        backend_version=versions.UNKNOWN,
        schemas_package_version="1",
        alembic_head=versions.alembic_revision(db_session),
        tree_clean=True,
    )
    with pytest.raises(UnknownVersionError, match="backend_version"):
        assert_publishable(db_session, draft_release, author_accounts=_allowlist(db_session), source=source)


def test_refuses_an_account_outside_the_allowlist_naming_only_the_username(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    _publish(db_session, draft_release, curator, thermo_candidates[0], species_entry)
    allowlist = _allowlist(db_session)
    stranger = AppUser(
        username="dormant-stranger",
        email="dormant-stranger@example.invalid",
        role=AppUserRole.user,
    )
    db_session.add(stranger)
    db_session.flush()

    with pytest.raises(AccountNotAllowlistedError) as excinfo:
        assert_publishable(db_session, draft_release, author_accounts=allowlist, source=_real_source(db_session))
    message = str(excinfo.value)
    assert "account_not_allowlisted" in message
    assert "dormant-stranger" in message
    assert "@" not in message
    assert f"id={stranger.id}" not in message and f"#{stranger.id}" not in message


def test_refuses_an_actor_outside_the_allowlist_naming_the_column_and_username(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    _publish(db_session, draft_release, curator, thermo_candidates[0], species_entry)
    curator.email = "release-curator@example.invalid"
    db_session.flush()
    allowlist = [name for name in _allowlist(db_session) if name != curator.username]

    with pytest.raises(ActorOutsideAllowlistError) as excinfo:
        assert_publishable(db_session, draft_release, author_accounts=allowlist, source=_real_source(db_session))
    message = str(excinfo.value)
    assert "actor_outside_allowlist" in message
    assert "release_selection.selected_by: release-curator" in message
    assert "@" not in message


def test_refuses_an_empty_allowlist(db_session, draft_release, curator, thermo_candidates, species_entry):
    _publish(db_session, draft_release, curator, thermo_candidates[0], species_entry)
    with pytest.raises(AccountNotAllowlistedError, match="author_allowlist_empty"):
        assert_publishable(db_session, draft_release, author_accounts=[], source=_real_source(db_session))


def test_actor_columns_equal_an_independent_walk_of_every_fk_onto_app_user():
    """Set equality against a second, independently written metadata walk.

    A named-column spot check stayed green when ``thermo.created_by`` was
    dropped from ``actor_columns()``; this cannot.
    """
    expected: set[tuple[str, str]] = set()
    for table in Base.metadata.sorted_tables:
        for constraint in table.foreign_key_constraints:
            for element in constraint.elements:
                if element.column.table.name == "app_user" and element.column.name == "id":
                    expected.add((table.name, element.parent.name))
    actual = set(build.actor_columns())
    assert actual == expected
    assert len(build.actor_columns()) == len(actual), "no duplicates"
    # Measured on this revision (Alembic head a55cc983501a): 52 foreign keys onto
    # app_user.id. A table can only add to this; a drop below it is a lost actor.
    assert len(actual) >= 52
    for pair in (("submission", "created_by"), ("release_selection", "selected_by"), ("record_review", "reviewed_by")):
        assert pair in actual


# ---------------------------------------------------------------------------
# release members
# ---------------------------------------------------------------------------


def test_collect_refuses_an_unfrozen_release(db_session, draft_release):
    with pytest.raises(ReleaseNotPublishableError, match="release_not_frozen"):
        collect_release_members(db_session, draft_release)


def test_collect_refuses_a_release_that_does_not_verify(
    db_session, draft_release, curator, thermo_candidates, species_entry, monkeypatch
):
    _publish(db_session, draft_release, curator, thermo_candidates[0], species_entry)

    def _broken(session, release):
        return VerificationReport(
            release_ref=release.public_ref,
            tag=release.tag,
            manifest_ref="rm_x",
            content_sha256_recorded="a",
            content_sha256_recomputed="b",
            artifacts_checked=4,
            problems=["selected_records.ndjson: stored bytes hash to b, recorded a"],
        )

    monkeypatch.setattr(build, "verify_release", _broken)
    with pytest.raises(ReleaseNotPublishableError, match="release_not_verified.*selected_records"):
        collect_release_members(db_session, draft_release)


def test_collect_emits_the_frozen_bytes_with_their_recorded_digests(
    db_session, draft_release, curator, thermo_candidates, species_entry
):
    manifest = _publish(db_session, draft_release, curator, thermo_candidates[0], species_entry)
    _, members = collect_release_members(db_session, draft_release)
    by_path = {member.path: (member, content) for member, content in members}
    assert by_path["release/manifest.json"][0].sha256 == manifest.content_sha256
    for row in manifest.artifacts:
        member, content = by_path[f"release/{row.path}"]
        assert member.sha256 == row.sha256
        assert content == bytes(row.content)
    assert len(members) == 5


# ---------------------------------------------------------------------------
# write + verify
# ---------------------------------------------------------------------------


def test_write_deposit_defaults_to_the_strict_source_checks(
    db_session, draft_release, curator, thermo_candidates, species_entry, tagged_repo, fake_alembic_head, tmp_path
):
    _publish(db_session, draft_release, curator, thermo_candidates[0], species_entry)
    (tagged_repo / "tracked.txt").write_text("two\n")
    with pytest.raises(DirtyTreeError):
        write_deposit(
            db_session,
            release_tag=draft_release.tag,
            output_dir=tmp_path / "out",
            author_accounts=_allowlist(db_session),
            repo_root=tagged_repo,
            generators=GENERATORS,
            generator_dir=GENERATOR_DIR,
        )
    assert not (tmp_path / "out").exists()


def test_switching_the_source_checks_off_reaches_the_next_refusal(
    db_session, draft_release, curator, thermo_candidates, species_entry, tagged_repo, tmp_path, monkeypatch
):
    """The switches are real: with them off, a dirty untagged repo gets as far as its missing pins."""
    _publish(db_session, draft_release, curator, thermo_candidates[0], species_entry)
    monkeypatch.setattr(build, "alembic_script_head", lambda root: versions.alembic_revision(db_session))
    (tagged_repo / "tracked.txt").write_text("two\n")
    with pytest.raises(SourcePinMissingError, match="backend/environment.yml"):
        write_deposit(
            db_session,
            release_tag=draft_release.tag,
            output_dir=tmp_path / "out",
            author_accounts=_allowlist(db_session),
            repo_root=tagged_repo,
            generators=GENERATORS,
            generator_dir=GENERATOR_DIR,
            require_clean_tree=False,
            require_exact_tag=False,
        )


def test_write_deposit_refuses_a_non_empty_output_directory(
    db_session, draft_release, curator, thermo_candidates, species_entry, tmp_path
):
    _publish(db_session, draft_release, curator, thermo_candidates[0], species_entry)
    out = tmp_path / "out"
    out.mkdir()
    (out / "leftover").write_text("x")
    with pytest.raises(OutputNotEmptyError):
        write_deposit(
            db_session,
            release_tag=draft_release.tag,
            output_dir=out,
            author_accounts=_allowlist(db_session),
            repo_root=REPO_ROOT,
            generators=GENERATORS,
            generator_dir=GENERATOR_DIR,
            require_clean_tree=False,
            require_exact_tag=False,
        )
    assert (out / "leftover").exists()


def test_write_deposit_refuses_when_the_archived_release_rows_differ_from_the_emitted_files(
    db_session, draft_release, curator, thermo_candidates, species_entry, tmp_path, monkeypatch
):
    """The archive and the release members come from one session; the build checks it rather than trusts it."""
    _publish(db_session, draft_release, curator, thermo_candidates[0], species_entry)
    real = build._archived_release_artifact_digests

    def _tampered(archive_path, manifest_id):
        digests = real(archive_path, manifest_id)
        first = sorted(digests)[0]
        return {**digests, first: "0" * 64}

    monkeypatch.setattr(build, "_archived_release_artifact_digests", _tampered)
    with pytest.raises(ReleaseNotPublishableError, match="archive_release_artifacts_differ"):
        write_deposit(
            db_session,
            release_tag=draft_release.tag,
            output_dir=tmp_path / "out",
            author_accounts=_allowlist(db_session),
            repo_root=REPO_ROOT,
            generators=GENERATORS,
            generator_dir=GENERATOR_DIR,
            require_clean_tree=False,
            require_exact_tag=False,
        )
    assert not (tmp_path / "out").exists(), "a refused build leaves nothing behind"


@pytest.fixture
def built_deposit(db_session, draft_release, curator, thermo_candidates, species_entry, tmp_path) -> Path:
    _publish(db_session, draft_release, curator, thermo_candidates[0], species_entry)
    result = write_deposit(
        db_session,
        release_tag=draft_release.tag,
        output_dir=tmp_path / "deposit",
        author_accounts=_allowlist(db_session),
        repo_root=REPO_ROOT,
        generators=GENERATORS,
        generator_dir=GENERATOR_DIR,
        require_clean_tree=False,
        require_exact_tag=False,
    )
    assert verify_deposit(result.path).ok
    return result.path


def _manifest(path: Path) -> dict:
    return json.loads((path / "MANIFEST.json").read_text())


def test_manifest_records_the_bound_source_and_never_an_email(built_deposit):
    document = _manifest(built_deposit)
    assert document["schema"] == "tckdb.deposit.v1"
    assert len(document["source"]["git_commit"]) == 40
    assert document["source"]["alembic_head"] == alembic_script_head(REPO_ROOT)
    # A relaxed (test) build must be byte-distinguishable from a strict one.
    assert set(document["source"]) == {
        "git_commit",
        "git_tag",
        "backend_version",
        "schemas_package_version",
        "alembic_head",
        "tree_clean",
        "checks",
    }
    assert document["source"]["checks"] == {"clean_tree": False, "exact_tag": False}
    assert isinstance(document["source"]["tree_clean"], bool)
    assert json.loads((built_deposit / "source" / "commit.json").read_text())["checks"] == document["source"]["checks"]
    assert set(document["release"]) == {"tag", "ref", "manifest_content_sha256"}
    assert document["privacy"]["author_accounts"]
    assert "@" not in (built_deposit / "MANIFEST.json").read_text()
    assert "@" not in (built_deposit / "ACCOUNTS.md").read_text()
    assert build.RESIDUAL_RISK_PARAGRAPH in (built_deposit / "ACCOUNTS.md").read_text()
    reproduce = (built_deposit / "REPRODUCE.md").read_text()
    for name in ("DB_USER", "DB_PASSWORD", "DB_HOST", "DB_PORT", "DB_NAME", "S3_ENDPOINT_URL", "S3_BUCKET"):
        assert name in reproduce
    assert document["source"]["git_commit"] in reproduce


def _sha256sum_check(stdin: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["sha256sum", "-c", "-"], input=stdin, cwd=cwd, capture_output=True, text=True)


def _assert_every_member_ok(result: subprocess.CompletedProcess[str], deposit: Path) -> None:
    members = [m["path"] for m in _manifest(deposit)["members"]]
    assert result.returncode == 0, result.stdout + result.stderr
    ok_lines = [line for line in result.stdout.splitlines() if line.endswith(": OK")]
    assert sorted(line[: -len(": OK")] for line in ok_lines) == sorted(members)
    assert "FAILED" not in result.stdout and "FAILED" not in result.stderr


def test_the_reproduce_step_zero_one_liner_passes_sha256sum(built_deposit):
    """The command a reader pastes from REPRODUCE.md must actually verify the deposit.

    The first version printed three spaces between digest and path, which
    ``sha256sum -c`` reads as a different filename and reports as
    ``FAILED open or read`` for every member (exit 1).
    """
    reproduce = (built_deposit / "REPRODUCE.md").read_text()
    lines = [line for line in reproduce.splitlines() if line.startswith("python3 -c ") and "sha256sum -c -" in line]
    assert len(lines) == 1, "REPRODUCE.md must carry exactly one step-0 one-liner"
    result = subprocess.run(lines[0], shell=True, cwd=built_deposit, capture_output=True, text=True)
    _assert_every_member_ok(result, built_deposit)


def test_checksum_lines_helper_passes_sha256sum_and_matches_the_ops_subcommand(built_deposit, capsys):
    document = _manifest(built_deposit)
    listing = checksum_lines(document)
    assert listing.count("\n") == len(document["members"])
    _assert_every_member_ok(_sha256sum_check(listing, built_deposit), built_deposit)

    from scripts.ops import build_publication_deposit as cli

    assert cli.main(["checksums", str(built_deposit)]) == 0
    assert capsys.readouterr().out == listing


def test_verify_detects_a_flipped_byte_in_a_release_artifact(built_deposit):
    target = built_deposit / "release" / "selected_records.ndjson"
    content = bytearray(target.read_bytes())
    content[0] ^= 0x01
    target.write_bytes(bytes(content))
    report = verify_deposit(built_deposit)
    assert not report.ok
    assert any("release/selected_records.ndjson: hashes to" in p for p in report.problems)


def test_verify_detects_a_member_dropped_from_the_manifest(built_deposit):
    document = _manifest(built_deposit)
    document["members"] = [m for m in document["members"] if m["path"] != "REPRODUCE.md"]
    (built_deposit / "MANIFEST.json").write_text(json.dumps(document))
    report = verify_deposit(built_deposit)
    assert not report.ok
    assert any("undeclared files present" in p and "REPRODUCE.md" in p for p in report.problems)
    assert any("role protocol: 0 member(s)" in p for p in report.problems)


def test_verify_detects_a_flipped_byte_inside_the_archive(built_deposit):
    document = _manifest(built_deposit)
    archive_path = next(m["path"] for m in document["members"] if m["role"] == "evidence_archive")
    target = built_deposit / archive_path
    content = bytearray(target.read_bytes())
    # Past the tar header of the first member, inside manifest.json's bytes.
    content[600] ^= 0x01
    target.write_bytes(bytes(content))
    report = verify_deposit(built_deposit)
    assert not report.ok
    assert any(f"{archive_path}: hashes to" in p for p in report.problems)


def test_verify_detects_an_undeclared_extra_file(built_deposit):
    (built_deposit / "expected_outputs" / "extra.json").write_text("{}")
    report = verify_deposit(built_deposit)
    assert not report.ok
    assert any("undeclared files present" in p and "expected_outputs/extra.json" in p for p in report.problems)


def test_verify_with_a_session_detects_a_revision_and_account_drift(built_deposit, db_session):
    clean = verify_deposit(built_deposit, session=db_session)
    assert clean.ok, clean.problems
    assert clean.database_checked

    db_session.add(AppUser(username="late-stranger", role=AppUserRole.user))
    db_session.flush()
    drifted = verify_deposit(built_deposit, session=db_session)
    assert not drifted.ok
    assert any("late-stranger" in p for p in drifted.problems)
