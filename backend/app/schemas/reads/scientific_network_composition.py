"""Shared public composition blocks for pressure-dependent network states."""

from __future__ import annotations

from pydantic import BaseModel, Field


class NetworkStateCompositionParticipant(BaseModel):
    """One public species participant in a network state.

    ``canonical_smiles`` is the *species*-level graph identity and is
    deliberately not unique among participants: two ``species_entry`` rows
    under one ``species`` share it by construction. The hydrazine network has
    exactly that shape — two diazene entries, one ``species`` row, one
    ``N=N`` — and a consumer that renders a state from ``canonical_smiles``
    alone collapses two distinct wells onto one string and can produce a
    channel that appears to run from a state to itself.

    ``species_entry_label`` is what tells them apart in prose.
    ``uq_species_entry_species_id`` makes ``(stereo_label,
    electronic_state_kind, electronic_state_label, term_symbol,
    isotope_key)`` unique within a species, and the label is built from
    exactly those five columns, so two participants of one species can never
    carry the same label: if it is ``None`` on both, they are the same entry.
    It is ``None`` when the entry is the plain ground-state, all-standard,
    stereo-unlabelled one — the common case, and the only entry of its
    species when it is the only one.

    ``species_entry_ref`` remains the machine identity. The label is for
    reading; nothing should key on it.
    """

    species_entry_ref: str
    species_ref: str
    canonical_smiles: str
    species_entry_label: str | None = None
    stoichiometry: int = Field(ge=1)


class NetworkStateComposition(BaseModel):
    """Bounded, deterministically ordered composition of a network state.

    ``participant_count_total`` describes the complete normalized state;
    ``participants`` is its deterministic, capped public prefix.

    ``state_label`` is that prefix rendered as one string — ``"N=N (E) +
    [H][H]"`` — and is the spelling every consumer should use, so that a
    state reads the same way in a plot title, a table and a paper. It carries
    each participant's ``species_entry_label`` for the reason given on the
    participant, and ends in ``" + ..."`` when ``participants_truncated`` is
    set, because a label that silently dropped a participant would assert a
    state that is not the one stored. Empty for a state with no participants
    at all.

    It is **not** ``network_state.label``, which is free text the depositor
    may or may not have supplied and which the Arkane ingester never sets.
    This one is derived, always present, and says what the state is made of.
    """

    participants: list[NetworkStateCompositionParticipant] = Field(default_factory=list)
    participant_count_total: int = 0
    participants_truncated: bool = False
    state_label: str = ""


__all__ = [
    "NetworkStateComposition",
    "NetworkStateCompositionParticipant",
]
