"""Local-validation tests for the Phase-2 reaction-side builders.

These tests exercise ``TransitionState``, ``ChemReaction``, and
``Kinetics`` in isolation — they never hit the network and never
construct a ``ComputedReactionUpload`` (which has its own snapshot
suite).
"""

from __future__ import annotations

import pytest

from tckdb_client.builders import (
    Calculation,
    ChemReaction,
    Geometry,
    Kinetics,
    LevelOfTheory,
    Species,
    SoftwareRelease,
    TCKDBBuilderValidationError,
    TransitionState,
)


def _gaussian() -> SoftwareRelease:
    return SoftwareRelease(software="Gaussian", version="16")


def _b3lyp() -> LevelOfTheory:
    return LevelOfTheory(method="B3LYP", basis="6-31G(d)")


def _xyz() -> Geometry:
    return Geometry.from_xyz("1\nx\nH 0 0 0")


# --- TransitionState --------------------------------------------------


class TestTransitionState:
    def test_minimal_ts(self):
        ts = TransitionState(charge=0, multiplicity=2)
        assert ts.charge == 0
        assert ts.multiplicity == 2
        assert ts.geometry is None

    def test_multiplicity_must_be_positive(self):
        with pytest.raises(TCKDBBuilderValidationError):
            TransitionState(charge=0, multiplicity=0)

    def test_charge_must_be_int(self):
        with pytest.raises(TCKDBBuilderValidationError):
            TransitionState(charge="zero", multiplicity=1)  # type: ignore[arg-type]

    def test_geometry_must_be_builder(self):
        with pytest.raises(TCKDBBuilderValidationError):
            TransitionState(charge=0, multiplicity=1, geometry="not a geom")  # type: ignore[arg-type]

    def test_label_smiles_must_be_non_empty(self):
        with pytest.raises(TCKDBBuilderValidationError):
            TransitionState(charge=0, multiplicity=1, label="")
        with pytest.raises(TCKDBBuilderValidationError):
            TransitionState(charge=0, multiplicity=1, smiles="   ")


# --- ChemReaction -----------------------------------------------------


class TestChemReaction:
    def test_minimal_reaction(self):
        a = Species(smiles="C", charge=0, multiplicity=1)
        b = Species(smiles="[CH3]", charge=0, multiplicity=2)
        rxn = ChemReaction(reactants=[a], products=[b])
        assert rxn.reversible is True
        assert rxn.transition_state is None
        assert rxn.kinetics == []

    def test_empty_reactants_rejected(self):
        b = Species(smiles="C", charge=0, multiplicity=1)
        with pytest.raises(TCKDBBuilderValidationError):
            ChemReaction(reactants=[], products=[b])

    def test_empty_products_rejected(self):
        a = Species(smiles="C", charge=0, multiplicity=1)
        with pytest.raises(TCKDBBuilderValidationError):
            ChemReaction(reactants=[a], products=[])

    def test_reactants_must_be_species(self):
        b = Species(smiles="C", charge=0, multiplicity=1)
        with pytest.raises(TCKDBBuilderValidationError):
            ChemReaction(reactants=["A"], products=[b])  # type: ignore[list-item]

    def test_transition_state_must_be_builder(self):
        a = Species(smiles="C", charge=0, multiplicity=1)
        with pytest.raises(TCKDBBuilderValidationError):
            ChemReaction(
                reactants=[a], products=[a],
                transition_state="not a TS",  # type: ignore[arg-type]
            )

    def test_reversible_must_be_bool(self):
        a = Species(smiles="C", charge=0, multiplicity=1)
        with pytest.raises(TCKDBBuilderValidationError):
            ChemReaction(reactants=[a], products=[a], reversible="yes")  # type: ignore[arg-type]

    def test_unique_species_preserves_order_and_dedups(self):
        a = Species(smiles="C", charge=0, multiplicity=1)
        b = Species(smiles="[CH3]", charge=0, multiplicity=2)
        c = Species(smiles="[H]", charge=0, multiplicity=2)
        rxn = ChemReaction(reactants=[a, b], products=[c, a])
        assert rxn.unique_species() == [a, b, c]

    def test_add_kinetics_appends(self):
        a = Species(smiles="C", charge=0, multiplicity=1)
        b = Species(smiles="[CH3]", charge=0, multiplicity=2)
        rxn = ChemReaction(reactants=[a], products=[b])
        k = Kinetics.modified_arrhenius(
            A=1.0, A_units="per_s", n=0, Ea=0,
        )
        rxn.add_kinetics(k)
        assert rxn.kinetics == [k]


# --- Kinetics ---------------------------------------------------------


