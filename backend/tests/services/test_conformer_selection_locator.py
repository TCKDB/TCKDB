"""The discriminator behind ``ambiguous_conformer_selection_locator``.

Why a unit test and not only a wire test
----------------------------------------
:func:`app.services.conformer_selection_locator.discriminate` classifies a
candidate set three ways, and **only one of the three is reachable through
a route**:

* *differs by a locator field* cannot happen, because the lookup filters on
  every field the locator has, so candidates agree on all of them;
* *differs by nothing reportable* cannot happen while
  ``uq_conformer_selection_conformer_group_id`` holds — it permits one
  selection per ``(group, scheme, kind)`` and candidates share scheme and
  kind, so two candidates always sit in two groups and always differ by
  ``conformer_group_ref``.

Both are worth *reporting distinctly* anyway: the first is the outcome
that would tell a depositor what to add, and its unreachability is the
finding that the locator is too narrow; the second would be a duplicate
curation row, which is a different bug and one the depositor should hear
about rather than be told to be more specific. A branch no test can enter
is how this repository's recurring defect looks, so the function is pure
and the classification is pinned here, over hand-built candidates.

The wire half — that the two codes, statuses and context keys reach a
client, and that a locator matching exactly one selection still succeeds —
is ``tests/api/test_api_conformer_selection_locator_codes.py``.
"""

from __future__ import annotations

import pytest

from app.db.models.common import ConformerSelectionKind
from app.services.conformer_selection_locator import (
    W_AMBIGUOUS_CONFORMER_SELECTION_LOCATOR,
    W_UNKNOWN_CONFORMER_SELECTION,
    SelectionCandidate,
    ambiguous_conformer_selection_locator,
    discriminate,
    selection_kind_token,
    unknown_conformer_selection,
)

_FIELD = "interpretation_assignments[0].conformer_selection"


def test_the_two_codes_are_two_distinct_published_strings() -> None:
    """Spelled out, not compared to the constants they came from.

    Every other assertion in this file reads ``error.code ==
    W_UNKNOWN_CONFORMER_SELECTION``, which is satisfied by whatever the
    constant happens to say -- including by both constants saying the same
    thing, which is precisely the collapse this work undid.
    """
    assert W_UNKNOWN_CONFORMER_SELECTION == "unknown_conformer_selection"
    assert (
        W_AMBIGUOUS_CONFORMER_SELECTION_LOCATOR
        == "ambiguous_conformer_selection_locator"
    )
    assert W_UNKNOWN_CONFORMER_SELECTION != W_AMBIGUOUS_CONFORMER_SELECTION_LOCATOR


def _candidate(
    selection_id: int,
    *,
    selection_kind: str = "lowest_energy",
    label: str | None = None,
    group_ref: str = "cgrp_aaaaaaaaaaaaaaaaaaaaaaaaaa",
    scheme_ref: str | None = None,
) -> SelectionCandidate:
    return SelectionCandidate(
        selection_id=selection_id,
        selection_kind=selection_kind,
        conformer_group_label=label,
        conformer_group_ref=group_ref,
        assignment_scheme_ref=scheme_ref,
    )


# ---------------------------------------------------------------------------
# Outcome 1: differs by something the locator can express
# ---------------------------------------------------------------------------


def test_a_differing_selection_kind_is_reported_as_actionable() -> None:
    found = discriminate(
        [
            _candidate(1, selection_kind="lowest_energy", group_ref="cgrp_a"),
            _candidate(2, selection_kind="curator_pick", group_ref="cgrp_b"),
        ]
    )
    assert found.discriminator == "selection_kind", found
    assert found.locator_can_express is True, found
    assert found.discriminator_values == ("lowest_energy", "curator_pick"), found


def test_a_differing_assignment_scheme_is_reported_as_actionable() -> None:
    found = discriminate(
        [
            _candidate(1, scheme_ref="cas_one", group_ref="cgrp_a"),
            _candidate(2, scheme_ref="cas_two", group_ref="cgrp_b"),
        ]
    )
    assert found.discriminator == "assignment_scheme_ref", found
    assert found.locator_can_express is True, found


def test_a_locator_field_wins_over_a_group_difference() -> None:
    """Both axes vary; the one the depositor can act on is the one named.

    This is what the ordering of ``_AXES`` buys, and the assertion that
    would fail if the axes were reordered or collected into a set.
    """
    found = discriminate(
        [
            _candidate(1, selection_kind="lowest_energy", label="A", group_ref="cgrp_a"),
            _candidate(2, selection_kind="curator_pick", label="B", group_ref="cgrp_b"),
        ]
    )
    assert found.differs_by == (
        "selection_kind",
        "conformer_group_label",
        "conformer_group_ref",
    ), found
    assert found.discriminator == "selection_kind", found
    assert found.locator_can_express is True, found


# ---------------------------------------------------------------------------
# Outcome 2: differs only by something the locator cannot express
# ---------------------------------------------------------------------------


def test_a_group_label_difference_is_reported_as_unactionable() -> None:
    found = discriminate(
        [
            _candidate(1, label="basin-A", group_ref="cgrp_a"),
            _candidate(2, label="basin-B", group_ref="cgrp_b"),
        ]
    )
    assert found.discriminator == "conformer_group_label", found
    assert found.locator_can_express is False, found
    assert found.discriminator_values == ("basin-A", "basin-B"), found


