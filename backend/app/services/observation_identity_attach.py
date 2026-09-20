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

Target rule (revised Phase C-E5 review round 2): the target must be a
ground-state, minimum-energy entry of its species (``kind=minimum`` and
``electronic_state_kind=ground``) -- but it no longer has to be the *unique*
such entry. The original rule refused precisely the isomer-ambiguity case
this tool exists for (see the model's module docstring: "often genuinely
ambiguous (isomers)"): a species with two ground-state minimum entries
(e.g. a cis/trans pair) could never take a curator attach at all, because a
curator's whole job here *is* to pick between them. The curator naming one
specific entry **is** the disambiguation a machine resolver could not
perform -- requiring uniqueness on top of that duplicated the resolver's own
conservatism in the one place a human was already supplying it. What is
still refused is a target that is not itself a ground-state minimum entry:
an observation reporting an experimental gas-phase property carries no
stereochemical or electronic-excited-state resolution of its own, so
attaching it to an excited-state or non-minimum target would silently claim
specificity the source data does not have.

The curation fact is recorded as a :class:`~app.db.models.submission.
SubmissionAuditEvent` on the submission the observation is linked to via
``submission_record_link`` -- never in ``raw_payload_json``, which is
provenance (the archive's forensic/round-trip copy of what was deposited)
and must stay byte-identical across a later curation act. An observation
with no submission link has nowhere honest to record the curation fact and
the attach is refused rather than silently skipping the record.

Every observation written by either importer (``app.services.
cccbdb_molecular_property_import``, ``app.services.thermoml_cp_import``) is
linked to the submission that deposited it *as of Phase C-E5 review round
3* -- this was not always true. Before that round, the CCCBDB importer
wrote rows with no submission link at all, so any such pre-existing row
refuses an attach with ``observation_identity_attach_requires_submission``
until an operator backfills a submission for it; see the "Legacy CCCBDB
rows" section of PR #513.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import not_found
from app.db.models.app_user import AppUser
from app.db.models.common import (
    SubmissionAuditEventKind,
    SubmissionRecordType,
)
from app.db.models.molecular_property_observation import (
    MolecularPropertyObservation,
)
from app.db.models.species import Species, SpeciesEntry
from app.db.models.submission import Submission, SubmissionRecordLink
from app.services.external_observation_identity import (
    ground_state_minimum_entries_for_species,
)
from app.services.scientific_read.handles import (
    parse_handle,
    resolve_species_entry_handle,
)
from app.services.submission import append_audit_event, resolve_actor_kind


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


def _assert_ground_state_minimum_entry(session: Session, target: SpeciesEntry) -> None:
    """Refuse an attach target that is not a ground-state minimum entry of
    its species.

    Uses the same shared definition
    (:func:`~app.services.external_observation_identity.
    ground_state_minimum_entries_for_species`) as the automatic resolver's
    "compatible entry" test, so the two never drift apart -- but,
    per the module docstring, this only checks *membership* in that set,
    not uniqueness within it.

    :raises ValueError: 422 ``observation_identity_target_not_ground_state_minimum``.
    """
    candidates = ground_state_minimum_entries_for_species(
        session, target.species_id
    )
    if target.id not in {row.id for row in candidates}:
        raise ValueError(
            "observation_identity_target_not_ground_state_minimum: an "
            "observation can only be attached to a ground-state, "
            "minimum-energy entry of a species; this entry is "
            f"kind={target.kind.value!r} "
            f"electronic_state_kind={target.electronic_state_kind.value!r}. "
            "An observation carries no stereochemical or excited-state "
            "resolution of its own, so it cannot be attached to a target "
            "that is not a ground-state minimum."
        )


def _hint_inchikey_connectivity_block(
    obs: MolecularPropertyObservation,
) -> str | None:
    """The connectivity block (the 14-character segment before the first
    hyphen) of the observation's own ``identity_hint.inchikey``, if it
    carries one.

    Only the connectivity block is used, never the stereo/protonation
    layers that follow it -- a source InChIKey with no stereo resolution
    of its own must still be attachable to a stereo-specific entry, since
    that is exactly the isomer-disambiguation case this tool exists for
    (see the module docstring).
    """
    raw = obs.raw_payload_json
    hint = raw.get("identity_hint") if isinstance(raw, dict) else None
    if not isinstance(hint, dict):
        return None
    inchikey = hint.get("inchikey")
    if not inchikey or not isinstance(inchikey, str):
        return None
    block = inchikey.strip().upper().split("-", 1)[0]
    return block or None


def _assert_no_identity_hint_conflict(
    session: Session, obs: MolecularPropertyObservation, target: SpeciesEntry
) -> None:
    """Refuse an attach when the observation's own identity hint names a
    different molecular connectivity than the attach target's species.

    Probe C (Phase C-E5 review round 3): without this check, an
    observation carrying a usable ``identity_hint.inchikey`` could be
    silently attached to an entry of an unrelated species -- the curator
    UI has no way to know the hint disagrees with the chosen target unless
    something checks. Compares connectivity blocks only (see
    :func:`_hint_inchikey_connectivity_block`); an observation with no
    usable hint is unaffected -- this is a plausibility check on the hint
    the depositor/importer already recorded, not a new identity signal.

    :raises ValueError: 422 ``observation_identity_hint_conflict``.
    """
    hint_block = _hint_inchikey_connectivity_block(obs)
    if hint_block is None:
        return
    species = session.get(Species, target.species_id)
    if species is None or not species.inchi_key:  # pragma: no cover — FK-guaranteed
        return
    species_block = species.inchi_key.strip().upper().split("-", 1)[0]
    if hint_block != species_block:
        raise ValueError(
            "observation_identity_hint_conflict: this observation's own "
            f"identity_hint.inchikey names a different molecular "
            f"connectivity ({hint_block}) than the target species's "
            f"InChIKey ({species_block}). Only the InChIKey's first "
            "(connectivity) block is compared, so a stereochemistry-only "
            "difference is still accepted -- resolving that is what a "
            "curator attach is for -- but a different molecular skeleton "
            "is refused."
        )


def _linked_submission_id(session: Session, observation_id: int) -> int | None:
    """The submission ``observation_id`` is linked to, if any.

    An observation may in principle be linked to more than one submission
    (the link table has no such constraint); this takes the earliest, which
    is the one that actually deposited the row.
    """
    return session.scalar(
        select(SubmissionRecordLink.submission_id)
        .where(
            SubmissionRecordLink.record_type
            == SubmissionRecordType.molecular_property_observation,
            SubmissionRecordLink.record_id == observation_id,
        )
        .order_by(SubmissionRecordLink.id.asc())
        .limit(1)
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
        entry. Must be a ground-state minimum entry of its species -- see
        the module docstring for why uniqueness is not required.
    :param actor: the curator performing the attach. Recorded by username
        on the submission audit event, never by row id.
    :param note: optional curator note, recorded alongside the actor.
    :raises NotFoundError: 404 for an unknown observation or species entry.
    :raises ValueError: 422 ``invalid_handle`` / ``handle_type_mismatch`` for
        a malformed observation handle;
        422 ``observation_identity_already_set`` if the observation already
        carries an identity (correction is supersession -- see module
        docstring -- not a repoint here);
        422 ``observation_identity_target_not_ground_state_minimum`` if the
        target is not a ground-state minimum entry of its species;
        422 ``observation_identity_hint_conflict`` if the observation
        carries its own ``identity_hint.inchikey`` and its connectivity
        block disagrees with the target species's InChIKey;
        422 ``observation_identity_attach_requires_submission`` if the
        observation is linked to no submission, so there is nowhere honest
        to record the curation fact.
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

    _assert_ground_state_minimum_entry(session, target)
    _assert_no_identity_hint_conflict(session, obs, target)

    submission_id = _linked_submission_id(session, obs.id)
    if submission_id is None:
        raise ValueError(
            "observation_identity_attach_requires_submission: this "
            "observation is linked to no submission, so there is nowhere "
            "to record the curation fact. Link the observation to a "
            "submission before attaching an identity to it."
        )
    submission = session.get(Submission, submission_id)
    assert submission is not None  # FK-guaranteed by submission_record_link

    obs.species_entry_id = target.id
    session.flush()

    append_audit_event(
        session,
        submission=submission,
        event_kind=SubmissionAuditEventKind.observation_identity_attached,
        actor_kind=resolve_actor_kind(actor),
        actor_user_id=actor.id,
        details_json={
            "observation_ref": obs.public_ref,
            "species_entry_ref": target.public_ref,
            "actor_username": actor.username,
            "note": note,
            "previous_species_entry_ref": None,
        },
    )

    return obs


__all__ = ["attach_observation_identity"]
