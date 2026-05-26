"""Read schemas for /api/v1/scientific/calculations/{calculation_ref_or_id}.

Default-shape detail response only — heavy include payloads (results,
dependencies, parameters, constraints, artifacts, geometries,
geometry_validation, scf_stability, scan, irc, path_search) are wired
through include validation but not yet expanded; see
``backend/docs/specs/scientific_calculation_reads.md``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.db.models.common import (
    ArtifactKind,
    CalculationDependencyRole,
    CalculationGeometryRole,
    CalculationQuality,
    CalculationType,
    ConstraintKind,
    CoordinateUnit,
    IRCDirection,
    PathSearchMethod,
    RecordReviewStatus,
    SCFStabilityStatus,
    ScanCoordinateKind,
    SpeciesEntryStateKind,
    StationaryPointKind,
    TransitionStateEntryStatus,
    ValidationStatus,
)
from app.schemas.reads.scientific_common import (
    GeometryValidationStatus,
    LevelOfTheorySummary,
    LiteratureSummary,
    RecordReviewBadge,
    ReviewStatusSummary,
    SCFStabilityStatusValue,
    SoftwareReleaseSummary,
    WorkflowToolReleaseSummary,
)
from app.services.trust.models import TrustFragment


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class CalculationDetailRequest(BaseModel):
    """Service-layer request for the calculation detail read."""

    include: list[str] = Field(default_factory=list)


class RequestEcho(BaseModel):
    """Echo of the parsed include list, post-validation and post-policy."""

    include: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Owner summaries
# ---------------------------------------------------------------------------


class SpeciesEntryOwnerSummary(BaseModel):
    """Compact species/species-entry owner shape for a calculation.

    Mirrors ``SpeciesCalculationsSpeciesContext`` but only carries the
    fields the calculation-detail endpoint reliably has on hand without
    additional heavy joins. ``species_id`` / ``species_entry_id`` are
    stripped by the Phase D internal-ids policy when not allowed.
    """

    species_id: int
    species_ref: str
    species_entry_id: int
    species_entry_ref: str
    canonical_smiles: str
    inchi_key: str
    charge: int
    multiplicity: int
    species_entry_kind: StationaryPointKind
    electronic_state_kind: SpeciesEntryStateKind


class TransitionStateEntryOwnerSummary(BaseModel):
    """Compact TS / TS-entry owner shape for a calculation."""

    transition_state_id: int
    transition_state_ref: str
    transition_state_entry_id: int
    transition_state_entry_ref: str
    label: str | None = None
    charge: int
    multiplicity: int
    status: TransitionStateEntryStatus
    reaction_entry_id: int | None = None
    reaction_entry_ref: str | None = None


class CalculationOwnerSummary(BaseModel):
    """Discriminated owner block.

    Exactly one of ``species_entry`` / ``transition_state_entry`` is
    non-null; ``kind`` mirrors that for cheap client-side branching.
    The schema invariant ``one_owner`` on the calculation table
    guarantees this.
    """

    kind: Literal["species_entry", "transition_state_entry"]
    species_entry: SpeciesEntryOwnerSummary | None = None
    transition_state_entry: TransitionStateEntryOwnerSummary | None = None


# ---------------------------------------------------------------------------
# Calculation core + provenance
# ---------------------------------------------------------------------------


class CalculationCoreBlock(BaseModel):
    """Direct calculation-row metadata.

    Phase B/D: ``calculation_ref`` is the public stable handle alongside
    ``calculation_id`` (the integer id is stripped when the deployment
    forbids exposing internal ids).
    """

    calculation_id: int
    calculation_ref: str
    type: CalculationType
    quality: CalculationQuality
    created_at: datetime
    review: RecordReviewBadge


class CalculationEvidenceProvenanceSummary(BaseModel):
    """Lightweight provenance/evidence summary for the detail endpoint.

    Cheap projection that surfaces:

    - whether the calculation has a primary result row (``has_result``),
    - the matching geometry-validation outcome (or ``not_present``),
    - the matching SCF-stability outcome (or ``not_present``),
    - convergence flag for opt/scan/irc/path-search calculations,
    - optional ``submission_ref``/``submission_id`` for traceability
      back to the submission that created the calculation. The fields
      are always present (possibly null) so callers can detect
      "no submission link" without an extra include.
    """

    has_result: bool
    converged: bool | None = None
    geometry_validation_status: GeometryValidationStatus
    scf_stability_status: SCFStabilityStatusValue
    submission_id: int | None = None
    submission_ref: str | None = None


# ---------------------------------------------------------------------------
# Per-type result summaries (lightweight; no point/mode arrays)
# ---------------------------------------------------------------------------


class CalculationSPResultSummary(BaseModel):
    """Summary projection of a ``calc_sp_result`` row."""

    electronic_energy_hartree: float | None = None
    electronic_energy_uncertainty_hartree: float | None = None


class CalculationOptResultSummary(BaseModel):
    """Summary projection of a ``calc_opt_result`` row."""

    converged: bool | None = None
    n_steps: int | None = None
    final_energy_hartree: float | None = None


class CalculationFreqResultSummary(BaseModel):
    """Summary projection of a ``calc_freq_result`` row.

    Per-mode arrays are intentionally omitted; they belong to a future
    heavier include token.
    """

    n_imag: int | None = None
    imag_freq_cm1: float | None = None
    zpe_hartree: float | None = None
    zpe_uncertainty_hartree: float | None = None


class CalculationScanResultSummary(BaseModel):
    """Summary projection of a ``calc_scan_result`` row.

    Per-coordinate / per-point arrays are deferred to ``include=scan``.
    """

    dimension: int
    is_relaxed: bool | None = None
    zero_energy_reference_hartree: float | None = None
    note: str | None = None


class CalculationIRCResultSummary(BaseModel):
    """Summary projection of a ``calc_irc_result`` row.

    Per-point arrays are deferred to ``include=irc``.
    """

    direction: IRCDirection
    has_forward: bool
    has_reverse: bool
    ts_point_index: int | None = None
    point_count: int | None = None
    zero_energy_reference_hartree: float | None = None
    note: str | None = None


class CalculationPathSearchResultSummary(BaseModel):
    """Summary projection of a ``calc_path_search_result`` row.

    Per-point arrays are deferred to ``include=path_search``.
    """

    method: PathSearchMethod
    is_double_ended: bool | None = None
    converged: bool | None = None
    n_points: int | None = None
    selected_ts_point_index: int | None = None
    climbing_image_index: int | None = None
    source_endpoint_count: int | None = None
    zero_energy_reference_hartree: float | None = None
    note: str | None = None


class CalculationArtifactSummary(BaseModel):
    """One artifact-metadata row for the ``include=artifacts`` heavy
    include of the calculation detail endpoint.

    **Metadata only — no body bytes, no pre-signed download URL.**
    The ``uri`` field carries the storage URI exactly as persisted
    (typically ``s3://bucket/key``); resolving it to a downloadable URL
    is an upload-side / artifact-service responsibility, not a public
    read concern.

    ``artifact_ref`` is always ``None`` until ``calculation_artifact``
    grows a ``public_ref`` column (see open question 1 in
    ``backend/docs/specs/scientific_calculation_reads.md``); the field
    exists in the schema now so adding refs later is non-breaking.

    ``artifact_id`` is subject to the Phase D internal-ID visibility
    policy and is stripped by the strip helper when the deployment
    forbids exposing internal ids.
    """

    artifact_id: int | None = None
    artifact_ref: str | None = None
    kind: ArtifactKind
    uri: str
    filename: str | None = None
    sha256: str | None = None
    bytes: int | None = None
    created_at: datetime | None = None


class CalculationGeometryLinkSummary(BaseModel):
    """One link from a calculation to a geometry, for the
    ``include=input_geometries`` / ``include=output_geometries`` heavy
    includes of the calculation detail endpoint.

    The link carries the geometry's public ref and a tiny amount of
    cheap metadata (``natoms``, ``geom_hash``) so callers can decide
    whether to fetch the full coordinate payload from
    ``/scientific/geometries/{geometry_ref}``. **No XYZ text and no
    per-atom arrays** are inlined here — that's the geometry detail
    endpoint's job.

    ``input_order`` is populated for input links (and ``None`` for
    output links); ``output_order`` and ``role`` are populated for
    output links (and ``None`` for input links). The wrapping field
    name on ``ScientificCalculationRecord``
    (``input_geometries`` vs ``output_geometries``) tells the caller
    which side of the link they're looking at, but the per-link
    direction-specific fields are also self-describing for clients
    that flatten the lists.

    ``geometry_id`` is subject to the Phase D internal-ID visibility
    policy and is stripped by the strip helper when the deployment
    forbids exposing internal ids; ``geometry_ref`` is always present.
    """

    geometry_id: int | None = None
    geometry_ref: str
    input_order: int | None = None
    output_order: int | None = None
    role: CalculationGeometryRole | None = None
    natoms: int | None = None
    geom_hash: str | None = None


class ScanCoordinateSummary(BaseModel):
    """One scan-coordinate row, projection for ``include=scan``.

    Carries the kind, atom indices, and numeric envelope. Per-point
    coordinate values (``calc_scan_point_coordinate_value``) are
    *not* exposed here — those live in the future specialized
    ``/scientific/calculations/{calculation_ref_or_id}/scan`` endpoint.

    ``atom_indices`` is the non-null atom-index slots in arity order
    so flattened consumers can iterate without re-checking the kind.
    """

    coordinate_index: int
    coordinate_kind: ScanCoordinateKind
    atom1_index: int
    atom2_index: int
    atom3_index: int | None = None
    atom4_index: int | None = None
    atom_indices: list[int]
    step_count: int | None = None
    step_size: float | None = None
    start_value: float | None = None
    end_value: float | None = None
    value_unit: CoordinateUnit | None = None
    resolution_degrees: int | None = None
    symmetry_number: int | None = None


class CalculationScanSummary(BaseModel):
    """``include=scan`` summary projection.

    Bounded — no per-point arrays, no per-point geometry refs, no
    coordinate-value rows. Aggregate fields (``coordinate_count``,
    ``point_count``, ``min/max_*_energy``) are computed via cheap SQL
    aggregates so the include is safe to populate on a search page.

    Full trajectory data (each point's coordinate values, geometry
    link, energy) belongs to the future specialized endpoint
    ``GET /scientific/calculations/{calculation_ref_or_id}/scan``.
    """

    dimension: int
    is_relaxed: bool | None = None
    zero_energy_reference_hartree: float | None = None
    note: str | None = None

    coordinate_count: int
    point_count: int

    coordinates: list[ScanCoordinateSummary] = Field(default_factory=list)

    min_electronic_energy_hartree: float | None = None
    max_electronic_energy_hartree: float | None = None
    min_relative_energy_kj_mol: float | None = None
    max_relative_energy_kj_mol: float | None = None


class CalculationPathSearchSummary(BaseModel):
    """``include=path_search`` summary projection.

    Bounded — no per-point arrays, no per-point geometry refs, no
    per-point energy / coordinate arrays. Aggregates come from cheap
    SQL queries on ``calc_path_search_point`` so the include is safe
    to populate on a search page.

    Full trajectory data (each path-search point's energy, path
    coordinate, gradients, geometry link) belongs to the future
    specialized endpoint
    ``GET /scientific/calculations/{calculation_ref_or_id}/path-search``.

    The schema carries TWO independent point-marker flags
    (``is_ts_guess``, ``is_climbing_image``) which the summary
    surfaces as two separate counts. ``ts_guess_count`` is the
    algorithm's own picked TS candidate; ``climbing_image_count`` is
    the NEB climbing-image flag. The two can overlap (NEB usually
    sets both on the climbing image) but are conceptually distinct,
    so the public summary doesn't merge them.
    """

    # Result-row fields (from calc_path_search_result)
    method: PathSearchMethod
    is_double_ended: bool | None = None
    converged: bool | None = None
    n_points: int | None = None
    selected_ts_point_index: int | None = None
    climbing_image_index: int | None = None
    source_endpoint_count: int | None = None
    zero_energy_reference_hartree: float | None = None
    note: str | None = None

    # Aggregates over calc_path_search_point.
    stored_point_count: int
    ts_guess_count: int
    climbing_image_count: int
    min_electronic_energy_hartree: float | None = None
    max_electronic_energy_hartree: float | None = None
    min_relative_energy_kj_mol: float | None = None
    max_relative_energy_kj_mol: float | None = None
    min_path_coordinate: float | None = None
    max_path_coordinate: float | None = None


class CalculationIRCSummary(BaseModel):
    """``include=irc`` summary projection.

    Bounded — no per-point arrays, no per-point geometry refs, no
    reaction-coordinate arrays. Aggregates (``forward_point_count``,
    ``reverse_point_count``, ``ts_point_count``, energy MIN/MAX,
    reaction-coordinate MIN/MAX) come from cheap SQL aggregates so the
    include is safe to populate on a search page.

    Full trajectory data (each IRC point's energy, reaction coordinate,
    geometry link) belongs to the future specialized endpoint
    ``GET /scientific/calculations/{calculation_ref_or_id}/irc``.

    Direction-counting policy: rows with ``direction = forward`` count
    toward ``forward_point_count``; rows with ``direction = reverse``
    count toward ``reverse_point_count``. Rows with ``direction = both``
    or ``direction IS NULL`` (e.g. an ORCA TS-marker row) are
    intentionally NOT double-counted. ``ts_point_count`` is independent
    and counts every row with ``is_ts = True`` regardless of direction.
    """

    direction: IRCDirection
    has_forward: bool
    has_reverse: bool
    ts_point_index: int | None = None
    point_count: int | None = None
    zero_energy_reference_hartree: float | None = None
    note: str | None = None

    forward_point_count: int
    reverse_point_count: int
    ts_point_count: int

    min_electronic_energy_hartree: float | None = None
    max_electronic_energy_hartree: float | None = None
    min_relative_energy_kj_mol: float | None = None
    max_relative_energy_kj_mol: float | None = None
    min_reaction_coordinate: float | None = None
    max_reaction_coordinate: float | None = None


class CalculationReviewEntry(BaseModel):
    """One ``record_review`` row for the calculation, projected for the
    ``include=review`` heavy include.

    ``record_review`` enforces ``UNIQUE (record_type, record_id)``, so
    the wrapping list on ``ScientificCalculationRecord.review_history``
    holds at most one entry. The list shape is preserved for API
    symmetry with other singleton-list includes
    (``geometry_validation``, ``scf_stability``).

    The compact :class:`RecordReviewBadge` on
    ``CalculationCoreBlock.review`` is not replaced by this include — the
    badge is the always-present trust signal; this entry is the
    expanded curator-context payload (note, submission link, reviewer
    ids when policy permits).

    ``reviewer_ref`` is not surfaced because ``app_user`` carries no
    public ref. ``submission_ref`` is loaded when the review row links
    a submission. All ``*_id`` fields are subject to the Phase D
    internal-ID visibility policy.
    """

    status: RecordReviewStatus
    note: str | None = None
    reviewed_at: datetime | None = None
    submission_ref: str | None = None
    review_id: int | None = None
    reviewer_id: int | None = None
    submission_id: int | None = None


class CalculationConstraintSummary(BaseModel):
    """One ``calculation_constraint`` row, projected for the
    ``include=constraints`` heavy include of the calculation detail
    endpoint.

    Carries the constraint kind plus the four atom-index slots
    (``atom1..4_index``) declared by the ORM, with arity enforced by
    the schema's CHECK constraint. The convenience ``atom_indices``
    list is the non-null indices in arity order so flattened consumers
    can iterate without re-checking the kind.

    ``calculation_id`` is subject to the Phase D internal-ID visibility
    policy and is stripped when the deployment forbids exposing
    internal ids. ``constraint_index`` is scientific/order metadata
    (not a DB-internal surrogate) and stays visible.

    No unit column exists on ``calculation_constraint`` today; one is
    not invented here.
    """

    calculation_id: int | None = None
    constraint_index: int
    constraint_kind: ConstraintKind
    atom1_index: int
    atom2_index: int | None = None
    atom3_index: int | None = None
    atom4_index: int | None = None
    atom_indices: list[int]
    target_value: float | None = None


class CalculationParameterSummary(BaseModel):
    """One parsed ``calculation_parameter`` row, projected for the
    ``include=parameters`` heavy include.

    Carries both the raw (software-specific) and canonical (normalized)
    key/value pairs plus the small bag of parser metadata that's
    public-safe (``section``, ``value_type``, ``unit``,
    ``parameter_index``). The full vocab row (descriptions, classification
    flags) is intentionally not inlined; callers that need it can fetch
    the vocab via a separate read.

    ``parameter_id`` is subject to the Phase D internal-ID visibility
    policy and is stripped by the strip helper when the deployment
    forbids exposing internal ids.
    """

    parameter_id: int | None = None
    raw_key: str
    raw_value: str
    canonical_key: str | None = None
    canonical_value: str | None = None
    section: str | None = None
    value_type: str | None = None
    unit: str | None = None
    parameter_index: int | None = None
    created_at: datetime | None = None


class CalculationGeometryValidationSummary(BaseModel):
    """Geometry-validation evidence for the ``include=geometry_validation``
    heavy include.

    Surfaces the result of the structure-consistency check that
    compares a calculation's output (and optionally input) geometry
    against the declared species identity. The full ``atom_mapping``
    JSONB column is **not** exposed in this MVP — atom indices in a
    public scientific surface are a separate review.

    The schema constrains at most one validation row per calculation
    (PK = ``calculation_id``); the wrapping field on
    ``ScientificCalculationRecord`` is still a list for API symmetry
    with other heavy includes — it will contain zero or one entry.

    ``input_geometry_id`` / ``output_geometry_id`` are subject to the
    Phase D internal-ID visibility policy. Refs are always present
    when the underlying geometry exists.
    """

    input_geometry_id: int | None = None
    input_geometry_ref: str | None = None
    output_geometry_id: int | None = None
    output_geometry_ref: str | None = None
    species_smiles: str
    is_isomorphic: bool
    rmsd: float | None = None
    n_mappings: int | None = None
    validation_status: ValidationStatus
    validation_reason: str | None = None
    rmsd_warning_threshold: float | None = None
    created_at: datetime | None = None


class CalculationSCFStabilitySummary(BaseModel):
    """SCF wavefunction stability evidence for the
    ``include=scf_stability`` heavy include.

    A row exists only when an SCF stability analysis was actually
    performed; absence is interpreted as ``not_checked`` by the
    cheap provenance summary on the default record (``available_sections.has_scf_stability``).

    The schema constrains at most one row per calculation
    (PK = ``calculation_id``); the wrapping field is still a list
    for API symmetry — it will contain zero or one entry.

    ``source_artifact_ref`` is always ``None`` because
    ``calculation_artifact`` has no ``public_ref`` column today (see
    ``include=artifacts``); ``source_calculation_ref`` resolves via
    the calculation's own ``public_ref`` when the link is present.
    Internal IDs follow the Phase D visibility policy.
    """

    status: SCFStabilityStatus
    lowest_eigenvalue: float | None = None
    instability_count: int | None = None
    instability_type: str | None = None
    reoptimized_wavefunction: bool | None = None
    note: str | None = None
    created_at: datetime | None = None
    source_calculation_id: int | None = None
    source_calculation_ref: str | None = None
    source_artifact_id: int | None = None
    source_artifact_ref: str | None = None


class CalculationWavefunctionDiagnosticSummary(BaseModel):
    """Parsed wavefunction-diagnostic evidence for the
    ``include=wavefunction_diagnostic`` heavy include.

    A row exists only when at least one diagnostic was actually parsed;
    absence reads as "not parsed / not applicable / not reported" via
    ``available_sections.has_wavefunction_diagnostic``.

    The schema constrains at most one row per calculation
    (PK = ``calculation_id``); the wrapping field is still a list for
    API symmetry — it will contain zero or one entry.

    Thresholds for interpreting T1/D1 (multireference heuristics) are
    deliberately NOT enforced or labelled by the schema — readers and
    curators apply heuristics on top.
    """

    t1_diagnostic: float | None = None
    d1_diagnostic: float | None = None
    t1_norm: float | None = None
    largest_t2_amplitude: float | None = None
    note: str | None = None
    created_at: datetime | None = None


class CalculationDependencySummary(BaseModel):
    """One edge in the calculation-dependency graph, projected for the
    ``include=dependencies`` heavy include of the calculation detail
    endpoint.

    ``direction`` is **relative to the requested calculation**:

    - ``"parent"`` → the requested calculation is the parent in this edge
      (``parent_calculation_ref == requested calc.public_ref``); the
      ``child_calculation_*`` fields point at the calculation that
      depends on it.
    - ``"child"`` → the requested calculation is the child in this edge
      (``child_calculation_ref == requested calc.public_ref``); the
      ``parent_calculation_*`` fields point at the calculation it
      depends on.

    Integer ids are subject to the Phase D internal-ID visibility
    policy and are stripped by the strip helper when the deployment
    forbids exposing them. Refs are always present.
    """

    role: CalculationDependencyRole
    direction: Literal["parent", "child"]
    parent_calculation_ref: str
    child_calculation_ref: str
    parent_calculation_id: int | None = None
    child_calculation_id: int | None = None


class CalculationResultSummary(BaseModel):
    """Wrapper carrying exactly one populated per-type result block.

    ``kind`` mirrors which sub-block is populated for cheap client-side
    branching. Sub-blocks for the other types are ``None``. The result
    summary intentionally omits internal integer ids and heavy point /
    mode arrays — those belong to ``include=scan``, ``include=irc``,
    ``include=path_search`` (per the spec).
    """

    kind: Literal[
        "sp", "opt", "freq", "scan", "irc", "path_search"
    ]
    sp: CalculationSPResultSummary | None = None
    opt: CalculationOptResultSummary | None = None
    freq: CalculationFreqResultSummary | None = None
    scan: CalculationScanResultSummary | None = None
    irc: CalculationIRCResultSummary | None = None
    path_search: CalculationPathSearchResultSummary | None = None


class AvailableCalculationSections(BaseModel):
    """Boolean map describing which heavy include sections have data.

    Computed from cheap EXISTS-style queries in the service layer so
    callers can avoid issuing follow-up requests for empty sections.
    All fields are always present; values reflect what an
    ``include=<token>`` would expand to.
    """

    has_results: bool
    has_dependencies: bool
    has_parameters: bool
    has_constraints: bool
    has_artifacts: bool
    has_input_geometries: bool
    has_output_geometries: bool
    has_geometry_validation: bool
    has_scf_stability: bool
    has_wavefunction_diagnostic: bool
    has_scan: bool
    has_irc: bool
    has_path_search: bool


# ---------------------------------------------------------------------------
# Top-level record + response envelope
# ---------------------------------------------------------------------------


class ScientificCalculationRecord(BaseModel):
    """One calculation projected as a scientific/provenance record.

    ``results`` is populated only when the caller supplies
    ``include=results`` *and* the calculation has a primary result row
    for its ``calculation_type``. When ``include=results`` is requested
    but no result row exists, ``results`` is explicitly ``null`` (the
    field is present so the caller can distinguish "asked but missing"
    from "did not ask"). When ``include=results`` is not requested, the
    field is omitted from the response payload entirely.
    """

    calculation: CalculationCoreBlock
    owner: CalculationOwnerSummary
    level_of_theory: LevelOfTheorySummary | None = None
    software_release: SoftwareReleaseSummary | None = None
    workflow_tool_release: WorkflowToolReleaseSummary | None = None
    literature: LiteratureSummary | None = None
    provenance: CalculationEvidenceProvenanceSummary
    available_sections: AvailableCalculationSections
    results: CalculationResultSummary | None = None
    dependencies: list[CalculationDependencySummary] | None = None
    artifacts: list[CalculationArtifactSummary] | None = None
    input_geometries: list[CalculationGeometryLinkSummary] | None = None
    output_geometries: list[CalculationGeometryLinkSummary] | None = None
    geometry_validation: list[CalculationGeometryValidationSummary] | None = None
    scf_stability: list[CalculationSCFStabilitySummary] | None = None
    wavefunction_diagnostic: (
        list[CalculationWavefunctionDiagnosticSummary] | None
    ) = None
    parameters: list[CalculationParameterSummary] | None = None
    constraints: list[CalculationConstraintSummary] | None = None
    review_history: list[CalculationReviewEntry] | None = None
    scan: CalculationScanSummary | None = None
    irc: CalculationIRCSummary | None = None
    path_search: CalculationPathSearchSummary | None = None
    trust: TrustFragment | None = None


class ScientificCalculationDetailResponse(BaseModel):
    """Response envelope for /api/v1/scientific/calculations/{handle}."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    record: ScientificCalculationRecord


__all__ = [
    "AvailableCalculationSections",
    "CalculationArtifactSummary",
    "CalculationConstraintSummary",
    "CalculationCoreBlock",
    "CalculationDependencySummary",
    "CalculationDetailRequest",
    "CalculationEvidenceProvenanceSummary",
    "CalculationFreqResultSummary",
    "CalculationGeometryLinkSummary",
    "CalculationGeometryValidationSummary",
    "CalculationIRCSummary",
    "CalculationIRCResultSummary",
    "CalculationOptResultSummary",
    "CalculationOwnerSummary",
    "CalculationParameterSummary",
    "CalculationPathSearchResultSummary",
    "CalculationPathSearchSummary",
    "CalculationResultSummary",
    "CalculationReviewEntry",
    "CalculationSCFStabilitySummary",
    "CalculationSPResultSummary",
    "CalculationWavefunctionDiagnosticSummary",
    "CalculationScanResultSummary",
    "CalculationScanSummary",
    "RequestEcho",
    "ScanCoordinateSummary",
    "ScientificCalculationDetailResponse",
    "ScientificCalculationRecord",
    "SpeciesEntryOwnerSummary",
    "TransitionStateEntryOwnerSummary",
]
