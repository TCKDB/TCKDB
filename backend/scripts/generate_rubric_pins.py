#!/usr/bin/env python
"""Pin each rubric's fingerprint beside its version.

Writes ``backend/app/services/trust/rubric_pins.json``. See ``app/services/trust/rubric_pins.py`` for what the
fingerprint covers and why.

Usage::

    conda run -n tckdb_env python backend/scripts/generate_rubric_pins.py
    conda run -n tckdb_env python backend/scripts/generate_rubric_pins.py --check

``--check`` writes nothing and exits non-zero when the pins disagree with the code (a rubric's checks changed without a
version bump, or a version was bumped without re-pinning). It is what CI runs.

Writing refuses to re-pin a rubric whose fingerprint changed under an unchanged version: bump the version first.

Two overrides, both recorded in the pins file so a reviewer sees them:

* ``--allow-unbumped NAME --reason TEXT`` re-pins one rubric without a bump (a change that decides nothing new) and
  appends ``{name, version, reason}`` to ``unbumped_overrides``. The reason is required.
* ``--repin-for-python`` re-pins every rubric after a Python version change (the AST dump differs between minors). It
  refuses unless the *only* thing that moved is the Python version, i.e. the pins were made under another minor and the
  rubrics' versions are unchanged. It records ``{python_repin: {from, to}}``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.services.trust import rubric_pins  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="exit non-zero if the committed pins are out of date")
    parser.add_argument("--allow-unbumped", metavar="NAME", action="append", default=[], help="re-pin NAME without a bump")
    parser.add_argument("--reason", help="why an unbumped re-pin is safe (required with --allow-unbumped)")
    parser.add_argument("--repin-for-python", action="store_true", help="re-pin after a Python minor version change")
    args = parser.parse_args(argv)

    path = rubric_pins.PINS_PATH
    document = rubric_pins.load_document(path) if path.exists() else {"pins": {}, "python": rubric_pins.python_minor()}
    existing = document["pins"]
    overrides = list(document.get("unbumped_overrides", []))
    pinned_python = document["python"]

    if args.repin_for_python:
        if pinned_python == rubric_pins.python_minor():
            print("the pins are already for this Python version", file=sys.stderr)
            return 1
        # Under a different Python the fingerprints cannot be compared, so the only thing that may be checked is that
        # no rubric's version moved. A code change landing in the same commit as the Python change is not detectable
        # here: re-pin in a commit that changes nothing else.
        fingerprints = rubric_pins.current_fingerprints()
        moved = [n for n, (v, _) in fingerprints.items() if str(v) not in existing.get(n, {})]
        if moved:
            print(f"versions moved for {', '.join(moved)}: re-pin normally, not for a Python change", file=sys.stderr)
            return 1
        overrides.append({"python_repin": {"from": pinned_python, "to": rubric_pins.python_minor()}})
        path.write_text(rubric_pins.render_pins(fingerprints, overrides), encoding="utf-8")
        print(f"re-pinned for Python {rubric_pins.python_minor()} (was {pinned_python})")
        return 0

    if args.check and pinned_python != rubric_pins.python_minor():
        print(
            f"rubric pins were made under Python {pinned_python}; this is {rubric_pins.python_minor()}. Run under "
            "tckdb_env (the version CI uses), or re-pin after a Python upgrade with --repin-for-python.",
            file=sys.stderr,
        )
        return 1

    fingerprints = rubric_pins.current_fingerprints()

    if args.check:
        if document.get("algorithm") != rubric_pins.ALGORITHM:
            print("the pins were made by an older fingerprint algorithm: run generate_rubric_pins.py", file=sys.stderr)
            return 1
        problems = rubric_pins.diagnose(fingerprints, existing)
        for problem in problems:
            print(problem, file=sys.stderr)
        if problems:
            return 1
        print("rubric pins are up to date")
        return 0

    unbumped = rubric_pins.unbumped_changes(fingerprints, existing)
    if document.get("algorithm") != rubric_pins.ALGORITHM:
        # The fingerprinting changed (not a rubric), so every fingerprint moved. Re-pin all, changing nothing else, and
        # say so in the pins file: a rule change landing in the same commit would otherwise hide in this re-pin.
        unbumped = []
        if "algorithm" in document:  # a first pin has nothing to re-pin from
            overrides.append({"algorithm_repin": {"from": document["algorithm"], "to": rubric_pins.ALGORITHM}})
    refused = [n for n in unbumped if n not in args.allow_unbumped]
    if refused:
        for name in refused:
            print(
                f"{name}: checks or grading changed but the version did not. Bump its version, then re-run. "
                "(--allow-unbumped NAME --reason TEXT overrides, and is recorded.)",
                file=sys.stderr,
            )
        return 1
    if args.allow_unbumped:
        if not (args.reason or "").strip():
            print("--allow-unbumped needs a non-empty --reason", file=sys.stderr)
            return 1
        for name in args.allow_unbumped:
            if name in unbumped:
                overrides.append({"name": name, "version": fingerprints[name][0], "reason": args.reason.strip()})
    path.write_text(rubric_pins.render_pins(fingerprints, overrides, python=None), encoding="utf-8")
    print(f"wrote {path} ({len(fingerprints)} rubrics)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
