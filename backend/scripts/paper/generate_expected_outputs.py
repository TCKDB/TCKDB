"""Run every registered manuscript-number generator against a database.

Writes ``<name>.json`` (canonical JSON) and ``<name>.md`` per generator into
``--output-dir``. The database is the one ``Settings.database_url`` resolves
from ``DB_USER``, ``DB_PASSWORD``, ``DB_HOST``, ``DB_PORT`` and ``DB_NAME``.

A reproducer runs this against the database restored from the deposit's
archive and byte-compares the result with the deposit's ``expected_outputs/``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.deps import SessionLocal  # noqa: E402
from app.services.deposit.expected_outputs import write_expected_outputs  # noqa: E402
from scripts.paper.registry import GENERATORS  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--only",
        action="append",
        default=None,
        help="Run only this generator (repeatable). Default: every registered generator.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    selected = GENERATORS
    if args.only:
        unknown = sorted(set(args.only) - set(GENERATORS))
        if unknown:
            print(f"error: unknown generator(s) {unknown}; known: {sorted(GENERATORS)}", file=sys.stderr)
            return 2
        selected = {name: GENERATORS[name] for name in args.only}
    with SessionLocal() as session:
        digests = write_expected_outputs(session, args.output_dir, selected)
        session.rollback()
    for path, digest in digests.items():
        print(f"{digest}  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
