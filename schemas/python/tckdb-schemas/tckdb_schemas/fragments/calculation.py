from datetime import datetime
from typing import Self

from pydantic import BaseModel, Field, model_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import (
    CalculationGeometryRole,
    CalculationQuality,
    CalculationType,
    ConstraintKind,
    HessianSource,
    IRCDirection,
    PathSearchMethod,
    SCFStabilityStatus,
)
from tckdb_schemas.fragments.calculation_origin import CalculationOriginMetadata
from tckdb_schemas.fragments.geometry import GeometryPayload
from tckdb_schemas.fragments.refs import (
    LevelOfTheoryRef,
    SoftwareReleaseRef,
    WorkflowToolReleaseRef,
)

# ---------------------------------------------------------------------------
# Constraint payload (lives in fragments so calculation upload payloads can
# reuse it without an entities → fragments cycle).
# ---------------------------------------------------------------------------


class CalculationConstraintPayload(BaseModel):
    """Geometric constraint applied to a calculation.

    Mirrors the ``calculation_constraint`` row shape and arity check
    enforced by the database. Generic across opt, TS, scan, IRC, NEB,
    and any other constrained run — for scans the held-fixed
    coordinates land here while the stepped coordinate lives in
    ``calc_scan_coordinate``.

    Arity by ``constraint_kind``:

    * ``cartesian_atom`` — one atom (atom2/3/4 must be null)
    * ``bond`` — two atoms
    * ``angle`` — three atoms
    * ``dihedral`` / ``improper`` — four atoms
    """

    constraint_index: int = Field(ge=1)
    constraint_kind: ConstraintKind
    atom1_index: int = Field(ge=1)
    atom2_index: int | None = Field(default=None, ge=1)
    atom3_index: int | None = Field(default=None, ge=1)
    atom4_index: int | None = Field(default=None, ge=1)
    target_value: float | None = None

    @model_validator(mode="after")
    def validate_arity_and_distinct_atoms(self) -> Self:
        atoms = [self.atom1_index]
        if self.atom2_index is not None:
            atoms.append(self.atom2_index)
        if self.atom3_index is not None:
            atoms.append(self.atom3_index)
        if self.atom4_index is not None:
            atoms.append(self.atom4_index)

        expected = {
            ConstraintKind.cartesian_atom: 1,
            ConstraintKind.bond: 2,
            ConstraintKind.angle: 3,
            ConstraintKind.dihedral: 4,
            ConstraintKind.improper: 4,
        }
        n_expected = expected[self.constraint_kind]
        if len(atoms) != n_expected:
            raise ValueError(
                f"{self.constraint_kind.value} constraint requires "
                f"{n_expected} atom index(es), got {len(atoms)}."
            )
        if len(set(atoms)) != len(atoms):
            raise ValueError("Constraint atom indices must be distinct.")
        return self


class CalculationConstraintCreate(CalculationConstraintPayload, SchemaBase):
    pass


class CalculationPayload(SchemaBase):
    """Reusable upload fragment for calculation provenance.

    :param type: Calculation type.
    :param quality: Curation quality flag.
    :param software_release: Required software release reference.
    :param workflow_tool_release: Optional workflow tool provenance reference.
    :param level_of_theory: Required level-of-theory reference.
    :param literature_id: Optional literature provenance id.
    """

    type: CalculationType
    quality: CalculationQuality = CalculationQuality.raw

    software_release: SoftwareReleaseRef
    workflow_tool_release: WorkflowToolReleaseRef | None = None
    level_of_theory: LevelOfTheoryRef

    literature_id: int | None = None


# ---------------------------------------------------------------------------
# Typed calculation-result payloads (upload-facing, no FK ids)
# ---------------------------------------------------------------------------


class OptResultPayload(SchemaBase):
    """Optional inline result for an optimisation calculation.

    :param converged: Whether the optimisation converged.
    :param n_steps: Number of optimisation steps.
    :param final_energy_hartree: Final electronic energy in hartree.
    """

    converged: bool | None = None
    n_steps: int | None = Field(default=None, ge=0)
    final_energy_hartree: float | None = None


