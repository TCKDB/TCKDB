"""The SDK builders can state a thermo record's target and protocol.

Both are optional attributed claims. The builder refuses a self-contradicting
one before any HTTP request, using the same rule the server runs, and emits the
wire shape the bundle schemas accept: a single-conformer target names the
upload's own conformer by bundle key, and the protocol's supporting
calculations are named by bundle key too.
"""

from __future__ import annotations

import pytest
from tckdb_schemas.thermo_declarations import ThermoProtocolDeclaration
from tckdb_schemas.workflows.computed_reaction_upload import ComputedReactionUploadRequest
from tckdb_schemas.workflows.computed_species_upload import ComputedSpeciesUploadRequest

from tckdb_client.builders import (
    Calculation,
    ChemReaction,
    ComputedReactionUpload,
    ComputedSpeciesUpload,
    Geometry,
    LevelOfTheory,
    SoftwareRelease,
    Species,
    TCKDBBuilderValidationError,
    Thermo,
    TransitionState,
)

REFERENCE = "formation_298k"
G4 = {
    "version": 1,
    "recipe": {"name": "g4"},
    "formation_reference": {"derivation": "atomization", "reference_data_source": "atct"},
    "thermal_approximation": {"ensemble_representation": "lowest_conformer"},
    "departures": [],
}


def _scalar(**extra) -> Thermo:
    return Thermo.scalar(enthalpy_reference_kind=REFERENCE, h298_kj_mol=-241.8, **extra)


# ---------------------------------------------------------------------------
# Refused before any request
# ---------------------------------------------------------------------------


def test_no_declaration_emits_neither_key():
    payload = _scalar().to_payload()
    assert "thermodynamic_target" not in payload and "protocol" not in payload


def test_an_equilibrium_target_emits_without_a_conformer():
    assert _scalar(thermodynamic_target="equilibrium_ensemble").to_payload()["thermodynamic_target"] == {
        "kind": "equilibrium_ensemble"
    }


@pytest.mark.parametrize("kind", ["ground_state", "Equilibrium_Ensemble", ""])
def test_an_unrecognised_target_kind_is_refused(kind):
    with pytest.raises(TCKDBBuilderValidationError, match="thermo_declaration_invalid"):
        _scalar(thermodynamic_target=kind)


def test_a_protocol_with_an_unknown_field_or_version_is_refused():
    with pytest.raises(TCKDBBuilderValidationError, match="Thermo.protocol"):
        _scalar(protocol={**G4, "basis_quality": "high"})
    with pytest.raises(TCKDBBuilderValidationError, match="thermo_protocol_version_unsupported"):
        _scalar(protocol={**G4, "version": 2})


def test_a_protocol_may_not_carry_its_own_supporting_calculation_keys():
    with pytest.raises(TCKDBBuilderValidationError, match="protocol_calculations"):
        _scalar(protocol={**G4, "supporting_calculations": [{"calculation_key": "calc_0"}]})


def test_protocol_calculations_need_a_protocol():
    sr, lot = SoftwareRelease(software="Gaussian", version="16"), LevelOfTheory(method="wb97xd", basis="def2tzvp")
    opt = Calculation.opt(sr, lot, output_geometry=Geometry.from_xyz("1\nH\nH 0 0 0"), converged=True)
    with pytest.raises(TCKDBBuilderValidationError, match="needs a protocol"):
        _scalar(protocol_calculations=[opt])


def test_departures_empty_list_survives_into_the_payload():
    """"No departures" is a statement; omitting the key says nothing."""
    declared = _scalar(protocol=G4).to_payload()["protocol"]
    assert declared["departures"] == []
    omitted = _scalar(protocol={"version": 1, "recipe": {"name": "g4"}}).to_payload()["protocol"]
    assert "departures" not in omitted


def test_a_typed_protocol_model_is_accepted():
    model = ThermoProtocolDeclaration.model_validate(G4)
    assert _scalar(protocol=model).to_payload()["protocol"] == G4


def test_a_single_conformer_target_without_the_upload_conformer_is_refused_at_emission():
    thermo = _scalar(thermodynamic_target="single_conformer")
    with pytest.raises(TCKDBBuilderValidationError, match="declares none for the species"):
        thermo.to_payload()


# ---------------------------------------------------------------------------
# ComputedSpeciesUpload
# ---------------------------------------------------------------------------


@pytest.fixture
def water_geom() -> Geometry:
    return Geometry.from_xyz("3\nwater\nO 0.0 0.0 0.117\nH 0.0 0.757 -0.469\nH 0.0 -0.757 -0.469")


def _species_calcs(water_geom):
    sr = SoftwareRelease(software="Gaussian", version="16")
    lot = LevelOfTheory(method="wb97xd", basis="def2tzvp")
    opt = Calculation.opt(sr, lot, output_geometry=water_geom, final_energy_hartree=-76.4, converged=True, label="opt")
    sp = Calculation.sp(
        sr, lot, input_geometry=water_geom, electronic_energy_hartree=-76.45, depends_on=opt, label="sp"
    )
    return opt, sp


def _species_upload(thermo: Thermo, opt, sp):
    return ComputedSpeciesUpload(
        species=Species(smiles="O", charge=0, multiplicity=1, label="water"),
        calculations=[opt, sp],
        primary_calculation=opt,
        thermo=thermo,
    )


