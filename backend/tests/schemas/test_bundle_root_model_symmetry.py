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


def test_kinetics_and_transport_have_no_second_spelling():
    """Recorded because "cover those too" needs an answer either way.

    Kinetics exists only on the reaction root — a species bundle has no
    reaction to have a rate for — and transport has no bundle model on
    either root; it is a standalone route only. Neither is a pair, so
    neither can drift. This asserts that rather than leaving the reader
    to infer it from MODEL_PAIRS, and it will fail the day a second
    spelling is introduced, which is the day it would need adding above.
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
