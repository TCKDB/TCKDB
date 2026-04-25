"""Validate calculation output geometry against species identity.

Policy (species-entry optimizations):
- Not graph-isomorphic → fail (hard gate)
- Isomorphic + RMSD above threshold → warning (advisory)
- Otherwise → pass

Graph isomorphism is the identity criterion.
RMSD is a suspicion signal, not an identity criterion.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.chemistry.torsion_fingerprint import kabsch_rmsd, resolve_atom_mapping
from app.db.models.common import ValidationStatus

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
