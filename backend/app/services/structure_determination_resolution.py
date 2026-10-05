"""Resolve and persist structure determinations declared on a conformer or transition-state upload.

A declaration (``tckdb_schemas.structure_declarations.StructureDeterminationDeclaration``) is a
source-attributed claim about a geometry, basin or saddle. This module turns it into rows:

* **Owner.** The upload's own: a conformer upload's species entry (and, for a ``conformer_basin``,
  the observation it created), a transition-state upload's transition state entry. A ``conformer_basin``
  needs a species owner and an observation, a ``saddle_point`` a transition-state owner; anything else is
  refused with ``structure_determination_mismatch`` (``context.reason`` ``target``).
* **Sources.** Each pin names a calculation by local key (in this request) or public ref (already
  deposited) and must belong to the owner (``reason`` ``owner``); for a basin, to the owner's observation
  (``reason`` ``observation``). One calculation can play several roles. The calculation the evaluated geometry
  is read from must itself be one of the pinned sources.
* **Evaluated geometry.** The named calculation's one ``output`` or ``input`` geometry. A calculation with no
  geometry on that side, or several, does not pin one and is refused (``reason`` ``geometry``): a Hessian or
  energy is never attached to a geometry by guesswork.
* **The key is an identifier.** The owner (for a basin, its observation), the source attribution and the
  ``determination_key`` name one determination (``uq_structure_determination_key``). Stating the key again with
  the same content resolves to the existing row, with or without an Idempotency-Key, so a repeat is never an
  additional determination; stating it with different content (target kind, quantity, energy convention, recipe,
  evaluated geometry or pinned calculations) is refused (``reason`` ``content``), because a determination is
  immutable and silently merging would re-describe it. A basin is per observation and each conformer upload
  creates a new observation, so a basin claim cannot be restated across uploads; a geometry or saddle claim can,
  over calculations already deposited.

Nothing is inferred. A source row records the geometry its role's result describes only where the
calculation's type makes it unambiguous (a single-point or frequency job's one input geometry, an
optimization's one output geometry) and leaves it NULL otherwise.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tckdb_schemas.local_key_codes import W_CALCULATION_KEY_UNDECLARED, undeclared_key_context
from tckdb_schemas.structure_declarations import (
    W_STRUCTURE_DECLARATION_INVALID,
    StructureCalculationPin,
    StructureDeterminationDeclaration,
    structure_determination_error,
)

from app.api.error_contract import CodedValueError
from app.db.models.calculation import Calculation
from app.db.models.common import (
    CalculationType,
    StructureDeterminationQuantity,
    StructureDeterminationTargetKind,
    StructureSourceRole,
)
from app.db.models.structure_determination import StructureDetermination, StructureDeterminationSource
from app.services.calc_isotopes import assert_isotopes
from app.services.calculation_geometry_composition import assert_calculation_geometry_composition
from app.services.calculation_resolution import resolve_workflow_tool_release_ref
from app.services.literature_resolution import resolve_or_create_literature
from app.services.upload_reference import W_UNKNOWN_CALCULATION_REF, unknown_reference

#: A declaration contradicts the stored facts it names: the target does not fit the owner, a source calculation
#: belongs to another owner or (for a basin) another observation, a pinned calculation has no single geometry on
#: the side named, or the key was already used with different content. ``context['reason']`` names which:
#: ``target``, ``owner``, ``observation``, ``geometry`` or ``content``.
W_STRUCTURE_DETERMINATION_MISMATCH = "structure_determination_mismatch"

#: Bumped only when what goes into ``identity_hash`` or ``content_hash`` changes.
IDENTITY_VERSION = 1

__all__ = [
    "W_STRUCTURE_DETERMINATION_MISMATCH",
    "DeterminationOwner",
    "content_hash",
    "identity_hash",
    "persist_structure_determinations",
]


@dataclass(frozen=True)
class DeterminationOwner:
    """The upload's own subject: exactly one of a species entry or a transition state entry.

    :param conformer_observation_id: The observation the upload created, for a conformer upload.
    """

    species_entry_id: int | None = None
    transition_state_entry_id: int | None = None
    conformer_observation_id: int | None = None

    def __post_init__(self) -> None:
        if (self.species_entry_id is None) == (self.transition_state_entry_id is None):
            raise ValueError("a determination owner is exactly one of a species entry or a transition state entry")
        if self.conformer_observation_id is not None and self.species_entry_id is None:
            raise ValueError("a conformer observation belongs to a species entry")


def _canonical(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def identity_hash(
    *,
    owner: DeterminationOwner,
    target_kind: str,
    literature_id: int | None,
    workflow_tool_release_id: int | None,
    determination_key: str,
) -> str:
    """Digest of the identifier of a determination: owner, source attribution and key.

    It names the same tuple ``uq_structure_determination_key`` makes unique. Only a basin is about an
    observation; a bare geometry or saddle claim is the same claim whichever upload happened to create a
    conformer observation beside it, so the observation enters only for a ``conformer_basin``.
    """
    return _canonical(
        {
            "version": IDENTITY_VERSION,
            "species_entry_id": owner.species_entry_id,
            "transition_state_entry_id": owner.transition_state_entry_id,
            "conformer_observation_id": owner.conformer_observation_id if target_kind == "conformer_basin" else None,
            "literature_id": literature_id,
            "workflow_tool_release_id": workflow_tool_release_id,
            "determination_key": determination_key,
        }
    )


def content_hash(
    *,
    target_kind: str,
    quantity: str | None,
    energy_convention: dict[str, Any] | None,
    actual_recipe: dict[str, Any] | None,
    evaluated_geometry_id: int,
    sources: list[tuple[str, int]],
) -> str:
    """Digest of everything a determination claims beyond its identifier: what a restatement must repeat."""
    return _canonical(
        {
            "version": IDENTITY_VERSION,
            "target_kind": target_kind,
            "quantity": quantity,
            "energy_convention": energy_convention,
            "actual_recipe": actual_recipe,
            "evaluated_geometry_id": evaluated_geometry_id,
            "sources": sorted(sources),
        }
    )


def _mismatch(reason: str, message: str) -> CodedValueError:
    return CodedValueError(
        W_STRUCTURE_DETERMINATION_MISMATCH,
        message,
        context={"reason": reason, "field": "structure_determinations"},
        message_prefix=False,
    )


def _resolve_pin(
    session: Session,
    pin: StructureCalculationPin,
    calculations_by_key: Mapping[str, Calculation],
) -> Calculation:
    if pin.calculation_key is not None:
        row = calculations_by_key.get(pin.calculation_key)
        if row is None:
            raise CodedValueError(
                W_CALCULATION_KEY_UNDECLARED,
                f"structure_determinations names calculation_key {pin.calculation_key!r}, which no calculation "
                "in this request declares. Put a matching 'key' on 'calculation' or on one of "
                "'additional_calculations'.",
                context=undeclared_key_context(
                    field="structure_determinations", key=pin.calculation_key, declared=calculations_by_key
                ),
                message_prefix=False,
            )
        return row
    assert pin.calculation_ref is not None
    row = session.scalar(select(Calculation).where(Calculation.public_ref == pin.calculation_ref))
    if row is None:
        raise unknown_reference(
            code=W_UNKNOWN_CALCULATION_REF,
            field="structure_determinations.calculation_ref",
            kind="calculation",
            ref=pin.calculation_ref,
            remedy="Name a calculation already deposited, by the calc_ ref an earlier upload returned.",
        )
    return row


def _single_geometry(calculation: Calculation, side: str) -> int | None:
    links = calculation.output_geometries if side == "output" else calculation.input_geometries
    ids = {link.geometry_id for link in links}
    return next(iter(ids)) if len(ids) == 1 else None


#: The side of a calculation whose one geometry a source role's result describes, by calculation type. A type not
#: listed (a composite, an IRC, a scan, a path search) names no geometry here: it stays NULL, never a guess.
_ROLE_SIDE: dict[CalculationType, str] = {
    CalculationType.opt: "output",
    CalculationType.sp: "input",
    CalculationType.freq: "input",
}


def _source_geometry_id(calculation: Calculation) -> int | None:
    side = _ROLE_SIDE.get(calculation.type)
    return _single_geometry(calculation, side) if side is not None else None


def _check_owned(calculation: Calculation, owner: DeterminationOwner, target_kind: StructureDeterminationTargetKind) -> None:
    """A pinned or evaluating calculation belongs to the owner and, for a basin, to the owner's observation."""
    if (calculation.species_entry_id, calculation.transition_state_entry_id) != (
        owner.species_entry_id,
        owner.transition_state_entry_id,
    ):
        raise _mismatch(
            "owner",
            "A calculation of a structure determination belongs to the determination's own species entry or "
            "transition state entry; one of the calculations named belongs to another.",
        )
    if (
        target_kind is StructureDeterminationTargetKind.conformer_basin
        and calculation.conformer_observation_id != owner.conformer_observation_id
    ):
        raise _mismatch(
            "observation",
            "A conformer_basin claim is about one observation: every calculation it names is anchored to that "
            "observation. One of the calculations named is anchored to another observation, or to none.",
        )


