"""Provenance-presence warnings emitted at workflow boundaries.

Scientific-product uploads (thermo, transport, statmech, kinetics) all
carry optional provenance fragments — literature, software release,
workflow-tool release, level of theory, frequency scale factor — whose
absence is currently invisible: the resolved FK columns are simply
NULL and the upload succeeds silently. That lets records accumulate
with no audit trail even when upstream provenance likely existed.

This module emits structured :class:`UploadWarning` entries at the
upload boundary whenever provenance *that is scientifically meaningful
for the record type and origin* was omitted. It is deliberately
conservative: only a small set of high-signal categories, no warnings
for fields that are not typically expected for a given origin.

Two complementary behaviors are expected and live elsewhere:

* Provenance fragments that are **supplied and valid** are resolved
  and persisted by the existing resolution services — no silent drop.
* Provenance fragments that are **supplied but malformed or
  unresolvable** fail at Pydantic validation (required names, etc.) or
  in the resolver service (e.g. ``resolve_or_create_literature``
  raising for an unresolvable DOI) — silent NULL persistence is not a
  normal outcome for attempted provenance.
"""

from __future__ import annotations

from app.db.models.common import (
    NetworkEnergyTransferScope,
    NetworkSolveKind,
    ScientificOriginKind,
    TunnelingModel,
)
from app.schemas.upload_warning import UploadWarning
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.schemas.workflows.statmech_upload import StatmechUploadRequest
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.schemas.workflows.transport_upload import TransportUploadRequest

# ---------------------------------------------------------------------------
# Warning codes
# ---------------------------------------------------------------------------

W_MISSING_LITERATURE_PROVENANCE = "missing_literature_provenance"
W_MISSING_SOFTWARE_RELEASE_PROVENANCE = "missing_software_release_provenance"
W_MISSING_WORKFLOW_TOOL_PROVENANCE = "missing_workflow_tool_provenance"
W_MISSING_LEVEL_OF_THEORY_PROVENANCE = "missing_level_of_theory_provenance"
W_MISSING_FREQUENCY_SCALE_FACTOR_PROVENANCE = (
    "missing_frequency_scale_factor_provenance"
)

# Scientific-evidence gaps that are reported rather than rejected. Each names
# evidence a *computed* record would normally carry but which a legitimate
# depositor may genuinely not have: a monatomic species has no frequencies, a
# rate read out of a mechanism file has no partition functions in this
# database, and a paper reporting "Eckart tunneling" ships no barrier heights.
W_MISSING_STATMECH_SOURCE_CALCULATIONS = "missing_statmech_source_calculations"
W_MISSING_STATMECH_FREQUENCY_SOURCE = "missing_statmech_frequency_source"
W_MISSING_KINETICS_INTERPRETATIONS = "missing_kinetics_interpretation_assignments"
W_MISSING_TUNNELING_APPLICATION = "missing_tunneling_application_evidence"
W_MISSING_TS_INTERPRETATION = "missing_kinetics_transition_state_interpretation"
W_NETWORK_WIDE_ENERGY_TRANSFER = "network_wide_energy_transfer_scope"
W_REPORTED_NETWORK_SOLVE = "reported_network_solve"


# Origins for which computational provenance (software + workflow tool)
# is the expected audit trail. Non-computational origins expect a
# literature anchor instead.
_COMPUTATIONAL_ORIGINS = frozenset({ScientificOriginKind.computed})


# ---------------------------------------------------------------------------
# Warning constructors
# ---------------------------------------------------------------------------


def _literature_warning(field: str = "literature") -> UploadWarning:
    return UploadWarning(
        field=field,
        code=W_MISSING_LITERATURE_PROVENANCE,
        message=(
            "No literature provenance was supplied. Non-computed records "
            "(experimental or estimated) should carry a literature "
            "reference so the source of the data can be audited."
        ),
    )


def _software_release_warning(
    field: str = "software_release",
) -> UploadWarning:
    return UploadWarning(
        field=field,
        code=W_MISSING_SOFTWARE_RELEASE_PROVENANCE,
        message=(
            "No software release provenance was supplied. Computed "
            "records should identify which electronic-structure or "
            "post-processing software produced them."
        ),
    )


def _workflow_tool_release_warning(
    field: str = "workflow_tool_release",
) -> UploadWarning:
    return UploadWarning(
        field=field,
        code=W_MISSING_WORKFLOW_TOOL_PROVENANCE,
        message=(
            "No workflow-tool release provenance was supplied. Computed "
            "records should identify the orchestration tool (e.g. ARC) "
            "that produced them."
        ),
    )


