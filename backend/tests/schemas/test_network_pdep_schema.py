"""Tests for app/schemas/entities/network_pdep.py."""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.db.models.common import (
    ArrheniusAUnits,
    NetworkChannelKind,
    NetworkKineticsModelKind,
    NetworkSolveCalculationRole,
    NetworkStateKind,
    PressureUnit,
    TemperatureUnit,
)
from app.schemas.entities.network_pdep import (
    NetworkChannelCreate,
    NetworkChannelRead,
    NetworkKineticsChebyshevCreate,
    NetworkKineticsChebyshevRead,
    NetworkKineticsCreate,
    NetworkKineticsPlogCreate,
    NetworkKineticsPlogRead,
    NetworkKineticsPointCreate,
    NetworkKineticsPointRead,
    NetworkKineticsRead,
    NetworkSolveBathGasCreate,
    NetworkSolveBathGasRead,
    NetworkSolveCreate,
    NetworkSolveEnergyTransferCreate,
    NetworkSolveRead,
    NetworkSolveSourceCalculationCreate,
    NetworkSolveSourceCalculationRead,
    NetworkStateCreate,
    NetworkStateParticipantCreate,
    NetworkStateParticipantRead,
    NetworkStateRead,
)

# ---------------------------------------------------------------------------
# NetworkState
# ---------------------------------------------------------------------------


class TestNetworkStateCreate:
    def test_valid(self) -> None:
        state = NetworkStateCreate(
            network_id=1,
            kind=NetworkStateKind.well,
            label="ethanol well",
            participants=[
                NetworkStateParticipantCreate(species_entry_id=1),
                NetworkStateParticipantCreate(species_entry_id=2, stoichiometry=2),
            ],
        )
        assert state.kind == NetworkStateKind.well
        assert len(state.participants) == 2
        assert state.participants[1].stoichiometry == 2

    def test_requires_at_least_one_participant(self) -> None:
        with pytest.raises(ValidationError):
            NetworkStateCreate(
                network_id=1,
                kind=NetworkStateKind.well,
                participants=[],
            )

    def test_rejects_duplicate_participants(self) -> None:
        with pytest.raises(ValidationError, match="unique by species_entry_id"):
            NetworkStateCreate(
                network_id=1,
                kind=NetworkStateKind.bimolecular,
                participants=[
                    NetworkStateParticipantCreate(species_entry_id=1),
                    NetworkStateParticipantCreate(species_entry_id=1),
                ],
            )

    def test_rejects_zero_stoichiometry(self) -> None:
        with pytest.raises(ValidationError):
            NetworkStateParticipantCreate(species_entry_id=1, stoichiometry=0)


class TestNetworkStateRead:
    def test_from_orm(self) -> None:
        participant = SimpleNamespace(
            state_id=10, species_entry_id=1, stoichiometry=1,
        )
        state = SimpleNamespace(
            id=10,
            network_id=1,
            kind=NetworkStateKind.well,
            composition_hash="a" * 64,
            label=None,
            participants=[participant],
        )
        read = NetworkStateRead.model_validate(state)
        assert read.id == 10
        assert read.composition_hash == "a" * 64
        assert len(read.participants) == 1

    def test_participant_from_orm(self) -> None:
        p = SimpleNamespace(state_id=5, species_entry_id=3, stoichiometry=2)
        read = NetworkStateParticipantRead.model_validate(p)
        assert read.state_id == 5
        assert read.stoichiometry == 2


# ---------------------------------------------------------------------------
# NetworkChannel
# ---------------------------------------------------------------------------


class TestNetworkChannelCreate:
    def test_valid(self) -> None:
        ch = NetworkChannelCreate(
            network_id=1, source_state_id=10, sink_state_id=20,
            kind=NetworkChannelKind.isomerization,
        )
        assert ch.source_state_id == 10

    def test_rejects_source_eq_sink(self) -> None:
        with pytest.raises(ValidationError, match="must differ"):
            NetworkChannelCreate(
                network_id=1, source_state_id=10, sink_state_id=10,
                kind=NetworkChannelKind.dissociation,
            )


