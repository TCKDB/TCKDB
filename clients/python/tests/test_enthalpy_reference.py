"""Builders refuse undeclared enthalpy before any HTTP request."""

import pytest

from tckdb_client.builders.thermo import Thermo
from tckdb_client.builders.validation import TCKDBBuilderValidationError

REFERENCE = "formation_298k"


@pytest.mark.parametrize("kwargs", [
    {"h298_kj_mol": 0},
    {"nasa_block": {"t_low": 200, "t_mid": 1000, "t_high": 3000,
                    **{f"{p}{i}": 1 for p in ("a", "b") for i in range(1, 8)}}},
    {"point_table": [{"temperature_k": 400, "h_kj_mol": 0}]},
])
def test_builder_refuses_missing_reference(kwargs):
    with pytest.raises(TCKDBBuilderValidationError, match="enthalpy_declaration_absent"):
        Thermo(**kwargs)
    builder = Thermo(**kwargs, enthalpy_reference_kind=REFERENCE)
    assert builder.enthalpy_reference_kind == REFERENCE


def test_builder_refuses_unused_reference():
    with pytest.raises(TCKDBBuilderValidationError, match="enthalpy_declaration_without_content"):
        Thermo.scalar(s298_j_mol_k=20, enthalpy_reference_kind=REFERENCE)


def test_builder_refuses_nonformation_quantity():
    with pytest.raises(TCKDBBuilderValidationError, match="molecular_property_observation"):
        Thermo.scalar(h298_kj_mol=20, enthalpy_reference_kind="sensible_increment")


def test_builder_flags_a_typo_of_the_legal_value_distinctly():
    """A wrongly-capitalised near-miss is a typo, not a different quantity
    -- it must not get the same 'go elsewhere' refusal as a genuinely
    unsupported reference kind (see test_builder_refuses_nonformation_quantity)."""
    with pytest.raises(TCKDBBuilderValidationError, match="enthalpy_reference_kind_unrecognized"):
        Thermo.scalar(h298_kj_mol=20, enthalpy_reference_kind="Formation_298k")