class FrequencyModePayload(BaseModel):
    """One vibrational mode within a frequency calculation result.

    Imaginary modes use a negative ``frequency_cm1`` together with
    ``is_imaginary=True``. Producers that have only positive magnitudes
    must flip the sign before upload; the cross-field validator below
    refuses inconsistent combinations rather than silently normalising,
    so the source of truth stays at the producer boundary.

    :param mode_index: 1-based ordering from the ESS output.
    :param frequency_cm1: Harmonic frequency in cm⁻¹; negative for
        imaginary modes.
    :param is_imaginary: Whether the mode is imaginary. Required and
        consistent with the sign of ``frequency_cm1``.
    :param reduced_mass_amu: Reduced mass in amu, when reported.
    :param force_constant_mdyne_angstrom: Force constant in
        mDyne/Ångström, when reported.
    :param ir_intensity_km_mol: IR intensity in km/mol, when reported.
    :param raman_activity: Raman activity (Å⁴/amu), when reported.
    :param symmetry_label: Irreducible representation label
        (e.g. ``"A1"``, ``"E"``), when reported.
    :param note: Optional free-text annotation.
    """

    mode_index: int = Field(ge=1)
    frequency_cm1: float
    is_imaginary: bool
    reduced_mass_amu: float | None = Field(default=None, gt=0)
    force_constant_mdyne_angstrom: float | None = None
    ir_intensity_km_mol: float | None = Field(default=None, ge=0)
    raman_activity: float | None = None
    symmetry_label: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def validate_sign_matches_is_imaginary(self) -> Self:
        if self.is_imaginary and self.frequency_cm1 >= 0:
            raise ValueError(
                "is_imaginary=True requires frequency_cm1 < 0. Negate the "
                "magnitude at the producer boundary before upload."
            )
        if not self.is_imaginary and self.frequency_cm1 < 0:
            raise ValueError(
                "frequency_cm1 < 0 requires is_imaginary=True."
            )
        return self


class FreqResultPayload(SchemaBase):
    """Optional inline result for a frequency calculation.

    :param n_imag: Number of imaginary frequencies.
    :param imag_freq_cm1: Value of the imaginary frequency in cm⁻¹.
    :param zpe_hartree: Zero-point energy in hartree.
    :param modes: Optional per-mode frequency rows. When supplied,
        ``mode_index`` values must be unique within the payload, and
        the count of imaginary modes must agree with ``n_imag`` if both
        are present.
    """

    n_imag: int | None = None
    imag_freq_cm1: float | None = None
    zpe_hartree: float | None = None
    modes: list[FrequencyModePayload] | None = None

    @model_validator(mode="after")
    def validate_modes_consistency(self) -> Self:
        if self.modes is None:
            return self
        indices = [m.mode_index for m in self.modes]
        if len(set(indices)) != len(indices):
            raise ValueError("mode_index values must be unique within a freq result.")
        if self.n_imag is not None:
            imaginary_count = sum(1 for m in self.modes if m.is_imaginary)
            if imaginary_count != self.n_imag:
                raise ValueError(
                    f"n_imag={self.n_imag} does not match imaginary mode count "
                    f"{imaginary_count} in modes."
                )
        return self


class SPResultPayload(SchemaBase):
    """Optional inline result for a single-point calculation.

    :param electronic_energy_hartree: Electronic energy in hartree.
    """

    electronic_energy_hartree: float | None = None


