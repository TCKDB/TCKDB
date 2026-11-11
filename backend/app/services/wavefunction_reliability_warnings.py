"""Wavefunction reliability diagnostics beyond their conventional thresholds are reported, not judged.

A calculation can carry numbers its program printed about how far the result can be trusted:
``calc_wavefunction_diagnostic`` (``T1``, ``D1``, the singles norm, the largest ``T2`` amplitude) and
``calc_spin_diagnostic`` (``<S^2>``). This module reads them back after the calculation is stored and reports three
things, each with its source or its absence of one named: a closed-shell T1 above Lee and Taylor's rule of thumb, an
``<S^2>`` further than the field's conventional 0.1 from S(S+1), and a program's printed ideal ``<S^2>`` that is not
S(S+1) of the declared multiplicity.

**Nothing is refused and nothing changes.** The result is stored as deposited. The checks never alter a value, a
review status, an evidence label or a selection, and a thermochemistry or kinetics record that cites a flagged
calculation earns no warning of its own: the warning is about the calculation. The tier is ``warn``: a
large single-excitation amplitude or a contaminated determinant is a notable property of a correct result, not a
contradiction.

What is checked
---------------
**Closed-shell T1.** One warning, :data:`W_T1_DIAGNOSTIC_ABOVE_CLOSED_SHELL_THRESHOLD`, when all of these hold:

* the multiplicity of the entry the calculation belongs to is 1, so it is treated as closed shell (read from the
  entry, never inferred from a printed ``<S^2>``);
* ``t1_diagnostic`` is stored (absent is absent: no row, or a row without a T1, earns nothing);
* the calculation's method is a coupled-cluster method with singles on a restricted reference (CCSD, CCSD(T), their
  F12 and local, DLPNO and PNO variants, CCSDT, CCSDT(Q), QCISD and QCISD(T); an unrestricted ``U`` method is not in
  scope, since an unrestricted reference is the case where programs define T1 differently; see
  :func:`is_coupled_cluster_method`);
* ``t1_diagnostic`` is strictly above :data:`T1_CLOSED_SHELL_THRESHOLD` (0.02).

The number is Lee and Taylor's: "a large T1 (i.e., >0.02) probably indicates the need for a multireference electron
correlation procedure" (Int. J. Quantum Chem. 1989, 36 (S23), 199, doi:10.1002/qua.560360824). It is a rule of thumb,
and a contested one: it raises many alarms for calculations that are accurate (Sprague and Irikura, Theor. Chem. Acc.
2014, 133, 1544; Faulstich et al., J. Phys. Chem. A 2023, 127, 9106), and ORCA's manual argues that T1 measures orbital
relaxation, not multireference character. The sentence therefore says only that the value is above the rule of thumb,
names its source and scope, and says that T1 "does not by itself establish multireference character".

**Why closed shell only.** It is the one regime where the definition is the same across programs (Lee and Taylor's
formula on restricted amplitudes) and where the number has a primary source. For an open-shell calculation the
programs print different quantities under one name (ORCA the UHF singles norm over sqrt(N); Molpro an undocumented
ROHF-based value), the commonly quoted open-shell figure (0.044) reached us only through secondary sources, and the
deposited Molpro value is, for a producer that reads the first ``T1 diagnostic`` line, the RMP2-stage value. No
open-shell T1 warning is raised, and no ``D1`` warning (``D1`` has no primary-text threshold we could verify and is
not deposited by the current producers). Both stay stored and shown. Conditions for adding open-shell T1 are recorded
in the register entry (:data:`CHECK_WAVEFUNCTION_RELIABILITY_DIAGNOSTICS`).

Where it runs
-------------
The diagnostics enter only through the producer payload, written in one place
(``calculation_resolution.persist_calculation_result``); no parser reads a ``T1 diagnostic`` line from an uploaded log,
and nothing writes a diagnostic after the deposit. So the check runs once per request, as
:func:`~app.services.scan_coordinate_conformance.collect_scan_coordinate_warnings` does, over every calculation the
request persisted, after they are flushed, in every workflow that does. A later path that stores a diagnostic must call
:func:`collect_wavefunction_reliability_warnings` over the calculations it touched.

**Spin contamination.** :data:`W_SPIN_CONTAMINATION_ABOVE_CONVENTIONAL_THRESHOLD` when the stored ``s_squared`` is
further from S(S+1) than 0.1, strictly, with S from the entry's declared multiplicity (never inferred from
``s_squared`` or ``s_squared_expected``). The threshold is a convention of the field, not a finding, and the sentence
says so. ``s_squared_annihilated`` is not used.

**Printed ideal value against the entry.** :data:`W_SPIN_EXPECTED_CONTRADICTS_MULTIPLICITY` when the
``s_squared_expected`` the program printed differs from S(S+1) of the entry's multiplicity by more than 1e-3. It
compares two declared quantities, needs no threshold source, and is separate from contamination: both can apply to
one calculation and then produce two warnings.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.chemistry.method_names import method_identity_key
from app.db.models.calculation import Calculation, CalculationSpinDiagnostic, CalculationWavefunctionDiagnostic
from app.db.models.common import SubmissionRecordType
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.species import Species, SpeciesEntry
from app.db.models.transition_state import TransitionStateEntry
from app.schemas.upload_warning import UploadWarning
from app.scientific_checks import (
    CheckTier,
    CodeChannel,
    ConstantThreshold,
    PythonCheck,
    ScientificCheck,
)
from app.services.deposit_warnings import note_warning_subject

__all__ = [
    "CHECK_WAVEFUNCTION_RELIABILITY_DIAGNOSTICS",
    "S_SQUARED_EXPECTED_TOLERANCE",
    "S_SQUARED_THRESHOLD",
    "T1_CLOSED_SHELL_THRESHOLD",
    "W_SPIN_CONTAMINATION_ABOVE_CONVENTIONAL_THRESHOLD",
    "W_SPIN_EXPECTED_CONTRADICTS_MULTIPLICITY",
    "W_T1_DIAGNOSTIC_ABOVE_CLOSED_SHELL_THRESHOLD",
    "collect_wavefunction_reliability_warnings",
    "ideal_s_squared",
    "is_coupled_cluster_method",
]

#: The warning a closed-shell coupled-cluster calculation earns when its T1 is above the conventional rule of thumb.
W_T1_DIAGNOSTIC_ABOVE_CLOSED_SHELL_THRESHOLD = "t1_diagnostic_above_closed_shell_threshold"

#: Lee and Taylor (1989): a closed-shell T1 above this "probably indicates the need for a multireference electron
#: correlation procedure". Strictly greater than: a value of exactly 0.02 is on the line, not past it.
T1_CLOSED_SHELL_THRESHOLD = 0.02

#: The warning a calculation earns when its stored ``<S^2>`` is further from S(S+1) than the conventional criterion.
W_SPIN_CONTAMINATION_ABOVE_CONVENTIONAL_THRESHOLD = "spin_contamination_above_conventional_threshold"

#: The warning a calculation earns when the ideal ``<S^2>`` its program printed is not S(S+1) of the entry's
#: multiplicity.
W_SPIN_EXPECTED_CONTRADICTS_MULTIPLICITY = "spin_expected_contradicts_multiplicity"

#: The conventional criterion for spin contamination: ``|<S^2> - S(S+1)| > 0.1``. A convention, not a finding: no
#: primary paper recommends it (see :data:`CHECK_WAVEFUNCTION_RELIABILITY_DIAGNOSTICS`). Strictly greater than.
S_SQUARED_THRESHOLD = 0.1

#: How far the ideal ``<S^2>`` a program prints may be from S(S+1) of the declared multiplicity before the two are
#: said to disagree. Printed values are rounded to 4 to 6 decimals, so this is far above print noise and far below the
#: gap between neighbouring spin states (0.75, 2, 3.75).
S_SQUARED_EXPECTED_TOLERANCE = 1e-3


def ideal_s_squared(multiplicity: int) -> float:
    """S(S+1) for the spin multiplicity, with S = (multiplicity - 1) / 2: 0 for a singlet, 0.75, 2, 3.75, ..."""
    spin = (multiplicity - 1) / 2
    return spin * (spin + 1)


#: A coupled-cluster method with singles on a restricted reference, after the identity key (lower case, curated
#: aliases): an optional ``rhf-`` reference, an optional local prefix (``dlpno-``, ``pno-l``, ``lpno-``, ``l``) or
#: ``r``,
#: CCSD / CCSDT / CCSDTQ / QCISD, an optional perturbative term (``(t)`` ``(t0)`` ``(t1)`` ``(t*)`` ``(q)`` ``(tq)`` or
#: ``[t]``), an optional F12 variant (``-f12``, ``-f12a`` to ``-f12d``) and an optional ``/ri`` or ``/cbs``. An
#: unrestricted ``u`` prefix is deliberately absent: an unrestricted reference is where programs define T1 differently.
#: CCD (no singles), CC2 and CC3 (approximate singles), EOM-CCSD, BCCD(T), MP2 and composite recipes are not in it.
_COUPLED_CLUSTER = re.compile(
    r"^(?:rhf-)?(?:dlpno-|pno-l|lpno-|l|r)?(?:ccsdtq|ccsdt|ccsd|qcisd)"
    r"(?:\((?:t|t0|t1|t\*|q|tq)\)|\[t\])?(?:-f12[abcd]?)?(?:/(?:ri|cbs))?$"
)


def is_coupled_cluster_method(method: str | None) -> bool:
    """True when ``method`` is a restricted coupled-cluster (or QCISD) method with singles, where T1 is defined.

    Anything else, including an unrestricted method, a method the table does not recognise and a composite recipe that
    contains a CCSD(T) step, is not: absent is absent.
    """
    if method is None or not method.strip():
        return False
    return _COUPLED_CLUSTER.fullmatch(method_identity_key(method)) is not None


@dataclass(frozen=True)
class _CalculationFacts:
    """What the checks need to know about one persisted calculation, read once."""

    calculation_id: int
    method: str | None
    multiplicity: int | None
    wavefunction: CalculationWavefunctionDiagnostic | None
    spin: CalculationSpinDiagnostic | None


def _t1_closed_shell(facts: _CalculationFacts) -> UploadWarning | None:
    """The closed-shell T1 warning, or ``None`` when any of its four conditions does not hold."""
    if facts.multiplicity != 1 or facts.wavefunction is None:
        return None
    t1 = facts.wavefunction.t1_diagnostic
    if t1 is None or not is_coupled_cluster_method(facts.method):
        return None
    if not t1 > T1_CLOSED_SHELL_THRESHOLD:
        return None
    return UploadWarning.from_code(
        field="wavefunction_diagnostic.t1_diagnostic",
        code=W_T1_DIAGNOSTIC_ABOVE_CLOSED_SHELL_THRESHOLD,
        context={
            "t1_diagnostic": float(t1),
            "threshold": T1_CLOSED_SHELL_THRESHOLD,
            "multiplicity": 1,
        },
    )


def _spin_contamination(facts: _CalculationFacts) -> UploadWarning | None:
    """The contamination warning: ``|s_squared - S(S+1)| > 0.1`` with S from the entry's multiplicity.

    Uses ``s_squared`` (the value the program printed for the calculation), never ``s_squared_annihilated``, and never
    the program's own ideal value: the expectation is the entry's declared multiplicity.
    """
    if facts.multiplicity is None or facts.spin is None:
        return None
    ideal = ideal_s_squared(facts.multiplicity)
    deviation = abs(facts.spin.s_squared - ideal)
    # Rounded so that a deviation of exactly 0.1 (2.1 against 2.0) is not pushed over the line by float noise.
    if not round(deviation, 9) > S_SQUARED_THRESHOLD:
        return None
    return UploadWarning.from_code(
        field="spin_diagnostic.s_squared",
        code=W_SPIN_CONTAMINATION_ABOVE_CONVENTIONAL_THRESHOLD,
        context={
            "s_squared": float(facts.spin.s_squared),
            "ideal_s_squared": ideal,
            "multiplicity": facts.multiplicity,
            "threshold": S_SQUARED_THRESHOLD,
        },
    )


def _spin_expected_contradicts_multiplicity(facts: _CalculationFacts) -> UploadWarning | None:
    """The program's printed ideal ``<S^2>`` is not S(S+1) of the declared multiplicity (not contamination)."""
    if facts.multiplicity is None or facts.spin is None or facts.spin.s_squared_expected is None:
        return None
    ideal = ideal_s_squared(facts.multiplicity)
    if not round(abs(facts.spin.s_squared_expected - ideal), 9) > S_SQUARED_EXPECTED_TOLERANCE:
        return None
    return UploadWarning.from_code(
        field="spin_diagnostic.s_squared_expected",
        code=W_SPIN_EXPECTED_CONTRADICTS_MULTIPLICITY,
        context={
            "s_squared_expected": float(facts.spin.s_squared_expected),
            "expected_from_entry": ideal,
            "multiplicity": facts.multiplicity,
        },
    )