def _level_of_theory_warning(
    field: str = "energy_level_of_theory",
) -> UploadWarning:
    return UploadWarning(
        field=field,
        code=W_MISSING_LEVEL_OF_THEORY_PROVENANCE,
        message=(
            "No energy level-of-theory provenance was supplied. Computed "
            "kinetics should declare the electronic-energy level of "
            "theory so source SP calculations can be anchored to it."
        ),
    )


def _freq_scale_factor_warning(
    field: str = "freq_scale_factor",
) -> UploadWarning:
    return UploadWarning(
        field=field,
        code=W_MISSING_FREQUENCY_SCALE_FACTOR_PROVENANCE,
        message=(
            "No frequency scale factor provenance was supplied. Computed "
            "statmech should record the scaling applied to harmonic "
            "frequencies; use value=1.0 for explicitly unscaled results."
        ),
    )


def _computed_common_warnings(
    *,
    software_release: object | None,
    workflow_tool_release: object | None,
) -> list[UploadWarning]:
    warnings: list[UploadWarning] = []
    if software_release is None:
        warnings.append(_software_release_warning())
    if workflow_tool_release is None:
        warnings.append(_workflow_tool_release_warning())
    return warnings


# ---------------------------------------------------------------------------
# Per-product entry points
# ---------------------------------------------------------------------------


def collect_thermo_provenance_warnings(
    request: ThermoUploadRequest,
) -> list[UploadWarning]:
    """Structured warnings for provenance absent from a thermo upload."""
    if request.scientific_origin in _COMPUTATIONAL_ORIGINS:
        return _computed_common_warnings(
            software_release=request.software_release,
            workflow_tool_release=request.workflow_tool_release,
        )
    if request.literature is None:
        return [_literature_warning()]
    return []


def collect_transport_provenance_warnings(
    request: TransportUploadRequest,
) -> list[UploadWarning]:
    """Structured warnings for provenance absent from a transport upload."""
    if request.scientific_origin in _COMPUTATIONAL_ORIGINS:
        return _computed_common_warnings(
            software_release=request.software_release,
            workflow_tool_release=request.workflow_tool_release,
        )
    if request.literature is None:
        return [_literature_warning()]
    return []


def collect_statmech_content_warnings(
    *,
    scientific_origin: ScientificOriginKind,
    source_calculation_roles: set[str],
    has_rotational_structure: bool = False,
    field: str = "statmech",
) -> list[UploadWarning]:
    """Report absent supporting evidence on a computed statmech record.

    Request-type agnostic so the standalone statmech route and the nested
    bundle/network seams report the same gaps in the same shape.

    Neither gap is an error. An experimental, literature or imported statmech
    has no calculation at all, and rejecting a computed one would lose the
    record rather than improve it. Where a rate coefficient actually *depends*
    on the partition function, the kinetics interpretation seam enforces the
    source link as a hard requirement instead.

    :param has_rotational_structure: True when the record itself shows the
        subject has internal structure — any rotational constant, or declared
        torsions. A monatomic species has neither, and its partition function
        is analytic with no vibrational modes, so a missing ``freq`` source is
        expected there and warning about it would fire on every atom in every
        deposit. Scoping to polyatomics keeps the signal honest without that
        noise.
    """
    if scientific_origin not in _COMPUTATIONAL_ORIGINS:
        return []
    if not source_calculation_roles:
        return [
            UploadWarning(
                field=f"{field}.source_calculations",
                code=W_MISSING_STATMECH_SOURCE_CALCULATIONS,
                message=(
                    "No source calculations were linked to this computed statmech "
                    "record, so the partition-function interpretation cannot be "
                    "traced back to the calculations it was derived from."
                ),
            )
        ]
    if has_rotational_structure and "freq" not in source_calculation_roles:
        return [
            UploadWarning(
                field=f"{field}.source_calculations",
                code=W_MISSING_STATMECH_FREQUENCY_SOURCE,
                message=(
                    "This computed statmech describes a species with rotational "
                    "structure, so its partition function uses vibrational modes, "
                    "but no source calculation with role='freq' was linked. The "
                    "frequencies behind this record are untraceable."
                ),
            )
        ]
    return []


