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
    CalculationHessian,
    CalculationInputGeometry,
    CalculationOutputGeometry,
    CalculationScanCoordinate,
    CalculationScanPoint,
    CalculationScanPointCoordinateValue,
    CalculationScanResult,
    CalculationSPResult,
    CalculationWavefunctionDiagnostic,
)
from app.db.models.common import (
    AppUserRole,
    CalculationDependencyRole,
    CalculationType,
)
from app.db.models.energy_correction import (
    AppliedEnergyCorrection,
    AppliedEnergyCorrectionComponent,
    EnergyCorrectionScheme,
    EnergyCorrectionSchemeAtomParam,
    EnergyCorrectionSchemeBondParam,
    EnergyCorrectionSchemeComponentParam,
    FrequencyScaleFactor,
)
from app.db.models.species import ConformerGroup, ConformerObservation
from app.db.models.statmech import (
    Statmech,
    StatmechSourceCalculation,
    StatmechTorsion,
    StatmechTorsionDefinition,
)
from app.db.models.thermo import (
    ThermoNASA,
    ThermoPoint,
    ThermoSourceCalculation,
)
from app.schemas.fragments.calculation import OutputGeometryEntry
from app.schemas.fragments.geometry import GeometryPayload
from app.schemas.workflows.computed_species_upload import (
    CalculationInBundle,
    ComputedSpeciesUploadRequest,
)
from app.workflows.computed_species import persist_computed_species_upload

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _calc(
    key: str,
    *,
    calc_type: str = "opt",
    wavefunction_diagnostic: dict | None = None,
    **overrides,
) -> dict:
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
    if wavefunction_diagnostic is not None:
        base["wavefunction_diagnostic"] = wavefunction_diagnostic
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


def test_bundle_sp_calc_with_wavefunction_diagnostic_persists(
    db_engine,
) -> None:
    """A bundle SP additional calc carrying ``wavefunction_diagnostic``
    persists one ``calc_wavefunction_diagnostic`` row anchored to the SP
    calculation row."""
    with Session(db_engine) as session, session.begin():
        user_id = _ensure_user(session, username="bundle_wfn_diag")
        bundle = _hydrogen_bundle()
        bundle.conformers[0].additional_calculations = [
            type(bundle.conformers[0].primary_calculation)(
                **_calc(
                    "sp0",
                    calc_type="sp",
                    wavefunction_diagnostic={
                        "t1_diagnostic": 0.0179,
                        "d1_diagnostic": 0.045,
                        "note": "ORCA CCSD(T)",
                    },
                )
            ),
        ]
        outcome = persist_computed_species_upload(
            session, bundle, created_by=user_id
        )

        sp_id = outcome.conformers[0].additional_calculations[0].id
        rows = session.scalars(
            select(CalculationWavefunctionDiagnostic).where(
                CalculationWavefunctionDiagnostic.calculation_id == sp_id
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].t1_diagnostic == pytest.approx(0.0179)
        assert rows[0].d1_diagnostic == pytest.approx(0.045)
        assert rows[0].note == "ORCA CCSD(T)"


def test_bundle_freq_calc_with_hessian_persists(db_engine) -> None:
    """A bundle freq additional calc carrying a ``hessian`` persists one
    ``calculation_hessian`` row bound to the geometry the Hessian was
    computed at, with the correct triangle length (3N(3N+1)/2)."""
    with Session(db_engine) as session, session.begin():
        user_id = _ensure_user(session, username="bundle_hessian")
        bundle = _hydrogen_bundle()
        # 1-atom geometry → 3N = 3 → lower triangle length = 3*4/2 = 6.
        triangle = [float(i) for i in range(6)]
        bundle.conformers[0].additional_calculations = [
            type(bundle.conformers[0].primary_calculation)(
                **_calc(
                    "freq0",
                    calc_type="freq",
                    hessian={
                        "geometry": dict(_H_GEOM),
                        "lower_triangle_hartree_bohr2": triangle,
                        "source": "parsed_fchk",
                    },
                )
            ),
        ]
        outcome = persist_computed_species_upload(
            session, bundle, created_by=user_id
        )

        freq_id = outcome.conformers[0].additional_calculations[0].id
        rows = session.scalars(
            select(CalculationHessian).where(
                CalculationHessian.calculation_id == freq_id
            )
        ).all()
        assert len(rows) == 1
        hess = rows[0]
        assert hess.natoms == 1
        assert len(hess.lower_triangle_hartree_bohr2) == 6
        assert hess.geometry_id is not None
        # The Hessian binds to the same content-addressed geometry as the
        # conformer/freq input geometry (H atom at origin).
        input_geom = session.scalar(
            select(CalculationInputGeometry).where(
                CalculationInputGeometry.calculation_id == freq_id
            )
        )
        assert input_geom is not None
        assert hess.geometry_id == input_geom.geometry_id


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


def test_bundle_freq_and_sp_get_input_geometry_rows(db_engine) -> None:
    """Bundle additionals of type freq/sp must produce one
    calculation_input_geometry row each, pointing at the conformer
    geometry. The primary opt has one output_geometry row (role=final);
    freq/sp produce zero output_geometry rows under the narrowed
    fallback (only opt qualifies for the auto-create).
    """
    with Session(db_engine) as session, session.begin():
        user_id = _ensure_user(session, username="bundle_input_geom")
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
        freq_id = outcome.conformers[0].additional_calculations[0].id
        sp_id = outcome.conformers[0].additional_calculations[1].id

        # Primary opt: 1 output row, 0 input rows.
        primary_outputs = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == primary_id
            )
        ).all()
        assert len(primary_outputs) == 1
        conformer_geom_id = primary_outputs[0].geometry_id
        primary_inputs = session.scalars(
            select(CalculationInputGeometry).where(
                CalculationInputGeometry.calculation_id == primary_id
            )
        ).all()
        assert primary_inputs == []

        # freq: zero output rows; exactly one input row at input_order=1
        # pointing at the conformer geometry (the input fallback for
        # freq is unchanged by this PR).
        freq_outputs = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == freq_id
            )
        ).all()
        assert freq_outputs == []
        freq_inputs = session.scalars(
            select(CalculationInputGeometry).where(
                CalculationInputGeometry.calculation_id == freq_id
            )
        ).all()
        assert len(freq_inputs) == 1
        assert freq_inputs[0].input_order == 1
        assert freq_inputs[0].geometry_id == conformer_geom_id

        # sp: zero output rows; exactly one input row at input_order=1
        # pointing at the conformer geometry.
        sp_outputs = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == sp_id
            )
        ).all()
        assert sp_outputs == []
        sp_inputs = session.scalars(
            select(CalculationInputGeometry).where(
                CalculationInputGeometry.calculation_id == sp_id
            )
        ).all()
        assert len(sp_inputs) == 1
        assert sp_inputs[0].input_order == 1
        assert sp_inputs[0].geometry_id == conformer_geom_id

        # Bundle scope: exactly the freq + sp calcs get input rows;
        # the primary opt does not. Scope to the three calcs in this
        # bundle to avoid coupling to rows persisted by prior tests
        # (db_engine is session-scoped; see conftest).
        bundle_calc_ids = [primary_id, freq_id, sp_id]
        bundle_input_rows = session.scalars(
            select(CalculationInputGeometry).where(
                CalculationInputGeometry.calculation_id.in_(bundle_calc_ids)
            )
        ).all()
        assert len(bundle_input_rows) == 2
        assert {r.calculation_id for r in bundle_input_rows} == {freq_id, sp_id}


def test_bundle_explicit_input_geometries_for_opt(db_engine) -> None:
    """A producer that knows opt's pre-opt input xyz can declare it via
    ``input_geometries``; the workflow lands one
    ``calculation_input_geometry`` row pointing at the resolved geometry,
    which is distinct from opt's output (the conformer geometry)."""
    pre_opt_xyz = "1\npre-opt H\nH 0.0 0.0 0.123"
    with Session(db_engine) as session, session.begin():
        user_id = _ensure_user(session, username="bundle_explicit_opt")
        bundle = _hydrogen_bundle()
        bundle.conformers[0].primary_calculation.input_geometries = [
            type(bundle.conformers[0].geometry)(xyz_text=pre_opt_xyz),
        ]
        outcome = persist_computed_species_upload(
            session, bundle, created_by=user_id
        )
        opt_id = outcome.conformers[0].primary_calculation.id

        opt_inputs = session.scalars(
            select(CalculationInputGeometry).where(
                CalculationInputGeometry.calculation_id == opt_id
            )
        ).all()
        assert len(opt_inputs) == 1
        assert opt_inputs[0].input_order == 1

        opt_outputs = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == opt_id
            )
        ).all()
        assert len(opt_outputs) == 1
        # Producer-declared input is the pre-opt geometry, not the
        # converged conformer geometry that ended up as opt's output.
        assert opt_inputs[0].geometry_id != opt_outputs[0].geometry_id


def test_bundle_explicit_input_geometries_overrides_freq_sp_fallback(
    db_engine,
) -> None:
    """When ``input_geometries`` is set on a freq calc, the workflow must
    use the producer-declared geometry, not the conformer geometry that
    the fallback would otherwise pick."""
    declared_xyz = "1\ndeclared\nH 0.0 0.0 0.999"
    with Session(db_engine) as session, session.begin():
        user_id = _ensure_user(session, username="bundle_explicit_freq")
        bundle = _hydrogen_bundle()
        freq_in = type(bundle.conformers[0].primary_calculation)(
            **_calc("freq0", calc_type="freq")
        )
        freq_in.input_geometries = [
            type(bundle.conformers[0].geometry)(xyz_text=declared_xyz),
        ]
        bundle.conformers[0].additional_calculations = [freq_in]
        outcome = persist_computed_species_upload(
            session, bundle, created_by=user_id
        )
        freq_id = outcome.conformers[0].additional_calculations[0].id

        freq_inputs = session.scalars(
            select(CalculationInputGeometry).where(
                CalculationInputGeometry.calculation_id == freq_id
            )
        ).all()
        assert len(freq_inputs) == 1

        # The conformer geometry (opt output) and the producer-declared
        # geometry resolve to different Geometry rows, so the freq input
        # row must point at the declared one.
        primary_id = outcome.conformers[0].primary_calculation.id
        conformer_geom_id = session.scalars(
            select(CalculationOutputGeometry.geometry_id).where(
                CalculationOutputGeometry.calculation_id == primary_id
            )
        ).one()
        assert freq_inputs[0].geometry_id != conformer_geom_id


def test_bundle_empty_input_geometries_uses_fallback(db_engine) -> None:
    """When every calc has an empty ``input_geometries``, the workflow
    preserves the prior PR's behavior: freq+sp link to the conformer
    geometry, opt skips."""
    with Session(db_engine) as session, session.begin():
        user_id = _ensure_user(session, username="bundle_fallback")
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
        opt_id = outcome.conformers[0].primary_calculation.id
        freq_id = outcome.conformers[0].additional_calculations[0].id
        sp_id = outcome.conformers[0].additional_calculations[1].id

        rows = session.scalars(
            select(CalculationInputGeometry).where(
                CalculationInputGeometry.calculation_id.in_(
                    [opt_id, freq_id, sp_id]
                )
            )
        ).all()
        assert {r.calculation_id for r in rows} == {freq_id, sp_id}


def test_bundle_multi_input_geometries_for_one_calc(db_engine) -> None:
    """A producer can declare multiple input geometries for one calc;
    each lands at ``input_order = 1, 2, ...`` in declaration order."""
    xyz_a = "1\ngeom-a\nH 0.0 0.0 0.111"
    xyz_b = "1\ngeom-b\nH 0.0 0.0 0.222"
    with Session(db_engine) as session, session.begin():
        user_id = _ensure_user(session, username="bundle_multi_input")
        bundle = _hydrogen_bundle()
        bundle.conformers[0].primary_calculation.input_geometries = [
            type(bundle.conformers[0].geometry)(xyz_text=xyz_a),
            type(bundle.conformers[0].geometry)(xyz_text=xyz_b),
        ]
        outcome = persist_computed_species_upload(
            session, bundle, created_by=user_id
        )
        opt_id = outcome.conformers[0].primary_calculation.id

        rows = session.scalars(
            select(CalculationInputGeometry)
            .where(CalculationInputGeometry.calculation_id == opt_id)
            .order_by(CalculationInputGeometry.input_order)
        ).all()
        assert [r.input_order for r in rows] == [1, 2]
        assert rows[0].geometry_id != rows[1].geometry_id