#: The checks run on every calculation, in order. Each is independent of the others.
_CHECKS: tuple[Callable[[_CalculationFacts], UploadWarning | None], ...] = (
    _t1_closed_shell,
    _spin_contamination,
    _spin_expected_contradicts_multiplicity,
)


def _facts(session: Session, calculation_ids: list[int]) -> list[_CalculationFacts]:
    rows = session.execute(
        select(
            Calculation.id,
            LevelOfTheory.method,
            Species.multiplicity,
            TransitionStateEntry.multiplicity,
        )
        .outerjoin(LevelOfTheory, LevelOfTheory.id == Calculation.lot_id)
        .outerjoin(SpeciesEntry, SpeciesEntry.id == Calculation.species_entry_id)
        .outerjoin(Species, Species.id == SpeciesEntry.species_id)
        .outerjoin(TransitionStateEntry, TransitionStateEntry.id == Calculation.transition_state_entry_id)
        .where(Calculation.id.in_(calculation_ids))
    ).all()
    by_id = {row[0]: row for row in rows}
    wavefunction = {
        d.calculation_id: d
        for d in session.scalars(
            select(CalculationWavefunctionDiagnostic).where(
                CalculationWavefunctionDiagnostic.calculation_id.in_(calculation_ids)
            )
        )
    }
    spin = {
        d.calculation_id: d
        for d in session.scalars(
            select(CalculationSpinDiagnostic).where(CalculationSpinDiagnostic.calculation_id.in_(calculation_ids))
        )
    }
    facts: list[_CalculationFacts] = []
    for calculation_id in calculation_ids:
        row = by_id.get(calculation_id)
        if row is None:
            continue
        _, method, species_multiplicity, ts_multiplicity = row
        facts.append(
            _CalculationFacts(
                calculation_id=calculation_id,
                method=method,
                multiplicity=species_multiplicity if species_multiplicity is not None else ts_multiplicity,
                wavefunction=wavefunction.get(calculation_id),
                spin=spin.get(calculation_id),
            )
        )
    return facts


