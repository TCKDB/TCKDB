"""Collection point for the scientific check register.

Most entries live beside the check they describe, in the service or
chemistry module that performs it. This module holds the two populations
that *cannot* declare themselves at the point of definition, plus the
list of modules the collector walks.

Why these entries are here rather than beside their checks
----------------------------------------------------------
**PostgreSQL constraints and triggers have no Python object.** A check
constraint is a string in ``__table_args__`` that ``NAMING_CONVENTION``
expands on the way to the database, and a trigger is raw SQL inside an
Alembic revision. Neither can hold a declaration, so the register names
the schema object and the drift guard verifies that name against live
schema metadata — which is a real guard, where a comment would not be.

**The wire-schema package is forbidden from importing the backend.**
``schemas/python/tckdb-schemas/tests/test_import_boundaries.py`` bans
every import rooted at ``app`` from ``tckdb_schemas``, by AST scan and by
a runtime ``sys.modules`` check in a subprocess. A wire-schema check
therefore cannot reference :class:`~app.scientific_checks.ScientificCheck`
where it is defined. Declaring it here keeps the boundary intact and
still holds the real function object, so renaming
``evaluate_transition_state_frequency`` breaks the import of this module
rather than leaving a stale string in a document.

Everything else belongs next to its check. Add it there.
"""

from __future__ import annotations

import sys
from types import ModuleType

from tckdb_schemas.fragments import reaction_atom_map as wire_atom_map
from tckdb_schemas.stationary_point import (
    TAU_ANALYTIC_DEFAULT_CM1,
    TAU_ANALYTIC_TIGHT_CM1,
    TAU_FINITE_DIFFERENCE_ENERGY_CM1,
    TAU_FINITE_DIFFERENCE_GRADIENT_CM1,
    TAU_PARAMETER_KEYS,
    TAU_PROTOCOL_NOT_RECORDED_CM1,
    TS_IMAGINARY_FREQUENCY_MIN_CM1,
    W_N_IMAG_CONTRADICTS_MINIMUM,
    W_N_IMAG_HIGHER_ORDER_SADDLE,
    W_N_IMAG_SUGGESTS_TS,
    W_TS_EXTRA_IMAGINARY_MODE_ABOVE_TAU,
    W_TS_EXTRA_IMAGINARY_MODES_BELOW_TAU,
    W_TS_EXTRA_IMAGINARY_MODES_NOT_ASSESSABLE,
    W_TS_IMAG_FREQ_TOO_SMALL,
    W_TS_NO_IMAGINARY_MODE,
    W_TS_REACTION_COORDINATE_AMBIGUOUS,
    W_TS_REACTION_COORDINATE_NOT_DESIGNATED,
    evaluate_species_entry_frequency,
    evaluate_transition_state_frequency,
    resolve_tau,
)

from app.chemistry import species as chemistry_species
from app.chemistry import units as chemistry_units
from app.scientific_checks import (
    CheckTier,
    CodeChannel,
    ConstantThreshold,
    DatabaseConstraint,
    DesignPosition,
    ProvenanceThreshold,
    PythonCheck,
    ScientificCheck,
    collect_constraint_rejections,
    collect_registered_checks,
)
from app.services import (
    charge_multiplicity_reconciliation,
    reaction_resolution,
    species_resolution,
    transition_state_validation,
)
from app.services import geometry_validation as geometry_validation_service
from app.services import reaction_atom_map as reaction_atom_map_service
from app.services.artifact_integrity import record_integrity_observation
from app.services.artifact_storage import load_artifact_bytes
from app.services.provenance_warnings import (
    W_NETWORK_WIDE_ENERGY_TRANSFER,
    W_REPORTED_NETWORK_SOLVE,
)
from app.services.trust.evaluator import (
    _detect_calculation_hard_fail as detect_calculation_hard_fail,
)

# ---------------------------------------------------------------------------
# Stationary points — the imaginary-mode spectrum is the definition
# ---------------------------------------------------------------------------

#: ADR 0012's protocol table, declared once and rendered into both the
#: register document and the τ entry below.
_TAU_VALUES: tuple[tuple[str, str], ...] = (
    (
        "analytic Hessian, tight grid, tight optimisation",
        f"{TAU_ANALYTIC_TIGHT_CM1:.0f}",
    ),
    (
        "analytic Hessian, default grid and tolerances",
        f"{TAU_ANALYTIC_DEFAULT_CM1:.0f}",
    ),
    (
        "finite-difference Hessian from analytic gradients",
        f"{TAU_FINITE_DIFFERENCE_GRADIENT_CM1:.0f}",
    ),
    (
        "finite-difference Hessian from energies",
        f"{TAU_FINITE_DIFFERENCE_ENERGY_CM1:.0f}",
    ),
    (
        "Hessian method not recorded",
        f"{TAU_PROTOCOL_NOT_RECORDED_CM1:.0f}",
    ),
)

_TAU_THRESHOLD = ProvenanceThreshold(
    name="tau",
    unit="cm-1",
    resolver=resolve_tau,
    parameter_keys=TAU_PARAMETER_KEYS,
    values=_TAU_VALUES,
    fallback=(
        f"When ``freq.hessian_method`` is absent — which is the common case, "
        f"because most outputs do not say — tau is "
        f"{TAU_PROTOCOL_NOT_RECORDED_CM1:.0f} cm-1 and the record stores "
        f"``protocol_not_recorded`` as the basis. The fallback is deliberately "
        f"the *conservative* row rather than the analytic one: assuming the "
        f"better case would flag genuine quadrature noise as a real "
        f"higher-order saddle. Crucially, tau never decides between blocking "
        f"and warning — every blocking rule here is a contract about what the "
        f"record says, not about magnitude — so missing provenance changes how "
        f"loudly a record is flagged and never whether it is accepted."
    ),
    rationale=(
        "The Hessian noise floor is flat in omega-squared, not in omega, so "
        "the uncertainty in a frequency diverges as omega goes to zero: at "
        "300 cm-1 the sign is never in doubt, at 20 cm-1 it is indeterminate. "
        "Where the crossover sits depends on how the second derivatives were "
        "built, on what integration grid, and how tightly the geometry was "
        "converged. The same -42 cm-1 is real negative curvature under an "
        "analytic Hessian on a tight grid and indistinguishable from zero "
        "under a numerical one on a default grid, so it cannot be classified "
        "from the frequency list at all — only against the protocol that "
        "produced it. A constant here would be a claim about physics that is "
        "false."
    ),
)

