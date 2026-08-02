"""Upload payloads for species-level transport properties.

``TransportUploadPayload`` is the shared inline payload used by nested
upload paths (conformer bundle, network PDep). ``TransportUploadRequest``
is the standalone upload payload accepted by
``POST /api/v1/uploads/transport``.
"""

from typing import Self

from pydantic import Field, model_validator
from tckdb_schemas.stationary_point import (
    StationaryPointFinding,
    raise_for_blocking_findings,
)

from app.db.models.common import ScientificOriginKind, TransportCalculationRole
from app.schemas.common import SchemaBase
from app.schemas.fragments.calculation import CalculationWithResultsPayload
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.fragments.refs import SoftwareReleaseRef, WorkflowToolReleaseRef
from app.schemas.utils import normalize_optional_text
from app.schemas.workflows.literature_upload import LiteratureUploadRequest
from app.schemas.workflows.stationary_point_seam import inline_calculation_findings


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
    def validate_has_scientific_content(self) -> Self:
        """Reject uploads that carry only identity/provenance and no transport data.

        At least one of ``sigma_angstrom``, ``epsilon_over_k_k``,
        ``dipole_debye``, ``polarizability_angstrom3``, or
        ``rotational_relaxation`` must be present. Provenance-only fields such
        as ``literature``, ``software_release``, ``workflow_tool_release``, and
        ``note`` do not count.

        A transport row carrying none of these is definitionally empty — it
        claims to be transport data while carrying no transport property — so
        this is a contract violation and blocks at upload, per ADR 0008
        (``docs/adr/0008-validation-tiers-definitions-block-expectations-warn.md``).
        The read-time counterpart is
        :attr:`~app.services.trust.models.HardFailReason.no_transport_property_present`,
        which this check owns as the blocking tier; the trust label remains as a
        backstop for rows that entered by a non-upload path.
        """
        if not (
            self.sigma_angstrom is not None
            or self.epsilon_over_k_k is not None
            or self.dipole_debye is not None
            or self.polarizability_angstrom3 is not None
            or self.rotational_relaxation is not None
        ):
            raise ValueError(
                "Transport upload must include at least one transport "
                "property: sigma_angstrom, epsilon_over_k_k, dipole_debye, "
                "polarizability_angstrom3, or rotational_relaxation."
            )
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

    def stationary_point_findings(self) -> list[StationaryPointFinding]:
        """Judge the declared kind against this upload's inline frequency evidence."""
        return inline_calculation_findings(
            self.species_entry.species_entry_kind, list(self.calculations)
        )

    @model_validator(mode="after")
    def validate_n_imag_matches_species_entry_kind(self) -> Self:
        """Refuse inline frequency evidence that contradicts the declared kind.

        Definitional, therefore blocking (ADR 0008). The inline
        calculations are scoped to this upload's species entry, so the
        declared kind and the parsed ``n_imag`` are both present here.
        """
        raise_for_blocking_findings(self.stationary_point_findings())
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
