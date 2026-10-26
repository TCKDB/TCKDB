"""D3 byte-identity across the Phase D foundation (WP-F) extraction.

WP-F moved D3's participant counting, composition and element/charge
balance out of ``consistency/kinetics.py`` into the neutral
``consistency/stoichiometry.py`` helper. D3 keeps its own reason tokens by
mapping at its call site. This module pins, for every D3 fixture scenario
the existing suites exercise plus the composition-ordering cases they do
not, two things captured at ``main`` ``0bc86f03`` *before* the extraction:

* the context hash (``AdvisoryResult.digest.context_hash``), which is what a
  stored review's currency is judged against -- a changed hash would restale
  every recorded D3 review; and
* the ordered reason sequence of every finding, which is what a curator
  reads.

The expected values are literals, not recomputed by production code, so
any change to D3's reasons, their order or its hashed inputs goes red here.

The 42 hashes were re-captured once, in versioning PR-4. The context hash folds in the
recipe version (``context_schema_version``), which was the string ``"v1"`` on main and is
the integer ``1`` now: the same recipe, spelled as a number, hashes differently. (Checked by
dumping the hashed payload on main and here: identical apart from that one value.) The
reason sequences are byte-identical to the originals (asserted when the literals were
re-captured); only the hash moved, for that one stated cause.
"""
import json

import pytest

from app.db.models.kinetics import Kinetics
from app.db.models.reaction import ReactionEntry, ReactionEntryStructureParticipant
from app.services.consistency.kinetics import compare_kinetics
from tests.services.test_phase_d_numerical import AVOGADRO, R, _reaction, _thermo


def _set(record, **values):
    for key, value in values.items():
        setattr(record, key, value)


def _base(**kwargs):
    forward, reverse, mapping = _reaction(**kwargs)
    return forward, reverse, mapping


def _field(field, value):
    forward, reverse, mapping = _reaction()
    setattr(reverse, field, value)
    return forward, reverse, mapping, [500]


def _missing_mapping():
    forward, reverse, mapping = _reaction()
    return forward, reverse, {1: mapping[1]}, [500]


def _unbalanced():
    forward, reverse, mapping = _reaction()
    forward.reaction_entry.structure_participants.pop()
    return forward, reverse, mapping, [500]


def _degeneracy(value):
    forward, reverse, mapping = _reaction()
    _set(forward, degeneracy_convention="not_applied", degeneracy=value)
    return forward, reverse, mapping, [500]


def _charge():
    molecule = _thermo(1, cp_r=2, entropy_constant=0, smiles="[H]", charge=0)
    ion = _thermo(2, cp_r=2, entropy_constant=0, smiles="[H+]", charge=1)
    entry = ReactionEntry(id=1, public_ref="re_charge")
    entry.structure_participants = [
        ReactionEntryStructureParticipant(species_entry_id=1, species_entry=molecule.species_entry,
                                          role="reactant", participant_index=1),
        ReactionEntryStructureParticipant(species_entry_id=2, species_entry=ion.species_entry,
                                          role="product", participant_index=1),
    ]
    common = {
        "reaction_entry_id": 1, "reaction_entry": entry, "model_kind": "modified_arrhenius",
        "a": 1.0, "n": 0.0, "ea_kj_mol": 0.0, "a_units": "per_s", "is_third_body": False,
        "tmin_k": 200, "tmax_k": 3000, "pressure_context": "high_p_limit",
        "degeneracy_convention": "already_applied",
    }
    forward = Kinetics(id=1, public_ref="kin_charge_f", direction="forward", **common)
    reverse = Kinetics(id=2, public_ref="kin_charge_r", direction="reverse", **common)
    return forward, reverse, {1: molecule, 2: ion}, [500]


def _pressure(value):
    forward, reverse, mapping = _reaction()
    mapping[2].reference_pressure_bar = value
    return forward, reverse, mapping, [500]


def _reordered():
    forward, reverse, mapping = _reaction()
    forward.reaction_entry.structure_participants.reverse()
    return forward, reverse, dict(reversed(list(mapping.items()))), [700, 500, 500]


def _uncertainty():
    forward, reverse, mapping = _reaction()
    _set(reverse, a_uncertainty=0.2, a_uncertainty_kind="additive")
    return forward, reverse, mapping, [500, 700]