CHECK_TRANSITION_STATE_REACTION_COORDINATE = ScientificCheck(
    group="Stationary points",
    sort_key=1,
    code=(
        W_TS_NO_IMAGINARY_MODE,
        W_TS_REACTION_COORDINATE_NOT_DESIGNATED,
        W_TS_REACTION_COORDINATE_AMBIGUOUS,
    ),
    asserts=(
        "A transition state has at least one imaginary vibrational mode, "
        "exactly one of them is designated the reaction coordinate, and no "
        "undeclared mode is stiff enough to make that designation meaningless."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional, and narrower than what it replaced. ADR 0008's worked "
        "example was ``n_imag == 1``; ADR 0012 retired that, because "
        "'exactly one negative eigenvalue' is a statement about the exact "
        "Hessian at the exact stationary point on a smooth surface and a "
        "deposit contains none of those. Two scientifically correct "
        "calculations of the same saddle point can return ``n_imag == 1`` and "
        "``n_imag == 3``, so a gate a depositor passes by switching to "
        "``Int=UltraFine`` is not a gate on science — and its cheapest "
        "workaround is deleting a line from the frequency list, which turns "
        "visible ambiguity into invisible falsehood. What survives the "
        "translation into a database row is a *contract*: no imaginary mode "
        "at all means there is no reaction coordinate and the structure is "
        "not a transition state; more than one with no designation means the "
        "record cannot answer the question every transition-state-theory code "
        "asks it; and an undeclared mode at least as stiff as the designated "
        "one means the designation is an assertion the record does not "
        "support. None of the three can be produced by a correct calculation "
        "that has been honestly described."
    ),
    adr="0012, 0008",
    enforced_by=(
        PythonCheck(
            evaluate_transition_state_frequency,
            note=(
                "A transition state carries no ``stationary_point_kind`` "
                "column — the entity *is* the claim — so the rule needs no "
                "kind argument. Upload schemas call "
                "``raise_for_blocking_findings`` from a ``model_validator``, "
                "so the contradiction becomes a 422 before the route body "
                "opens a submission. The designation is then persisted on "
                "``calc_freq_result.reaction_coordinate_mode_index``, which "
                "is what lets the read-time trust rubric cite this judgement "
                "instead of re-deriving it."
            ),
        ),
        DesignPosition(
            where=(
                "``_check_ts_reaction_coordinate_designated`` and "
                "``_detect_transition_state_entry_hard_fail`` "
                "(``backend/app/services/trust/rubrics.py``, "
                "``backend/app/services/trust/evaluator.py``) ask at read time "
                "whether the record carries the designation this blocking tier "
                "required of it — a question about persisted state, not a "
                "second opinion about physics. They cannot disagree with the "
                "verdict above, which is the collapse ADR 0008 section 9 asked "
                "for."
            )
        ),
    ),
    escape_hatch=(
        "Three, and they are the point of the rule. Deposit no frequency "
        "evidence: ``n_imag=None`` produces no findings at all, because "
        "absence is never contradiction. Or deposit the extra imaginary "
        "modes honestly — designate the reaction coordinate and give each "
        "other mode a disposition (``torsion``, ``rigid_body_residue``, "
        "``intermolecular``, ``ring_pucker``, ``symmetry_breaking``, or an "
        "explicit ``unassigned``) — and a genuine higher-order saddle is "
        "accepted with a warning and a structural flag. Or, if the extra mode "
        "really is the barrier, designate *it*. What has no door is refusing "
        "to say which mode is the reaction coordinate."
    ),
)

CHECK_MINIMUM_ZERO_IMAGINARY_MODES = ScientificCheck(
    group="Stationary points",
    sort_key=2,
    code=W_N_IMAG_CONTRADICTS_MINIMUM,
    asserts=(
        "A species entry declared a minimum has no imaginary vibrational "
        "modes."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional. A covalently bound minimum whose own frequency "
        "evidence reports an imaginary mode is mislabelled: the correct "
        "response is to re-optimise on a tighter integration grid or to "
        "declare it as something else, so refusing the deposit rejects no "
        "correct calculation."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            evaluate_species_entry_frequency,
            note=(
                "One imaginary mode and two-or-more are folded into a single "
                "blocking message that names "
                f"``{W_N_IMAG_HIGHER_ORDER_SADDLE}`` for the higher-order "
                "case. A stationary-point kind the module has not been taught "
                "about produces no findings — adding an enum member must be a "
                "deliberate decision, not a default."
            ),
        ),
    ),
    escape_hatch=(
        "Declare ``species_entry_kind='vdw_complex'``, which records the same "
        "mode with a warning instead, or deposit the structure through a "
        "transition-state payload if the single imaginary mode is real."
    ),
)

