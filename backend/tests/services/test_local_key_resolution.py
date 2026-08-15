"""The one seam every bundle-local key lookup goes through.

Two halves, and the second is the one that matters. A resolver that
refused every key would satisfy every "this is refused" assertion here;
what pins it down is that a *declared* key comes back with the value the
workflow put under it, unchanged and untouched.

The last test is structural rather than behavioural: it asserts that the
three bundle workflows contain no raw ``map[key]`` *read* of a calc-key
namespace. It is written to be falsifiable -- it also asserts that the
same pattern still finds the *writes*, so a regex that stopped matching
anything at all fails instead of passing forever.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.api.error_contract import CodedValueError
from app.services.local_key_resolution import (
    W_CALCULATION_KEY_UNDECLARED,
    resolve_calculation_key,
    resolve_declared_key,
)

_WORKFLOWS = Path(__file__).resolve().parents[2] / "app" / "workflows"

#: Every workflow that resolves a calculation by local key, and the name
#: it gives that namespace. The two standalone product workflows are on
#: the list for the same reason as the three bundle ones: the issue was
#: filed against the bundles, and stopping there would have left two raw
#: subscripts behind a PR whose title says there are none.
_CALC_KEY_MAPS = {
    "computed_species.py": "calc_keys_to_id",
    "computed_reaction.py": "calculation_key_to_id",
    "network_pdep.py": "calculation_key_to_id",
    "thermo.py": "calculations_by_key",
    "transport.py": "calculations_by_key",
}


def test_a_declared_key_returns_what_the_workflow_stored() -> None:
    """The half that makes every refusal below mean something."""
    assert resolve_calculation_key(
        "opt1", {"opt1": 17, "sp1": 18}, field="thermo.x"
    ) == 17


def test_the_value_type_is_whatever_the_caller_put_in() -> None:
    """Generic on purpose: two workflows keep ids, one keeps ORM rows."""
    sentinel = object()
    assert resolve_calculation_key(
        "opt1", {"opt1": sentinel}, field="thermo.x"
    ) is sentinel


def test_an_undeclared_key_is_a_coded_refusal() -> None:
    with pytest.raises(CodedValueError) as exc_info:
        resolve_calculation_key(
            "ghost",
            {"opt1": 17, "sp1": 18},
            field="thermo.source_calculations[0].calculation_key",
        )
    error = exc_info.value
    assert error.code == W_CALCULATION_KEY_UNDECLARED
    assert error.context == {
        "field": "thermo.source_calculations[0].calculation_key",
        "key": "ghost",
        "declared_keys": ["opt1", "sp1"],
    }


def test_the_refusal_names_the_declared_alternatives() -> None:
    """Naming what *is* declared is what makes the fix mechanical."""
    with pytest.raises(CodedValueError) as exc_info:
        resolve_calculation_key("ghost", {"opt1": 17}, field="f")
    assert "'opt1'" in str(exc_info.value)
    assert "does not name a calculation declared in this upload" in str(
        exc_info.value
    )


def test_an_empty_namespace_says_so_rather_than_listing_nothing() -> None:
    with pytest.raises(CodedValueError) as exc_info:
        resolve_calculation_key("ghost", {}, field="f")
    assert "declares no such name at all" in str(exc_info.value)
    assert exc_info.value.context["declared_keys"] == []


def test_no_row_id_reaches_the_depositor() -> None:
    """DR-0028 Requirement 2. The ids are the map's *values*."""
    with pytest.raises(CodedValueError) as exc_info:
        resolve_calculation_key("ghost", {"opt1": 424242}, field="f")
    assert "424242" not in str(exc_info.value)
    assert 424242 not in exc_info.value.context.values()


def test_the_code_is_overridable_for_a_published_one() -> None:
    """``statmech_calculation_key_undeclared`` predates this seam."""
    with pytest.raises(CodedValueError) as exc_info:
        resolve_calculation_key(
            "ghost", {"opt1": 1}, field="f", code="a_published_code"
        )
    assert exc_info.value.code == "a_published_code"


def test_the_subject_and_remedy_are_the_callers() -> None:
    """The applied-correction wrapper reuses the lookup, not the sentence."""
    with pytest.raises(CodedValueError) as exc_info:
        resolve_declared_key(
            "ghost",
            {"a": 1},
            field="f",
            code="c",
            subject="anything",
            remedy="Do the thing.",
        )
    message = str(exc_info.value)
    assert "does not name anything declared in this upload" in message
    assert message.endswith("Do the thing.")


def _map_accesses(source: str, name: str) -> tuple[list[str], list[str]]:
    """Split ``name[...]`` occurrences into writes and reads.

    A write is an assignment into the namespace -- the workflow filling
    its own map -- and is exactly what the lookup helper is not for. A
    read is the cross-reference that used to be able to ``KeyError``.
    """
    pattern = re.compile(rf"\b{re.escape(name)}\[(.*?)\]", re.DOTALL)
    writes: list[str] = []
    reads: list[str] = []
    for match in pattern.finditer(source):
        tail = source[match.end():match.end() + 40].lstrip()
        (writes if tail.startswith("=") and not tail.startswith("==")
         else reads).append(match.group(0))
    return writes, reads


@pytest.mark.parametrize(("module", "map_name"), sorted(_CALC_KEY_MAPS.items()))
def test_no_raw_subscript_reads_a_calc_key_namespace(
    module: str, map_name: str
) -> None:
    source = (_WORKFLOWS / module).read_text(encoding="utf-8")
    # Without this the whole test is vacuous against a renamed map: a
    # pattern that matches nothing would report zero reads forever.
    assert source, f"{module} is empty"
    writes, reads = _map_accesses(source, map_name)
    assert writes, (
        f"{module} has no `{map_name}[...] = ...` at all, so this test "
        f"is not looking at the namespace it thinks it is -- the map was "
        f"probably renamed. Fix the name in _CALC_KEY_MAPS."
    )
    assert reads == [], (
        f"{module} reads {map_name} by raw subscript: {reads}. Every "
        f"cross-reference must go through "
        f"app.services.local_key_resolution.resolve_calculation_key, or "
        f"an undeclared key is a 500 again."
    )
