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
