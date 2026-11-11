#!/usr/bin/env python
"""Generate ``docs/guides/scientific_check_register.md`` from the declarations.

Every ``file::name`` in the document is resolved from the live function object at generation
time. Anchors carry a qualified name rather than a line number, so a comment added to a declaring
module does not change the document.

Usage::

    conda run -n tckdb_env python backend/scripts/generate_scientific_check_register.py
    conda run -n tckdb_env python backend/scripts/generate_scientific_check_register.py --check

``--check`` regenerates in memory and exits non-zero if the committed document differs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.scientific_checks import (  # noqa: E402
    CheckTier,
    CodeChannel,
    ConstantThreshold,
    DatabaseConstraint,
    DesignPosition,
    ProvenanceThreshold,
    PythonCheck,
    ScientificCheck,
)
from app.scientific_checks.declarations import register  # noqa: E402
from app.scientific_checks.numbering import number_checks  # noqa: E402

OUTPUT = REPO_ROOT / "docs" / "guides" / "scientific_check_register.md"

_TIER_BLURB = {
    CheckTier.block: ("Refuses the payload. Reserved for a definition or a contract no correct calculation could violate."),
    CheckTier.warn: "Accepts the payload and records a machine-readable warning.",
    CheckTier.label: (
        "Labels a stored record at read time without refusing anything, as a `HardFailReason` in the trust evaluator."
    ),
    CheckTier.review: "Referred to `machine_review`. Every cross-check against external reference data is here.",
    CheckTier.structural: (
        "Enforced by the shape of the schema, so a record violating it cannot be represented."
    ),
}

_CHANNEL_BLURB = {
    CodeChannel.error_envelope: (
        "the `code` field of the 422 error body — a client can branch on it"
    ),
    CodeChannel.upload_warning: (
        "the `code` field of an `UploadWarning` returned alongside the "
        "accepted upload, and stored with the record it concerns (or, when no one "
        "record could take it, with its submission), which shows it, in TCKDB's "
        "own wording, under `record_warnings` or `deposit_warnings` on that "
        "record's detail reads and on the per-parent kinetics and thermo reads"
    ),
    CodeChannel.trust_label: (
        "a read-time trust label (`HardFailReason`), not any refusal"
    ),
    CodeChannel.database_constraint: (
        "PostgreSQL only, so the refusal is a 409 rather than a 422 — named, "
        "where the constraint declares a rejection code"
    ),
    CodeChannel.none: "*nothing carries a code*",
}

_PREAMBLE = """# The scientific check register

**Generated. Do not edit by hand.** Regenerate with
`conda run -n tckdb_env python backend/scripts/generate_scientific_check_register.py`.

## What this is

The checks that encode chemistry rather than plumbing: elemental balance, charge
conservation, the imaginary-mode count of a saddle point, the bijectivity of an
atom map. String lengths, enum membership and `multiplicity >= 1` are not listed.
Every `file::name` here is resolved from the live function object at generation
time, and anchors name the function rather than a line.

## How to read a row

- **Asserts** — what the check requires, in one sentence.
- **Tier** — what happens when the check fires.
- **Code**, and **where it reaches a client** — the machine-readable code and
  the channel that carries it. An `error_envelope` code is raised through
  `CodedValidationError` and asserted by an end-to-end test.
- **Enforced at** — `file::qualified_name` for Python, or the constraint or
  trigger name for PostgreSQL, verified against live schema metadata. Where a
  constraint names the 409 code it returns, that code is printed with it.
- **Thresholds** — the numeric lines the check fires on. A constant is printed.
  A provenance-derived threshold is resolved per record from the execution
  provenance it carries, so it is printed as the resolver, the parameter keys
  it reads, the per-protocol table, and what it falls back to when those keys
  are absent.
- **Escape hatch** — how legitimate chemistry the check would otherwise refuse
  is deposited instead.
- **Recorded divergence** — where a check's guarantee is narrower than its name
  suggests.

