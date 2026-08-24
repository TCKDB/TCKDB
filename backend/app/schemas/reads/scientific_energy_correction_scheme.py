"""Read schemas for the scientific energy-correction-scheme surface.

Covers:

- ``GET /api/v1/scientific/energy-correction-schemes/{energy_correction_scheme_ref_or_id}``
- ``GET/POST /api/v1/scientific/energy-correction-schemes/search``

EnergyCorrectionScheme is a content-derived reference table (prefix
``ecs_``). It is not in ``SubmissionRecordType``, so it has no per-row
review history; the envelope still carries an empty ``review_summary``
for shape parity.

See ``backend/docs/specs/scientific_correction_reads.md``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.db.models.common import (
    EnergyCorrectionApplicationRole,
    EnergyCorrectionSchemeKind,
    EnergyUnit,
)
from app.schemas.reads.scientific_common import (
    LevelOfTheorySummary,
    LiteratureSummary,
    ProfiledRequestEcho,
    ReviewStatusSummary,
)

# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class RequestEcho(ProfiledRequestEcho):
    """Echo of the parsed include list, post-validation and post-policy."""

    include: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Core block
# ---------------------------------------------------------------------------


class EnergyCorrectionSchemeCoreBlock(BaseModel):
    """Direct energy_correction_scheme row metadata."""

    energy_correction_scheme_id: int | None = None
    energy_correction_scheme_ref: str
    name: str
    scheme_kind: EnergyCorrectionSchemeKind
    version: str | None = None
    units: EnergyUnit | None = None
    note: str | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Correction terms (include=corrections)
# ---------------------------------------------------------------------------


class EnergyCorrectionTermSummary(BaseModel):
    """One correction parameter row projected for ``include=corrections``.

    Covers all three child tables uniformly:

    - ``atom`` from ``energy_correction_scheme_atom_param``
      (target = element symbol).
    - ``bond`` from ``energy_correction_scheme_bond_param``
      (target = bond key, e.g. ``C-H``).
    - ``component`` from ``energy_correction_scheme_component_param``
      (correction_kind = Melius sub-type, target = composite key).

    Child rows do not get standalone public refs — there is no use
    case for addressing them outside the parent scheme.
    """

    correction_kind: str
    target: str
    value: float
    component_kind: str | None = None


class EnergyCorrectionSchemeUsageSummary(BaseModel):
    """One application of this scheme, and what it came to.

    Sourced from ``applied_energy_correction`` rows whose ``scheme_id``
    matches the scheme. ``record_*`` / ``endpoint`` point at the
    species/reaction/transition-state entry the correction was applied to.

    **The magnitude travels with the pointer.** A scheme's parameters are
    the recipe; ``applied_value`` is what that recipe evaluated to for this
    record. Serving the first without the second leaves a reader able to
    see a 45-entry bond-additivity table and see that it was used, and
    never able to see the number it produced.

    ``applied_value`` and ``applied_value_unit`` are the stored pair,
    reproduced exactly — the unit varies by scheme in real data
    (a Petersson BAC total in kcal/mol, an atom-energy total in hartree),
    so it is carried explicitly rather than assumed from
    ``EnergyCorrectionSchemeCoreBlock.units``. ``applied_value_hartree`` is
    the same quantity converted once, named for its unit, and ``None``
    rather than ``0.0`` for a unit this build cannot convert.

    ``source_calculation_ref`` is **which energy** the correction was
    computed against. Without it the applied value is a number attached to
    a species with no statement of what it is an addend to; with it, the
    reader can fetch that calculation and find the uncorrected energy
    beside it.
    """

    record_type: str
    record_ref: str
    record_id: int | None = None
    endpoint: str

    #: Stripped unless ``include=internal_ids`` and the deployment allows it.
    applied_energy_correction_id: int | None = None

    #: Which term of the energy expression this correction is. Reported,
    #: not interpreted.
    application_role: EnergyCorrectionApplicationRole

    applied_value: float
    applied_value_unit: EnergyUnit
    applied_value_hartree: float | None = None

    temperature_k: float | None = None
    applied_note: str | None = None

    #: The calculation whose energy this correction applies to, when the
    #: depositor recorded one. ``None`` where they did not.
    source_calculation_ref: str | None = None
    source_calculation_endpoint: str | None = None

    #: How many breakdown rows this application has. The breakdown itself
    #: is served on the calculation surface
    #: (``/scientific/calculations/{ref}?include=energy_corrections``),
    #: reached via ``source_calculation_ref``: ``used_by`` is a usage index
    #: of up to 50 entries and a per-bond table on each would make an index
    #: into a payload.
    component_count: int


class EnergyCorrectionSchemeEvidenceSummary(BaseModel):
    """Bounded evidence projection for an ECS row."""

    atom_param_count: int
    bond_param_count: int
    component_param_count: int
    has_corrections: bool
    applied_usage_count: int
    has_applied_usage: bool
    has_literature_source: bool


class AvailableEnergyCorrectionSchemeSections(BaseModel):
    """Boolean map describing which heavy include sections have data."""

    has_corrections: bool
    has_used_by: bool
    has_literature: bool


# ---------------------------------------------------------------------------
# Record + response envelope
# ---------------------------------------------------------------------------


class ScientificEnergyCorrectionSchemeRecord(BaseModel):
    """One ECS row projected as a scientific record."""

    energy_correction_scheme: EnergyCorrectionSchemeCoreBlock
    level_of_theory: LevelOfTheorySummary | None = None
    literature: LiteratureSummary | None = None
    evidence_summary: EnergyCorrectionSchemeEvidenceSummary
    available_sections: AvailableEnergyCorrectionSchemeSections

    # Optional include blocks
    corrections: list[EnergyCorrectionTermSummary] | None = None
    used_by: list[EnergyCorrectionSchemeUsageSummary] | None = None


class ScientificEnergyCorrectionSchemeDetailResponse(BaseModel):
    """Response envelope for ``GET /scientific/energy-correction-schemes/{handle}``."""

    request: RequestEcho
    review_summary: ReviewStatusSummary
    record: ScientificEnergyCorrectionSchemeRecord


__all__ = [
    "AvailableEnergyCorrectionSchemeSections",
    "EnergyCorrectionSchemeCoreBlock",
    "EnergyCorrectionSchemeEvidenceSummary",
    "EnergyCorrectionSchemeUsageSummary",
    "EnergyCorrectionTermSummary",
    "RequestEcho",
    "ScientificEnergyCorrectionSchemeDetailResponse",
    "ScientificEnergyCorrectionSchemeRecord",
]
