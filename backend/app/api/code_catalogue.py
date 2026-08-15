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
    :param note: A fact the site cannot state about itself. Deliberately
        rare — this is an enumeration, not a second copy of the refusal
        messages. Required on every :attr:`Reach.guard` entry, because
        "no request can produce this" is a claim and an unexplained claim
        is the kind that rots.
    """

    code: str
    status: int
    surface: Surface
    origin: str
    reach: Reach = Reach.request
    note: str | None = None

    @property
    def is_client_facing(self) -> bool:
        """Whether a client should be able to import and branch on this.

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


#: Every code the API can report, ordered by code so a diff is readable.
#:
#: Adding one is cheap on purpose: an entry claims only that the code
#: exists and where it comes from. That is the whole point of separating
#: this from :mod:`app.scientific_checks`, whose entries claim something a
#: referee could argue with and must stay expensive.
CATALOGUE: tuple[ApiCode, ...] = (
    ApiCode("applied_energy_correction_source_calculation_owner_mismatch", 422, Surface.coded_exception,
            "backend/app/services/calculation_ownership.py",
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
            "backend/app/services/energy_correction_resolution.py"),
    ApiCode("arrhenius_a_units_molecularity_mismatch", 422, Surface.coded_exception,
            "backend/app/chemistry/units.py"),
    ApiCode("artifact_integrity_failed", 502, Surface.response_literal,
            "backend/app/api/errors.py"),
    ApiCode("artifact_storage_unavailable", 503, Surface.response_literal,
            "backend/app/api/errors.py"),
    ApiCode("atom_map_atoms_unaccounted_for", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py"),
    ApiCode("atom_map_contradicts_irc_mapping", 422, Surface.coded_exception,
            "backend/app/services/reaction_atom_map.py"),
    ApiCode("atom_map_element_not_conserved", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py"),
    ApiCode("atom_map_element_not_conserved", 409, Surface.database_constraint,
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py",
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
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py"),
    ApiCode("atom_map_inferred_requires_note", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py"),
    ApiCode("atom_map_not_a_bijection", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py"),
    ApiCode("atom_map_not_a_bijection", 409, Surface.database_constraint,
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py"),
    ApiCode("atom_map_participant_not_declared", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py"),
    ApiCode("atom_map_without_transition_state", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/reaction_atom_map.py"),
    ApiCode("calculation_geometry_composition_mismatch", 422, Surface.coded_exception,
            "backend/app/services/calculation_geometry_composition.py",
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
            "backend/app/services/scientific_read/handles.py"),
    ApiCode("calculation_key_undeclared", 422, Surface.coded_exception,
            "backend/app/services/local_key_resolution.py",
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
    ApiCode("composed_search_candidate_limit_exceeded", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/common.py"),
    ApiCode("composed_search_invalid_page", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/common.py"),
    ApiCode("composed_search_pagination_changed", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/common.py"),
    ApiCode("composed_search_pagination_stalled", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/common.py"),
    ApiCode("curation_policy_version_conflict", 409, Surface.message_prefix,
            "backend/app/services/release/curation.py",
            note=(
                "409, not the 422 its ValueError base would suggest. "
                "ReleaseCurationError subclasses ValueError, so reading the "
                "raise site says 422; the one route that can reach it wraps "
                "it in HTTPException(409) instead "
                "(api/routes/releases_admin.py). The status is a property of "
                "the route, not of the raise, which is why it was recorded "
                "wrong until the observer started checking the pair."
            )),
    ApiCode("curator_task_not_found", 404, Surface.coded_exception,
            "backend/app/services/machine_review/curator_task_lifecycle.py"),
    ApiCode("cursor_offset_conflict", 422, Surface.coded_exception,
            "backend/app/services/scientific_read/analytics.py"),
    ApiCode("cursor_query_mismatch", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/keyset.py"),
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
    ApiCode("doi_already_recorded", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
    ApiCode("domain_error", 400, Surface.generic_fallback,
            "backend/app/api/errors.py"),
    ApiCode("energy_transfer_scope_columns_disagree", 409, Surface.database_constraint,
            "backend/app/scientific_checks/declarations.py"),
    ApiCode("export_all_cap_exceeded", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/export.py"),
    ApiCode("export_seed_empty", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/export.py"),
    ApiCode("export_seed_unresolved", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/export.py"),
    ApiCode("freq_list_exceeds_geometry_degrees_of_freedom", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/frequency_completeness.py",
            note=(
                "One of two codes that module reports, and the only one that "
                "refuses. Its sibling freq_list_incomplete_for_geometry is an "
                "UploadWarning on an accepted 201 and is deliberately absent "
                "from this catalogue, which enumerates error bodies only. "
                "Minted from a variable: raise_for_blocking_findings reports "
                "whichever code its blocking finding carries, so the scan "
                "cannot see it and the origin is the defining module."
            )),
    ApiCode("freq_n_imag_disagrees_with_modes", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/fragments/calculation.py"),
    ApiCode("geometry_too_large", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/geometry.py"),
    ApiCode("handle_not_found", 404, Surface.coded_exception,
            "backend/app/services/scientific_read/calculation_paths.py"),
    ApiCode("handle_type_mismatch", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/handles.py"),
    ApiCode("idempotency_conflict", 409, Surface.response_literal,
            "backend/app/api/errors.py"),
    ApiCode("idempotency_in_progress", 409, Surface.response_literal,
            "backend/app/api/errors.py",
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
            "backend/app/api/errors.py"),
    ApiCode("invalid_cursor", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/keyset.py"),
    ApiCode("invalid_handle", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/handles.py"),
    ApiCode("invalid_idempotency_key", 400, Surface.response_literal,
            "backend/app/api/errors.py"),
    ApiCode("invalid_pagination", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/common.py"),
    ApiCode("invalid_range", 422, Surface.coded_exception,
            "backend/app/services/scientific_read/analytics.py"),
    ApiCode("invalid_structure_query", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/structure_search.py"),
    ApiCode("invalid_temperature_range", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/common.py"),
    ApiCode("irc_result_not_found", 404, Surface.coded_exception,
            "backend/app/services/scientific_read/calculation_paths.py"),
    ApiCode("kinetics_interpretation_conformer_selection_owner_mismatch", 422, Surface.coded_exception,
            "backend/app/services/calculation_ownership.py"),
    ApiCode("kinetics_interpretation_statmech_owner_mismatch", 422, Surface.coded_exception,
            "backend/app/services/calculation_ownership.py",
            note=(
                "One code, two comparisons: a reactant/product assignment "
                "is held to the participant's species entry and a "
                "transition-state one to the declared TS entry. Not two "
                "entries, because the field and the repair are the same "
                "-- name the statmech belonging to this subject -- and "
                "which owner disagreed is already in context['owner_kind']."
            )),
    ApiCode("level_of_theory_handle_conflict", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/handles.py"),
    ApiCode("limit_too_large", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/common.py",
            note=(
                "Split out of invalid_pagination, which covered four "
                "conditions with three different remedies. A limit above the "
                "hosted cap is recoverable by resending the same query with "
                "a smaller page size; a negative offset is a caller bug. "
                "Unreachable through a GET route in the shipped "
                "configuration, where MAX_LIMIT and public_max_limit are "
                "both 200 and Query(le=200) refuses anything larger first -- "
                "reachable through a POST search body, and through any GET "
                "on a deployment that lowers public_max_limit."
            )),
    ApiCode("lowest_energy_unavailable", 422, Surface.coded_exception,
            "backend/app/services/scientific_read/species_calculations_search.py"),
    ApiCode("manifest_already_frozen", 422, Surface.message_prefix,
            "backend/app/services/release/manifest.py"),
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
    ApiCode("ml_export_all_cap_exceeded", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/ml_dataset.py"),
    ApiCode("ml_export_lot_unresolved", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/ml_dataset.py"),
    ApiCode("ml_export_seed_empty", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/ml_dataset.py"),
    ApiCode("ml_export_seed_unresolved", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/ml_dataset.py"),
    ApiCode("multiple_structure_queries", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/structure_search.py"),
    ApiCode("n_imag_contradicts_minimum", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py"),
    ApiCode("network_solve_reported_requires_literature", 409, Surface.database_constraint,
            "backend/app/scientific_checks/declarations.py"),
    ApiCode("non_finite_value", 422, Surface.message_prefix,
            "backend/app/services/release/artifacts.py"),
    ApiCode("offset_too_large", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/common.py",
            note=(
                "The sibling of limit_too_large, and the reason they are two "
                "codes rather than one: retrying with a smaller offset "
                "returns different rows, so this one is not recoverable by "
                "resending. Reachable on any paginated read against the "
                "shipped configuration -- offset carries no upper bound at "
                "the route, so settings.public_max_offset is the only one."
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
            "backend/app/schemas/reads/scientific_kinetics.py"),
    ApiCode("query_timeout", 503, Surface.response_literal,
            "backend/app/api/errors.py"),
    ApiCode("query_too_expensive", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/provenance.py"),
    ApiCode("rate_limit_exceeded", 429, Surface.response_literal,
            "backend/app/api/rate_limit.py"),
    ApiCode("rationale_required", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
    ApiCode("reaction_charge_not_conserved", 422, Surface.coded_exception,
            "backend/app/services/reaction_resolution.py"),
    ApiCode("reaction_mass_balance_failed", 422, Surface.coded_exception,
            "backend/app/services/reaction_resolution.py"),
    ApiCode("record_has_no_subject", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
    ApiCode("record_not_approved", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
    ApiCode("record_ref_not_selectable", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
    ApiCode("record_subject_mismatch", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
    ApiCode("record_type_not_selectable", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
    ApiCode("reaction_entry_handle_conflict", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/handles.py"),
    ApiCode("reaction_handle_conflict", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/handles.py"),
    ApiCode("reference_conflict", 409, Surface.sqlstate_category,
            "backend/app/api/errors.py"),
    ApiCode("release_artifact_corrupt", 500, Surface.message_prefix,
            "backend/app/api/routes/scientific/releases.py"),
    ApiCode("release_not_draft", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
    ApiCode("release_not_published", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
    ApiCode("release_scoping_not_implemented", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/profile.py"),
    ApiCode("release_selects_nothing", 422, Surface.message_prefix,
            "backend/app/services/release/manifest.py"),
    ApiCode("release_tag_taken", 409, Surface.message_prefix,
            "backend/app/services/release/curation.py",
            note=(
                "409 for the same reason as "
                "curation_policy_version_conflict: the route wraps the "
                "ReleaseCurationError in HTTPException(409) rather than "
                "letting its ValueError base reach the 422 handler."
            )),
    ApiCode("request_validation_error", 422, Surface.generic_fallback,
            "backend/app/api/errors.py"),
    ApiCode("resource_not_found", 404, Surface.generic_fallback,
            "backend/app/api/errors.py"),
    ApiCode("scan_result_not_found", 404, Surface.coded_exception,
            "backend/app/services/scientific_read/calculation_paths.py"),
    ApiCode("schema_not_initialized", 503, Surface.response_literal,
            "backend/app/api/routes/health.py"),
    ApiCode("selection_already_stands", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
    ApiCode("selection_already_superseded", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
    ApiCode("selection_no_longer_approved", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
    ApiCode("smiles_too_long", 422, Surface.message_prefix,
            "backend/app/schemas/reads/scientific_kinetics_search.py"),
    ApiCode("species_entry_handle_conflict", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/handles.py"),
    ApiCode("species_geometry_composition_mismatch", 422, Surface.coded_exception,
            "backend/app/services/species_resolution.py"),
    ApiCode("species_geometry_isotope_mismatch", 422, Surface.coded_exception,
            "backend/app/services/species_resolution.py"),
    ApiCode("species_handle_conflict", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/handles.py"),
    ApiCode("species_kind_conflict", 422, Surface.coded_exception,
            "backend/app/services/species_resolution.py"),
    ApiCode("species_smiles_charge_mismatch", 422, Surface.coded_exception,
            "backend/app/chemistry/species.py"),
    ApiCode("state_conflict", 409, Surface.sqlstate_category,
            "backend/app/api/errors.py"),
    ApiCode("statmech_calculation_key_undeclared", 422, Surface.coded_exception,
            "backend/app/services/statmech_resolution.py"),
    ApiCode("statmech_source_calculation_owner_mismatch", 422, Surface.coded_exception,
            "backend/app/services/calculation_ownership.py"),
    ApiCode("statmech_source_role_type_mismatch", 422, Surface.coded_exception,
            "backend/app/services/statmech_resolution.py"),
    ApiCode("statmech_subject_not_exactly_one", 409, Surface.database_constraint,
            "backend/app/scientific_checks/declarations.py"),
    ApiCode("statmech_torsion_scan_calculation_owner_mismatch", 422, Surface.coded_exception,
            "backend/app/services/calculation_ownership.py"),
    ApiCode("stored_species_smiles_unparseable", 422, Surface.coded_exception,
            "backend/app/services/reaction_resolution.py"),
    ApiCode("subject_type_mismatch", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
    ApiCode("supersedes_same_record", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
    ApiCode("tckdb_client_version_invalid", 426, Surface.detail_object,
            "backend/app/api/client_version.py"),
    ApiCode("tckdb_client_version_missing", 426, Surface.detail_object,
            "backend/app/api/client_version.py"),
    ApiCode("tckdb_client_version_unsupported", 426, Surface.detail_object,
            "backend/app/api/client_version.py"),
    ApiCode("thermo_source_calculation_owner_mismatch", 422, Surface.coded_exception,
            "backend/app/services/calculation_ownership.py"),
    ApiCode("thermo_source_role_type_mismatch", 422, Surface.coded_exception,
            "backend/app/workflows/thermo.py"),
    ApiCode("thermo_statmech_owner_mismatch", 422, Surface.coded_exception,
            "backend/app/services/calculation_ownership.py",
            note=(
                "The ownership family's first code over a cited row that "
                "is not a calculation. Distinct from "
                "thermo_source_calculation_owner_mismatch because the "
                "field is: existing_statmech_id names a partition "
                "function, source_calculations name jobs, and a depositor "
                "repairs them in different places."
            )),
    ApiCode("transition_state_charge_mismatch", 422, Surface.coded_exception,
            "backend/app/services/reaction_resolution.py"),
    ApiCode("transition_state_composition_mismatch", 422, Surface.coded_exception,
            "backend/app/services/reaction_resolution.py"),
    ApiCode("transition_state_irc_mapping_element_mismatch", 422, Surface.coded_exception,
            "backend/app/services/reaction_resolution.py"),
    ApiCode("transition_state_no_imaginary_mode", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py"),
    ApiCode("transition_state_reaction_coordinate_ambiguous", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py"),
    ApiCode("transition_state_reaction_coordinate_not_designated", 422, Surface.coded_exception,
            "schemas/python/tckdb-schemas/tckdb_schemas/stationary_point.py"),
    ApiCode("transport_source_calculation_owner_mismatch", 422, Surface.coded_exception,
            "backend/app/services/calculation_ownership.py",
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
            "backend/app/api/errors.py"),
    ApiCode("unknown_curation_policy", 404, Surface.message_prefix,
            "backend/app/api/routes/releases_admin.py"),
    ApiCode("unknown_include_token", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/common.py"),
    ApiCode("unknown_record", 422, Surface.message_prefix,
            "backend/app/services/release/curation.py"),
    ApiCode("unknown_record_type", 422, Surface.message_prefix,
            "backend/app/services/scientific_read/literature_records.py"),
    ApiCode("unknown_release", 404, Surface.message_prefix,
            "backend/app/services/scientific_read/releases.py"),
    ApiCode("unknown_release_artifact", 404, Surface.message_prefix,
            "backend/app/api/routes/scientific/releases.py"),
    ApiCode("unknown_selection", 404, Surface.message_prefix,
            "backend/app/api/routes/releases_admin.py"),
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


__all__ = [
    "CATALOGUE",
    "STATUS_FALLBACK_PATTERN",
    "ApiCode",
    "Reach",
    "Surface",
    "catalogued_codes",
    "client_facing",
]
