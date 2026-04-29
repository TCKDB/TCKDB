"""Workflow-layer tests for the computed-species bundle.

Covers the cross-key resolution, conformer-group reuse semantics,
DR-0028 role/type compatibility, and orchestration order without going
through HTTP.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.calculation import (
    Calculation,
    CalculationDependency,
    CalculationFreqResult,
    CalculationSPResult,
)
from app.db.models.common import (
    AppUserRole,
    CalculationDependencyRole,
    CalculationType,
)
from app.db.models.species import ConformerGroup, ConformerObservation
from app.db.models.thermo import (
    Thermo,
    ThermoNASA,
    ThermoPoint,
    ThermoSourceCalculation,
)
from app.schemas.workflows.computed_species_upload import (
    ComputedSpeciesUploadRequest,
)
from app.workflows.computed_species import persist_computed_species_upload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _calc(key: str, *, calc_type: str = "opt", **overrides) -> dict:
    base: dict = {
        "key": key,
        "type": calc_type,
        "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
        "software_release": {"name": "Gaussian", "version": "16"},
    }
    if calc_type == "opt":
        base.setdefault("opt_result", {"converged": True})
    elif calc_type == "freq":
        base.setdefault("freq_result", {"n_imag": 0})
    elif calc_type == "sp":
        base.setdefault("sp_result", {"electronic_energy_hartree": -76.4})
    base.update(overrides)
    return base


_H_GEOM = {"xyz_text": "1\nH atom\nH 0.0 0.0 0.0"}
_H_GEOM_2 = {"xyz_text": "1\nH atom\nH 0.0 0.0 0.5"}
_WATER_GEOM = {
    "xyz_text": (
        "3\nwater\n"
        "O 0.0 0.0 0.117\n"
        "H 0.0 0.757 -0.469\n"
        "H 0.0 -0.757 -0.469"
    )
}


def _hydrogen_bundle(**overrides) -> ComputedSpeciesUploadRequest:
    base: dict = {
        "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
        "conformers": [
            {
                "key": "c0",
                "geometry": dict(_H_GEOM),
                "primary_calculation": _calc("opt0", calc_type="opt"),
            }
        ],
    }
    base.update(overrides)
    return ComputedSpeciesUploadRequest(**base)


def _ensure_user(session: Session, *, username: str) -> int:
    user = session.scalar(select(AppUser).where(AppUser.username == username))
    if user is None:
        user = AppUser(username=username, role=AppUserRole.user)
        session.add(user)
        session.flush()
    return user.id


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_minimal_bundle_persists(db_engine) -> None:
    with Session(db_engine) as session, session.begin():
        user_id = _ensure_user(session, username="bundle_min")
        outcome = persist_computed_species_upload(
            session, _hydrogen_bundle(), created_by=user_id
        )
        assert outcome.species_entry_id is not None
        assert len(outcome.conformers) == 1
        assert outcome.conformers[0].primary_calculation.type == CalculationType.opt
        assert outcome.thermo is None


def test_bundle_with_freq_and_sp_creates_auto_dependencies(db_engine) -> None:
    """Additional freq + sp calcs auto-link freq_on / single_point_on
    edges to the primary opt — same semantics as /uploads/conformers."""
    with Session(db_engine) as session, session.begin():
        user_id = _ensure_user(session, username="bundle_auto_deps")
        bundle = _hydrogen_bundle()
        bundle.conformers[0].additional_calculations = [
            type(bundle.conformers[0].primary_calculation)(
                **_calc("freq0", calc_type="freq")
            ),
            type(bundle.conformers[0].primary_calculation)(
                **_calc("sp0", calc_type="sp")
            ),
        ]
        outcome = persist_computed_species_upload(
            session, bundle, created_by=user_id
        )
        primary_id = outcome.conformers[0].primary_calculation.id
        edges = session.scalars(
            select(CalculationDependency).where(
                CalculationDependency.parent_calculation_id == primary_id
            )
        ).all()
        roles = {e.dependency_role for e in edges}
        assert CalculationDependencyRole.freq_on in roles
        assert CalculationDependencyRole.single_point_on in roles
        # Result rows persisted.
        assert (
            session.scalar(
                select(func.count()).select_from(CalculationFreqResult)
            )
            >= 1
        )
        assert (
            session.scalar(select(func.count()).select_from(CalculationSPResult))
            >= 1
        )


def test_multiple_conformers_create_distinct_groups(db_engine) -> None:
    """3 distinct geometries → 3 conformer_observation rows (groups may
    share for trivial single-atom species without a torsion fingerprint)."""
    with Session(db_engine) as session, session.begin():
        _ensure_user(session, username="bundle_multi")
        bundle_data = {
            "species_entry": {
                "smiles": "[CH3]",
                "charge": 0,
                "multiplicity": 2,
            },
            "conformers": [
                {
                    "key": f"c{i}",
                    "geometry": {
                        "xyz_text": (
                            "4\nmethyl\n"
                            "C 0.0 0.0 0.0\n"
                            "H 1.0 0.0 0.0\n"
                            "H -0.5 0.866 0.0\n"
                            f"H -0.5 -0.866 {0.0 + 0.001 * i}"
                        )
                    },
                    "primary_calculation": _calc(f"opt{i}", calc_type="opt"),
                }
                for i in range(3)
            ],
        }
        outcome = persist_computed_species_upload(
            session, ComputedSpeciesUploadRequest(**bundle_data)
        )
        assert len(outcome.conformers) == 3
        # 3 observations on the same species entry.
        assert (
            session.scalar(
                select(func.count())
                .select_from(ConformerObservation)
                .join(ConformerGroup)
                .where(
                    ConformerGroup.species_entry_id == outcome.species_entry_id
                )
            )
            == 3
        )


def test_chemistry_only_bundle_omits_thermo(db_engine) -> None:
    with Session(db_engine) as session, session.begin():
        outcome = persist_computed_species_upload(
            session, _hydrogen_bundle()
        )
        assert outcome.thermo is None


# ---------------------------------------------------------------------------
# Thermo
# ---------------------------------------------------------------------------


def _bundle_with_thermo() -> ComputedSpeciesUploadRequest:
    return ComputedSpeciesUploadRequest(
        species_entry={"smiles": "O", "charge": 0, "multiplicity": 1},
        conformers=[
            {
                "key": "c0",
                "geometry": dict(_WATER_GEOM),
                "primary_calculation": _calc("opt0", calc_type="opt"),
                "additional_calculations": [
                    _calc("freq0", calc_type="freq"),
                    _calc("sp0", calc_type="sp"),
                ],
            }
        ],
        thermo={
            "h298_kj_mol": -241.8,
            "s298_j_mol_k": 188.8,
            "tmin_k": 200.0,
            "tmax_k": 3000.0,
            "nasa": {
                "t_low": 200.0,
                "t_mid": 1000.0,
                "t_high": 3500.0,
                "a1": 4.198,
                "b1": 3.034,
            },
            "points": [
                {"temperature_k": 298.15, "cp_j_mol_k": 33.59},
                {"temperature_k": 1000.0, "cp_j_mol_k": 41.27},
            ],
            "source_calculations": [
                {"calculation_key": "opt0", "role": "opt"},
                {"calculation_key": "freq0", "role": "freq"},
                {"calculation_key": "sp0", "role": "sp"},
            ],
        },
    )


def test_thermo_block_persists_with_source_links(db_engine) -> None:
    with Session(db_engine) as session, session.begin():
        outcome = persist_computed_species_upload(
            session, _bundle_with_thermo()
        )
        assert outcome.thermo is not None
        assert outcome.thermo.h298_kj_mol == pytest.approx(-241.8)

        nasa = session.scalar(
            select(ThermoNASA).where(ThermoNASA.thermo_id == outcome.thermo.id)
        )
        assert nasa is not None
        assert nasa.t_low == pytest.approx(200.0)

        points = session.scalars(
            select(ThermoPoint).where(ThermoPoint.thermo_id == outcome.thermo.id)
        ).all()
        assert len(points) == 2

        sources = session.scalars(
            select(ThermoSourceCalculation).where(
                ThermoSourceCalculation.thermo_id == outcome.thermo.id
            )
        ).all()
        assert len(sources) == 3
        # Every source calc id is one of the bundle's calcs.
        bundle_calc_ids = {
            outcome.conformers[0].primary_calculation.id,
            *(c.id for c in outcome.conformers[0].additional_calculations),
        }
        assert {s.calculation_id for s in sources} == bundle_calc_ids


def test_thermo_role_type_mismatch_raises(db_engine) -> None:
    with Session(db_engine) as session, session.begin():
        # source role=opt but pointing at a freq calc → 422 in route, ValueError here.
        bundle = _bundle_with_thermo()
        bundle.thermo.source_calculations[0].calculation_key = "freq0"
        with pytest.raises(ValueError) as exc:
            persist_computed_species_upload(session, bundle)
        assert "incompatible" in str(exc.value)


def test_dependency_role_type_mismatch_raises(db_engine) -> None:
    """freq_on must point at an opt parent. Pointing it at the same
    calc as another freq calc (which is type=freq) raises ValueError."""
    with Session(db_engine) as session, session.begin():
        bundle = _hydrogen_bundle()
        bundle.conformers[0].additional_calculations = [
            type(bundle.conformers[0].primary_calculation)(
                **_calc("freq0", calc_type="freq")
            ),
            type(bundle.conformers[0].primary_calculation)(
                **_calc(
                    "freq1",
                    calc_type="freq",
                    depends_on=[
                        {
                            "parent_calculation_key": "freq0",
                            "role": "freq_on",
                        }
                    ],
                )
            ),
        ]
        with pytest.raises(ValueError) as exc:
            persist_computed_species_upload(session, bundle)
        assert "incompatible" in str(exc.value)


# ---------------------------------------------------------------------------
# Cross-bundle conformer-group reuse (DR-0029 Requirement 6)
# ---------------------------------------------------------------------------


def test_two_bundles_same_basin_reuse_conformer_group(db_engine) -> None:
    """Bundle A creates a group; bundle B with the same molecule and
    geometry reuses A's group but creates a fresh conformer_observation."""
    with Session(db_engine) as session, session.begin():
        outcome_a = persist_computed_species_upload(session, _hydrogen_bundle())
        outcome_b = persist_computed_species_upload(session, _hydrogen_bundle())

        group_a = outcome_a.conformers[0].group_id
        group_b = outcome_b.conformers[0].group_id
        # Same basin → same group; observations distinct.
        assert group_a == group_b
        assert (
            outcome_a.conformers[0].observation.id
            != outcome_b.conformers[0].observation.id
        )
