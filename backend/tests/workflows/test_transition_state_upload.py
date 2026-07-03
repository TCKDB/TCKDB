"""Tests for the standalone transition-state upload pipeline.

Covers:
- Basic TS upload with primary opt only
- Upload with additional freq/sp calculations + typed result blocks
- Dependency edges and geometry linkage
- Schema validation (type mismatches, disallowed result blocks)
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.app_user import AppUser
from app.db.models.calculation import (
    Calculation,
    CalculationDependency,
    CalculationFreqResult,
    CalculationIRCPoint,
    CalculationIRCResult,
    CalculationOptResult,
    CalculationOutputGeometry,
    CalculationPathSearchPoint,
    CalculationPathSearchResult,
    CalculationSPResult,
)
from app.db.models.common import (
    CalculationDependencyRole,
    CalculationGeometryRole,
    CalculationType,
    IRCDirection,
    PathSearchMethod,
)
from app.db.models.transition_state import TransitionState
from app.schemas.fragments.calculation import (
    CalculationWithResultsPayload,
    IRCPointPayload,
    IRCResultPayload,
    PathSearchPointPayload,
    PathSearchResultPayload,
)
from app.schemas.workflows.transition_state_upload import (
    TransitionStateUploadRequest,
)
from app.workflows.transition_state import persist_transition_state_upload

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SOFTWARE = {"name": "gaussian", "version": "16", "revision": "C.02"}
_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}
_LOT_SP = {"method": "CCSD(T)", "basis": "cc-pVTZ"}

_XYZ_TS = """\
3
H transfer TS
H  0.0  0.0  0.0
H  0.0  0.0  0.9
H  0.0  0.0  1.8
"""

# Embedded reaction content: [H] + [H][H] → [H] + [H][H]
_REACTION = {
    "reversible": True,
    "reactants": [
        {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
        {"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}},
    ],
    "products": [
        {"species_entry": {"smiles": "[H]", "charge": 0, "multiplicity": 2}},
        {"species_entry": {"smiles": "[H][H]", "charge": 0, "multiplicity": 1}},
    ],
}


def _basic_ts_request() -> TransitionStateUploadRequest:
    """Minimal TS upload request with primary opt only."""
    return TransitionStateUploadRequest(
        reaction=_REACTION,
        charge=0,
        multiplicity=2,
        geometry={"xyz_text": _XYZ_TS},
        primary_opt=CalculationWithResultsPayload(
            type="opt",
            software_release=_SOFTWARE,
            level_of_theory=_LOT,
        ),
        label="H-transfer TS",
        note="test upload",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_basic_ts_upload_creates_concept_and_entry(db_engine) -> None:
    """Primary opt only — verifies TS concept, entry, geometry, and calc."""
    with Session(db_engine) as session, session.begin():
        session.add(AppUser(id=50, username="ts_tester"))
        session.flush()

        ts_entry = persist_transition_state_upload(
            session,
            _basic_ts_request(),
            created_by=50,
        )

        assert ts_entry.id is not None
        assert ts_entry.charge == 0
        assert ts_entry.multiplicity == 2
        assert ts_entry.created_by == 50

        # TS concept
        ts = session.get(TransitionState, ts_entry.transition_state_id)
        assert ts is not None
        assert ts.reaction_entry_id is not None
        assert ts.label == "H-transfer TS"
        assert ts.note == "test upload"

        # Calculation
        calcs = session.scalars(
            select(Calculation).where(
                Calculation.transition_state_entry_id == ts_entry.id
            )
        ).all()
        assert len(calcs) == 1
        assert calcs[0].type == CalculationType.opt

        # Output geometry link
        geo_links = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == calcs[0].id
            )
        ).all()
        assert len(geo_links) == 1


def test_ts_upload_with_additional_calcs_and_results(db_engine) -> None:
    """Upload with freq (+ result) and sp (+ result) additional calcs."""
    with Session(db_engine) as session, session.begin():
        session.add(AppUser(id=51, username="ts_tester_full"))
        session.flush()

        request = TransitionStateUploadRequest(
            reaction=_REACTION,
            charge=0,
            multiplicity=2,
            geometry={"xyz_text": _XYZ_TS},
            primary_opt=CalculationWithResultsPayload(
                type="opt",
                software_release=_SOFTWARE,
                level_of_theory=_LOT,
                opt_result={
                    "converged": True,
                    "n_steps": 42,
                    "final_energy_hartree": -1.234,
                },
            ),
            additional_calculations=[
                CalculationWithResultsPayload(
                    type="freq",
                    software_release=_SOFTWARE,
                    level_of_theory=_LOT,
                    freq_result={
                        "n_imag": 1,
                        "imag_freq_cm1": -1523.4,
                        "zpe_hartree": 0.012,
                    },
                ),
                CalculationWithResultsPayload(
                    type="sp",
                    software_release={"name": "orca", "version": "5.0"},
                    level_of_theory=_LOT_SP,
                    sp_result={"electronic_energy_hartree": -1.567},
                ),
            ],
        )
        ts_entry = persist_transition_state_upload(
            session, request, created_by=51
        )

        # 3 calculations total
        calcs = session.scalars(
            select(Calculation).where(
                Calculation.transition_state_entry_id == ts_entry.id
            )
        ).all()
        assert len(calcs) == 3

        opt_calc = next(c for c in calcs if c.type == CalculationType.opt)
        freq_calc = next(c for c in calcs if c.type == CalculationType.freq)
        sp_calc = next(c for c in calcs if c.type == CalculationType.sp)

        # Opt result
        opt_result = session.get(CalculationOptResult, opt_calc.id)
        assert opt_result is not None
        assert opt_result.converged is True
        assert opt_result.n_steps == 42

        # Freq result
        freq_result = session.get(CalculationFreqResult, freq_calc.id)
        assert freq_result is not None
        assert freq_result.n_imag == 1
        assert freq_result.imag_freq_cm1 == pytest.approx(-1523.4)

        # SP result
        sp_result = session.get(CalculationSPResult, sp_calc.id)
        assert sp_result is not None
        assert sp_result.electronic_energy_hartree == pytest.approx(-1.567)

        # Dependency edges: freq→opt and sp→opt
        deps = session.scalars(
            select(CalculationDependency).where(
                CalculationDependency.parent_calculation_id == opt_calc.id
            )
        ).all()
        assert len(deps) == 2
        dep_roles = {d.dependency_role for d in deps}
        assert CalculationDependencyRole.freq_on in dep_roles
        assert CalculationDependencyRole.single_point_on in dep_roles

        # Under the narrowed fallback only opt auto-claims the saddle
        # geometry. Freq and sp produce zero output_geometry rows unless
        # the producer declares them explicitly.
        geo_links = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id.in_(
                    [c.id for c in calcs]
                )
            )
        ).all()
        assert len(geo_links) == 1
        assert geo_links[0].calculation_id == opt_calc.id


def test_ts_upload_without_results_succeeds(db_engine) -> None:
    """Additional calcs without result blocks are fine."""
    with Session(db_engine) as session, session.begin():
        session.add(AppUser(id=52, username="ts_no_results"))
        session.flush()

        request = TransitionStateUploadRequest(
            reaction=_REACTION,
            charge=0,
            multiplicity=1,
            geometry={"xyz_text": _XYZ_TS},
            primary_opt=CalculationWithResultsPayload(
                type="opt",
                software_release=_SOFTWARE,
                level_of_theory=_LOT,
            ),
            additional_calculations=[
                CalculationWithResultsPayload(
                    type="freq",
                    software_release=_SOFTWARE,
                    level_of_theory=_LOT,
                ),
            ],
        )
        ts_entry = persist_transition_state_upload(session, request, created_by=52)

        freq_calc = session.scalar(
            select(Calculation).where(
                Calculation.transition_state_entry_id == ts_entry.id,
                Calculation.type == CalculationType.freq,
            )
        )
        assert freq_calc is not None
        # No freq result row
        assert session.get(CalculationFreqResult, freq_calc.id) is None


# ---------------------------------------------------------------------------
# Schema validation tests (no DB needed)
# ---------------------------------------------------------------------------


def test_schema_rejects_non_opt_primary():
    """primary_opt must have type='opt'."""
    with pytest.raises(ValueError, match="primary_opt must have type 'opt'"):
        TransitionStateUploadRequest(
            reaction=_REACTION,
            charge=0,
            multiplicity=1,
            geometry={"xyz_text": "1\n\nH 0 0 0"},
            primary_opt=CalculationWithResultsPayload(
                type="freq",
                software_release=_SOFTWARE,
                level_of_theory=_LOT,
            ),
        )


def test_schema_rejects_opt_in_additional():
    """Additional calculations cannot be type='opt'."""
    with pytest.raises(ValueError, match="not allowed"):
        TransitionStateUploadRequest(
            reaction=_REACTION,
            charge=0,
            multiplicity=1,
            geometry={"xyz_text": "1\n\nH 0 0 0"},
            primary_opt=CalculationWithResultsPayload(
                type="opt",
                software_release=_SOFTWARE,
                level_of_theory=_LOT,
            ),
            additional_calculations=[
                CalculationWithResultsPayload(
                    type="opt",
                    software_release=_SOFTWARE,
                    level_of_theory=_LOT,
                ),
            ],
        )


def test_schema_rejects_mismatched_result_block():
    """freq_result on an sp calculation should fail."""
    with pytest.raises(ValueError, match="not allowed for calculation type"):
        CalculationWithResultsPayload(
            type="sp",
            software_release=_SOFTWARE,
            level_of_theory=_LOT,
            freq_result={"n_imag": 1},
        )


def test_schema_allows_irc_additional():
    """IRC should be accepted as an additional calculation type."""
    request = TransitionStateUploadRequest(
        reaction=_REACTION,
        charge=0,
        multiplicity=1,
        geometry={"xyz_text": "1\n\nH 0 0 0"},
        primary_opt=CalculationWithResultsPayload(
            type="opt",
            software_release=_SOFTWARE,
            level_of_theory=_LOT,
        ),
        additional_calculations=[
            CalculationWithResultsPayload(
                type="irc",
                software_release=_SOFTWARE,
                level_of_theory=_LOT,
            ),
        ],
    )
    assert len(request.additional_calculations) == 1
    assert request.additional_calculations[0].type == CalculationType.irc


# ---------------------------------------------------------------------------
# End-to-end IRC / path-search write-path tests
# ---------------------------------------------------------------------------


_XYZ_IRC_F = "1\nF1\nH  0.0  0.0  1.2\n"
_XYZ_IRC_R = "1\nR1\nH  0.0  0.0  0.8\n"
_XYZ_PS_0 = "1\nI0\nH  0.0  0.0  0.0\n"
_XYZ_PS_2 = "1\nI2\nH  0.0  0.0  1.5\n"


def test_ts_upload_with_irc_additional_persists_irc_result(db_engine) -> None:
    """TS upload carrying an IRC additional calc persists IRC structured rows."""

    with Session(db_engine) as session, session.begin():
        # Let the DB assign the user id rather than hardcoding 60 — other
        # committed tests in the suite may consume IDs in that range and
        # trigger a unique-constraint violation under certain orderings.
        writer = AppUser(username="ts_irc_writer")
        session.add(writer)
        session.flush()
        writer_id = writer.id

        request = TransitionStateUploadRequest(
            reaction=_REACTION,
            charge=0,
            multiplicity=2,
            geometry={"xyz_text": _XYZ_TS},
            primary_opt=CalculationWithResultsPayload(
                type="opt",
                software_release=_SOFTWARE,
                level_of_theory=_LOT,
            ),
            additional_calculations=[
                CalculationWithResultsPayload(
                    type="irc",
                    software_release=_SOFTWARE,
                    level_of_theory=_LOT,
                    irc_result=IRCResultPayload(
                        direction=IRCDirection.both,
                        has_forward=True,
                        has_reverse=True,
                        ts_point_index=0,
                        points=[
                            IRCPointPayload(
                                point_index=0,
                                direction=None,
                                is_ts=True,
                                geometry={"xyz_text": _XYZ_TS},
                            ),
                            IRCPointPayload(
                                point_index=1,
                                direction=IRCDirection.forward,
                                geometry={"xyz_text": _XYZ_IRC_F},
                            ),
                            IRCPointPayload(
                                point_index=2,
                                direction=IRCDirection.reverse,
                                geometry={"xyz_text": _XYZ_IRC_R},
                            ),
                        ],
                    ),
                ),
            ],
        )
        ts_entry = persist_transition_state_upload(
            session, request, created_by=writer_id
        )

        irc_calc = session.scalar(
            select(Calculation).where(
                Calculation.transition_state_entry_id == ts_entry.id,
                Calculation.type == CalculationType.irc,
            )
        )
        assert irc_calc is not None

        irc_result = session.get(CalculationIRCResult, irc_calc.id)
        assert irc_result is not None
        assert irc_result.direction == IRCDirection.both
        assert irc_result.point_count == 3

        points = session.scalars(
            select(CalculationIRCPoint).where(
                CalculationIRCPoint.calculation_id == irc_calc.id
            )
        ).all()
        assert {p.point_index for p in points} == {0, 1, 2}

        # Dependency edge back to the primary opt
        deps = session.scalars(
            select(CalculationDependency).where(
                CalculationDependency.child_calculation_id == irc_calc.id,
                CalculationDependency.dependency_role
                == CalculationDependencyRole.irc_start,
            )
        ).all()
        assert len(deps) == 1

        # Output geometry roles: irc_forward + irc_reverse from the
        # IRC sampled points. No role=final auto-row: the narrowed
        # fallback only fires for opt; IRC must declare any final-role
        # output geometry explicitly via ``output_geometries``.
        irc_links = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == irc_calc.id
            )
        ).all()
        roles = {link.role for link in irc_links}
        assert CalculationGeometryRole.final not in roles
        assert CalculationGeometryRole.irc_forward in roles
        assert CalculationGeometryRole.irc_reverse in roles


def test_ts_upload_with_path_search_neb_additional_persists_points(
    db_engine,
) -> None:
    """TS upload carrying a path_search (NEB) additional calc persists
    path-search points and wires the inverted ``optimized_from`` edge:
    the path-search calc is the *parent* of the primary TS opt.
    """

    with Session(db_engine) as session, session.begin():
        session.add(AppUser(id=61, username="ts_path_search_writer"))
        session.flush()

        request = TransitionStateUploadRequest(
            reaction=_REACTION,
            charge=0,
            multiplicity=2,
            geometry={"xyz_text": _XYZ_TS},
            primary_opt=CalculationWithResultsPayload(
                type="opt",
                software_release=_SOFTWARE,
                level_of_theory=_LOT,
            ),
            additional_calculations=[
                CalculationWithResultsPayload(
                    type="path_search",
                    software_release=_SOFTWARE,
                    level_of_theory=_LOT,
                    path_search_result=PathSearchResultPayload(
                        method=PathSearchMethod.neb,
                        is_double_ended=True,
                        converged=True,
                        n_points=3,
                        climbing_image_index=1,
                        points=[
                            PathSearchPointPayload(
                                point_index=0,
                                electronic_energy_hartree=-1.0,
                                geometry={"xyz_text": _XYZ_PS_0},
                            ),
                            PathSearchPointPayload(
                                point_index=1,
                                electronic_energy_hartree=-0.9,
                                is_climbing_image=True,
                                is_ts_guess=True,
                                geometry={"xyz_text": _XYZ_TS},
                            ),
                            PathSearchPointPayload(
                                point_index=2,
                                electronic_energy_hartree=-1.05,
                                geometry={"xyz_text": _XYZ_PS_2},
                            ),
                        ],
                    ),
                ),
            ],
        )
        ts_entry = persist_transition_state_upload(
            session, request, created_by=61
        )

        ps_calc = session.scalar(
            select(Calculation).where(
                Calculation.transition_state_entry_id == ts_entry.id,
                Calculation.type == CalculationType.path_search,
            )
        )
        assert ps_calc is not None
        ps_result = session.get(CalculationPathSearchResult, ps_calc.id)
        assert ps_result is not None
        assert ps_result.method is PathSearchMethod.neb

        points = session.scalars(
            select(CalculationPathSearchPoint).where(
                CalculationPathSearchPoint.calculation_id == ps_calc.id
            )
        ).all()
        assert {p.point_index for p in points} == {0, 1, 2}

        # Inverted edge: path-search is parent of the primary opt via
        # ``optimized_from``. The TS opt is the child.
        primary_opt = session.scalar(
            select(Calculation).where(
                Calculation.transition_state_entry_id == ts_entry.id,
                Calculation.type == CalculationType.opt,
            )
        )
        assert primary_opt is not None
        deps = session.scalars(
            select(CalculationDependency).where(
                CalculationDependency.parent_calculation_id == ps_calc.id,
                CalculationDependency.child_calculation_id == primary_opt.id,
                CalculationDependency.dependency_role
                == CalculationDependencyRole.optimized_from,
            )
        ).all()
        assert len(deps) == 1

        # Path-search calculations store LoT on the parent calculation row.
        # Since this path_search calc and the primary opt calc use the same LoT ref,
        # they should resolve to the same level_of_theory row.
        assert ps_calc.lot_id is not None
        assert ps_calc.lot.method == _LOT["method"]
        assert ps_calc.lot.basis == _LOT["basis"]
        assert primary_opt.lot_id is not None
        assert ps_calc.lot_id == primary_opt.lot_id

        # Climbing image reuses the shared TS saddle geometry — the
        # path-search-point role and any other output rows must not
        # double-insert the same (calc, geom) pair.
        links = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == ps_calc.id
            )
        ).all()
        geom_ids = [link.geometry_id for link in links]
        assert len(geom_ids) == len(set(geom_ids))  # no dupes on (calc, geom)
        roles = {link.role for link in links}
        assert CalculationGeometryRole.path_search_point in roles
