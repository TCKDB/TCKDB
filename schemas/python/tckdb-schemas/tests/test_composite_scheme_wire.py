"""The user-built composite scheme on the wire (ADR 0021, P5).

Covers ``LevelOfTheoryRef.composite_scheme``, the shape rules behind it, the
assembled ``composite_result.inputs`` and the software rule, on every payload
shape that carries calculations. Each refusal is asserted by its ``code`` (never
a substring of prose), and every rule is also run on an object built with
``model_construct`` -- the wire models' own validators do not run there, and the
backend relies on the same functions to refuse it at the write.
"""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.composite_scheme_rules import (
    assert_composite_scheme_definition,
    match_inputs_to_definition,
)
from tckdb_schemas.fragments.calculation import (
    CalculationWithResultsPayload,
    CompositeInputPayload,
    CompositeResultPayload,
    assert_composite_calculation_shape,
)
from tckdb_schemas.fragments.refs import (
    CompositeSchemeDefinition,
    CompositeSchemeInputLevel,
    LevelOfTheoryRef,
    collect_ref_warnings,
)
from tckdb_schemas.shared.calculation_in import CalculationIn
from tckdb_schemas.workflows.computed_species_upload import CalculationDependencyInBundle, CalculationInBundle

TZ = {"method": "CCSD(T)", "basis": "cc-pVTZ"}
QZ = {"method": "CCSD(T)", "basis": "cc-pVQZ"}

#: Worked payload (b): CCSD(T)/CBS from a TZ/QZ pair, reference at QZ, correlation by x^-3.
SCHEME_B = {
    "kind": "extrapolation",
    "terms": [
        {
            "key": "ref",
            "operation": "value",
            "energy_component": "reference",
            "inputs": [{"slot": "value", "level_of_theory": QZ}],
        },
        {
            "key": "corr",
            "operation": "extrapolation",
            "energy_component": "correlation",
            "formula": "inverse_power",
            "exponent": 3,
            "inputs": [
                {"slot": "cardinal", "cardinal_number": 3, "level_of_theory": TZ},
                {"slot": "cardinal", "cardinal_number": 4, "level_of_theory": QZ},
            ],
        },
    ],
}

FC = {"method": "CCSD(T)", "basis": "cc-pCVTZ", "core_treatment": "frozen_core"}
AE = {"method": "CCSD(T)", "basis": "cc-pCVTZ", "core_treatment": "all_electron"}

#: Worked payload (c): focal point, a base plus core-valence, higher-order, relativistic and DBOC terms.
SCHEME_C = {
    "kind": "additive",
    "terms": [
        {
            "key": "base",
            "operation": "base",
            "energy_component": "total",
            "inputs": [{"slot": "value", "level_of_theory": QZ}],
        },
        {
            "key": "dcv",
            "operation": "difference",
            "energy_component": "total",
            "inputs": [{"slot": "high", "level_of_theory": AE}, {"slot": "low", "level_of_theory": FC}],
        },
        {
            "key": "dtq",
            "operation": "difference",
            "energy_component": "total",
            "inputs": [
                {"slot": "high", "level_of_theory": {"method": "CCSDT(Q)", "basis": "cc-pVDZ"}},
                {"slot": "low", "level_of_theory": {"method": "CCSD(T)", "basis": "cc-pVDZ"}},
            ],
        },
        {
            "key": "drel",
            "operation": "difference",
            "energy_component": "total",
            "inputs": [
                {"slot": "high", "level_of_theory": {"method": "CCSD(T)", "basis": "cc-pVTZ-DK", "keywords": "DKH2"}},
                {"slot": "low", "level_of_theory": {"method": "CCSD(T)", "basis": "cc-pVTZ"}},
            ],
        },
        {
            "key": "dboc",
            "operation": "value",
            "energy_component": "dboc",
            "inputs": [{"slot": "value", "level_of_theory": {"method": "HF", "basis": "cc-pVDZ"}}],
        },
    ],
}