CHECK_VDW_COMPLEX_IMAGINARY_MODE = ScientificCheck(
    group="Stationary points",
    sort_key=3,
    code=(
        W_N_IMAG_CONTRADICTS_MINIMUM,
        W_N_IMAG_HIGHER_ORDER_SADDLE,
        W_N_IMAG_SUGGESTS_TS,
    ),
    asserts=(
        "A van der Waals complex is formally a minimum, so an imaginary mode "
        "on one is recorded and flagged rather than refused — unless the mode "
        "is too stiff to be an intermolecular one, which suggests a genuine "
        "reaction coordinate."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    tier_rationale=(
        "The carve-out is the scientific content. A van der Waals complex is "
        "held together by intermolecular forces and its stretch, bends and "
        "hindered internal rotations sit below roughly 50 cm-1 — the region "
        "where numerical noise in a finite-difference or quadrature-grid "
        "Hessian is comparable to the true curvature. A small imaginary mode "
        "there is usually a grid artifact, so refusing it would force an "
        "expensive re-run for a physically meaningless mode. This is what "
        "earns ``vdw_complex`` a separate enum member: it is the only place "
        "the two minimum kinds behave differently."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            evaluate_species_entry_frequency,
            note=(
                "Same entry point as the blocking minimum rule; the declared "
                "kind decides the tier while the code names the finding. A "
                f"mode at or above {TS_IMAGINARY_FREQUENCY_MIN_CM1:.0f} cm-1 "
                f"additionally raises ``{W_N_IMAG_SUGGESTS_TS}``, because that "
                "is far too stiff to be intermolecular."
            ),
        ),
    ),
    escape_hatch=(
        "This *is* the escape hatch for the blocking minimum rule. Its own "
        "cost is that a genuinely mislabelled saddle point deposited as a van "
        "der Waals complex is accepted with a warning."
    ),
)

