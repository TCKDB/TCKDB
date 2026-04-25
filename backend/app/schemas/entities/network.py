from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.db.models.common import NetworkSpeciesRole
from app.schemas.common import (
    ORMBaseSchema,
    SchemaBase,
    TimestampedCreatedByReadSchema,
)


class NetworkReactionBase(BaseModel):
    """Shared fields for a network-reaction link.

    :param reaction_entry_id: Referenced reaction-entry row.
    """

    reaction_entry_id: int


class NetworkReactionCreate(NetworkReactionBase, SchemaBase):
    """Nested create payload for a network-reaction link."""


class NetworkReactionUpdate(SchemaBase):
    """Patch schema for a network-reaction link.

    This schema assumes the parent network id and reaction-entry id come from the route.
    """

    pass


class NetworkReactionRead(NetworkReactionBase, ORMBaseSchema):
    """Read schema for a network-reaction link."""

    network_id: int


class NetworkSpeciesBase(BaseModel):
    """Shared fields for a network-species link.

    :param species_entry_id: Referenced species-entry row.
    :param role: Role of the species within the network.
    """

    species_entry_id: int
    role: NetworkSpeciesRole


class NetworkSpeciesCreate(NetworkSpeciesBase, SchemaBase):
    """Nested create payload for a network-species link."""


class NetworkSpeciesUpdate(SchemaBase):
    """Patch schema for a network-species link.

    This schema assumes the parent network id and species-entry id come from the route.
    """

    role: NetworkSpeciesRole | None = None


class NetworkSpeciesRead(NetworkSpeciesBase, ORMBaseSchema):
    """Read schema for a network-species link."""

    network_id: int


class NetworkBase(BaseModel):
    """Shared scalar fields for a reaction network.

    :param name: Optional network name.
    :param description: Optional free-text network description.
    :param literature_id: Optional linked literature row.
    :param software_release_id: Optional software provenance.
    :param workflow_tool_release_id: Optional workflow provenance.
    """

    name: str | None = None
    description: str | None = None

    literature_id: int | None = None
    software_release_id: int | None = None
    workflow_tool_release_id: int | None = None


class NetworkCreate(NetworkBase, SchemaBase):
    """Create schema for a reaction network.

    Nested creation is supported for reaction and species links.
    Parent foreign keys for those child rows are taken from the created network
    resource rather than from the payload.
    """

    reactions: list[NetworkReactionCreate] = Field(default_factory=list)
    species_links: list[NetworkSpeciesCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_links(self) -> Self:
        reaction_ids = [reaction.reaction_entry_id for reaction in self.reactions]
        if len(set(reaction_ids)) != len(reaction_ids):
            raise ValueError("Network reactions must be unique by reaction_entry_id.")

        species_keys = [
            (species_link.species_entry_id, species_link.role)
            for species_link in self.species_links
        ]
        if len(set(species_keys)) != len(species_keys):
            raise ValueError(
                "Network species links must be unique by (species_entry_id, role)."
            )

        return self


class NetworkUpdate(SchemaBase):
    """Patch schema for a reaction network."""

    name: str | None = None
    description: str | None = None

    literature_id: int | None = None
    software_release_id: int | None = None
    workflow_tool_release_id: int | None = None


class NetworkRead(NetworkBase, TimestampedCreatedByReadSchema):
    """Read schema for a reaction network."""

    reactions: list[NetworkReactionRead] = Field(default_factory=list)
    species_links: list[NetworkSpeciesRead] = Field(default_factory=list)
