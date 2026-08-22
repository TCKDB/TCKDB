"""A depositor must actually *receive* the linear-count warning.

The unit tests in ``tests/services/test_frequency_geometry_linearity.py``
prove the judgement. This file proves the wiring: that the judgement
reaches the ``warnings`` array of a real 201, on the real routes, for a
real molecule — because a check that is *computed* and a warning a
depositor *sees* are different claims, and only the second is the point.

Every case is run twice, once with a genuinely bent molecule and once
with a genuinely linear one depositing the *same mode count*. A test
using only one geometry kind cannot distinguish this check from the
``3N - 6`` floor it sits on top of: the floor accepts both, and a rule
that flagged every ``3N - 5`` deposit would pass a bent-only file while
being wrong about every linear molecule in the database.

The same applies in the mirror direction, which the second half of this
file covers: a **linear** molecule depositing ``3N - 6`` modes is one
vibration short, and the floor cannot see it because ``3N - 6`` is the
floor and it warns strictly below. The deposit used there is CO2's real
spectrum with its doubly degenerate bend collapsed to a single entry --
what a de-duplicating parser actually emits — and the control is water
depositing its own correct three modes.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.frequency_geometry_linearity import (
    W_FREQ_LIST_BENT_COUNT_FOR_LINEAR_GEOMETRY,
    W_FREQ_LIST_LINEAR_COUNT_FOR_BENT_GEOMETRY,
)

_SOFTWARE = {"name": "Gaussian", "version": "16"}
_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}

#: Water: bent, 3 atoms. 3N-6 = 3 vibrations, so a four-mode list is one
#: mode too many — and is exactly the count a linear triatomic has.
_WATER_XYZ = (
    "3\nwater\n"
    "O  0.000000  0.000000  0.117300\n"
    "H  0.000000  0.757200 -0.469200\n"
    "H  0.000000 -0.757200 -0.469200"
)

#: Carbon dioxide: linear, 3 atoms. 3N-5 = 4 vibrations, so a four-mode
#: list is complete and correct. Same atom count, same mode count,
#: opposite verdict.
_CO2_XYZ = (
    "3\ncarbon dioxide\n"
    "C  0.000000  0.000000  0.000000\n"
    "O  0.000000  0.000000  1.162000\n"
    "O  0.000000  0.000000 -1.162000"
)

#: One rigid-body eigenvalue left in an otherwise complete water
#: spectrum. Four entries.
_WATER_PLUS_ONE = [12.0, 1595.0, 3657.0, 3756.0]
#: CO2's four genuine vibrations: the bend is doubly degenerate.
_CO2_SPECTRUM = [667.0, 667.0, 1333.0, 2349.0]
#: The same spectrum after a parser collapsed the degenerate bend to one
#: entry. Three modes — exactly ``3N - 6``, the count a *bent* triatomic
#: has, and one vibration short of CO2's real four.
_CO2_DEDUPLICATED = [667.0, 1333.0, 2349.0]
#: Water's complete and correct three-mode spectrum.
_WATER_SPECTRUM = [1595.0, 3657.0, 3756.0]


def _freq_calc(frequencies: list[float], *, key: str | None = None) -> dict:
    calc: dict = {
        "type": "freq",
        "software_release": _SOFTWARE,
        "level_of_theory": _LOT,
        "freq_result": {
            "n_imag": 0,
            "modes": [
                {
                    "mode_index": index + 1,
                    "frequency_cm1": value,
                    "is_imaginary": False,
                }
                for index, value in enumerate(frequencies)
            ],
        },
    }
    if key is not None:
        calc["key"] = key
    return calc


def _linearity_warnings(resp) -> list[dict]:
    return [
        w
        for w in resp.json()["warnings"]
        if w["code"] == W_FREQ_LIST_LINEAR_COUNT_FOR_BENT_GEOMETRY
    ]


def _short_list_warnings(resp) -> list[dict]:
    """The mirror direction: a linear geometry one vibration short."""
    return [
        w
        for w in resp.json()["warnings"]
        if w["code"] == W_FREQ_LIST_BENT_COUNT_FOR_LINEAR_GEOMETRY
    ]


def _completeness_warnings(resp) -> list[dict]:
    """The wire-side floor, which must stay silent on a ``3N - 6`` list."""
    return [
        w
        for w in resp.json()["warnings"]
        if w["code"] == "freq_list_incomplete_for_geometry"
    ]


# ---------------------------------------------------------------------------
# /uploads/conformers — the reconciliation path
# ---------------------------------------------------------------------------


def _conformer_payload(
    *, smiles: str, xyz_text: str, frequencies: list[float], label: str
) -> dict:
    return {
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": 1},
        "geometry": {"xyz_text": xyz_text},
        "calculation": {
            "type": "opt",
            "software_release": _SOFTWARE,
            "level_of_theory": _LOT,
        },
        "additional_calculations": [_freq_calc(frequencies)],
        "label": label,
    }


def test_bent_water_depositing_the_linear_count_is_flagged(client: TestClient):
    """The residue PR #162 left open, closed and visible on the response.

    Four modes clears the ``3N - 6`` floor of three and sits far below
    the ``3N`` ceiling of nine, so nothing said anything before this.
    """
    resp = client.post(
        "/api/v1/uploads/conformers",
        json=_conformer_payload(
            smiles="O",
            xyz_text=_WATER_XYZ,
            frequencies=_WATER_PLUS_ONE,
            label="water-linear-count",
        ),
    )
    assert resp.status_code == 201, resp.text[:800]
    warnings = _linearity_warnings(resp)
    assert len(warnings) == 1, resp.json()["warnings"]
    assert warnings[0]["field"] == "calculations[1].freq_result.modes"
    assert "3N-6 = 3" in warnings[0]["message"]


def test_linear_carbon_dioxide_depositing_the_same_count_is_not(client: TestClient):
    """The control that makes the test above mean anything.

    Identical atom count, identical mode count, and the record is
    correct — CO2 really does have four vibrations. A check that
    classified every geometry as bent, or that keyed on ``3N - 5``
    alone, would flag this one.
    """
    resp = client.post(
        "/api/v1/uploads/conformers",
        json=_conformer_payload(
            smiles="O=C=O",
            xyz_text=_CO2_XYZ,
            frequencies=_CO2_SPECTRUM,
            label="co2-linear-count",
        ),
    )
    assert resp.status_code == 201, resp.text[:800]
    assert _linearity_warnings(resp) == []


def test_water_depositing_its_own_three_modes_is_not_flagged(client: TestClient):
    """The complete record for a bent molecule stays silent."""
    resp = client.post(
        "/api/v1/uploads/conformers",
        json=_conformer_payload(
            smiles="O",
            xyz_text=_WATER_XYZ,
            frequencies=[1595.0, 3657.0, 3756.0],
            label="water-complete",
        ),
    )
    assert resp.status_code == 201, resp.text[:800]
    assert _linearity_warnings(resp) == []


def test_the_warning_never_refuses_the_deposit(client: TestClient):
    """ADR 0008: this asserts an expectation, so it may only annotate.

    The boundary between linear and bent is a tolerance, and a rule whose
    answer moves when a constant moves cannot block. The record is
    stored — the response carries the ids to prove it — and flagged.
    """
    resp = client.post(
        "/api/v1/uploads/conformers",
        json=_conformer_payload(
            smiles="O",
            xyz_text=_WATER_XYZ,
            frequencies=_WATER_PLUS_ONE,
            label="water-still-stored",
        ),
    )
    assert resp.status_code == 201, resp.text[:800]
    assert resp.json()["id"] > 0
    assert resp.json()["species_entry_id"] > 0
    assert _linearity_warnings(resp) != []


# ---------------------------------------------------------------------------
# /uploads/computed-species — the bundle route the ARC adapter uses
# ---------------------------------------------------------------------------


def _species_bundle(*, smiles: str, xyz_text: str, frequencies: list[float]) -> dict:
    return {
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": 1},
        "conformers": [
            {
                "key": "c0",
                "geometry": {"xyz_text": xyz_text},
                "primary_calculation": {
                    "key": "opt0",
                    "type": "opt",
                    "software_release": _SOFTWARE,
                    "level_of_theory": _LOT,
                    "opt_result": {"converged": True},
                },
                "additional_calculations": [_freq_calc(frequencies, key="freq0")],
            }
        ],
    }


def test_the_bundle_route_reports_it_too(client: TestClient):
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=_species_bundle(
            smiles="O", xyz_text=_WATER_XYZ, frequencies=_WATER_PLUS_ONE
        ),
    )
    assert resp.status_code == 201, resp.text[:800]
    warnings = _linearity_warnings(resp)
    assert len(warnings) == 1, resp.json()["warnings"]
    assert (
        warnings[0]["field"]
        == "conformers['c0'].calculations['freq0'].freq_result.modes"
    )


def test_the_bundle_route_stays_silent_for_a_linear_molecule(client: TestClient):
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=_species_bundle(
            smiles="O=C=O", xyz_text=_CO2_XYZ, frequencies=_CO2_SPECTRUM
        ),
    )
    assert resp.status_code == 201, resp.text[:800]
    assert _linearity_warnings(resp) == []


# ---------------------------------------------------------------------------
# /uploads/statmech — a product upload, where the calculation must name
# its own geometry because there is no conformer to fall back to
# ---------------------------------------------------------------------------


def _statmech_payload(*, smiles: str, xyz_text: str, frequencies: list[float]) -> dict:
    calc = _freq_calc(frequencies)
    calc["input_geometries"] = [{"xyz_text": xyz_text}]
    return {
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": 1},
        "scientific_origin": "computed",
        "statmech_treatment": "rrho",
        "external_symmetry": 2,
        "calculations": [{"key": "freq0", "calculation": calc}],
    }


def test_a_product_upload_reports_it_from_the_calculations_own_geometry(
    client: TestClient,
):
    resp = client.post(
        "/api/v1/uploads/statmech",
        json=_statmech_payload(
            smiles="O", xyz_text=_WATER_XYZ, frequencies=_WATER_PLUS_ONE
        ),
    )
    assert resp.status_code == 201, resp.text[:800]
    warnings = _linearity_warnings(resp)
    assert len(warnings) == 1, resp.json()["warnings"]
    assert warnings[0]["field"] == "calculations['freq0'].freq_result.modes"


def test_a_product_upload_stays_silent_for_a_linear_molecule(client: TestClient):
    resp = client.post(
        "/api/v1/uploads/statmech",
        json=_statmech_payload(
            smiles="O=C=O", xyz_text=_CO2_XYZ, frequencies=_CO2_SPECTRUM
        ),
    )
    assert resp.status_code == 201, resp.text[:800]
    assert _linearity_warnings(resp) == []


# ---------------------------------------------------------------------------
# The mirror direction: a linear geometry one vibration short
#
# Nothing reported this before. The wire floor for an N-atom geometry is
# 3N-6 and it warns strictly below, so a linear molecule depositing
# exactly 3N-6 sat on the accepted line and passed in silence — while a
# consumer recomputing a partition function from it got a number rather
# than an error.
# ---------------------------------------------------------------------------


def test_linear_co2_missing_a_degenerate_bend_is_flagged(client: TestClient):
    """The defect as it actually arrives, on a real route.

    CO2's bend is doubly degenerate and its spectrum carries 667 twice.
    A parser that de-duplicates equal frequencies emits three modes,
    which is what this deposits.
    """
    resp = client.post(
        "/api/v1/uploads/conformers",
        json=_conformer_payload(
            smiles="O=C=O",
            xyz_text=_CO2_XYZ,
            frequencies=_CO2_DEDUPLICATED,
            label="co2-bent-count",
        ),
    )
    assert resp.status_code == 201, resp.text[:800]
    warnings = _short_list_warnings(resp)
    assert len(warnings) == 1, resp.json()["warnings"]
    assert warnings[0]["field"] == "calculations[1].freq_result.modes"
    assert "3N-5 = 4" in warnings[0]["message"]
    assert "degenerate" in warnings[0]["message"]


def test_bent_water_depositing_the_same_count_is_not(client: TestClient):
    """The control that makes the test above mean anything.

    Identical atom count, identical mode count, and the record is
    correct — water really does have three vibrations. A check that
    classified every geometry as linear, or that keyed on ``3N - 6``
    alone, would flag the most common molecule in the database.
    """
    resp = client.post(
        "/api/v1/uploads/conformers",
        json=_conformer_payload(
            smiles="O",
            xyz_text=_WATER_XYZ,
            frequencies=_WATER_SPECTRUM,
            label="water-bent-count-control",
        ),
    )
    assert resp.status_code == 201, resp.text[:800]
    assert _short_list_warnings(resp) == []


def test_the_short_list_collects_no_second_warning(client: TestClient):
    """The floor and this check cannot both speak about one list.

    ``3N - 6`` is the floor's own threshold and it warns strictly below,
    so the deposit that trips this check is precisely the one the floor
    accepts. A depositor gets one explanation, not two.
    """
    resp = client.post(
        "/api/v1/uploads/conformers",
        json=_conformer_payload(
            smiles="O=C=O",
            xyz_text=_CO2_XYZ,
            frequencies=_CO2_DEDUPLICATED,
            label="co2-one-warning-only",
        ),
    )
    assert resp.status_code == 201, resp.text[:800]
    assert len(_short_list_warnings(resp)) == 1
    assert _completeness_warnings(resp) == []
    assert _linearity_warnings(resp) == []


def test_the_short_list_warning_never_refuses_the_deposit(client: TestClient):
    """ADR 0008 again: an expectation may annotate, never block.

    A partial or frozen-atom Hessian on a linear molecule produces this
    same count honestly, so there is a correct deposit this would refuse
    if it blocked.
    """
    resp = client.post(
        "/api/v1/uploads/conformers",
        json=_conformer_payload(
            smiles="O=C=O",
            xyz_text=_CO2_XYZ,
            frequencies=_CO2_DEDUPLICATED,
            label="co2-still-stored",
        ),
    )
    assert resp.status_code == 201, resp.text[:800]
    assert resp.json()["id"] > 0
    assert resp.json()["species_entry_id"] > 0
    assert _short_list_warnings(resp) != []


def test_the_bundle_route_reports_the_short_list_too(client: TestClient):
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=_species_bundle(
            smiles="O=C=O", xyz_text=_CO2_XYZ, frequencies=_CO2_DEDUPLICATED
        ),
    )
    assert resp.status_code == 201, resp.text[:800]
    warnings = _short_list_warnings(resp)
    assert len(warnings) == 1, resp.json()["warnings"]
    assert (
        warnings[0]["field"]
        == "conformers['c0'].calculations['freq0'].freq_result.modes"
    )


def test_the_bundle_route_stays_silent_for_a_complete_bent_molecule(
    client: TestClient,
):
    resp = client.post(
        "/api/v1/uploads/computed-species",
        json=_species_bundle(
            smiles="O", xyz_text=_WATER_XYZ, frequencies=_WATER_SPECTRUM
        ),
    )
    assert resp.status_code == 201, resp.text[:800]
    assert _short_list_warnings(resp) == []


def test_a_product_upload_reports_the_short_list(client: TestClient):
    resp = client.post(
        "/api/v1/uploads/statmech",
        json=_statmech_payload(
            smiles="O=C=O", xyz_text=_CO2_XYZ, frequencies=_CO2_DEDUPLICATED
        ),
    )
    assert resp.status_code == 201, resp.text[:800]
    warnings = _short_list_warnings(resp)
    assert len(warnings) == 1, resp.json()["warnings"]
    assert warnings[0]["field"] == "calculations['freq0'].freq_result.modes"


def test_the_completeness_floor_can_still_fire(client: TestClient):
    """Guard the guard for ``test_the_short_list_collects_no_second_warning``.

    That test asserts the floor stays *silent* on a ``3N - 6`` list. An
    assertion of absence is worth nothing unless the thing asserted
    absent can be present, and ``_completeness_warnings`` filters on a
    code that a typo would make permanently unmatchable. Two modes on
    CO2 is genuinely below the floor of three, so the floor speaks here
    — and the short-list check does not, because it speaks only at
    exactly three.
    """
    resp = client.post(
        "/api/v1/uploads/conformers",
        json=_conformer_payload(
            smiles="O=C=O",
            xyz_text=_CO2_XYZ,
            frequencies=[1333.0, 2349.0],
            label="co2-genuinely-below-the-floor",
        ),
    )
    assert resp.status_code == 201, resp.text[:800]
    assert _completeness_warnings(resp) != [], resp.json()["warnings"]
    assert _short_list_warnings(resp) == []
