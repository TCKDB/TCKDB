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


# Captured at main 0bc86f03, before the stoichiometry extraction.
EXPECTED = {
    "baseline_p1.01325_cm3_mol_s": (
        "4d63c87175bded4e45570d8c95b42debb087d78bab410c4f4a1569d54cfde5cf",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p1.01325_cm3_molecule_s": (
        "ccdf3d188ca9e94951f264b3906146b6fb09869d235161f000c6ac42f3f6b1b1",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p1.01325_m3_mol_s": (
        "5cea234fb73e204b7b612fc3b629bd830f95ce571ba9e3865e7df7c6180fd539",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p1.0_cm3_mol_s": (
        "31778643d46bfa655b97de865df90e88211d2ad0a7935158a1d21a869a3dc976",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p1.0_cm3_molecule_s": (
        "e3c29f7522e42518a3982407e18d63de9facc8e7b413074d80706b4072543341",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p1.0_m3_mol_s": (
        "8c5ae050ffc919ac722cf332a6e138e218f365bfe54ad89b0ecb9e2c4bb155e6",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p2.5_cm3_mol_s": (
        "2f87984532aae7757317fb5481e9ba99e087a06061e4747e7fef4be055e84081",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p2.5_cm3_molecule_s": (
        "f42704f0939027338e4f0805d566875c4ca0cb90d53996d433c48dcf1bd46563",
        ["-", "-", "-", "-", None, None, None],
    ),
    "baseline_p2.5_m3_mol_s": (
        "d6405b6d84aabb5a553306342bfb714d196a399b8bdcfa25d5e552e684cb5a73",
        ["-", "-", "-", "-", None, None, None],
    ),
    "changed_uncertainty": (
        "6b94b53a3ebd544671c8a59a6ae1b7c790aa96dd8a0ce382caf532816f8245eb",
        ["-", "-", "-", "-", None, None],
    ),
    "degeneracy_1": (
        "95239c8016f24506b48ebab3fa338a484458af9e7cbede60682e747d48322c7b",
        ["-", "-", "-", "-", None],
    ),
    "degeneracy_2": (
        "bd8500898847bead64f9b052dbfe0163ecef9f0427342cde150436b03cdaff37",
        ["-", "-", "-", "-", None],
    ),
    "degeneracy_6": (
        "168499c5c00d966eaf41cc1005a01144ce6b823ef20c5e109a245d446df935d2",
        ["-", "-", "-", "-", None],
    ),
    "degeneracy_missing": (
        "6212b8dc1a2f762eb54bc242cb507d09feb870aee8f8c5959150d356c1143926",
        ["-", "-", "-", "-", "missing_or_invalid_degeneracy"],
    ),
    "field_a_units": (
        "707ddf5698f879d92a91961bb700308f84d1c80295c484399b0cba6e9b700636",
        ["-", "-", "-", "-", "incompatible_or_missing_rate_units"],
    ),
    "field_degeneracy_convention": (
        "6504a18bbdea764f2a882787f9a9cfef5fd74428e529878c686c608793579e49",
        ["-", "-", "-", "-", "ambiguous_degeneracy_convention"],
    ),
    "field_direction": (
        "902b49a3f0e7736c9147d4d5c04db7ef9a256a9ee044e168053df19162e822ce",
        ["-", "-", "-", "-", "explicit_opposite_directions_required"],
    ),
    "field_pressure_context": (
        "0b21a859ac538b6b4ef2cf7dfe039db78df72978284075b584c9ff6c8b7e48a4",
        ["-", "-", "-", "-", "non_elementary_or_ambiguous_effective_rate"],
    ),
    "field_third_body": (
        "7a42ffa20bb6883c881586e20d54dc2b7c67c589b03c4f77fadae31e1b7377fb",
        ["-", "-", "-", "-", "non_elementary_or_ambiguous_effective_rate"],
    ),
    "field_tmin": (
        "f110229cae2d0084e752803847288cdc0360575aa9e403475a63778145a1dac8",
        ["-", "-", "-", "-", "missing_or_invalid_rate_domain"],
    ),
    "incompatible_pressure": (
        "6e95ef848e29e9945ecb794712172a2181f965c0572d1b6c069e4b7330ab4ba0",
        ["-", "-", "-", "-", "incompatible_reference_pressures"],
    ),
    "inferred_collider": (
        "203f094c0b691534cbef9d92f0c5f4d25a2c9ad90bc7e81c0d6902a934f86be6",
        ["-", "-", "-", "-", "inferred_third_body_unsupported"],
    ),
    "isotope_product": (
        "4bab215983c7b024d79d3e05901931794797b0d099b2b64eb77546f55186dede",
        ["-", "-", "-", "-", "isotope_specific_equilibrium_unsupported"],
    ),
    "isotope_reactant": (
        "7e0c14014f281917a0818a36259943c9efb85697d33c4fc7331bd061f3b2546c",
        ["-", "-", "-", "-", "isotope_specific_equilibrium_unsupported"],
    ),
    "isotope_then_unparseable": (
        "63bb637fb2c2ea125e7bd44599f9614f7bb60c901e40d7e31cb0a22db1ef0c08",
        ["-", "-", "-", "-", "isotope_specific_equilibrium_unsupported"],
    ),
    "missing_mapping": (
        "a6eb3f8fcda6c85698ce0567acbf0ff5a5aa33db78a259088dee0bc1a130cf31",
        ["-", "-", "-", "incomplete_or_incompatible_thermo_mapping"],
    ),
    "no_products": (
        "a8adddf18eb91c8f6a0a2144ab2dc3d6b50885099aaee3da8a84ff1425ba31b5",
        ["-", "-", "-", "-", "missing_or_unsupported_participants"],
    ),
    "outside_rate_domain": (
        "7f45fe84b5ed6c77dfdba25aa87c87ac19fb6c83b4b363c1ee1e61211dd41650",
        ["-", "-", "-", "-", "temperature_outside_rate_domain"],
    ),
    "reordered_sets": (
        "d0a7a14c5b4172bffad0e73b407f8bd177a817230b99c4b15b6362037fdb142b",
        ["-", "-", "-", "-", None, None],
    ),
    "reverse_scaled": (
        "ba7f1f65197df2fd2d69ecbd79977f51f11c5f145967b38548186dd460957a9a",
        ["-", "-", "-", "-", None],
    ),
    "third_order_cm6": (
        "31c919bbc55e12d561dbb44924b7db57f912c49d24b87cd846b55b8f984ee554",
        ["-", "-", "-", "-", "-", None, None],
    ),
    "third_order_m6": (
        "f4c9ba31510ea6fbb0d4a676b78ce1092157ff880d8d2be8afb93b0a306a4c24",
        ["-", "-", "-", "-", "-", None, None],
    ),
    "third_order_molecule": (
        "e260a7e9d1707b89bff5db640bfc49027fc2257efc8268221a0130a9113b66c5",
        ["-", "-", "-", "-", "-", None, None],
    ),
    "unbalanced_charge": (
        "6bb440b7052df4a197d8c4889bfff998f2262305d82826100d1c1dc944a980cf",
        ["-", "-", "-", "-", "unbalanced_stoichiometry"],
    ),
    "unbalanced_elements": (
        "413b851c428084ee40b8a14db23ce9c7a8dd51fce52960933bb1c079b8c53ed6",
        ["-", "-", "-", "-", "unbalanced_stoichiometry"],
    ),
    "unknown_role": (
        "d94d854fff3f1de0b72e3d0e4c3d279a4f2f1c405dfee3cf43193f84ba3d75bf",
        ["-", "-", "-", "-", "missing_or_unsupported_participants"],
    ),
    "unparseable_product": (
        "728b357641139e3997d1ef9d76c6c60c993922644e1cf77ccb1c8a7da569ec3f",
        ["-", "-", "-", "-", "unusable_species_composition"],
    ),
    "unparseable_reactant": (
        "427ed7dd5c313da37d7161a265ff3c77d341c7830a44b2405b7dac89cd370e10",
        ["-", "-", "-", "-", "unusable_species_composition"],
    ),
    "unparseable_then_isotope": (
        "f210453ac5064f571a2be90de5b734c5903cdb0d4e200be7bc50039a2e6d518c",
        ["-", "-", "-", "-", "unusable_species_composition"],
    ),
    "unrecorded_pressure": (
        "347c8efb57522c3c0dfff262a5ae9300115eb0855a5d3a3fc40c64dbfe784ebc",
        ["-", "-", "-", "-", "missing_or_invalid_reference_pressure"],
    ),
}


def test_every_scenario_is_pinned():
    assert len(SCENARIOS) == 40
    assert set(EXPECTED) == set(SCENARIOS)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_d3_hash_and_reasons_are_byte_identical_to_pre_extraction(name):
    context_hash, reasons = _run(name)
    expected_hash, expected_reasons = EXPECTED[name]
    assert context_hash == expected_hash
    assert reasons == expected_reasons
