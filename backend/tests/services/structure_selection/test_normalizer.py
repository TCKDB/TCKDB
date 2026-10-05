"""Recipe normalisation: what is established, what contradicts, which cohort a unit can be compared in."""

from __future__ import annotations

import pytest

from app.services.structure_selection.normalizer import (
    REQUIRED_FACTS,
    declared_fact,
    normalize_recipe,
    recipe_request_mismatches,
)
from tests.services.structure_selection._support import declaration, level


def recipe(lvl=None, decl=..., **kw):
    return normalize_recipe(
        lvl if lvl is not None else level(),
        declaration() if decl is ... else decl,
        constraint_rows=kw.pop("constraint_rows", 0),
        needs_constraints=kw.pop("needs_constraints", False),
        **kw,
    )


def fact(r, name):
    return next(f for f in r.facts if f.name == name)


def test_a_fully_declared_recipe_has_a_cohort_and_nothing_unestablished():
    r = recipe()
    assert r.cohort_key is not None and r.conflicts == () and r.unestablished == ()


def test_two_equal_declarations_share_a_cohort_and_one_changed_fact_splits_it():
    assert recipe().cohort_key == recipe().cohort_key
    for change in (
        {"electronic_state": {"state": "known", "root": 1}},
        {"relativistic_treatment": {"state": "known", "value": "x2c"}},
        {"effective_core_potential": {"state": "known", "value": "def2-ecp"}},
        {"core_treatment": {"state": "known", "value": "all_electron"}},
        {"solvation": {"state": "known", "kind": "implicit_solvent", "detail": "smd:water"}},
        {"numerical_approximations": [{"kind": "density_fitting"}]},
        {"included_corrections": ["dispersion"]},
        {"spin_treatment": {"state": "known", "value": "unrestricted"}},
    ):
        assert recipe(decl=declaration(**change)).cohort_key != recipe().cohort_key, change


def test_the_level_of_theory_is_part_of_the_cohort():
    assert recipe(level(ref="lot_b")).cohort_key != recipe().cohort_key
    assert recipe(level(ref=None)).cohort_key is None  # no identity at all: nothing to compare on


def test_equal_nulls_do_not_prove_equivalence_an_undeclared_recipe_has_no_cohort():
    r = recipe(decl=None)
    assert r.cohort_key is None
    assert set(r.unestablished) == set(REQUIRED_FACTS)


def test_an_explicit_unknown_is_unestablished_exactly_as_a_missing_fact_is():
    r = recipe(decl=declaration(relativistic_treatment={"state": "unknown"}))
    assert r.unestablished == ("relativistic_treatment",) and r.cohort_key is None


def test_not_applicable_is_established():
    r = recipe(decl=declaration(effective_core_potential={"state": "not_applicable"}))
    assert fact(r, "effective_core_potential").state == "not_applicable" and r.cohort_key is not None


def test_a_level_that_states_a_fact_establishes_it_without_a_declaration():
    r = recipe(level(spin_treatment="restricted", core_treatment="frozen_core"), declaration(
        spin_treatment={"state": "unknown"}, core_treatment={"state": "unknown"}))
    assert fact(r, "spin_treatment").state == "known" and fact(r, "spin_treatment").source == "lot"
    assert fact(r, "core_treatment").value == "frozen_core"


def test_a_declaration_that_contradicts_the_level_is_a_conflict_not_a_precedence():
    r = recipe(level(spin_treatment="unrestricted"), declaration(spin_treatment={"state": "known", "value": "restricted"}))
    assert r.conflicts == ("spin_treatment",) and r.cohort_key is None
    r = recipe(level(core_treatment="all_electron"), declaration(core_treatment={"state": "known", "value": "frozen_core"}))
    assert r.conflicts == ("core_treatment",)
    # An agreeing declaration is established by both.
    r = recipe(level(core_treatment="frozen_core"), declaration(core_treatment={"state": "known", "value": "frozen_core"}))
    assert fact(r, "core_treatment").source == "both" and r.conflicts == ()


def test_a_missing_solvent_is_not_gas_phase_and_a_declared_gas_phase_contradicts_a_named_solvent():
    undeclared = recipe(decl=declaration(solvation=None))
    assert "solvation" in undeclared.unestablished
    named = recipe(level(solvent="water", solvent_model="smd"), declaration(solvation={"state": "known", "kind": "gas_phase"}))
    assert named.conflicts == ("solvation",)
    implicit = recipe(level(solvent="water", solvent_model="smd"), declaration(solvation={"state": "known", "kind": "implicit_solvent"}))
    assert implicit.conflicts == () and fact(implicit, "solvation").value == "implicit_solvent:water:smd"


