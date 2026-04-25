"""Resolution service for energy correction upload payloads.

Handles dedup-or-create for correction schemes and frequency scale factors,
and creates applied correction rows with resolved FK IDs.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

import app.db.models  # noqa: F401
from app.db.models.energy_correction import (
    AppliedEnergyCorrection,
    AppliedEnergyCorrectionComponent,
    EnergyCorrectionScheme,
    EnergyCorrectionSchemeAtomParam,
    EnergyCorrectionSchemeBondParam,
    EnergyCorrectionSchemeComponentParam,
    FrequencyScaleFactor,
)
from app.db.models.software import Software
from app.db.models.workflow import WorkflowToolRelease
from app.schemas.fragments.refs import FreqScaleFactorRef
from app.schemas.workflows.energy_correction_upload import (
    AppliedEnergyCorrectionUploadPayload,
    EnergyCorrectionSchemeRef,
    FrequencyScaleFactorRef as LegacyFrequencyScaleFactorRef,
)
from app.services.calculation_resolution import (
    resolve_level_of_theory_ref,
    resolve_workflow_tool_release_ref,
)
from app.services.literature_resolution import resolve_or_create_literature
from app.services.software_resolution import resolve_software

# ---------------------------------------------------------------------------
# Scheme resolution
# ---------------------------------------------------------------------------


def resolve_or_create_scheme(
    session: Session,
    ref: EnergyCorrectionSchemeRef,
    *,
    created_by: int | None = None,
) -> EnergyCorrectionScheme:
    """Resolve or create an energy correction scheme.

    Dedup key: (kind, name, level_of_theory_id, version).

    :param session: Active SQLAlchemy session.
    :param ref: Upload-facing scheme reference.
    :param created_by: Optional application user id.
    :returns: Existing or newly created scheme row.
    """
    lot = (
        resolve_level_of_theory_ref(session, ref.level_of_theory)
        if ref.level_of_theory is not None
        else None
    )
    lot_id = lot.id if lot else None

    existing = session.scalar(
        select(EnergyCorrectionScheme).where(
            EnergyCorrectionScheme.kind == ref.kind,
            EnergyCorrectionScheme.name == ref.name,
            (
                EnergyCorrectionScheme.level_of_theory_id == lot_id
                if lot_id is not None
                else EnergyCorrectionScheme.level_of_theory_id.is_(None)
            ),
            (
                EnergyCorrectionScheme.version == ref.version
                if ref.version is not None
                else EnergyCorrectionScheme.version.is_(None)
            ),
        )
    )
    if existing is not None:
        return existing

    literature = (
        resolve_or_create_literature(session, ref.source_literature)
        if ref.source_literature is not None
        else None
    )

    scheme = EnergyCorrectionScheme(
        kind=ref.kind,
        name=ref.name,
        level_of_theory_id=lot_id,
        source_literature_id=literature.id if literature else None,
        version=ref.version,
        units=ref.units,
        note=ref.note,
        created_by=created_by,
    )
    session.add(scheme)
    session.flush()

    for p in ref.atom_params:
        session.add(
            EnergyCorrectionSchemeAtomParam(
                scheme_id=scheme.id, element=p.element, value=p.value
            )
        )
    for p in ref.bond_params:
        session.add(
            EnergyCorrectionSchemeBondParam(
                scheme_id=scheme.id, bond_key=p.bond_key, value=p.value
            )
        )
    for p in ref.component_params:
        session.add(
            EnergyCorrectionSchemeComponentParam(
                scheme_id=scheme.id,
                component_kind=p.component_kind,
                key=p.key,
                value=p.value,
            )
        )

    if ref.atom_params or ref.bond_params or ref.component_params:
        session.flush()

    return scheme


# ---------------------------------------------------------------------------
# Frequency scale factor resolution
# ---------------------------------------------------------------------------


def resolve_or_create_frequency_scale_factor(
    session: Session,
    ref: LegacyFrequencyScaleFactorRef,
    *,
    created_by: int | None = None,
) -> FrequencyScaleFactor:
    """Resolve or create a frequency scale factor (energy-correction upload path).

    Dedup key: full identity (lot, software=null, scale_kind, value, lit, wtr=null).

    :param session: Active SQLAlchemy session.
    :param ref: Upload-facing frequency scale factor reference.
    :param created_by: Optional application user id.
    :returns: Existing or newly created FSF row.
    """
    lot = resolve_level_of_theory_ref(session, ref.level_of_theory)

    literature = (
        resolve_or_create_literature(session, ref.source_literature)
        if ref.source_literature is not None
        else None
    )
    lit_id = literature.id if literature else None

    return _resolve_or_create_fsf_row(
        session,
        level_of_theory_id=lot.id,
        software_id=None,
        scale_kind=ref.scale_kind,
        value=ref.value,
        source_literature_id=lit_id,
        workflow_tool_release_id=None,
        note=ref.note,
        created_by=created_by,
    )


def resolve_or_create_freq_scale_factor_ref(
    session: Session,
    ref: FreqScaleFactorRef,
    *,
    created_by: int | None = None,
) -> FrequencyScaleFactor:
    """Resolve or create a frequency scale factor from a statmech upload ref.

    Dedup key: full identity (lot, software, scale_kind, value, lit, workflow_tool_release).

    :param session: Active SQLAlchemy session.
    :param ref: Upload-facing freq scale factor ref (from refs.py).
    :param created_by: Optional application user id.
    :returns: Existing or newly created FSF row.
    """
    lot = resolve_level_of_theory_ref(session, ref.level_of_theory)

    software_id = None
    if ref.software is not None:
        sw = resolve_software(session, ref.software.name)
        software_id = sw.id

    wtr_id = None
    if ref.workflow_tool_release is not None:
        wtr = resolve_workflow_tool_release_ref(session, ref.workflow_tool_release)
        wtr_id = wtr.id

    return _resolve_or_create_fsf_row(
        session,
        level_of_theory_id=lot.id,
        software_id=software_id,
        scale_kind=ref.scale_kind,
        value=ref.value,
        source_literature_id=None,
        workflow_tool_release_id=wtr_id,
        note=ref.note,
        created_by=created_by,
    )


def _resolve_or_create_fsf_row(
    session: Session,
    *,
    level_of_theory_id: int,
    software_id: int | None,
    scale_kind,
    value: float,
    source_literature_id: int | None,
    workflow_tool_release_id: int | None,
    note: str | None,
    created_by: int | None,
) -> FrequencyScaleFactor:
    """Core dedup-or-create logic for FrequencyScaleFactor rows.

    Uniqueness is on the full identity of the definition (all fields).
    """
    from sqlalchemy.exc import IntegrityError

    def _match(col, val):
        return col == val if val is not None else col.is_(None)

    existing = session.scalar(
        select(FrequencyScaleFactor).where(
            FrequencyScaleFactor.level_of_theory_id == level_of_theory_id,
            _match(FrequencyScaleFactor.software_id, software_id),
            FrequencyScaleFactor.scale_kind == scale_kind,
            FrequencyScaleFactor.value == value,
            _match(FrequencyScaleFactor.source_literature_id, source_literature_id),
            _match(FrequencyScaleFactor.workflow_tool_release_id, workflow_tool_release_id),
        )
    )
    if existing is not None:
        return existing

    try:
        with session.begin_nested():
            fsf = FrequencyScaleFactor(
                level_of_theory_id=level_of_theory_id,
                software_id=software_id,
                scale_kind=scale_kind,
                value=value,
                source_literature_id=source_literature_id,
                workflow_tool_release_id=workflow_tool_release_id,
                note=note,
                created_by=created_by,
            )
            session.add(fsf)
            session.flush()
    except IntegrityError:
        fsf = session.scalar(
            select(FrequencyScaleFactor).where(
                FrequencyScaleFactor.level_of_theory_id == level_of_theory_id,
                _match(FrequencyScaleFactor.software_id, software_id),
                FrequencyScaleFactor.scale_kind == scale_kind,
                FrequencyScaleFactor.value == value,
                _match(FrequencyScaleFactor.source_literature_id, source_literature_id),
                _match(
                    FrequencyScaleFactor.workflow_tool_release_id,
                    workflow_tool_release_id,
                ),
            )
        )
    return fsf


# ---------------------------------------------------------------------------
# Applied energy correction creation
# ---------------------------------------------------------------------------


def create_applied_energy_correction(
    session: Session,
    payload: AppliedEnergyCorrectionUploadPayload,
    *,
    target_species_entry_id: int | None = None,
    target_reaction_entry_id: int | None = None,
    source_conformer_observation_id: int | None = None,
    source_calculation_id: int | None = None,
    created_by: int | None = None,
) -> AppliedEnergyCorrection:
    """Resolve provenance refs and create an applied energy correction.

    The workflow orchestrator is responsible for resolving local string keys
    (``source_conformer_key``, ``source_calculation_key``) to integer IDs
    before calling this function.

    :param session: Active SQLAlchemy session.
    :param payload: Upload-facing applied correction payload.
    :param target_species_entry_id: Resolved target species entry id.
    :param target_reaction_entry_id: Resolved target reaction entry id.
    :param source_conformer_observation_id: Resolved source conformer id.
    :param source_calculation_id: Resolved source calculation id.
    :param created_by: Optional application user id.
    :returns: Newly created ``AppliedEnergyCorrection`` row.
    """
    scheme_id = None
    fsf_id = None

    if payload.scheme is not None:
        scheme = resolve_or_create_scheme(
            session, payload.scheme, created_by=created_by
        )
        scheme_id = scheme.id

    if payload.frequency_scale_factor is not None:
        fsf = resolve_or_create_frequency_scale_factor(
            session, payload.frequency_scale_factor, created_by=created_by
        )
        fsf_id = fsf.id

    applied = AppliedEnergyCorrection(
        target_species_entry_id=target_species_entry_id,
        target_reaction_entry_id=target_reaction_entry_id,
        source_conformer_observation_id=source_conformer_observation_id,
        source_calculation_id=source_calculation_id,
        scheme_id=scheme_id,
        frequency_scale_factor_id=fsf_id,
        application_role=payload.application_role,
        value=payload.value,
        value_unit=payload.value_unit,
        temperature_k=payload.temperature_k,
        note=payload.note,
        created_by=created_by,
    )
    session.add(applied)
    session.flush()

    for comp in payload.components:
        session.add(
            AppliedEnergyCorrectionComponent(
                applied_correction_id=applied.id,
                component_kind=comp.component_kind,
                key=comp.key,
                multiplicity=comp.multiplicity,
                parameter_value=comp.parameter_value,
                contribution_value=comp.contribution_value,
            )
        )

    if payload.components:
        session.flush()

    return applied