class TestNetworkChannelRead:
    def test_from_orm(self) -> None:
        ch = SimpleNamespace(
            id=5, network_id=1, source_state_id=10, sink_state_id=20,
            kind=NetworkChannelKind.association,
        )
        read = NetworkChannelRead.model_validate(ch)
        assert read.id == 5


# ---------------------------------------------------------------------------
# NetworkSolve
# ---------------------------------------------------------------------------


class TestNetworkSolveCreate:
    def test_valid_with_children(self) -> None:
        solve = NetworkSolveCreate(
            network_id=1,
            tmin_k=300, tmax_k=2000,
            pmin_bar=0.01, pmax_bar=100,
            bath_gases=[
                NetworkSolveBathGasCreate(species_entry_id=1, mole_fraction=0.7),
                NetworkSolveBathGasCreate(species_entry_id=2, mole_fraction=0.3),
            ],
            energy_transfers=[
                NetworkSolveEnergyTransferCreate(
                    model="single_exponential_down",
                    alpha0_cm_inv=200, t_exponent=0.85, t_ref_k=300,
                ),
            ],
            source_calculations=[
                NetworkSolveSourceCalculationCreate(
                    calculation_id=1, role=NetworkSolveCalculationRole.well_energy,
                ),
            ],
        )
        assert len(solve.bath_gases) == 2
        assert len(solve.energy_transfers) == 1
        assert len(solve.source_calculations) == 1

    def test_rejects_tmin_gt_tmax(self) -> None:
        with pytest.raises(ValidationError, match="tmin_k"):
            NetworkSolveCreate(
                network_id=1, tmin_k=2000, tmax_k=300,
            )

    def test_rejects_pmin_gt_pmax(self) -> None:
        with pytest.raises(ValidationError, match="pmin_bar"):
            NetworkSolveCreate(
                network_id=1, pmin_bar=100, pmax_bar=0.01,
            )

    def test_rejects_duplicate_bath_gases(self) -> None:
        with pytest.raises(ValidationError, match="unique by species_entry_id"):
            NetworkSolveCreate(
                network_id=1,
                bath_gases=[
                    NetworkSolveBathGasCreate(species_entry_id=1, mole_fraction=0.5),
                    NetworkSolveBathGasCreate(species_entry_id=1, mole_fraction=0.5),
                ],
            )

    def test_rejects_duplicate_source_calculations(self) -> None:
        with pytest.raises(ValidationError, match="unique by"):
            NetworkSolveCreate(
                network_id=1,
                source_calculations=[
                    NetworkSolveSourceCalculationCreate(
                        calculation_id=1, role=NetworkSolveCalculationRole.well_energy,
                    ),
                    NetworkSolveSourceCalculationCreate(
                        calculation_id=1, role=NetworkSolveCalculationRole.well_energy,
                    ),
                ],
            )

    def test_allows_same_calc_different_roles(self) -> None:
        solve = NetworkSolveCreate(
            network_id=1,
            source_calculations=[
                NetworkSolveSourceCalculationCreate(
                    calculation_id=1, role=NetworkSolveCalculationRole.well_energy,
                ),
                NetworkSolveSourceCalculationCreate(
                    calculation_id=1, role=NetworkSolveCalculationRole.well_freq,
                ),
            ],
        )
        assert len(solve.source_calculations) == 2

    def test_bath_gas_rejects_zero_mole_fraction(self) -> None:
        with pytest.raises(ValidationError):
            NetworkSolveBathGasCreate(species_entry_id=1, mole_fraction=0)

    def test_bath_gas_rejects_gt_one(self) -> None:
        with pytest.raises(ValidationError):
            NetworkSolveBathGasCreate(species_entry_id=1, mole_fraction=1.01)


