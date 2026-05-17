"""Public ``Thermo.kind`` property + summary-collector hygiene.

The ``Thermo._kind`` attribute was previously the only nominally
private read in the summary layer. This module pins:

* ``Thermo.scalar(...).kind == "scalar"`` (likewise nasa / points),
* the bare-constructor sentinel ``"generic"``,
* ``ComputedSpeciesUpload.summary().to_dict()["thermo_kind"]`` reads
  the public property (consistency check),
* no module under ``tckdb_client.builders`` reads ``thermo._kind``
  any more (source-text guard).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tckdb_client.builders import (
    Calculation,
    ComputedSpeciesUpload,
    Geometry,
    LevelOfTheory,
    Species,
    SoftwareRelease,
    Thermo,
)


SR = SoftwareRelease(software="Gaussian", version="16")
LOT = LevelOfTheory(method="wb97xd", basis="def2tzvp")

WATER_XYZ = (
    "3\nh2o\n"
    "O 0.0 0.0 0.117\n"
    "H 0.0 0.757 -0.469\n"
    "H 0.0 -0.757 -0.469"
)


# ---------------------------------------------------------------------
# ``Thermo.kind`` reports the factory tag.
# ---------------------------------------------------------------------


def test_scalar_thermo_kind():
    assert Thermo.scalar(h298_kj_mol=-74.6).kind == "scalar"


def test_nasa_thermo_kind():
    t = Thermo.nasa(
        coeffs_low=[0.5] + [0.0] * 6,
        coeffs_high=[0.5] + [0.0] * 6,
        t_low=200, t_mid=1000, t_high=5000,
        h298_kj_mol=-74.6,
    )
    assert t.kind == "nasa"


def test_points_thermo_kind():
    t = Thermo.points(
        [
            {"temperature_k": 298.15, "cp_j_mol_k": 33.6, "h_kj_mol": 0.0},
            {"temperature_k": 500.0, "cp_j_mol_k": 35.2},
        ],
    )
    assert t.kind == "points"


def test_bare_thermo_kind_is_generic():
    """The bare constructor (reserved for internals/tests) reports
    ``"generic"`` — the sentinel the summary code degrades to when
    a thermo block isn't built through a factory."""
    assert Thermo(h298_kj_mol=-74.6).kind == "generic"


def test_thermo_kind_is_read_only():
    """``kind`` is a property — assignment should fail at the
    interpreter level, keeping the factory tag a single source of truth."""
    t = Thermo.scalar(h298_kj_mol=-74.6)
    with pytest.raises(AttributeError):
        t.kind = "nasa"  # type: ignore[misc]


# ---------------------------------------------------------------------
# Summary consistency — the public property feeds the collector.
# ---------------------------------------------------------------------


def _make_species_upload(thermo: Thermo | None) -> ComputedSpeciesUpload:
    geom = Geometry.from_xyz(WATER_XYZ)
    opt = Calculation.opt(
        SR, LOT, output_geometry=geom, converged=True,
        final_energy_hartree=-76.4, label="opt",
    )
    return ComputedSpeciesUpload(
        species=Species(smiles="O", charge=0, multiplicity=1, label="water"),
        calculations=[opt],
        primary_calculation=opt,
        thermo=thermo,
    )


@pytest.mark.parametrize(
    "thermo_factory, expected_kind",
    [
        (lambda: Thermo.scalar(h298_kj_mol=-241.8, s298_j_mol_k=188.8), "scalar"),
        (
            lambda: Thermo.nasa(
                coeffs_low=[0.5] + [0.0] * 6,
                coeffs_high=[0.5] + [0.0] * 6,
                t_low=200, t_mid=1000, t_high=5000,
                h298_kj_mol=-241.8,
            ),
            "nasa",
        ),
        (
            lambda: Thermo.points(
                [
                    {"temperature_k": 298.15, "cp_j_mol_k": 33.6, "h_kj_mol": 0.0},
                    {"temperature_k": 500.0, "cp_j_mol_k": 35.2},
                ],
            ),
            "points",
        ),
    ],
)
def test_summary_thermo_kind_matches_property(thermo_factory, expected_kind):
    thermo = thermo_factory()
    upload = _make_species_upload(thermo)
    data = upload.summary().to_dict()
    # The summary value must match what the public property reports;
    # if they ever diverge, one of them is buggy.
    assert data["thermo_kind"] == expected_kind
    assert data["thermo_kind"] == thermo.kind


def test_summary_thermo_kind_none_when_no_thermo():
    upload = _make_species_upload(thermo=None)
    data = upload.summary().to_dict()
    assert data["thermo_kind"] is None
    assert data["has_thermo"] is False


# ---------------------------------------------------------------------
# Source-text guard: builder code must not read ``thermo._kind``.
# ---------------------------------------------------------------------


_BUILDERS_DIR = (
    Path(__file__).resolve().parents[1]
    / "src" / "tckdb_client" / "builders"
)


def test_no_thermo_private_kind_reads_in_builder_layer():
    """No file under ``tckdb_client.builders`` should reach for
    ``thermo._kind`` any more — the public property is the only
    documented surface. The ``_kind`` field itself is allowed to
    persist on ``Thermo`` as an implementation detail; we only
    forbid reads outside its own module."""
    offenders: list[str] = []
    for path in _BUILDERS_DIR.glob("*.py"):
        if path.name == "thermo.py":
            # Implementation lives here; the property reads ``self._kind``.
            continue
        text = path.read_text(encoding="utf-8")
        if "._kind" in text:
            offenders.append(path.name)
    assert not offenders, (
        f"builder modules read ``thermo._kind`` directly: {offenders}. "
        "Use the public ``Thermo.kind`` property instead."
    )
