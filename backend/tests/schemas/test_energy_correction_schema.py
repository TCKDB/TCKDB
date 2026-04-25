"""Tests for app/schemas/entities/energy_correction.py."""

import pytest
from pydantic import ValidationError

from app.db.models.common import (
    AppliedCorrectionComponentKind,
    EnergyCorrectionApplicationRole,
    EnergyCorrectionSchemeKind,
    FrequencyScaleKind,
    MeliusBacComponentKind,
)
from app.schemas.entities.energy_correction import (
    AppliedEnergyCorrectionComponentCreate,
    AppliedEnergyCorrectionCreate,
    EnergyCorrectionSchemeAtomParamCreate,
    EnergyCorrectionSchemeBondParamCreate,
    EnergyCorrectionSchemeComponentParamCreate,
    EnergyCorrectionSchemeCreate,
    EnergyCorrectionSchemeUpdate,
    FrequencyScaleFactorCreate,
    FrequencyScaleFactorUpdate,
)


# ---------------------------------------------------------------------------
# EnergyCorrectionSchemeAtomParam
# ---------------------------------------------------------------------------


class TestSchemeAtomParam:
    def test_valid(self) -> None:
        p = EnergyCorrectionSchemeAtomParamCreate(element="C", value=-37.785385)
        assert p.element == "C"
        assert p.value == -37.785385

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            EnergyCorrectionSchemeAtomParamCreate(
                element="C", value=1.0, extra_field="bad"
            )


# ---------------------------------------------------------------------------
# EnergyCorrectionSchemeBondParam
# ---------------------------------------------------------------------------


class TestSchemeBondParam:
    def test_valid(self) -> None:
        p = EnergyCorrectionSchemeBondParamCreate(bond_key="C-H", value=-0.11)
        assert p.bond_key == "C-H"

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            EnergyCorrectionSchemeBondParamCreate(
                bond_key="C-H", value=1.0, oops=True
            )


# ---------------------------------------------------------------------------
# EnergyCorrectionSchemeComponentParam
# ---------------------------------------------------------------------------


class TestSchemeComponentParam:
    def test_valid(self) -> None:
        p = EnergyCorrectionSchemeComponentParamCreate(
            component_kind=MeliusBacComponentKind.atom_corr, key="C", value=-0.05
        )
        assert p.component_kind == MeliusBacComponentKind.atom_corr


# ---------------------------------------------------------------------------
# EnergyCorrectionScheme
# ---------------------------------------------------------------------------


class TestSchemeCreate:
    def test_valid_with_all_children(self) -> None:
        s = EnergyCorrectionSchemeCreate(
            kind=EnergyCorrectionSchemeKind.bac_petersson,
            name="Arkane Petersson BAC 2024",
            units="kcal_mol",
            atom_params=[
                EnergyCorrectionSchemeAtomParamCreate(element="C", value=-37.79),
                EnergyCorrectionSchemeAtomParamCreate(element="H", value=-0.50),
            ],
            bond_params=[
                EnergyCorrectionSchemeBondParamCreate(bond_key="C-H", value=-0.11),
                EnergyCorrectionSchemeBondParamCreate(bond_key="C=C", value=0.20),
            ],
            component_params=[
                EnergyCorrectionSchemeComponentParamCreate(
                    component_kind=MeliusBacComponentKind.atom_corr,
                    key="C",
                    value=-0.05,
                ),
            ],
        )
        assert len(s.atom_params) == 2
        assert len(s.bond_params) == 2
        assert len(s.component_params) == 1

    def test_valid_minimal(self) -> None:
        s = EnergyCorrectionSchemeCreate(
            kind=EnergyCorrectionSchemeKind.atom_energy,
            name="CBS-QB3 atom energies",
        )
        assert s.atom_params == []
        assert s.bond_params == []
        assert s.component_params == []

    def test_rejects_duplicate_element(self) -> None:
        with pytest.raises(ValidationError, match="unique by element"):
            EnergyCorrectionSchemeCreate(
                kind=EnergyCorrectionSchemeKind.atom_energy,
                name="test",
                atom_params=[
                    EnergyCorrectionSchemeAtomParamCreate(element="C", value=1.0),
                    EnergyCorrectionSchemeAtomParamCreate(element="C", value=2.0),
                ],
            )

    def test_rejects_duplicate_bond_key(self) -> None:
        with pytest.raises(ValidationError, match="unique by bond_key"):
            EnergyCorrectionSchemeCreate(
                kind=EnergyCorrectionSchemeKind.bac_petersson,
                name="test",
                bond_params=[
                    EnergyCorrectionSchemeBondParamCreate(bond_key="C-H", value=1.0),
                    EnergyCorrectionSchemeBondParamCreate(bond_key="C-H", value=2.0),
                ],
            )

    def test_rejects_duplicate_component_key(self) -> None:
        with pytest.raises(ValidationError, match="unique by .component_kind, key."):
            EnergyCorrectionSchemeCreate(
                kind=EnergyCorrectionSchemeKind.bac_melius,
                name="test",
                component_params=[
                    EnergyCorrectionSchemeComponentParamCreate(
                        component_kind=MeliusBacComponentKind.atom_corr,
                        key="C",
                        value=1.0,
                    ),
                    EnergyCorrectionSchemeComponentParamCreate(
                        component_kind=MeliusBacComponentKind.atom_corr,
                        key="C",
                        value=2.0,
                    ),
                ],
            )