def statmech_has_rotational_structure(statmech) -> bool:
    """True when a statmech payload shows its subject is not a single atom.

    Uses only evidence the record itself carries: a rotational constant or a
    declared torsion. Both are absent for a monatomic species and present for
    essentially any real polyatomic deposit. Deliberately conservative — a
    polyatomic that reports neither simply produces no signal, which is the
    right bias for a warning.
    """
    return (
        getattr(statmech, "rotational_constant_a_cm1", None) is not None
        or getattr(statmech, "rotational_constant_b_cm1", None) is not None
        or getattr(statmech, "rotational_constant_c_cm1", None) is not None
        or bool(getattr(statmech, "torsions", ()))
    )


def collect_statmech_provenance_warnings(
    request: StatmechUploadRequest,
) -> list[UploadWarning]:
    """Structured warnings for provenance absent from a statmech upload.

    Computed statmech additionally expects a ``freq_scale_factor``
    anchor: a NULL value means "unknown/not recorded", and leaving it
    implicit erases a scientifically meaningful piece of the record.
    """
    warnings: list[UploadWarning] = []
    if request.scientific_origin in _COMPUTATIONAL_ORIGINS:
        warnings.extend(
            _computed_common_warnings(
                software_release=request.software_release,
                workflow_tool_release=request.workflow_tool_release,
            )
        )
        if request.freq_scale_factor is None:
            warnings.append(_freq_scale_factor_warning())
    elif request.literature is None:
        warnings.append(_literature_warning())
    return warnings


def collect_kinetics_provenance_warnings(
    request: KineticsUploadRequest,
) -> list[UploadWarning]:
    """Structured warnings for provenance absent from a kinetics upload.

    Computed kinetics additionally expects ``energy_level_of_theory``:
    without it, source SP calculations cannot be auto-resolved and the
    kinetics record loses its electronic-energy anchor.
    """
    warnings: list[UploadWarning] = []
    if request.scientific_origin in _COMPUTATIONAL_ORIGINS:
        warnings.extend(
            _computed_common_warnings(
                software_release=request.software_release,
                workflow_tool_release=request.workflow_tool_release,
            )
        )
        if request.energy_level_of_theory is None:
            warnings.append(_level_of_theory_warning())
    elif request.literature is None:
        warnings.append(_literature_warning())
    return warnings


def collect_kinetics_content_warnings(
    request: KineticsUploadRequest,
) -> list[UploadWarning]:
    """Report scientific evidence a rate could carry but does not.

    Separate from the provenance collector because these describe *scientific
    content* rather than the provenance fragments attached to it. Neither gap
    is an error:

    * ``scientific_origin='computed'`` says the number came from a
      calculation, not that the calculation's partition functions live here.
      A rate read out of a CHEMKIN mechanism or an Arkane TST result
      deposited without its statmech legitimately carries no assignments.
    * ``tunneling_model`` is a reported label. A paper stating "Eckart
      tunneling was applied" ships no imaginary frequency and no barriers.
    """
    warnings: list[UploadWarning] = []
    if (
        request.scientific_origin in _COMPUTATIONAL_ORIGINS
        and not request.interpretation_assignments
    ):
        warnings.append(
            UploadWarning(
                field="interpretation_assignments",
                code=W_MISSING_KINETICS_INTERPRETATIONS,
                message=(
                    "No statmech interpretation assignments were supplied, so this "
                    "computed rate does not record which partition functions in "
                    "this database it was built from."
                ),
            )
        )
    # An interpretation set that names reactants and products but no
    # transition state describes a TST rate with no Q-double-dagger. Requiring
    # it would be wrong — a rate whose parameterization comes from a
    # master-equation fit has no single dividing surface to point at, which is
    # exactly what ``network_kinetics_ref`` declares — but for everything else
    # (including variational TST, which still evaluates Q at the variational
    # dividing surface) the omission is a real gap worth naming.
    subjects = {
        "transition_state" if item.role == "transition_state" else item.role
        for item in request.interpretation_assignments
    }
    if (
        request.interpretation_assignments
        and "transition_state" not in subjects
        and request.network_kinetics_ref is None
    ):
        warnings.append(
            UploadWarning(
                field="interpretation_assignments",
                code=W_MISSING_TS_INTERPRETATION,
                message=(
                    "The interpretation set names reactant and product partition "
                    "functions but no transition state, so the rate's activated "
                    "complex is unaccounted for. Expected for a rate fitted from a "
                    "master-equation solve; otherwise supply the "
                    "role='transition_state' assignment."
                ),
            )
        )
    if (
        request.tunneling_model not in (None, TunnelingModel.none)
        and request.tunneling_application is None
    ):
        warnings.append(
            UploadWarning(
                field="tunneling_application",
                code=W_MISSING_TUNNELING_APPLICATION,
                message=(
                    f"tunneling_model='{request.tunneling_model.value}' is declared "
                    "with no typed tunneling_application evidence, so the correction "
                    "is recorded as a reported attribute and cannot be replayed."
                ),
            )
        )
    return warnings


