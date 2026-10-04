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
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.fragments.calculation import (
    COMPOSITE_PRINTED_ROUNDING_HARTREE,
    composite_arithmetic_tolerance_hartree,
)
from tckdb_schemas.fragments.ts_validation_evidence import (
    TransitionStateValidationEvidenceIn,
)
from tckdb_schemas.upload_warning import UploadWarning

from app.api.error_contract import CodedValueError
from app.db.models.calculation import (
    Calculation,
    CalculationInputGeometry,
    CalculationOutputGeometry,
)
from app.db.models.common import CalculationGeometryRole, CalculationType, ReactionRole
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
from app.services.network_energy_sources import (
    E_NETWORK_STATE_ENERGY_SUM_MISMATCH,
    W_NETWORK_STATE_ENERGY_SUM_NOT_COMPARED,
    collect_state_energy_source_warnings,
    compare_state_energy_sums,
)
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

#: A stated energy contradicts the energy TCKDB stores for the calculation it
#: cites, beyond the printed-precision tolerance (issue #638). Block tier
#: (ADR 0008): the record names a calculation and a number, and the number is
#: not that calculation's.
E_TS_ENERGY_ORDERING_STATED_ENERGY_MISMATCH = "ts_energy_ordering_stated_energy_mismatch"

#: Emitted when some stated energy of an ``energy_ordering`` record could not be
#: held against a stored one. The record is accepted and each such energy is
#: stored as ``not_compared`` with the reason.
W_TS_ENERGY_ORDERING_NOT_COMPARED = "transition_state_energy_ordering_not_compared"

#: ``transition_state_validation_energy.stored_energy_comparison`` values.
COMPARISON_AGREES = "agrees"
COMPARISON_NOT_COMPARED = "not_compared"

#: Why a stated energy was not compared (``not_compared_reason``). Stable tokens.
NOT_COMPARED_STORED_ENERGY_NOT_STATED = "stored_energy_not_stated"
NOT_COMPARED_ZPE_NOT_STATED = "zpe_not_stated"
NOT_COMPARED_NO_ELECTRONIC_ENERGY_TO_PAIR = "no_electronic_energy_to_pair"
NOT_COMPARED_GEOMETRY_NOT_PAIRED = "geometry_not_paired"
NOT_COMPARED_ZPE_SCALING_UNSTATED = "zpe_scaling_unstated"

#: A stated ``zpe_scale_factor`` is expected to carry at least four decimals (or be exactly the
#: multiplier used), so its rounding error is at most half a unit of the fourth place, 5e-5. A
#: factor stated to fewer decimals can exceed this and is then refused.
_ZPE_SCALE_FACTOR_PRINTED_ERROR = 5e-5

#: Two printed values are in every comparison of a stated electronic energy with
#: a stored one (the stated value and the stored one), three in an E0 (E0,
#: electronic energy, zero-point energy). The weighting is the shared one
#: :func:`composite_arithmetic_tolerance_hartree` documents.
_ROUNDED_QUANTITIES_ELECTRONIC = 2
_ROUNDED_QUANTITIES_E0 = 3

#: Slack for float noise in ``|stated - stored| <= tolerance``; the same value
#: ``tckdb_schemas.composite_total`` uses for the same comparison.
_TOLERANCE_FLOAT_SLACK = 1e-12

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


@dataclass(frozen=True)
class StoredEnergyComparison:
    """What holding one stated energy against the stored one concluded.

    A disagreement has no value here: it raises instead, so a row is only ever
    written with ``agrees`` or ``not_compared``.
    """

    status: str
    reason: str | None = None


_AGREES = StoredEnergyComparison(COMPARISON_AGREES)


def _stored_electronic_energy(calculation: Calculation) -> float | None:
    """The electronic energy stored for an ``sp`` or ``opt``; None when not stated."""

    if calculation.type == CalculationType.sp:
        result = calculation.sp_result
        return None if result is None else result.electronic_energy_hartree
    if calculation.type == CalculationType.opt:
        opt_result = calculation.opt_result
        return None if opt_result is None else opt_result.final_energy_hartree
    return None


