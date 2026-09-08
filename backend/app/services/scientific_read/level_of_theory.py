"""Service implementation for the scientific level-of-theory detail surface.

One endpoint here; search ships in a sibling module:

- ``GET /scientific/level-of-theories/{ref_or_id}`` -- one
  ``level_of_theory`` row, rendered as the reference page a computational
  chemist checks before trusting a number computed at that level: its
  identity, the correction schemes and frequency scale factor(s) actually
  deposited against it, and how many calculations use it.

``level_of_theory`` is not in ``SubmissionRecordType``, so it has no
per-row review history; the response envelope still carries an empty
``review_summary`` for shape parity with the rest of the scientific
surface -- same posture as the energy-correction-scheme and
frequency-scale-factor surfaces this module is deliberately shaped like.

**The join discipline this module exists to get right.** Two distinct
``level_of_theory`` rows can share a ``method``/``basis`` pair and differ
only in ``dispersion``, ``solvent`` or ``spin_treatment`` (DR-0034) --
``LevelOfTheorySummary.display``'s own docstring says so. Every query in
this module that attaches a correction scheme, a frequency scale factor,
or a calculation to a level of theory joins on the row's integer id
(``level_of_theory_id`` / ``lot_id``), never on method/basis text. A join
on text would silently pool two chemically distinct levels of theory that
happen to render the same string -- exactly the failure DR-0034 exists to
prevent. See ``tests/api/scientific/test_api_level_of_theory.py`` for the
fixture that pins this (two LOTs sharing method/basis, differing only in
``spin_treatment``, only one carrying a scheme).

See ``docs/plans/methods-surface.md`` §5.1-5.2 (``plan-methods-surface-v2``,
not committed to this repo).
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import not_found
from app.db.models.calculation import Calculation
from app.db.models.energy_correction import EnergyCorrectionScheme, FrequencyScaleFactor
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.literature import Literature
from app.db.models.software import SoftwareRelease
from app.schemas.reads.scientific_common import ReviewStatusSummary
from app.schemas.reads.scientific_level_of_theory import (
    AvailableLevelOfTheorySections,
    LevelOfTheoryCalculationUsageSummary,
    LevelOfTheoryCoreBlock,
    LevelOfTheoryEvidenceSummary,
    LevelOfTheoryFrequencyScaleFactorGroup,
    LevelOfTheoryFrequencyScaleFactorProvenance,
    RequestEcho,
    ScientificLevelOfTheoryDetailResponse,
    ScientificLevelOfTheoryRecord,
)
from app.services.scientific_read.common import validate_includes
from app.services.scientific_read.energy_correction_schemes import (
    build_energy_correction_scheme_record,
)
from app.services.scientific_read.frequency_scale_factors import (
    _build_software_release_summary,
    _build_workflow_release_summary,
)
from app.services.scientific_read.handles import resolve_level_of_theory_handle
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)

# LOT is not reviewable (no SubmissionRecordType entry), so ``review`` is
# intentionally not a legal token. ``software`` (the LOT-scoped
# software/version breakdown, methods-surface plan §5.3) is deliberately
# NOT in this table yet -- it depends on a new aggregation service that
# ships in a follow-up change; asking for it 422s with
# ``unknown_include_token`` rather than silently answering with nothing.
# ``include=all`` expands to ``correction_schemes,frequency_scale_factors,
# used_by`` minus internal_ids per the standard policy.
_LEGAL_INCLUDE_TOKENS: set[str] = {
    "correction_schemes",
    "frequency_scale_factors",
    "used_by",
    "internal_ids",
    "all",
}
_INTERNAL_INCLUDE_TOKENS: set[str] = {"internal_ids"}

_USAGE_LIMIT = 50

_RECORD_TYPE_MAPPING = {
    "species_entry": "species-entries",
    "transition_state_entry": "transition-state-entries",
}


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


def get_level_of_theory(
    session: Session,
    *,
    level_of_theory_handle: str,
    include: list[str] | None = None,
) -> ScientificLevelOfTheoryDetailResponse:
    """Resolve a level-of-theory handle and return its scientific projection."""
    includes = validate_includes(
        include or [],
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/level-of-theories/{level_of_theory_ref_or_id}",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    lot_id = resolve_level_of_theory_handle(session, level_of_theory_handle)
    lot = session.get(LevelOfTheory, lot_id)
    if lot is None:  # pragma: no cover — defended by resolver 404
        raise not_found("level_of_theory", row_id=lot_id, code="handle_not_found")

    record = build_level_of_theory_record(session, lot=lot, includes=includes)

    return ScientificLevelOfTheoryDetailResponse(
        request=RequestEcho(include=sorted(includes)),
        # LOT is non-reviewable; the summary is always empty.
        review_summary=ReviewStatusSummary(),
        record=record,
    )


# ---------------------------------------------------------------------------
# Shared per-record builder (reused by search)
# ---------------------------------------------------------------------------


def build_level_of_theory_record(
    session: Session,
    *,
    lot: LevelOfTheory,
    includes: set[str],
) -> ScientificLevelOfTheoryRecord:
    """Project one LevelOfTheory row into the public record shape."""
    calc_usage_count = _count_calculation_usage(session, lot.id)
    has_schemes = _has_correction_schemes(session, lot.id)
    has_fsf = _has_frequency_scale_factors(session, lot.id)
    distinct_software = _count_distinct_software(session, lot.id)

    evidence = LevelOfTheoryEvidenceSummary(
        calculation_usage_count=calc_usage_count,
        has_correction_schemes=has_schemes,
        has_frequency_scale_factors=has_fsf,
        distinct_software_count=distinct_software,
    )
    available = AvailableLevelOfTheorySections(
        has_correction_schemes=has_schemes,
        has_frequency_scale_factors=has_fsf,
        has_used_by=calc_usage_count > 0,
    )

    core = LevelOfTheoryCoreBlock(
        level_of_theory_id=lot.id,
        level_of_theory_ref=lot.public_ref,
        method=lot.method,
        basis=lot.basis,
        aux_basis=lot.aux_basis,
        cabs_basis=lot.cabs_basis,
        dispersion=lot.dispersion,
        solvent=lot.solvent,
        solvent_model=lot.solvent_model,
        keywords=lot.keywords,
        spin_treatment=lot.spin_treatment,
        lot_hash=lot.lot_hash,
        created_at=lot.created_at,
    )

    correction_schemes_block = None
    if "correction_schemes" in includes:
        correction_schemes_block = _build_correction_schemes(session, lot.id)

    fsf_block = None
    if "frequency_scale_factors" in includes:
        fsf_block = _build_frequency_scale_factors(session, lot.id)

    used_by_block = None
    if "used_by" in includes:
        used_by_block = _build_used_by(session, lot.id)

    return ScientificLevelOfTheoryRecord(
        level_of_theory=core,
        evidence_summary=evidence,
        available_sections=available,
        correction_schemes=correction_schemes_block,
        frequency_scale_factors=fsf_block,
        used_by=used_by_block,
    )


# ---------------------------------------------------------------------------
# Evidence helpers
# ---------------------------------------------------------------------------


def _count_calculation_usage(session: Session, lot_id: int) -> int:
    """Number of calculations attributing *lot_id*.

    Usage-derived: an ``INNER JOIN`` from ``calculation.lot_id`` is
    exactly what a ``COUNT(*) WHERE lot_id = :lot_id`` already is -- a
    level of theory nothing attaches to counts zero here, and
    ``search_levels_of_theory`` uses this same predicate (``EXISTS``) to
    keep such a row out of search results entirely. See that module's
    docstring for the OUTER-vs-INNER distinction this guards.
    """
    return int(
        session.scalar(
            select(func.count())
            .select_from(Calculation)
            .where(Calculation.lot_id == lot_id)
        )
        or 0
    )


def _has_correction_schemes(session: Session, lot_id: int) -> bool:
    return (
        session.scalar(
            select(EnergyCorrectionScheme.id)
            .where(EnergyCorrectionScheme.level_of_theory_id == lot_id)
            .limit(1)
        )
        is not None
    )


def _has_frequency_scale_factors(session: Session, lot_id: int) -> bool:
    return (
        session.scalar(
            select(FrequencyScaleFactor.id)
            .where(FrequencyScaleFactor.level_of_theory_id == lot_id)
            .limit(1)
        )
        is not None
    )


def _count_distinct_software(session: Session, lot_id: int) -> int:
    """Distinct ``software`` packages observed running a calculation at *lot_id*.

    A headline count, not the per-package breakdown (``include=software``,
    methods-surface plan §5.3, added separately once the LOT-scoped
    aggregation service lands). ``INNER JOIN`` from ``calculation`` through
    ``software_release``, same usage-derived discipline as everywhere else
    in this module -- a package with zero calculations at this LOT
    contributes nothing to the count.
    """
    return int(
        session.scalar(
            select(func.count(func.distinct(SoftwareRelease.software_id)))
            .select_from(Calculation)
            .join(
                SoftwareRelease,
                SoftwareRelease.id == Calculation.software_release_id,
            )
            .where(Calculation.lot_id == lot_id)
        )
        or 0
    )


# ---------------------------------------------------------------------------
# include=correction_schemes
# ---------------------------------------------------------------------------


def _build_correction_schemes(session: Session, lot_id: int):
    """Schemes joined on ``level_of_theory_id`` -- the FK, never text.

    This is the fix for the gap the methods-surface plan names directly:
    there is no public ``lot_ref=`` filter on
    ``/scientific/energy-correction-schemes/search`` (deliberately -- see
    that module), so the only exact way to ask "which schemes belong to
    this level of theory" is from the LOT side, where the foreign key is
    available. Each scheme is projected with its own ``corrections``
    section populated, so the LOT page can render the full parameter
    table inline without a second round trip per scheme.
    """
    schemes = session.scalars(
        select(EnergyCorrectionScheme)
        .where(EnergyCorrectionScheme.level_of_theory_id == lot_id)
        .order_by(
            EnergyCorrectionScheme.kind.asc(),
            EnergyCorrectionScheme.name.asc(),
            EnergyCorrectionScheme.id.asc(),
        )
    ).all()
    return [
        build_energy_correction_scheme_record(
            session, ecs=scheme, includes={"corrections"}
        )
        for scheme in schemes
    ]


# ---------------------------------------------------------------------------
# include=frequency_scale_factors
# ---------------------------------------------------------------------------


def _build_frequency_scale_factors(session: Session, lot_id: int):
    """FSF rows for *lot_id*, deduplicated by ``(scale_kind, value)``.

    Joined on ``FrequencyScaleFactor.level_of_theory_id`` -- the FK. Ten
    distinct rows can (and, on the live archive, do) evaluate to the same
    ``(scale_kind, value)`` pair; each stays visible inside its group as a
    separate ``frequency_scale_factor_ref`` with its own provenance rather
    than collapsing to one row (methods-surface plan §4.2 item 4).
    """
    rows = session.scalars(
        select(FrequencyScaleFactor)
        .where(FrequencyScaleFactor.level_of_theory_id == lot_id)
        .order_by(
            FrequencyScaleFactor.scale_kind.asc(),
            FrequencyScaleFactor.value.asc(),
            FrequencyScaleFactor.id.asc(),
        )
    ).all()

    groups: dict[tuple[str, float], list[FrequencyScaleFactor]] = {}
    order: list[tuple[str, float]] = []
    for fsf in rows:
        key = (fsf.scale_kind.value, fsf.value)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(fsf)

    out: list[LevelOfTheoryFrequencyScaleFactorGroup] = []
    for scale_kind, value in order:
        members = groups[(scale_kind, value)]
        provenance = [
            LevelOfTheoryFrequencyScaleFactorProvenance(
                frequency_scale_factor_ref=fsf.public_ref,
                frequency_scale_factor_id=fsf.id,
                software_release=_build_software_release_summary(
                    session, fsf.software_id
                ),
                workflow_tool_release=_build_workflow_release_summary(
                    session, fsf.workflow_tool_release_id
                ),
                source_literature_ref=_literature_ref(
                    session, fsf.source_literature_id
                ),
            )
            for fsf in members
        ]
        out.append(
            LevelOfTheoryFrequencyScaleFactorGroup(
                scale_kind=members[0].scale_kind,
                value=value,
                frequency_scale_factor_count=len(members),
                frequency_scale_factors=provenance,
            )
        )
    return out


def _literature_ref(session: Session, literature_id: int | None) -> str | None:
    if literature_id is None:
        return None
    return session.scalar(
        select(Literature.public_ref).where(Literature.id == literature_id)
    )


# ---------------------------------------------------------------------------
# include=used_by
# ---------------------------------------------------------------------------


def _build_used_by(
    session: Session, lot_id: int
) -> list[LevelOfTheoryCalculationUsageSummary]:
    """Bounded, ordered-by-id list of calculations attributing this LOT."""
    from app.db.models.species import SpeciesEntry
    from app.db.models.transition_state import TransitionStateEntry

    rows = session.execute(
        select(
            Calculation.id,
            Calculation.public_ref,
            Calculation.type,
            Calculation.species_entry_id,
            Calculation.transition_state_entry_id,
        )
        .where(Calculation.lot_id == lot_id)
        .order_by(Calculation.id.asc())
        .limit(_USAGE_LIMIT)
    ).all()
    if not rows:
        return []

    species_entry_ids = [r.species_entry_id for r in rows if r.species_entry_id]
    ts_entry_ids = [
        r.transition_state_entry_id for r in rows if r.transition_state_entry_id
    ]
    species_refs: dict[int, str] = {}
    if species_entry_ids:
        species_refs = dict(
            session.execute(
                select(SpeciesEntry.id, SpeciesEntry.public_ref).where(
                    SpeciesEntry.id.in_(species_entry_ids)
                )
            ).all()
        )
    ts_refs: dict[int, str] = {}
    if ts_entry_ids:
        ts_refs = dict(
            session.execute(
                select(
                    TransitionStateEntry.id, TransitionStateEntry.public_ref
                ).where(TransitionStateEntry.id.in_(ts_entry_ids))
            ).all()
        )

    out: list[LevelOfTheoryCalculationUsageSummary] = []
    for row in rows:
        record_type: str | None = None
        record_ref: str | None = None
        if row.species_entry_id is not None:
            record_type = "species_entry"
            record_ref = species_refs.get(row.species_entry_id)
        elif row.transition_state_entry_id is not None:
            record_type = "transition_state_entry"
            record_ref = ts_refs.get(row.transition_state_entry_id)
        record_endpoint = (
            f"/api/v1/scientific/{_RECORD_TYPE_MAPPING[record_type]}/{record_ref}"
            if record_type is not None and record_ref is not None
            else None
        )
        out.append(
            LevelOfTheoryCalculationUsageSummary(
                calculation_ref=row.public_ref,
                calculation_id=row.id,
                endpoint=f"/api/v1/scientific/calculations/{row.public_ref}",
                type=row.type,
                record_type=record_type,
                record_ref=record_ref,
                record_endpoint=record_endpoint,
            )
        )
    return out


__all__ = [
    "_INTERNAL_INCLUDE_TOKENS",
    "_LEGAL_INCLUDE_TOKENS",
    "build_level_of_theory_record",
    "get_level_of_theory",
]
