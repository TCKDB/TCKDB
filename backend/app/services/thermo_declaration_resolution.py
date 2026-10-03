"""Resolve and enforce a thermo record's target and protocol declarations.

A depositor may declare, on a thermo record, what its values describe
(``thermodynamic_target``) and how they were produced (``protocol``); the wire
shapes and the self-contradiction rules live in
:mod:`tckdb_schemas.thermo_declarations`. This module is the half that needs a
database:

* a ``single_conformer`` target names a conformer group, by public ref or by
  the local key of a conformer the same bundle declares; the group must exist
  and belong to the record's own species entry;
* a protocol's supporting calculations name calculations by local key or public
  ref; each must exist and belong to the record's own species entry, and what
  is *stored* is the calculation's public ref, never the key and never an id.

Three layers, none of which may be removed because the others exist (ADR 0017):
the request schema refuses a self-contradicting declaration first; the workflow
calls :func:`resolve_thermo_declarations`, which re-derives every check from
the declaration itself rather than trusting that validation ran (a payload built
with ``model_construct`` skips every validator); and :func:`persist_thermo`
calls :func:`assert_thermo_declaration_columns` on the resolved columns, the
last stop before the row is written, for callers that never go through a
workflow at all.

Declarations are attributed claims. Nothing here infers a target from a statmech
link, defaults a missing one, or checks a protocol's recipe against the linked
calculations; that is later, separate work.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from tckdb_schemas.local_key_codes import (
    W_CALCULATION_KEY_UNDECLARED,
    W_CONFORMER_KEY_UNDECLARED,
)
from tckdb_schemas.thermo_declarations import (
    THERMO_PROTOCOL_VERSIONS,
    W_THERMO_DECLARATION_INVALID,
    W_THERMO_PROTOCOL_VERSION_UNSUPPORTED,
    W_THERMO_TARGET_GROUP_NOT_ALLOWED,
    W_THERMO_TARGET_GROUP_REQUIRED,
    StoredThermoProtocolDeclaration,
    ThermoProtocolDeclaration,
    thermo_declaration_error,
)

from app.api.error_contract import CodedValueError
from app.db.models.calculation import Calculation
from app.db.models.common import ThermoTargetKind
from app.db.models.species import ConformerGroup
from app.services.calculation_ownership import assert_calculation_owned_by, assert_owned_by
from app.services.local_key_resolution import resolve_calculation_key, resolve_declared_key
from app.services.upload_reference import (
    W_UNKNOWN_CALCULATION_REF,
    W_UNKNOWN_CONFORMER_GROUP_REF,
    unknown_reference,
)

logger = logging.getLogger(__name__)

#: A thermo target names a conformer group owned by another species entry.
#: Its own code: "you cited someone else's conformer group" is repaired by
#: choosing a group of this record's species entry, not by depositing anything.
W_THERMO_TARGET_GROUP_OWNER_MISMATCH = "thermo_target_group_owner_mismatch"

#: A protocol's supporting calculation is owned by another species entry. Its
#: own code, distinct from the thermo source-link one: a source link says which
#: jobs produced the number; this says which jobs a *declaration* rests on, and
#: a client repairing one has nothing to say about the other.
W_THERMO_PROTOCOL_CALCULATION_OWNER_MISMATCH = "thermo_protocol_calculation_owner_mismatch"

_CONFORMER_KEY_REMEDY = (
    "Every conformer in a bundle carries a required 'key'; a thermodynamic_target "
    "conformer_key must match one of them."
)

__all__ = [
    "W_THERMO_PROTOCOL_CALCULATION_OWNER_MISMATCH",
    "W_THERMO_TARGET_GROUP_OWNER_MISMATCH",
    "ResolvedThermoDeclarations",
    "assert_thermo_declaration",
    "assert_thermo_declaration_columns",
    "resolve_thermo_declarations",
]


@dataclass(frozen=True)
class ResolvedThermoDeclarations:
    """The three ``thermo`` columns a declaration resolves to. All ``None`` when none was made."""

    thermodynamic_target_kind: ThermoTargetKind | None = None
    target_conformer_group_id: int | None = None
    protocol_declaration: dict[str, Any] | None = None


def assert_thermo_declaration(payload: object) -> None:
    """Refuse a self-contradicting declaration on a whole thermo payload.

    Runs the shared producer rule on the payload as it stands, so a payload
    assembled without validation is judged the same as a validated one.
    """
    error = thermo_declaration_error(payload)
    if error is not None:
        code, message = error
        raise CodedValueError(
            code,
            message,
            context={"field": "protocol" if code.startswith("thermo_protocol") else "thermodynamic_target"},
            message_prefix=False,
        )


def _group_row(session: Session, *, ref: str | None = None, group_id: int | None = None):
    stmt = select(ConformerGroup.id, ConformerGroup.species_entry_id)
    stmt = stmt.where(
        ConformerGroup.public_ref == ref if ref is not None else ConformerGroup.id == group_id
    )
    return session.execute(stmt).first()


def _assert_group_owned(group, *, field: str, species_entry_id: int) -> None:
    assert_owned_by(
        subject_noun="conformer group",
        row_id=group.id,
        row_species_entry_id=group.species_entry_id,
        row_transition_state_entry_id=None,
        code=W_THERMO_TARGET_GROUP_OWNER_MISMATCH,
        target="thermo",
        context=field,
        species_entry_id=species_entry_id,
    )


def _resolve_target(
    session: Session,
    target: Any,
    *,
    species_entry_id: int,
    conformer_group_ids_by_key: Mapping[str, int] | None,
    field: str,
) -> tuple[ThermoTargetKind | None, int | None]:
    if target is None:
        return None, None
    kind = ThermoTargetKind(target.kind.value if hasattr(target.kind, "value") else target.kind)
    if kind is ThermoTargetKind.equilibrium_ensemble:
        return kind, None

    ref = getattr(target, "conformer_group_ref", None)
    key = getattr(target, "conformer_key", None)
    if ref is not None:
        group = _group_row(session, ref=ref)
        if group is None:
            raise unknown_reference(
                code=W_UNKNOWN_CONFORMER_GROUP_REF,
                field=f"{field}.conformer_group_ref",
                kind="conformer_group",
                ref=ref,
                remedy="Deposit the conformer group first, or correct the ref.",
            )
        _assert_group_owned(
            group, field=f"{field}.conformer_group_ref", species_entry_id=species_entry_id
        )
        return kind, group.id

    if key is None:
        # ``assert_thermo_declaration`` has already refused this; stated here too
        # because this function is also reachable without it.
        raise CodedValueError(
            W_THERMO_TARGET_GROUP_REQUIRED,
            "A single_conformer target must name the conformer group it describes.",
            context={"field": field},
            message_prefix=False,
        )
    group_id = resolve_declared_key(
        key,
        conformer_group_ids_by_key or {},
        field=f"{field}.conformer_key",
        code=W_CONFORMER_KEY_UNDECLARED,
        subject="conformer",
        remedy=_CONFORMER_KEY_REMEDY,
        scope="declared in this bundle",
    )
    group = _group_row(session, group_id=group_id)
    if group is None:  # pragma: no cover - a key map never holds a row that is not there
        raise CodedValueError(
            W_CONFORMER_KEY_UNDECLARED, _CONFORMER_KEY_REMEDY, context={"field": f"{field}.conformer_key"}
        )
    _assert_group_owned(
        group, field=f"{field}.conformer_key", species_entry_id=species_entry_id
    )
    return kind, group.id


def _calculation_by_ref(session: Session, ref: str, *, field: str) -> Calculation:
    calc = session.execute(select(Calculation).where(Calculation.public_ref == ref)).scalar_one_or_none()
    if calc is None:
        raise unknown_reference(
            code=W_UNKNOWN_CALCULATION_REF,
            field=field,
            kind="calculation",
            ref=ref,
            remedy=(
                "Declare the job inline in this request with a calculation_key, or deposit it "
                "first and cite the ref this API returned for it."
            ),
        )
    return calc


def _resolve_protocol(
    session: Session,
    protocol: Any,
    *,
    species_entry_id: int,
    calculations_by_key: Mapping[str, Any] | None,
    field: str,
) -> dict[str, Any] | None:
    if protocol is None:
        return None
    wire = (
        protocol
        if isinstance(protocol, ThermoProtocolDeclaration)
        else ThermoProtocolDeclaration.model_validate(protocol)
    )
    refs: list[str] = []
    for index, supporting in enumerate(wire.supporting_calculations):
        where = f"{field}.supporting_calculations[{index}]"
        if supporting.calculation_key is not None:
            found = resolve_calculation_key(
                supporting.calculation_key,
                calculations_by_key or {},
                field=f"{where}.calculation_key",
            )
            resolved = found if isinstance(found, Calculation) else session.get(Calculation, found)
            context = f"{where}.calculation_key='{supporting.calculation_key}'"
        elif supporting.calculation_ref is not None:
            resolved = _calculation_by_ref(session, supporting.calculation_ref, field=f"{where}.calculation_ref")
            context = f"{where}.calculation_ref"
        else:
            raise CodedValueError(
                W_THERMO_DECLARATION_INVALID,
                "A supporting calculation gives exactly one of calculation_key or calculation_ref.",
                context={"field": where},
                message_prefix=False,
            )
        if resolved is None:  # pragma: no cover - a key map never holds a row that is not there
            raise CodedValueError(
                W_CALCULATION_KEY_UNDECLARED,
                f"{where} names a calculation that is not in this request.",
                context={"field": where},
                message_prefix=False,
            )
        calc = resolved
        assert_calculation_owned_by(
            calc,
            code=W_THERMO_PROTOCOL_CALCULATION_OWNER_MISMATCH,
            target="thermo protocol declaration",
            context=context,
            species_entry_id=species_entry_id,
        )
        if calc.public_ref not in refs:
            refs.append(calc.public_ref)
    return _stored_json(wire, refs)


def _stored_json(wire: ThermoProtocolDeclaration, calculation_refs: list[str]) -> dict[str, Any]:
    """The canonical stored form: absent stays absent, ``departures: []`` stays ``[]``."""
    raw = wire.model_dump(mode="json", exclude_none=True)
    raw.pop("supporting_calculations", None)
    if calculation_refs:
        raw["supporting_calculations"] = [{"calculation_ref": ref} for ref in calculation_refs]
    stored = StoredThermoProtocolDeclaration.model_validate(raw)
    return stored.model_dump(mode="json", exclude_none=True, exclude_defaults=True)


def resolve_thermo_declarations(
    session: Session,
    payload: object,
    *,
    species_entry_id: int,
    conformer_group_ids_by_key: Mapping[str, int] | None = None,
    calculations_by_key: Mapping[str, Any] | None = None,
    field_prefix: str = "",
) -> ResolvedThermoDeclarations:
    """Turn a payload's declarations into the columns they resolve to, checking each.

    The checks are re-derived from the declaration, not assumed from validation:
    a payload built with ``model_construct`` is refused here exactly as a parsed
    one is.

    :param payload: Anything with ``thermodynamic_target`` and ``protocol``
        attributes (the standalone request, or a bundle thermo block).
    :param species_entry_id: The record's own species entry; the group and every
        supporting calculation must belong to it.
    :param conformer_group_ids_by_key: In a bundle, the conformer keys it
        declares and the group each resolved to. Empty on the standalone route.
    :param calculations_by_key: The calculation keys the request declares, to
        the row (or row id) each resolved to.
    :param field_prefix: Prefix for the ``field`` in refusals (``"thermo."``).
    """
    assert_thermo_declaration(payload)
    target = getattr(payload, "thermodynamic_target", None)
    protocol = getattr(payload, "protocol", None)
    kind, group_id = _resolve_target(
        session,
        target,
        species_entry_id=species_entry_id,
        conformer_group_ids_by_key=conformer_group_ids_by_key,
        field=f"{field_prefix}thermodynamic_target",
    )
    stored = _resolve_protocol(
        session,
        protocol,
        species_entry_id=species_entry_id,
        calculations_by_key=calculations_by_key,
        field=f"{field_prefix}protocol",
    )
    return ResolvedThermoDeclarations(kind, group_id, stored)


def assert_thermo_declaration_columns(
    session: Session,
    *,
    species_entry_id: int,
    thermodynamic_target_kind: object,
    target_conformer_group_id: int | None,
    protocol_declaration: object,
) -> dict[str, Any] | None:
    """The last stop before a thermo row is written: check the resolved columns themselves.

    :func:`persist_thermo` calls this on whatever it was handed, so a resolved
    payload built without validation (or by a caller that never went through a
    workflow) is judged the same as one a workflow produced.

    :returns: The protocol declaration in its canonical stored form (``None``
        when there is none): what the caller writes, so a declaration that
        reached here as a loose dict is stored validated, not as handed over.
    :raises CodedValueError: a group without a ``single_conformer`` target, a
        ``single_conformer`` target without a group, a group owned by another
        species entry, a protocol whose version is unsupported or whose shape is
        invalid, or a supporting calculation owned by another species entry.
    """
    kind = (
        ThermoTargetKind(getattr(thermodynamic_target_kind, "value", thermodynamic_target_kind))
        if thermodynamic_target_kind is not None
        else None
    )
    if kind is ThermoTargetKind.single_conformer and target_conformer_group_id is None:
        raise CodedValueError(
            W_THERMO_TARGET_GROUP_REQUIRED,
            "A single_conformer target must name the conformer group it describes.",
            context={"field": "thermodynamic_target"},
            message_prefix=False,
        )
    if kind is not ThermoTargetKind.single_conformer and target_conformer_group_id is not None:
        raise CodedValueError(
            W_THERMO_TARGET_GROUP_NOT_ALLOWED,
            "Only a single_conformer target names a conformer group.",
            context={"field": "thermodynamic_target"},
            message_prefix=False,
        )
    if target_conformer_group_id is not None:
        group = _group_row(session, group_id=target_conformer_group_id)
        if group is None:
            raise unknown_reference(
                code=W_UNKNOWN_CONFORMER_GROUP_REF,
                field="thermodynamic_target",
                kind="conformer_group",
                row_id=target_conformer_group_id,
                remedy="Name a conformer group that exists.",
            )
        _assert_group_owned(group, field="thermodynamic_target", species_entry_id=species_entry_id)

    if protocol_declaration is None:
        return None
    raw = (
        protocol_declaration.model_dump(mode="json")
        if hasattr(protocol_declaration, "model_dump")
        else protocol_declaration
    )
    version = raw.get("version") if isinstance(raw, dict) else None
    if version not in THERMO_PROTOCOL_VERSIONS:
        raise CodedValueError(
            W_THERMO_PROTOCOL_VERSION_UNSUPPORTED,
            f"protocol.version {version!r} is not supported; supported versions: "
            f"{sorted(THERMO_PROTOCOL_VERSIONS)}.",
            context={"field": "protocol.version"},
            message_prefix=False,
        )
    try:
        stored = StoredThermoProtocolDeclaration.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"])
        raise CodedValueError(
            W_THERMO_DECLARATION_INVALID,
            f"protocol.{where}: {first['msg']}" if where else f"protocol: {first['msg']}",
            context={"field": "protocol"},
            message_prefix=False,
        ) from exc
    for index, supporting in enumerate(stored.supporting_calculations):
        calc = _calculation_by_ref(
            session,
            supporting.calculation_ref,
            field=f"protocol.supporting_calculations[{index}].calculation_ref",
        )
        assert_calculation_owned_by(
            calc,
            code=W_THERMO_PROTOCOL_CALCULATION_OWNER_MISMATCH,
            target="thermo protocol declaration",
            context=f"protocol.supporting_calculations[{index}].calculation_ref",
            species_entry_id=species_entry_id,
        )
    return stored.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
