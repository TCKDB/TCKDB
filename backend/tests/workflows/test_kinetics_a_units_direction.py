"""The Arrhenius A-units order follows the side the coefficient is a rate of (#695).

A forward coefficient is a rate of the reactants, a reverse one of the products.
H2 -> 2H deposited as the reverse rate (2H -> H2) is bimolecular, so its A is in
cm3/mol/s; the same record read forward is unimolecular.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db.models.common import ArrheniusAUnits, KineticsDirection
from app.schemas.workflows.kinetics_upload import KineticsUploadRequest
from app.workflows.kinetics import persist_kinetics_upload

_H2 = {"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}}
_H = {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}}


def _request(*, direction, a_units, reactants=(_H2,), products=(_H, _H), **extra) -> dict:
    return {
        "reaction": {"reversible": True, "reactants": list(reactants), "products": list(products)},
        "scientific_origin": "experimental",
        "model_kind": "modified_arrhenius",
        "direction": direction,
        "a": 1.0e12,
        "a_units": a_units,
        "n": 0.0,
        "software_release": {"name": "gaussian", "version": "09", "revision": "D.01"},
        "workflow_tool_release": {"name": "ARC", "version": "1.0.0"},
        "literature": {"doi": "10.1000/example.doi", "title": "t"},
        **extra,
    }


def _patch_doi(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.literature_resolution.fetch_doi_metadata",
        lambda doi: {"title": "t", "issued": 2024, "URL": f"https://doi.org/{doi}"},
    )


# H2 -> 2H: one reactant, two products.
@pytest.mark.parametrize(
    ("direction", "a_units"),
    [
        ("forward", "per_s"),  # unimolecular
        ("reverse", "cm3_mol_s"),  # 2H -> H2, bimolecular
    ],
)
def test_units_matching_the_coefficient_side_are_accepted(direction, a_units) -> None:
    request = KineticsUploadRequest.model_validate(_request(direction=direction, a_units=a_units))
    assert request.a_units == ArrheniusAUnits(a_units)


@pytest.mark.parametrize(
    ("direction", "a_units", "word"),
    [
        ("forward", "cm3_mol_s", "unimolecular"),
        ("reverse", "per_s", "bimolecular"),
    ],
)
def test_units_of_the_wrong_side_are_refused(direction, a_units, word) -> None:
    with pytest.raises(ValidationError, match=word):
        KineticsUploadRequest.model_validate(_request(direction=direction, a_units=a_units))


@pytest.mark.parametrize("a_units", ["per_s", "cm3_mol_s"])
def test_unstated_direction_accepts_units_of_either_side(a_units) -> None:
    """H2 -> 2H states no direction: first order (reactant side) or second order (product side) is possible."""
    request = KineticsUploadRequest.model_validate(_request(direction=None, a_units=a_units))
    assert request.direction is None
    assert request.a_units == ArrheniusAUnits(a_units)


def test_unstated_direction_refuses_units_matching_neither_side_and_says_how_to_fix_it() -> None:
    with pytest.raises(ValidationError) as raised:
        KineticsUploadRequest.model_validate(_request(direction=None, a_units="cm6_mol2_s"))
    message = str(raised.value)
    assert "matches neither the reactant side (order 1) nor the product side (order 2)" in message
    assert "State direction" in message
    error = raised.value.errors()[0]["ctx"]["error"]
    assert error.code == "arrhenius_a_units_molecularity_mismatch"
    assert error.context["reactant_molecularity"] == 1 and error.context["product_molecularity"] == 2


def test_unstated_direction_with_equal_sides_is_checked_as_before() -> None:
    same = {"reactants": (_H2,), "products": (_H2,)}
    KineticsUploadRequest.model_validate(_request(direction=None, a_units="per_s", **same))
    with pytest.raises(ValidationError, match="unimolecular"):
        KineticsUploadRequest.model_validate(_request(direction=None, a_units="cm3_mol_s", **same))


def test_unstated_direction_and_third_body_shifts_both_sides() -> None:
    """Simple +M: orders 2 (reactant side) and 3 (product side); first order is neither."""
    for units in ("cm3_mol_s", "cm6_mol2_s"):
        KineticsUploadRequest.model_validate(_request(direction=None, a_units=units, is_third_body=True))
    with pytest.raises(ValidationError, match="matches neither"):
        KineticsUploadRequest.model_validate(_request(direction=None, a_units="per_s", is_third_body=True))


def test_net_rate_has_no_order_to_check() -> None:
    """A net rate folds both directions: no side's order applies, so even units matching neither side pass."""
    for units in ("per_s", "cm3_mol_s", "cm6_mol2_s"):
        KineticsUploadRequest.model_validate(_request(direction="net", a_units=units))


