"""``t0_k``: the Arrhenius reference temperature, on both kinetics routes (#620).

``k = A * (T / T0)**n * exp(-Ea / RT)``. Before this field existed a producer
that fitted with T0 = 298 K had to fold ``A / T0**n`` into ``a`` and the T0 it
fitted with was lost. The field is on the reaction bundle
(``BundleKineticsIn``) and on the standalone route (``KineticsUploadRequest``)
with one definition of its default, bound and meaning.

Every test states what it would catch if the behaviour regressed; the
refusal tests are paired with an accepted neighbour, because "a 4xx arrived"
is also what a model that refuses every payload produces.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError
from tckdb_schemas.workflows.computed_reaction_upload import BundleKineticsIn

from app.schemas.workflows.kinetics_upload import KineticsUploadRequest

_REACTION = {
    "reversible": False,
    "reactants": [
        {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
        {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
    ],
    "products": [{"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}}],
}


def _standalone(**overrides) -> dict:
    body = {
        "reaction": _REACTION,
        "scientific_origin": "computed",
        "model_kind": "modified_arrhenius",
        "a": 1.0e13,
        "a_units": "cm3_mol_s",
        "n": 2.0,
    }
    body.update(overrides)
    return body


def _bundle(**overrides) -> dict:
    body = {
        "reactant_keys": ["h", "h"],
        "product_keys": ["h2"],
        "scientific_origin": "computed",
        "model_kind": "modified_arrhenius",
        "a": 1.0e13,
        "a_units": "cm3_mol_s",
        "n": 2.0,
    }
    body.update(overrides)
    return body


_ROUTES = pytest.mark.parametrize(
    ("model", "build"),
    [(KineticsUploadRequest, _standalone), (BundleKineticsIn, _bundle)],
    ids=["standalone", "bundle"],
)


@_ROUTES
def test_t0_defaults_to_one_kelvin(model, build):
    """Omitting it is the plain ``A * T**n`` form, which is what every record
    sent before the field existed meant. A default of ``None`` or 298.15 would
    silently change those records' rates."""
    assert model.model_validate(build()).t0_k == 1.0


@_ROUTES
def test_t0_round_trips_through_dump_and_validate(model, build):
    """The value survives the model, in both directions, unchanged."""
    parsed = model.model_validate(build(t0_k=298.15))
    assert parsed.t0_k == 298.15
    assert model.model_validate(parsed.model_dump(mode="json")).t0_k == 298.15


@_ROUTES
@pytest.mark.parametrize("bad", [0, 0.0, -1.0, -298.0, math.inf, math.nan, "hot"])
def test_t0_must_be_a_finite_positive_temperature(model, build, bad):
    """``T/T0`` with T0 <= 0 is meaningless, and inf/nan poison every
    evaluation downstream. The accepted neighbour is the same payload with a
    valid value, so the refusal is about ``t0_k`` and not the payload."""
    model.model_validate(build(t0_k=1.0))
    with pytest.raises(ValidationError) as excinfo:
        model.model_validate(build(t0_k=bad))
    assert "t0_k" in str(excinfo.value)


@_ROUTES
def test_unknown_key_is_still_refused_so_the_field_is_not_a_typo_sink(model, build):
    """``extra='forbid'`` must survive the change: ``t0`` (no ``_k``) is not
    silently accepted as though it were the field."""
    with pytest.raises(ValidationError):
        model.model_validate(build(t0=298.15))


def test_both_routes_publish_the_same_t0_description():
    """One wording of the convention, or the two routes document two rates."""
    standalone = KineticsUploadRequest.model_json_schema()["properties"]["t0_k"]
    bundle = BundleKineticsIn.model_json_schema()["properties"]["t0_k"]
    assert standalone == bundle
    assert "(T / T0)**n" in standalone["description"]


@pytest.mark.parametrize(
    ("kind", "extra"),
    [
        (
            "plog",
            {"plog_entries": [{"entry_index": 1, "pressure_bar": 1.0, "a": 1.0, "a_units": "cm3_mol_s", "n": 0.0, "ea_kj_mol": 0.0}]},
        ),
        (
            "chebyshev",
            {
                "chebyshev": {
                    "n_temperature": 1,
                    "n_pressure": 1,
                    "tmin_k": 300.0,
                    "tmax_k": 2000.0,
                    "pmin_bar": 0.1,
                    "pmax_bar": 100.0,
                    "coefficients": [[1.0]],
                }
            },
        ),
        (
            "multi_arrhenius",
            {
                "a": None,
                "arrhenius_entries": [
                    {"entry_index": 1, "a": 1.0, "a_units": "cm3_mol_s", "n": 0.0},
                    {"entry_index": 2, "a": 2.0, "a_units": "cm3_mol_s", "n": 0.0},
                ],
            },
        ),
    ],
)
def test_standalone_refuses_t0_on_forms_that_have_no_scalar_arrhenius(kind, extra):
    """PLOG entries, sum-of-Arrhenius terms and Chebyshev surfaces are always
    at 1 K, so a T0 there would be a number with nothing to apply to. The
    same payload at the default T0 is accepted."""
    base = {**_standalone(model_kind=kind, a=None, a_units=None, n=None), **extra}
    KineticsUploadRequest.model_validate({**base, "t0_k": 1.0})
    with pytest.raises(ValidationError) as excinfo:
        KineticsUploadRequest.model_validate({**base, "t0_k": 298.0})
    assert "t0_k applies to the scalar Arrhenius parameters" in str(excinfo.value)


def test_standalone_accepts_t0_on_a_falloff_rate_high_pressure_limit():
    """A falloff rate's scalar row is its k-infinity, which T0 applies to."""
    body = _standalone(
        model_kind="troe",
        is_third_body=False,
        falloff={
            "low_a": 1.0e18,
            "low_a_units": "cm6_mol2_s",
            "troe_alpha": 0.5,
            "troe_t3": 100.0,
            "troe_t1": 1000.0,
        },
        t0_k=298.0,
    )
    assert KineticsUploadRequest.model_validate(body).t0_k == 298.0
