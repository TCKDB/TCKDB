"""The two bundle roots must carry the same fields, or say why not.

TCKDB has two bundle upload roots — ``ComputedSpeciesUploadRequest`` and
``ComputedReactionUploadRequest`` — and several of their nested models are
two spellings of one thing: the reaction bundle's per-species thermo and
the species bundle's thermo describe the same ``thermo`` row, resolved by
the same services, subject to the same table constraints. When one gains
a field and the other does not, a depositor's record is silently less
complete depending on which route they used, and nothing says so.

That has now happened twice, for two different reasons:

* **Congenital.** ``BundleThermoIn`` (8 fields) and ``ThermoInBundle``
  (15) were written three days apart in April 2026 and never agreed.
  Fixed in #151.
* **Regression.** ``BundleStatmechIn`` was born narrow in ``17706cf7``,
  but the three rotational constants were then added to
  ``StatmechInBundle`` **only**, by ``264519f1`` — two days after
  ``0ea9182f`` had correctly updated both models in a single commit. The
  habit was right and then it lapsed. Fixed in #142.

The second is the one this file exists for. Nothing in the tree could
distinguish a one-sided addition from a deliberate asymmetry, so the
regression was invisible at review time and stayed invisible for months.
This test makes the two field sets an assertion: they must match, and a
field that genuinely belongs to only one root has to be written into
:data:`ALLOWED_ASYMMETRIES` with a stated reason. "Remember to update
both" becomes something that fails.

The allowlist is deliberately keyed by ``(pair, field)`` and deliberately
requires prose. A bare set of names would let the next person silence a
real regression by appending to it; having to write down *why* a field
belongs to one root and not the other is the point where a mistake
becomes visible.
"""

from __future__ import annotations

import pytest
from tckdb_schemas.workflows import computed_reaction_upload as rx
from tckdb_schemas.workflows import computed_species_upload as sp


def _standalone_kinetics_model():
    """``KineticsUploadRequest``, the standalone route's kinetics shape.

    Imported inside a function because it lives backend-side, not in the
    wire package: the standalone kinetics schema was never extracted when
    the bundle roots were (``9fde2742``). That split is itself part of why
    the two drifted — the models do not sit next to each other, and
    nothing imported both until this file did.
    """
    from app.schemas.workflows.kinetics_upload import KineticsUploadRequest

    return KineticsUploadRequest

# ---------------------------------------------------------------------------
# The pairs
# ---------------------------------------------------------------------------

#: ``(name, species-root model, reaction-root model)``.
#:
#: Only models that describe *the same database row* belong here. The two
#: roots also share ``StatmechSourceCalcIn`` and ``ThermoSourceCalcInBundle``
#: as literally the same class, which needs no test — there is no second
#: definition to drift from.
MODEL_PAIRS = [
    ("thermo", sp.ThermoInBundle, rx.BundleThermoIn),
    ("statmech", sp.StatmechInBundle, rx.BundleStatmechIn),
    ("statmech_torsion", sp.StatmechTorsionInBundle, rx.BundleStatmechTorsionIn),
    ("conformer", sp.ConformerInBundle, rx.ConformerIn),
    ("calculation", sp.CalculationInBundle, rx.ComputedReactionCalculationIn),
]

