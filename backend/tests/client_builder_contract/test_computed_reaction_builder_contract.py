"""Contract test: ``tckdb-client`` Phase-2 reaction builder payloads
must validate against ``ComputedReactionUploadRequest``.

Lives in the backend test tree on purpose — it imports both the
client builder layer and the backend schema to assert wire-shape
alignment. The client wheel itself stays backend-independent.

If this test fails after a backend schema change, the builder layer
needs to update its payload assembly. If it fails after a builder
change, re-emit the payload via ``to_payload()`` and re-validate.
"""

from __future__ import annotations

import pytest

pytest.importorskip("tckdb_client.builders")

from tckdb_client.builders import (  # noqa: E402
    Calculation,
    ChemReaction,
    ComputedReactionUpload,
    Geometry,
    Kinetics,
    LevelOfTheory,
    Species,
    SoftwareRelease,
    TransitionState,
)

from app.schemas.workflows.computed_reaction_upload import (  # noqa: E402
    ComputedReactionUploadRequest,
)


@pytest.fixture
def minimal_upload() -> ComputedReactionUpload:
    ts_geom = Geometry.from_xyz(
        "3\nts\nC 0.0 0.0 0.0\nH 0.0 0.0 0.8\nH 0.0 0.0 -1.0"
    )
    sr = SoftwareRelease(software="Gaussian", version="16")
    lot = LevelOfTheory(method="wb97xd", basis="def2tzvp")
    ts_opt = Calculation.opt(
        sr, lot, output_geometry=ts_geom,
        final_energy_hartree=-270.55, converged=True, label="ts opt",
    )
    ts_freq = Calculation.freq(
        sr, lot, n_imag=1, imag_freq_cm1=-1200.0, zpe_hartree=0.201,
        depends_on=ts_opt, label="ts freq",
    )
    kin = Kinetics.modified_arrhenius(
        A=1.2e13, A_units="cm3/mol/s", n=0.5, Ea=10.0, Ea_units="kJ/mol",
        Tmin=300, Tmax=2500,
        source_calculations={"ts_energy": ts_opt, "freq": ts_freq},
    )
    rxn = ChemReaction(
        reactants=[
            Species(smiles="[CH3]", charge=0, multiplicity=2, label="ch3"),
            Species(smiles="[H]", charge=0, multiplicity=2, label="h"),
        ],
        products=[Species(smiles="C", charge=0, multiplicity=1, label="ch4")],
        family="H_Abstraction",
        transition_state=TransitionState(
            charge=0, multiplicity=2, geometry=ts_geom, label="ts",
        ),
        kinetics=[kin],
    )
    return ComputedReactionUpload(
        reaction=rxn, calculations=[ts_opt, ts_freq],
    )


def test_builder_payload_validates_against_backend_schema(minimal_upload):
    payload = minimal_upload.to_payload()
    validated = ComputedReactionUploadRequest.model_validate(payload)
    assert validated.reaction_family == "H_Abstraction"
    assert validated.reversible is True
    assert validated.reactant_keys == ["ch3", "h"]
    assert validated.product_keys == ["ch4"]
    assert validated.transition_state is not None
    assert validated.transition_state.calculation.type.value == "opt"
    assert len(validated.transition_state.calculations) == 1
    assert validated.transition_state.calculations[0].type.value == "freq"
    # Kinetics roles are translated to backend enum members.
    assert len(validated.kinetics) == 1
    kin = validated.kinetics[0]
    assert kin.a == 1.2e13
    assert kin.a_units.value == "cm3_mol_s"
    assert kin.reported_ea == 10.0
    assert kin.reported_ea_units.value == "kj_mol"
    assert [(sc.calculation_key, sc.role.value) for sc in kin.source_calculations] == [
        ("ts_opt", "ts_energy"),
        ("ts_freq", "freq"),
    ]


def test_builder_payload_is_deterministic_under_schema_validation(minimal_upload):
    p1 = minimal_upload.to_payload()
    p2 = minimal_upload.to_payload()
    v1 = ComputedReactionUploadRequest.model_validate(p1)
    v2 = ComputedReactionUploadRequest.model_validate(p2)
    assert v1.model_dump(mode="json") == v2.model_dump(mode="json")


def test_kcal_per_mol_input_validates_in_kj_per_mol(minimal_upload):
    """The builder's kcal/mol → kJ/mol conversion must survive schema validation."""
    g = Geometry.from_xyz("1\nx\nH 0 0 0")
    sr = SoftwareRelease(software="Gaussian", version="16")
    lot = LevelOfTheory(method="wb97xd", basis="def2tzvp")
    opt = Calculation.opt(sr, lot, output_geometry=g, converged=True)
    kin = Kinetics.modified_arrhenius(
        A=1.0e13, A_units="cm3_mol_s", n=0.5, Ea=10.0, Ea_units="kcal/mol",
        source_calculations={"ts_energy": opt},
    )
    rxn = ChemReaction(
        reactants=[Species(smiles="[CH3]", charge=0, multiplicity=2)],
        products=[Species(smiles="C", charge=0, multiplicity=1)],
        family="H_Abstraction",
        transition_state=TransitionState(charge=0, multiplicity=2, geometry=g),
        kinetics=[kin],
    )
    upload = ComputedReactionUpload(reaction=rxn, calculations=[opt])
    validated = ComputedReactionUploadRequest.model_validate(upload.to_payload())
    assert validated.kinetics[0].reported_ea == pytest.approx(41.84)
    assert validated.kinetics[0].reported_ea_units.value == "kj_mol"
