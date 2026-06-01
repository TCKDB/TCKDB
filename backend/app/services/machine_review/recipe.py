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
    COMPUTED_TRANSITION_STATE_V1,
    COMPUTED_TRANSPORT_V1,
)

#: Active machine-review prompt version (private constant; no config system yet).
ACTIVE_MACHINE_REVIEW_PROMPT_VERSION = "machine_review_v1"

# The deployed computed-trust rubrics whose versions form the machine-review
# rubric recipe. Each is the single source of its own version — listed here only
# to bind it into the recipe, never to restate a version number by hand.
_ACTIVE_RUBRICS: tuple[EvidenceRubric, ...] = (
    COMPUTED_CALCULATION_V1,
    COMPUTED_KINETICS_V1,
    COMPUTED_THERMO_V1,
    COMPUTED_STATMECH_V1,
    COMPUTED_TRANSPORT_V1,
    COMPUTED_TRANSITION_STATE_V1,
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
