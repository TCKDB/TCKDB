"""Consistency between a declared stationary point and its frequency evidence.

The Hessian eigenvalue spectrum is what a stationary point *is*: zero
imaginary modes is a minimum, one is a first-order saddle point, two or
more is a higher-order saddle. For minima that statement survives the
translation into a database row unchanged, and this module still refuses
a minimum whose own frequency evidence contradicts it.

For transition states it does not survive, and
`ADR 0012 <../../../../docs/adr/0012-imaginary-modes-are-judged-by-magnitude-not-counted.md>`_
replaced the count with a magnitude-and-protocol judgement. A deposit
does not contain the exact Hessian at the exact stationary point on a
smooth surface; it contains a finite-precision Hessian at an
approximately converged geometry on a quadrature grid. Two scientifically
correct calculations of the same saddle point can return ``n_imag == 1``
and ``n_imag == 3``, so a gate a depositor passes by switching to
``Int=UltraFine`` is not a gate on science. Worse, its cheapest
workaround is deleting a line from the frequency list.

What replaces it is narrower and does survive:

======================  ==========================================  =====================
declared                rule                                        tier
======================  ==========================================  =====================
``minimum``             ``n_imag == 0``                             block
``vdw_complex``         ``n_imag == 0`` *expected*                  warn
transition state        at least one imaginary mode                 block
transition state        exactly one designated reaction coordinate  block
transition state        no undeclared extra at or above |ω_RC|      block
transition state        extra imaginary modes, all below τ          warn
transition state        an extra imaginary mode at or above τ       warn + structural flag
transition state        ``|ω_RC| >= 100`` cm⁻¹                      warn
======================  ==========================================  =====================

τ is not a constant. The noise floor of a Hessian is flat in ω², not in
ω, so the uncertainty in a frequency diverges as ω → 0 and the same
−42 cm⁻¹ is real negative curvature under one protocol and
indistinguishable from zero under another. τ is therefore read from the
execution provenance the record actually carries — see
:func:`resolve_tau` — and every finding it decides carries the τ it used
and why, so a reader can re-decide later.

**τ never decides between blocking and warning.** Every blocking rule
above is a contract about what the record *says*, not about magnitude;
τ only separates a quiet warning from a flagged one. That is what makes
it safe to resolve τ from provenance that a payload may or may not
carry: missing provenance changes how loudly a record is flagged, never
whether it is accepted.

Why the two minimum kinds split, given that a van der Waals complex is
formally also a minimum:

* A covalently bound **minimum** whose own frequency evidence reports an
  imaginary mode is mislabelled. The correct response is to re-optimise
  on a tighter integration grid or to declare it as something else, so
  refusing the deposit is right — no correct calculation produces the
  record as submitted.
* A **van der Waals complex** is held together by intermolecular forces,
  and its intermolecular stretch, bends and hindered internal rotations
  sit below roughly 50 cm⁻¹. That is the region where numerical noise in
  a finite-difference or quadrature-grid Hessian is comparable to the
  true curvature, so a small imaginary mode there is usually a grid
  artifact rather than evidence of a saddle point. Refusing it would
  force an expensive re-run for a physically meaningless mode, so it is
  recorded and flagged instead. This is what makes ``vdw_complex`` earn
  its separate enum member: it is the only place the two kinds behave
  differently.
* A **transition state** with zero imaginary modes has no reaction
  coordinate at all and is not the thing it claims to be, so that still
  blocks.
* A transition state whose reaction coordinate is very small is
  *suspicious* but can be perfectly real — flat barriers and variational
  transition states genuinely produce them. Magnitude is therefore a
  quality expectation, never a definition, and ADR 0008 names it
  explicitly as a check that must not block.

Absence is never contradiction: a payload with no frequency evidence
produces no findings at all, in every case.

This module is the **single owner** of every imaginary-mode finding, and
ADR 0008 §9 means that literally: the read-time trust rubric cites the
judgement recorded here rather than re-deriving it from ``n_imag``. See
``backend/app/services/trust/rubrics.py``.

Pure functions only — no Pydantic, no backend imports, no payload
shapes. Callers extract scalars from whatever shape they hold and pass
them in.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.enums import (
    HessianMethod,
    ImaginaryModeDisposition,
    StationaryPointKind,
)

# ---------------------------------------------------------------------------
# The tunable expectation on the reaction coordinate itself
# ---------------------------------------------------------------------------

#: Magnitude below which a transition state's reaction coordinate is
#: reported as suspiciously soft (cm⁻¹). This is an expectation, not a
#: definition — it never blocks. The value is a starting point rather
#: than a physical constant: reaction coordinates for hydrogen transfers
#: run to thousands of cm⁻¹, while genuinely flat or variational barriers
#: can fall well under 100, so the threshold trades false positives on
#: loose saddle points against silence on under-converged ones. It is
#: also the scale that separates a van der Waals complex's soft
#: intermolecular modes from a real reaction coordinate, and is reused
#: for that judgement below.
#:
#: Unlike :func:`resolve_tau`, this one really is fixed in code, because
#: it is a statement about chemistry (what a reaction coordinate looks
#: like) rather than about numerics (what the Hessian noise floor is).
TS_IMAGINARY_FREQUENCY_MIN_CM1: float = 100.0


# ---------------------------------------------------------------------------
# τ — the protocol-dependent noise floor
# ---------------------------------------------------------------------------

#: Analytic Hessian on a tight grid with a tight optimisation.
TAU_ANALYTIC_TIGHT_CM1: float = 15.0
#: Analytic Hessian, default grid and tolerances.
TAU_ANALYTIC_DEFAULT_CM1: float = 30.0
#: Finite-difference Hessian built from analytic gradients.
TAU_FINITE_DIFFERENCE_GRADIENT_CM1: float = 50.0
#: Finite-difference Hessian built from energies; semi-empirical or
#: numerical composite protocols.
TAU_FINITE_DIFFERENCE_ENERGY_CM1: float = 80.0
#: The protocol was not recorded. Deliberately equal to the
#: finite-difference-from-gradients value: assuming the *better* of the
#: two plausible unrecorded cases would flag modes that are only noise.
TAU_PROTOCOL_NOT_RECORDED_CM1: float = 50.0

#: Canonical ``calculation_parameter`` keys :func:`resolve_tau_from_parameters`
#: reads. Named here so the scientific-check register can declare exactly
#: which recorded provenance the threshold is derived from, and so a drift
#: guard can check those keys still exist in the parameter vocabulary.
TAU_PARAMETER_KEYS: tuple[str, ...] = (
    "freq.hessian_method",
    "grid.quality",
    "opt.convergence",
)

#: ``grid.quality`` values that count as a tight integration grid. Values
#: are whatever the parsers emit — Gaussian passes its raw grid token
#: through, ORCA emits its ``DefGridN`` / ``GridN`` keyword.
_TIGHT_GRID_VALUES = frozenset(
    {
        "ultrafine",
        "superfine",
        "ultrafinegrid",
        "defgrid3",
        "grid5",
        "grid6",
        "grid7",
        "99590",
        "-99590",
        "199974",
    }
)

#: ``opt.convergence`` values that count as a tight optimisation.
_TIGHT_OPT_VALUES = frozenset({"tight", "very_tight", "verytight"})


class TauBasis(str, Enum):
    """Why :func:`resolve_tau` returned the value it did.

    Stored alongside the τ it explains. ADR 0012 requires that a reader
    can re-decide a borderline mode later, and "τ was 50" is not
    re-decidable without "…because nothing recorded says how the Hessian
    was built".
    """

    #: Analytic second derivatives, tight grid, tight optimisation.
    analytic_tight = "analytic_tight"
    #: Analytic second derivatives, anything less than tight elsewhere.
    analytic_default = "analytic_default"
    #: Hessian from finite differences of analytic gradients.
    finite_difference_gradient = "finite_difference_gradient"
    #: Hessian from finite differences of energies.
    finite_difference_energy = "finite_difference_energy"
    #: The frequency job's Hessian method is not in the record.
    protocol_not_recorded = "protocol_not_recorded"


@dataclass(frozen=True)
class TauResolution:
    """The τ used for one judgement, and the provenance that chose it.

    :param tau_cm1: The threshold actually applied, in cm⁻¹.
    :param basis: Which row of ADR 0012's protocol table was matched.
    :param reason: Producer-facing prose naming the recorded parameters
        that decided it, including the ones that were absent.
    :param hessian_method: The frequency job's Hessian method as
        recorded, or ``None`` when it was not recorded.
    :param grid_quality: ``grid.quality`` as recorded, or ``None``.
    :param opt_convergence: ``opt.convergence`` as recorded, or ``None``.
    """

    tau_cm1: float
    basis: TauBasis
    reason: str
    hessian_method: HessianMethod | None = None
    grid_quality: str | None = None
    opt_convergence: str | None = None


def _normalise(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip().lower()
    return stripped or None


def resolve_tau(
    hessian_method: HessianMethod | str | None = None,
    grid_quality: str | None = None,
    opt_convergence: str | None = None,
) -> TauResolution:
    """Resolve ADR 0012's τ from recorded execution provenance.

    The table is ADR 0012's, and the fallback is the conservative row:
    an unrecorded Hessian method is never assumed analytic, because
    assuming the better case would flag genuine noise as a structural
    finding.

    Note what is deliberately *not* consulted. ``opt.initial_hessian``
    (Gaussian ``CalcFC`` / ``CalcAll``) names where the optimiser got its
    starting Hessian, which is a different object from the one the
    frequency job diagonalised; using it here would look like evidence
    and be a guess. ``integral.accuracy``, ``opt.eigen_test`` and
    ``opt.saddle_order`` are recorded but do not move the noise floor of
    a small eigenvalue enough to earn a row in the table.

    :param hessian_method: The frequency job's second-derivative method,
        as recorded. ``None`` — the common case, because most outputs do
        not say — selects the conservative τ.
    :param grid_quality: Recorded ``grid.quality``.
    :param opt_convergence: Recorded ``opt.convergence``.
    :returns: The threshold and the provenance that chose it.
    """
    grid = _normalise(grid_quality)
    convergence = _normalise(opt_convergence)

    method: HessianMethod | None
    if hessian_method is None:
        method = None
    elif isinstance(hessian_method, HessianMethod):
        method = hessian_method
    else:
        try:
            method = HessianMethod(_normalise(hessian_method))
        except ValueError:
            method = None

    if method is None:
        return TauResolution(
            tau_cm1=TAU_PROTOCOL_NOT_RECORDED_CM1,
            basis=TauBasis.protocol_not_recorded,
            reason=(
                f"tau = {TAU_PROTOCOL_NOT_RECORDED_CM1:.0f} cm-1: no "
                f"'freq.hessian_method' parameter is recorded for this "
                f"calculation, so it is not known whether the second "
                f"derivatives were analytic or finite-difference. The "
                f"conservative value is used rather than assuming the "
                f"better case. Record Gaussian 'Freq=Numer' or ORCA "
                f"'NumFreq'/'AnFreq' in the calculation parameters to "
                f"tighten it."
            ),
            grid_quality=grid,
            opt_convergence=convergence,
        )

    if method is HessianMethod.finite_difference_energy:
        return TauResolution(
            tau_cm1=TAU_FINITE_DIFFERENCE_ENERGY_CM1,
            basis=TauBasis.finite_difference_energy,
            reason=(
                f"tau = {TAU_FINITE_DIFFERENCE_ENERGY_CM1:.0f} cm-1: the "
                f"Hessian was built by finite differences of energies, "
                f"which inherits SCF noise divided by the square of the "
                f"displacement step."
            ),
            hessian_method=method,
            grid_quality=grid,
            opt_convergence=convergence,
        )

    if method is HessianMethod.finite_difference_gradient:
        return TauResolution(
            tau_cm1=TAU_FINITE_DIFFERENCE_GRADIENT_CM1,
            basis=TauBasis.finite_difference_gradient,
            reason=(
                f"tau = {TAU_FINITE_DIFFERENCE_GRADIENT_CM1:.0f} cm-1: the "
                f"Hessian was built by finite differences of analytic "
                f"gradients."
            ),
            hessian_method=method,
            grid_quality=grid,
            opt_convergence=convergence,
        )

    tight_grid = grid in _TIGHT_GRID_VALUES
    tight_opt = convergence in _TIGHT_OPT_VALUES
    if tight_grid and tight_opt:
        return TauResolution(
            tau_cm1=TAU_ANALYTIC_TIGHT_CM1,
            basis=TauBasis.analytic_tight,
            reason=(
                f"tau = {TAU_ANALYTIC_TIGHT_CM1:.0f} cm-1: analytic second "
                f"derivatives on integration grid {grid!r} with "
                f"optimisation convergence {convergence!r}."
            ),
            hessian_method=method,
            grid_quality=grid,
            opt_convergence=convergence,
        )

    missing = []
    if not tight_grid:
        missing.append(
            f"grid.quality={grid!r}" if grid else "grid.quality not recorded"
        )
    if not tight_opt:
        missing.append(
            f"opt.convergence={convergence!r}"
            if convergence
            else "opt.convergence not recorded"
        )
    return TauResolution(
        tau_cm1=TAU_ANALYTIC_DEFAULT_CM1,
        basis=TauBasis.analytic_default,
        reason=(
            f"tau = {TAU_ANALYTIC_DEFAULT_CM1:.0f} cm-1: analytic second "
            f"derivatives, but not on a tight grid with a tight "
            f"optimisation ({'; '.join(missing)})."
        ),
        hessian_method=method,
        grid_quality=grid,
        opt_convergence=convergence,
    )


def resolve_tau_from_parameters(
    parameters: Iterable[tuple[str | None, str | None]],
) -> TauResolution:
    """Resolve τ from ``(canonical_key, canonical_value)`` parameter pairs.

    Takes bare pairs rather than a payload or an ORM row so this module
    keeps its no-Pydantic, no-backend contract. Callers pass whatever
    they hold: upload payload observations before persistence, or
    ``calculation_parameter`` rows after it.

    Later pairs win, so a caller may layer a more specific source over a
    general one.

    :param parameters: ``(canonical_key, canonical_value)`` pairs. Keys
        outside :data:`TAU_PARAMETER_KEYS` are ignored.
    :returns: The threshold and the provenance that chose it.
    """
    observed: dict[str, str | None] = {}
    for key, value in parameters:
        if key in TAU_PARAMETER_KEYS:
            observed[key] = value
    return resolve_tau(
        hessian_method=observed.get("freq.hessian_method"),
        grid_quality=observed.get("grid.quality"),
        opt_convergence=observed.get("opt.convergence"),
    )


# ---------------------------------------------------------------------------
# Machine-readable codes
# ---------------------------------------------------------------------------

#: Frequency evidence reports an imaginary mode on an entry declared a
#: minimum. Blocking for ``minimum``; reported as a warning for
#: ``vdw_complex``, where the same fact is usually Hessian noise. The
#: code names the *finding*; the declared kind decides the tier.
W_N_IMAG_CONTRADICTS_MINIMUM = "n_imag_contradicts_minimum"

#: Two or more imaginary modes. Folded into the blocking message for
#: ``minimum``; reported as a warning for ``vdw_complex``.
W_N_IMAG_HIGHER_ORDER_SADDLE = "n_imag_higher_order_saddle"

#: Narrowed by ADR 0008's tier split. Before the split this fired on
#: every species entry with one imaginary mode, alongside
#: ``W_N_IMAG_CONTRADICTS_MINIMUM``, and said "if this is a TS, use the
#: transition-state endpoint". That advice now lives inside the blocking
#: message for ``minimum``, and the only declared kind that still reaches
#: the warning tier with an imaginary mode is ``vdw_complex`` — where
#: suggesting a transition state would usually be wrong, because the mode
#: is intermolecular noise.
#:
#: So the code keeps its name and gains a precise, reachable subject: a
#: van der Waals complex whose imaginary mode is *too stiff* to be
#: intermolecular noise (at or above
#: :data:`TS_IMAGINARY_FREQUENCY_MIN_CM1`) is not showing a grid
#: artifact, and really does look like a first-order saddle point.
W_N_IMAG_SUGGESTS_TS = "n_imag_suggests_transition_state"

#: A transition state whose frequency evidence reports no imaginary mode
#: at all. There is no reaction coordinate, so the structure is not a
#: transition state. Definitional, therefore blocking. This is what
#: survives of the old ``transition_state_n_imag_not_one``, which ADR
#: 0012 retired because it also refused correct higher-order records.
W_TS_NO_IMAGINARY_MODE = "transition_state_no_imaginary_mode"

#: More than one imaginary mode, and the payload does not say which one
#: is the reaction coordinate. Blocking, because designating exactly one
#: reaction coordinate and removing it from the partition function is the
#: contract every transition-state-theory code enforces; a record that
#: cannot answer "which mode" cannot be used as a transition state.
W_TS_REACTION_COORDINATE_NOT_DESIGNATED = (
    "transition_state_reaction_coordinate_not_designated"
)

#: An undeclared extra imaginary mode is at least as stiff as the
#: designated reaction coordinate. Blocking: at that point the
#: designation is an assertion the record does not support, and nothing
#: honest can be said about which mode is the barrier.
W_TS_REACTION_COORDINATE_AMBIGUOUS = (
    "transition_state_reaction_coordinate_ambiguous"
)

#: Extra imaginary modes exist but all sit below τ, so their sign is not
#: determined at the protocol that produced them. Accepted with a
#: warning and no structural flag.
W_TS_EXTRA_IMAGINARY_MODES_BELOW_TAU = (
    "transition_state_extra_imaginary_modes_below_tau"
)

#: An extra imaginary mode sits at or above τ. Real negative curvature at
#: this protocol, so the record is a genuine higher-order saddle: accepted,
#: warned, and structurally flagged.
W_TS_EXTRA_IMAGINARY_MODE_ABOVE_TAU = (
    "transition_state_extra_imaginary_mode_above_tau"
)

#: A reaction coordinate was designated but the frequency list was not
#: deposited, so the extra modes cannot be judged against τ at all.
#: Accepted, warned and flagged — ADR 0012 requires the complete signed
#: frequency list precisely so this case does not arise.
W_TS_EXTRA_IMAGINARY_MODES_NOT_ASSESSABLE = (
    "transition_state_extra_imaginary_modes_not_assessable"
)

#: The transition state's reaction coordinate is below
#: :data:`TS_IMAGINARY_FREQUENCY_MIN_CM1` in magnitude. An expectation,
#: therefore a warning.
W_TS_IMAG_FREQ_TOO_SMALL = "transition_state_imaginary_frequency_too_small"


#: Stationary-point kinds that are, by definition, minima on the
#: potential energy surface: every one of them *expects* zero imaginary
#: modes. Membership says nothing about the tier — ``minimum`` and
#: ``vdw_complex`` share the expectation and differ in the consequence.
MINIMUM_KINDS = frozenset({StationaryPointKind.minimum, StationaryPointKind.vdw_complex})


#: The remediation ladder, named in every extra-mode warning. The third
#: rung is the one that matters: it turns the warning from a nuisance
#: into an instrument for finding real scientific errors.
_REMEDIATION = (
    "Remediation, in order: re-run the frequency job on a tighter "
    "integration grid (most modes between -10 and -40 cm-1 vanish); "
    "re-optimise more tightly and repeat; if they persist, displace "
    "along each extra imaginary mode by 0.1-0.3 A and re-optimise -- if "
    "the energy drops and the extra modes disappear, the original was a "
    "genuine higher-order saddle. If the mode is a torsion, scan it and "
    "treat it as a hindered rotor. Passing IRC evidence makes a soft "
    "extra mode moot and is the strongest single thing you can supply."
)


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


class ValidationTier(str, Enum):
    """Consequence of a finding, in ADR 0008's vocabulary."""

    #: Refuse the payload. Reserved for definitions and contracts.
    block = "block"
    #: Accept the payload and record a machine-readable annotation.
    warn = "warn"


