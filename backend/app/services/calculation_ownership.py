"""One owner-consistency rule, for every record that cites another.

What the rule is
----------------
A scientific product — a thermo record, a statmech record, a transport
record, an applied energy correction, a rate coefficient's interpretation
— cites the records that produced or explain its numbers. Those records
must belong to the *same subject* the citing record is about. A thermo
record for propane citing a calculation of water is not a weaker link; it
is a meaningless one, and no correct deposit can produce it, so ADR 0008
puts the refusal in the ``block`` tier.

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

Why the module is still named for calculations
----------------------------------------------
It was written for them, and five catalogue entries name this path as the
origin of their code. #195 widened it: a thermo record cites a **statmech**
row by ``existing_statmech_id``, and a kinetics interpretation cites one by
``statmech_ref`` and a **conformer selection** by content — three more
citations of a record owned by a subject, refused by three more inline
copies of this comparison, each raising a bare ``ValueError``. The
comparison is identical; only the noun changes. Splitting it across two
modules by the type of the cited row would recreate exactly the drift the
consolidation removed, so the noun became a parameter
(:func:`assert_owned_by`) and the filename stayed put. Renaming it is a
mechanical change to five ``origin`` strings whenever someone wants it;
duplicating the rule is not recoverable.

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
from app.db.models.statmech import Statmech

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
#:
#: Reachable by two routes, which is worth writing down because it was
#: read as unreachable once and then as reachable by exactly one.
#:
#: ``_persist_statmech_block`` is shared by the species bundle and the
#: PDep bundle. Under ``/uploads/computed-species`` the calc-key map is
#: one species entry's own, so the comparison is against a value just
#: assigned; under ``/uploads/networks/pdep`` the map handed to the same
#: seam spans every species *and* every transition state. The PDep
#: payload schema narrows a **species** torsion's scan key to that
#: species's own calculations before the seam ever sees it — and does not
#: do the same for a **transition state**'s. So a TS torsion naming a
#: species scan calculation reaches this guard, and
#: ``tests/api/test_api_network_pdep_ownership.py`` provokes it on the
#: wire.
#:
#: ``/uploads/computed-reaction`` is the second route (#193). It does not
#: use the shared seam — it persists torsions inline — and resolved the
#: scan key against the bundle-global map with no owner check at all, so
#: one species' rotor could be parameterised by another's scan and the
#: deposit succeeded. That call site now routes through this function;
#: ``tests/api/test_api_bundle_torsion_scan_ownership.py`` provokes it.
#: A transition state cannot reach the rule on that route at all —
#: ``BundleTransitionStateIn`` carries no statmech, hence no torsions.
W_STATMECH_TORSION_SCAN_CALCULATION_OWNER_MISMATCH = (
    "statmech_torsion_scan_calculation_owner_mismatch"
)

#: A transport source link cites a calculation owned by another subject.
#:
#: The one code here that no write path can produce: transport's single
#: guard reads a calculation the same loop persisted against the target's
#: own species entry, its source-link payload carries no
#: ``existing_calculation_id``, and the other two callers of
#: ``resolve_and_create_transport`` pass no source calculations at all.
#: Catalogued as ``Reach.guard`` and not exported to clients; kept as the
#: tripwire for the path that changes any of those three facts.
W_TRANSPORT_SOURCE_CALCULATION_OWNER_MISMATCH = (
    "transport_source_calculation_owner_mismatch"
)

#: An applied energy correction names a source calculation owned by
#: another subject.
#:
#: Reachable since #195, and the route is the one the previous note named:
#: ``/uploads/computed-reaction`` resolves ``source_calculation_key`` in a
#: namespace spanning every species in the bundle *and* the transition
#: state, then attaches the correction to one species entry or to the TS
#: entry. Both of those comparisons used to be spelled inline and raised a
#: bare ``ValueError``, so a depositor who cited a sibling species' job
#: received ``validation_error``; both now call this function.
#:
#: The three older call sites — ``workflows/thermo.py`` once,
#: ``workflows/computed_species.py`` twice — still cannot produce it, for
#: the reason they never could: they read the calculation out of a key map
#: the enclosing block built for the target's own owner, and
#: ``AppliedEnergyCorrectionUploadPayload`` carries no
#: ``existing_calculation_id``. A code is reachable when *some* path can
#: produce it, so the entry is no longer ``Reach.guard`` and clients can
#: branch on it. ``tests/api/test_api_bundle_ownership_codes.py`` provokes
#: both bundle routes on the wire.
W_APPLIED_CORRECTION_SOURCE_CALCULATION_OWNER_MISMATCH = (
    "applied_energy_correction_source_calculation_owner_mismatch"
)

#: A thermo record's ``existing_statmech_id`` names a statmech record
#: owned by another species entry.
#:
#: The first code here over a cited row that is not a calculation, and it
#: is a separate code from ``thermo_source_calculation_owner_mismatch``
#: for the reason the whole family is separate: "you cited another
#: species' partition function" and "you cited another species' job" are
#: different repairs reaching different fields.
#:
#: Reachable by the first clause of the reachability rule and the
#: plainest instance of it — ``existing_statmech_id`` is a literal row id
#: the depositor supplies, so the enclosing block scopes nothing.
W_THERMO_STATMECH_OWNER_MISMATCH = "thermo_statmech_owner_mismatch"

#: A kinetics interpretation assignment names a statmech record that does
#: not belong to the subject it was assigned to.
#:
#: One code across both subject kinds, deliberately. The reactant/product
#: case compares against the participant's species entry and the
#: transition-state case against the declared TS entry, but the field is
#: the same ``statmech_ref`` and the repair is the same sentence: name the
#: statmech that belongs to this subject. Which owner disagreed is a fact
#: about the request, not a different contract, and it is already carried
#: in ``context["owner_kind"]``.
W_KINETICS_INTERPRETATION_STATMECH_OWNER_MISMATCH = (
    "kinetics_interpretation_statmech_owner_mismatch"
)

#: A kinetics interpretation's conformer selection belongs to a different
#: species entry than the statmech it accompanies.
#:
#: Separate from the code above because the offending field is a different
#: one: the depositor's ``statmech_ref`` may be entirely correct and the
#: ``conformer_selection`` locator the thing to fix. Both are resolved in
#: a database-wide namespace — a public ref and a content locator — so
#: neither is scoped by the enclosing block.
W_KINETICS_INTERPRETATION_CONFORMER_SELECTION_OWNER_MISMATCH = (
    "kinetics_interpretation_conformer_selection_owner_mismatch"
)


def assert_owned_by(
    *,
    subject_noun: str,
    row_id: int | None,
    row_species_entry_id: int | None,
    row_transition_state_entry_id: int | None,
    code: str,
    target: str,
    context: str,
    species_entry_id: int | None = None,
    transition_state_entry_id: int | None = None,
) -> None:
    """Refuse a cited record that belongs to another subject.

    The one implementation. :func:`assert_calculation_owned_by` and
    :func:`assert_statmech_owned_by` are typed spellings of it for the two
    ORM rows that carry both owner columns; a caller holding neither row —
    a conformer selection, whose species is reached through its group —
    calls this directly with the two ids it resolved.

    :param subject_noun: What the cited record is, as a depositor would
        say it (``"calculation"``, ``"statmech record"``, ``"conformer
        selection"``). Appears in the sentence, never in the code.
    :param row_id: Primary key of the cited row, for the log only. Never
        reaches the response body.
    :param row_species_entry_id: The species entry the cited record
        belongs to, or ``None``.
    :param row_transition_state_entry_id: The transition-state entry the
        cited record belongs to, or ``None``.
    :param code: The machine-readable code this refusal reports; one of
        the ``W_*`` constants in this module. Passed rather than derived
        because the subject is what a client tells apart.
    :param target: The record that cited it, named the way a depositor
        would say it (``"thermo"``, ``"statmech torsion"``). Appears in
        the sentence, never in the code.
    :param context: Field path naming the offending link, echoed verbatim.
    :param species_entry_id: The species entry the target belongs to, or
        ``None`` when the target is not a species record.
    :param transition_state_entry_id: The transition-state entry the
        target belongs to, or ``None``.
    :raises CodedValueError: if the cited record belongs to a different
        subject than the one(s) supplied.
    :raises ValueError: if neither owner id is supplied. A check with
        nothing to compare against would pass for every input, which is
        the one failure mode a defensive guard must not have.
    """
    if species_entry_id is None and transition_state_entry_id is None:
        raise ValueError(
            "assert_owned_by was given neither a species entry nor a "
            "transition state entry to check against; such a call would "
            f"accept every {subject_noun} and guard nothing."
        )

    if species_entry_id is not None and row_species_entry_id != species_entry_id:
        owner_noun = "species entry"
        expected: int = species_entry_id
        actual = row_species_entry_id
    elif transition_state_entry_id is not None and (
        row_transition_state_entry_id != transition_state_entry_id
    ):
        owner_noun = "transition state entry"
        expected = transition_state_entry_id
        actual = row_transition_state_entry_id
    else:
        return

    # ``context`` already names the record the way the depositor wrote it,
    # which is the identifier they can act on. The row ids go to the log
    # instead -- a 422 body must not hand out primary keys.
    logger.warning(
        "%s: %s id=%s is owned by %s=%s, not %s (target=%s)",
        context,
        subject_noun,
        row_id,
        owner_noun.replace(" ", "_") + "_id",
        actual,
        expected,
        target,
    )
    raise CodedValueError(
        code,
        f"{context}: this {subject_noun} belongs to another {owner_noun}, "
        f"not to the {target} target. A supporting {subject_noun} must be "
        f"one of the target {owner_noun}'s own.",
        context={
            "field": context,
            "target": target,
            "owner_kind": owner_noun.replace(" ", "_"),
        },
        message_prefix=False,
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

    See :func:`assert_owned_by` for every parameter; this reads the two
    owner columns off the row so a caller cannot pass the wrong pair.
    """
    assert_owned_by(
        subject_noun="calculation",
        row_id=calculation.id,
        row_species_entry_id=calculation.species_entry_id,
        row_transition_state_entry_id=calculation.transition_state_entry_id,
        code=code,
        target=target,
        context=context,
        species_entry_id=species_entry_id,
        transition_state_entry_id=transition_state_entry_id,
    )


def assert_statmech_owned_by(
    statmech: Statmech,
    *,
    code: str,
    target: str,
    context: str,
    species_entry_id: int | None = None,
    transition_state_entry_id: int | None = None,
) -> None:
    """Refuse a cited statmech record that belongs to another subject.

    ``statmech`` carries the same two nullable owner columns a calculation
    does — exactly one of them is non-null, by check constraint — so the
    comparison is the calculation one with a different noun. See
    :func:`assert_owned_by`.
    """
    assert_owned_by(
        subject_noun="statmech record",
        row_id=statmech.id,
        row_species_entry_id=statmech.species_entry_id,
        row_transition_state_entry_id=statmech.transition_state_entry_id,
        code=code,
        target=target,
        context=context,
        species_entry_id=species_entry_id,
        transition_state_entry_id=transition_state_entry_id,
    )


__all__ = [
    "W_APPLIED_CORRECTION_SOURCE_CALCULATION_OWNER_MISMATCH",
    "W_KINETICS_INTERPRETATION_CONFORMER_SELECTION_OWNER_MISMATCH",
    "W_KINETICS_INTERPRETATION_STATMECH_OWNER_MISMATCH",
    "W_STATMECH_SOURCE_CALCULATION_OWNER_MISMATCH",
    "W_STATMECH_TORSION_SCAN_CALCULATION_OWNER_MISMATCH",
    "W_THERMO_SOURCE_CALCULATION_OWNER_MISMATCH",
    "W_THERMO_STATMECH_OWNER_MISMATCH",
    "W_TRANSPORT_SOURCE_CALCULATION_OWNER_MISMATCH",
    "assert_calculation_owned_by",
    "assert_owned_by",
    "assert_statmech_owned_by",
]