CHECK_TRANSITION_STATE_EXTRA_IMAGINARY_MODES = ScientificCheck(
    group="Stationary points",
    sort_key=4,
    code=(
        W_TS_EXTRA_IMAGINARY_MODES_BELOW_TAU,
        W_TS_EXTRA_IMAGINARY_MODE_ABOVE_TAU,
        W_TS_EXTRA_IMAGINARY_MODES_NOT_ASSESSABLE,
    ),
    asserts=(
        "A transition state's imaginary modes other than the reaction "
        "coordinate are judged by magnitude against a tolerance read from the "
        "protocol that produced them, not by counting them."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    tier_rationale=(
        "The extra modes cannot be classified from the frequency list, so "
        "refusing the deposit would assert a determination the deposit does "
        "not contain the information to support — an expectation about "
        "numerical quality wearing the costume of a definition. They can also "
        "be simply correct: a harmonic model is inapplicable to a torsion, a "
        "ring pucker or an intermolecular mode in a loose complex, and a "
        "transition state can sit at a maximum of a torsional profile while "
        "being a perfectly correct reactive bottleneck, in which case the "
        "extra negative eigenvalue is not an artefact but exactly right. "
        "Warning rather than blocking is also the only choice that preserves "
        "evidence: a record carrying all three frequencies and the full "
        "protocol can be reassessed under a better rule in five years, while "
        "a refused deposit leaves nothing behind."
    ),
    adr="0012",
    thresholds=(_TAU_THRESHOLD,),
    enforced_by=(
        PythonCheck(
            evaluate_transition_state_frequency,
            note=(
                "Above tau the record is flagged as well as warned: the flag "
                "is ADR 0012's answer to 'warnings get ignored', excluding "
                "the record from default transition-state consumption without "
                "creating the incentive to edit the frequency list that a hard "
                "block creates. Below tau it is warned only. The flag and the "
                "tau that decided it are persisted on ``calc_freq_result`` "
                "rather than recomputed, so a later parser improvement cannot "
                "silently re-label historical records."
            ),
        ),
    ),
    escape_hatch=(
        "None is needed — the check never refuses. Its cost runs the other "
        "way: a genuine higher-order saddle, a torsional maximum and a "
        "valley-ridge inflection are all accepted, and only the structural "
        "flag separates them from a clean first-order saddle for a consumer "
        "who reads it."
    ),
    divergence=(
        "ADR 0012 recommends replacing the threshold with a determination — "
        "projecting each imaginary eigenvector onto the six rigid-body vectors "
        "(more than about 90 percent overlap means projection residue) and "
        "onto dihedral-rotation vectors (more than about 70 percent means a "
        "torsion) — and says those should be implemented *before* tau is "
        "tuned, because a determination beats a threshold wherever one is "
        "available. They are not implemented, and cannot be: "
        "``backend/app/db/models/transition_state.py`` records that "
        "normal-mode displacement evidence is deliberately absent from TCKDB, "
        "'a producer-side heuristic, not a database record'. So the "
        "disposition on each extra mode is *declared by the depositor* and "
        "TCKDB cannot check it. Recorded, not resolved: reversing that "
        "decision is a schema change and its own ADR."
    ),
)

CHECK_TRANSITION_STATE_SOFT_REACTION_COORDINATE = ScientificCheck(
    group="Stationary points",
    sort_key=5,
    code=W_TS_IMAG_FREQ_TOO_SMALL,
    asserts=(
        "A transition state's reaction coordinate should exceed roughly "
        f"{TS_IMAGINARY_FREQUENCY_MIN_CM1:.0f} cm-1 in magnitude."
    ),
    tier=CheckTier.warn,
    channel=CodeChannel.upload_warning,
    tier_rationale=(
        "ADR 0008's worked counter-example, and the sharpest statement of the "
        "whole rule. A very soft imaginary mode is suspicious — often an "
        "under-converged geometry or a coarse integration grid — but it can be "
        "perfectly real, because flat barriers and variational transition "
        "states genuinely produce them. Magnitude is therefore a quality "
        "expectation, never a definition, and a check that could fire on a "
        "correct novel result must not block."
    ),
    adr="0008, 0012",
    thresholds=(
        ConstantThreshold(
            name="TS_IMAGINARY_FREQUENCY_MIN_CM1",
            value=TS_IMAGINARY_FREQUENCY_MIN_CM1,
            unit="cm-1",
            rationale=(
                "A starting point rather than a physical constant: reaction "
                "coordinates for hydrogen transfers run to thousands of cm-1, "
                "while genuinely flat barriers fall well under 100. Unlike "
                "tau it really is fixed in code, because it is a statement "
                "about chemistry — what a reaction coordinate looks like — "
                "rather than about numerics. It is also the scale that "
                "separates a van der Waals complex's soft intermolecular "
                "modes from a real reaction coordinate, and is reused for "
                "that judgement."
            ),
        ),
        _TAU_THRESHOLD,
    ),
    enforced_by=(
        PythonCheck(
            evaluate_transition_state_frequency,
            note=(
                "ADR 0012 changed what this fires *on* without changing what "
                "it fires at. It used to read the single imaginary mode of a "
                "record that had passed the ``n_imag == 1`` gate; it now reads "
                "the designated reaction coordinate of a record that may carry "
                "several. Both thresholds are declared above because both "
                "reach the same message: the warning quotes the protocol's "
                "tau alongside its own constant, so a reader who is told a "
                "reaction coordinate is soft can see immediately whether that "
                "calculation could resolve a small mode at all."
            ),
        ),
    ),
    escape_hatch=(
        "None is needed — the check never refuses. A referee should read the "
        "threshold as a tunable reporting line, not a claim about physics."
    ),
)

# ---------------------------------------------------------------------------
# Atom mapping across a reaction (ADR 0011)
# ---------------------------------------------------------------------------

_ATOM_MAP_DUAL_ENFORCEMENT = (
    "Stated twice on purpose: once at the wire boundary, where the payload "
    "already holds every XYZ block the rule needs so the refusal arrives as a "
    "clean 422 before anything is written, and once as a database constraint, "
    "where a second write path cannot get around it."
)

CHECK_ATOM_MAP_ELEMENT_CONSERVED = ScientificCheck(
    group="Atom mapping across a reaction",
    sort_key=1,
    code=wire_atom_map.W_ATOM_MAP_ELEMENT_NOT_CONSERVED,
    asserts=(
        "An atom does not change element on the way across a reaction: carbon "
        "does not map onto nitrogen."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional. A map asserting that an element transmutes is a record "
        "that cannot be what it says it is, not an unusual result."
    ),
    adr="0011, 0008",
    enforced_by=(
        PythonCheck(wire_atom_map.validate_reaction_atom_map, note=_ATOM_MAP_DUAL_ENFORCEMENT),
        DatabaseConstraint(
            table="reaction_atom_map_pair",
            name="ck_reaction_atom_map_pair_element_matches",
            kind="check",
            definition="upper(element) = upper(ts_element)",
            # Deliberately the *same* code the wire check raises. The two
            # sites enforce one claim, so a client that learned to handle
            # ``atom_map_element_not_conserved`` from a 422 handles it
            # identically when a second write path reaches the database
            # first; only the status differs, and the status is what says
            # whether the payload was rejected before or after resolution.
            rejection_code=wire_atom_map.W_ATOM_MAP_ELEMENT_NOT_CONSERVED,
            rejection_detail=(
                "An atom map pairs two atoms of different elements. An atom "
                "does not change element on the way across a reaction."
            ),
        ),
    ),
    escape_hatch=(
        "Case is not load-bearing. The comparison is deliberately "
        "case-insensitive because the two ends quote two different "
        "geometries and nothing guarantees they spell an element the same "
        "way — carbon becoming nitrogen is a contradiction, while ``Cl`` "
        "becoming ``CL`` is one program shouting where another did not. "
        "``b4e7c1d20f83`` canonicalises the symbol on the way into "
        "``geometry_atom.element``, which makes disagreement rare on rows "
        "written through the API; it is a convention rather than a "
        "constraint, so both the database check and the Python check still "
        "normalise instead of assuming it. Isotope mass number is "
        "deliberately *not* carried across the same way, because a NULL "
        "disables a MATCH SIMPLE foreign key; isotope consistency is checked "
        "in the service layer instead."
    ),
)

CHECK_ATOM_MAP_IS_A_BIJECTION = ScientificCheck(
    group="Atom mapping across a reaction",
    sort_key=2,
    code=wire_atom_map.W_ATOM_MAP_NOT_A_BIJECTION,
    asserts=(
        "One saddle-point atom is claimed by exactly one atom of each leg, and "
        "one participant atom maps to exactly one saddle-point atom."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional. A map is a bijection or it is not a map; an atom "
        "claimed twice describes no mechanism at all."
    ),
    adr="0011, 0008",
    enforced_by=(
        PythonCheck(wire_atom_map.validate_reaction_atom_map, note=_ATOM_MAP_DUAL_ENFORCEMENT),
        DatabaseConstraint(
            table="reaction_atom_map_pair",
            name="uq_reaction_atom_map_pair_ts_atom_index",
            kind="unique",
            definition="(atom_map_id, side, ts_atom_index)",
            rejection_code=wire_atom_map.W_ATOM_MAP_NOT_A_BIJECTION,
            rejection_detail=(
                "Two atoms of one leg claim the same saddle-point atom. A map "
                "is a bijection or it is not a map."
            ),
        ),
        DatabaseConstraint(
            table="reaction_atom_map_pair",
            name="uq_reaction_atom_map_pair_atom_map_id",
            kind="unique",
            definition="(atom_map_id, structure_participant_id, atom_index)",
            rejection_code=wire_atom_map.W_ATOM_MAP_NOT_A_BIJECTION,
            rejection_detail=(
                "One participant atom is mapped more than once. A map is a "
                "bijection or it is not a map."
            ),
        ),
    ),
    escape_hatch=(
        "Per leg, not globally: the reactant and product legs each claim the "
        "whole saddle point, which is the point of storing two maps both "
        "pointing at it. A ``side`` column exists on the pair row purely so "
        "this can be a unique constraint, because SQL cannot dereference the "
        "participant to find its role."
    ),
)

CHECK_ATOM_MAP_INDICES_ARE_GEOMETRY_RELATIVE = ScientificCheck(
    group="Atom mapping across a reaction",
    sort_key=3,
    code=wire_atom_map.W_ATOM_MAP_INDICES_NOT_GEOMETRY_RELATIVE,
    asserts=(
        "Every atom index in a map is counted against a named geometry that "
        "the participant actually owns, and names an atom that geometry "
        "actually has."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional, and it is where ADR 0011's central choice is cashed "
        "out. Atom indices are not a property of a species — "
        "``geometry_atom.atom_index`` is a property of a *geometry* — so "
        "'reactant atom 3' is meaningless until the geometry being counted is "
        "named. An index counted against the wrong geometry silently means "
        "the wrong atom, which is the failure mode geometry-relative indexing "
        "was chosen to make impossible."
    ),
    adr="0011, 0008",
    enforced_by=(
        PythonCheck(wire_atom_map.validate_reaction_atom_map, note=_ATOM_MAP_DUAL_ENFORCEMENT),
        DatabaseConstraint(
            table="reaction_atom_map_pair",
            name="fk_reaction_atom_map_pair_geometry_id_geometry_atom",
            kind="foreign_key",
            definition=(
                "(geometry_id, atom_index, element) -> "
                "geometry_atom(geometry_id, atom_index, element)"
            ),
        ),
        DatabaseConstraint(
            table="reaction_atom_map_pair",
            name="fk_reaction_atom_map_pair_ts_geometry_id_geometry_atom",
            kind="foreign_key",
            definition=(
                "(transition_state_geometry_id, ts_atom_index, ts_element) -> "
                "geometry_atom(geometry_id, atom_index, element)"
            ),
        ),
        DatabaseConstraint(
            table="reaction_atom_map_pair",
            name="fk_reaction_atom_map_pair_structure_participant",
            kind="foreign_key",
            definition=(
                "(structure_participant_id, side) -> "
                "reaction_entry_structure_participant(id, role)"
            ),
        ),
    ),
    escape_hatch=(
        "None, and the cost is stated in ADR 0011: the map is welded to the "
        "geometries it names, so depositing a second conformer or "
        "re-optimising at another level of theory does not carry it across. "
        "Canonical-order-relative indexing would be portable, and was "
        "rejected because its failure mode is a map that looks fine and "
        "refers to a different atom order than the depositor intended. "
        "Portability can be added later as a derived view; correctness cannot "
        "be retrofitted onto records nobody can verify."
    ),
)

CHECK_ATOM_MAP_ACCOUNTS_FOR_EVERY_ATOM = ScientificCheck(
    group="Atom mapping across a reaction",
    sort_key=4,
    code=wire_atom_map.W_ATOM_MAP_ATOMS_UNACCOUNTED_FOR,
    asserts=(
        "When a map covers every declared participant of an atom-balanced "
        "reaction, both legs claim the same saddle-point atoms and no "
        "saddle-point atom is left unclaimed."
    ),
    tier=CheckTier.block,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Definitional, but only under a precondition that has to be checked "
        "first. A reactant atom unaccounted for in the products is a "
        "contradiction *when no species is missing*. When the declared "
        "reaction is not atom-balanced a species genuinely is missing, and "
        "the same discrepancy is incompleteness rather than contradiction — "
        "so the rule is gated on the map being complete over every "
        "participant and on the reaction balancing, and warns instead "
        "otherwise."
    ),
    adr="0011, 0008",
    enforced_by=(
        PythonCheck(
            wire_atom_map.validate_reaction_atom_map,
            note=(
                "Wire boundary only. This one has no database counterpart: it "
                "is a statement about a whole map rather than about one pair "
                "row, and a per-row constraint cannot see it."
            ),
        ),
    ),
    escape_hatch=(
        "Leave the map incomplete, or deposit an unbalanced reaction — either "
        "drops the rule to the warning tier by design rather than by "
        "accident."
    ),
)

