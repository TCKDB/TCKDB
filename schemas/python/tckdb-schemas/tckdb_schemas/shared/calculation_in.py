"""Shared bundle-local calculation / geometry shapes and their adapter.

Extracted from ``app.schemas.workflows.network_pdep_upload`` because
``computed_reaction_upload`` reaches across to reuse the base
``CalculationIn``, ``GeometryIn``, and ``calculation_in_to_with_results_payload``
adapter. The remaining network-PDep schemas (states, channels, solve,
species, transition state, micro reactions) stay backend-side.
"""

from datetime import datetime

from pydantic import Field, field_validator

from tckdb_schemas.common import SchemaBase
from tckdb_schemas.enums import CalculationQuality, CalculationType
from tckdb_schemas.fragments.artifact import ArtifactIn
from tckdb_schemas.fragments.calculation import (
    CalculationParameterObservation,
    CalculationWithResultsPayload,
    FreqResultPayload,
    FrequencyModePayload,
    HessianPayload,
    OptResultPayload,
    SPResultPayload,
)
from tckdb_schemas.fragments.refs import (
    LevelOfTheoryRef,
    SoftwareReleaseRef,
    WorkflowToolReleaseRef,
)


class CalculationIn(SchemaBase):
    """A calculation defined within this upload.

    :param key: Globally unique local key for this calculation.
    :param type: Calculation type (opt, freq, sp, irc, scan).
    :param quality: Curation quality flag.
    :param geometry_key: Local key referencing a geometry defined elsewhere
        in the payload. For species calculations, typically points to a
        conformer's geometry. For TS calculations, defaults to the TS geometry.
    :param software_release: Required software provenance reference.
    :param level_of_theory: Required level-of-theory reference.
    :param workflow_tool_release: Optional workflow-tool provenance reference.
    :param literature_id: Optional literature provenance id.
    :param sp_electronic_energy_hartree: SP result (if type=sp).
    :param opt_converged: Opt result (if type=opt).
    :param opt_n_steps: Opt result (if type=opt).
    :param opt_final_energy_hartree: Opt result (if type=opt).
    :param freq_n_imag: Freq result (if type=freq).
    :param freq_imag_freq_cm1: Freq result (if type=freq).
    :param freq_zpe_hartree: Freq result (if type=freq).
    :param parameters: Optional parsed execution-control parameter observations,
        routed through the shared calculation persistence seam.
    :param parameters_json: Optional JSON snapshot from the parser.
    :param parameters_parser_version: Optional parser version tag.
    :param parameters_extracted_at: Optional extraction timestamp.
    :param artifacts: Optional list of file artifacts (logs, inputs, etc.).
    """

    key: str = Field(min_length=1)
    type: CalculationType
    quality: CalculationQuality = CalculationQuality.raw

    geometry_key: str | None = Field(default=None, min_length=1)

    software_release: SoftwareReleaseRef
    level_of_theory: LevelOfTheoryRef
    workflow_tool_release: WorkflowToolReleaseRef | None = None
    literature_id: int | None = None

    # Optional inline results (avoids separate result upload)
    sp_electronic_energy_hartree: float | None = None

    opt_converged: bool | None = None
    opt_n_steps: int | None = Field(default=None, ge=0)
    opt_final_energy_hartree: float | None = None

    freq_n_imag: int | None = None
    freq_imag_freq_cm1: float | None = None
    freq_zpe_hartree: float | None = None
    freq_frequencies_cm1: list[float] | None = None

    # Optional inline Cartesian Hessian (geometry-bound at persistence).
    hessian: HessianPayload | None = None

    # Parsed execution-control parameters (routed through the shared seam).
    parameters: list[CalculationParameterObservation] | None = None
    parameters_json: dict | None = None
    parameters_parser_version: str | None = None
    parameters_extracted_at: datetime | None = None

    # Optional file artifacts
    artifacts: list[ArtifactIn] = Field(default_factory=list)


def calculation_in_to_with_results_payload(
    calc_in: "CalculationIn",
) -> CalculationWithResultsPayload:
    """Adapt a bundle-local ``CalculationIn`` to the shared upload shape.

    Translates the flat per-type result fields (``sp_electronic_energy_hartree``,
    ``opt_converged``, ...) into the typed result blocks used by the shared
    calculation persistence seam, and forwards provenance, parameters, and
    parameter-snapshot metadata unchanged. Bundle-only fields (``key``,
    ``geometry_key``, ``artifacts``) are consumed by the workflow directly and
    are not part of the shared payload.
    """

    opt_result: OptResultPayload | None = None
    freq_result: FreqResultPayload | None = None
    sp_result: SPResultPayload | None = None

    if calc_in.type == CalculationType.opt and (
        calc_in.opt_converged is not None
        or calc_in.opt_n_steps is not None
        or calc_in.opt_final_energy_hartree is not None
    ):
        opt_result = OptResultPayload(
            converged=calc_in.opt_converged,
            n_steps=calc_in.opt_n_steps,
            final_energy_hartree=calc_in.opt_final_energy_hartree,
        )
    if calc_in.type == CalculationType.freq and (
        calc_in.freq_n_imag is not None
        or calc_in.freq_imag_freq_cm1 is not None
        or calc_in.freq_zpe_hartree is not None
        or calc_in.freq_frequencies_cm1 is not None
    ):
        modes = None
        if calc_in.freq_frequencies_cm1 is not None:
            # Sign convention: negative magnitudes mean imaginary modes; the
            # canonical FrequencyModePayload validator will reject any
            # inconsistent pair.
            modes = [
                FrequencyModePayload(
                    mode_index=i + 1,
                    frequency_cm1=value,
                    is_imaginary=value < 0,
                )
                for i, value in enumerate(calc_in.freq_frequencies_cm1)
            ]
        freq_result = FreqResultPayload(
            n_imag=calc_in.freq_n_imag,
            imag_freq_cm1=calc_in.freq_imag_freq_cm1,
            zpe_hartree=calc_in.freq_zpe_hartree,
            modes=modes,
        )
    if (
        calc_in.type == CalculationType.sp
        and calc_in.sp_electronic_energy_hartree is not None
    ):
        sp_result = SPResultPayload(
            electronic_energy_hartree=calc_in.sp_electronic_energy_hartree,
        )

    return CalculationWithResultsPayload(
        type=calc_in.type,
        quality=calc_in.quality,
        software_release=calc_in.software_release,
        workflow_tool_release=calc_in.workflow_tool_release,
        level_of_theory=calc_in.level_of_theory,
        literature_id=calc_in.literature_id,
        opt_result=opt_result,
        freq_result=freq_result,
        sp_result=sp_result,
        hessian=calc_in.hessian,
        parameters=calc_in.parameters,
        parameters_json=calc_in.parameters_json,
        parameters_parser_version=calc_in.parameters_parser_version,
        parameters_extracted_at=calc_in.parameters_extracted_at,
    )


class GeometryIn(SchemaBase):
    """A geometry defined within this upload, with a local key for reuse.

    :param key: Globally unique local key for this geometry.
    :param xyz_text: Raw XYZ text block.
    """

    key: str = Field(min_length=1)
    xyz_text: str = Field(min_length=1)

    @field_validator("xyz_text")
    @classmethod
    def strip_xyz(cls, value: str) -> str:
        return value.strip()