@dataclass(frozen=True)
class ImaginaryMode:
    """One imaginary mode as the depositor described it.

    :param frequency_cm1: Signed frequency in cm⁻¹. Negative by
        convention for an imaginary mode; only the magnitude is used, so
        a producer that hands over positive magnitudes is not silently
        misread.
    :param mode_index: 1-based index within the frequency list, used to
        match against the designated reaction coordinate.
    :param disposition: What this mode is, if the depositor said.
        ``None`` means undeclared, which is the state the blocking rule
        cares about.
    """

    frequency_cm1: float
    mode_index: int | None = None
    disposition: ImaginaryModeDisposition | None = None

    @property
    def magnitude_cm1(self) -> float:
        return abs(self.frequency_cm1)


@dataclass(frozen=True)
class StationaryPointFinding:
    """One consistency finding about one piece of frequency evidence.

    :param tier: Whether the finding refuses the payload or annotates it.
    :param code: Stable machine-readable code, shared across tiers so
        blocking messages and upload warnings speak one vocabulary.
    :param location: Human-readable path to the payload element that
        produced the finding, used verbatim so a depositor can find it.
    :param message: Producer-facing explanation, including the remedy.
    :param structural_flag: Whether the record should be excluded from
        default query results and bulk transition-state exports unless
        explicitly opted into. ADR 0012's answer to "warnings get
        ignored": the flag is the protection a hard block was providing,
        without the incentive to edit the frequency list.
    :param tau: The τ this finding was decided against, and why that τ.
        ``None`` on findings no threshold took part in. ADR 0012 requires
        it so a reader can re-decide the mode later.
    """

    tier: ValidationTier
    code: str
    location: str
    message: str
    structural_flag: bool = False
    tau: TauResolution | None = field(default=None, compare=True)


