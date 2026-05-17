"""Local-validation tests for the Phase-4 :class:`Statmech` builder.

These tests stay inside the builder layer — they never hit the network
and never assemble a ``ComputedSpeciesUpload`` /
``ComputedReactionUpload``. Coverage for field validation and the
``source_calculations`` normalisation forms.
"""

from __future__ import annotations

import pytest

from tckdb_client.builders import (
    Calculation,
    Geometry,
    LevelOfTheory,
    SoftwareRelease,
    Statmech,
    TCKDBBuilderValidationError,
)


def _sr() -> SoftwareRelease:
    return SoftwareRelease(software="Gaussian", version="16")


def _lot() -> LevelOfTheory:
    return LevelOfTheory(method="wb97xd", basis="def2tzvp")


def _opt() -> Calculation:
    return Calculation.opt(
        _sr(), _lot(),
        output_geometry=Geometry.from_xyz("1\nx\nH 0 0 0"),
        converged=True,
    )


# --- field validation ------------------------------------------------


class TestStatmechFields:
    def test_empty_statmech_is_legal(self):
        # Every field optional; the builder is happy with a "no-op"
        # block. (The server may still 422 if scientific content is
        # required by the bundle workflow — that's the schema's call.)
        s = Statmech()
        assert s.external_symmetry is None
        assert s.point_group is None
        assert s.source_calculations == []

    def test_external_symmetry_must_be_positive(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Statmech(external_symmetry=0)
        with pytest.raises(TCKDBBuilderValidationError):
            Statmech(external_symmetry=-1)

    def test_point_group_must_be_non_empty(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Statmech(point_group="   ")

    def test_is_linear_must_be_bool(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Statmech(is_linear=1)  # type: ignore[arg-type]
        with pytest.raises(TCKDBBuilderValidationError):
            Statmech(is_linear="no")  # type: ignore[arg-type]
        Statmech(is_linear=True)
        Statmech(is_linear=False)

    def test_rigid_rotor_kind_must_be_known_token(self):
        for ok in (
            "atom", "linear", "spherical_top", "symmetric_top", "asymmetric_top",
        ):
            Statmech(rigid_rotor_kind=ok)
        with pytest.raises(TCKDBBuilderValidationError):
            Statmech(rigid_rotor_kind="wobbly_top")

    def test_statmech_treatment_must_be_known_token(self):
        for ok in ("rrho", "rrho_1d", "rrho_nd", "rrho_1d_nd", "rrho_ad", "rrao"):
            Statmech(statmech_treatment=ok)
        with pytest.raises(TCKDBBuilderValidationError):
            Statmech(statmech_treatment="quantum_magic")

    def test_uses_projected_frequencies_must_be_bool(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Statmech(uses_projected_frequencies=1)  # type: ignore[arg-type]

    def test_label_and_note_must_be_non_empty_when_supplied(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Statmech(label="")
        with pytest.raises(TCKDBBuilderValidationError):
            Statmech(note="   ")


# --- source_calculations forms --------------------------------------


class TestStatmechSourceCalculations:
    def test_dict_form(self):
        opt = _opt()
        s = Statmech(source_calculations={"opt": opt})
        assert [r for r, _ in s.source_calculations_iter()] == ["opt"]

    def test_dict_of_list_form(self):
        opt = _opt()
        s = Statmech(
            source_calculations={"freq": [opt], "sp": [opt]},
        )
        roles = [r for r, _ in s.source_calculations_iter()]
        assert roles == ["freq", "sp"]

    def test_list_of_tuples_form_preserves_order(self):
        opt = _opt()
        s = Statmech(
            source_calculations=[("scan", opt), ("freq", opt), ("opt", opt)],
        )
        roles = [r for r, _ in s.source_calculations_iter()]
        assert roles == ["scan", "freq", "opt"]

    def test_unknown_role_rejected(self):
        opt = _opt()
        with pytest.raises(TCKDBBuilderValidationError):
            Statmech(source_calculations={"made_up_role": opt})

    def test_non_calculation_value_rejected(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Statmech(source_calculations={"opt": "not a calc"})  # type: ignore[dict-item]

    def test_bad_tuple_shape_rejected(self):
        opt = _opt()
        with pytest.raises(TCKDBBuilderValidationError):
            Statmech(source_calculations=[("opt", opt, "extra")])  # type: ignore[list-item]


# --- to_payload --------------------------------------------------------


class TestStatmechToPayload:
    def test_to_payload_emits_only_supplied_fields(self):
        s = Statmech(external_symmetry=2, point_group="C2v", is_linear=False)
        payload = s.to_payload()
        assert payload == {
            "external_symmetry": 2,
            "point_group": "C2v",
            "is_linear": False,
        }

    def test_to_payload_emits_source_calculations_with_lookup(self):
        opt = _opt()
        s = Statmech(source_calculations={"opt": opt})
        payload = s.to_payload(
            calc_key_lookup=lambda calc: "opt_key" if calc is opt else "???",
        )
        assert payload["source_calculations"] == [
            {"calculation_key": "opt_key", "role": "opt"},
        ]

    def test_to_payload_requires_lookup_when_emitting_sources(self):
        opt = _opt()
        s = Statmech(source_calculations={"opt": opt})
        with pytest.raises(TCKDBBuilderValidationError):
            s.to_payload(allow_source_calculations=True)

    def test_to_payload_omits_sources_when_disabled(self):
        opt = _opt()
        s = Statmech(source_calculations={"opt": opt})
        payload = s.to_payload(allow_source_calculations=False)
        assert "source_calculations" not in payload
