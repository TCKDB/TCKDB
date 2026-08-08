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
    TS_IMAGINARY_FREQUENCY_MIN_CM1,
    W_N_IMAG_CONTRADICTS_MINIMUM,
    W_N_IMAG_HIGHER_ORDER_SADDLE,
    W_N_IMAG_SUGGESTS_TS,
    W_TS_IMAG_FREQ_TOO_SMALL,
    W_TS_N_IMAG_NOT_ONE,
    evaluate_species_entry_frequency,
    evaluate_transition_state_frequency,
)

from app.chemistry import species as chemistry_species
from app.chemistry import units as chemistry_units
from app.scientific_checks import (
    CheckTier,
    DatabaseConstraint,
    DesignPosition,
    PythonCheck,
    ScientificCheck,
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
from app.services.provenance_warnings import (
    W_NETWORK_WIDE_ENERGY_TRANSFER,
    W_REPORTED_NETWORK_SOLVE,
)

# ---------------------------------------------------------------------------
# Stationary points — the imaginary-mode spectrum is the definition
# ---------------------------------------------------------------------------

CHECK_TRANSITION_STATE_ONE_IMAGINARY_MODE = ScientificCheck(
    group="Stationary points",
    sort_key=1,
    code=W_TS_N_IMAG_NOT_ONE,
    asserts=(
        "A transition state has exactly one imaginary vibrational mode."
    ),
    tier=CheckTier.block,
    tier_rationale=(
        "Definitional — ADR 0008's worked example. The Hessian eigenvalue "
        "spectrum *is* the definition of a stationary point: zero imaginary "
        "modes is a minimum and the geometry never reached a barrier top, two "
        "or more is a higher-order saddle. Either way the record is not the "
        "first-order saddle point it declares itself to be, and no correct "
        "calculation produces it as submitted."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            evaluate_transition_state_frequency,
            note=(
                "A transition state carries no ``stationary_point_kind`` "
                "column — the entity *is* the claim — so the rule needs no "
                "kind argument. Upload schemas call "
                "``raise_for_blocking_findings`` from a ``model_validator``, "
                "so the contradiction becomes a 422 before the route body "
                "opens a submission."
            ),
        ),
    ),
    escape_hatch=(
        "Deposit no frequency evidence: ``n_imag=None`` produces no findings "
        "at all. Absence is never contradiction."
    ),
    divergence=(
        "The module calls itself 'the single owner' of these findings, and "
        "ADR 0008 requires that where one fact is checked in more than one "
        "tier the blocking tier owns it and the others cite it. "
        "``_check_ts_single_imaginary_frequency_for_ts`` in "
        "``app/services/trust/rubrics.py`` nevertheless re-derives the same "
        "physical fact independently at read time, and the trust evaluator "
        "promotes it to a hard fail. ADR 0008 names this duplication as a "
        "defect to be collapsed onto one owner; it has not been collapsed."
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

CHECK_TRANSITION_STATE_IMAGINARY_MODE_MAGNITUDE = ScientificCheck(
    group="Stationary points",
    sort_key=4,
    code=W_TS_IMAG_FREQ_TOO_SMALL,
    asserts=(
        "A transition state's single imaginary mode should exceed roughly "
        f"{TS_IMAGINARY_FREQUENCY_MIN_CM1:.0f} cm-1 in magnitude."
    ),
    tier=CheckTier.warn,
    tier_rationale=(
        "ADR 0008's worked counter-example, and the sharpest statement of the "
        "whole rule. A very soft imaginary mode is suspicious — often an "
        "under-converged geometry or a coarse integration grid — but it can be "
        "perfectly real, because flat barriers and variational transition "
        "states genuinely produce them. Magnitude is therefore a quality "
        "expectation, never a definition, and a check that could fire on a "
        "correct novel result must not block."
    ),
    adr="0008",
    enforced_by=(
        PythonCheck(
            evaluate_transition_state_frequency,
            note=(
                "The threshold is explicitly a starting point rather than a "
                "physical constant: reaction coordinates for hydrogen "
                "transfers run to thousands of cm-1, while genuinely flat "
                "barriers fall well under 100. It is also the scale that "
                "separates a van der Waals complex's soft intermolecular "
                "modes from a real reaction coordinate, and is reused for that "
                "judgement."
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
    code=None,
    asserts=(
        "An atom does not change element on the way across a reaction: carbon "
        "does not map onto nitrogen."
    ),
    tier=CheckTier.block,
    tier_rationale=(
        "Definitional. A map asserting that an element transmutes is a record "
        "that cannot be what it says it is, not an unusual result."
    ),
    adr="0011, 0008",
    emitted=False,
    enforced_by=(
        PythonCheck(wire_atom_map.validate_reaction_atom_map, note=_ATOM_MAP_DUAL_ENFORCEMENT),
        DatabaseConstraint(
            table="reaction_atom_map_pair",
            name="ck_reaction_atom_map_pair_element_matches",
            kind="check",
            definition="upper(element) = upper(ts_element)",
        ),
    ),
    escape_hatch=(
        "Case is not load-bearing. The comparison is deliberately "
        "case-insensitive because the two ends quote two different "
        "geometries, and a geometry stores the symbol its depositor's XYZ "
        "wrote — carbon becoming nitrogen is a contradiction, while ``Cl`` "
        "becoming ``CL`` is one program shouting where another did not. "
        "Isotope mass number is deliberately *not* carried across the same "
        "way, because a NULL disables a MATCH SIMPLE foreign key; isotope "
        "consistency is checked in the service layer instead."
    ),
)

CHECK_ATOM_MAP_IS_A_BIJECTION = ScientificCheck(
    group="Atom mapping across a reaction",
    sort_key=2,
    code=None,
    asserts=(
        "One saddle-point atom is claimed by exactly one atom of each leg, and "
        "one participant atom maps to exactly one saddle-point atom."
    ),
    tier=CheckTier.block,
    tier_rationale=(
        "Definitional. A map is a bijection or it is not a map; an atom "
        "claimed twice describes no mechanism at all."
    ),
    adr="0011, 0008",
    emitted=False,
    enforced_by=(
        PythonCheck(wire_atom_map.validate_reaction_atom_map, note=_ATOM_MAP_DUAL_ENFORCEMENT),
        DatabaseConstraint(
            table="reaction_atom_map_pair",
            name="uq_reaction_atom_map_pair_ts_atom_index",
            kind="unique",
            definition="(atom_map_id, side, ts_atom_index)",
        ),
        DatabaseConstraint(
            table="reaction_atom_map_pair",
            name="uq_reaction_atom_map_pair_atom_map_id",
            kind="unique",
            definition="(atom_map_id, structure_participant_id, atom_index)",
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
    code=None,
    asserts=(
        "Every atom index in a map is counted against a named geometry that "
        "the participant actually owns, and names an atom that geometry "
        "actually has."
    ),
    tier=CheckTier.block,
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
    emitted=False,
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
    code=None,
    asserts=(
        "When a map covers every declared participant of an atom-balanced "
        "reaction, both legs claim the same saddle-point atoms and no "
        "saddle-point atom is left unclaimed."
    ),
    tier=CheckTier.block,
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
    emitted=False,
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
    sort_key=7,
    code=None,
    asserts=(
        "An atom map records whether a human asserted it or an algorithm "
        "produced it, an inferred map names the algorithm, and neither "
        "attribution can be relabelled afterwards."
    ),
    tier=CheckTier.structural,
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
    emitted=False,
    enforced_by=(
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

CHECK_STATMECH_HAS_EXACTLY_ONE_SUBJECT = ScientificCheck(
    group="Statistical mechanics",
    sort_key=1,
    code=None,
    asserts=(
        "A partition function belongs to exactly one subject — a species entry "
        "or a transition-state entry, never both and never neither."
    ),
    tier=CheckTier.structural,
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
    emitted=False,
    enforced_by=(
        DatabaseConstraint(
            table="statmech",
            name="ck_statmech_statmech_exactly_one_subject",
            kind="check",
            definition="(species_entry_id IS NULL) <> (transition_state_entry_id IS NULL)",
        ),
    ),
    escape_hatch=None,
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


__all__ = ["DECLARING_MODULES", "register"]