def test_bundle_duplicate_input_geometries_rejected(db_engine) -> None:
    """Declaring the same geometry twice in a single calc's
    ``input_geometries`` list is rejected as a 422 (``ValueError`` from
    a workflow-level pre-check, not a bare ``IntegrityError``)."""
    same_xyz = "1\nsame\nH 0.0 0.0 0.314"
    with Session(db_engine) as session, session.begin():
        _ensure_user(session, username="bundle_dup_input")
        bundle = _hydrogen_bundle()
        bundle.conformers[0].primary_calculation.input_geometries = [
            type(bundle.conformers[0].geometry)(xyz_text=same_xyz),
            type(bundle.conformers[0].geometry)(xyz_text=same_xyz),
        ]
        with pytest.raises(ValueError, match="more than once"):
            persist_computed_species_upload(session, bundle)


def test_bundle_explicit_output_geometries_for_opt(db_engine) -> None:
    """A producer can declare opt's converged output explicitly via
    ``output_geometries``; the producer-explicit path runs and the
    fallback does NOT also fire."""
    declared_xyz = "1\ndeclared-final\nH 0.0 0.0 0.987"
    with Session(db_engine) as session, session.begin():
        from app.db.models.common import CalculationGeometryRole

        _ensure_user(session, username="bundle_explicit_output_opt")
        bundle = _hydrogen_bundle()
        bundle.conformers[0].primary_calculation.output_geometries = [
            OutputGeometryEntry(
                geometry=GeometryPayload(xyz_text=declared_xyz),
                role=CalculationGeometryRole.final,
            ),
        ]
        outcome = persist_computed_species_upload(session, bundle)
        opt_id = outcome.conformers[0].primary_calculation.id

        rows = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == opt_id
            )
        ).all()
        # Exactly one row — proves the fallback did NOT also fire on top
        # of the producer-explicit declaration.
        assert len(rows) == 1
        assert rows[0].output_order == 1
        assert rows[0].role == CalculationGeometryRole.final


def test_bundle_explicit_output_geometries_for_scan(db_engine) -> None:
    """A scan calc that declares three output geometries with role=scan_point
    produces three rows at output_order=1, 2, 3 with the matching role."""
    xyz_a = "1\nscan-1\nH 0.0 0.0 0.10"
    xyz_b = "1\nscan-2\nH 0.0 0.0 0.20"
    xyz_c = "1\nscan-3\nH 0.0 0.0 0.30"
    with Session(db_engine) as session, session.begin():
        from app.db.models.common import CalculationGeometryRole

        _ensure_user(session, username="bundle_scan_outputs")
        bundle = _hydrogen_bundle()
        scan_in = CalculationInBundle(
            key="scan0",
            type="scan",
            level_of_theory={"method": "B3LYP", "basis": "6-31G(d)"},
            software_release={"name": "Gaussian", "version": "16"},
            output_geometries=[
                OutputGeometryEntry(
                    geometry=GeometryPayload(xyz_text=xyz_a),
                    role=CalculationGeometryRole.scan_point,
                ),
                OutputGeometryEntry(
                    geometry=GeometryPayload(xyz_text=xyz_b),
                    role=CalculationGeometryRole.scan_point,
                ),
                OutputGeometryEntry(
                    geometry=GeometryPayload(xyz_text=xyz_c),
                    role=CalculationGeometryRole.scan_point,
                ),
            ],
        )
        bundle.conformers[0].additional_calculations = [scan_in]
        outcome = persist_computed_species_upload(session, bundle)
        scan_id = outcome.conformers[0].additional_calculations[0].id

        rows = session.scalars(
            select(CalculationOutputGeometry)
            .where(CalculationOutputGeometry.calculation_id == scan_id)
            .order_by(CalculationOutputGeometry.output_order)
        ).all()
        assert [r.output_order for r in rows] == [1, 2, 3]
        assert all(r.role == CalculationGeometryRole.scan_point for r in rows)
        assert len({r.geometry_id for r in rows}) == 3


def test_bundle_explicit_output_geometries_with_irc_roles(db_engine) -> None:
    """An IRC calc that declares one ``irc_forward`` and one ``irc_reverse``
    output geometry produces two rows with the producer-declared roles."""
    xyz_fwd = "1\nirc-fwd\nH 0.0 0.0 0.40"
    xyz_rev = "1\nirc-rev\nH 0.0 0.0 0.50"
    with Session(db_engine) as session, session.begin():
        from app.db.models.common import CalculationGeometryRole

        _ensure_user(session, username="bundle_irc_outputs")
        bundle = _hydrogen_bundle()
        irc_in = CalculationInBundle(
            key="irc0",
            type="irc",
            level_of_theory={"method": "B3LYP", "basis": "6-31G(d)"},
            software_release={"name": "Gaussian", "version": "16"},
            output_geometries=[
                OutputGeometryEntry(
                    geometry=GeometryPayload(xyz_text=xyz_fwd),
                    role=CalculationGeometryRole.irc_forward,
                ),
                OutputGeometryEntry(
                    geometry=GeometryPayload(xyz_text=xyz_rev),
                    role=CalculationGeometryRole.irc_reverse,
                ),
            ],
        )
        bundle.conformers[0].additional_calculations = [irc_in]
        outcome = persist_computed_species_upload(session, bundle)
        irc_id = outcome.conformers[0].additional_calculations[0].id

        rows = session.scalars(
            select(CalculationOutputGeometry)
            .where(CalculationOutputGeometry.calculation_id == irc_id)
            .order_by(CalculationOutputGeometry.output_order)
        ).all()
        assert [r.role for r in rows] == [
            CalculationGeometryRole.irc_forward,
            CalculationGeometryRole.irc_reverse,
        ]
        assert [r.output_order for r in rows] == [1, 2]


def test_bundle_empty_output_geometries_opt_uses_fallback(db_engine) -> None:
    """When opt has empty ``output_geometries``, the narrowed fallback
    fires: one row at (role=final, output_order=1) pointing at the
    conformer geometry."""
    with Session(db_engine) as session, session.begin():
        _ensure_user(session, username="bundle_opt_fallback")
        bundle = _hydrogen_bundle()
        outcome = persist_computed_species_upload(session, bundle)
        opt_id = outcome.conformers[0].primary_calculation.id

        from app.db.models.common import CalculationGeometryRole

        rows = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == opt_id
            )
        ).all()
        assert len(rows) == 1
        assert rows[0].role == CalculationGeometryRole.final
        assert rows[0].output_order == 1


def test_bundle_empty_output_geometries_freq_sp_get_zero_rows(db_engine) -> None:
    """Freq and sp with empty ``output_geometries`` produce ZERO
    calculation_output_geometry rows. THIS IS THE BEHAVIOR CHANGE: the
    pre-PR fallback would have written one row each; the narrowed
    fallback only fires for opt."""
    with Session(db_engine) as session, session.begin():
        _ensure_user(session, username="bundle_freq_sp_zero_outputs")
        bundle = _hydrogen_bundle()
        bundle.conformers[0].additional_calculations = [
            type(bundle.conformers[0].primary_calculation)(
                **_calc("freq0", calc_type="freq")
            ),
            type(bundle.conformers[0].primary_calculation)(
                **_calc("sp0", calc_type="sp")
            ),
        ]
        outcome = persist_computed_species_upload(session, bundle)
        freq_id = outcome.conformers[0].additional_calculations[0].id
        sp_id = outcome.conformers[0].additional_calculations[1].id

        freq_rows = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == freq_id
            )
        ).all()
        sp_rows = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == sp_id
            )
        ).all()
        assert freq_rows == []
        assert sp_rows == []


