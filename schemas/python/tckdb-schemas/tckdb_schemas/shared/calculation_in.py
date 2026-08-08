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
from tckdb_schemas.enums import (
    CalculationQuality,
    CalculationType,
    ImaginaryModeDisposition,
)
from tckdb_schemas.fragments.artifact import ArtifactIn
from tckdb_schemas.fragments.execution_environment import ExecutionEnvironmentManifestPayload
from tckdb_schemas.fragments.geometry import GeometryPayload
from tckdb_schemas.fragments.calculation import (
    CalculationParameterObservation,
    CalculationWithResultsPayload,
    FreqResultPayload,
    FrequencyModePayload,
    HessianPayload,
    OptResultPayload,
    SpinDiagnosticPayload,
    SPResultPayload,
    WavefunctionDiagnosticPayload,
)
from tckdb_schemas.fragments.refs import (
    LevelOfTheoryRef,
    SoftwareReleaseRef,
    WorkflowToolReleaseRef,
)
from tckdb_schemas.stationary_point import (
    StationaryPointFinding,
    evaluate_transition_state_frequency,
    resolve_tau_from_parameters,
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
    :param wavefunction_diagnostic: Optional inline T1/D1 wavefunction
        diagnostic, forwarded to the shared calculation persistence seam.
    :param spin_diagnostic: Optional inline spin-contamination ``<S^2>``
        diagnostic, forwarded to the shared calculation persistence seam.
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
    execution_environment: ExecutionEnvironmentManifestPayload | None = None

    # Optional inline results (avoids separate result upload)
    sp_electronic_energy_hartree: float | None = None

    opt_converged: bool | None = None
    opt_n_steps: int | None = Field(default=None, ge=0)
    opt_final_energy_hartree: float | None = None

    freq_n_imag: int | None = None
    freq_imag_freq_cm1: float | None = None
    freq_zpe_hartree: float | None = None
    freq_frequencies_cm1: list[float] | None = None

    #: 1-based index into ``freq_frequencies_cm1`` naming the reaction
    #: coordinate. Required by ADR 0012 for a transition state with more
    #: than one imaginary mode; meaningless anywhere else.
    freq_reaction_coordinate_mode_index: int | None = Field(default=None, ge=1)

    #: What each *other* imaginary mode is, keyed by the same 1-based
    #: index. Declared, never inferred.
    freq_imaginary_dispositions: dict[int, ImaginaryModeDisposition] | None = None

    # Optional inline Cartesian Hessian (geometry-bound at persistence).
    hessian: HessianPayload | None = None

    # Optional inline post-hoc diagnostics (forwarded to the shared seam).
    wavefunction_diagnostic: WavefunctionDiagnosticPayload | None = None
    spin_diagnostic: SpinDiagnosticPayload | None = None

    # Parsed execution-control parameters (routed through the shared seam).
    parameters: list[CalculationParameterObservation] | None = None
    parameters_json: dict | None = None
    parameters_parser_version: str | None = None
    parameters_extracted_at: datetime | None = None

    # Optional file artifacts
    artifacts: list[ArtifactIn] = Field(default_factory=list)


def freq_evidence(calc_in: "CalculationIn") -> tuple[int | None, float | None]:
    """Return the ``(n_imag, imag_freq_cm1)`` this calculation will persist.

    The flat ``freq_*`` fields are only translated into a result block by
    :func:`calculation_in_to_with_results_payload` when ``type`` is
    ``freq``; on any other type they are silently dropped. Consistency
    checks must therefore read frequency evidence through this helper
    rather than off the fields directly, so they judge exactly what the
    database will hold.
    """
    if calc_in.type != CalculationType.freq:
        return (None, None)
    return (calc_in.freq_n_imag, calc_in.freq_imag_freq_cm1)


def freq_result_of(calc_in: "CalculationIn") -> FreqResultPayload | None:
    """Build the ``FreqResultPayload`` this calculation will persist.

    The single place the flat ``freq_*`` fields become the canonical
    result block, so a consistency check and the persistence seam can
    never disagree about what was deposited. ADR 0012's judgement needs
    the whole block — the frequency list, the designated reaction
    coordinate and each other imaginary mode's disposition — not just
    the ``(n_imag, imag_freq_cm1)`` pair :func:`freq_evidence` returns.

    Returns ``None`` when the calculation is not a frequency job or
    carries no frequency evidence at all.
    """
    if calc_in.type != CalculationType.freq:
        return None
    if (
        calc_in.freq_n_imag is None
        and calc_in.freq_imag_freq_cm1 is None
        and calc_in.freq_zpe_hartree is None
        and calc_in.freq_frequencies_cm1 is None
    ):
        return None

    dispositions = calc_in.freq_imaginary_dispositions or {}
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
                imaginary_disposition=dispositions.get(i + 1),
            )
            for i, value in enumerate(calc_in.freq_frequencies_cm1)
        ]
    return FreqResultPayload(
        n_imag=calc_in.freq_n_imag,
        imag_freq_cm1=calc_in.freq_imag_freq_cm1,
        zpe_hartree=calc_in.freq_zpe_hartree,
        modes=modes,
        reaction_coordinate_mode_index=(
            calc_in.freq_reaction_coordinate_mode_index
        ),
    )


