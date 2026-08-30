"""The vocabulary a reader of the public API meets.

Why this exists
---------------
Every token TCKDB puts on the wire is a word somebody has to decode.
``matched_direction: "reverse"``, ``under_review`` beside
``not_reviewed``, ``ts_graph_or_smiles_present`` reported as
``missing``, ``spc_...`` beside ``spe_...`` — each of these is a real
distinction the code takes seriously and none of them is guessable from
the string. The API answered them nowhere, so a reader either asked or
assumed, and an assumption about a token is a citation risk rather than
a nuisance.

Why it is generated
-------------------
A hand-written glossary is stale the first time somebody adds a code,
and a stale glossary is worse than none: it is confidently wrong. So the
*tokens* are read from the live sources at generation time — the enum
members, the ref registry, the code catalogue, the rubric definitions —
and the committed document is compared against a fresh render in CI.
Adding a member to a documented enum turns the gate red until the
document is regenerated; renaming one fails before that, in the
declaration.

The inclusion rule
------------------
**A token belongs here when both halves hold.**

1. **A reader can meet it.** The literal string can appear in a public
   API response body. This half is mechanical:
   :func:`app.glossary.reachability.wire_enums` computes the enums
   reachable from ``app/schemas/reads/**`` and from the trust fragment,
   and ``backend/tests/scripts/test_api_vocabulary.py`` refuses a
   declared vocabulary that is not in that set. An enum only an
   administrator, a migration or an internal service ever sees is not
   vocabulary a reader needs; it is implementation detail with a
   public-looking name.

2. **Chemistry does not decode it.** The token names something about
   *TCKDB's own process* — how a record was reviewed, how much evidence
   stands behind it, how a query matched, how a record is named, or why
   a request was refused. This half is the judgement, and it is declared
   per vocabulary rather than derived, in the same way membership of the
   scientific check register is declared: the declaration *is* the
   claim.

Half 2 is what keeps the document short. TCKDB defines 96 enums and 75
of them reach a read schema; most carry chemistry — ``chebyshev``,
``hartree``, ``rigid_rotor``, ``wigner``, ``gas`` — and a chemist
reading a rate expression is not helped by TCKDB explaining what a
Chebyshev fit is. Explaining them anyway would bury the twenty tokens
that genuinely have no meaning outside this database. So the rule is
stated in the negative too, because an exclusion nobody wrote down gets
re-litigated: **vocabulary whose meaning is chemistry is out**, however
often it appears.

What is deliberately absent
---------------------------
* **The scientific checks.** ``docs/guides/scientific_check_register.md``
  is generated from its own declarations and published beside this
  document. Duplicating it here would create a second copy to keep in
  step. This document links to it.
* **Reaction family names.** ``reaction_family`` carries ``id``,
  ``name`` and ``created_at`` and nothing else: there is no display
  name and no description column, so "Hydrogen Abstraction" for
  ``H_Abstraction`` exists nowhere in the database and inventing 125 of
  them would be exactly the confident fiction this generator exists to
  prevent. The gap is recorded in the rendered document instead.
* **Prose per refusal code.** :mod:`app.api.code_catalogue` deliberately
  carries no description of what each code means — the refusal already
  has a sentence, the one the depositor receives, and a second copy is
  drift with extra steps. So the code table here renders the facts the
  catalogue *does* hold (status, whether structured ``context`` is owed,
  whether replaying can help) and never invents a definition.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import get_args

__all__ = [
    "Group",
    "RefPrefix",
    "RubricCheck",
    "Term",
    "Vocabulary",
    "ref_prefixes",
    "rubric_checks",
]


@dataclass(frozen=True)
class Term:
    """One token on the wire, and what it means in plain language.

    :param token: The literal string, exactly as it appears in a
        response. Checked against the live source: a declared token that
        is not a member of the enum it is declared under fails
        ``test_every_declared_token_exists_in_its_source``.
    :param means: The definition, written for somebody who knows
        chemistry and does not know this database. One or two sentences.
    :param example: The concrete case, for the tokens whose meaning turns
        on something non-obvious. Most terms do not need one; the ones
        that do are the reason this document exists.
    """

    token: str
    means: str
    example: str | None = None


class Group(str, Enum):
    """Where a vocabulary sits in the rendered document.

    Ordering is by reading order, not alphabetical: a reader arrives
    holding a response, so identity comes before judgement and judgement
    before the refusal they may never see.
    """

    identity = "How a record is named"
    review = "What a person has said about a record"
    trust = "What TCKDB can check by itself"
    contract = "Which contract a response answers under"
    query = "How your query matched"
    provenance = "How a record was produced"


@dataclass(frozen=True)
class Vocabulary:
    """One closed set of tokens a reader meets, with every member glossed.

    :param enum: The live enum. Its members are the authority on what
        tokens exist; :attr:`terms` must cover every one of them, so a
        member added upstream fails the coverage guard rather than being
        silently omitted.
    :param carried_by: Which response field carries it, named as a
        reader would find it — ``trust.trust_status``, not
        ``TrustFragment.trust_status``.
    :param summary: What the whole set is for, in one or two sentences.
    :param terms: One :class:`Term` per member.
    :param worked_example: The concrete case, where the set as a whole
        is the confusing thing rather than any single token.
    """

    group: Group
    title: str
    enum: type[Enum]
    carried_by: str
    summary: str
    terms: tuple[Term, ...]
    worked_example: str | None = None
    projection: object | None = None
    projected: tuple[Term, ...] = ()

    @property
    def member_values(self) -> tuple[str, ...]:
        return tuple(str(member.value) for member in self.enum)

    @property
    def declared_tokens(self) -> tuple[str, ...]:
        return tuple(term.token for term in self.terms)

    @property
    def projected_tokens(self) -> tuple[str, ...]:
        return tuple(term.token for term in self.projected)

    @property
    def projection_tokens(self) -> tuple[str, ...]:
        """The literal the read layer actually serialises, if it differs.

        A stored enum is not always what reaches a reader: geometry
        validation is stored as three values and *served* as four,
        because the read layer projects the absence of a row into a
        token of its own. A reader meets the projected token and can
        look it up nowhere, so the vocabulary declares the read
        layer's ``Literal`` and the test suite requires the two
        halves together to cover it exactly.
        """
        if self.projection is None:
            return ()
        return tuple(str(argument) for argument in get_args(self.projection))


@dataclass(frozen=True)
class RefPrefix:
    """One public-reference prefix, read from the live registry.

    Nothing here is hand-written: the prefix, the record type and the
    content-derived flag all come from
    :mod:`app.services.public_refs`, so a new prefix appears in the
    document the moment it appears in the registry.
    """

    prefix: str
    record_type: str
    content_derived: bool


@dataclass(frozen=True)
class RubricCheck:
    """One deterministic trust check, read from the live rubric registry.

    The ``explain`` string is the rubric's own — the same sentence the
    evaluator would use — so this table cannot describe a check the code
    does not run.
    """

    rubric: str
    version: int
    record_type: str
    name: str
    kind: str
    weight: int
    explain: str


def ref_prefixes() -> tuple[RefPrefix, ...]:
    """Every public-ref prefix, ordered by prefix.

    Reads :data:`app.services.public_refs.PREFIXES` and the
    content-derived set beside it. The content-derived/opaque split is
    the load-bearing fact — it decides whether two TCKDB instances can
    be expected to agree on a ref — and a reader has no way to infer it
    from the string.
    """
    from app.services.public_refs import _CONTENT_DERIVED, PREFIXES

    return tuple(
        sorted(
            (
                RefPrefix(
                    prefix=prefix,
                    record_type=class_name,
                    content_derived=class_name in _CONTENT_DERIVED,
                )
                for class_name, prefix in PREFIXES.items()
            ),
            key=lambda entry: entry.prefix,
        )
    )


def rubric_checks() -> tuple[RubricCheck, ...]:
    """Every check name a ``trust.evidence.checks`` map can contain.

    Read from :data:`app.services.trust.rubrics.RUBRIC_REGISTRY`, in the
    rubric's own declared check order — which is also the key order of
    the serialised map, so the document and a real response list the
    checks the same way round.
    """
    from app.services.trust.rubrics import RUBRIC_REGISTRY

    seen: set[tuple[str, str]] = set()
    checks: list[RubricCheck] = []
    for record_type, rubric in sorted(RUBRIC_REGISTRY.items()):
        for spec in rubric.checks:
            key = (rubric.name, spec.name)
            if key in seen:
                continue
            seen.add(key)
            checks.append(
                RubricCheck(
                    rubric=rubric.name,
                    version=rubric.version,
                    record_type=record_type,
                    name=spec.name,
                    kind=spec.kind.value,
                    weight=spec.weight,
                    explain=spec.explain,
                )
            )
    return tuple(checks)