def test_bundle_output_geometries_with_role_required_at_schema_layer() -> None:
    """``OutputGeometryEntry.role`` is required (no default). A payload
    that omits ``role`` is rejected at Pydantic validation."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as excinfo:
        ComputedSpeciesUploadRequest(
            **{
                "species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2},
                "conformers": [
                    {
                        "key": "c0",
                        "geometry": dict(_H_GEOM),
                        "primary_calculation": {
                            **_calc("opt0", calc_type="opt"),
                            "output_geometries": [
                                # role intentionally missing
                                {"geometry": dict(_H_GEOM_2)},
                            ],
                        },
                    }
                ],
            }
        )
    assert "role" in str(excinfo.value).lower()


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


# ---------------------------------------------------------------------------
# Conformer-context invariant (audit: species-side calcs must carry
# conformer_observation_id; products must trace to conformer observations)
# ---------------------------------------------------------------------------


def _bundle_multi_conformer_thermo_statmech() -> ComputedSpeciesUploadRequest:
    """Two distinct methyl conformers, each with opt + freq, plus thermo and
    statmech blocks whose source calculations reference calcs from both
    conformers by bundle-local key.
    """

    def _conf(i: int) -> dict:
        return {
            "key": f"c{i}",
            "geometry": {
                "xyz_text": (
                    "4\nmethyl\n"
                    "C 0.0 0.0 0.0\n"
                    "H 1.0 0.0 0.0\n"
                    "H -0.5 0.866 0.0\n"
                    f"H -0.5 -0.866 {0.001 * i}"
                )
            },
            "primary_calculation": _calc(f"opt{i}", calc_type="opt"),
            "additional_calculations": [_calc(f"freq{i}", calc_type="freq")],
        }

    return ComputedSpeciesUploadRequest(
        species_entry={"smiles": "[CH3]", "charge": 0, "multiplicity": 2},
        conformers=[_conf(0), _conf(1)],
        thermo={
            "h298_kj_mol": 146.7,
            "s298_j_mol_k": 194.2,
            "source_calculations": [
                {"calculation_key": "opt0", "role": "opt"},
                {"calculation_key": "freq0", "role": "freq"},
                # Source calc drawn from the *second* conformer too.
                {"calculation_key": "opt1", "role": "opt"},
            ],
        },
        statmech={
            "external_symmetry": 6,
            "point_group": "D3h",
            "is_linear": False,
            "source_calculations": [
                {"calculation_key": "freq0", "role": "freq"},
                {"calculation_key": "freq1", "role": "freq"},
            ],
        },
    )


def test_bundle_species_calcs_carry_conformer_observation_id(db_engine) -> None:
    """Every species-side calculation a computed-species bundle persists must
    carry ``conformer_observation_id`` (the audit invariant). Mirrors the SQL
    audit predicate: zero species-side calcs with a NULL conformer link.
    """
    with Session(db_engine) as session, session.begin():
        outcome = persist_computed_species_upload(
            session, _bundle_multi_conformer_thermo_statmech()
        )
        spe_id = outcome.species_entry_id

        # Direct mirror of the audit query, scoped to this species entry.
        orphaned = session.scalars(
            select(Calculation.id).where(
                Calculation.species_entry_id == spe_id,
                Calculation.transition_state_entry_id.is_(None),
                Calculation.conformer_observation_id.is_(None),
            )
        ).all()
        assert orphaned == []

        # Every bundle calc matches the expected computed-species model.
        bundle_calc_ids = []
        for conf in outcome.conformers:
            bundle_calc_ids.append(conf.primary_calculation.id)
            bundle_calc_ids.extend(c.id for c in conf.additional_calculations)
        assert len(bundle_calc_ids) == 4  # 2 conformers × (opt + freq)

        calcs = session.scalars(
            select(Calculation).where(Calculation.id.in_(bundle_calc_ids))
        ).all()
        for c in calcs:
            assert c.species_entry_id == spe_id
            assert c.transition_state_entry_id is None
            assert c.conformer_observation_id is not None
            # The conformer observation belongs to this species entry.
            obs = session.get(ConformerObservation, c.conformer_observation_id)
            assert obs is not None
            grp = session.get(ConformerGroup, obs.conformer_group_id)
            assert grp.species_entry_id == spe_id

        # Two distinct conformers → two distinct observations preserved
        # (conformer context is not collapsed onto the species entry).
        observation_ids = {c.conformer_observation_id for c in calcs}
        assert len(observation_ids) == 2


def test_bundle_thermo_statmech_sources_trace_to_conformer_observations(
    db_engine,
) -> None:
    """Computed thermo and statmech source calculations must trace back to a
    conformer observation — i.e. each source calc carries a non-null
    ``conformer_observation_id``.
    """
    with Session(db_engine) as session, session.begin():
        outcome = persist_computed_species_upload(
            session, _bundle_multi_conformer_thermo_statmech()
        )
        assert outcome.thermo is not None
        assert outcome.statmech is not None

        thermo_sources = session.scalars(
            select(ThermoSourceCalculation).where(
                ThermoSourceCalculation.thermo_id == outcome.thermo.id
            )
        ).all()
        assert len(thermo_sources) == 3
        for s in thermo_sources:
            calc = session.get(Calculation, s.calculation_id)
            assert calc.conformer_observation_id is not None
            obs = session.get(
                ConformerObservation, calc.conformer_observation_id
            )
            assert obs is not None

        statmech_sources = session.scalars(
            select(StatmechSourceCalculation).where(
                StatmechSourceCalculation.statmech_id == outcome.statmech.id
            )
        ).all()
        assert len(statmech_sources) == 2
        for s in statmech_sources:
            calc = session.get(Calculation, s.calculation_id)
            assert calc.conformer_observation_id is not None
            obs = session.get(
                ConformerObservation, calc.conformer_observation_id
            )
            assert obs is not None


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


def test_optimized_from_with_freq_parent_raises(db_engine) -> None:
    """``optimized_from`` parent must be opt or path_search.

    Regression: the bundle path delivers ``role`` as a wire-mirror enum
    (``tckdb_schemas.enums.CalculationDependencyRole.optimized_from``);
    the service-layer check previously used ``is`` against the backend
    enum and silently skipped this validation. Pointing ``optimized_from``
    at a freq parent must now raise 422-style ValueError.
    """
    with Session(db_engine) as session, session.begin():
        bundle = _hydrogen_bundle()
        klass = type(bundle.conformers[0].primary_calculation)
        bundle.conformers[0].additional_calculations = [
            klass(**_calc("freq0", calc_type="freq")),
            klass(
                **_calc(
                    "freq1",
                    calc_type="freq",
                    depends_on=[
                        {
                            "parent_calculation_key": "freq0",
                            "role": "optimized_from",
                        }
                    ],
                )
            ),
        ]
        with pytest.raises(ValueError) as exc:
            persist_computed_species_upload(session, bundle)
        assert "optimized_from" in str(exc.value)
        assert "opt" in str(exc.value)


# ---------------------------------------------------------------------------
# Idempotent dependency-edge insertion
# ---------------------------------------------------------------------------


def _hydrogen_bundle_with_freq_dep() -> ComputedSpeciesUploadRequest:
    """Hydrogen bundle: primary opt + a freq calc that explicitly declares
    ``depends_on`` the primary opt with role ``freq_on``. The auto-edge
    logic would also create this exact edge — so any non-idempotent
    insertion fails with ``pk_calculation_dependency``.
    """
    bundle = _hydrogen_bundle()
    klass = type(bundle.conformers[0].primary_calculation)
    bundle.conformers[0].additional_calculations = [
        klass(
            **_calc(
                "freq0",
                calc_type="freq",
                depends_on=[
                    {"parent_calculation_key": "opt0", "role": "freq_on"}
                ],
            )
        ),
    ]
    return bundle


def test_redundant_explicit_dep_matching_auto_edge_is_idempotent(
    db_engine,
) -> None:
    """Bundle declares an explicit ``depends_on`` that matches the
    auto-edge created from primary→freq. Insertion must be idempotent:
    one edge persisted, no duplicate-key error."""
    with Session(db_engine) as session, session.begin():
        outcome = persist_computed_species_upload(
            session, _hydrogen_bundle_with_freq_dep()
        )
        primary_id = outcome.conformers[0].primary_calculation.id
        freq_id = outcome.conformers[0].additional_calculations[0].id
        edges = session.scalars(
            select(CalculationDependency).where(
                CalculationDependency.parent_calculation_id == primary_id,
                CalculationDependency.child_calculation_id == freq_id,
            )
        ).all()
        assert len(edges) == 1
        assert edges[0].dependency_role is CalculationDependencyRole.freq_on


def test_explicit_dep_with_conflicting_role_to_auto_edge_raises(
    db_engine,
) -> None:
    """Auto-edge fires with role=freq_on for a primary→freq pair. An
    explicit depends_on for the same pair but with a different role
    (``optimized_from`` — type-compatible since parent is opt) must
    surface as a clear ValueError, not a silent overwrite or pk
    violation."""
    with Session(db_engine) as session, session.begin():
        bundle = _hydrogen_bundle()
        klass = type(bundle.conformers[0].primary_calculation)
        bundle.conformers[0].additional_calculations = [
            klass(
                **_calc(
                    "freq0",
                    calc_type="freq",
                    depends_on=[
                        {
                            "parent_calculation_key": "opt0",
                            "role": "optimized_from",
                        }
                    ],
                )
            ),
        ]
        with pytest.raises(ValueError) as exc:
            persist_computed_species_upload(session, bundle)
        msg = str(exc.value)
        assert "different role" in msg
        assert "freq_on" in msg
        assert "optimized_from" in msg


def test_duplicate_explicit_deps_in_same_bundle_is_idempotent(
    db_engine,
) -> None:
    """Same ``depends_on`` triple declared twice within a single bundle
    must not double-insert. Exercises the in-session ``session.new``
    branch of the helper directly."""
    with Session(db_engine) as session, session.begin():
        bundle = _hydrogen_bundle()
        klass = type(bundle.conformers[0].primary_calculation)
        bundle.conformers[0].additional_calculations = [
            klass(
                **_calc(
                    "freq0",
                    calc_type="freq",
                    depends_on=[
                        {"parent_calculation_key": "opt0", "role": "freq_on"},
                        {"parent_calculation_key": "opt0", "role": "freq_on"},
                    ],
                )
            ),
        ]
        outcome = persist_computed_species_upload(session, bundle)
        primary_id = outcome.conformers[0].primary_calculation.id
        freq_id = outcome.conformers[0].additional_calculations[0].id
        edges = session.scalars(
            select(CalculationDependency).where(
                CalculationDependency.parent_calculation_id == primary_id,
                CalculationDependency.child_calculation_id == freq_id,
            )
        ).all()
        assert len(edges) == 1


def test_dependency_edge_idempotent_across_committed_transactions(
    db_engine,
) -> None:
    """Faithful reproduction of the original migration bug: edge persisted
    in transaction A, fresh session in transaction B re-inserts the same
    edge via the helper. The helper must hit the DB (identity map empty)
    and no-op."""
    from app.services.calculation_resolution import (
        add_dependency_edge_idempotent as _add_dependency_edge_idempotent,
    )

    with Session(db_engine) as session, session.begin():
        outcome = persist_computed_species_upload(
            session, _hydrogen_bundle_with_freq_dep()
        )
        primary_id = outcome.conformers[0].primary_calculation.id
        freq_id = outcome.conformers[0].additional_calculations[0].id

    # Fresh session — identity map is empty, helper must round-trip.
    with Session(db_engine) as session, session.begin():
        before = session.scalar(
            select(func.count()).select_from(CalculationDependency)
        )
        edge = _add_dependency_edge_idempotent(
            session,
            parent_calculation_id=primary_id,
            child_calculation_id=freq_id,
            dependency_role=CalculationDependencyRole.freq_on,
            context="cross-transaction re-insert",
        )
        session.flush()
        after = session.scalar(
            select(func.count()).select_from(CalculationDependency)
        )
        assert after == before
        assert edge.parent_calculation_id == primary_id
        assert edge.child_calculation_id == freq_id

        with pytest.raises(ValueError) as exc:
            _add_dependency_edge_idempotent(
                session,
                parent_calculation_id=primary_id,
                child_calculation_id=freq_id,
                dependency_role=CalculationDependencyRole.optimized_from,
                context="cross-transaction conflicting re-insert",
            )
        assert "different role" in str(exc.value)


def test_per_role_child_uniqueness_rejected_in_session(db_engine) -> None:
    """In-session: a ``freq_on`` edge is pending for child=C from parent=A.
    A second pending ``freq_on`` for the same child from a different
    parent must be rejected by the helper before flush — never reach
    the partial unique index."""
    from app.services.calculation_resolution import (
        add_dependency_edge_idempotent as _add_dependency_edge_idempotent,
    )

    with Session(db_engine) as session, session.begin():
        outcome = persist_computed_species_upload(
            session, _hydrogen_bundle_with_freq_dep()
        )
        # outcome flushed already; bring three calcs into a fresh, single
        # transaction by adding two more opts and exercising the helper
        # against the freq child.
        primary_a = outcome.conformers[0].primary_calculation
        freq_child = outcome.conformers[0].additional_calculations[0]

        # Stage a brand-new opt calc as a candidate "different parent".
        # Build it through the workflow so all FK resolution happens
        # naturally; the simplest way is a fresh bundle.
        outcome2 = persist_computed_species_upload(
            session,
            ComputedSpeciesUploadRequest(
                species_entry={
                    "smiles": "[H]",
                    "charge": 0,
                    "multiplicity": 2,
                },
                conformers=[
                    {
                        "key": "c0",
                        "geometry": dict(_H_GEOM_2),
                        "primary_calculation": _calc("opt_b", calc_type="opt"),
                    }
                ],
            ),
        )
        primary_b = outcome2.conformers[0].primary_calculation

        with pytest.raises(ValueError) as exc:
            _add_dependency_edge_idempotent(
                session,
                parent_calculation_id=primary_b.id,
                child_calculation_id=freq_child.id,
                dependency_role=CalculationDependencyRole.freq_on,
                context="second-parent attempt",
            )
        msg = str(exc.value)
        assert "freq_on" in msg
        assert "different parent" in msg
        # And nothing leaked into the DB.
        assert primary_a.id != primary_b.id


def test_per_role_child_uniqueness_rejected_across_transactions(
    db_engine,
) -> None:
    """Persisted: a committed ``freq_on`` edge already exists for child=C
    from parent=A. A fresh transaction trying to add a second
    ``freq_on`` for the same child from parent=B must surface a clean
    422-flavoured ValueError, never hit the DB constraint."""
    from app.services.calculation_resolution import (
        add_dependency_edge_idempotent as _add_dependency_edge_idempotent,
    )

    with Session(db_engine) as session, session.begin():
        outcome_a = persist_computed_species_upload(
            session, _hydrogen_bundle_with_freq_dep()
        )
        primary_a_id = outcome_a.conformers[0].primary_calculation.id
        freq_child_id = outcome_a.conformers[0].additional_calculations[0].id

        outcome_b = persist_computed_species_upload(
            session,
            ComputedSpeciesUploadRequest(
                species_entry={
                    "smiles": "[H]",
                    "charge": 0,
                    "multiplicity": 2,
                },
                conformers=[
                    {
                        "key": "c0",
                        "geometry": dict(_H_GEOM_2),
                        "primary_calculation": _calc("opt_b", calc_type="opt"),
                    }
                ],
            ),
        )
        primary_b_id = outcome_b.conformers[0].primary_calculation.id

    # Fresh transaction — identity map empty, helper must round-trip.
    with Session(db_engine) as session, session.begin():
        before = session.scalar(
            select(func.count()).select_from(CalculationDependency)
        )
        with pytest.raises(ValueError) as exc:
            _add_dependency_edge_idempotent(
                session,
                parent_calculation_id=primary_b_id,
                child_calculation_id=freq_child_id,
                dependency_role=CalculationDependencyRole.freq_on,
                context="cross-transaction second-parent attempt",
            )
        msg = str(exc.value)
        assert "freq_on" in msg
        assert "different parent" in msg
        # Sanity: A still owns the freq_on parent slot.
        assert primary_a_id != primary_b_id
        after = session.scalar(
            select(func.count()).select_from(CalculationDependency)
        )
        assert after == before


def _opt_only_bundle(geom_xyz: str) -> ComputedSpeciesUploadRequest:
    """Single-conformer hydrogen bundle with just a primary opt — no
    additional calcs, no auto-edges. Useful when a test needs a bare
    ``Calculation`` row with no pre-existing dependency edges."""
    return ComputedSpeciesUploadRequest(
        species_entry={"smiles": "[H]", "charge": 0, "multiplicity": 2},
        conformers=[
            {
                "key": "c0",
                "geometry": {"xyz_text": geom_xyz},
                "primary_calculation": _calc("opt_only", calc_type="opt"),
            }
        ],
    )


def test_per_role_child_uniqueness_does_not_block_unrestricted_role(
    db_engine,
) -> None:
    """``arkane_source`` is not in the per-role-child uniqueness set.
    Two parents pointing to the same child with role=arkane_source must
    coexist — the helper must not erroneously reject the second.

    Uses three fresh opt calcs with no pre-existing edges so the only
    constraint under test is the per-role child uniqueness check."""
    from app.services.calculation_resolution import (
        _ONE_PARENT_PER_CHILD_ROLES,
    )
    from app.services.calculation_resolution import (
        add_dependency_edge_idempotent as _add_dependency_edge_idempotent,
    )

    # Sanity-pin the constant so a future schema tweak fails this guard.
    assert (
        CalculationDependencyRole.arkane_source
        not in _ONE_PARENT_PER_CHILD_ROLES
    )

    with Session(db_engine) as session, session.begin():
        a = persist_computed_species_upload(
            session, _opt_only_bundle("1\nH\nH 0.0 0.0 0.0")
        )
        b = persist_computed_species_upload(
            session, _opt_only_bundle("1\nH\nH 0.0 0.0 0.5")
        )
        c = persist_computed_species_upload(
            session, _opt_only_bundle("1\nH\nH 0.0 0.0 1.0")
        )
        parent_a_id = a.conformers[0].primary_calculation.id
        parent_b_id = b.conformers[0].primary_calculation.id
        child_id = c.conformers[0].primary_calculation.id

        _add_dependency_edge_idempotent(
            session,
            parent_calculation_id=parent_a_id,
            child_calculation_id=child_id,
            dependency_role=CalculationDependencyRole.arkane_source,
            context="arkane_source A",
        )
        _add_dependency_edge_idempotent(
            session,
            parent_calculation_id=parent_b_id,
            child_calculation_id=child_id,
            dependency_role=CalculationDependencyRole.arkane_source,
            context="arkane_source B",
        )
        session.flush()

        edges = session.scalars(
            select(CalculationDependency).where(
                CalculationDependency.child_calculation_id == child_id,
                CalculationDependency.dependency_role
                == CalculationDependencyRole.arkane_source,
            )
        ).all()
        assert {e.parent_calculation_id for e in edges} == {
            parent_a_id,
            parent_b_id,
        }


def test_dependency_edge_helper_idempotent_against_persisted_row(
    db_engine,
) -> None:
    """Direct exercise of the helper: a persisted DB row plus a re-insert
    attempt with the same role no-ops; with a different role raises."""
    from app.services.calculation_resolution import (
        add_dependency_edge_idempotent as _add_dependency_edge_idempotent,
    )

    with Session(db_engine) as session, session.begin():
        outcome = persist_computed_species_upload(
            session, _hydrogen_bundle_with_freq_dep()
        )
        primary_id = outcome.conformers[0].primary_calculation.id
        freq_id = outcome.conformers[0].additional_calculations[0].id

        # Same role: no-op.
        before_count = session.scalar(
            select(func.count()).select_from(CalculationDependency)
        )
        edge = _add_dependency_edge_idempotent(
            session,
            parent_calculation_id=primary_id,
            child_calculation_id=freq_id,
            dependency_role=CalculationDependencyRole.freq_on,
            context="test re-insert",
        )
        session.flush()
        after_count = session.scalar(
            select(func.count()).select_from(CalculationDependency)
        )
        assert after_count == before_count
        assert edge.dependency_role is CalculationDependencyRole.freq_on

        # Different role on same pair: raises.
        with pytest.raises(ValueError) as exc:
            _add_dependency_edge_idempotent(
                session,
                parent_calculation_id=primary_id,
                child_calculation_id=freq_id,
                dependency_role=CalculationDependencyRole.single_point_on,
                context="test conflicting re-insert",
            )
        assert "different role" in str(exc.value)


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


# ---------------------------------------------------------------------------
# Statmech block (DR follow-up: computed-species statmech parity)
# ---------------------------------------------------------------------------


def test_computed_species_statmech_block_persists_with_fsf(db_engine) -> None:
    """The computed-species bundle accepts an inline statmech block,
    resolves the unified ``FreqScaleFactorRef`` through the shared
    resolver, and links the resulting FSF row through
    ``statmech.frequency_scale_factor_id``. Local-key
    ``source_calculations`` resolve against the bundle's global
    calc-key namespace and are written as ``StatmechSourceCalculation``
    rows. No ``applied_energy_correction`` row is produced for this
    path."""
    bundle = _hydrogen_bundle(
        conformers=[
            {
                "key": "c0",
                "geometry": dict(_H_GEOM),
                "primary_calculation": _calc("opt0", calc_type="opt"),
                "additional_calculations": [
                    _calc("freq0", calc_type="freq"),
                ],
            }
        ],
        statmech={
            "scientific_origin": "computed",
            "is_linear": True,
            "external_symmetry": 1,
            "statmech_treatment": "rrho",
            "freq_scale_factor": {
                "level_of_theory": {"method": "B3LYP", "basis": "6-31G(d)"},
                "scale_kind": "fundamental",
                "value": 0.977,
                "software": {"name": "Gaussian"},
                "note": "B3LYP/6-31G(d) fundamental factor",
            },
            "source_calculations": [
                {"calculation_key": "freq0", "role": "freq"},
            ],
            "note": "computed-species statmech block",
        },
    )

    with Session(db_engine) as session, session.begin():
        user_id = _ensure_user(session, username="bundle_statmech")
        aec_before = session.scalar(
            select(func.count()).select_from(AppliedEnergyCorrection)
        )

        outcome = persist_computed_species_upload(
            session, bundle, created_by=user_id
        )

        assert outcome.statmech is not None
        statmech = session.get(Statmech, outcome.statmech.id)
        assert statmech is not None
        assert statmech.species_entry_id == outcome.species_entry_id
        assert statmech.is_linear is True
        assert statmech.external_symmetry == 1
        assert statmech.note == "computed-species statmech block"

        # FSF resolved through the unified path.
        assert statmech.frequency_scale_factor_id is not None
        fsf = session.get(FrequencyScaleFactor, statmech.frequency_scale_factor_id)
        assert fsf is not None
        assert fsf.value == 0.977
        assert fsf.scale_kind.value == "fundamental"
        assert fsf.software is not None
        assert fsf.software.name == "Gaussian"

        # Source calculation link by local key.
        links = session.scalars(
            select(StatmechSourceCalculation).where(
                StatmechSourceCalculation.statmech_id == statmech.id
            )
        ).all()
        assert len(links) == 1
        linked_calc = session.get(Calculation, links[0].calculation_id)
        assert linked_calc is not None
        assert linked_calc.type == CalculationType.freq

        # Statmech FSF use must not produce an applied_energy_correction row.
        aec_after = session.scalar(
            select(func.count()).select_from(AppliedEnergyCorrection)
        )
        assert aec_after == aec_before


def test_computed_species_statmech_source_key_must_resolve(db_engine) -> None:
    """Statmech ``source_calculations`` keys are validated against the
    bundle's global calc namespace at the schema layer; ghost keys are
    rejected before the workflow runs."""
    with pytest.raises(Exception, match="undefined calculation_key"):
        _hydrogen_bundle(
            statmech={
                "is_linear": True,
                "source_calculations": [
                    {"calculation_key": "ghost", "role": "freq"},
                ],
            }
        )


# ---------------------------------------------------------------------------
# Statmech torsion definitions in the species bundle
# ---------------------------------------------------------------------------


def _ethane_bundle_with_scan(**overrides) -> dict:
    """Ethane-shaped bundle (8 atoms) exposing opt/freq/scan calc keys.

    Atom indices used by torsion coordinate definitions therefore stay
    within 1..8, matching what ARC produces.
    """
    return {
        "species_entry": {"smiles": "CC", "charge": 0, "multiplicity": 1},
        "conformers": [
            {
                "key": "c0",
                "geometry": {
                    "xyz_text": (
                        "8\nethane\n"
                        "C 0.000 0.000 0.762\n"
                        "C 0.000 0.000 -0.762\n"
                        "H 1.018 0.000 1.157\n"
                        "H -0.509 -0.882 1.157\n"
                        "H -0.509 0.882 1.157\n"
                        "H -1.018 0.000 -1.157\n"
                        "H 0.509 0.882 -1.157\n"
                        "H 0.509 -0.882 -1.157"
                    )
                },
                "primary_calculation": _calc("opt0", calc_type="opt"),
                "additional_calculations": [
                    _calc("freq0", calc_type="freq"),
                    _calc("scan0", calc_type="scan"),
                ],
            }
        ],
        **overrides,
    }


def test_species_statmech_torsion_with_one_coordinate_persists(db_engine) -> None:
    """1D rotor with a single coordinate writes one
    ``statmech_torsion_definition`` row, atom quartet preserved 1-based."""
    bundle = ComputedSpeciesUploadRequest(
        **_ethane_bundle_with_scan(
            statmech={
                "is_linear": False,
                "statmech_treatment": "rrho",
                "torsions": [
                    {
                        "torsion_index": 1,
                        "symmetry_number": 3,
                        "treatment_kind": "hindered_rotor",
                        "dimension": 1,
                        "top_description": "CH3 about C-C",
                        "source_scan_calculation_key": "scan0",
                        "coordinates": [
                            {
                                "coordinate_index": 1,
                                "atom1_index": 5,
                                "atom2_index": 1,
                                "atom3_index": 2,
                                "atom4_index": 6,
                            }
                        ],
                    }
                ],
            }
        )
    )

    with Session(db_engine) as session, session.begin():
        user_id = _ensure_user(session, username="bundle_torsion_1d")
        outcome = persist_computed_species_upload(
            session, bundle, created_by=user_id
        )
        assert outcome.statmech is not None

        torsion = session.scalars(
            select(StatmechTorsion).where(
                StatmechTorsion.statmech_id == outcome.statmech.id
            )
        ).one()
        assert torsion.dimension == 1
        assert torsion.top_description == "CH3 about C-C"
        assert torsion.source_scan_calculation_id is not None

        coords = session.scalars(
            select(StatmechTorsionDefinition).where(
                StatmechTorsionDefinition.torsion_id == torsion.id
            )
        ).all()
        assert len(coords) == 1
        c = coords[0]
        assert c.coordinate_index == 1
        assert (c.atom1_index, c.atom2_index, c.atom3_index, c.atom4_index) == (
            5,
            1,
            2,
            6,
        )

        scan_calc = session.get(Calculation, torsion.source_scan_calculation_id)
        assert scan_calc.type == CalculationType.scan


def test_species_statmech_torsion_without_coordinates_writes_no_definitions(
    db_engine,
) -> None:
    """Producers may omit ``coordinates``: the torsion row is created
    but no ``statmech_torsion_definition`` rows are written. This
    matches behavior prior to the coordinate plumbing."""
    bundle = ComputedSpeciesUploadRequest(
        **_ethane_bundle_with_scan(
            statmech={
                "is_linear": False,
                "torsions": [
                    {"torsion_index": 1, "symmetry_number": 3},
                ],
            }
        )
    )

    with Session(db_engine) as session, session.begin():
        user_id = _ensure_user(session, username="bundle_torsion_nocoords")
        outcome = persist_computed_species_upload(
            session, bundle, created_by=user_id
        )
        torsion = session.scalars(
            select(StatmechTorsion).where(
                StatmechTorsion.statmech_id == outcome.statmech.id
            )
        ).one()
        assert torsion.dimension == 1
        assert torsion.source_scan_calculation_id is None

        coord_count = session.scalar(
            select(func.count())
            .select_from(StatmechTorsionDefinition)
            .where(StatmechTorsionDefinition.torsion_id == torsion.id)
        )
        assert coord_count == 0


def test_species_statmech_torsion_dimension_two_persists_two_definitions(
    db_engine,
) -> None:
    """A 2D coupled rotor with two coordinates writes two definition rows."""
    bundle = ComputedSpeciesUploadRequest(
        **_ethane_bundle_with_scan(
            statmech={
                "is_linear": False,
                "torsions": [
                    {
                        "torsion_index": 1,
                        "dimension": 2,
                        "coordinates": [
                            {
                                "coordinate_index": 1,
                                "atom1_index": 5,
                                "atom2_index": 1,
                                "atom3_index": 2,
                                "atom4_index": 6,
                            },
                            {
                                "coordinate_index": 2,
                                "atom1_index": 4,
                                "atom2_index": 1,
                                "atom3_index": 2,
                                "atom4_index": 7,
                            },
                        ],
                    }
                ],
            }
        )
    )
    with Session(db_engine) as session, session.begin():
        user_id = _ensure_user(session, username="bundle_torsion_2d")
        outcome = persist_computed_species_upload(
            session, bundle, created_by=user_id
        )
        torsion = session.scalars(
            select(StatmechTorsion).where(
                StatmechTorsion.statmech_id == outcome.statmech.id
            )
        ).one()
        assert torsion.dimension == 2
        coords = session.scalars(
            select(StatmechTorsionDefinition).where(
                StatmechTorsionDefinition.torsion_id == torsion.id
            )
        ).all()
        assert {c.coordinate_index for c in coords} == {1, 2}


def test_species_statmech_torsion_coords_length_must_match_dimension() -> None:
    with pytest.raises(Exception, match="must equal dimension"):
        ComputedSpeciesUploadRequest(
            **_ethane_bundle_with_scan(
                statmech={
                    "is_linear": False,
                    "torsions": [
                        {
                            "torsion_index": 1,
                            "dimension": 2,
                            "coordinates": [
                                {
                                    "coordinate_index": 1,
                                    "atom1_index": 5,
                                    "atom2_index": 1,
                                    "atom3_index": 2,
                                    "atom4_index": 6,
                                }
                            ],
                        }
                    ],
                }
            )
        )


def test_species_statmech_torsion_duplicate_coordinate_index_rejected() -> None:
    with pytest.raises(Exception, match="coordinate_index"):
        ComputedSpeciesUploadRequest(
            **_ethane_bundle_with_scan(
                statmech={
                    "is_linear": False,
                    "torsions": [
                        {
                            "torsion_index": 1,
                            "dimension": 2,
                            "coordinates": [
                                {
                                    "coordinate_index": 1,
                                    "atom1_index": 5,
                                    "atom2_index": 1,
                                    "atom3_index": 2,
                                    "atom4_index": 6,
                                },
                                {
                                    "coordinate_index": 1,
                                    "atom1_index": 4,
                                    "atom2_index": 1,
                                    "atom3_index": 2,
                                    "atom4_index": 7,
                                },
                            ],
                        }
                    ],
                }
            )
        )


def test_species_statmech_torsion_scan_key_missing_rejected() -> None:
    with pytest.raises(Exception, match="undefined calculation_key"):
        ComputedSpeciesUploadRequest(
            **_ethane_bundle_with_scan(
                statmech={
                    "is_linear": False,
                    "torsions": [
                        {
                            "torsion_index": 1,
                            "source_scan_calculation_key": "ghost",
                        }
                    ],
                }
            )
        )


def test_species_statmech_torsion_scan_key_must_be_scan_type() -> None:
    """A ``source_scan_calculation_key`` referencing a non-scan calc
    (e.g. type=freq) is rejected at the schema layer."""
    with pytest.raises(Exception, match="must reference a scan-type calculation"):
        ComputedSpeciesUploadRequest(
            **_ethane_bundle_with_scan(
                statmech={
                    "is_linear": False,
                    "torsions": [
                        {
                            "torsion_index": 1,
                            "source_scan_calculation_key": "freq0",
                        }
                    ],
                }
            )
        )


# ---------------------------------------------------------------------------
# Scan result persistence on bundle calculations
# ---------------------------------------------------------------------------


def _ethane_scan_result_payload(
    *,
    points: int = 3,
) -> dict:
    """A 1D dihedral scan result with ``points`` scan points in degrees.

    The dihedral runs over atoms 5-1-2-6 of the ethane fixture (CH3 about
    the C-C bond), matching the torsion definition used elsewhere.
    """
    coordinate_values = [0.0 + i * 30.0 for i in range(points)]
    return {
        "dimension": 1,
        "is_relaxed": True,
        "coordinates": [
            {
                "coordinate_index": 1,
                "coordinate_kind": "dihedral",
                "atom1_index": 5,
                "atom2_index": 1,
                "atom3_index": 2,
                "atom4_index": 6,
                "step_count": points,
                "step_size": 30.0,
                "start_value": 0.0,
                "end_value": 30.0 * (points - 1),
                "value_unit": "degree",
                "resolution_degrees": 30.0,
                "symmetry_number": 3,
            }
        ],
        "points": [
            {
                "point_index": i + 1,
                "electronic_energy_hartree": -79.5 - 1e-4 * i,
                "relative_energy_kj_mol": float(i),
                "coordinate_values": [
                    {
                        "coordinate_index": 1,
                        "coordinate_value": coordinate_values[i],
                        "value_unit": "degree",
                    }
                ],
            }
            for i in range(points)
        ],
    }


def test_bundle_scan_calculation_persists_scan_result_rows(db_engine) -> None:
    """A bundle with a type=scan additional calc carrying scan_result
    persists rows in calc_scan_result, calc_scan_coordinate,
    calc_scan_point, and calc_scan_point_coordinate_value."""
    payload = _ethane_bundle_with_scan()
    # Locate the scan calc in the fixture and attach a scan_result.
    additional = payload["conformers"][0]["additional_calculations"]
    scan_idx = next(i for i, c in enumerate(additional) if c["type"] == "scan")
    additional[scan_idx] = {
        **additional[scan_idx],
        "scan_result": _ethane_scan_result_payload(points=3),
    }
    bundle = ComputedSpeciesUploadRequest(**payload)

    with Session(db_engine) as session, session.begin():
        user_id = _ensure_user(session, username="bundle_scan_result")
        outcome = persist_computed_species_upload(
            session, bundle, created_by=user_id
        )

        scan_calc = next(
            c
            for c in outcome.conformers[0].additional_calculations
            if c.type == CalculationType.scan
        )
        result = session.scalars(
            select(CalculationScanResult).where(
                CalculationScanResult.calculation_id == scan_calc.id
            )
        ).one()
        assert result.dimension == 1
        assert result.is_relaxed is True

        coords = session.scalars(
            select(CalculationScanCoordinate).where(
                CalculationScanCoordinate.calculation_id == scan_calc.id
            )
        ).all()
        assert len(coords) == 1
        assert coords[0].coordinate_kind == "dihedral"
        assert (
            coords[0].atom1_index,
            coords[0].atom2_index,
            coords[0].atom3_index,
            coords[0].atom4_index,
        ) == (5, 1, 2, 6)

        points = session.scalars(
            select(CalculationScanPoint)
            .where(CalculationScanPoint.calculation_id == scan_calc.id)
            .order_by(CalculationScanPoint.point_index)
        ).all()
        assert [p.point_index for p in points] == [1, 2, 3]

        values = session.scalars(
            select(CalculationScanPointCoordinateValue)
            .where(
                CalculationScanPointCoordinateValue.calculation_id == scan_calc.id
            )
            .order_by(CalculationScanPointCoordinateValue.point_index)
        ).all()
        assert len(values) == 3
        assert [v.coordinate_value for v in values] == [0.0, 30.0, 60.0]


def test_bundle_scan_inline_point_geometry_populates_geometry_id(db_engine) -> None:
    """A scan_result whose points carry inline ``geometry`` payloads is
    resolved through ``resolve_geometry_payload`` and the resolved IDs land
    on ``calc_scan_point.geometry_id``."""
    payload = _ethane_bundle_with_scan()
    additional = payload["conformers"][0]["additional_calculations"]
    scan_idx = next(i for i, c in enumerate(additional) if c["type"] == "scan")
    scan_result = _ethane_scan_result_payload(points=3)
    for i, point in enumerate(scan_result["points"], start=1):
        point["geometry"] = {
            "xyz_text": f"1\nscan-pt-{i}\nH 0.0 0.0 {0.10 * i:.3f}",
        }
    additional[scan_idx] = {**additional[scan_idx], "scan_result": scan_result}
    bundle = ComputedSpeciesUploadRequest(**payload)

    with Session(db_engine) as session, session.begin():
        user_id = _ensure_user(session, username="bundle_scan_inline_geom")
        outcome = persist_computed_species_upload(
            session, bundle, created_by=user_id
        )
        scan_calc = next(
            c
            for c in outcome.conformers[0].additional_calculations
            if c.type == CalculationType.scan
        )
        points = session.scalars(
            select(CalculationScanPoint)
            .where(CalculationScanPoint.calculation_id == scan_calc.id)
            .order_by(CalculationScanPoint.point_index)
        ).all()
        assert [p.point_index for p in points] == [1, 2, 3]
        assert all(p.geometry_id is not None for p in points)
        assert len({p.geometry_id for p in points}) == 3


def test_bundle_scan_result_on_non_scan_calc_rejected() -> None:
    """``scan_result`` on a calc whose type is not 'scan' is rejected at
    the schema layer (422 surface)."""
    payload = _ethane_bundle_with_scan()
    additional = payload["conformers"][0]["additional_calculations"]
    freq_idx = next(i for i, c in enumerate(additional) if c["type"] == "freq")
    additional[freq_idx] = {
        **additional[freq_idx],
        "scan_result": _ethane_scan_result_payload(points=2),
    }
    with pytest.raises(Exception, match="not allowed for calculation type 'freq'"):
        ComputedSpeciesUploadRequest(**payload)


def test_bundle_scan_calc_rejects_non_scan_result_blocks() -> None:
    """A ``type=scan`` calc carrying ``freq_result`` (or any other
    non-scan result block) is rejected at the schema layer."""
    payload = _ethane_bundle_with_scan()
    additional = payload["conformers"][0]["additional_calculations"]
    scan_idx = next(i for i, c in enumerate(additional) if c["type"] == "scan")
    additional[scan_idx] = {
        **additional[scan_idx],
        "freq_result": {"n_imag": 0},
    }
    with pytest.raises(Exception, match="not allowed for calculation type 'scan'"):
        ComputedSpeciesUploadRequest(**payload)


def test_bundle_torsion_resolves_to_scan_calc_with_scan_result(db_engine) -> None:
    """A statmech torsion's ``source_scan_calculation_key`` resolves to a
    bundle-local type=scan calc that itself carries a ``scan_result`` —
    the torsion's ``source_scan_calculation_id`` points at that calc."""
    payload = _ethane_bundle_with_scan()
    additional = payload["conformers"][0]["additional_calculations"]
    scan_idx = next(i for i, c in enumerate(additional) if c["type"] == "scan")
    additional[scan_idx] = {
        **additional[scan_idx],
        "scan_result": _ethane_scan_result_payload(points=2),
    }
    payload["statmech"] = {
        "is_linear": False,
        "torsions": [
            {
                "torsion_index": 1,
                "symmetry_number": 3,
                "treatment_kind": "hindered_rotor",
                "dimension": 1,
                "source_scan_calculation_key": "scan0",
            }
        ],
    }
    bundle = ComputedSpeciesUploadRequest(**payload)

    with Session(db_engine) as session, session.begin():
        _ensure_user(session, username="bundle_torsion_scan_result")
        outcome = persist_computed_species_upload(session, bundle)

        scan_calc = next(
            c
            for c in outcome.conformers[0].additional_calculations
            if c.type == CalculationType.scan
        )
        torsion = session.scalars(
            select(StatmechTorsion).where(
                StatmechTorsion.statmech_id == outcome.statmech.id
            )
        ).one()
        assert torsion.source_scan_calculation_id == scan_calc.id

        result = session.scalars(
            select(CalculationScanResult).where(
                CalculationScanResult.calculation_id == scan_calc.id
            )
        ).one()
        assert result.dimension == 1