#: ``(pair_name, field_name) -> reason``. Every entry is a claim that the
#: asymmetry is deliberate, and the reason is the evidence for that claim.
ALLOWED_ASYMMETRIES: dict[tuple[str, str], str] = {
    # --- thermo ---------------------------------------------------------
    (
        "thermo",
        "applied_energy_corrections",
    ): (
        "Species-root only, by #151's deliberate choice. The reaction "
        "bundle already declares applied corrections one level up, on "
        "BundleSpeciesIn, against the same resolved species entry. Adding "
        "a second place to say it would let one deposit make the claim "
        "twice and require inventing a rule for which one counts."
    ),
    # --- conformer ------------------------------------------------------
    (
        "conformer",
        "primary_calculation",
    ): (
        "Species-root only. The species bundle's conformer distinguishes "
        "the calculation that defines the conformer from the ones merely "
        "run on it; the reaction bundle's conformer carries exactly one "
        "('calculation') and hangs the rest off BundleSpeciesIn."
        "calculations, which is a different decomposition of the same "
        "data rather than a missing field."
    ),
    (
        "conformer",
        "additional_calculations",
    ): (
        "Species-root only, the other half of the same decomposition as "
        "primary_calculation above."
    ),
    (
        "conformer",
        "calculation",
    ): (
        "Reaction-root only. The singular counterpart of the species "
        "root's primary_calculation/additional_calculations split."
    ),
    (
        "conformer",
        "scientific_origin",
    ): (
        "Reaction-root only. The species bundle takes one species and "
        "carries origin on the products (thermo/statmech) rather than on "
        "the conformer. Not a gap this test should force closed: moving "
        "it would change what the species route means, not just what it "
        "can carry."
    ),
    # --- calculation ----------------------------------------------------
    #
    # The two calculation models diverge structurally, not by omission:
    # the species root nests typed result payloads, the reaction root
    # flattens the same numbers onto the calculation. Both write the same
    # calc_*_result rows. Listed field-by-field rather than exempting the
    # pair wholesale, so a genuinely new one-sided field is still caught.
    (
        "calculation",
        "sp_result",
    ): "Species-root nested payload; reaction root flattens to sp_electronic_energy_hartree.",
    (
        "calculation",
        "opt_result",
    ): "Species-root nested payload; reaction root flattens to opt_converged/opt_n_steps/opt_final_energy_hartree.",
    (
        "calculation",
        "freq_result",
    ): "Species-root nested payload; reaction root flattens to the freq_* fields.",
    (
        "calculation",
        "sp_electronic_energy_hartree",
    ): (
        "Reaction-root flattening of the species root's sp_result payload; "
        "both write the same calc_sp_result row."
    ),
    (
        "calculation",
        "opt_converged",
    ): (
        "Reaction-root flattening of the species root's opt_result payload; "
        "both write the same calc_opt_result row."
    ),
    (
        "calculation",
        "opt_n_steps",
    ): (
        "Reaction-root flattening of the species root's opt_result payload; "
        "both write the same calc_opt_result row."
    ),
    (
        "calculation",
        "opt_final_energy_hartree",
    ): (
        "Reaction-root flattening of the species root's opt_result payload; "
        "both write the same calc_opt_result row."
    ),
    (
        "calculation",
        "freq_n_imag",
    ): "Reaction-root flattening of freq_result.",
    (
        "calculation",
        "freq_zpe_hartree",
    ): "Reaction-root flattening of freq_result.",
    (
        "calculation",
        "freq_frequencies_cm1",
    ): "Reaction-root flattening of freq_result.",
    (
        "calculation",
        "freq_imag_freq_cm1",
    ): "Reaction-root flattening of freq_result.",
    (
        "calculation",
        "freq_imaginary_dispositions",
    ): "Reaction-root flattening of freq_result.",
    (
        "calculation",
        "freq_reaction_coordinate_mode_index",
    ): (
        "Reaction-root only, and correctly so: it names which normal mode "
        "is the reaction coordinate, which is meaningless without a "
        "reaction."
    ),
    (
        "calculation",
        "geometry_key",
    ): (
        "Reaction-root only. The reaction bundle shares geometries across "
        "species by key; the species bundle has one species and attaches "
        "geometries to conformers directly."
    ),
    (
        "calculation",
        "conformer_key",
    ): (
        "Reaction-root only, for the same structural reason as geometry_key "
        "directly above, and verified rather than assumed. conformer_key "
        "names the conformer observation a calculation is evidence for. The "
        "species bundle has no key to name: its calculations exist only as "
        "ConformerInBundle.primary_calculation and .additional_calculations, "
        "lexically nested inside the conformer they belong to, and "
        "app/workflows/computed_species.py sets conformer_observation_id "
        "from the enclosing observation at both of its two persist sites, "
        "with no condition on any field. There is no third path. A "
        "calculation deposited through the species root "
        "therefore cannot be unanchored, which is exactly the failure "
        "conformer_key exists to prevent on the reaction root, where "
        "calculations sit in a flat per-species list and the link has to be "
        "stated. Adding the field here would create one that can only ever "
        "be ignored -- or, worse, disagree with the nesting that already "
        "decides the answer -- which is the same 'a field that looks like "
        "it does something and does not' defect this asymmetry's own PR was "
        "written to remove."
    ),
    # ``literature`` / ``literature_id`` were both listed here until the
    # reaction root's raw FK was replaced by the same inline fragment the
    # species root always took. Both roots now spell the citation
    # ``literature``, so there is no asymmetry left to exempt — and
    # ``test_the_allowlist_describes_asymmetries_that_still_exist`` would
    # fail if these entries were left behind.
}


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pair_name", "species_model", "reaction_model"),
    MODEL_PAIRS,
    ids=[p[0] for p in MODEL_PAIRS],
)
def test_bundle_root_models_carry_the_same_fields(
    pair_name, species_model, reaction_model
):
    """Neither root may gain a field the other lacks without a reason.

    The failure message prints the offending names and the exact
    allowlist key to add, because the correct response to this test going
    red is usually "widen the other model", and occasionally "record why
    not" — and the difference should be a decision, not a guess.
    """
    species_only = set(species_model.model_fields) - set(reaction_model.model_fields)
    reaction_only = set(reaction_model.model_fields) - set(species_model.model_fields)

    unexplained = sorted(
        name
        for name in species_only | reaction_only
        if (pair_name, name) not in ALLOWED_ASYMMETRIES
    )

    assert not unexplained, (
        f"{species_model.__name__} and {reaction_model.__name__} describe the "
        f"same row but disagree on {len(unexplained)} field(s): {unexplained}.\n"
        f"  only on {species_model.__name__}: {sorted(species_only)}\n"
        f"  only on {reaction_model.__name__}: {sorted(reaction_only)}\n"
        "Either widen the model that is missing them, or add "
        f'ALLOWED_ASYMMETRIES[("{pair_name}", "<field>")] = "<why>" '
        "in this file."
    )


