"""Shared upload payload for species-level transport properties.

``TransportUploadPayload`` is the inline payload nested inside other
upload requests (the conformer upload, the network PDep upload). It is on
the wire because those parents are, so a consumer that can build a
conformer upload can build the transport block inside it.

The standalone ``TransportUploadRequest`` stays backend-side: it adds
inline supporting calculations addressed by local key and leans on the
backend's stationary-point seam to judge them.
"""

from typing import Self

from pydantic import Field, model_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import ScientificOriginKind
from tckdb_schemas.fragments.refs import SoftwareReleaseRef, WorkflowToolReleaseRef
from tckdb_schemas.literature import LiteratureUploadRequest
from tckdb_schemas.utils import normalize_optional_text


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
        The read-time counterpart is the backend's
        ``HardFailReason.no_transport_property_present``, which this check owns
        as the blocking tier; the trust label remains as a backstop for rows
        that entered by a non-upload path.
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
