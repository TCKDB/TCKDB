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


# ---------------------------------------------------------------------------
# Request-agnostic core
# ---------------------------------------------------------------------------


class _NotApplicable:
    """Sentinel: this record type has no such anchor, so do not judge it.

    Distinct from ``None``, and the distinction is the point. ``None``
    means *the depositor could have supplied this and did not* — a real
    gap, worth a warning. ``NOT_APPLICABLE`` means *there is no field on
    this payload to supply it through*, which is a fact about the schema
    rather than about the deposit, and warning about it would tell a
    depositor to do something they cannot do.

    The concrete case is ``energy_level_of_theory`` on a bundle. On the
    standalone kinetics route it is a real anchor: ``app.workflows.
    kinetics`` uses it to auto-resolve source SP calculations. A bundle
    names its source calculations by key instead and has no field for
    it, so it is NOT_APPLICABLE there.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "NOT_APPLICABLE"


NOT_APPLICABLE = _NotApplicable()


def collect_provenance_warnings(
    *,
    scientific_origin: ScientificOriginKind,
    software_release: object | None,
    workflow_tool_release: object | None,
    literature: object | None,
    freq_scale_factor: object | None = NOT_APPLICABLE,
    energy_level_of_theory: object | None = NOT_APPLICABLE,
    field_prefix: str = "",
) -> list[UploadWarning]:
    """Warn about provenance anchors a record could carry and does not.

    Request-agnostic, for the reason ``collect_statmech_content_warnings``
    is: the standalone routes and the two bundle roots make the same
    claim about the same rows, so they should report the same gaps in
    the same shape. Before this, the bundle roots reported nothing at
    all — the collectors existed and only the standalone routes called
    them.

    Callers pass the **effective** value, not the raw field. On the
    reaction bundle a per-species ``software_release`` falls back to the
    bundle-level ``analysis_software_release``, and the workflow
    persists the fallback, so warning on the raw per-species field would
    warn about provenance that was in fact recorded.

    :param field_prefix: Dot-path prefix naming the subject, e.g.
        ``"species['ch4'].statmech."``. A bundle carries many records
        and a bare ``software_release`` on a twenty-species deposit
        names none of them; ``UploadWarning.field`` is already
        documented as a dot-path and already carries indexed paths from
        the standalone kinetics route, so this needs no new machinery.
    :param freq_scale_factor: Statmech's extra anchor, or
        :data:`NOT_APPLICABLE` for record types that have none.
    :param energy_level_of_theory: Kinetics' extra anchor, or
        :data:`NOT_APPLICABLE`.
    """
    warnings: list[UploadWarning] = []
    if scientific_origin in _COMPUTATIONAL_ORIGINS:
        if software_release is None:
            warnings.append(
                _software_release_warning(f"{field_prefix}software_release")
            )
        if workflow_tool_release is None:
            warnings.append(
                _workflow_tool_release_warning(f"{field_prefix}workflow_tool_release")
            )
        if freq_scale_factor is None:
            warnings.append(
                _freq_scale_factor_warning(f"{field_prefix}freq_scale_factor")
            )
        if energy_level_of_theory is None:
            warnings.append(
                _level_of_theory_warning(f"{field_prefix}energy_level_of_theory")
            )
    elif literature is None:
        warnings.append(_literature_warning(f"{field_prefix}literature"))
    return warnings


# ---------------------------------------------------------------------------
# Per-product entry points
# ---------------------------------------------------------------------------


def collect_thermo_provenance_warnings(
    request: ThermoUploadRequest,
) -> list[UploadWarning]:
    """Structured warnings for provenance absent from a thermo upload."""
    return collect_provenance_warnings(
        scientific_origin=request.scientific_origin,
        software_release=request.software_release,
        workflow_tool_release=request.workflow_tool_release,
        literature=request.literature,
    )


def collect_transport_provenance_warnings(
    request: TransportUploadRequest,
) -> list[UploadWarning]:
    """Structured warnings for provenance absent from a transport upload."""
    return collect_provenance_warnings(
        scientific_origin=request.scientific_origin,
        software_release=request.software_release,
        workflow_tool_release=request.workflow_tool_release,
        literature=request.literature,
    )


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
    return collect_provenance_warnings(
        scientific_origin=request.scientific_origin,
        software_release=request.software_release,
        workflow_tool_release=request.workflow_tool_release,
        literature=request.literature,
        freq_scale_factor=request.freq_scale_factor,
    )


def collect_kinetics_provenance_warnings(
    request: KineticsUploadRequest,
) -> list[UploadWarning]:
    """Structured warnings for provenance absent from a kinetics upload.

    Computed kinetics additionally expects ``energy_level_of_theory``:
    without it, source SP calculations cannot be auto-resolved and the
    kinetics record loses its electronic-energy anchor.
    """
    return collect_provenance_warnings(
        scientific_origin=request.scientific_origin,
        software_release=request.software_release,
        workflow_tool_release=request.workflow_tool_release,
        literature=request.literature,
        energy_level_of_theory=request.energy_level_of_theory,
    )


def collect_kinetics_content_warnings(
    request: KineticsUploadRequest,
) -> list[UploadWarning]:
    """Adapter for the standalone ``/uploads/kinetics`` request shape.

    Kept so the standalone route reads unchanged. All three inputs exist on
    ``KineticsUploadRequest``, so all three are judged.
    """
    return collect_kinetics_content_warnings_for(
        scientific_origin=request.scientific_origin,
        interpretation_assignments=request.interpretation_assignments,
        network_kinetics_ref=request.network_kinetics_ref,
        tunneling_model=request.tunneling_model,
        tunneling_application=request.tunneling_application,
    )


def collect_kinetics_content_warnings_for(
    *,
    scientific_origin: ScientificOriginKind,
    interpretation_assignments: object | None = NOT_APPLICABLE,
    network_kinetics_ref: object | None = NOT_APPLICABLE,
    tunneling_model: object | None = None,
    tunneling_application: object | None = NOT_APPLICABLE,
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

    **Why the parameters, and why they default to** :data:`NOT_APPLICABLE`.

    This collector used to read ``interpretation_assignments``,
    ``network_kinetics_ref`` and ``tunneling_application`` straight off a
    ``KineticsUploadRequest``. ``BundleKineticsIn`` — the reaction bundle's
    kinetics block, and the model the ARC adapter actually deposits through
    — carries none of the three, but *does* carry ``tunneling_model``. So a
    bundle could declare ``tunneling_model='eckart'`` and, the moment this
    collector was wired to that route, be told to supply
    ``tunneling_application``: a field that does not exist on that model,
    on a payload whose ``SchemaBase`` is ``extra="forbid"``. A depositor
    following the advice would get a 422.

    :data:`NOT_APPLICABLE` is how a caller says *there is no field here to
    fill*, as distinct from ``None``/empty meaning *there is one and it was
    left empty*. Judging is opt-in: a caller that does not pass an argument
    is not asked about it, so a new caller cannot accidentally emit advice
    it has no field to act on.

    That the bundle must pass all three sentinels is a statement about the
    bundle's schema, not an endorsement of it — see ``BundleKineticsIn``'s
    docstring for the drift this records.

    :param interpretation_assignments: The assignment list, or
        :data:`NOT_APPLICABLE` where the payload has no such field.
    :param network_kinetics_ref: The master-equation handle, or
        :data:`NOT_APPLICABLE`. Only consulted to *suppress* the
        missing-TS-interpretation warning, never to raise one.
    :param tunneling_model: The reported label. Always available.
    :param tunneling_application: The typed evidence, or
        :data:`NOT_APPLICABLE`.
    """
    warnings: list[UploadWarning] = []
    if (
        scientific_origin in _COMPUTATIONAL_ORIGINS
        and interpretation_assignments is not NOT_APPLICABLE
        and not interpretation_assignments
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
    #
    # ``network_kinetics_ref`` is only ever read to *suppress* this warning,
    # so a caller with no such field is not silently accused of omitting one:
    # NOT_APPLICABLE is not ``None``, and only ``None`` lets the warning
    # through. The guard is unreachable anyway when
    # ``interpretation_assignments`` is NOT_APPLICABLE, since the sentinel is
    # not iterable — hence the explicit check before the comprehension.
    subjects: set[object] = set()
    if interpretation_assignments is not NOT_APPLICABLE:
        subjects = {
            "transition_state" if item.role == "transition_state" else item.role
            for item in interpretation_assignments
        }
    if (
        interpretation_assignments is not NOT_APPLICABLE
        and interpretation_assignments
        and "transition_state" not in subjects
        and network_kinetics_ref is None
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
    # The case #155 exists for. A caller that *has* a tunneling_application
    # field and left it empty gets told so; a caller that has no such field
    # gets nothing, because "attach the typed evidence" is not something it
    # can do. Note the condition is on the *evidence* being NOT_APPLICABLE,
    # not on the label: a bundle can and does declare ``tunneling_model``.
    if (
        tunneling_application is not NOT_APPLICABLE
        and tunneling_model not in (None, TunnelingModel.none)
        and tunneling_application is None
    ):
        warnings.append(
            UploadWarning(
                field="tunneling_application",
                code=W_MISSING_TUNNELING_APPLICATION,
                message=(
                    f"tunneling_model='{tunneling_model.value}' is declared "
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