# ---------------------------------------------------------------------------
# Top-level applied energy corrections (AEC/BAC) on the species bundle
# ---------------------------------------------------------------------------


_LOT_AEC = {"method": "B3LYP", "basis": "6-31G(d)"}


def _aec_scheme_ref(**overrides) -> dict:
    """An atom_energy scheme reference suitable for aec_total."""
    base: dict = {
        "kind": "atom_energy",
        "name": "AEC v1",
        "level_of_theory": dict(_LOT_AEC),
        "version": "1.0",
        "units": "hartree",
    }
    base.update(overrides)
    return base


def _bac_petersson_scheme_ref(**overrides) -> dict:
    base: dict = {
        "kind": "bac_petersson",
        "name": "Petersson BAC v1",
        "level_of_theory": dict(_LOT_AEC),
        "version": "1.0",
        "units": "hartree",
    }
    base.update(overrides)
    return base


def _bac_melius_scheme_ref(**overrides) -> dict:
    base: dict = {
        "kind": "bac_melius",
        "name": "Melius BAC v1",
        "level_of_theory": dict(_LOT_AEC),
        "version": "1.0",
        "units": "hartree",
    }
    base.update(overrides)
    return base


_METHANE_GEOM = {
    "xyz_text": (
        "5\nmethane\n"
        "C 0.0 0.0 0.0\n"
        "H 0.629 0.629 0.629\n"
        "H -0.629 -0.629 0.629\n"
        "H -0.629 0.629 -0.629\n"
        "H 0.629 -0.629 -0.629"
    )
}


