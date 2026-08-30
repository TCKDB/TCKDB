#!/usr/bin/env python
"""Generate ``docs/guides/api_vocabulary.md`` from the live sources.

What this is
------------
A glossary of the tokens TCKDB puts on the wire: the review statuses,
the trust badges and check outcomes, the reaction-search words, the
identifier prefixes, and every code a refusal can carry. It exists
because a reader holding a response could not decode
``matched_direction: "reverse"``, ``under_review`` beside
``not_reviewed``, or ``"ts_graph_or_smiles_present": "missing"`` from
the strings alone, and nothing in the API said.

Why it is generated, and what that buys
---------------------------------------
Hand-written, it would be stale the first time somebody added a code,
and a stale glossary is worse than none: it is confidently wrong. So
every token here is read from the source that defines it at generation
time — enum members, the public-ref registry, the code catalogue, the
rubric definitions — and ``--check`` compares a fresh render against the
committed document, which is what CI runs. Adding a member to a
documented enum fails the gate until the document is regenerated;
renaming one fails earlier, in the declaration.

The inclusion rule, which is the whole judgement
------------------------------------------------
A token is in when **both** halves hold:

1. **A reader can meet it** — the literal string can appear in a public
   response body. Mechanical: ``app.glossary.reachability`` computes
   the enums reachable from the read schemas and the trust fragment,
   and the test suite refuses a declared vocabulary outside that set.
2. **Chemistry does not decode it** — the token names something about
   TCKDB's own process rather than about chemistry. Declared per
   vocabulary; the declaration is the claim.

Half 2 is why this document is short. Seventy-five enums reach a read
schema and most of them carry chemistry — ``chebyshev``, ``wigner``,
``hartree``, ``rigid_rotor`` — which a chemist does not need TCKDB to
explain, and explaining them anyway would bury the twenty tokens that
have no meaning outside this database.

Prose lives in ``app/glossary/declarations.py`` beside the enum it
describes; this file only renders. See :mod:`app.glossary` for the full
statement of the rule and for what is deliberately left out (the
scientific check register, which is generated and published separately;
reaction family display names, which exist nowhere in the schema).

Usage::

    conda run -n tckdb_env python backend/scripts/generate_api_vocabulary.py
    conda run -n tckdb_env python backend/scripts/generate_api_vocabulary.py --check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.api.code_catalogue import (  # noqa: E402
    ApiCode,
    Shape,
    client_facing,
    never_retryable,
)
from app.glossary import (  # noqa: E402
    Group,
    RefPrefix,
    RubricCheck,
    Vocabulary,
    ref_prefixes,
    rubric_checks,
)
from app.glossary.declarations import vocabularies  # noqa: E402
from app.services.trust.models import EvidenceCheckKind  # noqa: E402

OUTPUT = REPO_ROOT / "docs" / "guides" / "api_vocabulary.md"

#: What a rubric's check-kind column means. Hand-written because the
#: enum's own docstring is written for the evaluator, and asserted
#: exhaustive by the generator, so a new kind fails here rather than
#: rendering a blank cell. ``EvidenceCheckKind`` is deliberately *not* a
#: declared vocabulary: it is excluded from the serialised trust
#: fragment, so no reader meets it in a response — only in this
#: document's own table.
_CHECK_KIND_BLURB = {
    EvidenceCheckKind.required: (
        "failing it stops the record reaching `well_supported`, however good "
        "the ratio is"
    ),
    EvidenceCheckKind.optional: (
        "contributes to completeness; its absence blocks no badge"
    ),
    EvidenceCheckKind.warning: "informational; carries zero weight",
}

_PREAMBLE_TEMPLATE = """# The API vocabulary

**Generated. Do not edit by hand.** Regenerate with
`conda run -n tckdb_env python backend/scripts/generate_api_vocabulary.py`.
Every token below is read from the code that defines it — the enum members,
the public-reference registry, the code catalogue, the trust rubrics — so this
document cannot describe a word TCKDB does not use. A term added upstream and
not regenerated here turns the CI gate red.

## What this is

TCKDB answers in tokens: `not_reviewed`, `hard_failed`, `matched_direction`,
`spc_01h9k…`, `smiles_too_long`. Each of them is a real distinction the code
takes seriously, and most of them are not guessable from the string. This is
the list, with a plain-language definition for each and the concrete case where
the meaning turns on something non-obvious.