def test_species_upload_names_its_own_conformer_and_the_protocols_calculations_by_key(water_geom):
    opt, sp = _species_calcs(water_geom)
    thermo = _scalar(thermodynamic_target="single_conformer", protocol=G4, protocol_calculations=[opt, sp])
    payload = _species_upload(thermo, opt, sp).to_payload()

    conformer_key = payload["conformers"][0]["key"]
    assert payload["thermo"]["thermodynamic_target"] == {"kind": "single_conformer", "conformer_key": conformer_key}
    primary_key = payload["conformers"][0]["primary_calculation"]["key"]
    sp_key = payload["conformers"][0]["additional_calculations"][0]["key"]
    assert payload["thermo"]["protocol"]["supporting_calculations"] == [
        {"calculation_key": primary_key},
        {"calculation_key": sp_key},
    ]
    # And the server's own schema accepts exactly what the builder emitted.
    request = ComputedSpeciesUploadRequest.model_validate(payload)
    assert request.thermo.thermodynamic_target.conformer_key == conformer_key
    assert request.thermo.protocol.recipe.name.value == "g4"


def test_species_upload_payload_is_deterministic_with_declarations(water_geom):
    opt, sp = _species_calcs(water_geom)
    upload = _species_upload(_scalar(thermodynamic_target="single_conformer", protocol=G4), opt, sp)
    assert upload.to_payload() == upload.to_payload()


def test_species_upload_without_declarations_is_unchanged(water_geom):
    opt, sp = _species_calcs(water_geom)
    upload = _species_upload(_scalar(), opt, sp)
    thermo = upload.to_payload()["thermo"]
    assert "thermodynamic_target" not in thermo and "protocol" not in thermo
    ComputedSpeciesUploadRequest.model_validate(upload.to_payload())


# ---------------------------------------------------------------------------
# ComputedReactionUpload
# ---------------------------------------------------------------------------


def _ch4_opt():
    sr = SoftwareRelease(software="Gaussian", version="16")
    lot = LevelOfTheory(method="wb97xd", basis="def2tzvp")
    ch4_geom = Geometry.from_xyz("5\nch4\nC 0 0 0\nH 0 0 1\nH 0 0 -1\nH 0 1 0\nH 0 -1 0")
    return Calculation.opt(sr, lot, output_geometry=ch4_geom, converged=True, final_energy_hartree=-40.5, label="ch4 opt")


def _reaction_upload(thermo: Thermo, ch4_opt=None):
    sr = SoftwareRelease(software="Gaussian", version="16")
    lot = LevelOfTheory(method="wb97xd", basis="def2tzvp")
    ts_geom = Geometry.from_xyz("3\nts\nC 0 0 0\nH 0 0 0.8\nH 0 0 -1.0")
    ch4 = Species(smiles="C", charge=0, multiplicity=1, label="CH4")
    ch3 = Species(smiles="[CH3]", charge=0, multiplicity=2, label="CH3")
    ts_opt = Calculation.opt(sr, lot, output_geometry=ts_geom, converged=True)
    ts_freq = Calculation.freq(sr, lot, n_imag=1, imag_freq_cm1=-1200.0, depends_on=ts_opt)
    rxn = ChemReaction(
        reactants=[ch3],
        products=[ch4],
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=ts_geom),
    )
    return ComputedReactionUpload(
        reaction=rxn,
        calculations=[ts_opt, ts_freq],
        species_calculations={ch4: [ch4_opt]} if ch4_opt is not None else {},
        species_thermo={ch4: thermo},
    )


def test_reaction_upload_resolves_target_and_protocol_keys_in_the_species_block():
    ch4_opt = _ch4_opt()
    thermo = _scalar(thermodynamic_target="single_conformer", protocol=G4, protocol_calculations=[ch4_opt])
    payload = _reaction_upload(thermo, ch4_opt).to_payload()
    block = next(sp for sp in payload["species"] if sp["key"] == "ch4")

    (conformer,) = block["conformers"]
    assert block["thermo"]["thermodynamic_target"] == {"kind": "single_conformer", "conformer_key": conformer["key"]}
    assert block["thermo"]["protocol"]["supporting_calculations"] == [
        {"calculation_key": conformer["calculation"]["key"]}
    ]
    request = ComputedReactionUploadRequest.model_validate(payload)
    ch4 = next(sp for sp in request.species if sp.key == "ch4")
    assert ch4.thermo.thermodynamic_target.conformer_key == conformer["key"]
    # The slot kept its place in the block, so key order does not depend on the declaration.
    plain_ch4_opt = _ch4_opt()
    plain = _reaction_upload(_scalar(), plain_ch4_opt).to_payload()
    plain_block = next(sp for sp in plain["species"] if sp["key"] == "ch4")
    assert list(block) == list(plain_block)


def test_reaction_upload_single_conformer_without_species_calculations_is_refused():
    upload = _reaction_upload(_scalar(thermodynamic_target="single_conformer"))
    with pytest.raises(TCKDBBuilderValidationError, match="declares none for the species"):
        upload.to_payload()


def test_reaction_upload_equilibrium_target_needs_no_conformer():
    upload = _reaction_upload(_scalar(thermodynamic_target="equilibrium_ensemble", protocol=G4))
    block = next(sp for sp in upload.to_payload()["species"] if sp["key"] == "ch4")
    assert block["thermo"]["thermodynamic_target"] == {"kind": "equilibrium_ensemble"}
    assert block["thermo"]["protocol"] == G4
