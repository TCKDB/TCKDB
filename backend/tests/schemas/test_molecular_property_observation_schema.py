"""Schema-level tests for ``MolecularPropertyObservationCreate``.

Exercises the Pydantic validators independently of the CCCBDB builder.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.db.models.common import MolecularPropertyKind, ScientificOriginKind
from app.schemas.entities.molecular_property_observation import (
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
