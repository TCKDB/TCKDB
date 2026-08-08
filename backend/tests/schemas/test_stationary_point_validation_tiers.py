"""Imaginary-frequency validation across its tiers (ADR 0008, ADR 0012).

======================  ==========================================  =====================
declared                rule                                        tier
======================  ==========================================  =====================
``minimum``             ``n_imag == 0``                             block
``vdw_complex``         ``n_imag == 0`` *expected*                  warn
transition state        at least one imaginary mode                 block
transition state        exactly one designated reaction coordinate  block
transition state        no undeclared extra at or above |ω_RC|      block
transition state        extra imaginary modes, all below τ          warn
transition state        an extra imaginary mode at or above τ       warn + structural flag
transition state        ``|ω_RC| >= 100 cm⁻¹``                      warn
======================  ==========================================  =====================

The minimum rules are unchanged by ADR 0012 and are definitions: no
correct calculation produces a covalently bound minimum with an imaginary
mode. The transition-state blocking rules are *contracts about what the
record says*, not about magnitude — ADR 0012 retired ``n_imag == 1``
because two scientifically correct calculations of the same saddle point
can return ``n_imag == 1`` and ``n_imag == 3``, so the count was a gate a
depositor could pass by changing an integration grid. The warning rules
are expectations: a van der Waals complex's intermolecular modes sit low
enough that Hessian grid noise can fake a small imaginary mode, a
genuinely flat or variational barrier can have a soft reaction
coordinate, and an extra imaginary mode below the protocol's noise floor
has an undetermined sign. Refusing any of them would reject correct
science.

Absence is never contradiction — an upload carrying no frequency evidence
is unaffected in every case.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tckdb_schemas.enums import (
    HessianMethod,
    ImaginaryModeDisposition,
    StationaryPointKind as SchemasStationaryPointKind,
)
from tckdb_schemas.stationary_point import (
    TAU_ANALYTIC_TIGHT_CM1,
    TAU_PROTOCOL_NOT_RECORDED_CM1,
    TS_IMAGINARY_FREQUENCY_MIN_CM1,
    ImaginaryMode,
    TauBasis,
    W_N_IMAG_CONTRADICTS_MINIMUM,
    W_N_IMAG_HIGHER_ORDER_SADDLE,
    W_N_IMAG_SUGGESTS_TS,
    W_TS_EXTRA_IMAGINARY_MODES_BELOW_TAU,
    W_TS_EXTRA_IMAGINARY_MODES_NOT_ASSESSABLE,
    W_TS_EXTRA_IMAGINARY_MODE_ABOVE_TAU,
    W_TS_IMAG_FREQ_TOO_SMALL,
    W_TS_NO_IMAGINARY_MODE,
    W_TS_REACTION_COORDINATE_AMBIGUOUS,
    W_TS_REACTION_COORDINATE_NOT_DESIGNATED,
    ValidationTier,
    evaluate_species_entry_frequency,
    evaluate_transition_state_frequency,
    resolve_tau,
)
from tckdb_schemas.workflows.computed_reaction_upload import (
    BundleSpeciesIn,
    BundleTransitionStateIn,
)
from tckdb_schemas.workflows.computed_species_upload import (
    ComputedSpeciesUploadRequest,
)

from app.schemas.workflows.conformer_upload import ConformerUploadRequest
from app.schemas.workflows.network_pdep_upload import (
    NetworkSpeciesIn,
    TransitionStateIn,
)
from app.schemas.workflows.statmech_upload import StatmechUploadRequest
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.schemas.workflows.transition_state_upload import (
    TransitionStateUploadRequest,
)
from app.schemas.workflows.transport_upload import TransportUploadRequest

_XYZ = "1\ncomment\nH 0.0 0.0 0.0"
_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}
_SPECIES_ENTRY = {"smiles": "[H]", "charge": 0, "multiplicity": 2}

#: Comfortably below the threshold — a soft mode.
_SOFT_CM1 = -30.0
#: Comfortably above it — a real reaction coordinate.
_STIFF_CM1 = -1500.0


def _codes(findings) -> set[str]:
    return {f.code for f in findings}


def _tiers(findings) -> set[ValidationTier]:
    return {f.tier for f in findings}


# ---------------------------------------------------------------------------
# The pure evaluators
# ---------------------------------------------------------------------------


class TestSpeciesEntryEvaluator:
    @pytest.mark.parametrize("kind", list(SchemasStationaryPointKind))
    def test_absence_is_not_contradiction(
        self, kind: SchemasStationaryPointKind
    ) -> None:
        assert evaluate_species_entry_frequency(kind, None, location="x") == []

    @pytest.mark.parametrize("kind", list(SchemasStationaryPointKind))
    def test_zero_imaginary_modes_is_consistent(
        self, kind: SchemasStationaryPointKind
    ) -> None:
        assert evaluate_species_entry_frequency(kind, 0, location="x") == []

    @pytest.mark.parametrize("n_imag", [1, 2, 7])
    def test_minimum_with_any_imaginary_mode_blocks(self, n_imag: int) -> None:
        findings = evaluate_species_entry_frequency(
            SchemasStationaryPointKind.minimum, n_imag, location="calc"
        )
        assert _tiers(findings) == {ValidationTier.block}
        assert _codes(findings) == {W_N_IMAG_CONTRADICTS_MINIMUM}
        assert "calc" in findings[0].message

    def test_minimum_blocking_is_magnitude_blind(self) -> None:
        """ADR 0008 forbids a magnitude threshold on a blocking check."""
        soft = evaluate_species_entry_frequency(
            SchemasStationaryPointKind.minimum, 1, _SOFT_CM1, location="c"
        )
        stiff = evaluate_species_entry_frequency(
            SchemasStationaryPointKind.minimum, 1, _STIFF_CM1, location="c"
        )
        assert _tiers(soft) == _tiers(stiff) == {ValidationTier.block}

    @pytest.mark.parametrize("n_imag", [1, 2, 7])
    def test_vdw_complex_never_blocks(self, n_imag: int) -> None:
        findings = evaluate_species_entry_frequency(
            SchemasStationaryPointKind.vdw_complex, n_imag, location="calc"
        )
        assert findings
        assert _tiers(findings) == {ValidationTier.warn}

    def test_vdw_complex_one_mode_reports_the_minimum_contradiction(self) -> None:
        findings = evaluate_species_entry_frequency(
            SchemasStationaryPointKind.vdw_complex, 1, _SOFT_CM1, location="c"
        )
        assert _codes(findings) == {W_N_IMAG_CONTRADICTS_MINIMUM}

    def test_vdw_complex_two_modes_report_a_higher_order_saddle(self) -> None:
        findings = evaluate_species_entry_frequency(
            SchemasStationaryPointKind.vdw_complex, 2, location="c"
        )
        assert _codes(findings) == {W_N_IMAG_HIGHER_ORDER_SADDLE}

    def test_stiff_vdw_mode_additionally_suggests_a_transition_state(self) -> None:
        """A mode this stiff cannot be an intermolecular vdW mode, so it is
        not the grid noise the vdW carve-out exists to tolerate."""
        findings = evaluate_species_entry_frequency(
            SchemasStationaryPointKind.vdw_complex, 1, _STIFF_CM1, location="c"
        )
        assert W_N_IMAG_SUGGESTS_TS in _codes(findings)
        assert _tiers(findings) == {ValidationTier.warn}

    def test_unknown_magnitude_does_not_suggest_a_transition_state(self) -> None:
        findings = evaluate_species_entry_frequency(
            SchemasStationaryPointKind.vdw_complex, 1, None, location="c"
        )
        assert W_N_IMAG_SUGGESTS_TS not in _codes(findings)

    def test_suggests_ts_boundary_is_inclusive(self) -> None:
        at = evaluate_species_entry_frequency(
            SchemasStationaryPointKind.vdw_complex,
            1,
            -TS_IMAGINARY_FREQUENCY_MIN_CM1,
            location="c",
        )
        just_below = evaluate_species_entry_frequency(
            SchemasStationaryPointKind.vdw_complex,
            1,
            -(TS_IMAGINARY_FREQUENCY_MIN_CM1 - 0.1),
            location="c",
        )
        assert W_N_IMAG_SUGGESTS_TS in _codes(at)
        assert W_N_IMAG_SUGGESTS_TS not in _codes(just_below)


#: The record that forced ADR 0012: a clean reaction coordinate at
#: -1300 cm⁻¹ plus two soft modes the old rule refused outright.
_MOTIVATING_MODES = (
    ImaginaryMode(-1300.0, mode_index=1),
    ImaginaryMode(-42.0, mode_index=2),
    ImaginaryMode(-13.0, mode_index=3),
)


class TestTauResolution:
    """τ is read from provenance, and says which row of the table it used."""

    def test_no_provenance_gives_the_conservative_value(self) -> None:
        tau = resolve_tau()
        assert tau.tau_cm1 == TAU_PROTOCOL_NOT_RECORDED_CM1
        assert tau.basis is TauBasis.protocol_not_recorded
        assert "freq.hessian_method" in tau.reason

    def test_an_unrecorded_hessian_method_is_never_assumed_analytic(self) -> None:
        """Even a tight grid and a tight optimisation cannot buy the analytic
        row: the frequency job's method is the term that dominates."""
        tau = resolve_tau(grid_quality="ultrafine", opt_convergence="tight")
        assert tau.basis is TauBasis.protocol_not_recorded

    def test_a_fully_recorded_tight_protocol_gives_the_tightest_value(self) -> None:
        tau = resolve_tau(
            HessianMethod.analytic,
            grid_quality="ultrafine",
            opt_convergence="tight",
        )
        assert tau.tau_cm1 == TAU_ANALYTIC_TIGHT_CM1
        assert tau.basis is TauBasis.analytic_tight

    def test_the_reason_names_what_was_missing(self) -> None:
        tau = resolve_tau(HessianMethod.analytic, grid_quality="ultrafine")
        assert tau.basis is TauBasis.analytic_default
        assert "opt.convergence not recorded" in tau.reason

    @pytest.mark.parametrize(
        ("method", "expected"),
        [
            (HessianMethod.finite_difference_gradient, TauBasis.finite_difference_gradient),
            (HessianMethod.finite_difference_energy, TauBasis.finite_difference_energy),
        ],
    )
    def test_finite_difference_protocols_widen_the_floor(
        self, method: HessianMethod, expected: TauBasis
    ) -> None:
        tau = resolve_tau(method, grid_quality="ultrafine", opt_convergence="tight")
        assert tau.basis is expected
        assert tau.tau_cm1 > TAU_ANALYTIC_TIGHT_CM1


