"""Tests for app/schemas/entities/thermo.py."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.db.models.common import ScientificOriginKind, ThermoCalculationRole
from app.schemas.entities.thermo import (
    ThermoCreate,
    ThermoNASACreate,
    ThermoNASARead,
    ThermoPointCreate,
    ThermoPointRead,
    ThermoRead,
    ThermoSourceCalculationCreate,
    ThermoSourceCalculationRead,
)


# ---------------------------------------------------------------------------
# ThermoPoint
# ---------------------------------------------------------------------------


class TestThermoPoint:
    def test_valid(self) -> None:
        p = ThermoPointCreate(
            temperature_k=298, cp_j_mol_k=75.3, h_kj_mol=-50.0, s_j_mol_k=200.0,
        )
        assert p.temperature_k == 298
        assert p.cp_j_mol_k == 75.3

    def test_rejects_zero_temperature(self) -> None:
        with pytest.raises(ValidationError):
            ThermoPointCreate(temperature_k=0)

    def test_rejects_negative_temperature(self) -> None:
        with pytest.raises(ValidationError):
            ThermoPointCreate(temperature_k=-100)

    def test_cp_only_point(self) -> None:
        p = ThermoPointCreate(temperature_k=298, cp_j_mol_k=75.3)
        assert p.h_kj_mol is None
        assert p.cp_j_mol_k == 75.3

    def test_read_from_orm(self) -> None:
        p = SimpleNamespace(
            thermo_id=1, temperature_k=500,
            cp_j_mol_k=80.0, h_kj_mol=10.0, s_j_mol_k=250.0, g_kj_mol=-115.0,
        )
        read = ThermoPointRead.model_validate(p)
        assert read.thermo_id == 1
        assert read.cp_j_mol_k == 80.0
        assert read.g_kj_mol == -115.0


# ---------------------------------------------------------------------------
# ThermoNASA
# ---------------------------------------------------------------------------


class TestThermoNASA:
    def test_valid_with_all_bounds(self) -> None:
        nasa = ThermoNASACreate(
            t_low=200, t_mid=1000, t_high=5000,
            a1=1, a2=2, a3=3, a4=4, a5=5, a6=6, a7=7,
        )
        assert nasa.t_mid == 1000

    def test_valid_with_no_bounds(self) -> None:
        nasa = ThermoNASACreate(a1=1, a2=2, a3=3, a4=4, a5=5, a6=6, a7=7)
        assert nasa.t_low is None

    def test_rejects_partial_bounds(self) -> None:
        with pytest.raises(ValidationError, match="all provided or all omitted"):
            ThermoNASACreate(t_low=200, t_mid=1000)

    def test_rejects_t_mid_le_t_low(self) -> None:
        with pytest.raises(ValidationError, match="t_mid must be greater"):
            ThermoNASACreate(t_low=1000, t_mid=1000, t_high=5000)

    def test_rejects_t_high_le_t_mid(self) -> None:
        with pytest.raises(ValidationError, match="t_high must be greater"):
            ThermoNASACreate(t_low=200, t_mid=1000, t_high=1000)

    def test_read_from_orm(self) -> None:
        nasa = SimpleNamespace(
            thermo_id=1, t_low=200, t_mid=1000, t_high=5000,
            a1=1, a2=2, a3=3, a4=4, a5=5, a6=6, a7=7,
            b1=1, b2=2, b3=3, b4=4, b5=5, b6=6, b7=7,
        )
        read = ThermoNASARead.model_validate(nasa)
        assert read.thermo_id == 1
        assert read.a1 == 1


# ---------------------------------------------------------------------------
# ThermoSourceCalculation
# ---------------------------------------------------------------------------


class TestThermoSourceCalculation:
    def test_read_from_orm(self) -> None:
        sc = SimpleNamespace(
            thermo_id=1, calculation_id=5, role=ThermoCalculationRole.freq,
        )
        read = ThermoSourceCalculationRead.model_validate(sc)
        assert read.thermo_id == 1
        assert read.role == ThermoCalculationRole.freq


# ---------------------------------------------------------------------------
# Thermo (parent)
# ---------------------------------------------------------------------------


class TestThermoCreate:
    def test_valid_with_all_children(self) -> None:
        t = ThermoCreate(
            species_entry_id=1,
            scientific_origin=ScientificOriginKind.computed,
            h298_kj_mol=-50.0,
            s298_j_mol_k=200.0,
            points=[
                ThermoPointCreate(temperature_k=298, h_kj_mol=-50.0),
                ThermoPointCreate(temperature_k=500, h_kj_mol=-48.0),
            ],
            nasa=ThermoNASACreate(
                t_low=200, t_mid=1000, t_high=5000,
                a1=1, a2=2, a3=3, a4=4, a5=5, a6=6, a7=7,
            ),
            source_calculations=[
                ThermoSourceCalculationCreate(
                    calculation_id=1, role=ThermoCalculationRole.sp,
                ),
                ThermoSourceCalculationCreate(
                    calculation_id=2, role=ThermoCalculationRole.freq,
                ),
            ],
        )
        assert len(t.points) == 2
        assert t.nasa is not None
        assert len(t.source_calculations) == 2

    def test_rejects_duplicate_temperature_points(self) -> None:
        with pytest.raises(ValidationError, match="unique by temperature_k"):
            ThermoCreate(
                species_entry_id=1,
                scientific_origin=ScientificOriginKind.computed,
                points=[
                    ThermoPointCreate(temperature_k=298),
                    ThermoPointCreate(temperature_k=298),
                ],
            )

    def test_rejects_duplicate_source_calculations(self) -> None:
        with pytest.raises(ValidationError, match="unique by"):
            ThermoCreate(
                species_entry_id=1,
                scientific_origin=ScientificOriginKind.computed,
                source_calculations=[
                    ThermoSourceCalculationCreate(
                        calculation_id=1, role=ThermoCalculationRole.sp,
                    ),
                    ThermoSourceCalculationCreate(
                        calculation_id=1, role=ThermoCalculationRole.sp,
                    ),
                ],
            )

    def test_valid_with_temperature_range(self) -> None:
        t = ThermoCreate(
            species_entry_id=1,
            scientific_origin=ScientificOriginKind.computed,
            tmin_k=200, tmax_k=5000,
        )
        assert t.tmin_k == 200
        assert t.tmax_k == 5000

    def test_rejects_tmin_gt_tmax(self) -> None:
        with pytest.raises(ValidationError, match="tmin_k"):
            ThermoCreate(
                species_entry_id=1,
                scientific_origin=ScientificOriginKind.computed,
                tmin_k=5000, tmax_k=200,
            )

    def test_valid_with_uncertainty(self) -> None:
        t = ThermoCreate(
            species_entry_id=1,
            scientific_origin=ScientificOriginKind.computed,
            h298_kj_mol=-50.0,
            h298_uncertainty_kj_mol=2.5,
            s298_j_mol_k=200.0,
            s298_uncertainty_j_mol_k=1.0,
        )
        assert t.h298_uncertainty_kj_mol == 2.5
        assert t.s298_uncertainty_j_mol_k == 1.0

    def test_rejects_negative_uncertainty(self) -> None:
        with pytest.raises(ValidationError):
            ThermoCreate(
                species_entry_id=1,
                scientific_origin=ScientificOriginKind.computed,
                h298_uncertainty_kj_mol=-1.0,
            )

    def test_allows_zero_uncertainty(self) -> None:
        t = ThermoCreate(
            species_entry_id=1,
            scientific_origin=ScientificOriginKind.computed,
            h298_uncertainty_kj_mol=0.0,
        )
        assert t.h298_uncertainty_kj_mol == 0.0

    def test_allows_same_calc_different_roles(self) -> None:
        t = ThermoCreate(
            species_entry_id=1,
            scientific_origin=ScientificOriginKind.computed,
            source_calculations=[
                ThermoSourceCalculationCreate(
                    calculation_id=1, role=ThermoCalculationRole.sp,
                ),
                ThermoSourceCalculationCreate(
                    calculation_id=1, role=ThermoCalculationRole.freq,
                ),
            ],
        )
        assert len(t.source_calculations) == 2


class TestThermoRead:
    def test_from_orm(self) -> None:
        point = SimpleNamespace(
            thermo_id=1, temperature_k=298,
            cp_j_mol_k=75.3, h_kj_mol=-50.0, s_j_mol_k=200.0, g_kj_mol=None,
        )
        nasa = SimpleNamespace(
            thermo_id=1, t_low=200, t_mid=1000, t_high=5000,
            a1=1, a2=2, a3=3, a4=4, a5=5, a6=6, a7=7,
            b1=None, b2=None, b3=None, b4=None, b5=None, b6=None, b7=None,
        )
        source_calc = SimpleNamespace(
            thermo_id=1, calculation_id=5, role=ThermoCalculationRole.sp,
        )
        thermo = SimpleNamespace(
            id=1, species_entry_id=1,
            scientific_origin=ScientificOriginKind.computed,
            literature_id=None, workflow_tool_release_id=None,
            software_release_id=None,
            h298_kj_mol=-50.0, s298_j_mol_k=200.0,
            h298_uncertainty_kj_mol=2.5, s298_uncertainty_j_mol_k=1.0,
            tmin_k=200, tmax_k=5000,
            note=None,
            created_at="2024-01-01T00:00:00", created_by=None,
            points=[point], nasa=nasa, source_calculations=[source_calc],
        )
        read = ThermoRead.model_validate(thermo)
        assert read.id == 1
        assert read.h298_uncertainty_kj_mol == 2.5
        assert read.tmin_k == 200
        assert len(read.points) == 1
        assert read.points[0].cp_j_mol_k == 75.3
        assert read.nasa is not None
        assert read.nasa.a1 == 1
        assert len(read.source_calculations) == 1
