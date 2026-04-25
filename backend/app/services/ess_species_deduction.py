"""Deduce SpeciesEntry fields from ESS output results.

Bridges the ESS-agnostic ``ESSResult`` contract to the domain-specific
fields on ``SpeciesEntry``.  Each deduction returns the value plus a
confidence/source tag so callers can decide whether to auto-populate
or merely validate.

This module contains NO database dependencies — pure functions only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.ess_result import ESSResult


@dataclass(frozen=True)
class Deduction:
    """A single deduced value with provenance.

    :param field: SpeciesEntry field name (e.g. "kind", "term_symbol").
    :param value: The deduced value, or None if not deducible.
    :param source: Where the value came from (e.g. "freq.n_imag",
        "orca.point_group", "multiplicity+linearity").
    :param confidence: How reliable the deduction is.
        - "definitive": directly measured by the ESS (e.g. n_imag from freq)
        - "derived": computed from ESS outputs (e.g. term_symbol from mult+PG)
        - "heuristic": inferred with assumptions (e.g. ground state if no TD-DFT)
    """

    field: str
    value: Any
    source: str
    confidence: str  # "definitive" | "derived" | "heuristic"


def deduce_stationary_point_kind(result: ESSResult) -> Deduction | None:
    """Deduce stationary point classification from frequency analysis.

    The Hessian eigenvalue spectrum is the mathematical definition:
    - 0 imaginary frequencies → minimum
    - 1 imaginary frequency  → first-order saddle point (transition state)
    - 2+ imaginary            → higher-order saddle point (bad geometry)

    This function is context-agnostic — it reports what the freq analysis
    found.  The caller decides whether n_imag=1 belongs on a
    TransitionStateEntry rather than a SpeciesEntry.
    """
    if result.freq is None:
        return None

    n = result.freq.n_imag

    if n == 0:
        value = "minimum"
    elif n == 1:
        value = "transition_state"
    else:
        value = f"saddle_order_{n}"

    return Deduction(
        field="stationary_point_kind",
        value=value,
        source=f"freq.n_imag={n}",
        confidence="definitive",
    )


_EXCITED_STATE_METHODS = frozenset({
    "eom-ccsd", "eom-ccsd(t)", "eom-mp2",
    "cis", "cis(d)",
    "sf-tddft",
})

_AMBIGUOUS_METHODS = frozenset({
    "casscf", "caspt2", "nevpt2", "mrci",
    "dlpno-nevpt2", "dlpno-steom-ccsd",
})


def deduce_electronic_state(result: ESSResult) -> Deduction | None:
    """Deduce electronic state from calculation method and job keywords.

    The electronic state is an input choice, not an output measurement.
    This reads back what the user told the ESS to do:
    - TD-DFT / EOM / CIS keywords → excited
    - Multi-reference methods → ambiguous, return None
    - Everything else → ground (the method targets it by construction)
    """
    job_types = {jt.lower() for jt in result.meta.job_types}
    method = (result.meta.method or "").lower()

    # Explicit excited-state job type
    if "td" in job_types or "tddft" in job_types:
        return Deduction(
            field="electronic_state_kind",
            value="excited",
            source="job_type.td",
            confidence="definitive",
        )

    # Excited-state method
    if method in _EXCITED_STATE_METHODS:
        return Deduction(
            field="electronic_state_kind",
            value="excited",
            source=f"method.{method}",
            confidence="definitive",
        )

    # Multi-reference — genuinely ambiguous without root info
    if method in _AMBIGUOUS_METHODS:
        return None

    # Standard single-reference method without excited-state keywords
    if method or job_types:
        return Deduction(
            field="electronic_state_kind",
            value="ground",
            source="method.default",
            confidence="heuristic",
        )

    return None


def deduce_term_symbol(result: ESSResult) -> Deduction | None:
    """Deduce term symbol from multiplicity + symmetry.

    Delegates to the existing ``derive_term_symbol()`` but adds
    point_group from the ESS output when available.
    """
    if result.meta.multiplicity is None:
        return None

    from app.chemistry.species import derive_term_symbol

    term = derive_term_symbol(
        result.meta.multiplicity,
        point_group=(
            result.symmetry.point_group if result.symmetry else None
        ),
        is_linear=result.symmetry.is_linear if result.symmetry else None,
    )
    if term is None:
        return None

    source = "multiplicity"
    if result.symmetry and result.symmetry.point_group:
        source += f"+point_group({result.symmetry.point_group})"
        confidence = "derived"
    elif result.symmetry and result.symmetry.is_linear is not None:
        source += "+is_linear"
        confidence = "derived"
    else:
        confidence = "heuristic"

    return Deduction(
        field="term_symbol", value=term, source=source, confidence=confidence
    )


def deduce_charge_multiplicity(
    result: ESSResult,
) -> list[Deduction]:
    """Cross-check charge/multiplicity from ESS output."""
    deductions = []
    if result.meta.charge is not None:
        deductions.append(
            Deduction(
                field="charge",
                value=result.meta.charge,
                source=f"{result.meta.software_name}.header",
                confidence="definitive",
            )
        )
    if result.meta.multiplicity is not None:
        deductions.append(
            Deduction(
                field="multiplicity",
                value=result.meta.multiplicity,
                source=f"{result.meta.software_name}.header",
                confidence="definitive",
            )
        )
    return deductions


def deduce_all(result: ESSResult) -> list[Deduction]:
    """Run all deductions and return the full list."""
    deductions: list[Deduction] = []

    for fn in (
        deduce_stationary_point_kind,
        deduce_electronic_state,
        deduce_term_symbol,
    ):
        d = fn(result)
        if d is not None:
            deductions.append(d)

    deductions.extend(deduce_charge_multiplicity(result))
    return deductions
