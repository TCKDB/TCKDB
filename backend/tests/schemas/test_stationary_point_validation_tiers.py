"""The four-way split of imaginary-frequency validation (ADR 0008).

======================  ====================================  ======
declared                rule                                  tier
======================  ====================================  ======
``minimum``             ``n_imag == 0``                       block
``vdw_complex``         ``n_imag == 0`` *expected*            warn
transition state        ``n_imag == 1``                       block
transition state        ``|imag_freq_cm1| >= 100 cm⁻¹``       warn
======================  ====================================  ======

The two blocking rules are definitions: no correct calculation produces a
covalently bound minimum with an imaginary mode, or a "transition state"
that is really a well or a higher-order saddle. The two warning rules are
expectations: a van der Waals complex's intermolecular modes sit low
enough that Hessian grid noise can fake a small imaginary mode, and a
genuinely flat or variational barrier can have a soft reaction
coordinate. Refusing either would reject correct science.

Absence is never contradiction — an upload carrying no frequency evidence
is unaffected in every case.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tckdb_schemas.enums import StationaryPointKind as SchemasStationaryPointKind
from tckdb_schemas.stationary_point import (
    TS_IMAGINARY_FREQUENCY_MIN_CM1,
    W_N_IMAG_CONTRADICTS_MINIMUM,
    W_N_IMAG_HIGHER_ORDER_SADDLE,
    W_N_IMAG_SUGGESTS_TS,
    W_TS_IMAG_FREQ_TOO_SMALL,
    W_TS_N_IMAG_NOT_ONE,
    ValidationTier,
    evaluate_species_entry_frequency,
    evaluate_transition_state_frequency,
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


class TestTransitionStateEvaluator:
    def test_absence_is_not_contradiction(self) -> None:
        assert evaluate_transition_state_frequency(None, location="x") == []

    def test_zero_imaginary_modes_blocks(self) -> None:
        findings = evaluate_transition_state_frequency(0, location="ts")
        assert _tiers(findings) == {ValidationTier.block}
        assert _codes(findings) == {W_TS_N_IMAG_NOT_ONE}

    @pytest.mark.parametrize("n_imag", [2, 3, 9])
    def test_higher_order_saddle_blocks(self, n_imag: int) -> None:
        findings = evaluate_transition_state_frequency(n_imag, location="ts")
        assert _tiers(findings) == {ValidationTier.block}
        assert W_N_IMAG_HIGHER_ORDER_SADDLE in findings[0].message

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


def _standalone_ts(
    n_imag: int | None, imag_freq_cm1: float | None = None
) -> TransitionStateUploadRequest:
    freq: dict = {
        "type": "freq",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT,
    }
    if n_imag is not None:
        freq["freq_result"] = {"n_imag": n_imag, "imag_freq_cm1": imag_freq_cm1}
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
    n_imag: int | None, imag_freq_cm1: float | None = None
) -> dict:
    calcs = []
    if n_imag is not None:
        calcs.append(
            {
                "key": "ts_freq",
                "type": "freq",
                "geometry_key": "ts_geom",
                "software_release": _SOFTWARE,
                "level_of_theory": _LOT,
                "freq_n_imag": n_imag,
                "freq_imag_freq_cm1": imag_freq_cm1,
            }
        )
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
    n_imag: int | None, imag_freq_cm1: float | None = None
) -> BundleTransitionStateIn:
    return BundleTransitionStateIn(**_bundle_ts_payload(n_imag, imag_freq_cm1))


def _network_ts(
    n_imag: int | None, imag_freq_cm1: float | None = None
) -> TransitionStateIn:
    return TransitionStateIn(
        key="ts1",
        micro_reaction_key="mr1",
        **_bundle_ts_payload(n_imag, imag_freq_cm1),
    )


_TS_BUILDERS = [_standalone_ts, _bundle_ts, _network_ts]


class TestTransitionStateSeams:
    @pytest.mark.parametrize("build", _TS_BUILDERS)
    def test_zero_imaginary_modes_is_rejected(self, build) -> None:
        with pytest.raises(ValidationError) as exc:
            build(0)
        assert W_TS_N_IMAG_NOT_ONE in str(exc.value)

    @pytest.mark.parametrize("build", _TS_BUILDERS)
    @pytest.mark.parametrize("n_imag", [2, 4])
    def test_higher_order_saddle_is_rejected(self, build, n_imag: int) -> None:
        with pytest.raises(ValidationError) as exc:
            build(n_imag)
        assert W_TS_N_IMAG_NOT_ONE in str(exc.value)

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
