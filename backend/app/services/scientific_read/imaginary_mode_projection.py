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
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

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
from app.db.models.calculation import CalculationFreqMode, CalculationHessian
from app.db.models.common import ImaginaryModeDisposition
from app.db.models.geometry import GeometryAtom

logger = logging.getLogger(__name__)

__all__ = [
    "DeclarationAgreement",
    "ImaginaryModeProjection",
    "ImaginaryModeProjectionResult",
    "ProjectionStatus",
    "build_imaginary_mode_projection",
]


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

    hessian = session.scalar(select(CalculationHessian).where(CalculationHessian.calculation_id == calculation_id))
    if hessian is None:
        return ImaginaryModeProjectionResult(status=ProjectionStatus.hessian_not_stored)

    atoms = list(
        session.scalars(
            select(GeometryAtom)
            .where(GeometryAtom.geometry_id == hessian.geometry_id)
            .order_by(GeometryAtom.atom_index)
        ).all()
    )
    if not atoms or len(atoms) != hessian.natoms:
        return ImaginaryModeProjectionResult(status=ProjectionStatus.geometry_incomplete, natoms=hessian.natoms)

    masses: list[float] = []
    for atom in atoms:
        mass = atomic_mass(atom.element, atom.isotope_mass_number)
        if mass is None or mass <= 0.0:
            return ImaginaryModeProjectionResult(status=ProjectionStatus.masses_unresolved, natoms=hessian.natoms)
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
        return ImaginaryModeProjectionResult(status=ProjectionStatus.geometry_incomplete, natoms=hessian.natoms)

    max_curvature = max(curvatures, key=abs) if curvatures else 0.0
    if abs(max_curvature) > FRAME_CONSISTENCY_TOLERANCE_CM1:
        return ImaginaryModeProjectionResult(
            status=ProjectionStatus.rigid_body_curvature_too_large,
            natoms=hessian.natoms,
            rigid_body_dimension=rigid_body.dimension,
            is_linear=rigid_body.is_linear,
            max_rigid_body_curvature_cm1=max_curvature,
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
