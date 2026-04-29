"""Schema-layer tests for the computed-species bundle upload payload.

These tests validate every model_validator in
``app/schemas/workflows/computed_species_upload.py`` without touching
the DB. Workflow- and API-layer tests live separately.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.workflows.computed_species_upload import (
    CalculationInBundle,
    ComputedSpeciesUploadRequest,
    ConformerInBundle,
    ThermoInBundle,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_MINIMAL_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}
_MINIMAL_SOFTWARE = {"name": "Gaussian", "version": "16"}
_MINIMAL_GEOMETRY = {"xyz_text": "1\nH\nH 0.0 0.0 0.0"}
_MINIMAL_SPECIES = {"smiles": "[H]", "charge": 0, "multiplicity": 2}


def _calc(key: str = "opt0", *, calc_type: str = "opt", **overrides) -> dict:
    base: dict = {
        "key": key,
        "type": calc_type,
        "level_of_theory": dict(_MINIMAL_LOT),
        "software_release": dict(_MINIMAL_SOFTWARE),
    }
    if calc_type == "opt":
        base["opt_result"] = {"converged": True}
    elif calc_type == "freq":
        base["freq_result"] = {"n_imag": 0}
    elif calc_type == "sp":
        base["sp_result"] = {"electronic_energy_hartree": -76.4}
    base.update(overrides)
    return base


def _conformer(key: str = "c0", *, primary_key: str = "opt0", **overrides) -> dict:
    base: dict = {
        "key": key,
        "geometry": dict(_MINIMAL_GEOMETRY),
        "primary_calculation": _calc(primary_key, calc_type="opt"),
    }
    base.update(overrides)
    return base


def _bundle(**overrides) -> dict:
    base: dict = {
        "species_entry": dict(_MINIMAL_SPECIES),
        "conformers": [_conformer()],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_minimal_bundle_parses():
    req = ComputedSpeciesUploadRequest(**_bundle())
    assert req.conformers[0].primary_calculation.type.value == "opt"
    assert req.thermo is None


# ---------------------------------------------------------------------------
# Local-key namespace and uniqueness
# ---------------------------------------------------------------------------


def test_duplicate_conformer_keys_rejected():
    payload = _bundle(
        conformers=[
            _conformer(key="c0", primary_key="opt0"),
            _conformer(key="c0", primary_key="opt1"),
        ]
    )
    with pytest.raises(ValidationError) as exc:
        ComputedSpeciesUploadRequest(**payload)
    assert "unique keys" in str(exc.value)


def test_duplicate_calculation_keys_global_namespace():
    payload = _bundle(
        conformers=[
            _conformer(key="c0", primary_key="opt0"),
            _conformer(key="c1", primary_key="opt0"),
        ]
    )
    with pytest.raises(ValidationError) as exc:
        ComputedSpeciesUploadRequest(**payload)
    assert "unique" in str(exc.value)


def test_dependency_key_must_resolve():
    conf = _conformer(key="c0", primary_key="opt0")
    conf["additional_calculations"] = [
        _calc(
            "freq0",
            calc_type="freq",
            depends_on=[{"parent_calculation_key": "missing", "role": "freq_on"}],
        )
    ]
    payload = _bundle(conformers=[conf])
    with pytest.raises(ValidationError) as exc:
        ComputedSpeciesUploadRequest(**payload)
    assert "depends_on" in str(exc.value) or "undefined" in str(exc.value)


def test_thermo_source_key_must_resolve():
    payload = _bundle(
        thermo={
            "h298_kj_mol": -100.0,
            "source_calculations": [
                {"calculation_key": "ghost", "role": "opt"},
            ],
        },
    )
    with pytest.raises(ValidationError) as exc:
        ComputedSpeciesUploadRequest(**payload)
    assert "ghost" in str(exc.value) or "undefined" in str(exc.value)


# ---------------------------------------------------------------------------
# Per-calc validators
# ---------------------------------------------------------------------------


def test_primary_calculation_must_be_opt():
    conf = _conformer(key="c0", primary_key="bad")
    conf["primary_calculation"] = _calc("bad", calc_type="freq")
    payload = _bundle(conformers=[conf])
    with pytest.raises(ValidationError) as exc:
        ComputedSpeciesUploadRequest(**payload)
    assert "must be 'opt'" in str(exc.value)


def test_result_block_must_match_type():
    bad_calc = _calc("freq0", calc_type="freq")
    bad_calc["opt_result"] = {"converged": True}
    bad_calc.pop("freq_result", None)
    conf = _conformer()
    conf["additional_calculations"] = [bad_calc]
    payload = _bundle(conformers=[conf])
    with pytest.raises(ValidationError) as exc:
        ComputedSpeciesUploadRequest(**payload)
    assert "not allowed for calculation type" in str(exc.value)


# ---------------------------------------------------------------------------
# DB-FK rejection
# ---------------------------------------------------------------------------


def test_existing_calculation_id_inline_rejected_via_extra_forbid():
    """Top-level fields use ``extra='forbid'`` so a stray
    ``existing_calculation_id`` on a calc payload is rejected at parse time.
    """
    bad = _calc("opt0", calc_type="opt")
    bad["existing_calculation_id"] = 7
    conf = _conformer(primary_key="opt0")
    conf["primary_calculation"] = bad
    payload = _bundle(conformers=[conf])
    with pytest.raises(ValidationError) as exc:
        ComputedSpeciesUploadRequest(**payload)
    msg = str(exc.value).lower()
    assert "extra" in msg or "existing_calculation_id" in msg


def test_existing_calculation_id_inside_parameters_json_rejected():
    """Recursive walk: ``existing_calculation_id`` nested under an opaque
    ``parameters_json`` is also caught (DR-0029 Open Question #5).
    """
    bad = _calc(
        "opt0",
        calc_type="opt",
        parameters_json={"existing_calculation_id": 99},
    )
    conf = _conformer(primary_key="opt0")
    conf["primary_calculation"] = bad
    payload = _bundle(conformers=[conf])
    with pytest.raises(ValidationError) as exc:
        ComputedSpeciesUploadRequest(**payload)
    assert "existing_calculation_id" in str(exc.value)


def test_source_calculation_id_inside_parameters_json_rejected():
    bad = _calc(
        "opt0",
        calc_type="opt",
        parameters_json={"nested": {"source_calculation_id": 1}},
    )
    conf = _conformer(primary_key="opt0")
    conf["primary_calculation"] = bad
    payload = _bundle(conformers=[conf])
    with pytest.raises(ValidationError) as exc:
        ComputedSpeciesUploadRequest(**payload)
    assert "source_calculation_id" in str(exc.value)


# ---------------------------------------------------------------------------
# Bundle structure
# ---------------------------------------------------------------------------


def test_empty_conformers_rejected():
    with pytest.raises(ValidationError):
        ComputedSpeciesUploadRequest(**_bundle(conformers=[]))


def test_thermo_unique_source_calculation_pairs():
    thermo = {
        "h298_kj_mol": -100.0,
        "source_calculations": [
            {"calculation_key": "opt0", "role": "opt"},
            {"calculation_key": "opt0", "role": "opt"},
        ],
    }
    with pytest.raises(ValidationError) as exc:
        ThermoInBundle(**thermo)
    assert "unique" in str(exc.value)


def test_thermo_requires_scientific_content():
    with pytest.raises(ValidationError) as exc:
        ThermoInBundle()
    assert "at least one" in str(exc.value)


def test_thermo_temperature_range():
    with pytest.raises(ValidationError):
        ThermoInBundle(h298_kj_mol=1.0, tmin_k=500.0, tmax_k=300.0)
