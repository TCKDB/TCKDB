"""Workflow-level scientific-invariant regressions.

These tests exercise the most load-bearing scientific invariants through
real ingestion seams rather than helper functions, so a regression that
slips past a unit test still fails here.

Scope is deliberately small: one thermo workflow check, and one shared
calculation-resolution check. Broader workflow coverage lives in the
per-workflow test files.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.calculation import Calculation, CalculationSPResult
from app.db.models.common import CalculationType
from app.db.models.level_of_theory import LevelOfTheory
from app.db.models.thermo import Thermo, ThermoNASA, ThermoPoint
from app.schemas.workflows.thermo_upload import ThermoUploadRequest
from app.services.calculation_resolution import (
    _level_of_theory_hash,
    resolve_and_persist_calculation_with_results,
)
from app.services.species_resolution import resolve_species_entry
from app.workflows.thermo import persist_thermo_upload


# ---------------------------------------------------------------------------
# Workflow regression 1: thermo upload preserves invariant-relevant fields
# ---------------------------------------------------------------------------


_THERMO_SPECIES = {"smiles": "CCCO", "charge": 0, "multiplicity": 1}

_WATER_NASA = {
    "t_low": 200.0,
    "t_mid": 1000.0,
    "t_high": 3500.0,
    "a1": 4.19864056, "a2": -2.0364341e-3, "a3": 6.52040211e-6,
    "a4": -5.48797062e-9, "a5": 1.77197817e-12,
    "a6": -3.02937267e4, "a7": -0.849032208,
    "b1": 3.03399249, "b2": 2.17691804e-3, "b3": -1.64072518e-7,
    "b4": -9.7041987e-11, "b5": 1.68200992e-14,
    "b6": -3.00042971e4, "b7": 4.9667701,
}

_THERMO_POINTS = [
    {"temperature_k": 298.15, "cp_j_mol_k": 33.59, "h_kj_mol": -241.8,
     "s_j_mol_k": 188.8, "g_kj_mol": -228.6},
    {"temperature_k": 500.0, "cp_j_mol_k": 35.22, "h_kj_mol": -234.9,
     "s_j_mol_k": 206.5, "g_kj_mol": -219.1},
]


def test_thermo_upload_preserves_nasa_coefficients_and_tabulated_points(
    db_engine,
) -> None:
    """Push a full thermo payload through ``persist_thermo_upload`` and
    verify that every invariant-carrying field survives unchanged.

    Invariant-relevant fields here:

    - every NASA coefficient and every temperature bound
    - every tabulated ``(T, Cp, H, S, G)`` row

    If a refactor silently drops or rescales one of these, downstream
    thermodynamic computations based on the persisted row diverge from
    the upload — exactly the "valid-looking but wrong" regression this
    suite is here to catch.
    """
    request = ThermoUploadRequest(
        species_entry=dict(_THERMO_SPECIES),
        scientific_origin="computed",
        h298_kj_mol=-255.0,
        s298_j_mol_k=322.9,
        nasa=_WATER_NASA,
        points=_THERMO_POINTS,
        note="invariant-regression",
    )

    with Session(db_engine) as session, session.begin():
        thermo = persist_thermo_upload(session, request)

        # NASA block — every coefficient must round-trip exactly.
        nasa = session.scalars(
            select(ThermoNASA).where(ThermoNASA.thermo_id == thermo.id)
        ).one()
        for field, expected in _WATER_NASA.items():
            actual = getattr(nasa, field)
            assert actual == pytest.approx(expected, rel=1e-12), (
                f"NASA.{field} drifted: {actual} != {expected}"
            )

        # Tabulated points — every (T, Cp, H, S, G) tuple must survive.
        points = session.scalars(
            select(ThermoPoint)
            .where(ThermoPoint.thermo_id == thermo.id)
            .order_by(ThermoPoint.temperature_k)
        ).all()
        assert len(points) == len(_THERMO_POINTS)
        for persisted, expected in zip(points, _THERMO_POINTS):
            assert persisted.temperature_k == pytest.approx(expected["temperature_k"])
            assert persisted.cp_j_mol_k == pytest.approx(expected["cp_j_mol_k"])
            assert persisted.h_kj_mol == pytest.approx(expected["h_kj_mol"])
            assert persisted.s_j_mol_k == pytest.approx(expected["s_j_mol_k"])
            assert persisted.g_kj_mol == pytest.approx(expected["g_kj_mol"])

        # Scalar 298 K fields must also round-trip, since downstream
        # consumers often use those directly rather than the NASA form.
        persisted = session.get(Thermo, thermo.id)
        assert persisted is not None
        assert persisted.h298_kj_mol == pytest.approx(-255.0)
        assert persisted.s298_j_mol_k == pytest.approx(322.9)


# ---------------------------------------------------------------------------
# Workflow regression 2: shared calculation-resolution preserves identity
# ---------------------------------------------------------------------------


def test_two_calculations_with_equivalent_lot_share_one_level_of_theory_row(
    db_engine,
) -> None:
    """Two calculations uploaded with the same level-of-theory payload must
    resolve to the same ``LevelOfTheory`` row, and the persisted row's
    ``lot_hash`` must match the deterministic value produced by
    ``_level_of_theory_hash``.

    This is the load-bearing dedupe guarantee: if the workflow ever
    started splitting equivalent LoT inputs into separate rows — for
    example because of a normalization bug on a new field — downstream
    consumers of level-of-theory identity (LoT-keyed lookups, energy
    corrections, frequency scale factors) would silently see forks.
    """
    from app.schemas.fragments.calculation import CalculationWithResultsPayload
    from app.schemas.fragments.identity import SpeciesEntryIdentityPayload
    from app.schemas.fragments.refs import LevelOfTheoryRef

    level_of_theory_ref = LevelOfTheoryRef(
        method="B3LYP",
        basis="6-31G(d)",
    )
    software = {"name": "Gaussian", "version": "16"}

    calc_payload_a = {
        "type": "sp",
        "software_release": software,
        "level_of_theory": level_of_theory_ref,
        "sp_result": {"electronic_energy_hartree": -79.85},
    }
    calc_payload_b = {
        "type": "sp",
        "software_release": software,
        "level_of_theory": level_of_theory_ref,
        "sp_result": {"electronic_energy_hartree": -79.86},
    }

    with Session(db_engine) as session, session.begin():
        species_entry = resolve_species_entry(
            session,
            SpeciesEntryIdentityPayload(
                smiles="CC", charge=0, multiplicity=1,
            ),
        )

        calc_a = resolve_and_persist_calculation_with_results(
            session,
            CalculationWithResultsPayload(**calc_payload_a),
            species_entry_id=species_entry.id,
        )
        calc_b = resolve_and_persist_calculation_with_results(
            session,
            CalculationWithResultsPayload(**calc_payload_b),
            species_entry_id=species_entry.id,
        )

        # Same LoT content → shared row.
        assert calc_a.lot_id == calc_b.lot_id, (
            "Equivalent level-of-theory payloads must resolve to the same row; "
            "splitting them would corrupt LoT-keyed dedupe across the whole DB."
        )

        lot = session.get(LevelOfTheory, calc_a.lot_id)
        assert lot is not None
        assert lot.lot_hash == _level_of_theory_hash(level_of_theory_ref)

        # Electronic energies are invariant-relevant scientific metadata:
        # both values must survive the shared-LoT dedupe untouched.
        sp_rows_by_calc = {
            row.calculation_id: row
            for row in session.scalars(
                select(CalculationSPResult).where(
                    CalculationSPResult.calculation_id.in_([calc_a.id, calc_b.id])
                )
            ).all()
        }
        assert sp_rows_by_calc[calc_a.id].electronic_energy_hartree == pytest.approx(-79.85)
        assert sp_rows_by_calc[calc_b.id].electronic_energy_hartree == pytest.approx(-79.86)

        assert calc_a.type == CalculationType.sp
        assert calc_b.type == CalculationType.sp
