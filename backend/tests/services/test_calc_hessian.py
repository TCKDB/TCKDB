"""Cartesian Hessian storage tests (DR-0030).

Covers the HessianPayload validator (triangle-length invariant), the
persistence path through ``resolve_and_persist_calculation_with_results``
(including the mandatory geometry binding and geometry dedup), and the
DB-level cardinality CHECK constraint.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models.calculation import CalculationHessian
from app.db.models.common import HessianSource
from app.db.models.geometry import Geometry
from app.schemas.fragments.calculation import CalculationWithResultsPayload
from app.services.calculation_resolution import (
    resolve_and_persist_calculation_with_results,
)

_INCHI_COUNTER = 0


def _next_inchi_key(prefix: str) -> str:
    global _INCHI_COUNTER
    _INCHI_COUNTER += 1
    stem = f"{prefix}{_INCHI_COUNTER:0>21}"
    return stem[:27]


def _create_species_entry(connection, *, inchi_key: str) -> int:
    species_id = connection.execute(
        text(
            """
            INSERT INTO species (kind, smiles, inchi_key, charge, multiplicity, stereo_kind)
            VALUES ('molecule', :smiles, :inchi_key, 0, 1, 'achiral')
            RETURNING id
            """
        ),
        {"smiles": inchi_key, "inchi_key": inchi_key},
    ).scalar_one()
    return connection.execute(
        text(
            "INSERT INTO species_entry (species_id) VALUES (:species_id) RETURNING id"
        ),
        {"species_id": species_id},
    ).scalar_one()


# A 2-atom (H2) geometry → 3N = 6 → lower triangle length = 6*7/2 = 21.
_H2_XYZ = "2\n\nH 0.000000 0.000000 0.000000\nH 0.000000 0.000000 0.740000\n"
_H2_TRIANGLE = [float(i) for i in range(21)]


def _freq_calc_with_hessian(
    *, triangle: list[float], xyz: str = _H2_XYZ
) -> CalculationWithResultsPayload:
    return CalculationWithResultsPayload.model_validate(
        {
            "type": "freq",
            "software_release": {"name": "Gaussian", "version": "16"},
            "level_of_theory": {"method": "wB97X-D", "basis": "def2-TZVP"},
            "freq_result": {"n_imag": 0, "zpe_hartree": 0.01},
            "hessian": {
                "geometry": {"xyz_text": xyz},
                "lower_triangle_hartree_bohr2": triangle,
                "source": "parsed_fchk",
            },
        }
    )


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------


class TestHessianPayloadValidation:
    def test_correct_triangle_length_accepted(self) -> None:
        payload = _freq_calc_with_hessian(triangle=_H2_TRIANGLE)
        assert payload.hessian is not None
        assert len(payload.hessian.lower_triangle_hartree_bohr2) == 21

    def test_wrong_triangle_length_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must have exactly 21"):
            _freq_calc_with_hessian(triangle=[0.0, 1.0, 2.0])

    def test_empty_triangle_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _freq_calc_with_hessian(triangle=[])


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_persist_hessian_binds_geometry_and_natoms(db_engine) -> None:
    with Session(db_engine) as session:
        with session.begin():
            species_entry_id = _create_species_entry(
                session.connection(), inchi_key=_next_inchi_key("HESS")
            )
            calc = resolve_and_persist_calculation_with_results(
                session,
                _freq_calc_with_hessian(triangle=_H2_TRIANGLE),
                species_entry_id=species_entry_id,
            )
            session.flush()

            row = session.scalar(
                select(CalculationHessian).where(
                    CalculationHessian.calculation_id == calc.id
                )
            )
            assert row is not None
            assert row.natoms == 2
            assert len(row.lower_triangle_hartree_bohr2) == 21
            assert row.source == HessianSource.parsed_fchk
            # Geometry binding is mandatory and points at a real geometry.
            geom = session.get(Geometry, row.geometry_id)
            assert geom is not None
            assert geom.natoms == 2


def test_hessian_geometry_is_deduped_with_calc_input_geometry(db_engine) -> None:
    """The Hessian's geometry resolves through the content-addressed seam,
    so re-uploading the same XYZ does not create a duplicate geometry row."""
    with Session(db_engine) as session:
        with session.begin():
            se1 = _create_species_entry(
                session.connection(), inchi_key=_next_inchi_key("HESSDEDUPA")
            )
            se2 = _create_species_entry(
                session.connection(), inchi_key=_next_inchi_key("HESSDEDUPB")
            )
            calc1 = resolve_and_persist_calculation_with_results(
                session,
                _freq_calc_with_hessian(triangle=_H2_TRIANGLE),
                species_entry_id=se1,
            )
            calc2 = resolve_and_persist_calculation_with_results(
                session,
                _freq_calc_with_hessian(triangle=_H2_TRIANGLE),
                species_entry_id=se2,
            )
            session.flush()

            g1 = session.scalar(
                select(CalculationHessian.geometry_id).where(
                    CalculationHessian.calculation_id == calc1.id
                )
            )
            g2 = session.scalar(
                select(CalculationHessian.geometry_id).where(
                    CalculationHessian.calculation_id == calc2.id
                )
            )
            assert g1 == g2  # same XYZ → same deduped geometry row


def test_db_check_rejects_wrong_cardinality(db_engine) -> None:
    """The DB CHECK is the last line of defence if a row is inserted
    outside the validated payload path."""
    from sqlalchemy.exc import IntegrityError

    with Session(db_engine) as session:
        with session.begin():
            connection = session.connection()
            species_entry_id = _create_species_entry(
                connection, inchi_key=_next_inchi_key("HESSCHECK")
            )
            # Minimal calc + geometry rows to satisfy the FKs.
            calc_id = connection.execute(
                text(
                    """
                    INSERT INTO calculation (type, species_entry_id)
                    VALUES ('freq', :se) RETURNING id
                    """
                ),
                {"se": species_entry_id},
            ).scalar_one()
            geom_id = connection.execute(
                text(
                    """
                    INSERT INTO geometry (natoms, geom_hash, xyz_text, public_ref)
                    VALUES (2, :h, :xyz, :ref) RETURNING id
                    """
                ),
                {
                    "h": _next_inchi_key("GHASH") + "geomhashpad0000000000000",
                    "xyz": _H2_XYZ,
                    "ref": "geom_" + _next_inchi_key("GR"),
                },
            ).scalar_one()

        with pytest.raises(IntegrityError):
            with session.begin():
                session.connection().execute(
                    text(
                        """
                        INSERT INTO calc_hessian
                          (calculation_id, geometry_id, natoms,
                           lower_triangle_hartree_bohr2, source)
                        VALUES (:c, :g, 2, ARRAY[1.0, 2.0, 3.0], 'uploaded')
                        """
                    ),
                    {"c": calc_id, "g": geom_id},
                )
