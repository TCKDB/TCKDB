"""Local-validation tests for the Phase-3B :class:`Thermo` builder.

These tests stay inside the builder layer — they never hit the network
and never assemble a ``ComputedReactionUpload``. Coverage for the
:meth:`Thermo.scalar` / :meth:`Thermo.nasa` / :meth:`Thermo.points`
factories independently from the upload-level plumbing.
"""

from __future__ import annotations

import pytest

from tckdb_client.builders import (
    Calculation,
    Geometry,
    LevelOfTheory,
    SoftwareRelease,
    TCKDBBuilderValidationError,
    Thermo,
)


def _gaussian() -> SoftwareRelease:
    return SoftwareRelease(software="Gaussian", version="16")


def _b3lyp() -> LevelOfTheory:
    return LevelOfTheory(method="wb97xd", basis="def2tzvp")


def _opt() -> Calculation:
    return Calculation.opt(
        _gaussian(), _b3lyp(),
        output_geometry=Geometry.from_xyz("1\nx\nH 0 0 0"),
        converged=True,
    )


# --- Thermo.scalar ----------------------------------------------------


class TestThermoScalar:
    def test_h298_only_ok(self):
        t = Thermo.scalar(h298_kj_mol=-74.6)
        assert t.h298_kj_mol == -74.6
        assert t.s298_j_mol_k is None
        assert t._kind == "scalar"

    def test_s298_only_ok(self):
        t = Thermo.scalar(s298_j_mol_k=186.3)
        assert t.s298_j_mol_k == 186.3

    def test_at_least_one_scalar_required(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Thermo.scalar()

    def test_temperature_bounds_must_be_positive(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Thermo.scalar(h298_kj_mol=0.0, tmin_k=0)
        with pytest.raises(TCKDBBuilderValidationError):
            Thermo.scalar(h298_kj_mol=0.0, tmax_k=-1)

    def test_tmin_must_be_le_tmax(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Thermo.scalar(h298_kj_mol=0.0, tmin_k=2000, tmax_k=300)

    def test_to_payload_emits_only_supplied_keys(self):
        t = Thermo.scalar(h298_kj_mol=-74.6, tmax_k=2000)
        payload = t.to_payload()
        assert payload == {"h298_kj_mol": -74.6, "tmax_k": 2000.0}


# --- Thermo.nasa ------------------------------------------------------


class TestThermoNasa:
    @pytest.fixture
    def coeffs(self):
        return [1.0] * 7

    def test_nasa_happy_path(self, coeffs):
        t = Thermo.nasa(
            coeffs_low=coeffs, coeffs_high=coeffs,
            t_low=200, t_mid=1000, t_high=5000,
            h298_kj_mol=-74.6,
        )
        assert t._kind == "nasa"
        assert t.nasa_block["a1"] == 1.0
        assert t.nasa_block["b1"] == 1.0
        assert t.nasa_block["t_low"] == 200.0
        # tmin/tmax mirror the NASA range so the bundle thermo block
        # carries the expected upper-level bounds for free.
        assert t.tmin_k == 200.0
        assert t.tmax_k == 5000.0

    def test_coeffs_must_be_length_7(self, coeffs):
        with pytest.raises(TCKDBBuilderValidationError):
            Thermo.nasa(
                coeffs_low=[1.0, 2.0],
                coeffs_high=coeffs,
                t_low=200, t_mid=1000, t_high=5000,
            )
        with pytest.raises(TCKDBBuilderValidationError):
            Thermo.nasa(
                coeffs_low=coeffs,
                coeffs_high=[1.0],
                t_low=200, t_mid=1000, t_high=5000,
            )

    def test_coeffs_must_be_numeric(self, coeffs):
        with pytest.raises(TCKDBBuilderValidationError):
            Thermo.nasa(
                coeffs_low=["zero"] + [1.0] * 6,
                coeffs_high=coeffs,
                t_low=200, t_mid=1000, t_high=5000,
            )

    def test_temperatures_must_be_ordered(self, coeffs):
        with pytest.raises(TCKDBBuilderValidationError):
            Thermo.nasa(
                coeffs_low=coeffs, coeffs_high=coeffs,
                t_low=1000, t_mid=500, t_high=5000,
            )
        with pytest.raises(TCKDBBuilderValidationError):
            Thermo.nasa(
                coeffs_low=coeffs, coeffs_high=coeffs,
                t_low=200, t_mid=1000, t_high=900,
            )

    def test_temperatures_must_be_positive(self, coeffs):
        with pytest.raises(TCKDBBuilderValidationError):
            Thermo.nasa(
                coeffs_low=coeffs, coeffs_high=coeffs,
                t_low=0, t_mid=1000, t_high=5000,
            )

    def test_to_payload_emits_nasa_block(self, coeffs):
        t = Thermo.nasa(
            coeffs_low=coeffs, coeffs_high=coeffs,
            t_low=200, t_mid=1000, t_high=5000,
        )
        payload = t.to_payload()
        assert "nasa" in payload
        assert payload["nasa"]["a1"] == 1.0
        assert payload["nasa"]["b7"] == 1.0
        assert payload["tmin_k"] == 200.0


# --- Thermo.points ----------------------------------------------------


class TestThermoPoints:
    def test_points_happy_path(self):
        t = Thermo.points(
            [
                {"temperature_k": 298.15, "cp_j_mol_k": 35.3, "h_kj_mol": 0.0},
                {"temperature_k": 500.0, "cp_j_mol_k": 46.0},
            ],
            tmin_k=200, tmax_k=2000,
        )
        assert t._kind == "points"
        assert len(t.point_table) == 2
        assert t.point_table[0]["temperature_k"] == 298.15

    def test_empty_points_rejected(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Thermo.points([])

    def test_missing_temperature_rejected(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Thermo.points([{"cp_j_mol_k": 35.0}])

    def test_non_positive_temperature_rejected(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Thermo.points([{"temperature_k": 0}])
        with pytest.raises(TCKDBBuilderValidationError):
            Thermo.points([{"temperature_k": -5}])

    def test_point_value_must_be_numeric(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Thermo.points([{"temperature_k": 298.15, "cp_j_mol_k": "high"}])

    def test_to_payload_emits_points(self):
        t = Thermo.points(
            [{"temperature_k": 298.15, "h_kj_mol": 0.0}],
        )
        payload = t.to_payload()
        assert payload["points"] == [
            {"temperature_k": 298.15, "h_kj_mol": 0.0}
        ]


# --- source_calculations ---------------------------------------------


class TestThermoSourceCalculations:
    def test_dict_form(self):
        opt = _opt()
        t = Thermo.scalar(
            h298_kj_mol=0.0,
            source_calculations={"opt": opt, "sp": opt},
        )
        assert [r for r, _ in t.source_calculations_iter()] == ["opt", "sp"]

    def test_dict_of_list_form(self):
        opt = _opt()
        sp = Calculation.sp(
            _gaussian(), _b3lyp(),
            input_geometry=Geometry.from_xyz("1\nx\nH 0 0 0"),
            electronic_energy_hartree=-1.0,
        )
        t = Thermo.scalar(
            h298_kj_mol=0.0,
            source_calculations={"sp": [opt, sp]},
        )
        assert [r for r, _ in t.source_calculations_iter()] == ["sp", "sp"]

    def test_list_of_tuples_form(self):
        opt = _opt()
        t = Thermo.scalar(
            h298_kj_mol=0.0,
            source_calculations=[("opt", opt), ("freq", opt), ("sp", opt)],
        )
        roles = [r for r, _ in t.source_calculations_iter()]
        assert roles == ["opt", "freq", "sp"]

    def test_unknown_role_rejected(self):
        opt = _opt()
        with pytest.raises(TCKDBBuilderValidationError):
            Thermo.scalar(
                h298_kj_mol=0.0,
                source_calculations={"made_up_role": opt},
            )

    def test_non_calculation_value_rejected(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Thermo.scalar(
                h298_kj_mol=0.0,
                source_calculations={"opt": "not a calc"},  # type: ignore[dict-item]
            )

    def test_bad_list_shape_rejected(self):
        opt = _opt()
        with pytest.raises(TCKDBBuilderValidationError):
            Thermo.scalar(
                h298_kj_mol=0.0,
                source_calculations=[("opt", opt, "extra")],  # type: ignore[list-item]
            )

    def test_to_payload_omits_source_calculations_by_default(self):
        """``BundleThermoIn`` does not carry the field; default emit drops it."""
        opt = _opt()
        t = Thermo.scalar(h298_kj_mol=0.0, source_calculations={"opt": opt})
        assert "source_calculations" not in t.to_payload()

    def test_to_payload_emits_source_calculations_when_opted_in(self):
        """The computed-species thermo path flips this flag and supplies
        a key lookup so ``calculation_key`` values resolve into the
        bundle's global namespace."""
        opt = _opt()
        t = Thermo.scalar(h298_kj_mol=0.0, source_calculations={"opt": opt})
        payload = t.to_payload(
            allow_source_calculations=True,
            calc_key_lookup=lambda calc: "calc_1" if calc is opt else "???",
        )
        assert payload["source_calculations"] == [
            {"calculation_key": "calc_1", "role": "opt"},
        ]

    def test_to_payload_requires_lookup_when_emitting_sources(self):
        """Emitting sources without a lookup is a programming error —
        the assembler must always pass its KeyMinter.lookup."""
        opt = _opt()
        t = Thermo.scalar(h298_kj_mol=0.0, source_calculations={"opt": opt})
        with pytest.raises(TCKDBBuilderValidationError):
            t.to_payload(allow_source_calculations=True)
