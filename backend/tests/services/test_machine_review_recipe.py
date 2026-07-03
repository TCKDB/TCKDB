"""Tests for the shared private machine-review recipe module.

The *recipe* (active prompt version + active rubric versions) is the single
source of truth every machine-review consumer reads (readiness-audit risk R1).
These tests pin that it preserves the prompt version, derives rubric versions
from the deployed trust rubric constants (never hand-maintained), and that the
typed recipe model carries no mutation payload.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.machine_review.recipe import (
    ACTIVE_MACHINE_REVIEW_PROMPT_VERSION,
    ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS,
    MachineReviewActiveRecipe,
    get_active_machine_review_recipe,
    public_rubric_name,
)
from app.services.trust.rubrics import (
    COMPUTED_CALCULATION_V1,
    COMPUTED_KINETICS_V1,
    COMPUTED_STATMECH_V1,
    COMPUTED_THERMO_V1,
    COMPUTED_TRANSITION_STATE_V1,
    COMPUTED_TRANSPORT_V1,
)

_ACTIVE_RUBRICS = (
    COMPUTED_CALCULATION_V1,
    COMPUTED_KINETICS_V1,
    COMPUTED_THERMO_V1,
    COMPUTED_STATMECH_V1,
    COMPUTED_TRANSPORT_V1,
    COMPUTED_TRANSITION_STATE_V1,
)


def test_active_machine_review_recipe_preserves_prompt_version():
    """The prompt version is the documented private constant."""
    assert ACTIVE_MACHINE_REVIEW_PROMPT_VERSION == "machine_review_v1"
    assert get_active_machine_review_recipe().prompt_version == "machine_review_v1"


def test_active_machine_review_recipe_derives_rubric_versions_from_trust_constants():
    """Rubric versions are derived from the trust rubric constants, not hand-set.

    Every entry's key is ``<rubric.name>_v<rubric.version>`` and its value is the
    integer rubric version as a string, taken straight from the deployed
    ``COMPUTED_*_V1`` constants — so a rubric ``version`` bump changes the recipe
    automatically (it cannot drift from the evaluator).
    """
    expected = {
        public_rubric_name(rubric): str(rubric.version) for rubric in _ACTIVE_RUBRICS
    }
    assert expected == ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS
    assert get_active_machine_review_recipe().rubric_versions == expected

    # Each key carries the version it maps to (key/value can't silently diverge).
    for rubric in _ACTIVE_RUBRICS:
        name = public_rubric_name(rubric)
        assert name.endswith(f"_v{rubric.version}")
        assert ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS[name] == str(rubric.version)


def test_active_recipe_snapshot_is_an_independent_copy():
    """Mutating a returned recipe's dict never mutates the module constant."""
    recipe = get_active_machine_review_recipe()
    recipe.rubric_versions["computed_calculation_v1"] = "999"
    assert ACTIVE_MACHINE_REVIEW_RUBRIC_VERSIONS["computed_calculation_v1"] == "1"


def test_recipe_model_forbids_mutation_payload_fields():
    """The recipe model is frozen and rejects any extra/mutation field."""
    field_names = set(MachineReviewActiveRecipe.model_fields)
    assert field_names == {"prompt_version", "rubric_versions"}
    forbidden = ("set_", "mutation", "override", "apply", "is_certified",
                 "benchmark", "review_status", "trust_status", "evidence")
    for token in forbidden:
        assert not any(token in name for name in field_names), token

    recipe = MachineReviewActiveRecipe(prompt_version="machine_review_v1", rubric_versions={})
    with pytest.raises(ValidationError):
        recipe.prompt_version = "x"  # frozen

    with pytest.raises(ValidationError):
        MachineReviewActiveRecipe(
            prompt_version="machine_review_v1",
            rubric_versions={},
            set_review_status="approved",  # type: ignore[call-arg]
        )
