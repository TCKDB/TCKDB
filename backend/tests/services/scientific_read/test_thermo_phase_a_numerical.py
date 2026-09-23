"""Numerical custody, not chemical accuracy: pinned Cantera 3.2.0 / RMG 3.3.0."""

import math
from collections import Counter

import cantera as ct
import numpy as np
import pytest
from cantera import ck2yaml

from app.db.models.common import RecordReviewStatus
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.contribution_bundle_export import _thermo_to_upload
from app.services.scientific_read.chemkin_serialize import _nasa_card
from app.services.scientific_read.export import SelectedThermo
from app.workflows.thermo import persist_thermo_upload
from tests.schemas.test_thermo_phase_a_contract import IDENTITY, interval


def reduced(model, temperature):
    return np.array([model.cp(temperature) / ct.gas_constant,
                     model.h(temperature) / (ct.gas_constant * temperature),
                     model.s(temperature) / ct.gas_constant])


def nasa7_model(data):
    return ct.NasaPoly2(data["t_low"], data["t_high"], ct.one_atm,
                        [data["t_mid"], *[data[f"b{i}"] for i in range(1, 8)],
                         *[data[f"a{i}"] for i in range(1, 8)]])


def print_error_bound(coefficients, temperature):
    t = temperature
    half_units = [0 if c == 0 else 0.5 * 10 ** (math.floor(math.log10(abs(c))) - 8)
                  for c in coefficients]
    weights = np.array([[1, t, t**2, t**3, t**4, 0, 0],
                        [1, t/2, t**2/3, t**3/4, t**4/5, 1/t, 0],
                        [math.log(t), t, t**2/2, t**3/3, t**4/4, 0, 1]])
    return weights @ half_units + 1e-10


@pytest.mark.parametrize("analytical", [False, True])
def test_nasa7_cantera_and_printed_precision(db_session, tmp_path, analytical):
    assert ct.__version__ == "3.2.0", "Revalidate numerical fixtures before changing the engine pin"
    # Source: H2O in Cantera 3.2.0's bundled GRI-Mech 3.0 data, or constant Cp/R=3.5.
    water = next(species for species in ct.Species.list_from_file("gri30.yaml") if species.name == "H2O").thermo
    low, high = list(water.coeffs[8:]), list(water.coeffs[1:8])
    if analytical:
        low = high = [3.5, 0, 0, 0, 0, -1000, 4]
    data = {"t_low": water.min_temp, "t_mid": water.coeffs[0], "t_high": water.max_temp,
            **{f"a{i}": c for i, c in enumerate(low, 1)},
            **{f"b{i}": c for i, c in enumerate(high, 1)}}
    row = persist_thermo_upload(db_session, ThermoUploadRequest(
        enthalpy_reference_kind="formation_from_elements_298k", species_entry=IDENTITY, nasa=data, phase="gas", reference_pressure_bar=1.01325))
    replay = _thermo_to_upload(row)["nasa"]
    assert replay == data
    source, restored = nasa7_model(data), nasa7_model(replay)
    selected = SelectedThermo(row, row.nasa, [], "nasa", RecordReviewStatus.not_reviewed)
    native = selected.to_dict()["nasa"]
    assert native["low_coefficients"] == low and native["high_coefficients"] == high
    cards = _nasa_card("H2O", Counter(H=2, O=1), selected)
    thermo_file, yaml_file = tmp_path / "therm.dat", tmp_path / "thermo.yaml"
    thermo_file.write_text("THERMO ALL\n200 1000 3500\n" + "\n".join(cards) + "\nEND\n")
    ck2yaml.convert(input_file=None, thermo_file=str(thermo_file), out_name=str(yaml_file), quiet=True)
    printed = ct.Species.list_from_file(str(yaml_file))[0].thermo
    assert abs(printed.min_temp - source.min_temp) <= 0.0005
    assert abs(printed.max_temp - source.max_temp) <= 0.0005
    # Cantera collapses identical low/high blocks to one interval. Check the
    # printed join itself; evaluating that collapsed model is still equivalent.
    assert abs(float(cards[0][65:73]) - source.coeffs[0]) <= 0.005
    for t in [200, 298.15, 700, 999.999, 1000, 1000.001, 2000, 3500]:
        expected = reduced(source, t)
        assert np.max(np.abs(expected - reduced(restored, t))) <= 1e-10
        block = low if t <= 1000 else high
        assert np.all(np.abs(expected - reduced(printed, t)) <= print_error_bound(block, t))
        if analytical:
            assert abs(expected[0] - 3.5) <= 1e-10
    if not analytical:
        swapped = {**data, **{f"a{i}": c for i, c in enumerate(high, 1)},
                   **{f"b{i}": c for i, c in enumerate(low, 1)}}
        assert np.max(abs(reduced(source, 298.15) - reduced(nasa7_model(swapped), 298.15))) > 1e-3
        wrong_units = {**data, "a6": data["a6"] * 1000}
        assert abs(reduced(source, 298.15)[1] - reduced(nasa7_model(wrong_units), 298.15)[1]) > 1


