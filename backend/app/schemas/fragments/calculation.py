from datetime import datetime
from typing import Self

from pydantic import Field, model_validator

from app.db.models.common import CalculationQuality, CalculationType, IRCDirection
from app.schemas.common import SchemaBase
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.fragments.refs import (
    LevelOfTheoryRef,
    SoftwareReleaseRef,
    WorkflowToolReleaseRef,
)


class CalculationOwnerRequiredMixin:
    """Shared validator for calculation schemas that require exactly one owner."""

    @model_validator(mode="after")
    def validate_exactly_one_owner(self) -> Self:
        owner_count = sum(
            value is not None
            for value in (self.species_entry_id, self.transition_state_entry_id)
        )
        if owner_count != 1:
            raise ValueError(
                "Exactly one of species_entry_id or transition_state_entry_id must be set"
            )
        return self


class CalculationPayload(SchemaBase):
    """Reusable upload fragment for calculation provenance.

    :param type: Calculation type.
    :param quality: Curation quality flag.
    :param software_release: Required software release reference.
    :param workflow_tool_release: Optional workflow tool provenance reference.
    :param level_of_theory: Required level-of-theory reference.
    :param literature_id: Optional literature provenance id.
    """

    type: CalculationType
    quality: CalculationQuality = CalculationQuality.raw

    software_release: SoftwareReleaseRef
    workflow_tool_release: WorkflowToolReleaseRef | None = None
    level_of_theory: LevelOfTheoryRef

    literature_id: int | None = None


# ---------------------------------------------------------------------------
# Typed calculation-result payloads (upload-facing, no FK ids)
# ---------------------------------------------------------------------------


class OptResultPayload(SchemaBase):
    """Optional inline result for an optimisation calculation.

    :param converged: Whether the optimisation converged.
    :param n_steps: Number of optimisation steps.
    :param final_energy_hartree: Final electronic energy in hartree.
    """

    converged: bool | None = None
    n_steps: int | None = Field(default=None, ge=0)
    final_energy_hartree: float | None = None


class FreqResultPayload(SchemaBase):
    """Optional inline result for a frequency calculation.

    :param n_imag: Number of imaginary frequencies.
    :param imag_freq_cm1: Value of the imaginary frequency in cm⁻¹.
    :param zpe_hartree: Zero-point energy in hartree.
    """

    n_imag: int | None = None
    imag_freq_cm1: float | None = None
    zpe_hartree: float | None = None


class SPResultPayload(SchemaBase):
    """Optional inline result for a single-point calculation.

    :param electronic_energy_hartree: Electronic energy in hartree.
    """

    electronic_energy_hartree: float | None = None


class IRCPointPayload(SchemaBase):
    """Upload-facing inline payload for one IRC-path sampled point.

    Geometries are accepted inline as ``GeometryPayload`` and resolved/deduped
    via the existing geometry resolution service at persistence time.

    :param point_index: Zero-based index preserving the source step number.
    :param direction: Per-point direction (nullable for the TS marker point).
    :param is_ts: Whether this point marks the transition state.
    :param reaction_coordinate: Reaction-coordinate value at this point.
    :param electronic_energy_hartree: Electronic energy in hartree.
    :param relative_energy_kj_mol: Relative energy vs the zero-energy reference.
    :param max_gradient: Max gradient component at this point.
    :param rms_gradient: RMS gradient at this point.
    :param geometry: Optional inline geometry payload for this point.
    :param note: Optional free-text note.
    """

    point_index: int = Field(ge=0)
    direction: IRCDirection | None = None
    is_ts: bool = False
    reaction_coordinate: float | None = None
    electronic_energy_hartree: float | None = None
    relative_energy_kj_mol: float | None = None
    max_gradient: float | None = None
    rms_gradient: float | None = None
    geometry: GeometryPayload | None = None
    note: str | None = None


class IRCResultPayload(SchemaBase):
    """Upload-facing inline result for an IRC calculation.

    :param direction: Overall run mode (forward / reverse / both).
    :param has_forward: True when at least one forward-branch point is present.
    :param has_reverse: True when at least one reverse-branch point is present.
    :param ts_point_index: Optional index of the point marked as TS.
    :param point_count: Optional total sampled-point count (consistency check).
    :param zero_energy_reference_hartree: Optional energy used as relative zero.
    :param note: Optional free-text note.
    :param points: Sampled IRC-path points attached to the result.
    """

    direction: IRCDirection
    has_forward: bool
    has_reverse: bool
    ts_point_index: int | None = Field(default=None, ge=0)
    point_count: int | None = Field(default=None, ge=0)
    zero_energy_reference_hartree: float | None = None
    note: str | None = None
    points: list[IRCPointPayload] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_points(self) -> Self:
        """Enforce unique indices, TS index consistency, and direction flags."""

        if not self.points:
            return self

        indices = [point.point_index for point in self.points]
        if len(set(indices)) != len(indices):
            raise ValueError("IRC point_index values must be unique.")

        if (
            self.ts_point_index is not None
            and self.ts_point_index not in set(indices)
        ):
            raise ValueError(
                "ts_point_index must match the point_index of one of the provided points."
            )

        has_forward_in_points = any(
            point.direction == IRCDirection.forward for point in self.points
        )
        has_reverse_in_points = any(
            point.direction == IRCDirection.reverse for point in self.points
        )
        if has_forward_in_points and not self.has_forward:
            raise ValueError(
                "has_forward must be true when forward-direction points are provided."
            )
        if has_reverse_in_points and not self.has_reverse:
            raise ValueError(
                "has_reverse must be true when reverse-direction points are provided."
            )
        return self


