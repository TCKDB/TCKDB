"""The curator write path for species names: add a name, retire a name, forward names when species merge.

A name is TCKDB's own word for a species, so only a curator or an admin writes one here. A name is never deleted: it
is retired, and a retired name stays retired. Adding the same normalised name to the same species again creates a new
live row. A duplicate is a live row with the same species and the same ``name_key``.

Every refusal names public refs or nothing; row ids are logged and never returned.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import NotFoundError, not_found
from app.db.models.app_user import AppUser
from app.db.models.common import SpeciesNameKind, SpeciesNameSourceKind
from app.db.models.species import Species
from app.db.models.species_name import SpeciesName
from app.services.public_refs import PREFIXES
from app.services.scientific_read.handles import is_ref_handle
from app.services.species_merge import canonical_species
from app.services.species_name_normalisation import name_problem, normalise_name

logger = logging.getLogger(__name__)

#: Longer raw input is refused before it is normalised.
_MAX_RAW_LENGTH = 256

INVALID_MESSAGE = (
    "species_name_invalid: a name is 1 to 128 characters of letters, digits, spaces and - , ' ( ) [ ] + . : "
    "with at least one letter or digit"
)
DUPLICATE_MESSAGE = "species_name_duplicate: this species already has a live name with the same normalised form"


def _invalid(problem: str) -> HTTPException:
    return HTTPException(status_code=422, detail=f"{INVALID_MESSAGE} ({problem})")


def _is_ref_of(model: type, ref: str) -> bool:
    return is_ref_handle(ref) and ref.split("_", 1)[0] == PREFIXES[model.__name__]


def validated_name(name: str) -> tuple[str, str]:
    """The stored ``(name, name_key)`` of ``name``. Raises a 422 ``species_name_invalid`` without echoing the input."""
    if len(name) > _MAX_RAW_LENGTH:
        raise _invalid("name_length")
    problem = name_problem(name)
    if problem is not None:
        raise _invalid(problem)
    return " ".join(name.split()), normalise_name(name)


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
        raise HTTPException(status_code=409, detail=DUPLICATE_MESSAGE)
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
    except IntegrityError as exc:  # a concurrent add of the same live name
        raise HTTPException(status_code=409, detail=DUPLICATE_MESSAGE) from exc
    session.refresh(row)
    return row, species


def retire_species_name(session: Session, *, name_ref: str) -> tuple[SpeciesName, Species]:
    """Retire the name ``name_ref`` names. A name that is already retired is returned unchanged.

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
        session.flush()
        session.refresh(row)
    species = session.get(Species, row.species_id)
    assert species is not None
    return row, species


def forward_species_names(session: Session, *, loser: Species, survivor: Species) -> None:
    """Move the live names of ``loser`` to ``survivor``, ahead of the merge marker being set.

    A live name whose normalised form the survivor already holds live is retired instead of moved, so the unique
    index on live ``(species, name_key)`` is never broken. Retired names stay on the loser. Never commits.
    """
    held = set(
        session.scalars(
            select(SpeciesName.name_key).where(SpeciesName.species_id == survivor.id, SpeciesName.retired_at.is_(None))
        )
    )
    moving = session.scalars(
        select(SpeciesName)
        .where(SpeciesName.species_id == loser.id, SpeciesName.retired_at.is_(None))
        .order_by(SpeciesName.id)
    ).all()
    for row in moving:
        if row.name_key in held:
            row.retired_at = func.now()
        else:
            row.species_id = survivor.id
            held.add(row.name_key)
    session.flush()
