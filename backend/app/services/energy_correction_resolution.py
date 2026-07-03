"""Resolution service for energy correction upload payloads.

Handles dedup-or-create for correction schemes and frequency scale factors,
and creates applied correction rows with resolved FK IDs.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.energy_correction import (
    AppliedEnergyCorrection,
    AppliedEnergyCorrectionComponent,
    EnergyCorrectionScheme,
    EnergyCorrectionSchemeAtomParam,
    EnergyCorrectionSchemeBondParam,
    EnergyCorrectionSchemeComponentParam,
    FrequencyScaleFactor,
)
from app.schemas.fragments.refs import FreqScaleFactorRef
from app.schemas.workflows.energy_correction_upload import (
    AppliedEnergyCorrectionUploadPayload,
    EnergyCorrectionSchemeRef,
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
        scheme = existing
    else:
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

    _merge_scheme_params(session, scheme, ref)

    return scheme


# Absolute tolerance for comparing scheme parameter values. Scheme params
# are stored reference constants; a relative tolerance would be too
# forgiving for large Hartree-valued AEC params. A producer that sends a
# value differing by more than serialization noise should either match
# the existing scheme or use a distinct scheme identity / version.
_PARAM_VALUE_ABS_TOL = 1e-10


def _assert_param_value_compatible(
    *,
    table_name: str,
    key: str,
    existing_value: float,
    supplied_value: float,
) -> None:
    """Raise if an existing scheme parameter conflicts with a supplied value.

    Energy-correction scheme parameters are reference-library values.
    Reusing a scheme identity with a different value for the same parameter
    key would make the scheme row scientifically ambiguous, so conflicts
    are rejected instead of silently overwriting or ignoring the new value.
    """
    if abs(existing_value - supplied_value) <= _PARAM_VALUE_ABS_TOL:
        return

    raise ValueError(
        f"Conflicting {table_name} value for key='{key}': "
        f"existing={existing_value!r}, supplied={supplied_value!r}. "
        "Use a distinct energy_correction_scheme identity if these parameters "
        "represent a different correction library."
    )


def _merge_scheme_params(
    session: Session,
    scheme: EnergyCorrectionScheme,
    ref: EnergyCorrectionSchemeRef,
) -> None:
    """Idempotently persist scheme parameter rows from an upload ref.

    For each param in ``ref``:

    * if no row exists for the param's key, insert one;
    * if a row exists with the same value (within float tolerance), no-op;
    * if a row exists with a different value, raise ``ValueError`` so the
      API surfaces a 422 rather than silently overwriting reference data.
    """
    added = False

    if ref.atom_params:
        existing_atoms = {
            row.element: row
            for row in session.scalars(
                select(EnergyCorrectionSchemeAtomParam).where(
                    EnergyCorrectionSchemeAtomParam.scheme_id == scheme.id
                )
            ).all()
        }
        for p in ref.atom_params:
            cur = existing_atoms.get(p.element)
            if cur is None:
                session.add(
                    EnergyCorrectionSchemeAtomParam(
                        scheme_id=scheme.id, element=p.element, value=p.value
                    )
                )
                added = True
            else:
                _assert_param_value_compatible(
                    table_name="energy_correction_scheme_atom_param",
                    key=p.element,
                    existing_value=cur.value,
                    supplied_value=p.value,
                )

    if ref.bond_params:
        existing_bonds = {
            row.bond_key: row
            for row in session.scalars(
                select(EnergyCorrectionSchemeBondParam).where(
                    EnergyCorrectionSchemeBondParam.scheme_id == scheme.id
                )
            ).all()
        }
        for p in ref.bond_params:
            cur = existing_bonds.get(p.bond_key)
            if cur is None:
                session.add(
                    EnergyCorrectionSchemeBondParam(
                        scheme_id=scheme.id, bond_key=p.bond_key, value=p.value
                    )
                )
                added = True
            else:
                _assert_param_value_compatible(
                    table_name="energy_correction_scheme_bond_param",
                    key=p.bond_key,
                    existing_value=cur.value,
                    supplied_value=p.value,
                )

    if ref.component_params:
        existing_components = {
            (row.component_kind, row.key): row
            for row in session.scalars(
                select(EnergyCorrectionSchemeComponentParam).where(
                    EnergyCorrectionSchemeComponentParam.scheme_id == scheme.id
                )
            ).all()
        }
        for p in ref.component_params:
            cur = existing_components.get((p.component_kind, p.key))
            if cur is None:
                session.add(
                    EnergyCorrectionSchemeComponentParam(
                        scheme_id=scheme.id,
                        component_kind=p.component_kind,
                        key=p.key,
                        value=p.value,
                    )
                )
                added = True
            else:
                _assert_param_value_compatible(
                    table_name="energy_correction_scheme_component_param",
                    key=f"{p.component_kind.value}:{p.key}",
                    existing_value=cur.value,
                    supplied_value=p.value,
                )

    if added:
        session.flush()


# ---------------------------------------------------------------------------
# Frequency scale factor resolution
# ---------------------------------------------------------------------------


def resolve_or_create_freq_scale_factor_ref(
    session: Session,
    ref: FreqScaleFactorRef,
    *,
    created_by: int | None = None,
) -> FrequencyScaleFactor:
    """Resolve or create a frequency scale factor from the unified FSF ref.

    Dedup key: the full DB identity tuple
    ``(level_of_theory, software, scale_kind, value, source_literature,
    workflow_tool_release)``. ``note`` is descriptive and never used for
    matching — when the identity collides with an existing row, the row
    is reused and the incoming ``note`` is ignored.

    :param session: Active SQLAlchemy session.
    :param ref: Unified upload-facing frequency scale factor reference.
    :param created_by: Optional application user id for newly created rows.
    :returns: Existing or newly created ``FrequencyScaleFactor`` row.
    """
    lot = resolve_level_of_theory_ref(session, ref.level_of_theory)

    software_id = None
    if ref.software is not None:
        sw = resolve_software(session, ref.software.name)
        software_id = sw.id

    literature = (
        resolve_or_create_literature(session, ref.source_literature)
        if ref.source_literature is not None
        else None
    )
    lit_id = literature.id if literature else None

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
        source_literature_id=lit_id,
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
    target_transition_state_entry_id: int | None = None,
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
    :param target_transition_state_entry_id: Resolved target transition-state
        entry id. Exactly one of the three target ids must be set; this is
        enforced by the table's CHECK constraint.
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
        fsf = resolve_or_create_freq_scale_factor_ref(
            session, payload.frequency_scale_factor, created_by=created_by
        )
        fsf_id = fsf.id

    applied = AppliedEnergyCorrection(
        target_species_entry_id=target_species_entry_id,
        target_reaction_entry_id=target_reaction_entry_id,
        target_transition_state_entry_id=target_transition_state_entry_id,
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
