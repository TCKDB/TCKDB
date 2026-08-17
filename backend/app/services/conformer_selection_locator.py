"""Refusing a conformer-selection *content locator* that names nothing, or too much.

The locator
-----------
``ConformerSelectionContentRef`` is how a depositor cites a stored
conformer selection when they hold no handle for it. Instead of a ref they
*describe* the row — ``{species_entry, selection_kind,
assignment_scheme_ref}``, read as "the lowest-energy selection for this
species entry under this scheme" — and the workflow expects the
description to pick out exactly one row.

Two conditions, two repairs, and one sentence
---------------------------------------------
Until this module, both failures raised one bare ``ValueError``:

    conformer_selection content locator must resolve exactly one selection.

so both arrived as ``422 validation_error`` with an empty ``context``.
They are opposite repairs. **Zero** matched means the described row is not
in the database, and no corrected body conjures it — the depositor must
deposit the selection, or they named the wrong species entry. **Several**
matched means every row the description could mean exists; the world is
fine and the *request* is under-determined. A client receiving one string
for both cannot tell which, and the two calls to action share no step.

So: zero is :data:`W_UNKNOWN_CONFORMER_SELECTION` at **404**, which is
where :mod:`app.services.upload_reference` already puts "you named a row
that is not there" for every other spelling on this route. Several is
:data:`W_AMBIGUOUS_CONFORMER_SELECTION_LOCATOR` at **422** — not 409,
because nothing about the stored data is inconsistent; the payload simply
did not say enough.

Why the 404 is built here and not by ``unknown_reference``
----------------------------------------------------------
:func:`app.services.upload_reference.unknown_reference` takes exactly one
of ``ref=`` (a public ref, echoed) or ``row_id=`` (a database id, logged
only), because the choice of spelling *is* the choice of disclosure rule.
A content locator is a third spelling and neither of those: there is no
single string the caller wrote for us to quote back. What there is, is the
locator's own describable fields, so those are what ``context`` carries.
The 404 keeps ``unknown_reference``'s body shape — ``context['field']``
and ``context['kind']`` — deliberately, so a client reading a
``field``/``kind`` pair does not have to learn a second layout depending
on how the row was named.

Why the 422 has to say what *differs*
-------------------------------------
"Be more specific" is only advice if there is something to add. The
locator has three fields and the lookup filters on all three, so **every
candidate agrees on all three by construction** — and the axis that
actually separates them is the conformer group, which the locator cannot
name at all. ``conformer_group`` is unique on ``(species_entry_id,
label)`` with NULLs distinct, so one species entry may own many groups,
each able to carry a ``lowest_energy`` selection with a NULL scheme, and
the locator joins on ``species_entry_id`` alone.

Told only "be more specific", that depositor is being sent to look for a
field that does not exist. An error whose advice cannot be followed is
worse than a vague one, because it teaches people to stop reading the
advice. :func:`discriminate` therefore computes what varies across the
candidate set and the refusal says it, with
``context['locator_can_express']`` stating outright whether the difference
is one the payload could have expressed.

The three outcomes, and which of them a request can reach
--------------------------------------------------------
:func:`discriminate` classifies a candidate set three ways, and only one
of them is reachable through a route today. That is recorded here rather
than pruned, because the *unreachable* ones are what the classification
exists to prove:

1. **Differs by a locator field** (``selection_kind``,
   ``assignment_scheme_ref``). Actionable: add the field. Structurally
   unreachable — those are the two fields the ``WHERE`` clause pins.
2. **Differs by something the locator cannot express** (the conformer
   group's ``label``, or failing that its ``public_ref``). The only
   outcome a request reaches, and the evidence that the locator is too
   narrow.
3. **Differs by nothing reportable.** A duplicate curation row rather
   than an under-determined request, and worth saying so rather than
   dressing it up as ambiguity. Unreachable while
   ``uq_conformer_selection_conformer_group_id`` holds: it permits at most
   one selection per ``(group, scheme, kind)``, and candidates share
   scheme and kind, so two candidates always sit in two groups and always
   differ by ``conformer_group_ref``.

:func:`discriminate` is a pure function over
:class:`SelectionCandidate` values precisely so that all three are
exercised by unit tests rather than asserted about in prose. A branch that
no test can enter is the failure mode this repository produces most.

What ``context`` may carry
--------------------------
No database primary key (DR-0028 Requirement 2), which rules out the
selection ids and the group ids — the obvious way to disambiguate and the
one that is not available. Every discriminator here is a public-surface
value instead: a group **label**, a group **public ref**, an assignment
scheme **public ref**, a ``selection_kind``, and a count. ``match_count``
follows ``freq_mode_index_not_unique``'s ``{"duplicate_mode_indices":
[...], "mode_count": N}`` — the house shape for "several of a thing".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.api.error_contract import CodedValueError
from app.api.errors import NotFoundError

#: A conformer-selection content locator describes no stored selection.
#: 404 rather than 422 for the reason
#: :mod:`app.services.upload_reference` gives at length: the namespace is
#: the whole database, and no corrected body can conjure the row.
W_UNKNOWN_CONFORMER_SELECTION = "unknown_conformer_selection"

#: A conformer-selection content locator describes more than one stored
#: selection. 422 and not 409: every row it could mean exists, so nothing
#: about the world is wrong — the request is under-determined, which is
#: what a 422 says.
W_AMBIGUOUS_CONFORMER_SELECTION_LOCATOR = "ambiguous_conformer_selection_locator"

#: The record kind, in the API's own vocabulary. Shared by both refusals
#: so a client keys off one spelling.
_KIND = "conformer_selection"


@dataclass(frozen=True)
class SelectionCandidate:
    """One stored selection a content locator could have meant.

    Deliberately not the ORM row. The refusal may publish only
    public-surface values, so the candidate carries exactly those plus the
    ``selection_id`` the caller needs on the success path — and the id is
    never an axis, which is what makes it impossible for
    :func:`discriminate` to put one in ``context``.
    """

    selection_id: int
    selection_kind: str
    conformer_group_label: str | None
    conformer_group_ref: str
    assignment_scheme_ref: str | None


#: The axes a candidate set can differ along, **ordered so that the ones
#: the locator can express come first**. :func:`discriminate` reports the
#: first axis that varies, so the ordering is what makes it prefer advice
#: the depositor can act on over advice they cannot.
_AXES: tuple[str, ...] = (
    "selection_kind",
    "assignment_scheme_ref",
    "conformer_group_label",
    "conformer_group_ref",
)

#: The subset of :data:`_AXES` that ``ConformerSelectionContentRef``
#: actually has a field for. ``species_entry`` is not here because it is
#: not an axis: the lookup joins on it, so it cannot vary.
_LOCATOR_AXES: frozenset[str] = frozenset({"selection_kind", "assignment_scheme_ref"})


@dataclass(frozen=True)
class Discrimination:
    """What separates the candidates a locator matched.

    :param match_count: How many selections the locator described.
    :param differs_by: Every axis that varies, in :data:`_AXES` order.
    :param discriminator: The axis the refusal names — the first entry of
        ``differs_by``, so a locator-expressible axis wins if one varies.
        ``None`` when nothing varies.
    :param discriminator_values: The distinct values of ``discriminator``,
        in candidate order — which the caller is responsible for making
        deterministic, since this is a published list. Not sorted here:
        ``None`` is a legitimate group label and sorting a list containing
        it raises.
    :param locator_can_express: Whether ``discriminator`` is a field the
        payload could have set. The half a client branches on, and the
        half that decides whether the advice is followable.
    """

    match_count: int
    differs_by: tuple[str, ...]
    discriminator: str | None
    discriminator_values: tuple[str | None, ...]
    locator_can_express: bool

    def as_context(self, *, field: str) -> dict[str, object]:
        """The refusal's ``context``. Public-surface values only."""
        return {
            "field": field,
            "kind": _KIND,
            "match_count": self.match_count,
            "differs_by": list(self.differs_by),
            "discriminator": self.discriminator,
            "discriminator_values": list(self.discriminator_values),
            "locator_can_express": self.locator_can_express,
        }