def _smiles(**by_key):
    forward, reverse, mapping = _reaction()
    for key, smiles in by_key.items():
        mapping[int(key[1:])].species_entry.species.smiles = smiles
    return forward, reverse, mapping, [500]


def _isotope_key(**by_key):
    """Entries stored the way ``resolve_species_entry`` stores an isotopologue.

    Bare species SMILES (labels stripped on upload), labelled ``isotope_key``.
    """
    forward, reverse, mapping = _reaction()
    for key, isotope_key in by_key.items():
        mapping[int(key[1:])].species_entry.isotope_key = isotope_key
    return forward, reverse, mapping, [500]


def _third_order(units, a):
    thermos = [_thermo(1, cp_r=1, entropy_constant=0, smiles="[H]"),
               _thermo(2, cp_r=1, entropy_constant=0, smiles="[O]"),
               _thermo(3, cp_r=3, entropy_constant=0, smiles="O")]
    reaction = ReactionEntry(id=1, public_ref="re_three")
    reaction.structure_participants = [
        ReactionEntryStructureParticipant(species_entry_id=t.species_entry_id, species_entry=t.species_entry,
                                          role=role, participant_index=i)
        for t, role, i in [(thermos[0], "reactant", 1), (thermos[0], "reactant", 2),
                           (thermos[1], "reactant", 3), (thermos[2], "product", 1)]
    ]
    common = {"reaction_entry_id": 1, "reaction_entry": reaction, "model_kind": "modified_arrhenius",
              "ea_kj_mol": 0.0, "is_third_body": False, "tmin_k": 200, "tmax_k": 2000,
              "pressure_context": "high_p_limit", "degeneracy_convention": "already_applied"}
    forward = Kinetics(id=1, public_ref="kin_three_f", direction="forward", a=a, a_units=units, n=0, **common)
    reverse = Kinetics(id=2, public_ref="kin_three_r", direction="reverse",
                       a=(100000 / R)**2, a_units="per_s", n=-2, **common)
    return forward, reverse, {t.species_entry_id: t for t in thermos}, [500, 900]


def _collider():
    forward, reverse, mapping = _reaction()
    entry = forward.reaction_entry
    for role, index in [("reactant", 2), ("product", 3)]:
        entry.structure_participants.append(ReactionEntryStructureParticipant(
            species_entry_id=2, species_entry=mapping[2].species_entry, role=role, participant_index=index))
    _set(forward, a_units="m3_mol_s")
    _set(reverse, a_units="m6_mol2_s")
    return forward, reverse, mapping, [500]


def _no_products():
    forward, reverse, mapping = _reaction()
    for participant in forward.reaction_entry.structure_participants:
        participant.role = "reactant"
    return forward, reverse, mapping, [500]


def _unknown_role():
    forward, reverse, mapping = _reaction()
    forward.reaction_entry.structure_participants[0].role = "spectator"
    return forward, reverse, mapping, [500]


