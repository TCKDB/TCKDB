"""Resolve and persist the statistical model a statmech record declares.

A declaration (``tckdb_schemas.statmech_declarations.StatmechModelDeclaration``) says which model a statmech
record supplies. This module turns it into rows (``statmech_model_declaration``, ``..._member``, ``..._source``):

* **Owner.** The declaring record's own subject. A species-owned record takes an ``atom``, ``basin`` or
  ``explicit_equilibrium_ensemble`` scope; a transition-state-owned record takes ``ts_determination``, naming a
  determination of the same deposit by ``determination_key`` or an existing one by ``determination_ref``. A mismatch is ``statmech_model_declaration_mismatch`` with a
  ``context.reason``.
* **Basin.** A ``conformer_group_ref`` of the record's own species entry. Inside a conformer deposit an omitted ref
  is that conformer's own group; anywhere else it must be stated, because nothing else says which basin is meant.
* **Members.** Each is an existing statmech record of the same species entry, cited by public ref (programmatic
  chaining, held to the same ownership rule a chained calculation is). A record is a member once; a repeated record is
  evidence, not another state.
* **Sources.** A calculation by local key or public ref (it must belong to the declaring subject, and a stated geometry
  must be one it ran on or produced), imported literature parameters, or a frequency scale factor. A scan-role source is
  a scan calculation. Nothing is fabricated for an imported model.
* **Identity.** ``content_hash`` is a sha256 over the canonical declaration, its members and its sources by public ref,
  a recomputable checksum. A record carries at most one declaration.

Nothing is inferred and nothing is defaulted: an omitted fact is stored as omitted.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.statmech_declarations import (
    STATMECH_MODEL_VERSIONS,
    W_STATMECH_MODEL_INVALID,
    W_STATMECH_MODEL_VERSION_UNSUPPORTED,
)
from tckdb_schemas.statmech_declarations import (
    StatmechModelDeclaration as StatmechModelDeclarationIn,
)

from app.api.error_contract import CodedValueError
from app.db.models.calculation import Calculation, CalculationInputGeometry, CalculationOutputGeometry
from app.db.models.common import CalculationType, StatmechModelScopeKind, StatmechModelSourceRole
from app.db.models.energy_correction import FrequencyScaleFactor
from app.db.models.geometry import Geometry
from app.db.models.literature import Literature
from app.db.models.species import ConformerGroup
from app.db.models.statmech import Statmech
from app.db.models.statmech_declaration import (
    StatmechModelDeclaration,
    StatmechModelMember,
    StatmechModelSource,
)
from app.db.models.structure_determination import StructureDetermination
from app.db.models.workflow import WorkflowToolRelease
from app.schemas.upload_warning import UploadWarning
from app.services.calculation_resolution import resolve_workflow_tool_release_ref
from app.services.energy_correction_resolution import resolve_or_create_freq_scale_factor_ref
from app.services.literature_resolution import resolve_or_create_literature
from app.services.local_key_resolution import resolve_calculation_key
from app.services.upload_reference import (
    W_UNKNOWN_CALCULATION_REF,
    W_UNKNOWN_CONFORMER_GROUP_REF,
    W_UNKNOWN_STATMECH_REF,
    unknown_reference,
)

#: A declaration contradicts the subject it sits on, or a record it cites.
W_STATMECH_MODEL_MISMATCH = "statmech_model_declaration_mismatch"
W_STATMECH_CALCULATION_KEY_UNDECLARED = "statmech_calculation_key_undeclared"
#: A ``determination_ref`` names no structure determination.
W_UNKNOWN_STRUCTURE_DETERMINATION_REF = "unknown_structure_determination_ref"

FIELD = "statmech.model_declaration"


def _mismatch(reason: str, message: str) -> CodedValueError:
    return CodedValueError(
        W_STATMECH_MODEL_MISMATCH, message, context={"reason": reason, "field": FIELD}, message_prefix=False
    )


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def stored_recipe(declaration: StatmechModelDeclarationIn) -> dict[str, Any]:
    """The recipe as made: JSON, with a field the producer did not state left out (a default is not a statement)."""
    return declaration.recipe.model_dump(mode="json", exclude_none=True, exclude_unset=True)


def _revalidated(declaration: StatmechModelDeclarationIn) -> StatmechModelDeclarationIn:
    """A declaration that skipped request validation (``model_construct``) is validated again here."""
    version = getattr(declaration, "version", None)
    if type(version) is not int or version not in STATMECH_MODEL_VERSIONS:
        raise CodedValueError(
            W_STATMECH_MODEL_VERSION_UNSUPPORTED,
            f"model_declaration.version must be one of {sorted(STATMECH_MODEL_VERSIONS)}.",
            context={"field": f"{FIELD}.version"},
            message_prefix=False,
        )
    try:
        return StatmechModelDeclarationIn.model_validate(declaration.model_dump(mode="json", exclude_unset=True))
    except ValueError as exc:
        raise CodedValueError(
            W_STATMECH_MODEL_INVALID,
            "model_declaration is not a valid declaration; state its scope, attribution and consistent keys.",
            context={"field": FIELD},
            message_prefix=False,
        ) from exc


def _group_by_ref(session: Session, ref: str, *, field: str) -> ConformerGroup:
    group = session.scalar(select(ConformerGroup).where(ConformerGroup.public_ref == ref))
    if group is None:
        raise unknown_reference(
            code=W_UNKNOWN_CONFORMER_GROUP_REF,
            field=field,
            kind="conformer group",
            ref=ref,
            remedy="Deposit the conformer first, or state the basin of this upload.",
        )
    return group


def _calculation_for(
    session: Session,
    source_index: int,
    *,
    key: str | None,
    ref: str | None,
    calculations_by_key: Mapping[str, Any],
) -> Calculation:
    field = f"{FIELD}.sources[{source_index}]"
    if key is not None:
        found = resolve_calculation_key(
            key, calculations_by_key, field=f"{field}.calculation_key", code=W_STATMECH_CALCULATION_KEY_UNDECLARED
        )
        return found if isinstance(found, Calculation) else session.get(Calculation, found)  # type: ignore[return-value]
    assert ref is not None
    calc = session.scalar(select(Calculation).where(Calculation.public_ref == ref))
    if calc is None:
        raise unknown_reference(
            code=W_UNKNOWN_CALCULATION_REF,
            field=f"{field}.calculation_ref",
            kind="calculation",
            ref=ref,
            remedy="Declare the job inline with a calculation_key, or deposit it first.",
        )
    return calc


def _belongs_to(calc: Calculation, statmech: Statmech) -> bool:
    if statmech.species_entry_id is not None:
        return calc.species_entry_id == statmech.species_entry_id
    return calc.transition_state_entry_id is not None and calc.transition_state_entry_id == statmech.transition_state_entry_id


def declaration_content_hash(
    *,
    version: int,
    scope_kind: str,
    scope_basin_ref: str | None,
    scope_determination_ref: str | None,
    recipe: dict[str, Any],
    members: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    literature_ref: str | None,
    workflow_tool_release_ref: str | None,
) -> str:
    """sha256 over the canonical declaration, its members and its sources, by public ref. A checksum, not an identity."""
    payload = {
        "version": version,
        "scope_kind": scope_kind,
        "basin": scope_basin_ref,
        "determination": scope_determination_ref,
        "attribution": {"literature": literature_ref, "workflow_tool_release": workflow_tool_release_ref},
        "recipe": recipe,
        "members": sorted(members, key=lambda m: m["member_key"]),
        "sources": sorted(sources, key=lambda s: s["source_key"]),
    }
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def recompute_content_hash(session: Session, row: StatmechModelDeclaration) -> str:
    """The checksum of a stored declaration, rebuilt from its rows. Equal to ``row.content_hash`` unless a row changed."""
    group = session.get(ConformerGroup, row.conformer_group_id) if row.conformer_group_id is not None else None
    determination = (
        session.get(StructureDetermination, row.structure_determination_id)
        if row.structure_determination_id is not None
        else None
    )
    literature = session.get(Literature, row.literature_id) if row.literature_id is not None else None
    release = (
        session.get(WorkflowToolRelease, row.workflow_tool_release_id) if row.workflow_tool_release_id is not None else None
    )
    members = [
        {
            "member_key": m.member_key,
            "statmech": session.get(Statmech, m.member_statmech_id).public_ref,  # type: ignore[union-attr]
            "basin": session.get(ConformerGroup, m.conformer_group_id).public_ref if m.conformer_group_id else None,  # type: ignore[union-attr]
            "multiplicity": m.counting_multiplicity,
            "relative_energy_kj_mol": m.relative_energy_kj_mol,
            "region_key": m.region_key,
            **({"relative_energy_convention": m.relative_energy_convention.value} if m.relative_energy_convention else {}),
        }
        for m in row.members
    ]
    sources = []
    for src in row.sources:
        if src.calculation_id is not None:
            locator: dict[str, Any] = {"calculation": session.get(Calculation, src.calculation_id).public_ref}  # type: ignore[union-attr]
            if src.geometry_id is not None:
                locator["geometry"] = session.get(Geometry, src.geometry_id).public_ref  # type: ignore[union-attr]
        elif src.literature_id is not None:
            locator = {"literature": session.get(Literature, src.literature_id).public_ref}  # type: ignore[union-attr]
        else:
            locator = {"frequency_scale_factor": session.get(FrequencyScaleFactor, src.frequency_scale_factor_id).public_ref}  # type: ignore[union-attr]
        sources.append({"source_key": src.source_key, "role": src.role.value, "member_key": src.member_key, **locator})
    return declaration_content_hash(
        version=row.schema_version,
        scope_kind=row.scope_kind.value,
        scope_basin_ref=group.public_ref if group is not None else None,
        scope_determination_ref=determination.public_ref if determination is not None else None,
        recipe=row.recipe,
        members=members,
        sources=sources,
        literature_ref=literature.public_ref if literature is not None else None,
        workflow_tool_release_ref=release.public_ref if release is not None else None,
    )


def persist_statmech_model_declaration(
    session: Session,
    statmech: Statmech,
    declaration: StatmechModelDeclarationIn | None,
    *,
    calculations_by_key: Mapping[str, Any] | None = None,
    own_conformer_group_id: int | None = None,
    determinations_by_key: Mapping[str, StructureDetermination] | None = None,
    created_by: int | None = None,
    warnings_out: list[UploadWarning] | None = None,
) -> StatmechModelDeclaration | None:
    """Create the declaration (and its members and sources) of a freshly created statmech record.

    Call after the record, its source calculations and the upload's own calculations exist and are flushed.

    :param own_conformer_group_id: The basin a conformer deposit is about; an omitted ``basin`` ref means this one.
    :param determinations_by_key: The structure determinations this deposit declared, by their ``key`` (a transition-state
        deposit); a ``determination_key`` is resolved here.
    :raises CodedValueError: ``statmech_model_declaration_invalid`` /
        ``statmech_model_declaration_version_unsupported`` (a declaration built without request validation),
        ``statmech_model_declaration_mismatch`` (``context.reason``: ``owner``, ``scope``, ``basin``, ``determination``,
        ``member_owner``, ``source_owner``, ``source_geometry``, ``source_type``), ``statmech_calculation_key_undeclared``.
    :raises NotFoundError: ``unknown_statmech_ref``, ``unknown_calculation_ref``, ``unknown_conformer_group_ref``.
    """
    if declaration is None:
        return None
    declaration = _revalidated(declaration)
    keys: Mapping[str, Any] = calculations_by_key or {}
    kind = StatmechModelScopeKind(declaration.scope_kind.value)

    ts_scope = kind is StatmechModelScopeKind.ts_determination
    if ts_scope != (statmech.species_entry_id is None):
        raise _mismatch(
            "owner",
            "A transition-state-owned record declares a ts_determination model, and a species-owned record an atom, "
            "basin or ensemble one.",
        )
    if kind is StatmechModelScopeKind.atom and statmech.rigid_rotor_kind is not None and statmech.rigid_rotor_kind.value != "atom":
        raise _mismatch("scope", "An atom scope contradicts the record's own rigid_rotor_kind.")

    group_id: int | None = None
    group_ref: str | None = None
    determination_id: int | None = None
    determination_ref: str | None = None
    if ts_scope:
        if declaration.determination_key is not None:
            found = (determinations_by_key or {}).get(declaration.determination_key)
            if found is None:
                raise _mismatch(
                    "determination",
                    f"{FIELD}.determination_key names a determination that is not declared in this deposit.",
                )
        else:
            found = session.scalar(
                select(StructureDetermination).where(StructureDetermination.public_ref == declaration.determination_ref)
            )
            if found is None:
                raise unknown_reference(
                    code=W_UNKNOWN_STRUCTURE_DETERMINATION_REF,
                    field=f"{FIELD}.determination_ref",
                    kind="structure determination",
                    ref=str(declaration.determination_ref),
                    remedy="Deposit the determination with the transition state first, or declare it in this deposit.",
                )
        if (
            found.transition_state_entry_id is None
            or found.transition_state_entry_id != statmech.transition_state_entry_id
            or found.target_kind.value != "saddle_point"
        ):
            raise _mismatch("determination", "The determination is not a saddle determination of the record's own transition state.")
        determination_id, determination_ref = found.id, found.public_ref
    if kind is StatmechModelScopeKind.basin:
        if declaration.conformer_group_ref is not None:
            group = _group_by_ref(session, declaration.conformer_group_ref, field=f"{FIELD}.conformer_group_ref")
        elif own_conformer_group_id is not None:
            group = session.get(ConformerGroup, own_conformer_group_id)
            assert group is not None
        else:
            raise _mismatch("basin", "A basin scope names its conformer_group_ref; nothing else says which basin is meant.")
        if group.species_entry_id != statmech.species_entry_id:
            raise _mismatch("basin", "The basin is a conformer group of another species entry.")
        group_id, group_ref = group.id, group.public_ref

    literature = (
        resolve_or_create_literature(session, declaration.literature, warnings_out=warnings_out, field_prefix=f"{FIELD}.literature.")
        if declaration.literature is not None
        else None
    )
    release = resolve_workflow_tool_release_ref(session, declaration.workflow_tool_release)

    member_facts: list[dict[str, Any]] = []
    member_rows: list[StatmechModelMember] = []
    source_rows: list[StatmechModelSource] = []
    for index, member in enumerate(declaration.members):
        field = f"{FIELD}.members[{index}]"
        target = session.scalar(select(Statmech).where(Statmech.public_ref == member.statmech_ref))
        if target is None:
            raise unknown_reference(
                code=W_UNKNOWN_STATMECH_REF,
                field=f"{field}.statmech_ref",
                kind="statmech",
                ref=member.statmech_ref,
                remedy="Deposit the member's statmech record first.",
            )
        if target.species_entry_id is None or target.species_entry_id != statmech.species_entry_id:
            raise _mismatch("member_owner", f"{field}.statmech_ref names a record of another species entry.")
        member_group_id: int | None = None
        if member.conformer_group_ref is not None:
            member_group = _group_by_ref(session, member.conformer_group_ref, field=f"{field}.conformer_group_ref")
            if member_group.species_entry_id != statmech.species_entry_id:
                raise _mismatch("member_owner", f"{field}.conformer_group_ref names a basin of another species entry.")
            member_group_id = member_group.id
        member_rows.append(
            StatmechModelMember(
                member_key=member.member_key,
                member_statmech_id=target.id,
                conformer_group_id=member_group_id,
                counting_multiplicity=member.counting_multiplicity,
                relative_energy_kj_mol=member.relative_energy_kj_mol,
                relative_energy_convention=member.relative_energy_convention,
                region_key=member.region_key,
            )
        )
        member_facts.append(
            {
                "member_key": member.member_key,
                "statmech": target.public_ref,
                "basin": member.conformer_group_ref,
                "multiplicity": member.counting_multiplicity,
                "relative_energy_kj_mol": member.relative_energy_kj_mol,
                "region_key": member.region_key,
                # Only when stated, so a member without an energy hashes exactly as it did before the field existed.
                **(
                    {"relative_energy_convention": member.relative_energy_convention.value}
                    if member.relative_energy_convention
                    else {}
                ),
            }
        )

    source_facts: list[dict[str, Any]] = []
    for index, src in enumerate(declaration.sources):
        field = f"{FIELD}.sources[{index}]"
        calc_id: int | None = None
        geometry_id: int | None = None
        literature_id: int | None = None
        fsf_id: int | None = None
        locator: dict[str, Any]
        if src.calculation_key is not None or src.calculation_ref is not None:
            calc = _calculation_for(session, index, key=src.calculation_key, ref=src.calculation_ref, calculations_by_key=keys)
            if not _belongs_to(calc, statmech):
                raise _mismatch("source_owner", f"{field} names a calculation of another subject.")
            if src.role is StatmechModelSourceRole.torsional_surface and calc.type is not CalculationType.scan:
                raise _mismatch("source_type", f"{field}: a torsional_surface source is a scan calculation.")
            calc_id = calc.id
            locator = {"calculation": calc.public_ref}
            if src.geometry_ref is not None:
                geometry = session.scalar(select(Geometry).where(Geometry.public_ref == src.geometry_ref))
                on_side = (
                    geometry is not None
                    and session.scalar(
                        select(CalculationInputGeometry.geometry_id).where(
                            CalculationInputGeometry.calculation_id == calc.id,
                            CalculationInputGeometry.geometry_id == geometry.id,
                        )
                    )
                    is None
                    and session.scalar(
                        select(CalculationOutputGeometry.geometry_id).where(
                            CalculationOutputGeometry.calculation_id == calc.id,
                            CalculationOutputGeometry.geometry_id == geometry.id,
                        )
                    )
                    is None
                )
                if geometry is None or on_side:
                    raise _mismatch("source_geometry", f"{field}.geometry_ref is not a geometry the calculation ran on or produced.")
                geometry_id = geometry.id
                locator["geometry"] = geometry.public_ref
        elif src.literature is not None:
            lit = resolve_or_create_literature(
                session, src.literature, warnings_out=warnings_out, field_prefix=f"{field}.literature."
            )
            literature_id = lit.id
            locator = {"literature": lit.public_ref}
        else:
            assert src.freq_scale_factor is not None
            fsf = resolve_or_create_freq_scale_factor_ref(
                session, src.freq_scale_factor, created_by=created_by, warnings_out=warnings_out
            )
            fsf_id = fsf.id
            locator = {"frequency_scale_factor": fsf.public_ref}
        source_rows.append(
            StatmechModelSource(
                source_key=src.source_key,
                role=StatmechModelSourceRole(src.role.value),
                calculation_id=calc_id,
                geometry_id=geometry_id,
                literature_id=literature_id,
                frequency_scale_factor_id=fsf_id,
                member_key=src.member_key,
            )
        )
        source_facts.append({"source_key": src.source_key, "role": src.role.value, "member_key": src.member_key, **locator})

    # Every check above has passed: only now does the declaration (and anything that references it) get created.
    # The checksum is complete before the first INSERT: a declared model is immutable, so no row is updated afterwards.
    content_hash = declaration_content_hash(
        version=declaration.version,
        scope_kind=declaration.scope_kind.value,
        scope_basin_ref=group_ref,
        scope_determination_ref=determination_ref,
        recipe=stored_recipe(declaration),
        members=member_facts,
        sources=source_facts,
        literature_ref=literature.public_ref if literature is not None else None,
        workflow_tool_release_ref=release.public_ref if release is not None else None,
    )
    row = StatmechModelDeclaration(
        statmech_id=statmech.id,
        schema_version=declaration.version,
        scope_kind=kind,
        conformer_group_id=group_id,
        structure_determination_id=determination_id,
        literature_id=literature.id if literature is not None else None,
        workflow_tool_release_id=release.id if release is not None else None,
        recipe=stored_recipe(declaration),
        content_hash=content_hash,
        created_by=created_by,
    )
    session.add(row)
    session.flush()

    for child in (*member_rows, *source_rows):
        child.declaration_id = row.id
        session.add(child)
    session.flush()
    return row
