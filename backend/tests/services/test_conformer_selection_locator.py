"""The discriminator behind ``ambiguous_conformer_selection_locator``.

Why a unit test and not only a wire test
----------------------------------------
:func:`app.services.conformer_selection_locator.discriminate` classifies a
candidate set three ways, and **only one of the three is reachable through
a route**. Which one changed when the locator gained
``conformer_group_ref``, and the flip is the fix:

* *differs by a locator field* is now the reachable one. Two conformer
  groups under one species entry differ by ``conformer_group_ref``, which
  the payload can set, so the refusal names a field a depositor can act
  on. Before the field existed this outcome could not happen at all — the
  lookup filtered on every field the locator had, so candidates agreed on
  all of them, and that unreachability was the finding that the locator
  was too narrow;
* *differs by something the locator cannot express* is now the unreachable
  one. Only the group ``label`` is inexpressible, and two candidates in
  two groups always differ by ``public_ref`` as well, which is ordered
  first;
* *differs by nothing reportable* cannot happen while
  ``uq_conformer_selection_conformer_group_id`` holds — it permits one
  selection per ``(group, scheme, kind)`` and candidates share scheme and
  kind, so two candidates always sit in two groups and always differ by
  ``conformer_group_ref``.

The two unreachable ones are worth *reporting distinctly* anyway:
``locator_can_express`` is a published field a client branches on, and
``false`` means nothing if no branch can produce it; and "differ by
nothing" would be a duplicate curation row, which is a different bug and
one the depositor should hear about rather than be told to be more
specific. A branch no test can enter is how this repository's recurring
defect looks, so the function is pure and the classification is pinned
here, over hand-built candidates.

The wire half — that the two codes, statuses and context keys reach a
client, and that a locator matching exactly one selection still succeeds —
is ``tests/api/test_api_conformer_selection_locator_codes.py``.
"""

from __future__ import annotations

import pytest

from app.db.models.common import ConformerSelectionKind
from app.schemas.workflows.kinetics_upload import ConformerSelectionContentRef
from app.services.conformer_selection_locator import (
    _AXES,
    _LOCATOR_AXES,
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
        "conformer_group_ref",
        "conformer_group_label",
    ), found
    assert found.discriminator == "selection_kind", found
    assert found.locator_can_express is True, found


def test_two_groups_are_separated_by_the_ref_and_not_by_the_label() -> None:
    """The axis a depositor can act on, where two axes vary.

    Both groups are labelled, so ``conformer_group_label`` varies and is
    reported — but the locator has no label field and does have a
    ``conformer_group_ref`` one, so the ref is what the advice names.
    Ordering the label first would name the difference a depositor cannot
    express while a field that expresses it sits unused, which is the
    defect ``conformer_group_ref`` was added to end.
    """
    found = discriminate(
        [
            _candidate(1, label="basin-A", group_ref="cgrp_a"),
            _candidate(2, label="basin-B", group_ref="cgrp_b"),
        ]
    )
    assert found.differs_by == (
        "conformer_group_ref",
        "conformer_group_label",
    ), found
    assert found.discriminator == "conformer_group_ref", found
    assert found.locator_can_express is True, found
    assert found.discriminator_values == ("cgrp_a", "cgrp_b"), found


def test_every_locator_axis_is_a_field_the_payload_actually_has() -> None:
    """``locator_can_express`` is a claim about a schema, checked against it.

    The whole refusal turns on this set being the truth about
    ``ConformerSelectionContentRef``. Nothing else fails if it drifts: a
    forgotten entry would quietly tell depositors a settable field cannot
    be set, and a spurious one would tell them to set a field that does
    not exist — the failure the ref was added to remove, reintroduced by a
    constant.
    """
    fields = set(ConformerSelectionContentRef.model_fields)
    assert _LOCATOR_AXES <= fields, (_LOCATOR_AXES - fields, fields)
    assert "conformer_group_ref" in _LOCATOR_AXES, _LOCATOR_AXES
    # The label is the axis that is *not* expressible, and the reason the
    # widening is a public ref: ``uq_conformer_group_species_entry_id``
    # does not declare ``postgresql_nulls_not_distinct``, so one species
    # entry may own any number of unlabelled groups.
    assert "conformer_group_label" not in fields, fields
    assert "conformer_group_label" not in _LOCATOR_AXES, _LOCATOR_AXES
    # Every axis is either expressible or accounted for as not.
    assert set(_AXES) - _LOCATOR_AXES == {"conformer_group_label"}, _AXES


