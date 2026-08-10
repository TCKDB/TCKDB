#!/usr/bin/env python
"""Generate ``clients/python/src/tckdb_client/rejection_codes.py``.

Why generate it
---------------
The codes exist on the server and nowhere else, so a client hard-codes
strings -- and a typo in a hard-coded string does not fail, it silently
never matches. ``if err.code == "reaction_mass_balace_failed":`` is a
branch that will never be taken, on the day the deposit it was written
for finally arrives. Generating the enum from the register moves that
from a runtime non-event to an import error, and keeps the two in step
by construction rather than by discipline.

What goes in, and what deliberately does not
--------------------------------------------
Only the codes a *refusal* carries: the ``error_envelope`` channel (HTTP
422, refused before anything was written) and the rejection codes
declared on database constraints (HTTP 409, refused by a position
PostgreSQL holds). ``upload_warning`` codes arrive alongside an
*accepted* upload, in a different part of the response, and putting them
in an enum named for rejections would invite a client to treat an
accepted deposit as a failed one. ``trust_label`` codes are not refusals
at all.

Nothing else is emitted. No file paths, no line numbers, no docstrings,
no counts, no prose lifted from ``asserts``. That is a deliberate
constraint on the *output*, not laziness about it: this file is compared
against its committed copy in CI, so whatever appears in it becomes a
thing that can turn the gate red. The register generator learned this
the expensive way -- it keyed on line numbers, a comment added elsewhere
shifted a check from 120 to 121, and the gate fired on a digit. A gate
that fires on cosmetic movement gets regenerated reflexively without
anyone reading the diff, which is exactly how a real drift eventually
lands unnoticed. So this file changes when, and only when, the set of
codes or the status carrying them changes -- which is precisely when a
client author needs to look.

Usage::

    conda run -n tckdb_env python backend/scripts/generate_client_rejection_codes.py
    conda run -n tckdb_env python backend/scripts/generate_client_rejection_codes.py --check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.scientific_checks import CodeChannel  # noqa: E402
from app.scientific_checks.declarations import (  # noqa: E402
    constraint_rejections,
    register,
)

OUTPUT = (
    REPO_ROOT
    / "clients"
    / "python"
    / "src"
    / "tckdb_client"
    / "rejection_codes.py"
)

GENERATOR = "backend/scripts/generate_client_rejection_codes.py"

_HEADER = f'''"""Machine-readable codes TCKDB reports when it refuses a deposit.

**Generated. Do not edit by hand.** Regenerate with
``conda run -n tckdb_env python {GENERATOR}``;
``backend/tests/api/test_client_rejection_codes_generated.py`` fails if
this file and the server's scientific check register disagree.

Every member here is a position TCKDB takes about chemistry -- a claim a
referee could argue with -- and each one is proved to arrive in a real
HTTP response body by a test on the server side. The register that
generates this file is rendered for humans at
``docs/guides/scientific_check_register.md``, which is where to look for
what a code asserts, why it refuses rather than warns, and what the
escape hatch is for legitimate chemistry it would otherwise reject.

Deliberately absent: warning codes, which arrive alongside an *accepted*
upload, and trust labels, which are applied at read time and refuse
nothing. Both would be misread as failures under this name.

Using it::

    from tckdb_client import RejectionCode, rejection_code

    try:
        client.upload_reaction(payload)
    except TCKDBHTTPError as exc:
        match rejection_code(exc.code):
            case RejectionCode.REACTION_MASS_BALANCE_FAILED:
                raise                      # the deposit is wrong; do not retry
            case RejectionCode.SPECIES_GEOMETRY_COMPOSITION_MISMATCH:
                payload = repair(payload)  # recoverable
            case None:
                raise                      # a code this client does not know

Use :func:`rejection_code` rather than ``RejectionCode(exc.code)``. A
server is routinely newer than the client pinned against it, and a code
added since this file was generated must not turn a handled refusal into
an unhandled ``ValueError``.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "CONFLICT_REJECTION_CODES",
    "RejectionCode",
    "VALIDATION_REJECTION_CODES",
    "rejection_code",
]


class RejectionCode(str, Enum):
    """A code TCKDB reports in the ``code`` field of a refusal body.

    ``str`` subclass so a member compares equal to the wire string and
    can be used wherever the raw code was.
    """

'''

_FOOTER = '''

def rejection_code(value: object) -> RejectionCode | None:
    """Return the member for *value*, or ``None`` if this client cannot name it.

    ``None`` covers three genuinely different situations, and a caller
    should treat all three the same way -- as a refusal it does not have
    a specific branch for: the server is newer than this client and sent
    a code added since; the refusal was not a scientific one (a rate
    limit, an authentication failure, a generic conflict); or there was
    no code at all.
    """
    if not isinstance(value, str):
        return None
    try:
        return RejectionCode(value)
    except ValueError:
        return None
'''


def _member(code: str) -> str:
    return code.upper()


def render() -> str:
    checks = register()

    validation = sorted(
        {
            code
            for check in checks
            if check.channel is CodeChannel.error_envelope
            for code in check.codes
        }
    )
    conflict = sorted({code for code, _detail in constraint_rejections().values()})

    if not validation:
        raise SystemExit(
            "The register declares no error_envelope codes. Generating an "
            "empty enum would quietly remove a published client API; refusing."
        )

    lines = [_HEADER]
    for code in sorted(set(validation) | set(conflict)):
        lines.append(f'    {_member(code)} = "{code}"\n')

    lines.append(
        "\n\n#: Codes carried by an HTTP 422: the payload was refused before\n"
        "#: anything was written, so nothing was stored and a corrected\n"
        "#: payload may be sent again under the same idempotency key.\n"
        "VALIDATION_REJECTION_CODES: frozenset[RejectionCode] = frozenset(\n"
        "    {\n"
    )
    for code in validation:
        lines.append(f"        RejectionCode.{_member(code)},\n")
    lines.append("    }\n)\n")

    lines.append(
        "\n#: Codes carried by an HTTP 409: a position PostgreSQL holds\n"
        "#: refused the write. A code may appear in both sets -- the same\n"
        "#: claim can be enforced at the wire boundary and again in the\n"
        "#: schema, and which one fires depends on the write path, not on\n"
        "#: what the depositor did wrong.\n"
        "CONFLICT_REJECTION_CODES: frozenset[RejectionCode] = frozenset(\n"
        "    {\n"
    )
    for code in conflict:
        lines.append(f"        RejectionCode.{_member(code)},\n")
    lines.append("    }\n)\n")

    lines.append(_FOOTER)
    return "".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the committed file is out of date",
    )
    args = parser.parse_args()

    rendered = render()
    if args.check:
        if not OUTPUT.exists():
            print(f"{OUTPUT} does not exist; run without --check.", file=sys.stderr)
            return 1
        if OUTPUT.read_text() != rendered:
            print(
                f"{OUTPUT} is out of date with the scientific check register. "
                f"Regenerate it with `python {GENERATOR}` "
                "and bump the client version in clients/python/pyproject.toml.",
                file=sys.stderr,
            )
            return 1
        print(f"{OUTPUT} is up to date.")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered)
    print(f"Wrote {OUTPUT}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