class TestTransitionStateEvaluator:
    def test_absence_is_not_contradiction(self) -> None:
        assert evaluate_transition_state_frequency(None, location="x") == []

    def test_zero_imaginary_modes_blocks(self) -> None:
        """Unchanged in tier, renamed in code: with no imaginary mode there
        is no reaction coordinate, which is what ADR 0012 kept of the old
        rule."""
        findings = evaluate_transition_state_frequency(0, location="ts")
        assert _tiers(findings) == {ValidationTier.block}
        assert _codes(findings) == {W_TS_NO_IMAGINARY_MODE}

    @pytest.mark.parametrize("n_imag", [2, 3, 9])
    def test_extra_modes_without_a_designation_block(self, n_imag: int) -> None:
        """ADR 0012 blocks on the *contract*, not the count: a record that
        will not say which mode is the barrier cannot be used as one."""
        findings = evaluate_transition_state_frequency(n_imag, location="ts")
        assert _tiers(findings) == {ValidationTier.block}
        assert _codes(findings) == {W_TS_REACTION_COORDINATE_NOT_DESIGNATED}

    def test_the_motivating_record_is_accepted_with_a_warning(self) -> None:
        """-1300, -42, -13 cm⁻¹. Refused outright before ADR 0012."""
        findings = evaluate_transition_state_frequency(
            3,
            -1300.0,
            location="ts",
            imaginary_modes=_MOTIVATING_MODES,
            reaction_coordinate_mode_index=1,
        )
        assert _tiers(findings) == {ValidationTier.warn}
        assert _codes(findings) == {W_TS_EXTRA_IMAGINARY_MODES_BELOW_TAU}
        assert not any(f.structural_flag for f in findings)

    def test_the_motivating_record_is_accepted_under_every_tau(self) -> None:
        """ADR 0012 claims this record survives every row of the protocol
        table, which is the claim that makes tau uncontroversial here."""
        for method in (None, *HessianMethod):
            tau = resolve_tau(
                method, grid_quality="ultrafine", opt_convergence="tight"
            )
            findings = evaluate_transition_state_frequency(
                3,
                -1300.0,
                location="ts",
                imaginary_modes=_MOTIVATING_MODES,
                reaction_coordinate_mode_index=1,
                tau=tau,
            )
            assert _tiers(findings) == {ValidationTier.warn}, method

    def test_a_finding_records_the_tau_it_used_and_why(self) -> None:
        """ADR 0012 requires that a reader can re-decide the mode later,
        which "tau was 50" alone does not support."""
        tau = resolve_tau()
        findings = evaluate_transition_state_frequency(
            3,
            -1300.0,
            location="ts",
            imaginary_modes=_MOTIVATING_MODES,
            reaction_coordinate_mode_index=1,
            tau=tau,
        )
        assert findings[0].tau is tau
        assert findings[0].tau.basis is TauBasis.protocol_not_recorded
        assert str(int(TAU_PROTOCOL_NOT_RECORDED_CM1)) in findings[0].message

    def test_an_extra_mode_at_or_above_tau_is_flagged_not_refused(self) -> None:
        tau = resolve_tau(
            HessianMethod.analytic,
            grid_quality="ultrafine",
            opt_convergence="tight",
        )
        findings = evaluate_transition_state_frequency(
            2,
            -1300.0,
            location="ts",
            imaginary_modes=(
                ImaginaryMode(-1300.0, mode_index=1),
                ImaginaryMode(-42.0, mode_index=2, disposition=ImaginaryModeDisposition.torsion),
            ),
            reaction_coordinate_mode_index=1,
            tau=tau,
        )
        assert _tiers(findings) == {ValidationTier.warn}
        assert _codes(findings) == {W_TS_EXTRA_IMAGINARY_MODE_ABOVE_TAU}
        assert all(f.structural_flag for f in findings)

    def test_the_same_record_is_unflagged_under_a_looser_protocol(self) -> None:
        """The point of ADR 0012: -42 cm⁻¹ is real curvature under an
        analytic Hessian on a tight grid and indistinguishable from zero
        under an unrecorded protocol. The same numbers, two verdicts."""
        modes = (
            ImaginaryMode(-1300.0, mode_index=1),
            ImaginaryMode(-42.0, mode_index=2, disposition=ImaginaryModeDisposition.torsion),
        )
        loose = evaluate_transition_state_frequency(
            2,
            -1300.0,
            location="ts",
            imaginary_modes=modes,
            reaction_coordinate_mode_index=1,
            tau=resolve_tau(),
        )
        assert _codes(loose) == {W_TS_EXTRA_IMAGINARY_MODES_BELOW_TAU}
        assert not any(f.structural_flag for f in loose)

    def test_an_undeclared_mode_stiffer_than_the_barrier_blocks(self) -> None:
        findings = evaluate_transition_state_frequency(
            2,
            -400.0,
            location="ts",
            imaginary_modes=(
                ImaginaryMode(-400.0, mode_index=1),
                ImaginaryMode(-900.0, mode_index=2),
            ),
            reaction_coordinate_mode_index=1,
        )
        assert _tiers(findings) == {ValidationTier.block}
        assert _codes(findings) == {W_TS_REACTION_COORDINATE_AMBIGUOUS}

    def test_declaring_the_competing_mode_reopens_the_door(self) -> None:
        """The block is on ambiguity, not on stiffness: a symmetry-breaking
        mode a depositor has named is a record a reader can act on."""
        findings = evaluate_transition_state_frequency(
            2,
            -400.0,
            location="ts",
            imaginary_modes=(
                ImaginaryMode(-400.0, mode_index=1),
                ImaginaryMode(
                    -900.0,
                    mode_index=2,
                    disposition=ImaginaryModeDisposition.symmetry_breaking,
                ),
            ),
            reaction_coordinate_mode_index=1,
        )
        assert _tiers(findings) == {ValidationTier.warn}
        assert W_TS_EXTRA_IMAGINARY_MODE_ABOVE_TAU in _codes(findings)

    def test_an_explicit_unassigned_counts_as_declared(self) -> None:
        """An honest "I looked and could not classify it" is a statement a
        reader can act on; it never suppresses the flag."""
        findings = evaluate_transition_state_frequency(
            2,
            -400.0,
            location="ts",
            imaginary_modes=(
                ImaginaryMode(-400.0, mode_index=1),
                ImaginaryMode(
                    -900.0,
                    mode_index=2,
                    disposition=ImaginaryModeDisposition.unassigned,
                ),
            ),
            reaction_coordinate_mode_index=1,
        )
        assert _tiers(findings) == {ValidationTier.warn}
        assert all(f.structural_flag for f in findings)

    def test_a_designation_with_no_frequency_list_is_flagged_not_refused(self) -> None:
        findings = evaluate_transition_state_frequency(
            3, -1300.0, location="ts", reaction_coordinate_mode_index=1
        )
        assert _tiers(findings) == {ValidationTier.warn}
        assert _codes(findings) == {W_TS_EXTRA_IMAGINARY_MODES_NOT_ASSESSABLE}
        assert all(f.structural_flag for f in findings)

    def test_tau_never_decides_between_blocking_and_warning(self) -> None:
        """The property that makes it safe to resolve tau from provenance a
        payload may not carry."""
        cases = [
            (0, None, None),
            (3, None, None),
            (
                2,
                (ImaginaryMode(-400.0, mode_index=1), ImaginaryMode(-900.0, mode_index=2)),
                1,
            ),
            (3, _MOTIVATING_MODES, 1),
            (1, (ImaginaryMode(-1300.0, mode_index=1),), None),
        ]
        for n_imag, modes, rc_index in cases:
            tiers = {
                frozenset(
                    _tiers(
                        evaluate_transition_state_frequency(
                            n_imag,
                            -1300.0,
                            location="ts",
                            imaginary_modes=modes,
                            reaction_coordinate_mode_index=rc_index,
                            tau=resolve_tau(
                                method,
                                grid_quality="ultrafine",
                                opt_convergence="tight",
                            ),
                        )
                    )
                )
                for method in (None, *HessianMethod)
            }
            assert len(tiers) == 1, (n_imag, tiers)

    def test_one_stiff_mode_is_clean(self) -> None:
        assert evaluate_transition_state_frequency(1, _STIFF_CM1, location="ts") == []

    def test_one_soft_mode_warns_but_never_blocks(self) -> None:
        findings = evaluate_transition_state_frequency(1, _SOFT_CM1, location="ts")
        assert _tiers(findings) == {ValidationTier.warn}
        assert _codes(findings) == {W_TS_IMAG_FREQ_TOO_SMALL}

    def test_one_mode_of_unknown_magnitude_is_clean(self) -> None:
        assert evaluate_transition_state_frequency(1, None, location="ts") == []

    def test_magnitude_threshold_boundary_is_exclusive(self) -> None:
        at = evaluate_transition_state_frequency(
            1, -TS_IMAGINARY_FREQUENCY_MIN_CM1, location="ts"
        )
        just_below = evaluate_transition_state_frequency(
            1, -(TS_IMAGINARY_FREQUENCY_MIN_CM1 - 0.1), location="ts"
        )
        assert at == []
        assert _codes(just_below) == {W_TS_IMAG_FREQ_TOO_SMALL}

    def test_the_softness_rule_reads_the_designated_coordinate(self) -> None:
        """ADR 0012 changed what this fires on without changing what it
        fires at: the reaction coordinate, not "the" imaginary mode."""
        findings = evaluate_transition_state_frequency(
            2,
            None,
            location="ts",
            imaginary_modes=(
                ImaginaryMode(-30.0, mode_index=1),
                ImaginaryMode(-8.0, mode_index=2),
            ),
            reaction_coordinate_mode_index=1,
        )
        assert W_TS_IMAG_FREQ_TOO_SMALL in _codes(findings)
        assert _tiers(findings) == {ValidationTier.warn}

    def test_sign_convention_is_irrelevant(self) -> None:
        """Producers write the imaginary mode as a negative magnitude, but a
        positive one means the same thing."""
        negative = evaluate_transition_state_frequency(1, -50.0, location="ts")
        positive = evaluate_transition_state_frequency(1, 50.0, location="ts")
        assert _codes(negative) == _codes(positive) == {W_TS_IMAG_FREQ_TOO_SMALL}


