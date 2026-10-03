"""Counting atoms in an XYZ block raises no coded refusal, and the contract tracer can see that (#623).

``atom_count_of_xyz`` used to call ``parse_xyz_elements`` and swallow the
``atom_map_geometry_unparseable`` refusal it raises. The producer contract's
tracer follows calls but cannot see a ``try``/``except``, so it listed that
code against every surface whose rules merely count atoms, and a call from the
one-atom-primary rule had to be hidden behind ``getattr`` to keep the code off
that rule. The counter now counts through :func:`xyz_block_shape`, which has no
refusal on any path, so a plain call is honest and the tracer agrees.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from tckdb_schemas.fragments.reaction_atom_map import (
    W_ATOM_MAP_GEOMETRY_UNPARSEABLE,
    parse_xyz_elements,
    xyz_block_shape,
)
from tckdb_schemas.frequency_completeness import atom_count_of_xyz
from tckdb_schemas.workflows.computed_species_upload import require_opt_primary_unless_monatomic

BACKEND_ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = BACKEND_ROOT / "scripts" / "generate_producer_contract.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_producer_contract", GENERATOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("generate_producer_contract", module)
    spec.loader.exec_module(module)
    return module


generator = _load_generator()

_WATER = "3\nw\nO 0 0 0.1\nH 0 0.7 -0.5\nH 0 -0.7 -0.5"

_UNCOUNTABLE = {
    "empty": "",
    "blank": "   \n  ",
    "header_not_an_integer": "three\nw\nO 0 0 0",
    "header_not_a_whole_number": "1.5\nw\nO 0 0 0",
    "header_larger_than_body": "2\nw\nO 0 0 0",
    "header_smaller_than_body": "1\nw\nO 0 0 0\nH 0 0 1",
    "negative_header": "-1\nw\nO 0 0 0",
}


def test_the_counter_reaches_no_coded_refusal_for_the_tracer():
    assert generator.refusal_site(atom_count_of_xyz) is None


def test_control_the_tracer_does_see_the_refusal_parse_xyz_elements_raises():
    """Guard the guard: the check above is vacuous if the tracer sees nothing anywhere."""
    site = generator.refusal_site(parse_xyz_elements)
    assert site is not None and site.endswith(":parse_xyz_elements"), site


def test_the_one_atom_primary_rule_traces_to_its_own_refusal_only():
    site = generator.refusal_site(require_opt_primary_unless_monatomic)
    assert site is not None and site.endswith(":require_opt_primary_unless_monatomic"), site


@pytest.mark.parametrize(
    ("xyz", "count"),
    [
        ("1\nH\nH 0 0 0", 1),
        ("1\nH\nH 1.0 0.0 0.0", 1),
        (_WATER, 3),
        (f"\n{_WATER}\n\n", 3),
        (None, None),
        *[(text, None) for text in _UNCOUNTABLE.values()],
    ],
    ids=["atom", "shifted_atom", "water", "padded_water", "none", *_UNCOUNTABLE],
)
def test_atom_count_of_xyz(xyz, count):
    assert atom_count_of_xyz(xyz) == count


@pytest.mark.parametrize("xyz", list(_UNCOUNTABLE.values()), ids=list(_UNCOUNTABLE))
def test_parse_xyz_elements_still_refuses_what_the_counter_declines(xyz):
    """The refusal itself is unchanged: same code, same ValueError family."""
    with pytest.raises(ValueError) as raised:
        parse_xyz_elements(xyz)
    assert getattr(raised.value, "code", None) == W_ATOM_MAP_GEOMETRY_UNPARSEABLE


def test_parse_xyz_elements_messages_are_unchanged():
    with pytest.raises(ValueError, match="geometry is not a valid XYZ block"):
        parse_xyz_elements("three\nw\nO 0 0 0")
    with pytest.raises(ValueError, match="geometry declares 2 atoms but carries 1 coordinate lines"):
        parse_xyz_elements("2\nw\nO 0 0 0")
    assert parse_xyz_elements(_WATER) == ["O", "H", "H"]
    assert parse_xyz_elements("1\nx\nCL 0 0 0") == ["Cl"]


def test_xyz_block_shape_never_raises_and_splits_header_from_body():
    assert xyz_block_shape(_WATER) == (3, ["O 0 0 0.1", "H 0 0.7 -0.5", "H 0 -0.7 -0.5"])
    assert xyz_block_shape("") == (None, [])
    assert xyz_block_shape("x\ny") == (None, [])
