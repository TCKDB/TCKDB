"""``LevelOfTheoryRef.method`` guards at the wire (ADR 0021)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.fragments.refs import (
    LEVEL_OF_THEORY_METHOD_IS_COMPOUND,
    W_LEVEL_OF_THEORY_METHOD_NAMES_CORRECTION_TABLE,
    LevelOfTheoryRef,
    SoftwareReleaseRef,
    collect_ref_warnings,
    collect_software_release_version_warnings,
    correction_table_method_stem,
)


@pytest.mark.parametrize(
    "method",
    ["ccsd(t)-f12//b3lyp", "x//y", "b3lyp//", "//b3lyp", " a // b "],
)
def test_a_compound_method_is_refused_with_a_code(method):
    with pytest.raises(ValidationError) as err:
        LevelOfTheoryRef(method=method, basis="def2-tzvp")
    original = err.value.errors()[0]["ctx"]["error"]
    assert isinstance(original, CodedValidationError)
    assert original.code == LEVEL_OF_THEORY_METHOD_IS_COMPOUND == "level_of_theory_method_is_compound"
    assert original.context["field"] == "method"
    assert "separate calculations" in original.detail
    assert "single-point" in original.detail and "optimization" in original.detail


@pytest.mark.parametrize(
    ("method", "named"),
    [
        ("G3//B3LYP", "G3B3"),
        ("g3//b3lyp", "G3B3"),
        ("G3 // B3", "G3B3"),
        ("G3//B3LYP/6-31G(d)", "G3B3"),
        ("G3(MP2)//B3LYP", "G3MP2B3"),
        ("g3mp2//b3lyp", "G3MP2B3"),
        ("G3(MP2)//B3", "G3MP2B3"),
    ],
)
def test_the_literature_names_of_g3b3_refuse_with_advice_naming_the_method(method, named):
    """``G3//B3LYP`` is one recipe, so "send two calculations" would be wrong advice."""
    with pytest.raises(ValidationError) as err:
        LevelOfTheoryRef(method=method)
    original = err.value.errors()[0]["ctx"]["error"]
    assert original.code == "level_of_theory_method_is_compound"
    assert original.context["named_method"] == named
    assert f"method={named!r}" in original.detail
    assert "one recipe" in original.detail


@pytest.mark.parametrize("method", ["G4//B3LYP", "G3//MP2", "CCSD(T)//G3", "G3//B3LYP//x"])
def test_other_pairs_keep_the_generic_advice_and_no_named_method(method):
    with pytest.raises(ValidationError) as err:
        LevelOfTheoryRef(method=method)
    original = err.value.errors()[0]["ctx"]["error"]
    assert "named_method" not in original.context
    assert "separate calculations" in original.detail


@pytest.mark.parametrize("method", ["b3lyp", "b3lyp/6-31g", "wB97X-D3(BJ)", "CBS-QB3", "a/b"])
def test_a_method_without_a_double_slash_is_accepted(method):
    assert LevelOfTheoryRef(method=method).method == method


@pytest.mark.parametrize(
    ("method", "stem"),
    [
        ("cbs-qb3-paraskevas", "cbs-qb3"),
        ("CBS-QB3-Paraskevas", "cbs-qb3"),
        ("cbsqb3-paraskevas", "cbsqb3"),
        ("cbsqb32023", "cbsqb3"),
        ("cbs-qb3-2023", "cbs-qb3"),
        ("CBS-QB3 2023", None),
        ("rocbs-qb3-paraskevas", "rocbs-qb3"),
        ("g4mp22019", "g4mp2"),
        ("g4(mp2)-2019", "g4(mp2)"),
        ("g3mp2b32008", "g3mp2b3"),
        ("w1bd-2009", "w1bd"),
        ("w1u2009", "w1u"),
        ("cbs-qb3", None),
        ("cbsqb3", None),
        ("g4", None),
        ("g4mp2", None),
        ("w1bd", None),
        ("w1-bd", None),
        ("b3lyp-2023", "b3lyp"),
        ("wb97xd", None),
        ("cbs-qb3-d3bj", None),
        ("cbs-qb3-paraskevas-extra", None),
        ("cbs-qb3-20234", None),
        # Arkane strips hyphens: the RMG-database key is "cbsqb3paraskevas".
        ("cbsqb3paraskevas", "cbsqb3"),
        ("CBSQB3Paraskevas", "cbsqb3"),
        # Year suffixes on ordinary methods (ADR decision 12).
        ("b3lyp2023", "b3lyp"),
        ("b2plypd32023", "b2plypd3"),
        ("dlpnoccsd(t)f122023", "dlpnoccsd(t)f12"),
        ("m062x-2021", "m062x"),
        ("wb97x-d32023", "wb97x-d3"),
        # Unbalanced parentheses never yield a stem that does not exist.
        ("g3(mp22023", None),
        ("g3(mp2)2023", "g3(mp2)"),
        ("g3(mp2)b32008", "g3(mp2)b3"),
        ("g4(mp22019", None),
        # Real names stay silent.
        ("b2plyp", None),
        ("w1", None),
        ("m062x", None),
        ("wb97x-d3", None),
        ("pbe0", None),
        ("g2000", None),
        ("w2-2", None),
        ("g4mp2-6x", None),
        ("b3lyp-d3bj", None),
        ("pbe0-d3bj", None),
        ("ccsd(t)-f12", None),
        ("dlpno-ccsd(t)-f12", None),
    ],
)
def test_correction_table_stem(method, stem):
    assert correction_table_method_stem(method) == stem


def test_a_correction_table_name_warns_and_is_not_rewritten():
    ref = LevelOfTheoryRef(method="CBS-QB3-Paraskevas")
    assert ref.method == "CBS-QB3-Paraskevas"  # never aliased or rewritten
    warning = ref.method_warning("calculation.level_of_theory.")
    assert warning is not None
    assert warning.code == W_LEVEL_OF_THEORY_METHOD_NAMES_CORRECTION_TABLE
    assert warning.field == "calculation.level_of_theory.method"
    assert "cbs-qb3" in warning.message


def test_an_ordinary_method_has_no_warning():
    assert LevelOfTheoryRef(method="CBS-QB3").method_warning() is None
    assert LevelOfTheoryRef(method="b3lyp").method_warning() is None


class _Calc(BaseModel):
    level_of_theory: LevelOfTheoryRef
    software_release: SoftwareReleaseRef | None = None


class _Request(BaseModel):
    calculations: list[_Calc]
    by_key: dict[str, _Calc] = {}


def test_collect_ref_warnings_walks_nested_lists_and_dicts():
    request = _Request(
        calculations=[
            _Calc(level_of_theory=LevelOfTheoryRef(method="b3lyp")),
            _Calc(
                level_of_theory=LevelOfTheoryRef(method="cbsqb32023"),
                software_release=SoftwareReleaseRef(name="gaussian", version="Gaussian 09, Revision D.01"),
            ),
        ],
        by_key={"k": _Calc(level_of_theory=LevelOfTheoryRef(method="cbs-qb3-paraskevas"))},
    )
    warnings = collect_ref_warnings(request)
    assert sorted((w.field, w.code) for w in warnings) == [
        ("by_key['k'].level_of_theory.method", "level_of_theory_method_names_correction_table"),
        ("calculations[1].level_of_theory.method", "level_of_theory_method_names_correction_table"),
        ("calculations[1].software_release.version", "software_release_version_is_composite"),
    ]


def test_the_software_only_collector_is_unchanged():
    request = _Request(calculations=[_Calc(level_of_theory=LevelOfTheoryRef(method="cbsqb32023"))])
    assert collect_software_release_version_warnings(request) == []
