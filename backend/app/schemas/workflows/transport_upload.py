"""Upload payloads for species-level transport properties.

``TransportUploadPayload`` is the shared inline payload used by nested
upload paths (conformer bundle, network PDep). It is re-exported from
``tckdb_schemas.workflows.transport_upload`` because it is reachable from
published contracts. ``TransportUploadRequest`` is the standalone upload
payload accepted by ``POST /api/v1/uploads/transport`` and stays here: it
adds inline supporting calculations addressed by local key and judges them
through the backend's stationary-point seam.
"""

from typing import Self

from pydantic import Field, model_validator
from tckdb_schemas.local_key_codes import (
    W_CALCULATION_KEY_UNDECLARED,
    undeclared_key_error,
)
from tckdb_schemas.stationary_point import (
    StationaryPointFinding,
    raise_for_blocking_findings,
)
from tckdb_schemas.workflows.transport_upload import TransportUploadPayload

from app.db.models.common import TransportCalculationRole
from app.schemas.common import SchemaBase
from app.schemas.fragments.calculation import CalculationWithResultsPayload
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.workflows.stationary_point_seam import inline_calculation_findings

__all__ = [
    "TransportCalculationIn",
    "TransportSourceCalculationIn",
    "TransportUploadPayload",
    "TransportUploadRequest",
]


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
        for index, sc in enumerate(self.source_calculations):
            if sc.calculation_key not in defined:
                raise undeclared_key_error(
                    W_CALCULATION_KEY_UNDECLARED,
                    f"source_calculations references undefined "
                    f"calculation_key '{sc.calculation_key}'.",
                    field=f"source_calculations[{index}].calculation_key",
                    key=sc.calculation_key,
                    declared=defined,
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
