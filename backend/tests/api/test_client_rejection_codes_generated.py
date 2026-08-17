"""The generated client enum must not drift from the code catalogue.

The staleness gate, in the same shape as the one guarding
``docs/guides/scientific_check_register.md``: regenerate in memory,
compare against the committed file, fail if they differ. This lives on
the server side because only the backend can import the catalogue --
``clients/python`` has no dependency on ``app`` and must not gain one.

The enum is generated from :mod:`app.api.code_catalogue`, not from the
scientific check register. Both are still checked below: the catalogue
because it is the source, and the register because every scientific
refusal code must survive the change of source — that containment is what
``test_api_code_catalogue.py`` guards from the other direction.

What this buys a client author is narrow and specific. Without it the
codes exist on the server and are transcribed by hand into ARC and every
other consumer, where a typo is not an error: ``exc.code ==
"reaction_mass_balace_failed"`` compiles, runs, and simply never matches,
so the branch written for an unbalanced deposit silently never fires. A
generated enum turns that into an ``AttributeError`` at import.

The gate is deliberately narrow too. The generated file carries codes and
statuses and nothing else -- no prose, no anchors, no counts -- so it
cannot fire on a reworded sentence or a moved line. See the generator's
docstring for why that restraint is the point rather than an omission.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from app.scientific_checks import CodeChannel
from app.scientific_checks.declarations import constraint_rejections, register

REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATOR = REPO_ROOT / "backend" / "scripts" / "generate_client_rejection_codes.py"
GENERATED = (
    REPO_ROOT
    / "clients"
    / "python"
    / "src"
    / "tckdb_client"
    / "rejection_codes.py"
)


def test_generated_enum_is_in_sync_with_the_register() -> None:
    assert GENERATED.exists(), f"{GENERATED} has not been generated"
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"{GENERATED} is out of date with the scientific check register.\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_every_refusal_code_the_register_declares_is_exported() -> None:
    """Read as a client would, not by re-running the generator.

    The sync test above compares two renderings of the same function, so
    it cannot notice the generator dropping a whole channel -- both sides
    would drop it together. This one derives the expected set
    independently from the register and looks for each code as a literal
    in the file a client actually imports.
    """
    expected = {
        code
        for check in register()
        if check.channel is CodeChannel.error_envelope
        for code in check.codes
    }
    expected |= {code for code, _detail in constraint_rejections().values()}
    assert expected, "no refusal codes at all -- the guard would pass vacuously"

    published = GENERATED.read_text()
    missing = sorted(code for code in expected if f'"{code}"' not in published)
    assert not missing, (
        f"These codes are declared to reach a client but are absent from "
        f"{GENERATED.name}: {missing}. A consumer would have to hard-code them, "
        "and a hard-coded code that is wrong never matches rather than failing."
    )


def test_every_constraint_rejection_is_in_the_conflict_set() -> None:
    """A 409 code must land in the set that advises on 409s.

    Membership in ``RejectionCode`` is not enough: a client reading
    ``CONFLICT_REJECTION_CODES`` to decide whether the write reached the
    database needs the code to be *in* it. Two codes -- the atom map's
    element conservation and its bijectivity -- are enforced both at the
    wire boundary and by a check constraint, and a catalogue that listed
    each of them once, at 422, dropped both from this set without
    changing any code's value. Nothing noticed until a 409 was observed
    arriving in a real response.
    """
    published = GENERATED.read_text()
    conflict_block = published.split("CONFLICT_REJECTION_CODES")[-1]
    expected = {code for code, _detail in constraint_rejections().values()}
    assert expected, "no constraint rejections -- the guard would be vacuous"

    missing = sorted(
        code for code in expected if code.upper() not in conflict_block
    )
    assert not missing, (
        f"These codes are refused by PostgreSQL and reported as a 409, but "
        f"CONFLICT_REJECTION_CODES does not contain them: {missing}"
    )


def test_every_client_facing_catalogue_code_is_exported() -> None:
    """The same read, against the source the enum is now generated from.

    Without this the test above would be the only independent check, and
    it only covers the scientific subset -- so the generator could drop
    every read-API code and stay green, which is the state this work was
    opened to end.
    """
    from app.api.code_catalogue import client_facing

    expected = {entry.code for entry in client_facing()}
    assert len(expected) > 50, (
        f"only {len(expected)} client-facing codes; the catalogue has "
        "stopped enumerating what the API returns"
    )
    published = GENERATED.read_text()
    missing = sorted(code for code in expected if f'"{code}"' not in published)
    assert not missing, (
        f"The catalogue says these reach a client, but {GENERATED.name} does "
        f"not export them: {missing}"
    )


def test_every_never_retryable_code_reaches_the_client_deny_list() -> None:
    """The second export, read the way a client reads it.

    ``NON_RETRYABLE_CODES`` is the only route by which a 5xx code reaches a
    client at all, and it exists because the enum cannot carry one:
    ``is_client_facing`` requires a 4xx, on the correct grounds that a 5xx
    refuses nothing the caller did. The consequence, until #234, was that
    ``artifact_object_missing`` was honest server-side and unusable
    client-side — 502 is in ``tckdb_client``'s default retry set, so the
    client replayed a custody break on a backoff schedule forever.

    Checked against the block rather than the whole file, because the
    codes also appear in the header prose: a substring search over the
    file would pass on the docstring alone while the set was empty.
    """
    from app.api.code_catalogue import never_retryable

    expected = {entry.code for entry in never_retryable()}
    assert expected, (
        "the catalogue declares no never-retryable code, so the deny list "
        "would be empty and the retry layer's fail-safe default would hide "
        "it completely"
    )

    published = GENERATED.read_text()
    assert "NON_RETRYABLE_CODES: frozenset[str] = frozenset(" in published, (
        "the deny-list export is gone from the generated file; a client "
        "importing it would fail, and one that used a stale copy would "
        "resume replaying deterministic failures"
    )
    block = published.split("NON_RETRYABLE_CODES: frozenset[str] = frozenset(")[-1]
    block = block.split(")")[0]

    missing = sorted(code for code in expected if f'"{code}"' not in block)
    assert not missing, (
        f"The catalogue says replaying these can never succeed, but "
        f"NON_RETRYABLE_CODES does not carry them: {missing}. A client would "
        "retry each one to its attempt cap for a condition guaranteed not to "
        "clear."
    )

    # The other direction: nothing in the deny list may also be an enum
    # member. A code cannot simultaneously be a refusal of the caller's
    # request and a 5xx the caller did not cause, and publishing it twice
    # under two meanings is how a consumer ends up branching on the wrong
    # one.
    from app.api.code_catalogue import client_facing

    overlap = expected & {entry.code for entry in client_facing()}
    assert not overlap, (
        f"{sorted(overlap)} is both exported as a RejectionCode member and "
        "listed as never retryable. If a 4xx genuinely needs both, say so "
        "deliberately -- this assertion is here because the two sets "
        "answering different questions is the whole design."
    )


def test_the_generated_file_carries_no_anchor_that_moves_for_free() -> None:
    """Nothing derived from where a check lives, for the reason #119 established.

    A gate keyed on something that moves cosmetically gets regenerated
    reflexively without anyone reading the diff, which is how a real
    drift eventually lands unnoticed. #119's case was a line number: a
    comment added to a service module shifted a check from 120 to 121 and
    turned the tier red on main.

    Two things are forbidden, and the difference matters. A line-number
    anchor moves whenever anything above it does. A path under
    ``backend/app`` or ``schemas`` would be *rendered from the live check
    object*, so it moves whenever a check is relocated -- which is not a
    contract change and must not read as one. Fixed prose naming the
    generator and this guard is neither: it is part of the header, it is
    not derived from anything, and it changes only if somebody edits it.
    """
    published = GENERATED.read_text()

    line_anchors = re.findall(r"\.py:\d+", published)
    assert not line_anchors, (
        f"The generated enum carries line-number anchors: {line_anchors}. "
        "Editing the code above a check would then turn the staleness gate red."
    )

    derived_paths = re.findall(r"\b(?:backend/app|schemas)/\S+\.py", published)
    assert not derived_paths, (
        "The generated enum names the modules checks live in, so moving a "
        f"declaration would fire the gate without any contract changing: "
        f"{derived_paths}"
    )
