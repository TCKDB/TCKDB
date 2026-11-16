"""Build or verify a ``tckdb.deposit`` publication deposit.

``build`` reads one published dataset release from the database (never over
HTTP), writes the evidence archive from the same session, copies the source
pins and the package tree of the registry named by ``--generators`` (importable from
``--generator-root``), runs the registry to produce
``expected_outputs/``, and binds everything by SHA-256 in ``MANIFEST.json``.
It refuses on a dirty tree, an untagged commit, an unknown package version,
a database not at the Alembic script head, a release that does not verify,
and any account or actor outside ``--author-account``.

``verify`` re-hashes a deposit offline; ``--db`` additionally checks it
against the database the environment points at (a restored one, typically).

Exit codes: 0 ok; 1 other error; 2 release not publishable; 3 source
(dirty tree, no tag, unknown version, missing pin); 4 Alembic revision
mismatch; 5 privacy (account or actor outside the allowlist); 6 output not
empty; 7 verification found problems.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.deps import SessionLocal  # noqa: E402
from app.services.deposit import DepositError, checksum_lines, verify_deposit, write_deposit  # noqa: E402
from app.services.deposit.build import MANIFEST_NAME  # noqa: E402
from app.services.deposit.generator_spec import load_deposit_generators  # noqa: E402

EXIT_VERIFICATION_FAILED = 7


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Assemble a deposit for one published release.")
    build.add_argument("--release", required=True, help="dataset release tag, e.g. 2026.07.0")
    build.add_argument("--output", type=Path, required=True, help="directory to create (must not have content)")
    build.add_argument(
        "--author-account",
        action="append",
        default=[],
        metavar="USERNAME",
        help="an account whose data may be deposited (repeatable; every account must be listed)",
    )
    build.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="checkout to bind the deposit to (default: the one this script lives in)",
    )

    build.add_argument(
        "--generators",
        metavar="MODULE:ATTR",
        default=None,
        help="the generator registry: a mapping of name to generator, importable as module:attr (required)",
    )
    build.add_argument(
        "--generator-root",
        type=Path,
        default=None,
        help="directory the registry imports from; its top-level package is copied into scripts/ (required)",
    )

    verify = subparsers.add_parser("verify", help="Re-hash a deposit; with --db, check it against the database.")
    verify.add_argument("deposit", type=Path)
    verify.add_argument("--db", action="store_true", help="also compare with the database the environment points at")

    checksums = subparsers.add_parser("checksums", help="Print '<sha256>  <path>' per member, for sha256sum -c.")
    checksums.add_argument("deposit", type=Path)
    return parser.parse_args(argv)


def _checksums(args: argparse.Namespace) -> int:
    document = json.loads((args.deposit / MANIFEST_NAME).read_bytes())
    sys.stdout.write(checksum_lines(document))
    return 0


def _build(args: argparse.Namespace) -> int:
    load_deposit_generators(args.generators, args.generator_root)  # refuse before touching the database
    with SessionLocal() as session:
        result = write_deposit(
            session,
            release_tag=args.release,
            output_dir=args.output,
            author_accounts=args.author_account,
            repo_root=args.repo_root,
            generator_spec=args.generators,
            generator_root=args.generator_root,
        )
        session.rollback()  # the build reads; release the archive's snapshot locks promptly
    source = result.manifest["source"]
    if result.source.untracked_paths:
        listed = ", ".join(result.source.untracked_paths[:10])
        extra = len(result.source.untracked_paths) - 10
        print(
            f"warning: {len(result.source.untracked_paths)} untracked path(s) in the checkout are not bound "
            f"by the deposit: {listed}" + (f" (+{extra} more)" if extra > 0 else ""),
            file=sys.stderr,
        )
    print(f"Wrote {result.path}")
    print(f"Release {args.release}  commit {source['git_commit']}  tag {source['git_tag']}  alembic {source['alembic_head']}")
    print(f"Members: {len(result.members)}")
    return 0


def _verify(args: argparse.Namespace) -> int:
    if args.db:
        with SessionLocal() as session:
            report = verify_deposit(args.deposit, session=session)
            session.rollback()
    else:
        report = verify_deposit(args.deposit)
    print(f"Checked {report.path}  release {report.release_tag}  members hashed: {report.members_checked}  database: {'yes' if report.database_checked else 'no'}")
    if report.ok:
        print("Deposit verified.")
        return 0
    for problem in report.problems:
        print(f"problem: {problem}", file=sys.stderr)
    return EXIT_VERIFICATION_FAILED


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.command == "build":
            return _build(args)
        if args.command == "checksums":
            return _checksums(args)
        return _verify(args)
    except DepositError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
