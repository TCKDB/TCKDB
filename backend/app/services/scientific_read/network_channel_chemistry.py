"""Bounded chemistry projection and filters for pressure-dependent networks.

This module is the single seam between the normalized network-state tables and
machine-facing network and network-kinetics reads. Callers get stable public
identifiers and canonical chemistry without learning internal state ids.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.common import SpeciesEntryStateKind
from app.db.models.network_pdep import (
    NetworkChannel,
    NetworkKinetics,
    NetworkStateParticipant,
)
from app.db.models.species import Species, SpeciesEntry
from app.schemas.reads.scientific_network_composition import (
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
    participant_count_total: int | None = None,
) -> NetworkStateComposition:
    """Return a deterministic, bounded public projection of one state."""
    if state_id is None:
        return NetworkStateComposition()

    total = participant_count_total
    if total is None:
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
            SpeciesEntry.stereo_label,
            SpeciesEntry.electronic_state_kind,
            SpeciesEntry.electronic_state_label,
            SpeciesEntry.term_symbol,
            SpeciesEntry.isotope_key,
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
            species_entry_label=species_entry_label(
                stereo_label=row.stereo_label,
                electronic_state_kind=row.electronic_state_kind,
                electronic_state_label=row.electronic_state_label,
                term_symbol=row.term_symbol,
                isotope_key=row.isotope_key,
            ),
            stoichiometry=row.stoichiometry,
        )
        for row in rows
    ]
    truncated = total > len(participants)
    return NetworkStateComposition(
        participants=participants,
        participant_count_total=int(total),
        participants_truncated=truncated,
        state_label=render_state_label(participants, truncated=truncated),
    )


def species_entry_label(
    *,
    stereo_label: str | None,
    electronic_state_kind: SpeciesEntryStateKind | None,
    electronic_state_label: str | None,
    term_symbol: str | None,
    isotope_key: str | None,
) -> str | None:
    """Return the short discriminator that tells two entries of one species apart.

    Built from every column of ``uq_species_entry_species_id`` except the
    species itself, which is what makes the result a real discriminator rather
    than a hint: two entries of one species differ in at least one of these by
    construction, so they cannot both render as ``None`` and cannot render the
    same. Two entries that agree on all five are one row.

    ``ground`` electronic state is omitted because it is the default and
    saying so of every ordinary species would bury the one entry that is not
    ground in noise. Everything else is spelled as stored: these are the
    depositor's own labels (``E``, ``Z``, ``T1``, a term symbol, an isotope
    key) and rewording them would put a spelling in a plot title that appears
    nowhere else in the record.

    :returns: A compact label, or ``None`` for the plain ground-state,
        all-standard, stereo-unlabelled entry.
    """

    parts: list[str] = []
    if stereo_label:
        parts.append(stereo_label)
    if (
        electronic_state_kind is not None
        and electronic_state_kind != SpeciesEntryStateKind.ground
    ):
        parts.append(electronic_state_kind.value)
    if electronic_state_label:
        parts.append(electronic_state_label)
    if term_symbol:
        parts.append(term_symbol)
    if isotope_key:
        parts.append(isotope_key)
    return " ".join(parts) if parts else None


def render_state_label(
    participants: Sequence[NetworkStateCompositionParticipant],
    *,
    truncated: bool,
) -> str:
    """Render one network state as ``"N=N (E) + [H][H]"``.

    The single spelling of a state, produced here so that a plot title, a
    table row and a paper agree. Rendering from ``canonical_smiles`` alone —
    which is what every consumer did while this function did not exist — makes
    two distinct wells of one species read identically, and turns a real
    isomerisation into a channel that appears to run from a state to itself.

    :param participants: The state's public participant prefix, in the order
        the projection produced.
    :param truncated: Whether participants were dropped by the public cap. A
        truncated label ends in ``" + ..."``: a state label that quietly
        omitted a participant would assert a state nobody stored.
    """

    parts: list[str] = []
    for participant in participants:
        term = participant.canonical_smiles
        if participant.species_entry_label:
            term = f"{term} ({participant.species_entry_label})"
        if participant.stoichiometry != 1:
            term = f"{participant.stoichiometry} {term}"
        parts.append(term)
    if truncated:
        parts.append("...")
    return " + ".join(parts)


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
    "render_state_label",
    "species_entry_label",
]