class TestSchemeUpdate:
    def test_all_optional(self) -> None:
        u = EnergyCorrectionSchemeUpdate()
        assert u.kind is None
        assert u.name is None

    def test_partial_update(self) -> None:
        u = EnergyCorrectionSchemeUpdate(note="updated note")
        assert u.note == "updated note"
        assert u.kind is None


# ---------------------------------------------------------------------------
# FrequencyScaleFactor
# ---------------------------------------------------------------------------


class TestFrequencyScaleFactor:
    def test_valid(self) -> None:
        f = FrequencyScaleFactorCreate(
            level_of_theory_id=1,
            scale_kind=FrequencyScaleKind.zpe,
            value=0.984,
        )
        assert f.value == 0.984
        assert f.scale_kind == FrequencyScaleKind.zpe

    def test_rejects_zero_value(self) -> None:
        with pytest.raises(ValidationError):
            FrequencyScaleFactorCreate(
                level_of_theory_id=1,
                scale_kind=FrequencyScaleKind.fundamental,
                value=0,
            )

    def test_rejects_negative_value(self) -> None:
        with pytest.raises(ValidationError):
            FrequencyScaleFactorCreate(
                level_of_theory_id=1,
                scale_kind=FrequencyScaleKind.enthalpy,
                value=-0.5,
            )

    def test_update_partial(self) -> None:
        u = FrequencyScaleFactorUpdate(value=0.975)
        assert u.value == 0.975
        assert u.scale_kind is None


# ---------------------------------------------------------------------------
# AppliedEnergyCorrectionComponent
# ---------------------------------------------------------------------------


class TestAppliedComponent:
    def test_valid(self) -> None:
        c = AppliedEnergyCorrectionComponentCreate(
            component_kind=AppliedCorrectionComponentKind.bond,
            key="C-H",
            multiplicity=5,
            parameter_value=-0.11,
            contribution_value=-0.55,
        )
        assert c.multiplicity == 5
        assert c.contribution_value == -0.55

    def test_default_multiplicity(self) -> None:
        c = AppliedEnergyCorrectionComponentCreate(
            component_kind=AppliedCorrectionComponentKind.molecular,
            key="mol_corr",
            parameter_value=-1.19,
            contribution_value=-1.19,
        )
        assert c.multiplicity == 1

    def test_rejects_zero_multiplicity(self) -> None:
        with pytest.raises(ValidationError):
            AppliedEnergyCorrectionComponentCreate(
                component_kind=AppliedCorrectionComponentKind.atom,
                key="C",
                multiplicity=0,
                parameter_value=1.0,
                contribution_value=1.0,
            )


# ---------------------------------------------------------------------------
# AppliedEnergyCorrection
# ---------------------------------------------------------------------------


