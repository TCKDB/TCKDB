"""Service-level tests for IRC and NEB result persistence.

Exercises ``persist_calculation_result`` via the shared
``resolve_and_persist_calculation_with_results`` helper, focusing on:

- IRC result bundles (metadata, per-point rows, output-geometry links)
- NEB image bundles (per-image rows, output-geometry links)
- Type/result compatibility rejection
- Deduplication of the ``(calculation_id, geometry_id)`` pair
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models.calculation import (
    CalculationIRCPoint,
    CalculationIRCResult,
    CalculationNEBImageResult,
    CalculationOutputGeometry,
)
from app.db.models.common import (
    CalculationGeometryRole,
    CalculationType,
    IRCDirection,
)
from app.schemas.fragments.calculation import (
    CalculationWithResultsPayload,
    IRCPointPayload,
    IRCResultPayload,
    NEBImageResultPayload,
    NEBResultPayload,
)
from app.services.calculation_resolution import (
    persist_calculation,
    persist_calculation_result,
    resolve_and_persist_calculation_with_results,
    resolve_calculation_create_request,
)
from app.schemas.fragments.calculation import CalculationCreateRequest


_SOFTWARE = {"name": "gaussian", "version": "16", "revision": "C.02"}
_LOT = {"method": "B3LYP", "basis": "6-31G(d)"}


_INCHI_COUNTER = 0


def _next_inchi_key(prefix: str) -> str:
    global _INCHI_COUNTER
    _INCHI_COUNTER += 1
    stem = f"{prefix}{_INCHI_COUNTER:0>21}"
    return stem[:27]


def _create_species_entry(session: Session, *, inchi_key: str) -> int:
    species_id = session.connection().execute(
        text(
            """
            INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind)
            VALUES ('molecule', '[H]', :inchi_key, 0, 1, 'achiral')
            RETURNING id
            """
        ),
        {"inchi_key": inchi_key},
    ).scalar_one()
    return session.connection().execute(
        text(
            """
            INSERT INTO species_entry (species_id)
            VALUES (:species_id)
            RETURNING id
            """
        ),
        {"species_id": species_id},
    ).scalar_one()


def _xyz(comment: str, z: float) -> str:
    return f"1\n{comment}\nH  0.0  0.0  {z:.3f}\n"


# ---------------------------------------------------------------------------
# IRC
# ---------------------------------------------------------------------------


def test_persist_irc_result_with_points_and_geometries(db_engine) -> None:
    """IRC result row plus forward/reverse/TS points with geometry links."""

    with Session(db_engine) as session, session.begin():
        species_entry_id = _create_species_entry(
            session, inchi_key=_next_inchi_key("IRCPATH")
        )

        irc_payload = IRCResultPayload(
            direction=IRCDirection.both,
            has_forward=True,
            has_reverse=True,
            ts_point_index=0,
            points=[
                IRCPointPayload(
                    point_index=0,
                    direction=None,
                    is_ts=True,
                    geometry={"xyz_text": _xyz("TS", 1.0)},
                ),
                IRCPointPayload(
                    point_index=1,
                    direction=IRCDirection.forward,
                    geometry={"xyz_text": _xyz("F1", 1.2)},
                ),
                IRCPointPayload(
                    point_index=2,
                    direction=IRCDirection.reverse,
                    geometry={"xyz_text": _xyz("R1", 0.8)},
                ),
            ],
        )
        upload = CalculationWithResultsPayload(
            type=CalculationType.irc,
            software_release=_SOFTWARE,
            level_of_theory=_LOT,
            irc_result=irc_payload,
        )

        calc = resolve_and_persist_calculation_with_results(
            session,
            upload,
            species_entry_id=species_entry_id,
        )

        irc_result = session.get(CalculationIRCResult, calc.id)
        assert irc_result is not None
        assert irc_result.direction == IRCDirection.both
        assert irc_result.ts_point_index == 0
        assert irc_result.point_count == 3

        points = session.scalars(
            select(CalculationIRCPoint).where(
                CalculationIRCPoint.calculation_id == calc.id
            )
        ).all()
        assert {p.point_index for p in points} == {0, 1, 2}
        ts_point = next(p for p in points if p.is_ts)
        assert ts_point.point_index == 0
        assert ts_point.direction is None
        assert ts_point.geometry_id is not None

        # Output-geometry links: only forward/reverse, not the TS point.
        output_links = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == calc.id
            )
        ).all()
        roles = {link.role for link in output_links}
        assert roles == {
            CalculationGeometryRole.irc_forward,
            CalculationGeometryRole.irc_reverse,
        }
        # Deterministic ordering: forward point_index=1 → output_order=3, etc.
        by_role = {link.role: link for link in output_links}
        assert by_role[CalculationGeometryRole.irc_forward].output_order == 3
        assert by_role[CalculationGeometryRole.irc_reverse].output_order == 4


def test_persist_irc_result_rejected_for_non_irc_calc(db_engine) -> None:
    """The defensive type check in ``persist_calculation_result`` fires."""

    with Session(db_engine) as session, session.begin():
        species_entry_id = _create_species_entry(
            session, inchi_key=_next_inchi_key("IRCBAD")
        )

        request = CalculationCreateRequest(
            type=CalculationType.sp,
            species_entry_id=species_entry_id,
            software_release=_SOFTWARE,
            level_of_theory=_LOT,
        )
        resolved = resolve_calculation_create_request(session, request)
        calculation = persist_calculation(session, resolved)

        bad_upload = CalculationWithResultsPayload.model_construct(
            type=CalculationType.sp,
            software_release=_SOFTWARE,
            level_of_theory=_LOT,
            irc_result=IRCResultPayload(
                direction=IRCDirection.forward,
                has_forward=True,
                has_reverse=False,
            ),
        )
        with pytest.raises(ValueError, match="irc_result is only allowed"):
            persist_calculation_result(session, calculation, bad_upload)


# ---------------------------------------------------------------------------
# NEB
# ---------------------------------------------------------------------------


def test_persist_neb_result_with_images_and_geometries(db_engine) -> None:
    """NEB image rows plus per-image output-geometry links."""

    with Session(db_engine) as session, session.begin():
        species_entry_id = _create_species_entry(
            session, inchi_key=_next_inchi_key("NEBPATH")
        )

        neb_payload = NEBResultPayload(
            images=[
                NEBImageResultPayload(
                    image_index=0,
                    electronic_energy_hartree=-1.0,
                    geometry={"xyz_text": _xyz("I0", 0.0)},
                ),
                NEBImageResultPayload(
                    image_index=1,
                    electronic_energy_hartree=-0.9,
                    is_climbing_image=True,
                    geometry={"xyz_text": _xyz("I1", 0.5)},
                ),
                NEBImageResultPayload(
                    image_index=2,
                    electronic_energy_hartree=-1.05,
                    geometry={"xyz_text": _xyz("I2", 1.0)},
                ),
            ],
        )
        upload = CalculationWithResultsPayload(
            type=CalculationType.neb,
            software_release=_SOFTWARE,
            level_of_theory=_LOT,
            neb_result=neb_payload,
        )

        calc = resolve_and_persist_calculation_with_results(
            session,
            upload,
            species_entry_id=species_entry_id,
        )

        images = session.scalars(
            select(CalculationNEBImageResult).where(
                CalculationNEBImageResult.calculation_id == calc.id
            )
        ).all()
        assert {img.image_index for img in images} == {0, 1, 2}
        climbing = next(img for img in images if img.is_climbing_image)
        assert climbing.image_index == 1

        links = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == calc.id
            )
        ).all()
        assert len(links) == 3
        assert all(
            link.role == CalculationGeometryRole.neb_image for link in links
        )
        assert {link.output_order for link in links} == {2, 3, 4}


def test_neb_result_dedupes_shared_image_geometry(db_engine) -> None:
    """Two images with the same geometry produce only one output-geometry row."""

    with Session(db_engine) as session, session.begin():
        species_entry_id = _create_species_entry(
            session, inchi_key=_next_inchi_key("NEBDEDUP")
        )

        shared_xyz = _xyz("SHARED", 0.5)
        neb_payload = NEBResultPayload(
            images=[
                NEBImageResultPayload(
                    image_index=0,
                    geometry={"xyz_text": shared_xyz},
                ),
                NEBImageResultPayload(
                    image_index=1,
                    geometry={"xyz_text": shared_xyz},
                ),
            ],
        )
        upload = CalculationWithResultsPayload(
            type=CalculationType.neb,
            software_release=_SOFTWARE,
            level_of_theory=_LOT,
            neb_result=neb_payload,
        )

        calc = resolve_and_persist_calculation_with_results(
            session,
            upload,
            species_entry_id=species_entry_id,
        )

        links = session.scalars(
            select(CalculationOutputGeometry).where(
                CalculationOutputGeometry.calculation_id == calc.id
            )
        ).all()
        assert len(links) == 1  # deduped by geometry_id