class TestKinetics:
    def test_modified_arrhenius_happy_path(self):
        k = Kinetics.modified_arrhenius(
            A=1.0e13, A_units="cm3/mol/s", n=0.5, Ea=20.0, Ea_units="kJ/mol",
            Tmin=300, Tmax=2000,
        )
        assert k.model_kind == "modified_arrhenius"
        assert k.a == 1.0e13
        assert k.a_units == "cm3_mol_s"
        assert k.reported_ea == 20.0
        assert k.reported_ea_units == "kj_mol"
        assert k.tmin_k == 300.0
        assert k.tmax_k == 2000.0

    def test_a_must_be_positive(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Kinetics.modified_arrhenius(A=0, A_units="per_s", n=0, Ea=0)
        with pytest.raises(TCKDBBuilderValidationError):
            Kinetics.modified_arrhenius(A=-1, A_units="per_s", n=0, Ea=0)

    def test_a_must_be_numeric(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Kinetics.modified_arrhenius(A="1e13", A_units="per_s", n=0, Ea=0)  # type: ignore[arg-type]
        with pytest.raises(TCKDBBuilderValidationError):
            Kinetics.modified_arrhenius(A=True, A_units="per_s", n=0, Ea=0)  # type: ignore[arg-type]

    def test_a_units_aliases(self):
        for alias in ("s^-1", "1/s", "per_s"):
            k = Kinetics.modified_arrhenius(A=1.0, A_units=alias, n=0, Ea=0)
            assert k.a_units == "per_s"
        for alias in ("cm3/mol/s", "cm3_mol_s"):
            k = Kinetics.modified_arrhenius(A=1.0, A_units=alias, n=0, Ea=0)
            assert k.a_units == "cm3_mol_s"

    def test_a_units_rejected_when_unknown(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Kinetics.modified_arrhenius(A=1.0, A_units="bogus", n=0, Ea=0)

    def test_ea_kcal_per_mol_converts_to_kj_per_mol(self):
        k = Kinetics.modified_arrhenius(
            A=1.0, A_units="per_s", n=0, Ea=10.0, Ea_units="kcal/mol",
        )
        # 10 kcal/mol * 4.184 = 41.84 kJ/mol
        assert k.reported_ea == pytest.approx(41.84)
        assert k.reported_ea_units == "kj_mol"

    def test_ea_kj_per_mol_passes_through(self):
        for alias in ("kJ/mol", "kj_mol"):
            k = Kinetics.modified_arrhenius(
                A=1.0, A_units="per_s", n=0, Ea=5.0, Ea_units=alias,
            )
            assert k.reported_ea == 5.0
            assert k.reported_ea_units == "kj_mol"

    def test_ea_units_rejected_when_unknown(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Kinetics.modified_arrhenius(
                A=1.0, A_units="per_s", n=0, Ea=1.0, Ea_units="cal/cm",
            )

    def test_temperature_bounds_positive(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Kinetics.modified_arrhenius(
                A=1.0, A_units="per_s", n=0, Ea=0, Tmin=0, Tmax=2000,
            )
        with pytest.raises(TCKDBBuilderValidationError):
            Kinetics.modified_arrhenius(
                A=1.0, A_units="per_s", n=0, Ea=0, Tmin=300, Tmax=-1,
            )

    def test_tmin_must_be_le_tmax(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Kinetics.modified_arrhenius(
                A=1.0, A_units="per_s", n=0, Ea=0, Tmin=2000, Tmax=300,
            )

    def test_degeneracy_must_be_positive(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Kinetics.modified_arrhenius(
                A=1.0, A_units="per_s", n=0, Ea=0, degeneracy=0,
            )

    def test_degeneracy_convention_is_validated_and_emitted(self):
        kinetics = Kinetics.modified_arrhenius(
            A=1.0,
            A_units="per_s",
            n=0,
            Ea=0,
            degeneracy=2.0,
            degeneracy_convention="not_applied",
        )
        payload = kinetics.to_payload(
            reactant_keys=["a"],
            product_keys=["b"],
            calc_key_lookup=lambda calculation: "unused",
        )
        assert payload["degeneracy_convention"] == "not_applied"

        with pytest.raises(TCKDBBuilderValidationError):
            Kinetics.modified_arrhenius(
                A=1.0,
                A_units="per_s",
                n=0,
                Ea=0,
                degeneracy_convention="assumed",
            )

    def test_source_calculations_role_aliasing(self):
        opt = Calculation.opt(_gaussian(), _b3lyp(), output_geometry=_xyz())
        k = Kinetics.modified_arrhenius(
            A=1.0, A_units="per_s", n=0, Ea=0,
            source_calculations={"ts_energy": opt},
        )
        roles = [r for r, _ in k.source_calculations_iter()]
        assert roles == ["ts_energy"]

    def test_source_calculations_rejects_unknown_role(self):
        opt = Calculation.opt(_gaussian(), _b3lyp(), output_geometry=_xyz())
        with pytest.raises(TCKDBBuilderValidationError):
            Kinetics.modified_arrhenius(
                A=1.0, A_units="per_s", n=0, Ea=0,
                source_calculations={"vibrational_data": opt},
            )

    def test_source_calculations_values_must_be_calculations(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Kinetics.modified_arrhenius(
                A=1.0, A_units="per_s", n=0, Ea=0,
                source_calculations={"ts_energy": "not a calc"},  # type: ignore[dict-item]
            )

    def test_tunneling_model_must_be_non_empty(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Kinetics.modified_arrhenius(
                A=1.0, A_units="per_s", n=0, Ea=0, tunneling_model="",
            )
