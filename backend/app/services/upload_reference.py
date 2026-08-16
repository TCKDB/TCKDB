"""Refusing an upload that cites a row the database does not have.

The condition
-------------
A deposit names an already-stored record by its **public ref** — the
statmech an interpretation was built from, the transition-state entry a
rate is anchored to, the calculation a tunneling correction was read out
of — and nothing in the database answers to that ref. The thing the
depositor pointed at is not there.

Why 404 and not 422
-------------------
The three statuses say different things to a program, and a client
retries differently for each. 422 means "your message is malformed: fix
it and resend". 409 means "your message is fine, the world is not how you
assumed". 404 means "the thing you named does not exist". A ref naming no
row is the third, and answering 422 invited a client to resend a payload
that could only ever fail again — or, worse, to treat the ref as a typo
when the real repair is to deposit the record first.

This is also what the *other* upload roots already do. ``/uploads/thermo``
answers 404 when ``existing_statmech_id`` or a source link's
``existing_calculation_id`` names no row, and ``/uploads/statmech`` does
the same; ``/uploads/kinetics`` answered 422 for the identical condition
expressed as a public ref instead of a row id. The status was a property
of *how the caller spelled the name*, which is not a distinction a
depositor can act on.

Not the same thing as a local key
----------------------------------
:mod:`app.services.local_key_resolution` refuses a ``*_key`` that names
nothing **declared in the same payload**. That stays 422 and must: the
payload is self-contained, the declared names are printed back, and a
corrected body is accepted. Here the namespace is the whole database and
no corrected body can conjure the row.

Why one code per kind of row, and not one code for all of them
---------------------------------------------------------------
``calculation_key_undeclared`` is deliberately *one* code across a dozen
fields, because they resolve against one namespace and are repaired one
way. These are the opposite case, and it is the test
:mod:`app.api.code_catalogue` already applies: a missing statmech, a
missing transition-state entry and a missing calculation are repaired by
depositing three different things through three different endpoints. Two
fields needing two different right answers is what earns two codes.
``context['field']`` still says which ref asked, because a payload can
cite several of the same kind.
"""

from __future__ import annotations

from app.api.errors import NotFoundError

#: An interpretation assignment's ``statmech_ref`` names no statmech row.
W_UNKNOWN_STATMECH_REF = "unknown_statmech_ref"

#: A ``transition_state_entry_ref`` names no transition-state entry. One
#: code for three raise sites — the TS-anchored reaction lookup, the
#: interpretation assignment and the tunneling block — because they are
#: one condition about one namespace and ``context['field']`` separates
#: them. Only the first is reachable through a route: the schema confines
#: an interpretation's ref to ``role='transition_state'``, and every such
#: ref plus the tunneling one is collected and resolved by the anchor
#: lookup before either of the other two runs. The other two stay as
#: tripwires against a future reordering, which is cheap; what is not
#: cheap is a depositor discovering the difference.
W_UNKNOWN_TRANSITION_STATE_ENTRY_REF = "unknown_transition_state_entry_ref"

#: A ``source_calculation_ref`` names no calculation row.
W_UNKNOWN_CALCULATION_REF = "unknown_calculation_ref"

#: An artifact locator — ``(calculation_ref, sha256)`` — names no stored
#: artifact. Its own code rather than :data:`W_UNKNOWN_CALCULATION_REF`
#: because the pair can fail for two reasons a depositor repairs
#: differently: the calculation is missing, or the calculation is there
#: and holds no file with that digest. ``context`` carries both halves so
#: the caller can tell which.
W_UNKNOWN_CALCULATION_ARTIFACT_REF = "unknown_calculation_artifact_ref"

#: A ``network_kinetics_ref`` names no network_kinetics row.
W_UNKNOWN_NETWORK_KINETICS_REF = "unknown_network_kinetics_ref"


def unknown_reference(
    *,
    code: str,
    field: str,
    kind: str,
    ref: str,
    remedy: str,
    **extra: object,
) -> NotFoundError:
    """Build the 404 for a payload ref that resolves to nothing.

    :param code: One of the ``W_UNKNOWN_*`` constants above.
    :param field: The payload path the depositor must look at, e.g.
        ``"interpretation_assignments[1].statmech_ref"``. Goes into
        ``context['field']`` as well as the sentence, because a payload
        can cite several refs of the same kind and "statmech not found"
        does not say which one.
    :param kind: The record kind, in the API's own vocabulary.
    :param ref: The ref the caller supplied, echoed back — the caller
        wrote it, so quoting it is what makes the refusal diagnosable.
    :param remedy: What to do about it, in one sentence.
    :param extra: Further structured context. Never a database primary
        key (DR-0028 Requirement 2); a public ref or a digest is fine.
    """
    # No ``"code: "`` prefix on the message: the exception carries the code
    # as an attribute and the handler passes it through unparsed, which is
    # the surface every new refusal should use. Writing it in the prose as
    # well would put a second copy where a reword can silently drop it.
    return NotFoundError(
        f"{field} {ref!r} does not name a {kind} in this database. {remedy}",
        code=code,
        context={"field": field, "kind": kind, "ref": ref, **extra},
    )


__all__ = [
    "W_UNKNOWN_CALCULATION_ARTIFACT_REF",
    "W_UNKNOWN_CALCULATION_REF",
    "W_UNKNOWN_NETWORK_KINETICS_REF",
    "W_UNKNOWN_STATMECH_REF",
    "W_UNKNOWN_TRANSITION_STATE_ENTRY_REF",
    "unknown_reference",
]