class TestNetworkSolveRead:
    def test_from_orm(self) -> None:
        bath_gas = SimpleNamespace(
            solve_id=1, species_entry_id=5, mole_fraction=1.0,
        )
        source_calc = SimpleNamespace(
            solve_id=1, calculation_id=10,
            role=NetworkSolveCalculationRole.barrier_energy,
        )
        energy_transfer = SimpleNamespace(
            id=1, solve_id=1, model="sed",
            alpha0_cm_inv=200, t_exponent=0.85, t_ref_k=300, note=None,
        )
        solve = SimpleNamespace(
            id=1, network_id=1, created_at="2024-01-01T00:00:00", created_by=None,
            literature_id=None, software_release_id=None,
            workflow_tool_release_id=None,
            me_method="chemrate", interpolation_model=None,
            grain_size_cm_inv=10, grain_count=200, emax_kj_mol=500,
            tmin_k=300, tmax_k=2000, pmin_bar=0.01, pmax_bar=100,
            note=None,
            bath_gases=[bath_gas],
            energy_transfers=[energy_transfer],
            source_calculations=[source_calc],
        )
        read = NetworkSolveRead.model_validate(solve)
        assert read.id == 1
        assert len(read.bath_gases) == 1
        assert read.bath_gases[0].mole_fraction == 1.0
        assert len(read.source_calculations) == 1

    def test_bath_gas_read_from_orm(self) -> None:
        bg = SimpleNamespace(solve_id=1, species_entry_id=5, mole_fraction=0.8)
        read = NetworkSolveBathGasRead.model_validate(bg)
        assert read.solve_id == 1

    def test_source_calculation_read_from_orm(self) -> None:
        sc = SimpleNamespace(
            solve_id=1, calculation_id=10,
            role=NetworkSolveCalculationRole.master_equation_run,
        )
        read = NetworkSolveSourceCalculationRead.model_validate(sc)
        assert read.role == NetworkSolveCalculationRole.master_equation_run


# ---------------------------------------------------------------------------
# NetworkKinetics
# ---------------------------------------------------------------------------


class TestNetworkKineticsCreate:
    def test_valid_chebyshev(self) -> None:
        k = NetworkKineticsCreate(
            channel_id=1, solve_id=1,
            model_kind=NetworkKineticsModelKind.chebyshev,
            chebyshev=NetworkKineticsChebyshevCreate(
                n_temperature=4, n_pressure=3,
                coefficients={"data": [[1, 2, 3]] * 4},
            ),
        )
        assert k.chebyshev is not None

    def test_valid_plog(self) -> None:
        k = NetworkKineticsCreate(
            channel_id=1, solve_id=1,
            model_kind=NetworkKineticsModelKind.plog,
            plog_entries=[
                NetworkKineticsPlogCreate(
                    pressure_bar=1.0, a=1e12, n=0.5, ea_kj_mol=50.0,
                ),
                NetworkKineticsPlogCreate(
                    pressure_bar=10.0, a=1e13, n=0.3, ea_kj_mol=55.0,
                ),
            ],
        )
        assert len(k.plog_entries) == 2

    def test_valid_tabulated(self) -> None:
        k = NetworkKineticsCreate(
            channel_id=1, solve_id=1,
            model_kind=NetworkKineticsModelKind.tabulated,
            points=[
                NetworkKineticsPointCreate(
                    temperature_k=300, pressure_bar=1.0, rate_value=1e5,
                ),
            ],
        )
        assert len(k.points) == 1

    def test_rejects_chebyshev_without_coefficients(self) -> None:
        with pytest.raises(ValidationError, match="requires chebyshev"):
            NetworkKineticsCreate(
                channel_id=1, solve_id=1,
                model_kind=NetworkKineticsModelKind.chebyshev,
            )

    def test_rejects_plog_without_entries(self) -> None:
        with pytest.raises(ValidationError, match="requires at least one plog"):
            NetworkKineticsCreate(
                channel_id=1, solve_id=1,
                model_kind=NetworkKineticsModelKind.plog,
            )

    def test_rejects_tabulated_without_points(self) -> None:
        with pytest.raises(ValidationError, match="requires at least one data point"):
            NetworkKineticsCreate(
                channel_id=1, solve_id=1,
                model_kind=NetworkKineticsModelKind.tabulated,
            )

    def test_rejects_duplicate_plog_entries(self) -> None:
        with pytest.raises(ValidationError, match="unique by"):
            NetworkKineticsCreate(
                channel_id=1, solve_id=1,
                model_kind=NetworkKineticsModelKind.plog,
                plog_entries=[
                    NetworkKineticsPlogCreate(
                        pressure_bar=1.0, entry_index=1, a=1e12, n=0.5, ea_kj_mol=50,
                    ),
                    NetworkKineticsPlogCreate(
                        pressure_bar=1.0, entry_index=1, a=1e13, n=0.3, ea_kj_mol=55,
                    ),
                ],
            )

    def test_allows_same_pressure_different_entry_index(self) -> None:
        k = NetworkKineticsCreate(
            channel_id=1, solve_id=1,
            model_kind=NetworkKineticsModelKind.plog,
            plog_entries=[
                NetworkKineticsPlogCreate(
                    pressure_bar=1.0, entry_index=1, a=1e12, n=0.5, ea_kj_mol=50,
                ),
                NetworkKineticsPlogCreate(
                    pressure_bar=1.0, entry_index=2, a=1e13, n=0.3, ea_kj_mol=55,
                ),
            ],
        )
        assert len(k.plog_entries) == 2

    def test_rejects_duplicate_points(self) -> None:
        with pytest.raises(ValidationError, match="unique by"):
            NetworkKineticsCreate(
                channel_id=1, solve_id=1,
                model_kind=NetworkKineticsModelKind.tabulated,
                points=[
                    NetworkKineticsPointCreate(
                        temperature_k=300, pressure_bar=1.0, rate_value=1e5,
                    ),
                    NetworkKineticsPointCreate(
                        temperature_k=300, pressure_bar=1.0, rate_value=2e5,
                    ),
                ],
            )

    def test_rejects_tmin_gt_tmax(self) -> None:
        with pytest.raises(ValidationError, match="tmin_k"):
            NetworkKineticsCreate(
                channel_id=1, solve_id=1,
                model_kind=NetworkKineticsModelKind.tabulated,
                tmin_k=2000, tmax_k=300,
                points=[
                    NetworkKineticsPointCreate(
                        temperature_k=500, pressure_bar=1, rate_value=1e5,
                    ),
                ],
            )

    def test_rejects_pmin_gt_pmax(self) -> None:
        with pytest.raises(ValidationError, match="pmin_bar"):
            NetworkKineticsCreate(
                channel_id=1, solve_id=1,
                model_kind=NetworkKineticsModelKind.tabulated,
                pmin_bar=100, pmax_bar=1,
                points=[
                    NetworkKineticsPointCreate(
                        temperature_k=500, pressure_bar=1, rate_value=1e5,
                    ),
                ],
            )