def _scheme(base: dict = SCHEME_B, **changes) -> dict:
    out = copy.deepcopy(base)
    out.update(changes)
    return out


def _term(scheme: dict, key: str) -> dict:
    return next(t for t in scheme["terms"] if t["key"] == key)


def _code(exc: pytest.ExceptionInfo) -> CodedValidationError:
    if isinstance(exc.value, ValidationError):
        error = exc.value.errors()[0]["ctx"]["error"]
        assert isinstance(error, CodedValidationError), error
        return error
    assert isinstance(exc.value, CodedValidationError), exc.value
    return exc.value


# ---------------------------------------------------------------------------
# method XOR composite_scheme
# ---------------------------------------------------------------------------


def test_the_two_worked_schemes_are_accepted():
    for scheme in (SCHEME_B, SCHEME_C):
        ref = LevelOfTheoryRef(composite_scheme=scheme)
        assert ref.method is None
        assert ref.composite_scheme is not None
    # An ordinary level is exactly what it was.
    assert LevelOfTheoryRef(method="b3lyp", basis="def2-tzvp").composite_scheme is None


def test_neither_method_nor_scheme_is_refused():
    with pytest.raises(ValidationError) as exc:
        LevelOfTheoryRef()
    assert _code(exc).code == "level_of_theory_requires_method_or_composite_scheme"


@pytest.mark.parametrize("method", ["CBS-QB3", "b3lyp"])
def test_a_method_with_an_inline_definition_is_refused_named_composite_or_not(method):
    with pytest.raises(ValidationError) as exc:
        LevelOfTheoryRef(method=method, composite_scheme=SCHEME_B)
    error = _code(exc)
    assert error.code == "level_of_theory_method_with_composite_scheme"
    assert error.context["method"] == method


def test_method_level_fields_next_to_a_scheme_are_refused():
    with pytest.raises(ValidationError) as exc:
        LevelOfTheoryRef(composite_scheme=SCHEME_B, basis="cc-pVQZ")
    error = _code(exc)
    assert error.code == "composite_scheme_malformed"
    assert error.context["rule"] == "ordinary_fields_with_scheme"


def test_the_named_method_kind_is_never_user_sent():
    with pytest.raises(ValidationError) as exc:
        LevelOfTheoryRef(composite_scheme=_scheme(kind="named_method"))
    assert _code(exc).code == "composite_scheme_named_method_not_sendable"


# ---------------------------------------------------------------------------
# nesting
# ---------------------------------------------------------------------------


def test_an_input_level_that_carries_its_own_scheme_is_a_nested_composite_and_is_refused():
    nested = _scheme()
    nested["terms"][0]["inputs"][0]["level_of_theory"] = {"composite_scheme": SCHEME_B}
    with pytest.raises(ValidationError) as exc:
        LevelOfTheoryRef(composite_scheme=nested)
    assert _code(exc).code == "composite_scheme_nested"


def test_an_input_level_type_cannot_hold_a_scheme_at_all():
    with pytest.raises(ValidationError) as exc:
        CompositeSchemeInputLevel(method="x", composite_scheme=SCHEME_B)
    assert _code(exc).code == "composite_scheme_nested"


# ---------------------------------------------------------------------------
# shape rules: each one, on the model and on a model_construct object
# ---------------------------------------------------------------------------


def _drop_exponent(s):
    del _term(s, "corr")["exponent"]


def _km_with_exponent(s):
    corr = _term(s, "corr")
    corr["formula"], corr["energy_component"], corr["exponent"] = "karton_martin_scf", "reference", 3


def _negative_exponent(s):
    _term(s, "corr")["exponent"] = -1


def _duplicate_cardinals(s):
    _term(s, "corr")["inputs"][1]["cardinal_number"] = 3


def _missing_cardinal(s):
    del _term(s, "corr")["inputs"][1]["cardinal_number"]


def _one_point(s):
    _term(s, "corr")["inputs"].pop()


def _three_point_gap(s):
    corr = _term(s, "corr")
    corr["formula"] = "exponential_three_point"
    del corr["exponent"]
    corr["inputs"] = [
        {"slot": "cardinal", "cardinal_number": n, "level_of_theory": TZ} for n in (2, 3, 5)
    ]