SCENARIOS = {
    **{
        f"baseline_p{pressure}_{units}": (lambda pressure=pressure, units=units: (
            *_base(pressure=pressure, reverse_units=units), [300, 700, 1500]))
        for pressure in (1.0, 1.01325, 2.5)
        for units in ("m3_mol_s", "cm3_mol_s", "cm3_molecule_s")
    },
    "reverse_scaled": lambda: (*_base(reverse_scale=1.5), [700]),
    "field_direction": lambda: _field("direction", "forward"),
    "field_third_body": lambda: _field("is_third_body", True),
    "field_pressure_context": lambda: _field("pressure_context", "apparent_at_pressure"),
    "field_degeneracy_convention": lambda: _field("degeneracy_convention", "unknown"),
    "field_a_units": lambda: _field("a_units", "per_s"),
    "field_tmin": lambda: _field("tmin_k", None),
    "missing_mapping": _missing_mapping,
    "unbalanced_elements": _unbalanced,
    "degeneracy_1": lambda: _degeneracy(1.0),
    "degeneracy_2": lambda: _degeneracy(2.0),
    "degeneracy_6": lambda: _degeneracy(6.0),
    "degeneracy_missing": lambda: _degeneracy(None),
    "unbalanced_charge": _charge,
    "outside_rate_domain": lambda: (*_base(), [100]),
    "incompatible_pressure": lambda: _pressure(2),
    "unrecorded_pressure": lambda: _pressure(None),
    "reordered_sets": _reordered,
    "changed_uncertainty": _uncertainty,
    "isotope_product": lambda: _smiles(k2="[2H]"),
    "isotope_reactant": lambda: _smiles(k1="[2H][2H]"),
    "unparseable_product": lambda: _smiles(k2="C(("),
    "unparseable_reactant": lambda: _smiles(k1="C(("),
    # First-failing species in participant order wins: isotope on the
    # reactant (entry 1) is reported, not the unparseable product.
    "isotope_then_unparseable": lambda: _smiles(k1="[2H][2H]", k2="C(("),
    "unparseable_then_isotope": lambda: _smiles(k1="C((", k2="[2H]"),
    "third_order_m6": lambda: _third_order("m6_mol2_s", 1.0),
    "third_order_cm6": lambda: _third_order("cm6_mol2_s", 1e12),
    "third_order_molecule": lambda: _third_order("cm6_molecule2_s", 1e12 / AVOGADRO**2),
    "inferred_collider": _collider,
    "no_products": _no_products,
    "unknown_role": _unknown_role,
    # Added with the D3 isotope fix (after the extraction): the two scenarios
    # whose reasons deliberately changed. See ISOTOPE_KEY_BEFORE_FIX below.
    "isotope_key_product": lambda: _isotope_key(k2="[2H]"),
    "isotope_key_reactant": lambda: _isotope_key(k1="[2H][2H]"),
}


def fingerprint(result):
    reasons = []
    for f in result.findings:
        payload = json.loads(f.message)
        reasons.append(payload["reason"] if "reason" in payload else "-")
    return result.digest.context_hash, reasons


def _run(name):
    forward, reverse, mapping, grid = SCENARIOS[name]()
    return fingerprint(compare_kinetics(forward, reverse, mapping, temperature_grid=grid))