def test_the_allowlist_describes_asymmetries_that_still_exist():
    """A stale exemption is a hole, so the allowlist is checked both ways.

    Without this, closing an asymmetry would leave its entry behind, and
    the leftover entry would silently exempt the field if it were ever
    removed from one root again. It also keeps the reasons honest: an
    entry that no longer corresponds to anything cannot be reviewed.
    """
    live: set[tuple[str, str]] = set()
    for pair_name, species_model, reaction_model in MODEL_PAIRS:
        species_fields = set(species_model.model_fields)
        reaction_fields = set(reaction_model.model_fields)
        for name in species_fields ^ reaction_fields:
            live.add((pair_name, name))

    stale = sorted(set(ALLOWED_ASYMMETRIES) - live)
    assert not stale, (
        f"{len(stale)} allowlist entr(y/ies) name an asymmetry that no longer "
        f"exists and should be deleted: {stale}"
    )


def test_every_allowlist_entry_states_a_reason():
    """An exemption with no reason is the failure mode this file guards.

    Cheap to assert, and it is the difference between an allowlist that
    documents decisions and one that accumulates silence.
    """
    unreasoned = sorted(
        key for key, reason in ALLOWED_ASYMMETRIES.items() if len(reason.strip()) < 40
    )
    assert not unreasoned, (
        f"These allowlist entries have no substantive reason: {unreasoned}"
    )


def test_the_statmech_pair_needs_no_exemptions():
    """#142's headline claim, asserted directly rather than by absence.

    ``test_bundle_root_models_carry_the_same_fields`` would also pass if
    somebody added six allowlist entries instead of six fields, so the
    claim that statmech is now *actually* symmetric is worth its own
    assertion. The six fields named here are precisely the ones
    ``BundleStatmechIn`` could not carry before #142.
    """
    statmech_exemptions = {
        field for (pair, field) in ALLOWED_ASYMMETRIES if pair == "statmech"
    }
    assert statmech_exemptions == set(), (
        "statmech should be symmetric with no exemptions; found "
        f"{sorted(statmech_exemptions)}"
    )

    reaction_fields = set(rx.BundleStatmechIn.model_fields)
    for field in (
        "literature",
        "software_release",
        "workflow_tool_release",
        "rotational_constant_a_cm1",
        "rotational_constant_b_cm1",
        "rotational_constant_c_cm1",
    ):
        assert field in reaction_fields, (
            f"BundleStatmechIn lost '{field}', which #142 added"
        )


