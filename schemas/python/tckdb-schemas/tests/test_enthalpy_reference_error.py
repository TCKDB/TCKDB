"""``enthalpy_reference_error``: which deposits must declare their enthalpy zero.

Focus: a tabulated Gibbs energy (``points[*].g_kj_mol``) is enthalpy
content. It is stored as H(T) - T*S(T) on the record's enthalpy zero, so it
carries H's reference exactly as a point H does. Every case pins the exact
code; the codes are literals, not the module's constants, so renaming a
constant's value goes red too.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from tckdb_schemas.enthalpy_reference import enthalpy_reference_error

REFERENCE = "formation_298k"
ABSENT = "enthalpy_declaration_absent"
WITHOUT_CONTENT = "enthalpy_declaration_without_content"


def _code(payload):
    error = enthalpy_reference_error(payload)
    return None if error is None else error[0]


G_ONLY = {"points": [{"temperature_k": 300.0, "g_kj_mol": -298.5}]}
G_AND_S = {"points": [{"temperature_k": 300.0, "s_j_mol_k": 188.9, "g_kj_mol": -298.5}]}
G_ON_ONE_POINT_OF_SEVERAL = {"points": [
    {"temperature_k": 300.0, "cp_j_mol_k": 33.6, "s_j_mol_k": 188.9},
    {"temperature_k": 500.0, "g_kj_mol": -338.0},
]}
G_ZERO = {"points": [{"temperature_k": 300.0, "g_kj_mol": 0.0}]}


@pytest.mark.parametrize(
    "content",
    [G_ONLY, G_AND_S, G_ON_ONE_POINT_OF_SEVERAL, G_ZERO],
    ids=["g_only", "g_and_s", "g_on_one_point_of_several", "g_exactly_zero"],
)
def test_point_gibbs_without_declaration_is_absent(content) -> None:
    assert _code(content) == ABSENT


@pytest.mark.parametrize(
    "content",
    [G_ONLY, G_AND_S, G_ON_ONE_POINT_OF_SEVERAL, G_ZERO],
    ids=["g_only", "g_and_s", "g_on_one_point_of_several", "g_exactly_zero"],
)
def test_point_gibbs_with_declaration_is_coherent(content) -> None:
    assert _code({**content, "enthalpy_reference_kind": REFERENCE}) is None


def test_point_gibbs_counts_on_attribute_payloads_too() -> None:
    """The server passes pydantic models, not dicts; ``get`` must read both."""
    point = SimpleNamespace(temperature_k=300.0, h_kj_mol=None, g_kj_mol=-298.5)
    undeclared = SimpleNamespace(points=[point], enthalpy_reference_kind=None)
    declared = SimpleNamespace(points=[point], enthalpy_reference_kind=REFERENCE)
    assert _code(undeclared) == ABSENT
    assert _code(declared) is None


@pytest.mark.parametrize(
    "content",
    [
        {"points": [{"temperature_k": 300.0, "cp_j_mol_k": 33.6, "s_j_mol_k": 188.9}]},
        {"points": [{"temperature_k": 300.0, "g_kj_mol": None, "s_j_mol_k": 188.9}]},
        {"s298_j_mol_k": 188.9},
        {"points": []},
    ],
    ids=["cp_s_points", "explicit_null_g", "s298_only", "empty_points"],
)
def test_no_h_and_no_g_is_still_without_content(content) -> None:
    assert _code(content) is None
    assert _code({**content, "enthalpy_reference_kind": REFERENCE}) == WITHOUT_CONTENT


def test_messages_are_unchanged_for_gibbs_content() -> None:
    """Counting G changes which deposits trip the refusals, not what they say."""
    assert enthalpy_reference_error(G_ONLY) == (
        ABSENT,
        "Enthalpy content requires enthalpy_reference_kind. Declare "
        "formation_298k only when the source states that convention; "
        "other enthalpy quantities belong in molecular_property_observation.",
    )
