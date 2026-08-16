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
    W_GEOMETRY_KEY_UNRESOLVED,
    W_MICRO_REACTION_KEY_UNDECLARED,
    W_NETWORK_CHANNEL_KEY_UNDECLARED,
    W_NETWORK_STATE_KEY_UNDECLARED,
    W_SPECIES_KEY_UNDECLARED,
    W_TRANSITION_STATE_KEY_UNDECLARED,
    resolve_calculation_key,
    resolve_declared_key,
    resolve_geometry_key,
    resolve_micro_reaction_key,
    resolve_network_channel_key,
    resolve_network_state_key,
    resolve_species_key,
    resolve_transition_state_key,
)

#: Every namespace resolver, with the code it is contracted to raise.
#: ``resolve_calculation_key`` is on the list too: it is the one that
#: existed first, and leaving it off would let a later edit collapse the
#: family onto its code without anything going red.
_RESOLVERS = [
    (resolve_calculation_key, W_CALCULATION_KEY_UNDECLARED, "a calculation"),
    (resolve_species_key, W_SPECIES_KEY_UNDECLARED, "a species"),
    (resolve_network_state_key, W_NETWORK_STATE_KEY_UNDECLARED, "a network state"),
    (
        resolve_network_channel_key,
        W_NETWORK_CHANNEL_KEY_UNDECLARED,
        "a network channel",
    ),
    (resolve_micro_reaction_key, W_MICRO_REACTION_KEY_UNDECLARED, "a micro reaction"),
    (
        resolve_transition_state_key,
        W_TRANSITION_STATE_KEY_UNDECLARED,
        "a transition state",
    ),
    (resolve_geometry_key, W_GEOMETRY_KEY_UNRESOLVED, "a geometry"),
]

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

#: The other local-key namespaces, and the workflows that hold them.
#:
#: Kept as a separate table from ``_CALC_KEY_MAPS`` rather than merged
#: into it, because the two were closed at different times and by
#: different arguments -- and a single table would let a future reader
#: assume the whole family was audited in one pass, which is the
#: assumption this work was filed to refute.
#:
#: Every read is routed, including the ones that look safe by
#: construction -- ``species_key_to_entry[sp.key]`` immediately after the
#: loop that wrote every ``sp.key``. "Safe because of the order the loops
#: run in" is precisely the reasoning that was false for
#: ``geometry_key_to_id``, where a transition state's calculation could
#: name a geometry declared by a later transition state. A guard that
#: admits exceptions has to be argued about every time it fires; this one
#: does not.
_NAMESPACE_MAPS = {
    ("computed_reaction.py", "species_key_to_entry"),
    ("computed_reaction.py", "geometry_key_to_id"),
    ("network_pdep.py", "species_key_to_entry"),
    ("network_pdep.py", "geometry_key_to_id"),
    ("network_pdep.py", "state_key_to_row"),
    ("network_pdep.py", "channel_key_to_row"),
    ("network_pdep.py", "reaction_key_to_entry"),
    ("network_pdep.py", "ts_key_to_entry"),
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


# ---------------------------------------------------------------------------
# The namespace family
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("resolve", "code", "subject"),
    _RESOLVERS,
    ids=[fn.__name__ for fn, _, _ in _RESOLVERS],
)
def test_every_resolver_returns_what_the_workflow_stored(
    resolve, code: str, subject: str
) -> None:
    """The half that makes every refusal below mean something."""
    sentinel = object()
    assert resolve("k", {"k": sentinel}, field="f") is sentinel


@pytest.mark.parametrize(
    ("resolve", "code", "subject"),
    _RESOLVERS,
    ids=[fn.__name__ for fn, _, _ in _RESOLVERS],
)
def test_every_resolver_refuses_an_undeclared_key_with_its_own_code(
    resolve, code: str, subject: str
) -> None:
    with pytest.raises(CodedValueError) as exc_info:
        resolve("ghost", {"real": 1}, field="some.field[0].key")
    error = exc_info.value
    assert error.code == code
    assert error.context == {
        "field": "some.field[0].key",
        "key": "ghost",
        "declared_keys": ["real"],
    }
    assert f"does not name {subject} " in str(error)
    # Naming the alternatives is what makes the fix mechanical.
    assert "'real'" in str(error)