It assumes you know chemistry and does not assume you know this database. So
`substructure` gets a sentence about which algorithm ran and `chebyshev` does
not appear at all: a Chebyshev fit means the same thing here as everywhere
else, and explaining it would bury the tokens that mean something only inside
TCKDB.

## What is in, and what is not

A token is here when **both** of these hold:

1. **You can meet it.** The literal string can appear in a public API response
   body. This half is checked mechanically: `app/glossary/reachability.py`
   computes the vocabulary the read schemas and the trust fragment can
   serialise, and the test suite refuses an entry outside that set. A word
   only an administrator or an internal service sees is not vocabulary you
   need.
2. **Chemistry does not decode it.** The token says something about *TCKDB's
   own process* — how a record was reviewed, how much evidence stands behind
   it, how your query matched, how a record is named, or why a request was
   refused.

Three things are deliberately absent:

- **The scientific checks.** What TCKDB guarantees about chemistry is its own
  generated document: [the scientific check
  register](scientific_check_register.md). Copying it here would create a
  second copy to keep in step.
- **Reaction family names.** A reaction's `family` is an RMG identifier —
  `H_Abstraction`, `Disproportionation`, `intra_H_migration` — carried
  verbatim. There are {family_count} of them seeded and the `reaction_family`
  table holds only an id, that name and a timestamp: **no display name and no
  description column exist**, so "Hydrogen Abstraction" is not a fact this
  database holds and inventing {family_count} of them here would be fiction
  with a generator's credibility. Recorded as a gap; giving families human-readable names is a
  schema decision, not a documentation one.
- **A definition per refusal code.** The code catalogue deliberately carries no
  prose — the refusal already has a sentence, the one you receive in `detail`,
  and a second copy would drift from it. The code table below therefore renders
  the facts the catalogue does hold, and invents nothing.

