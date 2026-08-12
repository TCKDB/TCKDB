"""A deliberately failing test, so that the aggregate check can be seen failing.

Task #110 asks for the new "Required gates" check to be bitten: a gate
never observed to block has not been shown to work. This file exists only
on the throwaway branch ``bite/gate110-red-backend`` and is deleted with
it. It is not a weakened, skipped or xfailed test -- it is a new failure,
introduced on purpose, on a branch that is never merged.

It lives under ``backend/tests/db/`` because that directory is covered by
the complement gate (``backend/scripts/test-rest.sh``), which is one of
the matrix legs of the path-filtered workflow the aggregate reports on.
"""

from __future__ import annotations


def test_the_aggregate_gate_can_be_made_to_fail() -> None:
    # Not `assert False`: ruff's B011 rejects that, and the API gate would
    # then fail at lint before any test ran -- which is a red backend-ci for
    # the wrong reason. The bite test wants the complement gate to fail on a
    # failing test.
    observed = 1
    assert observed == 2, "deliberate failure for task #110; this branch is not merged"