def expects_zero_imaginary_modes(kind: StationaryPointKind) -> bool:
    """Whether ``kind`` is formally a minimum on the potential surface."""
    return kind in MINIMUM_KINDS


# ---------------------------------------------------------------------------
# Species entries
# ---------------------------------------------------------------------------


def evaluate_species_entry_frequency(
    kind: StationaryPointKind,
    n_imag: int | None,
    imag_freq_cm1: float | None = None,
    *,
    location: str,
) -> list[StationaryPointFinding]:
    """Judge one species entry's declared kind against its own frequency evidence.

    Untouched by ADR 0012, which changed the transition-state rule only.

    :param kind: The stationary-point kind the uploader declared.
    :param n_imag: Imaginary-mode count parsed from the frequency evidence
        deposited with that same entry. ``None`` means no evidence was
        supplied — absence is not contradiction, so nothing is reported.
    :param imag_freq_cm1: Magnitude of the imaginary mode, if reported.
        Only consulted for ``vdw_complex``, to tell a soft intermolecular
        artifact from a genuine reaction coordinate.
    :param location: Path to the offending payload element.
    :returns: Findings, possibly empty. Never raises.
    """
    if n_imag is None or n_imag <= 0:
        return []

    if kind == StationaryPointKind.minimum:
        return [_minimum_contradiction(n_imag, location=location)]

    if kind == StationaryPointKind.vdw_complex:
        return _vdw_complex_findings(n_imag, imag_freq_cm1, location=location)

    # A kind this module has not been taught about. Say nothing rather
    # than guess a tier for it; adding an enum member must be a
    # deliberate decision here, not a default.
    return []