def test_declared_not_applicable_against_a_level_that_states_a_value_conflicts():
    r = recipe(level(aux_basis="def2/j"), declaration(auxiliary_basis={"state": "not_applicable"}))
    assert r.conflicts == ("auxiliary_basis",)
    r = recipe(level(dispersion="d3bj"), declaration(dispersion={"state": "known", "value": "d3bj"}))
    assert r.conflicts == () and r.cohort_key is not None
    r = recipe(level(dispersion="d3bj"), declaration(dispersion={"state": "not_applicable"}))
    assert r.conflicts == ("dispersion",) and r.cohort_key is None


def test_the_cohort_key_includes_a_declared_dispersion_even_though_it_is_not_required():
    a = recipe(decl=declaration(dispersion={"state": "known", "value": "d3bj"}))
    b = recipe(decl=declaration(dispersion={"state": "known", "value": "d4"}))
    c = recipe()
    assert len({a.cohort_key, b.cohort_key, c.cohort_key}) == 3


def test_constraints_are_required_only_for_an_endpoint_and_rows_establish_constrained():
    plain = recipe(needs_constraints=True)
    assert plain.unestablished == ("constraints",) and plain.cohort_key is None
    free = recipe(decl=declaration(constraints={"state": "known", "value": "unconstrained"}), needs_constraints=True)
    assert free.cohort_key is not None
    rows = recipe(needs_constraints=True, constraint_rows=2)
    assert fact(rows, "constraints").value == "constrained" and rows.cohort_key is not None
    assert rows.cohort_key != free.cohort_key
    contradicted = recipe(
        decl=declaration(constraints={"state": "known", "value": "unconstrained"}), needs_constraints=True, constraint_rows=1
    )
    assert contradicted.conflicts == ("constraints",)
    # Not an endpoint: constraints neither required nor keyed.
    assert recipe().cohort_key is not None


def test_a_determinations_own_recipe_adds_what_the_calculation_does_not_say_and_conflicts_where_it_differs():
    base = declaration(relativistic_treatment={"state": "unknown"})
    added = recipe(decl=base, determination_recipe={"relativistic_treatment": {"state": "known", "value": "x2c"}})
    assert fact(added, "relativistic_treatment").value == "x2c" and added.conflicts == ()
    clash = recipe(
        decl=declaration(relativistic_treatment={"state": "known", "value": "none"}),
        determination_recipe={"relativistic_treatment": {"state": "known", "value": "x2c"}},
    )
    assert clash.conflicts == ("relativistic_treatment",)


def test_list_facts_distinguish_none_from_not_stated_and_ignore_order():
    none = declared_fact({"included_corrections": []}, "included_corrections")
    assert none == ("known", "none")
    assert declared_fact({}, "included_corrections") is None
    a = declared_fact({"numerical_approximations": [{"kind": "integration_grid", "setting": "fine"}, {"kind": "density_fitting"}]}, "numerical_approximations")
    b = declared_fact({"numerical_approximations": [{"kind": "density_fitting"}, {"kind": "integration_grid", "setting": "fine"}]}, "numerical_approximations")
    assert a == b


@pytest.mark.parametrize(
    "requested, differs, unestablished",
    [
        ({"electronic_state": {"state": "known", "root": 0}}, [], []),
        ({"electronic_state": {"state": "known", "root": 1}}, ["electronic_state"], []),
        ({"relativistic_treatment": {"state": "known", "value": "x2c"}}, ["relativistic_treatment"], []),
    ],
)
def test_a_requested_recipe_is_met_or_differs(requested, differs, unestablished):
    assert recipe_request_mismatches(recipe(), requested) == (differs, unestablished)


def test_a_requested_fact_a_unit_does_not_establish_is_unestablished_never_assumed():
    r = recipe(decl=declaration(relativistic_treatment={"state": "unknown"}))
    assert recipe_request_mismatches(r, {"relativistic_treatment": {"state": "known", "value": "none"}}) == (
        [],
        ["relativistic_treatment"],
    )
    # A request that states a fact unknown asks nothing.
    assert recipe_request_mismatches(r, {"relativistic_treatment": {"state": "unknown"}}) == ([], [])
    conflicted = recipe(level(spin_treatment="unrestricted"), declaration(spin_treatment={"state": "known", "value": "restricted"}))
    assert recipe_request_mismatches(conflicted, {"spin_treatment": {"state": "known", "value": "restricted"}}) == (
        [],
        ["spin_treatment"],
    )
