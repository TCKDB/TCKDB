"""Drift guards for the generated API vocabulary.

``docs/guides/api_vocabulary.md`` tells a reader what TCKDB's tokens
mean. Its whole value is that it cannot quietly stop describing the
code, and a glossary that has drifted is worse than none: it answers
confidently and wrongly. These tests are what makes the claim true:

* the committed document must match a fresh render, so a token added to
  a documented source fails until somebody regenerates
  (:func:`test_generated_document_is_in_sync`);
* every token in the document must exist in the code, parsed back out of
  the committed Markdown rather than trusted from the render, so no
  entry can describe a status, prefix, check or code TCKDB does not have
  (:func:`test_every_token_in_the_document_exists_in_a_source`);
* every declared vocabulary must cover its enum exactly — no missing
  member, no invented member;
* the inclusion rule's mechanical half must hold: a declared vocabulary
  has to be reachable on the wire, and an enum every response carries
  has to be documented.

Together those are the two directions drift travels. Sync alone would
pass a document full of fiction as long as the generator rendered the
same fiction; token-existence alone would pass a stale committed file.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.api.code_catalogue import catalogued_codes, client_facing, never_retryable
from app.glossary import ref_prefixes, rubric_checks
from app.glossary.declarations import VOCABULARIES
from app.glossary.reachability import envelope_enums, wire_enums

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
GENERATOR = BACKEND_ROOT / "scripts" / "generate_api_vocabulary.py"
DOCUMENT = REPO_ROOT / "docs" / "guides" / "api_vocabulary.md"

#: A token cell in the document: a table row whose first column is a
#: single backticked identifier, or a bullet opening with one. That is
#: how every generated term, prefix, check name and code is rendered, so
#: parsing it back is a read of the *committed* file rather than of the
#: renderer that produced it.
_TOKEN_ROW = re.compile(r"^(?:\| `([^`]+)` \||- \*\*`([^`]+)`\*\*)", re.MULTILINE)


def _document_tokens() -> set[str]:
    return {
        match.group(1) or match.group(2)
        for match in _TOKEN_ROW.finditer(DOCUMENT.read_text())
    }


def _source_tokens() -> set[str]:
    """Every token the live sources can legitimately put in the document.

    Deliberately rebuilt from the sources rather than from the
    declarations: a declaration that invented a token would satisfy a
    check written against itself.
    """
    tokens: set[str] = set()
    for vocabulary in VOCABULARIES:
        tokens |= {str(member.value) for member in vocabulary.enum}
        tokens |= set(vocabulary.projection_tokens)
    tokens |= {f"{entry.prefix}_" for entry in ref_prefixes()}
    tokens |= {check.name for check in rubric_checks()}
    tokens |= catalogued_codes()
    return tokens


def test_the_document_exists_and_has_content() -> None:
    """Guard the guards: an absent file must not make the rest vacuous."""
    assert DOCUMENT.exists(), f"{DOCUMENT} is missing; run the generator."
    assert len(_document_tokens()) > 200, (
        "The document parses to almost no tokens, which would make "
        "test_every_token_in_the_document_exists_in_a_source pass by "
        "checking nothing. Either the render changed shape or the file is "
        "truncated."
    )


def test_generated_document_is_in_sync() -> None:
    """The committed document must match what the generator renders now.

    Run as a subprocess, exactly as CI does, so the check exercises the
    generator's own ``--check`` path rather than a re-implementation of
    it in this file.
    """
    result = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"{DOCUMENT} is out of date with its sources.\n"
        f"{result.stdout}{result.stderr}\n"
        "Regenerate with: conda run -n tckdb_env python "
        "backend/scripts/generate_api_vocabulary.py"
    )


def test_every_token_in_the_document_exists_in_a_source() -> None:
    """No entry may describe a token the codebase does not have.

    This is the guard against fiction. Every backticked token in the
    first column of a table (or opening a bullet) is matched against the
    union of the live enum members, the ref registry, the rubric check
    names and the code catalogue. An invented status, a renamed prefix
    or a code that has been deleted fails here.
    """
    invented = sorted(_document_tokens() - _source_tokens())
    assert not invented, (
        "These tokens appear in the document and in no source:\n"
        + "\n".join(f"  {token}" for token in invented)
        + "\nEither the source removed them (regenerate) or the glossary "
        "invented them (delete them)."
    )


@pytest.mark.parametrize(
    "vocabulary", VOCABULARIES, ids=[entry.enum.__name__ for entry in VOCABULARIES]
)
def test_declared_tokens_match_the_enum_exactly(vocabulary) -> None:
    """Every member glossed, and nothing glossed that is not a member.

    The first direction is what catches an upstream addition: a new enum
    member arrives with no definition and this fails rather than the
    document silently omitting it. The second is what stops a token
    being described that no response can carry.
    """
    declared = set(vocabulary.declared_tokens)
    members = set(vocabulary.member_values)
    assert declared == members, (
        f"{vocabulary.enum.__name__}: undocumented members "
        f"{sorted(members - declared)}, invented tokens "
        f"{sorted(declared - members)}."
    )
    assert len(vocabulary.declared_tokens) == len(declared), (
        f"{vocabulary.enum.__name__} declares a token twice."
    )


@pytest.mark.parametrize(
    "vocabulary",
    [entry for entry in VOCABULARIES if entry.projection is not None],
    ids=[
        entry.enum.__name__
        for entry in VOCABULARIES
        if entry.projection is not None
    ],
)
def test_a_projected_vocabulary_covers_what_the_read_layer_serialises(vocabulary) -> None:
    """Stored values plus projected ones must equal the served literal.

    Geometry validation is the case: three values in the database, four
    on the wire, because "nobody checked" is projected at read time. A
    reader meets the fourth and could look it up nowhere, so the
    declaration has to cover the read layer's ``Literal`` exactly — no
    served token undocumented, and nothing documented that the read
    layer cannot serialise.
    """
    served = set(vocabulary.projection_tokens)
    documented = set(vocabulary.declared_tokens) | set(vocabulary.projected_tokens)
    assert documented == served, (
        f"{vocabulary.enum.__name__}: the read layer serialises "
        f"{sorted(served - documented)} with no entry, and this document claims "
        f"{sorted(documented - served)} which it never serialises."
    )


@pytest.mark.parametrize(
    "vocabulary", VOCABULARIES, ids=[entry.enum.__name__ for entry in VOCABULARIES]
)
def test_every_declared_vocabulary_reaches_the_wire(vocabulary) -> None:
    """The mechanical half of the inclusion rule.

    A vocabulary is admissible only if a public response can serialise
    it — computed from the read schemas and the trust fragment, not
    asserted. Without this the rule's first half would be a sentence in
    a docstring and the document could fill with internal vocabulary
    that merely looks public.
    """
    assert vocabulary.enum in wire_enums(), (
        f"{vocabulary.enum.__name__} is documented but no public response can "
        "carry it. Either it is internal vocabulary and does not belong here, "
        "or it has stopped being served and the entry is stale."
    )


def test_every_enum_on_every_response_is_documented() -> None:
    """The guard against *under*-inclusion.

    The inclusion rule's second half is a judgement, so nothing can force
    a chemistry enum to be documented. But the envelope enums are
    different: a reader meets them on every single scientific response
    whatever they asked for, so an undocumented one is a token nobody can
    avoid and nobody can look up.
    """
    documented = {vocabulary.enum for vocabulary in VOCABULARIES}
    missing = sorted(
        enum.__name__ for enum in envelope_enums() - documented
    )
    assert not missing, (
        "These enums are on every scientific response and have no glossary "
        f"entry: {missing}. Add a Vocabulary in app/glossary/declarations.py."
    )


def test_every_glossed_term_says_something() -> None:
    """A term with an empty or placeholder definition is worse than absent."""
    offenders = [
        f"{vocabulary.enum.__name__}.{term.token}"
        for vocabulary in VOCABULARIES
        for term in vocabulary.terms
        if len(term.means.strip()) < 20 or term.means.strip().upper().startswith("TODO")
    ]
    assert not offenders, f"These terms carry no real definition: {offenders}"


def test_the_owner_confusions_are_answered() -> None:
    """The five tokens this document was opened for must be in it.

    Not a style check. Each of these was a real question asked while
    reading live responses, and a glossary that lost one of them to a
    later refactor would have lost the thing it was written for.
    """
    tokens = _document_tokens()
    for token in ("reverse", "under_review", "not_reviewed", "not_applicable"):
        assert token in tokens, f"{token} is no longer documented."
    body = DOCUMENT.read_text()
    assert "ts_graph_or_smiles_present" in body, (
        "The trust check names are no longer listed; the check that reads as "
        "an assertion inside a negated bucket was the reason for the table."
    )
    assert "H_Abstraction" in body, (
        "The reaction-family gap note is gone. It has to stay until families "
        "carry a display name, or a reader is left with an undocumented token."
    )


def test_the_reaction_family_gap_note_states_facts() -> None:
    """The one place the document names data it does not document.

    The gap note says families are RMG identifiers, gives three of them
    as examples, and states how many are seeded. Each of those is a
    claim about the seed list, and a note whose examples had been
    renamed — or whose count was out by fifty — would be the confident
    wrong answer the whole document exists to avoid. So the count is
    interpolated by the generator and the names are checked here.
    """
    from app.schemas.reaction_family import CANONICAL_REACTION_FAMILIES

    body = DOCUMENT.read_text()
    for name in ("H_Abstraction", "Disproportionation", "intra_H_migration"):
        assert name in body, f"{name} is no longer named in the gap note."
        assert name in CANONICAL_REACTION_FAMILIES, (
            f"The gap note names {name}, which is not a seeded reaction family."
        )
    assert f"There are {len(CANONICAL_REACTION_FAMILIES)} of them seeded" in body, (
        "The seeded-family count in the gap note does not match the seed list."
    )


def test_client_facing_codes_are_all_listed() -> None:
    """Every code a caller can receive must appear, or the table misleads.

    The catalogue is the authority on which codes reach a client, and a
    table that silently listed a subset would send a reader to grep for
    a code it had decided not to mention.
    """
    tokens = _document_tokens()
    missing = sorted(entry.code for entry in client_facing() if entry.code not in tokens)
    assert not missing, f"Client-facing codes absent from the document: {missing}"
    absent_retry = sorted(
        entry.code for entry in never_retryable() if entry.code not in tokens
    )
    assert not absent_retry, (
        f"Never-retryable codes absent from the document: {absent_retry}"
    )


def test_every_ref_prefix_is_listed_with_its_kind() -> None:
    """Both prefix tables together must cover the registry exactly.

    The content-derived/opaque split is the load-bearing fact about a
    reference — whether two instances can be expected to agree on it —
    and a prefix that fell out of one table without appearing in the
    other would leave that unanswered for exactly the reader who looked
    it up.
    """
    body = DOCUMENT.read_text()
    content_block = body.split("### Content-derived prefixes", 1)[1].split("###", 1)[0]
    opaque_block = body.split("### Opaque prefixes", 1)[1].split("\n## ", 1)[0]
    for entry in ref_prefixes():
        cell = f"| `{entry.prefix}_` |"
        block = content_block if entry.content_derived else opaque_block
        other = opaque_block if entry.content_derived else content_block
        assert cell in block, f"{entry.prefix}_ is missing from its prefix table."
        assert cell not in other, (
            f"{entry.prefix}_ is listed under the wrong kind of reference."
        )
