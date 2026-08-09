#!/usr/bin/env python
"""Generate ``docs/guides/scientific_check_register.md`` from the declarations.

The register is generated so it cannot drift. Every ``file:line`` below
is resolved from the live function object at generation time, so the
document has no pinned commit and no hand-maintained line numbers — the
failure mode that left ``docs/reviews/validation_check_audit.md`` stale
by many codes within weeks of being written.

Usage::

    conda run -n tckdb_env python backend/scripts/generate_scientific_check_register.py
    conda run -n tckdb_env python backend/scripts/generate_scientific_check_register.py --check

``--check`` regenerates in memory and exits non-zero if the committed
document differs, which is what CI runs.
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

OUTPUT = REPO_ROOT / "docs" / "guides" / "scientific_check_register.md"

_TIER_BLURB = {
    CheckTier.block: (
        "Refuses the payload. ADR 0008 permits this only for a definition or "
        "a contract — a record no correct calculation could produce."
    ),
    CheckTier.warn: (
        "Accepts the payload and records a machine-readable warning. The tier "
        "for expectations (which could fire on a correct novel result) and for "
        "absences (an incomplete record is still a true one)."
    ),
    CheckTier.label: (
        "Labels a stored record at read time without refusing anything — a "
        "`HardFailReason` in the trust evaluator. For facts TCKDB observes "
        "about a record after it was accepted, which no upload-time check "
        "could have refused because they did not exist yet."
    ),
    CheckTier.review: (
        "Referred to `machine_review` under a versioned rubric. ADR 0008 puts "
        "every cross-check against external reference data here."
    ),
    CheckTier.structural: (
        "Not an ADR 0008 consequence tier. The position is enforced by the "
        "shape of the schema, so a record violating it cannot be represented."
    ),
}

_CHANNEL_BLURB = {
    CodeChannel.error_envelope: (
        "the `code` field of the 422 error body — a client can branch on it"
    ),
    CodeChannel.upload_warning: (
        "the `code` field of an `UploadWarning` returned alongside the "
        "accepted upload"
    ),
    CodeChannel.trust_label: (
        "a read-time trust label (`HardFailReason`), not any refusal"
    ),
    CodeChannel.database_constraint: (
        "PostgreSQL only, so the client sees a generic 409 integrity code "
        "rather than this check's name"
    ),
    CodeChannel.none: "*nothing carries a code*",
}

_PREAMBLE = """# The scientific check register

**Generated. Do not edit by hand.** Regenerate with
`conda run -n tckdb_env python backend/scripts/generate_scientific_check_register.py`.
Every `file:line` here is resolved from the live function object at generation
time, so it cannot go stale the way a hand-written table does.

## What this is

An enumeration of what TCKDB *guarantees about chemistry*, as opposed to what
it merely stores. TCKDB runs roughly 326 Pydantic validators and 225 database
check constraints; almost all of them enforce plumbing — string lengths, enum
membership, foreign-key existence, `multiplicity >= 1`. This register contains
only the checks that encode a **scientific decision a referee could disagree
with**.

The inclusion test is *could this check be wrong in an interesting way?*
Elemental balance is a position. `max_length=64` is not, and `multiplicity >= 1`
is arithmetic rather than chemistry. There is deliberately no "paper-worthy"
column: **membership in this register is the claim.** An entry that fails the
test dilutes every other entry.

## Relationship to `docs/reviews/validation_check_audit.md`

The two sit beside each other and answer different questions. That audit asks
*is each Pydantic validator in the right ADR 0008 tier* — it is a placement
review of one tier, pinned to a commit, and it is now stale by several codes.
This register asks *what does TCKDB guarantee about chemistry* — it spans the
service layer, the wire schemas and the database, it is generated rather than
transcribed, and it is guarded in CI. Neither supersedes the other. Where they
disagree about a line number, this one is right by construction.

## How to read a row

- **Asserts** — what the check claims, in one sentence a chemist would
  recognise. Not a description of the implementation.
- **Tier** — the ADR 0008 consequence, with the justification for *that* tier
  in the ADR's own terms.
- **Code**, and **where it reaches a client** — these are two different facts
  and the register used to state only the first. A code is not a contract until
  something carries it somewhere a consumer can read it, and for a long time
  nothing did: six checks spelled their code inside an English sentence
  (`"... (reaction_mass_balance_failed)."`) that no parser matched, and the 422
  body's own `code` field said `validation_error` for every chemistry refusal
  in the system. Naming the channel is what makes the claim checkable —
  `backend/tests/db/test_scientific_check_register.py` holds each channel to
  its obligation, and an `error_envelope` code must both be raised through the
  typed path and be proved to arrive by an end-to-end HTTP test.
