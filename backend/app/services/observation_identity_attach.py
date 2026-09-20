"""Curator attachment of a species-entry identity to an unresolved observation.

``molecular_property_observation.species_entry_id`` is nullable by design: an
importer (CCCBDB today) may deposit a row whose identity is only catalogue
formula/name, which is often genuinely ambiguous (isomers). See that model's
module docstring. Once a curator has done the disambiguation work a machine
resolver could not, this is the one write path that fills the FK in.

Correction is supersession, not update (ADR 0003): a row that already
carries an identity is never repointed here. If the identity was wrong, the
row is the wrong data, and it is superseded by depositing a corrected
observation, exactly like every other scientific record in this codebase --
not edited in place.

Ambiguity rule (mirrors the resolver's own "one species, one ground-state
minimum entry" contract): the target must be *the* unique ground-state,
minimum-energy entry of its species. An observation reporting an
experimental gas-phase property carries no stereochemical or
electronic-excited-state resolution of its own, so attaching it to a target
that is not uniquely identifiable as the ground-state minimum for its
species would silently claim more specificity than the source data
supports. A species with two (or zero) entries meeting that description has
no unambiguous target and the attach is refused.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import not_found
from app.db.models.app_user import AppUser
from app.db.models.common import SpeciesEntryStateKind, StationaryPointKind
from app.db.models.molecular_property_observation import (
    MolecularPropertyObservation,
)
from app.db.models.species import SpeciesEntry
from app.services.scientific_read.handles import (
    parse_handle,
    resolve_species_entry_handle,
)


def _resolve_observation(
    session: Session, handle: str
) -> MolecularPropertyObservation:
    """Resolve an observation handle (integer id or ``mpo_...`` ref) to a row.

    :raises ValueError: 422 ``invalid_handle`` / ``handle_type_mismatch``.
    :raises NotFoundError: 404 when the handle names no row.
    """
    kind, parsed = parse_handle(handle)
    if kind == "id":
        obs = session.get(MolecularPropertyObservation, int(parsed))
        if obs is None:
            raise not_found(
                "molecular_property_observation", code="handle_not_found"
            )
        return obs

    ref = parsed
    prefix = ref.split("_", 1)[0]
    if prefix != "mpo":
        raise ValueError(
            f"handle_type_mismatch: expected a molecular_property_observation "
            f"handle (prefix 'mpo') but got prefix {prefix!r}"
        )
    obs = session.scalar(
        select(MolecularPropertyObservation).where(
            MolecularPropertyObservation.public_ref == ref
        )
    )
    if obs is None:
        raise not_found(
            "molecular_property_observation", ref=ref, code="handle_not_found"
        )
    return obs


def _assert_unique_ground_state_minimum_entry(
    session: Session, target: SpeciesEntry
) -> None:
    """Refuse an attach target that is not uniquely its species' ground-state
    minimum entry.

    :raises ValueError: 422 ``observation_identity_ambiguous_entry``.
    """
    candidates = session.scalars(
        select(SpeciesEntry).where(
            SpeciesEntry.species_id == target.species_id,
            SpeciesEntry.kind == StationaryPointKind.minimum,
            SpeciesEntry.electronic_state_kind == SpeciesEntryStateKind.ground,
        )
    ).all()
    candidate_ids = {row.id for row in candidates}
    if len(candidate_ids) != 1 or target.id not in candidate_ids:
        raise ValueError(
            "observation_identity_ambiguous_entry: an observation can only be "
            "attached to the unique ground-state, minimum-energy entry of a "
            f"species; this species has {len(candidate_ids)} entry/entries "
            "meeting that description, so the target is ambiguous. Curate the "
            "species entries first, or attach to a species with exactly one "
            "ground-state minimum entry."
        )


def attach_observation_identity(
    session: Session,
    *,
    observation_handle: str,
    species_entry_ref: str,
    actor: AppUser,
    note: str | None = None,
) -> MolecularPropertyObservation:
    """Attach ``species_entry_ref`` as the identity of an unresolved observation.

    Curator/admin only -- callers must gate this behind
    ``require_curator_or_admin`` before calling; the actor is recorded for
    the audit trail but no role check happens here.

    :param observation_handle: integer id or ``mpo_...`` public ref of the
        observation to attach.
    :param species_entry_ref: public ref (``spe_...``) of the target species
        entry. Must be the unique ground-state minimum entry of its species.
    :param actor: the curator performing the attach. Recorded by username,
        never by row id.
    :param note: optional curator note, recorded alongside the actor.
    :raises NotFoundError: 404 for an unknown observation or species entry.
    :raises ValueError: 422 ``invalid_handle`` / ``handle_type_mismatch`` for
        a malformed observation handle;
        422 ``observation_identity_already_set`` if the observation already
        carries an identity (correction is supersession -- see module
        docstring -- not a repoint here);
        422 ``observation_identity_ambiguous_entry`` if the target is not
        the unique ground-state minimum entry of its species.
    """
    obs = _resolve_observation(session, observation_handle)

    if obs.species_entry_id is not None:
        raise ValueError(
            "observation_identity_already_set: this observation already "
            "carries a species-entry identity. Correction is supersession, "
            "not update -- deposit a corrected observation instead of "
            "repointing this one."
        )

    target_id = resolve_species_entry_handle(session, species_entry_ref)
    target = session.get(SpeciesEntry, target_id)
    if target is None:  # pragma: no cover — resolve_species_entry_handle already 404s
        raise not_found("species_entry", ref=species_entry_ref)

    _assert_unique_ground_state_minimum_entry(session, target)

    obs.species_entry_id = target.id

    payload = dict(obs.raw_payload_json) if obs.raw_payload_json else {}
    payload["identity_attachment"] = {
        "actor": actor.username,
        "note": note,
        "species_entry_ref": target.public_ref,
        "attached_at": datetime.now(timezone.utc).isoformat(),
    }
    obs.raw_payload_json = payload

    session.flush()
    return obs


__all__ = ["attach_observation_identity"]