def _formula_on_value(s):
    _term(s, "ref")["formula"] = "inverse_power"


def _wrong_slot(s):
    _term(s, "ref")["inputs"][0]["slot"] = "high"


def _empirical(s):
    _term(s, "ref")["operation"] = "empirical"


def _kind_mismatch(s):
    s["kind"] = "additive"  # SCHEME_B has no difference term


def _duplicate_key(s):
    _term(s, "corr")["key"] = "ref"


def _km_on_correlation(s):
    corr = _term(s, "corr")
    corr["formula"] = "karton_martin_scf"
    del corr["exponent"]


MALFORMED = [
    (_drop_exponent, "exponent_required"),
    (_km_with_exponent, "exponent_forbidden"),
    (_negative_exponent, "exponent_invalid"),
    (_duplicate_cardinals, "cardinal_duplicate"),
    (_missing_cardinal, "cardinal_required"),
    (_one_point, "cardinal_count"),
    (_three_point_gap, "cardinals_not_consecutive"),
    (_formula_on_value, "formula_on_non_extrapolation"),
    (_wrong_slot, "slot_mismatch"),
    (_empirical, "empirical_not_user_definable"),
    (_kind_mismatch, "kind_operation_mismatch"),
    (_duplicate_key, "term_key_duplicate"),
    (_km_on_correlation, "component_mismatch"),
]


@pytest.mark.parametrize(("mutate", "rule"), MALFORMED, ids=[r for _, r in MALFORMED])
def test_each_malformed_definition_is_refused_with_its_rule(mutate, rule):
    scheme = _scheme()
    mutate(scheme)
    with pytest.raises(ValidationError) as exc:
        CompositeSchemeDefinition(**scheme)
    error = _code(exc)
    assert error.code == "composite_scheme_malformed"
    assert error.context["rule"] == rule


@pytest.mark.parametrize(("mutate", "rule"), MALFORMED, ids=[r for _, r in MALFORMED])
def test_the_same_rules_refuse_an_object_the_models_never_validated(mutate, rule):
    """``model_construct`` skips every validator: the shared function is what the backend re-runs."""
    scheme = _scheme()
    mutate(scheme)
    # Build the tree without validation by constructing every level bottom-up.
    terms = []
    for term in scheme["terms"]:
        inputs = [
            type("Input", (), {
                "slot": i["slot"],
                "cardinal_number": i.get("cardinal_number"),
                "level_of_theory": i["level_of_theory"],
            })()
            for i in term["inputs"]
        ]
        terms.append(
            type("Term", (), {
                "key": term["key"],
                "operation": term["operation"],
                "energy_component": term["energy_component"],
                "formula": term.get("formula"),
                "exponent": term.get("exponent"),
                "inputs": inputs,
            })()
        )
    definition = type("Definition", (), {"kind": scheme["kind"], "terms": terms})()
    with pytest.raises(CodedValidationError) as exc:
        assert_composite_scheme_definition(definition)
    assert exc.value.code == "composite_scheme_malformed"
    assert exc.value.context["rule"] == rule


def test_the_difference_slots_must_be_one_high_and_one_low():
    scheme = _scheme(SCHEME_C)
    _term(scheme, "dcv")["inputs"][1]["slot"] = "high"
    with pytest.raises(ValidationError) as exc:
        CompositeSchemeDefinition(**scheme)
    assert _code(exc).context["rule"] == "slot_mismatch"


def test_an_additive_scheme_without_a_difference_is_refused():
    scheme = _scheme(SCHEME_C)
    scheme["terms"] = [t for t in scheme["terms"] if t["operation"] != "difference"]
    with pytest.raises(ValidationError) as exc:
        CompositeSchemeDefinition(**scheme)
    assert _code(exc).context["rule"] == "kind_operation_mismatch"


