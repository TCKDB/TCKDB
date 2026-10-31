"""Owner decision 2026-10-31: a minimum's unrecorded Hessian method resolves
through the assumed program default (ADR 0012, 2026-09-04 table) at validation.
"""

from __future__ import annotations

from tests.api.test_api_minimum_imaginary_tau import _codes, _payload, _stored_freq


def test_a_gaussian_dft_minimum_with_a_minus_40_mode_and_no_method_is_refused(client):
    """Reproduction (a): accepted at tau 50 / protocol_not_recorded before."""
    resp = client.post("/api/v1/uploads/conformers", json=_payload(imag_freq_cm1=-40.0))
    assert resp.status_code == 422, resp.text
    assert resp.json()["code"] == "n_imag_contradicts_minimum"


def test_a_mode_below_the_assumed_tau_is_accepted_and_stores_the_assumed_basis(
    client, db_session
):
    resp = client.post(
        "/api/v1/uploads/conformers",
        json=_payload(imag_freq_cm1=-20.0, label="min-assumed-soft"),
    )
    assert resp.status_code == 201, resp.text
    assert "minimum_imaginary_modes_below_tau" in _codes(resp)
    stored = _stored_freq(db_session)
    assert stored.imaginary_mode_tau_cm1 == 30.0
    assert stored.imaginary_mode_tau_basis == "assumed_analytic_default"
    assert stored.imaginary_mode_structural_flag is False


def test_an_unknown_program_keeps_the_conservative_tau(client, db_session):
    resp = client.post(
        "/api/v1/uploads/conformers",
        json=_payload(
            imag_freq_cm1=-40.0,
            software={"name": "Molpro", "version": "2022.1"},
            label="min-molpro",
        ),
    )
    assert resp.status_code == 201, resp.text
    stored = _stored_freq(db_session)
    assert stored.imaginary_mode_tau_cm1 == 50.0
    assert stored.imaginary_mode_tau_basis == "protocol_not_recorded"


def test_a_recorded_method_beats_the_assumption(client, db_session):
    """Recorded finite-difference (tau 50) wins over the assumed analytic 30:
    -40 passes, and the recorded basis is what is stored."""
    resp = client.post(
        "/api/v1/uploads/conformers",
        json=_payload(
            imag_freq_cm1=-40.0,
            label="min-recorded-fd",
            parameters=[
                {
                    "raw_key": "freq",
                    "raw_value": "numer",
                    "canonical_key": "freq.hessian_method",
                    "canonical_value": "finite_difference_gradient",
                    "section": "freq",
                }
            ],
        ),
    )
    assert resp.status_code == 201, resp.text
    stored = _stored_freq(db_session)
    assert stored.imaginary_mode_tau_cm1 == 50.0
    assert stored.imaginary_mode_tau_basis == "finite_difference_gradient"


def _computed_species_with_soft_freq(software_name: str, label_z: float):
    from tests.api.test_api_statmech_thermo_levels import (
        _bundle_conformer,
        _computed_species_bundle_payload,
    )

    lot = {"method": "B3LYP", "basis": "6-31G(d)"}
    payload = _computed_species_bundle_payload(
        conformers=[_bundle_conformer(0, opt_lot=lot, xyz_z=label_z)],
        statmech_source_calculations=[{"calculation_key": "opt0", "role": "opt"}],
    )
    payload["conformers"][0]["additional_calculations"] = [
        {
            "key": "freq0",
            "type": "freq",
            "level_of_theory": lot,
            "software_release": {"name": software_name, "version": "16"},
            "freq_result": {"n_imag": 1, "imag_freq_cm1": -40.0},
        }
    ]
    return payload


def test_computed_species_refuses_the_minus_40_gaussian_dft_minimum(client):
    """The bundle (``CalculationInBundle``) adapter resolves the assumed tau too."""
    resp = client.post(
        "/api/v1/uploads/computed-species", json=_computed_species_with_soft_freq("Gaussian", 0.0)
    )
    assert resp.status_code == 422, resp.text[:600]
    assert resp.json()["code"] == "n_imag_contradicts_minimum"


def test_computed_species_unknown_program_keeps_the_conservative_tau(client):
    resp = client.post(
        "/api/v1/uploads/computed-species", json=_computed_species_with_soft_freq("Molpro", 0.1)
    )
    assert resp.status_code == 201, resp.text[:600]
    assert "minimum_imaginary_modes_below_tau" in {w["code"] for w in resp.json()["warnings"]}