# ---------------------------------------------------------------------------
# Seam 1 — conformer upload
# ---------------------------------------------------------------------------


def _conformer_payload(
    n_imag: int | None,
    *,
    kind: str = "minimum",
    imag_freq_cm1: float | None = None,
) -> dict:
    freq: dict = {
        "type": "freq",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT,
    }
    if n_imag is not None:
        freq["freq_result"] = {"n_imag": n_imag, "imag_freq_cm1": imag_freq_cm1}
    return {
        "species_entry": {**_SPECIES_ENTRY, "species_entry_kind": kind},
        "geometry": {"xyz_text": _XYZ},
        "calculation": {
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
        },
        "additional_calculations": [freq],
    }


class TestConformerUploadRequest:
    @pytest.mark.parametrize("n_imag", [1, 2, 5])
    def test_minimum_with_imaginary_modes_is_rejected(self, n_imag: int) -> None:
        with pytest.raises(ValidationError) as exc:
            ConformerUploadRequest(**_conformer_payload(n_imag))
        assert W_N_IMAG_CONTRADICTS_MINIMUM in str(exc.value)

    @pytest.mark.parametrize("n_imag", [1, 2, 5])
    def test_vdw_complex_with_imaginary_modes_is_accepted(self, n_imag: int) -> None:
        request = ConformerUploadRequest(
            **_conformer_payload(n_imag, kind="vdw_complex")
        )
        findings = request.stationary_point_findings()
        assert findings
        assert _tiers(findings) == {ValidationTier.warn}

    def test_zero_imaginary_modes_is_accepted(self) -> None:
        request = ConformerUploadRequest(**_conformer_payload(0))
        assert request.stationary_point_findings() == []

    @pytest.mark.parametrize("kind", ["minimum", "vdw_complex"])
    def test_no_frequency_evidence_is_unaffected(self, kind: str) -> None:
        request = ConformerUploadRequest(**_conformer_payload(None, kind=kind))
        assert request.additional_calculations[0].freq_result is None
        assert request.stationary_point_findings() == []

    def test_primary_calculation_is_checked_too(self) -> None:
        payload = _conformer_payload(0)
        payload["calculation"] = {
            "type": "freq",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
            "freq_result": {"n_imag": 2},
        }
        with pytest.raises(ValidationError) as exc:
            ConformerUploadRequest(**payload)
        assert W_N_IMAG_CONTRADICTS_MINIMUM in str(exc.value)