def _minimum_contradiction(
    n_imag: int, *, location: str
) -> StationaryPointFinding:
    if n_imag == 1:
        detail = (
            "One imaginary mode is a first-order saddle point, which belongs "
            "on a transition-state entry — deposit it through a "
            "transition-state or computed-reaction payload instead."
        )
    else:
        detail = (
            f"{n_imag} imaginary modes is a higher-order saddle point "
            f"({W_N_IMAG_HIGHER_ORDER_SADDLE}) — neither a minimum nor a "
            f"transition state."
        )
    return StationaryPointFinding(
        tier=ValidationTier.block,
        code=W_N_IMAG_CONTRADICTS_MINIMUM,
        location=location,
        message=(
            f"{location}: species_entry_kind is 'minimum' but the frequency "
            f"analysis reports {n_imag} imaginary mode"
            f"{'s' if n_imag != 1 else ''} "
            f"({W_N_IMAG_CONTRADICTS_MINIMUM}). A minimum has zero imaginary "
            f"modes by definition. {detail} Re-optimise on a tighter "
            f"integration grid, or declare the entry as what it actually is. "
            f"If the soft mode is intermolecular and this is a van der Waals "
            f"complex, declare species_entry_kind='vdw_complex', which "
            f"records the mode with a warning instead."
        ),
    )


