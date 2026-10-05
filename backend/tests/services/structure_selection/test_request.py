"""The normalised request: what each grain and intent allows, and the manifest round trip."""

from __future__ import annotations

import pytest

from app.db.models.common import CalculationQuality, RecordReviewStatus
from app.services.structure_selection.models import (
    DEFAULT_PERMITTED_QUALITY,
    AdminPolicy,
    CoverageRequirement,
    Grain,
    Intent,
    Quantity,
    ResultMode,
    SelectionBounds,
    StructureRequest,
    ValidationClaim,
)


def test_every_grain_accepts_only_its_intents():
    assert StructureRequest(Grain.calculation, Intent.recorded_minimum)
    assert StructureRequest(Grain.conformer, Intent.validated_minimum)
    assert StructureRequest(Grain.transition_state, Intent.validated_saddle)
    for grain, intent in (
        (Grain.calculation, Intent.validated_minimum),
        (Grain.calculation, Intent.validated_saddle),
        (Grain.conformer, Intent.recorded_minimum),
        (Grain.conformer, Intent.validated_saddle),
        (Grain.transition_state, Intent.recorded_minimum),
        (Grain.transition_state, Intent.validated_minimum),
    ):
        with pytest.raises(ValueError, match="not available at the"):
            StructureRequest(grain, intent)


def test_evidence_qualification_asks_for_no_energy_and_names_its_claim():
    request = StructureRequest(
        Grain.transition_state, Intent.qualify_evidence, quantity=None, validation_claim=ValidationClaim.reactive_connectivity
    )
    assert request.quantity is None and request.effective_claim is ValidationClaim.reactive_connectivity
    with pytest.raises(ValueError, match="asks for no energy"):
        StructureRequest(
            Grain.transition_state,
            Intent.qualify_evidence,
            quantity=Quantity.electronic_energy,
            validation_claim=ValidationClaim.first_order_saddle,
        )
    with pytest.raises(ValueError, match="states the validation_claim"):
        StructureRequest(Grain.transition_state, Intent.qualify_evidence, quantity=None)


def test_an_energy_intent_states_its_quantity():
    with pytest.raises(ValueError, match="state the quantity"):
        StructureRequest(Grain.calculation, Intent.recorded_minimum, quantity=None)


def test_a_validated_intent_implies_its_claim_and_a_claim_must_fit_intent_and_grain():
    assert StructureRequest(Grain.conformer, Intent.validated_minimum).effective_claim is ValidationClaim.local_minimum
    assert StructureRequest(Grain.transition_state, Intent.validated_saddle).effective_claim is ValidationClaim.first_order_saddle
    assert (
        StructureRequest(
            Grain.transition_state, Intent.validated_saddle, validation_claim=ValidationClaim.higher_order_saddle
        ).effective_claim
        is ValidationClaim.higher_order_saddle
    )
    for grain, intent, claim in (
        (Grain.conformer, Intent.validated_minimum, ValidationClaim.first_order_saddle),
        (Grain.transition_state, Intent.validated_saddle, ValidationClaim.local_minimum),
        (Grain.transition_state, Intent.validated_saddle, ValidationClaim.reactive_connectivity),
        (Grain.calculation, Intent.recorded_minimum, ValidationClaim.local_minimum),
    ):
        with pytest.raises(ValueError, match="does not fit"):
            StructureRequest(grain, intent, validation_claim=claim)


def test_connectivity_is_a_transition_state_requirement_only():
    assert StructureRequest(Grain.transition_state, Intent.validated_saddle, require_connectivity=True)
    with pytest.raises(ValueError, match="transition_state grain"):
        StructureRequest(Grain.conformer, Intent.validated_minimum, require_connectivity=True)


def test_members_are_named_once_and_never_empty():
    assert StructureRequest(Grain.calculation, Intent.recorded_minimum, member_refs=("calc_a", "calc_b"))
    with pytest.raises(ValueError, match="twice"):
        StructureRequest(Grain.calculation, Intent.recorded_minimum, member_refs=("calc_a", "calc_a"))
    with pytest.raises(ValueError, match="names no member"):
        StructureRequest(Grain.calculation, Intent.recorded_minimum, member_refs=())


def test_rejected_quality_is_never_permitted_unless_the_caller_names_it():
    assert CalculationQuality.rejected not in DEFAULT_PERMITTED_QUALITY
    default = StructureRequest(Grain.calculation, Intent.recorded_minimum)
    assert default.quality_set == DEFAULT_PERMITTED_QUALITY
    strict = StructureRequest(Grain.calculation, Intent.recorded_minimum, permitted_quality=frozenset({CalculationQuality.curated}))
    assert strict.quality_set == frozenset({CalculationQuality.curated})


@pytest.mark.parametrize(
    "request_",
    [
        StructureRequest(Grain.calculation, Intent.recorded_minimum),
        StructureRequest(
            Grain.calculation,
            Intent.recorded_minimum,
            quantity=Quantity.zero_kelvin_energy,
            coverage=CoverageRequirement.all_requested_members,
            min_review_status=RecordReviewStatus.approved,
            permitted_quality=frozenset({CalculationQuality.curated}),
            geometry_ref="geom_x",
            member_refs=("calc_a",),
            recipe={"version": 1, "electronic_state": {"state": "known", "root": 0}},
            require_stable_reference=True,
            admin_policy=AdminPolicy.earliest,
            result_mode=ResultMode.first,
            apply_rules=False,
            bounds=SelectionBounds(version="9", candidates=3, nested_rows=4, dependency_depth=2, manifest_bytes=5),
        ),
        StructureRequest(Grain.transition_state, Intent.validated_saddle, require_connectivity=True),
        StructureRequest(
            Grain.transition_state, Intent.qualify_evidence, quantity=None, validation_claim=ValidationClaim.reactive_connectivity
        ),
    ],
)
def test_a_request_round_trips_through_its_manifest_form(request_):
    assert StructureRequest.from_dict(request_.to_dict()) == request_


@pytest.mark.parametrize(
    ("grain", "intent", "claim"),
    [
        (Grain.conformer, Intent.validated_minimum, "local_minimum"),
        (Grain.conformer, Intent.protocol_preferred, "local_minimum"),
        (Grain.transition_state, Intent.validated_saddle, "first_order_saddle"),
        (Grain.transition_state, Intent.protocol_preferred, "first_order_saddle"),
    ],
)
def test_every_structure_consuming_intent_defaults_to_the_conventional_characterization(grain, intent, claim):
    from app.services.structure_selection import models

    # Protocol preference states its objective once the decision stage exists; before it, there is nothing to state.
    objective = getattr(models, "Objective", None)
    extra = {"objective": objective.physical_accuracy} if objective is not None and intent is Intent.protocol_preferred else {}
    request = StructureRequest(grain, intent, **extra)
    assert request.effective_claim.value == claim and request.to_dict()["validation_claim"] == claim
    # A calculation-grain request states no structural claim at all.
    assert StructureRequest(Grain.calculation, Intent.recorded_minimum).effective_claim is None
