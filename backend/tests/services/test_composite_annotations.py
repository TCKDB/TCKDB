"""The rules behind ``composite_energy_verification`` and ``legacy_composite_shape`` on a record's levels (ADR 0021, P7a).

Direct calls on plain objects: no read produced these inputs, so the rules are tested as the
rules, including on summaries built by hand that no builder would produce.

* a record that links several composites reports the **least** verified of them, so a
  disagreement in the second is never hidden by a clean first;
* the verification is reported only when the energy *came from* a composite;
* a legacy shape is reported for the roles that are not the intended composite shape, and never for a
  level a recipe or a composite calculation itself supplied.
"""

from __future__ import annotations

import pytest

from app.db.models.common import CompositeAssembly, CompositeSchemeKind
from app.db.models.common import CompositeEnergyVerificationState as S
from app.db.models.common import LegacyCompositeShape as L
from app.schemas.reads.scientific_common import (
    CompositeEnergyVerification,
    CompositeSchemeSummary,
    LevelOfTheorySummary,
)
from app.services.scientific_read.composite_annotations import (
    legacy_composite_shape,
    record_composite_verification,
)


def _v(state: S, reason: str | None = None) -> CompositeEnergyVerification:
    return CompositeEnergyVerification(state=state, assembly=CompositeAssembly.program_run, reason=reason)


def _level(*, bound: bool) -> LevelOfTheorySummary:
    scheme = (
        CompositeSchemeSummary(composite_scheme_ref="csch_x", kind=CompositeSchemeKind.named_method, name="CBS-QB3")
        if bound
        else None
    )
    return LevelOfTheorySummary(
        level_of_theory_id=1,
        level_of_theory_ref="lot_x",
        method="CBS-QB3" if bound else "B3LYP",
        core_treatment=None,
        composite_scheme=scheme,
    )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def test_only_a_composite_energy_has_a_verification():
    verifications = {1: _v(S.recomputed)}
    for source in ("sp", "opt", "imported", "ambiguous", None):
        assert record_composite_verification(energy_source=source, typed_composite_ids=[1], verifications=verifications) is None
    assert (
        record_composite_verification(energy_source="composite", typed_composite_ids=[1], verifications=verifications)
        == verifications[1]
    )


def test_a_composite_energy_with_nothing_verified_reports_none_not_a_guess():
    assert record_composite_verification(energy_source="composite", typed_composite_ids=[], verifications={}) is None
    assert record_composite_verification(energy_source="composite", typed_composite_ids=[7], verifications={}) is None


@pytest.mark.parametrize(
    ("states", "worst"),
    [
        ([S.recomputed, S.recompute_mismatch], S.recompute_mismatch),
        ([S.recompute_mismatch, S.recomputed], S.recompute_mismatch),
        ([S.log_reconciled, S.unverifiable], S.unverifiable),
        ([S.program_reported, S.log_reconciled], S.program_reported),
        ([S.program_reported, S.unverifiable, S.recomputed], S.unverifiable),
        ([S.unverifiable, S.recompute_mismatch, S.program_reported], S.recompute_mismatch),
    ],
)
def test_the_least_verified_composite_speaks_for_the_record(states, worst):
    verifications = {i + 1: _v(state) for i, state in enumerate(states)}
    got = record_composite_verification(
        energy_source="composite", typed_composite_ids=list(verifications), verifications=verifications
    )
    assert got is not None and got.state is worst


def _least(*verifications, order=None):
    ids = list(range(1, len(verifications) + 1))
    mapping = dict(zip(ids, verifications, strict=True))
    return record_composite_verification(
        energy_source="composite", typed_composite_ids=order or ids, verifications=mapping
    )


@pytest.mark.parametrize("reason", ["log_mismatch", "log_method_mismatch"])
def test_a_log_contradiction_is_not_hidden_by_a_plain_program_reported_whichever_comes_first(reason):
    contradiction, plain = _v(S.program_reported, reason), _v(S.program_reported)
    assert _least(contradiction, plain) is contradiction
    assert _least(plain, contradiction) is contradiction