class SCFStabilityPayload(SchemaBase):
    """Optional inline SCF wavefunction stability evidence.

    Attaches to any calculation type — there is no calc_type restriction.
    Producers must only emit ``status = stable`` when an actual
    SCF/wavefunction stability analysis was observed; ordinary SCF
    convergence does NOT qualify. When unsure whether a stability
    analysis was performed, omit the block — the read API will project
    ``not_checked``. Use ``status = inconclusive`` only when a stability
    analysis was clearly attempted but its result could not be parsed.

    :param status: Persisted status. ``not_checked`` is NOT a valid
        stored value — omit the block to express that.
    :param lowest_eigenvalue: Smallest eigenvalue from the stability
        Hessian (software-specific).
    :param instability_count: Number of distinct instabilities found.
    :param instability_type: Free-text describing the instability class
        (e.g. ``"RHF→UHF"``, ``"internal"``).
    :param reoptimized_wavefunction: Whether a stable wavefunction was
        obtained by stability optimisation / reoptimisation.
    :param source_calculation_id: Optional FK to the calculation whose
        log carries the stability evidence (when separate from the
        owning calculation).
    :param source_artifact_id: Optional FK to a ``calculation_artifact``
        row holding the stability log bytes (e.g. an ``ancillary`` or
        ``output_log`` artifact).
    """

    status: SCFStabilityStatus
    lowest_eigenvalue: float | None = None
    instability_count: int | None = Field(default=None, ge=0)
    instability_type: str | None = None
    reoptimized_wavefunction: bool | None = None
    source_calculation_id: int | None = None
    source_artifact_id: int | None = None
    note: str | None = None

    @model_validator(mode="after")
    def validate_status_consistency(self) -> Self:
        """Cross-field consistency between ``status`` and other fields.

        Mirrors the soft semantic invariants encoded as DB check
        constraints on ``calc_scf_stability``: catching them here gives
        a 422 with a producer-friendly message rather than letting the
        DB raise an opaque IntegrityError.

        Producer contract is intentionally narrow: we do NOT require
        evidence-bearing fields (``lowest_eigenvalue`` /
        ``source_artifact_id``) for ``status = stable`` — that is left
        to the producer documentation.
        """
        if (
            self.status == SCFStabilityStatus.stable
            and self.reoptimized_wavefunction is True
        ):
            raise ValueError(
                "scf_stability.status = 'stable' is inconsistent with "
                "reoptimized_wavefunction = True. A stable wavefunction "
                "did not need to be re-optimised; use 'stabilized' if a "
                "re-optimisation actually occurred."
            )
        if (
            self.status == SCFStabilityStatus.stabilized
            and self.instability_count == 0
        ):
            raise ValueError(
                "scf_stability.status = 'stabilized' implies at least "
                "one instability was found and then resolved; "
                "instability_count = 0 contradicts that. Leave it null "
                "if unknown."
            )
        if (
            self.status == SCFStabilityStatus.unstable
            and self.reoptimized_wavefunction is True
        ):
            raise ValueError(
                "scf_stability.status = 'unstable' records that an "
                "instability remains. Use 'stabilized' if a stable "
                "wavefunction was subsequently obtained."
            )
        return self


class HessianPayload(SchemaBase):
    """Optional inline Cartesian Hessian (second-derivative) matrix.

    The Hessian is the primitive from which harmonic frequencies, normal
    modes, and thermochemistry are derived. It is meaningful only relative
    to a specific atomic configuration, ordering, and orientation, so the
    payload carries its own ``geometry``: the resolution layer dedupes it
    through the content-addressed geometry seam (so it usually coincides
    with the calculation's input geometry with no duplication) and stores
    the resulting ``geometry_id`` as a mandatory binding.

    Only the lower triangle including the diagonal of the symmetric 3N×3N
    matrix is stored, row-major, in fixed units of hartree/bohr². For
    ``N`` atoms that is exactly ``3N(3N+1)/2`` values.

    Attaches to ``freq`` calculations and to ``opt`` calculations run with
    an analytic Hessian (``opt=calcall``-style). See DR-0030.

    :param geometry: The geometry the Hessian was computed at.
    :param lower_triangle_hartree_bohr2: Packed lower triangle (with
        diagonal), row-major, length ``3N(3N+1)/2``, in hartree/bohr².
    :param source: Where the matrix was obtained from.
    :param parser_version: Optional version tag of the parser that
        produced the matrix.
    :param note: Optional free-text annotation.
    """

    geometry: GeometryPayload
    lower_triangle_hartree_bohr2: list[float] = Field(min_length=1)
    source: HessianSource
    parser_version: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def validate_triangle_length(self) -> Self:
        # Standard XYZ: the first line is the integer atom count. If it is
        # malformed we defer to the backend geometry parser for the precise
        # error rather than duplicating its diagnostics here.
        stripped = self.geometry.xyz_text.strip().splitlines()
        if not stripped:
            return self
        try:
            n = int(stripped[0].strip())
        except ValueError:
            return self
        expected = (3 * n) * (3 * n + 1) // 2
        actual = len(self.lower_triangle_hartree_bohr2)
        if actual != expected:
            raise ValueError(
                f"hessian.lower_triangle_hartree_bohr2 has {actual} entries "
                f"but a {n}-atom Hessian lower triangle must have exactly "
                f"{expected} (= 3N(3N+1)/2 for N={n}). Provide the packed "
                f"lower triangle including the diagonal, row-major."
            )
        return self


