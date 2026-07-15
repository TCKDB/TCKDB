"""Upload payload for thermochemistry data attached to a species entry.

The backend resolves provenance refs (literature, software, workflow tool),
creates the ``Thermo`` row with optional child data (tabulated points,
NASA polynomials), and attaches it to the resolved species entry.
"""

from typing import Self

from pydantic import Field, model_validator

from app.db.models.common import (
    PhaseKind,
    ScientificOriginKind,
    ThermoCalculationRole,
)
from app.schemas.common import SchemaBase
from app.schemas.entities.thermo import ThermoNASACreate, ThermoPointCreate
from app.schemas.fragments.calculation import CalculationWithResultsPayload
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.fragments.refs import SoftwareReleaseRef, WorkflowToolReleaseRef
from app.schemas.utils import normalize_optional_text
from app.schemas.workflows.energy_correction_upload import (
    AppliedEnergyCorrectionUploadPayload,
)
from app.schemas.workflows.group_additivity_upload import (
    AppliedGroupAdditivityUploadPayload,
)
from app.schemas.workflows.literature_upload import LiteratureUploadRequest


class ThermoCalculationIn(SchemaBase):
    """An inline supporting calculation declared within a thermo upload.

    :param key: Local string key used to reference this calculation from
        ``source_calculations`` and from applied-correction
        ``source_calculation_key`` fields. Must be unique within the upload.
    :param calculation: Scientific content for the calculation. Resolved and
        persisted by the workflow, attached to the same species entry as
        the parent thermo record.
    """

    key: str = Field(min_length=1)
    calculation: CalculationWithResultsPayload


class ThermoSourceCalculationIn(SchemaBase):
    """Link between a thermo upload and a supporting calculation.

    Exactly one of ``calculation_key`` (inline reference into the same
    upload's ``calculations`` list) or ``existing_calculation_id`` (FK
    reference to a row already persisted in the ``calculation`` table)
    must be provided. See DR-0028 for the rationale: ARC's typical
    pipeline uploads opt/freq/sp during conformer upload, so the thermo
    upload should reference those existing rows rather than re-declare
    duplicate calculations.

    Audience guidance:

    * ``calculation_key`` is the **contributor-facing** path. Web uploads,
      community contributors, and general workflow tools should use local
      string keys so users never need to know database IDs. The future
      computed-species bundle endpoint will resolve calculations,
      artifacts, thermo, and thermo source links together server-side
      using these keys.
    * ``existing_calculation_id`` is an **advanced / programmatic**
      mechanism for clients that are chaining from a prior TCKDB upload
      response (e.g. ARC's adapter using IDs returned by the conformer
      upload, or replay/admin/repair tooling). It is not intended as the
      primary public upload UX.

    :param calculation_key: Local key of a calculation declared in
        ``ThermoUploadRequest.calculations``.
    :param existing_calculation_id: Database id of a calculation row that
        already exists. Intended for programmatic workflows that are
        chaining from a prior TCKDB upload response; contributor-facing
        bundle uploads should prefer ``calculation_key`` so users do not
        need to know database IDs. The workflow validates
        owner-consistency and role/type compatibility before linking.
    :param role: The scientific role the calculation plays for this thermo.
    """

    calculation_key: str | None = Field(default=None, min_length=1)
    existing_calculation_id: int | None = Field(default=None, gt=0)
    role: ThermoCalculationRole

    @model_validator(mode="after")
    def validate_exactly_one_reference(self) -> Self:
        """Require exactly one of calculation_key or existing_calculation_id."""
        if (self.calculation_key is None) == (self.existing_calculation_id is None):
            raise ValueError(
                "source_calculations entry must specify exactly one of "
                "calculation_key or existing_calculation_id."
            )
        return self