CHECK_ATOM_MAP_PROVENANCE_IS_DECLARED = ScientificCheck(
    group="Atom mapping across a reaction",
    sort_key=8,
    code=wire_atom_map.W_ATOM_MAP_INFERRED_REQUIRES_NOTE,
    asserts=(
        "An atom map records whether a human asserted it or an algorithm "
        "produced it, an inferred map names the algorithm, and neither "
        "attribution can be relabelled afterwards."
    ),
    tier=CheckTier.structural,
    channel=CodeChannel.error_envelope,
    tier_rationale=(
        "Not a runtime check but a shape. ADR 0011 permits inference only as "
        "a labelled and separable thing, because an atom map is a scientific "
        "claim about a mechanism and picking one by algorithm and storing it "
        "unlabelled would manufacture provenance — the same failure ADR 0009 "
        "identified when a network-wide energy-transfer value was duplicated "
        "across wells. The immutability trigger closes the laundering path a "
        "check constraint cannot see: ``UPDATE reaction_atom_map SET "
        "source='declared', note=NULL`` satisfies both existing constraints, "
        "and a CHECK cannot read ``OLD``."
    ),
    adr="0011",
    enforced_by=(
        PythonCheck(
            wire_atom_map.ReactionAtomMapIn.validate_inferred_names_its_algorithm,
            note=(
                "The wire half of the rule, and it was missing from this entry "
                "until the codes were audited: the register listed only the two "
                "schema objects, so it read as enforced by the database alone "
                "when in fact an inferred map with no note is refused at the "
                "payload boundary first. Only the note half is stated here — "
                "the immutability of ``source`` is a statement about an UPDATE, "
                "which no payload validator can see."
            ),
        ),
        DatabaseConstraint(
            table="reaction_atom_map",
            name="ck_reaction_atom_map_inferred_requires_note",
            kind="check",
            definition=(
                "source <> 'inferred' OR (note IS NOT NULL AND btrim(note) <> '')"
            ),
        ),
        DatabaseConstraint(
            table="reaction_atom_map",
            name="trg_reaction_atom_map_source_immutable",
            kind="trigger",
            definition=(
                "BEFORE UPDATE FOR EACH ROW: refuse any change to the source column"
            ),
        ),
    ),
    escape_hatch=(
        "``note`` and ``equivalent_map_count`` stay mutable on purpose, so a "
        "depositor can correct a description or record newly counted "
        "symmetry-equivalent maps without touching the attribution. Symmetry "
        "means a valid map is often not unique; ADR 0011 declines to "
        "canonicalise among equivalent maps and leaves reaction-path "
        "degeneracy to a later decision."
    ),
)