class WavefunctionDiagnosticPayload(SchemaBase):
    """Optional inline wavefunction diagnostics parsed from a calculation's output.

    Carries scalar coupled-cluster / multireference diagnostics — T1
    (Lee–Taylor), D1 (Janowski), the norm of the T1 amplitude vector,
    and the largest T2 amplitude. Spin-contamination ``<S^2>`` signals
    are intentionally NOT included in this first slice.

    Generic across calculation types that produce electronic-structure
    output (typically ``sp``). The producer contract is to emit a block
    only when at least one diagnostic was actually parsed from the
    calculation; this payload rejects an all-null block with no note.

    :param t1_diagnostic: Coupled-cluster T1 diagnostic (Lee–Taylor).
    :param d1_diagnostic: Janowski D1 diagnostic.
    :param t1_norm: Norm of the T1 amplitude vector, when reported.
    :param largest_t2_amplitude: Largest T2 amplitude magnitude, when
        reported.
    :param note: Optional free-text annotation.
    """

    t1_diagnostic: float | None = Field(default=None, ge=0)
    d1_diagnostic: float | None = Field(default=None, ge=0)
    t1_norm: float | None = Field(default=None, ge=0)
    largest_t2_amplitude: float | None = Field(default=None, ge=0)
    note: str | None = None

    @model_validator(mode="after")
    def validate_has_diagnostic_value(self) -> Self:
        if (
            self.t1_diagnostic is None
            and self.d1_diagnostic is None
            and self.t1_norm is None
            and self.largest_t2_amplitude is None
        ):
            raise ValueError(
                "wavefunction_diagnostic must include at least one of "
                "t1_diagnostic, d1_diagnostic, t1_norm, "
                "largest_t2_amplitude. Omit the block entirely if no "
                "diagnostic was parsed."
            )
        return self


class SpinDiagnosticPayload(SchemaBase):
    """Optional inline spin-contamination ``<S^2>`` evidence parsed from a
    calculation's output.

    The companion block to :class:`WavefunctionDiagnosticPayload`: T1/D1 stay
    there, spin-contamination signals land here. Carries the observed
    ``<S^2>`` and, when the ESS reports them, the ideal ``S(S+1)`` for the
    target spin state and the ``<S^2>`` after annihilation of the first spin
    contaminant.

    Applies to any UNRESTRICTED calculation (not just coupled cluster). The
    producer contract is to emit the block only when ``<S^2>`` was actually
    parsed; ``s_squared`` is required because the row exists precisely because
    that observation is present. Omit the block entirely for restricted /
    closed-shell runs that have no contamination to report.

    :param s_squared: Observed ``<S^2>`` expectation value.
    :param s_squared_expected: Ideal ``S(S+1)`` for the target spin state as
        reported by the ESS, when reported.
    :param s_squared_annihilated: ``<S^2>`` after annihilation of the first
        spin contaminant (e.g. Gaussian's "after annihilation" value), when
        reported.
    :param note: Optional free-text annotation.
    """

    s_squared: float = Field(ge=0)
    s_squared_expected: float | None = Field(default=None, ge=0)
    s_squared_annihilated: float | None = Field(default=None, ge=0)
    note: str | None = None


class IRCPointPayload(SchemaBase):
    """Upload-facing inline payload for one IRC-path sampled point.

    Geometries are accepted inline as ``GeometryPayload`` and resolved/deduped
    via the existing geometry resolution service at persistence time.

    :param point_index: Zero-based index preserving the source step number.
    :param direction: Per-point direction (nullable for the TS marker point).
    :param is_ts: Whether this point marks the transition state.
    :param reaction_coordinate: Reaction-coordinate value at this point.
    :param electronic_energy_hartree: Electronic energy in hartree.
    :param relative_energy_kj_mol: Relative energy vs the zero-energy reference.
    :param max_gradient: Max gradient component at this point.
    :param rms_gradient: RMS gradient at this point.
    :param geometry: Optional inline geometry payload for this point.
    :param note: Optional free-text note.
    """

    point_index: int = Field(ge=0)
    direction: IRCDirection | None = None
    is_ts: bool = False
    reaction_coordinate: float | None = None
    electronic_energy_hartree: float | None = None
    relative_energy_kj_mol: float | None = None
    max_gradient: float | None = None
    rms_gradient: float | None = None
    geometry: GeometryPayload | None = None
    note: str | None = None


