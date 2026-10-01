"""Standalone /statmech and /thermo refuse duplicate sps that declare output-only geometry (#615).

The no-optimisation duplicate rule of #610 keyed each linked ``sp`` by its
input geometry. The pressure-dependent network route links a geometry as a
calculation's final output only, so its sps were never compared. The shared
rule now keys an sp by its input geometry, else its output geometry; the
standalone uploads share that code, so an sp that declares only an output
geometry on the same geometry as an input-linked sp is now a duplicate here
too, as it already was for two input-linked sps.
"""

from __future__ import annotations

from sqlalchemy import delete, select

from app.db.models.calculation import (
    CalculationInputGeometry,
    CalculationOutputGeometry,
)
from app.db.models.common import CalculationGeometryRole
from tests.api.test_api_statmech_thermo_levels import (
    _LOT_A,
    _METHYL_SPECIES,
    _ROLE_DUPLICATE_STATMECH,
    _ROLE_DUPLICATE_THERMO,
    _assert_code,
    _deposit_conformer,
    _methyl_xyz,
    _opt_calc_at,
    _sp_calc_at,
    _standalone_statmech_payload,
    _thermo_payload,
)


def _input_sp_and_output_only_sp(client, db_session) -> list[int]:
    """Two same-level sps on one geometry; the second declares it as output only."""
    conf = _deposit_conformer(
        client,
        label="out-only-sp",
        species=dict(_METHYL_SPECIES),
        xyz_text=_methyl_xyz(0.0),
        primary={**_opt_calc_at(_LOT_A), "key": "opt_a"},
        additional=[
            {**_sp_calc_at(_LOT_A), "key": "sp_x"},
            {**_sp_calc_at(_LOT_A), "key": "sp_y"},
        ],
    )
    sp_x, sp_y = (c["calculation_id"] for c in conf["additional_calculations"])
    geometry_id = db_session.scalars(
        select(CalculationInputGeometry.geometry_id).where(
            CalculationInputGeometry.calculation_id == sp_y
        )
    ).one()
    db_session.execute(
        delete(CalculationInputGeometry).where(
            CalculationInputGeometry.calculation_id == sp_y
        )
    )
    db_session.add(
        CalculationOutputGeometry(
            calculation_id=sp_y,
            geometry_id=geometry_id,
            output_order=1,
            role=CalculationGeometryRole.final,
        )
    )
    db_session.flush()
    return [sp_x, sp_y]


def test_statmech_input_sp_and_output_only_sp_on_one_geometry_is_refused(client, db_session):
    sp_ids = _input_sp_and_output_only_sp(client, db_session)
    payload = _standalone_statmech_payload(
        species_entry=dict(_METHYL_SPECIES),
        source_calculations=[
            {"existing_calculation_id": sid, "role": "sp"} for sid in sp_ids
        ],
    )
    resp = client.post("/api/v1/uploads/statmech", json=payload)
    _assert_code(resp, _ROLE_DUPLICATE_STATMECH)


def test_thermo_input_sp_and_output_only_sp_on_one_geometry_is_refused(client, db_session):
    sp_ids = _input_sp_and_output_only_sp(client, db_session)
    payload = _thermo_payload("[CH3]")
    payload["species_entry"] = dict(_METHYL_SPECIES)
    payload["source_calculations"] = [
        {"existing_calculation_id": sid, "role": "sp"} for sid in sp_ids
    ]
    resp = client.post("/api/v1/uploads/thermo", json=payload)
    _assert_code(resp, _ROLE_DUPLICATE_THERMO)
