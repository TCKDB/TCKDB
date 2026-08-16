"""One lookup, for every local key an upload uses to name its own parts.

What a local key is
-------------------
An upload payload never carries a database id (DR-0029 Requirement 1).
When a thermo record has to say which calculations produced its number,
or a torsion has to name its rotor scan, or a solve has to cite the jobs
behind it, the payload writes a *local key* — a string the same request
declared on a calculation it also sent. The workflow builds a map from
those keys to the rows it just persisted, and every cross-reference in
the payload is resolved through it.

Why the lookup is a function and not a subscript
------------------------------------------------
It was a subscript, twenty-one times: nineteen across the three bundle
workflows and one each in the standalone thermo and transport product
workflows. A key naming nothing was a ``KeyError``: an unhandled 500
with nothing in it saying which key was wrong, where the honest answer
is a 422 naming the key and the keys that *are* declared.

The usual defence was that the request schema already refuses an
undeclared key before the workflow runs. That defence was true of most
sites and *false* of two, and it was false in the way this arrangement
always fails: the guard lives in ``tckdb_schemas`` — a different
distributable package from the workflow it protects — so the two drift
without anything going red. ``NetworkPDepUploadRequest`` narrows a
**species** statmech's source and torsion keys to that species's own
calculations and does not do the same for a **transition state**'s, so a
PDep TS statmech naming a key nothing declared reached
``_persist_statmech_block`` and raised ``KeyError`` out of the route.
That is not a hypothetical: ``tests/api/test_api_pdep_ts_statmech_
undeclared_key.py`` provokes it on the wire.

Keeping the schema checks is deliberate. They refuse one layer earlier,
they run in a client that never talks to a server, and a duplicated
check costs nothing while a missing one costs a 500. What changes here is
that the workflow no longer *depends* on them being right.

The other six namespaces, and what measuring them found
--------------------------------------------------------
Calculation keys were done first and the work deliberately stopped
there, because a species key and a channel key are different keys with
different remedies. Six more namespaces were then measured across
``computed_reaction`` and ``network_pdep`` — species, network state,
network channel, micro reaction, transition state, geometry — for 27
cross-reference reads. The expectation going in was that the schema
coverage would be uneven. It was, and worse than the calculation-key
pass found:

* ``computed_reaction`` was clean. Every one of its cross-references is
  narrowed by ``ComputedReactionUploadRequest.validate_species_key_refs``
  before the workflow runs.
* ``network_pdep`` had **five** wire-reachable 500s, in five of the six
  namespaces. Four of them — a solve's ``state_energies[].state_key``
  and its ``channel_barriers[]`` channel, micro-reaction and
  transition-state keys — are guarded only inside the
  ``kind == 'computed'`` branch of
  ``validate_mechanistic_channel_evidence``, so a ``reported`` solve
  (ADR 0010: k(T,P) transcribed from a paper, holding none of the
  master-equation inputs) walked straight past them into a ``KeyError``.
  The comment above that branch says a reported solve "still has to point
  it at a real path"; for three of those four fields it did not.
* The fifth is the #218 asymmetry again, on a different field.
  ``NetworkSpeciesIn`` narrows a species calculation's ``geometry_key``
  to that species's own conformer geometries; ``TransitionStateIn`` has
  no such validator, and ``validate_key_references`` checks TS
  calculations against the *global* geometry namespace. So a transition
  state's calculation could legally name a later transition state's
  geometry, which the workflow had not resolved yet. The same shape of
  gap, found the same way: by asking whether the guard covers *both*
  branches rather than assuming that one implies the other.

The lesson the calculation-key pass drew — that a guard in a different
distributable package drifts silently — is only half of it. Four of these
five gaps are in ``backend/app/schemas``, the *same* package as the
workflow. What they have in common is not distance but conditionality:
a guard that runs for one member of an enum and not the other, and a
workflow that runs for both.

What the refusal carries
------------------------
The offending field, the key as the depositor wrote it, and the keys that
are declared — because naming the alternatives is what turns a refusal
into a mechanical fix. Never a row id: the ids are the *values* of these
maps and they stay there, out of anything a depositor reads (DR-0028
Requirement 2).

Where the codes live now, and why not here
------------------------------------------
The codes this module raises are *defined* in
:mod:`tckdb_schemas.local_key_codes` and re-exported below. ADR 0017: a
refusal's code and context belong to the check, not to the layer that
happens to run it. The request-schema validators that refuse these same
mistakes one layer earlier may not import ``app`` (the wire package's
import-boundary test forbids it, statically and at runtime), so before
#219 they raised a bare ``ValueError`` and a client received the generic
``request_validation_error`` with an empty ``context`` — for the very
same mistake this module answers with a code and the list of names that
would have worked. Which layer fired decided what the depositor was told.
Moving the constants into the wire package is what makes both layers able
to say the same thing; :func:`tckdb_schemas.local_key_codes.undeclared_key_context`
is what stops them saying it differently.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar

from tckdb_schemas.local_key_codes import (
    W_CALCULATION_KEY_UNDECLARED,
    W_GEOMETRY_KEY_UNRESOLVED,
    W_MICRO_REACTION_KEY_UNDECLARED,
    W_NETWORK_CHANNEL_KEY_UNDECLARED,
    W_NETWORK_STATE_KEY_UNDECLARED,
    W_SPECIES_KEY_UNDECLARED,
    W_TRANSITION_STATE_KEY_UNDECLARED,
    undeclared_key_context,
)

from app.api.error_contract import CodedValueError

#: Re-exported so no backend import had to move when the codes did, and so
#: this module still reads as the place the seam's codes come from. They are
#: *defined* in :mod:`tckdb_schemas.local_key_codes` — see ADR 0017 and that
#: module's docstring — because a request-schema validator refuses the same
#: mistake one layer earlier and may not import ``app``.
__all__ = [
    "CALCULATION_KEY_REMEDY",
    "W_CALCULATION_KEY_UNDECLARED",
    "W_GEOMETRY_KEY_UNRESOLVED",
    "W_MICRO_REACTION_KEY_UNDECLARED",
    "W_NETWORK_CHANNEL_KEY_UNDECLARED",
    "W_NETWORK_STATE_KEY_UNDECLARED",
    "W_SPECIES_KEY_UNDECLARED",
    "W_TRANSITION_STATE_KEY_UNDECLARED",
    "resolve_calculation_key",
    "resolve_declared_key",
    "resolve_geometry_key",
    "resolve_micro_reaction_key",
    "resolve_network_channel_key",
    "resolve_network_state_key",
    "resolve_species_key",
    "resolve_transition_state_key",
]

#: How a depositor declares a calculation key in a bundle namespace.
#:
#: The remedy sentences all have the same shape because the payload does:
#: every one of these collections gives its members a required ``key``. That
#: shared shape is the reason these are parameterizations of one lookup
#: rather than several lookups.
CALCULATION_KEY_REMEDY = (
    "Every calculation in an upload carries a required 'key'; a "
    "calculation key must match one of them."
)

_SPECIES_KEY_REMEDY = (
    "Every species in an upload carries a required 'key'; a species key must "
    "match one of them."
)
_NETWORK_STATE_KEY_REMEDY = (
    "Every entry in 'states' carries a required 'key'; a state key must match "
    "one of them."
)
_NETWORK_CHANNEL_KEY_REMEDY = (
    "Every entry in 'channels' carries a required 'key'; a channel key must "
    "match one of them."
)
_MICRO_REACTION_KEY_REMEDY = (
    "Every entry in 'micro_reactions' carries a required 'key'; a micro "
    "reaction key must match one of them."
)
_TRANSITION_STATE_KEY_REMEDY = (
    "Every entry in 'transition_states' carries a required 'key'; a "
    "transition state key must match one of them."
)
_GEOMETRY_KEY_REMEDY = (
    "A calculation's 'geometry_key' must name a geometry declared by the "
    "species or transition state that owns the calculation, and declared "
    "before it."
)

T = TypeVar("T")


def resolve_declared_key(
    key: str,
    declared: Mapping[str, T],
    *,
    field: str,
    code: str,
    subject: str,
    remedy: str,
    scope: str = "declared in this upload",
) -> T:
    """Turn one local key into whatever the request declared under it.

    Generic in the value type on purpose. The workflows do not agree on
    what their calc-key map holds — ``computed_species``
    keeps ``Calculation`` rows because its next move is an ownership
    check that needs one, ``computed_reaction`` and ``network_pdep`` keep
    ids — and a helper that forced one shape would have been adopted by
    whichever workflows it happened to fit, which is how there came to be
    twenty-one subscripts instead of one lookup.

    :param key: The key the payload wrote. Never ``None`` — a caller
        with an optional field decides for itself what absence means,
        and none of them mean "look it up anyway".
    :param declared: Local name -> the row or id it names.
    :param field: Field path naming the offending key, echoed verbatim
        into the message and the context.
    :param code: The refusal's code. A parameter because the applied
        energy correction's source key had its own published code before
        this function existed and keeping it is a contract, not a
        preference.
    :param subject: Completes "does not name ``{subject}`` ``{scope}``" —
        ``"a calculation"``, or ``"anything"`` where the field accepts
        more than one kind of name.
    :param scope: What the namespace *is*, completing the same sentence.
        Defaults to "declared in this upload", which is true of every
        namespace whose map is complete before anything reads it. The
        geometry namespace is not one of those — it is filled as the
        workflow walks the species and transition states that declare
        geometries — so it overrides this rather than tell a depositor
        their key was never declared when it was declared ten lines
        further down their own file.
    :param remedy: Sentences appended after the list of declared names.
    :returns: The value ``declared`` holds for ``key``.
    :raises CodedValueError: if ``key`` names nothing declared.
    """
    if key in declared:
        return declared[key]

    known = sorted(declared)
    if known:
        available = "Declared names here: " + ", ".join(repr(k) for k in known) + "."
    else:
        available = "This upload declares no such name at all."
    raise CodedValueError(
        code,
        f"{field}='{key}' does not name {subject} {scope}. "
        f"{available} {remedy}".rstrip(),
        # Built by the wire package's helper, not spelled out here, so the
        # request-schema validator that refuses this same mistake one layer
        # earlier cannot report a different set of facts (ADR 0017).
        context=undeclared_key_context(field=field, key=key, declared=known),
        message_prefix=False,
    )


def resolve_calculation_key(
    key: str,
    declared: Mapping[str, T],
    *,
    field: str,
    code: str = W_CALCULATION_KEY_UNDECLARED,
) -> T:
    """Resolve one upload-local *calculation* key, or refuse with a code.

    The seam every raw calc-key subscript now goes through, in all five
    workflows that resolve a calculation by local key —
    ``computed_species``, ``computed_reaction``, ``network_pdep``,
    ``thermo`` and ``transport``.
    ``tests/services/test_local_key_resolution.py`` fails if a raw one
    comes back to any of them.

    :param key: The calculation key the payload wrote.
    :param declared: The workflow's calc-key namespace.
    :param field: Field path naming the offending key.
    :param code: Overridable so the statmech upload path can keep the
        code it published before this seam existed.
    :raises CodedValueError: if ``key`` names no declared calculation.
    """
    return resolve_declared_key(
        key,
        declared,
        field=field,
        code=code,
        subject="a calculation",
        remedy=CALCULATION_KEY_REMEDY,
    )


def resolve_species_key(
    key: str, declared: Mapping[str, T], *, field: str
) -> T:
    """Resolve one upload-local *species* key, or refuse with a code.

    :param key: The species key the payload wrote.
    :param declared: The workflow's species-key namespace.
    :param field: Field path naming the offending key.
    :raises CodedValueError: if ``key`` names no declared species.
    """
    return resolve_declared_key(
        key,
        declared,
        field=field,
        code=W_SPECIES_KEY_UNDECLARED,
        subject="a species",
        remedy=_SPECIES_KEY_REMEDY,
    )


def resolve_network_state_key(
    key: str, declared: Mapping[str, T], *, field: str
) -> T:
    """Resolve one upload-local network *state* key, or refuse with a code.

    :param key: The state key the payload wrote.
    :param declared: The workflow's state-key namespace.
    :param field: Field path naming the offending key.
    :raises CodedValueError: if ``key`` names no declared state.
    """
    return resolve_declared_key(
        key,
        declared,
        field=field,
        code=W_NETWORK_STATE_KEY_UNDECLARED,
        subject="a network state",
        remedy=_NETWORK_STATE_KEY_REMEDY,
    )


def resolve_network_channel_key(
    key: str, declared: Mapping[str, T], *, field: str
) -> T:
    """Resolve one upload-local network *channel* key, or refuse with a code.

    :param key: The channel key the payload wrote.
    :param declared: The workflow's channel-key namespace.
    :param field: Field path naming the offending key.
    :raises CodedValueError: if ``key`` names no declared channel.
    """
    return resolve_declared_key(
        key,
        declared,
        field=field,
        code=W_NETWORK_CHANNEL_KEY_UNDECLARED,
        subject="a network channel",
        remedy=_NETWORK_CHANNEL_KEY_REMEDY,
    )


def resolve_micro_reaction_key(
    key: str, declared: Mapping[str, T], *, field: str
) -> T:
    """Resolve one upload-local *micro reaction* key, or refuse with a code.

    :param key: The micro reaction key the payload wrote.
    :param declared: The workflow's micro-reaction-key namespace.
    :param field: Field path naming the offending key.
    :raises CodedValueError: if ``key`` names no declared micro reaction.
    """
    return resolve_declared_key(
        key,
        declared,
        field=field,
        code=W_MICRO_REACTION_KEY_UNDECLARED,
        subject="a micro reaction",
        remedy=_MICRO_REACTION_KEY_REMEDY,
    )


def resolve_transition_state_key(
    key: str, declared: Mapping[str, T], *, field: str
) -> T:
    """Resolve one upload-local *transition state* key, or refuse with a code.

    :param key: The transition state key the payload wrote.
    :param declared: The workflow's TS-key namespace.
    :param field: Field path naming the offending key.
    :raises CodedValueError: if ``key`` names no declared transition state.
    """
    return resolve_declared_key(
        key,
        declared,
        field=field,
        code=W_TRANSITION_STATE_KEY_UNDECLARED,
        subject="a transition state",
        remedy=_TRANSITION_STATE_KEY_REMEDY,
    )


def resolve_geometry_key(
    key: str, declared: Mapping[str, T], *, field: str
) -> T:
    """Resolve one upload-local *geometry* key, or refuse with a code.

    The one resolver here whose subject is not "declared in this upload".
    See :data:`W_GEOMETRY_KEY_UNRESOLVED` for why: the geometry namespace
    is built as the workflow walks the owners that declare geometries, so
    a key can be present in the payload and absent from this map, and a
    refusal claiming it was never declared would be false.

    :param key: The geometry key the payload wrote.
    :param declared: The geometry keys resolved so far in this upload.
    :param field: Field path naming the offending key.
    :raises CodedValueError: if ``key`` names no resolved geometry.
    """
    return resolve_declared_key(
        key,
        declared,
        field=field,
        code=W_GEOMETRY_KEY_UNRESOLVED,
        subject="a geometry",
        scope="this upload has resolved at this point",
        remedy=_GEOMETRY_KEY_REMEDY,
    )
