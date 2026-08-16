"""Compare a calculation's output geometry against its species' formula.

**What this actually checks is the molecular formula, not the molecular
graph.** The field is named ``is_isomorphic`` and the policy below is worded
as graph isomorphism, but the code beneath it does not test that.
:func:`app.chemistry.torsion_fingerprint.resolve_atom_mapping` falls back to
:func:`~app.chemistry.torsion_fingerprint._find_matches_using_smiles_graph`
whenever bond perception from XYZ fails or produces no substructure match,
which is the common case for the radicals, ions and stretched geometries this
service mostly sees — and that fallback rejects a candidate on one condition
only: the per-element atom counts disagree. Anything with the right formula is
reported as isomorphic.

Verified consequences, by direct call:

- Ethanol declared, dimethyl ether deposited — constitutional isomers, both
  C2H6O — **passes**.
- Methane with one hydrogen pulled out to 5 Å, i.e. a dissociated fragment
  pair — **passes**.

So the rearrangement, bond-breaking, dissociation and proton-transfer cases
this module was written to catch are **not** caught. A ``pass`` here means
"the output has the same atoms as the declared species", nothing more, and a
``fail`` here has only ever meant "the atom counts disagree".

The read surfaces therefore publish the verdict as ``formula_matches``
(:class:`app.schemas.reads.scientific_calculation.CalculationGeometryValidationSummary`,
:class:`app.schemas.entities.calculation.CalculationGeometryValidationRead`),
with ``is_isomorphic`` kept beside it as the stored column's name. A JSON key
travels without its docstring, so the name a consumer reads had to stop
claiming a guarantee this code does not make; renaming the column instead
would be a migration against a deployed table and a breaking change to a
published field, for no gain over publishing the true name alongside.

Connectivity validation is not implemented. Implementing it needs a bond
perception step that is trustworthy on exactly the strained, radical and
stretched structures where a rearrangement would matter, which
``rdDetermineBonds`` is not — it fails silently rather than loudly, so a naive
version would trade a visible gap for an invisible one.

Where the formula rule blocks
-----------------------------
Per ADR 0008 §9, where the same fact is checked in more than one tier the
blocking tier owns it and the others cite it. Formula agreement between a
structure and the identity it is deposited under is owned by two blocking
rules that between them cover every geometry:
:func:`app.services.species_resolution.assert_geometry_composition_matches_identity`
for a **conformer** geometry, and
:func:`app.services.calculation_geometry_composition.assert_calculation_geometry_composition`
for every geometry linked to a **calculation** (#143). Both refuse outright.

**Consequence for this service: ``validation_status=fail`` is no longer
reachable through any upload path.** The ``fail`` branch fires when
``is_isomorphic`` is false, and — as the verified consequences above record —
that happens only when the element counts disagree. No calculation can change
an element count, so that verdict was only ever reachable by depositing a
geometry no calculation could have produced, and such a deposit is now refused
before this service runs. What stays live, and is why the row still exists, is
the RMSD signal: a ``warning`` on a converged structure that moved further
than the threshold from its input is a suspicion about a *correct-formula*
deposit, which is exactly the expectation tier this service belongs to. The
note that used to stand here — that refusing an output geometry would wrongly
reject "an optimisation that drifted" — confused connectivity with
composition. A drifted or dissociated optimisation keeps its atoms and passes
the blocking rule, as the methane-at-5-A case above shows.

Not in scope here:

- SCF / wavefunction stability — that lives in ``calc_scf_stability``
  (see :class:`app.db.models.calculation.CalculationSCFStability`) and
  asks an electronic-structure question, not a geometry question.
- Frequency / stationary-point validation — number of imaginary modes,
  Hessian character, etc. — lives on the frequency result surfaces.

Policy (species-entry optimizations):
- Formula disagrees (recorded as ``is_isomorphic=False``) → fail
- Formula agrees + RMSD above threshold → warning (advisory)
- Otherwise → pass

Neither outcome refuses an upload; see
:func:`run_and_persist_geometry_validation`. RMSD is a suspicion signal, not
an identity criterion.

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
from app.scientific_checks import (
    CheckTier,
    CodeChannel,
    PythonCheck,
    ScientificCheck,
)
from app.services.best_effort import isolated_best_effort

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
    # --- Step 1: Identity check on output geometry. Named for graph
    # isomorphism; in practice a formula comparison — see the module
    # docstring. ---
    output_mapping = resolve_atom_mapping(species_smiles, output_atoms)

    if output_mapping.status in ("no_match", "error"):
        return GeometryValidationResult(
            species_smiles=species_smiles,
            is_isomorphic=False,
            rmsd=None,
            atom_mapping=None,
            n_mappings=output_mapping.n_mappings,
            validation_status=ValidationStatus.fail,
            # Says what was actually compared. The sentence this replaced
            # claimed the output was "not graph-isomorphic to species SMILES",
            # which named a test this function has never run: the mapping falls
            # back to per-element counts whenever bond perception from the XYZ
            # fails. A stored reason is read by curators and quoted in reviews,
            # so it may not describe a stronger check than the one that ran.
            validation_reason=f"Output geometry does not match the declared "
            f"species SMILES (mapping status: {output_mapping.status}). The "
            f"comparison is a molecular-formula check, not a connectivity one "
            f"-- see app.services.geometry_validation.",
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
        # "geometry validation", not "geometry_validation". A snake_case
        # token in front of a colon is how this codebase *declares a code*
        # (app.api.error_contract), and this string declares nothing -- it
        # is a log line that reaches no response body. It was the only one
        # of the five bare logger prefixes in the tree shaped like a code,
        # because it was the only one containing an underscore; the other
        # four ("manifest", "readyz", "startup", "status") cannot match the
        # code-position pattern at all. Held by
        # tests/api/test_error_contract_catalogue_gate.py::
        # TestNoLoggerFormatStringSitsInTheCodePosition.
        logger.exception(
            "geometry validation: chemistry layer raised for calculation_id=%s; "
            "skipping persistence",
            calculation.id,
        )
        return None

    def _persist() -> CalculationGeometryValidation:
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

    # The write is isolated for the same reason the ``except`` above exists:
    # this is best-effort by policy and must never abort the upload. Catching
    # the chemistry call alone did not deliver that, because the row was added
    # without a flush — so its INSERT was emitted by whatever flushed next, in
    # practice the route's COMMIT, outside every guard here. That is the
    # 2026-08-05 shape: a verdict *about* a calculation taking the calculation
    # with it. ``isolated_best_effort`` flushes inside a savepoint so an
    # unstorable ``atom_mapping``/``validation_reason`` fails where it can be
    # absorbed.
    return isolated_best_effort(
        session,
        _persist,
        what=f"geometry validation for calculation id={calculation.id}",
    )


CHECK_OPT_GEOMETRY_MATCHES_DECLARED_SPECIES = ScientificCheck(
    group="A structure against its own label",
    sort_key=6,  # Shifted from 5 by #143; see CHECK_SMILES_CHARGE_MATCHES_DECLARED.
    code=None,
    asserts=(
        "An optimisation's output geometry still describes the species it was "
        "declared for — the optimiser handed back the molecule it was given."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.none,
    tier_rationale=(
        "An expectation, and correctly non-blocking. An optimisation that "
        "rearranged, dissociated or transferred a proton is science to record, "
        "not a payload to refuse, and connectivity perception from XYZ is "
        "unreliable for exactly the weak complexes, radicals, ions and "
        "stretched geometries where a genuine rearrangement would matter. The "
        "result is written as an evidence row that grades the record at read "
        "time; it never refuses an upload. Since #143 the tier is also the "
        "only one left to it: the *composition* half of what this row reports "
        "is owned by ``calculation_geometry_composition_mismatch``, which "
        "blocks, so what this row can still say on its own is the RMSD "
        "suspicion — a correct-formula structure that moved further than "
        "expected, which is an expectation by construction."
    ),
    adr="0008, 0002",
    emitted=False,
    enforced_by=(
        PythonCheck(
            validate_calculation_geometry,
            note=(
                "Species-owned ``opt`` calculations only. Transition states "
                "are deliberately excluded, having no canonical SMILES to "
                "compare against. Best-effort by policy: a missing SMILES, a "
                "missing output geometry, unparseable coordinates or a raising "
                "chemistry layer all write nothing and let the upload "
                "continue. A Kabsch RMSD above 1.0 A against the input "
                "geometry is recorded as a separate suspicion signal. Its "
                "``fail`` outcome is no longer reachable through an upload "
                "path: ``is_isomorphic=False`` fires only on an element-count "
                "mismatch, no calculation can change an element count, and "
                "``assert_calculation_geometry_composition`` refuses such a "
                "deposit before this runs. The pure seam keeps the verdict "
                "and is pinned directly by "
                "``tests/workflows/test_geometry_validation_wiring.py``."
            ),
        ),
    ),
    escape_hatch=(
        "The whole check is advisory, so there is nothing to escape. What a "
        "consumer must not do is read a ``fail`` row as 'this calculation is "
        "scientifically invalid'; it means only that the automated identity "
        "validator found a mismatch."
    ),
    divergence=(
        "The stored column is named ``is_isomorphic`` and the surrounding "
        "policy is worded as graph isomorphism, but the code tests the "
        "**molecular formula** only. Atom mapping falls back to a "
        "SMILES-graph matcher whenever bond perception from XYZ fails, which "
        "is the common case for the radicals, ions and stretched geometries "
        "this service mostly sees, and that fallback rejects a candidate on "
        "one condition: the per-element atom counts disagree. Verified by "
        "direct call in the module docstring — ethanol declared with dimethyl "
        "ether deposited passes, and methane with one hydrogen pulled to 5 A "
        "passes. So the rearrangement, bond-breaking, dissociation and "
        "proton-transfer cases the module was written to catch are not "
        "caught. Already self-documented in the module docstring rather than "
        "discovered here; recorded because the field name is what a consumer "
        "sees and it still overstates the guarantee. **Partly closed:** the "
        "read surfaces now publish the same boolean under its true name, "
        "``formula_matches``, and the stored ``validation_reason`` no longer "
        "says 'not graph-isomorphic'. ``is_isomorphic`` is kept beside it "
        "because it is the stored column and a published field — renaming it "
        "is a migration against a deployed table and a breaking API change, "
        "and buys nothing that publishing the true name alongside does not. "
        "What stays open is the check itself: connectivity is still not "
        "tested, and cannot be until there is bond perception trustworthy on "
        "the strained and radical structures where a rearrangement would "
        "matter."
    ),
)
