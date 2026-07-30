"""Regression coverage for the typed execution-environment builder seam."""

from __future__ import annotations

from tckdb_client.builders.calculation import Calculation, LevelOfTheory, SoftwareRelease
from tckdb_client.builders.uploads import ComputedReactionUpload, ComputedSpeciesUpload


class _KeyMinter:
    def lookup(self, _calc):
        return "calc-1"


def _environment() -> dict:
    return {
        "schema_version": "tckdb.execution-environment.v1",
        "software_release": {"name": "Gaussian", "version": "16"},
        "runtime": {"runtime_kind": "container", "image": "registry.example/arc@sha256:" + "a" * 64},
        "executable": {"locator": "file:///opt/arc/bin/arc", "digest": "sha256:" + "b" * 64},
        "closure": [
            {"role": "runtime", "locator": "registry.example/arc@sha256:" + "a" * 64, "digest": "sha256:" + "a" * 64},
            {"role": "executable", "locator": "file:///opt/arc/bin/arc", "digest": "sha256:" + "b" * 64},
        ],
    }


def test_calculation_builder_validates_and_preserves_typed_environment():
    calc = Calculation.sp(
        SoftwareRelease("Gaussian", "16"),
        LevelOfTheory("wb97xd", "def2-svp"),
        electronic_energy_hartree=-40.0,
        execution_environment=_environment(),
    )
    assert calc.execution_environment.content_digest().startswith("sha256:")


def test_species_and_reaction_builder_wires_environment_on_the_calculation_payload():
    # The upload constructors are deliberately not exercised here: their
    # scientific owner fixtures vary, while both wire methods share this one
    # calculation serialization contract.
    calc = Calculation.sp(
        SoftwareRelease("Gaussian", "16"),
        LevelOfTheory("wb97xd", "def2-svp"),
        electronic_energy_hartree=-40.0,
        execution_environment=_environment(),
    )
    species_payload = ComputedSpeciesUpload._calc_payload(object(), calc, _KeyMinter())
    reaction_payload = ComputedReactionUpload._calc_payload_flat(
        object(), calc, key="calc-1", calc_keys=_KeyMinter(), geometry_key=None
    )
    assert species_payload["execution_environment"] == reaction_payload["execution_environment"]
    assert species_payload["execution_environment"]["runtime"]["runtime_kind"] == "container"


def _described_environment() -> dict:
    """What an uploader on a shared cluster can actually supply."""
    return {
        "schema_version": "tckdb.execution-environment.v1",
        "software_release": {"name": "Gaussian", "version": "16"},
        "runtime": {
            "runtime_kind": "described",
            "description": "Zeus cluster site install",
            "modules": [{"name": "gaussian", "version": "16.C01"}],
        },
        "executable": {"locator": "file:///opt/g16/g16"},
    }


def test_builder_accepts_a_described_environment_without_digests():
    """No lockfile, no binary hash, no container — still recordable."""
    calc = Calculation.sp(
        SoftwareRelease("Gaussian", "16"),
        LevelOfTheory("wb97xd", "def2-svp"),
        electronic_energy_hartree=-40.0,
        execution_environment=_described_environment(),
    )
    assert calc.execution_environment.closure_tier == "described"
    assert calc.execution_environment.content_digest().startswith("sha256:")


def test_described_environment_survives_both_upload_wire_paths():
    calc = Calculation.sp(
        SoftwareRelease("Gaussian", "16"),
        LevelOfTheory("wb97xd", "def2-svp"),
        electronic_energy_hartree=-40.0,
        execution_environment=_described_environment(),
    )
    species_payload = ComputedSpeciesUpload._calc_payload(object(), calc, _KeyMinter())
    reaction_payload = ComputedReactionUpload._calc_payload_flat(
        object(), calc, key="calc-1", calc_keys=_KeyMinter(), geometry_key=None
    )
    assert species_payload["execution_environment"] == reaction_payload["execution_environment"]
    runtime = species_payload["execution_environment"]["runtime"]
    assert runtime["runtime_kind"] == "described"
    assert runtime["modules"] == [{"name": "gaussian", "version": "16.C01"}]
