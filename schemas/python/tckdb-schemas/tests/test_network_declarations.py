"""Wire contract of ``tckdb_schemas.network_declarations`` (pure package, no backend)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tckdb_schemas.coded_error import CodedValidationError
from tckdb_schemas.network_declarations import (
    NETWORK_DECLARATION_VERSIONS,
    NetworkObservableDeclaration,
    NetworkProtocolDeclaration,
    NetworkTargetDeclaration,
    NetworkValidationDeclaration,
    NetworkValidityDomain,
    StoredNetworkTargetDeclaration,
    network_declaration_error,
    network_product_set_content_hash,
    validation_objective,
)

_VALIDITY = {
    "temperature_min_k": 300.0,
    "temperature_max_k": 2000.0,
    "pressure_min_bar": 0.01,
    "pressure_max_bar": 100.0,
}
_HASH = "a" * 64


def test_only_version_one_is_accepted_and_the_refusal_names_the_supported_versions() -> None:
    assert NETWORK_DECLARATION_VERSIONS == frozenset({1})
    with pytest.raises(ValidationError) as caught:
        NetworkProtocolDeclaration.model_validate({"version": 2, "reduction_method": "reservoir_state"})
    error = caught.value.errors()[0]["ctx"]["error"]
    assert isinstance(error, CodedValidationError)
    assert error.code == "network_declaration_version_unsupported"
    assert error.context == {"field": "protocol.version", "version": 2, "supported_versions": [1]}


def test_an_empty_declaration_is_not_a_declaration() -> None:
    with pytest.raises(ValidationError, match="at least one treatment"):
        NetworkProtocolDeclaration.model_validate({"version": 1})
    with pytest.raises(ValidationError, match="at least one claim"):
        NetworkTargetDeclaration.model_validate({"version": 1, "claim_origin": "source_publication"})
    with pytest.raises(ValidationError):
        NetworkValidationDeclaration.model_validate({"version": 1, "entries": []})


def test_a_validity_domain_is_ordered_on_both_axes_and_positive() -> None:
    assert NetworkValidityDomain.model_validate(_VALIDITY).pressure_max_bar == 100.0
    for patch in (
        {"temperature_min_k": 3000.0},
        {"pressure_min_bar": 1000.0},
        {"temperature_min_k": 0.0},
        {"pressure_max_bar": float("inf")},
    ):
        with pytest.raises(ValidationError):
            NetworkValidityDomain.model_validate({**_VALIDITY, **patch})


def test_an_observable_states_a_bounded_integer_order() -> None:
    base = {
        "version": 1,
        "observable": "total_loss_coefficient",
        "coefficient_basis": "kernel",
        "reaction_order": 2,
        "degeneracy_applied": False,
    }
    assert NetworkObservableDeclaration.model_validate(base).reaction_order == 2
    for order in (0, 4, 2.0, "2", True):
        with pytest.raises(ValidationError):
            NetworkObservableDeclaration.model_validate({**base, "reaction_order": order})


def test_model_fidelity_evidence_pins_its_reference_model_and_a_comparison_names_its_domain() -> None:
    entry = {"kind": "model_fidelity", "domain": _VALIDITY, "reference_solve_ref": "nsolve_x"}
    assert NetworkValidationDeclaration.model_validate({"version": 1, "entries": [entry]})
    with pytest.raises(ValidationError, match="reference_solve_ref"):
        NetworkValidationDeclaration.model_validate(
            {"version": 1, "entries": [{**entry, "reference_solve_ref": None}]}
        )
    with pytest.raises(ValidationError, match="domain"):
        NetworkValidationDeclaration.model_validate({"version": 1, "entries": [{**entry, "domain": None}]})
    with pytest.raises(ValidationError, match="reference_dataset"):
        NetworkValidationDeclaration.model_validate(
            {"version": 1, "entries": [{"kind": "representation_validation", "domain": _VALIDITY}]}
        )
    with pytest.raises(ValidationError, match="together"):
        NetworkValidationDeclaration.model_validate(
            {"version": 1, "entries": [{"kind": "uncertainty", "metric": "max_relative_error"}]}
        )
    assert validation_objective("convergence").value == "model_fidelity"
    assert validation_objective("uncertainty") is None


def test_the_topology_rule_reports_each_contradiction_with_its_own_message() -> None:
    kwargs = {"state_keys": {"a", "b"}, "channel_keys": {"c1"}, "bath_species": 1}
    base = {"version": 1, "claim_origin": "source_publication"}

    def error(**claims):
        target = NetworkTargetDeclaration.model_validate({**base, **claims})
        return network_declaration_error(target, **kwargs)

    assert error(partition={"retained": ["a", "b"]}) is None
    assert "not placed: ['b']" in error(partition={"retained": ["a"]})[1]
    assert "ghost" in error(boundaries=[{"state_key": "ghost", "kind": "open"}])[1]
    assert "specified_collider contradicts" in network_declaration_error(
        NetworkTargetDeclaration.model_validate({**base, "bath_scope": "specified_collider"}),
        state_keys={"a"},
        channel_keys=set(),
        bath_species=2,
    )[1]
    narrower = {**_VALIDITY, "temperature_max_k": 5000.0}
    assert "outside the solve's declared validity" in error(
        validity=_VALIDITY, outputs=[{"channel_key": "c1", "availability": "supplied", "validity": narrower}]
    )[1]
    assert error(
        validity=_VALIDITY,
        outputs=[{"channel_key": "c1", "availability": "supplied", "validity": {**_VALIDITY, "temperature_min_k": 400.0}}],
    ) is None


def test_the_stored_form_carries_hashes_and_refs_only() -> None:
    stored = {
        "version": 1,
        "claim_origin": "source_publication",
        "partition": {"retained": [_HASH]},
        "product_sets": [
            {
                "product_set_key": "p",
                "members": [{"determination_ref": "nkdet_x"}],
                "membership_version": 1,
                "content_hash": _HASH,
            }
        ],
    }
    assert StoredNetworkTargetDeclaration.model_validate(stored).product_sets[0].content_hash == _HASH
    with pytest.raises(ValidationError, match="composition hash"):
        StoredNetworkTargetDeclaration.model_validate({**stored, "partition": {"retained": ["local_key"]}})
    with pytest.raises(ValidationError):  # a stored product set is pinned
        StoredNetworkTargetDeclaration.model_validate(
            {**stored, "product_sets": [{"product_set_key": "p", "members": [{"determination_ref": "nkdet_x"}]}]}
        )
    with pytest.raises(ValidationError, match="public ref"):
        StoredNetworkTargetDeclaration.model_validate(
            {
                **stored,
                "product_sets": [
                    {
                        "product_set_key": "p",
                        "members": [{"determination_key": "k"}],
                        "membership_version": 1,
                        "content_hash": _HASH,
                    }
                ],
            }
        )


def test_the_membership_hash_pins_members_and_their_representations() -> None:
    one = network_product_set_content_hash([("nkdet_a", ["x"])])
    assert len(one) == 64
    assert one != network_product_set_content_hash([("nkdet_a", ["y"])])
    assert one != network_product_set_content_hash([("nkdet_b", ["x"])])
    assert one != network_product_set_content_hash([("nkdet_a", [])])  # "all fits" is not "fit x"
    assert one == network_product_set_content_hash([("nkdet_a", ["x"])])
