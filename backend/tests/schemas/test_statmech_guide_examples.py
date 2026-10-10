"""The JSON examples in the statmech-model guide validate against the models they describe."""

from __future__ import annotations

import json
import re
from pathlib import Path

from tckdb_schemas.statmech_declarations import StatmechModelDeclaration

GUIDE = Path(__file__).resolve().parents[3] / "docs/guides/declaring_statmech_models.md"


def _blocks() -> list[dict]:
    return [json.loads(b)["model_declaration"] for b in re.findall(r"```json\n(.*?)\n```", GUIDE.read_text(), flags=re.S)]


def test_the_guide_has_exactly_the_examples_this_test_checks():
    assert len(_blocks()) == 2  # a new example must be added here, or it is unchecked


def test_the_basin_example_states_what_it_says_and_nothing_else():
    declared = StatmechModelDeclaration.model_validate(_blocks()[0])
    assert declared.scope_kind.value == "basin" and declared.conformer_group_ref is None
    assert declared.recipe.scale_application.value == "applied"
    assert declared.recipe.optical_state.value == "not_counted" and declared.recipe.nuclear_spin is None  # not stated


def test_the_ensemble_example_counts_a_record_once_and_states_no_multiplicity_it_does_not_have():
    declared = StatmechModelDeclaration.model_validate(_blocks()[1])
    assert [m.member_key for m in declared.members] == ["anti", "gauche"]
    assert [m.counting_multiplicity for m in declared.members] == [None, 2]
