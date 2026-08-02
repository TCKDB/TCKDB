"""Consistency between a declared stationary point and its frequency evidence.

The Hessian eigenvalue spectrum *is* the definition of a stationary point:
zero imaginary modes is a minimum, exactly one is a first-order saddle
point, two or more is a higher-order saddle. A payload that declares one
thing and carries frequency evidence for another is internally
contradictory.

ADR 0008 (*definitions block, expectations warn*) sorts the four possible
findings into two tiers, and this module is their single owner:

======================  ====================================  ======
declared                rule                                  tier
======================  ====================================  ======
``minimum``             ``n_imag == 0``                       block
``vdw_complex``         ``n_imag == 0`` *expected*            warn
transition state        ``n_imag == 1``                       block
transition state        ``|imag_freq_cm1| >= 100 cm⁻¹``       warn
======================  ====================================  ======

Why the two minima split, given that a van der Waals complex is formally
also a minimum:

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
* A **transition state** with zero imaginary modes is a minimum, and one
  with two or more is a higher-order saddle. Either way it is not the
  first-order saddle point it claims to be, so that blocks.
* A transition state whose imaginary mode is very small is *suspicious*
  but can be perfectly real — flat barriers and variational transition
  states genuinely produce them. Magnitude is therefore a quality
  expectation, never a definition, and ADR 0008 names it explicitly as a
  check that must not block.

Absence is never contradiction: a payload with no frequency evidence
produces no findings at all, in every case.

Pure functions only — no Pydantic, no backend imports, no payload
shapes. Callers extract ``(n_imag, imag_freq_cm1)`` from whatever shape
they hold and pass scalars in.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from tckdb_schemas.enums import StationaryPointKind

# ---------------------------------------------------------------------------
# The tunable expectation
# ---------------------------------------------------------------------------

#: Magnitude below which a transition state's single imaginary mode is
#: reported as suspiciously soft (cm⁻¹). This is an expectation, not a
#: definition — it never blocks. The value is a starting point rather
#: than a physical constant: reaction coordinates for hydrogen transfers
#: run to thousands of cm⁻¹, while genuinely flat or variational barriers
#: can fall well under 100, so the threshold trades false positives on
#: loose saddle points against silence on under-converged ones. It is
#: also the scale that separates a van der Waals complex's soft
#: intermolecular modes from a real reaction coordinate, and is reused
#: for that judgement below.
TS_IMAGINARY_FREQUENCY_MIN_CM1: float = 100.0


# ---------------------------------------------------------------------------
# Machine-readable codes
# ---------------------------------------------------------------------------

#: Frequency evidence reports an imaginary mode on an entry declared a
#: minimum. Blocking for ``minimum``; reported as a warning for
#: ``vdw_complex``, where the same fact is usually Hessian noise. The
#: code names the *finding*; the declared kind decides the tier.
W_N_IMAG_CONTRADICTS_MINIMUM = "n_imag_contradicts_minimum"

#: Two or more imaginary modes. Folded into the blocking message for
#: ``minimum`` and for transition states; reported as a warning for
#: ``vdw_complex``.
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

#: A transition state whose frequency evidence does not report exactly
#: one imaginary mode. Definitional, therefore blocking.
W_TS_N_IMAG_NOT_ONE = "transition_state_n_imag_not_one"

#: A transition state's single imaginary mode is below
#: :data:`TS_IMAGINARY_FREQUENCY_MIN_CM1` in magnitude. An expectation,
#: therefore a warning.
W_TS_IMAG_FREQ_TOO_SMALL = "transition_state_imaginary_frequency_too_small"


#: Stationary-point kinds that are, by definition, minima on the
#: potential energy surface: every one of them *expects* zero imaginary
#: modes. Membership says nothing about the tier — ``minimum`` and
#: ``vdw_complex`` share the expectation and differ in the consequence.
MINIMUM_KINDS = frozenset({StationaryPointKind.minimum, StationaryPointKind.vdw_complex})


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
class StationaryPointFinding:
    """One consistency finding about one piece of frequency evidence.

    :param tier: Whether the finding refuses the payload or annotates it.
    :param code: Stable machine-readable code, shared across tiers so
        blocking messages and upload warnings speak one vocabulary.
    :param location: Human-readable path to the payload element that
        produced the finding, used verbatim so a depositor can find it.
    :param message: Producer-facing explanation, including the remedy.
    """

    tier: ValidationTier
    code: str
    location: str
    message: str


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


def evaluate_transition_state_frequency(
    n_imag: int | None,
    imag_freq_cm1: float | None = None,
    *,
    location: str,
) -> list[StationaryPointFinding]:
    """Judge a transition state against its own frequency evidence.

    A transition state is a first-order saddle point by declaration —
    there is no ``stationary_point_kind`` column on a transition-state
    entry, the entity *is* the claim — so the count check needs no kind
    argument.

    :param n_imag: Imaginary-mode count parsed from the frequency
        evidence deposited with this transition state. ``None`` means no
        evidence was supplied, and nothing is reported.
    :param imag_freq_cm1: Magnitude of the imaginary mode, if reported.
    :param location: Path to the offending payload element.
    :returns: Findings, possibly empty. Never raises.
    """
    if n_imag is None:
        return []

    if n_imag != 1:
        if n_imag <= 0:
            detail = (
                "Zero imaginary modes is a minimum, not a saddle point: the "
                "geometry did not converge onto a barrier top. Re-run the "
                "saddle-point search, or deposit the structure as a species "
                "entry."
            )
        else:
            detail = (
                f"{n_imag} imaginary modes is a higher-order saddle point "
                f"({W_N_IMAG_HIGHER_ORDER_SADDLE}), not the first-order "
                f"saddle a transition state is defined to be. Re-optimise "
                f"the geometry, following the spurious modes down if they "
                f"are real rather than grid artifacts."
            )
        return [
            StationaryPointFinding(
                tier=ValidationTier.block,
                code=W_TS_N_IMAG_NOT_ONE,
                location=location,
                message=(
                    f"{location}: a transition state has exactly one "
                    f"imaginary mode by definition, but the frequency "
                    f"analysis reports {n_imag} "
                    f"({W_TS_N_IMAG_NOT_ONE}). {detail}"
                ),
            )
        ]

    if (
        imag_freq_cm1 is not None
        and abs(imag_freq_cm1) < TS_IMAGINARY_FREQUENCY_MIN_CM1
    ):
        return [
            StationaryPointFinding(
                tier=ValidationTier.warn,
                code=W_TS_IMAG_FREQ_TOO_SMALL,
                location=location,
                message=(
                    f"{location}: the transition state's imaginary mode is "
                    f"{abs(imag_freq_cm1):.1f} cm⁻¹, below "
                    f"{TS_IMAGINARY_FREQUENCY_MIN_CM1:.0f} cm⁻¹. The saddle "
                    f"point count is correct, so the record is accepted; a "
                    f"mode this soft is nevertheless often an "
                    f"under-converged geometry or a coarse integration grid. "
                    f"It can also be real — flat and variational barriers "
                    f"genuinely produce one — which is why this is a quality "
                    f"expectation and not a refusal."
                ),
            )
        ]

    return []


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


def raise_for_blocking_findings(findings: list[StationaryPointFinding]) -> None:
    """Raise ``ValueError`` if any finding is at the blocking tier.

    Upload schemas call this from a ``model_validator`` so a definitional
    contradiction becomes a 422 naming the contradiction, before the
    route body opens a submission.
    """
    blocked = blocking_findings(findings)
    if blocked:
        raise ValueError(" ".join(f.message for f in blocked))


__all__ = [
    "MINIMUM_KINDS",
    "TS_IMAGINARY_FREQUENCY_MIN_CM1",
    "StationaryPointFinding",
    "ValidationTier",
    "W_N_IMAG_CONTRADICTS_MINIMUM",
    "W_N_IMAG_HIGHER_ORDER_SADDLE",
    "W_N_IMAG_SUGGESTS_TS",
    "W_TS_IMAG_FREQ_TOO_SMALL",
    "W_TS_N_IMAG_NOT_ONE",
    "blocking_findings",
    "evaluate_species_entry_frequency",
    "evaluate_transition_state_frequency",
    "expects_zero_imaginary_modes",
    "raise_for_blocking_findings",
    "warning_findings",
]