# ---------------------------------------------------------------------------
# Seams 2-4 — standalone product uploads with inline species calculations
# ---------------------------------------------------------------------------


def _keyed_calc(n_imag: int, imag_freq_cm1: float | None = None) -> dict:
    return {
        "key": "f1",
        "calculation": {
            "type": "freq",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
            "freq_result": {"n_imag": n_imag, "imag_freq_cm1": imag_freq_cm1},
        },
    }


def _statmech(kind: str, n_imag: int) -> StatmechUploadRequest:
    return StatmechUploadRequest(
        species_entry={**_SPECIES_ENTRY, "species_entry_kind": kind},
        calculations=[_keyed_calc(n_imag)],
    )


def _thermo(kind: str, n_imag: int) -> ThermoUploadRequest:
    return ThermoUploadRequest(
        species_entry={**_SPECIES_ENTRY, "species_entry_kind": kind},
        h298_kj_mol=1.0,
        calculations=[_keyed_calc(n_imag)],
    )


def _transport(kind: str, n_imag: int) -> TransportUploadRequest:
    return TransportUploadRequest(
        species_entry={**_SPECIES_ENTRY, "species_entry_kind": kind},
        sigma_angstrom=3.0,
        epsilon_over_k_k=100.0,
        calculations=[_keyed_calc(n_imag)],
    )


