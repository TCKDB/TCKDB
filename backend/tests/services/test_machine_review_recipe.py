"""Tests for the shared private machine-review recipe module.

The *recipe* (active prompt ref + the one rubric ref a review is stamped with) is
the single source of truth every machine-review consumer reads (readiness-audit
risk R1). These tests pin that it preserves the prompt, reads the rubric from the
deployed trust rubric registry (never hand-maintained), that a recipe holds
exactly one rubric (the currency rule, structurally), and that the typed recipe
model carries no mutation payload. That a stored recipe survives an unrelated
rubric being added is pinned in ``test_machine_review_recipe_filtering.py``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tckdb_schemas.fragments.version import VersionRef

from app.services.consistency.core import AdvisoryResult
from app.services.machine_review.admin_trigger import active_rubric_for_record_type
from app.services.machine_review.recipe import (
    ACTIVE_MACHINE_REVIEW_PROMPT,
    MachineReviewActiveRecipe,
    active_rubric,
)
from app.services.trust.rubrics import (
    COMPUTED_CALCULATION,
    COMPUTED_KINETICS,
    COMPUTED_STATMECH,
    COMPUTED_THERMO,
    COMPUTED_TRANSITION_STATE,
    COMPUTED_TRANSPORT,
    COMPUTED_TS_STATMECH,
    EXTERNAL_CP_COMPARISON,
    GIBBS_SELF_CONSISTENCY,
    HESS_CONSISTENCY,
    ISOTOPE_IDENTITY_CONSISTENCY,
    KIRCHHOFF_CONSISTENCY,
    THERMO_CONSISTENCY,
    THERMO_KINETICS_CONSISTENCY,
)

_ACTIVE_RUBRICS = (
    COMPUTED_CALCULATION,
    COMPUTED_KINETICS,
    COMPUTED_THERMO,
    COMPUTED_STATMECH,
    COMPUTED_TRANSPORT,
    COMPUTED_TRANSITION_STATE,
    # The owner-scoped rubric for transition-state-owned statmech.
    COMPUTED_TS_STATMECH,
    # Phase C-E4: not a computed-trust rubric (checks=()), but it is part of
    # the machine-review recipe -- see the comment on _ACTIVE_RUBRICS in
    # app.services.machine_review.recipe.
    EXTERNAL_CP_COMPARISON,
    THERMO_CONSISTENCY,
    THERMO_KINETICS_CONSISTENCY,
    GIBBS_SELF_CONSISTENCY,
    HESS_CONSISTENCY,
    ISOTOPE_IDENTITY_CONSISTENCY,
    KIRCHHOFF_CONSISTENCY,
)


def test_active_machine_review_recipe_preserves_the_prompt():
    """The prompt is the documented private constant: a name beside an integer version."""
    assert ACTIVE_MACHINE_REVIEW_PROMPT == VersionRef(name="machine_review", version=1)


def test_active_rubric_is_read_from_the_trust_rubric_registry():
    """The rubric ref comes straight from each deployed rubric, so a version bump changes it with no second edit."""
    for rubric in _ACTIVE_RUBRICS:
        ref = active_rubric(rubric)
        assert (ref.name, ref.version) == (rubric.name, rubric.version)
        assert active_rubric(rubric.name) == ref
        assert "_v" not in ref.name


def test_a_record_types_rubric_is_its_own_and_the_ref_is_a_fresh_value():
    assert active_rubric_for_record_type("calculation") == VersionRef(name="computed_calculation", version=1)

    advisory = AdvisoryResult(target=None, runner="thermo_consistency", rubric=THERMO_CONSISTENCY,
                              findings=(), inputs_json="{}")
    assert advisory.rubric_ref == VersionRef(name="thermo_consistency", version=1)
    assert advisory.prompt == VersionRef(name="thermo_consistency", version=1)


def test_recipe_model_forbids_mutation_payload_fields():
    """The recipe model is frozen and rejects any extra/mutation field; it holds ONE rubric, never a set."""
    field_names = set(MachineReviewActiveRecipe.model_fields)
    assert field_names == {"context_schema_version", "prompt", "rubric"}
    forbidden = ("set_", "mutation", "override", "apply", "is_certified",
                 "benchmark", "review_status", "trust_status", "evidence")
    for token in forbidden:
        assert not any(token in name for name in field_names), token

    recipe = MachineReviewActiveRecipe(prompt=ACTIVE_MACHINE_REVIEW_PROMPT, rubric=active_rubric("computed_thermo"))
    with pytest.raises(ValidationError):
        recipe.prompt = VersionRef(name="x", version=1)  # frozen

    with pytest.raises(ValidationError):
        MachineReviewActiveRecipe(
            prompt=ACTIVE_MACHINE_REVIEW_PROMPT,
            rubric=active_rubric("computed_thermo"),
            set_review_status="approved",  # type: ignore[call-arg]
        )

    # A second rubric cannot be carried: the recipe has no field for it and a dict is not a ref.
    with pytest.raises(ValidationError):
        MachineReviewActiveRecipe(
            prompt=ACTIVE_MACHINE_REVIEW_PROMPT,
            rubric={"computed_thermo": 1, "computed_kinetics": 1},  # type: ignore[arg-type]
        )