@pytest.mark.parametrize("reason", ["log_mismatch", "log_method_mismatch"])
def test_a_log_contradiction_is_not_hidden_by_an_unverifiable_composite(reason):
    contradiction, unverifiable = _v(S.program_reported, reason), _v(S.unverifiable, "input_energy_not_stated")
    assert _least(contradiction, unverifiable) is contradiction
    assert _least(unverifiable, contradiction) is contradiction


def test_a_recompute_mismatch_still_outranks_a_log_contradiction():
    mismatch, contradiction = _v(S.recompute_mismatch), _v(S.program_reported, "log_mismatch")
    assert _least(contradiction, mismatch) is mismatch
    assert _least(mismatch, contradiction) is mismatch


def test_a_contradiction_outranks_a_confirmation_and_two_contradictions_tie_to_the_first():
    a, b = _v(S.program_reported, "log_mismatch"), _v(S.program_reported, "log_method_mismatch")
    assert _least(_v(S.log_reconciled), a) is a
    assert _least(a, b) is a and _least(b, a) is b


@pytest.mark.parametrize("state", [S.program_reported, S.unverifiable, S.recomputed, S.log_reconciled, S.recompute_mismatch])
def test_a_genuine_tie_of_two_equal_states_is_the_first_composite(state):
    first, second = _v(state, "first"), _v(state, "second")
    assert _least(first, second) is first
    assert _least(first, second, order=[2, 1]) is second  # "first" follows the order the record links them


def test_a_composite_without_a_verification_is_skipped_not_counted_as_worst():
    got = record_composite_verification(
        energy_source="composite", typed_composite_ids=[1, 2], verifications={2: _v(S.recomputed)}
    )
    assert got is not None and got.state is S.recomputed


# ---------------------------------------------------------------------------
# Legacy shapes
# ---------------------------------------------------------------------------

_NONE = {"geometry": None, "geometry_source": None, "frequency": None, "frequency_source": None, "energy": None, "energy_source": None}


def _shape(**kwargs):
    return legacy_composite_shape(**{"composite_role_on_non_composite": False, **_NONE, **kwargs})


def test_a_record_with_no_levels_has_no_legacy_shape():
    assert _shape() is None


def test_a_non_composite_calculation_under_the_composite_role_is_that_shape_whatever_the_levels():
    assert _shape(composite_role_on_non_composite=True) is L.composite_role_on_non_composite_calculation
    # It outranks the named-method shape: the role misuse is the more specific fact.
    got = _shape(composite_role_on_non_composite=True, energy=_level(bound=True), energy_source="sp")
    assert got is L.composite_role_on_non_composite_calculation


@pytest.mark.parametrize(
    ("which", "source"),
    [("geometry", "opt"), ("frequency", "freq"), ("frequency", "opt"), ("energy", "sp"), ("energy", "opt")],
)
def test_an_opt_freq_or_sp_at_a_named_method_level_is_the_named_method_shape(which, source):
    kwargs = {which: _level(bound=True), f"{which}_source": source}
    assert _shape(**kwargs) is L.named_method_level_on_non_composite_calculation


@pytest.mark.parametrize(
    ("which", "source"),
    [("geometry", "composite_recipe"), ("frequency", "composite_recipe"), ("energy", "composite"), ("energy", "imported")],
)
def test_a_level_a_recipe_or_a_composite_supplied_is_not_a_legacy_shape(which, source):
    kwargs = {which: _level(bound=True), f"{which}_source": source}
    assert _shape(**kwargs) is None


def test_an_ordinary_level_from_any_source_is_not_a_legacy_shape():
    assert (
        _shape(
            geometry=_level(bound=False),
            geometry_source="opt",
            frequency=_level(bound=False),
            frequency_source="freq",
            energy=_level(bound=False),
            energy_source="sp",
        )
        is None
    )


def test_a_bound_level_with_no_source_is_not_annotated():
    """A level whose source is unknown is not claimed to be an opt or an sp."""
    assert _shape(energy=_level(bound=True), energy_source=None) is None
