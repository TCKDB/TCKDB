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
It was a subscript, nineteen times, across three bundle workflows. A key
naming nothing was a ``KeyError``: an unhandled 500 with nothing in it
saying which key was wrong, where the honest answer is a 422 naming the
key and the keys that *are* declared.

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

What the refusal carries
------------------------
The offending field, the key as the depositor wrote it, and the keys that
are declared — because naming the alternatives is what turns a refusal
into a mechanical fix. Never a row id: the ids are the *values* of these
maps and they stay there, out of anything a depositor reads (DR-0028
Requirement 2).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar

from app.api.error_contract import CodedValueError

#: A payload names a calculation key that the same upload never declared.
#:
#: One code for every such field, and the field is in ``context``. An
#: undeclared key on a thermo source link and on a torsion's rotor scan
#: are the *same* repair — declare the calculation, or fix the spelling
#: against the list the refusal prints — resolved against the *same*
#: bundle-global namespace. That is the test the ownership family
#: (:mod:`app.services.calculation_ownership`) fails and this one passes:
#: there, a thermo link and a torsion's scan are repaired by finding two
#: different right answers, so they get two codes; here there is one
#: right answer and it is spelled the same way whichever field asked.
#:
#: Deliberately *not* used for an applied energy correction's source key.
#: That field already had a code before this one existed
#: (``applied_energy_correction_source_key_undeclared``), it is pinned by
#: ``tests/api/test_api_upload_key_and_role_contracts.py`` on
#: ``/uploads/conformers``, and it spans a conformer key as well as a
#: calculation key — so a correction's source is one repair on that
#: route and would become two if the bundle routes answered differently.
W_CALCULATION_KEY_UNDECLARED = "calculation_key_undeclared"

#: How a depositor declares a calculation key in a bundle namespace.
CALCULATION_KEY_REMEDY = (
    "Every calculation in an upload carries a required 'key'; a "
    "calculation key must match one of them."
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
) -> T:
    """Turn one local key into whatever the request declared under it.

    Generic in the value type on purpose. The three bundle workflows do
    not agree on what their calc-key map holds — ``computed_species``
    keeps ``Calculation`` rows because its next move is an ownership
    check that needs one, ``computed_reaction`` and ``network_pdep`` keep
    ids — and a helper that forced one shape would have been adopted by
    whichever workflows it happened to fit, which is how there came to be
    nineteen subscripts instead of one lookup.

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
    :param subject: Completes "does not name ``{subject}`` declared in
        this upload" — ``"a calculation"``, or ``"anything"`` where the
        field accepts more than one kind of name.
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
        f"{field}='{key}' does not name {subject} declared in this upload. "
        f"{available} {remedy}".rstrip(),
        context={
            "field": field,
            "key": key,
            "declared_keys": known,
        },
        message_prefix=False,
    )


def resolve_calculation_key(
    key: str,
    declared: Mapping[str, T],
    *,
    field: str,
    code: str = W_CALCULATION_KEY_UNDECLARED,
) -> T:
    """Resolve one bundle-local *calculation* key, or refuse with a code.

    The seam every raw ``calc_keys_to_id[...]`` subscript in the three
    bundle workflows now goes through.

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
