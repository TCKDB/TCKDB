"""Shared, conservative identity resolution for external molecular-property
observation importers (CCCBDB, ThermoML, ...).

Extracted from ``app.services.cccbdb_molecular_property_import`` (Phase
C-E3) so the ThermoML persistence service can resolve identity the exact
same way without importing CCCBDB-specific code. Behaviour is unchanged
from the original CCCBDB-only implementation -- this module is a pure
relocation, not a rewrite.

Rule: resolve only on an exact single standard-InChIKey match to one
:class:`~app.db.models.species.Species` AND that species having exactly
one compatible :class:`~app.db.models.species.SpeciesEntry`
(``kind=minimum`` + ``electronic_state_kind=ground``). Anything else --
no InChIKey, multiple species, no compatible entry, multiple compatible
entries -- leaves ``species_entry_id=None`` and records why in
``warnings``. Never creates a species. Never resolves by name, CAS,
formula or SMILES (those are proposal-only signals, surfaced as
warnings).

:func:`ground_state_minimum_entries_for_species` is the shared definition
of "compatible entry" behind that rule (Phase C-E5 review round 2). It has
a second caller with a different consequence drawn from the same query:
the curator-only ``attach_observation_identity`` service
(``app/services/observation_identity_attach.py``) accepts *any* member of
that set as a legal attach target, not only a species where it has exactly
one member -- a curator naming one specific entry is itself the isomer
disambiguation this module's automatic resolver refuses to guess at.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.common import (
    SpeciesEntryStateKind,
    StationaryPointKind,
)
from app.db.models.species import Species, SpeciesEntry
from app.schemas.entities.molecular_property_observation import (
    MolecularPropertyObservationCreate,
)

IDENTITY_RESOLVED = "resolved"
IDENTITY_UNRESOLVED = "unresolved"
IDENTITY_AMBIGUOUS = "ambiguous"
IDENTITY_NOT_FOUND = "not_found"
IDENTITY_SKIPPED = "skipped"


@dataclass(frozen=True)
class IdentityResolution:
    species_entry_id: int | None
    status: str
    warnings: tuple[str, ...] = ()


def ground_state_minimum_entries_for_species(
    session: Session, species_id: int
) -> list[SpeciesEntry]:
    """Every ground-state, minimum-energy entry of one species.

    ``kind=minimum`` and ``electronic_state_kind=ground`` -- the shared
    definition of "the entry kind identity resolution treats as
    matchable". Used here to require *exactly one* such entry before
    resolving automatically, and by the curator identity-attach service to
    accept *any* such entry as a legal attach target.
    """
    return list(
        session.scalars(
            select(SpeciesEntry).where(
                SpeciesEntry.species_id == species_id,
                SpeciesEntry.kind == StationaryPointKind.minimum,
                SpeciesEntry.electronic_state_kind
                    == SpeciesEntryStateKind.ground,
            )
        ).all()
    )


def identity_hint(payload: MolecularPropertyObservationCreate) -> dict:
    hint = (
        payload.raw_payload_json.get("identity_hint")
        if payload.raw_payload_json else None
    )
    return hint if isinstance(hint, dict) else {}


def resolve_identity(
    payload: MolecularPropertyObservationCreate,
    session: Session,
) -> IdentityResolution:
    """Conservative identity resolution.

    Returns :class:`IdentityResolution` reflecting the outcome:

    * ``resolved`` -- exactly one :class:`Species` matched by InChIKey AND
      exactly one compatible :class:`SpeciesEntry`.
    * ``ambiguous`` -- multiple species rows or multiple compatible
      entries matched.
    * ``not_found`` -- no Species/SpeciesEntry matched the InChIKey.
    * ``unresolved`` -- no InChIKey available; identity hints are
      preserved on the row but no FK is set.
    """

    if payload.species_entry_id is not None:
        return IdentityResolution(
            species_entry_id=payload.species_entry_id,
            status=IDENTITY_RESOLVED,
        )

    hint = identity_hint(payload)
    inchikey = (hint.get("inchikey") or "").strip().upper() or None
    cas_number = (hint.get("cas_number") or "").strip() or None
    formula = (hint.get("formula") or "").strip() or None
    name = (hint.get("name") or "").strip() or None

    warnings: list[str] = []
    if inchikey is None:
        # Propose-only signals; we do NOT auto-resolve from these.
        if cas_number:
            warnings.append(
                "CAS present but no CAS identity table available for "
                "automatic resolution"
            )
        if formula and name:
            warnings.append(
                "formula+name available but not used for automatic "
                "resolution (proposal-only)"
            )
        elif formula:
            warnings.append(
                "formula available but not used for automatic resolution"
            )
        return IdentityResolution(
            species_entry_id=None,
            status=IDENTITY_UNRESOLVED,
            warnings=tuple(warnings),
        )

    species_rows = session.scalars(
        select(Species).where(Species.inchi_key == inchikey)
    ).all()
    if not species_rows:
        return IdentityResolution(
            species_entry_id=None,
            status=IDENTITY_NOT_FOUND,
            warnings=(f"no Species row matched inchi_key={inchikey!r}",),
        )
    if len(species_rows) > 1:
        return IdentityResolution(
            species_entry_id=None,
            status=IDENTITY_AMBIGUOUS,
            warnings=(
                f"{len(species_rows)} Species rows share inchi_key="
                f"{inchikey!r}; refusing to pick",
            ),
        )

    species = species_rows[0]
    compatible_entries = ground_state_minimum_entries_for_species(
        session, species.id
    )
    if not compatible_entries:
        return IdentityResolution(
            species_entry_id=None,
            status=IDENTITY_NOT_FOUND,
            warnings=(
                f"species_id={species.id} (inchi_key={inchikey!r}) has no "
                "minimum / ground SpeciesEntry; refusing to pick",
            ),
        )
    if len(compatible_entries) > 1:
        return IdentityResolution(
            species_entry_id=None,
            status=IDENTITY_AMBIGUOUS,
            warnings=(
                f"species_id={species.id} has {len(compatible_entries)} "
                "compatible minimum/ground entries; refusing to pick",
            ),
        )

    return IdentityResolution(
        species_entry_id=compatible_entries[0].id,
        status=IDENTITY_RESOLVED,
    )


__all__ = [
    "IDENTITY_AMBIGUOUS",
    "IDENTITY_NOT_FOUND",
    "IDENTITY_RESOLVED",
    "IDENTITY_SKIPPED",
    "IDENTITY_UNRESOLVED",
    "IdentityResolution",
    "ground_state_minimum_entries_for_species",
    "identity_hint",
    "resolve_identity",
]
