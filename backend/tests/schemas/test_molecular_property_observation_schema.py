"""Schema-level tests for ``MolecularPropertyObservationCreate``.

Exercises the Pydantic validators independently of the CCCBDB builder.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.db.models.common import (
    MolecularPropertyKind,
    ObservedStateBasis,
    ObservedUncertaintyAssessor,
    ObservedUncertaintyKind,
    ScientificOriginKind,
)
from app.schemas.entities.molecular_property_observation import (
    HEAT_CAPACITY_CP_UNIT,
    MolecularPropertyObservationCreate,
)


def _kwargs(**overrides):
    base = {
        "scientific_origin": ScientificOriginKind.experimental,
        "property_kind": MolecularPropertyKind.dipole_moment,
        "scalar_value": 1.855,
        "scalar_unit": "Debye",
    }
    base.update(overrides)
    return base


class TestAcceptsValidScalar:
    def test_minimal_scalar_payload(self):
        m = MolecularPropertyObservationCreate(**_kwargs())
        assert m.scalar_value == pytest.approx(1.855)
        assert m.scalar_unit == "Debye"


class TestAcceptsVectorAndTensor:
    def test_vector_payload(self):
        m = MolecularPropertyObservationCreate(
            **_kwargs(
                scalar_value=None,
                scalar_unit=None,
                vector_json=[0.0, 0.0, -1.855],
            )
        )
        assert m.vector_json == [0.0, 0.0, -1.855]

    def test_tensor_payload(self):
        m = MolecularPropertyObservationCreate(
            scientific_origin=ScientificOriginKind.experimental,
            property_kind=MolecularPropertyKind.polarizability,
            tensor_json=[
                [9.0, 0.0, 0.0],
                [0.0, 9.5, 0.0],
                [0.0, 0.0, 10.1],
            ],
        )
        assert len(m.tensor_json) == 3


class TestEmptyValueRepresentationRejected:
    def test_no_value_at_all(self):
        with pytest.raises(ValidationError) as exc:
            MolecularPropertyObservationCreate(
                **_kwargs(scalar_value=None, scalar_unit=None)
            )
        assert "at least one" in str(exc.value)


class TestScalarValueRequiresUnit:
    def test_scalar_without_unit(self):
        with pytest.raises(ValidationError) as exc:
            MolecularPropertyObservationCreate(
                **_kwargs(scalar_unit=None)
            )
        assert "scalar_unit is required" in str(exc.value)


class TestNegativeUncertaintyRejected:
    def test_negative_uncertainty(self):
        with pytest.raises(ValidationError):
            MolecularPropertyObservationCreate(
                **_kwargs(scalar_uncertainty=-0.1)
            )


class TestNonPositiveTemperatureRejected:
    @pytest.mark.parametrize("bad", [0.0, -10.0])
    def test_temperature(self, bad):
        with pytest.raises(ValidationError):
            MolecularPropertyObservationCreate(
                **_kwargs(temperature_k=bad)
            )

    @pytest.mark.parametrize("bad", [0.0, -1.0])
    def test_wavelength(self, bad):
        with pytest.raises(ValidationError):
            MolecularPropertyObservationCreate(
                **_kwargs(wavelength_nm=bad)
            )


class TestOtherKindRequiresLabel:
    def test_other_without_label_rejected(self):
        with pytest.raises(ValidationError) as exc:
            MolecularPropertyObservationCreate(
                scientific_origin=ScientificOriginKind.experimental,
                property_kind=MolecularPropertyKind.other,
                scalar_value=1.0,
                scalar_unit="kJ/mol",
            )
        assert "property_label is required" in str(exc.value)

    def test_other_with_label_accepted(self):
        m = MolecularPropertyObservationCreate(
            scientific_origin=ScientificOriginKind.experimental,
            property_kind=MolecularPropertyKind.other,
            property_label="atomization_energy_corrected",
            scalar_value=1234.5,
            scalar_unit="kJ/mol",
        )
        assert m.property_label == "atomization_energy_corrected"


def _cp_kwargs(**overrides):
    """A minimal, honest experimental ideal-gas Cp(298.15 K) point.

    Before Phase C-E1, this exact point could only be expressed as
    ``property_kind=other``, ``scalar_unit="J/K/mol"`` (free text -- no
    fixed-unit guarantee), no pressure, no state basis, and a bare
    ``scalar_uncertainty`` with no stated meaning (not even "standard
    deviation" vs "expanded, k=2"). That is the known problem this
    contract closes; see the migration test module for the DB-level
    version of the same story.
    """
    base = {
        "scientific_origin": ScientificOriginKind.experimental,
        "property_kind": MolecularPropertyKind.heat_capacity_cp,
        "scalar_value": 29.1,
        "scalar_unit": HEAT_CAPACITY_CP_UNIT,
        "temperature_k": 298.15,
    }
    base.update(overrides)
    return base


class TestExperimentalCpPointContract:
    """Phase C-E1: the previously-lossy experimental Cp(T) point.

    This is the first regression test of the new observation contract --
    what a bare ``property_kind=other`` row used to lose (a heat-capacity
    kind, a fixed unit, a state basis, pressure, and a typed uncertainty)
    is now expressible and validated.
    """

    def test_honest_cp_point_with_expanded_uncertainty_accepted(self):
        m = MolecularPropertyObservationCreate(
            **_cp_kwargs(
                pressure_bar=1.01325,
                state_basis=ObservedStateBasis.ideal_gas,
                scalar_uncertainty=0.5,
                uncertainty_kind=ObservedUncertaintyKind.expanded,
                uncertainty_coverage_factor=2.0,
                uncertainty_level_of_confidence_pct=95.0,
                uncertainty_assessor=ObservedUncertaintyAssessor.source_author,
            )
        )
        assert m.property_kind == MolecularPropertyKind.heat_capacity_cp
        assert m.scalar_unit == "J/mol/K"
        assert m.state_basis == ObservedStateBasis.ideal_gas
        assert m.uncertainty_kind == ObservedUncertaintyKind.expanded
        assert m.uncertainty_coverage_factor == pytest.approx(2.0)

    def test_cp_kind_requires_j_mol_k_unit(self):
        with pytest.raises(ValidationError) as exc:
            MolecularPropertyObservationCreate(
                **_cp_kwargs(scalar_unit="J/K/mol")
            )
        assert "scalar_unit must be" in str(exc.value)

    def test_cp_kind_requires_temperature(self):
        with pytest.raises(ValidationError) as exc:
            MolecularPropertyObservationCreate(
                **_cp_kwargs(temperature_k=None)
            )
        assert "temperature_k is required" in str(exc.value)


class TestUncertaintyKindIffValueForCp:
    def test_value_without_kind_rejected(self):
        with pytest.raises(ValidationError) as exc:
            MolecularPropertyObservationCreate(
                **_cp_kwargs(scalar_uncertainty=0.5)
            )
        assert "must be both set or both absent" in str(exc.value)

    def test_kind_without_value_rejected(self):
        with pytest.raises(ValidationError) as exc:
            MolecularPropertyObservationCreate(
                **_cp_kwargs(uncertainty_kind=ObservedUncertaintyKind.standard)
            )
        assert "must be both set or both absent" in str(exc.value)

    def test_both_absent_accepted(self):
        m = MolecularPropertyObservationCreate(**_cp_kwargs())
        assert m.scalar_uncertainty is None
        assert m.uncertainty_kind is None

    def test_non_cp_kind_may_carry_bare_uncertainty(self):
        """CCCBDB-style rows (no uncertainty kind) stay valid for every
        kind except heat_capacity_cp -- the DB CHECK is scoped the same
        way, so a pre-existing CCCBDB row is never rejected by this rule.
        """
        m = MolecularPropertyObservationCreate(
            scientific_origin=ScientificOriginKind.experimental,
            property_kind=MolecularPropertyKind.ionization_energy,
            scalar_value=9.0,
            scalar_unit="eV",
            scalar_uncertainty=0.1,
        )
        assert m.scalar_uncertainty == pytest.approx(0.1)
        assert m.uncertainty_kind is None


class TestCoverageFactorOnlyWithExpandedKind:
    def test_coverage_factor_with_standard_kind_rejected(self):
        with pytest.raises(ValidationError) as exc:
            MolecularPropertyObservationCreate(
                **_cp_kwargs(
                    scalar_uncertainty=0.5,
                    uncertainty_kind=ObservedUncertaintyKind.standard,
                    uncertainty_coverage_factor=2.0,
                )
            )
        assert "expanded or combined_expanded" in str(exc.value)

    def test_coverage_factor_with_combined_expanded_kind_accepted(self):
        m = MolecularPropertyObservationCreate(
            **_cp_kwargs(
                scalar_uncertainty=0.5,
                uncertainty_kind=ObservedUncertaintyKind.combined_expanded,
                uncertainty_coverage_factor=2.0,
            )
        )
        assert m.uncertainty_coverage_factor == pytest.approx(2.0)

    def test_coverage_factor_below_one_rejected(self):
        with pytest.raises(ValidationError):
            MolecularPropertyObservationCreate(
                **_cp_kwargs(
                    scalar_uncertainty=0.5,
                    uncertainty_kind=ObservedUncertaintyKind.expanded,
                    uncertainty_coverage_factor=0.5,
                )
            )

    def test_confidence_pct_out_of_range_rejected(self):
        with pytest.raises(ValidationError):
            MolecularPropertyObservationCreate(
                **_cp_kwargs(uncertainty_level_of_confidence_pct=0.0)
            )
        with pytest.raises(ValidationError):
            MolecularPropertyObservationCreate(
                **_cp_kwargs(uncertainty_level_of_confidence_pct=101.0)
            )


class TestPressureAndStateBasis:
    def test_non_positive_pressure_rejected(self):
        with pytest.raises(ValidationError):
            MolecularPropertyObservationCreate(**_cp_kwargs(pressure_bar=0.0))

    def test_real_gas_state_basis_accepted(self):
        m = MolecularPropertyObservationCreate(
            **_cp_kwargs(state_basis=ObservedStateBasis.real_gas)
        )
        assert m.state_basis == ObservedStateBasis.real_gas