def _bundle_with_sp_calc(*, smiles: str, **overrides) -> dict:
    """Bundle template that exposes a 'sp0' calc key for source linking.

    A distinct ``smiles`` per test is required because ``db_engine`` is
    session-scoped and writes commit between tests; reusing the same
    species across tests would accumulate ``applied_energy_correction``
    rows that target the same species entry.
    """
    return {
        "species_entry": {"smiles": smiles, "charge": 0, "multiplicity": 1},
        "conformers": [
            {
                "key": "c0",
                "geometry": dict(_METHANE_GEOM),
                "primary_calculation": _calc("opt0", calc_type="opt"),
                "additional_calculations": [
                    _calc("sp0", calc_type="sp"),
                ],
            }
        ],
        **overrides,
    }


def test_aec_total_no_components_persists(db_engine) -> None:
    """Spec test 1: AEC applied correction persists with scheme and no components."""
    bundle = ComputedSpeciesUploadRequest(
        **_bundle_with_sp_calc(
            smiles="FC",
            applied_energy_corrections=[
                {
                    "scheme": _aec_scheme_ref(),
                    "application_role": "aec_total",
                    "value": -0.123,
                    "value_unit": "hartree",
                    "source_calculation_key": "sp0",
                }
            ]
        )
    )
    with Session(db_engine) as session, session.begin():
        outcome = persist_computed_species_upload(session, bundle)
        applied = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id
                == outcome.species_entry_id
            )
        ).all()
        assert len(applied) == 1
        ac = applied[0]
        assert ac.application_role.value == "aec_total"
        assert ac.scheme_id is not None
        assert ac.frequency_scale_factor_id is None
        assert ac.value == -0.123
        assert ac.source_calculation_id is not None
        # No components for this row.
        comps = session.scalars(
            select(AppliedEnergyCorrectionComponent).where(
                AppliedEnergyCorrectionComponent.applied_correction_id == ac.id
            )
        ).all()
        assert comps == []


