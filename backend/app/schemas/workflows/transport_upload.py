"""Upload payloads for species-level transport properties.

``TransportUploadPayload`` is the shared inline payload used by nested
upload paths (conformer bundle, network PDep). ``TransportUploadRequest``
is the standalone upload payload accepted by
``POST /api/v1/uploads/transport``.
"""

from typing import Self

from pydantic import Field, model_validator

from app.db.models.common import ScientificOriginKind, TransportCalculationRole
from app.schemas.common import SchemaBase
from app.schemas.fragments.calculation import CalculationWithResultsPayload
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.fragments.refs import SoftwareReleaseRef, WorkflowToolReleaseRef
from app.schemas.utils import normalize_optional_text
from app.schemas.workflows.literature_upload import LiteratureUploadRequest


class TransportUploadPayload(SchemaBase):
    """Upload payload for species-level transport properties.

    The backend resolves provenance refs and creates a ``Transport`` row
    attached to the resolved species entry.

    :param scientific_origin: Scientific origin category.
    :param literature: Optional literature submission payload.
    :param software_release: Optional software provenance reference.
    :param workflow_tool_release: Optional workflow-tool provenance reference.
    :param sigma_angstrom: Lennard-Jones collision diameter in Å.
    :param epsilon_over_k_k: Lennard-Jones well depth ε/k_B in K.
    :param dipole_debye: Dipole moment in Debye.
    :param polarizability_angstrom3: Polarizability in Å³.
    :param rotational_relaxation: Rotational relaxation collision number (Z_rot).
    :param note: Optional free-text note.
    """

    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed

    literature: LiteratureUploadRequest | None = None
    software_release: SoftwareReleaseRef | None = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None

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

    @model_validator(mode="after")
    def validate_lj_pair(self) -> Self:
        """Require Lennard-Jones sigma and epsilon/k to be provided together."""
        if (self.sigma_angstrom is None) != (self.epsilon_over_k_k is None):
            raise ValueError(
                "sigma_angstrom and epsilon_over_k_k must be provided together "
                "or both omitted."
            )
        return self


class TransportCalculationIn(SchemaBase):
    """An inline supporting calculation declared within a transport upload.

    :param key: Local string key used to reference this calculation from
        ``source_calculations``. Must be unique within the upload.
    :param calculation: Scientific content for the calculation. Resolved
        and persisted by the workflow, attached to the same species entry
        as the parent transport record.
    """

    key: str = Field(min_length=1)
    calculation: CalculationWithResultsPayload


class TransportSourceCalculationIn(SchemaBase):
    """Link between a transport upload and a supporting calculation by key.

    :param calculation_key: Local key of a calculation declared in
        ``TransportUploadRequest.calculations``.
    :param role: Scientific role the calculation plays for this transport.
    """

    calculation_key: str = Field(min_length=1)
    role: TransportCalculationRole


class TransportUploadRequest(TransportUploadPayload):
    """Workflow-facing standalone transport upload payload.

    Extends :class:`TransportUploadPayload` with the fields needed to
    stand on its own: a resolvable owning species entry and optional
    inline supporting calculations linked by role.

    The backend resolves the species entry, persists any inline
    supporting calculations, resolves provenance references, and creates
    a new ``Transport`` row with attached ``transport_source_calculation``
    links. Transport is append-only — repeated uploads against the same
    species entry create independent rows.

    :param species_entry: Identity payload used to resolve the owning
        species entry.
    :param calculations: Inline supporting calculations declared by local
        string key. Each is persisted and scoped to the resolved
        species entry.
    :param source_calculations: Transport → supporting-calculation links,
        addressed by local key and role.
    """

    species_entry: SpeciesEntryIdentityPayload

    calculations: list[TransportCalculationIn] = Field(default_factory=list)

    source_calculations: list[TransportSourceCalculationIn] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_unique_calculation_keys(self) -> Self:
        keys = [c.key for c in self.calculations]
        if len(set(keys)) != len(keys):
            raise ValueError("Transport calculations must have unique keys.")
        return self

    @model_validator(mode="after")
    def validate_source_calculation_keys_exist(self) -> Self:
        """Every ``source_calculations[*].calculation_key`` must reference a
        calculation declared in this upload."""
        defined = {c.key for c in self.calculations}
        for sc in self.source_calculations:
            if sc.calculation_key not in defined:
                raise ValueError(
                    f"source_calculations references undefined "
                    f"calculation_key '{sc.calculation_key}'."
                )
        return self

    @model_validator(mode="after")
    def validate_unique_source_calculation_pairs(self) -> Self:
        pairs = [(sc.calculation_key, sc.role) for sc in self.source_calculations]
        if len(set(pairs)) != len(pairs):
            raise ValueError(
                "source_calculations must be unique by (calculation_key, role)."
            )
        return self