def transition_state_frequency_findings(
    calc_in: "CalculationIn", *, location: str
) -> list[StationaryPointFinding]:
    """Judge a bundle-local calculation's frequency evidence as a saddle point.

    The bundle form of
    :meth:`CalculationWithResultsPayload.transition_state_frequency_findings`,
    so every path that can carry a transition state — the standalone
    upload, the computed-reaction bundle and the pressure-dependent
    network bundle — reaches the same owner with the same inputs and
    cannot drift into three slightly different rules.
    """
    freq_result = freq_result_of(calc_in)
    if freq_result is None:
        return []
    return evaluate_transition_state_frequency(
        freq_result.n_imag,
        freq_result.imag_freq_cm1,
        location=location,
        imaginary_modes=freq_result.imaginary_modes(),
        reaction_coordinate_mode_index=(
            freq_result.reaction_coordinate_mode_index
        ),
        tau=resolve_tau_from_parameters(
            (observation.canonical_key, observation.canonical_value)
            for observation in (calc_in.parameters or ())
        ),
    )


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
        freq_result = freq_result_of(calc_in)
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
        execution_environment=calc_in.execution_environment,
        opt_result=opt_result,
        freq_result=freq_result,
        sp_result=sp_result,
        hessian=calc_in.hessian,
        wavefunction_diagnostic=calc_in.wavefunction_diagnostic,
        spin_diagnostic=calc_in.spin_diagnostic,
        parameters=calc_in.parameters,
        parameters_json=calc_in.parameters_json,
        parameters_parser_version=calc_in.parameters_parser_version,
        parameters_extracted_at=calc_in.parameters_extracted_at,
    )


class GeometryIn(SchemaBase):
    """A geometry defined within this upload, with a local key for reuse.

    :param key: Globally unique local key for this geometry.
    :param xyz_text: Raw XYZ text block.
    :param isotopes: Optional atom-resolved isotope labelling, mapping a
        1-based XYZ atom index to that atom's isotope mass number. See
        :class:`tckdb_schemas.fragments.geometry.GeometryPayload`.
    """

    key: str = Field(min_length=1)
    xyz_text: str = Field(min_length=1)
    isotopes: dict[int, int] | None = None

    @field_validator("xyz_text")
    @classmethod
    def strip_xyz(cls, value: str) -> str:
        return value.strip()

    def to_payload(self) -> "GeometryPayload":
        """Return the key-less geometry payload the resolution services take."""

        return GeometryPayload(xyz_text=self.xyz_text, isotopes=self.isotopes)
