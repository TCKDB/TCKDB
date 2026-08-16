"""The sharper half of the frequency-completeness question: is it linear?

``tckdb_schemas.frequency_completeness`` measures a deposited frequency
list against the *weakest certainly-true* floor, ``3N - 6`` for
``N >= 3``. That bound is deliberately loose, and its own docstring says
why: a collinear molecule has ``3N - 5`` modes, one **more** than
``3N - 6``, so comparing against ``3N - 6`` accepts every linear molecule
without ever having to decide whether a given geometry is linear —
a decision that needs a collinearity tolerance, which the wire package
has no business carrying (it is chemistry-free, imports no numerics, and
is forbidden from importing ``app`` by an AST scan *and* a runtime
``sys.modules`` check).

The residue that leaves
-----------------------
A **non-linear** molecule depositing exactly ``3N - 5`` modes sits
inside the accepted band and passes silently. Water with four
frequencies, methanol with thirteen: one mode too many for a bent
molecule, and exactly the count a linear molecule of the same atom count
would have. The two ways that happens in practice are both worth
telling a depositor about:

* the frequency job — or the script that read it — treated the molecule
  as linear and printed ``3N - 5`` modes, dropping one real vibration
  and keeping one rigid-body eigenvalue in its place; or
* one rigid-body translation/rotation leaked into an otherwise complete
  ``3N - 6`` list.

Neither is visible to the floor check, because ``3N - 5 > 3N - 6``.

Why this is a *second* check and not a tighter first one
--------------------------------------------------------
The wire-package bound keeps exactly the strength it can prove from an
atom count alone, and this module adds the strength that needs
coordinates. That split is the point. It is not a tightening of
``minimum_complete_mode_count``: that function still answers "what is
certainly true of any geometry with this many atoms", and it is still
called on every path, unchanged. This module answers a narrower question
about one specific set of coordinates, and only ever when the wire check
was already satisfied.

The alternative — moving a collinearity tolerance into the wire package
so both sides could share it — was considered and **not** done. It would
put a numerical judgement, and a numpy dependency, inside a package whose
whole contract is that it carries neither.

Tier: advisory, never blocking
------------------------------
`ADR 0008 <../../../docs/adr/0008-validation-tiers-definitions-block-expectations-warn.md>`_
lets a check block only when it asserts a definition or a contract, never
an expectation. "This molecule looks bent, so it should have ``3N - 6``
modes" rests on a *tolerance* — on where the line between bent and
straight is drawn — and a rule whose answer moves when a constant moves
is an expectation by construction. It is also, unlike the ``3N`` ceiling,
a rule a correct deposit can trip: a genuinely quasi-linear molecule, or
a producer who deposited ``3N - 6`` vibrations plus one designated
rigid-body eigenvalue on purpose, is depositing something true. So the
record is accepted and annotated, and a depositor who disagrees needs no
escape hatch — the warning costs them nothing but an explanation.

Where the tolerance comes from, and why not the existing one
------------------------------------------------------------
:data:`app.chemistry.normal_modes.LINEARITY_SINGULAR_VALUE_TOLERANCE`
already exists and already answers "is this geometry collinear" — but it
answers it for a *different purpose* and cannot be reused here. It is an
exact-rank test on the six rigid-body vectors, with a relative cutoff of
``1e-6``, used to decide how many directions to project out of a Hessian.
There, admitting a nearly-zero direction into the basis would corrupt the
projection, so the tolerance is set as tight as floating point allows.

Measured against real coordinates, that tightness is wrong for this
question: a CO2 geometry bent by one *thousandth* of a degree —
179.999°, which is ordinary optimiser noise and rounding in a deposited
XYZ — comes out at a relative singular value of ``4.5e-6`` and is
classified **not linear**. Reusing that constant here would therefore
have fired this warning on real, correct, linear molecules, which is the
one outcome an advisory check must not produce.

So this module carries its own pair of thresholds and its own three-way
answer. See :data:`COLLINEAR_MAX_TRANSVERSE_RATIO` and
:data:`BENT_MIN_TRANSVERSE_RATIO`.

What the check does *not* do
----------------------------
It does not report the mirror case — a geometry determined **linear**
whose list is ``3N - 6``, one mode short of the ``3N - 5`` a linear
molecule has. That is the same size of slack in the other direction and
is a reasonable follow-up, but the short-list warning already has an
owner (``freq_list_incomplete_for_geometry``) whose message and tier were
argued from partial Hessians, frozen-atom regions and lumped
participants; extending it to a case those arguments do not cover is its
own decision, not a rider on this one.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np

from app.chemistry.geometry import parse_xyz
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.upload_warning import UploadWarning
from app.scientific_checks import (
    CheckTier,
    CodeChannel,
    ConstantThreshold,
    DesignPosition,
    PythonCheck,
    ScientificCheck,
)

#: The deposited frequency list has exactly the mode count a **linear**
#: molecule of this size would have, but the geometry it is attached to
#: is not linear. Advisory: the record is accepted and annotated.
W_FREQ_LIST_LINEAR_COUNT_FOR_BENT_GEOMETRY = (
    "freq_list_linear_mode_count_for_bent_geometry"
)

#: At or below this, the geometry is treated as **linear** and the check
#: says nothing.
#:
#: The measure is scale-free: the second singular value of the centred
#: coordinate matrix divided by the first — the molecule's spread
#: *across* its long axis divided by its spread *along* it. Exactly
#: collinear gives zero, and no unit, bond length or molecule size enters.
#:
#: ``1e-3`` is roughly 0.2° of bend for a symmetric triatomic. Measured
#: on CO2 at a 1.16 Å bond length: 180.000° → 3.5e-17, 179.999° → 5.0e-6,
#: 179.99° → 5.0e-5, 179.9° → 5.0e-4. So every deposit of a linear
#: molecule that is straight to within a tenth of a degree — which covers
#: optimiser convergence noise and XYZ rounding by three orders of
#: magnitude — lands on the silent side.
COLLINEAR_MAX_TRANSVERSE_RATIO = 1e-3

#: At or above this, the geometry is treated as **bent** and the warning
#: may fire. Same measure as above.
#:
#: ``3e-2`` is roughly 6° of bend for a symmetric triatomic (CO2 at 175°
#: → 2.5e-2; at 170° → 5.1e-2), and water sits at 4.5e-1 — fifteen times
#: over. A molecule that is genuinely bent is bent by tens of degrees, so
#: the threshold is nowhere near anything real, which is what a threshold
#: on an advisory check should look like.
BENT_MIN_TRANSVERSE_RATIO = 3e-2

#: Below three atoms linearity is a fact rather than a measurement — one
#: atom has no vibrations, two are collinear by definition — and
#: ``minimum_complete_mode_count`` already gives those cases their exact
#: counts. Nothing here applies.
_MIN_ATOMS_FOR_LINEARITY_QUESTION = 3


class GeometryLinearity(str, Enum):
    """Three answers, because two would be a lie.

    The gap between :data:`COLLINEAR_MAX_TRANSVERSE_RATIO` and
    :data:`BENT_MIN_TRANSVERSE_RATIO` is deliberate and is the whole
    reason this is not a boolean. A molecule with a bond angle of 178° is
    genuinely ambiguous — quasi-linear molecules exist, their
    equilibrium structures are basin-dependent and method-dependent, and
    any single cutoff mis-sorts some of them. A check that had to answer
    "linear or bent" for such a geometry would answer confidently and
    sometimes wrongly, in a *message telling the depositor their record
    is wrong*.

    :cvar linear: Collinear to well within deposit noise. Say nothing:
        ``3N - 5`` is exactly right for it.
    :cvar bent: Bent by far more than any tolerance argument could
        explain away. This is the only value that lets a warning fire.
    :cvar undetermined: In between, or unmeasurable (fewer than three
        atoms, an unparseable or absent geometry, a degenerate coordinate
        set). Say nothing.
    """

    linear = "linear"
    bent = "bent"
    undetermined = "undetermined"


@dataclass(frozen=True)
class LinearityAssessment:
    """A linearity verdict and the number it was reached from.

    :param verdict: The three-way answer.
    :param transverse_ratio: ``sigma_2 / sigma_1`` of the centred
        coordinate matrix, or ``None`` when it could not be computed.
    """

    verdict: GeometryLinearity
    transverse_ratio: float | None


def transverse_extent_ratio(coordinates: np.ndarray) -> float | None:
    """How far the atoms spread *across* their long axis, relative to along it.

    Singular values of the centred ``(N, 3)`` coordinate matrix are the
    RMS extents along the three principal geometric axes. Their ratio
    ``sigma_2 / sigma_1`` is zero for a collinear set of points and grows
    with the bend, is invariant under translation, rotation and choice of
    length unit, and needs no masses — collinearity is a property of the
    positions alone, and weighting them by mass would make the answer
    depend on isotopic labelling, which moves no nucleus.

    :param coordinates: ``(N, 3)`` atomic positions.
    :returns: The ratio, or ``None`` for a degenerate set (all atoms on
        one point) where the question has no answer.
    """

    centred = np.asarray(coordinates, dtype=float)
    if centred.ndim != 2 or centred.shape[1] != 3 or centred.shape[0] == 0:
        return None
    centred = centred - centred.mean(axis=0)
    singular_values = np.linalg.svd(centred, compute_uv=False)
    if singular_values.size < 2 or singular_values[0] <= 0.0:
        return None
    return float(singular_values[1] / singular_values[0])


def classify_linearity(coordinates: np.ndarray) -> LinearityAssessment:
    """Sort a coordinate set into linear / bent / undetermined.

    :param coordinates: ``(N, 3)`` atomic positions.
    :returns: The verdict and the ratio behind it. Fewer than three atoms
        is :attr:`GeometryLinearity.undetermined` here rather than
        "linear": two atoms *are* collinear, but this module is only ever
        asked about geometries whose exact mode count the floor check has
        already pinned down, and answering a question nobody asks invites
        a caller to rely on it.
    """

    array = np.asarray(coordinates, dtype=float)
    if array.ndim != 2 or array.shape[0] < _MIN_ATOMS_FOR_LINEARITY_QUESTION:
        return LinearityAssessment(GeometryLinearity.undetermined, None)

    ratio = transverse_extent_ratio(array)
    if ratio is None:
        return LinearityAssessment(GeometryLinearity.undetermined, None)
    if ratio <= COLLINEAR_MAX_TRANSVERSE_RATIO:
        return LinearityAssessment(GeometryLinearity.linear, ratio)
    if ratio >= BENT_MIN_TRANSVERSE_RATIO:
        return LinearityAssessment(GeometryLinearity.bent, ratio)
    return LinearityAssessment(GeometryLinearity.undetermined, ratio)


def classify_xyz_linearity(xyz_text: str | None) -> LinearityAssessment:
    """:func:`classify_linearity` for an XYZ block.

    An unparseable geometry is refused by the fragment that owns that
    contract, so this declines to speak rather than reporting the same
    defect a second time in different words — the same rule
    ``atom_count_of_xyz`` follows on the wire side.
    """

    if xyz_text is None:
        return LinearityAssessment(GeometryLinearity.undetermined, None)
    try:
        parsed = parse_xyz(GeometryPayload(xyz_text=xyz_text))
    except (ValueError, TypeError):
        return LinearityAssessment(GeometryLinearity.undetermined, None)
    if not parsed.atoms:
        return LinearityAssessment(GeometryLinearity.undetermined, None)
    coordinates = np.array([[x, y, z] for _element, x, y, z in parsed.atoms])
    return classify_linearity(coordinates)


def evaluate_frequency_list_linearity(
    n_modes: int | None,
    xyz_text: str | None,
    *,
    location: str,
) -> list[UploadWarning]:
    """Judge one deposited frequency list against its geometry's shape.

    Fires on exactly one configuration: ``n_modes == 3N - 5`` for a
    geometry classified :attr:`GeometryLinearity.bent`. Everything else
    is silent, including every list the wire-side floor and ceiling
    already speak about — this check never reports a length those bounds
    reach, so a payload cannot collect two warnings for one list.

    :param n_modes: Length of the deposited frequency list. ``None``
        means no list was deposited; nothing is reported.
    :param xyz_text: The geometry the frequency job ran on, already
        resolved by the caller.
    :param location: Path to the payload element, used verbatim.
    :returns: Zero or one warning. Never raises, and never blocks.
    """

    if n_modes is None or xyz_text is None:
        return []

    assessment = classify_xyz_linearity(xyz_text)
    if assessment.verdict is not GeometryLinearity.bent:
        return []

    try:
        n_atoms = parse_xyz(GeometryPayload(xyz_text=xyz_text)).natoms
    except (ValueError, TypeError):  # pragma: no cover - classify already parsed
        return []

    linear_count = 3 * n_atoms - 5
    bent_count = 3 * n_atoms - 6
    if n_modes != linear_count:
        return []

    ratio = assessment.transverse_ratio
    return [
        UploadWarning(
            field=location,
            code=W_FREQ_LIST_LINEAR_COUNT_FOR_BENT_GEOMETRY,
            message=(
                f"{location}: the frequency list carries {n_modes} modes, "
                f"which is 3N-5 for this {n_atoms}-atom geometry -- the "
                f"vibrational count of a *linear* molecule "
                f"({W_FREQ_LIST_LINEAR_COUNT_FOR_BENT_GEOMETRY}). This "
                f"geometry is not linear: its atoms spread across their long "
                f"axis by {ratio:.2g} of their spread along it, against a "
                f"threshold of {BENT_MIN_TRANSVERSE_RATIO:g} for calling a "
                f"geometry bent (exactly collinear is 0). A non-linear "
                f"{n_atoms}-atom molecule has 3N-6 = {bent_count} "
                f"vibrations, so one mode here is either spurious or is a "
                f"rigid-body translation/rotation left in the list -- the "
                f"usual cause being a frequency job, or a script reading "
                f"one, that treated the molecule as linear. The record is "
                f"accepted and flagged, not refused: the boundary between "
                f"linear and bent is a tolerance rather than a definition, "
                f"and near-linear geometries are deliberately left "
                f"unreported. Deposit every mode the job printed, or the "
                f"geometry the job actually ran on."
            ),
        )
    ]


def evaluate_deposited_frequency_list_linearity(
    n_modes: int | None,
    *,
    input_geometry_xyz_text: str | None,
    fallback_xyz_text: str | None,
    location: str,
) -> list[UploadWarning]:
    """Judge a list against whichever geometry it will be bound to.

    Resolves the geometry the same way
    ``tckdb_schemas.frequency_completeness.evaluate_deposited_frequency_list``
    does — the calculation's own first input geometry when the producer
    named one, otherwise the enclosing conformer's or transition state's
    reference geometry, which is the fallback the persistence workflow
    itself applies for ``freq``. The two checks must count the same atoms
    to be talking about the same record, so the rule is restated here
    rather than approximated.
    """

    xyz_text = (
        input_geometry_xyz_text
        if input_geometry_xyz_text is not None
        else fallback_xyz_text
    )
    return evaluate_frequency_list_linearity(n_modes, xyz_text, location=location)


def calculation_linearity_warnings(
    calc: object,
    *,
    location: str,
    fallback_xyz_text: str | None = None,
) -> list[UploadWarning]:
    """Judge one calculation payload's frequency list, if it has one.

    The backend twin of
    ``CalculationWithResultsPayload.frequency_completeness_findings``.
    ``calc`` is read structurally rather than by type: the bundle
    calculation payloads re-declare the primitive payload's fields rather
    than inheriting them, so there is no single class to annotate, and
    every shape that reaches here carries ``freq_result`` and
    ``input_geometries`` under those names.

    :param calc: A calculation upload payload.
    :param location: Path to the payload element, used verbatim.
    :param fallback_xyz_text: The enclosing conformer's or transition
        state's reference geometry.
    """

    freq_result = getattr(calc, "freq_result", None)
    if freq_result is None:
        return []
    modes = getattr(freq_result, "modes", None)
    if modes is None:
        return []
    input_geometries = getattr(calc, "input_geometries", None) or ()
    return evaluate_deposited_frequency_list_linearity(
        len(modes),
        input_geometry_xyz_text=(
            input_geometries[0].xyz_text if input_geometries else None
        ),
        fallback_xyz_text=fallback_xyz_text,
        location=location,
    )


def calculation_in_linearity_warnings(
    calc_in: object,
    *,
    location: str,
    xyz_text: str | None,
) -> list[UploadWarning]:
    """The bundle-local twin of :func:`calculation_linearity_warnings`.

    A bundle's ``CalculationIn`` carries flat ``freq_*`` fields rather
    than a ``freq_result`` block, and only some of them become a stored
    result. It is read through ``freq_result_of`` for the same reason the
    wire-side completeness check does: judging the raw field would judge
    something the database will never hold.

    :param xyz_text: The geometry this calculation ran on, already
        resolved from ``geometry_key`` by the caller — a bundle-local
        calculation cannot see its own bundle's geometry namespace.
    """

    from tckdb_schemas.shared.calculation_in import freq_result_of

    freq_result = freq_result_of(calc_in)
    if freq_result is None or freq_result.modes is None:
        return []
    inline = getattr(calc_in, "input_geometries", None) or ()
    return evaluate_deposited_frequency_list_linearity(
        len(freq_result.modes),
        input_geometry_xyz_text=inline[0].xyz_text if inline else None,
        fallback_xyz_text=xyz_text,
        location=location,
    )


def computed_species_linearity_warnings(request: object) -> list[UploadWarning]:
    """Walk a computed-species bundle for bent geometries counted as linear.

    Mirrors ``ComputedSpeciesUploadRequest.stationary_point_findings``
    exactly, including the ``location`` strings, so a depositor who gets
    both warnings on one calculation sees one path rather than two
    spellings of it. The walk is repeated here rather than shared with
    the wire package because the wire package cannot reach a
    collinearity tolerance and this check cannot work without one; a test
    pins the two location formats together.
    """

    warnings: list[UploadWarning] = []
    for conformer in request.conformers:
        for calc in (
            conformer.primary_calculation,
            *conformer.additional_calculations,
        ):
            warnings.extend(
                calculation_linearity_warnings(
                    calc,
                    location=(
                        f"conformers['{conformer.key}'].calculations"
                        f"['{calc.key}'].freq_result.modes"
                    ),
                    fallback_xyz_text=conformer.geometry.xyz_text,
                )
            )
    return warnings


def computed_reaction_linearity_warnings(request: object) -> list[UploadWarning]:
    """Walk a computed-reaction bundle for bent geometries counted as linear.

    Mirrors the ``stationary_point_findings`` walks on ``BundleSpeciesIn``
    and ``BundleTransitionStateIn``, including their ``location``
    strings. A species-level calculation's geometry comes from its
    ``geometry_key``; an unresolvable key means the calculation named no
    geometry, and the check declines to speak rather than guessing one.
    """

    warnings: list[UploadWarning] = []
    for species in request.species:
        xyz_by_geometry_key = {
            conformer.geometry.key: conformer.geometry.xyz_text
            for conformer in species.conformers
        }
        for conformer in species.conformers:
            warnings.extend(
                calculation_in_linearity_warnings(
                    conformer.calculation,
                    location=(
                        f"species['{species.key}'].conformers"
                        f"['{conformer.key}'].calculation"
                        f".freq_frequencies_cm1"
                    ),
                    xyz_text=conformer.geometry.xyz_text,
                )
            )
        for calc in species.calculations:
            warnings.extend(
                calculation_in_linearity_warnings(
                    calc,
                    location=(
                        f"species['{species.key}'].calculations"
                        f"['{calc.key}'].freq_frequencies_cm1"
                    ),
                    xyz_text=xyz_by_geometry_key.get(calc.geometry_key or ""),
                )
            )

    transition_state = request.transition_state
    if transition_state is not None:
        for calc in (transition_state.calculation, *transition_state.calculations):
            warnings.extend(
                calculation_in_linearity_warnings(
                    calc,
                    location=(
                        f"transition_state.calculations['{calc.key}']"
                        f".freq_frequencies_cm1"
                    ),
                    xyz_text=transition_state.geometry.xyz_text,
                )
            )
    return warnings


def transition_state_upload_linearity_warnings(
    request: object,
) -> list[UploadWarning]:
    """Walk a standalone transition-state upload. Mirrors its findings walk."""

    warnings: list[UploadWarning] = []
    for label, calc in [
        ("primary_opt", request.primary_opt),
        *(
            (f"additional_calculations[{index}]", calc)
            for index, calc in enumerate(request.additional_calculations)
        ),
    ]:
        warnings.extend(
            calculation_linearity_warnings(
                calc,
                location=f"{label}.freq_result.modes",
                fallback_xyz_text=request.geometry.xyz_text,
            )
        )
    return warnings


def inline_calculation_linearity_warnings(
    calculations: object,
) -> list[UploadWarning]:
    """Walk the inline calculations of a statmech/thermo/transport upload.

    The backend twin of
    :func:`app.schemas.workflows.stationary_point_seam.inline_calculation_findings`,
    and silent for the same reason it is: these three requests carry a
    product and its evidence, never a conformer, so there is no reference
    geometry to fall back to and the question is answerable only for a
    calculation that named the geometry it ran on.
    """

    warnings: list[UploadWarning] = []
    for item in calculations:
        warnings.extend(
            calculation_linearity_warnings(
                item.calculation,
                location=f"calculations['{item.key}'].freq_result.modes",
            )
        )
    return warnings


def network_pdep_linearity_warnings(request: object) -> list[UploadWarning]:
    """Walk a pressure-dependent network upload. Mirrors its findings walks."""

    warnings: list[UploadWarning] = []
    for species in request.species:
        xyz_by_geometry_key = {
            conformer.geometry.key: conformer.geometry.xyz_text
            for conformer in species.conformers
        }
        for conformer in species.conformers:
            warnings.extend(
                calculation_in_linearity_warnings(
                    conformer.calculation,
                    location=(
                        f"species['{species.key}'].conformers"
                        f"['{conformer.key}'].calculation"
                        f"['{conformer.calculation.key}'].freq_frequencies_cm1"
                    ),
                    xyz_text=conformer.geometry.xyz_text,
                )
            )
        for calc in species.calculations:
            warnings.extend(
                calculation_in_linearity_warnings(
                    calc,
                    location=(
                        f"species['{species.key}'].calculations"
                        f"['{calc.key}'].freq_frequencies_cm1"
                    ),
                    xyz_text=xyz_by_geometry_key.get(calc.geometry_key or ""),
                )
            )
    for transition_state in request.transition_states:
        for calc in (transition_state.calculation, *transition_state.calculations):
            warnings.extend(
                calculation_in_linearity_warnings(
                    calc,
                    location=(
                        f"transition_states['{transition_state.key}']"
                        f".calculations['{calc.key}'].freq_frequencies_cm1"
                    ),
                    xyz_text=transition_state.geometry.xyz_text,
                )
            )
    return warnings


CHECK_FREQ_LIST_MODE_COUNT_MATCHES_GEOMETRY_SHAPE = ScientificCheck(
    group="Stationary points",
    sort_key=9,
    code=W_FREQ_LIST_LINEAR_COUNT_FOR_BENT_GEOMETRY,
    asserts=(
        "A frequency list carrying exactly ``3N - 5`` modes should be "
        "attached to a collinear geometry, because ``3N - 5`` is the "
        "vibrational count of a linear molecule and a non-linear one has "
        "``3N - 6``."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    tier_rationale=(
        "An expectation, not a definition, and ADR 0008 is explicit that only "
        "the latter may block. The verdict rests on a *tolerance* — on where "
        "the line between collinear and bent is drawn — and a rule whose "
        "answer moves when a constant moves is an expectation by "
        "construction. Unlike the ``3N`` ceiling, which no correct deposit "
        "can trip, this one can: a genuinely quasi-linear molecule sits on "
        "no side of the line, and a producer who deposited ``3N - 6`` "
        "vibrations plus one rigid-body eigenvalue on purpose deposited "
        "something true. So the check declines to answer in the ambiguous "
        "band and annotates rather than refuses outside it. It is also the "
        "*second* judgement on one list: the wire package's "
        "``freq_list_incomplete_for_geometry`` floor keeps exactly the "
        "strength an atom count can prove, and this adds only the strength "
        "coordinates can."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            evaluate_frequency_list_linearity,
            note=(
                "Owns the arithmetic and the verdict; the payload walks that "
                "resolve which geometry each frequency list is measured "
                "against live in the same module, one per upload shape, and "
                "restate the wire-side rule — the calculation's own first "
                "input geometry where the producer named one, otherwise the "
                "enclosing conformer's or transition state's reference "
                "geometry. The two checks must count the same atoms to be "
                "talking about the same record."
            ),
        ),
        DesignPosition(
            "The collinearity tolerance stays backend-side. "
            "``tckdb_schemas`` is forbidden from importing ``app`` by an AST "
            "scan and a runtime ``sys.modules`` check, and it carries no "
            "numerics; moving a tolerance into it to share one constant "
            "would put a numerical judgement inside a package whose contract "
            "is that it holds none. So the wire package keeps the bound it "
            "can prove from an atom count and the backend adds the sharper "
            "one where the coordinates are."
        ),
        DesignPosition(
            "``LINEARITY_SINGULAR_VALUE_TOLERANCE`` in "
            "``app.chemistry.normal_modes`` is *not* reused, though it "
            "answers the same question. It is an exact-rank test at 1e-6 "
            "relative, set that tight because admitting a nearly-zero "
            "direction into a projection basis corrupts the projection. "
            "Measured on real coordinates, CO2 bent by one thousandth of a "
            "degree — ordinary optimiser noise — comes out at 4.5e-6 and "
            "classifies as *not linear*, so reusing it would have fired this "
            "warning on correct linear molecules."
        ),
    ),
    thresholds=(
        ConstantThreshold(
            name="COLLINEAR_MAX_TRANSVERSE_RATIO",
            value=COLLINEAR_MAX_TRANSVERSE_RATIO,
            unit="dimensionless (sigma_2 / sigma_1 of the centred coordinates)",
            rationale=(
                "At or below this the geometry is treated as linear and "
                "nothing is reported. Roughly 0.2 degrees of bend for a "
                "symmetric triatomic — three orders of magnitude looser than "
                "optimiser convergence noise, so a real linear molecule "
                "cannot be flagged. Loosening it would silence the check for "
                "genuinely bent molecules; tightening it risks warning about "
                "correct ones, which is the failure an advisory check must "
                "not have."
            ),
        ),
        ConstantThreshold(
            name="BENT_MIN_TRANSVERSE_RATIO",
            value=BENT_MIN_TRANSVERSE_RATIO,
            unit="dimensionless (sigma_2 / sigma_1 of the centred coordinates)",
            rationale=(
                "At or above this the geometry is treated as bent and the "
                "warning may fire. Roughly 6 degrees of bend for a symmetric "
                "triatomic; water sits fifteen times over it. Between the two "
                "thresholds the check answers 'undetermined' and says "
                "nothing, which is what keeps a quasi-linear molecule from "
                "receiving a confident claim two degrees cannot support."
            ),
        ),
    ),
    escape_hatch=(
        "None needed — the warning is the accommodation, and the record is "
        "stored either way. A depositor whose molecule is genuinely "
        "quasi-linear will usually not see the warning at all, because the "
        "band between the two thresholds is deliberately silent."
    ),
)


__all__ = [
    "BENT_MIN_TRANSVERSE_RATIO",
    "CHECK_FREQ_LIST_MODE_COUNT_MATCHES_GEOMETRY_SHAPE",
    "COLLINEAR_MAX_TRANSVERSE_RATIO",
    "W_FREQ_LIST_LINEAR_COUNT_FOR_BENT_GEOMETRY",
    "GeometryLinearity",
    "LinearityAssessment",
    "calculation_in_linearity_warnings",
    "calculation_linearity_warnings",
    "classify_linearity",
    "classify_xyz_linearity",
    "computed_reaction_linearity_warnings",
    "computed_species_linearity_warnings",
    "evaluate_deposited_frequency_list_linearity",
    "evaluate_frequency_list_linearity",
    "inline_calculation_linearity_warnings",
    "network_pdep_linearity_warnings",
    "transition_state_upload_linearity_warnings",
    "transverse_extent_ratio",
]
