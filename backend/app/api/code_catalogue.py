"""Every code the API can put in the ``code`` field of an error body.

Why this exists, and why it is not the scientific check register
----------------------------------------------------------------
:mod:`app.scientific_checks` enumerates the positions TCKDB takes about
chemistry. Its inclusion test is "could this check be wrong in an
interesting way?", and membership *is* the claim that a check encodes a
scientific decision — so an entry that fails the test dilutes every other
entry. That register is also the manuscript's validation claim.

The client's ``RejectionCode`` enum used to be generated from that
register alone, which quietly made it serve a second, different purpose:
enumerating what the API can return. Those are different sets. A
role/type mismatch, an ownership refusal and ``missing_filter`` are real
codes a client must be able to branch on, and none of them can be wrong
in an interesting way — so the register was correctly refusing them, and
the consequence was that a client could not import them. The register was
being asked to choose between diluting a scientific claim and leaving
real codes unbranchable.

This module is the second set. It enumerates codes; it says nothing about
chemistry. The register is now an *annotated subset* of it, and
``backend/tests/api/test_api_code_catalogue.py`` checks the containment:
every code the register declares must exist here, and this list must stay
several times the size of that one, so "the catalogue" and "the register"
cannot quietly converge into a single list again.

There is deliberately no ``scientific`` flag on :class:`ApiCode`. Folding
the two lists into one with a boolean is the design this replaces: it
would make the scientific claim a one-word edit, which is exactly what
the register's inclusion test exists to prevent. Membership *here* is
cheap because it claims nothing; membership *there* stays expensive
because it claims a position a referee could argue with.

What is not in scope
--------------------
The ``code`` field of an *error* body, and nothing else. TCKDB puts codes
in successful responses too — ``UploadWarning``, the contribution-bundle
dry run's messages, the reproducibility rubric's warnings — and those are
a different contract with a different meaning: the deposit was accepted.
Listing them here, under a module a client reads to find out what refused
its request, would be the same category error as putting them in an enum
named ``RejectionCode``.

What an entry deliberately does not carry
-----------------------------------------
No prose describing what the code means. The refusal already has a
sentence — the one the depositor receives — and copying it here would
create a second copy to keep in step, which is drift with extra steps.
:attr:`ApiCode.origin` names the module that defines the literal, and the
drift guard checks the literal is still in it, so a reader is one grep
from the real message and a rename fails CI. Where a *fact* needs
recording that the site cannot state itself, :attr:`ApiCode.note` records
it — and those notes are few on purpose.

Nor any line numbers or rendered paths in the generated client file. See
``backend/scripts/generate_client_rejection_codes.py`` for why a gate that
fires on cosmetic movement is worse than no gate.

Which codes owe a client structured facts
-----------------------------------------
Every entry also carries a :class:`Shape`: whether the code names a
*thing* or a *relationship between things*, and therefore whether the
envelope's ``context`` owes a client anything. The rule and its reasoning
live on that class. What belongs here is the part a reader of *this list*
needs -- the borderline calls -- because a classification whose hard cases
are undocumented is one the next person re-litigates from scratch.

Measured on this file: 73 entries (71 distinct codes) are relationships,
98 are things. The cap family moved eight of them across on 2026-08-18 --
see the first borderline group below.

The groups where the call was genuinely arguable, and the argument:

* **Caps are relationships. Overruled 2026-08-18.** ``limit_too_large``,
  ``offset_too_large``, ``geometry_too_large``, ``smiles_too_long``,
  ``export_all_cap_exceeded``, ``ml_export_all_cap_exceeded``,
  ``query_too_expensive`` and
  ``composed_search_candidate_limit_exceeded`` were classified as things
  when :class:`Shape` was introduced, on the ground that the rule's
  discriminator is *conflict, mismatch, ambiguity* and a cap breach is
  none of those. That reading was invited to be overruled and was.

  The argument that won: **a limit refusal that does not state the limit
  is un-actionable.** A client that wants to retry must guess, or a human
  must go and read documentation -- and every one of these caps is a
  *setting* or a module constant, so on a deployment that lowers
  ``public_max_limit`` there is no document that has the right number in
  it. That is the same defect diagnosed in "Tell 'no such selection' from
  'which selection?'": advice that cannot be followed teaches people to
  stop reading advice.

  Publishing a cap is not a security concern. It is discoverable in about
  three requests by halving the value; the control is the cap being
  *enforced*, not the cap being secret; and it describes TCKDB's own
  configuration rather than anybody's data.

  What may **not** be published is the other number, and the rule for
  that is on :class:`Shape` -- see "The disclosure line". Four of the
  eight publish a threshold and deliberately publish nothing else.
* **A code that names both its fields is a thing.**
  ``parameter_value_requires_key``,
  ``canonical_parameter_value_requires_key`` and
  ``unsupported_ranking_for_calculation_type`` are each about two fields
  and each *names* both, so a ``context`` would be the code spelled a
  second way.
* **"Taken" is a thing.** ``email_taken``, ``username_taken`` and
  ``release_tag_taken`` imply a second claimant, but only one party is
  identifiable and the code says which field it is -- which was the point
  of splitting the first two in #225. The other claimant is another
  principal's row and is not ours to disclose.
* **``artifact_integrity_failed`` is a thing**, although it is a digest
  comparison. Its name states a property of one artifact, and the two
  digests are custody evidence for an operator -- recorded in
  ``artifact_integrity_event``, not handed to a caller who can act on
  neither. Its response body is pinned empty for exactly that reason.
* **``invalid_structure_query`` is a thing** because only one of its two
  conditions has a second thing (``mode`` not accepting a query kind); the
  other is an unparseable SMILES. A code that owes facts on one path and
  not the other should be split, which is a bigger decision than a
  classification.
* **``supersedes_same_record`` is a relationship** although it fires
  because two things are the *same* rather than different. The test the
  rule applies is whether the code asserts something about two things and
  names neither, and it does.
* **``tckdb_client_version_unsupported`` is a relationship that is
  deliberately not populated.** Its two facts are already
  machine-readable, in a ``detail`` that is a dict rather than a sentence
  -- see :attr:`Surface.detail_object`. That is why
  :attr:`ApiCode.owes_context` is a property and not a bare comparison
  against :attr:`Shape.relationship`.

Why completeness is checked, not claimed
----------------------------------------
A catalogue that asserts it is complete and is not would be exactly the
kind of check-that-cannot-fail this repository keeps finding. Codes reach
this file by four different routes, and no one of them is enough:

* **A static scan of raise sites** (:data:`Surface.coded_exception`,
  :data:`Surface.message_prefix`) finds every code written as a literal
  where the refusal is raised. It is the bulk of the catalogue and it is
  closed under inspection.
* **A scan cannot see a code minted from a variable.** Four mechanisms do
  exactly that: the :mod:`app.services.calculation_ownership` guards
  (:func:`~app.services.calculation_ownership.assert_owned_by` and its two
  typed wrappers)
  and ``scientific_read.handles.reconcile_id_ref`` both take their code as
  a *parameter*, ``tckdb_schemas.stationary_point`` raises with whichever
  code its blocking finding carries, and the integrity handler looks its
  code up by PostgreSQL constraint name. That is twenty-two codes the scan
  is blind to — nineteen until #195 gave the ownership family its three
  statmech-and-selection codes — and the ``*_handle_conflict`` family was
  found only by the
  observer below. Six of them have since been brought back into
  static view: #177 widened the scan to read a ``*_code=`` argument at
  any call, not only at a ``raise``, so the codes handed to
  ``reconcile_id_ref`` are seen where they are written. That matters
  because five of those six are emitted by no test — the observer's
  record across the three gates holds 101 distinct ``(status, code)``
  pairs and only ``level_of_theory_handle_conflict`` among them — and a
  runtime observer can only see what the suite provokes. For the rest the
  guard
  checks the *defining* module instead, which is where the literal
  actually lives. The nineteenth,
  ``freq_list_exceeds_geometry_degrees_of_freedom``, shows why that is the
  right key rather than a convenience: its literal lives in
  ``tckdb_schemas.frequency_completeness`` while the ``raise`` that
  carries it lives in ``stationary_point``, so no scan of raise sites
  could attribute it to the module a reader needs to open.
* **A handler, a middleware or a route writes the body itself** and names
  the code in a literal there — twenty-one of these, none of them at a
  ``raise``. They are found by reading the small number of places that
  build an error body, which is a closed set because the exception
  handlers are registered in one function.
* **A runtime observer** in ``backend/tests/conftest.py`` records the
  ``code`` of every error response the test suite produces and fails the
  test that emits one this catalogue does not list. That is what makes
  the completeness claim falsifiable rather than asserted, and it is the
  only one of the four that can catch a code assembled at request time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

#: Codes of the form ``http_<status>``, produced by
#: :func:`app.api.errors._http_exception_handler` whenever an
#: ``HTTPException`` carries a plain-string detail with no ``"code: "``
#: prefix. Enumerating each status separately would be noise: the code
#: carries nothing the HTTP status line does not already say, which is
#: also why the family is not exported to a client.
STATUS_FALLBACK_PATTERN = re.compile(r"^http_\d{3}$")


class Surface(str, Enum):
    """How a code gets into the response body.

    This is a statement about mechanism, not about importance. It is what
    makes the catalogue's completeness checkable: each value implies a
    different way of finding the codes that use it, and a code whose
    surface is misdeclared fails its guard.
    """

    #: The raiser attached the code to the exception and the handler
    #: passes it through unparsed — a
    #: :class:`~tckdb_schemas.coded_error.CodedValidationError` at 422, or
    #: an :class:`app.api.errors.NotFoundError` carrying ``code=`` at 404.
    #: The only surface where nothing reads English.
    coded_exception = "coded_exception"

    #: The raiser wrote ``"code: message"`` and the envelope promoted the
    #: token in the code position. The read API's published convention —
    #: see :mod:`app.api.error_contract` for what "code position" means
    #: and why the rule had to be narrowed to it.
    message_prefix = "message_prefix"

    #: An ``HTTPException`` whose ``detail`` is a *dict* carrying its own
    #: ``code`` key, lifted by :func:`app.api.error_contract.detail_code`.
    detail_object = "detail_object"

    #: A route, middleware or exception handler writes the body itself and
    #: names the code in a Python literal.
    response_literal = "response_literal"

    #: HTTP 409, keyed on the PostgreSQL constraint name the driver
    #: reports. The code is looked up from the scientific check register's
    #: :class:`~app.scientific_checks.DatabaseConstraint` declarations, so
    #: these are the one surface where the register is the source.
    database_constraint = "database_constraint"

    #: HTTP 409 classified only by SQLSTATE, because no register entry
    #: names the constraint that fired. Says *which kind* of consistency
    #: rule refused the write, not which rule.
    sqlstate_category = "sqlstate_category"

    #: The handler had nothing more specific to say and used its
    #: ``fallback_code``. Catalogued because the API emits it, excluded
    #: from the client enum because branching on it tells a caller
    #: nothing the HTTP status did not.
    generic_fallback = "generic_fallback"

    #: Not a code. A ``f"{context}: "`` prefix whose ``context`` happens
    #: to be a snake_case identifier, so it occupies the code position and
    #: the envelope reports it as though it were a contract. Recorded
    #: rather than hidden, and never exported to a client — a fabricated
    #: code that looks real is worse than an honestly generic one, which
    #: is the finding #159 was opened for.
    #:
    #: **No entry uses this today.** The two that did —
    #: ``create_applied_group_additivity`` and ``keyset_predicate`` —
    #: were reworded in #178 so that their refusals say what went wrong
    #: instead of naming the function it went wrong in, and a message
    #: with no token in the code position is not an accidental prefix at
    #: all. Cataloguing one was always the second-best fix: it stopped
    #: the token being *promoted* (#164) while leaving a depositor to
    #: read a function name. The value stays because the classification
    #: is what makes recording the next one cheaper than hiding it.
    accidental_prefix = "accidental_prefix"


class Reach(str, Enum):
    """Whether a *request* can produce this code.

    :class:`Surface` says how a code gets into a body. This says whether
    anything a caller can send makes it get there at all, and the two are
    independent. Keeping them apart is what lets the catalogue stay
    complete while the client enum stays honest: this module enumerates
    what exists, and ``RejectionCode`` answers "what might I get back".

    Three-way, not two: catalogued and client-facing (a caller can
    provoke it); catalogued and not client-facing (a real guard no
    request can trip); not catalogued at all (not a code). The middle
    case had no spelling before, so a guard could only be recorded by
    telling clients they might receive it, or by deleting the entry and
    leaving the next reader to rediscover the literal.

    This governs the client enum and nothing else. It is deliberately
    *not* consulted by :data:`app.api.error_contract.MESSAGE_PREFIX_CODES`
    — promotion is a question about whether a message declared a code,
    and gating that on reachability would degrade a refusal the day
    somebody made it reachable and forgot to reclassify.
    """

    #: A caller can provoke it with a request. The default, because the
    #: overwhelming majority of codes are refusals of something a
    #: depositor did.
    request = "request"

    #: No request can produce it: the branch compares against a value the
    #: request path established itself, or names a state nothing sets.
    #: Catalogued, because the literal exists and a reader who greps it
    #: deserves an answer; never exported, because an enum member a
    #: client can import and branch on but never receive is a lie in the
    #: contract.
    #:
    #: Not a defect to clean up. Several of these guard an ownership rule
    #: that no current write path can break because the calculation was
    #: scoped to the target three lines above the check — and in a
    #: database where a mis-attached calculation is a scientific error
    #: rather than a crash, a cheap tripwire against a bug five lines up
    #: is worth keeping. What is not worth keeping is telling a client it
    #: might receive one.
    #:
    #: Every entry carrying this value must state *why* in its
    #: :attr:`ApiCode.note`, and the reason has to be a property of the
    #: code rather than of the test suite. "No test provokes it" is not
    #: one: three quarters of the codes no test reaches are ordinary
    #: refusals nobody has got round to, and classifying those as guards
    #: would hide them from clients for the wrong reason entirely.
    guard = "guard"


class Replay(str, Enum):
    """Whether sending the identical request again could ever succeed.

    Why this is **declared and not derived**, unlike every other
    classification in this module
    -----------------------------------------------------------------
    :attr:`ApiCode.is_client_facing` is a property because
    :attr:`~ApiCode.status`, :attr:`~ApiCode.surface` and
    :attr:`~ApiCode.reach` already determine it. This is not derivable
    from anything an entry holds, and the pair that forced the field
    proves it: ``artifact_storage_unavailable`` and
    ``artifact_object_missing`` are both reported by one handler, from one
    exception type, about one subsystem, and they need opposite answers.
    The 503 is a store that did not answer, so waiting is exactly right.
    The 502 is a committed row pointing at bytes that are gone, so waiting
    is a loop that cannot terminate. No field distinguishes them because
    the distinction is a fact about the failure, not about the entry.

    So it is a declaration, and it carries a declaration's obligations:
    :attr:`Replay.never_succeeds` requires a :attr:`ApiCode.note` saying
    what makes the condition deterministic, for the reason
    :attr:`Reach.guard` requires one. An unexplained claim is the kind
    that rots, and this claim tells a client to stop trying.

    Why it is not the client's ``RejectionCode`` enum
    ------------------------------------------------
    Because these are 5xx codes and that enum means "a refusal of
    something the caller did". Widening
    :attr:`ApiCode.is_client_facing` to admit them would move seven
    entries into an enum named for rejections, three of which
    (``database_unavailable``, ``query_timeout``,
    ``schema_not_initialized``) refuse nothing at all. The retry question
    and the branch-on-it question are genuinely different questions, and
    the honest answer is a second export rather than one wider set — see
    ``clients/python/src/tckdb_client/rejection_codes.py``.
    """

    #: Replaying might work. The default, and the only safe default: a
    #: client that stops retrying a genuinely transient failure because
    #: nobody classified it is a worse outcome than the wasted attempts
    #: this field exists to prevent.
    may_succeed = "may_succeed"

    #: The condition is deterministic. The identical request will meet the
    #: identical answer however long the caller waits, so a retry
    #: schedule is a loop with no exit. Emitted to clients as
    #: ``NON_RETRYABLE_CODES`` and consulted by
    #: :meth:`tckdb_client.retry.RetryPolicy.code_is_retryable`.
    #:
    #: Only worth declaring where the status would otherwise *invite* a
    #: retry. Marking a 422 is true and inert — no client retries a 422 —
    #: and an inert declaration is a comment with a type, so the guard in
    #: ``backend/tests/api/test_api_code_catalogue.py`` refuses one.
    never_succeeds = "never_succeeds"


class Shape(str, Enum):
    """Whether the code names a *thing* or a *relationship between things*.

    Why this classification and not "does it have ``context`` yet"
    -------------------------------------------------------------
    The error contract tells clients to branch on :attr:`ApiCode.code` and
    read the envelope's structured ``context``, never the prose ``detail``
    — see :mod:`app.api.error_contract`. That advice is right, and for a
    large part of this catalogue ``context`` is an empty object, so the
    advice reduced to "read this empty dict" and the only place the reason
    lived was the prose a client was told not to parse.

    The obvious repair — populate ``context`` everywhere — is wrong. Most
    refusals have nothing to put there. Calvin's rule (2026-08-17) says
    which do:

        A code naming a **relationship** — conflict, mismatch, ambiguity —
        needs ``context``, because it asserts something about two or more
        things and names none of them. A code naming a **thing** —
        ``unknown_X``, ``missing_Y`` — does not, because the name is the
        whole message.

    ``unknown_release`` is complete as it stands: a client that reads the
    code knows exactly what happened and what to fix. ``state_conflict``
    is not: it says two things disagreed and names neither, so without
    ``context`` the only way to find out which is to read English.

    What this field is, and what it is not
    --------------------------------------
    It is a statement about the **code**, in the same way :class:`Surface`
    is a statement about mechanism: a fact that does not change when the
    implementation does. It is deliberately *not* a record of whether the
    obligation has been met — that is measured from the response body by
    the per-code wire tests, not declared here, because a declaration
    would go stale in the direction that hides work rather than the
    direction that fails.

    So a :attr:`relationship` entry with an empty ``context`` today is a
    known gap, not a lie: the classification is what makes the gap
    countable, and nothing in the test suite asserts that any such
    ``context`` *stays* empty. Pinning the empty object would make every
    future improvement cost a red test, which is why the suite carefully
    does not do it.

    Why the default is :attr:`thing`, and why that is safe
    -----------------------------------------------------
    Because most codes are things (measured: roughly four in seven, and
    it was five in eight before the cap family moved), so
    declaring the majority case would be noise of the kind
    :attr:`Reach.request` and :attr:`Replay.may_succeed` also avoid. The
    risk a default carries is the opposite of theirs, though: an
    unclassified *relationship* would read as a thing and quietly excuse
    itself from the obligation.

    That is closed by :data:`RELATIONSHIP_WORDS` and its guard in
    ``backend/tests/api/test_api_code_catalogue.py``. A code whose *name*
    contains a word that can only describe a relation between two things
    must declare :attr:`relationship`; the guard covers the spellings the
    codes here actually use, and it is the reason a new ``*_mismatch``
    cannot arrive classified as a thing by omission. The converse is not
    guarded and cannot be: plenty of relationships are spelled without
    one of those words — ``atom_map_not_a_bijection``,
    ``selection_already_stands``, ``multiple_structure_queries`` — so
    those are declared by hand and the declaration is the judgement.

    The disclosure line
    -------------------
    Deciding a code is a relationship settles that it owes ``context``.
    It does not settle *what goes in it*, and for the cap family the two
    questions come apart, because a cap refusal compares two numbers of
    different kinds. Calvin's rule of 2026-08-18:

        **Publish the THRESHOLD. Never publish the MEASURED VALUE that
        crossed it, where that value describes the corpus rather than the
        caller's own request.**

    A configured cap is a fact about TCKDB, and one a client must have to
    retry. A measured count is a fact about **how much data exists** —
    precisely the enumeration exposure
    ``docs/specs/public_identifier_policy.md`` argues against when it
    rejects sequential integer primary keys (§"Why this matters now",
    item 3: they "leak the total count of objects (and roughly the upload
    schedule) of every table"). A refusal that reports an exact match
    count turns any filter into a free census endpoint, which is worse
    than a primary key: a key leaks the count once, a countable refusal
    leaks it per query, forever, and cheaply.

    Three categories, and only the third is withheld:

    * **The threshold** — ``limit_max``, ``offset_max``, ``max_atoms``,
      ``max_length``, ``cap``, ``max_traversable``. TCKDB's own
      configuration. Always published.
    * **The caller's own request echoed back** — the ``limit`` they sent,
      the ``offset`` they sent, the length of the SMILES they sent.
      Telling someone they asked for 5000 discloses nothing they did not
      already know. Published.
    * **A server-side measurement of the corpus** — how many records
      matched, how many rows exist, how many entries a table holds.
      Logged, never published, and absent from ``detail`` as well as
      ``context``: ``detail`` is published too, so omitting it from one
      and not the other would be decorative.

    The discriminator between the second and third categories is not
    "did the server compute it" — the server computes the second as well.
    It is **what the number is about**. A geometry's atom count is
    chemistry: a property of one molecule the caller named, and strictly
    less than the same endpoint returns for any request under the cap. A
    ``len(...)`` over rows TCKDB stores is holdings. The first is
    published; the second is not.

    Four codes sit on the withheld side and carry the threshold alone:
    ``export_all_cap_exceeded``, ``ml_export_all_cap_exceeded``,
    ``query_too_expensive`` and
    ``composed_search_candidate_limit_exceeded``. That the count is
    *absent* is asserted per code in
    ``backend/tests/api/test_api_query_caps.py``, because an omission is
    the one property no other test notices when it is lost.
    """

    #: The name is the whole message. A client that reads the code knows
    #: what happened; ``context`` would repeat it in a second spelling.
    #: The default.
    thing = "thing"

    #: The code asserts a disagreement, a collision or an ambiguity
    #: between two or more things and names none of them, so ``context``
    #: is the only place the *which* can live. What goes in it is the
    #: things themselves — a field path, a public ref, a declared local
    #: key, a count — and never an internal identifier: not a database
    #: primary key (DR-0028 Requirement 2), and since 2026-08-18 not a
    #: raw database constraint name either. Both are logged instead, for
    #: the same reason: they are ours, not the depositor's, they do not
    #: survive a restore or a rename, and no public surface is keyed on
    #: them. A constraint that deserves a public contract earns a code of
    #: its own through the scientific check register — see
    #: :func:`app.api.errors._integrity_error_handler`.
    relationship = "relationship"


#: Words that can only describe a relation between two or more things.
#: A code whose name contains one of these must declare
#: :attr:`Shape.relationship`; the guard over this constant lives in
#: ``backend/tests/api/test_api_code_catalogue.py``.
#:
#: Deliberately short, and deliberately not "every word that hints at a
#: comparison". ``too_large``, ``exceeds`` and ``requires`` were all
#: considered and left out: each compares a supplied value against a
#: server-side limit or a sibling field that the *code itself already
#: names*, so the name is the whole message and forcing them into
#: :attr:`Shape.relationship` would turn a judgement into an accident of
#: vocabulary. See the borderline notes in the module docstring.
RELATIONSHIP_WORDS: tuple[str, ...] = (
    "ambiguous",
    "conflict",
    "contradicts",
    "disagree",
    "mismatch",
    "not_conserved",
)


#: Statuses a client may reasonably replay, so the statuses where a
#: :attr:`Replay.never_succeeds` declaration changes an outcome rather
#: than merely being true. Mirrors
#: :data:`tckdb_client.retry.DEFAULT_RETRY_STATUS_CODES`, spelled again
#: here because ``backend/app`` must not import the client package —
#: the same one-way rule that keeps ``clients/python`` free of ``app``.
#: Widening the client's set can only make this conservative; narrowing
#: it is what the guard over this constant is watching for.
REPLAYABLE_STATUSES: frozenset[int] = frozenset({429, 502, 503, 504})


@dataclass(frozen=True)
class ApiCode:
    """One code the API can report, and how it gets there.

    :param code: The wire string, exactly as it appears in the body.
        Changing it changes a published contract.
    :param status: The HTTP status that carries it. A code enforced in
        two places can arrive at two statuses — the atom map's claims are
        stated at the wire boundary and again as a check constraint — and
        that gets two entries, because the retry advice differs and a
        client reads it off the status.
    :param surface: The mechanism — see :class:`Surface`.
    :param origin: Repo-relative module that *defines* the literal, with
        no line number (a line-number anchor turns a cosmetic edit into a
        red gate; see :attr:`app.scientific_checks.PythonCheck.location`
        for the incident). Where a code is minted from a variable this is
        the module holding the constant, not the module that raises.
    :param reach: Whether a request can produce it — see :class:`Reach`.
        Defaults to :attr:`Reach.request`, so the classification costs
        nothing to the entries where it is uninteresting and has to be
        written down where it is not.
    :param replay: Whether replaying the request could succeed — see
        :class:`Replay`. The one classification here that is declared
        rather than derived, because nothing an entry holds implies it.
        Defaults to :attr:`Replay.may_succeed`, which is the fail-safe
        direction.
    :param shape: Whether the code names a thing or a relationship
        between things, and therefore whether it owes a client a populated
        ``context`` — see :class:`Shape`. Declared, because no other field
        implies it; defaults to :attr:`Shape.thing` with
        :data:`RELATIONSHIP_WORDS` guarding the omission that would matter.
    :param note: A fact the site cannot state about itself. Deliberately
        rare — this is an enumeration, not a second copy of the refusal
        messages. Required on every :attr:`Reach.guard` entry, because
        "no request can produce this" is a claim and an unexplained claim
        is the kind that rots — and on every
        :attr:`Replay.never_succeeds` entry, for the same reason.
    """

    code: str
    status: int
    surface: Surface
    origin: str
    reach: Reach = Reach.request
    replay: Replay = Replay.may_succeed
    shape: Shape = Shape.thing
    note: str | None = None

    @property
    def owes_context(self) -> bool:
        """Whether a client is owed structured facts beside this code.

        True exactly for :attr:`Shape.relationship`. A separate property
        rather than a bare comparison so the obligation has a name the
        tests and the client-facing docs can both cite, and so that
        narrowing it later — should a relationship code turn out to state
        its things in a structured ``detail`` instead, as
        ``tckdb_client_version_unsupported`` does — is one edit here rather
        than a search for ``is Shape.relationship``.
        """
        return self.shape is Shape.relationship

    @property
    def is_client_facing(self) -> bool:
        """Whether a client should be able to import and branch on this.

        This answers *can a caller branch on it*, and since #234 it no
        longer doubles as *should a caller retry it*. Those are different
        questions, and answering both with one rule is why a 5xx a client
        genuinely needs had nowhere to go: ``artifact_object_missing`` is
        correctly refused here — the caller did nothing wrong, so there is
        no branch for them to write — while being precisely the code a
        retry layer must recognise. The second question is answered by
        :attr:`replay` and exported separately; see
        :func:`never_retryable`.

        Derived, never declared. A 5xx is not a refusal of anything the
        caller did; a generic fallback repeats the status; an accidental
        prefix is not a contract at all; and a guard no request can trip
        is a code the caller will never see, so a branch written for it
        is a branch that never runs — the same silent non-event a
        hard-coded typo produces, which is what generating this enum
        exists to remove. Everything else is a refusal a caller can act
        on, and the point of this module is that acting on it must not
        require hard-coding a string.
        """
        return (
            400 <= self.status < 500
            and self.reach is Reach.request
            and self.surface
            not in {
                Surface.generic_fallback,
                Surface.accidental_prefix,
            }
        )

    @property
    def is_replay_futile(self) -> bool:
        """Whether a client's retry layer should refuse to replay this.

        The :attr:`replay` declaration, narrowed to the entries where it
        does any work. Two conditions beyond the declaration itself:

        * :attr:`Reach.request`, because a code no request can produce is
          a code no retry layer will ever be handed — the same reason a
          guard is not exported to the client enum.
        * A status in :data:`REPLAYABLE_STATUSES`, because a client does
          not retry the others anyway. Marking a 422 ``never_succeeds``
          is true and changes nothing, and publishing it would grow the
          exported set with entries that cannot be observed to matter.

        Note that this is *not* the complement of anything: an entry that
        is neither client-facing nor replay-futile is the ordinary case —
        a transient 5xx a client should keep retrying and has no branch
        for.
        """
        return (
            self.replay is Replay.never_succeeds
            and self.reach is Reach.request
            and self.status in REPLAYABLE_STATUSES
        )


#: Every code the API can report, ordered by code so a diff is readable.
#:
#: Adding one is cheap on purpose: an entry claims only that the code
#: exists and where it comes from. That is the whole point of separating
#: this from :mod:`app.scientific_checks`, whose entries claim something a
#: referee could argue with and must stay expensive.
CATALOGUE: tuple[ApiCode, ...] = (
    ApiCode("ambiguous_conformer_selection_locator", 422, Surface.coded_exception,
            "backend/app/services/conformer_selection_locator.py",
            shape=Shape.relationship,
            note=(
                "Shape.relationship, and it was already meeting the "
                "obligation before there was a field to name it: the code "
                "says several rows matched and names none of them, while "
                "Discrimination.as_context reports match_count, differs_by, "
                "discriminator, discriminator_values and locator_can_express "
                "-- the things that differ, never their ids. So this entry "
                "needed a declaration, not a repair. It is also the first "
                "code to arrive after RELATIONSHIP_WORDS existed, and it "
                "arrived misclassified by omission: written before Shape "
                "was, it took the Shape.thing default and the word guard "
                "refused it on the merge. Nothing else would have -- the "
                "merge was textually clean. "
                "The 'several matched' half of a split; unknown_conformer_"
                "selection is the 'none matched' half, at 404. Both used to "
                "be one bare ValueError reading 'must resolve exactly one "
                "selection', so 422 validation_error with an empty context "
                "covered two opposite repairs -- deposit the missing row, "
                "versus say more about which row you meant. 422 and not 409 "
                "because every candidate exists: the world is consistent and "
                "the request is under-determined. context reports what "
                "actually differs across the candidates (discriminator, "
                "discriminator_values) and whether the locator has a field "
                "for it (locator_can_express), because 'be more specific' is "
                "only advice if there is something to add. For a long time "
                "there was not: the lookup filtered on all three locator "
                "fields, so candidates agreed on all three and could only "
                "differ by the conformer group, which the locator could "
                "not name -- every observed refusal reported "
                "locator_can_express=false, which was the evidence the "
                "locator needed widening. It has since been widened. The "
                "locator carries an optional conformer_group_ref, the "
                "field is filtered on when supplied, and this refusal now "
                "names it: discriminator=conformer_group_ref, "
                "locator_can_express=true, and discriminator_values listing "
                "the cg_ refs to choose between. The code, the status and "
                "every context key are unchanged -- what changed is that "
                "the advice can now be followed."
            )),
    ApiCode("applied_energy_correction_source_calculation_owner_mismatch", 422, Surface.coded_exception,
            "backend/app/services/calculation_ownership.py",
            shape=Shape.relationship,
            note=(
                "Reach.guard until #195, and the note it carried named the "
                "repair that ended it: /uploads/computed-reaction resolves "
                "source_calculation_key in a namespace spanning every "
                "species and the transition state, then attaches the "
                "correction to one of them. Both of its ownership "
                "comparisons were inline and raised a bare ValueError, so "
                "the mistake answered validation_error; both now call the "
                "shared guard. The three older call sites still cannot "
                "produce it -- they read from a key map built for the "
                "target's own owner -- but reachability is a property of "
                "the code, not of every site that raises it."
            )),
    ApiCode("applied_energy_correction_source_key_undeclared", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/local_key_codes.py"),
    ApiCode("arrhenius_a_units_molecularity_mismatch", 422, Surface.coded_exception,
            "backend/app/chemistry/units.py",
            shape=Shape.relationship),
    ApiCode("artifact_integrity_failed", 502, Surface.response_literal,
            "backend/app/api/errors.py",
            replay=Replay.never_succeeds,
            note=(
                "Replay.never_succeeds because the verification is a "
                "comparison of stored bytes against a digest recorded when "
                "they were accepted, and both sides are fixed: the digest "
                "is immutable and the bytes are whatever they now are. The "
                "same request meets the same mismatch forever. The handler's "
                "own sentence already says so -- 'retrying will not clear "
                "it' -- and until #234 no client could act on it, because "
                "502 is in the client's default retry set and the code that "
                "contradicts it was excluded from the exported enum by "
                "is_client_facing's 4xx rule. Repair is an operator "
                "restoring the object, not a caller waiting."
            )),
    ApiCode("artifact_object_missing", 502, Surface.response_literal,
            "backend/app/api/errors.py",
            replay=Replay.never_succeeds,
            note=(
                "Replay.never_succeeds: the store answered, and what it "
                "answered was that the key is not there. Nothing about "
                "waiting changes that -- only an operator putting the "
                "object back does, which is a different request by a "
                "different principal. This is the pair that forced the "
                "Replay field to exist: it and artifact_storage_unavailable "
                "leave one handler, from one exception type, with opposite "
                "retry advice and no field between them that a property "
                "could read. "
                "Split from artifact_storage_unavailable in #226, which "
                "carried both 'the store did not answer' and 'the store "
                "answered and the object is not there' — opposite retry "
                "advice under one code. Told apart by "
                "ArtifactStorageUnavailable.missing, set from the S3 error "
                "code in load_artifact_bytes; the exception type is "
                "deliberately not split, so every opportunistic "
                "'except ArtifactStorageUnavailable' still catches both. "
                "502 rather than 404 or 410 although the bytes are gone: "
                "the request was well formed and the artifact record is "
                "still published, so the failure is TCKDB's custody and "
                "not the caller's mistake. 404 would also collide with "
                "this route's deliberate unknown-or-unapproved-digest "
                "answer, and 410 would invite a client to drop the "
                "reference that makes reclaim_restore possible. Its "
                "sibling artifact_integrity_failed sits at the same "
                "status for the same reason; the two differ in which "
                "artifact_integrity_event finding was recorded "
                "(object_missing vs digest/size mismatch) and therefore "
                "in what an operator does next."
            )),
    ApiCode("artifact_storage_full", 507, Surface.response_literal,
            "backend/app/api/errors.py",
            note=(
                "The third fact one handler can learn about the object "
                "store, after 'it did not answer' (503) and 'it answered, "
                "and the object is not there' (502): it answered, and it "
                "has no room. Told apart by "
                "ArtifactStorageUnavailable.full, set from the store's own "
                "error code in artifact_storage._raise_write_refusal. "
                "Measured, not inferred: MinIO answers XMinioStorageFull "
                "at 507 when the drive free-space threshold is breached, "
                "and XMinioAdminBucketQuotaExceeded at 400 when a hard "
                "bucket quota is passed. TCKDB reports 507 for both, "
                "because the upstream 400 classifies MinIO's own API call "
                "and not the depositor's request -- the depositor did "
                "nothing wrong either way. "
                "507 rather than a second code at 503 because the status "
                "must carry the advice on its own: 507 is registered with "
                "exactly this meaning (RFC 4918) and is absent from "
                "tckdb_client.retry.DEFAULT_RETRY_STATUS_CODES, so a "
                "pinned client and a non-Python caller both stop retrying "
                "without knowing this code exists. At 503 the status would "
                "invite the retry and only NON_RETRYABLE_CODES could "
                "contradict it, making correct behaviour depend on the "
                "caller having upgraded. "
                "Deliberately at the Replay.may_succeed default, and not a "
                "new Replay member for 'retryable, but not by you, and not "
                "soon'. Replay exists only to contradict a status that "
                "invites a retry, so a declaration at 507 would be inert "
                "by is_replay_futile's own rule; and never_succeeds would "
                "be a false claim, because its note must say what makes "
                "the condition deterministic and a full store is not -- an "
                "operator frees space and the identical request then "
                "succeeds. The vocabulary is adequate; the status carries "
                "what the vocabulary cannot."
            )),
    ApiCode("artifact_storage_unavailable", 503, Surface.response_literal,
            "backend/app/api/errors.py",
            note=(
                "Now means only what its sentence says: the store did not "
                "answer, and retrying is the right response. The case "
                "where it answered 'no such key' left this code in #226 — "
                "see artifact_object_missing. Deliberately left at the "
                "Replay.may_succeed default: this is the half of the "
                "original pair where the 503's retry advice was true all "
                "along, and it is the entry that makes the new field's "
                "guard non-vacuous."
            )),
    ApiCode("atom_map_atoms_unaccounted_for", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py",
            shape=Shape.relationship),
    ApiCode("atom_map_contradicts_irc_mapping", 422, Surface.coded_exception,
            "backend/app/services/reaction_atom_map.py",
            shape=Shape.relationship),
    ApiCode("atom_map_element_not_conserved", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py",
            shape=Shape.relationship),
    ApiCode("atom_map_element_not_conserved", 409, Surface.database_constraint,
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py",
            shape=Shape.relationship,
            note=(
                "Two entries, and both are real. The claim is stated once at "
                "the wire boundary and again as a check constraint, so which "
                "status a depositor sees depends on the write path rather "
                "than on what they did wrong. The origin is the same for "
                "both because the literal has one home: the register names "
                "the constraint by importing this constant, not by "
                "respelling the code."
            )),
    ApiCode("atom_map_geometry_unparseable", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py"),
    ApiCode("atom_map_indices_not_geometry_relative", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py",
            shape=Shape.relationship),
    ApiCode("atom_map_inferred_requires_note", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py"),
    ApiCode("atom_map_not_a_bijection", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py",
            shape=Shape.relationship),
    ApiCode("atom_map_not_a_bijection", 409, Surface.database_constraint,
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py",
            shape=Shape.relationship),
    ApiCode("atom_map_participant_not_declared", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py"),
    ApiCode("atom_map_without_transition_state", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py"),
    ApiCode("calculation_geometry_composition_mismatch", 422, Surface.coded_exception,
            "backend/app/services/calculation_geometry_composition.py",
            shape=Shape.relationship,
            note=(
                "A geometry linked to a calculation is not made of the atoms "
                "of the subject the calculation is filed under. One code "
                "across both owner kinds -- a species entry's own formula, or "
                "a transition state's reaction reactant sum -- because the "
                "field is the same geometry link and the repair is the same "
                "sentence; context['owner_kind'] says which reference "
                "disagreed. Distinct from "
                "species_geometry_composition_mismatch (a conformer geometry "
                "against its species entry) and "
                "transition_state_composition_mismatch (the saddle point a "
                "TS entry is resolved from): those are repaired in a "
                "payload's identity block, this one on a calculation."
            )),
    ApiCode("calculation_handle_conflict", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/handles.py",
            shape=Shape.relationship),
    ApiCode("calculation_key_undeclared", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/local_key_codes.py",
            note=(
                "One code for every bundle field that names a calculation by "
                "local key -- thermo and statmech source links, a torsion's "
                "rotor scan, a dependency's parent, IRC evidence, a PDep "
                "solve's energies, barriers and source calculations -- "
                "because they resolve against one namespace and are repaired "
                "one way: declare the calculation, or fix the spelling "
                "against the list the refusal prints. context['field'] says "
                "which one asked. That is the test the ownership family "
                "fails, where two fields need two different right answers. "
                "An applied energy correction's source key keeps its own "
                "older code for the same reason in reverse: there, a "
                "conformer name and a calculation name are one repair."
            )),
    ApiCode("canonical_parameter_value_requires_key", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/calculations_search.py"),
    ApiCode("client_sort_not_supported", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/common.py"),
    ApiCode("conformer_key_undeclared", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/local_key_codes.py",
            note=(
                "A calculation's 'conformer_key' -- the field that says which "
                "basin the calculation is evidence for. Distinct from "
                "'applied_energy_correction_source_key_undeclared', which also "
                "spans conformer names but answers a different question (where "
                "a correction came from) and is pinned on /uploads/conformers."
            )),
    ApiCode("composed_search_candidate_limit_exceeded", 422, Surface.coded_exception,
            "backend/app/services/scientific_read/common.py",
            shape=Shape.relationship,
            note=(
                "Two raise sites in collect_bounded_pages, and they are not "
                "the same disclosure question. The first compares the "
                "*match count* against public_max_offset + page_size: the "
                "bound is published as context['max_traversable'] and the "
                "match count is logged, because a filter that can be turned "
                "into an exact row count is a census endpoint TCKDB does "
                "not offer. The second simply ran past the offset bound and "
                "measures nothing, so it publishes offset_max. Both carry "
                "resource."
            )),
    ApiCode("composed_search_invalid_page", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/common.py",
            shape=Shape.relationship),
    ApiCode("composed_search_pagination_changed", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/common.py",
            shape=Shape.relationship),
    ApiCode("composed_search_pagination_stalled", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/common.py",
            shape=Shape.relationship),
    ApiCode("curation_policy_version_conflict", 409, Surface.message_prefix,
            "backend/app/services/release/curation.py",
            shape=Shape.relationship,
            note=(
                "409, not the 422 a bare ReleaseCurationError would reach. "
                "It was recorded wrong for a long time because the status "
                "used to be a property of the *route*: the raise site said "
                "ValueError and one except clause said 409. Since #211 the "
                "raise site says ReleaseStateConflict and the route reads "
                "the status off the class, so the two can no longer "
                "disagree. The observer checking the (status, code) pair is "
                "what caught the original mismatch."
            )),
    ApiCode("curator_task_not_found", 404, Surface.coded_exception,
            "backend/app/services/machine_review/curator_task_lifecycle.py"),
    ApiCode("cursor_offset_conflict", 422, Surface.coded_exception,
            "backend/app/services/scientific_read/analytics.py",
            shape=Shape.relationship),
    ApiCode("cursor_query_mismatch", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/keyset.py",
            shape=Shape.relationship),
    ApiCode("data_integrity_error", 500, Surface.generic_fallback,
            "backend/app/api/errors.py"),
    ApiCode("database_error", 503, Surface.generic_fallback,
            "backend/app/api/errors.py",
            note=(
                "The operational-error handler's fallback_code, and it is "
                "dead: that handler always passes an explicit code "
                "(database_unavailable, or query_timeout on SQLSTATE "
                "57014), and error_envelope reads the fallback only when "
                "code is falsy. Listed because the argument is written "
                "and a future edit that stops setting code would emit it "
                "-- an enumeration that omits a fallback nobody has "
                "reached yet is one that goes stale silently. Found by "
                "widening the closure scan to *_code keywords."
            )),
    ApiCode("database_unavailable", 503, Surface.response_literal,
            "backend/app/api/errors.py"),
    ApiCode("doi_already_recorded", 409, Surface.message_prefix,
            "backend/app/services/release/curation.py",
            shape=Shape.relationship,
            note=(
                "409 since #211. Recording the DOI the release already cites "
                "succeeds; only a *different* one refuses, and it refuses "
                "because a citation is already pointing somewhere. There is "
                "no corrected body -- the deposit would have to be redone."
            )),
    ApiCode("domain_error", 400, Surface.generic_fallback,
            "backend/app/api/errors.py"),
    ApiCode("email_taken", 409, Surface.message_prefix,
            "backend/app/api/routes/auth.py",
            note=(
                "Registration only, and paired with username_taken. Both "
                "arrive at 23505 -- a unique violation says nothing about "
                "which rule fired -- so the two are told apart by the "
                "constraint name psycopg reports in exc.orig.diag, not by "
                "the SQLSTATE and not by parsing the driver's message. "
                "Before #225 both answered http_409 with one sentence "
                "covering two fields, which is why a sign-up form could "
                "not highlight the input at fault; unique_conflict, the "
                "only code the SQLSTATE alone supports, would have been "
                "the same problem with a code attached. Discloses nothing "
                "registration does not already disclose by refusing at "
                "all -- and that argument covers this endpoint only."
            )),
    ApiCode("energy_transfer_scope_columns_disagree", 409, Surface.database_constraint,
            "backend/app/scientific_checks/declarations.py",
            shape=Shape.relationship),
    ApiCode("export_all_cap_exceeded", 422, Surface.coded_exception,
            "backend/app/services/scientific_read/export.py",
            shape=Shape.relationship,
            note=(
                "Shape.relationship since 2026-08-18, and one of the four "
                "codes that publish the threshold and withhold the "
                "measurement. context carries cap and record_type; the "
                "count that crossed it is an unfiltered row count over "
                "reaction_entry -- the corpus total -- so it is logged and "
                "appears in neither context nor detail. See the disclosure "
                "line on Shape."
            )),
    ApiCode("export_seed_empty", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/export.py"),
    ApiCode("export_seed_unresolved", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/export.py"),
    ApiCode("freq_list_exceeds_geometry_degrees_of_freedom", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/frequency_completeness.py",
            shape=Shape.relationship,
            note=(
                "One of two codes that module reports, and the only one that "
                "refuses. Its sibling freq_list_incomplete_for_geometry is an "
                "UploadWarning on an accepted 201 and is deliberately absent "
                "from this catalogue, which enumerates error bodies only. "
                "Minted from a variable: raise_for_blocking_findings reports "
                "whichever code its blocking finding carries, so the scan "
                "cannot see it and the origin is the defining module."
            )),
    ApiCode("freq_mode_index_not_unique", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/calculation.py",
            shape=Shape.relationship,
            note=(
                "Catalogued but deliberately absent from the scientific "
                "check register, unlike its file neighbour "
                "freq_n_imag_disagrees_with_modes. A repeated mode_index is "
                "a malformed list -- a concatenated serialisation, a "
                "restarted counter -- and asserts nothing about the "
                "potential energy surface, so it fails the register's "
                "inclusion test. That is the case this module exists for: a "
                "code a client must branch on, about no chemistry at all."
            )),
    ApiCode("freq_n_imag_disagrees_with_modes", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/calculation.py",
            shape=Shape.relationship),
    ApiCode("geometry_key_unresolved", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/local_key_codes.py",
            note=(
                "The one member of the local-key family that does not say "
                "'undeclared'. A geometry key is declared on a species "
                "conformer or a transition state and resolved as the workflow "
                "walks those owners, so a TS calculation naming a later TS's "
                "geometry names something the payload really contains and the "
                "workflow really cannot resolve -- calling that undeclared "
                "would be false."
            )),
    ApiCode("geometry_too_large", 422, Surface.coded_exception,
            "backend/app/services/scientific_read/geometry.py",
            shape=Shape.relationship,
            note=(
                "Shape.relationship since 2026-08-18. context carries "
                "max_atoms (settings.max_geometry_atoms_public) and atoms "
                "(the requested geometry's own atom count). The second is "
                "not the withheld kind of measurement: it is a property of "
                "one record the caller named, and strictly less than this "
                "endpoint returns for any request under the cap."
            )),
    ApiCode("handle_not_found", 404, Surface.coded_exception,
            "backend/app/services/scientific_read/calculation_paths.py"),
    ApiCode("handle_type_mismatch", 422, Surface.coded_exception,
            "backend/app/services/scientific_read/handles.py",
            shape=Shape.relationship,
            note=(
                "Surface.coded_exception since #210, and the detail did not "
                "move a byte. It was message_prefix -- two "
                "ValueError(f'handle_type_mismatch: ...') sites -- which is "
                "the surface with nowhere to put context, and this code "
                "names a relationship: two ref prefixes that are not the "
                "same one, neither of them named. Both sites now raise "
                "CodedValueError with message_prefix=True, so str(exc) is "
                "unchanged and only the route into `code` differs: declared "
                "on the exception rather than promoted out of the sentence. "
                "It leaves MESSAGE_PREFIX_CODES as a consequence, which is "
                "correct -- the envelope no longer needs to read English to "
                "find this code. Its six *_handle_conflict neighbours in the "
                "same module have the same defect and are *not* converted "
                "here: their code is minted from reconcile_id_ref's "
                "conflict_code parameter, and test_error_contract_catalogue_"
                "gate.py's TestTheShapeTheRaiseSiteScanCannotRead is written "
                "over exactly that spelling, so moving them means "
                "re-pointing that gate at the new shape rather than "
                "deleting it."
            )),
    ApiCode("idempotency_conflict", 409, Surface.response_literal,
            "backend/app/api/errors.py",
            shape=Shape.relationship),
    ApiCode("idempotency_in_progress", 409, Surface.response_literal,
            "backend/app/api/errors.py",
            shape=Shape.relationship,
            reach=Reach.guard,
            note=(
                "The ternary that mints it has one live arm. "
                "IdempotencyConflict.in_progress defaults to False and the "
                "one construction site never passes it, so no response can "
                "carry this code. Not a milestone that has not arrived: "
                "docs/specs/upload-idempotency-key-spec.md lists it under "
                "*Optional* -- 'Only implement idempotency_in_progress if "
                "needed by the chosen approach' -- and the approach chosen, a "
                "unique constraint plus the integrity handler, does not need "
                "it. It is a contingency, and app/api/idempotency.py names "
                "the change that would make it live."
            )),
    ApiCode("include_not_implemented_yet", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/calculations.py"),
    ApiCode("integrity_conflict", 409, Surface.generic_fallback,
            "backend/app/api/errors.py",
            shape=Shape.relationship),
    ApiCode("invalid_cursor", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/keyset.py"),
    ApiCode("invalid_handle", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/handles.py"),
    ApiCode("invalid_idempotency_key", 400, Surface.response_literal,
            "backend/app/api/errors.py"),
    ApiCode("invalid_pagination", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/common.py"),
    ApiCode("invalid_range", 422, Surface.coded_exception,
            "backend/app/services/scientific_read/analytics.py",
            shape=Shape.relationship),
    ApiCode("invalid_structure_query", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/structure_search.py"),
    ApiCode("invalid_temperature_range", 422, Surface.coded_exception,
            "backend/app/services/scientific_read/common.py",
            shape=Shape.relationship,
            note=(
                "Surface.coded_exception since #210, with the detail "
                "byte-identical: the raise moved to CodedValueError with "
                "message_prefix=True so it could carry the two bounds in "
                "context. Its sibling invalid_range in "
                "scientific_read/analytics.py was already coded_exception "
                "and already carried them, which is what made this one's "
                "empty context visible as an inconsistency rather than a "
                "policy."
            )),
    ApiCode("irc_result_not_found", 404, Surface.coded_exception,
            "backend/app/services/scientific_read/calculation_paths.py"),
    ApiCode("kinetics_interpretation_conformer_selection_owner_mismatch", 422, Surface.coded_exception,
            "backend/app/services/calculation_ownership.py",
            shape=Shape.relationship,
            note=(
                "Two raise sites, one repair: cite a conformer record "
                "belonging to the right species entry. The first holds the "
                "resolved selection to the interpretation subject's entry; "
                "the second, added with the locator's conformer_group_ref, "
                "holds the named conformer group to the entry the locator "
                "itself names -- a locator whose two halves contradict each "
                "other. Not a new code, by the argument the sibling "
                "kinetics_interpretation_statmech_owner_mismatch already "
                "carries: context['field'] separates them "
                "(...conformer_selection versus "
                "...conformer_selection.conformer_group_ref) and the "
                "corrective action is the same. 422 and not 404 because "
                "the group exists -- a 404 would tell a depositor to "
                "deposit a row they can already read."
            )),
    ApiCode("kinetics_interpretation_statmech_owner_mismatch", 422, Surface.coded_exception,
            "backend/app/services/calculation_ownership.py",
            shape=Shape.relationship,
            note=(
                "One code, two comparisons: a reactant/product assignment "
                "is held to the participant's species entry and a "
                "transition-state one to the declared TS entry. Not two "
                "entries, because the field and the repair are the same "
                "-- name the statmech belonging to this subject -- and "
                "which owner disagreed is already in context['owner_kind']."
            )),
    ApiCode("level_of_theory_handle_conflict", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/handles.py",
            shape=Shape.relationship),
    ApiCode("limit_too_large", 422, Surface.coded_exception,
            "backend/app/services/scientific_read/common.py",
            shape=Shape.relationship,
            note=(
                "Split out of invalid_pagination, which covered four "
                "conditions with three different remedies. A limit above the "
                "hosted cap is recoverable by resending the same query with "
                "a smaller page size; a negative offset is a caller bug. "
                "Unreachable through a GET route in the shipped "
                "configuration, where MAX_LIMIT and public_max_limit are "
                "both 200 and Query(le=200) refuses anything larger first -- "
                "reachable through a POST search body, and through any GET "
                "on a deployment that lowers public_max_limit. "
                "Shape.relationship since Calvin's overrule of 2026-08-18; "
                "context carries limit_max (a setting) and limit (the "
                "caller's own value). Surface.coded_exception from the same "
                "change, and detail did not move a byte."
            )),
    ApiCode("lowest_energy_unavailable", 422, Surface.coded_exception,
            "backend/app/services/scientific_read/species_calculations_search.py"),
    ApiCode("manifest_already_frozen", 409, Surface.message_prefix,
            "backend/app/services/release/manifest.py",
            note=(
                "409 since #211, for consistency with its module neighbour "
                "release_not_published rather than because a caller reported "
                "it: no route reaches it, because POST /publish calls "
                "publish_release first and that raises release_not_draft. The "
                "branch is live all the same -- a service-level test calls "
                "freeze_manifest directly -- so it keeps an entry. Whether it "
                "should be Reach.guard, and therefore unexported, is a "
                "separate question this pass did not settle."
            )),
    ApiCode("manifest_not_frozen", 404, Surface.message_prefix,
            "backend/app/api/routes/scientific/releases.py"),
    ApiCode("missing_filter", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/artifacts_search.py"),
    ApiCode("missing_identifier", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/species.py"),
    ApiCode("missing_reaction_search_filter", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/reactions.py"),
    ApiCode("missing_structure_query", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/structure_search.py"),
    ApiCode("ml_export_all_cap_exceeded", 422, Surface.coded_exception,
            "backend/app/services/scientific_read/ml_dataset.py",
            shape=Shape.relationship,
            note=(
                "The ML surface's own cap, and the same call as its native "
                "sibling export_all_cap_exceeded: context carries cap and "
                "record_type, and the species_entry row count that crossed "
                "it is logged rather than published."
            )),
    ApiCode("ml_export_lot_unresolved", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/ml_dataset.py"),
    ApiCode("ml_export_seed_empty", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/ml_dataset.py"),
    ApiCode("ml_export_seed_unresolved", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/ml_dataset.py"),
    ApiCode("micro_reaction_key_undeclared", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/local_key_codes.py"),
    ApiCode("multiple_structure_queries", 422, Surface.coded_exception,
            "backend/app/services/scientific_read/structure_search.py",
            shape=Shape.relationship,
            note=(
                "Surface.coded_exception since #210, detail unchanged. An "
                "ambiguity code that named none of the fields it was "
                "ambiguous between; context['supplied'] now does. Its "
                "neighbour missing_structure_query stays message_prefix "
                "and stays empty on purpose: it names a thing (no query "
                "was supplied) and the code is the whole message."
            )),
    ApiCode("n_imag_contradicts_minimum", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py",
            shape=Shape.relationship),
    ApiCode("network_channel_key_undeclared", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/local_key_codes.py"),
    ApiCode("network_solve_reported_requires_literature", 409, Surface.database_constraint,
            "backend/app/scientific_checks/declarations.py"),
    ApiCode("network_state_key_undeclared", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/local_key_codes.py"),
    ApiCode("non_finite_value", 422, Surface.message_prefix,
            "backend/app/services/release/artifacts.py"),
    ApiCode("offset_too_large", 422, Surface.coded_exception,
            "backend/app/services/scientific_read/common.py",
            shape=Shape.relationship,
            note=(
                "The sibling of limit_too_large, and the reason they are two "
                "codes rather than one: retrying with a smaller offset "
                "returns different rows, so this one is not recoverable by "
                "resending. Reachable on any paginated read against the "
                "shipped configuration -- offset carries no upper bound at "
                "the route, so settings.public_max_offset is the only one. "
                "Shape.relationship since 2026-08-18; context carries "
                "offset_max (a setting) and offset (the caller's own)."
            )),
    ApiCode("owner_missing", 404, Surface.coded_exception,
            "backend/app/services/scientific_read/calculations.py"),
    ApiCode("parameter_value_requires_key", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/calculations_search.py"),
    ApiCode("path_search_result_not_found", 404, Surface.coded_exception,
            "backend/app/services/scientific_read/calculation_paths.py"),
    ApiCode("post_search_fields_must_be_in_body", 422, Surface.message_prefix,
            "backend/app/api/routes/scientific/artifacts.py"),
    ApiCode("pressure_alias_conflict", 422, Surface.message_prefix,
            "backend/app/schemas/reads/scientific_kinetics.py",
            shape=Shape.relationship),
    ApiCode("query_timeout", 503, Surface.response_literal,
            "backend/app/api/errors.py"),
    ApiCode("query_too_expensive", 422, Surface.coded_exception,
            "backend/app/services/scientific_read/provenance.py",
            shape=Shape.relationship,
            note=(
                "Shape.relationship since 2026-08-18, published "
                "threshold-only. context carries section (which sub-array "
                "was the offender) and cap. len(block) -- how many "
                "calculations, geometries, artifacts, conformer groups or "
                "atom-map pairs TCKDB holds for the requested record -- is "
                "a count of the corpus, not a property of the caller's "
                "request, so it is logged. A caller who could read it off a "
                "cheap refusal would have a row-counting oracle."
            )),
    ApiCode("rate_limit_exceeded", 429, Surface.response_literal,
            "backend/app/api/rate_limit.py"),
    ApiCode("rationale_required", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
    ApiCode("reaction_charge_not_conserved", 422, Surface.coded_exception,
            "backend/app/services/reaction_resolution.py",
            shape=Shape.relationship),
    ApiCode("reaction_mass_balance_failed", 422, Surface.coded_exception,
            "backend/app/services/reaction_resolution.py",
            shape=Shape.relationship),
    ApiCode("record_has_no_subject", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
    ApiCode("record_not_approved", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
    ApiCode("record_ref_not_selectable", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
    ApiCode("record_subject_mismatch", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py",
            shape=Shape.relationship),
    ApiCode("record_type_not_selectable", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
    ApiCode("reaction_entry_handle_conflict", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/handles.py",
            shape=Shape.relationship),
    ApiCode("reaction_handle_conflict", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/handles.py",
            shape=Shape.relationship),
    ApiCode("reference_conflict", 409, Surface.sqlstate_category,
            "backend/app/api/errors.py",
            shape=Shape.relationship),
    ApiCode("release_artifact_corrupt", 500, Surface.message_prefix,
            "backend/app/api/routes/scientific/releases.py"),
    ApiCode("release_not_draft", 409, Surface.message_prefix,
            "backend/app/services/release/curation.py",
            note=(
                "409 since #211. A published release is frozen and cited; no "
                "corrected body makes a second publish or a late selection "
                "succeed, so 422 was telling a client to resend something "
                "that could never be accepted. It raises ReleaseStateConflict, "
                "and the route reads the status off the class."
            )),
    ApiCode("release_not_published", 409, Surface.message_prefix,
            "backend/app/services/release/curation.py",
            note=(
                "409 since #211, from two modules: curation.py refuses to "
                "withdraw or attach a DOI to a release that was never "
                "published, and manifest.py refuses to freeze one. Both are "
                "the same sentence about the same state, so they share a "
                "code and now share a status; the curation.py literal is the "
                "one the drift guard checks."
            )),
    ApiCode("release_scoping_not_implemented", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/profile.py"),
    ApiCode("release_selects_nothing", 422, Surface.message_prefix,
            "backend/app/services/release/manifest.py"),
    ApiCode("release_tag_taken", 409, Surface.message_prefix,
            "backend/app/services/release/curation.py",
            note=(
                "409 for the same reason as "
                "curation_policy_version_conflict, and by the same mechanism "
                "since #211: it raises ReleaseStateConflict, which the route "
                "maps to 409. A tag that is taken is taken; there is no "
                "corrected body, only a different tag."
            )),
    ApiCode("request_validation_error", 422, Surface.generic_fallback,
            "backend/app/api/errors.py"),
    ApiCode("resource_not_found", 404, Surface.generic_fallback,
            "backend/app/api/errors.py"),
    ApiCode("scan_result_not_found", 404, Surface.coded_exception,
            "backend/app/services/scientific_read/calculation_paths.py"),
    ApiCode("schema_not_initialized", 503, Surface.response_literal,
            "backend/app/api/routes/health.py"),
    ApiCode("selection_already_stands", 409, Surface.message_prefix,
            "backend/app/services/release/curation.py",
            shape=Shape.relationship,
            note=(
                "409 since #211, and it moved with its sibling rather than "
                "after it: this and selection_already_superseded are the same "
                "refusal seen from the two ends of a chain -- a row already "
                "exists that blocks the write -- and leaving one at 422 would "
                "have kept the inconsistency the pass was opened to remove."
            )),
    ApiCode("selection_already_superseded", 409, Surface.message_prefix,
            "backend/app/services/release/curation.py",
            shape=Shape.relationship,
            note=(
                "409 since #211. Supersession chains stay linear, so the row "
                "to replace is the one that currently stands -- a different "
                "URL, not a different body."
            )),
    ApiCode("selection_no_longer_approved", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
    ApiCode("smiles_too_long", 422, Surface.coded_exception,
            "backend/app/schemas/reads/scientific_kinetics_search.py",
            shape=Shape.relationship,
            note=(
                "Shape.relationship since 2026-08-18. Two sites -- this one "
                "and the reaction-search twin -- raise the wire package's "
                "CodedValidationError rather than the backend's subclass, "
                "because app.schemas.reads sits on the wire side. context "
                "carries max_length (a module constant) and length (the "
                "caller's own string, measured). The string is not echoed."
            )),
    ApiCode("species_entry_handle_conflict", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/handles.py",
            shape=Shape.relationship),
    ApiCode("species_geometry_composition_mismatch", 422, Surface.coded_exception,
            "backend/app/services/species_resolution.py",
            shape=Shape.relationship),
    ApiCode("species_geometry_isotope_mismatch", 422, Surface.coded_exception,
            "backend/app/services/species_resolution.py",
            shape=Shape.relationship),
    ApiCode("species_handle_conflict", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/handles.py",
            shape=Shape.relationship),
    ApiCode("species_key_undeclared", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/local_key_codes.py"),
    ApiCode("species_kind_conflict", 422, Surface.coded_exception,
            "backend/app/services/species_resolution.py",
            shape=Shape.relationship),
    ApiCode("species_smiles_charge_mismatch", 422, Surface.coded_exception,
            "backend/app/chemistry/species.py",
            shape=Shape.relationship),
    ApiCode("state_conflict", 409, Surface.sqlstate_category,
            "backend/app/api/errors.py",
            shape=Shape.relationship),
    ApiCode("statmech_calculation_key_undeclared", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/local_key_codes.py"),
    ApiCode("statmech_source_calculation_owner_mismatch", 422, Surface.coded_exception,
            "backend/app/services/calculation_ownership.py",
            shape=Shape.relationship),
    ApiCode("statmech_source_role_type_mismatch", 422, Surface.coded_exception,
            "backend/app/services/statmech_resolution.py",
            shape=Shape.relationship),
    ApiCode("statmech_subject_not_exactly_one", 409, Surface.database_constraint,
            "backend/app/scientific_checks/declarations.py"),
    ApiCode("statmech_torsion_scan_calculation_owner_mismatch", 422, Surface.coded_exception,
            "backend/app/services/calculation_ownership.py",
            shape=Shape.relationship),
    ApiCode("stored_species_smiles_unparseable", 422, Surface.coded_exception,
            "backend/app/services/reaction_resolution.py"),
    ApiCode("subject_type_mismatch", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py",
            shape=Shape.relationship),
    ApiCode("supersedes_same_record", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py",
            shape=Shape.relationship),
    ApiCode("tckdb_client_version_invalid", 426, Surface.detail_object,
            "backend/app/api/client_version.py"),
    ApiCode("tckdb_client_version_missing", 426, Surface.detail_object,
            "backend/app/api/client_version.py"),
    ApiCode("tckdb_client_version_unsupported", 426, Surface.detail_object,
            "backend/app/api/client_version.py",
            shape=Shape.relationship),
    ApiCode("thermo_source_calculation_owner_mismatch", 422, Surface.coded_exception,
            "backend/app/services/calculation_ownership.py",
            shape=Shape.relationship),
    ApiCode("thermo_source_role_type_mismatch", 422, Surface.coded_exception,
            "backend/app/workflows/thermo.py",
            shape=Shape.relationship),
    ApiCode("thermo_statmech_owner_mismatch", 422, Surface.coded_exception,
            "backend/app/services/calculation_ownership.py",
            shape=Shape.relationship,
            note=(
                "The ownership family's first code over a cited row that "
                "is not a calculation. Distinct from "
                "thermo_source_calculation_owner_mismatch because the "
                "field is: existing_statmech_id names a partition "
                "function, source_calculations name jobs, and a depositor "
                "repairs them in different places."
            )),
    ApiCode("too_many_element_symbols", 422, Surface.coded_exception,
            "backend/app/services/scientific_read/species.py",
            shape=Shape.relationship,
            note=(
                "/species/browse's elements= composition filter. Same cap "
                "family as limit_too_large / offset_too_large (Calvin's "
                "2026-08-18 overrule: a cap refusal that does not state "
                "the limit is un-actionable) -- context carries "
                "max_elements (the configured cap, a module constant "
                "here rather than a setting) and elements_count (the "
                "caller's own comma-separated symbol count). Exists "
                "because each additional symbol adds one more unindexed "
                "per-row cartridge regex on a public, unauthenticated "
                "endpoint; see MAX_ELEMENT_SYMBOLS in "
                "app/schemas/reads/_field_bounds.py for the measurement."
            )),
    ApiCode("transition_state_charge_mismatch", 422, Surface.coded_exception,
            "backend/app/services/reaction_resolution.py",
            shape=Shape.relationship),
    ApiCode("transition_state_composition_mismatch", 422, Surface.coded_exception,
            "backend/app/services/reaction_resolution.py",
            shape=Shape.relationship),
    ApiCode("transition_state_irc_mapping_element_mismatch", 422, Surface.coded_exception,
            "backend/app/services/reaction_resolution.py",
            shape=Shape.relationship),
    ApiCode("transition_state_key_undeclared", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/local_key_codes.py"),
    ApiCode("transition_state_no_imaginary_mode", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py"),
    ApiCode("transition_state_reaction_coordinate_ambiguous", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py",
            shape=Shape.relationship),
    ApiCode("transition_state_reaction_coordinate_not_designated", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py"),
    ApiCode("transport_source_calculation_owner_mismatch", 422, Surface.coded_exception,
            "backend/app/services/calculation_ownership.py",
            shape=Shape.relationship,
            reach=Reach.guard,
            note=(
                "No request produces it, and unlike its four siblings no "
                "write path anywhere can even produce the condition. "
                "Transport has one guard, in workflows/transport.py, over a "
                "calculation the same loop persisted two statements earlier "
                "with the transport target's own species entry; "
                "TransportSourceCalculationIn carries only calculation_key "
                "and role, so no request can name a foreign row; and the two "
                "other callers of resolve_and_create_transport (the conformer "
                "upload, the PDep bundle) pass no source calculations at all. "
                "The guard stays as the tripwire for the day one of them "
                "does."
            )),
    ApiCode("unique_conflict", 409, Surface.sqlstate_category,
            "backend/app/api/errors.py",
            shape=Shape.relationship),
    ApiCode("unknown_calculation_artifact_ref", 404, Surface.coded_exception,
            "backend/app/services/upload_reference.py",
            note=(
                "An artifact locator is a (calculation_ref, sha256) pair, and "
                "it has its own code rather than sharing "
                "unknown_calculation_ref because the pair fails for two "
                "reasons a depositor repairs differently: no such "
                "calculation, or that calculation holds no file with that "
                "digest. context carries both halves."
            )),
    ApiCode("unknown_calculation_ref", 404, Surface.coded_exception,
            "backend/app/services/upload_reference.py",
            note=(
                "Three roots since #230, and two spellings. "
                "/uploads/kinetics names the job by public ref; "
                "/uploads/thermo and /uploads/statmech name it by row id in "
                "source_calculations[i].existing_calculation_id, which "
                "answered the generic resource_not_found with an empty "
                "context until #230. One code because the missing row and "
                "its repair are identical and only the spelling differs -- "
                "splitting on spelling is the defect #195 removed from the "
                "status. context['field'] separates them; context['ref'] is "
                "present for the public-ref spelling only, because a row id "
                "is logged and never echoed (DR-0028 Requirement 2)."
            )),
    ApiCode("unknown_conformer_group_ref", 404, Surface.coded_exception,
            "backend/app/services/upload_reference.py",
            note=(
                "The third refusal a conformer_selection locator can "
                "produce, and the newest: it arrived with the locator's "
                "conformer_group_ref field, which was added because two "
                "conformer groups under one species entry made the locator "
                "unanswerable -- every ambiguous_conformer_selection_"
                "locator refusal reported locator_can_express=false and no "
                "corrected body existed. Not folded into "
                "unknown_conformer_selection, by that code's own argument: "
                "a ref naming nothing is repaired by fixing the string the "
                "caller wrote, a real group holding no such selection by "
                "depositing the selection. The third case -- a real group "
                "belonging to a different species entry -- is neither a "
                "404 nor a new code: the row exists and the locator "
                "contradicts itself, so it is 422 kinetics_interpretation_"
                "conformer_selection_owner_mismatch from the shared "
                "ownership guard, separated from that code's other raise "
                "site by context['field']."
            )),
    ApiCode("unknown_conformer_selection", 404, Surface.coded_exception,
            "backend/app/services/conformer_selection_locator.py",
            note=(
                "The one unknown_* on /uploads/kinetics not built by "
                "upload_reference.unknown_reference, and the reason is that "
                "helper's own rule: it takes exactly one of ref= (echoed) or "
                "row_id= (logged only), because the spelling chooses the "
                "disclosure rule. A conformer_selection content locator is a "
                "third spelling -- a description, not a name -- so there is "
                "no caller-written string to quote back and context carries "
                "the locator's own fields (selection_kind, plus "
                "assignment_scheme_ref and conformer_group_ref when set) "
                "instead. The field/kind "
                "pair is kept identical to unknown_reference's so a client "
                "reads one body layout. Paired with "
                "ambiguous_conformer_selection_locator at 422, which is the "
                "same locator matching too many rows rather than none, and "
                "with unknown_conformer_group_ref at 404, which is the "
                "locator's conformer_group_ref naming no group at all -- a "
                "different repair, so a different code."
            )),
    ApiCode("unknown_curation_policy", 404, Surface.message_prefix,
            "backend/app/api/routes/releases_admin.py"),
    ApiCode("unknown_element_symbol", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/species.py",
            note=(
                "/species/browse's elements= composition filter. RDKit's "
                "periodic table is the source of truth for what counts as "
                "a recognised symbol -- a typo'd 'Xx' (or an isotope token "
                "like 'D'/'T', which RDKit does not treat as an element) "
                "422s here rather than silently matching zero species, "
                "which would read indistinguishably from 'the archive "
                "holds none of that element'."
            )),
    ApiCode("unknown_include_token", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/common.py"),
    ApiCode("unknown_network_kinetics_ref", 404, Surface.coded_exception,
            "backend/app/services/upload_reference.py"),
    ApiCode("unknown_record", 404, Surface.message_prefix,
            "backend/app/services/release/curation.py",
            note=(
                "404 since #211. It was the fourth unknown_* on this router "
                "and the only one at 422: unknown_release, unknown_selection "
                "and unknown_curation_policy all answered 404 for the same "
                "condition. Distinct from record_ref_not_selectable, which "
                "stays 422 -- there the prefix is not a selectable kind at "
                "all, which is a malformed ref rather than a missing row."
            )),
    ApiCode("unknown_record_type", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/literature_records.py"),
    ApiCode("unknown_release", 404, Surface.message_prefix,
            "backend/app/services/scientific_read/releases.py"),
    ApiCode("unknown_release_artifact", 404, Surface.message_prefix,
            "backend/app/api/routes/scientific/releases.py"),
    ApiCode("unknown_selection", 404, Surface.message_prefix,
            "backend/app/api/routes/releases_admin.py"),
    ApiCode("unknown_statmech_ref", 404, Surface.coded_exception,
            "backend/app/services/upload_reference.py",
            note=(
                "404 since #204. Every ref-names-nothing refusal on "
                "/uploads/kinetics used to be a bare ValueError, so a client "
                "received 422 validation_error with no context and had to "
                "read English to find out which of four refs was wrong. The "
                "status matched neither the condition nor the sibling roots: "
                "/uploads/thermo and /uploads/statmech already answered 404 "
                "when a caller-supplied existing_*_id named no row, so the "
                "status depended on how the caller spelled the name. Those "
                "sibling roots answered it with no code and no context, "
                "which #230 fixed by pointing thermo's existing_statmech_id "
                "at this same code -- same missing row, same repair, and a "
                "code that differed by spelling would have rebuilt one "
                "field down what #195 removed from the status."
            )),
    ApiCode("unknown_transition_state_entry_ref", 404, Surface.coded_exception,
            "backend/app/services/upload_reference.py",
            note=(
                "One code, three raise sites in workflows/kinetics.py, and "
                "only the first is reachable through a route: the schema "
                "confines an interpretation's transition_state_entry_ref to "
                "role='transition_state', and the TS-anchored reaction lookup "
                "collects every such ref plus the tunneling one and resolves "
                "them before either of the other two sites runs. The other "
                "two stay as tripwires against a reordering. context['field'] "
                "says which ref asked."
            )),
    ApiCode("unsafe_lowest_energy_comparison", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/species_calculations_search.py"),
    ApiCode("unsupported_direction", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/reactions.py"),
    ApiCode("unsupported_filter", 422, Surface.coded_exception,
            "backend/app/api/error_contract.py"),
    ApiCode("unsupported_ranking_for_calculation_type", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/species_calculations_search.py"),
    ApiCode("unsupported_reaction_molecularity", 422, Surface.coded_exception,
            "backend/app/chemistry/units.py"),
    ApiCode("unsupported_release_record_type", 422, Surface.message_prefix,
            "backend/app/services/release/records.py"),
    ApiCode("username_taken", 409, Surface.message_prefix,
            "backend/app/api/routes/auth.py",
            note=(
                "The sibling of email_taken; see that entry for why the "
                "pair exists and how they are told apart. 409 rather than "
                "422 under Calvin's 2026-08-16 decision: the payload is "
                "well formed and the name is simply spoken for, which is "
                "a state conflict. There is no corrected body for the "
                "same username, only a different one."
            )),
    ApiCode("validation_error", 422, Surface.generic_fallback,
            "backend/app/api/errors.py"),
    ApiCode("withdraw_reason_required", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
)


def catalogued_codes() -> frozenset[str]:
    """Every code in the catalogue, as bare strings.

    Cheap enough to call per response: the tuple is a module constant and
    this rebuilds a small frozenset from it.
    """
    return frozenset(entry.code for entry in CATALOGUE)


def client_facing() -> tuple[ApiCode, ...]:
    """The entries a client should be able to import and branch on.

    See :attr:`ApiCode.is_client_facing` for the rule and why it is
    derived rather than declared.
    """
    return tuple(entry for entry in CATALOGUE if entry.is_client_facing)


def never_retryable() -> tuple[ApiCode, ...]:
    """The entries a client's retry layer must refuse to replay.

    Disjoint from :func:`client_facing` today, and the test suite pins
    that — but not by construction. The two answer different questions,
    and a 429 could in principle be both (importable *and* pointless to
    replay) without either rule contradicting the other; the assertion in
    ``backend/tests/api/test_client_rejection_codes_generated.py`` is
    there to make the first such entry a deliberate decision rather than
    an accident, and its message says so.

    See :attr:`ApiCode.is_replay_futile` for the rule, and :class:`Replay`
    for why this one classification is declared rather than derived.
    """
    return tuple(entry for entry in CATALOGUE if entry.is_replay_futile)


__all__ = [
    "CATALOGUE",
    "RELATIONSHIP_WORDS",
    "REPLAYABLE_STATUSES",
    "STATUS_FALLBACK_PATTERN",
    "ApiCode",
    "Reach",
    "Replay",
    "Shape",
    "Surface",
    "catalogued_codes",
    "client_facing",
    "never_retryable",
]
