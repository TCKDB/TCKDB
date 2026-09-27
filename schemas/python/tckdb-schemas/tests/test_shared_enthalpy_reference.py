"""``shared_enthalpy_reference``: one declared basis, or a named refusal.

Every case below pins the exact return pair. The reason values are literals
here, not the module's constants, so renaming a constant's value goes red.
"""

from __future__ import annotations

import pytest
from tckdb_schemas.enthalpy_reference import (
    ENTHALPY_REFERENCE_MIXED,
    ENTHALPY_REFERENCE_UNRECORDED,
    shared_enthalpy_reference,
)
from tckdb_schemas.enums import EnthalpyReferenceKind

F = EnthalpyReferenceKind.formation_298k


def test_reason_values_are_the_published_tokens() -> None:
    assert ENTHALPY_REFERENCE_UNRECORDED == "enthalpy_reference_unrecorded"
    assert ENTHALPY_REFERENCE_MIXED == "enthalpy_reference_mixed"


@pytest.mark.parametrize(
    "kinds,expected",
    [
        (["formation_298k"], ("formation_298k", None)),
        ([F], ("formation_298k", None)),
        ([F, "formation_298k", F], ("formation_298k", None)),
        ([None], (None, "enthalpy_reference_unrecorded")),
        ([F, None], (None, "enthalpy_reference_unrecorded")),
        ([None, F, None], (None, "enthalpy_reference_unrecorded")),
        (["formation_298k", "sensible_increment"], (None, "enthalpy_reference_mixed")),
        # Unrecorded wins over mixed: an undeclared term is the more basic
        # defect, and the ML export has always reported it first.
        (["formation_298k", "sensible_increment", None], (None, "enthalpy_reference_unrecorded")),
    ],
)
def test_every_case_returns_its_exact_pair(kinds, expected) -> None:
    assert shared_enthalpy_reference(kinds) == expected


def test_accepts_a_one_shot_iterable() -> None:
    assert shared_enthalpy_reference(k for k in [F, F]) == ("formation_298k", None)


def test_shared_kind_is_the_plain_token() -> None:
    kind, reason = shared_enthalpy_reference([F])
    assert reason is None
    assert type(kind) is str


@pytest.mark.parametrize("empty", [[], (), iter(())])
def test_no_terms_is_refused_not_a_vacuous_shared_basis(empty) -> None:
    with pytest.raises(ValueError, match="at least one term"):
        shared_enthalpy_reference(empty)