# Captured at main 0bc86f03, before the stoichiometry extraction; the two
# isotope_key_* entries were added with the D3 isotope fix and pin its result.
EXPECTED = {
    "baseline_p1.01325_cm3_mol_s": (
        "1a9fb79fb0d18e75aa449ef21ef2c67a32bf78732e53d3f43e66e72c70c55350",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p1.01325_cm3_molecule_s": (
        "22aa0291b7487732e4b5feeb959aeece8592b5441babe3459d263bd6ffc5bff4",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p1.01325_m3_mol_s": (
        "68a6e3edfd26878f07dd05a39381af8e4088314938605da219e943c7ad0a258d",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p1.0_cm3_mol_s": (
        "0ca26bcfd0bc9948618493bf3ff40414cbc8a0aa3058ff57b9c48ec43dc7a4bf",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p1.0_cm3_molecule_s": (
        "f42f0f10d9bfa0a8030f768697d83e49a41a75fe06a1db19ef96b8a0cf8be072",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p1.0_m3_mol_s": (
        "570add9d59fddf53e2de533e87ec35a33413d4ab7d1f68a5db55ec2f4a6acb12",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p2.5_cm3_mol_s": (
        "5376551d50e5464f4b12f1bb0e094421ebef5b4c8094b2d191154e0cf634dace",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p2.5_cm3_molecule_s": (
        "4f81d2f1954099aa05d8da05d0ba8cb177995d0207b5ac25e8367e078686cfbd",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p2.5_m3_mol_s": (
        "eeca3c48fec49579992aeb9c56f7f43ee5ce224a5a6e6c27573c847ec4af4e6e",
        ["-", "-", "-", "-", None, None, None],
    ),
    "changed_uncertainty": (
        "d8779532a5b492fda3af6bdeec22c4e7e56d7e6c3908b5ab0dfa698dea5954db",
        ["-", "-", "-", "-", None, None],
    ),
    "degeneracy_1": (
        "77dacd1768ad55dbe5e78dcc591cbf254dd049d2c95ebd010a7d73ac59905165",
        ["-", "-", "-", "-", None],
    ),
    "degeneracy_2": (
        "cdd286df602c387081a95b6078cf89567a631c94fa871ccae2be4de215e6ebc4",
        ["-", "-", "-", "-", None],
    ),
    "degeneracy_6": (
        "d868b468cf098fcd27174391a629dfa94be89c23ef71239d310c094e29c3a379",
        ["-", "-", "-", "-", None],
    ),
    "degeneracy_missing": (
        "9325ef8faf1386ccef445a569c761df7398b238fc42a2d61aa0e5b7c1554914d",
        ["-", "-", "-", "-", "missing_or_invalid_degeneracy"],
    ),
    "field_a_units": (
        "7b7f91a5a2c89c9155eacd85215030511a0cc65d64d2d4ebec2b11650c6d5928",
        ["-", "-", "-", "-", "incompatible_or_missing_rate_units"],
    ),
    "field_degeneracy_convention": (
        "b2e56723ea3b6c4f9f3e37c5dcbb7434a1eb8ad4af94b0ab9fd68a5dfe330534",
        ["-", "-", "-", "-", "ambiguous_degeneracy_convention"],
    ),
    "field_direction": (
        "c2e9674cffd35245d62c0be220e6a02460d86c1acd8e0f7ef60d21388c7a1eb0",
        ["-", "-", "-", "-", "explicit_opposite_directions_required"],
    ),
    "field_pressure_context": (
        "7e8ee074db8df5fb207ce9713daaf1379e8030aef03b821d2cf9cf802aa23226",
        ["-", "-", "-", "-", "non_elementary_or_ambiguous_effective_rate"],
    ),
    "field_third_body": (
        "ccd66e3031058e07a96fa6c38248249306dfae60061362cf00bd0bb829c867ca",
        ["-", "-", "-", "-", "non_elementary_or_ambiguous_effective_rate"],
    ),
    "field_tmin": (
        "19e78ee344205022d2452f4723fb43acd67afc2208163e992a3caf92feb6b9d6",
        ["-", "-", "-", "-", "missing_or_invalid_rate_domain"],
    ),
    "incompatible_pressure": (
        "70ee964d24b2bd38e2ae7e1a56441480d7dc549e692e9f3f62cf9139a4b532a4",
        ["-", "-", "-", "-", "incompatible_reference_pressures"],
    ),
    "inferred_collider": (
        "8888955f94ad016bc6daff313c6fb0ebad5f4744c6fb85ff725c5594f273053a",
        ["-", "-", "-", "-", "inferred_third_body_unsupported"],
    ),
    "isotope_product": (
        "f44fe59b1f9d6753d32fcb57a59bc825d191721e7909f601d7186c94dee76aa3",
        ["-", "-", "-", "-", "isotope_specific_equilibrium_unsupported"],
    ),
    "isotope_reactant": (
        "4ed7311e8c61f6b98633820fbbb3ecdbcbabe6496db457f1a8c4413510335d7d",
        ["-", "-", "-", "-", "isotope_specific_equilibrium_unsupported"],
    ),
    "isotope_then_unparseable": (
        "f0cf5c60afdf21b5220f37718b3d5986d39e881cf617890f58f16f2cb9538558",
        ["-", "-", "-", "-", "isotope_specific_equilibrium_unsupported"],
    ),
    "missing_mapping": (
        "8f86cbbbccfd138e6e84bb243d5567712c44ee5e26b86958967b3a0ba106be02",
        ["-", "-", "-", "incomplete_or_incompatible_thermo_mapping"],
    ),
    "no_products": (
        "835b681551a0f27dad64ad2294b3e5c795644a3fb02ef2bc999d8f69e068c1a9",
        ["-", "-", "-", "-", "missing_or_unsupported_participants"],
    ),
    "outside_rate_domain": (
        "993a171160bf64748fe3e8d84e7194103391d342fcf04f3956848852990d4c33",
        ["-", "-", "-", "-", "temperature_outside_rate_domain"],
    ),
    "reordered_sets": (
        "a0a4081b85b90191b34b17ea458066c9565455290bd2ad5b2c4f0200799b3cc5",
        ["-", "-", "-", "-", None, None],
    ),
    "reverse_scaled": (
        "0d7051d7b29aa0712045bea24b1e90153d6434f46cbb91d9a55928e0c61eb5b0",
        ["-", "-", "-", "-", None],
    ),
    "third_order_cm6": (
        "7d6630bc3a40a874f41e89c4a0dbd3cde4a82538ca80eab132804b24deb14df5",
        ["-", "-", "-", "-", "-", None, None],
    ),
    "third_order_m6": (
        "6d558a8f63c514a9376213a69b4c6957c73d611495444dd6a2a9a0fc9cc58670",
        ["-", "-", "-", "-", "-", None, None],
    ),
    "third_order_molecule": (
        "1e92c02651266b5ed406815f5b6f35c9a712927ed7f6c960b36ee3b34d9dc167",
        ["-", "-", "-", "-", "-", None, None],
    ),
    "unbalanced_charge": (
        "b38bb7fe388504abaf93d0e348c8cc968a419140916951080fbf4216290d85b2",
        ["-", "-", "-", "-", "unbalanced_stoichiometry"],
    ),
    "unbalanced_elements": (
        "64a6c2873b3ec173e53863dea2b484b241ff2dda8bba23dce1b44fad0f9e6272",
        ["-", "-", "-", "-", "unbalanced_stoichiometry"],
    ),
    "unknown_role": (
        "8b403047f039e49fa4c6299b1e8eedc22de0617bb73a0797bff6fb6f05496f31",
        ["-", "-", "-", "-", "missing_or_unsupported_participants"],
    ),
    "unparseable_product": (
        "39365a150771da2c049e9228e080affc7669cadaf13aaed1da77bbc2b96257e2",
        ["-", "-", "-", "-", "unusable_species_composition"],
    ),
    "unparseable_reactant": (
        "473fd6f0d341ae58b0f2bccf3fb6c2a076bd69dc5f1edc73915ac72b2a3f8418",
        ["-", "-", "-", "-", "unusable_species_composition"],
    ),
    "unparseable_then_isotope": (
        "6053964cf0b292bc3e4865992d65aa941399ab13e8259a3fe131551fce0940c3",
        ["-", "-", "-", "-", "unusable_species_composition"],
    ),
    "unrecorded_pressure": (
        "00cede8a285f4b16ea2421571c9ad1c360b08faa9dad2be3a0a2dc4e8ade15e7",
        ["-", "-", "-", "-", "missing_or_invalid_reference_pressure"],
    ),
    "isotope_key_product": (
        "4797ab14ad3ec1f0615f68613623c47f490f7d564d4af2b0a908bd3c8597f609",
        ["-", "-", "-", "-", "isotope_specific_equilibrium_unsupported"],
    ),
    "isotope_key_reactant": (
        "32eedd326a1b5b6b1dc30d3a2809e3928a1efe6a318cd35b3cc6e779b37bfb33",
        ["-", "-", "-", "-", "isotope_specific_equilibrium_unsupported"],
    ),
}