def _energy_geometry_ids(session: Session, calculation: Calculation) -> frozenset[int]:
    """The geometries a calculation's energy (or ZPE) is at.

    An ``sp`` and a ``freq`` are at their input geometry; an ``opt``'s energy
    is at its final output geometry.
    """

    if calculation.type == CalculationType.opt:
        rows = session.scalars(
            select(CalculationOutputGeometry.geometry_id).where(
                CalculationOutputGeometry.calculation_id == calculation.id,
                CalculationOutputGeometry.role == CalculationGeometryRole.final,
            )
        ).all()
    else:
        rows = session.scalars(
            select(CalculationInputGeometry.geometry_id).where(
                CalculationInputGeometry.calculation_id == calculation.id
            )
        ).all()
    return frozenset(rows)


def _stated_energy_mismatch(
    *,
    field: str,
    participant: str,
    energy_kind: str,
    stated: float,
    stored: float,
    tolerance: float,
    stored_zpe: float | None = None,
    zpe_scale_factor: float | None = None,
) -> CodedValueError:
    """The refusal for a stated energy the cited calculation does not store.

    Names the participant and both values and never a row id (DR-0028).
    """

    what = (
        "the stored electronic energy plus the stored zero-point energy of the "
        "frequency calculation it cites, scaled by the stated zpe_scale_factor "
        f"{zpe_scale_factor!r}"
        if energy_kind == "e0" and zpe_scale_factor is not None
        else "the stored electronic energy plus the stored zero-point energy of the "
        "frequency calculation it cites"
        if energy_kind == "e0"
        else "the energy stored for the calculation it cites"
    )
    context: dict[str, object] = {
        "field": field,
        "participant": participant,
        "energy_kind": energy_kind,
        "stated_hartree": stated,
        "stored_hartree": stored,
        "tolerance_hartree": tolerance,
    }
    if stored_zpe is not None:
        context["stored_zpe_hartree"] = stored_zpe
    if zpe_scale_factor is not None:
        context["zpe_scale_factor"] = zpe_scale_factor
    return CodedValueError(
        E_TS_ENERGY_ORDERING_STATED_ENERGY_MISMATCH,
        (
            f"{field} states {stated!r} Eh as the '{energy_kind}' energy of '{participant}', "
            f"but {what} is {stored!r} Eh (difference {abs(stated - stored):.3e}, "
            f"tolerance {tolerance:.2e}). State the stored energy or cite the right calculation."
        ),
        context=context,
    )