def _vdw_complex_findings(
    n_imag: int,
    imag_freq_cm1: float | None,
    *,
    location: str,
) -> list[StationaryPointFinding]:
    if n_imag == 1:
        code = W_N_IMAG_CONTRADICTS_MINIMUM
        count_phrase = "1 imaginary mode"
    else:
        code = W_N_IMAG_HIGHER_ORDER_SADDLE
        count_phrase = f"{n_imag} imaginary modes"

    findings = [
        StationaryPointFinding(
            tier=ValidationTier.warn,
            code=code,
            location=location,
            message=(
                f"{location}: species_entry_kind is 'vdw_complex' but the "
                f"frequency analysis reports {count_phrase}. A van der Waals "
                f"complex is formally a minimum, so zero is expected; its "
                f"intermolecular modes sit low enough that Hessian grid noise "
                f"is comparable to the true curvature, so the record is "
                f"accepted and flagged rather than refused. Re-run the "
                f"frequency job on a tighter integration grid if the mode "
                f"matters downstream."
            ),
        )
    ]

    if (
        imag_freq_cm1 is not None
        and abs(imag_freq_cm1) >= TS_IMAGINARY_FREQUENCY_MIN_CM1
    ):
        findings.append(
            StationaryPointFinding(
                tier=ValidationTier.warn,
                code=W_N_IMAG_SUGGESTS_TS,
                location=location,
                message=(
                    f"{location}: the imaginary mode is "
                    f"{abs(imag_freq_cm1):.1f} cm⁻¹, at or above "
                    f"{TS_IMAGINARY_FREQUENCY_MIN_CM1:.0f} cm⁻¹. That is far "
                    f"too stiff to be an intermolecular mode of a van der "
                    f"Waals complex, so it is unlikely to be Hessian grid "
                    f"noise and looks instead like a genuine reaction "
                    f"coordinate. If this really is a saddle point, deposit "
                    f"it as a transition state."
                ),
            )
        )

    return findings


