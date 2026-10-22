"""Shared private source of truth for the active machine-review recipe.

The *recipe* is the set of currency dimensions a machine review is stamped
with and compared against (policy
``backend/docs/specs/record_machine_review_policy.md`` §3.5): the active
**prompt** and the active **rubric**, each a
:class:`~tckdb_schemas.fragments.version.VersionRef` (a stable name and a
separate integer version). It is deliberately separate from the per-consumer
wiring (the admin trigger's record-type -> model/evaluator binding lives in
``admin_trigger.py``) so that every machine-review consumer -- the admin fake
trigger today, a real provider / background re-review later -- reads the
**same** recipe from one place (readiness-audit risk R1).

Design constraints kept here:

* The rubric a review is stamped with is **read from the deployed trust rubrics**
  (``RUBRICS``), never hand-maintained, so a rubric ``version`` bump changes the
  currency key automatically and cannot drift from the evaluator.
* No environment/config parsing is introduced -- these are private constants. If
  the project later grows a settings pattern, this module is the single seam to
  route it through.

Nothing here is public: it emits no ``trust.machine_review``, touches no public
``TrustFragment``, and is imported only by private machine-review code.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from tckdb_schemas.fragments.version import VersionRef

from app.services.machine_review.context_hash import MACHINE_REVIEW_CONTEXT_SCHEMA_VERSION
from app.services.trust.models import EvidenceRubric
from app.services.trust.rubrics import RUBRICS

#: Active machine-review prompt (private constant; no config system yet).
ACTIVE_MACHINE_REVIEW_PROMPT: VersionRef = VersionRef(name="machine_review", version=1)

# THE CURRENCY RULE, now structural. A machine review is stamped with exactly one
# rubric: the one that graded the record (reviewer family, per record type),
# or the one a scientific check declares for itself (scientific-check family, per
# advisory check and runner). :class:`MachineReviewActiveRecipe` holds a single
# ``rubric`` ref, so a recipe can no longer carry a second key, and the rubric of one
# record type can no longer restale another's reviews: adding or bumping an unrelated
# rubric changes no stored review's currency. Pinned by
# ``tests/services/test_machine_review_recipe_filtering.py``.
#
# The review-tier and advisory-check rubrics (the external-Cp comparison, the
# consistency checks, the isotope-identity check) are not computed-trust rubrics:
# they carry no checks and the trust evaluator never runs them. They are registered in
# ``RUBRICS`` all the same, so each runner reads its rubric version from the one
# registry rather than restating a number by hand.
#
# ``COMPUTED_TS_STATMECH`` is the owner-scoped statmech rubric for
# transition-state-owned records: the recipe for a record comes from the rubric that
# actually evaluated it (``admin_trigger.active_rubric_for_fragment``), not from the
# record type alone.


def active_rubric(rubric: EvidenceRubric | str) -> VersionRef:
    """The ref of the current version of a rubric, by rubric or by name.

    Resolves through ``RUBRICS``: an unknown name raises
    :class:`~app.versioning.UnknownVersion`, never a guess.
    """
    name = rubric if isinstance(rubric, str) else rubric.name
    return RUBRICS.current(name)


class MachineReviewActiveRecipe(BaseModel):
    """Active machine-review recipe used for context currency checks.

    One ``rubric``, never a set: the consumer's own rubric (see the currency rule
    above). ``extra="forbid"`` / ``frozen=True`` so it can carry no mutation
    instruction and cannot be edited after construction -- it *describes* the
    active recipe, it does not configure a side effect.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    context_schema_version: int = MACHINE_REVIEW_CONTEXT_SCHEMA_VERSION
    prompt: VersionRef
    rubric: VersionRef
