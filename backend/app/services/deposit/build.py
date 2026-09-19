"""Build and verify a ``tckdb.deposit.v1`` publication deposit.

What a deposit is
-----------------
A published dataset release freezes four NDJSON files and a manifest, but by
its own contract omits the evidence: raw artifact bytes, geometries and
calculation payloads. That evidence lives in a ``tckdb.archive.v1`` archive.
Neither says which git commit produced it, and nothing binds the two to the
scripts that turn the database into the numbers a manuscript quotes.

The deposit is the directory that binds all of it by digest::

    MANIFEST.json                 canonical JSON: schema, release, source,
                                  privacy, members [{path, sha256, bytes, role}]
    release/manifest.json         role release_manifest
    release/*.ndjson              role release_artifact  (exactly four)
    archive/<tag>.archive.tar     role evidence_archive  (written in the same
                                  session as the release members were read)
    source/commit.json, environment.yml, uv.lock, Dockerfile,
    CITATION.cff, LICENSE, LICENSE-DATA
                                  role source_pin
    scripts/*.py                  role result_generator
    expected_outputs/<name>.json, <name>.md
                                  role expected_output
    REPRODUCE.md                  role protocol
    ACCOUNTS.md                   role privacy

Refusals
--------
Every reason the build will not proceed is its own error class with an exit
code, so a runbook can name it: a dirty working tree (tracked paths only;
untracked files are logged, never refused), no exact tag on HEAD,
an ``"unknown"`` package version, a database whose Alembic revision is not
the script head, a release that does not verify, an account outside the
author allowlist, an actor reference resolving outside it, a non-empty
output directory. Refusal messages name usernames only -- never an email or
a database id.

No HTTP: the release is read from the database through
:func:`app.services.release.manifest.load_manifest` and
``ReleaseArtifact.content``. No redaction: the archive keeps its byte-exact,
fail-closed contract, which is why the allowlist exists.

``ReleaseManifest`` records no git commit of its own yet; the commit bound
here comes from ``git rev-parse HEAD`` at build time. A manifest column is a
follow-up once the manifest schema v2 bump (owned elsewhere) has landed.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import tarfile
from collections.abc import Collection, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from string import Template
from typing import Any

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.app_user import AppUser
from app.db.models.common import DatasetReleaseStatus
from app.db.models.dataset_release import DatasetRelease, ReleaseManifest
from app.services.archive import verify_archive, write_archive
from app.services.deposit.expected_outputs import Generator, write_expected_outputs
from app.services.release import versions
from app.services.release.artifacts import canonical_json
from app.services.release.manifest import load_manifest, verify_release

DEPOSIT_SCHEMA = "tckdb.deposit.v1"
MANIFEST_NAME = "MANIFEST.json"
REPRODUCE_TEMPLATE = Path(__file__).with_name("REPRODUCE.md.template")

#: Source pins copied verbatim from the checkout, as ``(repo-relative source,
#: deposit path)``. Every one is required: a missing pin is a refusal, not a
#: silently thinner deposit.
SOURCE_PINS: tuple[tuple[str, str], ...] = (
    ("backend/environment.yml", "source/environment.yml"),
    ("backend/uv.lock", "source/uv.lock"),
    ("backend/Dockerfile", "source/Dockerfile"),
    ("CITATION.cff", "source/CITATION.cff"),
    ("LICENSE", "source/LICENSE"),
    ("LICENSE-DATA", "source/LICENSE-DATA"),
)

ROLE_RELEASE_MANIFEST = "release_manifest"
ROLE_RELEASE_ARTIFACT = "release_artifact"
ROLE_EVIDENCE_ARCHIVE = "evidence_archive"
ROLE_SOURCE_PIN = "source_pin"
ROLE_RESULT_GENERATOR = "result_generator"
ROLE_EXPECTED_OUTPUT = "expected_output"
ROLE_PROTOCOL = "protocol"
ROLE_PRIVACY = "privacy"

#: How many members of each role a complete deposit carries; ``None`` means
#: at least one.
REQUIRED_ROLE_COUNTS: dict[str, int | None] = {
    ROLE_RELEASE_MANIFEST: 1,
    ROLE_RELEASE_ARTIFACT: 4,
    ROLE_EVIDENCE_ARCHIVE: 1,
    ROLE_SOURCE_PIN: None,
    ROLE_RESULT_GENERATOR: None,
    ROLE_EXPECTED_OUTPUT: None,
    ROLE_PROTOCOL: 1,
    ROLE_PRIVACY: 1,
}

#: Residual-risk statement every ``ACCOUNTS.md`` carries verbatim.
RESIDUAL_RISK_PARAGRAPH = (
    "Residual risk. The evidence archive is byte-exact and has no redaction "
    "mode: it carries the electronic-structure output files exactly as the "
    "authors uploaded them. Those files routinely embed the absolute paths of "
    "the directories they were run in, which reveal the authors' cluster "
    "user names, project names and scratch layout. Every account whose data "
    "is included is listed above and has agreed to that exposure; no other "
    "person's data is present, because the build refuses any account outside "
    "this list."
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class DepositError(RuntimeError):
    """Base class: the deposit was not built, and the message says why."""

    exit_code = 1


class ReleaseNotPublishableError(DepositError):
    """The release is missing, unfrozen, withdrawn, or does not verify."""

    exit_code = 2


class DirtyTreeError(DepositError):
    """``git status --porcelain`` is not empty."""

    exit_code = 3


class NoExactTagError(DepositError):
    """``HEAD`` carries no tag (``git describe --tags --exact-match``)."""

    exit_code = 3


class UnknownVersionError(DepositError):
    """A package version resolved to ``"unknown"``."""

    exit_code = 3


class SourcePinMissingError(DepositError):
    """A required source pin file is absent from the checkout."""

    exit_code = 3


class RevisionMismatchError(DepositError):
    """The database's ``alembic_version`` is not the script directory's head."""

    exit_code = 4


class AccountNotAllowlistedError(DepositError):
    """An ``app_user`` row exists outside the author allowlist."""

    exit_code = 5


class ActorOutsideAllowlistError(DepositError):
    """A row's actor reference resolves to an account outside the allowlist."""

    exit_code = 5


