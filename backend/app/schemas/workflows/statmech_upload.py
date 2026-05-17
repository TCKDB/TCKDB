"""Upload payloads for species-level statmech records.

The nested statmech payload (``ConformerUploadStatmechPayload``) is
submitted as a side effect of a conformer upload and accepts raw
supporting-calculation DB ids from the surrounding workflow.

``StatmechUploadRequest`` is the standalone upload payload accepted by
``POST /api/v1/uploads/statmech``. It carries the same scientific
content as the nested path, but keeps the upload boundary FK-free:
supporting calculations are declared inline and referenced by local
string keys, and provenance refs use the existing upload fragments.
"""

from __future__ import annotations

from typing import Self

from pydantic import Field, model_validator

from app.db.models.common import (
    RigidRotorKind,
    ScientificOriginKind,
    StatmechCalculationRole,
    StatmechTreatmentKind,
    TorsionTreatmentKind,
)
from app.schemas.common import SchemaBase
from app.schemas.fragments.calculation import CalculationWithResultsPayload
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.fragments.refs import (
    FreqScaleFactorRef,
    SoftwareReleaseRef,
    WorkflowToolReleaseRef,
)
from app.schemas.utils import normalize_optional_text
from app.schemas.workflows.literature_upload import LiteratureUploadRequest
from tckdb_schemas.statmech_bits import (  # noqa: F401  (re-exported)
    StatmechTorsionCoordinateIn,
)


class StatmechCalculationIn(SchemaBase):
    """An inline supporting calculation declared within a statmech upload.

    :param key: Local string key used to reference this calculation from
        ``source_calculations`` and torsion ``source_scan_calculation_key``
        fields. Must be unique within the upload.
    :param calculation: Scientific content for the calculation. Resolved
        and persisted by the workflow, scoped to the same species entry
        as the statmech target.
    """

    key: str = Field(min_length=1)
    calculation: CalculationWithResultsPayload


class StatmechSourceCalculationIn(SchemaBase):
    """Link between a statmech upload and a supporting calculation by key.

    :param calculation_key: Local key of a calculation declared in
        ``StatmechUploadRequest.calculations``.
    :param role: Scientific role the calculation plays for this statmech.
    """

    calculation_key: str = Field(min_length=1)
    role: StatmechCalculationRole


class StatmechTorsionIn(SchemaBase):
    """Torsion definition for a standalone statmech upload.

    Unlike the nested-create schema, the principal scan calculation is
    addressed by a local string key rather than a raw calculation id.

    :param torsion_index: One-based torsion number within the record.
    :param symmetry_number: Optional torsional symmetry number.
    :param treatment_kind: Optional torsion treatment classification.
    :param dimension: Number of coupled torsional coordinates.
    :param top_description: Optional description of the rotating top.
    :param invalidated_reason: Optional invalidation reason.
    :param note: Optional free-text note.
    :param source_scan_calculation_key: Optional local key referencing an
        inline calculation declared in ``StatmechUploadRequest.calculations``.
    :param coordinates: Ordered torsional coordinate definitions.
    """

    torsion_index: int = Field(ge=1)
    symmetry_number: int | None = Field(default=None, ge=1)
    treatment_kind: TorsionTreatmentKind | None = None

    dimension: int = Field(default=1, ge=1)
    top_description: str | None = None
    invalidated_reason: str | None = None
    note: str | None = None

    source_scan_calculation_key: str | None = None

    coordinates: list[StatmechTorsionCoordinateIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_coordinates(self) -> Self:
        if len(self.coordinates) != self.dimension:
            raise ValueError(
                "Number of torsion coordinates must equal dimension."
            )

        indices = [c.coordinate_index for c in self.coordinates]
        expected = list(range(1, self.dimension + 1))
        if sorted(indices) != expected:
            raise ValueError(
                "Torsion coordinate_index values must run contiguously "
                "from 1..dimension."
            )
        return self


class StatmechUploadRequest(SchemaBase):
    """Workflow-facing standalone statmech upload payload.

    The backend resolves the target species entry, persists any inline
    supporting calculations, resolves provenance references, and routes
    the resulting scientific payload through the canonical statmech
    resolution service. Statmech is append-only — repeated uploads
    against the same species entry create independent rows.

    :param species_entry: Identity payload used to resolve the owning
        species entry.
    :param scientific_origin: Scientific origin category for this record.
    :param literature: Optional literature submission payload.
    :param workflow_tool_release: Optional workflow-tool provenance.
    :param software_release: Optional software provenance.
    :param external_symmetry: Optional external symmetry number.
    :param point_group: Optional point-group label.
    :param is_linear: Optional linearity flag.
    :param rigid_rotor_kind: Optional rigid-rotor classification.
    :param statmech_treatment: Optional treatment classification.
    :param freq_scale_factor: Optional frequency scale factor ref.
    :param uses_projected_frequencies: Optional projected-frequency flag.
    :param note: Optional free-text note.
    :param calculations: Inline supporting calculations declared by key.
    :param source_calculations: Statmech → calculation links by key/role.
    :param torsions: Torsion definitions (source scans addressed by key).
    """

    species_entry: SpeciesEntryIdentityPayload

    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed

    literature: LiteratureUploadRequest | None = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None
    software_release: SoftwareReleaseRef | None = None

    external_symmetry: int | None = Field(default=None, ge=1)
    point_group: str | None = None

    is_linear: bool | None = None
    rigid_rotor_kind: RigidRotorKind | None = None
    statmech_treatment: StatmechTreatmentKind | None = None

    freq_scale_factor: FreqScaleFactorRef | None = None
    uses_projected_frequencies: bool | None = None
    note: str | None = None

    calculations: list[StatmechCalculationIn] = Field(default_factory=list)

    source_calculations: list[StatmechSourceCalculationIn] = Field(
        default_factory=list
    )

    torsions: list[StatmechTorsionIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_text_fields(self) -> Self:
        self.point_group = normalize_optional_text(self.point_group)
        self.note = normalize_optional_text(self.note)
        return self

    @model_validator(mode="after")
    def validate_unique_calculation_keys(self) -> Self:
        keys = [c.key for c in self.calculations]
        if len(set(keys)) != len(keys):
            raise ValueError("Statmech calculations must have unique keys.")
        return self

    @model_validator(mode="after")
    def validate_source_calculation_keys_exist(self) -> Self:
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

    @model_validator(mode="after")
    def validate_unique_torsion_indices(self) -> Self:
        indices = [t.torsion_index for t in self.torsions]
        if len(set(indices)) != len(indices):
            raise ValueError(
                "Torsion indices must be unique within a statmech upload."
            )
        return self

    @model_validator(mode="after")
    def validate_torsion_scan_calculation_keys(self) -> Self:
        defined = {c.key for c in self.calculations}
        for i, torsion in enumerate(self.torsions):
            key = torsion.source_scan_calculation_key
            if key is not None and key not in defined:
                raise ValueError(
                    f"torsions[{i}].source_scan_calculation_key '{key}' "
                    f"does not reference a declared calculation."
                )
        return self
