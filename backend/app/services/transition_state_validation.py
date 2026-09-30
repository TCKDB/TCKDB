"""Persistence seam for structured validation evidence on a TS candidate.

Every deposit path that can carry a transition state routes through here — the
pressure-dependent network bundle, the computed-reaction bundle, and the
standalone transition-state upload — so all three write identical rows and
report an identical gap.

Three kinds of evidence are written: ``irc`` (the path connects the declared
endpoints), ``energy_ordering`` (the saddle point lies above both wells, with
the energies compared) and ``imaginary_mode`` (the frequency calculation found
the expected imaginary mode).

IRC evidence is recommended, not required. Refusing a deposit without it would
lose the saddle point entirely, so its absence is reported as a structured
:class:`UploadWarning` instead. That is only honest if every path can actually
deposit the evidence: before this seam existed, only the PDep bundle could, so
a TS uploaded any other way always read back as ``validation: {"irc":
"absent"}`` even when the depositor had run the IRC.

What counts as IRC evidence
---------------------------
Only a passing ``irc`` record. The warning is about the IRC, because that is
the kind that says the saddle point connects the declared reactants and
products. A passing ``energy_ordering`` says it lies above both wells and a
passing ``imaginary_mode`` says it has the expected curvature; both are true
of a saddle point that connects some *other* pair of minima, so neither can
stand in for the IRC, and counting them would let a deposit silence the
warning while still not showing what it warns about.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.fragments.ts_validation_evidence import (
    TransitionStateValidationEvidenceIn,
)
from tckdb_schemas.upload_warning import UploadWarning

from app.db.models.calculation import Calculation
from app.db.models.common import CalculationType, ReactionRole
from app.db.models.reaction import ReactionEntryStructureParticipant
from app.db.models.transition_state import (
    TransitionStateValidationEnergy,
    TransitionStateValidationEvidence,
)
from app.scientific_checks import (
    CheckTier,
    CodeChannel,
    PythonCheck,
    ScientificCheck,
)
from app.services.calculation_ownership import (
    W_TS_VALIDATION_SOURCE_CALCULATION_OWNER_MISMATCH,
    assert_calculation_owned_by,
)
from app.services.local_key_resolution import resolve_calculation_key
from app.services.reaction_atom_map import (
    validate_atom_map_agrees_with_irc_evidence,
)
from app.services.reaction_resolution import (
    validate_ts_evidence_participant_composition,
)

#: Emitted when a transition state is deposited with no *passing* IRC evidence.
W_MISSING_TS_IRC_EVIDENCE = "transition_state_missing_irc_evidence"

#: Emitted when one energy kind of an ``energy_ordering`` record was taken
#: from calculations at more than one level of theory.
W_TS_ENERGY_ORDERING_MIXED_LEVELS = "transition_state_energy_ordering_mixed_levels"

#: The calculation types an ``energy_ordering`` energy may be taken from, by
#: ``energy_kind``. An electronic energy is what an ``sp`` reports or what an
#: ``opt`` ends on. An E0 includes the zero-point energy, which only a
#: ``freq`` calculation carries, so the E0 is cited to the ``freq`` it came
#: from. A record that composes an E0 from an ``sp`` and a ``freq`` cites the
#: ``freq`` here and the ``sp`` in its ``electronic`` group. ``irc``, ``scan``
#: and ``path_search`` report the energies of points along a path, not the
#: energy of a stationary point, so they are never a source.
_ENERGY_SOURCE_TYPES: dict[str, frozenset[CalculationType]] = {
    "electronic": frozenset({CalculationType.sp, CalculationType.opt}),
    "e0": frozenset({CalculationType.freq}),
}

#: How far apart, in cm^-1, a record's imaginary frequency and the frequency
#: result it cites may be before they are taken to disagree. Wider than
#: rounding in a log, far narrower than two different modes.
_IMAGINARY_FREQUENCY_TOLERANCE_CM1 = 1.0


def _assert_energy_sources_are_comparable(
    session: Session,
    record: TransitionStateValidationEvidenceIn,
    energy_calculation_ids: Sequence[int],
    *,
    subject_label: str,
    field_path: str,
    record_index: int,
    warnings: list[UploadWarning] | None,
) -> None:
    """Hold an ordering's sources against the kind of energy each claims.

    The sources are already resolved, so these are the checks the record's
    own numbers cannot make. The type rule is definitional and blocks: an
    energy taken from an IRC point, or an E0 from a bare single point, is not
    the quantity it is labelled as. Mixed levels of theory within one energy
    kind only warn (ADR 0008): comparing across levels is usually a mistake,
    but a depositor may do it deliberately (a literature well against a
    computed saddle point), and a warning leaves that legitimate case
    depositable while still naming the gap.
    """

    levels_by_kind: dict[str, set[int]] = {}
    for energy_index, (energy, calculation_id) in enumerate(
        zip(record.energies or [], energy_calculation_ids, strict=True)
    ):
        calculation = session.get(Calculation, calculation_id)
        assert calculation is not None  # resolved and ownership-checked above
        allowed = _ENERGY_SOURCE_TYPES[energy.energy_kind]
        if calculation.type not in allowed:
            raise ValueError(
                f"Transition state '{subject_label}' "
                f"{field_path}[{record_index}].energies[{energy_index}] takes a "
                f"'{energy.energy_kind}' energy from a "
                f"'{calculation.type.value}' calculation; a "
                f"'{energy.energy_kind}' energy comes from a "
                f"{' or '.join(sorted(t.value for t in allowed))} calculation."
            )
        if calculation.lot_id is not None:
            levels_by_kind.setdefault(energy.energy_kind, set()).add(calculation.lot_id)

    if warnings is None:
        return
    for energy_kind, levels in sorted(levels_by_kind.items()):
        if len(levels) > 1:
            warnings.append(
                UploadWarning(
                    field=f"{field_path}[{record_index}].energies",
                    code=W_TS_ENERGY_ORDERING_MIXED_LEVELS,
                    message=(
                        f"Transition state '{subject_label}' energy_ordering "
                        f"compares '{energy_kind}' energies taken at "
                        f"{len(levels)} different levels of theory. An ordering "
                        "is only meaningful between energies at one level."
                    ),
                )
            )


def _assert_imaginary_mode_matches_freq(
    source: Calculation,
    record: TransitionStateValidationEvidenceIn,
    *,
    subject_label: str,
) -> None:
    """Hold an ``imaginary_mode`` record against the frequency result it cites.

    The record is the producer's statement of what that calculation found,
    and the calculation's own stored result is the evidence, so a record that
    contradicts it is refused rather than stored beside it. Absence is not
    contradiction: a frequency result that records no ``n_imag`` or no value
    is simply not compared.

    A passing record with more than one imaginary mode must say which one is
    the reaction coordinate, because that is what ``stationary_point``
    requires of a transition state that has extra (typically torsional)
    imaginary modes. More than one mode is not refused as such: ADR 0012
    deliberately allows it.
    """

    freq = source.freq_result
    if freq is None:
        return
    count = record.imaginary_frequency_count
    if count is not None and freq.n_imag is not None and count != freq.n_imag:
        raise ValueError(
            f"Transition state '{subject_label}' imaginary_mode evidence states "
            f"{count} imaginary mode(s), but the frequency calculation it cites "
            f"recorded {freq.n_imag}."
        )
    value = record.imaginary_frequency_cm1
    if (
        value is not None
        and freq.imag_freq_cm1 is not None
        and abs(value - freq.imag_freq_cm1) > _IMAGINARY_FREQUENCY_TOLERANCE_CM1
    ):
        raise ValueError(
            f"Transition state '{subject_label}' imaginary_mode evidence states "
            f"an imaginary frequency of {value} cm^-1, but the frequency "
            f"calculation it cites recorded {freq.imag_freq_cm1} cm^-1."
        )
    effective = count if count is not None else freq.n_imag
    if (
        record.passed
        and effective is not None
        and effective > 1
        and freq.reaction_coordinate_mode_index is None
    ):
        raise ValueError(
            f"Transition state '{subject_label}' imaginary_mode evidence passes "
            f"with {effective} imaginary modes, but the frequency calculation it "
            "cites does not designate which one is the reaction coordinate."
        )


def _assert_energy_source_belongs_to_participant(
    session: Session,
    *,
    calculation_id: int,
    participant: str,
    reaction_entry_id: int,
    transition_state_entry_id: int,
    subject_label: str,
    context: str,
) -> None:
    """Refuse an energy taken from a calculation of some other subject.

    ``ts`` must come from the saddle point's own calculation, and
    ``reactant:N`` / ``product:N`` from the N-th declared participant's own
    species. The wire schemas refuse the same mistake earlier with a list of
    the keys that would have worked; this is what stops a caller that reached
    the seam another way. The message names the field the depositor wrote and
    never a row id (DR-0028).
    """

    calculation = session.get(Calculation, calculation_id)
    if calculation is None:
        raise ValueError(
            f"Transition state '{subject_label}' {context} names a calculation "
            "that does not exist."
        )
    species_entry_id: int | None = None
    if participant != "ts":
        side, _, position = participant.partition(":")
        species_entry_id = session.scalars(
            select(ReactionEntryStructureParticipant.species_entry_id).where(
                ReactionEntryStructureParticipant.reaction_entry_id == reaction_entry_id,
                ReactionEntryStructureParticipant.role == ReactionRole(side),
                ReactionEntryStructureParticipant.participant_index == int(position),
            )
        ).first()
        if species_entry_id is None:
            raise ValueError(
                f"Transition state '{subject_label}' {context} is the energy of "
                f"'{participant}', which the reaction does not declare."
            )
    assert_calculation_owned_by(
        calculation,
        code=W_TS_VALIDATION_SOURCE_CALCULATION_OWNER_MISMATCH,
        target="transition-state validation energy",
        context=f"{context} (the energy of '{participant}')",
        species_entry_id=species_entry_id,
        transition_state_entry_id=(
            transition_state_entry_id if participant == "ts" else None
        ),
    )


def persist_transition_state_validation_evidence(
    session: Session,
    evidence: Sequence[TransitionStateValidationEvidenceIn],
    *,
    transition_state_entry_id: int,
    reconstruction_calculation_ids: Sequence[int | None],
    calculation_ids_by_key: Mapping[str, int] | None = None,
    subject_label: str,
    field_path: str,
    reaction_entry_id: int,
    transition_state_geometry_id: int | None,
    created_by: int | None = None,
    warnings: list[UploadWarning] | None = None,
) -> list[TransitionStateValidationEvidence]:
    """Write one evidence row per record and report an absent-evidence gap.

    :param evidence: Producer-declared evidence records, already validated.
    :param transition_state_entry_id: The TS candidate the evidence describes.
    :param reconstruction_calculation_ids: Resolved calculation id per record,
        positionally aligned with ``evidence``. Each path resolves its own
        locator (a bundle-local calculation key, or the upload's single
        calculation of the needed type) before calling in. ``None`` is the
        right value for an ``energy_ordering`` record, which has no single
        source calculation and names one per energy, and only for it.
    :param calculation_ids_by_key: The path's calculation-key namespace, which
        resolves each ``energy_ordering`` energy's ``source_calculation_key``.
        ``None`` on a path with no key namespace, which therefore cannot carry
        an ``energy_ordering`` record.
    :param subject_label: Producer-facing name of the TS, for the warning text.
    :param field_path: Dot-path of the evidence field, for the warning.
    :param reaction_entry_id: The reaction whose declared participants the
        evidence's ``reactant:N`` / ``product:N`` mappings name. Required rather
        than optional: it is what lets the element check below run on *every*
        path, and a default would let a new path silently opt out of it.
    :param transition_state_geometry_id: Saddle-point geometry the mappings'
        atom indices count into. It is both checked against and *recorded* on
        every row that carries a mapping, so a reader can tell which ordering
        the indices were counted in rather than having to guess at one; a
        record with no mapping has no indices and stores no geometry. ``None``
        is accepted only for a path with no geometry and no mappings, which
        skips the element check as an absence.
    :param warnings: Optional sink for the absent-evidence warning.
    :returns: The persisted rows.
    """

    if len(reconstruction_calculation_ids) != len(evidence):
        raise ValueError(
            "reconstruction_calculation_ids must align with the evidence records."
        )

    # Definitional, therefore blocking (ADR 0008). The mappings' *shape* was
    # already settled at the wire boundary by ``validate_ts_evidence_set``;
    # what the mapped atoms actually **are** needs a species SMILES and so
    # needs RDKit, which the chemistry-free wire package does not have. Doing
    # it here rather than in the three workflows is deliberate: this seam is
    # the one place all three deposit paths already meet, and three call sites
    # is exactly how the pseudo-exemption divergence next door happened.
    validate_ts_evidence_participant_composition(
        session,
        evidence,
        reaction_entry_id=reaction_entry_id,
        transition_state_geometry_id=transition_state_geometry_id,
        subject_label=subject_label,
        field_path=field_path,
    )

    # Two passes, and the order is the point. Everything a record can be
    # refused for is settled in the first, before a row exists, so a refusal on
    # the third energy of the second record leaves nothing of the first record
    # in the session for anything later in the same request to see. The second
    # pass only writes.
    resolved_energy_ids: list[list[int]] = []
    for record_index, (record, calculation_id) in enumerate(
        zip(evidence, reconstruction_calculation_ids, strict=True)
    ):
        if record.kind == "energy_ordering":
            if calculation_id is not None:
                raise ValueError(
                    f"Transition state '{subject_label}' energy_ordering evidence "
                    "names its source calculations per energy and takes no "
                    "record-level calculation."
                )
        elif calculation_id is None:
            raise ValueError(
                f"Transition state '{subject_label}' validation evidence could not "
                f"be linked to the {'irc' if record.kind == 'irc' else 'freq'} "
                "calculation that produced it."
            )
        elif record.kind == "imaginary_mode":
            source = session.get(Calculation, calculation_id)
            if source is None:
                raise ValueError(
                    f"Transition state '{subject_label}' imaginary_mode evidence "
                    "names a calculation that does not exist."
                )
            assert_calculation_owned_by(
                source,
                code=W_TS_VALIDATION_SOURCE_CALCULATION_OWNER_MISMATCH,
                target="transition-state validation evidence",
                context=f"{field_path}[{record_index}].source_calculation_key",
                transition_state_entry_id=transition_state_entry_id,
            )
            if source.type != CalculationType.freq:
                raise ValueError(
                    f"Transition state '{subject_label}' imaginary_mode evidence "
                    "must name a freq calculation of this transition state."
                )
            _assert_imaginary_mode_matches_freq(
                source, record, subject_label=subject_label
            )
        # A mapping's values are ``geometry_atom.atom_index`` values counted
        # into the saddle-point geometry, so a record carrying one must say
        # which geometry that is. The database refuses the combination too
        # (``ck_transition_state_validation_evidence_mapping_names_geometry``);
        # raising here turns a caller's omission into a sentence naming the
        # argument that is missing rather than a constraint name.
        has_mapping = (
            record.reactant_participant_mapping is not None
            or record.product_participant_mapping is not None
        )
        if has_mapping and transition_state_geometry_id is None:
            raise ValueError(
                f"Transition state '{subject_label}' {field_path} carries "
                "participant mappings but no transition_state_geometry_id. The "
                "mappings' atom indices count into the saddle-point geometry, "
                "and an index with no geometry named beside it does not "
                "identify an atom."
            )

        energy_ids: list[int] = []
        for energy_index, energy in enumerate(record.energies or []):
            context = f"{field_path}[{record_index}].energies[{energy_index}]"
            if calculation_ids_by_key is None:
                raise ValueError(
                    f"Transition state '{subject_label}' {context} cannot be "
                    "linked to its source calculation: this deposit path has no "
                    "calculation-key namespace."
                )
            source_calculation_id = resolve_calculation_key(
                energy.source_calculation_key,
                calculation_ids_by_key,
                field=f"{context}.source_calculation_key",
            )
            _assert_energy_source_belongs_to_participant(
                session,
                calculation_id=source_calculation_id,
                participant=energy.participant,
                reaction_entry_id=reaction_entry_id,
                transition_state_entry_id=transition_state_entry_id,
                subject_label=subject_label,
                context=context,
            )
            energy_ids.append(source_calculation_id)
        if energy_ids:
            _assert_energy_sources_are_comparable(
                session,
                record,
                energy_ids,
                subject_label=subject_label,
                field_path=field_path,
                record_index=record_index,
                warnings=warnings,
            )
        resolved_energy_ids.append(energy_ids)

    rows: list[TransitionStateValidationEvidence] = []
    for record, calculation_id, energy_ids in zip(
        evidence, reconstruction_calculation_ids, resolved_energy_ids, strict=True
    ):
        has_mapping = (
            record.reactant_participant_mapping is not None
            or record.product_participant_mapping is not None
        )
        row = TransitionStateValidationEvidence(
            transition_state_entry_id=transition_state_entry_id,
            kind=record.kind,
            passed=record.passed,
            rationale=record.rationale,
            reconstruction_calculation_id=calculation_id,
            reactant_participant_mapping=record.reactant_participant_mapping,
            product_participant_mapping=record.product_participant_mapping,
            transition_state_geometry_id=(
                transition_state_geometry_id if has_mapping else None
            ),
            imaginary_frequency_count=record.imaginary_frequency_count,
            imaginary_frequency_cm1=record.imaginary_frequency_cm1,
            mode_displacement_agrees=record.mode_displacement_agrees,
            created_by=created_by,
        )
        session.add(row)
        rows.append(row)
        for energy, source_calculation_id in zip(
            record.energies or [], energy_ids, strict=True
        ):
            row.compared_energies.append(
                TransitionStateValidationEnergy(
                    participant=energy.participant,
                    energy_kind=energy.energy_kind,
                    energy_hartree=energy.energy_hartree,
                    source_calculation_id=source_calculation_id,
                )
            )

    # The same comparison the atom-map seam runs, from the other side. Whichever
    # of the two surfaces a deposit writes second is the one that can see both,
    # and today that is always the atom map — ``persist_computed_reaction_upload``
    # is the only path with an ``atom_map`` field and writes it after this call,
    # while every transition-state entry is created fresh by the deposit that
    # writes it, so a map can never arrive for a saddle point deposited earlier.
    # Both of those are incidental orderings a later edit could reverse, and the
    # check reads both surfaces from the database precisely so it does not
    # depend on either. Calling it here costs one indexed lookup that finds
    # nothing on today's paths and removes the ordering from the contract.
    validate_atom_map_agrees_with_irc_evidence(
        session,
        reaction_entry_id=reaction_entry_id,
        transition_state_entry_id=transition_state_entry_id,
        subject_label=subject_label,
        field_path=field_path,
    )

    if warnings is not None and not any(
        record.passed and record.kind == "irc" for record in evidence
    ):
        warnings.append(
            UploadWarning(
                field=field_path,
                code=W_MISSING_TS_IRC_EVIDENCE,
                message=(
                    f"Transition state '{subject_label}' was deposited without passed "
                    "IRC validation evidence. The saddle point is stored, but nothing "
                    "in this deposit shows it connects the declared reactants and "
                    "products. Energy-ordering and imaginary-mode evidence say "
                    "something else, and do not stand in for it."
                ),
            )
        )
    return rows


CHECK_TS_IRC_EVIDENCE = ScientificCheck(
    group="Stationary points",
    sort_key=6,
    code=W_MISSING_TS_IRC_EVIDENCE,
    asserts=(
        "A deposited saddle point should carry passing intrinsic-reaction-"
        "coordinate evidence that it connects the declared reactants and "
        "products."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    tier_rationale=(
        "Absence, not contradiction. Refusing a transition state without an "
        "IRC would lose the saddle point entirely, and a saddle point with no "
        "IRC is an incomplete record rather than a false one. The evidence is "
        "recommended, not required."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            persist_transition_state_validation_evidence,
            note=(
                "Every path that can carry a transition state routes through "
                "this seam — the PDep bundle, the computed-reaction bundle and "
                "the standalone transition-state upload — so all three write "
                "identical rows and report an identical gap. Before the seam "
                "existed only the PDep bundle could deposit the evidence, so a "
                "TS uploaded any other way always read back as ``irc: "
                "absent`` even when the depositor had run one."
            ),
        ),
    ),
    escape_hatch=(
        "None needed — the warning is the accommodation. Note the warning "
        "fires on absence of a *passing* ``irc`` record, so an IRC that was "
        "run and failed is stored and still warns, and so does a deposit "
        "whose only evidence is an energy ordering or an imaginary mode: "
        "neither shows the saddle point connects the declared endpoints."
    ),
)


CHECK_TS_ENERGY_ORDERING_LEVELS = ScientificCheck(
    group="Stationary points",
    sort_key=7,
    code=W_TS_ENERGY_ORDERING_MIXED_LEVELS,
    asserts=(
        "The energies an energy-ordering record compares should be taken at "
        "one level of theory per energy kind."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    tier_rationale=(
        "An expectation, not a definition. An ordering across levels is "
        "usually a mistake, but a deliberate one (a literature well against a "
        "computed saddle point) is a legitimate record, so refusing it would "
        "lose correct science; the warning names the gap instead (ADR 0008)."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            persist_transition_state_validation_evidence,
            note=(
                "Runs in the shared evidence seam, where each energy's source "
                "calculation has already been resolved, so all three deposit "
                "paths report it alike."
            ),
        ),
    ),
    escape_hatch=(
        "None needed: the warning is the accommodation. Take every energy of "
        "a kind at one level, or accept the warning."
    ),
)


__all__ = [
    "W_MISSING_TS_IRC_EVIDENCE",
    "W_TS_ENERGY_ORDERING_MIXED_LEVELS",
    "persist_transition_state_validation_evidence",
]
