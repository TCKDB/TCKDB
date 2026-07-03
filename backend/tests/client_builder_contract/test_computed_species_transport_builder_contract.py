"""Contract test for the Phase-5 ``ComputedSpeciesUpload.transport`` field.

Today's wire reality, locked in tests:

- The computed-species bundle schema (``ComputedSpeciesUploadRequest``)
  does NOT carry a transport field. The builder accepts ``transport=…``
  for forward compatibility but emits nothing on the wire — the bundle
  payload must still validate as if transport were absent.
- The standalone primitive shape (``TransportUploadPayload``) is the
  current home for transport data. ``Transport.to_payload()`` produces
  a dict that schema accepts.

Both invariants are pinned below. If the bundle schema later grows a
``transport`` field, flip the assembler in one place and update the
"no transport field on wire" assertion to "thermo-style emission".
"""

from __future__ import annotations

import pytest

pytest.importorskip("tckdb_client.builders")

from tckdb_client.builders import (
    Calculation,
    ComputedSpeciesUpload,
    Geometry,
    LevelOfTheory,
    SoftwareRelease,
    Species,
    Transport,
)

from app.schemas.workflows.computed_species_upload import (
    ComputedSpeciesUploadRequest,
)
from app.schemas.workflows.transport_upload import (
    TransportUploadPayload,
)


def _sr() -> SoftwareRelease:
    return SoftwareRelease(software="Gaussian", version="16")


def _lot() -> LevelOfTheory:
    return LevelOfTheory(method="wb97xd", basis="def2tzvp")


@pytest.fixture
def calc_trio():
    geom = Geometry.from_xyz(
        "3\nwater\n"
        "O 0.0 0.0 0.117\n"
        "H 0.0 0.757 -0.469\n"
        "H 0.0 -0.757 -0.469"
    )
    opt = Calculation.opt(
        _sr(), _lot(), output_geometry=geom,
        final_energy_hartree=-76.4, converged=True, label="opt",
    )
    freq = Calculation.freq(
        _sr(), _lot(), input_geometry=geom,
        n_imag=0, zpe_hartree=0.0214, depends_on=opt, label="freq",
    )
    sp = Calculation.sp(
        _sr(), _lot(), input_geometry=geom,
        electronic_energy_hartree=-76.45, depends_on=opt, label="sp",
    )
    return opt, freq, sp


@pytest.fixture
def water() -> Species:
    return Species(smiles="O", charge=0, multiplicity=1, label="water")


@pytest.fixture
def transport_block(calc_trio) -> Transport:
    opt, _freq, _sp = calc_trio
    return Transport(
        sigma_angstrom=2.7, epsilon_over_k_k=572.4,
        dipole_debye=1.85, polarizability_angstrom3=1.45,
        rotational_relaxation=4.0,
        source_calculations={"supporting_geometry": opt},
    )


# --- bundle-side contract --------------------------------------------


def test_bundle_payload_validates_with_transport_kwarg_present(
    water, calc_trio, transport_block,
):
    """Even with ``transport=…`` on the builder, the bundle payload
    must validate — the assembler silently drops transport until the
    bundle schema carries the field."""
    opt, freq, sp = calc_trio
    upload = ComputedSpeciesUpload(
        species=water,
        calculations=[opt, freq, sp],
        primary_calculation=opt,
        transport=transport_block,
    )
    payload = upload.to_payload()
    assert "transport" not in payload
    ComputedSpeciesUploadRequest.model_validate(payload)


def test_bundle_payload_byte_identical_with_or_without_transport(
    water, calc_trio, transport_block,
):
    """Adding the transport kwarg should not perturb the bundle payload."""
    opt, freq, sp = calc_trio
    payload_with = ComputedSpeciesUpload(
        species=water, calculations=[opt, freq, sp],
        primary_calculation=opt, transport=transport_block,
    ).to_payload()
    payload_without = ComputedSpeciesUpload(
        species=water, calculations=[opt, freq, sp],
        primary_calculation=opt,
    ).to_payload()
    assert payload_with == payload_without


# --- primitive transport contract ------------------------------------


def test_transport_to_payload_validates_against_primitive_schema(
    transport_block,
):
    """The same Transport builder can be shipped via the standalone
    ``/uploads/transport`` endpoint; ``to_payload()`` produces a dict
    that ``TransportUploadPayload`` accepts."""
    primitive = transport_block.to_payload()
    validated = TransportUploadPayload.model_validate(primitive)
    assert validated.sigma_angstrom == 2.7
    assert validated.epsilon_over_k_k == 572.4
    assert validated.dipole_debye == 1.85
    assert validated.polarizability_angstrom3 == 1.45
    assert validated.rotational_relaxation == 4.0


def test_transport_to_payload_twice_is_byte_stable(transport_block):
    p1 = transport_block.to_payload()
    p2 = transport_block.to_payload()
    v1 = TransportUploadPayload.model_validate(p1)
    v2 = TransportUploadPayload.model_validate(p2)
    assert v1.model_dump(mode="json") == v2.model_dump(mode="json")