def test_kinetics_and_transport_have_no_second_spelling_across_the_two_roots():
    """Recorded because "cover those too" needs an answer either way.

    Kinetics exists only on the reaction root — a species bundle has no
    reaction to have a rate for — and transport has no bundle model on
    either root; it is a standalone route only.

    This used to conclude "neither is a pair, so neither can drift". The
    first half is right and the second half was wrong, and the error was
    load-bearing: kinetics has no second spelling *across the two bundle
    roots*, but it does have one across the **bundle and the standalone
    route**, and that is where it drifted. See
    :func:`test_bundle_kinetics_records_the_same_science_as_the_standalone_route`.
    """
    assert not hasattr(sp, "KineticsInBundle"), (
        "A species-root kinetics model now exists and should be added to "
        "MODEL_PAIRS alongside BundleKineticsIn."
    )
    assert hasattr(rx, "BundleKineticsIn"), "reaction root lost its kinetics model"

    for module, label in ((sp, "species"), (rx, "reaction")):
        transport = [n for n in dir(module) if "Transport" in n]
        assert not transport, (
            f"the {label} bundle root gained transport model(s) {transport}; "
            "if both roots now have one, add the pair to MODEL_PAIRS."
        )


#: Scientific-content fields ``BundleKineticsIn`` lacks that
#: ``KineticsUploadRequest`` has, each with the evidence for whether the
#: asymmetry is deliberate. Unlike :data:`ALLOWED_ASYMMETRIES`, an entry
#: here is **not** a claim that the gap is correct — two of the three are
#: recorded drift. It is a claim that the gap is *known*, so that a fourth
#: appearing is visible on the day it lands rather than months later.
KNOWN_BUNDLE_KINETICS_GAPS: dict[str, str] = {
    "interpretation_assignments": (
        "DRIFT, not design. Added to KineticsUploadRequest by ee7377f5 (#66), "
        "a commit that edited computed_reaction_upload.py in the same diff to "
        "close the analogous transition-state evidence gap on parity grounds, "
        "and left kinetics one-sided with no recorded reason. Closing it needs "
        "a bundle-local-key assignment model plus persistence in "
        "app.workflows.computed_reaction — a feature, not a contract change."
    ),
    "tunneling_application": (
        "DRIFT, not design. Same commit, same omission, and the sharper half: "
        "BundleKineticsIn carries tunneling_model, the label, so a bundle can "
        "claim Eckart tunneling and never attach the evidence. The standalone "
        "route cross-checks label against evidence "
        "(validate_tunneling_declaration_agrees); the bundle has nothing to "
        "check against."
    ),
    "network_kinetics_ref": (
        "UNADDRESSED rather than decided. 2fb5c25b (#29) established that this "
        "model carries only scalar Arrhenius fields and its workflow writes no "
        "kinetics child tables, directing PLOG/Chebyshev to the single-reaction "
        "endpoint. That reasoning covers child rows; network_kinetics_ref is a "
        "nullable scalar column on kinetics itself, and BundleKineticsIn "
        "accepts pressure_context='pressure_dependent' — the exact state the "
        "handle names — with no way to name it."
    ),
}

#: Fields on ``KineticsUploadRequest`` the bundle deliberately spells
#: elsewhere or deliberately refuses. Excluded from the comparison rather
#: than listed as gaps, because for each of these the reason is on record.
_STANDALONE_ONLY_BY_DESIGN = frozenset(
    {
        # Identity. The bundle declares one reaction at its root and names
        # participants by local key; ``direction`` is expressed by which
        # keys land in ``reactant_keys`` vs ``product_keys``, which
        # BundleKineticsIn's own docstring states.
        "reaction",
        "direction",
        # Provenance. The reaction workflow writes ``request.literature``,
        # ``request.analysis_software_release`` and
        # ``request.workflow_tool_release`` onto every kinetics row it
        # creates, so these are supplied once at the bundle root.
        "software_release",
        "workflow_tool_release",
        "literature",
        # A resolution hint for auto-resolving source SP calculations. The
        # bundle names its source calculations by key instead, which is why
        # ``_collect_bundle_provenance_warnings`` already passes it as
        # NOT_APPLICABLE.
        "energy_level_of_theory",
        # Kinetics child tables. 2fb5c25b (#29) decided this explicitly:
        # BundleKineticsIn "carries only scalar Arrhenius fields … and its
        # workflow writes no kinetics child tables", and its
        # ``validate_model_kind`` refuses the forms that would need them,
        # directing those deposits to the single-reaction endpoint. This is
        # the one asymmetry here that is genuinely on record as a decision.
        "arrhenius_entries",
        "chebyshev",
        "falloff",
        "plog_entries",
        "third_body_efficiencies",
    }
)