_PRODUCT_BUILDERS = [_statmech, _thermo, _transport]


class TestStandaloneProductUploads:
    @pytest.mark.parametrize("build", _PRODUCT_BUILDERS)
    @pytest.mark.parametrize("n_imag", [1, 2])
    def test_minimum_is_rejected(self, build, n_imag: int) -> None:
        with pytest.raises(ValidationError) as exc:
            build("minimum", n_imag)
        assert W_N_IMAG_CONTRADICTS_MINIMUM in str(exc.value)

    @pytest.mark.parametrize("build", _PRODUCT_BUILDERS)
    @pytest.mark.parametrize("n_imag", [1, 2])
    def test_vdw_complex_is_accepted_and_warns(self, build, n_imag: int) -> None:
        request = build("vdw_complex", n_imag)
        assert _tiers(request.stationary_point_findings()) == {ValidationTier.warn}

    @pytest.mark.parametrize("build", _PRODUCT_BUILDERS)
    def test_zero_imaginary_modes_is_accepted(self, build) -> None:
        assert build("minimum", 0).stationary_point_findings() == []

    def test_no_inline_calculations_is_unaffected(self) -> None:
        request = StatmechUploadRequest(species_entry=_SPECIES_ENTRY)
        assert request.calculations == []
        assert request.stationary_point_findings() == []


