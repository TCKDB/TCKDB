"""The ``composite_result`` wire block and its pairing with ``type: "composite"`` (ADR 0021, P3a).

Every payload shape that carries calculations is covered, because the pairing
rule is repeated per shape and a shape that forgot it would accept a composite
energy as an ``sp`` result (or a composite calculation with nothing in it).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.enums import CalculationType
from tckdb_schemas.fragments.calculation import (
    COMPOSITE_ARITHMETIC_TOLERANCE_HARTREE,
    CalculationWithResultsPayload,
    CompositeResultPayload,
)
from tckdb_schemas.shared.calculation_in import CalculationIn
from tckdb_schemas.workflows.computed_species_upload import (
    CalculationInBundle,
    require_opt_primary_unless_monatomic,
)

_ELECTRONIC = -76.25
_ZPE = 0.0625
_E0 = _ELECTRONIC + _ZPE

_COMMON = {
    "software_release": {"name": "Gaussian", "version": "16"},
    "level_of_theory": {"method": "CBS-QB3"},
}


def _result(**overrides) -> dict:
    block = {
        "assembly": "program_run",
        "electronic_energy_hartree": _ELECTRONIC,
        "e0_hartree": _E0,
        "recipe_zpe_hartree": _ZPE,
    }
    block.update(overrides)
    return block


def _coded(exc_info: pytest.ExceptionInfo[ValidationError]) -> CodedValidationError:
    original = exc_info.value.errors()[0]["ctx"]["error"]
    assert isinstance(original, CodedValidationError), original
    return original


def test_the_tolerance_is_one_micro_hartree():
    assert COMPOSITE_ARITHMETIC_TOLERANCE_HARTREE == 1e-6


# -- e0 = electronic + recipe ZPE ------------------------------------------


@pytest.mark.parametrize("offset", [0.0, 0.9e-6, -0.9e-6])
def test_an_e0_within_the_tolerance_passes(offset):
    CompositeResultPayload(**_result(e0_hartree=_E0 + offset))


@pytest.mark.parametrize("offset", [1.1e-6, -1.1e-6, 1e-3])
def test_an_e0_beyond_the_tolerance_blocks_with_its_code(offset):
    with pytest.raises(ValidationError) as err:
        CompositeResultPayload(**_result(e0_hartree=_E0 + offset))
    coded = _coded(err)
    assert coded.code == "composite_e0_inconsistent"
    assert coded.context["tolerance_hartree"] == 1e-6
    assert coded.context["difference_hartree"] == pytest.approx(offset, abs=1e-9)


@pytest.mark.parametrize("absent", ["electronic_energy_hartree", "e0_hartree", "recipe_zpe_hartree"])
def test_the_e0_check_needs_all_three_numbers(absent):
    """Applied only when e0, electronic and recipe ZPE are all present."""
    block = _result(e0_hartree=_E0 + 5.0)
    block[absent] = None
    CompositeResultPayload(**block)


# -- terms sum to the ZPE-free energy -------------------------------------


def _terms(total: float, split: float = -0.25):
    return [
        {"term_position": 0, "value_hartree": total - split},
        {"term_position": 1, "value_hartree": split},
    ]


@pytest.mark.parametrize("offset", [0.0, 0.9e-6, -0.9e-6])
def test_terms_within_the_tolerance_sum_to_the_total(offset):
    CompositeResultPayload(**_result(terms=_terms(_ELECTRONIC + offset)))


@pytest.mark.parametrize("offset", [1.1e-6, -1.1e-6, 0.5])
def test_terms_beyond_the_tolerance_block_with_their_code(offset):
    with pytest.raises(ValidationError) as err:
        CompositeResultPayload(**_result(terms=_terms(_ELECTRONIC + offset)))
    coded = _coded(err)
    assert coded.code == "composite_terms_do_not_sum"
    assert coded.context["tolerance_hartree"] == 1e-6


def test_the_terms_check_needs_the_total():
    CompositeResultPayload(**_result(electronic_energy_hartree=None, e0_hartree=None, terms=_terms(-5.0)))


def test_terms_are_optional():
    assert CompositeResultPayload(**_result()).terms == []


# -- the rest of the block -------------------------------------------------


def test_every_energy_may_be_unstated():
    block = CompositeResultPayload(assembly="program_run")
    assert (block.electronic_energy_hartree, block.e0_hartree, block.recipe_zpe_hartree) == (None, None, None)


def test_a_negative_recipe_zpe_is_refused():
    with pytest.raises(ValidationError):
        CompositeResultPayload(**_result(recipe_zpe_hartree=-0.01, e0_hartree=None))


def test_a_non_finite_energy_is_refused():
    with pytest.raises(ValidationError):
        CompositeResultPayload(**_result(electronic_energy_hartree=float("nan")))


def test_duplicate_term_positions_are_refused():
    with pytest.raises(ValidationError):
        CompositeResultPayload(
            **_result(
                electronic_energy_hartree=None,
                e0_hartree=None,
                terms=[{"term_position": 0, "value_hartree": -1.0}, {"term_position": 0, "value_hartree": -2.0}],
            )
        )


def test_the_assembled_form_parses_so_the_server_can_refuse_it_by_name():
    assert CompositeResultPayload(assembly="assembled").assembly.value == "assembled"


# -- type pairing, in every payload shape ---------------------------------


def _primitive(calc_type: str, **extra):
    return CalculationWithResultsPayload(type=calc_type, **_COMMON, **extra)


def _bundle(calc_type: str, **extra):
    return CalculationInBundle(key="k", type=calc_type, **_COMMON, **extra)


def _flat(calc_type: str, **extra):
    return CalculationIn(key="k", type=calc_type, **_COMMON, **extra)


SHAPES = pytest.mark.parametrize("build", [_primitive, _bundle, _flat], ids=["primitive", "bundle", "flat"])


@SHAPES
def test_a_composite_with_its_result_is_accepted(build):
    calc = build("composite", composite_result=_result())
    assert calc.type is CalculationType.composite
    assert calc.composite_result is not None


@SHAPES
def test_a_composite_without_its_result_is_refused_with_its_code(build):
    with pytest.raises(ValidationError) as err:
        build("composite")
    assert _coded(err).code == "composite_type_requires_composite_result"


@SHAPES
@pytest.mark.parametrize("other", ["opt", "sp", "freq"])
def test_a_composite_result_on_another_type_is_refused_with_its_code(build, other):
    with pytest.raises(ValidationError) as err:
        build(other, composite_result=_result())
    coded = _coded(err)
    assert coded.code == "composite_result_requires_composite_type"
    assert coded.context["calculation_type"] == other


@pytest.mark.parametrize("build", [_primitive, _bundle], ids=["primitive", "bundle"])
def test_another_result_block_on_a_composite_is_refused(build):
    with pytest.raises(ValidationError):
        build("composite", composite_result=_result(), sp_result={"electronic_energy_hartree": -1.0})


# -- the conformer primary -------------------------------------------------


@pytest.mark.parametrize("calc_type", [CalculationType.opt, CalculationType.composite])
def test_a_conformer_primary_may_be_an_opt_or_a_composite(calc_type):
    require_opt_primary_unless_monatomic(calc_type, "3\nw\nO 0 0 0\nH 0 0 1\nH 0 1 0", subject="primary")


@pytest.mark.parametrize("calc_type", [CalculationType.freq, CalculationType.scan, CalculationType.sp])
def test_a_conformer_primary_of_any_other_type_is_still_refused(calc_type):
    with pytest.raises(ValueError, match="must be 'opt'"):
        require_opt_primary_unless_monatomic(calc_type, "3\nw\nO 0 0 0\nH 0 0 1\nH 0 1 0", subject="primary")
