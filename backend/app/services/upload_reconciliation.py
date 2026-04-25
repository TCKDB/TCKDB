"""Reconcile user-provided upload fields against available evidence.

Compares what the user claimed (e.g. ``species_entry_kind=minimum``)
against what the uploaded data shows (e.g. ``freq_result.n_imag=1``,
method keywords, multiplicity) and produces non-blocking warnings
for contradictions.

No database dependencies — pure functions only.
"""

from __future__ import annotations

from app.db.models.common import StationaryPointKind
from app.schemas.fragments.calculation import CalculationWithResultsPayload
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.upload_warning import UploadWarning
from app.schemas.workflows.conformer_upload import ConformerUploadStatmechPayload
from app.services.ess_result import (
    ESSFreqResult,
    ESSJobMeta,
    ESSResult,
    ESSSymmetry,
)
from app.services.ess_species_deduction import Deduction, deduce_all

# ---------------------------------------------------------------------------
# Warning codes
# ---------------------------------------------------------------------------

# Layer 1: freq-based
W_N_IMAG_CONTRADICTS_MINIMUM = "n_imag_contradicts_minimum"
W_N_IMAG_SUGGESTS_TS = "n_imag_suggests_transition_state"
W_N_IMAG_HIGHER_ORDER_SADDLE = "n_imag_higher_order_saddle"

# Layer 2: deduction-based
W_ELECTRONIC_STATE_CONTRADICTS_METHOD = "electronic_state_contradicts_method"
W_TERM_SYMBOL_MISMATCH = "term_symbol_mismatch"
W_CHARGE_MISMATCH = "charge_mismatch"
W_MULTIPLICITY_MISMATCH = "multiplicity_mismatch"


# ---------------------------------------------------------------------------
# Freq extraction
# ---------------------------------------------------------------------------


def extract_freq_n_imag(
    primary: CalculationWithResultsPayload,
    additional: list[CalculationWithResultsPayload],
) -> int | None:
    """Extract ``n_imag`` from the primary or additional calculations.

    Returns the first non-None ``n_imag`` found, or ``None`` if no
    frequency result is present in any calculation.
    """
    if primary.freq_result is not None and primary.freq_result.n_imag is not None:
        return primary.freq_result.n_imag
    for calc in additional:
        if calc.freq_result is not None and calc.freq_result.n_imag is not None:
            return calc.freq_result.n_imag
    return None


# ---------------------------------------------------------------------------
# ESSResult builder from upload payload
# ---------------------------------------------------------------------------


def build_ess_result_from_upload(
    payload: SpeciesEntryIdentityPayload,
    *,
    primary_calc: CalculationWithResultsPayload | None = None,
    additional_calcs: list[CalculationWithResultsPayload] | None = None,
    statmech: ConformerUploadStatmechPayload | None = None,
) -> ESSResult | None:
    """Build an ``ESSResult`` from structured upload payload fields.

    Returns ``None`` if there is not enough data to build a meaningful
    result (e.g. no calculation provided).
    """
    if primary_calc is None:
        return None

    calcs = [primary_calc] + (additional_calcs or [])

    # Job types from all calculations
    job_types = [c.type.value for c in calcs]

    # Freq result from primary or additional
    freq = None
    freq_n_imag = extract_freq_n_imag(primary_calc, additional_calcs or [])
    if freq_n_imag is not None:
        freq_payload = None
        for c in calcs:
            if c.freq_result is not None and c.freq_result.n_imag is not None:
                freq_payload = c.freq_result
                break
        if freq_payload is not None:
            freq = ESSFreqResult(
                n_imag=freq_payload.n_imag or 0,
                imag_freq_cm1=freq_payload.imag_freq_cm1,
                zpe_hartree=freq_payload.zpe_hartree,
            )

    # Symmetry from statmech payload
    symmetry = None
    if statmech is not None and (
        statmech.point_group is not None or statmech.is_linear is not None
    ):
        symmetry = ESSSymmetry(
            point_group=statmech.point_group,
            is_linear=statmech.is_linear,
        )

    meta = ESSJobMeta(
        software_name=primary_calc.software_release.name.lower(),
        software_version=primary_calc.software_release.version,
        software_build=primary_calc.software_release.build,
        charge=payload.charge,
        multiplicity=payload.multiplicity,
        method=primary_calc.level_of_theory.method,
        basis=primary_calc.level_of_theory.basis,
        aux_basis=primary_calc.level_of_theory.aux_basis,
        job_types=job_types,
    )

    return ESSResult(
        meta=meta,
        freq=freq,
        symmetry=symmetry,
        parser_version="upload_payload_v1",
    )


# ---------------------------------------------------------------------------
# Deduction → payload field mapping
# ---------------------------------------------------------------------------

# Maps Deduction.field → (payload attribute name, value accessor)
_DEDUCTION_FIELD_MAP: dict[str, tuple[str, str]] = {
    "stationary_point_kind": ("species_entry_kind", "value"),
    "electronic_state_kind": ("electronic_state_kind", "value"),
    "term_symbol": ("term_symbol", None),
    "charge": ("charge", None),
    "multiplicity": ("multiplicity", None),
}

# Maps Deduction.field → warning code for contradictions
_DEDUCTION_WARNING_CODES: dict[str, str] = {
    "stationary_point_kind": W_N_IMAG_CONTRADICTS_MINIMUM,
    "electronic_state_kind": W_ELECTRONIC_STATE_CONTRADICTS_METHOD,
    "term_symbol": W_TERM_SYMBOL_MISMATCH,
    "charge": W_CHARGE_MISMATCH,
    "multiplicity": W_MULTIPLICITY_MISMATCH,
}