def test_nasa9_cantera_multiple_intervals(db_session):
    intervals = [interval(1, 200, 1000), interval(2, 1000, 3000)]
    for i, item in enumerate(intervals):
        item.update(a1=100 + i, a2=-3 - i, a3=3.5 + i, a4=1e-5, a8=-1234, a9=4)
    row = persist_thermo_upload(db_session, ThermoUploadRequest(enthalpy_reference_kind="formation_from_elements_298k", species_entry=IDENTITY, nasa9_intervals=intervals))
    replay = _thermo_to_upload(row)["nasa9_intervals"]
    assert replay == intervals
    selected = SelectedThermo(row, None, [], "nasa9", RecordReviewStatus.not_reviewed,
                              nasa9_intervals=row.nasa9_intervals).to_dict()
    assert selected["nasa9"] == intervals

    def model(items):
        coefficients = [len(items)]
        for item in items:
            coefficients.extend([item["t_min_k"], item["t_max_k"], *[item[f"a{i}"] for i in range(1, 10)]])
        return ct.Nasa9PolyMultiTempRegion(200, 3000, ct.one_atm, coefficients)

    source, restored = model(intervals), model(replay)
    for t in [200, 298.15, 700, 999.999, 1000, 1000.001, 2000, 3000]:
        assert np.max(abs(reduced(source, t) - reduced(restored, t))) <= 1e-10
    constant = [interval(1, 200, 1000), interval(2, 1000, 3000)]
    for item in constant:
        item.update(a3=3.5)
    for t in [200, 1000, 3000]:
        assert abs(reduced(model(constant), t)[0] - 3.5) <= 1e-10


@pytest.mark.parametrize("constants", [False, True])
def test_wilhoit_rmg_reference_and_optional_constants(db_session, constants):
    # Generated with rmgpy 3.3.0 Wilhoit.get_heat_capacity; units J/(mol*K).
    # Cp0=30, CpInf=100, B=500 K, a0..a3=(1.2,-0.3,0.7,-0.2).
    expected = [(200, 30.951984292259176), (298.15, 32.57680665667479),
                (500, 37.0), (1000, 48.128943758573385),
                (2000, 63.10182400000001), (3000, 71.64836080204677)]
    data = {"cp0_j_mol_k": 30, "cp_inf_j_mol_k": 100, "b_k": 500,
            "a0": 1.2, "a1": -0.3, "a2": 0.7, "a3": -0.2, "h0_kj_mol": None, "s0_j_mol_k": None}
    if constants:
        data.update(h0_kj_mol=-123.456, s0_j_mol_k=12.345)
    row = persist_thermo_upload(db_session, ThermoUploadRequest(
        species_entry=IDENTITY, wilhoit=data,
        enthalpy_reference_kind="formation_from_elements_298k" if constants else None,
    ))
    replay = _thermo_to_upload(row)["wilhoit"]
    assert replay == data
    selected = SelectedThermo(row, None, [], "wilhoit", RecordReviewStatus.not_reviewed,
                              wilhoit=row.wilhoit).to_dict()["wilhoit"]
    assert selected == data
    for t, cp in expected:
        y = t / (t + selected["b_k"])
        actual = selected["cp0_j_mol_k"] + (selected["cp_inf_j_mol_k"] - selected["cp0_j_mol_k"]) * y*y * (
            1 + (y-1) * sum(selected[f"a{i}"] * y**i for i in range(4)))
        assert abs(actual - cp) / (ct.gas_constant / 1000) <= 1e-10
    # No H/S reconstruction is claimed when integration constants are absent.
    assert selected["h0_kj_mol"] == data["h0_kj_mol"]
    assert selected["s0_j_mol_k"] == data["s0_j_mol_k"]