def test_a_correction_table_method_in_an_input_level_still_warns_with_its_path():
    scheme = _scheme()
    scheme["terms"][0]["inputs"][0]["level_of_theory"] = {"method": "cbs-qb3-paraskevas"}
    ref = LevelOfTheoryRef(composite_scheme=scheme)

    from pydantic import BaseModel

    class Root(BaseModel):
        level_of_theory: LevelOfTheoryRef

    warnings = collect_ref_warnings(Root(level_of_theory=ref))
    assert [w.code for w in warnings] == ["level_of_theory_method_names_correction_table"]
    assert "composite_scheme.terms[0].inputs[0].level_of_theory.method" in warnings[0].field


# ---------------------------------------------------------------------------
# assembled composite_result.inputs
# ---------------------------------------------------------------------------

SOFTWARE = {"name": "orca", "version": "6.0.1"}


def _inputs(**overrides) -> list[dict]:
    rows = [
        {"term_key": "ref", "slot": "value", "calculation_key": "spq"},
        {"term_key": "corr", "slot": "cardinal", "cardinal_number": 3, "calculation_key": "spt"},
        {"term_key": "corr", "slot": "cardinal", "cardinal_number": 4, "calculation_key": "spq"},
    ]
    return rows


def _assembled(**result) -> dict:
    block = {"assembly": "assembled", "electronic_energy_hartree": -76.374, "inputs": _inputs()}
    block.update(result)
    return block


def _composite_calc(shape: str, *, scheme=SCHEME_B, software=None, result=None, **extra) -> dict:
    calc: dict = {
        "type": "composite",
        "level_of_theory": {"composite_scheme": scheme},
        "composite_result": result if result is not None else _assembled(),
    }
    if software is not None:
        calc["software_release"] = software
    if shape in ("bundle", "calculation_in"):
        calc["key"] = "cbs"
    calc.update(extra)
    return calc


SHAPES = {
    "bundle": CalculationInBundle,
    "calculation_in": CalculationIn,
    "primitive": CalculationWithResultsPayload,
}


@pytest.mark.parametrize("shape", SHAPES)
def test_an_assembled_composite_needs_no_software_on_any_calculation_shape(shape):
    calc = SHAPES[shape](**_composite_calc(shape))
    assert calc.software_release is None


@pytest.mark.parametrize("shape", SHAPES)
def test_every_other_calculation_still_needs_software_on_every_shape(shape):
    payload = {
        "type": "sp",
        "level_of_theory": {"method": "ccsd(t)", "basis": "cc-pvqz"},
        "sp_result": {"electronic_energy_hartree": -1.0},
    }
    if shape == "calculation_in":
        payload = {"type": "sp", "level_of_theory": payload["level_of_theory"], "sp_electronic_energy_hartree": -1.0}
    if shape in ("bundle", "calculation_in"):
        payload["key"] = "x"
    with pytest.raises(ValidationError) as exc:
        SHAPES[shape](**payload)
    assert _code(exc).code == "calculation_software_release_required"


def test_a_program_run_composite_still_needs_software():
    calc = {
        "type": "composite",
        "level_of_theory": {"method": "CBS-QB3"},
        "composite_result": {"assembly": "program_run", "electronic_energy_hartree": -76.0},
    }
    with pytest.raises(ValidationError) as exc:
        CalculationWithResultsPayload(**calc)
    assert _code(exc).code == "calculation_software_release_required"
    assert CalculationWithResultsPayload(**calc, software_release=SOFTWARE).software_release is not None


@pytest.mark.parametrize("shape", SHAPES)
def test_an_assembled_composite_at_a_named_method_is_not_accepted_on_any_shape(shape):
    calc = _composite_calc(shape)
    calc["level_of_theory"] = {"method": "CBS-QB3"}
    with pytest.raises(ValidationError) as exc:
        SHAPES[shape](**calc)
    assert _code(exc).code == "composite_assembled_not_accepted"


def test_inputs_belong_to_an_assembled_composite_only():
    with pytest.raises(ValidationError) as exc:
        CompositeResultPayload(assembly="program_run", inputs=_inputs())
    assert _code(exc).code == "composite_inputs_require_assembled"