# ---------------------------------------------------------------------------
# Seam 5 — computed-species bundle
# ---------------------------------------------------------------------------


def _bundle_conformer(n_imag: int, imag_freq_cm1: float | None = None) -> dict:
    return {
        "key": "c1",
        "geometry": {"xyz_text": _XYZ},
        "primary_calculation": {
            "key": "c1_opt",
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
        },
        "additional_calculations": [
            {
                "key": "c1_freq",
                "type": "freq",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "freq_result": {"n_imag": n_imag, "imag_freq_cm1": imag_freq_cm1},
            }
        ],
    }


class TestComputedSpeciesBundle:
    @pytest.mark.parametrize("n_imag", [1, 3])
    def test_minimum_is_rejected(self, n_imag: int) -> None:
        with pytest.raises(ValidationError) as exc:
            ComputedSpeciesUploadRequest(
                species_entry=_SPECIES_ENTRY,
                conformers=[_bundle_conformer(n_imag)],
            )
        assert W_N_IMAG_CONTRADICTS_MINIMUM in str(exc.value)

    @pytest.mark.parametrize("n_imag", [1, 3])
    def test_vdw_complex_is_accepted_and_warns(self, n_imag: int) -> None:
        request = ComputedSpeciesUploadRequest(
            species_entry={**_SPECIES_ENTRY, "species_entry_kind": "vdw_complex"},
            conformers=[_bundle_conformer(n_imag)],
        )
        assert _tiers(request.stationary_point_findings()) == {ValidationTier.warn}

    def test_zero_imaginary_modes_is_accepted(self) -> None:
        request = ComputedSpeciesUploadRequest(
            species_entry=_SPECIES_ENTRY,
            conformers=[_bundle_conformer(0)],
        )
        assert request.stationary_point_findings() == []


# ---------------------------------------------------------------------------
# Seams 6-7 — bundle species blocks (computed reaction, network PDep)
# ---------------------------------------------------------------------------


def _bundle_species(
    n_imag: int,
    *,
    kind: str = "minimum",
    imag_freq_cm1: float | None = None,
) -> dict:
    return {
        "key": "s1",
        "species_entry": {**_SPECIES_ENTRY, "species_entry_kind": kind},
        "conformers": [
            {
                "key": "s1_c1",
                "geometry": {"key": "s1_geom", "xyz_text": _XYZ},
                "calculation": {
                    "key": "s1_opt",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                },
            }
        ],
        "calculations": [
            {
                "key": "s1_freq",
                "type": "freq",
                "geometry_key": "s1_geom",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "freq_n_imag": n_imag,
                "freq_imag_freq_cm1": imag_freq_cm1,
            }
        ],
    }


_SPECIES_BLOCKS = [BundleSpeciesIn, NetworkSpeciesIn]


