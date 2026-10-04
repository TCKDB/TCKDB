"""Resolve and store a network solve's declarations and its fits' determinations.

The upload names states and determinations by local key. What is stored names states by
composition hash (a content locator within the network) and determinations by public ref, never
by a local key or a database id. Nothing here infers a claim: a determination, a role and a
declaration exist only because the depositor stated them, and a fit that states none keeps NULL.

Cross-table facts a CHECK cannot state are enforced here and refused with
``network_declaration_invalid``: that a determination's channel belongs to the solve's network
(the channel row is taken from this upload's own network), that every fit sharing a key agrees on
channel and observable, and that a product set names this solve's determinations and fits.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tckdb_schemas.network_declarations import (
    NETWORK_PRODUCT_SET_MEMBERSHIP_VERSION,
    W_NETWORK_DECLARATION_INVALID,
    NetworkProtocolDeclaration,
    NetworkTargetDeclaration,
    NetworkValidationDeclaration,
    StoredNetworkTargetDeclaration,
    network_declaration_error,
    network_product_set_content_hash,
)

from app.api.error_contract import CodedValueError
from app.db.models.network_pdep import (
    NetworkChannel,
    NetworkKinetics,
    NetworkKineticsDetermination,
    NetworkSolve,
    NetworkState,
)

__all__ = [
    "IDENTITY_VERSION",
    "determination_identity_hash",
    "persist_network_declarations",
]

#: Version of the content a determination's ``identity_hash`` digests.
IDENTITY_VERSION = 1


def _invalid(field: str, message: str) -> CodedValueError:
    return CodedValueError(
        W_NETWORK_DECLARATION_INVALID, message, context={"field": field}, message_prefix=False
    )


def determination_identity_hash(
    *, solve_id: int, channel_id: int, determination_key: str, observable: Mapping[str, Any]
) -> str:
    """Digest of exactly the content a network determination's identity is made of."""
    canonical = json.dumps(
        {
            "version": IDENTITY_VERSION,
            "solve_id": solve_id,
            "channel_id": channel_id,
            "determination_key": determination_key,
            "observable": dict(observable),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _find_or_create_determination(
    session: Session,
    *,
    solve: NetworkSolve,
    channel: NetworkChannel,
    key: str,
    observable: dict[str, Any],
    created_by: int | None,
) -> NetworkKineticsDetermination:
    identity_hash = determination_identity_hash(
        solve_id=solve.id, channel_id=channel.id, determination_key=key, observable=observable
    )
    existing = session.scalar(
        select(NetworkKineticsDetermination).where(NetworkKineticsDetermination.identity_hash == identity_hash)
    )
    if existing is not None:
        return existing
    row = NetworkKineticsDetermination(
        solve_id=solve.id,
        channel_id=channel.id,
        determination_key=key,
        observable_declaration=observable,
        identity_hash=identity_hash,
        created_by=created_by,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        # The same solve and key already exist with different content: a key names one
        # determination of one channel and one observable.
        raise _invalid(
            "solve.channel_kinetics",
            f"determination '{key}' already exists in this solve with a different channel or observable.",
        ) from None
    return row


def resolve_determinations(
    session: Session,
    *,
    solve: NetworkSolve,
    fits: list[Any],
    channel_key_to_row: Mapping[str, NetworkChannel],
    created_by: int | None,
) -> dict[str, NetworkKineticsDetermination]:
    """Find or create each determination the fits state, once per key.

    Fits sharing a key must name the same channel and the same observable; a fit stating a
    different one is refused (the request validator already refuses it, and a payload built
    without validation meets the same refusal here).
    """
    out: dict[str, NetworkKineticsDetermination] = {}
    seen: dict[str, tuple[str, str]] = {}
    for index, fit in enumerate(fits):
        if fit.determination is None:
            continue
        key = fit.determination.key
        observable = fit.determination.observable.model_dump(mode="json")
        canonical = json.dumps(observable, sort_keys=True)
        marker = (fit.channel_key or "", canonical)
        if seen.setdefault(key, marker) != marker:
            raise _invalid(
                f"solve.channel_kinetics[{index}].determination",
                f"determination '{key}' is stated with a different channel or observable by another fit.",
            )
        if key not in out:
            channel = channel_key_to_row.get(fit.channel_key or "")
            if channel is None:
                raise _invalid(
                    f"solve.channel_kinetics[{index}].channel_key",
                    f"determination '{key}' names a channel that is not one of this network's.",
                )
            out[key] = _find_or_create_determination(
                session,
                solve=solve,
                channel=channel,
                key=key,
                observable=observable,
                created_by=created_by,
            )
    return out


def attach_fit(fit_in: Any, row: NetworkKinetics, determinations: Mapping[str, NetworkKineticsDetermination]) -> None:
    """Set a fit row's determination, role and representation from what its payload stated."""
    if fit_in.determination is None:
        return
    determination = determinations[fit_in.determination.key]
    row.determination_id = determination.id
    row.representation_role = fit_in.determination.representation_role
    row.representation_declaration = fit_in.representation.model_dump(mode="json")


def _stored_target(
    target: NetworkTargetDeclaration,
    *,
    state_key_to_row: Mapping[str, NetworkState],
    determinations: Mapping[str, NetworkKineticsDetermination],
    representation_keys: Mapping[str, set[str]],
) -> dict[str, Any]:
    """The target in its stored form: states by composition hash, product-set members by public ref, pinned."""

    def state(key: str) -> str:
        return state_key_to_row[key].composition_hash

    body = target.model_dump(mode="json")
    partition = body.get("partition")
    if partition is not None:
        partition["retained"] = [state(k) for k in partition["retained"]]
        partition["eliminated"] = [state(k) for k in partition["eliminated"]]
        for lump in partition["lumps"]:
            lump["members"] = [state(k) for k in lump["members"]]
    for boundary in body["boundaries"]:
        boundary["state_key"] = state(boundary["state_key"])
    if body.get("regime") is not None:
        body["regime"]["initial_state_keys"] = [state(k) for k in body["regime"]["initial_state_keys"]]
    for index, product_set in enumerate(body["product_sets"]):
        pinned: list[tuple[str, list[str]]] = []
        for member in product_set["members"]:
            determination = determinations.get(member["determination_key"])
            if determination is None:
                raise _invalid(
                    f"solve.target.product_sets[{index}]",
                    f"product set '{product_set['product_set_key']}' names determination "
                    f"'{member['determination_key']}', which no fit of this solve declares.",
                )
            unknown = sorted(set(member["representation_keys"]) - representation_keys[member["determination_key"]])
            if unknown:
                raise _invalid(
                    f"solve.target.product_sets[{index}]",
                    f"product set '{product_set['product_set_key']}' names representation(s) {unknown} "
                    f"that determination '{member['determination_key']}' does not have.",
                )
            member["determination_ref"] = determination.public_ref
            member["determination_key"] = None
            pinned.append((determination.public_ref, list(member["representation_keys"])))
        product_set["membership_version"] = NETWORK_PRODUCT_SET_MEMBERSHIP_VERSION
        product_set["content_hash"] = network_product_set_content_hash(pinned)
    stored = StoredNetworkTargetDeclaration.model_validate(body)
    return stored.model_dump(mode="json", exclude_none=True)


def persist_network_declarations(
    session: Session,
    *,
    solve: NetworkSolve,
    solve_in: Any,
    state_key_to_row: Mapping[str, NetworkState],
    channel_key_to_row: Mapping[str, NetworkChannel],
    fit_rows: list[tuple[Any, NetworkKinetics]],
    determinations: Mapping[str, NetworkKineticsDetermination],
) -> None:
    """Store the solve's three declarations in their resolved form and group its fits.

    ``fit_rows`` pairs each fit payload with the row written for it, in payload order.
    """
    representation_keys: dict[str, set[str]] = {}
    for fit_in, _row in fit_rows:
        if fit_in.determination is not None:
            representation_keys.setdefault(fit_in.determination.key, set()).add(fit_in.representation.key)

    target = solve_in.target
    if target is not None:
        error = network_declaration_error(
            target,
            state_keys=set(state_key_to_row),
            channel_keys=set(channel_key_to_row),
            bath_species=len(solve_in.bath_gas) or None,
        )
        if error is not None:
            raise _invalid("solve.target", error[1])
        solve.target_declaration = _stored_target(
            target,
            state_key_to_row=state_key_to_row,
            determinations=determinations,
            representation_keys=representation_keys,
        )
    protocol: NetworkProtocolDeclaration | None = solve_in.protocol
    if protocol is not None:
        solve.protocol_declaration = protocol.model_dump(mode="json")
    validation: NetworkValidationDeclaration | None = solve_in.validation
    if validation is not None:
        _check_validation_references(session, validation, set(channel_key_to_row))
        solve.validation_declaration = validation.model_dump(mode="json")
    session.flush()


def _check_validation_references(
    session: Session, validation: NetworkValidationDeclaration, channel_keys: set[str]
) -> None:
    for index, entry in enumerate(validation.entries):
        unknown = sorted(set(entry.channel_keys) - channel_keys)
        if unknown:
            raise _invalid(
                f"solve.validation.entries[{index}]",
                f"validation evidence names channel(s) the network does not have: {unknown}.",
            )
        if entry.reference_solve_ref is not None:
            found = session.scalar(
                select(NetworkSolve.id).where(NetworkSolve.public_ref == entry.reference_solve_ref)
            )
            if found is None:
                raise _invalid(
                    f"solve.validation.entries[{index}].reference_solve_ref",
                    "reference_solve_ref names no network solve.",
                )
