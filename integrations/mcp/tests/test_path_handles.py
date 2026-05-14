"""Direct unit tests for ``tckdb_mcp.tools._path_handles.validate_path_handle``.

Endpoint-specific tests (e.g. ``test_reaction_kinetics_tool.py``) prove
each tool wires this helper correctly; the tests here exercise the
helper in isolation so a future change to the helper has its own
ground-truth coverage.
"""

from __future__ import annotations

import pytest

from tckdb_mcp.errors import MCPToolError
from tckdb_mcp.tools._path_handles import (
    PATH_UNSAFE_CHARS,
    PUBLIC_REF_MAX_LENGTH,
    validate_path_handle,
)


# ---------------------------------------------------------------------------
# Happy paths — every public prefix used by the read API
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "field", "prefix"),
    [
        ("rxe_01HZY3K9X2", "reaction_entry_ref", "rxe_"),
        ("spe_01HZ5K9X2A", "species_entry_ref", "spe_"),
        ("geom_01HZ7AAA", "geometry_ref", "geom_"),
        ("rxn_01HZY", "reaction_ref", "rxn_"),
        ("spc_01HZ5K", "species_ref", "spc_"),
        ("lot_b3lyp_6311g", "level_of_theory_ref", "lot_"),
    ],
)
def test_valid_refs_pass_through(value: str, field: str, prefix: str) -> None:
    assert validate_path_handle(value, field_name=field, expected_prefix=prefix) == value


def test_ref_at_exact_max_length_accepted() -> None:
    value = "rxe_" + ("A" * (PUBLIC_REF_MAX_LENGTH - len("rxe_")))
    assert len(value) == PUBLIC_REF_MAX_LENGTH
    assert (
        validate_path_handle(value, field_name="reaction_entry_ref", expected_prefix="rxe_")
        == value
    )


# ---------------------------------------------------------------------------
# Type / presence
# ---------------------------------------------------------------------------


def test_none_value_emits_is_required() -> None:
    with pytest.raises(MCPToolError) as excinfo:
        validate_path_handle(None, field_name="reaction_entry_ref", expected_prefix="rxe_")
    assert excinfo.value.code == "invalid_input"
    assert "reaction_entry_ref is required" in excinfo.value.detail


@pytest.mark.parametrize("bad", [42, 42.0, True, False, [], {}, b"rxe_abc"])
def test_non_string_value_rejected(bad: object) -> None:
    with pytest.raises(MCPToolError) as excinfo:
        validate_path_handle(
            bad, field_name="reaction_entry_ref", expected_prefix="rxe_"
        )
    assert excinfo.value.code == "invalid_input"
    assert "must be a string" in excinfo.value.detail
    assert type(bad).__name__ in excinfo.value.detail


def test_empty_string_rejected() -> None:
    with pytest.raises(MCPToolError) as excinfo:
        validate_path_handle(
            "", field_name="species_entry_ref", expected_prefix="spe_"
        )
    assert excinfo.value.code == "invalid_input"
    assert "must not be empty" in excinfo.value.detail


# ---------------------------------------------------------------------------
# Prefix / body
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected_prefix"),
    [
        ("spe_01HZ5K", "rxe_"),
        ("rxe_01HZY", "spe_"),
        ("rxn_01HZ", "rxe_"),
        ("42", "rxe_"),
        ("RXE_01HZY", "rxe_"),  # case-sensitive
        ("rxe", "rxe_"),  # missing underscore
    ],
)
def test_wrong_prefix_rejected(value: str, expected_prefix: str) -> None:
    with pytest.raises(MCPToolError) as excinfo:
        validate_path_handle(
            value, field_name="some_ref", expected_prefix=expected_prefix
        )
    assert excinfo.value.code == "invalid_input"
    assert expected_prefix in excinfo.value.detail


def test_integer_shaped_string_rejected_by_prefix_check() -> None:
    with pytest.raises(MCPToolError) as excinfo:
        validate_path_handle(
            "42", field_name="reaction_entry_ref", expected_prefix="rxe_"
        )
    assert "rxe_" in excinfo.value.detail


@pytest.mark.parametrize("prefix", ["rxe_", "spe_", "geom_", "rxn_", "spc_", "lot_"])
def test_bare_prefix_rejected(prefix: str) -> None:
    with pytest.raises(MCPToolError) as excinfo:
        validate_path_handle(
            prefix, field_name="x_ref", expected_prefix=prefix
        )
    assert "no body" in excinfo.value.detail
    assert prefix in excinfo.value.detail


def test_oversized_ref_rejected() -> None:
    value = "rxe_" + ("A" * (PUBLIC_REF_MAX_LENGTH - len("rxe_") + 1))
    assert len(value) == PUBLIC_REF_MAX_LENGTH + 1
    with pytest.raises(MCPToolError) as excinfo:
        validate_path_handle(
            value, field_name="reaction_entry_ref", expected_prefix="rxe_"
        )
    assert str(PUBLIC_REF_MAX_LENGTH) in excinfo.value.detail


def test_length_check_runs_before_body_check() -> None:
    """A 65-char string starting with 'rxe_' should fail on length, not body."""
    value = "rxe_" + ("A" * (PUBLIC_REF_MAX_LENGTH - len("rxe_") + 1))
    with pytest.raises(MCPToolError) as excinfo:
        validate_path_handle(
            value, field_name="reaction_entry_ref", expected_prefix="rxe_"
        )
    assert "exceeds" in excinfo.value.detail
    assert "no body" not in excinfo.value.detail


# ---------------------------------------------------------------------------
# Path-unsafe characters
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_char", sorted(PATH_UNSAFE_CHARS))
def test_each_path_unsafe_char_rejected(bad_char: str) -> None:
    value = f"rxe_abc{bad_char}def"
    with pytest.raises(MCPToolError) as excinfo:
        validate_path_handle(
            value, field_name="reaction_entry_ref", expected_prefix="rxe_"
        )
    assert "path-unsafe" in excinfo.value.detail


def test_path_traversal_attempt_rejected() -> None:
    with pytest.raises(MCPToolError) as excinfo:
        validate_path_handle(
            "rxe_../../admin",
            field_name="reaction_entry_ref",
            expected_prefix="rxe_",
        )
    assert "path-unsafe" in excinfo.value.detail


def test_multiple_unsafe_chars_listed_in_detail() -> None:
    with pytest.raises(MCPToolError) as excinfo:
        validate_path_handle(
            "rxe_a/b?c",
            field_name="reaction_entry_ref",
            expected_prefix="rxe_",
        )
    # Both offending chars surface in the sorted list.
    assert "/" in excinfo.value.detail
    assert "?" in excinfo.value.detail


# ---------------------------------------------------------------------------
# Field name propagation
# ---------------------------------------------------------------------------


def test_field_name_appears_in_every_error_message() -> None:
    field = "any_custom_ref_name"
    cases: list[object] = [
        None,
        42,
        "",
        "wrong_prefix",
        "rxe_",
        "rxe_" + ("A" * 100),
        "rxe_a/b",
    ]
    for value in cases:
        with pytest.raises(MCPToolError) as excinfo:
            validate_path_handle(
                value, field_name=field, expected_prefix="rxe_"
            )
        assert field in excinfo.value.detail, (
            f"expected {field!r} in error for value {value!r}: "
            f"{excinfo.value.detail!r}"
        )


def test_keyword_only_arguments_required() -> None:
    """Positional args other than ``value`` must raise TypeError."""
    with pytest.raises(TypeError):
        validate_path_handle("rxe_abc", "reaction_entry_ref", "rxe_")  # type: ignore[misc]
