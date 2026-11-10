"""Read schema for ``GET /scientific/species-entries/{ref}/conformer-usage``.

Which thermo and kinetics records name each conformer group of one species entry. Only declared links are listed:
a thermo row with no declared target, or an assignment with no conformer selection, appears nowhere.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.db.models.common import (
    ConformerSelectionKind,
    KineticsEnsemblePolicy,
    ThermoDeclarationSource,
)
from app.schemas.reads.scientific_common import ProfiledRequestEcho, ScientificResponse


class ConformerUsageRequestEcho(ProfiledRequestEcho):
    species_entry_ref: str


class ConformerUsageThermo(BaseModel):
    """A thermo record whose declared target is this one conformer group."""

    thermo_ref: str
    declared_by: ThermoDeclarationSource


class ConformerUsageKinetics(BaseModel):
    """A kinetics record whose interpretation assignment selects this conformer group."""

    kinetics_ref: str
    reaction_entry_ref: str
    role: str
    subject_key: str
    conformer_selection_kind: ConformerSelectionKind
    ensemble_policy: KineticsEnsemblePolicy


class ConformerUsageGroup(BaseModel):
    conformer_group_ref: str
    thermo: list[ConformerUsageThermo] = Field(default_factory=list)
    kinetics: list[ConformerUsageKinetics] = Field(default_factory=list)


class ConformerUsageResponse(ScientificResponse):
    """Usage by conformer group. Lists are ordered by ref, which is not a ranking.

    ``groups`` holds only the groups something uses. ``ensemble_thermo_refs`` are the thermo records declared as the
    equilibrium ensemble: they describe all shapes together, so they are listed once and not under any group.
    """

    request: ConformerUsageRequestEcho
    species_entry_ref: str
    groups: list[ConformerUsageGroup] = Field(default_factory=list)
    ensemble_thermo_refs: list[str] = Field(default_factory=list)
