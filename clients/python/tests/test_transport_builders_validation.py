"""Local-validation tests for the Phase-5 :class:`Transport` builder.

Coverage for field validation, LJ pair both-or-neither, source-calc
normalisation, and the role vocabulary.
"""

from __future__ import annotations

import pytest

from tckdb_client.builders import (
    Calculation,
    Geometry,
    LevelOfTheory,
    SoftwareRelease,
    TCKDBBuilderValidationError,
    Transport,
)


def _opt() -> Calculation:
    return Calculation.opt(
        SoftwareRelease(software="Gaussian", version="16"),
        LevelOfTheory(method="wb97xd", basis="def2tzvp"),
        output_geometry=Geometry.from_xyz("1\nx\nH 0 0 0"),
        converged=True,
    )


# --- field validation ------------------------------------------------


class TestTransportFields:
    def test_at_least_one_value_required(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Transport()

    def test_lj_pair_both_required(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Transport(sigma_angstrom=3.8)
        with pytest.raises(TCKDBBuilderValidationError):
            Transport(epsilon_over_k_k=150.0)

    def test_lj_pair_both_present_ok(self):
        t = Transport(sigma_angstrom=3.8, epsilon_over_k_k=150.0)
        assert t.sigma_angstrom == 3.8
        assert t.epsilon_over_k_k == 150.0

    def test_sigma_must_be_positive(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Transport(sigma_angstrom=0, epsilon_over_k_k=150)
        with pytest.raises(TCKDBBuilderValidationError):
            Transport(sigma_angstrom=-1, epsilon_over_k_k=150)

    def test_epsilon_must_be_positive(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Transport(sigma_angstrom=3.8, epsilon_over_k_k=0)
        with pytest.raises(TCKDBBuilderValidationError):
            Transport(sigma_angstrom=3.8, epsilon_over_k_k=-1)

    def test_rotational_relaxation_must_be_non_negative(self):
        # zero is allowed (server uses ``ge=0``)
        Transport(rotational_relaxation=0)
        with pytest.raises(TCKDBBuilderValidationError):
            Transport(rotational_relaxation=-1)

    def test_dipole_can_be_any_numeric(self):
        # A dipole can be 0, positive, or modelled with a sign — the
        # backend takes any float.
        Transport(dipole_debye=0)
        Transport(dipole_debye=-1.5)
        Transport(dipole_debye=3.2)

    def test_polarizability_can_be_any_numeric(self):
        Transport(polarizability_angstrom3=5.0)
        Transport(polarizability_angstrom3=0)

    def test_sigma_must_be_numeric(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Transport(sigma_angstrom="3.8", epsilon_over_k_k=150)  # type: ignore[arg-type]

    def test_dipole_must_be_numeric(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Transport(dipole_debye="0.1")  # type: ignore[arg-type]

    def test_bool_rejected_for_numeric_fields(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Transport(dipole_debye=True)  # type: ignore[arg-type]

    def test_label_and_note_must_be_non_empty_when_supplied(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Transport(dipole_debye=0, label="")
        with pytest.raises(TCKDBBuilderValidationError):
            Transport(dipole_debye=0, note="   ")


# --- source_calculations forms --------------------------------------


class TestTransportSourceCalculations:
    def test_dict_form(self):
        opt = _opt()
        t = Transport(
            dipole_debye=0.1,
            source_calculations={"supporting_geometry": opt},
        )
        assert [r for r, _ in t.source_calculations_iter()] == [
            "supporting_geometry",
        ]

    def test_dict_of_list_form(self):
        opt = _opt()
        t = Transport(
            dipole_debye=0.1,
            source_calculations={"dipole": [opt, opt]},
        )
        roles = [r for r, _ in t.source_calculations_iter()]
        assert roles == ["dipole", "dipole"]

    def test_list_of_tuples_form_preserves_order(self):
        opt = _opt()
        t = Transport(
            dipole_debye=0.1,
            source_calculations=[
                ("supporting_geometry", opt),
                ("polarizability", opt),
                ("full_transport", opt),
            ],
        )
        roles = [r for r, _ in t.source_calculations_iter()]
        assert roles == ["supporting_geometry", "polarizability", "full_transport"]

    def test_unknown_role_rejected(self):
        opt = _opt()
        with pytest.raises(TCKDBBuilderValidationError):
            Transport(
                dipole_debye=0.1,
                source_calculations={"made_up_role": opt},
            )

    def test_non_calculation_value_rejected(self):
        with pytest.raises(TCKDBBuilderValidationError):
            Transport(
                dipole_debye=0.1,
                source_calculations={"supporting_geometry": "not a calc"},  # type: ignore[dict-item]
            )

    def test_bad_tuple_shape_rejected(self):
        opt = _opt()
        with pytest.raises(TCKDBBuilderValidationError):
            Transport(
                dipole_debye=0.1,
                source_calculations=[("supporting_geometry", opt, "extra")],  # type: ignore[list-item]
            )


# --- to_payload ------------------------------------------------------


class TestTransportToPayload:
    def test_emits_only_supplied_fields(self):
        t = Transport(sigma_angstrom=3.8, epsilon_over_k_k=150.0, dipole_debye=0.1)
        assert t.to_payload() == {
            "sigma_angstrom": 3.8,
            "epsilon_over_k_k": 150.0,
            "dipole_debye": 0.1,
        }

    def test_emits_source_calculations_with_lookup(self):
        opt = _opt()
        t = Transport(
            dipole_debye=0.1,
            source_calculations={"supporting_geometry": opt},
        )
        payload = t.to_payload(
            allow_source_calculations=True,
            calc_key_lookup=lambda c: "opt_key" if c is opt else "???",
        )
        assert payload["source_calculations"] == [
            {"calculation_key": "opt_key", "role": "supporting_geometry"},
        ]

    def test_requires_lookup_when_emitting_sources(self):
        opt = _opt()
        t = Transport(
            dipole_debye=0.1,
            source_calculations={"supporting_geometry": opt},
        )
        with pytest.raises(TCKDBBuilderValidationError):
            t.to_payload(allow_source_calculations=True)

    def test_default_omits_source_calculations(self):
        """The bundle-upload assemblers leave the flag at False because
        the bundle schemas don't carry the field today."""
        opt = _opt()
        t = Transport(
            dipole_debye=0.1,
            source_calculations={"supporting_geometry": opt},
        )
        assert "source_calculations" not in t.to_payload()
