"""The SDK builder can state a kinetics fit's direction, determination, applicability and protocol.

All four are optional attributed claims. The builder refuses a self-contradicting one before any
HTTP request, using the same rule the server runs, never defaults one, and emits the wire shape
the bundle schema accepts: a protocol's supporting calculations are named by bundle key.
"""

from __future__ import annotations

import pytest
from tckdb_schemas.kinetics_declarations import KineticsProtocolDeclaration
from tckdb_schemas.workflows.computed_reaction_upload import ComputedReactionUploadRequest

from tckdb_client.builders import (
    Calculation,
    ChemReaction,
    ComputedReactionUpload,
    Geometry,
    Kinetics,
    LevelOfTheory,
    SoftwareRelease,
    Species,
    TCKDBBuilderValidationError,
    TransitionState,
)

DETERMINATION = {"key": "arkane-run-1", "target_kind": "whole_reaction", "representation_role": "complete"}
APPLICABILITY = {"version": 1, "phase": "gas", "pressure_dependence": "independent", "claim_origin": "depositor_interpretation"}
PROTOCOL = {"version": 1, "method_kind": "saddle_point_tst", "barrier_basis": "zpe_corrected", "departures": []}


def _kinetics(**extra) -> Kinetics:
    return Kinetics.modified_arrhenius(A=1.0e10, A_units="cm3/mol/s", n=2.0, Ea=10.0, **extra)


def _payload(kinetics: Kinetics, lookup=lambda calc: "calc_0") -> dict:
    return kinetics.to_payload(reactant_keys=["a", "b"], product_keys=["c"], calc_key_lookup=lookup)


def test_nothing_declared_emits_none_of_the_four_keys():
    payload = _payload(_kinetics())
    assert not {"direction", "determination", "applicability", "protocol"} & set(payload)


def test_a_declaration_is_emitted_as_the_wire_dict_and_nothing_is_defaulted():
    payload = _payload(
        _kinetics(direction="forward", determination=DETERMINATION, applicability=APPLICABILITY, protocol=PROTOCOL)
    )
    assert payload["direction"] == "forward"
    assert payload["determination"] == DETERMINATION
    assert payload["applicability"] == APPLICABILITY
    # "No departures" is a statement and survives; omitted says nothing.
    assert payload["protocol"] == PROTOCOL and payload["protocol"]["departures"] == []
    unstated = _payload(_kinetics(protocol={"version": 1, "method_kind": "saddle_point_tst"}))
    assert "departures" not in unstated["protocol"]


def test_a_typed_declaration_model_is_accepted():
    model = KineticsProtocolDeclaration.model_validate(PROTOCOL)
    assert _payload(_kinetics(protocol=model))["protocol"] == PROTOCOL


@pytest.mark.parametrize("direction", ["reverse", "backward", ""])
def test_a_bundle_fit_cannot_state_a_reverse_direction(direction):
    with pytest.raises(TCKDBBuilderValidationError, match="Kinetics.direction"):
        _kinetics(direction=direction)
    assert _payload(_kinetics(direction="net"))["direction"] == "net"


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"determination": {"representation_role": "complete"}}, "kinetics_determination_invalid"),
        (
            {"determination": {"key": "k", "target_kind": "whole_reaction", "representation_role": "complete"}},
            "kinetics_determination_invalid",  # no direction stated
        ),
        ({"applicability": {**APPLICABILITY, "version": 2}}, "kinetics_declaration_version_unsupported"),
        ({"applicability": {"version": 1, "phase": "gas"}}, "Kinetics.applicability"),
        ({"applicability": {**APPLICABILITY, "pressure_dependence": "fixed_pressure"}}, "kinetics_declaration_contradicts_record"),
        ({"protocol": {**PROTOCOL, "version": 2}}, "kinetics_declaration_version_unsupported"),
        ({"protocol": {**PROTOCOL, "basis_quality": "high"}}, "Kinetics.protocol"),
        ({"protocol": {"version": 1, "method_kind": "experimental"}}, "kinetics_declaration_contradicts_record"),
    ],
    ids=[
        "no_locator", "determination_without_direction", "applicability_version", "applicability_no_origin",
        "fixed_pressure_without_context", "protocol_version", "protocol_unknown_field", "experimental_method_on_a_computed_fit",
    ],
)
def test_a_self_contradicting_declaration_is_refused_before_any_request(kwargs, match):
    with pytest.raises(TCKDBBuilderValidationError, match=match):
        _kinetics(**kwargs)


