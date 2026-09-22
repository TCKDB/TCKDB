"""Shared private source of truth for the active machine-review recipe.

The *recipe* is the pair of currency dimensions a machine review is stamped
with and compared against (policy
``backend/docs/specs/record_machine_review_policy.md`` §3.5): the active
**prompt version** and the active **rubric versions**. It is deliberately
separate from the per-consumer wiring (the admin trigger's record-type →
model/evaluator binding lives in ``admin_trigger.py``) so that every future
machine-review consumer — the admin fake trigger today, a real provider /
background re-review later — reads the **same** recipe from one place
(readiness-audit risk R1).

Design constraints kept here:

* The rubric versions are **derived from the deployed trust rubric constants**
  (`COMPUTED_*_V1`), never hand-maintained, so a rubric ``version`` bump changes
  the currency key automatically and cannot drift from the evaluator.
* No environment/config parsing is introduced — these are private constants. If
  the project later grows a settings pattern, this module is the single seam to
  route it through.

Nothing here is public: it emits no ``trust.machine_review``, touches no public
``TrustFragment``, and is imported only by private machine-review code.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.services.trust.models import EvidenceRubric
from app.services.trust.rubrics import (
    COMPUTED_CALCULATION_V1,
    COMPUTED_KINETICS_V1,
    COMPUTED_STATMECH_V1,
    COMPUTED_THERMO_V1,
    COMPUTED_TRANSITION_STATE_V2,
    COMPUTED_TRANSPORT_V1,
    EXTERNAL_CP_COMPARISON_V2,
    THERMO_CONSISTENCY_V1,
    THERMO_KINETICS_CONSISTENCY_V1,
)

#: Active machine-review prompt version (private constant; no config system yet).
ACTIVE_MACHINE_REVIEW_PROMPT_VERSION = "machine_review_v1"

# The deployed computed-trust rubrics whose versions form the machine-review
# rubric recipe. Each is the single source of its own version — listed here only
# to bind it into the recipe, never to restate a version number by hand.
#
# EXTERNAL_CP_COMPARISON_V2 (Phase C-E4, bumped to v2 in Phase D's review
# round 2 -- see docs/research/tckdb-phase-d-verification.md) is not a
# computed-trust rubric -- it carries no checks and is never run by the trust
# evaluator (see its definition in ``app.services.trust.rubrics``) -- but it
# is listed here for the same reason every other entry is: so the
# review-tier external-Cp-comparison runner
# (``app.services.external_comparison.cp.run_and_record``) reads its rubric
# version from this one recipe rather than restating a version number by
# hand. THERMO_CONSISTENCY_V1 and THERMO_KINETICS_CONSISTENCY_V1 (Phase D's
# D1/D3 advisory checks, ``app.services.consistency``) are the same kind of
# entry for the same reason. Adding any of these three changes
# ``ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS`` (below) by one key each; every
# currency-check call site in this package filters that dict to the single
# rubric relevant to a record's own type
# (``active_rubric_versions_for_record_type`` in ``admin_trigger.py``), so no
# existing calculation/kinetics/thermo/statmech/transport/transition-state
# review is restaled by any of them -- only a future run of that same
# advisory check would ever compare against its own key. The v1->v2 bump is
# safe by the same argument that made adding v1 safe: it is still one key in
# this dict, still filtered to its own record type, and a rubric-version
# change is exactly the mechanism that is supposed to make a check's own
# past rows read as stale against its own current recipe -- it does not
# touch any other rubric's key or any other record's currency.
#
# That claim is about the rubric-version *dict*, and it held even before the
# family fix below. What was never true, until
# ``app.services.machine_review.query.MachineReviewRecordFamily`` existed, was
# the companion claim a reader might assume: that running the Cp check
# couldn't restale a thermo's existing *reviewer*-family review either. It
# could and did -- the currency query loaded every row for a thermo
# regardless of provider, so the Cp runner's own appended row (never matching
# the reviewer recipe, always newest) demoted the true current reviewer
# review to historical on every run. See the Phase C-E4 review round 2
# correction in ``docs/research/tckdb-phase-c-implementation-plan.md`` C4.
# ``get_record_machine_review_currency_for_record`` now defaults to the
# ``reviewer`` family, so this file's calls (via ``admin_trigger.py``) are
# unaffected by any Cp row and this second effect can no longer happen. The
# same family separation is what makes the D1/D3 restale regression tests in
# ``tests/services/test_phase_d_persistence.py`` pass.
#
# ``cp.RUNNER_VERSION`` (``"external_cp_comparison_v1"``) deliberately still
# reads "v1" even though it now stamps the v2 rubric above -- that is not
# drift. ``RUNNER_VERSION`` is the runner's own identity (the ``model``
# under which its rows are recorded and looked up by
# ``latest_cp_comparison_for_thermo``); the rubric version is a separate axis
# that already carries "has this record's comparison gone stale" via the
# currency check, so bumping it needs no matching bump to the runner
# identity, and NOT bumping it keeps every already-recorded v1-era row
# discoverable under the same ``model`` as the runner's newest rows.
_ACTIVE_RUBRICS: tuple[EvidenceRubric, ...] = (
    COMPUTED_CALCULATION_V1,
    COMPUTED_KINETICS_V1,
    COMPUTED_THERMO_V1,
    COMPUTED_STATMECH_V1,
    COMPUTED_TRANSPORT_V1,
    COMPUTED_TRANSITION_STATE_V2,
    EXTERNAL_CP_COMPARISON_V2,
    THERMO_CONSISTENCY_V1,
    THERMO_KINETICS_CONSISTENCY_V1,
)


def public_rubric_name(rubric: EvidenceRubric) -> str:
    """Return the public ``<name>_v<version>`` rubric name (e.g. ``computed_kinetics_v1``)."""
    return f"{rubric.name}_v{rubric.version}"


#: The full active rubric-version recipe, derived from the trust rubric
#: constants so it cannot drift from the deployed rubrics. Keyed by the public
#: rubric name (``computed_*_v1``), valued by the integer rubric version as a
#: string. A rubric bump changes both the key and the value, restaling reviews.
ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS: dict[str, str] = {
    public_rubric_name(rubric): str(rubric.version) for rubric in _ACTIVE_RUBRICS
}


class MachineReviewActiveRecipe(BaseModel):
    """Active machine-review recipe used for context currency checks.

    ``extra="forbid"`` / ``frozen=True`` so it can carry no mutation instruction
    and cannot be edited after construction — it *describes* the active recipe,
    it does not configure a side effect.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt_version: str
    rubric_versions: dict[str, str]


def get_active_machine_review_recipe() -> MachineReviewActiveRecipe:
    """Return the active machine-review prompt and rubric-version recipe.

    A single, shared snapshot for every consumer (admin trigger today, future
    provider orchestration). The rubric versions are derived from the deployed
    trust rubric constants, so the recipe stays in lockstep with the evaluator.
    """
    return MachineReviewActiveRecipe(
        prompt_version=ACTIVE_MACHINE_REVIEW_PROMPT_VERSION,
        rubric_versions=dict(ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS),
    )
