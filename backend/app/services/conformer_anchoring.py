"""Anchor a species-owned calculation to the conformer observation it evidences.

``calculation.conformer_observation_id`` is the load-bearing anchor: it is
what makes a calculation count as evidence for a torsional basin. A NULL
anchor is not a weaker link, it is no link -- the calculation belongs to no
basin, and every basin-scoped read walks straight past it.

Why this module exists
----------------------
Two bundle workflows -- ``app.workflows.computed_reaction`` and
``app.workflows.network_pdep`` -- each carried a byte-identical private
``_anchor_species_calculation_to_observation``. Both had the same defect, and
fixing one would have left the other. They are one function now, and this is
it.

The defect, and why it was invisible
------------------------------------
The old helper answered "which observation?" from ``geometry_key`` alone::

    if calc_in.geometry_key is None:
        return                       # silent no-op
    observation_id = ...get(calc_in.geometry_key)
    if observation_id is None:
        raise ValueError(...)        # an unresolvable key is loud

An *unresolvable* key raised. An *absent* key returned, having done nothing,
and said nothing to anybody. That asymmetry is the whole bug: the two
branches describe the same outcome -- this calculation did not get anchored --
and only one of them was audible.

It stayed invisible because one field was answering two different questions.
``geometry_key`` means "which declared geometry is this calculation's
geometry?", and it was *also* the sole input to "which observation does this
calculation belong to?". Those coincide for a calculation that ran on a
conformer's geometry, and they come apart for a staged optimisation: a coarse
pre-optimisation's output geometry is genuinely not any declared conformer
geometry, so the wire schema exempts ``opt`` from supplying ``geometry_key``
and a correct producer omits it. The anchor was then dropped on the floor,
for exactly the calculations that had done nothing wrong. Measured on the
deployed database: 43 species-owned ``opt`` rows, every one of them the
``optimized_from`` parent of a calculation that *was* anchored.

What changed
------------
``conformer_key`` now answers the anchoring question directly, and it is the
only field that means it -- resolved against the same species-scoped
conformer namespace an applied correction's ``source_conformer_key`` already
used, so the machinery is not new, it was simply never offered to
calculations. ``geometry_key`` stays as a fallback so that every payload
written before this field existed anchors exactly as it did before.

And when neither resolves, the calculation is still stored -- an anchor is
not a precondition for a calculation being real -- but the upload now says
so, as an :class:`~tckdb_schemas.upload_warning.UploadWarning` on the
response. That is this codebase's established channel for "the deposit was
accepted and here is something you probably wanted that did not happen": the
same list already carries single-point energy reconciliation and
provenance-presence warnings out of these very workflows. A warning rather
than a refusal because an unanchored calculation is not a contradiction --
``resolve_conformer_group`` requires a species entry and there is no TS
counterpart, so every TS calculation is correctly unanchored (124 of them on
the deployed database, all correct). Refusing here would refuse those.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from tckdb_schemas.local_key_codes import W_CONFORMER_KEY_UNDECLARED
from tckdb_schemas.upload_warning import UploadWarning

from app.db.models.calculation import Calculation
from app.services.local_key_resolution import resolve_declared_key

#: A species-owned calculation named neither a conformer nor a resolvable
#: geometry, so nothing said which basin it belongs to and it was stored with
#: no anchor.
#:
#: Not an error. The row is real, it is persisted, and the deposit stands --
#: what is missing is a link the depositor almost certainly meant to make.
#: The remedy is in the payload and the message names it: give the
#: calculation a ``conformer_key``.
W_CALCULATION_ANCHOR_UNRESOLVED = "calculation_conformer_anchor_unresolved"

#: How a depositor declares a name in the conformer namespace. Echoed into
#: the refusal so the sentence closes with the repair rather than the
#: complaint.
CONFORMER_KEY_REMEDY = (
    "Every conformer under this species carries a required 'key'; "
    "'conformer_key' must match one of them. A sibling species's conformer "
    "is not in scope -- a calculation owned by one species entry cannot be "
    "evidence for another's basin."
)


class _AnchorableCalculationIn(Protocol):
    """The three fields anchoring reads, and nothing else.

    Structural rather than nominal because the two callers hand in two
    different pydantic models -- ``CalculationIn`` and its computed-reaction
    subclass ``ComputedReactionCalculationIn`` -- and a shared helper that
    named one of them would have to import a workflow's schema to serve the
    other.
    """

    key: str
    geometry_key: str | None
    conformer_key: str | None


def anchor_species_calculation_to_observation(
    calculation: Calculation,
    calc_in: _AnchorableCalculationIn,
    *,
    observation_id_by_conformer_key: Mapping[str, int],
    observation_id_by_geometry_key: Mapping[str, int],
    warnings: list[UploadWarning],
    field: str,
) -> None:
    """Set ``calculation.conformer_observation_id``, or say why it could not.

    Resolution order is ``conformer_key`` then ``geometry_key``, and the
    order encodes which field is the anchor: ``conformer_key`` says which
    basin this calculation is evidence for, so where a producer has stated
    that, nothing else gets a vote. ``geometry_key`` is consulted only when
    ``conformer_key`` is absent, which is what keeps every pre-existing
    payload anchoring exactly as it did.

    Every outcome is now audible. An unresolvable ``conformer_key`` raises
    (a local key that names nothing is a broken promise about the payload's
    own contents, and no correct deposit can make it mean something). An
    unresolvable ``geometry_key`` raises, as it always did. And the case
    that used to be a bare ``return`` -- nothing named, nothing anchored --
    appends a warning. There is no longer a path out of this function that
    leaves the anchor unset without recording it.

    :param calculation: The persisted row to anchor. Mutated in place.
    :param calc_in: The payload block that produced it.
    :param observation_id_by_conformer_key: This species's own conformer
        keys -> observation id. Species-scoped, which is what makes the
        anchor owner-correct by construction.
    :param observation_id_by_geometry_key: Geometry key -> observation id,
        as resolved so far by the enclosing workflow.
    :param warnings: Upload-warning accumulator; appended to when neither
        field resolves.
    :param field: Field path naming this calculation, as the depositor's own
        payload spells it. Echoed verbatim into the warning and any refusal.
    :raises CodedValueError: if a key is set but names nothing declared.
    """
    if calc_in.conformer_key is not None:
        calculation.conformer_observation_id = resolve_declared_key(
            calc_in.conformer_key,
            observation_id_by_conformer_key,
            field=f"{field}.conformer_key",
            code=W_CONFORMER_KEY_UNDECLARED,
            subject="a conformer",
            scope="declared by this species",
            remedy=CONFORMER_KEY_REMEDY,
        )
        return

    if calc_in.geometry_key is not None:
        observation_id = observation_id_by_geometry_key.get(calc_in.geometry_key)
        if observation_id is None:
            raise ValueError(
                f"Species calculation '{calc_in.key}' geometry_key "
                f"'{calc_in.geometry_key}' does not resolve to a conformer "
                f"observation."
            )
        calculation.conformer_observation_id = observation_id
        return

    warnings.append(
        UploadWarning(
            field=field,
            code=W_CALCULATION_ANCHOR_UNRESOLVED,
            message=(
                f"Calculation '{calc_in.key}' named neither a 'conformer_key' "
                f"nor a 'geometry_key', so nothing said which conformer basin "
                f"it is evidence for. It is stored, but it is anchored to no "
                f"conformer observation and basin-scoped reads will not count "
                f"it. Set 'conformer_key' to one of this species's conformer "
                f"keys to anchor it."
            ),
        )
    )
