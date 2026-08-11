"""Read-time projection of a calculation's imaginary modes.

ADR 0012 asks for two determinations on every imaginary mode -- how much
of it is rigid-body motion, and how much is a rotation about a rotatable
bond -- "because a determination beats a threshold wherever one is
available". ADR 0013 deferred them on the stated grounds that TCKDB holds
no displacement vectors to project. It holds ``calc_hessian``, and
:mod:`app.chemistry.normal_modes` recovers the vectors from it exactly.

This module is the database glue: it loads the Hessian, the geometry it is
bound to and the stored frequency list, hands them to the numerics, and
pairs each determination with the ``imaginary_disposition`` the depositor
**declared** for the same mode.

Three properties are deliberate.

**Nothing is written.** No table, no column, no cache. ADR 0013 observes
that "it is the projection that is the inference, not the vector"; running
the projection at read time and persisting neither of them is what lets a
determination exist without TCKDB storing an inference.

**A determination never replaces a declaration.** Both are returned, side
by side, together with the overlaps and the thresholds that produced the
determination, and a disagreement is reported as a disagreement. TCKDB has
no basis for silently preferring either: the depositor saw the output file
and this module saw a matrix. Under ADR 0008 a projection is an
expectation, not a definition -- it may inform a reader, and it may not
block a record.

**Refusing to answer is a first-class answer.** Every way this can fail to
produce a projection has its own status, because "no residue was found"
and "nothing was checked" are opposite findings that a single empty result
would merge -- the defect
``backend/scripts/ops/verify_artifact_integrity.py`` was built to close.
A calculation with no Hessian reads ``hessian_not_stored``; one whose
geometry cannot be mass-weighted reads ``masses_unresolved``; one whose
rigid-body curvature says the geometry is not the frame the matrix was
computed in reads ``rigid_body_curvature_too_large``. None of them reads
as a clean bill of health.

**And none of them is the end of what TCKDB knows.** ADR 0012 already
judged every one of those records by magnitude, against a tau resolved
from the protocol that produced them, and persisted the tau, the row of
the protocol table it came from and the resulting structural flag on
``calc_freq_result``. Where a projection cannot be taken, that judgement
is returned beside the refusal -- see :class:`TauContext` -- so that a
reader gets "cannot be determined from stored data, and here is what the
magnitude judgement said" rather than an unqualified shrug. It is not
returned where a projection *was* taken, because ADR 0012's own rule is
that a determination beats a threshold wherever one is available, and
stacking the weaker answer next to the stronger one would only invite a
reader to average them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.stationary_point import TAU_PARAMETER_KEYS

from app.chemistry.normal_modes import (
    FRAME_CONSISTENCY_TOLERANCE_CM1,
    RIGID_BODY_OVERLAP_THRESHOLD,
    TORSION_OVERLAP_THRESHOLD,
    ModeDetermination,
    atomic_mass,
    match_stored_frequency,
    perceive_bonds,
    project_mode,
    rigid_body_curvature_cm1,
    rigid_body_subspace,
    rotatable_bonds,
    solve_normal_modes,
    torsion_axes,
    unpack_lower_triangle,
)
from app.db.models.calculation import (
    CalculationFreqMode,
    CalculationFreqResult,
    CalculationHessian,
    CalculationParameter,
)
from app.db.models.common import ImaginaryModeDisposition
from app.db.models.geometry import GeometryAtom

logger = logging.getLogger(__name__)

__all__ = [
    "TAU_INTERPRETATION_LIMIT",
    "DeclarationAgreement",
    "ImaginaryModeProjection",
    "ImaginaryModeProjectionResult",
    "ProjectionStatus",
    "TauContext",
    "TauProtocolParameter",
    "TauRankedMode",
    "build_imaginary_mode_projection",
]

#: Stated on every :class:`TauContext`, because the one inference a
#: reader is most likely to draw from a ranked magnitude list is the one
#: the data cannot support.
#:
#: ADR 0012 §"The definition does not survive the translation to a
#: database row": a transition state can sit at a maximum of a torsional
#: profile while being a perfectly correct reactive bottleneck, so the
#: extra negative eigenvalue is not an artefact but exactly right. A
#: torsion at -300 cm-1 and a genuine second reaction coordinate at
#: -300 cm-1 are the same number in a frequency list -- which is the
#: entire gap the projection exists to close, and the reason the block
#: reports the comparison and stops there.
TAU_INTERPRETATION_LIMIT = (
    "tau is the noise floor of the protocol that produced this record, "
    "not a verdict on what these modes are. Magnitude cannot separate a "
    "spurious mode from a real one that is not the reaction coordinate: "
    "a torsion, a genuine second-order saddle and a flat barrier whose "
    "reaction coordinate is not the largest imaginary mode are "
    "indistinguishable in a frequency list. The ranked magnitudes and "
    "the comparison against tau are reported; the assignment is not."
)


class ProjectionStatus(str, Enum):
    """Why the projection block says what it says.

    Only ``determined`` means a measurement was taken. Every other value
    names a specific reason none was, so that a reader is never invited to
    read silence as an absence of findings.
    """

    #: At least one stored imaginary mode was projected.
    determined = "determined"

    #: The frequency list is present and contains no imaginary mode.
    #: Nothing to project, and nothing wrong.
    no_imaginary_modes = "no_imaginary_modes"

    #: No ``calc_freq_mode`` rows at all. Not a frequency calculation, or
    #: the modes were never parsed.
    no_frequency_modes = "no_frequency_modes"

    #: No ``calc_hessian`` row. **Not determinable here** -- this is the
    #: 36% of frequency calculations that carry no matrix, and it must
    #: never be confused with "no residue found".
    hessian_not_stored = "hessian_not_stored"

    #: The geometry the Hessian is bound to has no atom rows, or its atom
    #: count disagrees with the matrix dimension.
    geometry_incomplete = "geometry_incomplete"

    #: An element symbol or isotope the periodic table does not know, so
    #: the matrix cannot be mass-weighted. Guessing a mass would corrupt
    #: every number downstream of it.
    masses_unresolved = "masses_unresolved"

    #: Translation and rotation are null directions of a Hessian at a
    #: stationary point. A large curvature along one means the geometry on
    #: file is not the orientation the matrix was computed in, or the
    #: geometry is far from stationary. Either way the subspace this
    #: module would project onto is not the matrix's own.
    rigid_body_curvature_too_large = "rigid_body_curvature_too_large"


class DeclarationAgreement(str, Enum):
    """How a determination stands relative to the declared disposition."""

    #: The mode carries no ``imaginary_disposition``. Normal: a single
    #: imaginary mode on a transition state has nothing to disambiguate.
    not_declared = "not_declared"

    #: The determination is the declared kind.
    agrees = "agrees"

    #: The determination positively excludes the declared kind. This is
    #: surfaced, never resolved: TCKDB reports both and prefers neither.
    conflicts = "conflicts"

    #: The two are about different things -- a declared kind the
    #: projections cannot speak to (``ring_pucker``, ``intermolecular``,
    #: ``symmetry_breaking``), or an ``unassigned`` declaration that a
    #: determination adds to without contradicting.
    inconclusive = "inconclusive"


@dataclass(frozen=True)
class ImaginaryModeProjection:
    """One stored imaginary mode, projected.

    :param mode_index: ``calc_freq_mode.mode_index``, 1-based.
    :param frequency_cm1: The stored (negative) frequency.
    :param declared_disposition: What the depositor said this mode is.
    :param recovered_frequency_cm1: The frequency the Hessian gives back.
    :param rigid_body_overlap: Fraction of the mode in rigid-body motion.
    :param torsion_overlap: Largest single-bond torsional fraction.
    :param torsion_subspace_overlap: Fraction in the span of all torsions.
    :param best_torsion_bond: Atom indices of the bond that achieved
        ``torsion_overlap``.
    :param determination: The classification, or ``None`` when this mode
        alone could not be projected.
    :param not_determined_reason: Why, when ``determination`` is ``None``.
    :param agreement: The determination against the declaration.
    """

    mode_index: int
    frequency_cm1: float
    declared_disposition: ImaginaryModeDisposition | None
    recovered_frequency_cm1: float | None
    rigid_body_overlap: float | None
    torsion_overlap: float | None
    torsion_subspace_overlap: float | None
    best_torsion_bond: tuple[int, int] | None
    determination: ModeDetermination | None
    not_determined_reason: str | None
    agreement: DeclarationAgreement


@dataclass(frozen=True)
class TauProtocolParameter:
    """One ``calculation_parameter`` row tau is resolved from.

    All of :data:`~tckdb_schemas.stationary_point.TAU_PARAMETER_KEYS` are
    reported, in that fixed order, whether or not the record carries
    them. An absent key is the reason a record takes a looser row of the
    protocol table, so listing only the present ones would hide the
    thing most worth seeing.

    :param canonical_key: The canonical parameter key.
    :param canonical_value: What the record says, or ``None`` when the
        parameter was never recorded for this calculation.
    """

    canonical_key: str
    canonical_value: str | None


@dataclass(frozen=True)
class TauRankedMode:
    """One stored imaginary mode, ranked by magnitude against tau.

    :param mode_index: ``calc_freq_mode.mode_index``, 1-based.
    :param frequency_cm1: The stored (negative) frequency.
    :param magnitude_cm1: Its absolute value -- the quantity ADR 0012
        judges, and the quantity this list is ordered by.
    :param is_designated_reaction_coordinate: Whether the depositor
        designated *this* mode the reaction coordinate. False on every
        mode when nothing was designated.
    :param declared_disposition: What the depositor said this mode is,
        for the modes that are not the reaction coordinate.
    :param at_or_above_tau: ``magnitude_cm1 >= tau``, or ``None`` when no
        tau is stored. A bare comparison of two persisted numbers, and
        deliberately not a classification: see
        :data:`TAU_INTERPRETATION_LIMIT`.
    """

    mode_index: int
    frequency_cm1: float
    magnitude_cm1: float
    is_designated_reaction_coordinate: bool
    declared_disposition: ImaginaryModeDisposition | None
    at_or_above_tau: bool | None


@dataclass(frozen=True)
class TauContext:
    """What ADR 0012's magnitude judgement said, for a record no
    projection could be taken on.

    Every field here is **read back**, not recomputed. ADR 0012 §"What
    implementation changed" is explicit that tau had to be stored rather
    than re-resolved, "because a later parser improvement would silently
    re-decide every historical record"; re-resolving it at read time to
    fill this block would be the exact defect that requirement exists to
    prevent. So ``tau_cm1``, ``tau_basis``, ``structural_flag`` and
    ``reaction_coordinate_mode_index`` come off ``calc_freq_result`` as
    the upload wrote them.

    ``protocol_parameters`` is the one place a reader can see the
    persisted parse the basis refers to. It is shown *beside* the stored
    basis rather than used to re-derive it, so if a parser has since
    learned to read a key that was absent when this record was judged,
    the reader sees the discrepancy instead of TCKDB quietly resolving
    it in either direction.

    :param tau_cm1: The tolerance applied to this record, in cm-1.
        ``None`` on a record deposited before ADR 0012 shipped.
    :param tau_basis: Which row of ADR 0012's protocol table was
        matched, as stored. Free text on the wire because the column is
        free text: a value this deployment has not been taught about is
        shown rather than rejected.
    :param structural_flag: ADR 0012's persisted flag -- ``True`` when an
        extra imaginary mode at or above tau excluded this record from
        default transition-state consumption, ``False`` when the record
        was judged and not flagged, ``None`` when it was never judged
        under ADR 0012 (a minimum, a pre-ADR deposit, or a saddle with a
        single imaginary mode and so nothing to disambiguate).
    :param reaction_coordinate_mode_index: The designated reaction
        coordinate, or ``None`` when none was designated.
    :param modes: Every stored imaginary mode, ordered by descending
        magnitude.
    :param protocol_parameters: The recorded provenance tau keys on, in
        :data:`~tckdb_schemas.stationary_point.TAU_PARAMETER_KEYS` order.
    :param interpretation_limit: :data:`TAU_INTERPRETATION_LIMIT`.
    """

    tau_cm1: float | None
    tau_basis: str | None
    structural_flag: bool | None
    reaction_coordinate_mode_index: int | None
    modes: tuple[TauRankedMode, ...] = ()
    protocol_parameters: tuple[TauProtocolParameter, ...] = ()
    interpretation_limit: str = TAU_INTERPRETATION_LIMIT


@dataclass(frozen=True)
class ImaginaryModeProjectionResult:
    """The whole block for one calculation.

    :param status: Whether a measurement was taken, and if not why not.
    :param modes: One entry per stored imaginary mode; empty unless
        ``status`` is ``determined``.
    :param natoms: Atom count of the Hessian, when one was loaded.
    :param rigid_body_dimension: 5 for a linear geometry, 6 otherwise.
    :param is_linear: Whether the geometry is collinear.
    :param max_rigid_body_curvature_cm1: Largest curvature along a
        rigid-body direction, the frame/stationarity self-check.
    :param rotatable_bonds: Perceived rotatable bonds, as atom-index pairs.
    :param rigid_body_overlap_threshold: Threshold applied, echoed so a
        reader can re-decide from the overlaps without re-running anything.
    :param torsion_overlap_threshold: Likewise.
    :param tau_context: ADR 0012's stored magnitude judgement, present
        only when ``status`` is one of the not-determinable values *and*
        the record has imaginary modes to judge. ``None`` on a
        ``determined`` block on purpose: the determination is the
        stronger answer and ADR 0012 says so.
    """

    status: ProjectionStatus
    modes: tuple[ImaginaryModeProjection, ...] = ()
    natoms: int | None = None
    rigid_body_dimension: int | None = None
    is_linear: bool | None = None
    max_rigid_body_curvature_cm1: float | None = None
    rotatable_bonds: tuple[tuple[int, int], ...] = ()
    rigid_body_overlap_threshold: float = RIGID_BODY_OVERLAP_THRESHOLD
    torsion_overlap_threshold: float = TORSION_OVERLAP_THRESHOLD
    tau_context: TauContext | None = None

    @property
    def conflict_count(self) -> int:
        return sum(1 for mode in self.modes if mode.agreement is DeclarationAgreement.conflicts)


#: Declared dispositions the projections can positively confirm or exclude.
#: Everything else is outside the measurement's vocabulary and can only
#: come back ``inconclusive`` -- claiming otherwise would let a projection
#: that cannot see ring puckering pronounce on one.
_DECIDABLE_DECLARATIONS: dict[ImaginaryModeDisposition, ModeDetermination] = {
    ImaginaryModeDisposition.rigid_body_residue: ModeDetermination.rigid_body_residue,
    ImaginaryModeDisposition.torsion: ModeDetermination.torsion,
}


def _agreement(
    declared: ImaginaryModeDisposition | None,
    determined: ModeDetermination | None,
) -> DeclarationAgreement:
    """Compare a declaration with a determination without preferring either."""

    if declared is None:
        return DeclarationAgreement.not_declared
    if determined is None:
        return DeclarationAgreement.inconclusive
    expected = _DECIDABLE_DECLARATIONS.get(declared)
    if expected is not None:
        return DeclarationAgreement.agrees if determined is expected else DeclarationAgreement.conflicts
    # The declaration names a kind the projections cannot see. They can
    # still contradict it, but only by positively identifying one of the
    # two kinds they can: a mode that is 99% rigid-body rotation is not a
    # ring pucker, whatever the deposit says.
    if determined in _DECIDABLE_DECLARATIONS.values():
        if declared is ImaginaryModeDisposition.unassigned:
            # "I looked and could not classify it" is not contradicted by
            # a classification; it is answered by one.
            return DeclarationAgreement.inconclusive
        return DeclarationAgreement.conflicts
    return DeclarationAgreement.inconclusive


def _tau_context(
    session: Session,
    calculation_id: int,
    imaginary: list[CalculationFreqMode],
) -> TauContext:
    """Read back ADR 0012's magnitude judgement for one calculation.

    Called only where a projection could not be taken. Nothing here is
    derived from provenance: the tau, its basis and the flag are the
    values persisted at upload, and the only arithmetic performed is
    comparing each stored magnitude against the stored tau.

    :param session: Open read session.
    :param calculation_id: ``calculation.id``.
    :param imaginary: The stored imaginary modes, in ``mode_index``
        order.
    :returns: The judgement, ranked by magnitude.
    """

    freq_result = session.get(CalculationFreqResult, calculation_id)
    tau_cm1 = freq_result.imaginary_mode_tau_cm1 if freq_result is not None else None
    designated = freq_result.reaction_coordinate_mode_index if freq_result is not None else None

    recorded: dict[str, str | None] = {}
    for key, value in session.execute(
        select(CalculationParameter.canonical_key, CalculationParameter.canonical_value)
        .where(CalculationParameter.calculation_id == calculation_id)
        .where(CalculationParameter.canonical_key.in_(TAU_PARAMETER_KEYS))
    ).all():
        # Later rows win, matching ``resolve_tau_from_parameters``.
        recorded[key] = value

    ranked = sorted(imaginary, key=lambda mode: (-abs(mode.frequency_cm1), mode.mode_index))
    return TauContext(
        tau_cm1=tau_cm1,
        tau_basis=freq_result.imaginary_mode_tau_basis if freq_result is not None else None,
        structural_flag=(freq_result.imaginary_mode_structural_flag if freq_result is not None else None),
        reaction_coordinate_mode_index=designated,
        modes=tuple(
            TauRankedMode(
                mode_index=mode.mode_index,
                frequency_cm1=mode.frequency_cm1,
                magnitude_cm1=abs(mode.frequency_cm1),
                is_designated_reaction_coordinate=(designated is not None and mode.mode_index == designated),
                declared_disposition=mode.imaginary_disposition,
                at_or_above_tau=(None if tau_cm1 is None else abs(mode.frequency_cm1) >= tau_cm1),
            )
            for mode in ranked
        ),
        protocol_parameters=tuple(
            TauProtocolParameter(canonical_key=key, canonical_value=recorded.get(key))
            for key in TAU_PARAMETER_KEYS
        ),
    )


def build_imaginary_mode_projection(session: Session, calculation_id: int) -> ImaginaryModeProjectionResult:
    """Project the imaginary modes of one calculation, computing nothing else.

    :param session: Open read session.
    :param calculation_id: ``calculation.id``.
    :returns: The block, whose ``status`` says whether anything was
        measured and, when nothing was, exactly what stopped it.
    """

    stored_modes = list(
        session.scalars(
            select(CalculationFreqMode)
            .where(CalculationFreqMode.calculation_id == calculation_id)
            .order_by(CalculationFreqMode.mode_index)
        ).all()
    )
    if not stored_modes:
        return ImaginaryModeProjectionResult(status=ProjectionStatus.no_frequency_modes)
    imaginary = [mode for mode in stored_modes if mode.is_imaginary]
    if not imaginary:
        return ImaginaryModeProjectionResult(status=ProjectionStatus.no_imaginary_modes)

    # From here on every exit is a refusal to determine, and each one
    # carries ADR 0012's stored magnitude judgement so that the refusal
    # is qualified rather than bare. Built once and reused: it is two
    # reads and no arithmetic worth repeating.
    tau_context = _tau_context(session, calculation_id, imaginary)

    hessian = session.scalar(select(CalculationHessian).where(CalculationHessian.calculation_id == calculation_id))
    if hessian is None:
        return ImaginaryModeProjectionResult(
            status=ProjectionStatus.hessian_not_stored,
            tau_context=tau_context,
        )

    atoms = list(
        session.scalars(
            select(GeometryAtom)
            .where(GeometryAtom.geometry_id == hessian.geometry_id)
            .order_by(GeometryAtom.atom_index)
        ).all()
    )
    if not atoms or len(atoms) != hessian.natoms:
        return ImaginaryModeProjectionResult(
            status=ProjectionStatus.geometry_incomplete,
            natoms=hessian.natoms,
            tau_context=tau_context,
        )

    masses: list[float] = []
    for atom in atoms:
        mass = atomic_mass(atom.element, atom.isotope_mass_number)
        if mass is None or mass <= 0.0:
            return ImaginaryModeProjectionResult(
                status=ProjectionStatus.masses_unresolved,
                natoms=hessian.natoms,
                tau_context=tau_context,
            )
        masses.append(mass)

    elements = [atom.element.strip() for atom in atoms]
    coordinates = np.array([[atom.x, atom.y, atom.z] for atom in atoms], dtype=float)

    try:
        matrix = unpack_lower_triangle(hessian.lower_triangle_hartree_bohr2, hessian.natoms)
        rigid_body = rigid_body_subspace(coordinates, masses)
        curvatures = rigid_body_curvature_cm1(matrix, masses, rigid_body)
    except ValueError:
        logger.warning(
            "imaginary-mode projection: unusable Hessian or geometry for calculation row %s",
            calculation_id,
        )
        return ImaginaryModeProjectionResult(
            status=ProjectionStatus.geometry_incomplete,
            natoms=hessian.natoms,
            tau_context=tau_context,
        )

    max_curvature = max(curvatures, key=abs) if curvatures else 0.0
    if abs(max_curvature) > FRAME_CONSISTENCY_TOLERANCE_CM1:
        return ImaginaryModeProjectionResult(
            status=ProjectionStatus.rigid_body_curvature_too_large,
            natoms=hessian.natoms,
            rigid_body_dimension=rigid_body.dimension,
            is_linear=rigid_body.is_linear,
            max_rigid_body_curvature_cm1=max_curvature,
            tau_context=tau_context,
        )

    bonds = perceive_bonds(elements, coordinates)
    rotatable = rotatable_bonds(hessian.natoms, bonds)
    axes = torsion_axes(coordinates, masses, bonds, rotatable, rigid_body)
    recovered = solve_normal_modes(matrix, masses)

    projections: list[ImaginaryModeProjection] = []
    for mode in imaginary:
        match = match_stored_frequency(mode.frequency_cm1, recovered)
        if not match.matched:
            reason = "degenerate_eigenvector" if match.degenerate else "not_matched_in_recovered_spectrum"
            projections.append(
                ImaginaryModeProjection(
                    mode_index=mode.mode_index,
                    frequency_cm1=mode.frequency_cm1,
                    declared_disposition=mode.imaginary_disposition,
                    recovered_frequency_cm1=match.recovered_frequency_cm1,
                    rigid_body_overlap=None,
                    torsion_overlap=None,
                    torsion_subspace_overlap=None,
                    best_torsion_bond=None,
                    determination=None,
                    not_determined_reason=reason,
                    agreement=_agreement(mode.imaginary_disposition, None),
                )
            )
            continue

        assert match.mode is not None  # narrowed by ``matched``
        projected = project_mode(match.mode, rigid_body, axes)
        projections.append(
            ImaginaryModeProjection(
                mode_index=mode.mode_index,
                frequency_cm1=mode.frequency_cm1,
                declared_disposition=mode.imaginary_disposition,
                recovered_frequency_cm1=match.recovered_frequency_cm1,
                rigid_body_overlap=projected.rigid_body_overlap,
                torsion_overlap=projected.torsion_overlap,
                torsion_subspace_overlap=projected.torsion_subspace_overlap,
                best_torsion_bond=projected.best_torsion_bond,
                determination=projected.determination,
                not_determined_reason=None,
                agreement=_agreement(mode.imaginary_disposition, projected.determination),
            )
        )

    return ImaginaryModeProjectionResult(
        status=ProjectionStatus.determined,
        modes=tuple(projections),
        natoms=hessian.natoms,
        rigid_body_dimension=rigid_body.dimension,
        is_linear=rigid_body.is_linear,
        max_rigid_body_curvature_cm1=max_curvature,
        rotatable_bonds=rotatable,
    )