def _distinct(values: list[str | None]) -> tuple[str | None, ...]:
    """The distinct entries of *values*, in first-seen order."""
    seen: list[str | None] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return tuple(seen)


def discriminate(candidates: list[SelectionCandidate]) -> Discrimination:
    """Work out what separates *candidates*, and whether the payload could say it.

    Pure, and the reason this module has a function rather than an inline
    block: the three outcomes it distinguishes are not all reachable
    through a route (see the module docstring), so unit tests over
    hand-built candidates are the only honest way to hold the
    classification.

    :param candidates: Two or more selections one locator matched.
        Calling it with fewer is not wrong — it answers ``differs_by=()``
        — but the caller has a better refusal available in that case.
    """
    differs = tuple(
        axis
        for axis in _AXES
        if len({getattr(candidate, axis) for candidate in candidates}) > 1
    )
    discriminator = differs[0] if differs else None
    values: tuple[str | None, ...] = ()
    if discriminator is not None:
        values = _distinct([getattr(c, discriminator) for c in candidates])
    return Discrimination(
        match_count=len(candidates),
        differs_by=differs,
        discriminator=discriminator,
        discriminator_values=values,
        locator_can_express=discriminator in _LOCATOR_AXES,
    )


def unknown_conformer_selection(
    *,
    field: str,
    selection_kind: str,
    assignment_scheme_ref: str | None,
) -> NotFoundError:
    """Build the 404 for a locator that describes no stored selection.

    :param field: The payload path, e.g.
        ``"interpretation_assignments[0].conformer_selection"``. Carried in
        ``context['field']`` as well as the sentence, because one payload
        can hold several locators.
    :param selection_kind: The kind the locator asked for. Echoed: the
        caller wrote it.
    :param assignment_scheme_ref: The scheme public ref the locator asked
        for, if any. Omitted from ``context`` when absent, matching
        ``unknown_reference``'s treatment of ``ref``.
    """
    described = f"selection_kind={selection_kind!r}"
    if assignment_scheme_ref is not None:
        described += f", assignment_scheme_ref={assignment_scheme_ref!r}"
    else:
        described += ", no assignment scheme"
    context: dict[str, object] = {
        "field": field,
        "kind": _KIND,
        "selection_kind": selection_kind,
    }
    if assignment_scheme_ref is not None:
        context["assignment_scheme_ref"] = assignment_scheme_ref
    # No ``"code: "`` prefix in the prose: the exception carries the code
    # as an attribute and the handler passes it through unparsed.
    return NotFoundError(
        f"{field} ({described}) describes no conformer selection for that "
        "species entry in this database. Deposit the conformer selection "
        "first, or correct the species entry the locator names.",
        code=W_UNKNOWN_CONFORMER_SELECTION,
        context=context,
    )


