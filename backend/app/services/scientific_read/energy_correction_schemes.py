"""Service implementation for the scientific energy-correction-scheme detail surface.

One endpoint here; search ships in a sibling module:

- ``GET /scientific/energy-correction-schemes/{ref_or_id}`` — one ECS row.

EnergyCorrectionScheme is a content-derived reference table. It is
not in ``SubmissionRecordType``, so it has no per-row review history;
the response envelope still carries an empty ``review_summary`` for
shape parity.

See ``backend/docs/specs/scientific_correction_reads.md``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import NotFoundError
from app.db.models.energy_correction import (
    AppliedEnergyCorrection,
    EnergyCorrectionScheme,
    EnergyCorrectionSchemeAtomParam,
    EnergyCorrectionSchemeBondParam,
    EnergyCorrectionSchemeComponentParam,
)
from app.db.models.literature import Literature
from app.schemas.reads.scientific_common import (
    LevelOfTheorySummary,
    LiteratureSummary,
    ReviewStatusSummary,
)
from app.schemas.reads.scientific_energy_correction_scheme import (
    AvailableEnergyCorrectionSchemeSections,
    EnergyCorrectionSchemeCoreBlock,
    EnergyCorrectionSchemeEvidenceSummary,
    EnergyCorrectionSchemeUsageSummary,
    EnergyCorrectionTermSummary,
    RequestEcho,
    ScientificEnergyCorrectionSchemeDetailResponse,
    ScientificEnergyCorrectionSchemeRecord,
)
from app.services.scientific_read.common import validate_includes
from app.services.scientific_read.handles import (
    resolve_energy_correction_scheme_handle,
)
from app.services.scientific_read.internal_ids import (
    filter_internal_ids_from_resolved,
)

# ECS is not reviewable. ``review`` is not a legal token; ``include=all``
# expands to ``corrections,used_by,literature`` minus internal_ids per
# standard policy.
_LEGAL_INCLUDE_TOKENS: set[str] = {
    "corrections",
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


def get_energy_correction_scheme(
    session: Session,
    *,
    energy_correction_scheme_handle: str,
    include: list[str] | None = None,
) -> ScientificEnergyCorrectionSchemeDetailResponse:
    """Resolve an ECS handle and return its scientific projection."""
    includes = validate_includes(
        include or [],
        _LEGAL_INCLUDE_TOKENS,
        "/scientific/energy-correction-schemes/{energy_correction_scheme_ref_or_id}",
        internal_tokens=_INTERNAL_INCLUDE_TOKENS,
    )
    includes = filter_internal_ids_from_resolved(includes)

    ecs_id = resolve_energy_correction_scheme_handle(
        session, energy_correction_scheme_handle
    )
    ecs = session.get(EnergyCorrectionScheme, ecs_id)
    if ecs is None:  # pragma: no cover — defended by resolver 404
        raise NotFoundError(
            f"energy_correction_scheme not found (energy_correction_scheme_id={ecs_id})",
            code="handle_not_found",
        )

    record = build_energy_correction_scheme_record(
        session, ecs=ecs, includes=includes
    )

    return ScientificEnergyCorrectionSchemeDetailResponse(
        request=RequestEcho(include=sorted(includes)),
        # ECS is non-reviewable; the summary is always empty.
        review_summary=ReviewStatusSummary(),
        record=record,
    )


# ---------------------------------------------------------------------------
# Shared per-record builder (reused by search)
# ---------------------------------------------------------------------------


def build_energy_correction_scheme_record(
    session: Session,
    *,
    ecs: EnergyCorrectionScheme,
    includes: set[str],
) -> ScientificEnergyCorrectionSchemeRecord:
    """Project one EnergyCorrectionScheme row into the public record shape."""
    atom_n = _count_atom_params(session, ecs.id)
    bond_n = _count_bond_params(session, ecs.id)
    comp_n = _count_component_params(session, ecs.id)
    applied_n = _count_applied_usage(session, ecs.id)
    total_terms = atom_n + bond_n + comp_n

    evidence = EnergyCorrectionSchemeEvidenceSummary(
        atom_param_count=atom_n,
        bond_param_count=bond_n,
        component_param_count=comp_n,
        has_corrections=total_terms > 0,
        applied_usage_count=applied_n,
        has_applied_usage=applied_n > 0,
        has_literature_source=ecs.source_literature_id is not None,
    )
    available = AvailableEnergyCorrectionSchemeSections(
        has_corrections=total_terms > 0,
        has_used_by=applied_n > 0,
        has_literature=ecs.source_literature_id is not None,
    )

    lot_summary = _build_lot_summary(session, ecs.level_of_theory_id)
    lit_summary = _build_literature_summary(session, ecs.source_literature_id)

    core = EnergyCorrectionSchemeCoreBlock(
        energy_correction_scheme_id=ecs.id,
        energy_correction_scheme_ref=ecs.public_ref,
        name=ecs.name,
        scheme_kind=ecs.kind,
        version=ecs.version,
        units=ecs.units,
        note=ecs.note,
        created_at=ecs.created_at,
    )

    corrections_block: list[EnergyCorrectionTermSummary] | None = None
    if "corrections" in includes:
        corrections_block = _build_corrections(session, ecs.id)

    used_by_block: list[EnergyCorrectionSchemeUsageSummary] | None = None
    if "used_by" in includes:
        used_by_block = _build_used_by(session, ecs.id)

    return ScientificEnergyCorrectionSchemeRecord(
        energy_correction_scheme=core,
        level_of_theory=lot_summary,
        literature=lit_summary,
        evidence_summary=evidence,
        available_sections=available,
        corrections=corrections_block,
        used_by=used_by_block,
    )


# ---------------------------------------------------------------------------
# Loaders + builders
# ---------------------------------------------------------------------------


def _count_atom_params(session: Session, ecs_id: int) -> int:
    from sqlalchemy import func

    return int(
        session.scalar(
            select(func.count())
            .select_from(EnergyCorrectionSchemeAtomParam)
            .where(EnergyCorrectionSchemeAtomParam.scheme_id == ecs_id)
        )
        or 0
    )


def _count_bond_params(session: Session, ecs_id: int) -> int:
    from sqlalchemy import func

    return int(
        session.scalar(
            select(func.count())
            .select_from(EnergyCorrectionSchemeBondParam)
            .where(EnergyCorrectionSchemeBondParam.scheme_id == ecs_id)
        )
        or 0
    )


def _count_component_params(session: Session, ecs_id: int) -> int:
    from sqlalchemy import func

    return int(
        session.scalar(
            select(func.count())
            .select_from(EnergyCorrectionSchemeComponentParam)
            .where(EnergyCorrectionSchemeComponentParam.scheme_id == ecs_id)
        )
        or 0
    )


def _count_applied_usage(session: Session, ecs_id: int) -> int:
    from sqlalchemy import func

    return int(
        session.scalar(
            select(func.count())
            .select_from(AppliedEnergyCorrection)
            .where(AppliedEnergyCorrection.scheme_id == ecs_id)
        )
        or 0
    )


def _build_corrections(
    session: Session, ecs_id: int
) -> list[EnergyCorrectionTermSummary]:
    """Bounded correction-term projection across all three child tables."""
    out: list[EnergyCorrectionTermSummary] = []
    atoms = session.scalars(
        select(EnergyCorrectionSchemeAtomParam)
        .where(EnergyCorrectionSchemeAtomParam.scheme_id == ecs_id)
        .order_by(EnergyCorrectionSchemeAtomParam.element.asc())
    ).all()
    for row in atoms:
        out.append(
            EnergyCorrectionTermSummary(
                correction_kind="atom",
                target=row.element,
                value=row.value,
            )
        )
    bonds = session.scalars(
        select(EnergyCorrectionSchemeBondParam)
        .where(EnergyCorrectionSchemeBondParam.scheme_id == ecs_id)
        .order_by(EnergyCorrectionSchemeBondParam.bond_key.asc())
    ).all()
    for row in bonds:
        out.append(
            EnergyCorrectionTermSummary(
                correction_kind="bond",
                target=row.bond_key,
                value=row.value,
            )
        )
    components = session.scalars(
        select(EnergyCorrectionSchemeComponentParam)
        .where(EnergyCorrectionSchemeComponentParam.scheme_id == ecs_id)
        .order_by(
            EnergyCorrectionSchemeComponentParam.component_kind.asc(),
            EnergyCorrectionSchemeComponentParam.key.asc(),
        )
    ).all()
    for row in components:
        kind = (
            row.component_kind.value
            if hasattr(row.component_kind, "value")
            else str(row.component_kind)
        )
        out.append(
            EnergyCorrectionTermSummary(
                correction_kind="component",
                target=row.key,
                value=row.value,
                component_kind=kind,
            )
        )
    return out


def _build_used_by(
    session: Session, ecs_id: int
) -> list[EnergyCorrectionSchemeUsageSummary]:
    rows = session.execute(
        select(
            AppliedEnergyCorrection.id,
            AppliedEnergyCorrection.target_species_entry_id,
            AppliedEnergyCorrection.target_reaction_entry_id,
            AppliedEnergyCorrection.target_transition_state_entry_id,
        )
        .where(AppliedEnergyCorrection.scheme_id == ecs_id)
        .order_by(AppliedEnergyCorrection.id.asc())
        .limit(_USAGE_LIMIT)
    ).all()
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
    out: list[EnergyCorrectionSchemeUsageSummary] = []
    for row in rows:
        if row.target_species_entry_id is not None:
            kind, target_id = "species_entry", row.target_species_entry_id
        elif row.target_reaction_entry_id is not None:
            kind, target_id = "reaction_entry", row.target_reaction_entry_id
        elif row.target_transition_state_entry_id is not None:
            kind, target_id = (
                "transition_state_entry",
                row.target_transition_state_entry_id,
            )
        else:
            continue
        model_cls, segment = mapping[kind]
        ref = session.scalar(
            select(model_cls.public_ref).where(model_cls.id == target_id)
        )
        if ref is None:
            continue
        out.append(
            EnergyCorrectionSchemeUsageSummary(
                record_type=kind,
                record_ref=ref,
                record_id=target_id,
                endpoint=f"/api/v1/scientific/{segment}/{ref}",
            )
        )
    return out


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
    "build_energy_correction_scheme_record",
    "get_energy_correction_scheme",
]