def test_bundle_kinetics_records_the_same_science_as_the_standalone_route():
    """The comparison nothing in the tree was making.

    ``BundleKineticsIn`` and ``KineticsUploadRequest`` describe the same
    ``kinetics`` row. The bundle is the route the ARC adapter deposits
    through, so a field the bundle lacks is missing from the *majority* of
    deposits, not a minority.

    The gate is deliberately shaped like ``ALLOWED_ASYMMETRIES`` but reads
    the opposite way: entries in :data:`KNOWN_BUNDLE_KINETICS_GAPS` are
    recorded gaps, most of them drift. Closing one means deleting its entry;
    a *new* one means this test names it and someone has to decide.
    """
    standalone = _standalone_kinetics_model()
    bundle_fields = set(rx.BundleKineticsIn.model_fields)
    standalone_fields = set(standalone.model_fields)

    missing = standalone_fields - bundle_fields - _STANDALONE_ONLY_BY_DESIGN
    unexplained = sorted(missing - set(KNOWN_BUNDLE_KINETICS_GAPS))

    assert not unexplained, (
        f"{standalone.__name__} carries {len(unexplained)} scientific-content "
        f"field(s) BundleKineticsIn does not: {unexplained}.\n"
        "A reaction-bundle deposit therefore records less than the identical "
        "standalone deposit, and nothing tells the depositor. Either widen "
        "BundleKineticsIn (and persist it in app.workflows.computed_reaction), "
        "or add the field to KNOWN_BUNDLE_KINETICS_GAPS with the git evidence "
        "for why it belongs on only one route."
    )


def test_the_by_design_exclusions_still_name_real_fields():
    """A stale exclusion is a hole that silently swallows the next gap.

    ``_STANDALONE_ONLY_BY_DESIGN`` subtracts names before anything is
    judged, so a name that no longer exists on either model — a rename, a
    removal — would sit there forever excusing nothing, and a *new* field
    that happened to reuse the name would be excused without anyone
    deciding. Checked against the union so a field legitimately added to
    the bundle later does not fail this.
    """
    known = set(_standalone_kinetics_model().model_fields) | set(
        rx.BundleKineticsIn.model_fields
    )
    stale = sorted(_STANDALONE_ONLY_BY_DESIGN - known)
    assert not stale, (
        f"_STANDALONE_ONLY_BY_DESIGN excludes {stale}, which exists on neither "
        "kinetics model. Drop the entries."
    )


def test_the_known_gap_list_describes_gaps_that_still_exist():
    """A stale entry would hide a closed gap behind a "known" label."""
    standalone = _standalone_kinetics_model()
    bundle_fields = set(rx.BundleKineticsIn.model_fields)
    stale = sorted(
        name
        for name in KNOWN_BUNDLE_KINETICS_GAPS
        if name in bundle_fields or name not in standalone.model_fields
    )
    assert not stale, (
        f"KNOWN_BUNDLE_KINETICS_GAPS still lists {stale}, which BundleKineticsIn "
        "now has (or the standalone route no longer does). Delete the entries — "
        "leaving them turns a closed gap into a permanent excuse."
    )


def test_the_bundle_can_claim_tunneling_it_cannot_evidence():
    """Pins the concrete consequence, not just the field list.

    The field-set tests above would still pass if ``tunneling_model`` were
    also dropped from the bundle — a *consistent* model that simply says
    less. This one asserts the specific inconsistency that makes the gap
    worth fixing rather than worth tolerating: the label is accepted and
    the evidence has nowhere to go.
    """
    assert "tunneling_model" in rx.BundleKineticsIn.model_fields
    assert "tunneling_application" not in rx.BundleKineticsIn.model_fields