# ---------------------------------------------------------------------------
# Transition states
# ---------------------------------------------------------------------------


def _describe(mode: ImaginaryMode) -> str:
    index = f"mode {mode.mode_index}" if mode.mode_index is not None else "a mode"
    disposition = (
        mode.disposition.value if mode.disposition is not None else "undeclared"
    )
    return f"{index} at {mode.frequency_cm1:.1f} cm-1 ({disposition})"


def evaluate_transition_state_frequency(
    n_imag: int | None,
    imag_freq_cm1: float | None = None,
    *,
    location: str,
    imaginary_modes: Sequence[ImaginaryMode] | None = None,
    reaction_coordinate_mode_index: int | None = None,
    tau: TauResolution | None = None,
) -> list[StationaryPointFinding]:
    """Judge a transition state against its own frequency evidence.

    A transition state is a first-order saddle point by declaration —
    there is no ``stationary_point_kind`` column on a transition-state
    entry, the entity *is* the claim — so this needs no kind argument.

    :param n_imag: Imaginary-mode count parsed from the frequency
        evidence deposited with this transition state. ``None`` means no
        evidence was supplied, and nothing is reported.
    :param imag_freq_cm1: Magnitude of the reaction coordinate, if
        reported as a scalar. Used for the softness expectation when the
        full mode list is absent.
    :param location: Path to the offending payload element.
    :param imaginary_modes: The imaginary modes as deposited. Required to
        judge extras; ``None`` with ``n_imag > 1`` is itself a finding.
    :param reaction_coordinate_mode_index: ``mode_index`` of the mode the
        depositor designates the reaction coordinate. Required whenever
        ``n_imag > 1``.
    :param tau: The protocol-derived threshold, from :func:`resolve_tau`.
        Defaults to the "protocol not recorded" row, which is what a
        caller holding no provenance should get and should say.
    :returns: Findings, possibly empty. Never raises.
    """
    if n_imag is None:
        return []

    if n_imag <= 0:
        return [
            StationaryPointFinding(
                tier=ValidationTier.block,
                code=W_TS_NO_IMAGINARY_MODE,
                location=location,
                message=(
                    f"{location}: a transition state has at least one "
                    f"imaginary mode by definition — it is the reaction "
                    f"coordinate — but the frequency analysis reports none "
                    f"({W_TS_NO_IMAGINARY_MODE}). Zero imaginary modes is a "
                    f"minimum: the geometry did not converge onto a barrier "
                    f"top. Re-run the saddle-point search, or deposit the "
                    f"structure as a species entry."
                ),
            )
        ]

    resolved_tau = tau if tau is not None else resolve_tau()

    if n_imag == 1:
        return _soft_reaction_coordinate_findings(
            _reaction_coordinate_magnitude(
                imaginary_modes, reaction_coordinate_mode_index, imag_freq_cm1
            ),
            location=location,
            tau=resolved_tau,
        )

    if reaction_coordinate_mode_index is None:
        return [
            StationaryPointFinding(
                tier=ValidationTier.block,
                code=W_TS_REACTION_COORDINATE_NOT_DESIGNATED,
                location=location,
                message=(
                    f"{location}: the frequency analysis reports {n_imag} "
                    f"imaginary modes and the payload does not say which one "
                    f"is the reaction coordinate "
                    f"({W_TS_REACTION_COORDINATE_NOT_DESIGNATED}). Extra "
                    f"imaginary modes are accepted — a torsional maximum or a "
                    f"loose intermolecular mode is real chemistry, not a "
                    f"defect — but exactly one mode must be designated and "
                    f"removed from the partition function, which is the "
                    f"contract every transition-state-theory code enforces. "
                    f"Set freq_result.reaction_coordinate_mode_index, and give "
                    f"each other imaginary mode a disposition. {_REMEDIATION}"
                ),
            )
        ]

    if not imaginary_modes:
        return [
            StationaryPointFinding(
                tier=ValidationTier.warn,
                code=W_TS_EXTRA_IMAGINARY_MODES_NOT_ASSESSABLE,
                location=location,
                structural_flag=True,
                tau=resolved_tau,
                message=(
                    f"{location}: mode "
                    f"{reaction_coordinate_mode_index} is designated the "
                    f"reaction coordinate and {n_imag - 1} other imaginary "
                    f"mode(s) are reported, but no frequency list was "
                    f"deposited, so they cannot be judged against tau "
                    f"({W_TS_EXTRA_IMAGINARY_MODES_NOT_ASSESSABLE}). "
                    f"{resolved_tau.reason} The record is accepted and "
                    f"flagged. Deposit the complete signed, unrounded "
                    f"frequency list so the extra modes can be assessed."
                ),
            )
        ]

    designated = [
        mode
        for mode in imaginary_modes
        if mode.mode_index == reaction_coordinate_mode_index
    ]
    extras = [
        mode
        for mode in imaginary_modes
        if mode.mode_index != reaction_coordinate_mode_index
    ]
    rc_magnitude = designated[0].magnitude_cm1 if designated else None

    findings: list[StationaryPointFinding] = []

    if rc_magnitude is not None:
        ambiguous = [
            mode
            for mode in extras
            if mode.disposition is None and mode.magnitude_cm1 >= rc_magnitude
        ]
        if ambiguous:
            return [
                StationaryPointFinding(
                    tier=ValidationTier.block,
                    code=W_TS_REACTION_COORDINATE_AMBIGUOUS,
                    location=location,
                    tau=resolved_tau,
                    message=(
                        f"{location}: mode "
                        f"{reaction_coordinate_mode_index} is designated the "
                        f"reaction coordinate at {rc_magnitude:.1f} cm-1, but "
                        f"{', '.join(_describe(m) for m in ambiguous)} is at "
                        f"least as stiff and carries no declared disposition "
                        f"({W_TS_REACTION_COORDINATE_AMBIGUOUS}). The "
                        f"designation is not supported by the record: nothing "
                        f"honest can be said about which mode is the barrier. "
                        f"Declare what the competing mode is, or re-optimise. "
                        f"{_REMEDIATION}"
                    ),
                )
            ]

    above_tau = [
        mode for mode in extras if mode.magnitude_cm1 >= resolved_tau.tau_cm1
    ]
    if above_tau:
        findings.append(
            StationaryPointFinding(
                tier=ValidationTier.warn,
                code=W_TS_EXTRA_IMAGINARY_MODE_ABOVE_TAU,
                location=location,
                structural_flag=True,
                tau=resolved_tau,
                message=(
                    f"{location}: "
                    f"{', '.join(_describe(m) for m in above_tau)} "
                    f"{'is' if len(above_tau) == 1 else 'are'} at or above "
                    f"tau = {resolved_tau.tau_cm1:.0f} cm-1, so at this "
                    f"protocol the negative curvature is real rather than "
                    f"numerical noise "
                    f"({W_TS_EXTRA_IMAGINARY_MODE_ABOVE_TAU}). The structure "
                    f"is a genuine higher-order saddle. It is accepted "
                    f"because that can be correct chemistry — a transition "
                    f"state may sit at a torsional maximum and still be the "
                    f"right reactive bottleneck — and flagged because a "
                    f"consumer treating it as a plain first-order saddle "
                    f"would be wrong. {resolved_tau.reason} {_REMEDIATION}"
                ),
            )
        )
    elif extras:
        findings.append(
            StationaryPointFinding(
                tier=ValidationTier.warn,
                code=W_TS_EXTRA_IMAGINARY_MODES_BELOW_TAU,
                location=location,
                tau=resolved_tau,
                message=(
                    f"{location}: "
                    f"{', '.join(_describe(m) for m in extras)} "
                    f"{'is' if len(extras) == 1 else 'are'} imaginary but "
                    f"below tau = {resolved_tau.tau_cm1:.0f} cm-1, where the "
                    f"sign of the curvature is not determined at this "
                    f"protocol "
                    f"({W_TS_EXTRA_IMAGINARY_MODES_BELOW_TAU}). The record is "
                    f"accepted without a structural flag. "
                    f"{resolved_tau.reason} {_REMEDIATION}"
                ),
            )
        )

    findings.extend(
        _soft_reaction_coordinate_findings(
            rc_magnitude if rc_magnitude is not None else _abs_or_none(imag_freq_cm1),
            location=location,
            tau=resolved_tau,
        )
    )
    return findings


