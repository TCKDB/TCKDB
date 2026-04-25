from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql import ColumnElement

from rdkit import Chem

from app.chemistry.species import (
    canonical_species_identity,
    classify_stereo_kind,
    derive_stereo_label_from_3d,
    derive_unmapped_smiles,
    identity_mol_from_smiles,
)
from app.db.models.common import StereoKind
from app.db.models.species import Species, SpeciesEntry
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload


def null_safe_equals(column: ColumnElement, value: str | None) -> ColumnElement[bool]:
    """Build a nullable equality predicate for identity lookups.

    :param column: SQLAlchemy column expression to compare.
    :param value: Candidate value, possibly ``None``.
    :returns: ``column IS NULL`` when ``value`` is ``None``, otherwise ``column = value``.
    """

    return column.is_(None) if value is None else column == value


def resolve_species(
    session: Session,
    payload: SpeciesEntryIdentityPayload,
) -> Species:
    """Resolve or create a species row from upload identity data.

    :param session: Active SQLAlchemy session.
    :param payload: Upload-facing species-entry identity payload.
    :returns: Existing or newly created ``Species`` row.
    :raises ValueError: If the payload cannot be canonicalized into a valid species identity.
    """

    canonical_smiles, inchi_key = canonical_species_identity(payload)

    # Derive stereo_kind from molecular graph if not explicitly provided
    stereo_kind = payload.stereo_kind
    if stereo_kind == StereoKind.unspecified:
        ident_mol = identity_mol_from_smiles(payload.smiles)
        stereo_kind, _auto_label = classify_stereo_kind(ident_mol)

    species = session.scalar(select(Species).where(Species.inchi_key == inchi_key))
    if species is None:
        try:
            with session.begin_nested():
                species = Species(
                    kind=payload.molecule_kind,
                    smiles=canonical_smiles,
                    inchi_key=inchi_key,
                    charge=payload.charge,
                    multiplicity=payload.multiplicity,
                    stereo_kind=stereo_kind,
                )
                session.add(species)
                session.flush()
        except IntegrityError:
            species = session.scalar(select(Species).where(Species.inchi_key == inchi_key))

    return species


def resolve_species_entry(
    session: Session,
    payload: SpeciesEntryIdentityPayload,
    *,
    created_by: int | None = None,
    xyz_text: str | None = None,
) -> SpeciesEntry:
    """Resolve or create a species-entry row from upload identity data.

    :param session: Active SQLAlchemy session.
    :param payload: Upload-facing resolved identity payload.
    :param created_by: Optional application user id for new rows.
    :returns: Existing or newly created ``SpeciesEntry`` row.
    :raises ValueError: If the underlying species identity cannot be canonicalized.
    """

    species = resolve_species(session, payload)

    # Derive R/S or E/Z label from 3D geometry when available
    stereo_label = payload.stereo_label
    if stereo_label is None and species.stereo_kind != StereoKind.achiral and xyz_text:
        stereo_label = derive_stereo_label_from_3d(payload.smiles, xyz_text)

    species_entry = session.scalar(
        select(SpeciesEntry).where(
            SpeciesEntry.species_id == species.id,
            SpeciesEntry.kind == payload.species_entry_kind,
            null_safe_equals(SpeciesEntry.stereo_label, stereo_label),
            SpeciesEntry.electronic_state_kind == payload.electronic_state_kind,
            null_safe_equals(
                SpeciesEntry.electronic_state_label,
                payload.electronic_state_label,
            ),
            null_safe_equals(SpeciesEntry.term_symbol, payload.term_symbol),
            null_safe_equals(
                SpeciesEntry.isotopologue_label,
                payload.isotopologue_label,
            ),
        )
    )
    if species_entry is None:
        # Auto-derive unmapped_smiles and mol SMILES for the RDKit cartridge
        unmapped = payload.unmapped_smiles
        if unmapped is None:
            unmapped = derive_unmapped_smiles(payload.smiles)

        mol_smiles = Chem.MolToSmiles(
            identity_mol_from_smiles(payload.smiles), canonical=True
        )

        try:
            with session.begin_nested():
                species_entry = SpeciesEntry(
                    species_id=species.id,
                    kind=payload.species_entry_kind,
                    mol=mol_smiles,
                    unmapped_smiles=unmapped,
                    stereo_label=stereo_label,
                    electronic_state_kind=payload.electronic_state_kind,
                    electronic_state_label=payload.electronic_state_label,
                    term_symbol_raw=payload.term_symbol_raw,
                    term_symbol=payload.term_symbol,
                    isotopologue_label=payload.isotopologue_label,
                    created_by=created_by,
                )
                session.add(species_entry)
                session.flush()
        except IntegrityError:
            species_entry = session.scalar(
                select(SpeciesEntry).where(
                    SpeciesEntry.species_id == species.id,
                    SpeciesEntry.kind == payload.species_entry_kind,
                    null_safe_equals(SpeciesEntry.stereo_label, stereo_label),
                    SpeciesEntry.electronic_state_kind == payload.electronic_state_kind,
                    null_safe_equals(
                        SpeciesEntry.electronic_state_label,
                        payload.electronic_state_label,
                    ),
                    null_safe_equals(SpeciesEntry.term_symbol, payload.term_symbol),
                    null_safe_equals(
                        SpeciesEntry.isotopologue_label,
                        payload.isotopologue_label,
                    ),
                )
            )

    return species_entry


def resolve_species_entry_reference(
    session: Session,
    *,
    species_entry_id: int | None = None,
    payload: SpeciesEntryIdentityPayload | None = None,
    created_by: int | None = None,
) -> SpeciesEntry:
    """Resolve a species entry from either an existing id or an identity payload.

    :param session: Active SQLAlchemy session.
    :param species_entry_id: Existing species-entry id to reuse.
    :param payload: Upload-facing resolved identity payload to resolve when no id is supplied.
    :param created_by: Optional application user id for newly created rows.
    :returns: Existing or newly created ``SpeciesEntry`` row.
    :raises ValueError: If the reference is missing, ambiguous, or points at no stored row.
    """

    if (species_entry_id is None) == (payload is None):
        raise ValueError(
            "Provide exactly one of species_entry_id or species_entry payload."
        )

    if species_entry_id is not None:
        species_entry = session.get(SpeciesEntry, species_entry_id)
        if species_entry is None:
            raise ValueError(f"Unknown species_entry_id={species_entry_id}")
        return species_entry

    assert payload is not None
    return resolve_species_entry(session, payload, created_by=created_by)