# ---------------------------------------------------------------------------
# Pressure-dependent networks
# ---------------------------------------------------------------------------

#: 409 codes for the PDep constraints. These are refusal codes, not
#: :mod:`app.services.provenance_warnings` tokens, and the distinction is
#: load-bearing: ``network_wide_energy_transfer_scope`` is the *warning*
#: that says a network-wide <dE>down was declared, which is accepted
#: science. The code below is the opposite -- a row whose ``scope`` and
#: whose columns contradict each other, which is not a deposit anyone
#: meant to make. Reusing the warning token for it would tell a client
#: that an accepted upload and a refused one were the same event.
R_ENERGY_TRANSFER_SCOPE_COLUMNS_DISAGREE = "energy_transfer_scope_columns_disagree"
R_NETWORK_SOLVE_REPORTED_REQUIRES_LITERATURE = (
    "network_solve_reported_requires_literature"
)

CHECK_NETWORK_SOLVE_KIND_EVIDENCE = ScientificCheck(
    group="Pressure-dependent networks",
    sort_key=1,
    code=W_REPORTED_NETWORK_SOLVE,
    asserts=(
        "A set of phenomenological k(T,P) declares whether this database holds "
        "the master-equation derivation behind it; a ``computed`` solve must "
        "actually carry master-equation evidence, and a ``reported`` one must "
        "cite the publication it was transcribed from."
    ),
    tier=CheckTier.structural,
    channel=CodeChannel.upload_warning,
    tier_rationale=(
        "The blocking half is definitional: a computed solve with *zero* state "
        "energies contradicts its own kind, and a reported solve with no "
        "literature would assert rates carrying neither a derivation nor a "
        "source. The accepting half is why the token exists at all — published "
        "PLOG and Chebyshev fits are correct, common, citable science, so the "
        "coverage rules that are right for a solve run here could fire on a "
        "correct result and must not block. They warn instead, on every read "
        "path that reaches a rate."
    ),
    adr="0010, 0008",
    enforced_by=(
        DatabaseConstraint(
            table="network_solve",
            name="ck_network_solve_reported_requires_literature",
            kind="check",
            definition="kind <> 'reported' OR literature_id IS NOT NULL",
            rejection_code=R_NETWORK_SOLVE_REPORTED_REQUIRES_LITERATURE,
            rejection_detail=(
                "A network solve declared as 'reported' cites no literature. "
                "Transcribed rates must name the publication they came from, "
                "because nothing else in the deposit accounts for them."
            ),
        ),
        DatabaseConstraint(
            table="network_solve",
            name="ct_network_solve_computed_evidence",
            kind="trigger",
            definition=(
                "deferred constraint trigger, at COMMIT: a computed solve "
                "must hold at least one state energy; at least one "
                "energy-transfer model if its network declares a well; at "
                "least one channel barrier if its network declares a "
                "saddle-point path"
            ),
        ),
    ),
    escape_hatch=(
        "Declare ``kind='reported'`` and cite the literature. That relaxes the "
        "state-energy, channel-barrier and energy-transfer coverage rules a "
        "computed solve is held to, which is what makes a paper's "
        "supplementary table depositable at all — before the token existed "
        "such rates could not be deposited, so they went into somebody's "
        "private mechanism file, uncited and unversioned."
    ),
    divergence=(
        "Existence, not coverage — and the trigger must not be read as the "
        "whole contract. The database guarantees a computed solve carries "
        "nonzero evidence of each applicable class; the three coverage rules "
        "(one energy per state, one energy-transfer model per (well, collider) "
        "pair or a network-wide declaration, one barrier per saddle-point "
        "path) remain properties of the single wired upload path. A computed "
        "solve with four energies out of five passes the database and fails "
        "the validator. ADR 0010's amendment states this deliberately: a "
        "computed solve with *zero* energies is a contradiction and may block, "
        "while an incomplete one is a true record to be graded by the trust "
        "and reproducibility layers. Separately, ``kind`` cannot surface in "
        "CHEMKIN export, which has no provenance field; a tripwire test guards "
        "the moment network kinetics first reach mechanism output."
    ),
)