def _get_payload_value(
    payload: SpeciesEntryIdentityPayload,
    field: str,
) -> object:
    """Get the comparable value from the payload for a deduction field."""
    mapping = _DEDUCTION_FIELD_MAP.get(field)
    if mapping is None:
        return None
    attr_name, value_accessor = mapping
    val = getattr(payload, attr_name, None)
    if val is None:
        return None
    if value_accessor == "value":
        return val.value if hasattr(val, "value") else val
    return val


# ---------------------------------------------------------------------------
# Layer 1: direct n_imag checks (structural warnings)
# ---------------------------------------------------------------------------

_MINIMUM_KINDS = frozenset({StationaryPointKind.minimum, StationaryPointKind.vdw_complex})


def _check_n_imag(
    kind: StationaryPointKind,
    n_imag: int,
) -> list[UploadWarning]:
    """Check consistency between stationary point kind and n_imag."""
    warnings: list[UploadWarning] = []

    if n_imag == 0:
        return warnings

    if n_imag == 1:
        if kind in _MINIMUM_KINDS:
            warnings.append(UploadWarning(
                field="species_entry_kind",
                code=W_N_IMAG_CONTRADICTS_MINIMUM,
                message=(
                    f"Frequency analysis shows 1 imaginary frequency, "
                    f"but species_entry_kind is '{kind.value}'. "
                    f"A minimum should have 0 imaginary frequencies."
                ),
            ))
        warnings.append(UploadWarning(
            field="species_entry_kind",
            code=W_N_IMAG_SUGGESTS_TS,
            message=(
                "Frequency analysis shows exactly 1 imaginary frequency, "
                "which is the signature of a transition state. "
                "If this is a TS, use the transition-state upload endpoint instead."
            ),
        ))
        return warnings

    # n_imag >= 2
    warnings.append(UploadWarning(
        field="species_entry_kind",
        code=W_N_IMAG_HIGHER_ORDER_SADDLE,
        message=(
            f"Frequency analysis shows {n_imag} imaginary frequencies. "
            f"This is a higher-order saddle point, not a valid minimum "
            f"or transition state. The geometry may need re-optimization."
        ),
    ))
    return warnings


# ---------------------------------------------------------------------------
# Layer 2: deduction-based reconciliation
# ---------------------------------------------------------------------------


def _reconcile_deduction(
    deduction: Deduction,
    payload: SpeciesEntryIdentityPayload,
) -> UploadWarning | None:
    """Compare a single deduction against the payload and return a warning if mismatched."""
    payload_value = _get_payload_value(payload, deduction.field)

    # Skip fields we can't map or where the payload has no value to compare
    if deduction.field not in _DEDUCTION_FIELD_MAP:
        return None

    # For nullable fields (term_symbol): if user didn't provide one, no contradiction
    if payload_value is None:
        return None

    # Compare
    if str(payload_value) == str(deduction.value):
        return None

    code = _DEDUCTION_WARNING_CODES.get(deduction.field, "deduction_mismatch")
    attr_name = _DEDUCTION_FIELD_MAP[deduction.field][0]

    return UploadWarning(
        field=attr_name,
        code=code,
        message=(
            f"Provided {attr_name}='{payload_value}' but "
            f"{deduction.source} indicates '{deduction.value}'."
        ),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def reconcile_species_entry(
    payload: SpeciesEntryIdentityPayload,
    *,
    freq_n_imag: int | None = None,
) -> list[UploadWarning]:
    """Layer 1: reconcile using only freq n_imag (no calculation context).

    Use :func:`reconcile_species_entry_full` when calculation and
    statmech data are available for richer deduction-based checks.
    """
    warnings: list[UploadWarning] = []
    if freq_n_imag is not None:
        warnings.extend(_check_n_imag(payload.species_entry_kind, freq_n_imag))
    return warnings


def reconcile_species_entry_full(
    payload: SpeciesEntryIdentityPayload,
    *,
    primary_calc: CalculationWithResultsPayload | None = None,
    additional_calcs: list[CalculationWithResultsPayload] | None = None,
    statmech: ConformerUploadStatmechPayload | None = None,
) -> list[UploadWarning]:
    """Layer 2: reconcile using full deduction pipeline.

    Builds an ``ESSResult`` from the upload payload, runs all deduction
    functions, and compares each deduction against the user-provided
    values.  Includes Layer 1 n_imag structural warnings.

    :param payload: The user-provided species-entry identity.
    :param primary_calc: Primary calculation payload (opt/freq/sp).
    :param additional_calcs: Additional calculations (freq, sp, etc.).
    :param statmech: Optional statmech payload with symmetry info.
    :returns: List of warnings (may be empty).
    """
    warnings: list[UploadWarning] = []
    additional = additional_calcs or []

    # Layer 1: structural n_imag checks
    freq_n_imag = None
    if primary_calc is not None:
        freq_n_imag = extract_freq_n_imag(primary_calc, additional)
    if freq_n_imag is not None:
        warnings.extend(_check_n_imag(payload.species_entry_kind, freq_n_imag))

    # Layer 2: deduction-based checks
    ess_result = build_ess_result_from_upload(
        payload,
        primary_calc=primary_calc,
        additional_calcs=additional,
        statmech=statmech,
    )
    if ess_result is None:
        return warnings

    deductions = deduce_all(ess_result)
    for d in deductions:
        # Skip stationary_point_kind — already handled by Layer 1's
        # more specific n_imag warnings
        if d.field == "stationary_point_kind":
            continue
        w = _reconcile_deduction(d, payload)
        if w is not None:
            warnings.append(w)

    return warnings