def _check_target(declaration: StructureDeterminationDeclaration, owner: DeterminationOwner) -> None:
    kind = StructureDeterminationTargetKind(declaration.target_kind.value)
    if kind is StructureDeterminationTargetKind.conformer_basin and owner.conformer_observation_id is None:
        raise _mismatch(
            "target",
            "A conformer_basin determination belongs to a conformer upload, which creates the observation "
            "it is about; use target_kind 'geometry' elsewhere.",
        )
    if kind is StructureDeterminationTargetKind.saddle_point and owner.transition_state_entry_id is None:
        raise _mismatch(
            "target",
            "A saddle_point determination belongs to a transition-state upload; use target_kind 'geometry' "
            "or 'conformer_basin' on a conformer upload.",
        )


def persist_structure_determinations(
    session: Session,
    declarations: list[StructureDeterminationDeclaration],
    *,
    owner: DeterminationOwner,
    calculations_by_key: Mapping[str, Calculation],
    created_by: int | None = None,
) -> list[StructureDetermination]:
    """Find or create the determinations an upload declares; one row per declaration, in order.

    Call after the upload's calculations (and their input and output geometries) exist and are flushed.

    :raises CodedValueError: ``structure_determination_invalid`` (a self-contradicting declaration built without
        request validation), ``structure_determination_mismatch`` (``context.reason``: ``target``, ``owner``,
        ``geometry`` or ``content``), ``calculation_key_undeclared``.
    :raises NotFoundError: ``unknown_calculation_ref``.
    """
    rows: list[StructureDetermination] = []
    seen_identities: set[str] = set()
    for declaration in declarations:
        error = structure_determination_error(declaration)
        if error is not None:
            code, message = error
            raise CodedValueError(code, message, context={"field": "structure_determinations"}, message_prefix=False)
        _check_target(declaration, owner)

        target_kind = StructureDeterminationTargetKind(declaration.target_kind.value)
        pins = [(source.role, _resolve_pin(session, source, calculations_by_key)) for source in declaration.sources]
        evaluating = _resolve_pin(session, declaration.evaluated_geometry, calculations_by_key)
        for calculation in (*(c for _, c in pins), evaluating):
            _check_owned(calculation, owner, target_kind)
        if evaluating.id not in {c.id for _, c in pins}:
            raise _mismatch(
                "geometry",
                "The evaluated_geometry names a calculation the determination does not pin: the geometry it is "
                "about is read from one of its own sources.",
            )
        evaluated_geometry_id = _single_geometry(evaluating, declaration.evaluated_geometry.side.value)
        if evaluated_geometry_id is None:
            raise _mismatch(
                "geometry",
                f"The evaluated_geometry calculation has no single {declaration.evaluated_geometry.side.value} "
                "geometry, so it does not pin one; name a calculation with exactly one geometry on that side.",
            )

        literature_id = (
            resolve_or_create_literature(session, declaration.literature).id
            if declaration.literature is not None
            else None
        )
        release = resolve_workflow_tool_release_ref(session, declaration.workflow_tool_release)
        workflow_tool_release_id = release.id if release is not None else None
        if literature_id is None and workflow_tool_release_id is None:  # pragma: no cover - the rule above refuses it
            raise CodedValueError(
                W_STRUCTURE_DECLARATION_INVALID,
                "A determination states its source attribution.",
                context={"field": "structure_determinations"},
                message_prefix=False,
            )

        recipe = (
            declaration.actual_recipe.model_dump(mode="json", exclude_none=True)
            if declaration.actual_recipe is not None
            else None
        )
        convention = (
            declaration.energy_convention.model_dump(mode="json", exclude_none=True)
            if declaration.energy_convention is not None
            else None
        )
        quantity = declaration.quantity.value if declaration.quantity is not None else None
        ident = identity_hash(
            owner=owner,
            target_kind=target_kind.value,
            literature_id=literature_id,
            workflow_tool_release_id=workflow_tool_release_id,
            determination_key=declaration.key,
        )
        if ident in seen_identities:
            raise _mismatch("content", "One request states a determination key once; a repeated key is refused.")
        seen_identities.add(ident)
        content = content_hash(
            target_kind=target_kind.value,
            quantity=quantity,
            energy_convention=convention,
            actual_recipe=recipe,
            evaluated_geometry_id=evaluated_geometry_id,
            sources=[(role.value, calculation.id) for role, calculation in pins],
        )

        existing = session.scalar(select(StructureDetermination).where(StructureDetermination.identity_hash == ident))
        if existing is not None:
            if existing.content_hash != content:
                raise _mismatch(
                    "content",
                    "This determination key was already stated, for this subject and source, with different "
                    "content (target kind, quantity, energy convention, recipe, evaluated geometry or pinned "
                    "calculations). A determination is immutable: state a different key for a different claim.",
                )
            rows.append(existing)
            continue

        row = StructureDetermination(
            species_entry_id=owner.species_entry_id,
            transition_state_entry_id=owner.transition_state_entry_id,
            conformer_observation_id=(
                owner.conformer_observation_id
                if target_kind is StructureDeterminationTargetKind.conformer_basin
                else None
            ),
            target_kind=target_kind,
            quantity=StructureDeterminationQuantity(quantity) if quantity is not None else None,
            evaluated_geometry_id=evaluated_geometry_id,
            literature_id=literature_id,
            workflow_tool_release_id=workflow_tool_release_id,
            determination_key=declaration.key,
            energy_convention=convention,
            actual_recipe=recipe,
            identity_hash=ident,
            content_hash=content,
            created_by=created_by,
        )
        try:
            with session.begin_nested():
                session.add(row)
                session.flush()
                # The database refuses a source that is not pinned in the transaction that creates its
                # determination; this says that it is (see ``tckdb_structure_source_insert_guard``).
                session.execute(
                    text("SELECT set_config('tckdb.structure_determination_writing', :id, true)"),
                    {"id": str(row.id)},
                )
                for role, calculation in pins:
                    source_geometry_id = _source_geometry_id(calculation)
                    if source_geometry_id is not None:
                        # The geometry the calculation's own links name was composition-checked when it was linked;
                        # a source repeats it, and the check is repeated here so no write site of a
                        # geometry-bearing table is exempt (a row that names a geometry it does not own would
                        # otherwise be one more place to get it wrong).
                        assert_calculation_geometry_composition(
                            session,
                            calc=calculation,
                            geometry_id=source_geometry_id,
                            field=f"structure_determinations['{declaration.key}']",
                        )
                        assert_isotopes(
                            session,
                            calc=calculation,
                            geometry_id=source_geometry_id,
                            field=f"structure_determinations['{declaration.key}']",
                        )
                    session.add(
                        StructureDeterminationSource(
                            determination_id=row.id,
                            role=StructureSourceRole(role.value),
                            calculation_id=calculation.id,
                            species_entry_id=owner.species_entry_id,
                            transition_state_entry_id=owner.transition_state_entry_id,
                            conformer_observation_id=row.conformer_observation_id,
                            geometry_id=source_geometry_id,
                        )
                    )
                session.flush()
                session.execute(text("SELECT set_config('tckdb.structure_determination_writing', '', true)"))
        except IntegrityError:
            # A concurrent upload created the same content first; it is the one row.
            winner = session.scalar(
                select(StructureDetermination).where(StructureDetermination.identity_hash == ident)
            )
            if winner is None:  # pragma: no cover - an IntegrityError that is not the hash
                raise
            if winner.content_hash != content:
                raise _mismatch("content", "This determination key was already used with different content.") from None
            row = winner
        rows.append(row)
    return rows