def _abs_or_none(value: float | None) -> float | None:
    return None if value is None else abs(value)


def _reaction_coordinate_magnitude(
    imaginary_modes: Sequence[ImaginaryMode] | None,
    reaction_coordinate_mode_index: int | None,
    imag_freq_cm1: float | None,
) -> float | None:
    """Magnitude of the reaction coordinate from whatever the caller has.

    Prefers the designated mode, falls back to the single imaginary mode
    in the list, and finally to the scalar ``imag_freq_cm1`` that older
    payloads carry.
    """
    if imaginary_modes:
        if reaction_coordinate_mode_index is not None:
            for mode in imaginary_modes:
                if mode.mode_index == reaction_coordinate_mode_index:
                    return mode.magnitude_cm1
        if len(imaginary_modes) == 1:
            return imaginary_modes[0].magnitude_cm1
    return _abs_or_none(imag_freq_cm1)


def _soft_reaction_coordinate_findings(
    magnitude_cm1: float | None,
    *,
    location: str,
    tau: TauResolution,
) -> list[StationaryPointFinding]:
    if magnitude_cm1 is None or magnitude_cm1 >= TS_IMAGINARY_FREQUENCY_MIN_CM1:
        return []
    return [
        StationaryPointFinding(
            tier=ValidationTier.warn,
            code=W_TS_IMAG_FREQ_TOO_SMALL,
            location=location,
            tau=tau,
            message=(
                f"{location}: the transition state's reaction coordinate is "
                f"{magnitude_cm1:.1f} cm⁻¹, below "
                f"{TS_IMAGINARY_FREQUENCY_MIN_CM1:.0f} cm⁻¹ "
                f"({W_TS_IMAG_FREQ_TOO_SMALL}). A reaction coordinate this "
                f"soft is often an under-converged geometry or a coarse "
                f"integration grid. It can also be real — flat and "
                f"variational barriers genuinely produce one — which is why "
                f"this is a quality expectation and not a refusal. For "
                f"reference, the noise floor for this calculation's extra "
                f"modes is {tau.reason}"
            ),
        )
    ]