def test_aec_total_with_atom_components_persists(db_engine) -> None:
    """Spec test 2: AEC applied correction persists with atom components."""
    bundle = ComputedSpeciesUploadRequest(
        **_bundle_with_sp_calc(
            smiles="FCC",
            applied_energy_corrections=[
                {
                    "scheme": _aec_scheme_ref(
                        name="AEC atom-comps test",
                        atom_params=[
                            {"element": "H", "value": -0.5},
                        ],
                    ),
                    "application_role": "aec_total",
                    "value": -0.5,
                    "value_unit": "hartree",
                    "source_calculation_key": "sp0",
                    "components": [
                        {
                            "component_kind": "atom",
                            "key": "H",
                            "multiplicity": 1,
                            "parameter_value": -0.5,
                            "contribution_value": -0.5,
                        }
                    ],
                }
            ]
        )
    )
    with Session(db_engine) as session, session.begin():
        outcome = persist_computed_species_upload(session, bundle)
        ac = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id
                == outcome.species_entry_id
            )
        ).one()
        comps = session.scalars(
            select(AppliedEnergyCorrectionComponent).where(
                AppliedEnergyCorrectionComponent.applied_correction_id == ac.id
            )
        ).all()
        assert len(comps) == 1
        assert comps[0].component_kind.value == "atom"
        assert comps[0].key == "H"
        assert comps[0].contribution_value == -0.5
        # Scheme atom params persisted.
        atom_params = session.scalars(
            select(EnergyCorrectionSchemeAtomParam).where(
                EnergyCorrectionSchemeAtomParam.scheme_id == ac.scheme_id
            )
        ).all()
        assert {p.element for p in atom_params} == {"H"}


def test_bac_petersson_with_bond_components_persists(db_engine) -> None:
    """Spec test 3: Petersson BAC persists with bond components."""
    bundle = ComputedSpeciesUploadRequest(
        **_bundle_with_sp_calc(
            smiles="FCCC",
            applied_energy_corrections=[
                {
                    "scheme": _bac_petersson_scheme_ref(
                        name="Petersson bond-comps test",
                        bond_params=[
                            {"bond_key": "C-H", "value": -0.11},
                        ],
                    ),
                    "application_role": "bac_total",
                    "value": -0.66,
                    "value_unit": "hartree",
                    "source_calculation_key": "sp0",
                    "components": [
                        {
                            "component_kind": "bond",
                            "key": "C-H",
                            "multiplicity": 6,
                            "parameter_value": -0.11,
                            "contribution_value": -0.66,
                        }
                    ],
                }
            ]
        )
    )
    with Session(db_engine) as session, session.begin():
        outcome = persist_computed_species_upload(session, bundle)
        ac = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id
                == outcome.species_entry_id
            )
        ).one()
        assert ac.application_role.value == "bac_total"
        scheme = session.get(EnergyCorrectionScheme, ac.scheme_id)
        assert scheme.kind.value == "bac_petersson"
        comps = session.scalars(
            select(AppliedEnergyCorrectionComponent).where(
                AppliedEnergyCorrectionComponent.applied_correction_id == ac.id
            )
        ).all()
        assert len(comps) == 1
        assert comps[0].component_kind.value == "bond"
        assert comps[0].multiplicity == 6
        # Scheme bond params persisted.
        bond_params = session.scalars(
            select(EnergyCorrectionSchemeBondParam).where(
                EnergyCorrectionSchemeBondParam.scheme_id == ac.scheme_id
            )
        ).all()
        assert {p.bond_key for p in bond_params} == {"C-H"}


def test_bac_melius_no_components_persists(db_engine) -> None:
    """Spec test 4: Melius BAC persists with no components.

    Melius BAC totals are scientifically meaningful but lack a stable
    simple decomposition, so producers may submit just the total.
    """
    bundle = ComputedSpeciesUploadRequest(
        **_bundle_with_sp_calc(
            smiles="FCCCC",
            applied_energy_corrections=[
                {
                    "scheme": _bac_melius_scheme_ref(),
                    "application_role": "bac_total",
                    "value": -0.0421,
                    "value_unit": "hartree",
                    "source_calculation_key": "sp0",
                }
            ]
        )
    )
    with Session(db_engine) as session, session.begin():
        outcome = persist_computed_species_upload(session, bundle)
        ac = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id
                == outcome.species_entry_id
            )
        ).one()
        scheme = session.get(EnergyCorrectionScheme, ac.scheme_id)
        assert scheme.kind.value == "bac_melius"
        comps = session.scalars(
            select(AppliedEnergyCorrectionComponent).where(
                AppliedEnergyCorrectionComponent.applied_correction_id == ac.id
            )
        ).all()
        assert comps == []


def test_source_calculation_key_resolves_by_local_key(db_engine) -> None:
    """Spec test 5: source_calculation_key resolves by local key.

    The applied correction row's ``source_calculation_id`` must match
    the calculation persisted for the bundle-local key.
    """
    bundle = ComputedSpeciesUploadRequest(
        **_bundle_with_sp_calc(
            smiles="CCCCCC",
            applied_energy_corrections=[
                {
                    "scheme": _aec_scheme_ref(),
                    "application_role": "aec_total",
                    "value": -0.123,
                    "value_unit": "hartree",
                    "source_calculation_key": "sp0",
                }
            ]
        )
    )
    with Session(db_engine) as session, session.begin():
        outcome = persist_computed_species_upload(session, bundle)
        sp_calc = next(
            c
            for c in outcome.conformers[0].additional_calculations
            if c.type == CalculationType.sp
        )
        ac = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id
                == outcome.species_entry_id
            )
        ).one()
        assert ac.source_calculation_id == sp_calc.id


def test_undefined_source_calculation_key_returns_422() -> None:
    """Spec test 6: undefined source_calculation_key returns 422 (schema-level)."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="undefined calculation_key"):
        ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="CCCCCCC",
                applied_energy_corrections=[
                    {
                        "scheme": _aec_scheme_ref(),
                        "application_role": "aec_total",
                        "value": -0.1,
                        "value_unit": "hartree",
                        "source_calculation_key": "ghost",
                    }
                ]
            )
        )


def test_aec_total_with_non_atom_energy_scheme_returns_422() -> None:
    """Spec test 7: aec_total with non-atom-energy scheme returns 422."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="aec_total.*atom_energy"):
        ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="CCCCCCCC",
                applied_energy_corrections=[
                    {
                        "scheme": _bac_petersson_scheme_ref(),  # wrong kind
                        "application_role": "aec_total",
                        "value": -0.1,
                        "value_unit": "hartree",
                        "source_calculation_key": "sp0",
                    }
                ]
            )
        )