"""


def _md(text: str) -> str:
    """Render declaration prose as Markdown.

    Declarations are written in the RST flavour the surrounding docstrings
    use, so ``literal`` becomes `literal` on the way out. Longest marker
    first, or the double backticks would be eaten one at a time.
    """
    return text.replace("``", "`")


def _tier_table(checks: list[ScientificCheck]) -> str:
    counts: dict[CheckTier, int] = {}
    for check in checks:
        counts[check.tier] = counts.get(check.tier, 0) + 1
    lines = [
        "## Tiers, and how many entries sit in each",
        "",
        "| Tier | Entries | Meaning |",
        "| --- | --- | --- |",
    ]
    for tier in CheckTier:
        lines.append(f"| `{tier.value}` | {counts.get(tier, 0)} | {_TIER_BLURB[tier]} |")
    lines.append(f"| **total** | **{len(checks)}** | |")
    lines.append("")
    return "\n".join(lines)


def _channel_table(checks: list[ScientificCheck]) -> str:
    """How many entries carry a code a client can act on, by channel."""
    counts: dict[CodeChannel, int] = {}
    for check in checks:
        counts[check.channel] = counts.get(check.channel, 0) + 1
    lines = [
        "## Where a check's code reaches a client",
        "",
        "| Channel | Entries | What a consumer can do with it |",
        "| --- | --- | --- |",
    ]
    for channel in CodeChannel:
        lines.append(
            f"| `{channel.value}` | {counts.get(channel, 0)} | "
            f"{_CHANNEL_BLURB[channel]} |"
        )
    lines.append(f"| **total** | **{len(checks)}** | |")
    lines.append("")
    return "\n".join(lines)


def _render_enforcement(check: ScientificCheck) -> str:
    parts: list[str] = []
    for site in check.enforced_by:
        if isinstance(site, PythonCheck):
            body = f"{site.label} — `{site.location}`"
        elif isinstance(site, DatabaseConstraint):
            body = site.label
            if site.definition:
                body += f"\n  `{site.definition}`"
            if site.rejection_code:
                body += (
                    f"\n  Violating this returns **409 `{site.rejection_code}`** — "
                    f"{_md(site.rejection_detail or '')}"
                )
        elif isinstance(site, DesignPosition):
            body = site.label
        else:  # pragma: no cover - exhaustive over Enforcement
            raise TypeError(f"unknown enforcement site: {site!r}")
        parts.append(_md(f"- {body}"))
    return "\n".join(parts)


def _render_thresholds(check: ScientificCheck) -> list[str]:
    """Render a check's numeric lines, distinguishing the two kinds.

    A constant gets its number. A provenance-derived threshold gets its
    resolver, the keys it reads, its per-protocol table and what happens
    when the provenance is absent.
    """
    if not check.thresholds:
        return []
    lines = ["**Thresholds.**", ""]
    for threshold in check.thresholds:
        if isinstance(threshold, ConstantThreshold):
            lines += [
                f"- `{threshold.name}` = **{threshold.summary}** — a constant "
                f"fixed in code.",
                f"  {_md(threshold.rationale)}",
            ]
        elif isinstance(threshold, ProvenanceThreshold):
            keys = ", ".join(f"`{key}`" for key in threshold.parameter_keys)
            lines += [
                f"- `{threshold.name}` — **not a constant.** Resolved per "
                f"record, in {threshold.unit}, by "
                f"`{threshold.resolver.__qualname__}` "
                f"(`{threshold.location}`) from the recorded execution "
                f"provenance {keys}.",
                f"  {_md(threshold.rationale)}",
                "",
                f"  | recorded protocol | `{threshold.name}` / {threshold.unit} |",
                "  | --- | --- |",
            ]
            lines += [
                f"  | {protocol} | {value} |" for protocol, value in threshold.values
            ]
            lines += [
                "",
                f"  *When the provenance is missing.* {_md(threshold.fallback)}",
            ]
        else:  # pragma: no cover - exhaustive over Threshold
            raise TypeError(f"unknown threshold: {threshold!r}")
    lines.append("")
    return lines


def _render_check(check: ScientificCheck, index: int) -> str:
    codes = ", ".join(f"`{code}`" for code in check.codes) or "*(none — prose only)*"
    lines = [
        f"### {index}. {_md(check.asserts)}",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| **Tier** | `{check.tier.value}` |",
        f"| **Code** | {codes} |",
        f"| **Code reaches a client via** | {_CHANNEL_BLURB[check.channel]} |",
        "",
        "**Enforced at.**",
        "",
        _render_enforcement(check),
        "",
    ]
    lines += _render_thresholds(check)
    if check.escape_hatch:
        lines += [f"**Escape hatch.** {_md(check.escape_hatch)}", ""]
    else:
        lines += ["**Escape hatch.** None.", ""]
    if check.divergence:
        lines += [f"**Recorded divergence.** {_md(check.divergence)}", ""]
    if check.codes and not check.emitted:
        lines += [
            "*(The code above is a register label; this check raises prose with no machine-readable code.)*",
            "",
        ]
    if not check.codes:
        lines += [
            "*(No machine-readable code reaches a client for this entry.)*",
            "",
        ]
    return "\n".join(lines)


def _divergence_index(checks: list[ScientificCheck], numbering: dict[int, int]) -> str:
    """Short index of every recorded documentation-versus-behaviour gap."""
    rows = sorted(
        ((numbering[id(check)], check) for check in checks if check.divergence),
        key=lambda row: row[0],
    )
    if not rows:
        return ""
    lines = [
        "## Recorded divergences",
        "",
        "Checks whose guarantee is narrower than their name suggests.",
        "",
    ]
    for index, check in rows:
        lines.append(f"- **[{index}]** {_md(check.asserts)}")
    lines.append("")
    return "\n".join(lines)


def render() -> str:
    checks = register()
    out = [_PREAMBLE, _tier_table(checks), _channel_table(checks)]

    groups, numbering = number_checks(checks)

    out.append(_divergence_index(checks, numbering))

    out.append("## Entries\n")
    for group in groups:
        out.append(f"## {group}\n")
        for check in checks:
            if check.group != group:
                continue
            out.append(_render_check(check, numbering[id(check)]))

    out.append(
        "---\n\n"
        "Declarations live beside the checks they describe; see "
        "`backend/app/scientific_checks/__init__.py` for how to add one, and "
        "`backend/app/scientific_checks/declarations.py` for the PostgreSQL "
        "objects and wire-schema checks that cannot sit beside a Python check.\n"
    )
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the committed document is out of date",
    )
    args = parser.parse_args()

    rendered = render()
    if args.check:
        if not OUTPUT.exists():
            print(f"{OUTPUT} does not exist; run without --check.", file=sys.stderr)
            return 1
        if OUTPUT.read_text() != rendered:
            print(
                f"{OUTPUT} is out of date with the declarations. "
                "Regenerate it with "
                "`python backend/scripts/generate_scientific_check_register.py`.",
                file=sys.stderr,
            )
            return 1
        print(f"{OUTPUT} is up to date.")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered)
    print(f"Wrote {OUTPUT} ({len(register())} entries).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
