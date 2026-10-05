"""The deterministic structure assessor.

Question asked of one unit: can it support the requested energy and, where asked, the requested structural
claim? The answer is one of ``applicable``, ``incompatible``, ``unsupported`` or ``unresolved`` (see
:class:`~app.services.selection_kernel.Applicability`), with every finding that led there, split into
*blocking* (a known failure of the claim the request makes), *advisory* (disclosed, excludes nothing) and
*comparative-unknown* (a missing comparative fact: it creates no preference edge and excludes nothing).
Pure over the normalised facts; it reads no database.

The rules that run through every check:

* **An unstated fact is unknown, never a default.** A missing energy is unavailable for numerical ordering,
  never zero. A missing solvent is not gas phase. A missing stability analysis is not a stable wavefunction and
  not an unstable one: it contradicts nothing and certifies nothing.
* **A known failure blocks only the claim it invalidates.** A demonstrated unconverged optimisation cannot
  support an optimised-minimum claim, but its finite endpoint value stays a labelled answer to the recorded-value
  question. A failed rerun on another geometry does not touch an older bundle on this one.
* **Curvature belongs to a geometry.** A frequency or Hessian result supports a minimum or saddle claim only on
  the geometry the determination evaluates. A same-entry frequency at another geometry is not evidence for this
  one, and nothing is borrowed from it.
* **The heuristic is not the verdict.** An automated geometry check that failed is attention, not proof. A
  confirmed, still-live finding that invalidates the subject blocks; an unknown finding kind or version is
  disclosed and never promoted to a universal failure.

What the checks do not do: they never compare two units (that is the decision stage), never invent an energy
convention, never add a zero-point energy, and never read a unit's neighbour.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from tckdb_schemas.enums import ImaginaryModeDisposition, StationaryPointKind
from tckdb_schemas.stationary_point import (
    W_TS_EXTRA_IMAGINARY_MODE_ABOVE_TAU,
    W_TS_EXTRA_IMAGINARY_MODES_NOT_ASSESSABLE,
    W_TS_NO_IMAGINARY_MODE,
    W_TS_REACTION_COORDINATE_AMBIGUOUS,
    W_TS_REACTION_COORDINATE_NOT_DESIGNATED,
    ImaginaryMode,
    TauBasis,
    TauResolution,
    evaluate_species_entry_frequency,
    evaluate_transition_state_frequency,
    resolve_tau,
)

from app.db.models.common import CalculationType
from app.services.selection_kernel import Applicability
from app.services.structure_selection.models import (
    SUPPORTED_FINDING_VERSIONS,
    ClaimSupport,
    CurvatureFacts,
    EnergyScope,
    EnergyValue,
    FindingFacts,
    Grain,
    NormalizedCalculation,
    NormalizedDetermination,
    NormalizedRecipe,
    NormalizedSource,
    Quantity,
    Reason,
    StructureAssessment,
    StructureRequest,
    StructureSubject,
    ValidationClaim,
)
from app.services.structure_selection.normalizer import normalize_recipe, recipe_request_mismatches

_PRECEDENCE = (Applicability.incompatible, Applicability.unsupported, Applicability.unresolved)
_MINIMUM_KINDS = frozenset({"minimum", "vdw_complex"})

#: The side of a calculation whose geometry its energy describes, by type. A type not listed cannot supply an
#: energy for a determination (an IRC or scan is not an energy source here).
ENERGY_SIDE: dict[str, str] = {"opt": "output", "sp": "input", "composite": "output"}
#: The side whose geometry a curvature result describes, by type.
CURVATURE_SIDE: dict[str, str] = {"freq": "input", "opt": "output", "composite": "output"}

_NON_ROLE_KINDS = frozenset(
    {
        "identity_incompatibility",
        "state_incompatibility",
        "path_incompatibility",
        "contradictory_characterization",
        "adjudication",
    }
)


class _Findings:
    def __init__(self) -> None:
        self.reasons: list[Reason] = []
        self.blocking: list[str] = []
        self.advisory: list[str] = []
        self.comparative_unknown: list[str] = []

    def add(self, code: str, applicability: Applicability) -> None:
        self.reasons.append(Reason(code, applicability))

    def incompatible(self, code: str) -> None:
        self.add(code, Applicability.incompatible)

    def unsupported(self, code: str) -> None:
        self.add(code, Applicability.unsupported)

    def unresolved(self, code: str) -> None:
        self.add(code, Applicability.unresolved)

    def block(self, code: str) -> None:
        self.blocking.append(code)

    def note(self, code: str) -> None:
        self.advisory.append(code)

    def verdict(self) -> Applicability:
        present = {r.applicability for r in self.reasons}
        return next((a for a in _PRECEDENCE if a in present), Applicability.applicable)


# ---------------------------------------------------------------------------
# Energy
# ---------------------------------------------------------------------------


def _single(refs: tuple[str, ...]) -> str | None:
    return refs[0] if len(refs) == 1 else None


def energy_value(
    c: NormalizedCalculation, quantity: Quantity, f: _Findings, *, geometry_ref: str | None
) -> EnergyValue | None:
    """The calculation's own supplied value of ``quantity`` and what it is a value of, or ``None`` with a reason.

    Never converts one quantity into another: a single point or optimisation does not supply an E0, and an E0 is
    not an electronic energy. A missing number is ``unresolved`` (``energy_not_deposited``), never zero.
    """
    e = c.energy
    scope: EnergyScope
    hartree: float | None
    uncertainty: float | None = None
    if quantity is Quantity.zero_kelvin_energy:
        if c.type != CalculationType.composite.value:
            f.incompatible("record_does_not_supply_requested_quantity")
            return None
        hartree, scope = e.composite_e0_hartree, EnergyScope.composite_zero_kelvin
        if hartree is not None and e.composite_recipe_zpe_hartree is None:
            # An E0 whose zero-point and correction inclusion the record does not state cannot be told apart from
            # an electronic energy relabelled; the convention is never assumed.
            f.unresolved("e0_convention_not_stated")
            return None
    else:
        if c.type == CalculationType.sp.value:
            hartree, scope, uncertainty = e.sp_electronic_hartree, EnergyScope.recorded_single_point, e.sp_uncertainty_hartree
        elif c.type == CalculationType.opt.value:
            hartree = e.opt_final_hartree
            if e.opt_converged is False:
                scope = EnergyScope.unconverged_endpoint
                f.note("optimization_not_converged_value_is_an_intermediate_geometry")
            else:
                scope = EnergyScope.optimized_endpoint
                if e.opt_converged is None:
                    f.note("optimization_convergence_unknown")
        elif c.type == CalculationType.composite.value:
            hartree, scope = e.composite_electronic_hartree, EnergyScope.composite_electronic
        else:
            f.incompatible("record_does_not_supply_requested_quantity")
            return None
    if hartree is None:
        f.unresolved("energy_not_deposited")
        return None
    if not math.isfinite(hartree):
        f.incompatible("energy_not_finite")
        return None
    if uncertainty is None:
        f.comparative_unknown.append("energy_uncertainty_not_stated")
    return EnergyValue(
        quantity=quantity.value,
        hartree=hartree,
        scope=scope,
        calculation_ref=c.calculation_ref,
        geometry_ref=geometry_ref,
        uncertainty_hartree=uncertainty,
    )


def _evaluated_geometry(c: NormalizedCalculation) -> str | None:
    """The one geometry the calculation's energy describes, by its type's side, when its own links name exactly one."""
    side = ENERGY_SIDE.get(c.type)
    if side is None:
        return None
    return _single(c.output_geometry_refs if side == "output" else c.input_geometry_refs)


# ---------------------------------------------------------------------------
# Findings and recipe
# ---------------------------------------------------------------------------


def live_findings(findings: tuple[FindingFacts, ...]) -> list[FindingFacts]:
    """Findings not settled by a later one. A settled finding is history, not evidence.

    Only an **authorized adjudication about the same subject** settles a finding. Naming an earlier finding is not
    enough: a producer's own assertion of "does not invalidate" does not erase an authorized disproof, and an
    adjudication about a different calculation, geometry or determination says nothing about this one. A
    superseding finding this release cannot read (a newer semantic version), or whose target is not in the loaded
    set (so its subject cannot be checked), settles nothing: the disproof stands until a readable, authorized,
    same-subject adjudication says otherwise.
    """
    by_ref = {f.finding_ref: f for f in findings}
    settled: set[str] = set()
    for f in findings:
        if f.supersedes_ref is None or f.kind != "adjudication" or f.authority != "authorized_adjudication":
            continue
        if f.semantic_version not in SUPPORTED_FINDING_VERSIONS:
            continue
        target = by_ref.get(f.supersedes_ref)
        if target is None or (target.scope, target.subject_ref) != (f.scope, f.subject_ref):
            continue
        settled.add(target.finding_ref)
    return [f for f in findings if f.finding_ref not in settled]


def _apply_finding(f: _Findings, finding: FindingFacts, *, blocks: bool, role_needed: bool) -> None:
    if finding.semantic_version not in SUPPORTED_FINDING_VERSIONS:
        f.note("finding_version_unsupported")
        return
    if finding.verdict == "does_not_invalidate":
        return
    if finding.kind == "role_invalidation":
        if finding.role is None or not role_needed:
            return
        label = f"role_invalidated:{finding.role}:{finding.authority}"
    elif finding.kind in _NON_ROLE_KINDS:
        if not blocks:
            f.note(f"finding_on_unneeded_subject:{finding.kind}")
            return
        label = f"finding_invalidates:{finding.kind}:{finding.authority}"
    else:
        # A kind this release does not read: disclosed, never promoted to a failure.
        f.note("finding_kind_unsupported")
        return
    if finding.verdict == "invalidates":
        f.block(label)
    else:
        f.unresolved(f"finding_unresolved:{finding.kind}")


def _recipe_findings(
    recipe: NormalizedRecipe, request: StructureRequest, f: _Findings, *, declaration_state: str
) -> None:
    if declaration_state == "unreadable":
        f.note("actual_protocol_declaration_unreadable")
    for fact in recipe.conflicts:
        f.unresolved(f"recipe_conflict:{fact}")
    for fact in recipe.unestablished:
        f.note(f"recipe_fact_unestablished:{fact}")
    if recipe.cohort_key is None:
        f.comparative_unknown.append("cohort_not_established")
    if request.recipe is not None:
        differs, missing = recipe_request_mismatches(recipe, request.recipe)
        for fact in differs:
            f.incompatible(f"recipe_fact_differs:{fact}")
        for fact in missing:
            f.unresolved(f"recipe_fact_unestablished_for_request:{fact}")


def _subject_findings(c: NormalizedCalculation, request: StructureRequest, f: _Findings) -> None:
    if c.geometry_validation == "fail":
        f.note("geometry_validation_heuristic_failed")
    elif c.geometry_validation == "warning":
        f.note("geometry_validation_heuristic_warning")
    if c.scf_stability == "unstable":
        if request.require_stable_reference:
            f.block("scf_wavefunction_unstable")
        else:
            f.note("scf_wavefunction_unstable")
    elif request.require_stable_reference and c.scf_stability is None:
        f.unresolved("scf_stability_not_checked")
    elif request.require_stable_reference and c.scf_stability != "stable":
        f.unresolved("scf_stability_inconclusive")


def _root_check(recipe: NormalizedRecipe, subject: StructureSubject, f: _Findings) -> None:
    by_name = {x.name: x for x in recipe.facts}
    state = by_name.get("electronic_state")
    if state is not None and state.state == "known" and subject.electronic_state_kind == "ground" and state.value != "0":
        f.incompatible("declared_root_is_not_the_ground_state_of_the_entry")


# ---------------------------------------------------------------------------
# Calculation grain
# ---------------------------------------------------------------------------


def assess_calculation(c: NormalizedCalculation, *, request: StructureRequest, subject: StructureSubject) -> StructureAssessment:
    """Assess one calculation as a recorded-value candidate."""
    f = _Findings()
    assert request.quantity is not None
    geometry = _evaluated_geometry(c)
    value = energy_value(c, request.quantity, f, geometry_ref=geometry)
    if value is not None and request.geometry_ref is not None:
        if geometry is None:
            f.unresolved("evaluated_geometry_unknown")
        elif geometry != request.geometry_ref:
            f.incompatible("evaluated_at_other_geometry")
    recipe = normalize_recipe(
        c.level, c.declaration, constraint_rows=c.constraint_rows, needs_constraints=c.type == CalculationType.opt.value
    )
    _recipe_findings(recipe, request, f, declaration_state=c.declaration_state)
    _root_check(recipe, subject, f)
    _subject_findings(c, request, f)
    if c.lineage_cyclic:
        f.unresolved("dependency_cycle")
    for finding in live_findings(c.findings):
        _apply_finding(f, finding, blocks=True, role_needed=True)
    verdict = f.verdict()
    return StructureAssessment(
        unit_ref=c.calculation_ref,
        applicability=verdict,
        reasons=tuple(f.reasons),
        blocking=tuple(f.blocking),
        advisory=tuple(f.advisory),
        comparative_unknown=tuple(f.comparative_unknown),
        energy=value if verdict is Applicability.applicable and not f.blocking else None,
        recipe=recipe,
    )


# ---------------------------------------------------------------------------
# Determination grain
# ---------------------------------------------------------------------------


@dataclass
class _Roles:
    """The pinned sources of a determination by role, split into usable calculations and unavailable pins."""

    usable: dict[str, list[tuple[NormalizedSource, NormalizedCalculation]]] = field(default_factory=dict)
    unavailable: dict[str, list[NormalizedSource]] = field(default_factory=dict)


def _roles(d: NormalizedDetermination, calcs: dict[str, NormalizedCalculation]) -> _Roles:
    roles = _Roles()
    for s in d.sources:
        calc = calcs.get(s.calculation_ref) if s.calculation_ref is not None else None
        if (
            calc is not None
            and d.conformer_observation_ref is not None
            and calc.conformer_observation_ref is not None
            and calc.conformer_observation_ref != d.conformer_observation_ref
        ):
            # A basin claim about one observation does not borrow evidence anchored to another observation.
            roles.unavailable.setdefault(s.role, []).append(
                NormalizedSource(
                    role=s.role,
                    geometry_ref=s.geometry_ref,
                    calculation_ref=None,
                    unavailable_reason="source_anchored_to_another_observation",
                )
            )
        elif calc is None:
            roles.unavailable.setdefault(s.role, []).append(s)
        else:
            roles.usable.setdefault(s.role, []).append((s, calc))
    return roles


def _geometry_on_side(c: NormalizedCalculation, side: str) -> tuple[str, ...]:
    return c.output_geometry_refs if side == "output" else c.input_geometry_refs


def _coherence(c: NormalizedCalculation, side: str | None, evaluated: str) -> str:
    """How a source's own geometry links relate to the evaluated geometry: ``same``, ``other``, ``ambiguous``,
    ``unestablished``.

    Deliberately conservative: a calculation with several geometries on the side that matters is ``ambiguous`` even
    when one of its output links is marked ``final``. The output link's ``role`` (final or initial) is not read in
    this version, so an optimisation that stored both an initial and a final geometry does not answer for either
    until a producer pins the one it means (a determination's evaluated geometry names it)."""
    if side is None:
        return "unestablished"
    refs = _geometry_on_side(c, side)
    if not refs:
        return "unestablished"
    if evaluated not in refs:
        return "other"
    return "same" if len(refs) == 1 else "ambiguous"


def _tau(cv: CurvatureFacts) -> TauResolution | None:
    """The stored tau as the shared owner's value, or ``None`` (the owner then uses its own "not recorded" row)."""
    if cv.tau_cm1 is None or cv.tau_basis is None:
        return None
    try:
        basis = TauBasis(cv.tau_basis)
    except ValueError:
        return None
    return TauResolution(tau_cm1=cv.tau_cm1, basis=basis, reason="tau as recorded with the frequency result.")


def _owner_modes(cv: CurvatureFacts) -> list[ImaginaryMode]:
    out: list[ImaginaryMode] = []
    for index, frequency, disposition in cv.imaginary_modes:
        try:
            parsed = ImaginaryModeDisposition(disposition) if disposition is not None else None
        except ValueError:
            parsed = None
        out.append(ImaginaryMode(frequency_cm1=frequency, mode_index=index, disposition=parsed))
    return out


def _minimum_support(cv: CurvatureFacts, subject: StructureSubject, n: int) -> tuple[str, str | None, list[str]]:
    """A minimum claim, judged by the *magnitude* of the stored imaginary modes against the stored tau (ADR 0012).

    ADR 0012 says imaginary modes are judged by magnitude, not counted, and the owner's warning tier only means "accept
    the deposit and flag it". Above tau the sign of the curvature is decided, so a stiff imaginary mode is real negative
    curvature; below it the sign is not determined. For any entry kind:

    * **supported** with no imaginary mode, or when every imaginary mode is below tau, however many there are (the owner's
      count-based findings are surfaced as advisories and never contradict by themselves);
    * **contradicted** only when at least one stored mode is at or above tau;
    * **unresolved** when there are imaginary modes and no magnitude was stored to judge them by (or fewer were stored
      than counted: the rest are not assumed soft).
    """
    if subject.stationary_point_kind not in _MINIMUM_KINDS:
        return "contradicts", None, ["entry_kind_is_not_a_minimum"]
    if n == 0:
        return "supports", None, []
    if cv.imaginary_modes:
        magnitudes = [abs(frequency) for _, frequency, _ in cv.imaginary_modes]
    elif cv.imag_freq_cm1 is not None and n == 1:
        magnitudes = [abs(cv.imag_freq_cm1)]
    else:
        return "unresolved", None, ["imaginary_mode_magnitude_not_stored"]
    if len(magnitudes) < n:
        # Fewer modes stored than counted: the rest cannot be judged and are not assumed soft.
        return "unresolved", None, ["imaginary_mode_magnitude_not_stored"]
    resolution = _tau(cv) or resolve_tau()
    findings = evaluate_species_entry_frequency(
        StationaryPointKind(subject.stationary_point_kind), n, max(magnitudes), location="curvature"
    )
    notes = [f"stationary_point_finding:{f.code}" for f in findings]
    if any(m >= resolution.tau_cm1 for m in magnitudes):
        return "contradicts", None, [*notes, "imaginary_mode_at_or_above_tau"]
    return "supports", None, [*notes, "imaginary_modes_below_tau"]


def _saddle_support(
    cv: CurvatureFacts, claim: ValidationClaim, n: int
) -> tuple[str, str | None, list[str]]:
    """A saddle claim, judged by the shared owner from the stored modes, reaction coordinate and tau.

    Without a stored mode list the owner cannot judge extras, and its own answer for that is "flagged, not
    assessable"; the persisted structural flag (the owner's judgement at deposit time) is then the only evidence there
    is, and it is read as such, never re-derived from the count.
    """
    higher = claim is ValidationClaim.higher_order_saddle
    if n <= 0:
        return "contradicts", None, ["no_imaginary_mode"]
    if higher and n == 1:
        return "contradicts", None, ["not_a_higher_order_saddle"]
    if n > 1 and not cv.imaginary_modes:
        flag, designated = cv.structural_flag, cv.reaction_coordinate_mode_index is not None
        if flag is True and designated:
            if higher:
                return "supports", "local_higher_order_saddle", ["not_first_order_tst_suitable"]
            return "unsupported", None, ["higher_order_saddle_flagged"]
        if flag is False and designated and not higher:
            return "supports", "extra_imaginary_modes_below_tau", ["extra_imaginary_modes_below_tau"]
        return "unresolved", None, ["imaginary_mode_treatment_not_recorded"]
    findings = evaluate_transition_state_frequency(
        n,
        cv.imag_freq_cm1,
        location="curvature",
        imaginary_modes=_owner_modes(cv),
        reaction_coordinate_mode_index=cv.reaction_coordinate_mode_index,
        tau=_tau(cv),
    )
    notes = [f"stationary_point_finding:{f.code}" for f in findings]
    codes = {f.code for f in findings}
    if W_TS_NO_IMAGINARY_MODE in codes:
        return "contradicts", None, notes
    if codes & {W_TS_REACTION_COORDINATE_NOT_DESIGNATED, W_TS_REACTION_COORDINATE_AMBIGUOUS}:
        return "unresolved", None, ["reaction_coordinate_not_established", *notes]
    if W_TS_EXTRA_IMAGINARY_MODES_NOT_ASSESSABLE in codes:
        return "unresolved", None, ["imaginary_mode_treatment_not_recorded", *notes]
    flagged = W_TS_EXTRA_IMAGINARY_MODE_ABOVE_TAU in codes
    if higher:
        if flagged:
            return "supports", "local_higher_order_saddle", ["not_first_order_tst_suitable", *notes]
        return "unresolved", None, ["extra_imaginary_modes_below_tau", *notes]
    if flagged:
        return "unsupported", None, ["higher_order_saddle_flagged", *notes]
    if n > 1:
        return "supports", "extra_imaginary_modes_below_tau", ["extra_imaginary_modes_below_tau", *notes]
    return "supports", None, notes


def _witness_support(
    c: NormalizedCalculation, claim: ValidationClaim, subject: StructureSubject
) -> tuple[str, str | None, list[str]]:
    """``(verdict, treatment, advisory)`` of one curvature witness already known to sit on the evaluated geometry.

    ``verdict`` is ``supports``, ``contradicts``, ``unsupported`` or ``unresolved``. Curvature is judged by the shared
    stationary-point owner (``tckdb_schemas.stationary_point``, ADR 0012) over the persisted facts: nothing here counts
    imaginary modes against a constant, and nothing is re-derived from a Hessian. A van der Waals complex's soft mode
    and a transition state's extra modes below tau are the owner's warnings and therefore do not contradict a claim.
    """
    cv = c.curvature
    if not cv.has_freq_result:
        return "unresolved", None, ["hessian_curvature_not_evaluated" if cv.has_hessian else "curvature_result_missing"]
    n = cv.n_imag
    if n is None:
        return "unresolved", None, ["imaginary_mode_count_not_stated"]
    if claim is ValidationClaim.local_minimum:
        return _minimum_support(cv, subject, n)
    if claim in (ValidationClaim.first_order_saddle, ValidationClaim.higher_order_saddle):
        return _saddle_support(cv, claim, n)
    return "unresolved", None, ["claim_not_evaluated"]


def _connectivity(
    d: NormalizedDetermination, roles: _Roles, f: _Findings, evaluated: str
) -> ClaimSupport:
    sources = {c.calculation_ref for _, c in roles.usable.get("connectivity", [])}
    if not sources:
        if roles.unavailable.get("connectivity"):
            f.unresolved(roles.unavailable["connectivity"][0].unavailable_reason or "required_evidence_unavailable")
        else:
            f.unresolved("connectivity_not_established")
        return ClaimSupport(ValidationClaim.reactive_connectivity.value, False)
    rows = [r for r in d.validation_evidence if r["kind"] == "irc" and r["calculation_ref"] in sources]
    if not rows:
        f.unresolved("connectivity_not_established")
        return ClaimSupport(ValidationClaim.reactive_connectivity.value, False)
    refs = tuple(sorted({r["calculation_ref"] for r in rows}))
    if any(r["passed"] is False for r in rows):
        f.block("reactive_connectivity_refuted")
        return ClaimSupport(ValidationClaim.reactive_connectivity.value, False, witness_refs=refs)
    bound = [r for r in rows if r["geometry_ref"] == evaluated]
    if not bound:
        f.unresolved("connectivity_evidence_not_bound_to_the_evaluated_geometry")
        return ClaimSupport(ValidationClaim.reactive_connectivity.value, False, witness_refs=refs)
    return ClaimSupport(ValidationClaim.reactive_connectivity.value, True, witness_refs=refs)


def _curvature(
    d: NormalizedDetermination,
    roles: _Roles,
    claim: ValidationClaim,
    subject: StructureSubject,
    f: _Findings,
    *,
    energy_level_ref: str | None,
) -> ClaimSupport:
    if roles.usable.get("alternative_characterization") and not roles.usable.get("curvature"):
        f.unsupported("alternative_characterization_not_supported")
        return ClaimSupport(claim.value, False)
    witnesses = roles.usable.get("curvature", [])
    if not witnesses:
        unavailable = roles.unavailable.get("curvature", [])
        f.unresolved(
            (unavailable[0].unavailable_reason or "required_evidence_unavailable")
            if unavailable
            else "curvature_evidence_not_declared"
        )
        return ClaimSupport(claim.value, False)
    supports: list[NormalizedCalculation] = []
    contradicts: list[NormalizedCalculation] = []
    unsupported: list[NormalizedCalculation] = []
    unresolved_notes: list[str] = []
    treatment: str | None = None
    for _, calc in witnesses:
        side = CURVATURE_SIDE.get(calc.type)
        relation = _coherence(calc, side, d.evaluated_geometry_ref)
        if relation == "other":
            f.note("curvature_on_other_geometry")
            continue
        if relation == "ambiguous":
            unresolved_notes.append("curvature_geometry_ambiguous")
            continue
        if relation == "unestablished":
            if calc.curvature.has_hessian and calc.curvature.hessian_geometry_ref == d.evaluated_geometry_ref:
                # A stored Hessian bound to exactly this geometry is a witness that exists but is not evaluated.
                unresolved_notes.append("hessian_curvature_not_evaluated")
            else:
                unresolved_notes.append("curvature_geometry_not_established")
            continue
        verdict, how, notes = _witness_support(calc, claim, subject)
        for n in notes:
            if verdict in ("supports",):
                f.note(n)
            elif verdict == "contradicts":
                f.note(n)
        if verdict == "supports":
            supports.append(calc)
            treatment = how or treatment
        elif verdict == "contradicts":
            contradicts.append(calc)
        elif verdict == "unsupported":
            unsupported.append(calc)
            unresolved_notes.extend(notes)
        else:
            unresolved_notes.extend(notes)
    witness_refs = tuple(sorted(c.calculation_ref for c in (*supports, *contradicts)))
    if supports and contradicts:
        f.unresolved("curvature_witnesses_disagree")
        return ClaimSupport(claim.value, False, witness_refs=witness_refs)
    if contradicts:
        f.block("curvature_contradicts_claim")
        return ClaimSupport(claim.value, False, witness_refs=witness_refs)
    if supports:
        surfaces = tuple(sorted({c.level.level_ref for c in supports if c.level.level_ref is not None}))
        if energy_level_ref is not None and surfaces and surfaces != (energy_level_ref,):
            f.note("curvature_surface_differs_from_energy")
        return ClaimSupport(
            claim.value, True, witness_refs=witness_refs, surface_level_refs=surfaces, treatment=treatment
        )
    if unsupported:
        f.unsupported(unresolved_notes[0] if unresolved_notes else "curvature_treatment_not_supported")
    else:
        f.unresolved(unresolved_notes[0] if unresolved_notes else "no_curvature_witness_on_the_evaluated_geometry")
    return ClaimSupport(claim.value, False)


def assess_determination(
    d: NormalizedDetermination,
    *,
    request: StructureRequest,
    subject: StructureSubject,
    calculations: dict[str, NormalizedCalculation],
) -> StructureAssessment:
    """Assess one determination as a basin or saddle candidate."""
    f = _Findings()
    roles = _roles(d, calculations)
    value: EnergyValue | None = None
    energy_calc: NormalizedCalculation | None = None
    energy_level_ref: str | None = None

    if request.geometry_ref is not None and d.evaluated_geometry_ref != request.geometry_ref:
        f.incompatible("evaluated_at_other_geometry")

    # --- the energy -------------------------------------------------------------------------------------------
    if request.quantity is not None:
        if d.quantity is None:
            f.unresolved("energy_not_stated")
        elif d.quantity != request.quantity.value:
            f.incompatible("determination_supplies_other_quantity")
        else:
            usable = roles.usable.get("energy", [])
            if not usable:
                unavailable = roles.unavailable.get("energy", [])
                f.unresolved(
                    (unavailable[0].unavailable_reason or "required_evidence_unavailable")
                    if unavailable
                    else "energy_source_not_declared"
                )
            elif len(usable) > 1:
                f.unresolved("energy_source_ambiguous")
            else:
                _, energy_calc = usable[0]
                energy_level_ref = energy_calc.level.level_ref
                side = ENERGY_SIDE.get(energy_calc.type)
                relation = _coherence(energy_calc, side, d.evaluated_geometry_ref)
                if side is None:
                    f.incompatible("source_type_cannot_supply_an_energy")
                elif relation == "other":
                    f.incompatible("energy_at_other_geometry")
                elif relation == "ambiguous":
                    f.unresolved("energy_geometry_ambiguous")
                elif relation == "unestablished":
                    f.unresolved("energy_geometry_not_established")
                else:
                    value = energy_value(energy_calc, request.quantity, f, geometry_ref=d.evaluated_geometry_ref)
                    if value is not None and energy_calc.type == CalculationType.opt.value:
                        # A validated claim needs a converged optimisation; the recorded value alone does not.
                        if request.effective_claim is not None and energy_calc.energy.opt_converged is False:
                            f.block("optimization_not_converged")
                        elif request.effective_claim is not None and energy_calc.energy.opt_converged is None:
                            f.unresolved("optimization_convergence_unknown")

    # --- the recipe -------------------------------------------------------------------------------------------
    recipe: NormalizedRecipe | None = None
    if energy_calc is not None:
        recipe = normalize_recipe(
            energy_calc.level,
            energy_calc.declaration,
            constraint_rows=energy_calc.constraint_rows,
            needs_constraints=energy_calc.type == CalculationType.opt.value,
            determination_recipe=d.actual_recipe,
        )
        _recipe_findings(recipe, request, f, declaration_state=energy_calc.declaration_state)
        _root_check(recipe, subject, f)
        _subject_findings(energy_calc, request, f)
        if energy_calc.lineage_cyclic:
            f.unresolved("dependency_cycle")

    # --- the structural claim ---------------------------------------------------------------------------------
    claims: list[ClaimSupport] = []
    claim = request.effective_claim
    if claim is not None and claim is not ValidationClaim.reactive_connectivity:
        claims.append(_curvature(d, roles, claim, subject, f, energy_level_ref=energy_level_ref))
    if claim is ValidationClaim.reactive_connectivity or request.require_connectivity:
        claims.append(_connectivity(d, roles, f, d.evaluated_geometry_ref))
    if claim is not None:
        _lineage_advisory(d, roles, f)

    # --- findings, each at its own subject and role -------------------------------------------------------------
    needed_roles = _needed_roles(request)
    needed_refs = {c.calculation_ref for role in needed_roles for _, c in roles.usable.get(role, [])}
    for finding in live_findings(d.findings):
        if finding.scope == "determination":
            _apply_finding(f, finding, blocks=True, role_needed=True)
        elif finding.scope == "geometry":
            _apply_finding(f, finding, blocks=finding.subject_ref == d.evaluated_geometry_ref, role_needed=True)
        else:
            _apply_finding(f, finding, blocks=finding.subject_ref in needed_refs, role_needed=_role_needed(finding, d, needed_roles))

    verdict = f.verdict()
    eligible = verdict is Applicability.applicable and not f.blocking
    return StructureAssessment(
        unit_ref=d.determination_ref,
        applicability=verdict,
        reasons=tuple(f.reasons),
        blocking=tuple(f.blocking),
        advisory=tuple(f.advisory),
        comparative_unknown=tuple(f.comparative_unknown),
        energy=value if eligible else None,
        recipe=recipe,
        claim=_combined_claim(claims) if claims else None,
    )


def _needed_roles(request: StructureRequest) -> frozenset[str]:
    needed: set[str] = set()
    if request.quantity is not None:
        needed.add("energy")
    claim = request.effective_claim
    if claim is not None and claim is not ValidationClaim.reactive_connectivity:
        needed.update({"curvature", "alternative_characterization"})
    if claim is ValidationClaim.reactive_connectivity or request.require_connectivity:
        needed.add("connectivity")
    return frozenset(needed)


def _role_needed(finding: FindingFacts, d: NormalizedDetermination, needed: frozenset[str]) -> bool:
    return finding.role in needed if finding.role is not None else True


def _combined_claim(claims: list[ClaimSupport]) -> ClaimSupport:
    """One claim summary: supported only when every stated claim is, with each witness and surface kept."""
    if len(claims) == 1:
        return claims[0]
    return ClaimSupport(
        claim="+".join(c.claim for c in claims),
        supported=all(c.supported for c in claims),
        witness_refs=tuple(sorted({r for c in claims for r in c.witness_refs})),
        surface_level_refs=tuple(sorted({r for c in claims for r in c.surface_level_refs})),
        treatment=next((c.treatment for c in claims if c.treatment), None),
    )


def _lineage_advisory(d: NormalizedDetermination, roles: _Roles, f: _Findings) -> None:
    """Disclose a role source that no typed dependency path ties to the determination's geometry optimisation.

    The geometry match is what certifies a source; this only reports whether the stored typed edges (``freq_on``,
    ``single_point_on``, ``optimized_from``, ``composite_input``) also say the two belong together.
    """
    optimisations = {c.calculation_ref for _, c in roles.usable.get("geometry_optimization", [])}
    if not optimisations:
        return
    for role in ("energy", "curvature"):
        for _, calc in roles.usable.get(role, []):
            if calc.calculation_ref in optimisations:
                continue
            reachable = {e.parent_ref for e in calc.lineage}
            if not (reachable & optimisations):
                f.note(f"source_not_linked_to_geometry_optimization:{role}")


def assess_unit(
    unit: NormalizedCalculation | NormalizedDetermination,
    *,
    request: StructureRequest,
    subject: StructureSubject,
    calculations: dict[str, NormalizedCalculation],
) -> StructureAssessment:
    """Assess one unit at the request's grain."""
    if request.grain is Grain.calculation:
        assert isinstance(unit, NormalizedCalculation)
        return assess_calculation(unit, request=request, subject=subject)
    assert isinstance(unit, NormalizedDetermination)
    return assess_determination(unit, request=request, subject=subject, calculations=calculations)