def _compare_stated_energies_with_stored(
    session: Session,
    record: TransitionStateValidationEvidenceIn,
    energy_calculation_ids: Sequence[int],
    *,
    field_path: str,
    record_index: int,
) -> list[StoredEnergyComparison]:
    """Hold each stated energy against what TCKDB stores for its calculation.

    Runs after the source-type rule, so every ``electronic`` source is an
    ``sp`` or ``opt`` and every ``e0`` source a ``freq``.

    * ``electronic``: the stated value against the cited ``sp``'s
      ``electronic_energy_hartree`` or the cited ``opt``'s final energy.
    * ``e0``: a ``freq`` result stores a zero-point energy and no electronic
      energy, so the E0 is compared with the stored electronic energy of the
      *same participant's* ``electronic`` entry in this record plus the cited
      ``freq``'s ZPE, and only when the two calculations are at one geometry
      (the ``sp``'s input, the ``opt``'s final, the ``freq``'s input), each
      declared exactly once. Anything less is not a pairing, and is not guessed.
      TCKDB stores the producer's unscaled ZPE. With the energy's
      ``zpe_scale_factor`` stated the sum is ``electronic + s * zpe`` and a
      disagreement raises; with none stated it is ``electronic + zpe`` and a
      disagreement is returned as ``not_compared`` / ``zpe_scaling_unstated``,
      because a scaled ZPE cannot be told from a wrong number and only a
      stated convention makes a contradiction provable.

    A stored value that contradicts the stated one raises
    ``ts_energy_ordering_stated_energy_mismatch``. A comparison that cannot be
    made (a stored energy or ZPE that is not stated, no electronic entry to
    pair an E0 with, a pairing that cannot be established) is returned as
    ``not_compared`` with its reason, never as agreement.
    """

    energies = list(record.energies or [])
    calculations = [session.get(Calculation, calculation_id) for calculation_id in energy_calculation_ids]
    results: list[StoredEnergyComparison | None] = [None] * len(energies)
    electronic_by_participant: dict[str, tuple[Calculation, float | None]] = {}

    for index, (energy, calculation) in enumerate(zip(energies, calculations, strict=True)):
        assert calculation is not None  # resolved and ownership-checked above
        if energy.energy_kind != "electronic":
            continue
        stored = _stored_electronic_energy(calculation)
        electronic_by_participant[energy.participant] = (calculation, stored)
        if stored is None:
            results[index] = StoredEnergyComparison(
                COMPARISON_NOT_COMPARED, NOT_COMPARED_STORED_ENERGY_NOT_STATED
            )
            continue
        tolerance = composite_arithmetic_tolerance_hartree(_ROUNDED_QUANTITIES_ELECTRONIC)
        if abs(energy.energy_hartree - stored) > tolerance + _TOLERANCE_FLOAT_SLACK:
            raise _stated_energy_mismatch(
                field=f"{field_path}[{record_index}].energies[{index}]",
                participant=energy.participant,
                energy_kind="electronic",
                stated=energy.energy_hartree,
                stored=stored,
                tolerance=tolerance,
            )
        results[index] = _AGREES

    for index, (energy, calculation) in enumerate(zip(energies, calculations, strict=True)):
        assert calculation is not None
        if energy.energy_kind != "e0":
            continue
        freq = calculation.freq_result
        zpe = None if freq is None else freq.zpe_hartree
        if zpe is None:
            results[index] = StoredEnergyComparison(COMPARISON_NOT_COMPARED, NOT_COMPARED_ZPE_NOT_STATED)
            continue
        partner = electronic_by_participant.get(energy.participant)
        if partner is None:
            results[index] = StoredEnergyComparison(
                COMPARISON_NOT_COMPARED, NOT_COMPARED_NO_ELECTRONIC_ENERGY_TO_PAIR
            )
            continue
        partner_calculation, electronic = partner
        if electronic is None:
            results[index] = StoredEnergyComparison(
                COMPARISON_NOT_COMPARED, NOT_COMPARED_STORED_ENERGY_NOT_STATED
            )
            continue
        electronic_geometry = _energy_geometry_ids(session, partner_calculation)
        freq_geometry = _energy_geometry_ids(session, calculation)
        if len(electronic_geometry) != 1 or electronic_geometry != freq_geometry:
            results[index] = StoredEnergyComparison(
                COMPARISON_NOT_COMPARED, NOT_COMPARED_GEOMETRY_NOT_PAIRED
            )
            continue
        scale = energy.zpe_scale_factor
        if scale is not None:
            # The producer states how it scaled the zero-point energy, so the sum is known and a
            # disagreement is a contradiction. Rounded quantities: the E0, the electronic energy,
            # the ZPE (weight ``scale``), and the factor itself, stated to at least four decimals
            # (at most half a unit, 5e-5, of relative error), which moves the sum by ``5e-5 * zpe``.
            stored_e0 = electronic + scale * zpe
            rounded = (
                _ROUNDED_QUANTITIES_E0 - 1
                + scale
                + abs(zpe) * _ZPE_SCALE_FACTOR_PRINTED_ERROR / COMPOSITE_PRINTED_ROUNDING_HARTREE
            )
            tolerance = composite_arithmetic_tolerance_hartree(rounded)
            if abs(energy.energy_hartree - stored_e0) > tolerance + _TOLERANCE_FLOAT_SLACK:
                raise _stated_energy_mismatch(
                    field=f"{field_path}[{record_index}].energies[{index}]",
                    participant=energy.participant,
                    energy_kind="e0",
                    stated=energy.energy_hartree,
                    stored=stored_e0,
                    tolerance=tolerance,
                    stored_zpe=zpe,
                    zpe_scale_factor=scale,
                )
            results[index] = _AGREES
            continue
        # No factor stated: the sum is electronic + ZPE as stored. TCKDB stores the producer's
        # unscaled ZPE, so an E0 built with a scaled one cannot be told from a wrong number here;
        # that is not a contradiction it can prove, so it is recorded, never refused.
        stored_e0 = electronic + zpe
        tolerance = composite_arithmetic_tolerance_hartree(_ROUNDED_QUANTITIES_E0)
        if abs(energy.energy_hartree - stored_e0) > tolerance + _TOLERANCE_FLOAT_SLACK:
            results[index] = StoredEnergyComparison(
                COMPARISON_NOT_COMPARED, NOT_COMPARED_ZPE_SCALING_UNSTATED
            )
            continue
        results[index] = _AGREES

    return [result for result in results if result is not None]