def test_unlabelled_groups_fall_back_to_the_group_ref() -> None:
    """Two groups with no label at all.

    ``uq_conformer_group_species_entry_id`` does not declare
    ``postgresql_nulls_not_distinct``, so one species entry may own many
    unlabelled groups — and then the *label* discriminates nothing and the
    public ref is the only handle left. Without this fallback the refusal
    would land in outcome 3 and call two genuinely different curation rows
    a duplicate.
    """
    found = discriminate(
        [_candidate(1, group_ref="cgrp_a"), _candidate(2, group_ref="cgrp_b")]
    )
    assert found.differs_by == ("conformer_group_ref",), found
    assert found.discriminator == "conformer_group_ref", found
    assert found.locator_can_express is False, found
    assert found.discriminator_values == ("cgrp_a", "cgrp_b"), found


def test_one_labelled_and_one_unlabelled_group_still_discriminate() -> None:
    found = discriminate(
        [
            _candidate(1, label=None, group_ref="cgrp_a"),
            _candidate(2, label="basin-B", group_ref="cgrp_b"),
        ]
    )
    assert found.discriminator == "conformer_group_label", found
    assert found.discriminator_values == (None, "basin-B"), found
    # A ``None`` in the published list must not crash the sentence, which
    # is the one thing sorting or ``", ".join`` would have done.
    assert "unlabelled" in str(
        ambiguous_conformer_selection_locator(
            field=_FIELD,
            candidates=[
                _candidate(1, label=None, group_ref="cgrp_a"),
                _candidate(2, label="basin-B", group_ref="cgrp_b"),
            ],
        )
    )


# ---------------------------------------------------------------------------
# Outcome 3: differs by nothing reportable
# ---------------------------------------------------------------------------


def test_indistinguishable_candidates_are_reported_as_duplicates() -> None:
    """Not reachable through a route; see the module docstring.

    Reported as a duplicate curation row rather than as ambiguity, because
    "be more specific" would be false twice over here: there is nothing to
    add *and* nothing that separates them.
    """
    found = discriminate([_candidate(1), _candidate(2)])
    assert found.differs_by == (), found
    assert found.discriminator is None, found
    assert found.discriminator_values == (), found
    assert found.locator_can_express is False, found

    error = ambiguous_conformer_selection_locator(
        field=_FIELD, candidates=[_candidate(1), _candidate(2)]
    )
    assert "duplicate curation rows" in str(error), str(error)
    assert "be more specific" not in str(error), str(error)


# ---------------------------------------------------------------------------
# The two refusals themselves
# ---------------------------------------------------------------------------


def test_the_ambiguity_refusal_publishes_the_documented_context() -> None:
    error = ambiguous_conformer_selection_locator(
        field=_FIELD,
        candidates=[
            _candidate(1, label="basin-A", group_ref="cgrp_a"),
            _candidate(2, label="basin-B", group_ref="cgrp_b"),
            _candidate(3, label="basin-C", group_ref="cgrp_c"),
        ],
    )
    assert error.code == W_AMBIGUOUS_CONFORMER_SELECTION_LOCATOR
    assert error.context == {
        "field": _FIELD,
        "kind": "conformer_selection",
        "match_count": 3,
        "differs_by": ["conformer_group_label", "conformer_group_ref"],
        "discriminator": "conformer_group_label",
        "discriminator_values": ["basin-A", "basin-B", "basin-C"],
        "locator_can_express": False,
    }, error.context
    # ``message_prefix=False``: the code is an attribute, and repeating it
    # in the prose puts a second copy where a reword can drop it.
    assert not str(error).startswith(W_AMBIGUOUS_CONFORMER_SELECTION_LOCATOR)


def test_no_context_value_is_a_row_id() -> None:
    """The selection ids are the obvious discriminator and the banned one.

    DR-0028 Requirement 2. ``SelectionCandidate`` carries the id because
    the success path needs it, so the guard that matters is that no axis
    names it.
    """
    error = ambiguous_conformer_selection_locator(
        field=_FIELD,
        candidates=[
            _candidate(918, label="basin-A", group_ref="cgrp_a"),
            _candidate(919, label="basin-B", group_ref="cgrp_b"),
        ],
    )
    rendered = str(error) + str(error.context)
    for token in ("918", "919", "selection_id", "conformer_group_id"):
        assert token not in rendered, (token, rendered)


@pytest.mark.parametrize("scheme_ref", [None, "cas_aaaaaaaaaaaaaaaaaaaaaaaaaa"])
def test_the_unknown_refusal_echoes_only_what_the_caller_wrote(scheme_ref) -> None:
    error = unknown_conformer_selection(
        field=_FIELD,
        selection_kind="lowest_energy",
        assignment_scheme_ref=scheme_ref,
    )
    assert error.code == W_UNKNOWN_CONFORMER_SELECTION
    assert error.context["field"] == _FIELD
    assert error.context["kind"] == "conformer_selection"
    assert error.context["selection_kind"] == "lowest_energy"
    if scheme_ref is None:
        # Omitted rather than published as null, matching
        # ``unknown_reference``'s treatment of an absent ``ref``.
        assert "assignment_scheme_ref" not in error.context, error.context
    else:
        assert error.context["assignment_scheme_ref"] == scheme_ref, error.context


def test_a_selection_kind_read_back_from_the_database_is_a_token() -> None:
    """``str()`` of a ``str``-Enum is ``"ClassName.member"`` on 3.11+.

    Which is what would have reached ``context`` and the client's branch.
    """
    assert (
        selection_kind_token(ConformerSelectionKind.lowest_energy) == "lowest_energy"
    )
    assert selection_kind_token("curator_pick") == "curator_pick"