def ambiguous_conformer_selection_locator(
    *,
    field: str,
    candidates: list[SelectionCandidate],
) -> CodedValueError:
    """Build the 422 for a locator that describes more than one selection.

    The sentence changes with the outcome :func:`discriminate` reports,
    because the three outcomes have three different calls to action — and
    one of them has none, which is the case worth saying out loud rather
    than papering over with "be more specific".
    """
    found = discriminate(candidates)
    listed = ", ".join(
        "unlabelled" if value is None else repr(value)
        for value in found.discriminator_values
    )
    head = (
        f"{found.match_count} conformer selections match {field}, so the "
        "content locator does not name one"
    )
    if found.discriminator is None:
        # Unreachable through a route while
        # ``uq_conformer_selection_conformer_group_id`` holds; see the
        # module docstring. Reported as what it would be if it happened --
        # duplicate curation rows -- rather than as ambiguity, because
        # "be more specific" would be false here in a second way.
        detail = (
            f"{head}. They differ in nothing this API can report, which "
            "makes them duplicate curation rows rather than an "
            "under-determined request. Please report this: no locator can "
            "separate them."
        )
    elif found.locator_can_express:
        detail = (
            f"{head}. They differ by {found.discriminator}: {listed}. Set "
            f"{found.discriminator} on the locator to name exactly one."
        )
    else:
        detail = (
            f"{head}. They differ by {found.discriminator} ({listed}), "
            "which the content locator has no field for -- so no corrected "
            "locator resolves this. Give the selections distinct assignment "
            "schemes, or report that the locator needs widening."
        )
    return CodedValueError(
        W_AMBIGUOUS_CONFORMER_SELECTION_LOCATOR,
        detail,
        context=found.as_context(field=field),
        message_prefix=False,
    )


def selection_kind_token(value: str | Enum) -> str:
    """The wire token for a ``selection_kind`` read back from the database.

    ``ConformerSelectionKind`` is a ``str`` subclass, so it survives a
    JSON encoder unnoticed -- but ``str()`` of it is ``"ConformerSelectionKind.
    lowest_energy"`` on Python 3.11+, which is what would land in
    ``context`` if this normalisation were skipped.
    """
    return value.value if isinstance(value, Enum) else value


__all__ = [
    "W_AMBIGUOUS_CONFORMER_SELECTION_LOCATOR",
    "W_UNKNOWN_CONFORMER_SELECTION",
    "Discrimination",
    "SelectionCandidate",
    "ambiguous_conformer_selection_locator",
    "discriminate",
    "selection_kind_token",
    "unknown_conformer_selection",
]