"""


def _preamble() -> str:
    """The preamble, with the reaction-family count read from the seed list.

    The count is interpolated rather than typed because it is the one
    number in the prose that can go stale silently: the gap note's whole
    point is that these names exist and their human forms do not, and a
    gap note quoting the wrong size is a gap note nobody trusts.
    """
    from app.schemas.reaction_family import CANONICAL_REACTION_FAMILIES

    return _PREAMBLE_TEMPLATE.format(family_count=len(CANONICAL_REACTION_FAMILIES))


def _md(text: str) -> str:
    """Render declaration prose as Markdown (RST double-backticks first)."""
    return text.replace("``", "`")


def _human_record_type(class_name: str) -> str:
    """``ConformerObservation`` -> ``conformer observation``.

    Derived from the ORM class name rather than declared, so a new
    prefix documents itself and no hand-maintained name can disagree
    with the registry.
    """
    words: list[str] = []
    current = ""
    for character in class_name:
        if character.isupper() and current:
            words.append(current)
            current = character
        else:
            current += character
    if current:
        words.append(current)
    return " ".join(word.lower() for word in words)


def _identifier_section(prefixes: tuple[RefPrefix, ...]) -> str:
    content_derived = [entry for entry in prefixes if entry.content_derived]
    opaque = [entry for entry in prefixes if not entry.content_derived]
    lines = [
        f"## {Group.identity.value}",
        "",
        "Every record TCKDB serves is named by a **public reference** — a short "
        "prefix, an underscore, and 26 characters: `spc_`, `rxn_`, `cg_`, "
        "`lot_`. The prefix tells you what kind of record it is, and it is the "
        "only part you are meant to read.",
        "",
        "There are two kinds of reference, and the difference matters more than "
        "it looks:",
        "",
        "- **Content-derived** — computed from the record's own canonical "
        "identity. The same species has the **same reference on every TCKDB "
        "instance**, so two deployments can be compared, and a reference of "
        "this kind is a claim about *what the thing is*.",
        "- **Opaque** — 130 random bits. It identifies one row in one database "
        "and nothing more. Two instances that hold the same calculation give it "
        "different opaque references, and that is correct: an *event* is not "
        "the same event because it looks alike.",
        "",
        "Nothing in the string says which kind you are holding, which is why "
        "this table exists.",
        "",
        f"### Content-derived prefixes ({len(content_derived)})",
        "",
        "| Prefix | Names a | Same on every instance? |",
        "| --- | --- | --- |",
    ]
    lines += [
        f"| `{entry.prefix}_` | {_human_record_type(entry.record_type)} | yes |"
        for entry in content_derived
    ]
    lines += [
        "",
        f"### Opaque prefixes ({len(opaque)})",
        "",
        "| Prefix | Names a | Same on every instance? |",
        "| --- | --- | --- |",
    ]
    lines += [
        f"| `{entry.prefix}_` | {_human_record_type(entry.record_type)} | no — one row, one database |"
        for entry in opaque
    ]
    lines.append("")
    return "\n".join(lines)


def _render_vocabulary(vocabulary: Vocabulary) -> str:
    lines = [
        f"### {vocabulary.title}",
        "",
        f"*On the wire:* {_md(vocabulary.carried_by)}.",
        "",
        _md(vocabulary.summary),
        "",
        "| Token | What it means |",
        "| --- | --- |",
    ]
    for term in (*vocabulary.terms, *vocabulary.projected):
        means = _md(term.means)
        if term.example:
            means += f" **For example:** {_md(term.example)}"
        lines.append(f"| `{term.token}` | {means} |")
    lines.append("")
    if vocabulary.projected:
        stored = ", ".join(f"`{token}`" for token in vocabulary.member_values)
        extra = ", ".join(f"`{token}`" for token in vocabulary.projected_tokens)
        lines += [
            f"The record itself stores only {stored}; {extra} is added by the "
            "read layer, which is why you will not find it in the database "
            "schema.",
            "",
        ]
    if vocabulary.worked_example:
        lines += [f"> {_md(vocabulary.worked_example)}", ""]
    return "\n".join(lines)


def _trust_check_section(checks: tuple[RubricCheck, ...]) -> str:
    rubrics = sorted({(check.rubric, check.version) for check in checks})
    lines = [
        "### Check names",
        "",
        "*On the wire:* the **keys** of the `trust.evidence.checks` map.",
        "",
        "A check name is an assertion, so read it together with its value: "
        "`\"irc_evidence_present\": \"missing\"` means there is no IRC evidence. "
        "The names below are the rubrics' own, and each explanation is the "
        "sentence the rubric itself carries — not a paraphrase written here.",
        "",
        "`kind` decides what a failure costs: "
        + "; ".join(
            f"**{kind.value}** — {_CHECK_KIND_BLURB[kind]}" for kind in EvidenceCheckKind
        )
        + ". `weight` is that check's share of the completeness ratio.",
        "",
        "Which rubric applies is decided by the kind of record: "
        + ", ".join(f"`{name}` (v{version})" for name, version in rubrics)
        + ".",
        "",
        "| Check | Rubric | Kind | Weight | What it asks |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check in sorted(checks, key=lambda entry: (entry.name, entry.rubric)):
        explain = _md(check.explain) or "*(the rubric carries no explanation)*"
        lines.append(
            f"| `{check.name}` | `{check.rubric}` | {check.kind} | "
            f"{check.weight} | {explain} |"
        )
    lines.append("")
    return "\n".join(lines)


def _code_row(entry: ApiCode) -> str:
    names = (
        "a relationship — read `context`"
        if entry.shape is Shape.relationship
        else "a thing"
    )
    return f"| `{entry.code}` | {names} |"


def _refusal_section(entries: tuple[ApiCode, ...]) -> str:
    by_status: dict[int, list[ApiCode]] = {}
    for entry in entries:
        by_status.setdefault(entry.status, []).append(entry)
    lines = [
        "## Why a request was refused",
        "",
        "When TCKDB refuses a request the body carries a `code`. Branch on that, "
        "never on the English `detail` — the code is the contract and the "
        "sentence is not. These are every code a caller can receive; the "
        "catalogue also holds codes for internal guards and server faults, which "
        "no request can provoke and which are therefore not listed here.",
        "",
        "Two facts accompany each one, and neither is obvious from the name:",
        "",
        "- **A *thing* or a *relationship*.** A code naming a thing "
        "(`unknown_release`, `smiles_too_long`) is complete as it stands. A code "
        "naming a relationship (`state_conflict`, a mismatch, an ambiguity) "
        "asserts something about two or more things and names none of them, so "
        "the envelope's structured `context` owes you which ones. Where a "
        "`context` is still empty for such a code, that is a known gap rather "
        "than a claim that there is nothing to say.",
        "- **Whether replaying helps.** Every code here refuses something about "
        "the request, so replaying an unchanged request generally meets the "
        "same answer. The exception worth knowing is the short list at the end "
        "of this section: server-side conditions a retry layer must **not** "
        "replay, because they are deterministic and no wait will clear them.",
        "",
        "There is deliberately no definition column: the refusal already sent "
        "you a sentence, and a second copy kept here would drift from it. "
        "`backend/app/api/code_catalogue.py` names the module that raises each "
        "one.",
        "",
    ]
    for status in sorted(by_status):
        rows = sorted(by_status[status], key=lambda entry: entry.code)
        lines += [
            f"### HTTP {status} ({len(rows)} code{'' if len(rows) == 1 else 's'})",
            "",
            "| Code | Names |",
            "| --- | --- |",
        ]
        lines += [_code_row(entry) for entry in rows]
        lines.append("")
    lines += _futile_retry_block()
    return "\n".join(lines)


def _futile_retry_block() -> list[str]:
    """The codes a retry layer must refuse to replay.

    Read from :func:`app.api.code_catalogue.never_retryable`, which is
    the same source the client's ``NON_RETRYABLE_CODES`` is generated
    from — so a client and this document cannot disagree about which
    failures are permanent. They are server-side codes, which is why
    none of them appears in the tables above: a caller has no branch to
    write for them, only a retry decision to make.
    """
    entries = never_retryable()
    if not entries:  # pragma: no cover - the catalogue declares several
        return []
    lines = [
        f"### Codes it is pointless to replay ({len(entries)})",
        "",
        "These arrive at a status that normally invites a retry, and for these "
        "codes a retry is a loop with no exit: the condition is deterministic, "
        "so waiting cannot clear it and only an operator can. They refuse "
        "nothing you did, which is why they are absent from the tables above. "
        "The same list is generated into the Python client as "
        "`NON_RETRYABLE_CODES`; `backend/app/api/code_catalogue.py` records why "
        "each one is permanent.",
        "",
    ]
    for entry in sorted(entries, key=lambda code: code.code):
        lines.append(f"- **`{entry.code}`** — HTTP {entry.status}")
    lines.append("")
    return lines


def _counts_table(
    vocabs: tuple[Vocabulary, ...],
    prefixes: tuple[RefPrefix, ...],
    checks: tuple[RubricCheck, ...],
    codes: tuple[ApiCode, ...],
) -> str:
    glossed = sum(
        len(vocabulary.terms) + len(vocabulary.projected) for vocabulary in vocabs
    )
    lines = [
        "## What this covers",
        "",
        "| Kind of token | Count | Read from |",
        "| --- | --- | --- |",
        f"| Status, badge and query words | {glossed} | "
        f"{len(vocabs)} enums, declared in `backend/app/glossary/declarations.py` |",
        f"| Identifier prefixes | {len(prefixes)} | `backend/app/services/public_refs.py` |",
        f"| Trust check names | {len(checks)} | `backend/app/services/trust/rubrics.py` |",
        f"| Refusal codes a caller can receive | {len(codes)} | `backend/app/api/code_catalogue.py` |",
        f"| **total** | **{glossed + len(prefixes) + len(checks) + len(codes)}** | |",
        "",
    ]
    return "\n".join(lines)


def render() -> str:
    vocabs = vocabularies()
    prefixes = ref_prefixes()
    checks = rubric_checks()
    codes = client_facing()

    missing_kinds = [kind for kind in EvidenceCheckKind if kind not in _CHECK_KIND_BLURB]
    if missing_kinds:  # pragma: no cover - guarded by the test suite
        raise KeyError(
            "EvidenceCheckKind gained a member with no blurb in this generator: "
            f"{[kind.value for kind in missing_kinds]}"
        )

    out = [
        _preamble(),
        _counts_table(vocabs, prefixes, checks, codes),
        _identifier_section(prefixes),
    ]

    for group in Group:
        if group is Group.identity:
            continue
        in_group = [vocabulary for vocabulary in vocabs if vocabulary.group is group]
        if not in_group:
            continue
        out.append(f"## {group.value}\n")
        for vocabulary in in_group:
            out.append(_render_vocabulary(vocabulary))
        if group is Group.trust:
            out.append(_trust_check_section(checks))

    out.append(_refusal_section(codes))
    out.append(
        "---\n\n"
        "Tokens are read from the code at generation time; the definitions live "
        "in `backend/app/glossary/declarations.py`, beside the enum each one "
        "describes. `backend/tests/scripts/test_api_vocabulary.py` holds this "
        "document to its sources: it fails if the committed copy drifts from a "
        "fresh render, if a declared token is not a real member of its enum, or "
        "if an enum every response carries has no entry here.\n"
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
                f"{OUTPUT} is out of date with the sources. Regenerate it with "
                "`python backend/scripts/generate_api_vocabulary.py`.",
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
