"""Validate calculation output geometry against species identity.

This is a *structure-consistency* check: does the output geometry still
represent the molecule we claimed it does? It is intended to catch
optimizations that rearranged the molecule, broke/formed bonds,
dissociated, transferred a proton, or otherwise drifted to a different
chemical identity.

Not in scope here:

- SCF / wavefunction stability — that lives in ``calc_scf_stability``
  (see :class:`app.db.models.calculation.CalculationSCFStability`) and
  asks an electronic-structure question, not a geometry question.
- Frequency / stationary-point validation — number of imaginary modes,
  Hessian character, etc. — lives on the frequency result surfaces.

Policy (species-entry optimizations):
- Not graph-isomorphic → fail (hard gate)
- Isomorphic + RMSD above threshold → warning (advisory)
- Otherwise → pass

Graph isomorphism is the identity criterion.
RMSD is a suspicion signal, not an identity criterion.

Two layers:

* :func:`validate_calculation_geometry` is the pure chemistry seam — it
  takes parsed atom tuples and a SMILES, returns a result dataclass, and
  does not touch the DB.
* :func:`run_and_persist_geometry_validation` is the workflow seam — it
  inspects a persisted ``Calculation`` row's linked input/output
  geometries, calls the pure layer, and persists a
  ``CalculationGeometryValidation`` row. It is best-effort: if required
  data is missing or the chemistry layer raises, no row is written and
  the upload continues.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chemistry.geometry import parse_xyz
from app.chemistry.torsion_fingerprint import kabsch_rmsd, resolve_atom_mapping
from app.db.models.calculation import (
    Calculation,
    CalculationGeometryValidation,
    CalculationInputGeometry,
    CalculationOutputGeometry,
)
from app.db.models.common import (
    CalculationGeometryRole,
    CalculationType,
    ValidationStatus,
)
from app.db.models.geometry import Geometry
from app.schemas.fragments.geometry import GeometryPayload

logger = logging.getLogger(__name__)

DEFAULT_RMSD_WARNING_THRESHOLD = 1.0  # Angstrom


@dataclass
class GeometryValidationResult:
    """Result of geometry validation — not yet persisted."""

    species_smiles: str
    is_isomorphic: bool
    rmsd: float | None
    atom_mapping: dict[int, int] | None
    n_mappings: int | None
    validation_status: ValidationStatus
    validation_reason: str | None
    rmsd_warning_threshold: float | None
    input_geometry_id: int | None = None
    output_geometry_id: int | None = None


def validate_calculation_geometry(
    *,
    output_atoms: tuple[tuple[str, float, float, float], ...],
    species_smiles: str,
    input_atoms: tuple[tuple[str, float, float, float], ...] | None = None,
    input_geometry_id: int | None = None,
    output_geometry_id: int | None = None,
    rmsd_warning_threshold: float = DEFAULT_RMSD_WARNING_THRESHOLD,
) -> GeometryValidationResult:
    """Validate an output geometry against the claimed species identity.

    :param output_atoms: Parsed atoms (symbol, x, y, z) from the output geometry.
    :param species_smiles: Canonical SMILES for the species.
    :param input_atoms: Optional input geometry atoms for RMSD comparison.
    :param input_geometry_id: DB id of input geometry (for recording).
    :param output_geometry_id: DB id of output geometry (for recording).
    :param rmsd_warning_threshold: RMSD above this triggers warning.
    :returns: GeometryValidationResult (caller persists).
    """
    # --- Step 1: Graph isomorphism check on output geometry ---
    output_mapping = resolve_atom_mapping(species_smiles, output_atoms)

    if output_mapping.status in ("no_match", "error"):
        return GeometryValidationResult(
            species_smiles=species_smiles,
            is_isomorphic=False,
            rmsd=None,
            atom_mapping=None,
            n_mappings=output_mapping.n_mappings,
            validation_status=ValidationStatus.fail,
            validation_reason=f"Output geometry is not graph-isomorphic to species "
            f"SMILES (mapping status: {output_mapping.status})",
            rmsd_warning_threshold=rmsd_warning_threshold,
            input_geometry_id=input_geometry_id,
            output_geometry_id=output_geometry_id,
        )

    # --- Step 2: Isomorphic — compute RMSD if input geometry available ---
    rmsd_value: float | None = None

    if input_atoms is not None and output_mapping.mapped_coords is not None:
        input_mapping = resolve_atom_mapping(species_smiles, input_atoms)
        if (
            input_mapping.status not in ("no_match", "error")
            and input_mapping.mapped_coords is not None
        ):
            rmsd_value = kabsch_rmsd(
                input_mapping.mapped_coords,
                output_mapping.mapped_coords,
            )

    # --- Step 3: Apply policy ---
    status, reason = _decide_status(rmsd_value, rmsd_warning_threshold)

    return GeometryValidationResult(
        species_smiles=species_smiles,
        is_isomorphic=True,
        rmsd=rmsd_value,
        atom_mapping=output_mapping.mapping,
        n_mappings=output_mapping.n_mappings,
        validation_status=status,
        validation_reason=reason,
        rmsd_warning_threshold=rmsd_warning_threshold,
        input_geometry_id=input_geometry_id,
        output_geometry_id=output_geometry_id,
    )


def _decide_status(
    rmsd: float | None,
    threshold: float,
) -> tuple[ValidationStatus, str | None]:
    """Apply the validation policy.

    Policy:
    - not isomorphic → fail     (handled by caller before reaching here)
    - isomorphic, no RMSD       → pass
    - isomorphic, RMSD > thresh → warning
    - isomorphic, RMSD ≤ thresh → pass
    """
    if rmsd is None:
        return ValidationStatus.passed, None

    if rmsd > threshold:
        return (
            ValidationStatus.warning,
            f"Large RMSD ({rmsd:.3f} A) between input and output geometry "
            f"exceeds threshold ({threshold:.1f} A); conformer collapse or "
            f"poor starting geometry likely",
        )

    return ValidationStatus.passed, None


# ---------------------------------------------------------------------------
# Workflow-layer wiring
# ---------------------------------------------------------------------------


def _select_output_geometry(
    session: Session, calculation_id: int
) -> CalculationOutputGeometry | None:
    """Pick the geometry row to validate against for an opt calculation.

    Prefers the explicit ``final`` role (always emitted by the opt
    fallback path and the canonical role a producer would declare for
    the converged geometry). Falls back to the lowest ``output_order``
    when no row carries the ``final`` role.
    """
    rows = list(
        session.scalars(
            select(CalculationOutputGeometry)
            .where(CalculationOutputGeometry.calculation_id == calculation_id)
            .order_by(CalculationOutputGeometry.output_order)
        )
    )
    if not rows:
        return None
    for row in rows:
        if row.role == CalculationGeometryRole.final:
            return row
    return rows[0]


def _select_input_geometry(
    session: Session, calculation_id: int
) -> CalculationInputGeometry | None:
    """Pick the geometry row to use as the pre-opt reference.

    Returns the lowest-``input_order`` row, or ``None`` if no input
    geometry was attached. Opt calcs do not auto-attach an input
    geometry (the conformer geometry is the *output* of opt), so this
    is commonly absent and the caller must tolerate ``None``.
    """
    return session.scalars(
        select(CalculationInputGeometry)
        .where(CalculationInputGeometry.calculation_id == calculation_id)
        .order_by(CalculationInputGeometry.input_order)
        .limit(1)
    ).first()


def _atoms_from_geometry(
    geometry: Geometry,
) -> tuple[tuple[str, float, float, float], ...] | None:
    """Parse a Geometry row's stored xyz_text into atom tuples.

    Returns ``None`` when the row carries no xyz_text or the text fails
    to parse — the caller should skip validation rather than abort.
    """
    if geometry is None or not geometry.xyz_text:
        return None
    try:
        return parse_xyz(GeometryPayload(xyz_text=geometry.xyz_text)).atoms
    except ValueError:
        return None


def run_and_persist_geometry_validation(
    session: Session,
    calculation: Calculation,
    *,
    species_smiles: str | None,
    rmsd_warning_threshold: float = DEFAULT_RMSD_WARNING_THRESHOLD,
) -> CalculationGeometryValidation | None:
    """Run geometry-identity validation for an opt calc and persist the row.

    Phase-1 wiring: **species-owned opt calcs only**. This is best-effort
    by policy — if any required input is missing (no ``species_smiles``,
    no output geometry attached, unparseable xyz, chemistry layer
    raises) the function returns ``None`` and writes nothing, so the
    upload continues. A failed/warned validation result *is* persisted
    (as evidence); only the inability to *run* validation is silent.

    Geometry validation is recorded as evidence, never used as a hard
    upload gate. A persisted ``fail`` row means "the automated identity
    validator found a mismatch," **not** "the calculation is
    scientifically invalid." Connectivity perception from XYZ can fail
    or be ambiguous for weak complexes, radicals, charged species,
    stretched bonds, loose conformers, proton-transfer geometries, and
    dissociation-like structures, all of which can produce false-positive
    ``fail`` rows even when the calculation is fine. These rows are
    curator-attention signals, not inputs to automatic rejection.

    Transition-state calculations are intentionally **not** validated
    by this seam. A TS does not have a single canonical SMILES — its
    connectivity sits between the reactant and product graphs — so the
    species-graph isomorphism criterion this service uses would
    systematically reject every TS. TS validation requires a separate,
    reaction-aware validator that checks expected forming and breaking
    bonds against the reaction's atom map and ideally the IRC endpoint
    geometries. That is deferred to a later phase. Two layers enforce
    the deferral:

    * ``computed_reaction.persist_computed_reaction_upload`` only calls
      this helper for species-side calcs, never for the TS calc.
    * If a future caller does invoke this helper for a TS calc, it
      must do so without a ``species_smiles`` (the natural shape for
      TS), and the ``species_smiles`` skip-gate below catches that.

    Idempotent: if a validation row already exists for the calculation
    (same transaction or pre-persisted) it is returned unchanged.

    :param session: Active SQLAlchemy session. Caller controls flush.
    :param calculation: A persisted ``Calculation`` row with at least an
        output geometry attached (either flushed or pending in the
        session).
    :param species_smiles: Canonical SMILES for the declared species
        identity. ``None`` means the caller had no species identity to
        assert (e.g. TS calcs, deferred to a later phase) — validation
        skips and returns ``None``.
    :returns: The persisted (pending) row, or ``None`` if skipped.
    """
    if calculation.type != CalculationType.opt:
        return None
    if not species_smiles:
        return None
    if calculation.geometry_validation is not None:
        return calculation.geometry_validation

    output_link = _select_output_geometry(session, calculation.id)
    if output_link is None:
        return None
    output_atoms = _atoms_from_geometry(output_link.geometry)
    if output_atoms is None:
        return None

    input_link = _select_input_geometry(session, calculation.id)
    input_atoms = (
        _atoms_from_geometry(input_link.geometry) if input_link is not None else None
    )

    try:
        result = validate_calculation_geometry(
            output_atoms=output_atoms,
            species_smiles=species_smiles,
            input_atoms=input_atoms,
            input_geometry_id=input_link.geometry_id if input_link else None,
            output_geometry_id=output_link.geometry_id,
            rmsd_warning_threshold=rmsd_warning_threshold,
        )
    except Exception:
        logger.exception(
            "geometry_validation: chemistry layer raised for calculation_id=%s; "
            "skipping persistence",
            calculation.id,
        )
        return None

    row = CalculationGeometryValidation(
        calculation_id=calculation.id,
        input_geometry_id=result.input_geometry_id,
        output_geometry_id=result.output_geometry_id,
        species_smiles=result.species_smiles,
        is_isomorphic=result.is_isomorphic,
        rmsd=result.rmsd,
        atom_mapping=result.atom_mapping,
        n_mappings=result.n_mappings,
        validation_status=result.validation_status,
        validation_reason=result.validation_reason,
        rmsd_warning_threshold=result.rmsd_warning_threshold,
    )
    session.add(row)
    return row
