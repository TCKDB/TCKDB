"""Creation strictness must not constrain historical reads or lose null state."""

import pytest
from pydantic import ValidationError
from tckdb_schemas.thermo import ThermoNASACreate, ThermoPointCreate
from tckdb_schemas.workflows.computed_reaction_upload import BundleThermoIn
from tckdb_schemas.workflows.computed_species_upload import ThermoInBundle

from app.schemas.entities.thermo import ThermoNASABase, ThermoPointBase
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.workflows.computed_species import _build_synthetic_thermo_upload_request

IDENTITY = {"smiles": "O", "charge": 0, "multiplicity": 1}


def nasa7():
    return {"t_low": 200, "t_mid": 1000, "t_high": 3000,
            **{f"{p}{i}": 0 for p in "ab" for i in range(1, 8)}}


def interval(index, low, high):
    return {"interval_index": index, "t_min_k": low, "t_max_k": high,
            **{f"a{i}": 0 for i in range(1, 10)}}


@pytest.mark.parametrize("schema", [ThermoUploadRequest, ThermoInBundle, BundleThermoIn])
@pytest.mark.parametrize("value", [0, -123.456])
def test_zero_kelvin_is_content(schema, value):
    extra = {"species_entry": IDENTITY} if schema is ThermoUploadRequest else {}
    record = schema(**extra, enthalpy_formation_0k_kj_mol=value)
    assert record.enthalpy_formation_0k_kj_mol == value
    assert record.phase == "gas"
    assert record.reference_pressure_bar == 1
    with pytest.raises(ValidationError):
        schema(**extra, enthalpy_formation_0k_uncertainty_kj_mol=0)


@pytest.mark.parametrize("schema", [ThermoUploadRequest, ThermoInBundle, BundleThermoIn])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("field", ["h298_kj_mol", "s298_j_mol_k", "reference_pressure_bar",
                                   "enthalpy_formation_0k_kj_mol", "h298_uncertainty_kj_mol", "tmax_k"])
def test_nonfinite_scalar_refused(schema, value, field):
    extra = {"species_entry": IDENTITY} if schema is ThermoUploadRequest else {}
    with pytest.raises(ValidationError):
        schema(**extra, **{"enthalpy_reference_kind": "formation_298k", "h298_kj_mol": 0, field: value})


@pytest.mark.parametrize("schema", [ThermoUploadRequest, ThermoInBundle, BundleThermoIn])
@pytest.mark.parametrize("origin", ["computed", "experimental", "estimated"])
def test_state_null_and_omission(schema, origin):
    extra = {"species_entry": IDENTITY} if schema is ThermoUploadRequest else {}
    record = schema(**extra, h298_kj_mol=0, scientific_origin=origin)
    assert record.phase == ("gas" if origin == "computed" else None)
    assert record.reference_pressure_bar == (1 if origin == "computed" else None)
    record = schema(**extra, h298_kj_mol=0, scientific_origin=origin,
                    phase=None, reference_pressure_bar=None)
    replay = schema.model_validate(record.model_dump(exclude_unset=True))
    assert replay.phase is None and replay.reference_pressure_bar is None


def test_species_conversion_preserves_explicit_null():
    thermo = ThermoInBundle(enthalpy_formation_0k_kj_mol=0, phase=None, reference_pressure_bar=None)
    converted = _build_synthetic_thermo_upload_request(
        thermo_in=thermo, species_entry_payload=IDENTITY,
    )
    assert converted.enthalpy_formation_0k_kj_mol == 0
    assert converted.phase is None and converted.reference_pressure_bar is None


def test_strict_create_permissive_historical_fragments():
    assert ThermoNASABase().a1 is None
    assert ThermoPointBase(temperature_k=298).cp_j_mol_k is None
    with pytest.raises(ValidationError):
        ThermoNASACreate()
    with pytest.raises(ValidationError):
        ThermoPointCreate(temperature_k=298)
    assert ThermoPointCreate(temperature_k=298, g_kj_mol=0).g_kj_mol == 0
    assert ThermoNASACreate(**nasa7()).a1 == 0
    for key in nasa7():
        partial = nasa7()
        partial.pop(key)
        with pytest.raises(ValidationError):
            ThermoNASACreate(**partial)
        partial[key] = float("nan")
        with pytest.raises(ValidationError):
            ThermoNASACreate(**partial)


@pytest.mark.parametrize("intervals", [
    [interval(1, 200, 1000), interval(2, 900, 3000)],
    [interval(1, 200, 1000), interval(2, 1001, 3000)],
    [interval(2, 200, 1000), interval(1, 1000, 3000)],
    [interval(1, 200, 1000), interval(1, 1000, 3000)],
    [interval(1, 200, 1000), interval(3, 1000, 3000)],
])
def test_nasa9_invalid_coverage(intervals):
    with pytest.raises(ValidationError):
        ThermoUploadRequest(enthalpy_reference_kind="formation_298k", species_entry=IDENTITY, nasa9_intervals=intervals)


def test_nasa9_index_order_and_auxiliary_points():
    record = ThermoUploadRequest(enthalpy_reference_kind="formation_298k", species_entry=IDENTITY,
                                nasa9_intervals=[interval(2, 1000, 3000), interval(1, 200, 1000)],
                                points=[{"temperature_k": 298, "h_kj_mol": -1}])
    assert len(record.nasa9_intervals) == 2
    assert record.points[0].h_kj_mol == -1
