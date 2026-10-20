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
    COMPUTED_CALCULATION_V2,
    COMPUTED_KINETICS_V2,
    COMPUTED_STATMECH_V2,
    COMPUTED_THERMO_V2,
    COMPUTED_TRANSITION_STATE_V5,
    COMPUTED_TRANSPORT_V2,
    COMPUTED_TS_STATMECH_V1,
    EXTERNAL_CP_COMPARISON_V2,
    GIBBS_SELF_CONSISTENCY_V1,
    HESS_CONSISTENCY_V1,
    ISOTOPE_IDENTITY_CONSISTENCY_V1,
    KIRCHHOFF_CONSISTENCY_V1,
    THERMO_CONSISTENCY_V1,
    THERMO_KINETICS_CONSISTENCY_V1,
)

#: Active machine-review prompt version (private constant; no config system yet).
ACTIVE_MACHINE_REVIEW_PROMPT_VERSION = "machine_review_v1"

# The deployed computed-trust rubrics whose versions form the machine-review
# rubric recipe. Each is the single source of its own version — listed here only
# to bind it into the recipe, never to restate a version number by hand.
#
# THE CURRENCY RULE: a stored recipe must be the filtered view. Every consumer
# that stamps a review with ``rubric_versions`` (and every currency check that
# compares against it) takes only the one key for its own rubric out of
# ``ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS`` -- never the whole dict. The
# filtering sites are ``active_rubric_versions_for_record_type`` in
# ``admin_trigger.py`` (reviewer family, per record type),
# ``AdvisoryResult.recipe`` in ``app.services.consistency.core`` (scientific-check
# family, per advisory check and runner) and the two call sites in
# ``app.services.external_comparison.cp`` (external-Cp runner). Currency compares
# the stored dict for equality with the active one, so a consumer that stored
# the unfiltered dict would see every stored review go stale whenever *any*
# rubric is added to or bumped in the tuple below. That is why this module
# offers no "whole recipe" accessor: ``get_active_machine_review_recipe()``,
# which returned exactly that dict, was removed (issue #553) before anything
# could persist its result. The rule is pinned by
# ``tests/services/test_machine_review_recipe_filtering.py``, which stores each
# filtered recipe, adds an unrelated key, and asserts nothing restales.
#
# The review-tier and advisory-check rubrics (the external-Cp comparison, the
# consistency checks, the isotope-identity check) are not computed-trust rubrics:
# they carry no checks and the trust evaluator never runs them. They are listed
# here for the same reason as every other entry, so each runner reads its rubric
# version from this one recipe rather than restating a number by hand. Each is one
# more key, filtered to its own record type and its own scientific-check family
# (``app.services.machine_review.query.MachineReviewRecordFamily``), so a run of
# one never restales another's review: a rubric-version change makes only that
# check's own past rows read stale against its own current recipe.
#
# ``COMPUTED_TS_STATMECH_V1`` is the owner-scoped statmech rubric for
# transition-state-owned records. It is one more key, filtered like every other:
# the reviewer recipe for a record comes from the rubric that actually evaluated it
# (``admin_trigger.active_rubric_versions_for_fragment``), not from the record type
# alone. ``tests/services/test_trust_ts_statmech_owner_rubric.py`` pins both halves.
_ACTIVE_RUBRICS: tuple[EvidenceRubric, ...] = (
    COMPUTED_CALCULATION_V2,
    COMPUTED_KINETICS_V2,
    COMPUTED_THERMO_V2,
    COMPUTED_STATMECH_V2,
    COMPUTED_TS_STATMECH_V1,
    COMPUTED_TRANSPORT_V2,
    COMPUTED_TRANSITION_STATE_V5,
    EXTERNAL_CP_COMPARISON_V2,
    THERMO_CONSISTENCY_V1,
    THERMO_KINETICS_CONSISTENCY_V1,
    GIBBS_SELF_CONSISTENCY_V1,
    # D6 (Hess): same kind of entry as D1/D3 above -- one more key, read
    # only by its own runner's currency, so no stored review is restaled.
    HESS_CONSISTENCY_V1,
    ISOTOPE_IDENTITY_CONSISTENCY_V1,
    KIRCHHOFF_CONSISTENCY_V1,
)


def public_rubric_name(rubric: EvidenceRubric) -> str:
    """Return the public ``<name>_v<version>`` rubric name (e.g. ``computed_kinetics_v1``)."""
    return f"{rubric.name}_v{rubric.version}"


#: The full active rubric-version table, derived from the trust rubric
#: constants so it cannot drift from the deployed rubrics. Keyed by the public
#: rubric name (``computed_*_v1``), valued by the integer rubric version as a
#: string. A rubric bump changes both the key and the value, restaling reviews
#: of that rubric. This is a lookup table, not a recipe: never store or compare
#: against it whole -- take the one key for your own rubric (currency rule above).
ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS: dict[str, str] = {
    public_rubric_name(rubric): str(rubric.version) for rubric in _ACTIVE_RUBRICS
}


class MachineReviewActiveRecipe(BaseModel):
    """Active machine-review recipe used for context currency checks.

    ``rubric_versions`` must be a *filtered* view -- the one key for the
    consumer's own rubric (see the currency rule above ``_ACTIVE_RUBRICS``),
    never the whole of ``ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS``.

    ``extra="forbid"`` / ``frozen=True`` so it can carry no mutation instruction
    and cannot be edited after construction — it *describes* the active recipe,
    it does not configure a side effect.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt_version: str
    rubric_versions: dict[str, str]