class TestNetworkKineticsRead:
    def test_chebyshev_from_orm(self) -> None:
        cheb = SimpleNamespace(
            network_kinetics_id=1,
            n_temperature=4, n_pressure=3,
            coefficients={"data": [[1, 2, 3]] * 4},
        )
        read = NetworkKineticsChebyshevRead.model_validate(cheb)
        assert read.network_kinetics_id == 1
        assert read.n_temperature == 4

    def test_plog_from_orm(self) -> None:
        plog = SimpleNamespace(
            network_kinetics_id=1,
            pressure_bar=1.0, entry_index=1,
            a=1e12, n=0.5, ea_kj_mol=50.0,
        )
        read = NetworkKineticsPlogRead.model_validate(plog)
        assert read.pressure_bar == 1.0

    def test_point_from_orm(self) -> None:
        pt = SimpleNamespace(
            network_kinetics_id=1,
            temperature_k=500, pressure_bar=1.0, rate_value=1e8,
        )
        read = NetworkKineticsPointRead.model_validate(pt)
        assert read.rate_value == 1e8

    def test_full_kinetics_from_orm(self) -> None:
        cheb = SimpleNamespace(
            network_kinetics_id=1,
            n_temperature=4, n_pressure=3,
            coefficients={"data": [[1, 2, 3]] * 4},
        )
        kinetics = SimpleNamespace(
            id=1, channel_id=10, solve_id=20,
            model_kind=NetworkKineticsModelKind.chebyshev,
            tmin_k=300, tmax_k=2000, pmin_bar=0.01, pmax_bar=100,
            rate_units=ArrheniusAUnits.cm3_mol_s, pressure_units=PressureUnit.bar,
            temperature_units=TemperatureUnit.kelvin, stores_log10_k=False, note=None,
            created_at="2024-01-01T00:00:00",
            chebyshev=cheb, plog_entries=[], points=[],
        )
        read = NetworkKineticsRead.model_validate(kinetics)
        assert read.id == 1
        assert read.chebyshev is not None
        assert read.chebyshev.n_temperature == 4