def collect_network_solve_kind_warnings(solve) -> list[UploadWarning]:
    """Report k(T,P) transcribed from a paper rather than solved here.

    A ``reported`` solve carries rates but none of the master-equation inputs
    behind them: no state energies, no channel barriers, no collisional
    energy-transfer model. Three things a reader would ordinarily be able to
    do with a pressure-dependent record are therefore impossible — the rates
    cannot be re-derived, the fit cannot be checked against the underlying
    microcanonical data, and the network cannot be re-solved at temperatures
    or pressures outside the reported range. That is a real completeness
    limitation and a consumer comparing two solves of the same network should
    be told which one it is looking at.

    It is not an error. Published k(T,P) is ordinary, citable science, and
    under ADR 0008 a check that would fire on a correct result must not block.
    Refusing the deposit — which is what the contract did before ADR 0010 —
    did not make the data better, it only kept it out, and a record annotated
    as reported is strictly more useful than no record at all.

    The warning fires on the *kind*, not on the absence of any particular
    input, because the absence is the declared shape rather than an omission.
    A ``computed`` solve is silent here however sparse it is; its own coverage
    rules already hold it to the full set.

    :param solve: The ``NetworkSolveIn`` block of a PDep upload request.
    """
    warnings: list[UploadWarning] = []
    if solve is None or solve.kind is not NetworkSolveKind.reported:
        return warnings
    warnings.append(
        UploadWarning(
            field="solve.kind",
            code=W_REPORTED_NETWORK_SOLVE,
            message=(
                "These k(T,P) were transcribed from a publication, not "
                "derived here. The master-equation inputs behind them -- state "
                "energies, channel barriers and the collisional "
                "energy-transfer model -- are not in this database, so the "
                "rates cannot be re-derived, the fit cannot be checked, and "
                "the network cannot be re-solved outside the reported "
                "temperature and pressure range. The values are as "
                "trustworthy as the cited source; what is missing is the "
                "derivation. A computed solve is preferred where the "
                "underlying master-equation run is available."
            ),
        )
    )
    return warnings


def collect_network_energy_transfer_warnings(solve) -> list[UploadWarning]:
    """Report a ⟨ΔE⟩down declared for the whole network rather than per well.

    Collisional energy transfer is a property of a (well, collider) pair, and
    a network-wide declaration does not resolve it that far: every well in the
    solve was relaxed with the same ⟨ΔE⟩down, and any well-to-well variation
    was not determined. That is a real completeness limitation and a reader
    comparing two solves should be told about it.

    It is not an error. Arkane, RMG and MESS inputs routinely specify one
    ``SingleExponentialDown`` for the entire network, and such results are
    published as they stand. Under ADR 0008 the check could therefore fire on
    a correct novel result, so it warns rather than blocks — and warning is
    strictly better than the alternative the old contract forced, which was to
    paste one number once per well and make the record *look* well-resolved.
    See ADR 0009.

    :param solve: The ``NetworkSolveIn`` block of a PDep upload request.
    """
    warnings: list[UploadWarning] = []
    if solve is None:
        return warnings
    if any(
        item.scope == NetworkEnergyTransferScope.network_wide
        for item in solve.energy_transfer
    ):
        wells = "every collisionally stabilised well"
        warnings.append(
            UploadWarning(
                field="solve.energy_transfer",
                code=W_NETWORK_WIDE_ENERGY_TRANSFER,
                message=(
                    "Collisional energy transfer was declared once for the "
                    f"whole network and applied to {wells} and to the bath "
                    "gas as a whole. <DeltaE>down is a property of a (well, "
                    "collider) pair, so this record does not resolve how it "
                    "varies between wells; that variation was not determined "
                    "by the run. Per-well declarations are preferred where "
                    "the calculation supplies them."
                ),
            )
        )
    return warnings