def _warn_energies_not_compared(
    record: TransitionStateValidationEvidenceIn,
    comparisons: Sequence[StoredEnergyComparison],
    *,
    subject_label: str,
    field_path: str,
    record_index: int,
    warnings: list[UploadWarning] | None,
) -> None:
    """One warning per record naming each stated energy that was not compared."""

    if warnings is None:
        return
    skipped = [
        f"'{energy.participant}' {energy.energy_kind} ({comparison.reason})"
        for energy, comparison in zip(record.energies or [], comparisons, strict=True)
        if comparison.status == COMPARISON_NOT_COMPARED
    ]
    if not skipped:
        return
    scaling_hint = (
        " An E0 that is not the stored electronic energy plus the stored (unscaled) zero-point "
        "energy was not refused, because a scaled zero-point energy cannot be told from a wrong "
        "number; if you scaled it, state zpe_scale_factor on that energy and it will be checked."
        if any(c.reason == NOT_COMPARED_ZPE_SCALING_UNSTATED for c in comparisons)
        else ""
    )
    warnings.append(
        UploadWarning(
            field=f"{field_path}[{record_index}].energies",
            code=W_TS_ENERGY_ORDERING_NOT_COMPARED,
            message=(
                f"Transition state '{subject_label}' energy_ordering states energies that could "
                f"not be compared with the energies TCKDB stores for the calculations they cite: "
                f"{'; '.join(skipped)}. They are stored as not compared, and the ordering rests on "
                f"the stated numbers for them.{scaling_hint}"
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
        # Magnitudes: ``imag_freq_cm1`` has no sign rule on the payload, and
        # the house reads it as a magnitude (``stationary_point`` takes its
        # ``abs``), so a result stored as +1500 agrees with a record of -1500.
        and abs(abs(value) - abs(freq.imag_freq_cm1))
        > _IMAGINARY_FREQUENCY_TOLERANCE_CM1
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
    resolved_comparisons: list[list[StoredEnergyComparison]] = []
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
            comparisons = _compare_stated_energies_with_stored(
                session,
                record,
                energy_ids,
                field_path=field_path,
                record_index=record_index,
            )
            _warn_energies_not_compared(
                record,
                comparisons,
                subject_label=subject_label,
                field_path=field_path,
                record_index=record_index,
                warnings=warnings,
            )
        else:
            comparisons = []
        resolved_energy_ids.append(energy_ids)
        resolved_comparisons.append(comparisons)

    rows: list[TransitionStateValidationEvidence] = []
    for record, calculation_id, energy_ids, comparisons in zip(
        evidence,
        reconstruction_calculation_ids,
        resolved_energy_ids,
        resolved_comparisons,
        strict=True,
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
        for energy, source_calculation_id, comparison in zip(
            record.energies or [], energy_ids, comparisons, strict=True
        ):
            row.compared_energies.append(
                TransitionStateValidationEnergy(
                    participant=energy.participant,
                    energy_kind=energy.energy_kind,
                    energy_hartree=energy.energy_hartree,
                    source_calculation_id=source_calculation_id,
                    stored_energy_comparison=comparison.status,
                    not_compared_reason=comparison.reason,
                    zpe_scale_factor=energy.zpe_scale_factor,
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
    sort_key=10,
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


CHECK_TS_ENERGY_ORDERING_STATED_MISMATCH = ScientificCheck(
    group="Stationary points",
    sort_key=11,
    code=(E_TS_ENERGY_ORDERING_STATED_ENERGY_MISMATCH, E_NETWORK_STATE_ENERGY_SUM_MISMATCH),
    asserts=(
        "A stated energy should be the energy TCKDB stores for the "
        "calculation(s) it cites. An energy-ordering energy is the single "
        "point's (or optimisation's) energy, or for an E0 the paired "
        "electronic energy plus the cited frequency's ZPE, scaled by the "
        "stated ``zpe_scale_factor``; an E0 with no stated factor is never "
        "refused. A network state energy that cites one calculation per "
        "participant is the stoichiometric sum of their stored energies (on "
        "a zero shared by the solve's states, the difference between states)."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional. The record names a calculation and a number; a number "
        "that is not that calculation's stored one is a factual inconsistency "
        "between two things the same deposit asserts, not an expectation. "
        "Without it the ordering was checked against the depositor's own "
        "numbers only, and a mistyped or copied value could make a record pass "
        "that the stored energies fail. The tolerance is the shared "
        "printed-precision one, so printed rounding is never refused; a "
        "network state energy stated in kJ/mol is further allowed half a "
        "rounding unit (stated, else 1 kcal/mol) plus the spread of "
        "hartree-to-kJ/mol constants, and a value inside that allowance but "
        "beyond printed precision is stored as not compared, never refused. "
        "A comparison that cannot be made is not a contradiction and does not "
        "block (``CHECK_TS_ENERGY_ORDERING_NOT_COMPARED``)."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            persist_transition_state_validation_evidence,
            note=(
                "Runs in the shared evidence seam, so the PDep bundle, the "
                "computed-reaction bundle and the standalone upload enforce it "
                "alike, and it holds for a payload that bypassed the wire "
                "schemas. Wire-level checks cannot do it: the stored energies "
                "are in the database."
            ),
        ),
        PythonCheck(
            compare_state_energy_sums,
            note=(
                "The network route: reads the stored energies off the persisted "
                "calculations, so it holds for a payload that bypassed the wire "
                "schema. Run once per solve, after every state energy is "
                "resolved, because the shared-zero conventions compare states "
                "with each other and blame the outlier by majority."
            ),
        ),
    ),
    escape_hatch=(
        "State the stored energy, or cite the calculation the number came "
        "from. An E0 built with a scaled ZPE states ``zpe_scale_factor``. A "
        "network state energy states unrounded kJ/mol derived from hartree "
        "(x 2625.499639), or ``energy_precision_kj_mol``; sources are optional."
    ),
)


CHECK_TS_ENERGY_ORDERING_NOT_COMPARED = ScientificCheck(
    group="Stationary points",
    sort_key=12,
    code=(W_TS_ENERGY_ORDERING_NOT_COMPARED, W_NETWORK_STATE_ENERGY_SUM_NOT_COMPARED),
    asserts=(
        "Every energy an energy-ordering record or a network state energy "
        "states should be comparable with the energy TCKDB stores for the "
        "calculation(s) it cites."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    tier_rationale=(
        "Absence, not contradiction. A stored energy or zero-point energy that "
        "is not stated, an E0 with no electronic energy to pair, or a pairing "
        "TCKDB cannot establish leaves nothing to contradict; refusing would "
        "lose a record that may be right. The energy is stored as not compared "
        "with its reason, and the warning names it (ADR 0008)."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            persist_transition_state_validation_evidence,
            note=(
                "The outcome of every comparison is stored on the compared "
                "energy (``stored_energy_comparison`` / ``not_compared_reason``), "
                "so a reader can tell an energy that agrees with the stored one "
                "from one that was never held against it."
            ),
        ),
        PythonCheck(
            collect_state_energy_source_warnings,
            note=(
                "The network route: the outcome is stored on the state energy "
                "(``source_sum_comparison`` / ``source_sum_not_compared_reason``). "
                "Reasons include a convention with no stored per-source terms, a "
                "zero no other state shares, a source with no stored energy of "
                "the needed kind, a state alone on its zero and "
                "``stated_precision_unknown`` (beyond printed precision, inside "
                "honest rounding of the stated kJ/mol). Sources on only some "
                "participants warn separately, with "
                "``network_state_energy_sources_partial``."
            ),
        ),
    ),
    escape_hatch=(
        "None needed: the warning is the accommodation. Deposit the cited "
        "energy (and the ZPE, at the electronic energy's geometry) to compare; "
        "for a network state energy, cite every participant with an sp, opt or "
        "composite calculation and state ``energy_precision_kj_mol``."
    ),
)


__all__ = [
    "E_TS_ENERGY_ORDERING_STATED_ENERGY_MISMATCH",
    "W_MISSING_TS_IRC_EVIDENCE",
    "W_TS_ENERGY_ORDERING_MIXED_LEVELS",
    "W_TS_ENERGY_ORDERING_NOT_COMPARED",
    "persist_transition_state_validation_evidence",
]
