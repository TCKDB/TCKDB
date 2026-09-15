"""Resolution service for energy correction upload payloads.

Handles dedup-or-create for correction schemes and frequency scale factors,
and creates applied correction rows with resolved FK IDs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.enums import (
    EnergyCorrectionApplicationRole,
    EnergyCorrectionSchemeKind,
)
from tckdb_schemas.local_key_codes import (
    W_APPLIED_CORRECTION_SOURCE_KEY_UNDECLARED as _W_APPLIED_CORRECTION_SOURCE_KEY_UNDECLARED,
)

from app.api.error_contract import CodedValueError
from app.chemistry.species import species_smiles_has_any_bonds
from app.chemistry.units import convert_energy_to_hartree
from app.db.models.common import EnergyUnit
from app.db.models.energy_correction import (
    AppliedEnergyCorrection,
    AppliedEnergyCorrectionComponent,
    EnergyCorrectionScheme,
    EnergyCorrectionSchemeAtomParam,
    EnergyCorrectionSchemeBondParam,
    EnergyCorrectionSchemeComponentParam,
    FrequencyScaleFactor,
)
from app.db.models.species import Species, SpeciesEntry
from app.schemas.fragments.refs import FreqScaleFactorRef
from app.schemas.upload_warning import UploadWarning
from app.schemas.workflows.energy_correction_upload import (
    AppliedEnergyCorrectionUploadPayload,
    EnergyCorrectionSchemeRef,
)
from app.services.calculation_resolution import (
    resolve_level_of_theory_ref,
    resolve_workflow_tool_release_ref,
)
from app.services.literature_resolution import resolve_or_create_literature
from app.services.local_key_resolution import resolve_declared_key
from app.services.provenance_warnings import (
    collect_energy_correction_scheme_provenance_warnings,
)
from app.services.software_resolution import resolve_software_release_ref

#: An applied correction names a source the enclosing upload never declared.
#:
#: Re-exported; *defined* in :mod:`tckdb_schemas.local_key_codes`, because
#: four request schemas refuse the same mistake one layer earlier and may
#: not import ``app`` (ADR 0017).
W_APPLIED_CORRECTION_SOURCE_KEY_UNDECLARED = (
    _W_APPLIED_CORRECTION_SOURCE_KEY_UNDECLARED
)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Local key resolution
# ---------------------------------------------------------------------------


def resolve_applied_correction_source_key(
    key: str | None,
    declared: Mapping[str, T],
    *,
    field: str,
    declares: str,
) -> T | None:
    """Turn one applied-correction source key into the row it names.

    Blocking, and ADR 0008 permits it because this asserts a *contract*
    rather than an expectation: a local key is a promise that the same
    upload declared something under that name, and if it did not, no
    correct payload can make the link mean what it says. The alternative
    is worse than silence -- a key that resolves to whatever happens to be
    at hand attaches a correction to a calculation the depositor never
    chose, and they have no way to notice.

    ``declared`` is the namespace the enclosing request built from its own
    payload, so its keys are strings the depositor wrote. They are echoed
    into the message on purpose: naming what *is* declared is what turns
    the refusal into something fixable. Row ids never appear -- they are
    values of this map, not part of any message.

    The lookup itself moved to
    :func:`app.services.local_key_resolution.resolve_declared_key` when
    nineteen raw subscripts in the bundle workflows were routed through
    the same seam; what stays here is this field's *code* and its closing
    sentence. The code stays because it was published before that seam
    existed and is pinned on ``/uploads/conformers``, and because a
    correction's source is one repair whether the key names a conformer
    or a calculation -- splitting it by which kind of name the depositor
    used would answer the same question two ways on two routes.

    Generic in the value type: ``computed_species`` hands this a map of
    ``Calculation`` rows because its next move is an ownership check that
    needs one, while the other callers hand it ids.

    :param key: The key the payload wrote, or ``None`` for no link.
    :param declared: Declared local name -> the row or id it names.
    :param field: Field path naming the offending key, echoed verbatim.
    :param declares: How a depositor declares a name in this namespace,
        phrased as the remedy sentence's object.
    :returns: What ``declared`` holds for ``key``, or ``None`` when
        ``key`` is ``None``.
    :raises CodedValueError: if ``key`` is set but names nothing declared.
    """
    if key is None:
        return None
    return resolve_declared_key(
        key,
        declared,
        field=field,
        code=W_APPLIED_CORRECTION_SOURCE_KEY_UNDECLARED,
        subject="anything",
        remedy=(
            f"{declares} An energy correction whose source cannot be "
            f"named is a correction nobody can trace back to what it "
            f"corrected."
        ),
    )


# ---------------------------------------------------------------------------
# Scheme resolution
# ---------------------------------------------------------------------------


def resolve_or_create_scheme(
    session: Session,
    ref: EnergyCorrectionSchemeRef,
    *,
    created_by: int | None = None,
    warnings_out: list[UploadWarning] | None = None,
) -> EnergyCorrectionScheme:
    """Resolve or create an energy correction scheme.

    Dedup key: the full DB identity tuple ``(kind, name,
    level_of_theory_id, version, units, source_literature_id,
    software_release_id, workflow_tool_release_id)`` — matches
    ``uq_energy_correction_scheme_identity`` (correction-scheme-provenance
    plan v2 §3-§4). ``ref.software`` is a ``SoftwareReleaseRef`` (name,
    optionally version/revision/build): a depositor who names only the
    program resolves to the version-less release row for it (§3.2 --
    "program known, build not stated" is a first-class, complete value,
    not a degraded one), and reusing that same program name reuses that
    same row. A supplied citation, software release, or unit that differs
    from an existing same-``(kind, name, lot, version)`` row is never
    dropped: it is scientifically distinct identity, so it resolves to
    (or creates) a *different* row rather than silently overwriting or
    ignoring what the depositor sent. Two rows that agree on every field
    including these still collapse into one, exactly as before this
    widening — that residual ambiguity (same kind/LOT/release/units, both
    uncited) is real and is reported, not resolved, via ``warnings_out``.

    :param session: Active SQLAlchemy session.
    :param ref: Upload-facing scheme reference.
    :param created_by: Optional application user id.
    :param warnings_out: Optional sink for non-blocking provenance
        warnings (missing citation, missing software for a
        software-scoped kind, an ambiguous uncited sibling). Only
        populated when a *new* row is created — reusing an existing row
        already produced whatever warning applied when it was first
        created. ``None`` (the default) means "caller does not want
        these," matching every existing call site.
    :returns: Existing or newly created scheme row.
    """
    lot = (
        resolve_level_of_theory_ref(session, ref.level_of_theory)
        if ref.level_of_theory is not None
        else None
    )
    lot_id = lot.id if lot else None

    literature = (
        resolve_or_create_literature(
            session,
            ref.source_literature,
            warnings_out=warnings_out,
            field_prefix="source_literature.",
        )
        if ref.source_literature is not None
        else None
    )
    lit_id = literature.id if literature else None

    software_release_id = None
    if ref.software is not None:
        release = resolve_software_release_ref(session, ref.software)
        software_release_id = release.id

    wtr_id = None
    if ref.workflow_tool_release is not None:
        wtr = resolve_workflow_tool_release_ref(session, ref.workflow_tool_release)
        wtr_id = wtr.id if wtr is not None else None

    def _match(col, val):
        return col == val if val is not None else col.is_(None)

    existing = session.scalar(
        select(EnergyCorrectionScheme).where(
            EnergyCorrectionScheme.kind == ref.kind,
            EnergyCorrectionScheme.name == ref.name,
            _match(EnergyCorrectionScheme.level_of_theory_id, lot_id),
            # Neither `version` (dropped) nor `units` is matched on: this
            # chain must mirror uq_energy_correction_scheme_identity
            # exactly (a7d4e2b9c351), or the index and the resolver
            # disagree about what a duplicate is. A deposit in a second
            # unit is meant to land on the existing row; the parameter
            # comparison converts before it compares.
            _match(EnergyCorrectionScheme.source_literature_id, lit_id),
            _match(EnergyCorrectionScheme.software_release_id, software_release_id),
            _match(EnergyCorrectionScheme.workflow_tool_release_id, wtr_id),
        )
    )
    created = existing is None
    if existing is not None:
        scheme = existing
    else:
        scheme = EnergyCorrectionScheme(
            kind=ref.kind,
            name=ref.name,
            level_of_theory_id=lot_id,
            source_literature_id=lit_id,
            software_release_id=software_release_id,
            workflow_tool_release_id=wtr_id,
            units=ref.units,
            note=ref.note,
            created_by=created_by,
        )
        session.add(scheme)
        session.flush()

    _merge_scheme_params(session, scheme, ref)

    if warnings_out is not None and created:
        warnings_out.extend(
            collect_energy_correction_scheme_provenance_warnings(
                session, scheme=scheme
            )
        )

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
    existing_units: EnergyUnit | None = None,
    supplied_units: EnergyUnit | None = None,
) -> None:
    """Raise if an existing scheme parameter conflicts with a supplied value.

    Energy-correction scheme parameters are reference-library values.
    Reusing a scheme identity with a different value for the same
    parameter key would make the scheme row scientifically ambiguous, so
    conflicts are rejected instead of silently overwriting or ignoring
    the new value.

    **Both sides are converted to hartree before comparing** when their
    units are known. Without that, this function was unit-blind: a
    depositor re-sending the same library in kcal/mol was told its
    numbers conflicted (``existing=-0.42, supplied=-0.00067``) when they
    are the same physical value, and told to "use a distinct identity",
    which was not something they could do. ``a7d4e2b9c351`` removed
    ``units`` from the identity precisely so that deposit lands here, on
    the existing row -- which makes converting here the thing that has to
    work.

    When either unit is unknown, or is one ``convert_energy_to_hartree``
    has no factor for, the raw values are compared as before and the
    error says so. That is the honest fallback: refusing to compare would
    reject a deposit this function cannot prove is wrong, and comparing
    converted-against-raw would invent a conflict.
    """
    existing_cmp = existing_value
    supplied_cmp = supplied_value
    converted = False

    if existing_units is not None and supplied_units is not None:
        existing_h = convert_energy_to_hartree(existing_value, existing_units)
        supplied_h = convert_energy_to_hartree(supplied_value, supplied_units)
        if existing_h is not None and supplied_h is not None:
            existing_cmp, supplied_cmp = existing_h, supplied_h
            converted = True

    if abs(existing_cmp - supplied_cmp) <= _PARAM_VALUE_ABS_TOL:
        return

    if converted:
        detail = (
            f"existing={existing_value!r} {existing_units.value}, "
            f"supplied={supplied_value!r} {supplied_units.value} "
            "(compared in hartree)"
        )
    else:
        detail = (
            f"existing={existing_value!r}, supplied={supplied_value!r} "
            "(compared as deposited: the unit of one or both is not "
            "recorded, so neither could be converted)"
        )

    raise ValueError(
        f"Conflicting {table_name} value for key='{key}': {detail}. "
        "These are the same correction library by identity, so the "
        "values have to agree. If they represent a different library, "
        "give it a different citation or software release -- those are "
        "what distinguish one library from another."
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
                    existing_units=scheme.units,
                    supplied_units=ref.units,
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
                    existing_units=scheme.units,
                    supplied_units=ref.units,
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
                    existing_units=scheme.units,
                    supplied_units=ref.units,
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
    warnings_out: list[UploadWarning] | None = None,
) -> FrequencyScaleFactor:
    """Resolve or create a frequency scale factor from the unified FSF ref.

    Dedup key: the full DB identity tuple ``(level_of_theory,
    software_release, scale_kind, value, source_literature,
    workflow_tool_release)`` — matches
    ``uq_frequency_scale_factor_identity`` (correction-scheme-provenance
    plan v2 §6). ``ref.software`` is a ``SoftwareReleaseRef`` (name,
    optionally version/revision/build): a depositor who names only the
    program resolves to the version-less release row for it, mirroring
    ``resolve_or_create_scheme``. ``note`` is descriptive and never used
    for matching — when the identity collides with an existing row, the
    row is reused and the incoming ``note`` is ignored.

    :param session: Active SQLAlchemy session.
    :param ref: Unified upload-facing frequency scale factor reference.
    :param created_by: Optional application user id for newly created rows.
    :returns: Existing or newly created ``FrequencyScaleFactor`` row.
    """
    lot = resolve_level_of_theory_ref(session, ref.level_of_theory)

    software_release_id = None
    if ref.software is not None:
        release = resolve_software_release_ref(session, ref.software)
        software_release_id = release.id

    literature = (
        resolve_or_create_literature(
            session,
            ref.source_literature,
            warnings_out=warnings_out,
            field_prefix="source_literature.",
        )
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
        software_release_id=software_release_id,
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
    software_release_id: int | None,
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
            _match(FrequencyScaleFactor.software_release_id, software_release_id),
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
                software_release_id=software_release_id,
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
                _match(FrequencyScaleFactor.software_release_id, software_release_id),
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
# bac_total component contract
# ---------------------------------------------------------------------------

#: A ``bac_total`` correction with no component breakdown, targeting a
#: subject where "no bonds were summed" can never be established.
#:
#: A bond-additivity correction is definitionally a sum over bonds. A
#: transition state carries no bond assignment at all: Arkane's own
#: Petersson-BAC routine sums whatever bond dictionary it is handed, and
#: ARC supplies none for a saddle point, so a componentless total there
#: can never be told apart from "nothing was actually summed" (task
#: #264's measured 17 rows are exactly that shape). A species entry is
#: different: its identity SMILES states its bonds, so a monatomic
#: species has an honest zero total with nothing to decompose (the same
#: task's other 2 rows, on ``[H]`` and ``[O]``).
#:
#: Scoped to ``bac_petersson``. A ``bac_melius`` total does not decompose
#: into a stable per-component breakdown even for a bonded species -- see
#: ``test_bac_melius_no_components_persists`` -- so a componentless
#: Melius total proves nothing about whether bonds were summed, on either
#: kind of target, and is left alone.
W_BAC_TOTAL_REQUIRES_COMPONENTS = "bac_total_requires_components"


def assert_bac_total_has_required_components(
    session: Session,
    payload: AppliedEnergyCorrectionUploadPayload,
    *,
    field: str,
    target_species_entry_id: int | None = None,
    target_transition_state_entry_id: int | None = None,
) -> None:
    """Refuse a componentless ``bac_total`` unless the target has no bonds.

    Every workflow that is about to hand *payload* to
    :func:`create_applied_energy_correction` calls this first. Storage
    stores exactly what it is given -- see that function's own
    docstring -- so the shape has to be settled before it gets there,
    not inside it.

    :param session: Active session, used to look up the target species
        entry's identity SMILES when the target is a species entry.
    :param payload: The upload-facing correction about to be persisted.
    :param field: Field path naming this correction (not a sub-field of
        it), echoed verbatim in the refusal.
    :param target_species_entry_id: Resolved target species entry id, or
        ``None`` when the target is not a species entry.
    :param target_transition_state_entry_id: Resolved target transition
        state entry id, or ``None`` when the target is not one.
    :raises CodedValueError: if the shape cannot be shown to be honest.
    """
    if payload.application_role != EnergyCorrectionApplicationRole.bac_total:
        return
    if payload.components:
        return
    # ``bac_total`` always carries a scheme, of one of these two kinds
    # (``AppliedEnergyCorrectionUploadPayload.validate_role_source_compatibility``
    # and ``validate_role_scheme_kind_compatibility``, both schema-level).
    # The ``is not None`` check is defensive, not load-bearing.
    if (
        payload.scheme is not None
        and payload.scheme.kind == EnergyCorrectionSchemeKind.bac_melius
    ):
        return

    remedy = (
        "Supply the components that were summed, or omit this correction "
        "entirely: a correction that was not applied is expressed by "
        "leaving the row out, not by depositing a zero."
    )

    if target_transition_state_entry_id is not None:
        raise CodedValueError(
            W_BAC_TOTAL_REQUIRES_COMPONENTS,
            f"{field}: application_role='bac_total' with no components "
            f"cannot target a transition state. A transition state "
            f"carries no bond assignment, so a componentless total can "
            f"never be shown to be a real bond additivity correction "
            f"rather than an empty sum. {remedy}",
            context={"field": field, "target_kind": "transition_state_entry"},
            message_prefix=False,
        )

    if target_species_entry_id is not None:
        species_entry = session.get(SpeciesEntry, target_species_entry_id)
        species = (
            session.get(Species, species_entry.species_id)
            if species_entry is not None
            else None
        )
        if species is not None and species_smiles_has_any_bonds(species.smiles):
            raise CodedValueError(
                W_BAC_TOTAL_REQUIRES_COMPONENTS,
                f"{field}: application_role='bac_total' with no "
                f"components targets a species with at least one bond, "
                f"so a componentless total can never be shown to be a "
                f"real bond additivity correction rather than an empty "
                f"sum. {remedy}",
                context={"field": field, "target_kind": "species_entry"},
                message_prefix=False,
            )


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
    warnings_out: list[UploadWarning] | None = None,
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
    :param warnings_out: Optional sink for the scheme's non-blocking
        provenance warnings (see :func:`resolve_or_create_scheme`).
        ``None`` (the default) is a no-op, matching every existing caller.
    :returns: Newly created ``AppliedEnergyCorrection`` row.
    """
    scheme_id = None
    fsf_id = None

    if payload.scheme is not None:
        scheme = resolve_or_create_scheme(
            session, payload.scheme, created_by=created_by, warnings_out=warnings_out
        )
        scheme_id = scheme.id

    if payload.frequency_scale_factor is not None:
        fsf = resolve_or_create_freq_scale_factor_ref(
            session,
            payload.frequency_scale_factor,
            created_by=created_by,
            warnings_out=warnings_out,
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
