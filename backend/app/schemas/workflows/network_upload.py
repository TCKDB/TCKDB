from typing import Self

from pydantic import Field, model_validator

from app.db.models.common import NetworkSpeciesRole
from app.schemas.common import SchemaBase
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.fragments.refs import SoftwareReleaseRef, WorkflowToolReleaseRef
from app.schemas.utils import normalize_optional_text
from app.schemas.workflows.kinetics_upload import KineticsReactionUpload
from app.schemas.workflows.literature_upload import LiteratureUploadRequest


class NetworkUploadSpeciesLinkPayload(SchemaBase):
    """Workflow-facing network species link.

    :param species_entry: Species-entry identity payload to resolve or create.
    :param role: Role of the species within the network.
    """

    species_entry: SpeciesEntryIdentityPayload
    role: NetworkSpeciesRole


class NetworkUploadReactionPayload(SchemaBase):
    """Workflow-facing network reaction link.

    :param reaction: Reaction content to resolve or create as a reaction entry.
    """

    reaction: KineticsReactionUpload


class NetworkUploadRequest(SchemaBase):
    """Workflow-facing network upload payload.

    The backend resolves literature and provenance refs, species-entry identity
    payloads, and reaction payloads, then creates the network and its links.

    :param name: Optional network name.
    :param description: Optional free-text network description.
    :param literature: Optional literature submission payload.
    :param software_release: Optional software provenance reference.
    :param workflow_tool_release: Optional workflow provenance reference.
    :param species_links: Workflow-facing species links for the network.
    :param reactions: Workflow-facing reaction links for the network.
    """

    name: str | None = None
    description: str | None = None

    literature: LiteratureUploadRequest | None = None
    software_release: SoftwareReleaseRef | None = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None

    species_links: list[NetworkUploadSpeciesLinkPayload] = Field(default_factory=list)
    reactions: list[NetworkUploadReactionPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_optional_text_fields(self) -> Self:
        self.name = normalize_optional_text(self.name)
        self.description = normalize_optional_text(self.description)
        return self
