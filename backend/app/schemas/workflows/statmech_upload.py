"""Upload payloads for species-level statmech records.

``StatmechUploadRequest`` is the standalone upload payload accepted by
``POST /api/v1/uploads/statmech``. Supporting calculations are declared
inline and referenced by local string keys, and provenance refs use the
existing upload fragments, so the upload boundary stays FK-free.

The nested statmech payload (``ConformerUploadStatmechPayload``) now uses
the same key-based reference components from
``tckdb_schemas.statmech_bits``; it resolves those keys against the
``key`` fields the conformer upload puts on its own calculations.
"""

from __future__ import annotations

from typing import Self, TypeAlias

from pydantic import Field, model_validator
from tckdb_schemas.stationary_point import (
    StationaryPointFinding,
    raise_for_blocking_findings,
)
from tckdb_schemas.statmech_bits import (
    StatmechSourceCalcIn,
    StatmechTorsionIn,
)

from app.db.models.common import (
    RigidRotorKind,
    ScientificOriginKind,
    StatmechTreatmentKind,
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
from app.schemas.workflows.conformer_upload import ElectronicLevelIn
from app.schemas.workflows.literature_upload import LiteratureUploadRequest
from app.schemas.workflows.stationary_point_seam import inline_calculation_findings


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


#: Statmech → calculation link by local key. Was a class of its own here;
#: it is now the shared wire component, because the conformer, bundle and
#: standalone paths all express this link identically. Spelled as an
#: explicit ``TypeAlias``: a bare ``X = SomeClass`` assignment is a
#: *variable* to mypy, and annotating with it is an error.
StatmechSourceCalculationIn: TypeAlias = StatmechSourceCalcIn


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
    :param rotational_constant_a_cm1: Optional A rotational constant (cm⁻¹).
    :param rotational_constant_b_cm1: Optional B rotational constant (cm⁻¹).
    :param rotational_constant_c_cm1: Optional C rotational constant (cm⁻¹).
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
    rotational_constant_a_cm1: float | None = Field(default=None, gt=0)
    rotational_constant_b_cm1: float | None = Field(default=None, gt=0)
    rotational_constant_c_cm1: float | None = Field(default=None, gt=0)
    point_group: str | None = None

    is_linear: bool | None = None
    rigid_rotor_kind: RigidRotorKind | None = None
    statmech_treatment: StatmechTreatmentKind | None = None

    freq_scale_factor: FreqScaleFactorRef | None = None
    uses_projected_frequencies: bool | None = None
    optical_isomers: int | None = Field(default=None, ge=1)
    note: str | None = None

    calculations: list[StatmechCalculationIn] = Field(default_factory=list)

    source_calculations: list[StatmechSourceCalculationIn] = Field(
        default_factory=list
    )

    torsions: list[StatmechTorsionIn] = Field(default_factory=list)
    electronic_levels: list[ElectronicLevelIn] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_text_fields(self) -> Self:
        self.point_group = normalize_optional_text(self.point_group)
        self.note = normalize_optional_text(self.note)
        return self

    @model_validator(mode="after")
    def validate_electronic_levels(self) -> Self:
        indices = [lvl.level_index for lvl in self.electronic_levels]
        if len(set(indices)) != len(indices):
            raise ValueError(
                "electronic_levels level_index values must be unique."
            )
        return self

    @model_validator(mode="after")
    def validate_unique_calculation_keys(self) -> Self:
        keys = [c.key for c in self.calculations]
        if len(set(keys)) != len(keys):
            raise ValueError("Statmech calculations must have unique keys.")
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
    def validate_scientific_interpretation(self) -> Self:
        """Scope each requirement to the claim this record actually makes.

        ``statmech_treatment`` is deliberately NOT required. Real producers
        (ARC among them) deposit symmetry, rotational constants and a
        frequency scale factor without naming a treatment; an absent field is
        honest where an invented one would not be.

        Source calculations are likewise not required here. A monatomic
        species has no vibrational modes to point at, and an experimental,
        literature or imported statmech has no calculation at all — forcing
        either to fabricate one buys nothing. Their absence on a *computed*
        record is reported as a structured upload warning at the workflow
        seam, and is enforced as an error only where a rate coefficient
        actually depends on it (the kinetics interpretation seam).

        What IS enforced is the one claim that is self-contradictory when
        unsupported: a rotor-aware treatment is *defined* by the internal
        rotors it treats, so it must list them. With no torsions the correct
        treatment is plain ``rrho``.
        """
        rotor_aware = {"rrho_1d", "rrho_nd", "rrho_1d_nd"}
        if (
            self.statmech_treatment is not None
            and self.statmech_treatment.value in rotor_aware
            and not self.torsions
        ):
            raise ValueError(
                f"statmech_treatment='{self.statmech_treatment.value}' claims a "
                "rotor-aware treatment and must list the torsions it treated; "
                "use 'rrho' when the species has none."
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
