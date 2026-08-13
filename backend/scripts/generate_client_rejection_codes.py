#!/usr/bin/env python
"""Generate ``clients/python/src/tckdb_client/rejection_codes.py``.

Why generate it
---------------
The codes exist on the server and nowhere else, so a client hard-codes
strings -- and a typo in a hard-coded string does not fail, it silently
never matches. ``if err.code == "reaction_mass_balace_failed":`` is a
branch that will never be taken, on the day the deposit it was written
for finally arrives. Generating the enum from the server's own
enumeration moves that from a runtime non-event to an import error, and
keeps the two in step by construction rather than by discipline.

What goes in, and what deliberately does not
--------------------------------------------
The source is :mod:`app.api.code_catalogue` -- the enumeration of every
code the API can emit -- and not the scientific check register, which
this generator used to read.

That change is the point of the file. The register enumerates positions
about chemistry; its inclusion test is "could this check be wrong in an
interesting way?", and it correctly refuses a role/type mismatch, an
ownership refusal and ``missing_filter``. Generating the client enum from
it meant "correctly excluded from the register" also meant "a client
cannot import this code" -- which was true of two dozen live read-API
codes. One list cannot be both the scientific claim and the wire
contract without one of them being diluted.

Membership is now derived from :attr:`~app.api.code_catalogue.ApiCode.is_client_facing`:
a 4xx, and not a generic fallback (which repeats the status) or an
accidental prefix (a function name that landed in the code position and
is not a contract at all). ``upload_warning`` codes still do not appear:
they arrive alongside an *accepted* upload, and putting them in an enum
named for rejections would invite a client to treat an accepted deposit
as a failed one. Nor do trust labels, which refuse nothing, nor 5xx
codes, which refuse nothing the caller did.

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

from app.api.code_catalogue import client_facing  # noqa: E402

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
this file and the server's code catalogue disagree.

Every member is a refusal TCKDB can report with an HTTP 4xx: a deposit
that contradicts chemistry, a query that names a filter the endpoint does
not have, a calculation the depositor does not own, a handle of the wrong
type. They are not all scientific, and that is deliberate -- the subset
that *is* a position about chemistry is documented for humans at
``docs/guides/scientific_check_register.md``, which is where to look for
what such a code asserts, why it refuses rather than warns, and what the
escape hatch is for legitimate chemistry it would otherwise reject. A
code absent from that document is still a real refusal; it simply is not
a claim a referee could argue with.

Deliberately absent: warning codes, which arrive alongside an *accepted*
upload, and trust labels, which are applied at read time and refuse
nothing -- both would be misread as failures under this name. Also absent
are the ``http_<status>`` fallbacks and the generic ``validation_error``
family, which carry nothing the status line does not, and 5xx codes,
which refuse nothing the caller did.

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

:data:`REJECTION_STATUSES` gives the HTTP status each member arrives at,
which is the retry advice: 422 means nothing was written and a corrected
payload may be resent, 409 means the write reached the database, 404
means the record named is not there, 426 means upgrade this package, and
429 is the one status where retrying the same request unchanged is the
right thing to do.
"""

from __future__ import annotations

from enum import Enum

__all__ = [
    "CONFLICT_REJECTION_CODES",
    "REJECTION_STATUSES",
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
    entries = client_facing()

    validation = sorted({entry.code for entry in entries if entry.status == 422})
    conflict = sorted({entry.code for entry in entries if entry.status == 409})

    if not validation:
        raise SystemExit(
            "The catalogue declares no client-facing 422 codes. Generating "
            "an empty enum would quietly remove a published client API; "
            "refusing."
        )

    lines = [_HEADER]
    # Every client-facing code is a member, including the ones carried by a
    # status the two frozensets below do not cover (404, 426, 429 ...).
    # Membership is what makes a code importable, which is the whole point;
    # the sets are retry advice for the two statuses that have any.
    for code in sorted({entry.code for entry in entries}):
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
        "\n#: Codes carried by an HTTP 409: the write reached the database\n"
        "#: and a position it holds refused it, or an idempotency key was\n"
        "#: reused. A code may appear in both sets -- the same claim can be\n"
        "#: enforced at the wire boundary and again in the schema, and which\n"
        "#: one fires depends on the write path, not on what the depositor\n"
        "#: did wrong. Members in neither set are carried by some other 4xx\n"
        "#: (404, 426, 429); read the HTTP status for those.\n"
        "CONFLICT_REJECTION_CODES: frozenset[RejectionCode] = frozenset(\n"
        "    {\n"
    )
    for code in conflict:
        lines.append(f"        RejectionCode.{_member(code)},\n")
    lines.append("    }\n)\n")

    lines.append(
        "\n#: The HTTP status(es) each code arrives at. Every member appears\n"
        "#: here, including the ones carried by a status with no named set\n"
        "#: above -- a 404 that names a missing record, a 426 that asks the\n"
        "#: client to upgrade, a 429 that asks it to wait. A member with no\n"
        "#: status would be a code a consumer is told about with no\n"
        "#: indication of what to do next, which is what\n"
        "#: ``tests/test_rejection_codes.py`` refuses.\n"
        "#:\n"
        "#: A frozenset rather than an int because a claim enforced at the\n"
        "#: wire boundary and again in the schema reports the same code from\n"
        "#: both, at two different statuses.\n"
        "REJECTION_STATUSES: dict[RejectionCode, frozenset[int]] = {\n"
    )
    statuses: dict[str, set[int]] = {}
    for entry in entries:
        statuses.setdefault(entry.code, set()).add(entry.status)
    for code in sorted(statuses):
        rendered = ", ".join(str(status) for status in sorted(statuses[code]))
        lines.append(
            f"    RejectionCode.{_member(code)}: frozenset({{{rendered}}}),\n"
        )
    lines.append("}\n")

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
                f"{OUTPUT} is out of date with app/api/code_catalogue.py. "
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