CHECK_ENERGY_TRANSFER_SCOPE = ScientificCheck(
    group="Pressure-dependent networks",
    sort_key=2,
    code=W_NETWORK_WIDE_ENERGY_TRANSFER,
    asserts=(
        "A collisional energy-transfer model records whether its ⟨ΔE⟩down was "
        "determined per (well, collider) pair or declared once for the whole "
        "network."
    ),
    tier=CheckTier.structural,
    channel=CodeChannel.upload_warning,
    tier_rationale=(
        "A network-wide ⟨ΔE⟩down is correct, common, published science — "
        "Arkane, RMG and MESS inputs routinely specify a single "
        "``SingleExponentialDown`` applied network-wide — so a check demanding "
        "one entry per (well x collider) pair could fire on a correct result "
        "and must not block. It is an expectation about *resolution*, not a "
        "definition. What stays definitional still blocks: a ``per_well`` "
        "entry naming no well contradicts itself, a ``network_wide`` entry "
        "naming one contradicts itself, and a payload mixing the two is "
        "genuinely ambiguous."
    ),
    adr="0009, 0008",
    enforced_by=(
        DatabaseConstraint(
            table="network_solve_energy_transfer",
            name="ck_network_solve_energy_transfer_scope_columns_agree",
            kind="check",
            definition=(
                "(scope='per_well' AND state_id IS NOT NULL AND "
                "collider_species_entry_id IS NOT NULL) OR "
                "(scope='network_wide' AND state_id IS NULL AND "
                "collider_species_entry_id IS NULL)"
            ),
            rejection_code=R_ENERGY_TRANSFER_SCOPE_COLUMNS_DISAGREE,
            rejection_detail=(
                "A collisional energy-transfer model declares a scope its own "
                "columns contradict: a 'per_well' model must name the well and "
                "the collider it was determined for, and a 'network_wide' one "
                "must name neither."
            ),
        ),
    ),
    escape_hatch=(
        "Declare ``scope='network_wide'``. The physics behind the old per-well "
        "rule was never in dispute — ⟨ΔE⟩down depends on the density of states "
        "of the excited well and on the collider's ability to accept internal "
        "energy, so argon and helium do not relax the same well identically. "
        "The rule was wrong in practice because it confused what the quantity "
        "*is* with what a calculation *determined*: the only way to satisfy it "
        "was to paste one number once per well, and the repository's own "
        "Arkane ingester did exactly that. Those rows are indistinguishable "
        "from independently determined values — a provenance loss manufactured "
        "by the validation itself, worse than the gap it closed, because an "
        "absent value is honest while a duplicated one is a false positive "
        "every consumer will faithfully propagate."
    ),
)

# ---------------------------------------------------------------------------
# Statistical mechanics
# ---------------------------------------------------------------------------

#: The 409 code for the statmech subject constraint. This entry was the
#: register's own worked example of the gap #66 closes: it was the one
#: check declared with no code and no channel *solely* because its claim
#: was held by PostgreSQL, where nothing could carry a code to a client.
R_STATMECH_SUBJECT_NOT_EXACTLY_ONE = "statmech_subject_not_exactly_one"

CHECK_STATMECH_HAS_EXACTLY_ONE_SUBJECT = ScientificCheck(
    group="Statistical mechanics",
    sort_key=1,
    code=R_STATMECH_SUBJECT_NOT_EXACTLY_ONE,
    asserts=(
        "A partition function belongs to exactly one subject — a species entry "
        "or a transition-state entry, never both and never neither."
    ),
    tier=CheckTier.structural,
    channel=CodeChannel.database_constraint,
    tier_rationale=(
        "A modelling position rather than an arithmetic bound. Canonical "
        "transition state theory needs the saddle point's own partition "
        "function, so a transition state has to be a first-class subject of a "
        "statmech row. The alternative — encoding a transition state as a "
        "pseudo-species — would make every partition function's subject "
        "ambiguous and would put saddle points into a kind reserved for lumped "
        "and phenomenological constructs that the conservation laws "
        "deliberately exempt."
    ),
    adr="0008",
    emitted=True,
    enforced_by=(
        DatabaseConstraint(
            table="statmech",
            name="ck_statmech_statmech_exactly_one_subject",
            kind="check",
            definition="(species_entry_id IS NULL) <> (transition_state_entry_id IS NULL)",
            rejection_code=R_STATMECH_SUBJECT_NOT_EXACTLY_ONE,
            rejection_detail=(
                "A partition function names both a species entry and a "
                "transition-state entry, or neither. It belongs to exactly one "
                "subject."
            ),
        ),
    ),
    escape_hatch=None,
)

# ---------------------------------------------------------------------------
# Custody of the evidence
# ---------------------------------------------------------------------------