class TestBundleSpeciesBlocks:
    @pytest.mark.parametrize("model", _SPECIES_BLOCKS)
    @pytest.mark.parametrize("n_imag", [1, 2])
    def test_minimum_is_rejected(self, model, n_imag: int) -> None:
        with pytest.raises(ValidationError) as exc:
            model(**_bundle_species(n_imag))
        assert W_N_IMAG_CONTRADICTS_MINIMUM in str(exc.value)

    @pytest.mark.parametrize("model", _SPECIES_BLOCKS)
    @pytest.mark.parametrize("n_imag", [1, 2])
    def test_vdw_complex_is_accepted_and_warns(self, model, n_imag: int) -> None:
        species = model(**_bundle_species(n_imag, kind="vdw_complex"))
        assert _tiers(species.stationary_point_findings()) == {ValidationTier.warn}

    @pytest.mark.parametrize("model", _SPECIES_BLOCKS)
    def test_zero_imaginary_modes_is_accepted(self, model) -> None:
        assert model(**_bundle_species(0)).stationary_point_findings() == []

    @pytest.mark.parametrize("model", _SPECIES_BLOCKS)
    def test_non_freq_calculation_carrying_freq_fields_is_ignored(
        self, model
    ) -> None:
        """The bundle adapter only persists ``freq_*`` off a freq-type calc,
        so the check must read the same way or it would refuse a payload
        over a value the database never stores."""
        payload = _bundle_species(0)
        payload["calculations"][0]["type"] = "sp"
        payload["calculations"][0]["freq_n_imag"] = 3
        assert model(**payload).stationary_point_findings() == []


# ---------------------------------------------------------------------------
# Seams 8-10 — transition states
# ---------------------------------------------------------------------------


def _ts_reaction() -> dict:
    return {
        "reversible": True,
        "reactants": [{"species_entry": {**_SPECIES_ENTRY, "smiles": "[H]"}}],
        "products": [{"species_entry": {**_SPECIES_ENTRY, "smiles": "[H]"}}],
    }


#: The motivating record, as a producer would deposit it: the signed
#: frequency list, the designated reaction coordinate, and a disposition
#: for each other imaginary mode.
_MOTIVATING_FREQUENCIES = [-1300.0, -42.0, -13.0, 500.0]
_MOTIVATING_DISPOSITIONS = {2: "torsion", 3: "rigid_body_residue"}


def _standalone_ts(
    n_imag: int | None,
    imag_freq_cm1: float | None = None,
    *,
    frequencies: list[float] | None = None,
    reaction_coordinate_mode_index: int | None = None,
    dispositions: dict[int, str] | None = None,
) -> TransitionStateUploadRequest:
    freq: dict = {
        "type": "freq",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT,
    }
    if n_imag is not None:
        result: dict = {"n_imag": n_imag, "imag_freq_cm1": imag_freq_cm1}
        if frequencies is not None:
            dispositions = dispositions or {}
            result["modes"] = [
                {
                    "mode_index": index + 1,
                    "frequency_cm1": value,
                    "is_imaginary": value < 0,
                    "imaginary_disposition": dispositions.get(index + 1),
                }
                for index, value in enumerate(frequencies)
            ]
        if reaction_coordinate_mode_index is not None:
            result["reaction_coordinate_mode_index"] = reaction_coordinate_mode_index
        freq["freq_result"] = result
    return TransitionStateUploadRequest(
        reaction=_ts_reaction(),
        charge=0,
        multiplicity=2,
        geometry={"xyz_text": _XYZ},
        primary_opt={
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
        },
        additional_calculations=[freq],
    )


def _bundle_ts_payload(
    n_imag: int | None,
    imag_freq_cm1: float | None = None,
    *,
    frequencies: list[float] | None = None,
    reaction_coordinate_mode_index: int | None = None,
    dispositions: dict[int, str] | None = None,
) -> dict:
    calcs = []
    if n_imag is not None:
        calc: dict = {
            "key": "ts_freq",
            "type": "freq",
            "geometry_key": "ts_geom",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
            "freq_n_imag": n_imag,
            "freq_imag_freq_cm1": imag_freq_cm1,
        }
        if frequencies is not None:
            calc["freq_frequencies_cm1"] = frequencies
        if reaction_coordinate_mode_index is not None:
            calc["freq_reaction_coordinate_mode_index"] = (
                reaction_coordinate_mode_index
            )
        if dispositions is not None:
            calc["freq_imaginary_dispositions"] = dispositions
        calcs.append(calc)
    return {
        "charge": 0,
        "multiplicity": 2,
        "geometry": {"key": "ts_geom", "xyz_text": _XYZ},
        "calculation": {
            "key": "ts_opt",
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
        },
        "calculations": calcs,
    }


def _bundle_ts(
    n_imag: int | None, imag_freq_cm1: float | None = None, **kwargs
) -> BundleTransitionStateIn:
    return BundleTransitionStateIn(
        **_bundle_ts_payload(n_imag, imag_freq_cm1, **kwargs)
    )


