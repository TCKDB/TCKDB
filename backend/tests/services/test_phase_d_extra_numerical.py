"""Additional independent elementary-order and engine applicability regressions."""
import json

import pytest

from app.db.models.kinetics import Kinetics
from app.db.models.reaction import ReactionEntry, ReactionEntryStructureParticipant
from app.services.consistency.kinetics import compare_kinetics
from tests.services.test_phase_d_numerical import _reaction, _thermo

R = 8.31446261815324
NA = 6.02214076e23


@pytest.mark.parametrize("units,a", [("m6_mol2_s", 1.0), ("cm6_mol2_s", 1e12),
                                    ("cm6_molecule2_s", 1e12 / NA**2)])
def test_third_order_elementary_units_have_independent_equilibrium(units, a):
    # 2 H + O -> H2O, no shared collider. Cp/R equals atom count, so all
    # reaction standard quantities cancel and Kc = (p0/RT)^(-2).
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
    result = compare_kinetics(forward, reverse, {t.species_entry_id: t for t in thermos}, temperature_grid=[500, 900])
    rows = [json.loads(f.message) for f in result.findings if '"k_forward"' in f.message]
    assert len(rows) == 2
    for row in rows:
        assert row["k_forward"] == pytest.approx(1e6, rel=1e-12)
        assert row["equilibrium_kc"] == pytest.approx((100000 / (1000 * R * row["temperature_k"]))**-2)
        assert row["k_reverse_supplied"] == pytest.approx(row["k_reverse_thermo"], rel=1e-12)
        assert row["log_ratio_residual"] == pytest.approx(0, abs=1e-12)


def test_cantera_inferred_collider_does_not_become_an_elementary_comparison():
    forward, reverse, mapping = _reaction()
    # H + H2 -> 3 H causes Cantera to infer H as a third-body collider even
    # when constructed without third_body. Such a case is outside D3.
    entry = forward.reaction_entry
    for role, index in [("reactant", 2), ("product", 3)]:
        entry.structure_participants.append(ReactionEntryStructureParticipant(
            species_entry_id=2, species_entry=mapping[2].species_entry, role=role, participant_index=index))
    forward.a_units = "m3_mol_s"
    reverse.a_units = "m6_mol2_s"
    result = compare_kinetics(forward, reverse, mapping, temperature_grid=[500])
    rows = [json.loads(f.message) for f in result.findings if '"rate_units"' in f.message]
    assert len(rows) == 1
    assert rows[0]["reason"] == "inferred_third_body_unsupported"
    assert "k_forward" not in rows[0]
