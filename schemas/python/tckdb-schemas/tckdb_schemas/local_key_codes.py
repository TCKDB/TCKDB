r"""The codes a refusal carries when an upload names a local key nothing declared.

An upload payload never carries a database id (DR-0029 Requirement 1).
When a thermo record has to say which calculations produced its number,
or a channel has to name the states it joins, the payload writes a
*local key* — a string the same request declared elsewhere in itself.

Two layers can catch a key that names nothing:

* a **request-schema validator**, the moment the body is parsed, before a
  transaction opens and without a server in reach at all (these models
  run inside ``tckdb-client`` and in offline contribution-bundle tooling);
* the **workflow seam** (:mod:`app.services.local_key_resolution`), when
  the upload is being written and the namespace is finally complete.

Why the codes live here and not next to the seam
------------------------------------------------
ADR 0017: *a refusal's code and context belong to the check, not to the
layer that happens to run it.* Wherever both a validator and a seam can
refuse the same mistake, both raise the same
:class:`~tckdb_schemas.coded_error.CodedValidationError` — same ``code``,
same ``context`` keys — so which layer fired is invisible to a client.

That rule cannot be kept if the codes live under ``app``.
``schemas/python/tckdb-schemas/tests/test_import_boundaries.py`` forbids
``tckdb_schemas`` from importing anything under ``app``, statically and
at runtime, so a wire-schema validator could not name the seam's code
even if it wanted to. Before #219 it did not: the validator raised a bare
``ValueError`` and a client received the generic
``request_validation_error`` with an empty ``context``, while the very
same mistake caught one layer later received a specific code and the list
of names that *would* have worked. Which layer fired therefore decided
what the client was told — a published contract by accident, and one a
refactor could change silently. It had, twice in one month.

So the constants moved here, where both layers can import them, and the
backend re-exports them from their old homes
(:mod:`app.services.local_key_resolution`,
:mod:`app.services.statmech_resolution`,
:mod:`app.services.energy_correction_resolution`) so no backend import
had to move. The catalogue in ``app.api.code_catalogue`` names *this*
module as their origin.

Neither layer may be deleted because the other exists
-----------------------------------------------------
The validator is not redundant: it refuses earlier, and it refuses in a
client with no server in reach. The seam is not redundant either, and
that is the harder half to believe — #218 and #198 are both cases where a
validator's coverage was *narrower than the workflow's reach* (one member
of a union guarded, its sibling not) and the penalty was an unhandled
500. Guard coverage is the thing that drifts; the seam is the backstop
precisely because of it.

What a refusal in this family carries
-------------------------------------
``context`` is always exactly ``{"field", "key", "declared_keys"}``, built
by :func:`undeclared_key_context` so the two layers cannot spell it
differently. Never a row id: the ids are the *values* of these maps and
they stay there, out of anything a depositor reads (DR-0028
Requirement 2).

What is *not* in this family
----------------------------
A check that only the schema can make gets its own code and must not be
folded in here. "``source_scan_calculation_key`` must reference a
*scan-type* calculation", "a dependency may not name itself", "these keys
must be unique" and "this transition state does not belong to that micro
reaction" are all different refusals from "that name was never declared",
with different repairs. They stay bare ``ValueError``\ s, and a client
still sees ``request_validation_error`` for them — correctly, because no
seam offers a better answer for the same mistake.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from tckdb_schemas.coded_error import CodedValidationError

__all__ = [
    "W_APPLIED_CORRECTION_SOURCE_KEY_UNDECLARED",
    "W_CALCULATION_KEY_UNDECLARED",
    "W_CONFORMER_KEY_UNDECLARED",
    "W_GEOMETRY_KEY_UNRESOLVED",
    "W_MICRO_REACTION_KEY_UNDECLARED",
    "W_NETWORK_CHANNEL_KEY_UNDECLARED",
    "W_NETWORK_STATE_KEY_UNDECLARED",
    "W_SPECIES_KEY_UNDECLARED",
    "W_STATMECH_CALCULATION_KEY_UNDECLARED",
    "W_TRANSITION_STATE_KEY_UNDECLARED",
    "undeclared_key_context",
    "undeclared_key_error",
]

#: A payload names a calculation key that the same upload never declared.
#:
#: One code for every such field, and the field is in ``context``. An
#: undeclared key on a thermo source link and on a torsion's rotor scan
#: are the *same* repair — declare the calculation, or fix the spelling
#: against the list the refusal prints — resolved against the *same*
#: bundle-global namespace. That is the test the ownership family
#: (``app.services.calculation_ownership``) fails and this one passes:
#: there, a thermo link and a torsion's scan are repaired by finding two
#: different right answers, so they get two codes; here there is one
#: right answer and it is spelled the same way whichever field asked.
#:
#: Deliberately *not* used for an applied energy correction's source key.
#: That field already had a code before this one existed
#: (:data:`W_APPLIED_CORRECTION_SOURCE_KEY_UNDECLARED`), it is pinned by
#: ``tests/api/test_api_upload_key_and_role_contracts.py`` on
#: ``/uploads/conformers``, and it spans a conformer key as well as a
#: calculation key — so a correction's source is one repair on that
#: route and would become two if the bundle routes answered differently.
W_CALCULATION_KEY_UNDECLARED = "calculation_key_undeclared"

# ---------------------------------------------------------------------------
# The other five bundle namespaces
# ---------------------------------------------------------------------------
#
# One code each, and deliberately not one code between them. A calculation
# key and a species key are both "a name this upload declared", but they are
# not the same repair: an undeclared species key is fixed in the ``species``
# list, an undeclared state key in ``states``, an undeclared channel key in
# ``channels``. A client that wants to point the depositor at the right block
# of their own payload can only do that if the code says which block, and a
# single ``local_key_undeclared`` would have made ``context['field']`` the
# only way to tell — which is a string a client would have to parse.

#: A payload names a species key that the same upload never declared.
W_SPECIES_KEY_UNDECLARED = "species_key_undeclared"

#: A payload names a network state key that the same upload never declared.
W_NETWORK_STATE_KEY_UNDECLARED = "network_state_key_undeclared"

#: A payload names a network channel key that the same upload never declared.
W_NETWORK_CHANNEL_KEY_UNDECLARED = "network_channel_key_undeclared"

#: A payload names a micro reaction key that the same upload never declared.
W_MICRO_REACTION_KEY_UNDECLARED = "micro_reaction_key_undeclared"

#: A payload names a transition state key that the same upload never declared.
W_TRANSITION_STATE_KEY_UNDECLARED = "transition_state_key_undeclared"

#: A calculation's ``conformer_key`` names no conformer its own species declared.
#:
#: Its own code, by the same rule as the five above: the repair is made in
#: one specific block of the depositor's payload -- that species's
#: ``conformers`` list -- and no other code points there. It is deliberately
#: *not* :data:`W_APPLIED_CORRECTION_SOURCE_KEY_UNDECLARED`, which also spans
#: conformer names: that code answers "which source did this correction come
#: from?", is pinned on ``/uploads/conformers``, and its remedy sentence talks
#: about correction sources. Reusing it for a calculation's anchor would give
#: a client one code for two different questions.
#:
#: The namespace is scoped to the species that declared it, exactly like
#: ``source_conformer_key``: a sibling species's conformer is not in scope, so
#: ``declared_keys`` lists only the owning species's own conformer keys.
W_CONFORMER_KEY_UNDECLARED = "conformer_key_undeclared"

#: A calculation's ``geometry_key`` names no geometry this upload has resolved
#: for it.
#:
#: The one code in this family that does not say *undeclared*, because it is
#: the one namespace where the key can be real and still unusable. A geometry
#: key is declared on a species conformer or on a transition state, and the
#: workflow resolves geometries as it walks those owners in order — so a
#: transition state's calculation naming a *later* transition state's geometry
#: names something the payload genuinely contains and the workflow genuinely
#: cannot resolve. Calling that "undeclared" would be a refusal telling the
#: depositor something false about their own file. The repair is the same
#: either way and it is one repair: point the calculation at a geometry its
#: own species or transition state declares.
W_GEOMETRY_KEY_UNRESOLVED = "geometry_key_unresolved"

#: A statmech source link names a calculation the upload never declared.
#:
#: Its own code rather than :data:`W_CALCULATION_KEY_UNDECLARED`, because
#: it was published before the shared seam existed and is pinned on
#: ``/uploads/statmech`` and ``/uploads/conformers``. Keeping a published
#: code is a contract, not a preference.
W_STATMECH_CALCULATION_KEY_UNDECLARED = "statmech_calculation_key_undeclared"

#: An applied correction names a source the enclosing upload never declared.
#:
#: Spans a conformer key as well as a calculation key: a correction's
#: source is one repair whichever kind of name the depositor used, and
#: splitting it would answer the same question two ways on two routes.
W_APPLIED_CORRECTION_SOURCE_KEY_UNDECLARED = (
    "applied_energy_correction_source_key_undeclared"
)


def undeclared_key_context(
    *, field: str, key: str, declared: Iterable[str]
) -> dict[str, Any]:
    """The three facts every refusal in this family reports, and only those.

    One function so that a validator and the seam it shadows cannot spell
    the same context differently — which is the half of ADR 0017 that a
    reviewer cannot see by reading either layer alone.

    :param field: Field path naming the offending key, as the depositor's
        own payload spells it.
    :param key: The key the payload wrote, verbatim.
    :param declared: The names that *would* have resolved. Sorted here, so
        a caller holding a ``set`` cannot make the output order-dependent.
    :returns: ``{"field", "key", "declared_keys"}``. Never a row id.
    """
    return {"field": field, "key": key, "declared_keys": sorted(declared)}


def undeclared_key_error(
    code: str,
    detail: str,
    *,
    field: str,
    key: str,
    declared: Iterable[str],
) -> CodedValidationError:
    """Build the refusal a request-schema validator raises for an undeclared key.

    Returned rather than raised so the call site reads ``raise
    undeclared_key_error(...)`` and the traceback starts where the mistake
    was found.

    ``message_prefix=False`` is not optional here: these validators all
    had published prose before they had a code, and the whole point of
    ADR 0017 is that the ``code`` and ``context`` fields appear while
    ``detail`` does not move a byte. A consumer matching English keeps
    working; a consumer matching ``code`` starts working.

    :param code: One of this module's constants.
    :param detail: The refusal's existing sentence, unchanged.
    :param field: Field path naming the offending key.
    :param key: The key the payload wrote.
    :param declared: The names that would have resolved.
    """
    return CodedValidationError(
        code,
        detail,
        context=undeclared_key_context(field=field, key=key, declared=declared),
        message_prefix=False,
    )