def test_a_determination_is_accepted_once_its_direction_is_stated():
    assert _kinetics(direction="forward", determination=DETERMINATION)


def test_a_protocol_may_not_carry_its_own_supporting_calculation_keys():
    protocol = {**PROTOCOL, "supporting_calculations": [{"calculation_key": "calc_0", "purpose": "geometry"}]}
    with pytest.raises(TCKDBBuilderValidationError, match="protocol_calculations"):
        _kinetics(protocol=protocol)


def test_protocol_calculations_need_a_protocol_and_a_purpose():
    sr, lot = SoftwareRelease(software="Gaussian", version="16"), LevelOfTheory(method="wb97xd", basis="def2tzvp")
    opt = Calculation.opt(sr, lot, output_geometry=Geometry.from_xyz("1\nH\nH 0 0 0"), converged=True)
    with pytest.raises(TCKDBBuilderValidationError, match="needs a protocol"):
        _kinetics(protocol_calculations=[("geometry", opt)])
    with pytest.raises(TCKDBBuilderValidationError, match=r"\(purpose, Calculation\)"):
        _kinetics(protocol=PROTOCOL, protocol_calculations=[(None, opt)])


def _reaction_upload(kinetics: Kinetics, ts_opt, ts_freq):
    ts_geom = Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")
    rxn = ChemReaction(
        reactants=[Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")],
        products=[Species(smiles="C", charge=0, multiplicity=1, label="CH4")],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
        kinetics=[kinetics],
    )
    return ComputedReactionUpload(reaction=rxn, calculations=[ts_opt, ts_freq])


def test_a_reaction_upload_names_the_protocols_calculations_by_bundle_key_and_the_server_accepts_it():
    sr = SoftwareRelease(software="Gaussian", version="16")
    lot = LevelOfTheory(method="wb97xd", basis="def2tzvp")
    ts_geom = Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    ts_freq = Calculation.freq(sr, lot, n_imag=1, imag_freq_cm1=-1200.0, depends_on=ts_opt)
    kinetics = _kinetics(
        direction="forward",
        determination=DETERMINATION,
        applicability=APPLICABILITY,
        protocol=PROTOCOL,
        protocol_calculations=[("frequency", ts_freq), ("geometry", ts_opt)],
    )
    upload = _reaction_upload(kinetics, ts_opt, ts_freq)
    upload_with_source = upload.to_payload()
    # The bundle-level source the determination is scoped to.
    upload_with_source["workflow_tool_release"] = {"name": "Arkane", "version": "3.2"}
    request = ComputedReactionUploadRequest.model_validate(upload_with_source)
    (fit,) = request.kinetics
    assert fit.determination.key == "arkane-run-1" and fit.direction.value == "forward"
    assert [(c.purpose.value, c.calculation_key is not None) for c in fit.protocol.supporting_calculations] == [
        ("frequency", True),
        ("geometry", True),
    ]
    assert upload.to_payload() == upload.to_payload(), "emission is deterministic"


def test_a_protocol_calculation_that_is_not_in_the_upload_is_refused_at_assembly():
    sr = SoftwareRelease(software="Gaussian", version="16")
    lot = LevelOfTheory(method="wb97xd", basis="def2tzvp")
    ts_geom = Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    ts_freq = Calculation.freq(sr, lot, n_imag=1, imag_freq_cm1=-1200.0, depends_on=ts_opt)
    stray = Calculation.opt(sr, lot, output_geometry=Geometry.from_xyz("1\nH\nH 0 0 0"), converged=True)
    kinetics = _kinetics(protocol=PROTOCOL, protocol_calculations=[("geometry", stray)])
    with pytest.raises(TCKDBBuilderValidationError, match="protocol_calculation"):
        _reaction_upload(kinetics, ts_opt, ts_freq).to_payload()


def test_a_reaction_upload_without_declarations_is_unchanged():
    sr = SoftwareRelease(software="Gaussian", version="16")
    lot = LevelOfTheory(method="wb97xd", basis="def2tzvp")
    ts_geom = Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    ts_freq = Calculation.freq(sr, lot, n_imag=1, imag_freq_cm1=-1200.0, depends_on=ts_opt)
    payload = _reaction_upload(_kinetics(), ts_opt, ts_freq).to_payload()
    (fit,) = payload["kinetics"]
    assert not {"direction", "determination", "applicability", "protocol"} & set(fit)
    ComputedReactionUploadRequest.model_validate(payload)