def test_bac_total_with_non_bac_scheme_returns_422() -> None:
    """Spec test 8: bac_total with non-BAC scheme returns 422."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="bac_total.*bac_"):
        ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="CCCCCCCCC",
                applied_energy_corrections=[
                    {
                        "scheme": _aec_scheme_ref(),  # wrong kind
                        "application_role": "bac_total",
                        "value": -0.1,
                        "value_unit": "hartree",
                        "source_calculation_key": "sp0",
                    }
                ]
            )
        )


def test_repeated_upload_reuses_scheme_row(db_engine) -> None:
    """Spec test 9: repeated upload with same scheme identity reuses the row."""
    payload = {
        "applied_energy_corrections": [
            {
                "scheme": _aec_scheme_ref(),
                "application_role": "aec_total",
                "value": -0.1,
                "value_unit": "hartree",
                "source_calculation_key": "sp0",
            }
        ]
    }
    with Session(db_engine) as session, session.begin():
        bundle_a = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(smiles="CCCCCCCCCC", **payload)
        )
        outcome_a = persist_computed_species_upload(session, bundle_a)
        ac_a = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id
                == outcome_a.species_entry_id
            )
        ).one()
        scheme_id_a = ac_a.scheme_id

        # Different species, same scheme identity.
        bundle_b = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="CCCCCCCCCCC",
                applied_energy_corrections=[
                    {
                        "scheme": _aec_scheme_ref(),
                        "application_role": "aec_total",
                        "value": -0.2,
                        "value_unit": "hartree",
                        "source_calculation_key": "sp0",
                    }
                ],
            )
        )
        outcome_b = persist_computed_species_upload(session, bundle_b)
        ac_b = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id
                == outcome_b.species_entry_id
            )
        ).one()
        assert ac_b.scheme_id == scheme_id_a, (
            "scheme dedup must reuse existing row by (kind, name, lot, version)."
        )
        # Exactly one scheme row in the DB.
        assert (
            session.scalar(
                select(func.count()).select_from(EnergyCorrectionScheme).where(
                    EnergyCorrectionScheme.id == scheme_id_a
                )
            )
            == 1
        )


def test_note_does_not_affect_scheme_identity(db_engine) -> None:
    """Spec test 10: note does not affect scheme identity.

    Two uploads with the same identity tuple but different ``note`` text
    must reuse the existing scheme row; the second note is silently
    ignored, mirroring FrequencyScaleFactor's note semantics.
    """
    scheme_name = "AEC note-identity test"
    with Session(db_engine) as session, session.begin():
        bundle_a = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="CCCCCCCCCCCC",
                applied_energy_corrections=[
                    {
                        "scheme": _aec_scheme_ref(
                            name=scheme_name, note="first run"
                        ),
                        "application_role": "aec_total",
                        "value": -0.1,
                        "value_unit": "hartree",
                        "source_calculation_key": "sp0",
                    }
                ],
            )
        )
        outcome_a = persist_computed_species_upload(session, bundle_a)
        scheme_id_a = session.scalars(
            select(AppliedEnergyCorrection.scheme_id).where(
                AppliedEnergyCorrection.target_species_entry_id
                == outcome_a.species_entry_id
            )
        ).one()

        # Same identity, different note — different bundle (different species).
        bundle_b = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="CCCCCCCCCCCCC",
                applied_energy_corrections=[
                    {
                        "scheme": _aec_scheme_ref(
                            name=scheme_name, note="totally different note"
                        ),
                        "application_role": "aec_total",
                        "value": -0.2,
                        "value_unit": "hartree",
                        "source_calculation_key": "sp0",
                    }
                ],
            )
        )
        outcome_b = persist_computed_species_upload(session, bundle_b)
        scheme_id_b = session.scalars(
            select(AppliedEnergyCorrection.scheme_id).where(
                AppliedEnergyCorrection.target_species_entry_id
                == outcome_b.species_entry_id
            )
        ).one()
        assert scheme_id_a == scheme_id_b
        # Note remains the first-write value (FSF-style semantics).
        scheme = session.get(EnergyCorrectionScheme, scheme_id_a)
        assert scheme.note == "first run"


def test_aec_bac_does_not_create_frequency_scale_factor_row(db_engine) -> None:
    """Spec test 11: no frequency_scale_factor row is created for AEC/BAC."""
    with Session(db_engine) as session, session.begin():
        before = session.scalar(
            select(func.count()).select_from(FrequencyScaleFactor)
        )
        bundle = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="CCCCCCCCCCCCCC",
                applied_energy_corrections=[
                    {
                        "scheme": _aec_scheme_ref(),
                        "application_role": "aec_total",
                        "value": -0.1,
                        "value_unit": "hartree",
                        "source_calculation_key": "sp0",
                    },
                    {
                        "scheme": _bac_melius_scheme_ref(),
                        "application_role": "bac_total",
                        "value": -0.05,
                        "value_unit": "hartree",
                        "source_calculation_key": "sp0",
                    },
                ]
            )
        )
        persist_computed_species_upload(session, bundle)
        after = session.scalar(
            select(func.count()).select_from(FrequencyScaleFactor)
        )
        assert after == before, (
            "AEC/BAC paths must not produce frequency_scale_factor rows."
        )


def test_bundle_payload_rejects_db_ids() -> None:
    """Spec test 12: no DB IDs are accepted in the bundle payload.

    Two distinct rejection paths:
      (a) ``extra='forbid'`` in SchemaBase rejects an unknown field
          (e.g. ``scheme_id``) at any model boundary.
      (b) The recursive forbidden-fields walk catches FK ids buried in
          the opaque ``parameters_json`` dict of any inline calculation.
    """
    from pydantic import ValidationError

    # (a) Unknown FK id at the AppliedEnergyCorrection boundary.
    with pytest.raises(ValidationError):
        ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="CCCCCCCCCCCCCCC",
                applied_energy_corrections=[
                    {
                        "scheme": _aec_scheme_ref(),
                        "application_role": "aec_total",
                        "value": -0.1,
                        "value_unit": "hartree",
                        "source_calculation_key": "sp0",
                        # Unknown fields rejected by extra='forbid'.
                        "scheme_id": 42,
                    }
                ]
            )
        )

    # (b) FK id buried in parameters_json on an inline calc.
    with pytest.raises(ValidationError, match="must not include database"):
        ComputedSpeciesUploadRequest(
            species_entry={"smiles": "[H]", "charge": 0, "multiplicity": 2},
            conformers=[
                {
                    "key": "c0",
                    "geometry": dict(_H_GEOM),
                    "primary_calculation": {
                        **_calc("opt0", calc_type="opt"),
                        "parameters_json": {
                            "nested": {"existing_calculation_id": 99},
                        },
                    },
                }
            ],
        )


# ---------------------------------------------------------------------------
# Energy correction scheme parameter persistence
# ---------------------------------------------------------------------------


def _ac_with_scheme(scheme: dict, *, application_role: str, value: float) -> dict:
    return {
        "scheme": scheme,
        "application_role": application_role,
        "value": value,
        "value_unit": "hartree",
        "source_calculation_key": "sp0",
    }


def test_aec_scheme_atom_params_persist(db_engine) -> None:
    """Spec 1: AEC scheme atom_params populate the atom_param table."""
    bundle = ComputedSpeciesUploadRequest(
        **_bundle_with_sp_calc(
            smiles="BrCO",
            applied_energy_corrections=[
                _ac_with_scheme(
                    _aec_scheme_ref(
                        name="AEC params persist",
                        atom_params=[
                            {"element": "H", "value": -0.5},
                            {"element": "C", "value": -37.7},
                            {"element": "O", "value": -74.9},
                        ],
                    ),
                    application_role="aec_total",
                    value=-0.123,
                )
            ],
        )
    )
    with Session(db_engine) as session, session.begin():
        outcome = persist_computed_species_upload(session, bundle)
        ac = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id
                == outcome.species_entry_id
            )
        ).one()
        rows = session.scalars(
            select(EnergyCorrectionSchemeAtomParam).where(
                EnergyCorrectionSchemeAtomParam.scheme_id == ac.scheme_id
            )
        ).all()
        assert {(r.element, r.value) for r in rows} == {
            ("H", -0.5),
            ("C", -37.7),
            ("O", -74.9),
        }


def test_pbac_scheme_bond_params_persist(db_engine) -> None:
    """Spec 2: Petersson BAC scheme bond_params populate the bond_param table."""
    bundle = ComputedSpeciesUploadRequest(
        **_bundle_with_sp_calc(
            smiles="BrCN",
            applied_energy_corrections=[
                _ac_with_scheme(
                    _bac_petersson_scheme_ref(
                        name="PBAC params persist",
                        bond_params=[
                            {"bond_key": "C-H", "value": -0.11},
                            {"bond_key": "C-C", "value": -0.13},
                            {"bond_key": "C-N", "value": -0.27},
                        ],
                    ),
                    application_role="bac_total",
                    value=-0.42,
                )
            ],
        )
    )
    with Session(db_engine) as session, session.begin():
        outcome = persist_computed_species_upload(session, bundle)
        ac = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id
                == outcome.species_entry_id
            )
        ).one()
        rows = session.scalars(
            select(EnergyCorrectionSchemeBondParam).where(
                EnergyCorrectionSchemeBondParam.scheme_id == ac.scheme_id
            )
        ).all()
        assert {(r.bond_key, r.value) for r in rows} == {
            ("C-H", -0.11),
            ("C-C", -0.13),
            ("C-N", -0.27),
        }


def test_melius_scheme_component_params_persist(db_engine) -> None:
    """Spec 3: Melius BAC scheme component_params populate the table."""
    bundle = ComputedSpeciesUploadRequest(
        **_bundle_with_sp_calc(
            smiles="BrCCN",
            applied_energy_corrections=[
                _ac_with_scheme(
                    _bac_melius_scheme_ref(
                        name="Melius params persist",
                        component_params=[
                            {"component_kind": "atom_corr", "key": "C", "value": -0.001},
                            {"component_kind": "bond_corr_length", "key": "C-H", "value": 0.04},
                            {"component_kind": "mol_corr", "key": "global", "value": -0.002},
                        ],
                    ),
                    application_role="bac_total",
                    value=-0.04,
                )
            ],
        )
    )
    with Session(db_engine) as session, session.begin():
        outcome = persist_computed_species_upload(session, bundle)
        ac = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id
                == outcome.species_entry_id
            )
        ).one()
        rows = session.scalars(
            select(EnergyCorrectionSchemeComponentParam).where(
                EnergyCorrectionSchemeComponentParam.scheme_id == ac.scheme_id
            )
        ).all()
        assert {(r.component_kind.value, r.key, r.value) for r in rows} == {
            ("atom_corr", "C", -0.001),
            ("bond_corr_length", "C-H", 0.04),
            ("mol_corr", "global", -0.002),
        }


def test_repeated_scheme_param_upload_is_idempotent(db_engine) -> None:
    """Spec 4: re-uploading the same scheme + params is a no-op."""
    name = "AEC idempotency"
    atom_params = [{"element": "H", "value": -0.5}, {"element": "C", "value": -37.7}]
    with Session(db_engine) as session, session.begin():
        bundle_a = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="BrCCCN",
                applied_energy_corrections=[
                    _ac_with_scheme(
                        _aec_scheme_ref(name=name, atom_params=atom_params),
                        application_role="aec_total",
                        value=-0.1,
                    )
                ],
            )
        )
        outcome_a = persist_computed_species_upload(session, bundle_a)
        ac_a = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id
                == outcome_a.species_entry_id
            )
        ).one()
        scheme_id = ac_a.scheme_id

        bundle_b = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="BrCCCCN",
                applied_energy_corrections=[
                    _ac_with_scheme(
                        _aec_scheme_ref(name=name, atom_params=atom_params),
                        application_role="aec_total",
                        value=-0.1,
                    )
                ],
            )
        )
        persist_computed_species_upload(session, bundle_b)

        rows = session.scalars(
            select(EnergyCorrectionSchemeAtomParam).where(
                EnergyCorrectionSchemeAtomParam.scheme_id == scheme_id
            )
        ).all()
        assert {(r.element, r.value) for r in rows} == {("H", -0.5), ("C", -37.7)}


def test_conflicting_atom_param_value_raises(db_engine) -> None:
    """Spec 5: same atom key with a different value raises ValueError (→ 422)."""
    name = "AEC atom conflict"
    with Session(db_engine) as session, session.begin():
        bundle_a = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="BrCCCCCN",
                applied_energy_corrections=[
                    _ac_with_scheme(
                        _aec_scheme_ref(
                            name=name,
                            atom_params=[{"element": "H", "value": -0.5}],
                        ),
                        application_role="aec_total",
                        value=-0.1,
                    )
                ],
            )
        )
        persist_computed_species_upload(session, bundle_a)

    with Session(db_engine) as session, session.begin():
        bundle_b = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="BrCCCCCCN",
                applied_energy_corrections=[
                    _ac_with_scheme(
                        _aec_scheme_ref(
                            name=name,
                            atom_params=[{"element": "H", "value": -0.6}],
                        ),
                        application_role="aec_total",
                        value=-0.1,
                    )
                ],
            )
        )
        with pytest.raises(
            ValueError,
            match=r"energy_correction_scheme_atom_param.*key='H'",
        ):
            persist_computed_species_upload(session, bundle_b)


def test_conflicting_bond_param_value_raises(db_engine) -> None:
    """Spec 6: same bond key with a different value raises ValueError (→ 422)."""
    name = "PBAC bond conflict"
    with Session(db_engine) as session, session.begin():
        bundle_a = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="BrC=CC",
                applied_energy_corrections=[
                    _ac_with_scheme(
                        _bac_petersson_scheme_ref(
                            name=name,
                            bond_params=[{"bond_key": "C-H", "value": -0.11}],
                        ),
                        application_role="bac_total",
                        value=-0.1,
                    )
                ],
            )
        )
        persist_computed_species_upload(session, bundle_a)

    with Session(db_engine) as session, session.begin():
        bundle_b = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="BrC=CCC",
                applied_energy_corrections=[
                    _ac_with_scheme(
                        _bac_petersson_scheme_ref(
                            name=name,
                            bond_params=[{"bond_key": "C-H", "value": -0.22}],
                        ),
                        application_role="bac_total",
                        value=-0.1,
                    )
                ],
            )
        )
        with pytest.raises(
            ValueError,
            match=r"energy_correction_scheme_bond_param.*key='C-H'",
        ):
            persist_computed_species_upload(session, bundle_b)


def test_conflicting_component_param_value_raises(db_engine) -> None:
    """Bonus: same component (kind, key) with different value raises (→ 422)."""
    name = "Melius component conflict"
    with Session(db_engine) as session, session.begin():
        bundle_a = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="BrC=CCCO",
                applied_energy_corrections=[
                    _ac_with_scheme(
                        _bac_melius_scheme_ref(
                            name=name,
                            component_params=[
                                {"component_kind": "atom_corr", "key": "C", "value": -0.001},
                            ],
                        ),
                        application_role="bac_total",
                        value=-0.04,
                    )
                ],
            )
        )
        persist_computed_species_upload(session, bundle_a)

    with Session(db_engine) as session, session.begin():
        bundle_b = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="BrC=CCCCO",
                applied_energy_corrections=[
                    _ac_with_scheme(
                        _bac_melius_scheme_ref(
                            name=name,
                            component_params=[
                                {"component_kind": "atom_corr", "key": "C", "value": -0.999},
                            ],
                        ),
                        application_role="bac_total",
                        value=-0.04,
                    )
                ],
            )
        )
        with pytest.raises(
            ValueError,
            match=r"energy_correction_scheme_component_param.*key='atom_corr:C'",
        ):
            persist_computed_species_upload(session, bundle_b)


def test_scheme_without_params_remains_valid(db_engine) -> None:
    """Spec 8: schemes without params still work (no params persisted, no error)."""
    bundle = ComputedSpeciesUploadRequest(
        **_bundle_with_sp_calc(
            smiles="BrCCCCCO",
            applied_energy_corrections=[
                _ac_with_scheme(
                    _aec_scheme_ref(name="No-params scheme"),
                    application_role="aec_total",
                    value=-0.1,
                )
            ],
        )
    )
    with Session(db_engine) as session, session.begin():
        outcome = persist_computed_species_upload(session, bundle)
        ac = session.scalars(
            select(AppliedEnergyCorrection).where(
                AppliedEnergyCorrection.target_species_entry_id
                == outcome.species_entry_id
            )
        ).one()
        rows = session.scalars(
            select(EnergyCorrectionSchemeAtomParam).where(
                EnergyCorrectionSchemeAtomParam.scheme_id == ac.scheme_id
            )
        ).all()
        assert rows == []


def test_existing_paramless_scheme_can_be_extended_with_params(db_engine) -> None:
    """A scheme created without params can have params added by a later upload."""
    name = "Extend-with-params"
    with Session(db_engine) as session, session.begin():
        bundle_a = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="BrCCCCCCO",
                applied_energy_corrections=[
                    _ac_with_scheme(
                        _aec_scheme_ref(name=name),
                        application_role="aec_total",
                        value=-0.1,
                    )
                ],
            )
        )
        outcome_a = persist_computed_species_upload(session, bundle_a)
        scheme_id = session.scalars(
            select(AppliedEnergyCorrection.scheme_id).where(
                AppliedEnergyCorrection.target_species_entry_id
                == outcome_a.species_entry_id
            )
        ).one()

        bundle_b = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="BrCCCCCCCO",
                applied_energy_corrections=[
                    _ac_with_scheme(
                        _aec_scheme_ref(
                            name=name,
                            atom_params=[{"element": "H", "value": -0.5}],
                        ),
                        application_role="aec_total",
                        value=-0.1,
                    )
                ],
            )
        )
        persist_computed_species_upload(session, bundle_b)

        rows = session.scalars(
            select(EnergyCorrectionSchemeAtomParam).where(
                EnergyCorrectionSchemeAtomParam.scheme_id == scheme_id
            )
        ).all()
        assert {(r.element, r.value) for r in rows} == {("H", -0.5)}


def test_existing_paramless_scheme_can_be_backfilled_with_bond_params(db_engine) -> None:
    """A paramless PBAC scheme can have bond_params added by a later upload."""
    name = "Backfill bond params"
    with Session(db_engine) as session, session.begin():
        bundle_a = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="BrCCCCCCCCO",
                applied_energy_corrections=[
                    _ac_with_scheme(
                        _bac_petersson_scheme_ref(name=name),
                        application_role="bac_total",
                        value=-0.05,
                    )
                ],
            )
        )
        outcome_a = persist_computed_species_upload(session, bundle_a)
        scheme_id = session.scalars(
            select(AppliedEnergyCorrection.scheme_id).where(
                AppliedEnergyCorrection.target_species_entry_id
                == outcome_a.species_entry_id
            )
        ).one()

        bundle_b = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="BrCCCCCCCCCO",
                applied_energy_corrections=[
                    _ac_with_scheme(
                        _bac_petersson_scheme_ref(
                            name=name,
                            bond_params=[
                                {"bond_key": "C-H", "value": -0.11},
                                {"bond_key": "C-C", "value": -0.13},
                            ],
                        ),
                        application_role="bac_total",
                        value=-0.05,
                    )
                ],
            )
        )
        persist_computed_species_upload(session, bundle_b)

        rows = session.scalars(
            select(EnergyCorrectionSchemeBondParam).where(
                EnergyCorrectionSchemeBondParam.scheme_id == scheme_id
            )
        ).all()
        assert {(r.bond_key, r.value) for r in rows} == {
            ("C-H", -0.11),
            ("C-C", -0.13),
        }


def test_existing_paramless_scheme_can_be_backfilled_with_component_params(
    db_engine,
) -> None:
    """A paramless Melius scheme can have component_params added by a later upload."""
    name = "Backfill component params"
    with Session(db_engine) as session, session.begin():
        bundle_a = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="BrCCCCCCCCCCO",
                applied_energy_corrections=[
                    _ac_with_scheme(
                        _bac_melius_scheme_ref(name=name),
                        application_role="bac_total",
                        value=-0.04,
                    )
                ],
            )
        )
        outcome_a = persist_computed_species_upload(session, bundle_a)
        scheme_id = session.scalars(
            select(AppliedEnergyCorrection.scheme_id).where(
                AppliedEnergyCorrection.target_species_entry_id
                == outcome_a.species_entry_id
            )
        ).one()

        bundle_b = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="BrCCCCCCCCCCCO",
                applied_energy_corrections=[
                    _ac_with_scheme(
                        _bac_melius_scheme_ref(
                            name=name,
                            component_params=[
                                {
                                    "component_kind": "atom_corr",
                                    "key": "C",
                                    "value": -0.001,
                                },
                                {
                                    "component_kind": "mol_corr",
                                    "key": "global",
                                    "value": -0.002,
                                },
                            ],
                        ),
                        application_role="bac_total",
                        value=-0.04,
                    )
                ],
            )
        )
        persist_computed_species_upload(session, bundle_b)

        rows = session.scalars(
            select(EnergyCorrectionSchemeComponentParam).where(
                EnergyCorrectionSchemeComponentParam.scheme_id == scheme_id
            )
        ).all()
        assert {(r.component_kind.value, r.key, r.value) for r in rows} == {
            ("atom_corr", "C", -0.001),
            ("mol_corr", "global", -0.002),
        }


def test_repeated_bond_param_upload_is_idempotent(db_engine) -> None:
    """Re-uploading the same scheme + same bond params keeps a single row set."""
    name = "PBAC idempotency"
    bond_params = [{"bond_key": "C-H", "value": -0.11}, {"bond_key": "C-C", "value": -0.13}]
    with Session(db_engine) as session, session.begin():
        bundle_a = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="BrCCCCCCCCCCCCO",
                applied_energy_corrections=[
                    _ac_with_scheme(
                        _bac_petersson_scheme_ref(name=name, bond_params=bond_params),
                        application_role="bac_total",
                        value=-0.05,
                    )
                ],
            )
        )
        outcome_a = persist_computed_species_upload(session, bundle_a)
        scheme_id = session.scalars(
            select(AppliedEnergyCorrection.scheme_id).where(
                AppliedEnergyCorrection.target_species_entry_id
                == outcome_a.species_entry_id
            )
        ).one()

        bundle_b = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="BrCCCCCCCCCCCCCO",
                applied_energy_corrections=[
                    _ac_with_scheme(
                        _bac_petersson_scheme_ref(name=name, bond_params=bond_params),
                        application_role="bac_total",
                        value=-0.05,
                    )
                ],
            )
        )
        persist_computed_species_upload(session, bundle_b)

        rows = session.scalars(
            select(EnergyCorrectionSchemeBondParam).where(
                EnergyCorrectionSchemeBondParam.scheme_id == scheme_id
            )
        ).all()
        assert {(r.bond_key, r.value) for r in rows} == {
            ("C-H", -0.11),
            ("C-C", -0.13),
        }


def test_repeated_component_param_upload_is_idempotent(db_engine) -> None:
    """Re-uploading the same scheme + same component params keeps a single row set."""
    name = "Melius idempotency"
    component_params = [
        {"component_kind": "atom_corr", "key": "C", "value": -0.001},
        {"component_kind": "bond_corr_length", "key": "C-H", "value": 0.04},
    ]
    with Session(db_engine) as session, session.begin():
        bundle_a = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="BrCCCCCCCCCCCCCCO",
                applied_energy_corrections=[
                    _ac_with_scheme(
                        _bac_melius_scheme_ref(
                            name=name, component_params=component_params
                        ),
                        application_role="bac_total",
                        value=-0.04,
                    )
                ],
            )
        )
        outcome_a = persist_computed_species_upload(session, bundle_a)
        scheme_id = session.scalars(
            select(AppliedEnergyCorrection.scheme_id).where(
                AppliedEnergyCorrection.target_species_entry_id
                == outcome_a.species_entry_id
            )
        ).one()

        bundle_b = ComputedSpeciesUploadRequest(
            **_bundle_with_sp_calc(
                smiles="BrCCCCCCCCCCCCCCCO",
                applied_energy_corrections=[
                    _ac_with_scheme(
                        _bac_melius_scheme_ref(
                            name=name, component_params=component_params
                        ),
                        application_role="bac_total",
                        value=-0.04,
                    )
                ],
            )
        )
        persist_computed_species_upload(session, bundle_b)

        rows = session.scalars(
            select(EnergyCorrectionSchemeComponentParam).where(
                EnergyCorrectionSchemeComponentParam.scheme_id == scheme_id
            )
        ).all()
        assert {(r.component_kind.value, r.key, r.value) for r in rows} == {
            ("atom_corr", "C", -0.001),
            ("bond_corr_length", "C-H", 0.04),
        }
