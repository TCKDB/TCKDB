"""Read schema for ``GET /scientific/reaction-entries/{ref}/energy-profile``.

Electronic-energy profiles (reactants, transition state, products) of one reaction entry. One profile per transition-state
entry, level of theory and program; every point of a profile comes from that one level and that one program.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from app.db.models.common import RecordReviewStatus
from app.schemas.reads.scientific_common import LevelOfTheorySummary, ProfiledRequestEcho, ScientificResponse

ENERGY_KIND = "electronic"

PROFILE_DEFINITION = (
    "Each molecule's lowest computed energy at this level, from this program: the lowest single-point energy, or the "
    "lowest optimisation final energy when it has no single point. Electronic energy, no zero-point correction. "
    "Reactant and product energies are summed over the entry's participants, each counted once per occurrence."
)


class EnergyProfileUnavailableCode(str, Enum):
    """Why a transition-state entry has no complete profile at a level and program."""

    no_reactant_energy_at_level = "no_reactant_energy_at_level"
    no_product_energy_at_level = "no_product_energy_at_level"
    points_from_different_programs = "points_from_different_programs"
    no_ts_energy = "no_ts_energy"


class EnergyProfileRequestEcho(ProfiledRequestEcho):
    reaction_entry_ref: str
    #: These three apply to the kinetics records listed in ``cited_by_kinetics``.
    include_rejected: bool = False
    include_deprecated: bool = False
    min_review_status: RecordReviewStatus | None = None


class EnergyProfileSoftware(BaseModel):
    program: str
    version: str | None = None
    revision: str | None = None
    build: str | None = None


class EnergyProfileMolecule(BaseModel):
    """One participant's energy point. ``count`` is how many times the entry lists it on its side."""

    species_entry_ref: str
    count: int
    calculation_ref: str
    energy_type: Literal["single_point", "optimization"]
    electronic_energy_hartree: float
    software: EnergyProfileSoftware


class EnergyProfileTransitionState(BaseModel):
    transition_state_entry_ref: str
    calculation_ref: str
    energy_type: Literal["single_point", "optimization"]
    electronic_energy_hartree: float
    software: EnergyProfileSoftware


class EnergyProfileValues(BaseModel):
    reactants: float
    transition_state: float
    products: float


class ReactionEnergyProfile(BaseModel):
    """One complete profile: reactants, transition state and products at one level and one program."""

    transition_state_entry_ref: str
    level_of_theory: LevelOfTheorySummary
    program: str
    energy_kind: Literal["electronic"] = ENERGY_KIND
    definition: str = PROFILE_DEFINITION
    reactants: list[EnergyProfileMolecule]
    transition_state: EnergyProfileTransitionState
    products: list[EnergyProfileMolecule]
    #: Reactant and product values are sums over ``reactants`` and ``products`` weighted by ``count``.
    energy_hartree: EnergyProfileValues
    #: Relative to the reactants (so ``reactants`` is 0).
    relative_kj_mol: EnergyProfileValues


class EnergyProfileUnavailable(BaseModel):
    """A transition-state entry (at a level and program when it has an energy there) with no complete profile."""

    transition_state_entry_ref: str
    code: EnergyProfileUnavailableCode
    level_of_theory: LevelOfTheorySummary | None = None
    program: str | None = None


class CitedCalculation(BaseModel):
    """A calculation a kinetics record cites for an energy, with the energy it carries."""

    role: Literal["reactant_energy", "product_energy", "ts_energy"]
    calculation_ref: str
    species_entry_ref: str | None = None
    transition_state_entry_ref: str | None = None
    level_of_theory_ref: str | None = None
    software: EnergyProfileSoftware | None = None
    energy_type: Literal["single_point", "optimization"] | None = None
    electronic_energy_hartree: float | None = None


class KineticsCitedEnergies(BaseModel):
    """The energy calculations one kinetics record cites. Never merged into the profiles."""

    kinetics_ref: str
    calculations: list[CitedCalculation] = Field(default_factory=list)


class ReactionEnergyProfileResponse(ScientificResponse):
    """Profiles in display order of the transition-state entries, then level and program. Nothing is chosen."""

    request: EnergyProfileRequestEcho
    reaction_entry_ref: str
    profiles: list[ReactionEnergyProfile] = Field(default_factory=list)
    unavailable: list[EnergyProfileUnavailable] = Field(default_factory=list)
    cited_by_kinetics: list[KineticsCitedEnergies] = Field(default_factory=list)