def _network_ts(
    n_imag: int | None, imag_freq_cm1: float | None = None, **kwargs
) -> TransitionStateIn:
    return TransitionStateIn(
        key="ts1",
        micro_reaction_key="mr1",
        **_bundle_ts_payload(n_imag, imag_freq_cm1, **kwargs),
    )


_TS_BUILDERS = [_standalone_ts, _bundle_ts, _network_ts]


class TestTransitionStateSeams:
    @pytest.mark.parametrize("build", _TS_BUILDERS)
    def test_zero_imaginary_modes_is_rejected(self, build) -> None:
        with pytest.raises(ValidationError) as exc:
            build(0)
        assert W_TS_NO_IMAGINARY_MODE in str(exc.value)

    @pytest.mark.parametrize("build", _TS_BUILDERS)
    @pytest.mark.parametrize("n_imag", [2, 4])
    def test_extra_modes_without_a_designation_are_rejected(
        self, build, n_imag: int
    ) -> None:
        with pytest.raises(ValidationError) as exc:
            build(n_imag)
        assert W_TS_REACTION_COORDINATE_NOT_DESIGNATED in str(exc.value)

    @pytest.mark.parametrize("build", _TS_BUILDERS)
    def test_the_motivating_record_is_accepted_with_a_warning(self, build) -> None:
        """-1300, -42, -13 cm⁻¹ reaches every seam that can carry a
        transition state, and every one of them accepts it."""
        request = build(
            3,
            -1300.0,
            frequencies=_MOTIVATING_FREQUENCIES,
            reaction_coordinate_mode_index=1,
            dispositions=_MOTIVATING_DISPOSITIONS,
        )
        findings = request.stationary_point_findings()
        assert _tiers(findings) == {ValidationTier.warn}
        assert _codes(findings) == {W_TS_EXTRA_IMAGINARY_MODES_BELOW_TAU}

    @pytest.mark.parametrize("build", _TS_BUILDERS)
    def test_one_imaginary_mode_is_accepted(self, build) -> None:
        assert build(1, _STIFF_CM1).stationary_point_findings() == []

    @pytest.mark.parametrize("build", _TS_BUILDERS)
    def test_one_soft_imaginary_mode_is_accepted_and_warns(self, build) -> None:
        findings = build(1, _SOFT_CM1).stationary_point_findings()
        assert _codes(findings) == {W_TS_IMAG_FREQ_TOO_SMALL}
        assert _tiers(findings) == {ValidationTier.warn}

    @pytest.mark.parametrize("build", _TS_BUILDERS)
    def test_no_frequency_evidence_is_unaffected(self, build) -> None:
        assert build(None).stationary_point_findings() == []

    def test_a_transition_state_is_never_judged_as_a_minimum(self) -> None:
        """The species rule and the TS rule are mutually exclusive: what
        refuses a minimum is exactly what a transition state requires."""
        ts_findings = _bundle_ts(1, _STIFF_CM1).stationary_point_findings()
        species_findings = evaluate_species_entry_frequency(
            SchemasStationaryPointKind.minimum, 1, _STIFF_CM1, location="c"
        )
        assert ts_findings == []
        assert _tiers(species_findings) == {ValidationTier.block}


class TestReactionCoordinateDesignationIsWellFormed:
    """Payload-level consistency, separate from the chemistry judgement.

    An index naming a real mode, or naming nothing, is a malformed record
    whatever kind of stationary point it describes — so it is refused by
    the payload rather than by the rule.
    """

    def test_the_index_must_name_a_deposited_mode(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _standalone_ts(
                3,
                -1300.0,
                frequencies=_MOTIVATING_FREQUENCIES,
                reaction_coordinate_mode_index=99,
            )
        assert "names no mode" in str(exc.value)

    def test_the_index_must_name_an_imaginary_mode(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _standalone_ts(
                3,
                -1300.0,
                frequencies=_MOTIVATING_FREQUENCIES,
                reaction_coordinate_mode_index=4,
            )
        assert "not imaginary" in str(exc.value)

    def test_a_designation_needs_the_frequency_list(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _standalone_ts(3, -1300.0, reaction_coordinate_mode_index=1)
        assert "requires the frequency list" in str(exc.value)

    def test_the_reaction_coordinate_cannot_also_carry_a_disposition(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _standalone_ts(
                3,
                -1300.0,
                frequencies=_MOTIVATING_FREQUENCIES,
                reaction_coordinate_mode_index=1,
                dispositions={1: "torsion", **_MOTIVATING_DISPOSITIONS},
            )
        assert "cannot both be true" in str(exc.value)

    def test_a_real_mode_cannot_carry_a_disposition(self) -> None:
        with pytest.raises(ValidationError) as exc:
            _standalone_ts(
                3,
                -1300.0,
                frequencies=_MOTIVATING_FREQUENCIES,
                reaction_coordinate_mode_index=1,
                dispositions={**_MOTIVATING_DISPOSITIONS, 4: "torsion"},
            )
        assert "not imaginary" in str(exc.value)