def test_reverse_of_a_bimolecular_forward_reaction_is_unimolecular() -> None:
    """Reverse of A + B -> C: the product side has one species, so the order is 1."""
    kwargs = {"reactants": (_H, _H), "products": (_H2,)}
    KineticsUploadRequest.model_validate(_request(direction="reverse", a_units="per_s", **kwargs))
    with pytest.raises(ValidationError, match="unimolecular"):
        KineticsUploadRequest.model_validate(_request(direction="reverse", a_units="cm3_mol_s", **kwargs))


def test_sibling_entries_follow_the_same_side() -> None:
    entries = [
        {"entry_index": 1, "a": 1.0e12, "a_units": "cm3_mol_s", "n": 0.0},
        {"entry_index": 2, "a": 2.0e12, "a_units": "cm3_mol_s", "n": 0.0},
    ]
    payload = _request(direction="reverse", a_units=None, arrhenius_entries=entries)
    payload.pop("a")
    payload["model_kind"] = "multi_arrhenius"
    KineticsUploadRequest.model_validate(payload)
    payload["direction"] = "forward"
    with pytest.raises(ValidationError, match=r"arrhenius_entries\[1\].a_units"):
        KineticsUploadRequest.model_validate(payload)


def _plog(units: str, direction: str) -> dict:
    payload = _request(direction=direction, a_units=None)
    payload.pop("a")
    payload["model_kind"] = "plog"
    payload["plog_entries"] = [
        {"entry_index": 1, "pressure_bar": 0.1, "a": 1.0e12, "a_units": units, "n": 0.0},
        {"entry_index": 2, "pressure_bar": 1.0, "a": 2.0e12, "a_units": units, "n": 0.0},
    ]
    return payload


def test_plog_entries_follow_the_side_the_direction_names() -> None:
    KineticsUploadRequest.model_validate(_plog("cm3_mol_s", "reverse"))
    KineticsUploadRequest.model_validate(_plog("per_s", "forward"))
    with pytest.raises(ValidationError, match=r"plog_entries\[1\].a_units"):
        KineticsUploadRequest.model_validate(_plog("per_s", "reverse"))
    with pytest.raises(ValidationError, match=r"plog_entries\[1\].a_units"):
        KineticsUploadRequest.model_validate(_plog("cm3_mol_s", "forward"))
    # No direction: either side's order, and a PLOG entry of neither is refused naming its field.
    KineticsUploadRequest.model_validate(_plog("cm3_mol_s", None))
    with pytest.raises(ValidationError, match=r"plog_entries\[1\].a_units: .*matches neither"):
        KineticsUploadRequest.model_validate(_plog("cm6_mol2_s", None))


def test_falloff_low_pressure_units_are_one_order_above_the_side() -> None:
    falloff = {
        "low_a": 1.0e30, "low_a_units": "cm6_mol2_s", "low_n": -3.0, "low_ea_kj_mol": 0.0,
        "troe_alpha": 0.5, "troe_t3": 100.0, "troe_t1": 1000.0, "troe_t2": 5000.0,
    }
    # reverse of H2 -> 2H: bimolecular k-inf, so k0 is order 3.
    KineticsUploadRequest.model_validate(
        _request(direction="reverse", a_units="cm3_mol_s", model_kind="troe", falloff=falloff)
    )
    with pytest.raises(ValidationError, match="falloff.low_a_units"):
        KineticsUploadRequest.model_validate(
            _request(direction="forward", a_units="per_s", model_kind="troe", falloff=falloff)
        )


@pytest.mark.parametrize(
    ("direction", "a_units"),
    [("forward", "per_s"), ("reverse", "cm3_mol_s")],
)
def test_stored_row_keeps_direction_and_units(db_conn, monkeypatch, direction, a_units) -> None:
    _patch_doi(monkeypatch)
    request = KineticsUploadRequest.model_validate(_request(direction=direction, a_units=a_units))
    with Session(db_conn) as session, session.begin():
        kinetics = persist_kinetics_upload(session, request)
        session.flush()
        assert kinetics.direction == KineticsDirection(direction)
        assert kinetics.a_units == ArrheniusAUnits(a_units)


def test_stored_row_with_unstated_direction(db_conn, monkeypatch) -> None:
    _patch_doi(monkeypatch)
    request = KineticsUploadRequest.model_validate(_request(direction=None, a_units="per_s"))
    with Session(db_conn) as session, session.begin():
        kinetics = persist_kinetics_upload(session, request)
        session.flush()
        assert kinetics.direction is None
        assert kinetics.a_units == ArrheniusAUnits.per_s