def test_unlabelled_groups_fall_back_to_the_group_ref() -> None:
    """Two groups with no label at all.

    ``uq_conformer_group_species_entry_id`` does not declare
    ``postgresql_nulls_not_distinct``, so one species entry may own many
    unlabelled groups — and then the *label* discriminates nothing and the
    public ref is the only handle left. Without this fallback the refusal
    would land in outcome 3 and call two genuinely different curation rows
    a duplicate.

    It is also the case that decided the shape of the locator's new
    field: there is no label here for a label field to carry, so the
    handle had to be the public ref.
    """
    found = discriminate(
        [_candidate(1, group_ref="cgrp_a"), _candidate(2, group_ref="cgrp_b")]
    )
    assert found.differs_by == ("conformer_group_ref",), found
    assert found.discriminator == "conformer_group_ref", found
    assert found.locator_can_express is True, found
    assert found.discriminator_values == ("cgrp_a", "cgrp_b"), found


def test_one_labelled_and_one_unlabelled_group_still_discriminate() -> None:
    found = discriminate(
        [
            _candidate(1, label=None, group_ref="cgrp_a"),
            _candidate(2, label="basin-B", group_ref="cgrp_b"),
        ]
    )
    assert found.discriminator == "conformer_group_ref", found
    assert found.discriminator_values == ("cgrp_a", "cgrp_b"), found
    # The label difference is still reported, still with its ``None``
    # intact -- ``differs_by`` is what tells a human the two groups are
    # not the same basin under two names.
    assert found.differs_by == (
        "conformer_group_ref",
        "conformer_group_label",
    ), found


# ---------------------------------------------------------------------------
# Outcome 2: differs only by something the locator cannot express
# ---------------------------------------------------------------------------


def test_a_difference_only_in_the_label_is_reported_as_unactionable() -> None:
    """The branch ``locator_can_express: false`` exists to reach.

    No two stored rows can have this shape: a public ref identifies a
    conformer group and a group has one label, so two candidates sharing a
    ref share a label. The set is hand-built for the same reason outcome 3
    below is — the classification has a branch for "the axis that
    separates these is one you cannot write down", and a ``false`` a
    client branches on means nothing if the branch producing it is never
    executed. Since ``conformer_group_ref`` joined the locator this is the
    only way in; before it, this was every refusal the route produced.
    """
    candidates = [
        _candidate(1, label=None, group_ref="cgrp_a"),
        _candidate(2, label="basin-B", group_ref="cgrp_a"),
    ]
    found = discriminate(candidates)
    assert found.differs_by == ("conformer_group_label",), found
    assert found.discriminator == "conformer_group_label", found
    assert found.locator_can_express is False, found
    assert found.discriminator_values == (None, "basin-B"), found
    # A ``None`` in the published list must not crash the sentence, which
    # is the one thing sorting or a bare ``", ".join`` would have done.
    assert "unlabelled" in str(
        ambiguous_conformer_selection_locator(field=_FIELD, candidates=candidates)
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
        "differs_by": ["conformer_group_ref", "conformer_group_label"],
        "discriminator": "conformer_group_ref",
        "discriminator_values": ["cgrp_a", "cgrp_b", "cgrp_c"],
        "locator_can_express": True,
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
@pytest.mark.parametrize("group_ref", [None, "cg_aaaaaaaaaaaaaaaaaaaaaaaaaa"])
def test_the_unknown_refusal_echoes_only_what_the_caller_wrote(
    scheme_ref, group_ref
) -> None:
    error = unknown_conformer_selection(
        field=_FIELD,
        selection_kind="lowest_energy",
        assignment_scheme_ref=scheme_ref,
        conformer_group_ref=group_ref,
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
    if group_ref is None:
        assert "conformer_group_ref" not in error.context, error.context
        # And the remedy does not send them to a field they never set.
        assert "conformer group" not in str(error), str(error)
    else:
        assert error.context["conformer_group_ref"] == group_ref, error.context
        assert "conformer group" in str(error), str(error)


def test_a_selection_kind_read_back_from_the_database_is_a_token() -> None:
    """``str()`` of a ``str``-Enum is ``"ClassName.member"`` on 3.11+.

    Which is what would have reached ``context`` and the client's branch.
    """
    assert (
        selection_kind_token(ConformerSelectionKind.lowest_energy) == "lowest_energy"
    )
    assert selection_kind_token("curator_pick") == "curator_pick"
