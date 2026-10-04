"""Resolve and enforce a kinetics record's determination, applicability and protocol.

A depositor may declare, on a kinetics record, which determination of the rate it is a
representation of (``determination``), what the stored coefficient is a coefficient of
(``applicability``) and how the rate was produced (``protocol``). The wire shapes and the
self-contradiction rules live in :mod:`tckdb_schemas.kinetics_declarations`. This module is the
half that needs a database:

* a determination stated by content (``key`` and ``target``) is found or created from the
  record's reaction entry, direction, the target (a transition-state entry of that reaction, or
  a network channel that belongs to it), the record's own source attribution and the key; a
  determination cited by ``determination_ref`` is joined only when it states the same reaction
  entry, direction and source attribution as the record;
* an applicability declaration's colliders are resolved to species public refs, and what is
  *stored* carries refs only;
* a protocol's supporting calculations name calculations by local key or public ref; each must
  belong to a participant of the record's reaction (or to its transition state), and what is
  stored is the calculation's public ref, never the key and never an id.

Three layers, none of which may be removed because the others exist (ADR 0017): the request
schema refuses a self-contradicting declaration first; the workflow calls
:func:`resolve_kinetics_declarations`, which re-derives every check from the declaration itself
rather than trusting that validation ran (a payload built with ``model_construct`` skips every
validator); and the writers of a ``kinetics`` row call :func:`assert_kinetics_declaration_columns`
on the resolved columns, the last stop before the row is written, for callers that never go
through a workflow at all.

Declarations are attributed claims. Nothing here infers a determination, an applicability or a
protocol from a model kind, a direction or a source-calculation link, defaults a missing one, or
checks a protocol's claims against the linked calculations; that is later, separate work.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tckdb_schemas.bundle_source_rules import owner_mismatch_context, owner_mismatch_detail
from tckdb_schemas.kinetics_declarations import (
    KINETICS_APPLICABILITY_VERSIONS,
    KINETICS_PROTOCOL_VERSIONS,
    W_KINETICS_DECLARATION_CONTRADICTS_RECORD,
    W_KINETICS_DECLARATION_INVALID,
    W_KINETICS_DECLARATION_VERSION_UNSUPPORTED,
    W_KINETICS_DETERMINATION_INVALID,
    KineticsApplicabilityDeclaration,
    KineticsProtocolDeclaration,
    KineticsRecordFacts,
    StoredKineticsApplicabilityDeclaration,
    StoredKineticsProtocolDeclaration,
    kinetics_applicability_error,
    kinetics_declaration_context,
    kinetics_declaration_error,
    kinetics_protocol_origin_error,
    kinetics_record_facts,
    version_is_supported,
)
from tckdb_schemas.local_key_codes import W_CALCULATION_KEY_UNDECLARED

from app.api.error_contract import CodedValueError
from app.db.models.calculation import Calculation
from app.db.models.common import (
    KineticsDeterminationTargetKind,
    KineticsDirection,
    KineticsRepresentationRole,
    ReactionRole,
)
from app.db.models.kinetics import KineticsDetermination
from app.db.models.network import Network
from app.db.models.network_pdep import (
    NetworkChannel,
    NetworkChannelMicroReaction,
    NetworkKinetics,
)
from app.db.models.reaction import ReactionEntry, ReactionEntryStructureParticipant
from app.db.models.transition_state import TransitionStateEntry
from app.services.kinetics_ts_scope import TransitionStateScope, transition_state_belongs_to_rate
from app.services.local_key_resolution import resolve_calculation_key
from app.services.species_resolution import resolve_species
from app.services.upload_reference import (
    W_UNKNOWN_CALCULATION_REF,
    W_UNKNOWN_KINETICS_DETERMINATION_REF,
    W_UNKNOWN_NETWORK_CHANNEL,
    W_UNKNOWN_TRANSITION_STATE_ENTRY_REF,
    unknown_reference,
)

logger = logging.getLogger(__name__)

#: A record cites a determination that is not of its own reaction entry, direction or source, or
#: names a channel target (a transition state or network channel) that does not belong to its
#: reaction. One code, with ``context['reason']`` naming which differs (``reaction``,
#: ``direction``, ``source`` or ``target``): the repair is the same, to state what this record
#: states. Distinct from an unknown ref: the row exists.
W_KINETICS_DETERMINATION_MISMATCH = "kinetics_determination_mismatch"


#: A protocol's supporting calculation belongs to something other than a participant of the
#: record's reaction or its transition state.
W_KINETICS_PROTOCOL_CALCULATION_OWNER_MISMATCH = "kinetics_protocol_calculation_owner_mismatch"

#: Bumped only when what goes into ``identity_hash`` changes.
IDENTITY_VERSION = 1

__all__ = [
    "W_KINETICS_DETERMINATION_MISMATCH",
    "W_KINETICS_PROTOCOL_CALCULATION_OWNER_MISMATCH",
    "ResolvedKineticsDeclarations",
    "assert_kinetics_declaration",
    "assert_kinetics_declaration_columns",
    "determination_identity_hash",
    "resolve_kinetics_declarations",
]


@dataclass(frozen=True)
class ResolvedKineticsDeclarations:
    """The four ``kinetics`` columns a declaration resolves to. All ``None`` when none was made."""

    determination_id: int | None = None
    representation_role: KineticsRepresentationRole | None = None
    applicability_declaration: dict[str, Any] | None = None
    protocol_declaration: dict[str, Any] | None = None


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _attr(obj: Any, name: str) -> Any:
    """``obj.name`` for a model, a namespace or a plain dict: a declaration may arrive as any."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def assert_kinetics_declaration(payload: object, *, has_source: bool | None = None) -> None:
    """Refuse a self-contradicting declaration on a whole kinetics payload.

    Runs the shared producer rule on the payload as it stands, so a payload assembled without
    validation is judged the same as a validated one.
    """
    error = kinetics_declaration_error(payload, has_source=has_source)
    if error is not None:
        code, message = error
        raise CodedValueError(
            code, message, context=kinetics_declaration_context(code, message), message_prefix=False
        )