def test_an_assembled_result_with_no_inputs_is_missing_them():
    with pytest.raises(ValidationError) as exc:
        CompositeResultPayload(assembly="assembled", electronic_energy_hartree=-1.0)
    assert _code(exc).code == "composite_input_missing"


@pytest.mark.parametrize(
    "reference",
    [{}, {"calculation_key": "a", "calculation_ref": "calc_" + "a" * 26}],
    ids=["neither", "both"],
)
def test_an_input_names_its_calculation_by_exactly_one_of_key_or_ref(reference):
    with pytest.raises(ValidationError) as exc:
        CompositeInputPayload(term_key="ref", slot="value", **reference)
    assert _code(exc).code == "composite_input_reference_invalid"


def test_a_ref_must_look_like_a_calculation_ref_and_never_an_id():
    with pytest.raises(ValidationError):
        CompositeInputPayload(term_key="ref", slot="value", calculation_ref="lot_" + "a" * 26)
    with pytest.raises(ValidationError):
        CompositeInputPayload(term_key="ref", slot="value", calculation_ref="12345")
    with pytest.raises(ValidationError):
        CompositeInputPayload(term_key="ref", slot="value", calculation_id=12345)  # type: ignore[call-arg]
    ok = CompositeInputPayload(term_key="ref", slot="value", calculation_ref="calc_" + "a2" * 13)
    assert ok.calculation_ref.startswith("calc_")


def _match(inputs):
    return match_inputs_to_definition(CompositeSchemeDefinition(**SCHEME_B), [CompositeInputPayload(**i) for i in inputs])


def test_inputs_are_matched_to_term_positions_by_key_and_cardinal():
    matched = _match(_inputs())
    assert [(m.term_position, m.term_key, m.slot.value, m.cardinal_number) for m in matched] == [
        (0, "ref", "value", None),
        (1, "corr", "cardinal", 3),
        (1, "corr", "cardinal", 4),
    ]


@pytest.mark.parametrize(
    ("edit", "code"),
    [
        (lambda rows: rows.pop(), "composite_input_missing"),
        (lambda rows: rows.append(dict(rows[0])), "composite_input_duplicate"),
        (lambda rows: rows[0].update(term_key="nope"), "composite_input_slot_unknown"),
        (lambda rows: rows[1].update(cardinal_number=5), "composite_input_slot_unknown"),
        (lambda rows: rows[0].update(slot="high"), "composite_input_slot_unknown"),
    ],
    ids=["missing_slot", "duplicate", "unknown_term", "unknown_cardinal", "unknown_slot"],
)
def test_inputs_that_do_not_fill_the_scheme_exactly_once_are_refused_with_the_right_code(edit, code):
    rows = _inputs()
    edit(rows)
    with pytest.raises(CodedValidationError) as exc:
        _match(rows)
    assert exc.value.code == code


def test_the_shape_function_re_runs_the_input_rules_on_an_object_that_skipped_validation():
    result = CompositeResultPayload.model_construct(
        assembly="assembled",
        electronic_energy_hartree=-76.0,
        e0_hartree=None,
        recipe_zpe_hartree=None,
        terms=[],
        inputs=[CompositeInputPayload(term_key="ref", slot="value", calculation_key="spq")],
    )
    from tckdb_schemas.enums import CalculationType

    with pytest.raises(CodedValidationError) as exc:
        assert_composite_calculation_shape(
            CalculationType.composite,
            result,
            level_of_theory=LevelOfTheoryRef(composite_scheme=SCHEME_B),
            software_release=None,
        )
    assert exc.value.code == "composite_input_missing"


def test_depends_on_cannot_declare_a_composite_input_edge():
    with pytest.raises(ValidationError) as exc:
        CalculationDependencyInBundle(parent_calculation_key="spq", role="composite_input")
    assert _code(exc).code == "composite_input_edge_is_derived"
    assert CalculationDependencyInBundle(parent_calculation_key="opt0", role="freq_on").role.value == "freq_on"