- **Enforced at** — `file:line` for Python, or the constraint/trigger name for
  PostgreSQL. Database names are verified against live schema metadata by
  `backend/tests/db/test_scientific_check_register.py`, not trusted as strings.
- **Thresholds** — the numeric lines the check fires on, and *where each number
  comes from*. A constant is fixed in code and printed. A provenance-derived
  threshold is not a number at all: it is resolved per record from the
  execution provenance that record carries, so it is printed as the resolver,
  the parameter keys it reads, the per-protocol table, and what it falls back
  to when those keys are absent. ADR 0012's `tau` is the first of these, and
  the distinction is load-bearing — a register that printed it as "roughly
  50 cm-1" would be claiming the code holds a constant it does not have, and
  would hide the case a referee cares about most, which is the record whose
  protocol was never recorded.
- **Escape hatch** — how legitimate chemistry the check would otherwise refuse
  gets deposited instead. This column carries most of the weight. Charge
  conservation is only defensible as a blocking check *because* an electron can
  be declared as a participant; without that door the rule would be asserting
  "every participant was declared", which is an expectation about the depositor
  and ADR 0008 disqualifies an expectation from blocking.
- **Recorded divergence** — where a check's documentation and its behaviour
  disagree, or where a guarantee is narrower than its name suggests. Reported,
  never silently fixed.

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
    """How many entries carry a code a client can actually act on.

    This table is the answer to a question the register could not answer
    before: "how much of what TCKDB refuses can a program understand?" It
    is printed rather than left to be counted, because the last time
    somebody counted — eleven entries with no code, and every blocking
    chemistry check reporting ``validation_error`` — it took reading the
    whole document to find out.
    """
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
        elif isinstance(site, DesignPosition):
            body = site.label
        else:  # pragma: no cover - exhaustive over Enforcement
            raise TypeError(f"unknown enforcement site: {site!r}")
        if getattr(site, "note", None):
            body += f"\n  *{site.note}*"
        parts.append(_md(f"- {body}"))
    return "\n".join(parts)


def _render_thresholds(check: ScientificCheck) -> list[str]:
    """Render a check's numeric lines, distinguishing the two kinds.

    A constant gets its number. A provenance-derived threshold gets its
    resolver, the keys it reads, its per-protocol table and — the part a
    referee will go looking for — what happens when the provenance is
    absent. Printing a provenance threshold as a single number would
    state a constant the code does not have.
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
        f"| **Governing ADR** | {check.adr} |",
        "",
        f"**Why this tier.** {_md(check.tier_rationale)}",
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
            "*(The code above is a register label; this check raises prose "
            "with no machine-readable code, so no client can match on it.)*",
            "",
        ]
    if not check.codes:
        lines += [
            "*(No machine-readable code reaches anybody for this one. Recorded "
            "as a gap rather than invented, because a code nothing carries is a "
            "code no client can match on. See the enforcement sites above for "
            "why: a position held by a database constraint alone surfaces as a "
            "generic integrity conflict, and one held by schema shape or by a "
            "stored evidence row never surfaces as a refusal at all.)*",
            "",
        ]
    return "\n".join(lines)


def _ordered_groups(checks: list[ScientificCheck]) -> list[str]:
    """Groups in reading order, most fundamental claim first.

    Collection sorts alphabetically for determinism; a reader wants the
    conservation laws before the atom-map bookkeeping. Any group not
    listed here still appears, after the ones that are.
    """
    preferred = [
        "Conservation across a reaction",
        "A structure against its own label",
        "Stationary points",
        "Atom mapping across a reaction",
        "Rate coefficients",
        "Statistical mechanics",
        "Pressure-dependent networks",
        "Custody of the evidence",
        "Reproducibility",
    ]
    present = {check.group for check in checks}
    ordered = [group for group in preferred if group in present]
    ordered += sorted(present - set(ordered))
    return ordered


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
        "Where a check's documentation and its behaviour disagree, or where a "
        "guarantee is narrower than its name suggests. Every one of these is "
        "reported, never silently fixed — the register changes no check "
        "behaviour.",
        "",
    ]
    for index, check in rows:
        lines.append(f"- **[{index}]** {_md(check.asserts)}")
    lines.append("")
    return "\n".join(lines)


def render() -> str:
    checks = register()
    out = [_PREAMBLE, _tier_table(checks), _channel_table(checks)]

    groups = _ordered_groups(checks)
    numbering: dict[int, int] = {}
    index = 0
    for group in groups:
        for check in checks:
            if check.group == group:
                index += 1
                numbering[id(check)] = index

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
        "`backend/app/scientific_checks/declarations.py` for the two "
        "populations that cannot self-declare (PostgreSQL objects, and wire "
        "schemas that are forbidden from importing the backend).\n"
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