class NEBImageResultPayload(SchemaBase):
    """Upload-facing inline payload for one NEB image result.

    :param image_index: Zero-based image index (0 = reactant, N = product).
    :param electronic_energy_hartree: Electronic energy at this image.
    :param relative_energy_kj_mol: Energy relative to the zero-energy reference.
    :param path_distance_angstrom: Cumulative path distance at this image.
    :param max_force: Max force component at this image.
    :param rms_force: RMS force at this image.
    :param is_climbing_image: Whether this image was the climbing image.
    :param geometry: Optional inline geometry payload for this image.
    """

    image_index: int = Field(ge=0)
    electronic_energy_hartree: float | None = None
    relative_energy_kj_mol: float | None = None
    path_distance_angstrom: float | None = None
    max_force: float | None = None
    rms_force: float | None = None
    is_climbing_image: bool = False
    geometry: GeometryPayload | None = None


class NEBResultPayload(SchemaBase):
    """Upload-facing inline result bundle for a NEB calculation.

    :param images: One row per NEB image, unique on ``image_index``.
    """

    images: list[NEBImageResultPayload] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_image_indices(self) -> Self:
        """Require unique ``image_index`` across all provided images."""

        indices = [image.image_index for image in self.images]
        if len(set(indices)) != len(indices):
            raise ValueError("NEB image_index values must be unique.")
        return self


class CalculationParameterObservation(SchemaBase):
    """Upload-facing payload for one parsed execution-control parameter.

    Mirrors the ``calculation_parameter`` row contract: ``raw_key`` and
    ``raw_value`` are the always-present capture surface, every other field
    is optional enrichment. ``canonical_key`` is best-effort — when the
    parser emits a key that is not yet in ``calculation_parameter_vocab``
    the persistence layer demotes it to ``None`` rather than failing the
    upload.

    :param raw_key: Raw, software-specific key as parsed (e.g. ``"calcfc"``).
    :param raw_value: Raw value as parsed.
    :param canonical_key: Optional canonical key for cross-software queries.
    :param canonical_value: Optional normalized value paired with the key.
    :param section: Optional route-line section (``opt``, ``scf``, ...).
    :param value_type: Optional consumer hint (``bool``, ``int``, ...).
    :param unit: Optional unit string when the value carries dimensionality.
    :param parameter_index: Optional ordering for repeated/positional options.
    """

    raw_key: str = Field(min_length=1)
    raw_value: str
    canonical_key: str | None = None
    canonical_value: str | None = None
    section: str | None = None
    value_type: str | None = None
    unit: str | None = None
    parameter_index: int | None = Field(default=None, ge=0)


class CalculationWithResultsPayload(CalculationPayload):
    """A calculation with optional typed result blocks.

    Extends ``CalculationPayload`` with opt/freq/sp/irc/neb result fields.
    Validation enforces that only the result type matching the calculation
    type may be provided.

    :param opt_result: Inline optimisation result (type must be ``opt``).
    :param freq_result: Inline frequency result (type must be ``freq``).
    :param sp_result: Inline single-point result (type must be ``sp``).
    :param irc_result: Inline IRC result bundle (type must be ``irc``).
    :param neb_result: Inline NEB result bundle (type must be ``neb``).
    :param parameters: Optional parsed execution-control parameter
        observations. Each becomes one ``calculation_parameter`` row.
    :param parameters_json: Optional JSON snapshot of the parser output
        (debug/traceability only — relational rows are the queryable layer).
    :param parameters_parser_version: Optional version tag of the parser
        that produced the observations.
    :param parameters_extracted_at: Optional timestamp of extraction.
    """

    opt_result: OptResultPayload | None = None
    freq_result: FreqResultPayload | None = None
    sp_result: SPResultPayload | None = None
    irc_result: IRCResultPayload | None = None
    neb_result: NEBResultPayload | None = None

    parameters: list[CalculationParameterObservation] | None = None
    parameters_json: dict | None = None
    parameters_parser_version: str | None = None
    parameters_extracted_at: datetime | None = None

    @model_validator(mode="after")
    def validate_result_matches_type(self) -> Self:
        """Ensure only the result block matching ``self.type`` is set."""
        allowed = {
            CalculationType.opt: "opt_result",
            CalculationType.freq: "freq_result",
            CalculationType.sp: "sp_result",
            CalculationType.irc: "irc_result",
            CalculationType.neb: "neb_result",
        }
        allowed_field = allowed.get(self.type)
        for field_name in (
            "opt_result",
            "freq_result",
            "sp_result",
            "irc_result",
            "neb_result",
        ):
            value = getattr(self, field_name)
            if value is not None and field_name != allowed_field:
                raise ValueError(
                    f"Result block '{field_name}' is not allowed for "
                    f"calculation type '{self.type.value}'. "
                    f"Expected '{allowed_field}' or no result."
                )
        return self


# ---------------------------------------------------------------------------
# Internal resolved-calculation request (with FK owner ids)
# ---------------------------------------------------------------------------


class CalculationCreateRequest(CalculationOwnerRequiredMixin, SchemaBase):
    """Reusable upload-oriented request for calculation creation.

    :param type: Calculation type.
    :param quality: Curation quality flag.
    :param species_entry_id: Species-entry owner id when the calculation belongs to a species entry.
    :param transition_state_entry_id: Transition-state-entry owner id when applicable.
    :param software_release: Required software release reference.
    :param workflow_tool_release: Optional workflow tool provenance reference.
    :param level_of_theory: Required level-of-theory reference.
    :param literature_id: Optional literature provenance id.
    """

    type: CalculationType
    quality: CalculationQuality = CalculationQuality.raw

    species_entry_id: int | None = None
    transition_state_entry_id: int | None = None

    software_release: SoftwareReleaseRef
    workflow_tool_release: WorkflowToolReleaseRef | None = None
    level_of_theory: LevelOfTheoryRef

    literature_id: int | None = None
