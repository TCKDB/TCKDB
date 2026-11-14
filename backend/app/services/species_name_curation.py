"""The curator write path for species names: add a name, retire a name, forward names when species merge.

A name is TCKDB's own word for a species, so only a curator or an admin writes one here. A name is never deleted: it
is retired, and a retired name stays retired. Adding the same normalised name to the same species again creates a new
live row. A duplicate is a live row with the same species and the same ``name_key``.

Every refusal names public refs or nothing; row ids are logged and never returned.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.error_contract import CodedValueError
from app.api.errors import DomainError, NotFoundError, not_found
from app.db.models.app_user import AppUser
from app.db.models.common import SpeciesNameKind, SpeciesNameSourceKind
from app.db.models.species import Species
from app.db.models.species_name import SpeciesName
from app.services.public_refs import PREFIXES
from app.services.scientific_read.handles import is_ref_handle
from app.services.species_merge import canonical_species
from app.services.species_name_normalisation import display_name, name_problem, normalise_name

logger = logging.getLogger(__name__)

#: Longer raw input is refused before it is normalised.
_MAX_RAW_LENGTH = 256

INVALID_MESSAGE = (
    "a name is 1 to 128 characters of letters, digits, spaces and - , ' ( ) [ ] + . : with at least one letter or digit"
)
DUPLICATE_MESSAGE = "species_name_duplicate: this species already has a live name with the same normalised form"
LIVE_UNIQUE_INDEX = "uq_species_name_species_id_name_key_live"


class SpeciesNameDuplicate(DomainError):
    """The species already holds a live name with the same normalised form (409 ``species_name_duplicate``)."""

    def __init__(self) -> None:
        super().__init__(DUPLICATE_MESSAGE)


def _invalid(problem: str) -> CodedValueError:
    return CodedValueError("species_name_invalid", INVALID_MESSAGE, context={"problem": problem}, message_prefix=False)


def _is_ref_of(model: type, ref: str) -> bool:
    return is_ref_handle(ref) and ref.split("_", 1)[0] == PREFIXES[model.__name__]


def validated_name(name: str) -> tuple[str, str]:
    """The stored ``(name, name_key)`` of ``name``. Raises a 422 ``species_name_invalid`` without echoing the input."""
    if len(name) > _MAX_RAW_LENGTH:
        raise _invalid("name_length")
    problem = name_problem(name)
    if problem is not None:
        raise _invalid(problem)
    return display_name(name), normalise_name(name)


def add_species_name(
    session: Session, *, actor: AppUser, species_ref: str, name: str, kind: SpeciesNameKind
) -> tuple[SpeciesName, Species]:
    """Add a curated name to the species ``species_ref`` names (its survivor when it was merged).

    Never commits. Raises 404 for an unknown or malformed ref, 422 ``species_name_invalid`` and 409
    ``species_name_duplicate``.
    """
    shown, key = validated_name(name)
    species = None
    if _is_ref_of(Species, species_ref):
        species = session.scalar(select(Species).where(Species.public_ref == species_ref))
    if species is None:
        logger.info("species name target not found: ref=%s", species_ref)
        raise not_found("species", ref=species_ref)
    species = canonical_species(session, species)
    live = session.scalar(
        select(func.count())
        .select_from(SpeciesName)
        .where(SpeciesName.species_id == species.id, SpeciesName.name_key == key, SpeciesName.retired_at.is_(None))
    )
    if live:
        raise SpeciesNameDuplicate()
    row = SpeciesName(
        species_id=species.id,
        name=shown,
        name_key=key,
        kind=kind,
        source_kind=SpeciesNameSourceKind.curated,
        created_by_user_id=actor.id,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError as exc:
        orig = exc.orig
        diag = getattr(orig, "diag", None)
        if getattr(orig, "sqlstate", None) == "23505" and getattr(diag, "constraint_name", None) == LIVE_UNIQUE_INDEX:
            raise SpeciesNameDuplicate() from exc
        raise
    session.refresh(row)
    return row, species


def retire_species_name(session: Session, *, actor: AppUser, name_ref: str) -> tuple[SpeciesName, Species]:
    """Retire the name ``name_ref`` names. A name that is already retired is returned unchanged, with its original retirer.

    Never commits. Raises 404 for an unknown or malformed ref.
    """
    row = None
    if _is_ref_of(SpeciesName, name_ref):
        row = session.scalar(select(SpeciesName).where(SpeciesName.public_ref == name_ref))
    if row is None:
        logger.info("species name not found: ref=%s", name_ref)
        raise NotFoundError(f"species_name not found (name_ref={name_ref!r})")
    if row.retired_at is None:
        row.retired_at = func.now()
        row.retired_by_user_id = actor.id
        session.flush()
        session.refresh(row)
    species = session.get(Species, row.species_id)
    assert species is not None
    return row, species