def collect_wavefunction_reliability_warnings(
    session: Session,
    calculation_ids: Iterable[int],
) -> list[UploadWarning]:
    """Warn on every calculation among ``calculation_ids`` whose stored diagnostic is past its conventional threshold.

    Run once a request's calculations are flushed, over every calculation id the request persisted (or reused), by
    every workflow that persists calculations. **Never refuses** and never changes anything it reads; see the module
    note. Each warning is placed on its calculation's own record.

    :param session: Active SQLAlchemy session.
    :param calculation_ids: Every calculation id the request persisted; calculations with no diagnostic row earn
        nothing.
    :returns: Warnings in the request's calculation order.
    """
    ids = list(dict.fromkeys(calculation_ids))
    if not ids:
        return []
    warnings: list[UploadWarning] = []
    for facts in _facts(session, ids):
        if facts.wavefunction is None and facts.spin is None:
            continue
        for check in _CHECKS:
            warning = check(facts)
            if warning is None:
                continue
            note_warning_subject(session, warning, SubmissionRecordType.calculation, facts.calculation_id)
            warnings.append(warning)
    return warnings


# ---------------------------------------------------------------------------
# Scientific check registration (see backend/app/scientific_checks)
# ---------------------------------------------------------------------------

CHECK_WAVEFUNCTION_RELIABILITY_DIAGNOSTICS = ScientificCheck(
    group="Wavefunction reliability diagnostics",
    sort_key=1,
    code=(
        W_T1_DIAGNOSTIC_ABOVE_CLOSED_SHELL_THRESHOLD,
        W_SPIN_CONTAMINATION_ABOVE_CONVENTIONAL_THRESHOLD,
        W_SPIN_EXPECTED_CONTRADICTS_MULTIPLICITY,
    ),
    asserts=(
        "A wavefunction reliability diagnostic is reported when it is past its conventional "
        "threshold: closed-shell coupled-cluster T1 above 0.02, a stored <S^2> (not the "
        "annihilated one) more than 0.1 from S(S+1), or a printed ideal <S^2> more than 1e-3 "
        "from S(S+1), with S in both taken from the entry's declared multiplicity and never "
        "from ``s_squared_expected``."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    enforced_by=(
        PythonCheck(collect_wavefunction_reliability_warnings),
        PythonCheck(is_coupled_cluster_method),
    ),
    escape_hatch="None; the check only warns.",
    thresholds=(
        ConstantThreshold(
            name="t1_closed_shell_threshold",
            value=T1_CLOSED_SHELL_THRESHOLD,
            unit="dimensionless (singles norm over the square root of the number of correlated electrons)",
            rationale=(
                "A conventional rule of thumb for closed-shell coupled-cluster calculations, applied "
                "strictly greater than."
            ),
        ),
        ConstantThreshold(
            name="s_squared_contamination_threshold",
            value=S_SQUARED_THRESHOLD,
            unit="dimensionless (absolute deviation of <S^2> from S(S+1))",
            rationale=(
                "A conventional absolute deviation of <S^2> from S(S+1), applied strictly greater "
                "than."
            ),
        ),
        ConstantThreshold(
            name="s_squared_expected_tolerance",
            value=S_SQUARED_EXPECTED_TOLERANCE,
            unit="dimensionless (absolute difference between two <S^2> values)",
            rationale=(
                "Programs print the ideal <S^2> to four to six decimals, so 1e-3 is above print noise "
                "and far below the gap between spin states."
            ),
        ),
    ),
)