def test_the_seven_namespaces_have_seven_distinct_codes() -> None:
    """One code per namespace, and the reason it is not one between them.

    A species key, a state key and a channel key are repaired in three
    different blocks of the depositor's own payload. A client can only
    point at the right block if the code says which one; collapse them and
    ``context['field']`` becomes a string a client has to parse.
    """
    codes = [code for _, code, _ in _RESOLVERS]
    assert len(set(codes)) == len(codes), codes


@pytest.mark.parametrize(
    ("resolve", "code", "subject"),
    _RESOLVERS,
    ids=[fn.__name__ for fn, _, _ in _RESOLVERS],
)
def test_no_resolver_leaks_a_row_id(resolve, code: str, subject: str) -> None:
    """DR-0028 Requirement 2. The ids are every map's *values*."""
    with pytest.raises(CodedValueError) as exc_info:
        resolve("ghost", {"real": 424242}, field="f")
    assert "424242" not in str(exc_info.value)
    assert 424242 not in exc_info.value.context.values()


def test_only_the_geometry_resolver_declines_to_say_undeclared() -> None:
    """The one namespace whose map is incomplete while it is read.

    Geometry keys are resolved as the workflow walks the species and
    transition states that declare them, so a key can be in the payload
    and absent from the map. Every other namespace here is fully built
    before anything reads it, so "declared in this upload" is true of
    them and would be false of this one.
    """
    for resolve, _, _ in _RESOLVERS:
        with pytest.raises(CodedValueError) as exc_info:
            resolve("ghost", {"real": 1}, field="f")
        message = str(exc_info.value)
        if resolve is resolve_geometry_key:
            assert "declared in this upload" not in message, message
            assert "has resolved at this point" in message, message
        else:
            assert "declared in this upload" in message, message


def test_the_scope_default_is_what_every_other_caller_relies_on() -> None:
    """``scope`` is optional, and its default is the old fixed wording."""
    with pytest.raises(CodedValueError) as exc_info:
        resolve_declared_key(
            "ghost", {"a": 1}, field="f", code="c", subject="a thing", remedy="Fix it."
        )
    assert "does not name a thing declared in this upload" in str(exc_info.value)


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


def _is_bound(source: str, name: str) -> bool:
    """Whether ``name`` is assigned anywhere in ``source``.

    The anti-vacuity anchor for the namespace guard below, and it is
    deliberately not ``_CALC_KEY_MAPS``'s "does it have subscript
    writes?". Two of these namespaces are never written by subscript at
    all: ``channel_key_to_row`` is rebuilt wholesale from the rows the
    flush returned, and a future one may well be a comprehension too.
    Requiring a subscript write there would fail an honest map, and the
    natural repair -- dropping the anchor -- is what would make the guard
    vacuous. Asking "is this name bound here?" catches the rename this
    exists to catch without caring how the map is filled.
    """
    return re.search(
        rf"^\s*{re.escape(name)}\s*(?::[^=\n]+)?=(?!=)", source, re.MULTILINE
    ) is not None


@pytest.mark.parametrize(("module", "map_name"), sorted(_NAMESPACE_MAPS))
def test_no_raw_subscript_reads_a_namespace(module: str, map_name: str) -> None:
    """The same structural guard, for the other six namespaces.

    Falsifiable the same way, by a different anchor: it asserts the map
    is still *bound* in the module, so a rename fails here instead of
    silently reporting zero raw reads forever.
    """
    source = (_WORKFLOWS / module).read_text(encoding="utf-8")
    assert source, f"{module} is empty"
    assert _is_bound(source, map_name), (
        f"{module} never assigns `{map_name}` at all, so this test is not "
        f"looking at the namespace it thinks it is -- the map was probably "
        f"renamed. Fix the name in _NAMESPACE_MAPS."
    )
    _writes, reads = _map_accesses(source, map_name)
    assert reads == [], (
        f"{module} reads {map_name} by raw subscript: {reads}. Every "
        f"cross-reference must go through the matching resolver in "
        f"app.services.local_key_resolution, or an undeclared key is a "
        f"500 again."
    )