class OutputNotEmptyError(DepositError):
    """The output directory already has content."""

    exit_code = 6


# ---------------------------------------------------------------------------
# Source binding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceBinding:
    """The code the deposit is bound to."""

    git_commit: str
    git_tag: str | None
    backend_version: str
    schemas_package_version: str
    alembic_head: str
    tree_clean: bool
    #: Untracked, unignored paths at build time. Reported, never refused: an
    #: untracked file cannot change any tracked member the deposit copies,
    #: and the author's checkout deliberately keeps ``paper/`` and
    #: ``.agents/`` untracked and unignored.
    untracked_paths: tuple[str, ...] = ()

    def as_document(self) -> dict[str, Any]:
        return {
            "git_commit": self.git_commit,
            "git_tag": self.git_tag,
            "backend_version": self.backend_version,
            "schemas_package_version": self.schemas_package_version,
            "alembic_head": self.alembic_head,
        }


def _git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )


def alembic_script_head(repo_root: Path) -> str:
    """The single head of ``backend/alembic/versions``, from the script directory.

    Read from the files, not from any database, so the comparison in
    :func:`assert_publishable` is between two independent sources.
    """
    config = AlembicConfig(str(repo_root / "backend" / "alembic.ini"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RevisionMismatchError(
            f"alembic_heads_ambiguous: the script directory has {len(heads)} heads {sorted(heads)}; "
            "a deposit binds exactly one"
        )
    return heads[0]


def source_binding(
    repo_root: Path,
    *,
    require_clean_tree: bool = True,
    require_exact_tag: bool = True,
    require_known_versions: bool = True,
) -> SourceBinding:
    """Resolve commit, tag, package versions and Alembic head for ``repo_root``.

    The three ``require_*`` switches exist so a test can build a deposit from
    an untagged, uncommitted worktree; every one defaults to the refusing
    behaviour and the ops script never relaxes them.
    """
    repo_root = Path(repo_root).resolve()
    head = _git(repo_root, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise DepositError(f"not_a_git_repository: {repo_root}: {head.stderr.strip()}")
    git_commit = head.stdout.strip()

    # Only TRACKED paths make the tree dirty: modified, staged, deleted or
    # renamed. Untracked files are collected for the build log and never
    # refuse, because nothing the deposit copies can be changed by one.
    status = _git(repo_root, "status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0:
        raise DepositError(f"git_status_failed: {status.stderr.strip()}")
    tree_clean = status.stdout.strip() == ""
    if require_clean_tree and not tree_clean:
        changed = [line[3:] for line in status.stdout.splitlines() if line.strip()]
        raise DirtyTreeError(
            "dirty_tree: tracked paths differ from the commit a deposit would bind to: "
            + ", ".join(changed[:10])
            + (f" (+{len(changed) - 10} more)" if len(changed) > 10 else "")
        )
    untracked = _git(repo_root, "ls-files", "--others", "--exclude-standard")
    untracked_paths = tuple(line for line in untracked.stdout.splitlines() if line.strip())

    described = _git(repo_root, "describe", "--tags", "--exact-match", "HEAD")
    git_tag: str | None = described.stdout.strip() if described.returncode == 0 else None
    if require_exact_tag and git_tag is None:
        raise NoExactTagError(
            f"no_exact_tag: HEAD {git_commit[:12]} carries no tag; a deposit binds to a tagged commit"
        )

    backend_version = versions.backend_version()
    schemas_version = versions.schemas_package_version()
    if require_known_versions:
        unknown = [
            name
            for name, value in (
                ("backend_version", backend_version),
                ("schemas_package_version", schemas_version),
            )
            if value == versions.UNKNOWN
        ]
        if unknown:
            raise UnknownVersionError(
                "unknown_version: " + ", ".join(unknown) + " resolved to 'unknown'; install the "
                "packages (pip install -e backend, pip install -e schemas/python/tckdb-schemas) "
                "so the deposit can state what produced it"
            )

    return SourceBinding(
        git_commit=git_commit,
        git_tag=git_tag,
        backend_version=backend_version,
        schemas_package_version=schemas_version,
        alembic_head=alembic_script_head(repo_root),
        tree_clean=tree_clean,
        untracked_paths=untracked_paths,
    )


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Member:
    path: str
    sha256: str
    bytes: int
    role: str

    def as_document(self) -> dict[str, Any]:
        return asdict(self)


def _member(path: str, content: bytes, role: str) -> Member:
    return Member(path=path, sha256=hashlib.sha256(content).hexdigest(), bytes=len(content), role=role)


def _release_by_tag(session: Session, tag: str) -> DatasetRelease:
    release = session.scalars(select(DatasetRelease).where(DatasetRelease.tag == tag)).one_or_none()
    if release is None:
        raise ReleaseNotPublishableError(f"release_not_found: no dataset release is tagged {tag!r}")
    return release


def collect_release_members(
    session: Session, release: DatasetRelease
) -> tuple[ReleaseManifest, list[tuple[Member, bytes]]]:
    """The frozen manifest document and the four artifacts, as deposit members.

    Reads only the frozen rows. Refuses unless :func:`verify_release` is
    clean, and re-checks every digest it emits against the row that recorded
    it, so the deposit can never carry bytes the release did not.
    """
    if release.status is DatasetReleaseStatus.withdrawn:
        raise ReleaseNotPublishableError(
            f"release_withdrawn: release {release.tag!r} was retracted and cannot be deposited"
        )
    manifest = load_manifest(session, release)
    if manifest is None:
        raise ReleaseNotPublishableError(
            f"release_not_frozen: release {release.tag!r} has no frozen manifest; publish it first"
        )
    report = verify_release(session, release)
    if not report.ok:
        raise ReleaseNotPublishableError(
            f"release_not_verified: release {release.tag!r} fails verification: " + "; ".join(report.problems)
        )

    members: list[tuple[Member, bytes]] = []
    document = canonical_json(dict(manifest.document_json)).encode("utf-8")
    manifest_member = _member("release/manifest.json", document, ROLE_RELEASE_MANIFEST)
    if manifest_member.sha256 != manifest.content_sha256:
        raise ReleaseNotPublishableError(
            "release_manifest_digest_differs: the canonical document does not hash to the recorded "
            f"content_sha256 {manifest.content_sha256}"
        )
    members.append((manifest_member, document))
    for row in sorted(manifest.artifacts, key=lambda a: a.path):
        content = bytes(row.content)
        member = _member(f"release/{row.path}", content, ROLE_RELEASE_ARTIFACT)
        if member.sha256 != row.sha256 or member.bytes != row.byte_count:
            raise ReleaseNotPublishableError(
                f"release_artifact_digest_differs: {row.path} hashes to {member.sha256}, recorded {row.sha256}"
            )
        members.append((member, content))
    return manifest, members


# ---------------------------------------------------------------------------
# Publishability
# ---------------------------------------------------------------------------


def actor_columns(metadata=Base.metadata) -> list[tuple[str, str]]:
    """Every ``(table, column)`` that is a foreign key onto ``app_user.id``.

    Introspected rather than listed, so a table added later (a rights
    attestation with ``attested_by``, say) is covered the day it exists
    instead of the day someone remembers to add it here.
    """
    found: list[tuple[str, str]] = []
    for table in metadata.sorted_tables:
        for column in table.columns:
            for fk in column.foreign_keys:
                if fk.column.table.name == "app_user" and fk.column.name == "id":
                    found.append((table.name, column.name))
    return sorted(found)


def _actors_outside(session: Session, allowlist: frozenset[str]) -> list[tuple[str, str, list[str]]]:
    outside: list[tuple[str, str, list[str]]] = []
    for table_name, column_name in actor_columns():
        table = Base.metadata.tables[table_name]
        column = table.c[column_name]
        usernames = sorted(
            set(
                session.scalars(
                    select(AppUser.username)
                    .select_from(table)
                    .join(AppUser, AppUser.id == column)
                    .where(column.is_not(None))
                    .distinct()
                )
            )
            - allowlist
        )
        if usernames:
            outside.append((table_name, column_name, usernames))
    return outside


def assert_publishable(
    session: Session,
    release: DatasetRelease,
    *,
    author_accounts: Collection[str],
    source: SourceBinding,
) -> None:
    """Refuse unless every account, actor, version and revision is acceptable.

    Order matters for the message a curator sees: actor references are
    checked before the bare account list, so a stranger who *did* something
    is reported by what they did, and a dormant stranger by their existence.
    """
    allowlist = frozenset(author_accounts)
    if not allowlist:
        raise AccountNotAllowlistedError("author_allowlist_empty: at least one --author-account is required")

    outside = _actors_outside(session, allowlist)
    if outside:
        detail = "; ".join(f"{table}.{column}: {', '.join(names)}" for table, column, names in outside)
        raise ActorOutsideAllowlistError(
            "actor_outside_allowlist: rows reference accounts that are not authors: " + detail
        )

    strangers = sorted(set(session.scalars(select(AppUser.username))) - allowlist)
    if strangers:
        raise AccountNotAllowlistedError(
            "account_not_allowlisted: the database holds accounts that are not authors: " + ", ".join(strangers)
        )

    unknown = [
        name
        for name, value in (
            ("backend_version", source.backend_version),
            ("schemas_package_version", source.schemas_package_version),
        )
        if value == versions.UNKNOWN
    ]
    if unknown:
        raise UnknownVersionError("unknown_version: " + ", ".join(unknown) + " is 'unknown'")

    database_revision = versions.alembic_revision(session)
    if database_revision != source.alembic_head:
        raise RevisionMismatchError(
            f"revision_mismatch: the database is at {database_revision}, the checkout's Alembic head is "
            f"{source.alembic_head}; upgrade (or check out the matching commit) before depositing"
        )

    if release.status is not DatasetReleaseStatus.published:
        raise ReleaseNotPublishableError(
            f"release_not_published: release {release.tag!r} is {release.status.value}"
        )


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DepositResult:
    path: Path
    manifest: dict[str, Any]
    source: SourceBinding
    members: list[Member] = field(default_factory=list)


def _write(root: Path, relative: str, content: bytes) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def _archived_release_artifact_digests(archive_path: Path, manifest_id: int) -> dict[str, str]:
    """``{artifact path: sha256 of the archived bytes}`` for one manifest, read from the tar."""
    digests: dict[str, str] = {}
    with tarfile.open(archive_path, "r:*") as archive:
        handle = archive.extractfile("rows.ndjson")
        if handle is None:
            raise ReleaseNotPublishableError("archive_unreadable: rows.ndjson is missing from the archive")
        for raw in handle:
            if b'"release_artifact"' not in raw:
                continue
            record = json.loads(raw)
            if record.get("table") != "release_artifact":
                continue
            values = record["values"]
            if values.get("release_manifest_id") != manifest_id:
                continue
            content = base64.b64decode(values["content"]["$bytes"])
            digests[values["path"]] = hashlib.sha256(content).hexdigest()
    return digests


def _accounts_document(session: Session, allowlist: frozenset[str]) -> bytes:
    users = sorted(session.scalars(select(AppUser)), key=lambda user: user.username)
    lines = [
        "# Accounts whose data this deposit contains",
        "",
        "Every account on the publication instance, all within the author allowlist "
        "the build was given. Usernames, ORCIDs and affiliations only; no email "
        "address appears anywhere in the deposit.",
        "",
        "| username | ORCID | affiliation |",
        "| --- | --- | --- |",
    ]
    for user in users:
        if user.username not in allowlist:  # pragma: no cover - refused earlier
            raise AccountNotAllowlistedError(f"account_not_allowlisted: {user.username}")
        lines.append(f"| {user.username} | {user.orcid or ''} | {(user.affiliation or '').replace('|', '/')} |")
    lines.extend(["", RESIDUAL_RISK_PARAGRAPH, ""])
    return "\n".join(lines).encode("utf-8")


def _reproduce_document(*, tag: str, source: SourceBinding, archive_member: str) -> bytes:
    template = Template(REPRODUCE_TEMPLATE.read_text(encoding="utf-8"))
    return template.substitute(
        tag=tag,
        git_commit=source.git_commit,
        git_tag=source.git_tag or "(untagged build; not a publishable deposit)",
        alembic_head=source.alembic_head,
        backend_version=source.backend_version,
        schemas_package_version=source.schemas_package_version,
        archive_member=archive_member,
    ).encode("utf-8")


def write_deposit(
    session: Session,
    *,
    release_tag: str,
    output_dir: Path,
    author_accounts: Collection[str],
    repo_root: Path,
    generators: Mapping[str, Generator],
    generator_dir: Path,
    source: SourceBinding | None = None,
    require_clean_tree: bool = True,
    require_exact_tag: bool = True,
) -> DepositResult:
    """Assemble a deposit for ``release_tag`` into ``output_dir``.

    The release members, the archive and the expected outputs are all read
    or written through ``session`` -- one transaction, one state of the
    corpus -- so the archive cannot describe a database the release members
    were not read from. That is checked, not assumed: the ``release_artifact``
    rows inside the archive are re-hashed and compared with the emitted
    release members.
    """
    repo_root = Path(repo_root).resolve()
    output_dir = Path(output_dir)
    allowlist = frozenset(author_accounts)

    release = _release_by_tag(session, release_tag)
    if source is None:
        source = source_binding(
            repo_root, require_clean_tree=require_clean_tree, require_exact_tag=require_exact_tag
        )
    manifest, release_members = collect_release_members(session, release)
    assert_publishable(session, release, author_accounts=allowlist, source=source)

    pins: list[tuple[Path, str]] = []
    for relative_source, deposit_path in SOURCE_PINS:
        candidate = repo_root / relative_source
        if not candidate.is_file():
            raise SourcePinMissingError(f"source_pin_missing: {relative_source} is not in the checkout")
        pins.append((candidate, deposit_path))
    generator_sources = sorted(p for p in Path(generator_dir).glob("*.py"))
    if not generator_sources:
        raise DepositError(f"no_generators: {generator_dir} holds no generator sources")

    if output_dir.exists() and any(output_dir.iterdir()):
        raise OutputNotEmptyError(f"output_not_empty: {output_dir} already has content; refusing to overwrite")
    created_here = not output_dir.exists()
    output_dir.mkdir(parents=True, exist_ok=True)

    members: list[Member] = []
    try:
        for member, content in release_members:
            _write(output_dir, member.path, content)
            members.append(member)

        archive_member_path = f"archive/{release.tag}.archive.tar"
        archive_path = output_dir / archive_member_path
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        write_archive(session, archive_path)
        archived = _archived_release_artifact_digests(archive_path, manifest.id)
        emitted = {
            member.path.removeprefix("release/"): member.sha256
            for member in members
            if member.role == ROLE_RELEASE_ARTIFACT
        }
        if archived != emitted:
            raise ReleaseNotPublishableError(
                "archive_release_artifacts_differ: the release_artifact rows inside the archive do not hash "
                f"to the emitted release members (archived {sorted(archived)}, emitted {sorted(emitted)})"
            )
        members.append(_member(archive_member_path, archive_path.read_bytes(), ROLE_EVIDENCE_ARCHIVE))

        commit_document = canonical_json({**source.as_document(), "release_tag": release.tag}).encode("utf-8") + b"\n"
        _write(output_dir, "source/commit.json", commit_document)
        members.append(_member("source/commit.json", commit_document, ROLE_SOURCE_PIN))
        for candidate, deposit_path in pins:
            content = candidate.read_bytes()
            _write(output_dir, deposit_path, content)
            members.append(_member(deposit_path, content, ROLE_SOURCE_PIN))

        for path in generator_sources:
            content = path.read_bytes()
            deposit_path = f"scripts/{path.name}"
            _write(output_dir, deposit_path, content)
            members.append(_member(deposit_path, content, ROLE_RESULT_GENERATOR))

        expected_dir = output_dir / "expected_outputs"
        for name, digest in write_expected_outputs(session, expected_dir, generators).items():
            content = (expected_dir / name).read_bytes()
            assert hashlib.sha256(content).hexdigest() == digest
            members.append(_member(f"expected_outputs/{name}", content, ROLE_EXPECTED_OUTPUT))

        reproduce = _reproduce_document(tag=release.tag, source=source, archive_member=archive_member_path)
        _write(output_dir, "REPRODUCE.md", reproduce)
        members.append(_member("REPRODUCE.md", reproduce, ROLE_PROTOCOL))

        accounts = _accounts_document(session, allowlist)
        _write(output_dir, "ACCOUNTS.md", accounts)
        members.append(_member("ACCOUNTS.md", accounts, ROLE_PRIVACY))

        members.sort(key=lambda member: member.path)
        document = {
            "schema": DEPOSIT_SCHEMA,
            "release": {
                "tag": release.tag,
                "ref": release.public_ref,
                "manifest_content_sha256": manifest.content_sha256,
            },
            "source": source.as_document(),
            "privacy": {"author_accounts": sorted(allowlist)},
            "members": [member.as_document() for member in members],
        }
        _write(output_dir, MANIFEST_NAME, canonical_json(document).encode("utf-8") + b"\n")
    except BaseException:
        if created_here:
            shutil.rmtree(output_dir, ignore_errors=True)
        raise

    return DepositResult(path=output_dir, manifest=document, source=source, members=members)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DepositVerificationReport:
    path: Path
    release_tag: str | None
    members_checked: int
    problems: list[str]
    database_checked: bool

    @property
    def ok(self) -> bool:
        return not self.problems


def _walk_files(root: Path) -> Iterable[str]:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path.relative_to(root).as_posix()


def verify_deposit(path: Path, session: Session | None = None) -> DepositVerificationReport:
    """Re-hash every member; with ``session``, also check the deposit against a database.

    Offline (``session=None``): every member exists and hashes to its
    recorded digest and size; no undeclared file is present; every role is
    present in its required count; the release manifest member hashes to the
    recorded release digest and its artifact entries match the artifact
    members; the archive passes :func:`verify_archive`.

    With a session (``--db``): the tagged release exists, its frozen manifest
    and artifact bytes equal the deposit's, the database revision equals the
    bound Alembic head, and every account in the database is in the deposit's
    author list.
    """
    root = Path(path)
    problems: list[str] = []
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        return DepositVerificationReport(root, None, 0, [f"{MANIFEST_NAME} is missing"], session is not None)
    try:
        document = json.loads(manifest_path.read_bytes())
    except json.JSONDecodeError as exc:
        return DepositVerificationReport(root, None, 0, [f"{MANIFEST_NAME} is not valid JSON: {exc}"], session is not None)
    if not isinstance(document, dict) or document.get("schema") != DEPOSIT_SCHEMA:
        problems.append(f"schema is {document.get('schema') if isinstance(document, dict) else None!r}, expected {DEPOSIT_SCHEMA!r}")
        return DepositVerificationReport(root, None, 0, problems, session is not None)

    release_block = document.get("release") if isinstance(document.get("release"), dict) else {}
    release_tag = release_block.get("tag") if isinstance(release_block.get("tag"), str) else None
    source_block = document.get("source") if isinstance(document.get("source"), dict) else {}
    privacy_block = document.get("privacy") if isinstance(document.get("privacy"), dict) else {}
    raw_members = document.get("members")
    if not isinstance(raw_members, list) or not raw_members:
        problems.append("members list is missing or empty")
        return DepositVerificationReport(root, release_tag, 0, problems, session is not None)

    members: dict[str, dict[str, Any]] = {}
    checked = 0
    for entry in raw_members:
        if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) for k in ("path", "sha256", "role")):
            problems.append(f"malformed member entry: {entry!r}")
            continue
        relative = entry["path"]
        if relative in members:
            problems.append(f"duplicate member {relative}")
            continue
        members[relative] = entry
        target = root / relative
        if not target.is_file():
            problems.append(f"{relative}: missing")
            continue
        content = target.read_bytes()
        checked += 1
        digest = hashlib.sha256(content).hexdigest()
        if digest != entry["sha256"]:
            problems.append(f"{relative}: hashes to {digest}, recorded {entry['sha256']}")
        if entry.get("bytes") != len(content):
            problems.append(f"{relative}: is {len(content)} bytes, recorded {entry.get('bytes')}")

    declared = set(members) | {MANIFEST_NAME}
    undeclared = [relative for relative in _walk_files(root) if relative not in declared]
    if undeclared:
        problems.append(f"undeclared files present: {undeclared}")

    by_role: dict[str, list[str]] = {}
    for relative, entry in members.items():
        by_role.setdefault(entry["role"], []).append(relative)
    for role, required in REQUIRED_ROLE_COUNTS.items():
        count = len(by_role.get(role, []))
        if required is None and count == 0:
            problems.append(f"role {role}: no member")
        elif required is not None and count != required:
            problems.append(f"role {role}: {count} member(s), expected {required}")
    unknown_roles = sorted(set(by_role) - set(REQUIRED_ROLE_COUNTS))
    if unknown_roles:
        problems.append(f"unknown member roles: {unknown_roles}")

    manifest_members = by_role.get(ROLE_RELEASE_MANIFEST, [])
    artifact_members = {
        relative.removeprefix("release/"): entry for relative, entry in members.items() if entry["role"] == ROLE_RELEASE_ARTIFACT
    }
    if len(manifest_members) == 1 and (root / manifest_members[0]).is_file():
        entry = members[manifest_members[0]]
        recorded = release_block.get("manifest_content_sha256")
        if entry["sha256"] != recorded:
            problems.append(
                f"{manifest_members[0]}: hashes to {entry['sha256']}, release.manifest_content_sha256 is {recorded}"
            )
        try:
            release_document = json.loads((root / manifest_members[0]).read_bytes())
            listed = {a["path"]: a["sha256"] for a in release_document.get("artifacts", [])}
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
            problems.append(f"{manifest_members[0]}: unreadable release document: {exc}")
            listed = {}
        emitted = {path: entry["sha256"] for path, entry in artifact_members.items()}
        if listed and listed != emitted:
            problems.append(
                f"release artifact members {sorted(emitted)} do not match the release manifest's artifacts {sorted(listed)}"
            )

    for relative in by_role.get(ROLE_EVIDENCE_ARCHIVE, []):
        if (root / relative).is_file():
            report = verify_archive(root / relative)
            problems.extend(f"{relative}: {problem}" for problem in report.problems)
            if report.ok and source_block.get("alembic_head") not in report.database_revisions:
                problems.append(
                    f"{relative}: archive revision {report.database_revisions} differs from the bound "
                    f"Alembic head {source_block.get('alembic_head')!r}"
                )

    if session is not None:
        _verify_against_database(
            session,
            release_tag=release_tag,
            release_block=release_block,
            source_block=source_block,
            privacy_block=privacy_block,
            artifact_members=artifact_members,
            root=root,
            problems=problems,
        )

    return DepositVerificationReport(root, release_tag, checked, problems, session is not None)


def _verify_against_database(
    session: Session,
    *,
    release_tag: str | None,
    release_block: Mapping[str, Any],
    source_block: Mapping[str, Any],
    privacy_block: Mapping[str, Any],
    artifact_members: Mapping[str, Mapping[str, Any]],
    root: Path,
    problems: list[str],
) -> None:
    if release_tag is None:
        problems.append("database: no release tag recorded to look up")
        return
    release = session.scalars(select(DatasetRelease).where(DatasetRelease.tag == release_tag)).one_or_none()
    if release is None:
        problems.append(f"database: no release tagged {release_tag!r}")
        return
    if release.public_ref != release_block.get("ref"):
        problems.append(f"database: release ref {release.public_ref} differs from recorded {release_block.get('ref')}")
    manifest = load_manifest(session, release)
    if manifest is None:
        problems.append(f"database: release {release_tag!r} has no frozen manifest")
        return
    report = verify_release(session, release)
    problems.extend(f"database: {problem}" for problem in report.problems)
    if manifest.content_sha256 != release_block.get("manifest_content_sha256"):
        problems.append(
            f"database: manifest digest {manifest.content_sha256} differs from recorded "
            f"{release_block.get('manifest_content_sha256')}"
        )
    for row in manifest.artifacts:
        entry = artifact_members.get(row.path)
        if entry is None:
            problems.append(f"database: artifact {row.path} has no deposit member")
            continue
        target = root / "release" / row.path
        if target.is_file() and target.read_bytes() != bytes(row.content):
            problems.append(f"database: artifact {row.path} bytes differ from the deposit member")
    revision = versions.alembic_revision(session)
    if revision != source_block.get("alembic_head"):
        problems.append(
            f"database: revision {revision} differs from the bound Alembic head {source_block.get('alembic_head')!r}"
        )
    allowed = privacy_block.get("author_accounts")
    if not isinstance(allowed, list):
        problems.append("privacy.author_accounts is missing")
    else:
        strangers = sorted(set(session.scalars(select(AppUser.username))) - set(allowed))
        if strangers:
            problems.append("database: accounts outside the deposit's author list: " + ", ".join(strangers))


__all__ = [
    "DEPOSIT_SCHEMA",
    "MANIFEST_NAME",
    "REQUIRED_ROLE_COUNTS",
    "RESIDUAL_RISK_PARAGRAPH",
    "SOURCE_PINS",
    "AccountNotAllowlistedError",
    "ActorOutsideAllowlistError",
    "DepositError",
    "DepositResult",
    "DepositVerificationReport",
    "DirtyTreeError",
    "Member",
    "NoExactTagError",
    "OutputNotEmptyError",
    "ReleaseNotPublishableError",
    "RevisionMismatchError",
    "SourceBinding",
    "SourcePinMissingError",
    "UnknownVersionError",
    "actor_columns",
    "alembic_script_head",
    "assert_publishable",
    "collect_release_members",
    "source_binding",
    "verify_deposit",
    "write_deposit",
]
