"""The depositor-side builders fill in the enthalpy declaration when it is omitted.

The server stays strict (``enthalpy_declaration_absent``); the builders default
the declaration so the payload still carries an explicit claim. Only an
*omitted* argument triggers the default: an explicit value, including an
explicit ``None``, is passed through unchanged.
"""

import pytest

from tckdb_client.builders.thermo import Thermo
from tckdb_client.builders.validation import TCKDBBuilderValidationError

REFERENCE = "formation_298k"
COEFFS = [1.0] * 7
NASA_ARGS = {"coeffs_low": COEFFS, "coeffs_high": COEFFS, "t_low": 200, "t_mid": 1000, "t_high": 3000}

# Each enthalpy content form the builders can express, built through its factory.
ENTHALPY_FORMS = {
    "scalar_h298": lambda **kw: Thermo.scalar(h298_kj_mol=-241.8, **kw),
    "nasa": lambda **kw: Thermo.nasa(**NASA_ARGS, **kw),
    "points_h": lambda **kw: Thermo.points([{"temperature_k": 400, "h_kj_mol": -74.5}], **kw),
    "points_g": lambda **kw: Thermo.points([{"temperature_k": 300, "g_kj_mol": -298.5}], **kw),
}
# Forms with no enthalpy content.
NO_ENTHALPY_FORMS = {
    "scalar_s298": lambda **kw: Thermo.scalar(s298_j_mol_k=188.8, **kw),
    "scalar_0k_only": lambda **kw: Thermo.scalar(enthalpy_formation_0k_kj_mol=-238.9, **kw),
    "points_cp_s": lambda **kw: Thermo.points(
        [{"temperature_k": 400, "cp_j_mol_k": 35.0, "s_j_mol_k": 200.0}], **kw),
}

with_enthalpy = pytest.mark.parametrize("make", list(ENTHALPY_FORMS.values()), ids=list(ENTHALPY_FORMS))
without_enthalpy = pytest.mark.parametrize("make", list(NO_ENTHALPY_FORMS.values()), ids=list(NO_ENTHALPY_FORMS))


@with_enthalpy
def test_omitted_argument_defaults_to_formation_298k(make):
    builder = make()
    assert builder.enthalpy_reference_kind == REFERENCE
    assert builder.to_payload()["enthalpy_reference_kind"] == REFERENCE


@with_enthalpy
def test_explicit_value_is_kept(make):
    assert make(enthalpy_reference_kind=REFERENCE).to_payload()["enthalpy_reference_kind"] == REFERENCE
    with pytest.raises(TCKDBBuilderValidationError, match="enthalpy_quantity_not_storable_here"):
        make(enthalpy_reference_kind="sensible_increment")
    with pytest.raises(TCKDBBuilderValidationError, match="enthalpy_reference_kind_unrecognized"):
        make(enthalpy_reference_kind="Formation_298k")


@with_enthalpy
def test_explicit_none_stays_none_and_is_refused(make):
    with pytest.raises(TCKDBBuilderValidationError, match="enthalpy_declaration_absent"):
        make(enthalpy_reference_kind=None)


@without_enthalpy
def test_no_enthalpy_content_gets_no_default(make):
    builder = make()
    assert builder.enthalpy_reference_kind is None
    assert builder.to_payload().get("enthalpy_reference_kind") is None
    assert make(enthalpy_reference_kind=None).enthalpy_reference_kind is None
    with pytest.raises(TCKDBBuilderValidationError, match="enthalpy_declaration_without_content"):
        make(enthalpy_reference_kind=REFERENCE)
