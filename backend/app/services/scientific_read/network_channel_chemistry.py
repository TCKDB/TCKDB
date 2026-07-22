"""Bounded chemistry projection and filters for pressure-dependent channels.

This module is the single seam between the normalized network-state tables and
machine-facing network-kinetics reads.  Callers get stable public identifiers
and canonical chemistry without learning internal state ids.
"""

from __future__ import annotations

from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.network_pdep import (
    NetworkChannel,
    NetworkKinetics,
    NetworkStateParticipant,
)
from app.db.models.species import Species, SpeciesEntry
from app.schemas.reads.scientific_network_kinetics import (
    NetworkStateComposition,
    NetworkStateCompositionParticipant,
)
from app.schemas.reads.scientific_network_kinetics_search import (
    NetworkKineticsSearchRequest,
)


def build_network_state_composition(
    session: Session,
    *,
    state_id: int | None,
    cap: int,
) -> NetworkStateComposition:
    """Return a deterministic, bounded public projection of one state."""
    if state_id is None:
        return NetworkStateComposition()

    total = session.scalar(
        select(func.count())
        .select_from(NetworkStateParticipant)
        .where(NetworkStateParticipant.state_id == state_id)
    ) or 0
    rows = session.execute(
        select(
            SpeciesEntry.public_ref.label("species_entry_ref"),
            Species.public_ref.label("species_ref"),
            Species.smiles.label("canonical_smiles"),
            NetworkStateParticipant.stoichiometry,
        )
        .join(
            SpeciesEntry,
            SpeciesEntry.id == NetworkStateParticipant.species_entry_id,
        )
        .join(Species, Species.id == SpeciesEntry.species_id)
        .where(NetworkStateParticipant.state_id == state_id)
        .order_by(
            Species.smiles.asc(),
            SpeciesEntry.public_ref.asc(),
            NetworkStateParticipant.species_entry_id.asc(),
        )
        .limit(max(1, cap))
    ).all()
    participants = [
        NetworkStateCompositionParticipant(
            species_entry_ref=row.species_entry_ref,
            species_ref=row.species_ref,
            canonical_smiles=row.canonical_smiles,
            stoichiometry=row.stoichiometry,
        )
        for row in rows
    ]
    return NetworkStateComposition(
        participants=participants,
        participant_count_total=total,
        participants_truncated=total > len(participants),
    )


def apply_channel_chemistry_filters(
    stmt,
    request: NetworkKineticsSearchRequest,
):
    """AND-combine source/sink participant filters with multiset semantics.

    Repeating an identifier requests that stoichiometric count.  Every unique
    identifier and every populated field must match the corresponding state;
    unmentioned extra participants remain allowed.
    """
    if not any(
        (
            request.source_species_entry_refs,
            request.sink_species_entry_refs,
            request.source_smiles,
            request.sink_smiles,
        )
    ):
        return stmt

    channel = NetworkChannel.__table__.alias("chemistry_channel")
    stmt = stmt.where(channel.c.id == NetworkKinetics.channel_id)
    for values, state_column, identity_kind in (
        (
            request.source_species_entry_refs,
            channel.c.source_state_id,
            "species_entry_ref",
        ),
        (
            request.sink_species_entry_refs,
            channel.c.sink_state_id,
            "species_entry_ref",
        ),
        (
            request.source_smiles,
            channel.c.source_state_id,
            "smiles",
        ),
        (
            request.sink_smiles,
            channel.c.sink_state_id,
            "smiles",
        ),
    ):
        for value, required_stoichiometry in Counter(values).items():
            participant = NetworkStateParticipant.__table__.alias()
            entry = SpeciesEntry.__table__.alias()
            species = Species.__table__.alias()
            identity = (
                entry.c.public_ref
                if identity_kind == "species_entry_ref"
                else species.c.smiles
            )
            match = (
                select(participant.c.state_id)
                .join(entry, entry.c.id == participant.c.species_entry_id)
                .join(species, species.c.id == entry.c.species_id)
                .where(
                    participant.c.state_id == state_column,
                    identity == value,
                )
                .group_by(participant.c.state_id)
                .having(
                    func.sum(participant.c.stoichiometry)
                    >= required_stoichiometry
                )
            )
            stmt = stmt.where(match.exists())
    return stmt


__all__ = [
    "apply_channel_chemistry_filters",
    "build_network_state_composition",
]
