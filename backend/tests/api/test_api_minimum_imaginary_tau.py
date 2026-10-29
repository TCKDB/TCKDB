"""A declared minimum is judged by magnitude against the protocol's tolerance.

ADR 0012's note of 2026-10-29. Before it, a species entry declared a minimum
was refused for *any* imaginary mode, so a correct, published calculation with
a -15 cm-1 integration-grid artefact could not be deposited, while a
transition state's extra modes were already judged against tau. These tests
hold the one rule over HTTP, and the persisted half of it:

* every imaginary mode below tau: accepted, with
  ``minimum_imaginary_modes_below_tau``, and the tau that decided it stored on
  ``calc_freq_result`` together with the structural flag as ``false``;
* any mode at or above tau: refused, as before;
* tau is resolved from the calculation's own recorded parameters, so the
  boundary moves with the protocol;
* a van der Waals complex keeps its own warning path.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db.models.calculation import CalculationFreqResult

_ANALYTIC_TIGHT = [
    {
        "raw_key": "anfreq",
        "raw_value": "true",
        "canonical_key": "freq.hessian_method",
        "canonical_value": "analytic",
        "section": "freq",
    },
    {
        "raw_key": "grid",
        "raw_value": "ultrafine",
        "canonical_key": "grid.quality",
        "canonical_value": "ultrafine",
        "section": "integral",
    },
    {
        "raw_key": "tight",
        "raw_value": "true",
        "canonical_key": "opt.convergence",
        "canonical_value": "tight",
        "section": "opt",
    },
]


def _payload(
    *,
    imag_freq_cm1: float,
    kind: str = "minimum",
    parameters: list[dict] | None = None,
    software: dict | None = None,
    label: str = "min-a",
) -> dict:
    calc: dict = {
        "type": "freq",
        "software_release": software or {"name": "Gaussian", "version": "16"},
        "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        "freq_result": {"n_imag": 1, "imag_freq_cm1": imag_freq_cm1},
    }
    if parameters is not None:
        calc["parameters"] = parameters
    species_entry = {"smiles": "[H]", "charge": 0, "multiplicity": 2}
    if kind != "minimum":
        species_entry["species_entry_kind"] = kind
    return {
        "species_entry": species_entry,
        "geometry": {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"},
        "calculation": {
            "type": "sp",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        },
        "additional_calculations": [calc],
        "label": label,
        "note": "test upload",
    }


def _stored_freq(db_session) -> CalculationFreqResult:
    return db_session.scalars(
        select(CalculationFreqResult).order_by(CalculationFreqResult.calculation_id.desc())
    ).first()


def _codes(resp) -> set[str]:
    return {w["code"] for w in resp.json()["warnings"]}


def test_a_soft_mode_on_a_minimum_is_accepted_warned_and_its_tau_stored(client, db_session):
    """The reproduction: -15 cm-1, no recorded protocol. Refused on main."""
    resp = client.post("/api/v1/uploads/conformers", json=_payload(imag_freq_cm1=-15.0))
    assert resp.status_code == 201, resp.text
    assert "minimum_imaginary_modes_below_tau" in _codes(resp)
    assert "n_imag_contradicts_minimum" not in _codes(resp)

    stored = _stored_freq(db_session)
    assert stored.n_imag == 1
    assert stored.imag_freq_cm1 == -15.0
    # Judged and not flagged -- false, not NULL.
    assert stored.imaginary_mode_structural_flag is False
    assert stored.imaginary_mode_tau_cm1 == 50.0
    assert stored.imaginary_mode_tau_basis == "protocol_not_recorded"


def test_a_stiff_mode_on_a_minimum_is_still_refused(client):
    resp = client.post("/api/v1/uploads/conformers", json=_payload(imag_freq_cm1=-80.0))
    assert resp.status_code == 422
    assert resp.json()["code"] == "n_imag_contradicts_minimum"


def test_the_boundary_moves_with_the_recorded_protocol(client):
    """-20 cm-1: noise with no protocol (tau 50), real curvature under an
    analytic Hessian on a tight grid (tau 15)."""
    unrecorded = client.post("/api/v1/uploads/conformers", json=_payload(imag_freq_cm1=-20.0))
    assert unrecorded.status_code == 201, unrecorded.text
    assert "minimum_imaginary_modes_below_tau" in _codes(unrecorded)

    tight = client.post(
        "/api/v1/uploads/conformers",
        json=_payload(imag_freq_cm1=-20.0, parameters=_ANALYTIC_TIGHT, label="min-tight"),
    )
    assert tight.status_code == 422
    assert tight.json()["code"] == "n_imag_contradicts_minimum"


def test_a_tight_protocol_still_accepts_a_mode_below_its_own_tau(client, db_session):
    resp = client.post(
        "/api/v1/uploads/conformers",
        json=_payload(imag_freq_cm1=-10.0, parameters=_ANALYTIC_TIGHT, label="min-tight-soft"),
    )
    assert resp.status_code == 201, resp.text
    assert "minimum_imaginary_modes_below_tau" in _codes(resp)
    stored = _stored_freq(db_session)
    assert stored.imaginary_mode_tau_cm1 == 15.0
    assert stored.imaginary_mode_tau_basis == "analytic_tight"
    assert stored.imaginary_mode_structural_flag is False


def test_the_stored_tau_is_the_one_that_decided_acceptance(client, db_session):
    """-40 cm-1 is below the 50 cm-1 the deposit was judged at.

    The upload path may assume the producing program's default Hessian method
    when none is recorded, and for Gaussian DFT that assumption carries a
    smaller tau (30). Storing that would leave a record that contradicts its
    own acceptance, so the stored tau is the deciding one.
    """
    resp = client.post("/api/v1/uploads/conformers", json=_payload(imag_freq_cm1=-40.0))
    assert resp.status_code == 201, resp.text
    stored = _stored_freq(db_session)
    assert stored.imaginary_mode_tau_cm1 == 50.0
    assert stored.imaginary_mode_tau_basis == "protocol_not_recorded"
    assert abs(stored.imag_freq_cm1) < stored.imaginary_mode_tau_cm1


def test_a_vdw_complex_keeps_its_own_warning_and_stores_no_flag(client, db_session):
    resp = client.post(
        "/api/v1/uploads/conformers",
        json=_payload(imag_freq_cm1=-15.0, kind="vdw_complex", label="vdw-a"),
    )
    assert resp.status_code == 201, resp.text
    codes = _codes(resp)
    assert "n_imag_contradicts_minimum" in codes
    assert "minimum_imaginary_modes_below_tau" not in codes
    assert _stored_freq(db_session).imaginary_mode_structural_flag is None


def test_a_minimum_without_imaginary_modes_stores_no_flag(client, db_session):
    payload = _payload(imag_freq_cm1=0.0, label="min-clean")
    payload["additional_calculations"][0]["freq_result"] = {"n_imag": 0}
    resp = client.post("/api/v1/uploads/conformers", json=payload)
    assert resp.status_code == 201, resp.text
    assert "minimum_imaginary_modes_below_tau" not in _codes(resp)
    assert _stored_freq(db_session).imaginary_mode_structural_flag is None
