"""Pydantic v2 schemas for the ``molecular_property_observation`` model.

See ``backend/app/db/models/molecular_property_observation.py`` for
the ORM model and ``backend/docs/specs/cccbdb_importer.md`` §7 for
the motivating CCCBDB Schema Gap.
"""

from __future__ import annotations

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.models.common import MolecularPropertyKind, ScientificOriginKind
from app.schemas.common import (
    ORMBaseSchema,
    SchemaBase,
    TimestampedCreatedByReadSchema,
)


class MolecularPropertyObservationBase(BaseModel):
    """Shared scalar/vector/tensor fields for a molecular-property observation.

    :param species_entry_id: Resolved species-entry id, or ``None``
        when identity is still unresolved (importer bridge state).
    :param scientific_origin: ``computed`` / ``experimental`` /
        ``estimated``.
    :param property_kind: Machine token from
        :class:`MolecularPropertyKind`. Use ``other`` with an explicit
        ``property_label`` for kinds outside the enum.
    :param property_label: Free-text refinement of ``property_kind``;
        required when ``property_kind=other``.
    :param scalar_value: Scalar reading (e.g. dipole magnitude in Debye,
        IE in eV, Hf in kJ/mol).
    :param scalar_unit: Unit string for ``scalar_value``. Required
        when ``scalar_value`` is present.
    :param scalar_uncertainty: Symmetric uncertainty in the same unit
        as ``scalar_value``. Non-negative.
    :param vector_json: Vector value (e.g. dipole ``[x, y, z]``).
    :param tensor_json: Tensor value (e.g. 3×3 polarizability).
    :param temperature_k: Optional temperature condition.
    :param wavelength_nm: Optional wavelength condition.
    :param method_note: Free-text note about the measurement method.
    :param state_label_raw: Raw electronic-state / conformation label.
    """

    species_entry_id: int | None = None
    scientific_origin: ScientificOriginKind
    property_kind: MolecularPropertyKind
    property_label: str | None = None
    scalar_value: float | None = None
    scalar_unit: str | None = None
    scalar_uncertainty: float | None = Field(default=None, ge=0.0)
    vector_json: dict[str, Any] | list[Any] | None = None
    tensor_json: dict[str, Any] | list[Any] | None = None
    temperature_k: float | None = Field(default=None, gt=0.0)
    wavelength_nm: float | None = Field(default=None, gt=0.0)
    method_note: str | None = None
    state_label_raw: str | None = None

    literature_id: int | None = None
    software_release_id: int | None = None
    workflow_tool_release_id: int | None = None
    source_calculation_id: int | None = None

    external_source_name: str | None = None
    external_source_release: str | None = None
    external_source_doi: str | None = None
    external_source_url: str | None = None
    external_source_record_key: str | None = None
    external_source_page_kind: str | None = None
    external_source_content_sha256: str | None = None
    external_source_parser_version: str | None = None

    reference_label: str | None = None
    reference_comment: str | None = None
    raw_reference_text: str | None = None

    raw_payload_json: dict[str, Any] | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _at_least_one_value_representation(self) -> Self:
        if (
            self.scalar_value is None
            and self.vector_json is None
            and self.tensor_json is None
        ):
            raise ValueError(
                "at least one of scalar_value, vector_json, tensor_json must "
                "be set"
            )
        return self

    @model_validator(mode="after")
    def _scalar_value_requires_unit(self) -> Self:
        if self.scalar_value is not None and not self.scalar_unit:
            raise ValueError(
                "scalar_unit is required when scalar_value is set"
            )
        return self

    @model_validator(mode="after")
    def _property_kind_other_requires_label(self) -> Self:
        if (
            self.property_kind == MolecularPropertyKind.other
            and not self.property_label
        ):
            raise ValueError(
                "property_label is required when property_kind=other"
            )
        return self


class MolecularPropertyObservationCreate(
    MolecularPropertyObservationBase, SchemaBase
):
    """Create schema for ``molecular_property_observation``."""


class MolecularPropertyObservationRead(
    MolecularPropertyObservationBase, TimestampedCreatedByReadSchema, ORMBaseSchema
):
    """Read schema for ``molecular_property_observation``."""

    id: int


class MolecularPropertyObservationSourceMetadata(BaseModel):
    """Compact view of an observation's external-source provenance.

    Lifted directly from the CCCBDB importer's parser metadata; useful
    for downstream services that want provenance without re-reading
    all the optional columns.
    """

    model_config = ConfigDict(extra="forbid")

    external_source_name: str | None = None
    external_source_release: str | None = None
    external_source_doi: str | None = None
    external_source_url: str | None = None
    external_source_record_key: str | None = None
    external_source_page_kind: str | None = None
    external_source_content_sha256: str | None = None
    external_source_parser_version: str | None = None
