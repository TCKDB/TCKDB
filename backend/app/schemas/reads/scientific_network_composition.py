"""Shared public composition blocks for pressure-dependent network states."""

from __future__ import annotations

from pydantic import BaseModel, Field


class NetworkStateCompositionParticipant(BaseModel):
    """One public species participant in a network state."""

    species_entry_ref: str
    species_ref: str
    canonical_smiles: str
    stoichiometry: int = Field(ge=1)


class NetworkStateComposition(BaseModel):
    """Bounded, deterministically ordered composition of a network state.

    ``participant_count_total`` describes the complete normalized state;
    ``participants`` is its deterministic, capped public prefix.
    """

    participants: list[NetworkStateCompositionParticipant] = Field(default_factory=list)
    participant_count_total: int = 0
    participants_truncated: bool = False


__all__ = [
    "NetworkStateComposition",
    "NetworkStateCompositionParticipant",
]