class TestAppliedCorrectionCreate:
    def test_valid_bac_with_scheme_and_components(self) -> None:
        a = AppliedEnergyCorrectionCreate(
            scheme_id=1,
            application_role=EnergyCorrectionApplicationRole.bac_total,
            value=-1.84,
            value_unit="kcal_mol",
            target_species_entry_id=42,
            source_conformer_observation_id=10,
            components=[
                AppliedEnergyCorrectionComponentCreate(
                    component_kind=AppliedCorrectionComponentKind.bond,
                    key="C-H",
                    multiplicity=5,
                    parameter_value=-0.11,
                    contribution_value=-0.55,
                ),
                AppliedEnergyCorrectionComponentCreate(
                    component_kind=AppliedCorrectionComponentKind.bond,
                    key="C-C",
                    multiplicity=1,
                    parameter_value=0.20,
                    contribution_value=0.20,
                ),
            ],
        )
        assert a.value == -1.84
        assert a.scheme_id == 1
        assert a.frequency_scale_factor_id is None
        assert len(a.components) == 2

    def test_valid_zpe_with_frequency_scale_factor(self) -> None:
        a = AppliedEnergyCorrectionCreate(
            frequency_scale_factor_id=3,
            application_role=EnergyCorrectionApplicationRole.zpe,
            value=0.045231,
            value_unit="hartree",
            target_species_entry_id=42,
            source_calculation_id=5,
        )
        assert a.frequency_scale_factor_id == 3
        assert a.scheme_id is None

    def test_valid_thermal_with_fsf_and_temperature(self) -> None:
        a = AppliedEnergyCorrectionCreate(
            frequency_scale_factor_id=4,
            application_role=EnergyCorrectionApplicationRole.thermal_correction_enthalpy,
            value=0.050921,
            value_unit="hartree",
            temperature_k=298.15,
            target_species_entry_id=42,
            source_conformer_observation_id=10,
            source_calculation_id=20,
        )
        assert a.source_conformer_observation_id == 10
        assert a.temperature_k == 298.15

    def test_valid_reaction_target(self) -> None:
        a = AppliedEnergyCorrectionCreate(
            scheme_id=1,
            application_role=EnergyCorrectionApplicationRole.composite_delta,
            value=5.2,
            value_unit="kj_mol",
            target_reaction_entry_id=7,
        )
        assert a.target_reaction_entry_id == 7
        assert a.target_species_entry_id is None

    # --- target constraints ---

    def test_rejects_both_targets(self) -> None:
        with pytest.raises(ValidationError, match="Exactly one"):
            AppliedEnergyCorrectionCreate(
                scheme_id=1,
                application_role=EnergyCorrectionApplicationRole.bac_total,
                value=1.0,
                value_unit="hartree",
                target_species_entry_id=5,
                target_reaction_entry_id=10,
            )

    def test_rejects_no_target(self) -> None:
        with pytest.raises(ValidationError, match="Exactly one"):
            AppliedEnergyCorrectionCreate(
                scheme_id=1,
                application_role=EnergyCorrectionApplicationRole.bac_total,
                value=1.0,
                value_unit="hartree",
            )

    # --- provenance constraints ---

    def test_rejects_both_provenance_sources(self) -> None:
        with pytest.raises(ValidationError, match="Exactly one"):
            AppliedEnergyCorrectionCreate(
                scheme_id=1,
                frequency_scale_factor_id=2,
                application_role=EnergyCorrectionApplicationRole.zpe,
                value=1.0,
                value_unit="hartree",
                target_species_entry_id=42,
            )

    def test_rejects_no_provenance_source(self) -> None:
        with pytest.raises(ValidationError, match="Exactly one"):
            AppliedEnergyCorrectionCreate(
                application_role=EnergyCorrectionApplicationRole.zpe,
                value=1.0,
                value_unit="hartree",
                target_species_entry_id=42,
            )

    # --- component & temperature constraints ---

    def test_rejects_duplicate_components(self) -> None:
        with pytest.raises(ValidationError, match="unique by .component_kind, key."):
            AppliedEnergyCorrectionCreate(
                scheme_id=1,
                application_role=EnergyCorrectionApplicationRole.bac_total,
                value=1.0,
                value_unit="hartree",
                target_species_entry_id=42,
                components=[
                    AppliedEnergyCorrectionComponentCreate(
                        component_kind=AppliedCorrectionComponentKind.bond,
                        key="C-H",
                        parameter_value=1.0,
                        contribution_value=1.0,
                    ),
                    AppliedEnergyCorrectionComponentCreate(
                        component_kind=AppliedCorrectionComponentKind.bond,
                        key="C-H",
                        parameter_value=2.0,
                        contribution_value=2.0,
                    ),
                ],
            )

    def test_rejects_negative_temperature(self) -> None:
        with pytest.raises(ValidationError):
            AppliedEnergyCorrectionCreate(
                scheme_id=1,
                application_role=EnergyCorrectionApplicationRole.aec_total,
                value=1.0,
                value_unit="hartree",
                target_species_entry_id=42,
                temperature_k=-10,
            )

    def test_rejects_zero_temperature(self) -> None:
        with pytest.raises(ValidationError):
            AppliedEnergyCorrectionCreate(
                frequency_scale_factor_id=1,
                application_role=EnergyCorrectionApplicationRole.zpe,
                value=1.0,
                value_unit="hartree",
                target_species_entry_id=42,
                source_calculation_id=5,
                temperature_k=0,
            )

    # --- role ↔ source compatibility ---

    def test_rejects_fsf_role_with_scheme_source(self) -> None:
        """ZPE role requires frequency_scale_factor_id, not scheme_id."""
        with pytest.raises(ValidationError, match="requires frequency_scale_factor_id"):
            AppliedEnergyCorrectionCreate(
                scheme_id=1,
                application_role=EnergyCorrectionApplicationRole.zpe,
                value=0.05,
                value_unit="hartree",
                target_species_entry_id=42,
            )

    def test_rejects_scheme_role_with_fsf_source(self) -> None:
        """BAC role requires scheme_id, not frequency_scale_factor_id."""
        with pytest.raises(ValidationError, match="requires scheme_id"):
            AppliedEnergyCorrectionCreate(
                frequency_scale_factor_id=1,
                application_role=EnergyCorrectionApplicationRole.bac_total,
                value=-1.0,
                value_unit="kcal_mol",
                target_species_entry_id=42,
                source_calculation_id=5,
            )

    def test_rejects_thermal_role_with_scheme_source(self) -> None:
        with pytest.raises(ValidationError, match="requires frequency_scale_factor_id"):
            AppliedEnergyCorrectionCreate(
                scheme_id=1,
                application_role=EnergyCorrectionApplicationRole.thermal_correction_enthalpy,
                value=0.05,
                value_unit="hartree",
                target_species_entry_id=42,
                temperature_k=298.15,
            )

    def test_allows_composite_delta_with_either_source(self) -> None:
        """composite_delta is flexible — accepts scheme or fsf."""
        a = AppliedEnergyCorrectionCreate(
            scheme_id=1,
            application_role=EnergyCorrectionApplicationRole.composite_delta,
            value=5.0,
            value_unit="kj_mol",
            target_species_entry_id=42,
        )
        assert a.scheme_id == 1

        b = AppliedEnergyCorrectionCreate(
            frequency_scale_factor_id=1,
            application_role=EnergyCorrectionApplicationRole.composite_delta,
            value=5.0,
            value_unit="kj_mol",
            target_species_entry_id=42,
            source_calculation_id=10,
        )
        assert b.frequency_scale_factor_id == 1

    def test_allows_custom_role_with_either_source(self) -> None:
        a = AppliedEnergyCorrectionCreate(
            scheme_id=1,
            application_role=EnergyCorrectionApplicationRole.custom,
            value=1.0,
            value_unit="hartree",
            target_reaction_entry_id=7,
        )
        assert a.application_role == EnergyCorrectionApplicationRole.custom

    # --- fsf requires source_calculation ---

    def test_rejects_fsf_without_source_calculation(self) -> None:
        with pytest.raises(ValidationError, match="requires source_calculation_id"):
            AppliedEnergyCorrectionCreate(
                frequency_scale_factor_id=1,
                application_role=EnergyCorrectionApplicationRole.zpe,
                value=0.05,
                value_unit="hartree",
                target_species_entry_id=42,
            )
