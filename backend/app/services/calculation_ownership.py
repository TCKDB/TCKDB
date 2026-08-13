"""One owner-consistency rule, for every product that cites a calculation.

What the rule is
----------------
A scientific product — a thermo record, a statmech record, a transport
record, an applied energy correction — cites the calculations that produced
its numbers. Those calculations must belong to the *same subject* the
product is about. A thermo record for propane citing a calculation of
water is not a weaker link; it is a meaningless one, and no correct deposit
can produce it, so ADR 0008 puts the refusal in the ``block`` tier.

Why it lives here
-----------------
The rule was written five times: once in ``app.workflows.thermo``, once in
``app.workflows.transport``, once in ``app.services.statmech_resolution``
(moved there by #157) and twice more inline in the two bundle workflows,
each spelling the same comparison and each phrasing the refusal
differently. Only the thermo copy was pinned by a test, so the other four
could drift without anything going red — and they had already drifted:
thermo's copy logged nothing, so the row ids an operator needs to diagnose
a cross-owner link existed for statmech and transport and not for thermo.

One implementation cannot drift. What varies between the products is what
they *call* the offence, and that stays a parameter: a client repairing a
thermo link and a client repairing a torsion's scan link are doing
different things, so they get different codes (#158).

What never appears in the refusal
---------------------------------
Row ids. ``context`` names the offending field the way the depositor wrote
it, which is the identifier they can act on; the ids of the rows that
disagreed go to the log, where the operator is (DR-0028 Requirement 2).
"""

from __future__ import annotations

import logging

from app.api.error_contract import CodedValueError
from app.db.models.calculation import Calculation

logger = logging.getLogger(__name__)

#: A thermo source link cites a calculation owned by another subject.
#:
#: Deliberately distinct from the statmech and transport codes, and from
#: ``thermo_source_role_type_mismatch``: "you cited someone else's
#: calculation" and "you gave this calculation the wrong role" are
#: different repairs, and so are the same mistake made about an enthalpy
#: and about a partition function.
W_THERMO_SOURCE_CALCULATION_OWNER_MISMATCH = (
    "thermo_source_calculation_owner_mismatch"
)

#: A statmech source link cites a calculation owned by another subject.
W_STATMECH_SOURCE_CALCULATION_OWNER_MISMATCH = (
    "statmech_source_calculation_owner_mismatch"
)

#: A statmech torsion names a scan calculation owned by another subject.
#: Separate from the statmech source-link code because the torsion's scan
#: is a different field with a different repair — the depositor picked the
#: wrong rotor scan, not the wrong supporting job.
W_STATMECH_TORSION_SCAN_CALCULATION_OWNER_MISMATCH = (
    "statmech_torsion_scan_calculation_owner_mismatch"
)

#: A transport source link cites a calculation owned by another subject.
W_TRANSPORT_SOURCE_CALCULATION_OWNER_MISMATCH = (
    "transport_source_calculation_owner_mismatch"
)

#: An applied energy correction names a source calculation owned by
#: another subject.
W_APPLIED_CORRECTION_SOURCE_CALCULATION_OWNER_MISMATCH = (
    "applied_energy_correction_source_calculation_owner_mismatch"
)


def assert_calculation_owned_by(
    calculation: Calculation,
    *,
    code: str,
    target: str,
    context: str,
    species_entry_id: int | None = None,
    transition_state_entry_id: int | None = None,
) -> None:
    """Refuse a supporting calculation that belongs to another subject.

    :param calculation: The resolved calculation row the deposit cites.
    :param code: The machine-readable code this refusal reports; one of
        the ``W_*`` constants in this module. Passed rather than derived
        because the subject is what a client tells apart.
    :param target: The record the calculation was cited by, named the way
        a depositor would say it (``"thermo"``, ``"statmech torsion"``).
        Appears in the sentence, never in the code.
    :param context: Field path naming the offending link, echoed verbatim.
    :param species_entry_id: The species entry the target belongs to, or
        ``None`` when the target is not a species record.
    :param transition_state_entry_id: The transition-state entry the
        target belongs to, or ``None``.
    :raises CodedValueError: if the calculation belongs to a different
        subject than the one(s) supplied.
    :raises ValueError: if neither owner id is supplied. A check with
        nothing to compare against would pass for every input, which is
        the one failure mode a defensive guard must not have.
    """
    if species_entry_id is None and transition_state_entry_id is None:
        raise ValueError(
            "assert_calculation_owned_by was given neither a species entry "
            "nor a transition state entry to check against; such a call "
            "would accept every calculation and guard nothing."
        )

    if species_entry_id is not None and (
        calculation.species_entry_id != species_entry_id
    ):
        owner_noun = "species entry"
        expected: int = species_entry_id
        actual = calculation.species_entry_id
    elif transition_state_entry_id is not None and (
        calculation.transition_state_entry_id != transition_state_entry_id
    ):
        owner_noun = "transition state entry"
        expected = transition_state_entry_id
        actual = calculation.transition_state_entry_id
    else:
        return

    # ``context`` already names the calculation the way the depositor wrote
    # it, which is the identifier they can act on. The row ids go to the
    # log instead -- a 422 body must not hand out primary keys.
    logger.warning(
        "%s: calculation id=%s is owned by %s=%s, not %s (target=%s)",
        context,
        calculation.id,
        owner_noun.replace(" ", "_") + "_id",
        actual,
        expected,
        target,
    )
    raise CodedValueError(
        code,
        f"{context}: this calculation belongs to another {owner_noun}, not "
        f"to the {target} target. A supporting calculation must be one of "
        f"the target {owner_noun}'s own.",
        context={
            "field": context,
            "target": target,
            "owner_kind": owner_noun.replace(" ", "_"),
        },
        message_prefix=False,
    )


__all__ = [
    "W_APPLIED_CORRECTION_SOURCE_CALCULATION_OWNER_MISMATCH",
    "W_STATMECH_SOURCE_CALCULATION_OWNER_MISMATCH",
    "W_STATMECH_TORSION_SCAN_CALCULATION_OWNER_MISMATCH",
    "W_THERMO_SOURCE_CALCULATION_OWNER_MISMATCH",
    "W_TRANSPORT_SOURCE_CALCULATION_OWNER_MISMATCH",
    "assert_calculation_owned_by",
]
