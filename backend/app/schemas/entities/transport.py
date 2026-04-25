from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.db.models.common import ScientificOriginKind, TransportCalculationRole
from app.schemas.common import (
    ORMBaseSchema,
    SchemaBase,
    TimestampedCreatedByReadSchema,
)
from app.schemas.utils import normalize_optional_text


# ---------------------------------------------------------------------------
# Transport source calculation link
# ---------------------------------------------------------------------------


class TransportSourceCalculationBase(BaseModel):
    """Shared fields for a transport source-calculation link.

    :param calculation_id: Referenced calculation row.
    :param role: Semantic role of the supporting calculation.
    """

    calculation_id: int
    role: TransportCalculationRole


class TransportSourceCalculationCreate(TransportSourceCalculationBase, SchemaBase):
    """Nested create payload for a transport source-calculation link."""


class TransportSourceCalculationRead(TransportSourceCalculationBase, ORMBaseSchema):
    """Read schema for a transport source-calculation link."""

    transport_id: int


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


class TransportBase(BaseModel):
    """Shared scalar fields for a transport properties record.

    Lennard-Jones parameters and molecular transport data for a species.

    :param species_entry_id: Owning species-entry id.
    :param scientific_origin: Scientific origin category.
    :param literature_id: Optional linked literature row.
    :param software_release_id: Optional software provenance.
    :param workflow_tool_release_id: Optional workflow provenance.
    :param sigma_angstrom: Lennard-Jones collision diameter in Å.
    :param epsilon_over_k_k: Lennard-Jones well depth ε/k_B in K.
    :param dipole_debye: Dipole moment in Debye.
    :param polarizability_angstrom3: Polarizability in Å³.
    :param rotational_relaxation: Rotational relaxation collision number (Z_rot).
    :param note: Optional free-text note.
    """

    species_entry_id: int
    scientific_origin: ScientificOriginKind

    literature_id: int | None = None
    software_release_id: int | None = None
    workflow_tool_release_id: int | None = None

    sigma_angstrom: float | None = Field(default=None, gt=0)
    epsilon_over_k_k: float | None = Field(default=None, gt=0)

    dipole_debye: float | None = None
    polarizability_angstrom3: float | None = None
    rotational_relaxation: float | None = Field(default=None, ge=0)

    note: str | None = None


class TransportCreate(TransportBase, SchemaBase):
    """Create schema for a transport properties record."""

    source_calculations: list[TransportSourceCalculationCreate] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self

    @model_validator(mode="after")
    def validate_lj_pair(self) -> Self:
        """Require Lennard-Jones sigma and epsilon/k to be provided together."""
        if (self.sigma_angstrom is None) != (self.epsilon_over_k_k is None):
            raise ValueError(
                "sigma_angstrom and epsilon_over_k_k must be provided together "
                "or both omitted."
            )
        return self

    @model_validator(mode="after")
    def validate_unique_source_calculations(self) -> Self:
        keys = [
            (sc.calculation_id, sc.role) for sc in self.source_calculations
        ]
        if len(set(keys)) != len(keys):
            raise ValueError(
                "Transport source calculations must be unique by "
                "(calculation_id, role)."
            )
        return self


class TransportUpdate(SchemaBase):
    """Patch schema for a transport properties record."""

    scientific_origin: ScientificOriginKind | None = None

    literature_id: int | None = None
    software_release_id: int | None = None
    workflow_tool_release_id: int | None = None

    sigma_angstrom: float | None = Field(default=None, gt=0)
    epsilon_over_k_k: float | None = Field(default=None, gt=0)

    dipole_debye: float | None = None
    polarizability_angstrom3: float | None = None
    rotational_relaxation: float | None = Field(default=None, ge=0)

    note: str | None = None

    @model_validator(mode="after")
    def normalize_text(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self


class TransportRead(TransportBase, TimestampedCreatedByReadSchema):
    """Read schema for a transport properties record."""

    source_calculations: list[TransportSourceCalculationRead] = Field(
        default_factory=list
    )
