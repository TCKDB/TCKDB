"""A ``depends_on`` ring is a 422 that names the depositor's own keys, before any write.

The database refuses a cycle too (``trg_calculation_dependency_acyclic``), but by then a row id is in
the driver's error text. The bundle schemas stop it first, on the sync and the async path alike, since both
validate the payload before anything is written.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tckdb_schemas.bundle_source_rules import find_dependency_cycle
from tckdb_schemas.workflows.computed_reaction_upload import ComputedReactionUploadRequest

from app.schemas.workflows.computed_species_upload import ComputedSpeciesUploadRequest
from tests.client_builder_contract.test_computed_reaction_calculation_keys_response import _bundle_payload
from tests.schemas.test_computed_species_upload_schema import _bundle, _calc, _conformer


def test_find_dependency_cycle_distinguishes_a_ring_from_a_diamond():
    assert find_dependency_cycle({"a": ["b"], "b": ["a"]}) is not None
    assert sorted(find_dependency_cycle({"a": ["b"], "b": ["c"], "c": ["a"]})) == ["a", "b", "c"]
    # one calculation the parent of two others on converging paths is legal
    assert find_dependency_cycle({"d": ["b", "c"], "b": ["a"], "c": ["a"]}) is None
    assert find_dependency_cycle({}) is None


def _species_payload(first_parent: str | None, second_parent: str | None) -> dict:
    conf = _conformer(key="c0", primary_key="opt0")
    conf["primary_calculation"]["depends_on"] = (
        [{"parent_calculation_key": first_parent, "role": "optimized_from"}] if first_parent else []
    )
    conf["additional_calculations"] = [
        _calc(
            "freq0",
            calc_type="freq",
            depends_on=[{"parent_calculation_key": second_parent, "role": "freq_on"}] if second_parent else [],
        )
    ]
    return _bundle(conformers=[conf])


def test_a_species_bundle_with_a_depends_on_ring_is_refused_naming_its_keys():
    with pytest.raises(ValidationError) as exc:
        ComputedSpeciesUploadRequest(**_species_payload("freq0", "opt0"))
    message = str(exc.value)
    assert "depends_on forms a cycle" in message
    assert "opt0" in message and "freq0" in message


def test_a_species_bundle_with_a_one_way_dependency_is_accepted():
    ComputedSpeciesUploadRequest(**_species_payload(None, "opt0"))


def test_a_reaction_bundle_with_a_depends_on_ring_is_refused_naming_its_keys():
    payload = _bundle_payload()
    ts = payload["transition_state"]
    ts["calculation"]["depends_on"] = [{"parent_calculation_key": "ts-freq", "role": "optimized_from"}]
    ts["calculations"][0]["depends_on"] = [{"parent_calculation_key": "ts-opt", "role": "freq_on"}]
    with pytest.raises(ValidationError) as exc:
        ComputedReactionUploadRequest(**payload)
    message = str(exc.value)
    assert "depends_on forms a cycle" in message
    assert "ts-opt" in message and "ts-freq" in message


def test_a_reaction_bundle_with_a_one_way_dependency_is_accepted():
    payload = _bundle_payload()
    payload["transition_state"]["calculations"][0]["depends_on"] = [
        {"parent_calculation_key": "ts-opt", "role": "freq_on"}
    ]
    ComputedReactionUploadRequest(**payload)
