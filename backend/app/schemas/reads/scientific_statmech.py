"""Read schemas for the scientific statmech read surface.

Covers:

- ``GET /api/v1/scientific/statmech/{statmech_ref_or_id}``
- ``GET/POST /api/v1/scientific/statmech/search``

Statmech is attached at the **species_entry** level (direct FK), not
at the conformer level — so the read surface exposes a species
context block and surfaces conformer groups belonging to the same
species_entry under ``include=conformers`` as a lightweight context
hint, not a hard-coded link.

Frequencies are not stored on ``statmech`` rows — they live on
``calc_freq_result`` of the source freq calculation. The
``include=frequencies`` token therefore surfaces the source freq
calculation summary; full mode arrays remain available behind the
calculation detail endpoint.

See ``backend/docs/specs/scientific_statmech_reads.md``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models.common import (
    CalculationType,
    RigidRotorKind,
    ScientificOriginKind,
    StatmechCalculationRole,
    StatmechTreatmentKind,
    TorsionTreatmentKind,
)
from app.schemas.reads.scientific_common import (
    LevelOfTheorySummary,
    LiteratureSummary,
    RecordReviewBadge,
    ReviewStatusSummary,
    SoftwareReleaseSummary,
    WorkflowToolReleaseSummary,
)
from app.services.trust.models import TrustFragment

# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class StatmechDetailRequest(BaseModel):
    """Service-layer request for the statmech detail read."""

    include: list[str] = Field(default_factory=list)


class RequestEcho(BaseModel):
    """Echo of the parsed include list, post-validation and post-policy."""

    include: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Core block
# ---------------------------------------------------------------------------


class StatmechCoreBlock(BaseModel):
    """Direct statmech-row metadata.

    ``frequency_scale_factor_value`` is the resolved numeric scaling
    factor pulled from the linked ``frequency_scale_factor`` row (or
    ``None`` when the statmech row carries no scale factor). The
    full FSF row is surfaced separately via
    :class:`StatmechFrequencyScaleFactorSummary` so callers can see
    the LoT + software + literature provenance behind the scalar.

    ``scientific_origin`` is the producer (computed / experimental /
    estimated). ``statmech_treatment`` / ``rigid_rotor_kind`` /
    ``point_group`` / ``external_symmetry`` / ``is_linear`` /
    ``uses_projected_frequencies`` are the statmech inputs the row
    actually carries.
    """

    statmech_id: int | None = None
    statmech_ref: str
    scientific_origin: ScientificOriginKind
    statmech_treatment: StatmechTreatmentKind | None = None
    rigid_rotor_kind: RigidRotorKind | None = None
    point_group: str | None = None
    external_symmetry: int | None = None
    is_linear: bool | None = None
    uses_projected_frequencies: bool | None = None
    frequency_scale_factor_value: float | None = None
    note: str | None = None
    created_at: datetime
    review: RecordReviewBadge


# ---------------------------------------------------------------------------
# Species context
# ---------------------------------------------------------------------------


class StatmechSpeciesContext(BaseModel):
    """Lightweight species/species-entry pointer for a statmech record.

    Mirrors the species context shape used by the conformer and
    transition-state surfaces. Integer ids are Phase D policy-gated.
    """

    species_id: int | None = None
    species_ref: str
    species_entry_id: int | None = None
    species_entry_ref: str
    canonical_smiles: str | None = None
    inchi_key: str | None = None
    charge: int | None = None
    multiplicity: int | None = None


# ---------------------------------------------------------------------------
# Frequency scale factor + source-calc + torsion + conformer-context summaries
# ---------------------------------------------------------------------------


class StatmechFrequencyScaleFactorSummary(BaseModel):
    """Compact projection of a ``frequency_scale_factor`` row.

    The numeric ``value`` lives on the core block as
    ``frequency_scale_factor_value``; this summary surfaces the LoT /
    software / literature provenance behind the scalar.
    """

    frequency_scale_factor_id: int | None = None
    frequency_scale_factor_ref: str
    value: float
    scale_kind: str
    level_of_theory: LevelOfTheorySummary | None = None
    software: SoftwareReleaseSummary | None = None
    source_literature: LiteratureSummary | None = None


class StatmechSourceCalculationSummary(BaseModel):
    """Compact source-calculation projection for ``include=source_calculations``.

    Surfaces enough provenance for a caller to follow up with
    ``/scientific/calculations/{ref}`` for full detail. The ``role``
    column on ``statmech_source_calculation`` distinguishes
    opt / freq / sp / scan / composite / imported source calcs.
    """

    role: StatmechCalculationRole
    calculation_id: int | None = None
    calculation_ref: str
    calculation_type: CalculationType
    quality: str
    created_at: datetime
    review: RecordReviewBadge
    level_of_theory: LevelOfTheorySummary | None = None
    software_release: SoftwareReleaseSummary | None = None
    workflow_tool_release: WorkflowToolReleaseSummary | None = None


class StatmechTorsionCoordinateSummary(BaseModel):
    """One torsion-coordinate definition row (atom indices)."""

    coordinate_index: int
    atom1_index: int
    atom2_index: int
    atom3_index: int
    atom4_index: int


class StatmechTorsionSummary(BaseModel):
    """One ``statmech_torsion`` row projected for ``include=torsions``.

    Carries the treatment + dimension + symmetry-number scalars, the
    optional source-scan calculation ref, and the inline atom-index
    coordinate list. Per-row rotor-scan barrier scans / energies live
    only behind the source-scan calculation's detail endpoint.
    """

    torsion_index: int
    treatment_kind: TorsionTreatmentKind | None = None
    symmetry_number: int | None = None
    dimension: int
    top_description: str | None = None
    invalidated_reason: str | None = None
    note: str | None = None
    source_scan_calculation_ref: str | None = None
    source_scan_calculation_id: int | None = None
    coordinates: list[StatmechTorsionCoordinateSummary] = Field(
        default_factory=list
    )


class StatmechConformerContextItem(BaseModel):
    """One conformer group reachable via the statmech's species_entry.

    The conformer surface treats basins as
    ``conformer_group ↔ species_entry`` — statmech does not have a
    direct FK to a single basin, so this item is a *context hint*,
    not a hard membership pointer. Callers who need to identify which
    basin a statmech represents should consult curator notes or the
    source-calc graph.
    """

    conformer_group_id: int | None = None
    conformer_group_ref: str
    label: str | None = None


# ---------------------------------------------------------------------------
# Evidence + available sections + review history
# ---------------------------------------------------------------------------


class StatmechEvidenceSummary(BaseModel):
    """Compact statmech-evidence projection.

    Counts come from cheap aggregates over
    ``statmech_source_calculation`` and ``statmech_torsion``.
    ``has_rotor_scans`` is true iff at least one torsion row carries
    a ``source_scan_calculation_id``.
    """

    source_calculation_count: int
    has_opt_calculation: bool
    has_freq_calculation: bool
    has_sp_calculation: bool
    has_rotor_scans: bool
    torsion_count: int
    has_frequency_scale_factor: bool
    has_conformer_context: bool


class AvailableStatmechSections(BaseModel):
    """Boolean map describing which heavy include sections have data."""

    has_source_calculations: bool
    has_torsions: bool
    has_frequencies: bool
    has_conformers: bool
    has_review: bool


class StatmechReviewEntry(BaseModel):
    """One ``record_review`` row projected for ``include=review``."""

    status: str
    reviewed_at: datetime | None = None
    reviewed_by: int | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Frequency summary (include=frequencies)
# ---------------------------------------------------------------------------
#
# Frequencies don't live on ``statmech`` — they live on
# ``calc_freq_result`` of the source freq calculation(s). The
# ``include=frequencies`` token surfaces a pointer to those source
# freq calculations + the resolved scale factor that scales them.
# Full per-mode arrays remain available only behind
# ``GET /scientific/calculations/{ref}`` (the ``include=results``
# heavy include exposes a freq-result summary).


class StatmechFrequenciesSummary(BaseModel):
    """Where to fetch the underlying frequency data for this statmech.

    ``source_freq_calculation_refs`` lists the public refs of every
    freq calculation linked through ``statmech_source_calculation``.
    ``frequency_scale_factor_value`` is the scalar applied on top of
    those raw frequencies (mirrors the core block's field for
    callers consuming only this section). Per-mode arrays remain
    available behind
    ``GET /scientific/calculations/{calculation_ref}?include=results``.
    """

    source_freq_calculation_refs: list[str] = Field(default_factory=list)
    source_freq_calculation_ids: list[int] | None = None
    frequency_scale_factor_value: float | None = None
    note: str | None = None


# ---------------------------------------------------------------------------
# Record + response envelope
# ---------------------------------------------------------------------------


class ScientificStatmechRecord(BaseModel):
    """One statmech projected as a scientific record.

    Default response carries:

    - ``statmech`` — direct-row metadata + review badge.
    - ``species`` — parent species_entry context.
    - ``frequency_scale_factor`` — provenance behind the scalar
      (always present when the row has a scale factor; ``None``
      otherwise — symmetric with how other surfaces expose
      pointer-style summary blocks).
    - ``evidence_summary`` — bounded counts/booleans.
    - ``available_sections`` — boolean map of heavy include presence.
    - ``provenance`` block (LoT not directly on statmech today; the
      summary points at the FSF's LoT instead — documented in the
      spec).

    Optional heavy include blocks populate only when the caller opts
    in.
    """

    statmech: StatmechCoreBlock
    species: StatmechSpeciesContext
    frequency_scale_factor: StatmechFrequencyScaleFactorSummary | None = None
    software_release: SoftwareReleaseSummary | None = None
    workflow_tool_release: WorkflowToolReleaseSummary | None = None
    literature: LiteratureSummary | None = None
    evidence_summary: StatmechEvidenceSummary
    available_sections: AvailableStatmechSections

    # Optional include blocks
    source_calculations: list[StatmechSourceCalculationSummary] | None = None
    torsions: list[StatmechTorsionSummary] | None = None
    frequencies: StatmechFrequenciesSummary | None = None
    conformers: list[StatmechConformerContextItem] | None = None
    review_history: list[StatmechReviewEntry] | None = None
    trust: TrustFragment | None = None


class ScientificStatmechDetailResponse(BaseModel):
    """Response envelope for ``GET /scientific/statmech/{handle}``."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    record: ScientificStatmechRecord


__all__ = [
    "AvailableStatmechSections",
    "RequestEcho",
    "ScientificStatmechDetailResponse",
    "ScientificStatmechRecord",
    "StatmechConformerContextItem",
    "StatmechCoreBlock",
    "StatmechDetailRequest",
    "StatmechEvidenceSummary",
    "StatmechFrequenciesSummary",
    "StatmechFrequencyScaleFactorSummary",
    "StatmechReviewEntry",
    "StatmechSourceCalculationSummary",
    "StatmechSpeciesContext",
    "StatmechTorsionCoordinateSummary",
    "StatmechTorsionSummary",
]
