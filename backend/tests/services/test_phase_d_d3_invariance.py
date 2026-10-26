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

The 42 hashes were re-captured a second time with the entry stereo descriptor (DR-0038). The hashed inputs serialise
every column of each participant's ``species_entry``, and the entry gained ``stereo_descriptor``, ``stereo_resolution``
and ``stereo_smiles``; the descriptor is now part of what an entry *is*, so evidence that names it hashes differently.
Checked by dumping the hashed payload before and here: identical apart from those three new (null) keys. The reason
sequences are asserted unchanged by the re-capture. Stored D3 reviews read as stale after the deploy and are re-evaluated
by the ordinary currency rule.
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
        "278395abf8f3cec00aeb8a9996305ed3eab822a494b8eb585d0899fe0f18b499",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p1.01325_cm3_molecule_s": (
        "6a5a34de226be5731f1c093eb3a816c99229cf0354fb1212d61eb2f0ee916f7e",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p1.01325_m3_mol_s": (
        "c106bb3433edec72b632b33423931ca77a1fb60d11268fafe44295e0430a9858",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p1.0_cm3_mol_s": (
        "621b5e8ae37c240d82e50611585a774f96bbd27128f05a94a4f3b92828b05a2e",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p1.0_cm3_molecule_s": (
        "30cff730aa75bf6ef5b38b7e292860498f444a67b4f4d4301a5c9c38409b3ddc",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p1.0_m3_mol_s": (
        "e91d845360a72a3c0bfc3ce3a36842759de5b733da4171661644fa54ab870b00",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p2.5_cm3_mol_s": (
        "9950f64118d1ab15d095a4c4dd810c721230a200aaaed7b9e6ea3d91b2db9cc1",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p2.5_cm3_molecule_s": (
        "5f0cb106a61c87cec7b5afddc6f7e7403df9b4f8f84c6130ba8c179c30332062",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p2.5_m3_mol_s": (
        "0bf482dc11b8ad1bc98da3f593ae33cccab6206422c9b115d51500623c0d3f3c",
        ["-", "-", "-", "-", None, None, None],
    ),
    "changed_uncertainty": (
        "8c8a2190dc3a969e6bdb3c4057db4b7762f4d1cb92014d10c6f968bc72161dbb",
        ["-", "-", "-", "-", None, None],
    ),
    "degeneracy_1": (
        "dc2368a155e71eb9a2056e2f655b2a66ca9912700f7671263c3e270722bdebfb",
        ["-", "-", "-", "-", None],
    ),
    "degeneracy_2": (
        "b91617a6cea0d32337e9c0a7a2c357262322eef5046467688513598660cc1bad",
        ["-", "-", "-", "-", None],
    ),
    "degeneracy_6": (
        "da6e1aa13e428ad8b4a7096c7a0f31e6281524ad8f96610d1ed73a95ff9021ac",
        ["-", "-", "-", "-", None],
    ),
    "degeneracy_missing": (
        "628babc20c996360a19e3579e13edb9ad696e02faa3fc970eb784b5f2f068df6",
        ["-", "-", "-", "-", "missing_or_invalid_degeneracy"],
    ),
    "field_a_units": (
        "0a508c3320ff0178a44b31e309032df67cd90b8a79e19933bd2153198b2429d9",
        ["-", "-", "-", "-", "incompatible_or_missing_rate_units"],
    ),
    "field_degeneracy_convention": (
        "ee7b25bab234a5872beeefb369f034b0d4c17747558bfb70818899f1f4bfbb5e",
        ["-", "-", "-", "-", "ambiguous_degeneracy_convention"],
    ),
    "field_direction": (
        "f579052b5013985c02fc781e7960ab978e9ed6b69ea6df32977e694b23363547",
        ["-", "-", "-", "-", "explicit_opposite_directions_required"],
    ),
    "field_pressure_context": (
        "13cf4a39668711a63711933b5f2fc45bd5cb00b777d597f781341867a49958f5",
        ["-", "-", "-", "-", "non_elementary_or_ambiguous_effective_rate"],
    ),
    "field_third_body": (
        "36799a4834bcd5494ec26e2d3f4019123b264a23fef477ca173ebdbf2705594a",
        ["-", "-", "-", "-", "non_elementary_or_ambiguous_effective_rate"],
    ),
    "field_tmin": (
        "e3fb5ed9287b182563ad0be2e99e32aa7b52125c59912f9bb15ef3d5bdb505ff",
        ["-", "-", "-", "-", "missing_or_invalid_rate_domain"],
    ),
    "incompatible_pressure": (
        "2463b87fa7afb9ab3339e060a67a1a7e01c7d77563da710dbaa4fd46fc8fd8e1",
        ["-", "-", "-", "-", "incompatible_reference_pressures"],
    ),
    "inferred_collider": (
        "913e551af0fac6a9c9022cc7198d5d95d8502d89f2323b68cd65f9a76a1ac2bd",
        ["-", "-", "-", "-", "inferred_third_body_unsupported"],
    ),
    "isotope_product": (
        "3e3edd8f0c5fd994d6e4baf8814acdb89dce3cca2e19c36814a79ca3414c0ca8",
        ["-", "-", "-", "-", "isotope_specific_equilibrium_unsupported"],
    ),
    "isotope_reactant": (
        "1cea6db690f26f34d7de77358a7f747ae30593ac974f5955808814878cf9d38f",
        ["-", "-", "-", "-", "isotope_specific_equilibrium_unsupported"],
    ),
    "isotope_then_unparseable": (
        "89cb05abd037a442afe5b246b51077d3b8adb0f2556f63062a94accee1d26bf4",
        ["-", "-", "-", "-", "isotope_specific_equilibrium_unsupported"],
    ),
    "missing_mapping": (
        "6fade0a66102c9f32c68e534adb422d7ae6109bc807e89601593446a86188ea2",
        ["-", "-", "-", "incomplete_or_incompatible_thermo_mapping"],
    ),
    "no_products": (
        "c06376af6f214a6fcf36a195866cc53c83570e506dd165f4f09fabc7653cdcd4",
        ["-", "-", "-", "-", "missing_or_unsupported_participants"],
    ),
    "outside_rate_domain": (
        "0ee364090bc1a305e58d6408b1b4c08efa0ec630fad7a1e3ca4b9b29bdd79163",
        ["-", "-", "-", "-", "temperature_outside_rate_domain"],
    ),
    "reordered_sets": (
        "efe4bdf96f929adc20b898cafb76babee97a59a9380b44a7a8626ae9bbfb5c25",
        ["-", "-", "-", "-", None, None],
    ),
    "reverse_scaled": (
        "51953ed119dbb024594b387bed51311fa4574a5da595e7df8d54b04e381250a6",
        ["-", "-", "-", "-", None],
    ),
    "third_order_cm6": (
        "2262932b022d723f85dd596f2043b2e48d8aba75a41dfd4498be5b22a7d90795",
        ["-", "-", "-", "-", "-", None, None],
    ),
    "third_order_m6": (
        "45a91189110be572281c416a865bb76455c04d568e1a16b2be63259de6aaa074",
        ["-", "-", "-", "-", "-", None, None],
    ),
    "third_order_molecule": (
        "e422a341bfbe1d9825fe6d5bd95abb02e5cf75da2ac2d7b738a7ff402efe2373",
        ["-", "-", "-", "-", "-", None, None],
    ),
    "unbalanced_charge": (
        "1a1a7acfeda5fa5a546835c4a562148e6e29284ed766e84ed40d21cdc3a31b0f",
        ["-", "-", "-", "-", "unbalanced_stoichiometry"],
    ),
    "unbalanced_elements": (
        "55ebc968de7e16e391dee9af59379566c4d34fabbeef3ce47e2da5756f29e6bc",
        ["-", "-", "-", "-", "unbalanced_stoichiometry"],
    ),
    "unknown_role": (
        "4ae73c6831a134122d2e935398ef39ee611766568b118cf52ad390acd6d8ee6e",
        ["-", "-", "-", "-", "missing_or_unsupported_participants"],
    ),
    "unparseable_product": (
        "3dab552bc2e26d4e561b12f0e8fbbbded9500edb0b5f66dbb5ce2715700a0ad4",
        ["-", "-", "-", "-", "unusable_species_composition"],
    ),
    "unparseable_reactant": (
        "c6176857ca0f4ff4090c64459d2ec3a51a577a01b1f0a1100f39bcb2f794661a",
        ["-", "-", "-", "-", "unusable_species_composition"],
    ),
    "unparseable_then_isotope": (
        "0a12567cedc3959d0215f115b3bedac220104f09d92951d73f6fa59c6f00de8a",
        ["-", "-", "-", "-", "unusable_species_composition"],
    ),
    "unrecorded_pressure": (
        "23d4d93283a3376a99f4ab96f61da503b7aaffb8d350b6ecd499c2a86c92977f",
        ["-", "-", "-", "-", "missing_or_invalid_reference_pressure"],
    ),
    "isotope_key_product": (
        "fd092a27383b8b6a03e2f6186658fe0ba2a0b8125c410e8c548c16cda75ec784",
        ["-", "-", "-", "-", "isotope_specific_equilibrium_unsupported"],
    ),
    "isotope_key_reactant": (
        "641256b1809256340557874b00ecda0d7734f15e1c45e6d3dbecd1cb764142ca",
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
        "fd092a27383b8b6a03e2f6186658fe0ba2a0b8125c410e8c548c16cda75ec784",
        ["-", "-", "-", "-", None],
    ),
    "isotope_key_reactant": (
        "641256b1809256340557874b00ecda0d7734f15e1c45e6d3dbecd1cb764142ca",
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