class ThermoUploadRequest(SchemaBase):
    """Workflow-facing thermo upload payload.

    The backend resolves the species entry and provenance references,
    then creates a ``Thermo`` row with optional tabulated points,
    NASA polynomial coefficients, and applied energy corrections.
    """

    species_entry: SpeciesEntryIdentityPayload

    scientific_origin: ScientificOriginKind = ScientificOriginKind.computed

    literature: LiteratureUploadRequest | None = None
    software_release: SoftwareReleaseRef | None = None
    workflow_tool_release: WorkflowToolReleaseRef | None = None

    # Statmech linkage for computed thermo. This is an *advanced /
    # programmatic* mechanism (mirroring ``existing_calculation_id`` on
    # source-calculation entries, DR-0028): a client that has just
    # uploaded a statmech record threads its returned id through here so
    # the derived thermo cites its statmech basis. Contributor-facing
    # bundle uploads that resolve statmech server-side by local key are a
    # future enrichment; there is no raw contributor FK for statmech here.
    existing_statmech_id: int | None = Field(default=None, gt=0)

    h298_kj_mol: float | None = None
    s298_j_mol_k: float | None = None

    h298_uncertainty_kj_mol: float | None = Field(default=None, ge=0)
    s298_uncertainty_j_mol_k: float | None = Field(default=None, ge=0)

    enthalpy_formation_0k_kj_mol: float | None = None
    enthalpy_formation_0k_uncertainty_kj_mol: float | None = Field(
        default=None, ge=0
    )

    # Standard-state reference pressure (bar) and physical phase. These are
    # left unset (``None``) at the field level and are only *defaulted* for
    # computed uploads (see ``apply_computed_origin_defaults`` below): a QC
    # record is reasonably gas-phase @ 1 bar (IUPAC) unless stated
    # otherwise. For experimental/literature/estimated origins the defaults
    # are NOT applied — silently stamping a condensed-phase literature value
    # as ``gas @ 1 bar`` would reintroduce the ambiguity this schema removes.
    # Explicit values are always honored regardless of origin; for legacy
    # 1 atm data set ``reference_pressure_bar=1.01325``.
    reference_pressure_bar: float | None = Field(default=None, gt=0)
    phase: PhaseKind | None = None

    tmin_k: float | None = Field(default=None, gt=0)
    tmax_k: float | None = Field(default=None, gt=0)

    note: str | None = None

    # Nested child data
    points: list[ThermoPointCreate] = Field(default_factory=list)
    nasa: ThermoNASACreate | None = None

    # Supporting calculations declared inline, addressed by local string keys
    calculations: list[ThermoCalculationIn] = Field(default_factory=list)

    # Thermo -> supporting-calculation links, by local key and role
    source_calculations: list[ThermoSourceCalculationIn] = Field(default_factory=list)

    # Applied energy corrections for this thermo record
    applied_energy_corrections: list[AppliedEnergyCorrectionUploadPayload] = Field(
        default_factory=list
    )

    # Group-additivity (Benson) estimation provenance. Only meaningful for
    # ``scientific_origin=estimated`` thermo — it reifies which GA scheme and
    # which per-group contributions produced the estimated H298/S298. See
    # DR-0035. Validated below to reject attaching a GA breakdown to a
    # non-estimated record.
    group_additivity: AppliedGroupAdditivityUploadPayload | None = None

    @model_validator(mode="after")
    def normalize_text_fields(self) -> Self:
        self.note = normalize_optional_text(self.note)
        return self

    @model_validator(mode="after")
    def apply_computed_origin_defaults(self) -> Self:
        """Fill reference-state defaults only for computed uploads.

        A computed QC record is reasonably gas-phase @ 1 bar (IUPAC)
        unless stated otherwise, so ``phase``/``reference_pressure_bar``
        default to ``gas``/``1.0`` when the uploader omits them. For
        experimental/literature/estimated origins the fields stay ``None``
        unless explicitly provided — defaulting them would silently stamp
        e.g. a condensed-phase literature value as ``gas @ 1 bar``.

        Explicit values (including an explicit ``None``) are honored:
        ``model_fields_set`` distinguishes "omitted" from "provided".
        """
        if self.scientific_origin == ScientificOriginKind.computed:
            if "reference_pressure_bar" not in self.model_fields_set:
                self.reference_pressure_bar = 1.0
            if "phase" not in self.model_fields_set:
                self.phase = PhaseKind.gas
        return self

    @model_validator(mode="after")
    def validate_temperature_range(self) -> Self:
        if (
            self.tmin_k is not None
            and self.tmax_k is not None
            and self.tmin_k > self.tmax_k
        ):
            raise ValueError("tmin_k must be less than or equal to tmax_k.")
        return self

    @model_validator(mode="after")
    def validate_unique_points(self) -> Self:
        temps = [p.temperature_k for p in self.points]
        if len(set(temps)) != len(temps):
            raise ValueError("Thermo points must be unique by temperature_k.")
        return self

    @model_validator(mode="after")
    def validate_unique_calculation_keys(self) -> Self:
        keys = [c.key for c in self.calculations]
        if len(set(keys)) != len(keys):
            raise ValueError("Thermo calculations must have unique keys.")
        return self

    @model_validator(mode="after")
    def validate_source_calculation_keys_exist(self) -> Self:
        """Every source_calculations[*].calculation_key, when provided, must
        reference a calculation declared in this upload. Entries that use
        ``existing_calculation_id`` instead are skipped — those are resolved
        against the database in the workflow layer."""
        defined = {c.key for c in self.calculations}
        for sc in self.source_calculations:
            if sc.calculation_key is not None and sc.calculation_key not in defined:
                raise ValueError(
                    f"source_calculations references undefined "
                    f"calculation_key '{sc.calculation_key}'."
                )
        return self

    @model_validator(mode="after")
    def validate_unique_source_calculation_pairs(self) -> Self:
        pairs = [
            (sc.calculation_key, sc.existing_calculation_id, sc.role)
            for sc in self.source_calculations
        ]
        if len(set(pairs)) != len(pairs):
            raise ValueError(
                "source_calculations must be unique by "
                "(calculation_key, existing_calculation_id, role)."
            )
        return self

    @model_validator(mode="after")
    def validate_applied_correction_source_calc_keys(self) -> Self:
        """Every applied_energy_corrections[*].source_calculation_key, when
        provided, must reference a calculation declared in this upload. This
        prevents silent provenance loss where a correction would otherwise
        persist with a NULL source_calculation_id."""
        defined = {c.key for c in self.calculations}
        for i, correction in enumerate(self.applied_energy_corrections):
            key = correction.source_calculation_key
            if key is not None and key not in defined:
                raise ValueError(
                    f"applied_energy_corrections[{i}].source_calculation_key "
                    f"'{key}' does not reference a declared calculation."
                )
        return self

    @model_validator(mode="after")
    def validate_group_additivity_origin(self) -> Self:
        """A GA breakdown may only attach to an ``estimated`` thermo record.

        Group additivity *is* the estimation method; attaching it to a
        computed / experimental / literature record would misrepresent the
        record's scientific origin.
        """
        if (
            self.group_additivity is not None
            and self.scientific_origin != ScientificOriginKind.estimated
        ):
            raise ValueError(
                "group_additivity may only be attached to a thermo record "
                "with scientific_origin='estimated'."
            )
        return self

    @model_validator(mode="after")
    def validate_has_scientific_content(self) -> Self:
        """Reject uploads that carry only identity/provenance and no thermo data.

        At least one of: a scalar thermo value (``h298_kj_mol`` or
        ``s298_j_mol_k``), a NASA polynomial block, or one or more
        tabulated thermo points must be present. Provenance-only fields
        such as ``literature``, ``software_release``,
        ``workflow_tool_release``, and ``note`` do not count.
        """
        has_scalar = self.h298_kj_mol is not None or self.s298_j_mol_k is not None
        has_nasa = self.nasa is not None
        has_points = bool(self.points)
        if not (has_scalar or has_nasa or has_points):
            raise ValueError(
                "Thermo upload must include at least one of: a scalar "
                "thermo value (h298_kj_mol or s298_j_mol_k), a NASA block, "
                "or one or more thermo points."
            )
        return self
