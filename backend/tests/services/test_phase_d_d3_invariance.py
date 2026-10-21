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

The 42 hashes were re-captured once, in versioning PR-4: the context hash folds in
the machine-review recipe version (``MACHINE_REVIEW_CONTEXT_SCHEMA_VERSION``), which
moved 1 -> 2 on purpose so every review stored before the reset reads stale by name.
The reason sequences are byte-identical to the originals (asserted when the literals
were re-captured); only the hash moved, for that one stated cause.
Cantera floats are deliberately not pinned (they are already checked
analytically, with tolerances, by ``test_phase_d_numerical.py``).
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
        "e102b0be5fb5e95a333993294722dcea2e53bf24bfed23c11fb0a29ed41e5e7e",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p1.01325_cm3_molecule_s": (
        "d4b51e57cc818a039570cfb058b2cf5657751338aeed8f8060376e9182d71d5d",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p1.01325_m3_mol_s": (
        "397bf712ab4ff925ad1143b36f3c72dc52c228629c98db270d83fda92eed1f58",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p1.0_cm3_mol_s": (
        "9bda46f9fefcdd7d815ed03aef6539dcdccaf8cb124d4a4190131d50a025c91d",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p1.0_cm3_molecule_s": (
        "4999f5abbd8b87b501cdb412361d8b4393c387e36bfb4e142a95c316d9aa00e3",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p1.0_m3_mol_s": (
        "69e5c609250aa0c344807f2a7d036cd04e86d44be96459d6ad8ed9327bbf2259",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p2.5_cm3_mol_s": (
        "6bcfd475bab9a80e099c60429502fc439eaed58689d16607c4c1c5822a7d4cda",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p2.5_cm3_molecule_s": (
        "d46910de794ec0fe3048313e23d23c35d91eb677746c35d70d87b429a086ca87",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p2.5_m3_mol_s": (
        "4c5ecc3bef3074656ed0a0789d70914d78b04ab67de81954dd291d9c13d481b0",
        ["-", "-", "-", "-", None, None, None],
    ),
    "changed_uncertainty": (
        "9d2f5ca303659454a91bf2c15d73f5ac981121d9eec1a215e28b97c5afbc382c",
        ["-", "-", "-", "-", None, None],
    ),
    "degeneracy_1": (
        "e795a3a3c4dd4014bef1135ec1d1ecd04d9c0039fae893e2e80f4c60c91fd3b8",
        ["-", "-", "-", "-", None],
    ),
    "degeneracy_2": (
        "c2326231c65be0c2b574dc2d8cfa38871287992839e1904caf57d0bea9cd6719",
        ["-", "-", "-", "-", None],
    ),
    "degeneracy_6": (
        "6596172c0e1bbef9ca892e3a6bb8c2ec6d9c54f67a9c8295e5dcea03f62cae1c",
        ["-", "-", "-", "-", None],
    ),
    "degeneracy_missing": (
        "e9b1349bc9aa0ba1f20f5c8596bb7a6d6923d063de714f68052509f10ab2f45d",
        ["-", "-", "-", "-", "missing_or_invalid_degeneracy"],
    ),
    "field_a_units": (
        "97194417cf5baab743b21d95abbcc671933148247a2be6cd6b963054fc3f0cc9",
        ["-", "-", "-", "-", "incompatible_or_missing_rate_units"],
    ),
    "field_degeneracy_convention": (
        "b8518602d05844e4acd0ad85a7f73e4f7ad7583590467628488d971ca026148f",
        ["-", "-", "-", "-", "ambiguous_degeneracy_convention"],
    ),
    "field_direction": (
        "3b23e1dded95bbccafff57b39322a61aa7fbde0fff058466af360bdd20df14df",
        ["-", "-", "-", "-", "explicit_opposite_directions_required"],
    ),
    "field_pressure_context": (
        "d382a2363faded5faf89f4f155117bf99c579e082bacbe03fa717667ab63d4f2",
        ["-", "-", "-", "-", "non_elementary_or_ambiguous_effective_rate"],
    ),
    "field_third_body": (
        "410ff1030fc2d84e3b03a1ff967a86c0719fefcedee7ecb657719bcdf7bde309",
        ["-", "-", "-", "-", "non_elementary_or_ambiguous_effective_rate"],
    ),
    "field_tmin": (
        "8b7c55252f3aaf025a18dd4cda08e72b933fbc685ad63ed8ced2dbbf83e30a48",
        ["-", "-", "-", "-", "missing_or_invalid_rate_domain"],
    ),
    "incompatible_pressure": (
        "61c5c590f9f9c29906c07437705f883827ad2a08e96e48c74e25318c6631a2fb",
        ["-", "-", "-", "-", "incompatible_reference_pressures"],
    ),
    "inferred_collider": (
        "1732d7d795ed3992c8841a0da1caf53db2e6cda4dcd320f8a6c340b7f091928a",
        ["-", "-", "-", "-", "inferred_third_body_unsupported"],
    ),
    "isotope_product": (
        "bd78d01d77fda06061fa797e05e998082269923cd7bb3c538fefe2c82053c214",
        ["-", "-", "-", "-", "isotope_specific_equilibrium_unsupported"],
    ),
    "isotope_reactant": (
        "42e4e7546153e973b5607f5432bfb9117033663551e9b06b952cbabce3dea688",
        ["-", "-", "-", "-", "isotope_specific_equilibrium_unsupported"],
    ),
    "isotope_then_unparseable": (
        "c08d3cadc0e98567a4d11ad152d57a70387ebdb3a711dca67fa622c1902c1401",
        ["-", "-", "-", "-", "isotope_specific_equilibrium_unsupported"],
    ),
    "missing_mapping": (
        "9a7dc8abf36ea46a89f470c169c1b1c363a71c7049c4f6a8f968f82fd088b3ee",
        ["-", "-", "-", "incomplete_or_incompatible_thermo_mapping"],
    ),
    "no_products": (
        "f62eb581801b517ba378a7ee49f762f687ea9fc4bd83654256ef8ed3fbc901d5",
        ["-", "-", "-", "-", "missing_or_unsupported_participants"],
    ),
    "outside_rate_domain": (
        "c6f2de0d9ced5646ed412b2f22838f1763f25cc62e66e2a645aee1ebe85f5757",
        ["-", "-", "-", "-", "temperature_outside_rate_domain"],
    ),
    "reordered_sets": (
        "c520de81a540923f4d9130840ecfeecded3dd88e1c42108a057a365480aa5e2a",
        ["-", "-", "-", "-", None, None],
    ),
    "reverse_scaled": (
        "d3e09b9da2c0f13a9f2623c2955561505bb4a66097176103e05e8ddb5b2c41e4",
        ["-", "-", "-", "-", None],
    ),
    "third_order_cm6": (
        "d769467476bb9416aacfc794efdb0ccdd6a0d606e295c3dd856d5f8e9f7e8376",
        ["-", "-", "-", "-", "-", None, None],
    ),
    "third_order_m6": (
        "e296ef227aa1e25e58c4d2e41742bc9e4c9c8711fe0309c1b80efa79b0cbe655",
        ["-", "-", "-", "-", "-", None, None],
    ),
    "third_order_molecule": (
        "93235e765844d430f6954fff058c4278b025392258ddf60a40e50249f43053e3",
        ["-", "-", "-", "-", "-", None, None],
    ),
    "unbalanced_charge": (
        "7fc6caad2a15ef37edbdff512d338a44c6b5e21f33b64f00d1e38bdccddc8709",
        ["-", "-", "-", "-", "unbalanced_stoichiometry"],
    ),
    "unbalanced_elements": (
        "70d2215f5c6a02d1bfd593ad3d7eb270d5fcbfa150d2331845d0890f89fa12d8",
        ["-", "-", "-", "-", "unbalanced_stoichiometry"],
    ),
    "unknown_role": (
        "1499ddbc907eb6101339b7584ac70506f9a241435c3976b998c6a50ff219c042",
        ["-", "-", "-", "-", "missing_or_unsupported_participants"],
    ),
    "unparseable_product": (
        "cb0759d3c357fd50acb94321d97a2fe232ea06fd724a9f619a5a66e3c2524f26",
        ["-", "-", "-", "-", "unusable_species_composition"],
    ),
    "unparseable_reactant": (
        "250c4c3d58a56a2746b96bb4e35aea99f438756233deab16a35ffa01d2215322",
        ["-", "-", "-", "-", "unusable_species_composition"],
    ),
    "unparseable_then_isotope": (
        "03c2e572d464a84f653f3e3203821aa692ed9f26b4460bbab2685140be31fd2f",
        ["-", "-", "-", "-", "unusable_species_composition"],
    ),
    "unrecorded_pressure": (
        "57f11b2776845378ccef773f3bd54c450af778a311de939a3609bba5b58a6a2d",
        ["-", "-", "-", "-", "missing_or_invalid_reference_pressure"],
    ),
    "isotope_key_product": (
        "bca44797033a8c92098117af6f7562b385a90b1288728a79b2ffdac347f43bf2",
        ["-", "-", "-", "-", "isotope_specific_equilibrium_unsupported"],
    ),
    "isotope_key_reactant": (
        "9e00869d02cb834f6c590001a0101631a6d501d04cef1ddce32ce5d2dad071e9",
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
        "bca44797033a8c92098117af6f7562b385a90b1288728a79b2ffdac347f43bf2",
        ["-", "-", "-", "-", None],
    ),
    "isotope_key_reactant": (
        "9e00869d02cb834f6c590001a0101631a6d501d04cef1ddce32ce5d2dad071e9",
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