class IRCResultPayload(SchemaBase):
    """Upload-facing inline result for an IRC calculation.

    :param direction: Overall run mode (forward / reverse / both).
    :param has_forward: True when at least one forward-branch point is present.
    :param has_reverse: True when at least one reverse-branch point is present.
    :param ts_point_index: Optional index of the point marked as TS.
    :param point_count: Optional total sampled-point count (consistency check).
    :param zero_energy_reference_hartree: Optional energy used as relative zero.
    :param note: Optional free-text note.
    :param points: Sampled IRC-path points attached to the result.
    """

    direction: IRCDirection
    has_forward: bool
    has_reverse: bool
    ts_point_index: int | None = Field(default=None, ge=0)
    point_count: int | None = Field(default=None, ge=0)
    zero_energy_reference_hartree: float | None = None
    note: str | None = None
    points: list[IRCPointPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_points(self) -> Self:
        """Enforce unique indices, TS index consistency, and direction flags."""

        if not self.points:
            return self

        indices = [point.point_index for point in self.points]
        if len(set(indices)) != len(indices):
            raise ValueError("IRC point_index values must be unique.")

        if (
            self.ts_point_index is not None
            and self.ts_point_index not in set(indices)
        ):
            raise ValueError(
                "ts_point_index must match the point_index of one of the provided points."
            )

        has_forward_in_points = any(
            point.direction == IRCDirection.forward for point in self.points
        )
        has_reverse_in_points = any(
            point.direction == IRCDirection.reverse for point in self.points
        )
        if has_forward_in_points and not self.has_forward:
            raise ValueError(
                "has_forward must be true when forward-direction points are provided."
            )
        if has_reverse_in_points and not self.has_reverse:
            raise ValueError(
                "has_reverse must be true when reverse-direction points are provided."
            )
        return self


class PathSearchPointPayload(SchemaBase):
    """Upload-facing inline payload for one path-search point.

    Generalizes NEB images, GSM nodes, and string-method path points.

    :param point_index: Zero-based point index along the path.
    :param electronic_energy_hartree: Electronic energy at this point.
    :param relative_energy_kj_mol: Energy relative to the zero-energy reference.
    :param path_coordinate: Optional path-coordinate value (e.g. cumulative
        path distance for NEB, reaction-coordinate for GSM/string methods).
    :param max_force: Max force component at this point.
    :param rms_force: RMS force at this point.
    :param max_gradient: Max gradient component at this point.
    :param rms_gradient: RMS gradient at this point.
    :param is_ts_guess: Whether this point is the algorithm's TS guess.
    :param is_climbing_image: Whether this image was the climbing image
        (NEB-CI specific; ignored by string-method outputs).
    :param geometry: Optional inline geometry payload for this point.
    :param note: Optional free-text note.
    """

    point_index: int = Field(ge=0)
    electronic_energy_hartree: float | None = None
    relative_energy_kj_mol: float | None = None
    path_coordinate: float | None = None
    max_force: float | None = None
    rms_force: float | None = None
    max_gradient: float | None = None
    rms_gradient: float | None = None
    is_ts_guess: bool = False
    is_climbing_image: bool = False
    geometry: GeometryPayload | None = None
    note: str | None = None


class PathSearchResultPayload(SchemaBase):
    """Upload-facing inline result bundle for a path-search calculation.

    A path-search calculation explores a reaction path between or from
    molecular endpoints to produce a TS guess. The specific algorithm
    (NEB, GSM, growing/freezing string, ...) lives on ``method`` rather
    than as a separate top-level calculation type.

    :param method: The path-search algorithm used.
    :param is_double_ended: Whether the algorithm uses two endpoints
        (NEB, GSM) versus single-ended (growing string, freezing string).
    :param converged: Whether the path search converged.
    :param n_points: Total sampled-point count (consistency check).
    :param selected_ts_point_index: Index of the point selected as the
        TS guess (0-based). Must match a ``points[].point_index``.
    :param climbing_image_index: Optional index of the climbing image in
        NEB-CI runs.
    :param source_endpoint_count: Optional count of endpoint geometries
        consumed by the algorithm (typically 2 for double-ended runs).
    :param zero_energy_reference_hartree: Energy used as relative zero
        for ``relative_energy_kj_mol`` on each point.
    :param note: Optional free-text note.
    :param points: Path samples (images / nodes / path points), unique on
        ``point_index``.
    """

    method: PathSearchMethod
    is_double_ended: bool | None = None
    converged: bool | None = None
    n_points: int | None = Field(default=None, ge=1)
    selected_ts_point_index: int | None = Field(default=None, ge=0)
    climbing_image_index: int | None = Field(default=None, ge=0)
    source_endpoint_count: int | None = Field(default=None, ge=1)
    zero_energy_reference_hartree: float | None = None
    note: str | None = None
    points: list[PathSearchPointPayload] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_points(self) -> Self:
        """Enforce unique ``point_index``, TS-index / climbing-index
        consistency, and ``n_points`` agreement with the point list."""

        indices = [p.point_index for p in self.points]
        if len(set(indices)) != len(indices):
            raise ValueError("Path-search point_index values must be unique.")
        index_set = set(indices)

        if (
            self.selected_ts_point_index is not None
            and self.selected_ts_point_index not in index_set
        ):
            raise ValueError(
                "selected_ts_point_index must match the point_index of one "
                "of the provided points."
            )

        if (
            self.climbing_image_index is not None
            and self.climbing_image_index not in index_set
        ):
            raise ValueError(
                "climbing_image_index must match the point_index of one "
                "of the provided points."
            )

        if self.n_points is not None and self.n_points != len(self.points):
            raise ValueError(
                f"n_points={self.n_points} does not match the number of "
                f"provided points ({len(self.points)})."
            )
        return self


class CalculationParameterObservation(SchemaBase):
    """Upload-facing payload for one parsed execution-control parameter.

    Mirrors the ``calculation_parameter`` row contract: ``raw_key`` and
    ``raw_value`` are the always-present capture surface, every other field
    is optional enrichment. ``canonical_key`` is best-effort — when the
    parser emits a key that is not yet in ``calculation_parameter_vocab``
    the persistence layer demotes it to ``None`` rather than failing the
    upload.

    :param raw_key: Raw, software-specific key as parsed (e.g. ``"calcfc"``).
    :param raw_value: Raw value as parsed.
    :param canonical_key: Optional canonical key for cross-software queries.
    :param canonical_value: Optional normalized value paired with the key.
    :param section: Optional route-line section (``opt``, ``scf``, ...).
    :param value_type: Optional consumer hint (``bool``, ``int``, ...).
    :param unit: Optional unit string when the value carries dimensionality.
    :param parameter_index: Optional ordering for repeated/positional options.
    """

    raw_key: str = Field(min_length=1)
    raw_value: str
    canonical_key: str | None = None
    canonical_value: str | None = None
    section: str | None = None
    value_type: str | None = None
    unit: str | None = None
    parameter_index: int | None = Field(default=None, ge=0)


class OutputGeometryEntry(SchemaBase):
    """One declared output geometry on a calculation upload payload.

    Each entry carries a geometry payload and the role this geometry plays
    as a calculation output. The list position determines the
    ``output_order`` written to ``calculation_output_geometry`` (1-indexed).
    Producers must declare ``role`` explicitly; defaulting to ``final``
    would silently mis-classify scan iterations, IRC path points, and NEB
    images.
    """

    geometry: GeometryPayload
    role: CalculationGeometryRole


class CalculationWithResultsPayload(CalculationPayload):
    """A calculation with optional typed result blocks.

    Extends ``CalculationPayload`` with opt/freq/sp/irc/path_search result
    fields. Validation enforces that only the result type matching the
    calculation type may be provided.

    :param opt_result: Inline optimisation result (type must be ``opt``).
    :param freq_result: Inline frequency result (type must be ``freq``).
    :param sp_result: Inline single-point result (type must be ``sp``).
    :param irc_result: Inline IRC result bundle (type must be ``irc``).
    :param path_search_result: Inline path-search result bundle (type
        must be ``path_search``). Carries NEB, GSM, and other path-based
        TS-search algorithms via ``path_search_result.method``.
    :param parameters: Optional parsed execution-control parameter
        observations. Each becomes one ``calculation_parameter`` row.
    :param parameters_json: Optional JSON snapshot of the parser output
        (debug/traceability only — relational rows are the queryable layer).
    :param parameters_parser_version: Optional version tag of the parser
        that produced the observations.
    :param parameters_extracted_at: Optional timestamp of extraction.
    """

    opt_result: OptResultPayload | None = None
    freq_result: FreqResultPayload | None = None
    sp_result: SPResultPayload | None = None
    irc_result: IRCResultPayload | None = None
    path_search_result: PathSearchResultPayload | None = None

    scf_stability: SCFStabilityPayload | None = None
    wavefunction_diagnostic: WavefunctionDiagnosticPayload | None = None
    spin_diagnostic: SpinDiagnosticPayload | None = None
    hessian: HessianPayload | None = None

    input_geometries: list[GeometryPayload] = Field(
        default_factory=list,
        description=(
            "Geometries this calculation was run on. When empty, the "
            "workflow falls back to the conformer's reference geometry "
            "for calculation types in {freq, sp}; opt skips. List "
            "order maps to input_order = 1, 2, 3, ... in the database."
        ),
    )

    output_geometries: list[OutputGeometryEntry] = Field(
        default_factory=list,
        description=(
            "Geometries this calculation produced or reported. When "
            "empty, the workflow falls back to the conformer's "
            "reference geometry as a single (role=final, output_order=1) "
            "row for calc types in the narrow set {opt}. Freq, sp, "
            "and all other types get zero rows when the producer "
            "leaves this empty. List order maps to output_order = "
            "1, 2, 3, ... in the database."
        ),
    )

    parameters: list[CalculationParameterObservation] | None = None
    parameters_json: dict | None = None
    parameters_parser_version: str | None = None
    parameters_extracted_at: datetime | None = None

    constraints: list[CalculationConstraintCreate] = Field(
        default_factory=list,
        description=(
            "Coordinate constraints held fixed during this calculation. "
            "Generic across opt, freq, sp, irc, path_search, scan, and any "
            "other constrained run — these are input/provenance metadata and "
            "do not require a result block. For scan calculations, frozen "
            "coordinates may be declared here while the stepped coordinate "
            "is declared on the scan_result.coordinates list."
        ),
    )

    @model_validator(mode="after")
    def validate_constraint_indices_unique(self) -> Self:
        indices = [c.constraint_index for c in self.constraints]
        if len(set(indices)) != len(indices):
            raise ValueError(
                "Calculation constraint_index values must be unique within a calculation."
            )
        return self

    @model_validator(mode="after")
    def validate_result_matches_type(self) -> Self:
        """Ensure only the result block matching ``self.type`` is set."""
        allowed = {
            CalculationType.opt: "opt_result",
            CalculationType.freq: "freq_result",
            CalculationType.sp: "sp_result",
            CalculationType.irc: "irc_result",
            CalculationType.path_search: "path_search_result",
        }
        allowed_field = allowed.get(self.type)
        for field_name in (
            "opt_result",
            "freq_result",
            "sp_result",
            "irc_result",
            "path_search_result",
        ):
            value = getattr(self, field_name)
            if value is not None and field_name != allowed_field:
                raise ValueError(
                    f"Result block '{field_name}' is not allowed for "
                    f"calculation type '{self.type.value}'. "
                    f"Expected '{allowed_field}' or no result."
                )
        return self

    @model_validator(mode="after")
    def validate_tckdb_origin_metadata(self) -> Self:
        """If ``parameters_json["tckdb_origin"]`` is present, validate
        its shape against :class:`CalculationOriginMetadata`. Absence is
        allowed and means "executed" by default. See DR-0026 for the
        full convention.
        """
        if not self.parameters_json or not isinstance(self.parameters_json, dict):
            return self
        origin_block = self.parameters_json.get("tckdb_origin")
        if origin_block is None:
            return self
        CalculationOriginMetadata.model_validate(origin_block)
        return self
