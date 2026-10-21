"""``same_value``: version comparison that does not let ``True`` pass for ``1`` or ``1.0`` for ``1``.

Python's ``==`` says ``True == 1`` and ``1.0 == 1``. A recorded version compared to the running one with ``==`` (or
``!=``, or ``in``) therefore accepts a manifest that records ``true`` or ``1.0``. ``same_value`` is the comparison the
replay readers use instead: the types must match as well as the values, all the way down through dicts and lists.
"""

from __future__ import annotations

import pytest

from app.versioning import same_value


@pytest.mark.parametrize(
    "recorded, expected",
    [
        (1, 1),
        ("a", "a"),
        ({"name": "p", "version": 1}, {"name": "p", "version": 1}),
        ({"a": [1, 2], "b": {"c": 3}}, {"a": [1, 2], "b": {"c": 3}}),
        ([1, 2], (1, 2)),
        (None, None),
    ],
)
def test_equal_values_of_equal_types_are_the_same(recorded, expected) -> None:
    assert same_value(recorded, expected)


@pytest.mark.parametrize(
    "recorded, expected",
    [
        (True, 1),
        (1, True),
        (False, 0),
        (1.0, 1),
        (1, 1.0),
        ("1", 1),
        ({"name": "p", "version": True}, {"name": "p", "version": 1}),
        ({"name": "p", "version": 1.0}, {"name": "p", "version": 1}),
        ({"name": "p"}, {"name": "p", "version": 1}),
        ({"name": "p", "version": 1, "extra": 1}, {"name": "p", "version": 1}),
        ([1, True], [1, 1]),
        ([1], [1, 2]),
        ({"a": [1, 1.0]}, {"a": [1, 1]}),
    ],
)
def test_a_bool_a_float_or_a_difference_is_not_the_same(recorded, expected) -> None:
    assert not same_value(recorded, expected)
