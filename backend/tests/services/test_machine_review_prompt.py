"""The pin :mod:`app.services.machine_review.providers.prompt` says it has.

That module's docstring claims twice -- once in prose, once on the constant --
that the prompt version and the prompt text are "pinned together by a test" and
"pinned by ``tests/services/test_machine_review_prompt.py``". Until this file
existed, that file did not, so both claims were false and the two things they
name could drift freely.

**Why a hash of the text, and not just a version equality check.**
A stored machine review is judged current or stale against the active recipe
(:mod:`app.services.machine_review.recipe`), and
:mod:`app.services.machine_review.context_hash` folds the *prompt version* --
a short label -- into the ``context_hash`` it stores with each review. It does
not fold in the prompt text. So the label is the only thing standing between a
stored review and the words that produced it. Edit the text while leaving
``machine_review_v1`` alone and nothing anywhere notices: every review already
on disk goes on reporting that it was produced by ``machine_review_v1``, its
``context_hash`` still matches, it is still classified current, and it is now
claiming words it never saw. No test fails, no row changes, and the only
evidence is gone.

That is what the hash is for. It cannot be satisfied by remembering to bump
the version; it fails on the edit itself, in the pull request that makes it,
and the fix is either to revert the text or to bump the version and this
constant together -- which is the decision the prompt module says must be
made.

Three separate assertions rather than one, because they fail for different
reasons and a reader needs to know which:

* the version binding (prompt version == the recipe's active version),
* the text pin (the hash),
* ASCII (the prompt is a runtime string and reaches an external API and,
  through the summary, a database column).
"""

from __future__ import annotations

import hashlib

from app.services.machine_review.providers.prompt import (
    MACHINE_REVIEW_PROMPT_VERSION,
    MACHINE_REVIEW_SYSTEM_PROMPT,
)
from app.services.machine_review.recipe import ACTIVE_MACHINE_REVIEW_PROMPT_VERSION

#: SHA-256 of :data:`MACHINE_REVIEW_SYSTEM_PROMPT`, encoded UTF-8.
#:
#: **Changing the prompt text means changing this constant AND
#: ``ACTIVE_MACHINE_REVIEW_PROMPT_VERSION``, in the same commit.** Updating
#: only this one to get the test green re-opens exactly the hole the test
#: exists to close: reviews stored under the old version would still read as
#: current, and would still name a prompt whose words had changed underneath
#: them. If you are here because this assertion failed, the question to answer
#: is not "what is the new hash" but "does the recipe version need to move".
_PROMPT_SHA256 = "7e49c35c2806438f6658344e7ec38ce33683ae8f0cc4d2bee40bbbee6d75a832"


def test_prompt_version_is_the_recipes_active_version() -> None:
    """The prompt names itself with the version the recipe says is active.

    Two constants in two modules, and the currency machinery reads the recipe's
    while the review is produced by the prompt's. If they part company, a
    review is stamped with a version that did not write it.
    """
    assert MACHINE_REVIEW_PROMPT_VERSION == ACTIVE_MACHINE_REVIEW_PROMPT_VERSION


def test_prompt_text_is_pinned_to_its_version() -> None:
    """The text cannot change while the version stays put.

    See this module's docstring for why a version label alone is not enough:
    ``context_hash`` folds in the label, never the words.
    """
    digest = hashlib.sha256(MACHINE_REVIEW_SYSTEM_PROMPT.encode("utf-8")).hexdigest()
    assert digest == _PROMPT_SHA256, (
        "The machine-review system prompt changed. Decide whether this is a new "
        "prompt version: if the model is being asked something different, bump "
        "ACTIVE_MACHINE_REVIEW_PROMPT_VERSION (and MACHINE_REVIEW_PROMPT_VERSION "
        "with it) so stored reviews re-review instead of silently claiming words "
        "they never saw. Then update _PROMPT_SHA256 here."
    )


def test_prompt_text_is_ascii() -> None:
    """The prompt is a runtime string; it leaves the process as request bytes.

    The prompt module's own docstring commits to this. It is not covered by
    ``scripts/check_runtime_ascii.py``, which by design only looks at literals
    structurally at an emission site (a ``raise``, a log call, a
    ``message=``/``detail=`` keyword) -- a module-level assignment like this
    one is invisible to it.
    """
    assert MACHINE_REVIEW_SYSTEM_PROMPT.isascii()