CHECK_STORED_ARTIFACT_BYTES_MATCH_THEIR_DIGEST = ScientificCheck(
    group="Custody of the evidence",
    sort_key=1,
    code="artifact_integrity_failed",
    asserts=(
        "The bytes TCKDB serves for a stored artifact are the bytes it "
        "stored, and a record whose evidence is known not to be is labelled "
        "as such at read time rather than graded as if it were intact."
    ),
    tier=CheckTier.label,
    channel=CodeChannel.trust_label,
    tier_rationale=(
        "It cannot block, because the fact does not exist at upload: the "
        "artifact hashed correctly when it was accepted, and the break "
        "happened afterwards, in TCKDB's own custody. It cannot be a warning "
        "either, because a warning annotates the payload for whoever "
        "deposited it, while the party who needs to know here is every "
        "future reader of a record the depositor got right. So the "
        "consequence is a read-time label. It is a *hard* fail rather than "
        "an advisory one because the alternative is to keep publishing an "
        "evidence-completeness score computed over bytes TCKDB cannot "
        "produce — and note what this reason does not say: unlike every "
        "other hard fail, it does not claim the record is wrong, only that "
        "the evidence behind it can no longer be shown. The label fires on "
        "recorded detections only, so its absence is never a verification "
        "claim; an artifact nobody has read has been checked by nothing."
    ),
    adr="0014, 0004, 0002",
    emitted=True,
    enforced_by=(
        PythonCheck(
            load_artifact_bytes,
            note=(
                "Recomputes SHA-256 over every retrieval and compares it "
                "against the content-addressed key, in constant time. This "
                "is the detection; it is reached by the approved-byte "
                "download, the ESS-parameter backfill, archive streaming, "
                "and the operator verification pass, and by nothing else — "
                "an object none of those touch is never checked."
            ),
        ),
        PythonCheck(
            record_integrity_observation,
            note=(
                "Turns a detection into an append-only "
                "``artifact_integrity_event`` row, in its own transaction so "
                "the record survives the request that discovered it. Carries "
                "expected-versus-observed digest and size plus the store's "
                "own ``LastModified`` / ``ETag`` / ``ContentLength``, which "
                "is what lets an operator separate 'the object was modified "
                "after write' from 'we never stored what we said we did' "
                "from 'the store returned wrong bytes on this read'."
            ),
        ),
        PythonCheck(
            detect_calculation_hard_fail,
            note=(
                "Applies the label. Any calculation with a recorded break on "
                "any of its artifacts hard-fails, ahead of the "
                "geometry-validation verdict, and the existing "
                "``source_calculation_hard_failed_for_required_role`` "
                "propagates that to every product naming the calculation as "
                "a required source. Reads database rows only — the trust "
                "rubric's ``artifacts_present`` deliberately does not verify "
                "bytes, so that a storage outage can never be reported as a "
                "depositor who failed to upload a log."
            ),
        ),
        DatabaseConstraint(
            table="artifact_integrity_event",
            name="ck_artifact_integrity_event_observed_digest_present_iff_read",
            kind="check",
            definition=(
                "(finding = 'object_missing' AND observed_sha256 IS NULL) OR "
                "(finding <> 'object_missing' AND observed_sha256 IS NOT NULL)"
            ),
        ),
        DatabaseConstraint(
            table="artifact_integrity_event",
            name="ck_artifact_integrity_event_verified_requires_matching_digest",
            kind="check",
            definition="finding <> 'verified' OR observed_sha256 = sha256",
        ),
    ),
    escape_hatch=(
        "Restore the object. There is no legitimate deposit this refuses — "
        "it refuses nothing — but a label that could never be cleared would "
        "be a trap, so the door is a later observation rather than an "
        "edit: the corrupt object is never deleted or overwritten by "
        "TCKDB, and re-uploading the original bytes under the same digest "
        "lets the verification pass record a ``verified`` observation that "
        "supersedes the break. A check constraint requires that row to "
        "carry a digest matching the key, so the hard fail is cleared by "
        "bytes that actually hash correctly and never by an operator "
        "asserting that they do. The break row stays as the account of "
        "what happened."
    ),
)


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------

CHECK_REPRODUCIBILITY_IS_ITS_OWN_JUDGEMENT = ScientificCheck(
    group="Reproducibility",
    sort_key=1,
    code=None,
    asserts=(
        "Whether a record's preserved evidence is sufficient to understand, "
        "audit or repeat it is assessed separately from how far its evidence "
        "is trusted and from whether a curator approved it, and the three may "
        "disagree."
    ),
    tier=CheckTier.structural,
    channel=CodeChannel.none,
    tier_rationale=(
        "Not a check that fires but a position about what may never be "
        "collapsed into a single verdict. Reproducibility is graded under "
        "append-only, rubric-versioned assessments rather than as a field on a "
        "scientific row or an alias for review status, so a rubric can be "
        "revised without rewriting history and an old judgement stays "
        "interpretable. This is the same reasoning that made ADR 0005 record "
        "execution environments rather than grade them, and it is what lets "
        "the warning tiers elsewhere in this register be defensible: an "
        "incomplete record is accepted precisely because a separate layer "
        "exists to say how incomplete it is."
    ),
    adr="0002, 0005",
    emitted=False,
    enforced_by=(
        DesignPosition(
            where=(
                "``reproducibility_assessment`` rows carry ``described`` / "
                "``auditable`` / ``rerunnable`` under a versioned rubric, "
                "append-only, independent of ``record_review`` and of the "
                "trust evaluator (``app/services/trust/``)"
            )
        ),
    ),
    escape_hatch=None,
)


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

#: Every module holding :class:`ScientificCheck` declarations. A guard
#: test fails if a file in the repository constructs one and is not
#: listed here, so forgetting to register a module is caught rather than
#: silently omitted from the register.
DECLARING_MODULES: tuple[ModuleType, ...] = (
    chemistry_species,
    chemistry_units,
    charge_multiplicity_reconciliation,
    geometry_validation_service,
    reaction_atom_map_service,
    reaction_resolution,
    species_resolution,
    transition_state_validation,
    sys.modules[__name__],
)


def register() -> list[ScientificCheck]:
    """Return the whole scientific check register, deterministically ordered."""
    return collect_registered_checks(DECLARING_MODULES)


def constraint_rejections() -> dict[str, tuple[str, str]]:
    """Constraint name to the ``(code, detail)`` a 409 body should carry.

    Consumed by ``app.api.errors._integrity_error_handler``. Kept here
    rather than in the handler because the register is what the schema
    drift guard already checks against live ``pg_constraint`` metadata,
    so the name in this mapping is the name PostgreSQL will report or CI
    is already red.
    """
    return collect_constraint_rejections(register())


__all__ = ["DECLARING_MODULES", "constraint_rejections", "register"]