# ---------------------------------------------------------------------------
# Tier helpers
# ---------------------------------------------------------------------------


def blocking_findings(
    findings: list[StationaryPointFinding],
) -> list[StationaryPointFinding]:
    """Return only the findings that refuse the payload."""
    return [f for f in findings if f.tier is ValidationTier.block]


def warning_findings(
    findings: list[StationaryPointFinding],
) -> list[StationaryPointFinding]:
    """Return only the findings that annotate an accepted payload."""
    return [f for f in findings if f.tier is ValidationTier.warn]


def has_structural_flag(findings: Sequence[StationaryPointFinding]) -> bool:
    """Whether any accepted finding carries ADR 0012's structural flag.

    The value persisted with the frequency result, and the one the
    read-time trust rubric cites rather than re-deriving.
    """
    return any(f.structural_flag for f in findings)


def raise_for_blocking_findings(findings: list[StationaryPointFinding]) -> None:
    """Raise if any finding is at the blocking tier.

    Upload schemas call this from a ``model_validator`` so a definitional
    contradiction becomes a 422 naming the contradiction, before the
    route body opens a submission.

    Every finding already carries a ``code``; until the refusal did too,
    that code reached a client only as a substring of an English sentence
    and the response body said ``validation_error``. It is now the
    exception's own attribute, so the 422 names the contradiction in a
    field a client can switch on.

    A refusal reports one code only when the blocking findings agree on
    one. Two different contradictions in one payload are two things to fix,
    and naming either of them would tell the client the other is not there;
    the joined message still names both, and the envelope falls back to the
    generic code. ``message_prefix=False`` keeps the message exactly what it
    has always been.
    """
    blocked = blocking_findings(findings)
    if not blocked:
        return
    message = " ".join(f.message for f in blocked)
    codes = {f.code for f in blocked if f.code}
    if len(codes) != 1:
        raise ValueError(message)
    raise CodedValidationError(
        codes.pop(),
        message,
        context={"codes": sorted({f.code for f in blocked if f.code})},
        message_prefix=False,
    )


__all__ = [
    "MINIMUM_KINDS",
    "TAU_ANALYTIC_DEFAULT_CM1",
    "TAU_ANALYTIC_TIGHT_CM1",
    "TAU_FINITE_DIFFERENCE_ENERGY_CM1",
    "TAU_FINITE_DIFFERENCE_GRADIENT_CM1",
    "TAU_PARAMETER_KEYS",
    "TAU_PROTOCOL_NOT_RECORDED_CM1",
    "TS_IMAGINARY_FREQUENCY_MIN_CM1",
    "ImaginaryMode",
    "StationaryPointFinding",
    "TauBasis",
    "TauResolution",
    "ValidationTier",
    "W_N_IMAG_CONTRADICTS_MINIMUM",
    "W_N_IMAG_HIGHER_ORDER_SADDLE",
    "W_N_IMAG_SUGGESTS_TS",
    "W_TS_EXTRA_IMAGINARY_MODES_BELOW_TAU",
    "W_TS_EXTRA_IMAGINARY_MODES_NOT_ASSESSABLE",
    "W_TS_EXTRA_IMAGINARY_MODE_ABOVE_TAU",
    "W_TS_IMAG_FREQ_TOO_SMALL",
    "W_TS_NO_IMAGINARY_MODE",
    "W_TS_REACTION_COORDINATE_AMBIGUOUS",
    "W_TS_REACTION_COORDINATE_NOT_DESIGNATED",
    "blocking_findings",
    "evaluate_species_entry_frequency",
    "evaluate_transition_state_frequency",
    "expects_zero_imaginary_modes",
    "has_structural_flag",
    "raise_for_blocking_findings",
    "resolve_tau",
    "resolve_tau_from_parameters",
    "warning_findings",
]
