"""Service implementation for the scientific frequency-scale-factor detail surface.

One endpoint here; search ships in a sibling module:

- ``GET /scientific/frequency-scale-factors/{ref_or_id}`` — one FSF row.

FrequencyScaleFactor is a content-derived reference table. It is not
in ``SubmissionRecordType``, so it has no per-row review history; the
response envelope still carries an empty ``review_summary`` for shape
parity with the rest of the scientific surface.

See ``backend/docs/specs/scientific_correction_reads.md``.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import NotFoundError
from app.db.models.energy_correction import (
    AppliedEnergyCorrection,
    FrequencyScaleFactor,
)
from app.db.models.literature import Literature
from app.db.models.software import Software
from app.db.models.statmech import Statmech
from app.db.models.workflow import WorkflowTool, WorkflowToolRelease
from app.schemas.reads.scientific_common import (
    LevelOfTheorySummary,
    LiteratureSummary,
    ReviewStatusSummary,
    SoftwareReleaseSummary,
    WorkflowToolReleaseSummary,
)
from app.schemas.reads.scientific_frequency_scale_factor import (
    AvailableFrequencyScaleFactorSections,
    FrequencyScaleFactorCoreBlock,
    FrequencyScaleFactorEvidenceSummary,
    FrequencyScaleFactorUsageSummary,
    RequestEcho,
    ScientificFrequencyScaleFactorDetailResponse,
    ScientificFrequencyScaleFactorRecord,
)
from app.services.scientific_read.common import validate_includes
from app.services.scientific_read.handles import (
    resolve_frequency_scale_factor_handle,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)

# FSF is not reviewable (no SubmissionRecordType entry), so ``review``
# is intentionally not a legal token. ``include=all`` expands to
# ``used_by,literature`` minus internal_ids per the standard policy.
_LEGAL_INCLUDE_TOKENS: set[str] = {
    "used_by",
    "literature",
    "internal_ids",
    "all",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}

_USAGE_LIMIT = 50


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


def get_frequency_scale_factor(
    session: Session,
    *,
    frequency_scale_factor_handle: str,
    include: list[str] | None = None,
) -> ScientificFrequencyScaleFactorDetailResponse:
    """Resolve an FSF handle and return its scientific projection."""
    includes = validate_includes(
        include or [],
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/frequency-scale-factors/{frequency_scale_factor_ref_or_id}",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    fsf_id = resolve_frequency_scale_factor_handle(
        session, frequency_scale_factor_handle
    )
    fsf = session.get(FrequencyScaleFactor, fsf_id)
    if fsf is None:  # pragma: no cover — defended by resolver 404
        raise NotFoundError(
            f"frequency_scale_factor not found (frequency_scale_factor_id={fsf_id})",
            code="handle_not_found",
        )

    record = build_frequency_scale_factor_record(
        session, fsf=fsf, includes=includes
    )

    return ScientificFrequencyScaleFactorDetailResponse(
        request=RequestEcho(include=sorted(includes)),
        # FSF is non-reviewable; the summary is always empty.
        review_summary=ReviewStatusSummary(),
        record=record,
    )


# ---------------------------------------------------------------------------
# Shared per-record builder (reused by search)
# ---------------------------------------------------------------------------


def build_frequency_scale_factor_record(
    session: Session,
    *,
    fsf: FrequencyScaleFactor,
    includes: set[str],
) -> ScientificFrequencyScaleFactorRecord:
    """Project one FrequencyScaleFactor row into the public record shape."""
    statmech_count = _count_statmech_usage(session, fsf.id)

    evidence = FrequencyScaleFactorEvidenceSummary(
        has_literature_source=fsf.source_literature_id is not None,
        has_workflow_tool_source=fsf.workflow_tool_release_id is not None,
        has_software_dimension=fsf.software_id is not None,
        statmech_usage_count=statmech_count,
        has_statmech_usage=statmech_count > 0,
    )
    available = AvailableFrequencyScaleFactorSections(
        has_used_by=statmech_count > 0,
        has_literature=fsf.source_literature_id is not None,
    )

    lot_summary = _build_lot_summary(session, fsf.level_of_theory_id)
    sw_summary = _build_software_release_summary(session, fsf.software_id)
    wf_summary = _build_workflow_release_summary(
        session, fsf.workflow_tool_release_id
    )
    lit_summary = _build_literature_summary(session, fsf.source_literature_id)

    core = FrequencyScaleFactorCoreBlock(
        frequency_scale_factor_id=fsf.id,
        frequency_scale_factor_ref=fsf.public_ref,
        scale_kind=fsf.scale_kind,
        value=fsf.value,
        note=fsf.note,
        created_at=fsf.created_at,
    )

    used_by_block: list[FrequencyScaleFactorUsageSummary] | None = None
    if "used_by" in includes:
        used_by_block = _build_used_by(session, fsf.id)

    return ScientificFrequencyScaleFactorRecord(
        frequency_scale_factor=core,
        level_of_theory=lot_summary,
        software_release=sw_summary,
        workflow_tool_release=wf_summary,
        literature=lit_summary,
        evidence_summary=evidence,
        available_sections=available,
        used_by=used_by_block,
    )


# ---------------------------------------------------------------------------
# Loaders + builders
# ---------------------------------------------------------------------------


def _count_statmech_usage(session: Session, fsf_id: int) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(Statmech)
            .where(Statmech.frequency_scale_factor_id == fsf_id)
        )
        or 0
    )


def _build_used_by(
    session: Session, fsf_id: int
) -> list[FrequencyScaleFactorUsageSummary]:
    """Bounded inverse-link summary.

    Includes statmech rows directly referencing the FSF, plus any
    species/reaction/transition-state entries with an
    ``applied_energy_correction`` row whose
    ``frequency_scale_factor_id`` matches. Capped at ``_USAGE_LIMIT``
    rows to keep payloads bounded.
    """
    out: list[FrequencyScaleFactorUsageSummary] = []

    statmech_rows = session.execute(
        select(Statmech.id, Statmech.public_ref)
        .where(Statmech.frequency_scale_factor_id == fsf_id)
        .order_by(Statmech.id.asc())
        .limit(_USAGE_LIMIT)
    ).all()
    for row in statmech_rows:
        out.append(
            FrequencyScaleFactorUsageSummary(
                record_type="statmech",
                record_ref=row.public_ref,
                record_id=row.id,
                endpoint=f"/api/v1/scientific/statmech/{row.public_ref}",
            )
        )

    remaining = _USAGE_LIMIT - len(out)
    if remaining > 0:
        applied_rows = session.execute(
            select(
                AppliedEnergyCorrection.id,
                AppliedEnergyCorrection.target_species_entry_id,
                AppliedEnergyCorrection.target_reaction_entry_id,
                AppliedEnergyCorrection.target_transition_state_entry_id,
            )
            .where(AppliedEnergyCorrection.frequency_scale_factor_id == fsf_id)
            .order_by(AppliedEnergyCorrection.id.asc())
            .limit(remaining)
        ).all()
        for row in applied_rows:
            kind, target_id = _classify_applied_target(row)
            ref, endpoint = _resolve_target_pointer(session, kind, target_id)
            if ref is None:
                continue
            out.append(
                FrequencyScaleFactorUsageSummary(
                    record_type=kind,
                    record_ref=ref,
                    record_id=target_id,
                    endpoint=endpoint,
                )
            )
    return out


def _classify_applied_target(row) -> tuple[str, int | None]:
    if row.target_species_entry_id is not None:
        return "species_entry", row.target_species_entry_id
    if row.target_reaction_entry_id is not None:
        return "reaction_entry", row.target_reaction_entry_id
    if row.target_transition_state_entry_id is not None:
        return "transition_state_entry", row.target_transition_state_entry_id
    return "unknown", None


def _resolve_target_pointer(
    session: Session, kind: str, target_id: int | None
) -> tuple[str | None, str]:
    if target_id is None:
        return None, ""
    from app.db.models.reaction import ReactionEntry
    from app.db.models.species import SpeciesEntry
    from app.db.models.transition_state import TransitionStateEntry

    mapping = {
        "species_entry": (SpeciesEntry, "species-entries"),
        "reaction_entry": (ReactionEntry, "reaction-entries"),
        "transition_state_entry": (
            TransitionStateEntry,
            "transition-state-entries",
        ),
    }
    spec = mapping.get(kind)
    if spec is None:
        return None, ""
    model_cls, segment = spec
    ref = session.scalar(
        select(model_cls.public_ref).where(model_cls.id == target_id)
    )
    if ref is None:
        return None, ""
    return ref, f"/api/v1/scientific/{segment}/{ref}"


def _build_lot_summary(
    session: Session, lot_id: int | None
) -> LevelOfTheorySummary | None:
    if lot_id is None:
        return None
    from app.db.models.level_of_theory import LevelOfTheory

    lot = session.get(LevelOfTheory, lot_id)
    if lot is None:
        return None
    return LevelOfTheorySummary(
        level_of_theory_id=lot.id,
        level_of_theory_ref=lot.public_ref,
        method=lot.method,
        basis=lot.basis,
        dispersion=lot.dispersion,
        solvent=lot.solvent,
        label=None,
    )


def _build_software_release_summary(
    session: Session, software_id: int | None
) -> SoftwareReleaseSummary | None:
    """FSF row stores ``software_id`` (the software vendor), not a release.

    There's no release granularity on this row, so the summary shows the
    vendor name without a version. We synthesize a SoftwareReleaseSummary
    shape for symmetry with the rest of the surface.
    """
    if software_id is None:
        return None
    sw = session.get(Software, software_id)
    if sw is None:
        return None
    return SoftwareReleaseSummary(
        software_release_id=0,
        software_release_ref="",
        software=sw.name,
        version=None,
    )


def _build_workflow_release_summary(
    session: Session, workflow_tool_release_id: int | None
) -> WorkflowToolReleaseSummary | None:
    if workflow_tool_release_id is None:
        return None
    row = session.execute(
        select(
            WorkflowToolRelease.id,
            WorkflowToolRelease.public_ref,
            WorkflowToolRelease.version,
            WorkflowTool.name,
        )
        .join(
            WorkflowTool,
            WorkflowTool.id == WorkflowToolRelease.workflow_tool_id,
        )
        .where(WorkflowToolRelease.id == workflow_tool_release_id)
    ).one_or_none()
    if row is None:
        return None
    return WorkflowToolReleaseSummary(
        workflow_tool_release_id=row.id,
        workflow_tool_release_ref=row.public_ref,
        workflow_tool=row.name,
        version=row.version,
    )


def _build_literature_summary(
    session: Session, literature_id: int | None
) -> LiteratureSummary | None:
    if literature_id is None:
        return None
    lit = session.get(Literature, literature_id)
    if lit is None:
        return None
    return LiteratureSummary(
        id=lit.id,
        literature_ref=lit.public_ref,
        title=getattr(lit, "title", None),
        year=getattr(lit, "year", None),
        doi=getattr(lit, "doi", None),
    )


__all__ = [
    "_INTERNAL_INCLUDE_TOKENS",
    "_LEGAL_INCLUDE_TOKENS",
    "build_frequency_scale_factor_record",
    "get_frequency_scale_factor",
]
