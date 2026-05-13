"""Frequency-mode ingestion: parsers preserve modes; missing-mode warnings.

Covers three lanes:

* The bundle-side ``CalculationIn`` adapter
  (``network_pdep_upload.calculation_in_to_with_results_payload``) — turns
  flat ``freq_frequencies_cm1`` into a fully-populated
  ``FreqResultPayload.modes`` list with sign-derived ``is_imaginary``.
* Backwards compatibility — old payloads without ``freq_frequencies_cm1``
  (and without ``modes``) still produce a valid ``FreqResultPayload``.
* The ESS-parser-fidelity warning — surfaces when a calculation carries
  ``parameters_parser_version`` but the freq result has no modes.
"""

from __future__ import annotations

from app.schemas.fragments.calculation import (
    CalculationWithResultsPayload,
    FreqResultPayload,
    FrequencyModePayload,
)
from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
from app.schemas.workflows.network_pdep_upload import (
    CalculationIn,
    calculation_in_to_with_results_payload,
)
from app.services.upload_reconciliation import (
    W_FREQ_PARSED_NO_MODES,
    check_freq_parser_fidelity,
    reconcile_species_entry_full,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bundle_freq_calc(**overrides) -> CalculationIn:
    base = dict(
        key="freq1",
        type="freq",
        software_release={"name": "Gaussian", "version": "16"},
        level_of_theory={"method": "wB97X-D", "basis": "def2-TZVP"},
        freq_n_imag=0,
        freq_zpe_hartree=0.05,
    )
    base.update(overrides)
    return CalculationIn(**base)


def _identity(species_entry_kind: str = "minimum") -> SpeciesEntryIdentityPayload:
    return SpeciesEntryIdentityPayload(
        smiles="O",
        charge=0,
        multiplicity=1,
        species_entry_kind=species_entry_kind,
    )


# ---------------------------------------------------------------------------
# Bundle adapter: parser-provided frequencies materialize as modes
# ---------------------------------------------------------------------------


class TestBundleAdapterMaterializesModes:
    def test_flat_frequencies_become_modes_with_sign_derived_flag(self) -> None:
        calc_in = _bundle_freq_calc(
            freq_n_imag=1,
            freq_imag_freq_cm1=-1500.0,
            freq_frequencies_cm1=[-1500.0, 200.0, 1100.0, 3000.0],
        )
        payload = calculation_in_to_with_results_payload(calc_in)

        assert payload.freq_result is not None
        modes = payload.freq_result.modes
        assert modes is not None
        assert [m.mode_index for m in modes] == [1, 2, 3, 4]
        assert [m.is_imaginary for m in modes] == [True, False, False, False]
        # is_imaginary is derived from the sign; the FrequencyModePayload
        # validator would reject mismatched pairs at this point.
        assert modes[0].frequency_cm1 == -1500.0

    def test_no_flat_frequencies_means_no_modes(self) -> None:
        """Backwards compatibility: old payloads (no modes) still validate."""
        calc_in = _bundle_freq_calc(
            freq_n_imag=0,
            freq_zpe_hartree=0.02,
        )
        payload = calculation_in_to_with_results_payload(calc_in)
        assert payload.freq_result is not None
        assert payload.freq_result.modes is None

    def test_real_minimum_with_all_real_modes(self) -> None:
        calc_in = _bundle_freq_calc(
            freq_n_imag=0,
            freq_frequencies_cm1=[150.0, 320.0, 1100.0],
        )
        payload = calculation_in_to_with_results_payload(calc_in)
        modes = payload.freq_result.modes
        assert all(not m.is_imaginary for m in modes)
        assert [m.frequency_cm1 for m in modes] == [150.0, 320.0, 1100.0]


# ---------------------------------------------------------------------------
# Parser-fidelity warning
# ---------------------------------------------------------------------------


def _freq_calc_payload(
    *,
    modes: list[dict] | None = None,
    parameters_parser_version: str | None = None,
) -> CalculationWithResultsPayload:
    base: dict = {
        "type": "freq",
        "software_release": {"name": "Gaussian", "version": "16"},
        "level_of_theory": {"method": "wB97X-D", "basis": "def2-TZVP"},
        "freq_result": {
            "n_imag": 0,
            "zpe_hartree": 0.02,
        },
    }
    if modes is not None:
        base["freq_result"]["modes"] = modes
    if parameters_parser_version is not None:
        base["parameters_parser_version"] = parameters_parser_version
    return CalculationWithResultsPayload.model_validate(base)


class TestFreqParserFidelityWarning:
    def test_warns_when_parser_present_and_modes_missing(self) -> None:
        calc = _freq_calc_payload(
            modes=None, parameters_parser_version="gaussian-output@1.0"
        )
        warnings = check_freq_parser_fidelity([calc])
        assert len(warnings) == 1
        assert warnings[0].code == W_FREQ_PARSED_NO_MODES
        assert "calculations[0].freq_result.modes" in warnings[0].field

    def test_silent_when_parser_present_and_modes_supplied(self) -> None:
        calc = _freq_calc_payload(
            modes=[
                {"mode_index": 1, "frequency_cm1": 1100.0, "is_imaginary": False}
            ],
            parameters_parser_version="gaussian-output@1.0",
        )
        warnings = check_freq_parser_fidelity([calc])
        assert warnings == []

    def test_silent_when_no_parser_version_set(self) -> None:
        """Manual / legacy uploads must not be punished for missing modes."""
        calc = _freq_calc_payload(modes=None, parameters_parser_version=None)
        warnings = check_freq_parser_fidelity([calc])
        assert warnings == []

    def test_silent_for_non_freq_calcs(self) -> None:
        calc = CalculationWithResultsPayload.model_validate(
            {
                "type": "sp",
                "software_release": {"name": "Gaussian", "version": "16"},
                "level_of_theory": {"method": "wB97X-D", "basis": "def2-TZVP"},
                "sp_result": {"electronic_energy_hartree": -76.0},
                "parameters_parser_version": "gaussian-output@1.0",
            }
        )
        assert check_freq_parser_fidelity([calc]) == []

    def test_wired_into_reconcile_species_entry_full(self) -> None:
        primary = _freq_calc_payload(
            modes=None, parameters_parser_version="gaussian-output@1.0"
        )
        warnings = reconcile_species_entry_full(
            _identity(), primary_calc=primary, additional_calcs=[]
        )
        assert any(w.code == W_FREQ_PARSED_NO_MODES for w in warnings)


# ---------------------------------------------------------------------------
# Script-side parser produces FreqResult with frequencies_cm1
# (sanity check that the bulk-loader contract is honored)
# ---------------------------------------------------------------------------


class TestGaussianParserExposesFrequencies:
    def test_parse_freq_result_returns_full_list(self) -> None:
        from scripts.arc_ingestion.gaussian_results import parse_freq_result

        lines = [
            " Frequencies --   -1500.0    150.0    320.0\n",
            " Frequencies --    1100.0    1500.0    3000.0\n",
            " Zero-point correction=         0.123456\n",
            " 1\\1\\... \\NImag=1\\... \n",
        ]
        result = parse_freq_result(lines)
        assert result.frequencies_cm1 == [-1500.0, 150.0, 320.0, 1100.0, 1500.0, 3000.0]
        assert result.n_imag == 1
        assert result.imag_freq_cm1 == -1500.0


class TestBuilderEmitsFrequenciesField:
    def test_make_freq_calculation_passes_frequencies(self) -> None:
        """The ARC ingestion builder must forward parsed frequencies."""
        from types import SimpleNamespace

        from scripts.arc_ingestion.builder import _make_freq_calculation
        from scripts.arc_ingestion.gaussian_results import FreqResult

        run = SimpleNamespace(
            software_name="gaussian",
            software_version="16",
            software_revision="C.01",
            arc_version="1.0",
            arc_git_commit="abc123",
            freq_level=SimpleNamespace(method="wB97X-D", basis="def2-TZVP"),
        )
        sp_info = SimpleNamespace(
            freq_result=FreqResult(
                frequencies_cm1=[-1500.0, 150.0, 320.0],
                n_imag=1,
                imag_freq_cm1=-1500.0,
                zpe_hartree=0.05,
            ),
            paths=SimpleNamespace(freq_log=None),
        )

        calc = _make_freq_calculation(
            key="H2O_freq",
            geometry_key="H2O_geom",
            run=run,
            sp_info=sp_info,
            include_artifacts=False,
        )
        assert calc["freq_frequencies_cm1"] == [-1500.0, 150.0, 320.0]
        assert calc["freq_n_imag"] == 1
        assert calc["freq_imag_freq_cm1"] == -1500.0

    def test_make_freq_calculation_without_frequencies_omits_field(self) -> None:
        """Legacy parsed runs (no frequencies_cm1) keep the old shape."""
        from types import SimpleNamespace

        from scripts.arc_ingestion.builder import _make_freq_calculation
        from scripts.arc_ingestion.gaussian_results import FreqResult

        run = SimpleNamespace(
            software_name="gaussian",
            software_version="16",
            software_revision="C.01",
            arc_version="1.0",
            arc_git_commit="abc123",
            freq_level=SimpleNamespace(method="wB97X-D", basis="def2-TZVP"),
        )
        sp_info = SimpleNamespace(
            freq_result=FreqResult(
                frequencies_cm1=[],  # parser produced nothing parsable
                n_imag=0,
                imag_freq_cm1=None,
                zpe_hartree=0.05,
            ),
            paths=SimpleNamespace(freq_log=None),
        )

        calc = _make_freq_calculation(
            key="H2O_freq",
            geometry_key="H2O_geom",
            run=run,
            sp_info=sp_info,
            include_artifacts=False,
        )
        assert "freq_frequencies_cm1" not in calc
        assert calc["freq_n_imag"] == 0
