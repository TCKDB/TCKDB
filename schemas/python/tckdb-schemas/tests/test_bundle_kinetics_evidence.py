"""``BundleKineticsIn`` carries ``t0_k`` and the standalone route's kinetics evidence (#620).

These run against the wire package alone, with no backend: the evidence models
and their cross-field checks live in
``tckdb_schemas.fragments.kinetics_evidence`` and are used by both kinetics
routes, so the bundle cannot accept a block the standalone route would refuse.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tckdb_schemas.fragments import kinetics_evidence
from tckdb_schemas.workflows.computed_reaction_upload import BundleKineticsIn

_CONVENTIONS = {
    "ensemble_policy": "single_structure",
    "standard_state_convention": "ideal_gas_1_bar",
    "degeneracy_interpretation": "reaction_path_degeneracy",
}


def _kinetics(**overrides) -> dict:
    body = {
        "reactant_keys": ["a", "b"],
        "product_keys": ["c"],
        "scientific_origin": "computed",
        "a": 1.0e10,
        "a_units": "cm3_mol_s",
        "n": 2.0,
    }
    body.update(overrides)
    return body


def _set(*, with_ts: bool = False) -> list[dict]:
    rows = [
        {"role": "reactant", "participant_index": 1, "statmech_ref": "sm_a", **_CONVENTIONS},
        {"role": "reactant", "participant_index": 2, "statmech_ref": "sm_b", **_CONVENTIONS},
        {"role": "product", "participant_index": 1, "statmech_ref": "sm_c", **_CONVENTIONS},
    ]
    if with_ts:
        rows.append(
            {
                "role": "transition_state",
                "statmech_ref": "sm_ts",
                "transition_state_entry_ref": "tse_1",
                **_CONVENTIONS,
            }
        )
    return rows


def test_t0_k_defaults_to_one_kelvin_and_round_trips():
    assert BundleKineticsIn.model_validate(_kinetics()).t0_k == 1.0
    parsed = BundleKineticsIn.model_validate(_kinetics(t0_k=298.15))
    assert BundleKineticsIn.model_validate(parsed.model_dump(mode="json")).t0_k == 298.15


@pytest.mark.parametrize("bad", [0.0, -1.0, float("inf"), float("nan")])
def test_t0_k_must_be_finite_and_positive(bad):
    with pytest.raises(ValidationError, match="t0_k"):
        BundleKineticsIn.model_validate(_kinetics(t0_k=bad))


def test_a_complete_interpretation_set_with_a_tunneling_block_is_accepted():
    parsed = BundleKineticsIn.model_validate(
        _kinetics(
            interpretation_assignments=_set(with_ts=True),
            tunneling_application={
                "model": "wigner",
                "imaginary_frequency_cm1": -1500.0,
                "transition_state_entry_ref": "tse_1",
            },
            network_kinetics_ref="nkin_1",
        )
    )
    assert len(parsed.interpretation_assignments) == 4
    assert parsed.network_kinetics_ref == "nkin_1"
    assert parsed.tunneling_model.value == "wigner", "the label is filled from the evidence"


def test_the_bundle_uses_the_evidence_classes_the_standalone_route_uses():
    assert BundleKineticsIn.model_fields["tunneling_application"].annotation == (
        kinetics_evidence.KineticsTunnelingApplicationUpload | None
    )
    assert BundleKineticsIn.model_fields["interpretation_assignments"].annotation == list[
        kinetics_evidence.KineticsInterpretationAssignmentUpload
    ]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"interpretation_assignments": _set()[:-1]}, "missing: ['product:1']"),
        ({"interpretation_assignments": _set() + _set()[:1]}, "must be unique by role"),
        (
            {"interpretation_assignments": _set()},
            None,
        ),
        (
            {
                "interpretation_assignments": _set(),
                "tunneling_application": {
                    "model": "wigner",
                    "imaginary_frequency_cm1": -1.0,
                    "transition_state_entry_ref": "tse_1",
                },
            },
            "missing: ['transition_state']",
        ),
        (
            {
                "tunneling_model": "eckart",
                "tunneling_application": {
                    "model": "wigner",
                    "imaginary_frequency_cm1": -1.0,
                    "transition_state_entry_ref": "tse_1",
                },
            },
            "tunneling_application.model must match tunneling_model",
        ),
        (
            {
                "tunneling_application": {
                    "model": "eckart",
                    "imaginary_frequency_cm1": -1.0,
                    "transition_state_entry_ref": "tse_1",
                }
            },
            "Eckart tunneling requires reactant/product energies",
        ),
    ],
    ids=[
        "partial-set",
        "duplicate-subject",
        "complete-set-is-accepted",
        "tunneling-needs-the-ts-subject",
        "label-disagrees",
        "eckart-without-barriers",
    ],
)
def test_the_shared_validators_refuse_what_the_standalone_route_refuses(overrides, message):
    if message is None:
        BundleKineticsIn.model_validate(_kinetics(**overrides))
        return
    with pytest.raises(ValidationError) as excinfo:
        BundleKineticsIn.model_validate(_kinetics(**overrides))
    assert message in str(excinfo.value)


def test_a_participant_index_past_the_declared_list_is_refused():
    rows = _set()
    rows[2] = {**rows[2], "participant_index": 2}  # one product declared
    with pytest.raises(ValidationError, match="outside the declared product list"):
        BundleKineticsIn.model_validate(_kinetics(interpretation_assignments=rows))
