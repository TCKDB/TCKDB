"""Tests for upload reconciliation warnings (Layer 1 and Layer 2)."""

from __future__ import annotations

from app.db.models.common import (
    CalculationType,
    SpeciesEntryStateKind,
    StationaryPointKind,
)
from app.schemas.fragments.calculation import (
    CalculationWithResultsPayload,
    FreqResultPayload,
)
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.workflows.conformer_upload import ConformerUploadStatmechPayload
from app.services.upload_reconciliation import (
    W_ELECTRONIC_STATE_CONTRADICTS_METHOD,
    W_N_IMAG_CONTRADICTS_MINIMUM,
    W_N_IMAG_HIGHER_ORDER_SADDLE,
    W_N_IMAG_SUGGESTS_TS,
    W_TERM_SYMBOL_MISMATCH,
    build_ess_result_from_upload,
    extract_freq_n_imag,
    reconcile_species_entry,
    reconcile_species_entry_full,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _identity(
    *,
    kind: StationaryPointKind = StationaryPointKind.minimum,
    electronic_state_kind: SpeciesEntryStateKind = SpeciesEntryStateKind.ground,
    charge: int = 0,
    multiplicity: int = 1,
    term_symbol: str | None = None,
) -> SpeciesEntryIdentityPayload:
    return SpeciesEntryIdentityPayload(
        smiles="C",
        charge=charge,
        multiplicity=multiplicity,
        species_entry_kind=kind,
        electronic_state_kind=electronic_state_kind,
        term_symbol=term_symbol,
    )


def _freq_calc(n_imag: int) -> CalculationWithResultsPayload:
    return CalculationWithResultsPayload(
        type=CalculationType.freq,
        level_of_theory={"method": "b3lyp", "basis": "def2-svp"},
        software_release={"name": "gaussian", "version": "16"},
        freq_result=FreqResultPayload(n_imag=n_imag),
    )


def _opt_calc(method: str = "b3lyp") -> CalculationWithResultsPayload:
    return CalculationWithResultsPayload(
        type=CalculationType.opt,
        level_of_theory={"method": method, "basis": "def2-svp"},
        software_release={"name": "gaussian", "version": "16"},
    )


def _td_calc() -> CalculationWithResultsPayload:
    """A TD-DFT calculation (excited-state method)."""
    return CalculationWithResultsPayload(
        type=CalculationType.opt,
        level_of_theory={"method": "b3lyp", "basis": "def2-svp"},
        software_release={"name": "gaussian", "version": "16"},
    )


# ---------------------------------------------------------------------------
# extract_freq_n_imag
# ---------------------------------------------------------------------------


class TestExtractFreqNImag:
    def test_primary_freq_calc(self) -> None:
        assert extract_freq_n_imag(_freq_calc(0), []) == 0

    def test_primary_opt_additional_freq(self) -> None:
        assert extract_freq_n_imag(_opt_calc(), [_freq_calc(1)]) == 1

    def test_no_freq_results(self) -> None:
        assert extract_freq_n_imag(_opt_calc(), []) is None

    def test_primary_freq_takes_precedence(self) -> None:
        assert extract_freq_n_imag(_freq_calc(0), [_freq_calc(1)]) == 0


# ---------------------------------------------------------------------------
# reconcile_species_entry (Layer 1)
# ---------------------------------------------------------------------------


class TestReconcileSpeciesEntry:
    def test_no_freq_data_no_warnings(self) -> None:
        warnings = reconcile_species_entry(_identity())
        assert warnings == []

    def test_n_imag_zero_minimum_no_warnings(self) -> None:
        warnings = reconcile_species_entry(_identity(), freq_n_imag=0)
        assert warnings == []

    def test_n_imag_zero_vdw_complex_no_warnings(self) -> None:
        warnings = reconcile_species_entry(
            _identity(kind=StationaryPointKind.vdw_complex),
            freq_n_imag=0,
        )
        assert warnings == []

    def test_n_imag_one_minimum_produces_two_warnings(self) -> None:
        warnings = reconcile_species_entry(_identity(), freq_n_imag=1)
        codes = {w.code for w in warnings}
        assert W_N_IMAG_CONTRADICTS_MINIMUM in codes
        assert W_N_IMAG_SUGGESTS_TS in codes
        assert len(warnings) == 2

    def test_n_imag_one_vdw_complex_produces_two_warnings(self) -> None:
        warnings = reconcile_species_entry(
            _identity(kind=StationaryPointKind.vdw_complex),
            freq_n_imag=1,
        )
        codes = {w.code for w in warnings}
        assert W_N_IMAG_CONTRADICTS_MINIMUM in codes
        assert W_N_IMAG_SUGGESTS_TS in codes

    def test_n_imag_two_produces_higher_order_warning(self) -> None:
        warnings = reconcile_species_entry(_identity(), freq_n_imag=2)
        assert len(warnings) == 1
        assert warnings[0].code == W_N_IMAG_HIGHER_ORDER_SADDLE
        assert "2 imaginary frequencies" in warnings[0].message

    def test_n_imag_three_produces_higher_order_warning(self) -> None:
        warnings = reconcile_species_entry(_identity(), freq_n_imag=3)
        assert len(warnings) == 1
        assert warnings[0].code == W_N_IMAG_HIGHER_ORDER_SADDLE

    def test_warning_field_is_species_entry_kind(self) -> None:
        warnings = reconcile_species_entry(_identity(), freq_n_imag=1)
        assert all(w.field == "species_entry_kind" for w in warnings)

    def test_warning_messages_are_nonempty(self) -> None:
        warnings = reconcile_species_entry(_identity(), freq_n_imag=1)
        assert all(w.message for w in warnings)


# ---------------------------------------------------------------------------
# build_ess_result_from_upload
# ---------------------------------------------------------------------------


class TestBuildESSResult:
    def test_returns_none_without_calc(self) -> None:
        assert build_ess_result_from_upload(_identity()) is None

    def test_maps_software_name(self) -> None:
        result = build_ess_result_from_upload(
            _identity(), primary_calc=_opt_calc(),
        )
        assert result is not None
        assert result.meta.software_name == "gaussian"

    def test_maps_method_and_basis(self) -> None:
        result = build_ess_result_from_upload(
            _identity(), primary_calc=_opt_calc("wb97xd"),
        )
        assert result.meta.method == "wb97xd"
        assert result.meta.basis == "def2-svp"

    def test_maps_charge_multiplicity(self) -> None:
        result = build_ess_result_from_upload(
            _identity(charge=-1, multiplicity=2),
            primary_calc=_opt_calc(),
        )
        assert result.meta.charge == -1
        assert result.meta.multiplicity == 2

    def test_maps_job_types_from_calcs(self) -> None:
        result = build_ess_result_from_upload(
            _identity(),
            primary_calc=_opt_calc(),
            additional_calcs=[_freq_calc(0)],
        )
        assert "opt" in result.meta.job_types
        assert "freq" in result.meta.job_types

    def test_maps_freq_n_imag(self) -> None:
        result = build_ess_result_from_upload(
            _identity(),
            primary_calc=_opt_calc(),
            additional_calcs=[_freq_calc(1)],
        )
        assert result.freq is not None
        assert result.freq.n_imag == 1

    def test_maps_symmetry_from_statmech(self) -> None:
        result = build_ess_result_from_upload(
            _identity(),
            primary_calc=_opt_calc(),
            statmech=ConformerUploadStatmechPayload(point_group="C2v", is_linear=False),
        )
        assert result.symmetry is not None
        assert result.symmetry.point_group == "C2v"
        assert result.symmetry.is_linear is False

    def test_no_symmetry_without_statmech(self) -> None:
        result = build_ess_result_from_upload(
            _identity(), primary_calc=_opt_calc(),
        )
        assert result.symmetry is None


# ---------------------------------------------------------------------------
# reconcile_species_entry_full (Layer 2)
# ---------------------------------------------------------------------------


class TestReconcileSpeciesEntryFull:
    def test_no_calc_data_no_warnings(self) -> None:
        warnings = reconcile_species_entry_full(_identity())
        assert warnings == []

    def test_consistent_ground_state_dft_no_deduction_warnings(self) -> None:
        """Standard DFT with ground state claimed — heuristic agrees, no warning."""
        warnings = reconcile_species_entry_full(
            _identity(),
            primary_calc=_opt_calc("b3lyp"),
            additional_calcs=[_freq_calc(0)],
        )
        # No electronic_state warning (both say ground)
        # No n_imag warning (n_imag=0 with minimum)
        assert warnings == []

    def test_excited_state_claimed_but_standard_dft_warns(self) -> None:
        """User claims excited but method is standard DFT — contradiction."""
        warnings = reconcile_species_entry_full(
            _identity(electronic_state_kind=SpeciesEntryStateKind.excited),
            primary_calc=_opt_calc("b3lyp"),
            additional_calcs=[_freq_calc(0)],
        )
        codes = {w.code for w in warnings}
        assert W_ELECTRONIC_STATE_CONTRADICTS_METHOD in codes

    def test_n_imag_one_still_produces_layer1_warnings(self) -> None:
        """Layer 1 n_imag warnings fire even in full reconciliation."""
        warnings = reconcile_species_entry_full(
            _identity(),
            primary_calc=_freq_calc(1),
        )
        codes = {w.code for w in warnings}
        assert W_N_IMAG_CONTRADICTS_MINIMUM in codes
        assert W_N_IMAG_SUGGESTS_TS in codes

    def test_term_symbol_mismatch_warns(self) -> None:
        """User provides wrong term symbol vs what multiplicity+symmetry derives."""
        warnings = reconcile_species_entry_full(
            _identity(multiplicity=1, term_symbol="3A1"),
            primary_calc=_opt_calc(),
            statmech=ConformerUploadStatmechPayload(point_group="C2v"),
        )
        codes = {w.code for w in warnings}
        assert W_TERM_SYMBOL_MISMATCH in codes

    def test_correct_term_symbol_no_warning(self) -> None:
        """User provides correct term symbol — no warning."""
        warnings = reconcile_species_entry_full(
            _identity(multiplicity=1, term_symbol="1A1"),
            primary_calc=_opt_calc(),
            statmech=ConformerUploadStatmechPayload(point_group="C2v"),
        )
        # Filter to just term_symbol warnings
        term_warnings = [w for w in warnings if w.code == W_TERM_SYMBOL_MISMATCH]
        assert term_warnings == []

    def test_no_term_symbol_provided_no_warning(self) -> None:
        """User doesn't provide term_symbol — no contradiction possible."""
        warnings = reconcile_species_entry_full(
            _identity(multiplicity=1),
            primary_calc=_opt_calc(),
            statmech=ConformerUploadStatmechPayload(point_group="C2v"),
        )
        term_warnings = [w for w in warnings if w.code == W_TERM_SYMBOL_MISMATCH]
        assert term_warnings == []