# ---------------------------------------------------------------------------
# Determination
# ---------------------------------------------------------------------------


def determination_identity_hash(
    *,
    reaction_entry_id: int,
    direction: str,
    target_kind: str,
    target_transition_state_entry_id: int | None,
    target_network_channel_id: int | None,
    literature_id: int | None,
    workflow_tool_release_id: int | None,
    determination_key: str,
) -> str:
    """Digest of exactly the content a determination's identity is made of.

    The record's fitting software is deliberately absent: an Arrhenius fit and a Chebyshev fit
    of one determination may come from different fitting tools, and that must not split them.
    """
    canonical = json.dumps(
        {
            "version": IDENTITY_VERSION,
            "reaction_entry_id": reaction_entry_id,
            "direction": direction,
            "target_kind": target_kind,
            "target_transition_state_entry_id": target_transition_state_entry_id,
            "target_network_channel_id": target_network_channel_id,
            "literature_id": literature_id,
            "workflow_tool_release_id": workflow_tool_release_id,
            "determination_key": determination_key,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _not_owned(field: str, what: str) -> CodedValueError:
    return CodedValueError(
        W_KINETICS_DETERMINATION_MISMATCH,
        f"{field} names {what} that does not belong to this record's reaction.",
        context={"field": field, "reason": "target"},
        message_prefix=False,
    )


def _resolve_target(
    session: Session,
    target: Any,  # the determination declaration: it carries target_kind and the locators
    *,
    reaction_entry: ReactionEntry,
    ts_scope: TransitionStateScope,
    network_kinetics_id: int | None,
    field: str,
) -> tuple[KineticsDeterminationTargetKind, int | None, int | None]:
    kind = KineticsDeterminationTargetKind(_enum_value(_attr(target, "target_kind")))
    if kind is KineticsDeterminationTargetKind.whole_reaction:
        return kind, None, None

    ts_ref = _attr(target, "transition_state_entry_ref")
    network_ref = _attr(target, "network_ref")
    channel_key = _attr(target, "channel_key")
    if ts_ref is not None:
        ts_entry_id = session.scalar(
            select(TransitionStateEntry.id).where(TransitionStateEntry.public_ref == ts_ref)
        )
        if ts_entry_id is None:
            raise unknown_reference(
                code=W_UNKNOWN_TRANSITION_STATE_ENTRY_REF,
                field=f"{field}.transition_state_entry_ref",
                kind="transition_state_entry",
                ref=ts_ref,
                remedy="Deposit the transition state first, or correct the ref.",
            )
        if not transition_state_belongs_to_rate(session, ts_entry_id, reaction_entry, ts_scope):
            logger.warning(
                "%s: transition_state_entry id=%s is not a transition state of reaction_entry id=%s",
                field,
                ts_entry_id,
                reaction_entry.id,
            )
            raise _not_owned(f"{field}.transition_state_entry_ref", "a transition state")
        return kind, ts_entry_id, None

    network_id = session.scalar(select(Network.id).where(Network.public_ref == network_ref))
    channel_id = (
        session.scalar(
            select(NetworkChannel.id).where(
                NetworkChannel.network_id == network_id,
                NetworkChannel.channel_key == channel_key,
            )
        )
        if network_id is not None
        else None
    )
    if channel_id is None:
        raise unknown_reference(
            code=W_UNKNOWN_NETWORK_CHANNEL,
            field=f"{field}.network_ref",
            kind="network_channel",
            ref=network_ref,
            remedy=(
                "A network channel is named by (network ref, channel key) and no stored "
                "channel matches. Deposit the network first, or correct either half."
            ),
            channel_key=channel_key,
        )
    if network_kinetics_id is not None:
        linked = session.scalar(
            select(NetworkKinetics.channel_id).where(NetworkKinetics.id == network_kinetics_id)
        )
        owned = linked == channel_id
    else:
        owned = (
            session.scalar(
                select(NetworkChannelMicroReaction.id)
                .join(
                    ReactionEntry,
                    ReactionEntry.id == NetworkChannelMicroReaction.reaction_entry_id,
                )
                .where(
                    NetworkChannelMicroReaction.channel_id == channel_id,
                    ReactionEntry.reaction_id == reaction_entry.reaction_id,
                )
                .limit(1)
            )
            is not None
        )
    if not owned:
        logger.warning(
            "%s: network_channel id=%s is not a channel of reaction_entry id=%s",
            field,
            channel_id,
            reaction_entry.id,
        )
        raise _not_owned(f"{field}.network_ref", "a network channel")
    return kind, None, channel_id


def _mismatch(field: str, reason: str, message: str) -> CodedValueError:
    return CodedValueError(
        W_KINETICS_DETERMINATION_MISMATCH,
        message,
        context={"field": field, "reason": reason},
        message_prefix=False,
    )


def _check_joinable(
    determination: KineticsDetermination,
    *,
    reaction_entry_id: int,
    direction: str,
    literature_id: int | None,
    workflow_tool_release_id: int | None,
    field: str,
) -> None:
    if determination.reaction_entry_id != reaction_entry_id:
        logger.warning(
            "%s: determination id=%s is of reaction_entry id=%s, not %s",
            field,
            determination.id,
            determination.reaction_entry_id,
            reaction_entry_id,
        )
        raise _mismatch(
            field,
            "reaction",
            "The determination is of a different reaction entry than this record's. A record "
            "joins only a determination of its own reaction entry.",
        )
    if _enum_value(determination.direction) != direction:
        raise _mismatch(
            field,
            "direction",
            f"The determination is of the {_enum_value(determination.direction)} direction but "
            f"this record's direction is {direction}.",
        )
    if (
        determination.literature_id != literature_id
        or determination.workflow_tool_release_id != workflow_tool_release_id
    ):
        raise _mismatch(
            field,
            "source",
            "The determination names a different source (literature, workflow-tool release) "
            "than this record. A record joins only a determination with its own source "
            "attribution.",
        )


def _find_or_create_determination(
    session: Session,
    *,
    reaction_entry_id: int,
    direction: str,
    target: tuple[KineticsDeterminationTargetKind, int | None, int | None],
    literature_id: int | None,
    workflow_tool_release_id: int | None,
    key: str,
    created_by: int | None,
) -> KineticsDetermination:
    kind, ts_id, channel_id = target
    identity_hash = determination_identity_hash(
        reaction_entry_id=reaction_entry_id,
        direction=direction,
        target_kind=kind.value,
        target_transition_state_entry_id=ts_id,
        target_network_channel_id=channel_id,
        literature_id=literature_id,
        workflow_tool_release_id=workflow_tool_release_id,
        determination_key=key,
    )
    existing = session.scalar(
        select(KineticsDetermination).where(KineticsDetermination.identity_hash == identity_hash)
    )
    if existing is not None:
        return existing
    row = KineticsDetermination(
        reaction_entry_id=reaction_entry_id,
        direction=KineticsDirection(direction),
        target_kind=kind,
        target_transition_state_entry_id=ts_id,
        target_network_channel_id=channel_id,
        literature_id=literature_id,
        workflow_tool_release_id=workflow_tool_release_id,
        determination_key=key,
        identity_hash=identity_hash,
        created_by=created_by,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        # A concurrent upload created the same content first; it is the one row.
        winner = session.scalar(
            select(KineticsDetermination).where(KineticsDetermination.identity_hash == identity_hash)
        )
        if winner is None:  # pragma: no cover - an IntegrityError that is not the hash
            raise
        return winner
    return row


def _resolve_determination(
    session: Session,
    determination: Any,
    *,
    facts: KineticsRecordFacts,
    reaction_entry: ReactionEntry,
    literature_id: int | None,
    workflow_tool_release_id: int | None,
    network_kinetics_id: int | None,
    ts_scope: TransitionStateScope,
    created_by: int | None,
    field: str,
) -> KineticsDetermination:
    ref = _attr(determination, "determination_ref")
    key = _attr(determination, "key")
    direction = facts.direction
    if direction is None:  # pragma: no cover - assert_kinetics_declaration refused it first
        raise CodedValueError(
            W_KINETICS_DETERMINATION_INVALID,
            "A record that joins a determination states its direction.",
            context={"field": field, "missing": "direction"},
            message_prefix=False,
        )
    if ref is not None:
        row = session.scalar(
            select(KineticsDetermination).where(KineticsDetermination.public_ref == ref)
        )
        if row is None:
            raise unknown_reference(
                code=W_UNKNOWN_KINETICS_DETERMINATION_REF,
                field=f"{field}.determination_ref",
                kind="kinetics_determination",
                ref=ref,
                remedy=(
                    "Cite the ref this API returned for the determination, or state its "
                    "content with key and target."
                ),
            )
        _check_joinable(
            row,
            reaction_entry_id=reaction_entry.id,
            direction=direction,
            literature_id=literature_id,
            workflow_tool_release_id=workflow_tool_release_id,
            field=f"{field}.determination_ref",
        )
        return row
    target = _resolve_target(
        session,
        determination,
        reaction_entry=reaction_entry,
        ts_scope=ts_scope,
        network_kinetics_id=network_kinetics_id,
        field=field,
    )
    return _find_or_create_determination(
        session,
        reaction_entry_id=reaction_entry.id,
        direction=direction,
        target=target,
        literature_id=literature_id,
        workflow_tool_release_id=workflow_tool_release_id,
        key=key,
        created_by=created_by,
    )


# ---------------------------------------------------------------------------
# Applicability
# ---------------------------------------------------------------------------


def _species_ref_of(session: Session, holder: Any) -> str:
    """The public ref of the species a collider names, created from its content when new."""
    return resolve_species(session, _attr(holder, "species")).public_ref


def _resolve_applicability(
    session: Session,
    applicability: Any,
    *,
    determination_target_kind: KineticsDeterminationTargetKind | None,
    field: str,
) -> dict[str, Any]:
    wire = (
        applicability
        if isinstance(applicability, KineticsApplicabilityDeclaration)
        else KineticsApplicabilityDeclaration.model_validate(applicability)
    )
    raw = wire.model_dump(mode="json", exclude_none=True)
    raw.pop("colliders", None)
    stored_colliders: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(wire.colliders):
        where = f"{field}.colliders[{index}]"
        ref = _species_ref_of(session, item)
        if ref in seen:
            raise CodedValueError(
                W_KINETICS_DECLARATION_INVALID,
                f"{where} names a species the mixture already lists; a mixture component is "
                "listed once.",
                context={"field": where},
                message_prefix=False,
            )
        seen.add(ref)
        stored: dict[str, Any] = {"species_ref": ref}
        if item.mole_fraction is not None:
            stored["mole_fraction"] = item.mole_fraction
        stored_colliders.append(stored)
    if stored_colliders:
        raw["colliders"] = stored_colliders
    stored_wire = StoredKineticsApplicabilityDeclaration.model_validate(raw)
    return stored_wire.model_dump(mode="json", exclude_none=True, exclude_defaults=True)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


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


def _participant_species_entry_ids(session: Session, reaction_entry_id: int) -> set[int]:
    return set(
        session.scalars(
            select(ReactionEntryStructureParticipant.species_entry_id).where(
                ReactionEntryStructureParticipant.reaction_entry_id == reaction_entry_id
            )
        )
    )


def _assert_calculation_of_reaction(
    session: Session,
    calc: Calculation,
    *,
    reaction_entry: ReactionEntry,
    participants: set[int],
    ts_scope: TransitionStateScope,
    context: str,
) -> None:
    """A supporting calculation belongs to a participant of the reaction, or to its transition state."""
    if calc.species_entry_id is not None and calc.species_entry_id in participants:
        return
    if calc.transition_state_entry_id is not None and transition_state_belongs_to_rate(
        session, calc.transition_state_entry_id, reaction_entry, ts_scope
    ):
        return
    logger.warning(
        "%s: calculation id=%s (species_entry_id=%s, transition_state_entry_id=%s) is not of "
        "reaction_entry id=%s",
        context,
        calc.id,
        calc.species_entry_id,
        calc.transition_state_entry_id,
        reaction_entry.id,
    )
    raise CodedValueError(
        W_KINETICS_PROTOCOL_CALCULATION_OWNER_MISMATCH,
        owner_mismatch_detail(
            context=context,
            subject_noun="calculation",
            owner_noun="reaction participant or transition state",
            target="kinetics protocol declaration",
        ),
        context=owner_mismatch_context(
            field=context,
            target="kinetics protocol declaration",
            owner_noun="reaction participant or transition state",
        ),
        message_prefix=False,
    )


def _resolve_protocol(
    session: Session,
    protocol: Any,
    *,
    reaction_entry: ReactionEntry,
    ts_scope: TransitionStateScope,
    calculations_by_key: Mapping[str, Any] | None,
    field: str,
) -> dict[str, Any]:
    wire = (
        protocol
        if isinstance(protocol, KineticsProtocolDeclaration)
        else KineticsProtocolDeclaration.model_validate(protocol)
    )
    participants = _participant_species_entry_ids(session, reaction_entry.id)
    stored_calcs: list[dict[str, str]] = []
    for index, supporting in enumerate(wire.supporting_calculations):
        where = f"{field}.supporting_calculations[{index}]"
        if supporting.calculation_key is not None:
            found = resolve_calculation_key(
                supporting.calculation_key,
                calculations_by_key or {},
                field=f"{where}.calculation_key",
            )
            calc = found if isinstance(found, Calculation) else session.get(Calculation, found)
            context = f"{where}.calculation_key='{supporting.calculation_key}'"
        else:
            calc = _calculation_by_ref(session, supporting.calculation_ref or "", field=f"{where}.calculation_ref")
            context = f"{where}.calculation_ref"
        if calc is None:  # pragma: no cover - a key map never holds a row that is not there
            raise CodedValueError(
                W_CALCULATION_KEY_UNDECLARED,
                f"{where} names a calculation that is not in this request.",
                context={"field": where},
                message_prefix=False,
            )
        _assert_calculation_of_reaction(
            session,
            calc,
            reaction_entry=reaction_entry,
            participants=participants,
            ts_scope=ts_scope,
            context=context,
        )
        entry = {"calculation_ref": calc.public_ref, "purpose": supporting.purpose.value}
        if entry not in stored_calcs:
            stored_calcs.append(entry)
    return _stored_protocol(wire, stored_calcs)


def _stored_protocol(wire: KineticsProtocolDeclaration, calcs: list[dict[str, str]]) -> dict[str, Any]:
    """The canonical stored form: absent stays absent, ``departures: []`` stays ``[]``."""
    raw = wire.model_dump(mode="json", exclude_none=True)
    raw.pop("supporting_calculations", None)
    if calcs:
        raw["supporting_calculations"] = calcs
    stored = StoredKineticsProtocolDeclaration.model_validate(raw)
    return stored.model_dump(mode="json", exclude_none=True, exclude_defaults=True)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def resolve_kinetics_declarations(
    session: Session,
    payload: object,
    *,
    reaction_entry: ReactionEntry,
    literature_id: int | None,
    workflow_tool_release_id: int | None,
    network_kinetics_id: int | None = None,
    ts_scope: TransitionStateScope = "entry",
    calculations_by_key: Mapping[str, Any] | None = None,
    created_by: int | None = None,
    field_prefix: str = "",
) -> ResolvedKineticsDeclarations:
    """Turn a payload's declarations into the columns they resolve to, checking each.

    The checks are re-derived from the declaration, not assumed from validation: a payload
    built with ``model_construct`` is refused here exactly as a parsed one is.

    :param payload: Anything with ``determination``, ``applicability`` and ``protocol``
        attributes (the standalone request, or a bundle kinetics block).
    :param reaction_entry: The record's own reaction entry; the determination, a channel
        target and every supporting calculation must belong to it.
    :param literature_id: The record's resolved literature, one half of its source attribution.
    :param workflow_tool_release_id: The record's resolved workflow-tool release, the other half.
    :param network_kinetics_id: The record's resolved network counterpart, when it has one.
    :param ts_scope: ``"entry"`` on the standalone route, ``"reaction"`` in a bundle.
    :param calculations_by_key: The calculation keys the request declares, to the row (or row
        id) each resolved to.
    :param field_prefix: Prefix for the ``field`` in refusals (``"kinetics[0]."``).
    """
    has_source = literature_id is not None or workflow_tool_release_id is not None
    assert_kinetics_declaration(payload, has_source=has_source)
    facts = kinetics_record_facts(payload)
    determination_payload = _attr(payload, "determination")
    applicability_payload = _attr(payload, "applicability")
    protocol_payload = _attr(payload, "protocol")

    determination_row: KineticsDetermination | None = None
    role: KineticsRepresentationRole | None = None
    if determination_payload is not None:
        determination_row = _resolve_determination(
            session,
            determination_payload,
            facts=facts,
            reaction_entry=reaction_entry,
            literature_id=literature_id,
            workflow_tool_release_id=workflow_tool_release_id,
            network_kinetics_id=network_kinetics_id,
            ts_scope=ts_scope,
            created_by=created_by,
            field=f"{field_prefix}determination",
        )
        role = KineticsRepresentationRole(_enum_value(_attr(determination_payload, "representation_role")))

    applicability_stored = None
    if applicability_payload is not None:
        target_kind = determination_row.target_kind if determination_row is not None else None
        scope = _attr(applicability_payload, "scope")
        if (
            determination_payload is not None
            and _attr(determination_payload, "determination_ref") is not None
            and scope is not None
            and _enum_value(scope) != _enum_value(target_kind)
        ):
            # A key-stated determination was already compared in the shared rule; a cited
            # one's target is only known now that the row is read.
            raise CodedValueError(
                W_KINETICS_DECLARATION_CONTRADICTS_RECORD,
                f"applicability.scope: declares scope {_enum_value(scope)!r} but the "
                f"determination's target is {_enum_value(target_kind)!r}.",
                context={"field": f"{field_prefix}applicability.scope"},
                message_prefix=False,
            )
        applicability_stored = _resolve_applicability(
            session,
            applicability_payload,
            determination_target_kind=target_kind,
            field=f"{field_prefix}applicability",
        )

    protocol_stored = None
    if protocol_payload is not None:
        protocol_stored = _resolve_protocol(
            session,
            protocol_payload,
            reaction_entry=reaction_entry,
            ts_scope=ts_scope,
            calculations_by_key=calculations_by_key,
            field=f"{field_prefix}protocol",
        )
    return ResolvedKineticsDeclarations(
        determination_id=determination_row.id if determination_row is not None else None,
        representation_role=role,
        applicability_declaration=applicability_stored,
        protocol_declaration=protocol_stored,
    )


def assert_kinetics_declaration_columns(
    session: Session,
    *,
    reaction_entry_id: int,
    direction: object,
    scientific_origin: object,
    model_kind: object,
    is_third_body: bool,
    pressure_context: object,
    pressure_bar: float | None,
    literature_id: int | None,
    workflow_tool_release_id: int | None,
    network_kinetics_id: int | None,
    determination_id: int | None,
    representation_role: object,
    applicability_declaration: object,
    protocol_declaration: object,
    ts_scope: TransitionStateScope = "entry",
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The last stop before a kinetics row is written: check the resolved columns themselves.

    The writers of a ``kinetics`` row call this on whatever they were handed, so a resolved
    payload built without validation (or by a caller that never went through a workflow) is
    judged the same as one a workflow produced.

    :returns: ``(applicability, protocol)`` in their canonical stored forms (``None`` when
        absent): what the caller writes, so a declaration that reached here as a loose dict is
        stored validated, not as handed over.
    :raises CodedValueError: a determination without a role or a role without a determination;
        a determination that is not of this record's reaction entry, direction and source; a
        version that is unsupported or a shape that is invalid; an applicability declaration
        that contradicts the record; a method that contradicts the record's origin; or a
        supporting calculation that is not of this record's reaction.
    """
    if (determination_id is None) != (representation_role is None):
        raise CodedValueError(
            W_KINETICS_DECLARATION_INVALID,
            "A kinetics record states its determination and its role in it together: "
            "determination and representation_role are both present or both absent.",
            context={"field": "determination"},
            message_prefix=False,
        )
    direction_value = _enum_value(direction)
    determination_row: KineticsDetermination | None = None
    if determination_id is not None:
        determination_row = session.get(KineticsDetermination, determination_id)
        if determination_row is None:
            raise unknown_reference(
                code=W_UNKNOWN_KINETICS_DETERMINATION_REF,
                field="determination",
                kind="kinetics_determination",
                row_id=determination_id,
                remedy="Name a determination that exists.",
            )
        if direction_value is None:
            raise CodedValueError(
                W_KINETICS_DETERMINATION_INVALID,
                "A record that joins a determination states its direction.",
                context={"field": "determination", "missing": "direction"},
                message_prefix=False,
            )
        if literature_id is None and workflow_tool_release_id is None:
            raise CodedValueError(
                W_KINETICS_DETERMINATION_INVALID,
                "A record that joins a determination names its source: literature or "
                "workflow_tool_release.",
                context={"field": "determination", "missing": "source"},
                message_prefix=False,
            )
        _check_joinable(
            determination_row,
            reaction_entry_id=reaction_entry_id,
            direction=direction_value,
            literature_id=literature_id,
            workflow_tool_release_id=workflow_tool_release_id,
            field="determination",
        )

    reaction_entry = session.get(ReactionEntry, reaction_entry_id)
    facts = KineticsRecordFacts(
        direction=direction_value,
        scientific_origin=_enum_value(scientific_origin),
        model_kind=_enum_value(model_kind),
        is_third_body=is_third_body,
        n_reactants=(
            len(
                list(
                    session.scalars(
                        select(ReactionEntryStructureParticipant.id).where(
                            ReactionEntryStructureParticipant.reaction_entry_id == reaction_entry_id,
                            ReactionEntryStructureParticipant.role == ReactionRole.reactant,
                        )
                    )
                )
            )
            or None
        ),
        pressure_context=_enum_value(pressure_context),
        pressure_bar=pressure_bar,
        has_network_kinetics=network_kinetics_id is not None,
        # The child rows are written after the parent, so what is known here is what the
        # model kind promises; efficiencies are unknown at this stop and their rule is skipped.
        has_falloff=_enum_value(model_kind) in {"lindemann", "troe", "sri"},
        has_third_body_efficiencies=None,
    )

    applicability_stored = None
    if applicability_declaration is not None:
        raw = (
            applicability_declaration.model_dump(mode="json")
            if hasattr(applicability_declaration, "model_dump")
            else applicability_declaration
        )
        version = raw.get("version") if isinstance(raw, dict) else None
        if not version_is_supported(version, KINETICS_APPLICABILITY_VERSIONS):
            raise CodedValueError(
                W_KINETICS_DECLARATION_VERSION_UNSUPPORTED,
                f"applicability.version {version!r} is not supported; supported versions: "
                f"{sorted(KINETICS_APPLICABILITY_VERSIONS)}.",
                context={"field": "applicability.version"},
                message_prefix=False,
            )
        try:
            stored = StoredKineticsApplicabilityDeclaration.model_validate(raw)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            raise CodedValueError(
                W_KINETICS_DECLARATION_INVALID,
                f"applicability.{where}: {first['msg']}" if where else f"applicability: {first['msg']}",
                context={"field": "applicability"},
                message_prefix=False,
            ) from exc
        contradiction = kinetics_applicability_error(
            stored,
            facts,
            determination_target_kind=(
                determination_row.target_kind if determination_row is not None else None
            ),
        )
        if contradiction is not None:
            code, message = contradiction
            raise CodedValueError(
                code, message, context={"field": "applicability"}, message_prefix=False
            )
        applicability_stored = stored.model_dump(mode="json", exclude_none=True, exclude_defaults=True)

    protocol_stored = None
    if protocol_declaration is not None:
        raw = (
            protocol_declaration.model_dump(mode="json")
            if hasattr(protocol_declaration, "model_dump")
            else protocol_declaration
        )
        version = raw.get("version") if isinstance(raw, dict) else None
        if not version_is_supported(version, KINETICS_PROTOCOL_VERSIONS):
            raise CodedValueError(
                W_KINETICS_DECLARATION_VERSION_UNSUPPORTED,
                f"protocol.version {version!r} is not supported; supported versions: "
                f"{sorted(KINETICS_PROTOCOL_VERSIONS)}.",
                context={"field": "protocol.version"},
                message_prefix=False,
            )
        try:
            stored_protocol = StoredKineticsProtocolDeclaration.model_validate(raw)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(part) for part in first["loc"])
            raise CodedValueError(
                W_KINETICS_DECLARATION_INVALID,
                f"protocol.{where}: {first['msg']}" if where else f"protocol: {first['msg']}",
                context={"field": "protocol"},
                message_prefix=False,
            ) from exc
        origin = kinetics_protocol_origin_error(stored_protocol, facts)
        if origin is not None:
            code, message = origin
            raise CodedValueError(
                code, message, context={"field": "protocol.method"}, message_prefix=False
            )
        assert reaction_entry is not None
        participants = _participant_species_entry_ids(session, reaction_entry_id)
        for index, supporting in enumerate(stored_protocol.supporting_calculations):
            where = f"protocol.supporting_calculations[{index}].calculation_ref"
            calc = _calculation_by_ref(session, supporting.calculation_ref, field=where)
            _assert_calculation_of_reaction(
                session,
                calc,
                reaction_entry=reaction_entry,
                participants=participants,
                ts_scope=ts_scope,
                context=where,
            )
        protocol_stored = stored_protocol.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    return applicability_stored, protocol_stored