#: The deliberate D3 behaviour change (isotope content read from the
#: species entry). Before it, these two scenarios were evaluated as if the
#: participants were the natural-abundance species. The context hash is the
#: same before and after (``isotope_key`` was already a hashed input); only
#: the reasons changed. The other 40 pins are unchanged by the fix.
ISOTOPE_KEY_BEFORE_FIX = {
    "isotope_key_product": (
        "4797ab14ad3ec1f0615f68613623c47f490f7d564d4af2b0a908bd3c8597f609",
        ["-", "-", "-", "-", None],
    ),
    "isotope_key_reactant": (
        "32eedd326a1b5b6b1dc30d3a2809e3928a1efe6a318cd35b3cc6e779b37bfb33",
        ["-", "-", "-", "-", None],
    ),
}


def test_every_scenario_is_pinned():
    assert len(SCENARIOS) == 42
    assert set(EXPECTED) == set(SCENARIOS)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_d3_hash_and_reasons_are_byte_identical_to_pre_extraction(name):
    context_hash, reasons = _run(name)
    expected_hash, expected_reasons = EXPECTED[name]
    assert context_hash == expected_hash
    assert reasons == expected_reasons


def test_the_isotope_fix_changed_only_the_reasons_of_its_two_scenarios():
    assert len(ISOTOPE_KEY_BEFORE_FIX) == 2
    for name, (before_hash, before_reasons) in ISOTOPE_KEY_BEFORE_FIX.items():
        after_hash, after_reasons = EXPECTED[name]
        assert before_hash == after_hash
        assert [r for r in before_reasons if r != "-"] == [None]
        assert [r for r in after_reasons if r != "-"] == ["isotope_specific_equilibrium_unsupported"]
